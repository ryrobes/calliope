"""Real catalog drift and stale-approval regressions on a disposable database."""
import json
import os
import uuid

import pytest
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

import calliope
import local_sql_review as review
from test_zen_chat_layout import zen_page  # noqa: F401

OWNER = 'admin@example.com'
SESSION = str(uuid.uuid4())


@pytest.fixture
def sql_db():
    admin = os.environ.get('ACTIVITY_TEST_ADMIN_DSN')
    if not admin:
        pytest.skip('ACTIVITY_TEST_ADMIN_DSN must identify a disposable test cluster')
    name = 'calliope_sql_review_' + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8'")
    def factory():
        return psycopg.connect(make_conninfo(admin, dbname=name), row_factory=dict_row)
    try:
        with factory() as conn:
            conn.execute('CREATE SCHEMA rvbbit; CREATE SCHEMA ops; CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text)')
            conn.execute('INSERT INTO rvbbit.calliope_sessions VALUES(%s,%s)', (SESSION, OWNER))
            conn.execute(calliope._ACTION_DDL)
        yield factory
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE {name} WITH (FORCE)')


def plan(db, sql):
    return calliope._create_local_admin_sql_run(db, OWNER, SESSION, calliope._local_admin_sql_contract(sql))


@pytest.mark.parametrize('sql', [
    'CREATE TABLE ops.example(id bigint)',
    'CREATE OR REPLACE VIEW ops.example AS SELECT 1 AS id',
    'CREATE FUNCTION ops.example(x integer) RETURNS integer LANGUAGE sql AS $$ SELECT x $$',
    'CREATE TABLE "ops"."Mixed.Name" (id bigint)',
])
def test_external_creation_retires_receipt_and_stays_retired(sql_db, sql):
    run = plan(sql_db, sql)
    assert calliope._action_run_for_owner(sql_db, OWNER, run['id'])['verification'] == {}
    with sql_db() as conn:
        conn.execute(sql)
    refreshed = calliope._action_run_for_owner(sql_db, OWNER, run['id'])
    assert refreshed['verification']['local_sql_review']['reason'] == 'target_exists'
    assert refreshed['approved_at'] is None
    assert refreshed['status'] == 'planned'  # no false claim that this receipt executed
    with pytest.raises(ValueError, match='retired'):
        calliope._execute_local_admin_sql_run(sql_db, OWNER, run, explicitly_approved=True)
    assert calliope._action_run_for_owner(sql_db, OWNER, run['id'])['verification'] == refreshed['verification']


def test_replace_baseline_allows_intended_change_but_rejects_drift(sql_db):
    with sql_db() as conn:
        conn.execute('CREATE VIEW ops.dashboard AS SELECT 1 AS id')
    run = plan(sql_db, 'CREATE OR REPLACE VIEW ops.dashboard AS SELECT 2 AS id')
    assert not calliope._action_run_for_owner(sql_db, OWNER, run['id'])['verification']
    with sql_db() as conn:
        conn.execute('CREATE OR REPLACE VIEW ops.dashboard AS SELECT 3 AS id')
    with pytest.raises(ValueError, match='changed'):
        calliope._execute_local_admin_sql_run(sql_db, OWNER, run, explicitly_approved=True)
    with sql_db() as conn:
        assert conn.execute('SELECT id FROM ops.dashboard').fetchone()['id'] == 3
    fresh = plan(sql_db, 'CREATE OR REPLACE VIEW ops.dashboard AS SELECT 4 AS id')
    result = calliope._execute_local_admin_sql_run(sql_db, OWNER, fresh, explicitly_approved=True)
    assert result['run']['status'] == 'complete'
    with sql_db() as conn:
        assert conn.execute('SELECT id FROM ops.dashboard').fetchone()['id'] == 4


