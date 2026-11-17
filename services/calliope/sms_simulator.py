"""An authenticated virtual handset; only the SMS transport is simulated.

Uses the same durable inbox, login identity, turn admission, attachments and
Hermes reply formatting as Twilio. Outbound delivery stays in the local outbox.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID

import sms

BASE = '/api/calliope/sms/simulator'
PAGE = '/calliope/sms-simulator'
ASSETS = Path(__file__).with_name('calliope')


def require_enabled():
    if not sms.simulator_enabled():
        raise LookupError('SMS simulator is not enabled on this installation.')


def binding(conn, owner, ident):
    row = conn.execute("SELECT * FROM rvbbit.calliope_sms_bindings WHERE id=%s AND owner_email=%s AND transport='simulator' AND enabled",
                       (str(UUID(str(ident))), owner)).fetchone()
    if not row:
        raise LookupError('This simulated conversation is not available.')
    return row


def conversations(factory, owner):
    require_enabled()
    with factory() as conn:
        rows = conn.execute("SELECT b.id,b.created_at,x.session_id FROM rvbbit.calliope_sms_bindings b LEFT JOIN LATERAL "
            "(SELECT x.session_id FROM rvbbit.calliope_sms_sessions x JOIN rvbbit.calliope_sessions s ON s.id=x.session_id "
            "WHERE x.binding_id=b.id AND NOT s.archived ORDER BY x.created_at DESC LIMIT 1) x ON true "
            "WHERE b.owner_email=%s AND b.transport='simulator' AND b.enabled ORDER BY b.created_at DESC LIMIT 100", (owner,)).fetchall()
    return {'owner': owner, 'conversations': rows}


def create(factory, owner):
    require_enabled()
    ident = sms.uid()
    with factory() as conn:
        if not conn.execute('SELECT 1 FROM rvbbit.application_principals WHERE email=%s', (owner,)).fetchone():
            raise PermissionError('Sign in with a mapped Calliope login first.')
        conn.execute("INSERT INTO rvbbit.calliope_sms_bindings(id,phone_number,owner_email,configured_by,transport) VALUES(%s,%s,%s,%s,'simulator')",
                     (ident, 'sim:' + ident, owner, owner))
    return {'id': ident}


def enqueue(factory, config, owner, ident, values):
    require_enabled()
    from calliope import _decode_attachments
    if not isinstance(values, dict):
        raise ValueError('Expected a message object.')
    client_id = str(UUID(str(values.get('client_id', ''))))
    body = str(values.get('message') or '').strip()
    if len(body) > 16000:
        raise ValueError('Keep messages under 16,000 characters.')
    decoded = _decode_attachments(values.get('attachments', []), config)
    if any(d['mime'] not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'} or d.get('annotation') for d in decoded):
        raise ValueError('Attach JPEG, PNG, WebP or GIF images.')
    media = [{'name': d['name'], 'data_url': d['data_url']} for d in decoded]
    if not body and not media:
        raise ValueError('Type a message or attach a photo.')
    # A client-generated UUID survives a lost HTTP response. A retry cannot
    # duplicate a turn or quietly substitute a different message/recipient.
    with factory() as conn, conn.transaction():
        b = binding(conn, owner, ident)
        conn.execute('INSERT INTO rvbbit.calliope_sms_inbox(id,message_sid,binding_id,body,media) VALUES(%s,%s,%s,%s,%s::jsonb) '
            'ON CONFLICT(message_sid) DO NOTHING', (client_id, 'sim:' + client_id, b['id'], body, sms.dump(media)))
        saved = conn.execute('SELECT * FROM rvbbit.calliope_sms_inbox WHERE message_sid=%s', ('sim:' + client_id,)).fetchone()
        if str(saved['binding_id']) != str(b['id']) or saved['body'] != body or saved['media'] != media:
            raise ValueError('This message ID was already used for a different message.')
    return {'id': client_id, 'state': saved['state']}


def read(factory, owner, ident):
    require_enabled()
    with factory() as conn:
        b = binding(conn, owner, ident)
        session = conn.execute('SELECT s.id,s.archived FROM rvbbit.calliope_sms_sessions x JOIN rvbbit.calliope_sessions s ON s.id=x.session_id '
            'WHERE x.binding_id=%s ORDER BY x.created_at DESC LIMIT 1', (b['id'],)).fetchone()
        # Select only media counts. Image bytes have their own authenticated URL
        # and are not downloaded again on every transcript poll.
        incoming = conn.execute('SELECT id,body,state,last_error,created_at,jsonb_array_length(media) AS image_count '
            'FROM rvbbit.calliope_sms_inbox WHERE binding_id=%s ORDER BY created_at DESC LIMIT 200', (b['id'],)).fetchall()
        outgoing = conn.execute('SELECT o.id,o.body,o.state,o.created_at,o.turn_id,o.chunk_index FROM rvbbit.calliope_sms_outbox o '
            'JOIN rvbbit.calliope_sms_replies r ON r.turn_id=o.turn_id WHERE r.binding_id=%s ORDER BY o.created_at DESC,o.chunk_index DESC LIMIT 200', (b['id'],)).fetchall()
        busy = conn.execute("SELECT 1 FROM rvbbit.calliope_turns WHERE session_id=%s AND status='running' LIMIT 1", (session['id'],)).fetchone() if session else None
    messages = [{**m, 'direction': 'outgoing', 'images': [f'{BASE}/conversations/{ident}/images/{m["id"]}/{i}' for i in range(m['image_count'])]} for m in incoming]
    messages += [{**m, 'direction': 'incoming', 'images': []} for m in outgoing if m['state'] == 'sent']
    messages.sort(key=lambda m: (m['created_at'], m.get('chunk_index', -1), str(m['id'])))
    return {'id': ident, 'owner': owner, 'session_id': session['id'] if session else None,
            'busy': bool(busy), 'queued': sum(m['state'] in {'queued', 'delivering'} for m in incoming), 'messages': messages}


def image(factory, owner, ident, message_id, index):
    require_enabled()
    with factory() as conn:
        b = binding(conn, owner, ident)
        row = conn.execute('SELECT media->%s AS image FROM rvbbit.calliope_sms_inbox WHERE id=%s AND binding_id=%s',
                           (int(index), str(UUID(message_id)), b['id'])).fetchone()
    if not row or not row['image']:
        raise LookupError('Image unavailable.')
    head, encoded = row['image']['data_url'].split(',', 1)
    return base64.b64decode(encoded), head[5:].split(';')[0]


def register(mcp, factory, api_owner, response, config):
    from starlette.responses import HTMLResponse, Response, RedirectResponse
    if config is None:
        from calliope import CalliopeConfig
        config = CalliopeConfig.from_env()

    async def handle(request, operation, *, write=False):
        owner, err = api_owner(request)
        if err:
            return err
        try:
            require_enabled()
            if write:
                # This is a logged-in browser transport, never a public webhook.
                if request.headers.get('x-calliope-simulator') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
                    raise PermissionError('Use the signed-in SMS simulator.')
                raw = bytearray()
                async for part in request.stream():
                    raw.extend(part)
                    if len(raw) > config.max_image_bytes * 4 // 3 + 128 * 1024:
                        return response({'error': {'message': 'Message images exceed the upload limit.'}}, 413)
                values = json.loads(raw)
                result = await asyncio.to_thread(operation, owner, values)
            else:
                result = await asyncio.to_thread(operation, owner)
            result = result if isinstance(result, Response) else response(result)
            result.headers['cache-control'] = 'private, no-store'
            return result
        except (ValueError, TypeError) as exc:
            return response({'error': {'message': str(exc)}}, 400)
        except PermissionError as exc:
            return response({'error': {'message': str(exc)}}, 403)
        except LookupError as exc:
            return response({'error': {'message': str(exc)}}, 404)

    @mcp.custom_route(PAGE, methods=['GET'])
    async def page(request):
        owner, err = api_owner(request)
        if err:
            return RedirectResponse('/login?next=' + PAGE) if err.status_code == 401 else err
        if not sms.simulator_enabled():
            return Response(status_code=404)
        # This small standalone bundle has its own content version.
        version = hashlib.sha256(b''.join((ASSETS / ('sms-simulator.' + e)).read_bytes() for e in ('html', 'css', 'js'))).hexdigest()[:16]
        return HTMLResponse((ASSETS / 'sms-simulator.html').read_text().replace('__ASSET_VERSION__', version),
                            headers={'cache-control': 'private, no-store'})

    for ext, mime in [('js', 'text/javascript'), ('css', 'text/css')]:
        async def asset(request, ext=ext, mime=mime):
            return await handle(request, lambda owner: Response((ASSETS / ('sms-simulator.' + ext)).read_bytes(), media_type=mime))
        mcp.custom_route(PAGE + '.' + ext, methods=['GET'])(asset)

    @mcp.custom_route(BASE, methods=['GET'])
    async def list_conversations(request):
        return await handle(request, lambda owner: {**conversations(factory, owner), 'max_image_bytes': config.max_image_bytes})

    @mcp.custom_route(BASE + '/conversations', methods=['POST'])
    async def start(request):
        return await handle(request, lambda owner, values: create(factory, owner), write=True)

    @mcp.custom_route(BASE + '/conversations/{binding_id}', methods=['GET'])
    async def transcript(request):
        return await handle(request, lambda owner: read(factory, owner, request.path_params['binding_id']))

    @mcp.custom_route(BASE + '/conversations/{binding_id}/messages', methods=['POST'])
    async def submit(request):
        return await handle(request, lambda owner, values: enqueue(factory, config, owner, request.path_params['binding_id'], values), write=True)

    @mcp.custom_route(BASE + '/conversations/{binding_id}/images/{message_id}/{index:int}', methods=['GET'])
    async def media(request):
        def load(owner):
            data, mime = image(factory, owner, request.path_params['binding_id'], request.path_params['message_id'], request.path_params['index'])
            return Response(data, media_type=mime, headers={'x-content-type-options': 'nosniff'})
        return await handle(request, load)
