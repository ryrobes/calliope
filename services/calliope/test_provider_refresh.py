"""Catalog refresh guarantees, including optional tests against an EMPTY PostgreSQL DB."""
import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest
import provider_settings as settings

ROOT = Path(__file__).resolve().parents[2]


def inventory(*models):
    return {"ok": True, "providers": [{"id": "openrouter", "configured": True,
        "auth_kind": "api_key", "models": list(models)}]}


@pytest.fixture
def db(monkeypatch):
    dsn = os.environ.get('CALLIOPE_PROVIDER_TEST_DSN')
    if not dsn:
        pytest.skip('Set CALLIOPE_PROVIDER_TEST_DSN to an empty disposable database')
    def factory():
        return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    with factory() as c:
        assert not c.execute("SELECT to_regnamespace('rvbbit') AS n").fetchone()['n']
        c.execute('CREATE SCHEMA rvbbit')
        catalog = (ROOT/'crates/pg_rvbbit/src/provider_catalog.rs').read_text()
        c.execute(catalog[catalog.index('CREATE TABLE rvbbit.provider_catalog'):catalog.index('CREATE TABLE rvbbit.model_rate_cards')])
        c.execute('''CREATE TABLE rvbbit.cost_latest(created_at timestamptz,backend text,model text,
            tokens_in bigint,tokens_out bigint,status text,cost_usd numeric,source text)''')
        settings.ensure(factory)
    for name, result in [('agents', []), ('current_fallback', None), ('current_auxiliary', None), ('loadouts', [])]:
        monkeypatch.setattr(settings, name, lambda *_, result=result: result)
    try:
        yield factory
    finally:
        with factory() as c:
            c.execute('DROP SCHEMA rvbbit CASCADE')


def test_first_discovery_and_later_open_is_read_only(db, monkeypatch):
    assert not settings.summary(db)['inventory_initialized']  # seed Clover row is not a sync
    calls = []
    monkeypatch.setattr(settings, '_control', lambda *a, **kw: calls.append(kw) or inventory('one'))
    settings.refresh(db, models=True, only_if_empty=True)
    assert settings.summary(db)['inventory_initialized']
    assert settings.refresh(db, models=True, only_if_empty=True)['skipped']
    assert len(calls) == 1
    def readonly():
        c = db()
        c.execute('SET default_transaction_read_only = on')
        return c
    assert [x['model'] for x in settings.summary(readonly)['models']] == ['one']
    assert len(calls) == 1


def test_batch_ownership_dedup_and_atomic_rollback(db):
    settings._sync_inventory(db, inventory('old'))
    with db() as c:
        c.execute("INSERT INTO rvbbit.provider_models(provider,model,source,capabilities,available) VALUES('openrouter','owned','provider_api','[\"vision\"]',false)")
    settings._sync_inventory(db, inventory('new', 'new', 'owned'))
    with db() as c:
        rows = c.execute('SELECT model,source,capabilities,available FROM rvbbit.provider_models ORDER BY model').fetchall()
        assert [r['model'] for r in rows] == ['new', 'owned']
        assert rows[1] == dict(model='owned',source='provider_api',capabilities=['vision'],available=False)
        before = c.execute('SELECT * FROM rvbbit.calliope_provider_state ORDER BY provider').fetchall()
    broken = inventory('bad')
    broken['providers'][0]['capabilities'] = {'bad': {'context_window': 'not-an-integer'}}
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        settings._sync_inventory(db, broken)
    with db() as c:
        assert c.execute('SELECT * FROM rvbbit.calliope_provider_state ORDER BY provider').fetchall() == before
        assert c.execute('SELECT model,source,capabilities,available FROM rvbbit.provider_models ORDER BY model').fetchall() == rows


@pytest.mark.parametrize('bad', [{}, {'ok':False,'providers':[]}, {'providers':[]}, inventory(), inventory('\n')])
def test_bad_inventory_preserves_saved_catalog(db, bad):
    settings._sync_inventory(db, inventory('old'))
    with pytest.raises(settings.ProviderControlError):
        settings._sync_inventory(db, bad)
    assert [x['model'] for x in settings.summary(db)['models']] == ['old']


def test_network_fetch_holds_no_catalog_locks_and_overlaps_are_rejected(db, monkeypatch):
    settings._sync_inventory(db, inventory('old'))
    def fetch(*_, **kwargs):
        with db() as c:
            locks = c.execute("SELECT count(*) AS n FROM pg_locks WHERE relation IN ('rvbbit.provider_models'::regclass,'rvbbit.calliope_provider_state'::regclass) AND mode <> 'AccessShareLock'").fetchone()['n']
            assert locks == 0
        assert settings.summary(db)['models'][0]['model'] == 'old'
        with pytest.raises(settings.ProviderControlError) as err:
            settings.refresh(db, models=True)
        assert err.value.status == 409
        return inventory('new')
    monkeypatch.setattr(settings, '_control', fetch)
    settings.refresh(db, models=True)
    # Also verify the advisory lock was released on network failure.
    monkeypatch.setattr(settings, '_control', lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        settings.refresh(db, models=True)
    monkeypatch.setattr(settings, '_control', lambda *_a, **_k: inventory('recovered'))
    settings.refresh(db, models=True)


def test_readers_see_old_complete_catalog_until_publish(db):
    settings._sync_inventory(db, inventory('old'))
    with db() as gate:
        gate.execute('SELECT pg_advisory_lock(741209,99)')
        gate.execute('''CREATE FUNCTION rvbbit.pause_catalog() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_advisory_xact_lock(741209,99); RETURN NEW; END $$;
            CREATE TRIGGER pause_catalog BEFORE UPDATE ON rvbbit.provider_catalog
            FOR EACH ROW EXECUTE FUNCTION rvbbit.pause_catalog()''')
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(settings._sync_inventory, db, inventory('new','new-two'))
            try:
                deadline = time.monotonic()+5
                while not gate.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=99 AND NOT granted) AS waiting").fetchone()['waiting']:
                    assert not pending.done() and time.monotonic() < deadline
                    time.sleep(.01)
                def bounded_reader():
                    c = db(); c.execute("SET statement_timeout='1s'"); return c
                snap = settings.summary(bounded_reader)
                assert [x['model'] for x in snap['models']] == ['old']
                assert next(x for x in snap['providers'] if x['provider']=='openrouter')['model_count'] == 1
            finally:
                gate.execute('SELECT pg_advisory_unlock(741209,99)')
            pending.result(timeout=5)
    assert [x['model'] for x in settings.summary(db)['models']] == ['new','new-two']


