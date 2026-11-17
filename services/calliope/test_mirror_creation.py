"""Exercise ordinary-chat mirror actions against real control-plane SQL."""
import asyncio
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import calliope
import mirror_actions as mirrors
import server
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401
from test_zen_chat_layout import zen_page  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'crates/pg_rvbbit/sql/migrations'


@pytest.fixture
def mirror_db(db, monkeypatch):
    # Use production control-plane tables/functions, excluding views requiring the
    # full accelerator extension. The only stub is catalog-role authorization.
    with db() as conn:
        conn.execute("CREATE FUNCTION rvbbit.require_capability_catalog_admin() RETURNS void LANGUAGE sql AS 'SELECT';"
                     "CREATE TABLE rvbbit.credentials(credential_ref text PRIMARY KEY,status text)")
        conn.execute(next(MIGRATIONS.glob('0287_*.sql')).read_text().split('CREATE OR REPLACE VIEW rvbbit.mirror_lineage')[0])
        detection = next(MIGRATIONS.glob('0315_*.sql')).read_text()
        conn.execute(detection.split('CREATE OR REPLACE FUNCTION rvbbit.record_mirror_source_signature')[0])
        publication = next(MIGRATIONS.glob('0317_*.sql')).read_text()
        conn.execute(publication.split('CREATE TABLE IF NOT EXISTS rvbbit.mirror_publications')[0])
        conn.execute(re.search(r'CREATE OR REPLACE FUNCTION rvbbit.upsert_mirror_job\([\s\S]+?\$upsert_mirror_job\$;', publication)[0])
    actions = {a['id']: a for a in mirrors.catalog()}
    monkeypatch.setattr(calliope, '_action_by_id', lambda _db, _owner, aid: actions.get(aid))
    monkeypatch.setattr(calliope, '_session_for_owner', lambda *_: {'id': SESSION, 'owner_email': OWNER})
    monkeypatch.setattr(calliope, '_session_for_participant', lambda *_: {'id': SESSION, 'owner_email': OWNER})
    return db


def register_source(db, name='source', credential=True):
    with db() as conn:
        conn.execute("SELECT rvbbit.upsert_mirror_connection(%s,'Source','postgresql','{}',true)", (name,))
        if credential:
            conn.execute("INSERT INTO rvbbit.credentials VALUES(%s,'active') ON CONFLICT DO NOTHING", ('mirror/' + name + '/SOURCE_DSN',))


def create(db, **overrides):
    values = dict(connection_name='source', source_schema='public', destination_schema='source_mirror',
                  job_name='test_job', tables=json.dumps([{'source_table': 'orders'}]), run_now=False)
    values.update(overrides)
    return asyncio.run(calliope.administer_action_for_session(db, SESSION, 'mirror.create', values))


def test_create_defaults_manual_and_preserves_configuration_on_retry(mirror_db):
    register_source(mirror_db)
    first = create(mirror_db)
    assert first['approval_required'] is False
    assert first['run']['status'] == 'complete'
    result = first['run']['result']
    assert result['schedule_seconds'] is None and result['queued_run_id'] is None
    assert result['load_complete'] is False and not result['reused']
    with mirror_db() as conn:
        conn.execute("UPDATE rvbbit.mirror_jobs SET schedule_seconds=3600,enabled=false WHERE job_name='test_job'")
    second = create(mirror_db, run_now=True)
    assert second['run']['result']['reused']
    with mirror_db() as conn:
        row = conn.execute("SELECT * FROM rvbbit.mirror_jobs").fetchone()
        assert row['schedule_seconds'] == 3600 and not row['enabled']
        assert conn.execute("SELECT count(*) n FROM rvbbit.mirror_runs").fetchone()['n'] == 0


def test_on_demand_load_is_queued_once_even_with_concurrent_retries(mirror_db):
    register_source(mirror_db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(mirror_db, run_now=True), range(2)))
    assert all(r['run']['status'] == 'complete' for r in results)
    assert sum(not r['run']['result']['reused'] for r in results) == 1
    with mirror_db() as conn:
        runs = conn.execute('SELECT * FROM rvbbit.mirror_runs').fetchall()
        assert len(runs) == 1 and runs[0]['trigger'] == 'manual' and runs[0]['status'] == 'queued'
        assert conn.execute('SELECT next_run_at FROM rvbbit.mirror_jobs').fetchone()['next_run_at'] is None


