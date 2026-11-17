"""Hermes control-plane email transport for Calliope's reviewed outbox.

Does not enable the email gateway/auto-responder. Credentials are supplied from
the canonical credential store for each authenticated control request. The
durable local spool prevents an HTTP retry from becoming a second SMTP send.
"""
import base64
import contextlib
import hashlib
import imaplib
import json
import logging
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path


class MailTransportError(ValueError):
    """Useful probe diagnostics without exposing server replies or credentials."""

    def __init__(self, stage, exc):
        protocol, operation = stage.split('.', 1)
        label = protocol.upper()
        self.details = {'stage': stage, 'error_type': type(exc).__name__}
        if isinstance(exc, ssl.SSLCertVerificationError):
            code, message = 'tls_certificate', f'{label} server certificate could not be verified. Check the mail server hostname and certificate.'
        elif isinstance(exc, ssl.SSLError):
            code, message = 'tls', f'{label} secure connection failed. Check the server and port.'
        elif isinstance(exc, socket.gaierror):
            code, message = 'dns', f'{label} mail server hostname could not be resolved.'
        elif isinstance(exc, TimeoutError):
            code, message = 'timeout', f'{label} server timed out during {operation}. Check network access and the server port.'
        elif isinstance(exc, ConnectionError):
            code, message = 'connection', f'{label} connection failed during {operation}. Check network access and the server port.'
        elif isinstance(exc, imaplib.IMAP4.abort):
            code, message = 'disconnected', f'{label} server disconnected during {operation}. Try again.'
        elif operation == 'login' and isinstance(exc, (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError)):
            # Inspect known signals, but never return arbitrary server text: it
            # can contain the login name, password, or an authentication blob.
            reply = str(getattr(exc, 'smtp_error', exc)).lower()
            if 'application-specific password required' in reply or 'app password required' in reply:
                code, message = 'app_password_required', f'{label} server requires an app password for the mailbox account.'
            elif 'web login required' in reply or 'log in via your web browser' in reply:
                code, message = 'web_login_required', f'{label} server requires you to sign into the mailbox provider in your own browser before connecting.'
            elif 'disabled' in reply or 'not enabled' in reply:
                code, message = 'access_disabled', f'{label} access is disabled for this mailbox. Check the mail provider or Workspace administrator settings.'
            elif 'too many' in reply or 'rate limit' in reply:
                code, message = 'rate_limited', f'{label} server is limiting connections or login attempts. Wait a moment before retrying.'
            elif isinstance(exc, imaplib.IMAP4.error) and not any(x in reply for x in ('authenticationfailed', 'authentication failed', 'invalid credentials', 'login failed', 'username and password not accepted')):
                code, message = 'login_rejected', 'IMAP server did not accept the login request. This can be a mailbox access policy or a temporary server restriction.'
            else:
                code, message = 'authentication', f'{label} server rejected the mailbox login. Confirm the mailbox address and its app password. For Google Workspace, also check whether mail-client access is allowed for this account.'
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                self.details['smtp_code'] = exc.smtp_code
        elif operation == 'inbox':
            code, message = 'inbox', 'IMAP login succeeded, but the Inbox could not be opened.'
        elif operation == 'cursor':
            code, message = 'mailbox_metadata', 'IMAP login succeeded, but the server did not provide usable Inbox message identifiers.'
        elif isinstance(exc, OSError):
            code, message = 'network', f'{label} network connection failed during {operation}. Check server and network access.'
        else:
            code, message = 'transport', f'Mail connection failed at {label} {operation}. The connection receipt identifies the failed step.'
        self.details['code'] = code
        super().__init__(message)


