"""Version-bound query inventories for spatial artifact drill-downs.

An inventory is metadata, never a Stage surface or an instruction to run SQL.
Captured rows are deliberately not retained or shared across viewers.
"""
from __future__ import annotations

import hashlib
import json
import uuid
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import artifact_access
import calliope_access
import stage_context


class PreviewCache:
    """Short-lived, bounded results. Callers recheck grants before every access."""
    def __init__(self, ttl=300, max_bytes=32*1024*1024):
        self.ttl, self.max_bytes = ttl, max_bytes
        self.entries, self.lock = OrderedDict(), threading.Lock()

    def get(self, key, preview_id=None):
        with self.lock:
            entry = self.entries.get(key)
            if not entry or time.monotonic() - entry[0] > self.ttl:
                self.entries.pop(key, None)
                return None
            if preview_id and entry[2]['preview_id'] != preview_id:
                return None
            self.entries.move_to_end(key)
            return json.loads(json.dumps(entry[2]))

    def put(self, key, value):
        value = json.loads(json.dumps(value, default=str))
        value['preview_id'] = str(uuid.uuid4())
        size = len(json.dumps(value).encode())
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = (time.monotonic(), size, value)
            while len(self.entries) > 128 or sum(e[1] for e in self.entries.values()) > self.max_bytes:
                self.entries.popitem(last=False)
        return value