def test_get_route_never_refreshes_even_with_legacy_refresh_query():
    tree = ast.parse((ROOT/'services/calliope/calliope.py').read_text())
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name=='provider_admin_summary')
    func.decorator_list = []
    async def access(*_, **kwargs):
        assert kwargs == {'mutation':False}
        return 'admin', None
    def forbidden(*_, **__):
        raise AssertionError('GET must never refresh')
    ns = dict(asyncio=asyncio, hermes_admin_access=access, conn_factory=object(),
        provider_settings=SimpleNamespace(summary=lambda _: {'models':['saved']},refresh=forbidden,
            ProviderControlError=settings.ProviderControlError),json_response=lambda data,*_: data)
    exec(compile(ast.Module(body=[func], type_ignores=[]), '<provider-route>', 'exec'),ns)
    assert asyncio.run(ns['provider_admin_summary'](SimpleNamespace(query_params={'refresh':'true'}))) == {'models':['saved']}


def test_browser_open_refresh_first_use_and_failed_refresh():
    source = (ROOT/'services/calliope/calliope/calliope.js').read_text()
    fn = source[source.index('  async function loadProviderSettings('):source.index('  async function mutateProviderSettings(')]
    script = '''const assert=require('node:assert/strict');
const state={providerSettings:{loading:false,data:null}};
let requests=[], next=[], messages=[], speechSettings;
// Speech settings load independently and must not consume the catalog fixtures.
const api=async(path,opts={})=>{if(path==='/api/calliope/speech')return {};requests.push([path,opts]);const r=next.shift();if(r instanceof Error)throw r;return r};
const providerAdminOptions=(method,body)=>({method,headers:{'X-Calliope-Admin':'1'},body:JSON.stringify(body)});
const providerSettingsMessage=(msg)=>messages.push(msg);
const renderProviderSettings=()=>{};
''' + fn + '''
(async()=>{
 const saved={inventory_initialized:true,models:['saved']};
 next=[saved];await loadProviderSettings();assert.equal(requests.length,1);assert.equal(requests[0][0],'/api/calliope/admin/providers');assert.equal(requests[0][1].method,undefined);
 next=[new Error('refresh failed')];await assert.rejects(loadProviderSettings(true));assert.equal(state.providerSettings.data,saved);assert.equal(requests.at(-1)[1].method,'POST');assert.equal(requests.at(-1)[1].headers['X-Calliope-Admin'],'1');
 requests=[];next=[{inventory_initialized:false,models:[]},saved];await loadProviderSettings();assert.equal(requests.length,2);assert.equal(JSON.parse(requests[1][1].body).only_if_empty,true);assert.equal(state.providerSettings.data,saved);
 state.providerSettings.loading=true;requests=[];await loadProviderSettings(true);assert.equal(requests.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node','-e',script], check=True, capture_output=True, text=True)


def test_partial_inventory_does_not_disconnect_omitted_provider(db):
    both = inventory('old')
    both['providers'].append({'id':'clover','configured':True,'auth_kind':'environment','models':['calliope']})
    settings._sync_inventory(db, both)
    settings._sync_inventory(db, inventory('new'))
    with db() as c:
        assert c.execute("SELECT auth_state FROM rvbbit.calliope_provider_state WHERE provider='clover'").fetchone()['auth_state'] == 'configured'


def test_busy_catalog_writer_times_out_without_partial_publish(db):
    settings._sync_inventory(db, inventory('old'))
    with db() as writer:
        with writer.transaction():
            writer.execute("SELECT provider FROM rvbbit.calliope_provider_state WHERE provider='openrouter' FOR UPDATE")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                settings._sync_inventory(db, inventory('new'))
    assert [x['model'] for x in settings.summary(db)['models']] == ['old']


def test_refresh_route_requires_mutation_authorization():
    tree = ast.parse((ROOT/'services/calliope/calliope.py').read_text())
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name=='provider_admin_refresh')
    func.decorator_list = []
    async def access(*_, **kwargs):
        assert kwargs == {'mutation':True}
        return None, 'denied'
    ns = dict(hermes_admin_access=access)
    exec(compile(ast.Module(body=[func], type_ignores=[]), '<provider-route>', 'exec'), ns)
    assert asyncio.run(ns['provider_admin_refresh'](object())) == 'denied'