def test_conflicting_names_and_destinations_do_not_overwrite_jobs(mirror_db):
    register_source(mirror_db)
    create(mirror_db)
    with pytest.raises(ValueError, match='different mappings'):
        create(mirror_db, tables=json.dumps([{'source_table': 'invoices'}]))
    with pytest.raises(ValueError, match='already belongs'):
        create(mirror_db, job_name='other_job')
    with mirror_db() as conn:
        assert conn.execute('SELECT source_table FROM rvbbit.mirror_tables').fetchone()['source_table'] == 'orders'
        assert conn.execute('SELECT count(*) n FROM rvbbit.mirror_jobs').fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM rvbbit.calliope_action_runs WHERE status='failed'").fetchone()['n'] == 2


def test_missing_source_and_bad_incremental_mapping_leave_no_job(mirror_db):
    with pytest.raises(ValueError, match='Connect this SQL source'):
        create(mirror_db)
    register_source(mirror_db, credential=False)
    with pytest.raises(ValueError, match='Connect this SQL source'):
        create(mirror_db)
    with pytest.raises(ValueError, match='primary key'):
        create(mirror_db, tables=json.dumps([{'source_table': 'orders', 'load_mode': 'incremental_upsert'}]))
    with mirror_db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.mirror_jobs').fetchone()['n'] == 0


def test_secure_handoff_projection_retains_source_fields(mirror_db):
    values = {'connection_name': 'source', 'label': 'Source', 'dialect': 'postgresql'}
    result = asyncio.run(calliope.administer_action_for_session(mirror_db, SESSION, 'mirror.connect', values))
    assert result['secure_input_required'] and not result['approval_required']
    assert result['missing_secret_names'] == ['SOURCE_DSN']
    projected = calliope._project_tool_result('connect_mirror_source', result, {'session_id': SESSION, **values}, 'call-source')
    assert projected[0]['payload']['inputs'] == {**values, 'replace_credentials': False}
    assert projected[0]['payload']['form_contract']['adapter'] == 'sql'
    assert projected[0]['payload']['mode'] == 'secure_action_request'
    with pytest.raises(ValueError, match='Secrets cannot'):
        asyncio.run(calliope.administer_action_for_session(mirror_db, SESSION, 'mirror.connect',
                    {**values, 'secret:SOURCE_DSN': 'postgresql://secret@host/db'}))


def test_native_credential_is_stored_once_redacted_and_probe_failure_distinct(mirror_db, monkeypatch):
    secret = 'postgresql://readonly:unique-secret-canary@db.example/source'
    calls = []
    async def worker(factory, method, path, payload=None, **kwargs):
        calls.append((method, path))
        if method == 'PUT':
            assert payload == {'source_dsn': secret}
            with factory() as conn:
                conn.execute("INSERT INTO rvbbit.credentials VALUES('mirror/source/SOURCE_DSN','active')")
            return {'ok': True}
        raise RuntimeError('cannot reach database')
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', worker)
    values = {'connection_name': 'source', 'label': 'Source', 'dialect': 'postgresql', 'secret:SOURCE_DSN': secret}
    result = asyncio.run(calliope.administer_action_with_secure_inputs(mirror_db, OWNER, 'mirror.connect', values, SESSION))
    assert result['run']['status'] == 'complete'
    assert result['run']['verification']['credential_configured']
    assert result['run']['verification']['source_reachable'] is False
    assert 'unique-secret-canary' not in json.dumps(result, default=str)
    # Reuse does not overwrite the saved credentials, including when an old form is resubmitted.
    repeated = asyncio.run(calliope.administer_action_with_secure_inputs(mirror_db, OWNER, 'mirror.connect', values, SESSION))
    assert repeated['run']['result']['reused']
    assert sum(method == 'PUT' for method, _ in calls) == 1
    with mirror_db() as conn:
        assert 'unique-secret-canary' not in str(conn.execute('SELECT * FROM rvbbit.calliope_action_runs').fetchall())


