"""A2UI apps in the existing Postgres artifact registry; no per-app server.

Definitions live in dashboard_versions.source_files['app.json']; SQL actions
live in that same version's manifest.actions. Browser and desktop call the same
artifact-authorized endpoint. State is transient input, never a local database.
"""
from __future__ import annotations

import copy
import json
import re
import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path

import app_actions
import ui_app_files
import ui_charts
import ui_composition
import ui_records
import app_revisions
from psycopg import sql as pgsql
from pglast import ast, parse_sql
from pglast.visitors import Visitor
from pglast.stream import RawStream

CATALOG = 'https://calliope.local/a2ui/workspace/v1'
ROOT = Path(__file__).parent / 'ui_app_assets'
MAX_BYTES = 1_500_000


def catalog_definition():
    path = ROOT / 'catalog.json'
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / 'apps/calliope-canvas/business/catalog.json'
    return json.loads(path.read_text())


def release_metadata(doc):
    """Identity shared with previews; requirements include inactive branches."""
    from ui_app_preview import digest
    components = set()
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('component'), str): components.add(value['component'])
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(doc.get('components', []))
    walk(doc.get('fragments', {}))
    catalog = catalog_definition()
    # The oldest catalog that renders every component used, so a new catalog
    # version does not lock older clients out of apps that never use its additions.
    since = catalog.get('since', {})
    version = max([catalog.get('baseline', catalog['version'])] + [since.get(c, 0) for c in components])
    return {'definition_sha256': digest(doc), 'schema': doc['schema'],
            'catalog_id': catalog['id'], 'catalog_version': min(version, catalog['version']),
            'components': sorted(components)}


def asset_archive():
    import os
    return Path(os.environ.get('WAREHOUSE_CALLIOPE_DIR', '/app/data/calliope')) / 'ui-app-assets'


def archive_assets():
    """Keep hashed renderer builds with the existing durable server files."""
    import os
    import tempfile
    for source in ROOT.glob('assets/**/*'):
        if not source.is_file(): continue
        target = asset_archive() / source.relative_to(ROOT)
        if target.exists():
            if target.read_bytes() != source.read_bytes():
                raise ValueError('Immutable UI asset changed: ' + source.name)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(source.read_bytes())
            name = tmp.name
        os.replace(name, target)


def asset_path(relative):
    if Path(relative).is_absolute() or '..' in Path(relative).parts: return None
    for root in (ROOT, asset_archive()):
        target = (root / relative).resolve()
        if target.is_relative_to(root.resolve()) and target.is_file() and target.suffix in {'.js', '.css', '.png', '.woff2', '.svg'}:
            return target
    return None


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 500:
        raise ValueError('Expected an absolute JSON pointer')
    for key in path[1:].split('/'):
        key = key.replace('~1', '/').replace('~0', '~')
        if key in {'__proto__', 'constructor', 'prototype'}:
            raise ValueError('Reserved pointer segment')
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ValueError('Missing binding: ' + path) from None
    return value


def put(value, path, item):
    keys = path[1:].split('/')
    for key in keys[:-1]:
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    key = keys[-1].replace('~1', '/').replace('~0', '~')
    value[int(key) if isinstance(value, list) else key] = copy.deepcopy(item)


def resolve(value, data):
    if isinstance(value, dict):
        if set(value) == {'path'}:
            return copy.deepcopy(pointer(data, value['path']))
        return {k: resolve(v, data) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, data) for v in value]
    return value


def definition(source_files):
    raw = (source_files or {}).get('app.json')
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        raise ValueError('A2UI apps require source_files={"app.json": JSON_TEXT} (max 1.5 MB). '
                         'The app.json value must be the file contents as a string, not an object or path. '
                         'Use file.read_text() or json.dumps(document); upload_artifact is for HTML.')
    doc = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    validate(doc)
    return doc


def validate_link(value):
    from urllib.parse import urlsplit
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value):
        raise ValueError('Links require a root-relative path or HTTPS URL without whitespace')
    if not value:
        return  # An unresolved optional link renders disabled.
    parsed = urlsplit(value)
    if value.startswith('/') and not value.startswith('//'):
        return
    if value.startswith('https://') and parsed.hostname and not parsed.username and not parsed.password:
        parsed.port  # Validate malformed ports before publishing.
        return
    raise ValueError('Links require a root-relative path or HTTPS URL without credentials')


