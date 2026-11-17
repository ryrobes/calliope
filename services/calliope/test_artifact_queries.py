"""Versioned definitions stay private until an explicit, viewer-governed exploration."""
import json
from uuid import uuid4

import pytest
import artifact_queries as queries
import server
from test_data_explorer import explorer, db, OWNER, SESSION  # noqa:F401

SURFACE = {'id': str(uuid4()), 'session_id': SESSION, 'title': 'Orders',
           'kind': 'artifact', 'artifact_slug': 'orders', 'artifact_version': 2}
SQL = 'SELECT sum(amount) AS total FROM sales.orders'


def assembled(capture=None, objects=None, statements=None):
    return queries.assemble(SURFACE, {'html': '', 'source_files': {}},
        {'semantic_map': {'objects': objects or []}}, capture,
        lambda source: statements or [], server._render_semantic_sql)


def test_capture_dedup_identity_and_honest_semantic_links():
    obj = {'id': 'total', 'meaning': {'label': 'Order total'},
           'evaluator': {'sql': SQL}, 'source_queries': ['runtime_2'], 'query_capture_id': 'capture-a'}
    capture = {'capture_id': 'capture-a', 'captured_at': '2026-09-28T10:00:00Z',
               'queries': [{'query_id': 'runtime_1', 'sql': SQL, 'row_count': 1, 'rows': [{'total': 999}]},
                           {'query_id': 'runtime_2', 'sql': SQL}], 'total_queries': 2}
    result = assembled(capture, [obj], [SQL, "SELECT 'a b' FROM sales.orders", "SELECT 'a  b' FROM sales.orders",
                                         'SELECT * FROM ${table}', 'DELETE FROM sales.orders'])
    assert result['version'] == 2
    assert len(result['items']) == 4  # duplicate runtime/source collapsed, literals remain distinct
    first, *_, calc = result['items']
    assert first['source'] == 'captured' and calc['kind'] == 'calculation'
    assert calc['source_query_ids'] == [first['id']]
    assert not any('rows' in item or 'sample_rows' in item for item in result['items'])
    capture['capture_id'] = 'capture-b'
    assert assembled(capture, [obj])['items'][-1]['source_query_ids'] == []
    assert assembled(None, [obj])['items'][0]['source_query_ids'] == []
    assert 'No retained execution capture' in assembled(None, [obj])['note']


def test_incomplete_captures_blocked_but_previous_failures_can_retry():
    result = assembled({'queries': [{'sql': SQL, 'sql_truncated': True},
                                   {'sql': SQL + ' LIMIT 1', 'failed': True}]})
    assert not result['items'][0]['can_explore']
    assert result['items'][0]['unavailable_reason']
    assert result['items'][1]['can_explore']


def test_extract_supported_and_unsupported_functions_reported():
    result = assembled(statements=["SELECT extract(hour from now()) FROM sales.orders",
        "SELECT pg_read_file('/secret') FROM sales.orders"])
    assert result['items'][0]['can_explore']
    assert not result['items'][1]['can_explore']
    assert result['items'][1]['unavailable_reason']


def test_scalar_tiles_require_a_declared_single_row_value():
    item = {'kind': 'calculation', 'evaluator': {'shape': 'scalar', 'value_column': 'ratio'},
            'display': {'format': 'percent', 'decimals': 1}}
    result = {'rows': [{'ratio': '0.25'}], 'columns': ['ratio']}
    assert queries.scalar_result(item, result) == {'value': '0.25', 'column': 'ratio', 'display': item['display']}
    assert queries.scalar_result(item, dict(result, rows=result['rows']*2)) is None
    assert queries.scalar_result(item, dict(result, truncated=True)) is None
    assert queries.scalar_result(dict(item, kind='query'), result) is None
    assert queries.scalar_result(dict(item, evaluator={'shape': 'table'}), result) is None


def test_previews_create_no_drafts_cache_by_viewer_and_fork_server_rows(explorer, db):
    exp, ops, charges, _ = explorer
    contents = assembled(statements=[SQL])
    q = contents['items'][0]
    original, previews = exp.service, []
    allowed = True
    def service(op, args, subject, owner):
        if not allowed:
            raise PermissionError('Grant revoked')
        if op == 'artifact_contents': return contents
        if op == 'artifact_preview':
            previews.append((args, subject, owner))
            return original('run', args, subject, owner)
        return original(op, args, subject, owner)
    exp.service = service
    first = exp.preview_artifact(OWNER, 'reader', SURFACE['id'], q['id'])
    assert first['result']['rows'] == [{'total': '30'}]
    assert exp.preview_artifact(OWNER, 'reader', SURFACE['id'], q['id']) == first
    assert len(previews) == 1 and not exp.recent(OWNER) and not charges
    with db() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 0
        c.execute('UPDATE sales.orders SET amount=1')
    other = exp.preview_artifact(OWNER, 'other-role', SURFACE['id'], q['id'])
    assert len(previews) == 2 and other['preview_id'] != first['preview_id']
    fork = exp.from_artifact(OWNER, 'reader', SURFACE['id'], q['id'], first['preview_id'])
    assert fork['latest_run']['result']['rows'] == [{'total': '30'}]
    assert len(previews) == 2 and len([op for op in ops if op[0]=='run']) == 2
    allowed = False
    with pytest.raises(PermissionError): exp.preview_artifact(OWNER, 'reader', SURFACE['id'], q['id'])
    with pytest.raises(PermissionError): exp.from_artifact(OWNER, 'reader', SURFACE['id'], q['id'], first['preview_id'])
    allowed = True
    refreshed = exp.preview_artifact(OWNER, 'reader', SURFACE['id'], q['id'], refresh=True)
    assert refreshed['preview_id'] != first['preview_id'] and len(previews) == 3
    fork = exp.from_artifact(OWNER, 'reader', SURFACE['id'], q['id'], first['preview_id'])
    assert fork['latest_run']['result']['rows'] != first['result']['rows']


