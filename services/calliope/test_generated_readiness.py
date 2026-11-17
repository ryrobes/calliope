import io
import os
from pathlib import Path
import urllib.error
import pytest
import psycopg
from psycopg.rows import dict_row
import domain_agent_factory as factory
from test_hermes_provider_supervisor import _supervisor_module


def test_supervisor_defers_even_reading_profiles_while_busy(monkeypatch,tmp_path):
    m=_supervisor_module(monkeypatch,tmp_path);s=m.GatewaySupervisor()
    monkeypatch.setattr(s,'_gateway_probe',lambda:{'gateway_busy':True,'active_agents':1})
    monkeypatch.setattr(m.runpy,'run_path',lambda *_:pytest.fail('must not reconcile busy runtime'))
    assert s.reconcile_generated_profiles()['status']=='waiting_for_idle'


def test_supervisor_preserves_bindings_and_rechecks_idle(monkeypatch,tmp_path):
    m=_supervisor_module(monkeypatch,tmp_path);s=m.GatewaySupervisor()
    m.CONFIG_PATH.write_text('gateway: {multiplex_profile_allowlist: []}\n')
    s.runtime_env={'TEST_BINDING':'private-test-value'};calls=[]
    monkeypatch.setattr(s,'_gateway_probe',lambda:{'gateway_busy':False,'active_agents':0})
    monkeypatch.setattr(m.runpy,'run_path',lambda *_:{'_fetch_generated_specialists':lambda _: [{'specialist_key':'test-domain'}], 'reconcile':lambda _:calls.append('installed')})
    monkeypatch.setattr(m.POLICY,'reconcile_payload',lambda c:c.update(gateway={'multiplex_profile_allowlist':['test-domain']}))
    def apply(raw,expected,revision,bindings):
        assert calls==['installed'];assert bindings==s.runtime_env
        assert b'test-domain' in raw
        return {'ok':True}
    monkeypatch.setattr(s,'apply',apply)
    assert s.reconcile_generated_profiles()['status']=='reloaded'
    health=iter([{'gateway_busy':False,'active_agents':0},{'gateway_busy':True,'active_agents':1}])
    monkeypatch.setattr(s,'_gateway_probe',lambda:next(health))
    monkeypatch.setattr(s,'apply',lambda *_:pytest.fail('new work arrived'))
    assert s.reconcile_generated_profiles()['status']=='waiting_for_idle'


@pytest.fixture
def db():
    dsn=os.environ.get('GENERATED_READINESS_TEST_DSN')
    if not dsn:pytest.skip('requires an empty disposable database')
    def cf():return psycopg.connect(dsn,row_factory=dict_row)
    with cf() as c:
        assert c.execute("select to_regnamespace('rvbbit') n").fetchone()['n'] is None
        c.execute("CREATE SCHEMA rvbbit; CREATE TABLE rvbbit.generated_specialists(specialist_key text primary key,status text default 'active'); CREATE TABLE rvbbit.calliope_specialists(specialist_key text primary key,profile_name text,status text default 'active' CONSTRAINT calliope_specialists_status_check CHECK(status IN ('active','paused','disabled')),updated_at timestamptz);")
        c.execute("INSERT INTO rvbbit.generated_specialists VALUES ('tyche','active'),('iris','active'),('paused','active'); INSERT INTO rvbbit.calliope_specialists VALUES ('tyche','tyche','active',now()),('iris','iris','active',now()),('paused','paused','disabled',now())")
        c.execute((Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0412_generated_specialist_readiness.sql').read_text().split('-- An existing definition')[0])
    yield cf
    with cf() as c:c.execute('DROP SCHEMA rvbbit CASCADE')


def test_gate_recovery_missing_and_transient_failure(db,monkeypatch):
    monkeypatch.setattr(factory,'_hermes_config',lambda:{'url':'http://test','key':'test'})
    def request(config,method,path,**kw):
        if '/tyche/' in path:raise urllib.error.HTTPError(path,404,'not found',{},io.BytesIO(b'{"error":"Unknown or unconfigured profile"}'))
        return {'sessions':[]}
    monkeypatch.setattr(factory,'_hermes_json',request)
    assert factory.reconcile_readiness(db)=={'ready':['iris'],'pending':['tyche']}
    with db() as c:
        states={r['specialist_key']:r['status'] for r in c.execute('select * from rvbbit.calliope_specialists')}
        assert states=={'tyche':'provisioning','iris':'active','paused':'disabled'}
        c.execute("UPDATE rvbbit.calliope_specialists SET status='active' WHERE specialist_key='tyche'")
        assert c.execute("select status from rvbbit.calliope_specialists where specialist_key='tyche'").fetchone()['status']=='provisioning'
    monkeypatch.setattr(factory,'_hermes_json',lambda *_a,**_kw: (_ for _ in ()).throw(TimeoutError()))
    factory.reconcile_readiness(db)
    with db() as c:assert c.execute("select status from rvbbit.calliope_specialists where specialist_key='iris'").fetchone()['status']=='active'
    monkeypatch.setattr(factory,'_hermes_json',lambda *_a,**_kw:{'sessions':[]})
    factory.reconcile_readiness(db)
    with db() as c:
        assert c.execute("select status from rvbbit.calliope_specialists where specialist_key='tyche'").fetchone()['status']=='active'
        c.execute("INSERT INTO rvbbit.generated_specialists(specialist_key) VALUES ('new'); INSERT INTO rvbbit.calliope_specialists VALUES('new','new','active',now())")
        assert c.execute("select status from rvbbit.calliope_specialists where specialist_key='new'").fetchone()['status']=='provisioning'


def test_live_staging_is_separate_from_root_boot_staging(monkeypatch,tmp_path):
    import runpy
    module=runpy.run_path(str(Path(__file__).parents[2]/'docker/calliope/reconcile-hermes-specialists.py'))
    monkeypatch.setattr(module['tempfile'],'gettempdir',lambda:str(tmp_path))
    item={'specialist_key':'test-domain','display_name':'Test','soul_markdown':'# Test'}
    monkeypatch.setattr(module['os'],'getuid',lambda:0)
    root=module['_stage_generated_profile'](item)
    monkeypatch.setattr(module['os'],'getuid',lambda:1000)
    runtime=module['_stage_generated_profile'](item)
    assert root!=runtime
    assert (runtime/'SOUL.md').read_text()=='# Test\n'