class CaptureQueue:
    """One browser capture at a time; repeated UI polls never enqueue duplicates."""
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='artifact-query-capture')
        self.jobs, self.lock = OrderedDict(), threading.Lock()

    def ensure(self, key, work):
        with self.lock:
            now = time.monotonic()
            for old, (future, started) in list(self.jobs.items()):
                if future.done() and now-started > 120:
                    del self.jobs[old]
            if key not in self.jobs:
                if len(self.jobs) >= 32:
                    return 'busy'
                self.jobs[key] = (self.pool.submit(work), now)
            future = self.jobs[key][0]
            if not future.done():
                return 'loading'
            return 'error' if future.exception() else 'ready'

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.artifact_query_captures (
 dashboard_id bigint NOT NULL,
 version integer NOT NULL,
 capture_id uuid NOT NULL,
 captured_at timestamptz NOT NULL DEFAULT now(),
 queries jsonb NOT NULL DEFAULT '[]'::jsonb,
 total_queries integer NOT NULL,
 PRIMARY KEY(dashboard_id,version),
 FOREIGN KEY(dashboard_id,version) REFERENCES rvbbit.dashboard_versions(dashboard_id,version) ON DELETE CASCADE
);
"""


def record(factory, job, queries, *, only_missing=False):
    """Persist only exact-version execution descriptors, before model enrichment."""
    capture_id = str(uuid.uuid4())
    inventory = []
    for q in queries[:200]:
        # Keep the complete SQL supplied by the bounded capture, with its
        # truncation flag. Never promote a shortened statement for execution.
        item = {k: q[k] for k in ('query_id', 'sql', 'sql_truncated', 'columns', 'row_count',
                'truncated', 'engine', 'elapsed_ms', 'as_of') if k in q}
        item['failed'] = bool(q.get('error'))
        inventory.append(item)
    conflict = ('DO NOTHING' if only_missing else
        'DO UPDATE SET capture_id=excluded.capture_id,queries=excluded.queries,'
        'total_queries=excluded.total_queries,captured_at=now()')
    with factory() as conn:
        conn.execute('INSERT INTO rvbbit.artifact_query_captures '
                     '(dashboard_id,version,capture_id,queries,total_queries) VALUES(%s,%s,%s,%s::jsonb,%s) '
                     'ON CONFLICT(dashboard_id,version) ' + conflict,
                     (job['dashboard_id'], job['version'], capture_id, json.dumps(inventory, default=str), len(queries)))
    return capture_id


def fingerprint(sql, context=None):
    # Preserve literals and quoted identifiers. Whitespace-normalizing raw SQL
    # would conflate distinct values inside literals.
    return hashlib.sha256(json.dumps([sql.strip(), context or {}], sort_keys=True).encode()).hexdigest()


def query_sources(statement):
    """Resolve physical sources and apply the same read-only rules as Data."""
    import data_explorer
    import sqlglot
    from sqlglot.optimizer.scope import traverse_scope
    try:
        parsed = sqlglot.parse_one(statement, read='postgres')
        if not isinstance(parsed, sqlglot.exp.Query):
            raise ValueError('This definition is not a read-only query.')
        refs = list(dict.fromkeys(f'{table.db or "public"}.{table.name}'
            for scope in traverse_scope(parsed) for _, table in scope.selected_sources.values()
            if isinstance(table, sqlglot.exp.Table) and isinstance(table.this, sqlglot.exp.Identifier)))
        if not 1 <= len(refs) <= 12:
            raise ValueError('This definition needs a source combination that Data does not support yet.')
        data_explorer.guard_sql(statement, refs)
        return refs
    except sqlglot.errors.SqlglotError:
        raise ValueError('This saved SQL is not supported by Data yet.') from None


def scalar_result(item, result):
    """Only a declared scalar returning exactly one row gets a value tile."""
    evaluator = item.get('evaluator') or {}
    rows = result.get('rows') or []
    if (item.get('kind') != 'calculation' or evaluator.get('shape') != 'scalar'
            or len(rows) != 1 or result.get('truncated') or int(evaluator.get('row_index') or 0) != 0):
        return None
    names = [c if isinstance(c, str) else c.get('name') for c in result.get('columns') or []]
    row = rows[0] if isinstance(rows[0], dict) else dict(zip(names, rows[0]))
    column = evaluator.get('value_column') or 'value'
    if column not in row and len(row) == 1:
        column = next(iter(row))
    if column not in row:
        return None
    value = row[column]
    if isinstance(value, dict) and 'value' in value:
        value = value['value']
    return {'value': value, 'column': column, 'display': item.get('display') or {}}


def sql_metadata(statement, columns=None):
    return stage_context.surface_metadata({'kind': 'query', 'source': {'sql': statement},
                                           'payload': {'columns': columns or []}})


def query_title(statement, columns, tables):
    names = [c if isinstance(c, str) else c.get('name', '') for c in columns or []]
    try:
        import sqlglot
        parsed = sqlglot.parse_one(statement, read='postgres')
        names = names or [v for v in parsed.named_selects if v != '*']
        # Display the underlying dimension when an app uses opaque aliases such
        # as val/n for several different charts. No model or SQL execution needed.
        labels = {}
        for expression in parsed.selects:
            alias = expression.alias_or_name
            if alias.lower() in {'val', 'value', 'mm', 'hr', 'lat', 'lon', 'n'}:
                inputs = list(dict.fromkeys(c.name for c in expression.find_all(sqlglot.exp.Column)))
                labels[alias] = ', '.join(inputs[:2]) if inputs else ('count' if expression.find(sqlglot.exp.Count) else alias)
        names = [labels.get(name, name) for name in names]
    except Exception:
        pass
    fields = [n.replace('_', ' ') for n in names if n][:4]
    source = tables[0].split('.')[-1].replace('_', ' ') if tables else 'Saved query'
    return (', '.join(fields).capitalize() + ' · ' + source if fields else source.capitalize())[:180]


def assemble(surface, document, manifest, capture, extract_queries, render_sql):
    """Only source/captures from this immutable version; never slug-wide logs."""
    items, seen, runtime_ids = [], {}, {}
    capture = capture or {}
    observed_at = str(capture.get('captured_at') or '') or None

    def add(statement, source, q=None, semantic=None):
        q, semantic = q or {}, semantic or {}
        if not isinstance(statement, str) or not statement.strip():
            return None
        # Identity includes data-time context, not a source string's ordinal.
        context = {'as_of': q['as_of']} if q.get('as_of') else {}
        sha = fingerprint(statement, context)
        if not semantic and sha in seen:
            return seen[sha]
        metadata = sql_metadata(statement, q.get('columns'))
        meaning = semantic.get('meaning') or {}
        entry_id = 'meaning:' + str(semantic['id']) + ':' + sha[:24] if semantic else 'query:' + sha[:24]
        item = {'id': entry_id, 'sql_sha256': sha, 'sql': statement,
                'title': meaning.get('label') or query_title(statement, q.get('columns'), metadata['tables']),
                'description': meaning.get('description') or '', 'formula': meaning.get('formula') or '',
                'kind': 'calculation' if semantic else 'query', 'source': source,
                'tables': metadata['tables'], 'columns': metadata['columns'],
                'parameters': semantic.get('parameters') or {}, 'context': context,
                'captured_at': observed_at if source == 'captured' else None,
                'row_count': q.get('row_count'), 'engine': q.get('engine'), 'elapsed_ms': q.get('elapsed_ms'),
                'truncated': bool(q.get('truncated')), 'failed': bool(q.get('failed')),
                'sql_truncated': bool(q.get('sql_truncated')), 'source_query_ids': [],
                'bindings': semantic.get('bindings') or []}
        item['evaluator'] = {k: v for k, v in (semantic.get('evaluator') or {}).items()
                             if k in ('shape', 'value_column', 'row_index')}
        item['display'] = dict(semantic.get('display') or {})
        if meaning.get('unit'):
            item['display'].setdefault('unit', meaning['unit'])
        try:
            if item['sql_truncated']:
                raise ValueError('The captured SQL is incomplete.')
            query_sources(statement)
            item.update(can_explore=True, unavailable_reason=None)
        except ValueError as exc:
            item.update(can_explore=False, unavailable_reason=str(exc))
        items.append(item)
        if not semantic:
            seen[sha] = item
        return item

    for q in capture.get('queries') or []:
        item = add(q.get('sql'), 'captured', q)
        if item and q.get('query_id'):
            runtime_ids[str(q['query_id'])] = item['id']
    source = (document.get('html') or '') + '\n' + '\n'.join(
        str(value.get('content', '') if isinstance(value, dict) else value)
        for value in (document.get('source_files') or {}).values())
    for statement in extract_queries(source):
        # The extractor returns source literals. Skip interpolated / invalid
        # candidates rather than pretending to know their runtime SQL.
        try:
            import sqlglot
            parsed = sqlglot.parse(statement, read='postgres')
            if len(parsed) != 1 or not isinstance(parsed[0], sqlglot.exp.Query) or '${' in statement:
                continue
        except Exception:
            continue
        add(statement, 'declared')
    semantic_map = manifest.get('semantic_map') or {}
    for obj in semantic_map.get('objects') or []:
        try:
            statement, defaults = render_sql(obj)
        except ValueError:
            continue
        item = add(statement, 'calculation', semantic=obj)
        if item:
            item['parameter_values'] = defaults
            if capture.get('capture_id') and str(capture['capture_id']) == obj.get('query_capture_id'):
                item['source_query_ids'] = list(dict.fromkeys(runtime_ids[r] for r in obj.get('source_queries', []) if r in runtime_ids))
    return {'surface_id': str(surface['id']), 'session_id': str(surface['session_id']),
            'slug': surface['artifact_slug'], 'version': int(surface['artifact_version']),
            'title': surface['title'], 'items': items,
            'tables': sorted({t for item in items for t in item['tables']}),
            'captured_at': observed_at, 'capture_total': capture.get('total_queries'),
            'capture_retained': len(capture.get('queries') or []),
            'note': (f"Retained {len(capture.get('queries') or [])} of {capture.get('total_queries')} observed calls. "
                     if (capture.get('total_queries') or 0) > len(capture.get('queries') or []) else '') +
                    'Captured queries describe the observed page state; interactions can run additional queries.'
                    if capture else 'No retained execution capture for this version. Showing definitions from its saved source and enrichment.'}


def saved_query(surface):
    """Expose one exact Stage query through the governed preview path, without rows.

    No artifact grant substitutes for the chat grant checked by read(). Dataset
    grants, SQL validation and the viewer's execution identity are still checked
    by Explorer on every preview, including cache hits.
    """
    source, payload = surface.get('source') or {}, surface.get('payload') or {}
    args = source.get('args') or {}
    batch = args.get('batch') or {}
    origin = source.get('derived_from') or {}
    contexts = [payload.get('as_of_applied'), source.get('as_of'), args.get('as_of'),
                batch.get('as_of'), (origin.get('query_context') or {}).get('as_of')]
    as_of = next((v for v in contexts if v is not None and v != ''), None)
    context = {'as_of': as_of} if as_of is not None else {}
    statement = source.get('sql') or ''
    sha = fingerprint(statement, context)
    item = {'id': 'query:' + sha[:24], 'sql_sha256': sha, 'sql': statement,
            'title': surface.get('title') or 'Saved query', 'kind': 'query', 'source': 'stage',
            'context': context, 'parameters': {}, 'tables': [], 'columns': payload.get('columns') or [],
            'sql_truncated': bool(source.get('sql_truncated')), 'can_explore': False,
            'unavailable_reason': None}
    try:
        if not statement.strip() or item['sql_truncated'] or statement.rstrip().endswith('...[truncated]'):
            raise ValueError('The complete saved SQL is unavailable. Keep using the saved result.')
        if source.get('sheet') or payload.get('sheet'):
            raise ValueError('This query uses a sheet snapshot. Refresh it from its original conversation.')
        if as_of is not None and not isinstance(as_of, str):
            raise ValueError('The saved data-time scope cannot be replayed safely.')
        item['tables'] = query_sources(statement)
        item['can_explore'] = True
    except ValueError as exc:
        item['unavailable_reason'] = str(exc)
    return {'surface_id': str(surface['id']), 'session_id': str(surface['session_id']),
            'kind': 'query', 'slug': None, 'version': None, 'title': item['title'],
            'items': [item], 'tables': item['tables'],
            'note': 'Runs the exact saved query and data-time scope. Written analysis remains as saved.'}


def read(factory, owner, surface_id, effective_manifest, extract_queries, render_sql, capture_missing=None):
    with factory() as conn:
        surface = conn.execute('SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s::uuid',
                               (str(uuid.UUID(str(surface_id))),)).fetchone()
        if not surface:
            raise LookupError('Artifact not found')
        calliope_access.require_view(conn, surface['session_id'], owner)
        if surface['kind'] == 'query':
            return saved_query(surface)
        if surface['kind'] not in ('artifact', 'image') or not surface.get('artifact_slug') or not surface.get('artifact_version'):
            raise ValueError('Choose a published artifact version')
        artifact = artifact_access.require_view(conn, surface['artifact_slug'], owner)
        document = conn.execute('SELECT html,source_files,manifest FROM rvbbit.dashboard_versions '
                                'WHERE dashboard_id=%s AND version=%s',
                                (artifact['id'], surface['artifact_version'])).fetchone()
        if not document:
            raise LookupError('This published version is no longer available')
        capture = conn.execute('SELECT * FROM rvbbit.artifact_query_captures WHERE dashboard_id=%s AND version=%s',
                               (artifact['id'], surface['artifact_version'])).fetchone()
    manifest = effective_manifest(artifact['id'], surface['artifact_version'], document['manifest'])
    result = assemble(surface, document, manifest, capture, extract_queries, render_sql)
    result['capture_status'] = 'ready' if capture else 'unavailable'
    if not capture and capture_missing:
        result['capture_status'] = capture_missing(dict(dashboard_id=artifact['id'],
            slug=surface['artifact_slug'], version=surface['artifact_version'],
            owner_email=artifact.get('owner_email') or owner))
    return result
