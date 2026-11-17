"""Authenticated Calliope bridge to Hermes' native Twilio SMS adapter.

Calliope owns identity, conversations and the durable inbox. Hermes owns Twilio
signature validation, SMS formatting/chunking and submission. This never starts
a second gateway listener or mutates the gateway's environment.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit


def adapter(account):
    sys.path.insert(0, '/opt/hermes') if '/opt/hermes' not in sys.path else None
    from gateway.config import PlatformConfig
    from plugins.platforms.sms.adapter import SmsAdapter
    result = SmsAdapter(PlatformConfig())
    result._account_sid = account['account_sid']
    result._auth_token = account['auth_token']
    result._from_number = account['phone_number']
    result._webhook_url = account['webhook_url']
    return result


def validate(body):
    account, params = body['account'], body['params']
    valid = adapter(account)._validate_twilio_signature(account['webhook_url'], params, body.get('signature', ''))
    return {'valid': bool(valid and params.get('AccountSid') == account['account_sid']
                          and params.get('To') == account['phone_number'])}


def probe(body):
    async def check():
        import aiohttp
        account = body['account']
        adapter(account)  # Fail setup if the pinned native adapter is unavailable.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
            url = 'https://api.twilio.com/2010-04-01/Accounts/' + account['account_sid'] + '/IncomingPhoneNumbers.json'
            async with client.get(url, params={'PhoneNumber': account['phone_number']},
                                  auth=aiohttp.BasicAuth(account['account_sid'], account['auth_token'])) as response:
                if response.status != 200:
                    return {'ok': False, 'message': 'Twilio rejected these credentials. Check the Account SID and Auth Token.'}
                numbers = (await response.json()).get('incoming_phone_numbers', [])
                matches = [n for n in numbers if n.get('phone_number') == account['phone_number'] and n.get('capabilities', {}).get('sms')]
                return {'ok': bool(matches), 'message': None if matches else 'This account does not own that SMS-capable phone number.',
                        'webhook_configured': any(n.get('sms_url') == account['webhook_url'] and n.get('sms_method') == 'POST' for n in matches)}
    return asyncio.run(check())


def format_reply(body):
    native = adapter(body['account'])
    text = native.format_message(body['text'])
    # The inherited helper defaults to 4096 even though SmsAdapter declares
    # 1600. Pass the SMS limit explicitly, then submit each durable chunk once.
    return {'chunks': native.truncate_message(text, max_length=native.MAX_MESSAGE_LENGTH) if text.strip() else []}


def send(body, *, spool=None):
    """One native chunk per durable attempt. A timeout never blindly resends."""
    account, recipient, text = body['account'], body['to'], body['text']
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}', recipient) or not text or len(text) > 1600:
        raise ValueError('Invalid SMS envelope')
    attempt = str(body['attempt_id'])
    digest = hashlib.sha256(json.dumps([account['account_sid'], account['phone_number'], recipient, text]).encode()).hexdigest()
    path = Path(spool or Path(os.environ.get('HERMES_HOME', '/opt/data')) / '.calliope-control' / 'sms-spool.sqlite')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sends(id TEXT PRIMARY KEY,digest TEXT,state TEXT,result TEXT,updated REAL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT digest,state,result FROM sends WHERE id=?', (attempt,)).fetchone()
        if row:
            if row[0] != digest:
                raise ValueError('SMS attempt content changed')
            return json.loads(row[2]) if row[2] else {'state': 'uncertain', 'message': 'Check Twilio message logs before resending.'}
        db.execute('INSERT INTO sends VALUES(?,?,?,NULL,?)', (attempt, digest, 'sending', time.time()))
    try:
        result = asyncio.run(adapter(account).send(recipient, text))
        state = 'sent' if result.success and result.message_id else 'failed' if re.match(r'Twilio 4\d\d:', result.error or '') else 'uncertain'
        saved = {'state': state, 'message_id': result.message_id if state == 'sent' else None,
                 'message': None if state == 'sent' else 'Twilio submission failed. Check message logs before trying again.'}
    except Exception:
        saved = {'state': 'uncertain', 'message': 'Twilio submission could not be confirmed. Check message logs before resending.'}
    with sqlite3.connect(path) as db:
        db.execute('UPDATE sends SET state=?,result=?,updated=? WHERE id=?', (saved['state'], json.dumps(saved), time.time(), attempt))
    return saved


def media(body):
    """Fetch only media belonging to this signed message, after acknowledging it."""
    async def fetch():
        import aiohttp
        account, sid = body['account'], body['message_sid']
        prefix = f"/2010-04-01/Accounts/{account['account_sid']}/Messages/{sid}/Media/"
        images = []
        total = 0
        max_bytes = min(int(body.get('max_bytes') or 8 * 1024 * 1024), 25 * 1024 * 1024)
        items = body.get('media', [])
        if len(items) > 4:
            raise ValueError('Send up to four images in one message.')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            for index, item in enumerate(items):
                url = item['url']
                parsed = urlsplit(url)
                if (parsed.scheme != 'https' or parsed.netloc != 'api.twilio.com' or parsed.query or parsed.fragment
                        or not re.fullmatch(re.escape(prefix) + r'ME[0-9a-fA-F]{32}', parsed.path)):
                    raise ValueError('Invalid Twilio media reference')
                for hop in range(4):
                    auth = aiohttp.BasicAuth(account['account_sid'], account['auth_token']) if hop == 0 else None
                    async with client.get(url, auth=auth, allow_redirects=False) as response:
                        if response.status in (301, 302, 303, 307, 308):
                            url = response.headers.get('Location', '')
                            parsed = urlsplit(url)
                            host = parsed.hostname or ''
                            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (443, None) or not (
                                host == 'api.twilio.com' or host.endswith('.twiliocdn.com')):
                                raise ValueError('Unexpected Twilio media redirect')
                            continue
                        if response.status != 200:
                            raise ValueError('Twilio media unavailable')
                        kind = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                        if kind not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
                            raise ValueError('Send a JPEG, PNG, WebP or GIF image.')
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError('SMS images exceed the conversation image limit')
                            chunks.append(chunk)
                        images.append({'name': f'sms-image-{index+1}.{kind.split("/")[1]}',
                                       'data_url': f'data:{kind};base64,' + base64.b64encode(b''.join(chunks)).decode()})
                        break
                else:
                    raise ValueError('Too many Twilio media redirects')
        return {'attachments': images}
    return asyncio.run(fetch())
