"""Portable app records and personal live pins. Definitions and SQL stay in apps.

A reference follows the latest published record contract. Its identity never
contains a form draft, SQL, a component subtree, or a client-selected URL.
"""
import copy
import json
import re
import threading
import uuid

from app_actions import ActionError

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.ui_record_pins (
 id uuid PRIMARY KEY, actor text NOT NULL,
 artifact_id bigint NOT NULL REFERENCES rvbbit.dashboards(id),
 record_type text NOT NULL, record_id jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(actor,artifact_id,record_type,record_id)
);
REVOKE ALL ON rvbbit.ui_record_pins FROM PUBLIC;
"""
_lock, _ready = threading.Lock(), set()
SUMMARY_COMPONENTS = {'CStack', 'CSurface', 'CText', 'CBadge', 'CMeter', 'CSection', 'CDivider'}


def prepare(server):
    with _lock:
        if server._conn not in _ready:
            with server._conn() as conn:
                conn.execute(DDL)
                conn.commit()
            _ready.add(server._conn)


def reference(value, slug=None):
    if not isinstance(value, dict) or set(value) != {'slug', 'record', 'id'}:
        raise ValueError('Record references use slug, record and id')
    for key in ('slug', 'record'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', value[key]):
            raise ValueError('Invalid record reference')
    if slug is not None and value['slug'] != slug:
        raise ValueError('Record belongs to another app')
    ident = value['id']
    if type(ident) not in (str, int) or not str(ident) or len(str(ident)) > 512:
        raise ValueError('Record id must be a bounded string or integer')
    return {k: value[k] for k in ('slug', 'record', 'id')}


def dependencies(doc, names):
    from ui_apps import query_order
    graph = dict(query_order(doc.get('queries', {})))
    selected = set(names)
    if not selected or not selected <= graph.keys():
        raise ValueError('Summary queries must name published SQL bindings')
    todo = list(selected)
    while todo:
        for dep in graph[todo.pop()] - selected:
            selected.add(dep)
            todo.append(dep)
    return selected


def validate(doc):
    from ui_apps import navigation, pointer, resolve, validate as validate_app
    records = doc.get('records', {})
    if not isinstance(records, dict) or len(records) > 16:
        raise ValueError('Expected at most 16 record declarations')
    for name, record in records.items():
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', name) or not isinstance(record, dict) or set(record) != {'id', 'title', 'when', 'open', 'summary'}:
            raise ValueError('Records declare id, title, when, open and summary')
        key = record['id']
        if not isinstance(key, dict) or set(key) != {'path'} or not isinstance(key['path'], str) or key['path'] == '/state' or key['path'].startswith('/state/'):
            raise ValueError('Record identity binds server query results')
        pointer(doc['data'], key['path'])
        resolve(record['title'], doc['data'])
        nav = set(navigation(doc)['paths'])
        opening, when = record['open'], record['when']
        if not isinstance(opening, dict) or not opening or not set(opening) <= nav or not isinstance(when, dict) or not set(when) <= nav:
            raise ValueError('Record opening and visibility use declared navigation paths')
        if not any(v == {'recordId': True} for v in opening.values()):
            raise ValueError('Record opening must bind recordId')
        for path, value in list(when.items()) + list(opening.items()):
            pointer(doc['data'], path)
            if value != {'recordId': True} and type(value) not in (str, int, bool, type(None)):
                raise ValueError('Record navigation uses scalar literals or recordId')
        summary = record['summary']
        if not isinstance(summary, dict) or set(summary) != {'queries', 'components'} or not isinstance(summary['queries'], list):
            raise ValueError('Summary declares queries and components')
        selected = dependencies(doc, summary['queries'])
        if not any(key['path'].startswith(doc['queries'][q]['path'] + '/') or key['path'] == doc['queries'][q]['path'] for q in selected):
            raise ValueError('Summary must read its record identity')
        nodes = summary['components']
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 40 or any(not isinstance(n, dict) or n.get('component') not in SUMMARY_COMPONENTS for n in nodes):
            raise ValueError('Record summaries use at most 40 read-only presentation components')
        validate_app(summary_doc(doc, name))


def summary_doc(doc, name):
    record = doc['records'][name]
    selected = dependencies(doc, record['summary']['queries'])
    return {'schema': doc['schema'], 'title': doc.get('title'),
            'data': copy.deepcopy(doc['data']), 'components': record['summary']['components'],
            'queries': {k: v for k, v in doc['queries'].items() if k in selected}, 'events': {}}


def open_record(doc, data, ref):
    from ui_apps import put
    record = doc.get('records', {}).get(ref['record'])
    if not record:
        raise ActionError('RECORD_UNAVAILABLE', 'This app version no longer declares this record view', 404)
    for path, value in record['open'].items():
        put(data, path, ref['id'] if value == {'recordId': True} else value)


def links(doc, data, slug, queries):
    from ui_apps import pointer, resolve
    result = []
    for name, record in doc.get('records', {}).items():
        selected = dependencies(doc, record['summary']['queries'])
        if any(queries.get(q, {}).get('error') for q in selected):
            continue
        if any(pointer(data, p) != v for p, v in record['when'].items()):
            continue
        ident = resolve(record['id'], data)
        if type(ident) not in (str, int) or not str(ident):
            continue
        # Never advertise a stale loaded record while selection is changing.
        if any(pointer(data, p) != (ident if v == {'recordId': True} else v) for p, v in record['open'].items()):
            continue
        result.append({'reference': reference({'slug': slug, 'record': name, 'id': ident}),
                       'title': str(resolve(record['title'], data))[:240]})
    return result


def allowed(server, actor, slug):
    with server._conn() as conn:
        row = conn.execute("SELECT id FROM rvbbit.dashboards WHERE slug=%s AND runtime_kind='a2ui' AND NOT archived AND rvbbit.artifact_can_view(id,%s,false)", (slug, actor)).fetchone()
    if not row:
        raise ActionError('NOT_FOUND', 'App unavailable to your account', 404)
    return row['id']


def summary(server, actor, ref):
    import ui_apps
    ref = reference(ref)
    allowed(server, actor, ref['slug'])
    snap = ui_apps.snapshot(server, ref['slug'], actor, {'record': ref}, summary=ref['record'])
    data = snap['messages'][2]['updateDataModel']['value']
    # Return only declared display values, never the app's hidden datasets/forms.
    snap['messages'][1]['updateComponents']['components'] = ui_apps.resolve(snap['messages'][1]['updateComponents']['components'], data)
    snap['messages'][2]['updateDataModel']['value'] = {}
    return snap


def listing(server, actor):
    prepare(server)
    with server._conn() as conn:
        rows = conn.execute('SELECT p.id,d.slug,p.record_type,p.record_id FROM rvbbit.ui_record_pins p JOIN rvbbit.dashboards d ON d.id=p.artifact_id WHERE p.actor=%s ORDER BY p.created_at,p.id LIMIT 32', (actor,)).fetchall()
    # References owned by the caller remain removable even if access was revoked.
    return {'pins': [{'id': str(r['id']), 'reference': {'slug': r['slug'], 'record': r['record_type'], 'id': r['record_id']}} for r in rows]}


def change(server, actor, body):
    if not isinstance(body, dict):
        raise ValueError('Expected a pin operation')
    prepare(server)
    if body.get('operation') == 'remove' and set(body) == {'operation', 'id'}:
        ident = str(uuid.UUID(body['id']))
        with server._conn() as conn:
            conn.execute('DELETE FROM rvbbit.ui_record_pins WHERE id=%s AND actor=%s', (ident, actor))
            conn.commit()
        return {'removed': ident}
    if body.get('operation') != 'pin' or set(body) != {'operation', 'reference'}:
        raise ValueError('Use pin with a reference, or remove with a pin id')
    ref = reference(body['reference'])
    summary(server, actor, ref)  # Require a real, readable record before pinning.
    with server._conn() as conn:
        app = conn.execute("SELECT id FROM rvbbit.dashboards WHERE slug=%s AND runtime_kind='a2ui' AND NOT archived AND rvbbit.artifact_can_view(id,%s,false)", (ref['slug'], actor)).fetchone()
        if not app:
            raise ActionError('NOT_FOUND', 'App unavailable to your account', 404)
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('record-pins:' + actor,))
        args = (actor, app['id'], ref['record'], json.dumps(ref['id']))
        old = conn.execute('SELECT id FROM rvbbit.ui_record_pins WHERE actor=%s AND artifact_id=%s AND record_type=%s AND record_id=%s::jsonb', args).fetchone()
        if old:
            return {'id': str(old['id']), 'reference': ref}
        count = conn.execute('SELECT count(*) AS n FROM rvbbit.ui_record_pins WHERE actor=%s', (actor,)).fetchone()['n']
        if count >= 32:
            raise ActionError('PIN_LIMIT', 'This workspace supports 32 pinned records', 409)
        ident = str(uuid.uuid4())
        conn.execute('INSERT INTO rvbbit.ui_record_pins(id,actor,artifact_id,record_type,record_id) VALUES(%s,%s,%s,%s,%s::jsonb)', (ident, *args))
        conn.commit()
    return {'id': ident, 'reference': ref}


def register(m, server, session_actor, respond):
    import asyncio
    import contextvars
    import os

    async def dispatch(request, fn, *, write=False):
        if write:
            allowed_origins = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
            if (request.headers.get('sec-fetch-site') == 'cross-site'
                    or (request.headers.get('origin') and request.headers['origin'] not in allowed_origins)
                    or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
                return respond({'error': {'code': 'INVALID_ORIGIN'}}, 403)
        raw = await request.body() if write else b'{}'
        if len(raw) > 8000:
            return respond({'error': {'code': 'TOO_LARGE'}}, 413)
        def run():
            actor, _, error = session_actor(request)
            if error:
                return error
            token = server._SESSION_SUB.set(actor)
            try:
                return respond(fn(actor, json.loads(raw)))
            except ActionError as exc:
                return respond({'error': {'code': exc.code, 'message': str(exc)}}, exc.status)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return respond({'error': {'code': 'INVALID_RECORD', 'message': str(exc)}}, 400)
            finally:
                server._SESSION_SUB.reset(token)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/api/calliope/app-pins', methods=['GET'])
    async def get_pins(request):
        return await dispatch(request, lambda actor, _: listing(server, actor))

    @m.custom_route('/api/calliope/app-pins', methods=['POST'])
    async def mutate_pin(request):
        return await dispatch(request, lambda actor, body: change(server, actor, body), write=True)

    @m.custom_route('/api/apps/{slug}/record-summary', methods=['POST'])
    async def read_summary(request):
        return await dispatch(request, lambda actor, body: summary(server, actor, reference(body, request.path_params['slug'])), write=True)