RESULT_FORMATS = {'text', 'integer', 'plain', 'decimal', 'boolean', 'time', 'json'}


def _result_columns(columns):
    return isinstance(columns, list) and 1 <= len(columns) <= 256 and all(
        isinstance(c, dict) and set(c) <= {'name', 'type', 'format'}
        and isinstance(c.get('name'), str) and 0 < len(c['name']) <= 200
        and isinstance(c.get('type', 'text'), str) and c.get('format', 'text') in RESULT_FORMATS
        for c in columns)


def validate(doc):
    if not isinstance(doc, dict) or set(doc) - {'schema', 'title', 'components', 'data', 'queries', 'events', 'fragments', 'repeat', 'switch', 'forms', 'watch', 'navigation', 'records'}:
        raise ValueError('Use schema, title, components, data, queries and events')
    if doc.get('schema') != 'calliope.app.v1':
        raise ValueError('Expected schema calliope.app.v1')
    def bounded(value, depth=0):
        if depth > 32:
            raise ValueError('App nesting exceeds 32 levels')
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'__proto__', 'constructor', 'prototype'}:
                    raise ValueError('Reserved property name')
                bounded(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 5000:
                raise ValueError('Array exceeds 5000 entries')
            for child in value:
                bounded(child, depth + 1)
    bounded(doc)
    data, nodes = doc.get('data'), doc.get('components')
    if not isinstance(data, dict) or not isinstance(data.get('state'), dict):
        raise ValueError('data.state is the transient input object')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 500:
        raise ValueError('Expected 1..500 components')
    ui_composition.validate(doc)
    nodes = ui_composition.expand(doc, data)
    for path in doc.get('watch', []):
        if not isinstance(path, str) or not path.startswith('/state/'): raise ValueError('Watch transient state paths')
        pointer(data, path)
    catalog = catalog_definition()['components']
    builtin = {'Button', 'TextField', 'ChoicePicker', 'CheckBox'}
    ids = {}
    for node in nodes:
        if not isinstance(node, dict) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', str(node.get('id', ''))):
            raise ValueError('Invalid component ID')
        if node['id'] in ids or node.get('component') not in set(catalog) | builtin:
            raise ValueError('Duplicate ID or unsupported component')
        if node['component'] == 'CAttachments':
            binding = node.get('value')
            path = binding.get('path', '') if isinstance(binding,dict) else ''
            if (not node.get('readOnly') and not path.startswith('/state/')) or node.get('offline'):
                raise ValueError('Published attachments bind state or readOnly SQL results; offline capture is not supported')
        ids[node['id']] = node
        if node['component'] in catalog:
            props = catalog[node['component']]
            if set(node) - set(props) - {'id', 'component'}:
                raise ValueError('Unknown component property: ' + node['id'])
            for key, rule in props.items():
                if key in node and rule == 'scrollHeight' and node[key] != 'available' and (type(node[key]) is not int or not 160 <= node[key] <= 1000):
                    raise ValueError('Scroll height must be available or 160..1000 pixels')
                if key in node and rule == 'scrollMinimum' and (type(node[key]) is not int or not 160 <= node[key] <= 600):
                    raise ValueError('Minimum scroll height must be 160..600 pixels')
                if key in node and isinstance(rule, list) and node[key] not in rule:
                    raise ValueError('Invalid design token: ' + key)
        try:
            resolved = resolve(node, data)
        except ValueError as exc:
            raise ValueError(f"{node['id']}: {exc}") from exc
        if node['component'] == 'CLink':
            if not isinstance(resolved.get('label'), str) or not resolved['label'].strip():
                raise ValueError('Links require a readable label')
            validate_link(resolved.get('href'))
        if node['component'] == 'CRecordLink':
            if not isinstance(resolved.get('label'), str) or not resolved['label'].strip():
                raise ValueError('Record links require a readable label')
            if 'reference' not in node:
                raise ValueError('Record links require a reference or binding')
            if resolved.get('reference') is not None:
                ui_records.reference(resolved['reference'])
        if node['component'] == 'CAttachments':
            if type(node.get('readOnly', False)) is not bool or not isinstance(resolved.get('value'), list) or len(resolved['value']) > 5:
                raise ValueError('Attachments require a list of at most five IDs and a boolean readOnly')
            if 'names' in node and (not isinstance(resolved.get('names'),dict) or node['names']['path'].startswith('/state/')):
                raise ValueError('Attachment names bind a metadata object outside state')
        if node['component'] == 'CVegaChart':
            height = node.get('height', 260)
            if type(height) is not int or not 180 <= height <= 960:
                raise ValueError('Chart height must be 180..960 pixels')
            ui_charts.validate(resolved.get('spec', {}))
        if node['component'] == 'CResultTable':
            if not _result_columns(node.get('columns')):
                raise ValueError('Result tables need 1..256 columns of {name, type?, format?}')
            binding = node.get('rows')
            if not isinstance(binding, dict) or set(binding) != {'path'} or not str(binding['path']).startswith('/'):
                raise ValueError('Result table rows bind an absolute data path')
            if resolved.get('rows') is not None and not isinstance(resolved['rows'], list):
                raise ValueError('Result table rows must be a list')
            if type(node.get('visibleRows', 12)) is not int or not 3 <= node.get('visibleRows', 12) <= 50:
                raise ValueError('Result tables show 3..50 rows before scrolling')
            if 'rowCount' in node and (type(node['rowCount']) is not int or node['rowCount'] < 0):
                raise ValueError('Result table rowCount must be a non-negative integer')
        if node['component'] == 'CFilterBar' and not re.fullmatch(r'(filter|[a-z][a-z0-9_]*_filter)', str(node.get('onChange', ''))):
            raise ValueError('Filter actions must be named filter or *_filter')
    seen = set()
    def walk(ident, depth=0):
        if depth > 24 or ident not in ids or ident in seen:
            raise ValueError('Invalid component tree')
        seen.add(ident)
        node = ids[ident]
        for child in node.get('children', []) + ([node['child']] if 'child' in node else []):
            walk(child, depth + 1)
    walk('root')
    if seen != set(ids):
        raise ValueError('Unreachable components')
    queries = doc.get('queries', {})
    if not isinstance(queries, dict) or len(queries) > 32:
        raise ValueError('Expected at most 32 named SQL bindings')
    paths = []
    for name, query in queries.items():
        if not isinstance(query, dict) or set(query) - {'sql', 'params', 'path', 'shape', 'limit', 'dependsOn', 'cacheMs', 'timeoutMs'}:
            raise ValueError('Invalid SQL binding: ' + name)
        path = query.get('path', '')
        pointer(data, path)
        if path == '/state' or path.startswith('/state/'):
            raise ValueError('Query results cannot overwrite transient state')
        if any(path == p or path.startswith(p + '/') or p.startswith(path + '/') for p in paths):
            raise ValueError('SQL output paths must not overlap')
        paths.append(path)
        if query.get('shape', 'rows') not in {'rows', 'first', 'value'}:
            raise ValueError('Query shape must be rows, first or value')
        statement = query.get('sql', '')
        if not isinstance(statement, str) or len(statement) > 32000:
            raise ValueError('Invalid SQL length')
        parsed = parse_sql(statement)
        if len(parsed) != 1 or not isinstance(parsed[0].stmt, ast.SelectStmt):
            raise ValueError('SQL bindings require one SELECT; mutations use manifest.actions')
        _, indices = app_actions.statement_parameters(statement)
        params = query.get('params', [])
        if not isinstance(params, list) or len(params) > 32 or set(indices) != set(range(len(params))):
            raise ValueError('SQL params must match $1..$N exactly')
        for param in params:
            if app_actions.actor_parameter(param):
                continue
            if not isinstance(param, dict) or set(param) != {'path'}:
                raise ValueError('SQL params bind model paths or {"actor":true}')
            pointer(data, param['path'])
        if type(query.get('limit', 500)) is not int or not 1 <= query.get('limit', 500) <= 1000:
            raise ValueError('Query limit must be 1..1000')
        if type(query.get('cacheMs', 0)) is not int or not 0 <= query.get('cacheMs', 0) <= 300000: raise ValueError('Cache TTL must be 0..300000 ms')
        if type(query.get('timeoutMs', 5000)) is not int or not 100 <= query.get('timeoutMs', 5000) <= 8000: raise ValueError('Query timeout must be 100..8000 ms')
    query_order(queries)
    events = doc.get('events', {})
    if not isinstance(events, dict) or len(events) > 64:
        raise ValueError('Expected at most 64 events')
    for name, event in events.items():
        if not isinstance(event, dict) or set(event) - {'action', 'inputs', 'set', 'reset'}:
            raise ValueError('Events can set state and/or invoke a named action')
        if (name == 'filter' or name.endswith('_filter')) and (event.get('action') or event.get('set') or event.get('reset')):
            raise ValueError('Filter events are read-only')
        for path in event.get('set', {}):
            if not path.startswith('/state/'):
                raise ValueError('Events can only set state')
            pointer(data, path)
    for node in nodes + [n for f in doc.get('fragments', {}).values() for n in f['components']]:
        name = node.get('action', {}).get('event', {}).get('name') or node.get('onChange') or node.get('onSelect')
        if name and name not in events:
            raise ValueError('Undefined event: ' + name)
    ui_records.validate(doc)
    return doc


def validate_manifest(doc, manifest):
    actions = app_actions.normalize((manifest or {}).get('actions', {}))
    for event in doc.get('events', {}).values():
        if event.get('action'):
            if event['action'] not in actions:
                raise ValueError('Undefined manifest action: ' + event['action'])
            if set(event.get('inputs', {})) != set(actions[event['action']]['inputs']):
                raise ValueError('Event inputs must match the named action')


def shell():
    # The build hashes identify host assets; no generated JS is required per app.
    try:
        archive_assets()
        return (ROOT / 'published.html').read_text()
    except OSError as exc:
        raise ValueError('Build the shared UI app host before publishing A2UI apps') from exc


class BindParameters(Visitor):
    def __init__(self, values):
        super().__init__()
        self.values = values

    def visit_ParamRef(self, ancestors, node):
        value = self.values[node.number - 1]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, allow_nan=False)
        # Parse a driver-quoted SQL literal back into the AST. No textual
        # substitution can accidentally replace '$1' inside a string/comment.
        return parse_sql('SELECT ' + pgsql.Literal(value).as_string())[0].stmt.targetList[0].val