def _attempt(stage, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise MailTransportError(stage, exc) from None


def _password(account, protocol):
    password = account['password']
    if account[protocol + '_host'].lower().rstrip('.') in {'imap.gmail.com', 'smtp.gmail.com'}:
        # Google's app-password UI groups the sixteen letters with spaces.
        # Preserve other providers' passwords, where spaces can be significant.
        compact = re.sub(r'\s+', '', password)
        if re.fullmatch(r'[a-zA-Z]{16}', compact):
            return compact
    return password


def _close_imap(client):
    try:
        client.logout()
    except Exception:
        with contextlib.suppress(Exception):
            client.shutdown()


def _imap(account):
    client = _attempt('imap.connect', imaplib.IMAP4_SSL, account['imap_host'], int(account.get('imap_port', 993)),
                      ssl_context=ssl.create_default_context(), timeout=30)
    try:
        _attempt('imap.login', client.login, account['address'], _password(account, 'imap'))
        status, _ = _attempt('imap.inbox', client.select, 'INBOX', readonly=True)
        if status != 'OK':
            raise MailTransportError('imap.inbox', ValueError('INBOX could not be opened'))
        return client
    except Exception:
        _close_imap(client)
        raise


def _smtp(account):
    port = int(account.get('smtp_port', 587))
    if port == 465:
        client = _attempt('smtp.connect', smtplib.SMTP_SSL, account['smtp_host'], port, timeout=40, context=ssl.create_default_context())
    else:
        client = _attempt('smtp.connect', smtplib.SMTP, account['smtp_host'], port, timeout=40)
    try:
        if port != 465:
            _attempt('smtp.greeting', client.ehlo)
            _attempt('smtp.tls', client.starttls, context=ssl.create_default_context())
            _attempt('smtp.greeting', client.ehlo)
        _attempt('smtp.login', client.login, account['address'], _password(account, 'smtp'))
        return client
    except Exception:
        with contextlib.suppress(Exception):
            client.close()
        raise


def _cursor(client):
    return {'uidvalidity': int(client.response('UIDVALIDITY')[1][0]),
            'last_uid': int(client.response('UIDNEXT')[1][0]) - 1}


def probe(body):
    checks = {'imap_authenticated': False, 'inbox_opened': False, 'smtp_authenticated': False}
    try:
        client = _imap(body['account'])
        checks.update(imap_authenticated=True, inbox_opened=True)
        try:
            cursor = _attempt('imap.cursor', _cursor, client)
        finally:
            _close_imap(client)
        smtp = _smtp(body['account'])
        checks['smtp_authenticated'] = True
        with contextlib.suppress(Exception):
            smtp.close()
        return {'ok': True, 'cursor': cursor, 'checks': checks}
    except MailTransportError as exc:
        if exc.details['stage'] == 'imap.inbox':
            checks['imap_authenticated'] = True
        logging.getLogger(__name__).warning('Mail connection probe failed: %s', json.dumps({**exc.details, **checks}))
        return {'ok': False, 'message': str(exc), **exc.details, 'checks': checks}
    except Exception as exc:
        details = {'stage': 'probe', 'code': 'internal', 'error_type': type(exc).__name__}
        logging.getLogger(__name__).warning('Mail connection probe failed: %s', json.dumps({**details, **checks}))
        return {'ok': False, 'message': 'The mailbox connection test encountered an internal error. This is not a password rejection.', **details, 'checks': checks}


def poll(body):
    client = _imap(body['account'])
    try:
        current, previous = _cursor(client), body.get('cursor')
        if previous is None:
            return {'ok': True, 'cursor': current, 'messages': []}
        if previous['uidvalidity'] != current['uidvalidity']:
            # Server rebuilt the mailbox. Re-read by UID and deduplicate by
            # Message-ID in Calliope rather than silently skipping new mail.
            previous = {'uidvalidity': current['uidvalidity'], 'last_uid': 0}
        status, data = client.uid('search', None, 'UID', str(previous['last_uid'] + 1) + ':*')
        if status != 'OK':
            raise ValueError('Inbox search failed')
        ids = sorted(int(x) for x in (data[0] or b'').split() if int(x) > previous['last_uid'])[:15]
        messages, total = [], 0
        cursor = dict(previous)
        for number in ids:
            status, data = client.uid('fetch', str(number), '(BODY.PEEK[] RFC822.SIZE)')
            if status != 'OK':
                raise ValueError('Message fetch failed')
            raw = next((item[1] for item in data if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if raw is None:
                raise ValueError('Message fetch incomplete')
            if len(raw) > 18 * 1024 * 1024:
                # Persist a visible receipt for oversized mail; do not wedge
                # the inbox indefinitely or pretend its attachments arrived.
                msg = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
                from email.message import EmailMessage
                notice = EmailMessage()
                for key in ('From','To','Subject','Message-ID','In-Reply-To','References'):
                    if msg[key]:
                        notice[key] = msg[key]
                notice.set_content('This email exceeds the 18 MB import limit. Open the original mailbox to inspect its contents.')
                raw = notice.as_bytes()
            if messages and total + len(raw) > 18 * 1024 * 1024:
                break
            total += len(raw)
            messages.append({'uid': number, 'raw': base64.b64encode(raw).decode()})
            cursor['last_uid'] = number
        return {'ok': True, 'cursor': cursor, 'messages': messages}
    finally:
        _close_imap(client)


def send(body, *, spool=None):
    account, attempt = body['account'], str(body['attempt_id'])
    raw = base64.b64decode(body['mime'], validate=True)
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError('Message exceeds send limit')
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if msg['From'] != account['address'] or not msg['To'] or not msg['Message-ID']:
        raise ValueError('Invalid reviewed email envelope')
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(spool or Path(os.environ.get('HERMES_HOME', '/opt/data')) / '.calliope-control' / 'mail-spool.sqlite')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sends (id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT digest,state FROM sends WHERE id=?', (attempt,)).fetchone()
        if row:
            if row[0] != digest:
                raise ValueError('Attempt content changed')
            state = row[1] if row[1] != 'sending' else 'uncertain'
            return {'state': state, 'message': 'SMTP submission status is uncertain; check Sent mail before resending.' if state == 'uncertain' else None}
        db.execute('INSERT INTO sends VALUES(?,?,?,?)', (attempt, digest, 'sending', time.time()))
    # Persist first. A crash at any point after this cannot automatically resend.
    client, submitted = None, False
    try:
        client = _smtp(account)
        submitted = True
        refused = client.sendmail(account['address'], [str(msg['To'])], raw)
        state = 'failed' if refused else 'sent'
        message = 'Recipient was rejected by the mail server.' if refused else None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        state, message = 'failed', 'The mail server rejected this message.'
    except Exception:
        state = 'uncertain' if submitted else 'failed'
        message = ('SMTP submission status is uncertain; check Sent mail before resending.' if submitted
                   else 'Could not connect or authenticate with the mail server.')
    finally:
        if client:
            client.close()
    with sqlite3.connect(path) as db:
        db.execute('UPDATE sends SET state=?,updated=? WHERE id=?', (state, time.time(), attempt))
    return {'state': state, 'message': message, 'message_id': str(msg['Message-ID'])}
