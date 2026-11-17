"""Inert previews of the published app contract, using its actual materializer.

The caller owns authorization and supplies a governed query runner for reads.
This module deliberately has no action executor, connection or file upload API.
Server-read previews resolve attachment names through the same authorized host.
"""
import copy
import hashlib
import json
import time

import ui_apps
import ui_composition

VIEWPORTS = {'desktop': (1440, 1000), 'tablet': (950, 950), 'phone': (390, 844)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def viewport(name='desktop', width=None, height=None):
    if name not in VIEWPORTS:
        raise ValueError('Viewport must be desktop, tablet or phone')
    w, h = VIEWPORTS[name]
    w, h = w if width is None else width, h if height is None else height
    if type(w) is not int or type(h) is not int or not 390 <= w <= 1920 or not 400 <= h <= 1200:
        raise ValueError('Viewport bounds: width 390..1920, height 400..1200')
    return {'preset': name, 'width': w, 'height': h}


def snapshot(document, *, mode='supplied', data=None, state=None, event=None,
             context=None, expected_hash=None, query_runner=None, actor=None):
    """Materialize one revision. Supplied data is a complete model, not SQL rows.

    Reads use exactly the publication binding/order/shape rules. Events resolve
    their actual input payload but never execute commands or fabricate receipts.
    """
    doc = ui_apps.definition({'app.json': json.dumps(document, allow_nan=False)})
    source_hash = digest(doc)
    if expected_hash is not None and expected_hash != source_hash:
        raise ValueError('Preview revision changed; reload before interacting')
    if mode not in ('supplied', 'server-read'):
        raise ValueError('Preview mode must be supplied or server-read')
    if mode == 'server-read' and query_runner is None:
        raise ValueError('Server-read previews require an authorized query runner')
    if data is not None and (not isinstance(data, dict) or set(data) != set(doc['data'])):
        raise ValueError('Supplied data must match the definition model keys')
    model = copy.deepcopy(doc['data'] if data is None else data)
    if state is not None:
        if not isinstance(state, dict) or set(state) != set(doc['data']['state']):
            raise ValueError('State must match the definition state keys')
        model['state'] = copy.deepcopy(state)
    resets, observed = [], None
    if event:
        spec = doc.get('events', {}).get(event)
        if spec is None:
            raise ValueError('Unknown preview event: ' + str(event))
        scope = {**model, 'context': context or {},
                 'request': {'key': '00000000-0000-0000-0000-000000000000'}}
        observed = {'name': event, 'context': context or {}, 'simulated': True}
        if spec.get('action'):
            observed.update(action=spec['action'], inputs=ui_apps.resolve(spec.get('inputs', {}), scope))
            # A save did not happen. Do not clear its draft or apply post-save
            # state that depends on a receipt which does not exist.
        else:
            for path, value in spec.get('set', {}).items():
                ui_apps.put(model, path, ui_apps.resolve(value, scope))
            resets = spec.get('reset', [])
    queries = {}
    if mode == 'server-read':
        for name, dependencies in ui_apps.query_order(doc.get('queries', {})):
            query = doc['queries'][name]
            if any(queries[d].get('error') for d in dependencies):
                queries[name] = {'path': query['path'], 'error': {'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'An upstream SQL binding failed'}}
                continue
            result = query_runner(ui_apps.bound_sql(query, model, actor),
                                  limit=query.get('limit', 500), _max_rows=1000,
                                  _statement_timeout_ms=query.get('timeoutMs', 5000))
            # In-process captures bypass the published HTTP JSON response. Apply
            # that same wire representation before validation and hashing; SQL
            # numeric/date/UUID values otherwise fail before rendering starts.
            result = json.loads(json.dumps(result, default=str, allow_nan=False))
            queries[name] = {k: result[k] for k in ('error', 'row_count', 'elapsed_ms', 'truncated') if k in result}
            queries[name]['path'] = query['path']
            if result.get('error'):
                continue
            rows, shape = result['rows'], query.get('shape', 'rows')
            value = rows if shape == 'rows' else (rows[0] if rows else None)
            if shape == 'value':
                value = next(iter(rows[0].values())) if rows else None
            if value is not None:
                ui_apps.put(model, query['path'], value)
            ui_apps.hydrate(doc, model, state is None, resets, query['path'])
    else:
        queries = {name: {'path': q['path'], 'source': 'supplied', 'executed': False}
                   for name, q in doc.get('queries', {}).items()}
        ui_apps.hydrate(doc, model, state is None, resets)
    components = ui_composition.expand(doc, model)
    ui_apps.validate({**doc, 'data': model, 'components': components,
                      'fragments': {}, 'repeat': {}, 'switch': {}})
    errors = {name: q['error'] for name, q in queries.items() if q.get('error')}
    identity = {'schema': 'calliope.preview.v1', 'definition_sha256': source_hash,
                'data_sha256': digest(model), 'mode': mode, 'actions': 'simulated',
                'observed_at': time.time(), 'ready': not errors, 'errors': errors}
    surface = 'published-app'
    return {'title': doc.get('title', 'App preview'), 'version': None,
            'preview': identity, 'preview_event': observed, 'receipt': None,
            'queries': queries, 'watch': doc.get('watch', []), 'forms': doc.get('forms', []),
            'navigation': ui_apps.navigation(doc), 'resets': resets,
            'messages': [
                {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': ui_apps.CATALOG}},
                {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': components}},
                {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'value': model}}]}


def studio_snapshot(document, **options):
    value = snapshot(document, **options)
    for message in value['messages']:
        message[next(k for k in message if k != 'version')]['surfaceId'] = 'transaction-desk'
    model = value['messages'][2]['updateDataModel']['value']
    return {**value, 'view': 'studio', 'selected': 'preview', 'revision': 0,
            'draft': model.get('draft', {}), 'previewDefinition': document,
            'notice': 'App preview · supplied data · actions are simulated'}


def request_snapshot(server, document, body, subject):
    """Shared HTTP/tool boundary. Actor identity is never supplied in JSON."""
    if not isinstance(body, dict) or set(body) - {'mode', 'data', 'state', 'event', 'context', 'expected_hash'}:
        raise ValueError('Use mode, data, state, event, context and expected_hash')
    token = server._SESSION_SUB.set(subject)
    try:
        result = snapshot(document, **body, query_runner=server.tool_run_sql, actor=subject)
        if body.get('mode') == 'server-read':
            import ui_app_files
            nodes = result['messages'][1]['updateComponents']['components']
            model = result['messages'][2]['updateDataModel']['value']
            ui_app_files.references(server, subject, nodes, model, forms=document.get('forms', []))
            result['preview']['data_sha256'] = digest(model)
        return result
    finally:
        server._SESSION_SUB.reset(token)


def register(m, server, session_actor, respond):
    import asyncio
    import contextvars
    import os

    @m.custom_route('/api/calliope/ui-app-preview', methods=['POST'])
    async def preview(request):
        actor, session, error = session_actor(request)
        if error:
            return error
        origin = request.headers.get('origin')
        allowed = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
        if (request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in allowed)
                or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
            return respond({'error': {'code': 'INVALID_ORIGIN'}}, 403)
        raw = await request.body()
        if len(raw) > 2_000_000:
            return respond({'error': {'code': 'TOO_LARGE'}}, 413)
        def run():
            try:
                body = json.loads(raw)
                document = body.pop('document')
                return respond(request_snapshot(server, document, body, session.get('sub') or actor))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return respond({'error': {'code': 'INVALID_PREVIEW', 'message': str(exc)}}, 400)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)