def bound_sql(query, data, actor=None):
    parsed = parse_sql(query['sql'])
    BindParameters([app_actions.actor_value(actor) if app_actions.actor_parameter(p) else resolve(p, data)
                    for p in query.get('params', [])])(parsed)
    return RawStream()(parsed)


# Bounded process cache. Authority is checked on every request before cache use;
# keys include actor, app version, source hash and all bound SQL values.
_QUERY_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()

def query_order(queries):
    dependencies = {}
    for name, q in queries.items():
        declared = q.get('dependsOn', [])
        if not isinstance(declared, list) or any(not isinstance(n, str) for n in declared):
            raise ValueError('dependsOn is a list of query names')
        deps = set(declared)
        for param in q.get('params', []):
            if app_actions.actor_parameter(param): continue
            path = param['path']
            deps.update(k for k, other in queries.items() if path == other['path'] or path.startswith(other['path']+'/'))
        if not deps <= set(queries): raise ValueError('Unknown SQL dependency')
        dependencies[name] = deps
    ordered = []
    while len(ordered)<len(queries):
        ready = [n for n in queries if n not in ordered and dependencies[n] <= set(ordered)]
        if not ready: raise ValueError('Cyclic SQL bindings')
        ordered.extend(ready)
    return [(name, dependencies[name]) for name in ordered]