def test_alter_detects_column_drift_and_legacy_replacement_requires_fresh_plan(sql_db):
    with sql_db() as conn:
        conn.execute('CREATE TABLE ops.example(id bigint)')
    run = plan(sql_db, 'ALTER TABLE ops.example ADD COLUMN label text')
    with sql_db() as conn:
        conn.execute('ALTER TABLE ops.example ADD COLUMN outside_change boolean')
    assert calliope._action_run_for_owner(sql_db, OWNER, run['id'])['verification']['local_sql_review']['reason'] == 'target_changed'
    old = plan(sql_db, 'CREATE OR REPLACE VIEW ops.dashboard AS SELECT 1 AS id')
    with sql_db() as conn:
        conn.execute("UPDATE rvbbit.calliope_action_runs SET plan=plan-'local_sql_guard' WHERE id=%s", (old['id'],))
    assert calliope._action_run_for_owner(sql_db, OWNER, old['id'])['verification']['local_sql_review']['reason'] == 'missing_baseline'


def test_canonical_receipts_replace_stale_stage_snapshots(sql_db):
    sql = 'CREATE TABLE ops.example(id bigint)'
    run = plan(sql_db, sql)
    stale = {'id': 'surface', 'kind': 'action', 'payload': {'run': run, 'message': 'Waiting for approval'}}
    done = calliope._execute_local_admin_sql_run(sql_db, OWNER, run, explicitly_approved=True)['run']
    result = calliope._refresh_local_sql_surfaces(sql_db, SESSION, OWNER, [stale, stale])
    assert all(s['payload']['run']['status'] == 'complete' for s in result)
    assert result[0]['payload']['run']['id'] == done['id']
    assert not result[0]['payload']['approval_required'] and not result[0]['payload']['message']
    assert stale['payload']['run']['status'] == 'planned'
    assert calliope._action_run_for_owner(sql_db, 'other@example.com', run['id']) is None


def test_catalog_check_never_executes_select_functions_or_guesses_unqualified_names(sql_db):
    assert review.target("SELECT ops.create_something()") is None
    assert review.target('CREATE TABLE example(id bigint)') is None
    assert review.target('INSERT INTO ops.example VALUES(1)') is None
    run = plan(sql_db, 'CREATE TABLE ops.example(id bigint)')
    class Offline:
        def transaction(self):
            raise RuntimeError('offline')
    assert review.refresh(Offline(), run)['verification']['local_sql_review']['state'] == 'unavailable'
    with sql_db() as conn:
        assert conn.execute("SELECT to_regclass('ops.example') AS relation").fetchone()['relation'] is None


def test_retired_and_executed_receipts_remain_readable_without_approval(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const action={id:'local-sql',executor:'local_sql',receipt_only:true,category:'admin',title:'Run local RVBBIT CREATE SQL'};
      const base={action_snapshot:action,input_redacted:{sql:'CREATE TABLE ops.example(id bigint)',sql_sha256:'a'.repeat(64),statement_type:'create'}};
      qa.state.surfaces=[
        {id:'retired',turn_id:'one',kind:'action',title:action.title,payload:{action,run:{...base,id:'retired-run',status:'planned',
          verification:{local_sql_review:{state:'retired',message:'Target already exists. Request a fresh plan.'}}}}},
        {id:'executed',turn_id:'one',kind:'action',title:action.title,payload:{action,run:{...base,id:'done-run',status:'complete'}}},
        {id:'pending',turn_id:'one',kind:'action',title:action.title,payload:{action,run:{...base,id:'pending-run',status:'planned'}}}
      ];
      qa.renderChat();qa.renderStage();
    }''')
    assert page.locator('[data-approve-local-sql]').count() == 1
    assert page.locator('[data-approve-local-sql]').get_attribute('data-approve-local-sql') == 'pending-run'
    retired = page.locator('.action-surface.is-retired')
    assert 'Target already exists' in retired.inner_text()
    retired.locator('summary').click()
    assert retired.locator('pre').is_visible()
    assert 'CREATE TABLE ops.example' in retired.locator('pre').inner_text()
    assert 'line-through' in retired.locator('h4').evaluate('(el)=>getComputedStyle(el).textDecorationLine')
    executed = page.locator('[data-sql-execution]')
    assert 'Complete' in executed.inner_text()
    assert 'CREATE TABLE ops.example' in executed.locator('pre').inner_text()
    assert 'line-through' not in executed.locator('summary').first.evaluate('(el)=>getComputedStyle(el).textDecorationLine')
    page.screenshot(path='/tmp/local-sql-retired-receipts.png')