def test_preview_cache_bounds_and_queue_deduplication():
    import threading
    cache = queries.PreviewCache(max_bytes=400)
    for i in range(20): cache.put(i, {'result': 'x'*100})
    assert sum(e[1] for e in cache.entries.values()) <= 400
    assert cache.get(0) is None and cache.get(19)
    cache.ttl = -1
    assert cache.get(19) is None
    queue = queries.CaptureQueue()
    gate, calls = threading.Event(), []
    def work(): calls.append(1); gate.wait(3)
    try:
        assert queue.ensure(('app', 1), work) == 'loading'
        assert queue.ensure(('app', 1), work) == 'loading'
        gate.set()
        queue.pool.shutdown(wait=True)
        assert queue.ensure(('app', 1), work) == 'ready'
        assert calls == [1]
    finally:
        gate.set()
        queue.pool.shutdown(wait=True)


def test_record_read_version_and_separate_access_boundaries(db, monkeypatch):
    with db() as c:
        c.execute('CREATE TABLE rvbbit.dashboard_versions(dashboard_id bigint,version int,html text,source_files jsonb,manifest jsonb,PRIMARY KEY(dashboard_id,version));'
                  'CREATE TABLE rvbbit.calliope_surfaces(id uuid,session_id uuid,kind text,title text,artifact_slug text,artifact_version int)')
        c.execute(queries.DDL)
        c.execute('INSERT INTO rvbbit.dashboard_versions VALUES(1,1,\'old\',\'{}\',\'{}\'),(1,2,\'new\',\'{}\',\'{}\')')
        c.execute('INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,%s,%s,%s)', tuple(SURFACE[k] for k in ['id','session_id','kind','title','artifact_slug','artifact_version']))
    qs = [{'query_id': f'runtime_{i}', 'sql': SQL, 'rows': [{'secret': 'owner only'}], 'sample_rows': ['private'],
           'error': 'sensitive runtime detail' if i == 1 else None} for i in range(205)]
    capture_id = queries.record(db, {'dashboard_id': 1, 'version': 2}, qs)
    with db() as c:
        retained = c.execute('SELECT * FROM rvbbit.artifact_query_captures').fetchone()
    queries.record(db, {'dashboard_id': 1, 'version': 2}, [], only_missing=True)
    with db() as c:
        assert str(c.execute('SELECT capture_id FROM rvbbit.artifact_query_captures').fetchone()['capture_id']) == capture_id
    assert str(retained['capture_id']) == capture_id
    assert retained['total_queries'] == 205 and len(retained['queries']) == 200
    assert 'owner only' not in json.dumps(retained['queries']) and 'sensitive runtime' not in json.dumps(retained['queries'])
    calls = []
    monkeypatch.setattr(queries.calliope_access, 'require_view', lambda c, sid, owner: calls.append(('chat', str(sid), owner)))
    monkeypatch.setattr(queries.artifact_access, 'require_view', lambda c, slug, owner: calls.append(('artifact', slug, owner)) or {'id': 1})
    def effective(did, version, manifest):
        calls.append(('manifest', did, version))
        return manifest
    result = queries.read(db, OWNER, SURFACE['id'], effective, lambda source: [], server._render_semantic_sql)
    assert result['version'] == 2 and result['capture_total'] == 205
    assert calls == [('chat', SESSION, OWNER), ('artifact', 'orders', OWNER), ('manifest', 1, 2)]
    def forbidden(*args): raise PermissionError('no artifact access')
    monkeypatch.setattr(queries.artifact_access, 'require_view', forbidden)
    with pytest.raises(PermissionError):
        queries.read(db, OWNER, SURFACE['id'], effective, lambda source: [], server._render_semantic_sql)