def hydrate(doc, data, initial, resets, source=None, drafts=None):
    for form in doc.get('forms', []):
        if source and not (form['source']==source or form['source'].startswith(source+'/')): continue
        saved, local = pointer(data, form['source']), pointer(data, form['path'])
        if not isinstance(saved, dict): continue
        key = form.get('key', 'id')
        if drafts is not None or initial or form['path'] in resets or local.get(key)!=saved.get(key):
            value = saved
            if drafts is not None and form['path'] not in resets:
                hint = next((d['value'] for d in drafts if d['path']==form['path'] and d['value'].get(key)==saved.get(key)), None)
                if hint is not None:
                    value = local if local.get(key)==saved.get(key) else hint
            put(data, form['path'], copy.deepcopy(value))


def navigation(doc):
    paths = doc.get('navigation', {}).get('paths')
    if paths is None:
        # Older published versions already declare page switches and SQL inputs
        # for their form sources. Infer identity without business-specific names.
        paths = [r['path'] for r in doc.get('switch', {}).values() if r['path'].startswith('/state/')]
        for form in doc.get('forms', []):
            for query in doc.get('queries', {}).values():
                if form['source']==query['path'] or form['source'].startswith(query['path']+'/'):
                    paths.extend(p['path'] for p in query.get('params', []) if p.get('path', '').startswith('/state/'))
        paths = sorted(set(p for p in paths if p not in doc.get('watch', []) and not any(p==f['path'] or p.startswith(f['path']+'/') for f in doc.get('forms', []))))
    return {'paths': paths, 'events': {n:e['set'] for n,e in doc.get('events', {}).items() if e.get('set') and not e.get('action') and not e.get('reset')}}


