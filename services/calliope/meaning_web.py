"""Web Meaning workspace and authorized, unsent chat handoffs.

The native and web maps use meaning_graph. This module creates no ontology tables
and performs no definition promotion; the chosen intent becomes a chat draft.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from html import escape
from pathlib import Path
from urllib.parse import urlencode, quote
import uuid

from app_actions import ActionError
import meaning_graph as graph

ASSETS = Path(__file__).parent / 'calliope'
STATIC = {'meaning-pivot.js': 'text/javascript', 'meaning.css': 'text/css', 'meaning.js': 'text/javascript', 'meaning-layout.mjs': 'text/javascript', 'meaning-present.mjs': 'text/javascript', 'meaning-explore.mjs': 'text/javascript', 'meaning-desk.js': 'text/javascript', 'meaning-desk.css': 'text/css', 'meaning-data.mjs': 'text/javascript',
          'meaning-diagram.mjs': 'text/javascript', 'meaning-map.css': 'text/css', 'meaning-elk.js': 'text/javascript'}


def prompt_for(intent, node):
    title = node['title']
    if intent == 'promote':
        return (f'I would like to promote “{title}” into an agreed business definition. '
                'Review the pinned Meaning object, its exact identity, current status, definition and evidence. '
                'Explain what promotion means for this kind of object and resolve any missing scope or caveats with me. '
                'Use the existing Meaning/metric registry when we establish the right definition; preserve its provenance.')
    if intent == 'disagree':
        return (f'I disagree with the current interpretation of “{title}”. '
                'The object and its evidence are pinned on the Stage. Help me identify what is wrong and establish the correction. '
                'Ask what I mean before inferring a replacement; preserve the recorded evidence and distinguish a correction from a new definition.')
    return f'Help me understand “{title}”, using the Meaning object and evidence pinned on the Stage. Explain its scope, dependencies and anything uncertain.'


def handoff(server, actor, body):
    import calliope
    if not isinstance(body, dict) or set(body) - {'reference', 'intent', 'request_key', 'edge'}:
        raise ValueError('Use reference, intent, request_key and optional edge')
    intent = body.get('intent', 'ask')
    if intent not in ('ask', 'promote', 'disagree'):
        raise ValueError('Unknown Meaning discussion intent')
    key = str(uuid.UUID(str(body.get('request_key', ''))))
    reference = graph.reference(body.get('reference'))
    # Re-resolve authoritative metadata and current authorization. Never accept
    # browser-supplied definitions, SQL, titles, samples or evidence as trusted.
    packet = graph.inspect_object(server, actor, reference)
    node = packet['node']
    edge_id = body.get('edge')
    if edge_id:
        graph.bounded(edge_id, 700)
        neighborhood = graph.graph(server, actor, reference['area'], 40, reference['node'])
        edge = next((e for e in neighborhood['edges'] if e['id'] == edge_id and node['id'] in (e['from'], e['to'])), None)
        if not edge:
            raise ActionError('NOT_FOUND', 'That relationship is no longer available', 404)
        packet['relationship_focus'] = edge
    packet['intent'] = intent
    prompt = prompt_for(intent, node)
    fingerprint = json.dumps({'reference': reference, 'intent': intent, 'edge': edge_id}, sort_keys=True)
    config = calliope.CalliopeConfig.from_env()
    session = None
    # Serialize retries for this actor and request. The durable surface records
    # the completed handoff; a dropped HTTP response cannot create a second chat.
    with server._conn() as lock:
        with lock.transaction():
            lock.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('meaning-chat:' + actor + ':' + key,))
            old = lock.execute(
                "SELECT f.id,f.session_id,f.source FROM rvbbit.calliope_surfaces f "
                "JOIN rvbbit.calliope_sessions s ON s.id=f.session_id "
                "WHERE s.owner_email=%s AND f.source->>'origin'='meaning_map' "
                "AND f.source->>'request_key'=%s LIMIT 1", (actor, key)).fetchone()
            if old:
                if old['source'].get('fingerprint') != fingerprint:
                    raise ActionError('REQUEST_CONFLICT', 'This request key belongs to another discussion', 409)
                return handoff_result(str(old['session_id']), str(old['id']), prompt, reused=True)
            try:
                session = calliope._create_session_record_sync(config, server._conn, actor, f'Meaning · {node["title"]}')
                sid, tid = str(session['id']), str(uuid.uuid4())
                with server._conn() as conn:
                    conn.execute(
                        "INSERT INTO rvbbit.calliope_turns "
                        "(id,session_id,ordinal,user_message,assistant_message,status,completed_at,turn_kind) "
                        "VALUES (%s::uuid,%s::uuid,1,%s,%s,'complete',now(),'evidence_search')",
                        (tid, sid, f'Meaning · {node["title"]}',
                         'The selected Meaning object and its recorded evidence are on the Stage. Your discussion prompt is ready to edit; no definition has changed.'))
                surface = calliope._insert_surfaces(server._conn, sid, tid, [{
                    'kind': 'evidence', 'title': node['title'], 'tool_name': 'meaning_map_handoff',
                    'tool_call_id': 'meaning-map:' + key, 'lineage_key': 'meaning:' + node['id'],
                    'payload': {'mode': 'meaning_object', 'meaning_context': packet,
                                'query': node['title'], 'count': 1, 'items': []},
                    'source': {'origin': 'meaning_map', 'reference': reference, 'intent': intent,
                               'request_key': key, 'fingerprint': fingerprint},
                }])[0]
                with server._conn() as conn:
                    conn.execute('UPDATE rvbbit.calliope_turns SET selected_surface_id=%s::uuid WHERE id=%s::uuid', (surface['id'], tid))
                return handoff_result(sid, surface['id'], prompt)
            except Exception:
                if session:
                    with server._conn() as conn:
                        conn.execute('DELETE FROM rvbbit.calliope_sessions WHERE id=%s::uuid AND owner_email=%s', (session['id'], actor))
                    try:
                        calliope._hermes_json_sync(config, 'DELETE', '/api/sessions/' + quote(session['hermes_session_id'], safe=''))
                    except Exception:
                        pass
                raise


def handoff_result(session, surface, prompt, reused=False):
    return {'session': {'id': session}, 'surface': {'id': surface}, 'reused': reused,
            'url': '/calliope?' + urlencode({'session': session, 'surface': surface, 'prompt': prompt, 'view': 'zen', 'meaning_pin': surface})}


_bundle_cache = {}


def _bundle():
    """Asset version and gzip bodies, recomputed only when a file changes."""
    import gzip
    stamp = tuple((f, (ASSETS / f).stat().st_mtime_ns) for f in ['meaning.html', *STATIC])
    if _bundle_cache.get('stamp') != stamp:
        version = hashlib.sha256(b''.join((ASSETS / f).read_bytes() for f in ['meaning.html', *STATIC])).hexdigest()[:16]
        _bundle_cache.clear()
        _bundle_cache.update(stamp=stamp, version=version, gzip={f: gzip.compress((ASSETS / f).read_bytes(), 6) for f in STATIC})
    return _bundle_cache


def register(m, server, session_actor, dispatch):
    import meaning_data
    import auth
    import warehouse_theme
    import calliope_dreams
    from starlette.responses import HTMLResponse, RedirectResponse, Response

    import business_logic_web
    business_logic_web.register(m, server._conn, session_actor, server=server)

    @m.custom_route('/meaning', methods=['GET'])
    async def page(request):
        actor, session, error = session_actor(request)
        if error:
            return RedirectResponse('/gallery' if session else '/login?' + urlencode({'next': '/meaning'}), status_code=302)
        version = (await asyncio.to_thread(_bundle))['version']
        html = (ASSETS / 'meaning.html').read_text().replace('__MEANING_VERSION__', version)
        html = html.replace('__MEANING_OWNER__', escape(actor, quote=True))
        can_administer = await asyncio.to_thread(calliope_dreams.is_company_admin, server._conn, actor)
        html = html.replace('__CALLIOPE_WORKSPACE_NAV__', warehouse_theme.workspace_navigation('meaning', can_administer=can_administer))
        html = html.replace('__CALLIOPE_ACCOUNT__', warehouse_theme.account_control(session, workspace_menu=True, can_administer=can_administer))
        html = html.replace('__CALLIOPE_BACKGROUND__', auth.background_layer(0.18, 'linear-gradient(transparent,rgba(0,0,0,.18))', scene_key=actor))
        return HTMLResponse(html, headers={
            'cache-control': 'private, no-store', 'vary': 'Cookie, Authorization',
            'x-content-type-options': 'nosniff',
            'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"})

    @m.custom_route('/meaning/{asset}', methods=['GET'])
    async def asset(request):
        _, _, error = session_actor(request)
        if error:
            return error
        name = request.path_params['asset']
        if name not in STATIC:
            return Response(status_code=404)
        bundle = await asyncio.to_thread(_bundle)
        # A versioned URL names exact bytes; the layout engine alone is 1.6 MB.
        headers = {'cache-control': 'private, max-age=31536000, immutable' if request.query_params.get('v') == bundle['version'] else 'private, no-cache',
                   'x-content-type-options': 'nosniff', 'vary': 'Accept-Encoding'}
        if 'gzip' in request.headers.get('accept-encoding', ''):
            return Response(bundle['gzip'][name], media_type=STATIC[name], headers={**headers, 'content-encoding': 'gzip'})
        return Response((ASSETS / name).read_bytes(), media_type=STATIC[name], headers=headers)

    @m.custom_route('/api/calliope/meaning/discuss', methods=['POST'])
    async def discuss(request):
        return await dispatch(request, lambda actor, body: handoff(server, actor, body), True)

    @m.custom_route('/api/calliope/meaning/data', methods=['POST'])
    async def data(request):
        return await dispatch(request, lambda actor, body: meaning_data.explore(server, actor, body), True)
