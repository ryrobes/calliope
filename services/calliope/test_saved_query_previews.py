"""Native boards refresh exact saved queries through the existing Data gate."""
import json
from uuid import uuid4

import pytest
import artifact_queries as queries
from test_data_explorer import explorer, db, OWNER, SESSION  # noqa:F401

SQL = 'SELECT sum(amount) AS total FROM sales.orders'


def saved(source=None, payload=None):
    return {'id': str(uuid4()), 'session_id': SESSION, 'kind': 'query', 'title': 'Orders',
            'source': {'sql': SQL, **(source or {})},
            'payload': {'columns': ['total'], 'rows': [{'total': 'saved private value'}], **(payload or {})}}


@pytest.mark.parametrize('source,payload', [
    ({'args': {'as_of': '2026-09-01'}}, {}),
    ({'args': {'batch': {'as_of': '2026-09-01'}}}, {}),
    ({'derived_from': {'query_context': {'as_of': '2026-09-01'}}}, {}),
    ({}, {'as_of_applied': '2026-09-01'}),
])
def test_exact_scope_identity_and_no_saved_rows_in_inventory(source, payload):
    surface = saved(source, payload)
    result = queries.saved_query(surface)
    item = result['items'][0]
    assert result['kind'] == 'query' and result['surface_id'] == surface['id']
    assert item['can_explore'] and item['context'] == {'as_of': '2026-09-01'}
    assert item['sql'] == SQL and item['tables'] == ['sales.orders']
    assert 'saved private value' not in json.dumps(result)
    assert item['id'] != queries.saved_query(saved())['items'][0]['id']
    assert queries.saved_query(saved({'sql': "SELECT 'a b' FROM sales.orders"}))['items'][0]['id'] != \
        queries.saved_query(saved({'sql': "SELECT 'a  b' FROM sales.orders"}))['items'][0]['id']


@pytest.mark.parametrize('source,payload', [
    ({'sql_truncated': True}, {}), ({'sql': ''}, {}),
    ({'sql': SQL + '...[truncated]'}, {}),
    ({'sql': 'DELETE FROM sales.orders'}, {}),
    ({'sql': SQL + '; DELETE FROM sales.orders'}, {}),
    ({'sql': "SELECT pg_read_file('/etc/passwd') FROM sales.orders"}, {}),
    ({'sheet': {'surface_id': 'sheet'}}, {}),
    ({}, {'sheet': {'snapshot_hash': 'saved-sheet'}}),
    ({}, {'as_of_applied': True}),
])
def test_incomplete_effectful_and_snapshot_queries_are_not_refreshable(source, payload):
    item = queries.saved_query(saved(source, payload))['items'][0]
    assert not item['can_explore'] and item['unavailable_reason']


def test_refresh_reauthorizes_exact_source_and_preserves_saved_receipt(explorer, db):
    exp, ops, charges, _ = explorer
    surface = saved({'args': {'batch': {'as_of': '2026-09-01'}}})
    with db() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,source,payload) '
                     'VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb)',
                     (surface['id'], SESSION, 'query', 'Orders', json.dumps(surface['source']), json.dumps(surface['payload'])))
    original = exp.service
    reads = []
    def service(op, args, subject, owner):
        if op == 'artifact_contents':
            return queries.read(db, owner, args['surface_id'], None, None, None)
        if op == 'artifact_preview':
            reads.append((args, subject, owner))
            return original('run', args, subject, owner)
        return original(op, args, subject, owner)
    exp.service = service
    contents = service('artifact_contents', {'surface_id': surface['id']}, 'reader', OWNER)
    entry = contents['items'][0]['id']
    first = exp.preview_artifact(OWNER, 'reader', surface['id'], entry, refresh=True)
    assert first['result']['rows'] == [{'total': '30'}]
    assert reads[-1][0]['as_of'] == '2026-09-01' and reads[-1][1:] == ('reader', OWNER)
    assert exp.preview_artifact(OWNER, 'reader', surface['id'], entry) == first
    assert len(reads) == 1
    with pytest.raises(PermissionError):
        exp.preview_artifact('other@example.com', 'other', surface['id'], entry)
    with pytest.raises(LookupError):
        exp.preview_artifact(OWNER, 'reader', surface['id'], 'query:forged')
    with db() as conn:
        conn.execute('UPDATE sales.orders SET amount=1')
    second = exp.preview_artifact(OWNER, 'reader', surface['id'], entry, refresh=True)
    assert second['result']['rows'] == [{'total': '2'}] and second['preview_id'] != first['preview_id']
    assert second['context'] == {'as_of': '2026-09-01'}
    assert not exp.recent(OWNER) and not charges
    with db() as conn:
        row = conn.execute('SELECT payload FROM rvbbit.calliope_surfaces WHERE id=%s', (surface['id'],)).fetchone()
        assert row['payload'] == surface['payload']
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 1
    def denied(op, *args):
        if op == 'authorize': raise PermissionError('Dataset grant revoked')
        return service(op, *args)
    exp.service = denied
    with pytest.raises(PermissionError):
        exp.preview_artifact(OWNER, 'reader', surface['id'], entry)