def test_storage_failure_redacts_error_and_allows_repair(mirror_db, monkeypatch):
    secret = 'postgresql://readonly:unique-secret-canary@db.example/source'
    async def worker(*a, **kw):
        raise RuntimeError('worker error for ' + secret)
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', worker)
    values = {'connection_name': 'source', 'label': 'Source', 'dialect': 'postgresql', 'secret:SOURCE_DSN': secret}
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(calliope.administer_action_with_secure_inputs(mirror_db, OWNER, 'mirror.connect', values, SESSION))
    assert 'unique-secret-canary' not in str(failure.value)
    with mirror_db() as conn:
        rows = conn.execute('SELECT * FROM rvbbit.calliope_action_runs').fetchall()
        assert len(rows) == 1 and rows[0]['status'] == 'failed'
        assert 'unique-secret-canary' not in str(rows)
    handoff = asyncio.run(calliope.administer_action_for_session(mirror_db, SESSION, 'mirror.connect', values | {'secret:SOURCE_DSN': ''}))
    assert handoff['secure_input_required']


def test_discovery_uses_worker_and_owner_binding(mirror_db, monkeypatch):
    register_source(mirror_db)
    calls = []
    async def worker(*args, **kw):
        calls.append(args[1:])
        return {'schemas': ['public']} if args[1] == 'GET' else {'tables': [{'name': 'orders'}], 'truncated': False}
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', worker)
    assert mirrors.require_owner(mirror_db, SESSION, OWNER) == OWNER
    with pytest.raises(PermissionError, match='another user'):
        mirrors.require_owner(mirror_db, SESSION, 'other@example.com')
    assert asyncio.run(mirrors.discover(mirror_db, 'source'))['schemas'] == ['public']
    assert asyncio.run(mirrors.discover(mirror_db, 'source', 'public'))['tables'][0]['name'] == 'orders'
    assert calls[0] == ('GET', '/connections/source/schemas')
    assert calls[1][0:2] == ('POST', '/connections/source/discover')


def test_typed_tool_schemas_and_catalog_discoverable():
    from mcp.server.fastmcp.tools import Tool
    connect = Tool.from_function(server._mcp_connect_mirror_source).parameters
    create = Tool.from_function(server._mcp_create_mirror_job).parameters
    assert 'source_dsn' not in connect['properties']
    assert create['properties']['tables']['type'] == 'array'
    assert create['properties']['run_now']['default'] is False
    assert {'mirror.connect', 'mirror.create'} <= {a['id'] for a in calliope._dynamic_administration_actions({'company_admin': True})}
    assert calliope._dynamic_administration_actions({'company_admin': False}) == []


def test_native_source_form_submits_once_and_clears_password(zen_page):
    page = zen_page
    posted = []
    def execute(route):
        posted.append(route.request.post_data_json)
        route.fulfill(json={"run": {"id": "saved", "status": "complete",
            "action_snapshot": {"title": "Connect database source", "executor": "mirror_connect"},
            "verification": {"credential_configured": True, "source_reachable": False}}, "approval_required": False})
    page.route('**/api/calliope/actions/mirror.connect/execute', execute)
    page.evaluate("""() => {
      qa.state.surfaces=[{id:'source-form',kind:'action',title:'Connect source',payload:{
        mode:'secure_action_request',action_id:'mirror.connect',server:'source',missing_secret_names:['SOURCE_DSN'],
        inputs:{connection_name:'source',label:'Source',dialect:'postgresql'}}}];
      qa.els.stage.innerHTML=qa.surfaceCard(qa.state.surfaces[0]);
    }""")
    form = page.locator('[data-stage-secure-action-form]')
    assert 'read-only database URL' in form.inner_text()
    form.locator('input[type=password]').fill('postgresql://readonly:browser-canary@host/db')
    page.evaluate("qa.submitStageSecureAction(document.querySelector('[data-stage-secure-action-form]'))")
    assert len(posted) == 1
    assert posted[0]['inputs'] == {'connection_name': 'source', 'label': 'Source', 'dialect': 'postgresql',
                                    'secret:SOURCE_DSN': 'postgresql://readonly:browser-canary@host/db'}
    assert 'browser-canary' not in page.evaluate('JSON.stringify(qa.state.surfaces)')
    assert page.locator('input[type=password][name=SOURCE_DSN]').count() == 0
    assert 'Credentials saved. The database could not be reached' in page.locator('body').inner_text()
