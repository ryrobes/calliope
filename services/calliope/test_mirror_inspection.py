"""Managed DLT inspection, independent of source access and worker availability."""
import asyncio
import uuid

import pytest

import calliope
import server
from test_workspace_sql import db  # noqa: F401 — isolated disposable PostgreSQL


@pytest.fixture
def mirror_db(db, monkeypatch):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.mirror_runs(
            run_id uuid PRIMARY KEY, job_name text, trigger text, status text,
            attempt int, requested_at timestamptz, started_at timestamptz,
            heartbeat_at timestamptz, finished_at timestamptz, tables_succeeded int,
            tables_failed int, rows_loaded bigint, error_code text, error_message text);
            CREATE TABLE rvbbit.mirror_table_runs(
            run_id uuid, source_table text, destination_table text, load_mode text,
            status text, rows_loaded bigint, started_at timestamptz,
            finished_at timestamptz, error_code text, error_message text)''')
    monkeypatch.setattr(calliope, '_owner_for_calliope_session', lambda *_: 'admin@example.com')
    monkeypatch.setattr(calliope, '_setup_mirror_snapshot', lambda *_: {
        'installed': True, 'available': True, 'credential_store_ready': True,
        'worker': {'registered': True, 'warehouse_authenticated': True, 'authenticated': True},
        'connections': [{
            'connection_name': 'source', 'label': 'Source', 'dialect': 'postgresql',
            'enabled': True, 'credential_configured': True, 'credential_ref': 'private-ref',
            'metadata': {'password': 'never-render-this'},
            'jobs': [{'job_name': 'job', 'enabled': True, 'table_count': 1,
                'tables': [{'source_table': 'orders', 'destination_table': 'orders',
                    'load_mode': 'incremental_upsert', 'primary_key': ['id'],
                    'cursor_column': 'updated_at', 'enabled': True}]}],
        }],
    })
    async def health(*a, **kw):
        assert a[1:3] == ('GET', '/health')
        return {'ok': True, 'controller_auth_configured': True, 'dlt_version': 'test'}
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', health)
    return db


def status(db, **kw):
    return asyncio.run(calliope.mirror_status_for_session(db, 'session', **kw))


def test_job_index_and_bounded_history_are_not_credentials(mirror_db):
    with mirror_db() as c:
        for day in range(1, 4):
            c.execute("INSERT INTO rvbbit.mirror_runs(run_id,job_name,status,requested_at) VALUES(%s,'job','succeeded',%s)",
                      (uuid.uuid4(), f'2026-09-0{day}T00:00:00Z'))
    result = status(mirror_db, limit=2)
    assert result['installed'] and result['controller']['available']
    assert len(result['runs']) == 2 and result['runs_truncated']
    assert result['runs'][0]['requested_at'] > result['runs'][1]['requested_at']
    assert 'tables' not in result['jobs'][0]
    assert 'private-ref' not in str(result) and 'never-render-this' not in str(result)
    detail = status(mirror_db, job_name='job')
    assert detail['jobs'][0]['tables'][0]['cursor_column'] == 'updated_at'
    assert detail['capabilities']['arbitrary_dlt_jobs'] is False


def test_run_details_remain_available_with_worker_down(mirror_db, monkeypatch):
    run_id = str(uuid.uuid4())
    with mirror_db() as c:
        c.execute("INSERT INTO rvbbit.mirror_runs(run_id,job_name,status,requested_at) VALUES(%s,'job','failed',now())", (run_id,))
        c.execute("INSERT INTO rvbbit.mirror_table_runs(run_id,source_table,status,error_message) VALUES(%s,'orders','failed','password=not-for-chat')", (run_id,))
    async def down(*a, **kw):
        raise ConnectionError('worker offline')
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', down)
    result = status(mirror_db, run_id=run_id)
    assert result['installed']
    assert result['controller']['available'] is False
    assert result['runs'][0]['run_id'] == run_id
    assert result['run_tables'][0]['status'] == 'failed'
    assert 'not-for-chat' not in str(result)
    with pytest.raises(LookupError, match='run not found'):
        status(mirror_db, run_id=str(uuid.uuid4()))


def test_mirror_filters_fail_explicitly(mirror_db):
    with pytest.raises(LookupError, match='job not found'):
        status(mirror_db, job_name='missing')
    for kw in [{'run_id':'not-a-uuid'}, {'limit':0}, {'limit':101}]:
        with pytest.raises(ValueError):
            status(mirror_db, **kw)


def test_runtime_pack_installation_does_not_require_operators(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.capability_catalog(name text, runtime_name text, kind text, active bool);
            CREATE TABLE rvbbit.python_runtimes(name text,status text);
            CREATE TABLE rvbbit.memory_services(name text,status text);
            CREATE TABLE rvbbit.mcp_gateways(name text,status text);
            INSERT INTO rvbbit.capability_catalog VALUES('dlt_mirror','dlt_mirror','runtime_sidecar',true);
            INSERT INTO rvbbit.python_runtimes VALUES('dlt_mirror','ready')''')
        rows = [{'kind':'cap_pack','name':'dlt_mirror','doc':'status: available NOT currently installed — suggest installation to the user; never install autonomously.'}]
        result = server._refresh_runtime_pack_matches(c, rows)
        assert 'status: installed' in result[0]['doc']
        assert 'NOT currently installed' not in result[0]['doc']
        assert 'mirror_status' in result[0]['doc']
        catalog = [{'kind':'runtime_sidecar','name':'dlt_mirror','runtime_ready':False}]
        assert calliope._refresh_capability_runtime_readiness(c, catalog)[0]['runtime_ready']
        c.execute("UPDATE rvbbit.python_runtimes SET status='failed'")
        assert not calliope._refresh_capability_runtime_readiness(c, catalog)[0]['runtime_ready']
        assert calliope._capability_runtime_facts(c,['dlt_mirror'])['dlt_mirror']['installed']
        c.execute('DELETE FROM rvbbit.python_runtimes')
        result = server._refresh_runtime_pack_matches(c, result)
        assert 'status: available' in result[0]['doc']
        assert not calliope._capability_runtime_facts(c,['dlt_mirror'])['dlt_mirror']['installed']