def snapshot(server, slug, actor, body, *, summary=None):
    if not isinstance(body, dict) or set(body) - {'version', 'state', 'event', 'context', 'request_key', 'drafts', 'record'}:
        raise ValueError('Use version, state, event, context and request_key')
    version = body.get('version')
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Invalid app version')
    if body.get('event') and version is None:
        raise ValueError('Events require the loaded app version')
    app, row = server._load_live_app_version(slug, version)
    if not app or not row or row['kind'] != 'a2ui':
        raise app_actions.ActionError('NOT_FOUND', 'App version not found', 404)
    doc = definition(row['source_files'])
    source_doc = doc
    record_ref = ui_records.reference(body['record'], slug) if 'record' in body else None
    if record_ref and (body.get('event') or 'state' in body):
        raise ValueError('Opening a record cannot also supply state or execute an event')
    if record_ref and record_ref['record'] not in doc.get('records', {}):
        raise app_actions.ActionError('RECORD_UNAVAILABLE', 'Record view unavailable in this app version', 404)
    if summary is not None:
        doc = ui_records.summary_doc(doc, summary)
    data = copy.deepcopy(doc['data'])
    if record_ref:
        ui_records.open_record(source_doc, data, record_ref)
    drafts = body.get('drafts')
    if drafts is not None:
        if not isinstance(drafts, list) or len(drafts)>200: raise ValueError('At most 200 record drafts per request')
        declared = {f['path']: f.get('key','id') for f in doc.get('forms', [])}
        seen = set()
        for draft in drafts:
            if not isinstance(draft, dict) or set(draft)!={'path','value'} or not isinstance(draft['path'], str) or draft['path'] not in declared or not isinstance(draft['value'], dict):
                raise ValueError('Drafts must refer to a declared form')
            ident = draft['value'].get(declared[draft['path']])
            if type(ident) not in (str, int) or len(str(ident))>512: raise ValueError('Draft record key must be a string or integer')
            key = (draft['path'], type(ident), ident)
            if key in seen: raise ValueError('Duplicate record draft')
            seen.add(key)
    if 'state' in body:
        if not isinstance(body['state'], dict) or set(body['state']) != set(data['state']):
            raise ValueError('State must match the published state keys')
        data['state'] = copy.deepcopy(body['state'])
    receipt = None
    event = {}
    if body.get('event'):
        event = doc.get('events', {}).get(body['event'])
        if event is None:
            raise ValueError('Unknown event')
        scope = {**data, 'context': body.get('context') or {}, 'request': {'key':body.get('request_key')}}
        if event.get('action'):
            ui_app_files.references(server, actor, ui_composition.expand(doc, data), data, check=True)
            inputs = resolve(event.get('inputs', {}), scope)
            receipt = server._execute_app_action_request(slug, event['action'], actor, {
                'version': row['version'], 'inputs': inputs, 'request_key': body.get('request_key')})
        scope['receipt'] = receipt
        for path, value in event.get('set', {}).items():
            put(data, path, resolve(value, scope))
    queries = {}
    cache_scope = (actor, slug, row['version'], hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest())
    if receipt:
        with _CACHE_LOCK:
            for key in list(_QUERY_CACHE):
                if key[1]==slug: del _QUERY_CACHE[key]
    initial = 'state' not in body
    resets = event.get('reset', [])
    for name, deps in query_order(doc.get('queries', {})):
        query = doc['queries'][name]
        if any(queries[d].get('error') for d in deps):
            queries[name] = {'error': {'code':'DEPENDENCY_UNAVAILABLE','message':'An upstream SQL binding failed'}, 'path':query['path']}
            continue
        statement = bound_sql(query, data, actor)
        cache_key = (*cache_scope, name, statement)
        ttl = query.get('cacheMs', 0)/1000
        with _CACHE_LOCK:
            cached = _QUERY_CACHE.get(cache_key)
        hit = bool(ttl and cached and time.monotonic()-cached[0] < ttl)
        try:
            result = copy.deepcopy(cached[1]) if hit else server.tool_run_sql(
                statement, limit=query.get('limit', 500), _max_rows=1000,
                _statement_timeout_ms=query.get('timeoutMs', 5000))
        except Exception:
            result = {'error': {'code': 'QUERY_UNAVAILABLE', 'message': 'The SQL binding could not refresh'}}
        if result.get('error'):
            queries[name] = {'error': result['error'], 'path': query['path']}
            continue
        if ttl and not hit:
            with _CACHE_LOCK:
                _QUERY_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))
                _QUERY_CACHE.move_to_end(cache_key)
                while len(_QUERY_CACHE)>256: _QUERY_CACHE.popitem(last=False)
        rows = result['rows']
        shape = query.get('shape', 'rows')
        value = rows if shape == 'rows' else (rows[0] if rows else None)
        if shape == 'value': value = next(iter(rows[0].values())) if rows else None
        if value is not None: put(data, query['path'], value)
        hydrate(doc, data, initial, resets, query['path'], drafts)
        queries[name] = {k: result.get(k) for k in ('row_count', 'truncated', 'elapsed_ms', 'lineage')}
        queries[name].update(path=query['path'], dependsOn=sorted(deps), cache_hit=hit)
    # SQL results cannot introduce arbitrary components or chart I/O. Validate
    # the delivered graph too, while preserving an already committed receipt.
    try:
        components = ui_composition.expand(doc, data)
        rendered = {**doc, 'components':components, 'data':data, 'fragments':{}, 'repeat':{}, 'switch':{}}
        validate(rendered)
        ui_app_files.references(server, actor, components, data, forms=doc.get('forms', []))
    except (ValueError, KeyError, TypeError) as exc:
        queries['render'] = {'error':{'code':'INVALID_RENDER_DATA','message':str(exc)}}
        components = [{'id':'root','component':'CText','text':'The app data could not be rendered. Inspect the SQL binding errors.'}]
    if record_ref:
        declared = source_doc['records'][record_ref['record']]
        if any(queries.get(q, {}).get('error') for q in ui_records.dependencies(source_doc, declared['summary']['queries'])):
            raise app_actions.ActionError('RECORD_UNAVAILABLE', 'The record could not refresh', 503)
        if resolve(declared['id'], data) != record_ref['id']:
            raise app_actions.ActionError('NOT_FOUND', 'This record no longer exists or is unavailable', 404)
    record_links = ui_records.links(source_doc, data, slug, queries)
    if summary is not None:
        record_links = [link for link in record_links if link['reference'] == record_ref]
    surface = 'published-app'
    return {'slug': slug, 'version': row['version'], 'title': doc.get('title') or app['name'],
            'viewer': actor, 'latest_version': app.get('latest_version', row['version']),
            'update': app_revisions.release(server, slug, app, row, record_links[0]['reference'] if record_links else None),
            'release': (row.get('manifest') or {}).get('ui_runtime') or release_metadata(source_doc),
            'records': record_links,
            'queries': queries, 'receipt': receipt, 'watch': doc.get('watch', []),
            'forms': [{'path': f['path'], 'key': f.get('key', 'id'), 'source': f['source']} for f in doc.get('forms', [])],
            'resets': resets, 'navigation': navigation(doc), 'record_drafts': True,
            'commands': [name for name, event in doc.get('events', {}).items() if event.get('action')],
            'messages': [
                {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
                {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': components}},
                {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'value': data}}]}


def register(m, server, authorize, respond):
    import ui_chart_render
    import asyncio
    import contextvars
    import os
    from starlette.responses import FileResponse, HTMLResponse

    archive_assets()

    def session_actor(request):
        import auth
        session = auth.read_session_full(request)
        if not session or not session.get('mapped', True):
            return None, session, respond({'error': {'code': 'UNAUTHORIZED'}}, 401)
        actor = str(session.get('identity') or session.get('sub') or '').strip().lower()
        if not actor:
            return None, session, respond({'error': {'code': 'UNAUTHORIZED'}}, 401)
        return actor, session, None

    # Generic rendering accepts caller-owned inline data; it never loads an
    # artifact or SQL. The snapshot route still uses artifact-specific access.
    import app_revisions
    app_revisions.register(m, server, session_actor, respond)
    ui_chart_render.register(m, server, session_actor, respond)
    ui_app_files.register(m, server, session_actor, respond)
    ui_records.register(m, server, session_actor, respond)
    import ui_app_preview
    ui_app_preview.register(m, server, session_actor, respond)
    import meaning_graph
    meaning_graph.register(m, server, session_actor, respond)

    @m.custom_route('/api/calliope/ui-apps', methods=['GET'])
    async def list_apps(request):
        actor, _, error = session_actor(request)
        if error: return error
        def run():
            with server._conn() as conn:
                rows = conn.execute("SELECT slug,name,description,app_kind,latest_version,manifest->'ui_runtime' AS ui_runtime FROM rvbbit.dashboards "
                    "WHERE runtime_kind='a2ui' AND NOT archived AND rvbbit.artifact_can_view(id,%s,false) "
                    "ORDER BY updated_at DESC LIMIT 100", (actor,)).fetchall()
            return respond({'apps': rows})
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/api/apps/{slug}/conversation', methods=['GET'])
    async def app_conversation(request):
        actor, _, error = session_actor(request)
        if error: return error
        def run():
            import ui_app_context
            try:
                return respond(ui_app_context.conversation(server._conn, actor, request.path_params['slug']))
            except ValueError as exc:
                return respond({'error': {'code': 'NOT_FOUND', 'message': str(exc)}}, 404)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/api/apps/{slug}/embed/{version}', methods=['GET'])
    async def app_embed(request):
        def run():
            _, _, error = authorize(request)
            if error: return error
            try:
                version = int(request.path_params['version'])
                if version < 1: raise ValueError('Invalid version')
            except ValueError:
                return respond({'error': {'code': 'NOT_FOUND'}}, 404)
            app, row = server._load_live_app_version(request.path_params['slug'], version)
            if not app or not row or row['kind'] != 'a2ui':
                return respond({'error': {'code': 'NOT_FOUND'}}, 404)
            # Only platform-owned code may run with the parent's origin. Never
            # serve stored artifact HTML here, even when metadata says A2UI.
            return HTMLResponse(shell(), headers={
                'cache-control': 'private, no-store',
                'x-content-type-options': 'nosniff',
                'content-security-policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-eval'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
            })
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/api/apps/{slug}/ui', methods=['POST'])
    async def app_ui(request):
        origin = request.headers.get('origin')
        allowed = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
        if (request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in allowed)
                or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
            return respond({'error': {'code': 'INVALID_ORIGIN'}}, 403)
        raw = await request.body()
        if len(raw) > 160000:
            return respond({'error': {'code': 'TOO_LARGE'}}, 413)
        def run():
            actor, _, error = authorize(request)
            if error:
                return error
            token = server._SESSION_SUB.set(actor)
            try:
                result = snapshot(server, request.path_params['slug'], actor, json.loads(raw))
                return respond(result)
            except app_actions.ActionError as exc:
                return respond({'error': {'code': exc.code, 'message': str(exc)}}, exc.status)
            except (ValueError, TypeError, KeyError) as exc:
                return respond({'error': {'code': 'INVALID_APP_INPUT', 'message': str(exc)}}, 400)
            finally:
                server._SESSION_SUB.reset(token)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/ui-app-assets/{path:path}', methods=['GET'])
    async def assets(request):
        relative = request.path_params['path']
        target = asset_path(relative)
        if target is None:
            return respond({'error': {'code': 'NOT_FOUND'}}, 404)
        return FileResponse(target, headers={'x-content-type-options': 'nosniff', 'cache-control': 'public,max-age=31536000,immutable'})


def template(app_kind='workspace'):
    doc = {'schema': 'calliope.app.v1', 'title': 'Company app',
           'data': {'state': {}, 'time': 'Loading…'},
           'components': [{'id': 'root', 'component': 'CStack', 'children': ['clock']},
                          {'id': 'clock', 'component': 'CText', 'text': {'path': '/time'}}],
           'queries': {'clock': {'sql': 'SELECT current_timestamp::text AS time', 'path': '/time', 'shape': 'value'}},
           'events': {}}
    return {'runtime_kind': 'a2ui', 'app_kind': app_kind, 'manifest': {},
            'source_files': {'app.json': json.dumps(doc, indent=2)},
            'app_actions_example': app_actions.example(),
            'how_to_use': [
                'Publish with create_live_app(runtime_kind="a2ui", source_files={"app.json": JSON_STRING}, manifest={"actions": ...}).',
                'Definitions and versions are stored in Calliope Postgres. Open /apps/<slug> or select Apps in the desktop client.',
                'Declare named queries with sql, ordered params [{path:"/state/..."}], output path, shape (rows/first/value), and optional limit (1..1000). Params can consume other query outputs; dependencies are ordered automatically. Optional dependsOn, timeoutMs and cacheMs control execution.',
                'A query param {"actor":true} binds the authenticated caller, never client state. Use it in SQL predicates for assigned work. Server-read previews use the same caller. Action step.params may also contain {"actor":true}; do not declare it as a form/action input. Artifact grants and SQL predicates enforce access; hiding a control does not.',
                'SQL uses $1..$N parameters and the existing governed read-only query path. No app-specific REST handlers.',
                'data.state holds transient form/filter values. Other data paths have initial placeholders overwritten by SQL bindings.',
                'A Button event maps to events[name].action plus inputs. The action must exist in the same version\'s manifest.actions. Use version checks for edits.',
                'CFilterBar binds an object under /state and uses a filter event defined as {}. Reads re-run after debounce.',
                'Events can set /state/... paths with literals or {path:"/context/..."}. Query results and rendered component props can supply event context.',
                'SQL data and writes use the server; no local database, Python helper, automatic seeds, or app-specific backend is required.',
                'Reuse flat component graphs with fragments; repeat them over SQL rows with stable keys, or select a fragment with switch. SQL decision_tree results can drive switches without bespoke routes.',
                'forms initializes transient drafts from records while preserving edits; event.reset explicitly reloads a form. watch debounces state changes into SQL refreshes.',
                'Optional records[name] declares id:{path:"/record/id"}, title:{path:"/record/title"}, when:{"/state/page":"detail"}, open:{"/state/page":"detail","/state/selectedId":{recordId:true}}, and summary:{queries:["record"],components:[a read-only flat tree rooted at "root"]}. Open paths must be declared navigation.paths. Native Pin to Home persists an actor-owned reference; only summary SQL dependencies refresh. Do not put draft state in a record link.',
                'Vega-Lite supports inline/named datasets, layers, transforms and compositions. CVegaChart height is an integer from 180 to 960 pixels (default 260). Remote URLs, links and image marks are disabled. Native picking covers rectangles and circle/square points; brush/selection widgets are not native interactions.',
                'CAttachments uses the shared authenticated upload/download host; CArtwork uses a bundled asset enum. Native per-record drafts stay in the private recovery journal.',
                'CLink uses label and href (literal or {path:"/analysis_url"}). Use /d/<slug> or /apps/<slug> for this instance, or an HTTPS URL. Native opens the OS browser and retains drafts; Web opens a new tab. Preview links are inert. Empty href disables an unresolved link. Do not implement a link as a SQL action.',
                'CRecordLink uses label and reference:{path:"/target"}; target is {slug,record,id}, or null while unavailable. The destination app declares that record contract. Native opens it in place with Back and draft retention; Web opens the same record. UI Studio records a simulated open_record event. No copied form state, SQL or action belongs in a reference.',
            ]}
