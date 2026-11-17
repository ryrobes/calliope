"""Native browser login and share links. Uses the existing account/session wall.

The browser sees a public request id and a matching code, never the device secret.
Only the initiating client can collect a one-use session. Nothing secret travels
through an OS URL handler. Restarting the server expires unfinished sign-ins.
"""
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

import jwt
from starlette.responses import JSONResponse, RedirectResponse, Response
import auth

TTL = 600
HEADERS = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
           'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'}


class Pending:
    def __init__(self, clock=time.time):
        self.clock, self.rows, self.lock = clock, {}, threading.Lock()

    def start(self, ip, label):
        with self.lock:
            now = self.clock()
            self.rows = {k: v for k, v in self.rows.items() if v['expires'] > now}
            if len(self.rows) >= 2000 or sum(v['ip'] == ip for v in self.rows.values()) >= 12:
                raise ValueError('Too many sign-in requests. Try again in a few minutes.')
            request_id, secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
            code = '-'.join(secrets.token_hex(2).upper() for _ in range(2))
            row = {'secret_hash': hashlib.sha256(secret.encode()).hexdigest(),
                   'ip': ip, 'label': label, 'code': code, 'expires': now + TTL,
                   'session': None, 'status': 'pending'}
            self.rows[request_id] = row
            return {'request_id': request_id, 'device_secret': secret, 'code': code,
                    'expires_at': int(row['expires']), 'poll_seconds': 2,
                    'verification_path': '/auth/native/approve?' + urlencode({'request': request_id})}

    def get(self, request_id):
        row = self.rows.get(request_id)
        if not row or row['expires'] <= self.clock():
            raise LookupError('Sign-in expired. Start again in Calliope.')
        return row

    def approve(self, request_id, session, approved):
        with self.lock:
            row = self.get(request_id)
            if row['status'] != 'pending':
                raise LookupError('This sign-in has already been answered.')
            row['session'] = dict(session) if approved else None
            row['status'] = 'approved' if approved else 'denied'

    def poll(self, request_id, secret, cancel=False):
        with self.lock:
            row = self.get(request_id)
            if not hmac.compare_digest(row['secret_hash'], hashlib.sha256(secret.encode()).hexdigest()):
                raise LookupError('Sign-in expired. Start again in Calliope.')
            status = 'cancelled' if cancel else row['status']
            result = {'status': status}
            if status == 'approved':
                result['session'] = row['session']
            if status != 'pending':
                del self.rows[request_id]
            return result


def destination(params):
    """The handoff carries navigation only, never access grants or SQL."""
    result = {}
    for key in ('app', 'record', 'id'):
        value = params.get(key, '')
        if value:
            if not isinstance(value, str) or len(value) > (512 if key == 'id' else 160):
                raise ValueError('Invalid app link')
            if key != 'id' and not re.fullmatch(r'[A-Za-z0-9_-]+', value):
                raise ValueError('Invalid app link')
            result[key] = value
    if bool(result.get('record')) != bool(result.get('id')) or ('record' in result and 'app' not in result):
        raise ValueError('A record link needs an app, record type and id')
    return result