def test_explore_resolves_definition_runs_as_viewer_and_retains_origin(explorer, db):
    exp, operations, charges, _ = explorer
    contents = assembled(statements=[SQL])
    item = contents['items'][0]
    item['context'] = {'as_of': '2026-09-01T00:00:00Z'}
    original = exp.service
    def service(op, args, subject, owner):
        if op == 'artifact_contents':
            assert args == {'surface_id': SURFACE['id']} and subject == 'reader' and owner == OWNER
            return contents
        return original(op, args, subject, owner)
    exp.service = service
    with pytest.raises(LookupError): exp.from_artifact(OWNER, 'reader', SURFACE['id'], 'query:forged')
    draft = exp.from_artifact(OWNER, 'reader', SURFACE['id'], item['id'])
    assert draft['latest_run']['result']['rows'] == [{'total': '30'}]
    assert draft['context']['origin']['artifact_version'] == 2
    assert draft['context']['origin']['artifact_query_id'] == item['id']
    assert draft['context']['origin']['sql_sha256'] == item['sql_sha256']
    run = next(op for op in operations if op[0] == 'run')
    assert run[1]['as_of'] == '2026-09-01T00:00:00Z' and run[2:] == ('reader', OWNER)
    assert not charges  # no model needed to open a definition
    with db() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 0
    item['sql'] = 'DELETE FROM sales.orders'
    with pytest.raises(ValueError): exp.from_artifact(OWNER, 'reader', SURFACE['id'], item['id'])


def test_generated_capture_identity_survives_merge_without_marking_authored_objects():
    authored = {'semantic_map': {'objects': [{'id': 'authored', 'evaluator': {'sql': SQL}}]}}
    generated = {'id': 'generated', 'query_capture_id': 'capture-a', 'evaluator': {'sql': SQL}}
    result = server._merge_semantic_overlay(authored, {'status': 'ready', 'semantic_map': {'objects': [generated]}})
    assert 'query_capture_id' not in result['semantic_map']['objects'][0]
    assert result['semantic_map']['objects'][1]['query_capture_id'] == 'capture-a'


def test_explicit_publication_preserves_version_origin_and_rechecks_artifact_access(explorer, db, monkeypatch):
    import calliope
    exp, _, _, _ = explorer
    contents = assembled(statements=[SQL])
    original = exp.service
    exp.service = lambda op,args,subject,owner: contents if op == 'artifact_contents' else original(op,args,subject,owner)
    draft = exp.from_artifact(OWNER, 'reader', SURFACE['id'], contents['items'][0]['id'])
    monkeypatch.setattr(calliope.calliope_access, 'require_participant', lambda *args: None)
    def denied(*args): raise PermissionError('Artifact grant revoked')
    monkeypatch.setattr(calliope.artifact_access, 'require_view', denied)
    with pytest.raises(PermissionError):
        calliope._publish_data_run(db, exp.service, OWNER, 'reader', str(draft['latest_run']['id']), SESSION)
    with db() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_turns').fetchone()['n'] == 0
    monkeypatch.setattr(calliope.artifact_access, 'require_view', lambda *args: {'id': 1})
    def insert(factory, sid, tid, surfaces):
        surface = dict(surfaces[0], id=str(uuid4()), session_id=sid)
        with factory() as c:
            c.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,payload,source) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb)',
                      (surface['id'], sid, surface['kind'], surface['title'], json.dumps(surface['payload']), json.dumps(surface['source'])))
        return [surface]
    monkeypatch.setattr(calliope, '_insert_surfaces', insert)
    surface = calliope._publish_data_run(db, exp.service, OWNER, 'reader', str(draft['latest_run']['id']), SESSION)['surface']
    assert surface['parent_surface_id'] == SURFACE['id']
    origin = surface['source']['derived_from']
    assert (origin['artifact_slug'], origin['artifact_version'], origin['artifact_query_id']) == ('orders', 2, contents['items'][0]['id'])
    assert origin['sql_sha256'] == contents['items'][0]['sql_sha256']


def test_opaque_chart_aliases_show_the_underlying_dimension():
    assert queries.query_title('SELECT class AS val, count(*) AS n FROM public.reports GROUP BY 1',
        ['val','n'], ['public.reports']) == 'Class, count · reports'
    assert queries.query_title('SELECT season AS val, count(*) AS n FROM public.reports GROUP BY 1',
        ['val','n'], ['public.reports']) == 'Season, count · reports'


def test_generated_date_spine_is_a_function_not_an_unauthorized_table():
    sql = """WITH days AS (SELECT d::date AS day FROM
        generate_series('2010-07-01'::date, '2013-07-31'::date, interval '1 day') AS g(d)
        WHERE extract(month FROM d)=7)
        SELECT days.day,count(o.*) FROM days LEFT JOIN sales.orders o ON true GROUP BY days.day"""
    assert queries.query_sources(sql) == ['sales.orders']
    assert assembled(statements=[sql])['items'][0]['can_explore']
    with pytest.raises(ValueError):
        queries.query_sources('SELECT * FROM public.unsafe_function(), sales.orders')