def register(mcp, provider):
    pending = Pending()

    def page(body, status=200):
        response = auth._page(body, status)
        response.headers.update(HEADERS)
        # Chrome's form POST uses Origin:null with no-referrer. Keep same-origin
        # provenance for CSRF checks while sending no referrer off this instance.
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    def reply(body, status=200):
        return JSONResponse(body, status_code=status, headers=HEADERS)

    def same_origin(request):
        return request.headers.get('sec-fetch-site') != 'cross-site' and request.headers.get('origin', '') in (
            '', provider.public, str(request.base_url).rstrip('/'),
            os.environ.get('WAREHOUSE_NATIVE_PUBLIC_URL', '').rstrip('/'))

    async def body(request):
        if not same_origin(request) or request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise ValueError('Use the Calliope app to start sign-in.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise ValueError('Request is too large')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Expected an object')
        return value

    def csrf(request_id, request):
        cookie_hash = hashlib.sha256(request.cookies.get(auth.SESSION_COOKIE, '').encode()).hexdigest()
        return jwt.encode({'typ': 'native-approval', 'request': request_id,
                           'cookie': cookie_hash, 'exp': int(time.time()) + TTL},
                          auth.JWT_SECRET, algorithm=auth.JWT_ALG)

    @mcp.custom_route('/auth/native/start', methods=['POST'])
    async def start(request):
        try:
            value = await body(request)
            label = value.get('device', 'Calliope app')
            if label not in ('Calliope Android', 'Calliope iOS', 'Calliope Desktop', 'Calliope app'):
                raise ValueError('Unknown client')
            return reply(pending.start(auth._client_ip(request), label))
        except ValueError as exc:
            return reply({'error': {'message': str(exc)}}, 400)

    @mcp.custom_route('/auth/native/poll', methods=['POST'])
    async def poll(request):
        try:
            value = await body(request)
            if not all(isinstance(value.get(k), str) for k in ('request_id', 'device_secret')):
                raise ValueError('Invalid sign-in request')
            result = pending.poll(value['request_id'], value['device_secret'], value.get('cancel') is True)
            session = result.pop('session', None)
            if session:
                if not auth._account_valid(session['identity'], session['account_version']):
                    return reply({'error': {'message': 'Account access changed. Sign in again.'}}, 401)
                cookie = Response()
                auth.set_session(cookie, session['sub'], True, identity=session['identity'],
                    mapped=session['mapped'], via=session['via'], name=session['name'],
                    picture=session['picture'], auth_time=session['auth_time'],
                    account_version=session['account_version'])
                # Same session format/expiry/revocation as password sign-in.
                from http.cookies import SimpleCookie
                parsed = SimpleCookie(cookie.headers['set-cookie'])
                result.update(session_cookie=parsed[auth.SESSION_COOKIE].value, email=session['identity'])
            return reply(result)
        except (ValueError, LookupError) as exc:
            return reply({'error': {'message': str(exc)}}, 400)

    @mcp.custom_route('/auth/native/approve', methods=['GET', 'POST'])
    async def approve(request):
        request_id = request.query_params.get('request', '')
        try:
            row = pending.get(request_id)
        except LookupError as exc:
            return page('<h1>Sign-in expired</h1><p>' + html.escape(str(exc)) + '</p>', 410)
        session = auth.read_session_full(request)
        if not session:
            return RedirectResponse('/login?' + urlencode({'next': '/auth/native/approve?' + urlencode({'request': request_id})}),
                                    status_code=303, headers=HEADERS)
        if session['via'] not in ('password', 'google', 'pg') or not session['mapped']:
            return page('<h1>Sign in with your own account to connect a device.</h1>', 403)
        if request.method == 'POST':
            if not same_origin(request):
                return page('<h1>Reopen sign-in from Calliope.</h1>', 403)
            form = await request.form()
            try:
                token = jwt.decode(str(form.get('csrf', '')), auth.JWT_SECRET, algorithms=[auth.JWT_ALG])
                expected = jwt.decode(csrf(request_id, request), auth.JWT_SECRET, algorithms=[auth.JWT_ALG])
                if any(token.get(k) != expected[k] for k in ('typ', 'request', 'cookie')):
                    raise ValueError('Invalid sign-in confirmation')
                pending.approve(request_id, session, form.get('choice') == 'approve')
            except (ValueError, LookupError, jwt.PyJWTError):
                return page('<h1>This confirmation expired. Reopen sign-in from Calliope.</h1>', 403)
            return page('<h1>' + ('Connected' if form.get('choice') == 'approve' else 'Sign-in cancelled') +
                        '</h1><p>Return to Calliope to continue.</p><a class="btn" href="calliope://auth/complete">Open Calliope</a>')
        return page('<h1>Connect ' + html.escape(row['label']) + '</h1><p class="sub">Signed in as <strong>' +
                    html.escape(session['identity']) + '</strong></p><p>Confirm that this code matches the one in your app:</p>' +
                    '<p style="font-size:26px;letter-spacing:.1em"><strong>' + row['code'] + '</strong></p>' +
                    '<form method="post"><input type="hidden" name="csrf" value="' + csrf(request_id, request) +
                    '"><button name="choice" value="approve">Connect this device</button>' +
                    '<button name="choice" value="deny">Cancel</button></form>')

    def links(request):
        params = destination(request.query_params)
        origin = os.environ.get('WAREHOUSE_NATIVE_PUBLIC_URL', '').rstrip('/') or str(request.base_url).rstrip('/')
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('A valid HTTPS instance address is required')
        query = urlencode(params)
        return params, origin, origin + '/connect' + ('?' + query if query else ''), 'calliope://open?' + urlencode({'server': origin, **params})

    @mcp.custom_route('/connect', methods=['GET'])
    async def connect(request):
        try:
            params, origin, share, app_url = links(request)
        except ValueError as exc:
            return page('<h1>' + html.escape(str(exc)) + '</h1>', 400)
        fallback = '/apps/' + params['app'] if 'app' in params else '/gallery'
        if 'record' in params:
            fallback += '#record=' + quote(json.dumps(
                {'slug': params['app'], 'record': params['record'], 'id': params['id']}, separators=(',', ':')))
        return page('<h1>Your company apps, with Callie</h1><p class="sub">Connect the Calliope app to <strong>' +
            html.escape(origin) + '</strong>.</p><p>Sign in with your existing company account. Your apps follow your team access.</p>' +
            '<p><a class="btn" href="' + html.escape(app_url, quote=True) + '">Open in Calliope</a></p>' +
            '<p><a href="' + html.escape(fallback, quote=True) + '">Continue in the browser</a></p>' +
            '<img alt="Scan to connect your phone" width="220" height="220" src="/connect/qr?' +
            html.escape(urlencode(params), quote=True) + '"><p>Scan on your phone, or copy this link:</p>' +
            '<input aria-label="Share link" readonly value="' + html.escape(share, quote=True) +
            '"><p class="sub">Requires the installed Calliope client. This link does not grant access.</p>')

    @mcp.custom_route('/connect/qr', methods=['GET'])
    async def qr(request):
        try:
            _, _, share, _ = links(request)
            import qrcode
            image = qrcode.make(share)
            output = io.BytesIO()
            image.save(output, format='PNG')
            return Response(output.getvalue(), media_type='image/png', headers=HEADERS)
        except ValueError as exc:
            return reply({'error': {'message': str(exc)}}, 400)
