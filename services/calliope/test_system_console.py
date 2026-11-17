"""System identity, admin revocation, observation provenance and real ledger math."""
import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import make_conninfo
import pytest
import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

import calliope_access
import system_console as system

OWNER = 'admin@example.test'
MEMBER = 'member@example.test'

@pytest.fixture
def db():
    admin = os.getenv('ACTIVITY_TEST_ADMIN_DSN')
    if not admin:
        pytest.skip('ACTIVITY_TEST_ADMIN_DSN must identify a disposable cluster')
    name = 'system_test_' + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute('CREATE DATABASE '+name)
    def factory():
        return psycopg.connect(make_conninfo(admin, dbname=name), autocommit=True, row_factory=dict_row)
    try:
        with factory() as c:
            c.execute('''CREATE SCHEMA rvbbit;
              CREATE TABLE rvbbit.teams(id uuid PRIMARY KEY,system_key text,archived boolean DEFAULT false,name text);
              CREATE TABLE rvbbit.team_members(team_id uuid,principal_email text);
              CREATE TABLE rvbbit.application_principals(email text PRIMARY KEY,display_name text,avatar_key uuid);
              CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,archived boolean DEFAULT false,channel text DEFAULT 'web',title text);
              CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,created_at timestamptz DEFAULT now());''')
            c.execute(calliope_access.DDL)
            c.execute(system.DDL)
            c.execute('INSERT INTO rvbbit.teams(id,system_key,name) VALUES(%s,\'admins\',\'Admins\')',(uuid.uuid4(),))
            c.execute('INSERT INTO rvbbit.team_members SELECT id,%s FROM rvbbit.teams',(OWNER,))
            c.execute('INSERT INTO rvbbit.application_principals(email) VALUES(%s),(%s)',(OWNER,MEMBER))
        yield factory
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute('DROP DATABASE '+name+' WITH (FORCE)')


def insert(db, owner=OWNER, kind='system'):
    sid=str(uuid.uuid4())
    with db() as c:
        return dict(c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email,title,workspace_kind) VALUES(%s,%s,\'System\',%s) RETURNING *',(sid,owner,kind)).fetchone())


def test_resolver_reuses_after_rename_and_concurrent_creation_conflict(db):
    async def create(owner):return insert(db,owner)
    one=asyncio.run(system.resolve(db,OWNER,create))
    with db() as c:c.execute("UPDATE rvbbit.calliope_sessions SET title='My infrastructure' WHERE id=%s",(one['id'],))
    two=asyncio.run(system.resolve(db,OWNER,create))
    assert one['id']==two['id']
    with pytest.raises(psycopg.errors.UniqueViolation):insert(db)
    with db() as c:c.execute('UPDATE rvbbit.calliope_sessions SET archived=true WHERE id=%s',(one['id'],))
    assert asyncio.run(system.resolve(db,OWNER,create))['id']!=one['id']


def test_admin_gate_including_shared_thread_and_revocation(db):
    thread=insert(db);sid=str(thread['id'])
    with db() as c:
        c.execute('INSERT INTO rvbbit.calliope_session_view_grants(session_id,principal_email,granted_by,can_participate) VALUES(%s,%s,%s,true)',(sid,MEMBER,OWNER))
        assert not calliope_access.can_view(c,sid,MEMBER)
        assert not c.execute('SELECT rvbbit.calliope_session_can_participate(%s,%s) AS ok',(sid,MEMBER)).fetchone()['ok']
    with pytest.raises(PermissionError):system.require_workspace(db,MEMBER,sid)
    with db() as c:
        c.execute('DELETE FROM rvbbit.team_members WHERE principal_email=%s',(OWNER,))
        assert not calliope_access.can_view(c,sid,OWNER)
    with pytest.raises(PermissionError):system.require_workspace(db,OWNER,sid)
    # Normal chats remain accessible to their owners, without admin membership.
    plain=insert(db,MEMBER,None)
    with db() as c:assert calliope_access.can_view(c,plain['id'],MEMBER)


def test_ledger_unknown_partial_zero_and_errors(db):
    with db() as c:
        c.execute('CREATE SCHEMA clover_telemetry')
        c.execute((Path(__file__).parents[2]/'crates/rvbbit_hutch/src/ledger.sql').read_text())
        for i,(cost,status) in enumerate([(0,200),(100000000,200),(None,503)]):
            iid='call-'+str(i)
            c.execute('INSERT INTO clover_telemetry.invocations(event_id,instance_id,ts,invocation_id,tenant,backend,n_inputs,status,duration_ms,model_version) VALUES(%s,\'install\',now(),%s,\'local\',\'extract\',1,%s,100,\'gliner\')',(iid,iid,status))
            c.execute('INSERT INTO clover_telemetry.upstream_attempts(instance_id,ts,invocation_id,tenant,canonical_model,provider,upstream_model,attempt_no,mode,status,provider_cost_nanousd,cost_source,cost_status) VALUES(\'install\',now(),%s,\'local\',\'extract\',\'worker\',\'gliner\',1,\'specialist\',%s,%s,\'reported\',%s)',(iid,status,cost,'final' if cost is not None else 'unavailable'))
    usage=system.telemetry(db,'24h')
    assert usage['summary']['calls']==3 and usage['summary']['errors']==1
    assert usage['summary']['cost_complete']==2
    assert float(usage['summary']['known_cost_usd'])==.1
    assert next(r for r in usage['recent'] if r['invocation_id']=='call-2')['cost_usd'] is None
    assert next(r for r in usage['recent'] if r['invocation_id']=='call-0')['cost_usd']==0
    with db() as c:
        c.execute("UPDATE clover_telemetry.upstream_attempts SET provider_cost_nanousd=50000000,cost_status='final' WHERE invocation_id='call-2'")
    assert system.telemetry(db,'24h')['summary']['cost_complete']==3


def test_snapshot_and_context_trust_server_observation_only(db,monkeypatch):
    sid=str(insert(db)['id'])
    monkeypatch.setattr(system,'clover_inventory',lambda probe:{'state':'reachable','detail':'ok','data':[{'id':'extract','state':'configured','model_version':'gliner','enabled':True}]})
    snap=system.save_snapshot(db,OWNER,system.snapshot(db,OWNER,sid))
    assert snap['usage']['state']=='unconfigured'
    assert 'ledger' not in {s['id'] for s in snap['services']}
    assert next(s for s in snap['services'] if s['id']=='hindsight')['state']=='unconfigured'
    context=system.hydrate(db,OWNER,{'snapshot_id':snap['id'],'tab':'capabilities','capability':'extract','services':[{'state':'forged'}]},sid)
    assert context['capability']['model_version']=='gliner'
    assert context['capability']['model']=='gliner'
    assert 'forged' not in json.dumps(context)
    assert 'clover_telemetry.calls' in system.prompt(context)
    other=str(insert(db,OWNER,None)['id'])
    with pytest.raises(PermissionError):system.hydrate(db,OWNER,{'snapshot_id':snap['id']},other)
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{'snapshot_id':snap['id'],'capability':'imaginary'},sid)
    with pytest.raises(ValueError):system.snapshot(db,OWNER,sid,window='all;drop')
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{'snapshot_id':str(uuid.uuid4())},sid)


@pytest.mark.parametrize('response,state', [
    ({'status':'healthy','database':'connected'},'reachable'),
    ({'status':'healthy','database':'disconnected'},'unavailable'),
    ({'status':'starting'},'unavailable'),
])
def test_memory_component_checks_live_health_not_registered_status(db,monkeypatch,response,state):
    with db() as c:
        c.execute('CREATE TABLE rvbbit.memory_services(name text,provider text,status text,endpoint_url text)')
        c.execute("INSERT INTO rvbbit.memory_services VALUES('memory','hindsight','ready','http://memory:8888')")
    client=httpx.Client
    def handle(request):
        assert str(request.url)=='http://memory:8888/health'
        return httpx.Response(200,json=response)
    monkeypatch.setattr(system.httpx,'Client',lambda **kw:client(**kw,transport=httpx.MockTransport(handle)))
    assert system.memory_health(db)['state']==state


def test_routes_deny_nonadmin_and_do_not_create_or_contact_clover(db,monkeypatch):
    import provider_settings
    import speech_settings
    routes=[]; calls=[]
    class MCP:
        def custom_route(self,path,methods):
            def wrap(fn):routes.append(Route(path,fn,methods=methods));return fn
            return wrap
    async def create(owner):calls.append(owner);return insert(db,owner)
    async def health():return {'ok':True}
    async def desktop_health():
        calls.append('desktop')
        return {'id':'desktop','label':'Desktop / computer','state':'reachable','detail':'Control API, display and authenticated viewer responded.'}
    async def speech_control(**kwargs):return {'provider':'clover','model':'calliope-stt','enabled':True}
    monkeypatch.setattr(provider_settings,'summary',lambda factory: calls.append('settings') or {'agents':[{'key':'default','model':'calliope'}]})
    monkeypatch.setattr(speech_settings,'control',speech_control)
    system.register(MCP(),db,lambda request:(request.headers.get('x-user',MEMBER),None),lambda value,status=200:JSONResponse(value,status_code=status),create,health,desktop_health)
    monkeypatch.setattr(system,'clover_inventory',lambda probe: calls.append('clover') or {'state':'unconfigured','detail':'missing','data':[]})
    with TestClient(Starlette(routes=routes)) as client:
        assert client.post('/api/calliope/system/session').status_code==403
        assert client.get('/api/calliope/system/snapshot').status_code==403
        assert client.get('/api/calliope/system/checks').status_code==403
        assert client.post('/api/calliope/system/checks',json={'session_id':'fake'}).status_code==403
        assert client.get('/api/calliope/system/settings').status_code==403
        assert client.get('/api/calliope/system/detail-query?kind=errors').status_code==403
        assert client.post('/api/calliope/system/instance-ai',json={'action':'save','api_key':'never-used'}).status_code==403
        assert calls==[]
        headers={'x-user':OWNER}
        sid=client.post('/api/calliope/system/session',headers=headers).json()['session_id']
        assert client.post('/api/calliope/system/session',headers=headers).json()['session_id']==sid
        response=client.get('/api/calliope/system/snapshot',params={'session':sid},headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['services'][1]['id']=='hermes'
        assert response.json()['services'][-1]['id']=='desktop'
        assert calls.count('desktop')==1
        context=system.hydrate(db,OWNER,{'snapshot_id':response.json()['id'],'tab':'overview'},sid)
        assert context['services'][-1]['id']=='desktop'
        settings=client.get('/api/calliope/system/settings',params={'session':sid},headers=headers)
        assert settings.status_code==200,settings.text
        value=settings.json()
        assert client.post('/api/calliope/system/instance-ai',headers=headers,json={'session_id':sid,'action':'save'}).status_code==403
        q=client.get('/api/calliope/system/detail-query',headers=headers,params={'session':sid,'kind':'errors','window':'7d'}).json()
        assert '168 hours' in q['sql'] and 'c.status >= 400' in q['sql']
        assert value['settings']['agents'][0]['model']=='calliope'
        context=system.hydrate(db,OWNER,{'tab':'settings','settings_snapshot_id':value['observation']['id'],'settings_agent':'default'},sid)
        assert context['settings']['selected_agent']['model']=='calliope'
        assert context['settings']['speech']['model']=='calliope-stt'
        assert client.post('/api/calliope/system/checks',headers=headers,json={'session_id':sid}).status_code==403
        plain=str(insert(db,OWNER,None)['id'])
        before=len(calls)
        assert client.get('/api/calliope/system/settings',params={'session':plain},headers=headers).status_code==403
        assert len(calls)==before

from test_zen_chat_layout import zen_page  # noqa: F401,E402


def mount_system(page, usage=None):
    data={'id':'observation','probed':True,'observed_at':'2026-10-11T15:00:00Z','window':'24h','services':[{'id':'clover','label':'Clover','state':'reachable','detail':'Inventory responded.'}],
          'capabilities':[{'id':'extract','kind':'specialist','state':'ready','model_version':'gliner-medium','option':{'pack':'builtin/clover-gliner','reason':'Extracts exact spans.'},'usage':{'calls':8,'avg_ms':200}},
                          {'id':'timeseries_anomalies','kind':'specialist','state':'configured','model_version':'TSPulse','option':{},'usage':{'calls':2}}],
          'packages':[], 'warnings':[], 'usage':{'state':'available','summary':{'calls':10,'errors':0,'known_cost_usd':0,'cost_complete':2,'avg_ms':200},'backends':[], 'recent':[], 'daily':[], 'writers':[]}}
    if usage is not None:data['usage']=usage
    data['connections']={'mcp_servers':2,'mcp_tools':8,'servers':[
        {'name':'company_tools','transport':'http','tools':8,'last_call':'2026-10-11T14:00:00Z','failed_calls_24h':1},
        {'name':'local_tools','transport':'stdio','tools':0,'last_call':None,'failed_calls_24h':0}]}
    page.route('**/api/calliope/system/snapshot?*',lambda route:route.fulfill(json=data))
    page.evaluate('''() => {qa.state.surfaces=[{id:'receipt',turn_id:'one',kind:'answer',title:'System observation',payload:{text:'A saved receipt.'}}];qa.renderChat(true);qa.state.current.workspace_kind='system';qa.state.config.can_administer=true;qa.renderStage();}''')
    page.get_by_role('heading',name='System components',exact=True).wait_for()


def test_live_stage_survives_reconciliation_and_carries_selection(zen_page):
    page=zen_page;mount_system(page)
    page.locator('[data-system-tab="capabilities"]').first.click()
    page.locator('[data-capability="timeseries_anomalies"]').click()
    assert 'Time-series anomalies' in page.locator('#system-stage-context').inner_text()
    page.evaluate('window.systemRow=document.querySelector(\'[data-capability="timeseries_anomalies"]\');qa.renderStage();')
    assert page.evaluate('window.systemRow===document.querySelector(\'[data-capability="timeseries_anomalies"]\')')
    assert page.evaluate('CalliopeSystemStage.origin().snapshot_id')=='observation'
    assert page.evaluate('CalliopeSystemStage.origin().capability')=='timeseries_anomalies'
    page.get_by_role('button',name='Ask Callie about this',exact=True).click()
    assert 'Time-series anomalies' in page.evaluate('qa.composerValue()')
    page.evaluate('CalliopeSystemStage.showReceipts()')
    assert page.locator('#stage').is_visible()
    assert page.evaluate('CalliopeSystemStage.origin().tab')=='receipts'
    page.locator('[data-system-tab="capabilities"]').first.click()
    assert page.locator('[data-capability="timeseries_anomalies"]').get_attribute('aria-pressed')=='true'
    page.evaluate('qa.state.current={id:"normal",title:"Ordinary"};qa.renderStage();')
    assert page.locator('#system-stage').is_hidden()
    assert page.locator('#system-stage-context').is_hidden()
    assert page.evaluate('CalliopeSystemStage.origin()') is None


@pytest.mark.parametrize('width',[1500,1000,390])
def test_system_layout_uses_available_stage_without_page_overflow(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':width,'height':900});mount_system(page)
    page.locator('[data-system-tab="capabilities"]').first.click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    bounds=page.locator('#system-stage').bounding_box()
    assert bounds and bounds['height']>400
    if width==1500:page.screenshot(path='/tmp/calliope-system-stage/fixture-desktop.png')


@pytest.mark.parametrize('attribute',['data-focus-surface','data-focus-evidence-surface'])
def test_chat_receipt_links_reveal_the_existing_stage(zen_page,attribute):
    page=zen_page;mount_system(page)
    page.evaluate("""attribute => {
      qa.setupEvents();
      const button=document.createElement('button');
      button.textContent='Open saved receipt';button.setAttribute(attribute,'receipt');
      qa.els.messages.append(button);
    }""",attribute)
    assert page.locator('#stage').is_hidden()
    page.get_by_role('button',name='Open saved receipt',exact=True).click()
    assert page.locator('#stage').is_visible()
    assert page.locator('[data-system-receipts]').get_attribute('aria-current')=='page'


def test_capabilities_join_the_actual_ledger_backend_names(db,monkeypatch):
    sid=str(insert(db)['id'])
    definitions=[('extract','specialist','extract'),('calliope','chat','llm:calliope'),
                 ('calliope-voice','speech','speech:calliope-voice'),
                 ('calliope-image','image','media:calliope-image'),
                 ('calliope-stt','transcription','media:calliope-stt')]
    monkeypatch.setattr(system,'clover_inventory',lambda probe:{'state':'reachable','detail':'ok','data':[
        {'id':name,'kind':kind,'state':'configured','model_version':'version'} for name,kind,_ in definitions]})
    monkeypatch.setattr(system,'telemetry',lambda factory,window:{'state':'available','backends':[
        {'backend':backend,'calls':index+1} for index,(_,_,backend) in enumerate(definitions)]})
    caps=system.snapshot(db,OWNER,sid)['capabilities']
    assert [(c['ledger_backend'],c['usage']['calls']) for c in caps]==[
        (backend,index+1) for index,(_,_,backend) in enumerate(definitions)]


def test_saved_settings_context_excludes_credentials_and_untrusted_drafts(db,monkeypatch):
    sid=str(insert(db)['id'])
    monkeypatch.setattr(system,'clover_inventory',lambda probe:{'state':'unconfigured','detail':'none','data':[]})
    gateway=system.save_snapshot(db,OWNER,system.snapshot(db,OWNER,sid))
    safe=system.settings_observation(db,OWNER,sid,{
        'providers':[{'provider':'openrouter','display_name':'OpenRouter','auth_state':'configured','api_key':'PRIVATE_KEY','credential_ref':'PRIVATE_REF','metadata':{'secret':'PRIVATE_METADATA'}}],
        'agents':[{'key':'default','name':'Calliope','provider':'clover','model':'calliope','pending':{'state':'queued','model':'new-model','secret':'PRIVATE_PENDING'}}],
        'models':[{'model':'SHOULD_NOT_COPY_CATALOG'}],
    },{'provider':'clover','model':'calliope-stt','api_key':'PRIVATE_SPEECH'})
    value={'tab':'settings','settings_snapshot_id':safe['id'],'settings_section':'agents','settings_agent':'default',
           'settings':{'model':'FORGED'},'api_key':'UNTRUSTED_DRAFT'}
    context=system.hydrate(db,OWNER,value,sid)
    assert context['snapshot_id']==gateway['id']  # newest settings view is not mistaken for a gateway observation
    assert context['settings']['selected_agent']['model']=='calliope'
    assert context['settings']['selected_agent']['pending']['model']=='new-model'
    assert not any(x in json.dumps(context) for x in ['PRIVATE_','FORGED','UNTRUSTED_DRAFT','SHOULD_NOT_COPY'])
    assert 'saved provider/agent/speech' in system.prompt(context)
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{**value,'settings_agent':'unknown'},sid)
    other=str(insert(db,'another@example.test')['id'])
    other_ref=system.settings_observation(db,'another@example.test',other,{}, {})
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{**value,'settings_snapshot_id':other_ref['id']},sid)
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{**value,'settings_snapshot_id':gateway['id']},sid)
    with pytest.raises(ValueError):system.hydrate(db,OWNER,{**value,'snapshot_id':safe['id']},sid)


def mount_settings(page, stale_shared=False):
    mount_system(page)
    state={'inventory_initialized':True,'instance_openrouter_configured':True,'providers':[
        {'provider':'clover','display_name':'Clover / Hutch','auth_state':'configured','auth_kind':'environment','environment_managed':True,'model_count':2},
        {'provider':'openrouter','display_name':'OpenRouter','auth_state':'configured','auth_kind':'api_key','environment_managed':True,'metadata':{'instance_shared':True},'model_count':400},
        {'provider':'openai-api','display_name':'OpenAI API','auth_state':'missing','auth_kind':'api_key','model_count':0}],
        'models':[{'provider':'clover','model':'calliope','display_name':'Calliope'},{'provider':'clover','model':'calliope-luna','display_name':'Calliope Luna'}],
        'agents':[{'key':'default','name':'Calliope','provider':'clover','model':'calliope'}],
        'fallback':None,'auxiliary':None,'loadouts':[],'usage_30d':[]}
    speech={'provider':'clover','model':'calliope-stt','revision':'rev-one','enabled':True,'credential_available':True,'models':['calliope-stt']}
    reads=[];saves=[]
    def read(route):
        reads.append(route.request.url)
        route.fulfill(json={'settings':state,'speech':speech,'instance_ai':{'configured':True,'editable':True},'observation':{'id':'settings-'+str(len(reads)),'observed_at':'2026-10-11T16:00:00Z'}})
    def save(route):
        body=route.request.post_data_json
        saves.append({'body':body,'admin':route.request.headers.get('x-calliope-admin')})
        for change in body.get('changes',[]):
            state['agents'][0].update(provider=change['provider'],model=change['model'])
        route.fulfill(json={'ok':True,'revision_id':'model-change'})
    def refresh(route):
        state['providers'][1].update(auth_state='configured',metadata={'instance_shared':True},model_count=400)
        route.fulfill(json=state)
    if stale_shared:
        state['providers'][1].update(auth_state='missing',metadata={},model_count=0)
    page.route('**/api/calliope/system/settings?*',read)
    page.route('**/api/calliope/admin/providers/agents',save)
    page.route('**/api/calliope/admin/providers/refresh',refresh)
    page.evaluate('qa.state.expertPortraits={error:true};qa.setupEvents();')
    page.get_by_role('button',name='Settings',exact=True).click()
    page.locator('[data-provider-key="openai-api"]').wait_for()
    page.wait_for_function('CalliopeSystemStage.origin().settings_snapshot_id')
    return reads,saves


def test_shared_openrouter_boot_registration_refreshes_stale_ui_automatically(zen_page):
    page=zen_page;reads,saves=mount_settings(page,stale_shared=True)
    shared=page.locator('[data-provider-card="openrouter"]')
    shared.get_by_text('shared instance key',exact=True).wait_for()
    assert shared.get_by_text('400',exact=True).is_visible()
    assert len(reads)==2 and not saves
    assert shared.locator('input,[data-provider-disconnect]').count()==0


def test_settings_stays_inline_preserves_key_draft_and_refreshes_context(zen_page):
    page=zen_page;reads,saves=mount_settings(page)
    assert not page.locator('#provider-settings-dialog').evaluate('(node)=>node.open')
    field=page.locator('[data-provider-key="openai-api"]');field.fill('test-only-unsent-secret')
    assert page.evaluate('CalliopeSystemStage.origin().settings_provider')=='openai-api'
    assert 'test-only-unsent-secret' not in page.evaluate('JSON.stringify(CalliopeSystemStage.origin())')
    page.get_by_role('button',name='Overview',exact=True).click()
    page.get_by_role('button',name='Settings',exact=True).click()
    assert field.input_value()=='test-only-unsent-secret'
    page.get_by_role('button',name='Refresh system',exact=True).click()
    page.wait_for_function('CalliopeSystemStage.origin().settings_snapshot_id === "settings-2"')
    assert field.input_value()=='test-only-unsent-secret'
    assert not saves
    page.get_by_role('button',name='Ask Callie about these settings',exact=True).click()
    assert 'saved model and provider settings' in page.evaluate('qa.composerValue()')
    page.evaluate('qa.state.current={id:"ordinary"};qa.renderStage()')
    assert page.locator('#provider-settings-dialog .provider-settings-shell').count()==1
    assert page.locator('#system-stage').is_hidden()


def test_inline_model_change_waits_for_apply_and_uses_existing_admin_api(zen_page):
    page=zen_page;reads,saves=mount_settings(page)
    page.get_by_role('button',name='Models & voice',exact=True).click()
    page.locator('[data-model-picker-input="default"]').click()
    page.locator('[data-model-pick="default"][data-model-value="calliope-luna"]').click()
    assert not saves
    assert page.evaluate('CalliopeSystemStage.origin().settings_agent')=='default'
    page.get_by_role('button',name='Apply all · 1 restart',exact=True).click()
    page.wait_for_function('CalliopeSystemStage.origin().settings_snapshot_id === "settings-2"')
    assert len(saves)==1 and saves[0]['admin']=='1'
    assert saves[0]['body']['changes'][0]['model']=='calliope-luna'
    assert page.locator('[data-model-picker-input="default"]').input_value()=='Calliope Luna'
    assert not page.locator('#provider-settings-dialog').evaluate('(node)=>node.open')


def test_settings_context_does_not_break_chat_after_gateway_access_failure(zen_page):
    page=zen_page;mount_settings(page)
    page.route('**/api/calliope/system/snapshot?*',lambda route:route.fulfill(status=403,json={'error':{'message':'Access changed'}}))
    page.evaluate('CalliopeSystemStage.refresh()')
    assert page.evaluate('CalliopeSystemStage.origin().settings_snapshot_id')
    page.get_by_role('button',name='Overview',exact=True).click()
    assert page.evaluate('CalliopeSystemStage.origin()') is None


@pytest.mark.parametrize('width',[1500,1000,390])
def test_glass_and_inline_settings_fit_the_stage(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':width,'height':950});mount_settings(page)
    page.get_by_role('button',name='Models & voice',exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    assert page.locator('.system-body').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    glass=page.locator('.system-glass')
    assert glass.evaluate('(el)=>getComputedStyle(el).borderTopLeftRadius')==('10px' if width==390 else '12px')
    if width==1500:page.screenshot(path='/tmp/calliope-system-stage/settings-desktop.png')


def test_system_failure_count_opens_existing_sql_data_panel(zen_page):
    from test_data_sql_panel import sql_setup
    page=zen_page;frame,calls=sql_setup(page)
    page.locator('#workspace-tab-chat').click()
    mount_system(page)
    query=system.detail_query('errors','24h')
    page.route('**/api/calliope/system/detail-query?*',lambda r:r.fulfill(json=query))
    page.locator('[data-system-query="errors"]').first.click()
    frame.get_by_text('West',exact=True).wait_for()
    runs=[body for path,body in calls if path.endswith('/sql/run')]
    assert len(runs)==1 and '\nFROM\n  clover_telemetry.calls' in runs[0]['sql']
    assert "c.status >= 400" in runs[0]['sql']
    assert frame.get_by_role('textbox',name='Query title',exact=True).input_value()==query['title']
    assert page.locator('#system-stage').is_visible()


def test_instance_key_uses_own_connection_and_never_enters_chat_context(zen_page):
    page=zen_page;reads,saves=mount_settings(page);changes=[]
    shared=page.locator('[data-provider-card="openrouter"]')
    assert shared.get_by_text('shared instance key',exact=True).is_visible()
    assert shared.locator('input,[data-provider-disconnect]').count()==0
    assert shared.get_by_text('400',exact=True).is_visible()
    def changed(r):
        changes.append(r.request.post_data_json)
        r.fulfill(json={'configured':True,'editable':True,'message':'Key saved'})
    page.route('**/api/calliope/system/instance-ai',changed)
    assert not page.locator('[data-system-instance-key]').is_visible()
    page.get_by_text('Change key',exact=True).click()
    field=page.locator('[data-system-instance-key]');field.fill('new-instance-secret')
    assert 'new-instance-secret' not in page.evaluate('JSON.stringify(CalliopeSystemStage.origin())')
    page.get_by_role('button',name='Overview',exact=True).click()
    page.get_by_role('button',name='Settings',exact=True).click()
    assert field.input_value()=='new-instance-secret'
    page.get_by_role('button',name='Check & save',exact=True).click()
    page.get_by_text('Key saved',exact=True).wait_for()
    assert len(changes)==1 and changes[0]['api_key']=='new-instance-secret'
    assert field.input_value()=='' and not saves


def test_system_sql_drilldowns_use_live_admin_and_database_privileges(db):
    import data_sql
    with db() as c:
        c.execute('CREATE TABLE rvbbit.mcp_servers(name text,transport text)')
        c.execute('CREATE TABLE rvbbit.mcp_tools(server text)')
        c.execute('CREATE TABLE rvbbit.mcp_invocations(server text,invocation_at timestamptz,error text)')
    def factory(**kwargs):return db()
    statement=system.detail_query('mcp')['sql']
    allowed=data_sql.system_relations(factory,None,OWNER,statement)
    assert set(allowed)=={'rvbbit.mcp_servers','rvbbit.mcp_tools','rvbbit.mcp_invocations'}
    assert len(data_sql.prepare(statement,allowed,[])['sources'])==3
    with db() as c:c.execute('DELETE FROM rvbbit.team_members WHERE principal_email=%s',(OWNER,))
    with pytest.raises(PermissionError):data_sql.system_relations(factory,None,OWNER,statement)
    assert data_sql.system_relations(factory,None,OWNER,'SELECT 1')=={}


def test_cost_coverage_distinguishes_zero_rejections_history_and_missing_evidence(db):
    with db() as c:
        c.execute('CREATE SCHEMA clover_telemetry')
        c.execute((Path(__file__).parents[2]/'crates/rvbbit_hutch/src/ledger.sql').read_text())
        samples=[('local-zero',None,0,'final'),('api-charge',None,10,'final'),
                 ('rejected','lanes_saturated',None,None),('disabled','capability_unavailable',None,None),
                 ('unknown-local',None,None,None),('legacy',None,None,None),
                 ('provider-failed','upstream_error',None,None),('pending',None,None,'pending'),
                 ('partial',None,None,'unavailable')]
        for name,error,cost,state in samples:
            c.execute("""INSERT INTO clover_telemetry.invocations(event_id,instance_id,ts,invocation_id,tenant,backend,
              n_inputs,status,error_code,duration_ms,model_version,cost_basis)
              VALUES(%s,'install',now(),%s,'local','extract',1,%s,%s,100,'gliner',%s)""",
              (name,name,503 if error else 200,error,'legacy_specialist_mixed' if name=='legacy' else 'reference_tariff'))
            if state:
                c.execute("""INSERT INTO clover_telemetry.upstream_attempts(instance_id,ts,invocation_id,tenant,canonical_model,
                  provider,upstream_model,attempt_no,mode,status,provider_cost_nanousd,cost_source,cost_status)
                  VALUES('install',now(),%s,'local','extract','worker','gliner',1,'specialist',200,%s,'reported',%s)""",(name,cost,state))
    usage=system.telemetry(db,'24h'); s=usage['summary']
    assert (s['cost_reported'],s['cost_no_charge'],s['cost_not_sent'],s['cost_unreported'],s['cost_pending'],s['cost_partial'])==(1,1,2,3,1,1)
    assert s['cost_historical_unreported']==1
    assert sum(s['cost_'+k] for k in ('reported','no_charge','not_sent','unreported','pending','partial'))==s['calls']
    with db() as c:
        records={r['invocation_id']:r for r in c.execute(system.detail_query('costs')['sql'])}
    assert records['unknown-local']['cost_coverage']=='unreported'
    assert records['local-zero']['cost_coverage']=='no_charge'
    assert records['local-zero']['reported_cost_usd']==0
    assert records['provider-failed']['reported_cost_usd'] is None
    assert records['rejected']['cost_coverage']=='not_sent'
    with db() as c:
        gaps=c.execute(system.detail_query('cost-gaps')['sql']).fetchall()
    assert len(gaps)==5 and all(r['cost_coverage'] in ('unreported','partial','pending') for r in gaps)


def test_health_is_automatic_on_entry_and_both_navigation_paths(zen_page):
    page=zen_page;requests=[]
    page.on('request',lambda request:requests.append(request.url) if '/system/snapshot?' in request.url else None)
    mount_system(page)
    assert len(requests)==1 and 'probe=true' in requests[-1]
    assert 'workers responded' not in page.locator('.system-body').inner_text()
    assert 'configured routes' not in page.locator('.system-body').inner_text()
    page.get_by_role('button',name='Capabilities',exact=True).click()
    page.wait_for_function("document.querySelector('[data-system-refresh]')?.disabled===false")
    assert len(requests)==2 and 'probe=true' in requests[-1]
    assert page.locator('.system-counts').inner_text().split()==['2','Enabled','0','Unavailable','0','Disabled','0','Needs','setup']
    assert page.locator('[data-capability=timeseries_anomalies] .system-cap-evidence').inner_text().splitlines()==['Enabled','Readiness check missing']
    assert page.locator('[data-capability=extract] .system-cap-evidence').inner_text().splitlines()==['Enabled','Worker check passed']
    assert page.get_by_role('button',name='Entity extraction',exact=False).count()==1
    page.get_by_role('button',name='Overview',exact=True).click()
    page.wait_for_function("document.querySelector('[data-system-refresh]')?.disabled===false")
    assert len(requests)==3
    page.get_by_role('button',name='Explore all 2 capabilities →',exact=True).click()
    page.wait_for_function("document.querySelector('[data-system-refresh]')?.disabled===false")
    assert len(requests)==4 and all('probe=true' in url for url in requests)


def test_capability_status_reports_current_readiness():
    assert system.capability_status({'state':'configured','enabled':True,'health_scope':'route_only'},True)=={'availability':'enabled','health':'route_only'}
    assert system.capability_status({'state':'configured','enabled':True},True)=={'availability':'enabled','health':'not_offered'}
    assert system.capability_status({'state':'ready','enabled':True,'probe':'worker-health-only'},True)=={'availability':'enabled','health':'passed'}
    assert system.capability_status({'state':'unavailable','enabled':True,'probe':'worker-health-only'},True)=={'availability':'unavailable','health':'failed'}
    assert system.capability_status({'state':'missing_credentials','enabled':True},True)=={'availability':'needs_setup','health':'not_checked'}
    assert system.capability_status({'state':'disabled','enabled':False},True)=={'availability':'disabled','health':'not_checked'}
    assert system.capability_status({'state':'configured','enabled':True},False)['health']=='not_checked'


def test_connections_show_all_registered_servers_without_configuration_secrets(db):
    assert system.connections(db)=={'mcp_servers':0,'mcp_tools':0,'servers':[]}
    with db() as c:
        c.execute('CREATE TABLE rvbbit.mcp_servers(name text,transport text,url text,env jsonb)')
        c.execute('CREATE TABLE rvbbit.mcp_tools(server text)')
        c.execute('CREATE TABLE rvbbit.mcp_invocations(server text,invocation_at timestamptz,error text)')
        c.execute("INSERT INTO rvbbit.mcp_servers VALUES ('idle','stdio',NULL,'{\"KEY\":\"SECRET\"}'),('active','http','https://SECRET',NULL)")
        c.execute("INSERT INTO rvbbit.mcp_tools VALUES ('active'),('active')")
        c.execute("INSERT INTO rvbbit.mcp_invocations VALUES ('active',now(),NULL),('active',now()-interval '1 hour','SECRET'),('active',now()-interval '2 days','SECRET')")
    inventory=system.connections(db)
    assert (inventory['mcp_servers'],inventory['mcp_tools'])==(2,2)
    active,idle=inventory['servers']
    assert active['name']=='active' and active['tools']==2 and active['failed_calls_24h']==1 and active['last_call']
    assert idle['name']=='idle' and idle['tools']==0 and idle['last_call'] is None
    assert 'SECRET' not in json.dumps(inventory,default=str)
    assert set(active)=={'name','transport','tools','last_call','failed_calls_24h'}


@pytest.mark.parametrize('width',[1500,1000,390])
def test_overview_combines_usage_and_server_inventory_in_compact_responsive_layout(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':width,'height':950});mount_system(page)
    assert page.get_by_role('button',name='Usage',exact=True).count()==0
    assert page.get_by_role('heading',name='Requests over time',exact=True).count()==1
    assert page.locator('.system-stats').count()==1
    assert page.locator('.system-mcp-list article').count()==2
    assert 'company_tools' in page.locator('.system-mcp-list').inner_text()
    assert 'No recorded calls' in page.locator('.system-mcp-list').inner_text()
    assert '1 failed in 24h' in page.locator('.system-mcp-list').inner_text()
    assert page.locator('.system-head h2,.system-head .system-kicker').count()==0
    assert page.locator('.system-head').evaluate('(el)=>el.getBoundingClientRect().height')<=52
    for label in ['Refresh system','Recheck health']:
        button=page.get_by_role('button',name=label,exact=True)
        assert button.inner_text()=='' and button.locator('svg').count()==1
    # Button labels used to center themselves vertically when the cost tile grew.
    assert page.locator('.system-stat-link').evaluate('(el)=>getComputedStyle(el).justifyContent')=='flex-start'
    assert page.locator('.system-body').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.get_by_role('combobox',name='Usage period',exact=True).select_option('7d')
    page.wait_for_function("document.querySelector('[data-system-refresh]')?.disabled===false")
    assert 'Daily · 7d' in page.locator('.system-usage-activity').inner_text()
    assert page.get_by_role('combobox',name='Usage period',exact=True).input_value()=='7d'


def test_tabs_remember_their_scroll_and_service_expansion(zen_page):
    page=zen_page;page.set_viewport_size({'width':1280,'height':720});mount_system(page)
    page.locator('.system-component summary').click()
    page.locator('.system-body').evaluate('el=>el.scrollTop=220')
    overview_scroll=page.locator('.system-body').evaluate('el=>el.scrollTop')
    assert overview_scroll>0
    page.get_by_role('button',name='Capabilities',exact=True).click()
    assert page.locator('.system-body').evaluate('el=>el.scrollTop')==0
    page.get_by_role('button',name='Overview',exact=True).click()
    page.wait_for_function('document.querySelector(".system-body").scrollTop>0')
    assert abs(page.locator('.system-body').evaluate('el=>el.scrollTop')-overview_scroll)<2
    assert page.locator('.system-component').evaluate('el=>el.open')


def test_request_series_includes_quiet_intervals_and_matches_totals(db):
    with db() as c:
        c.execute('CREATE SCHEMA clover_telemetry')
        c.execute((Path(__file__).parents[2]/'crates/rvbbit_hutch/src/ledger.sql').read_text())
        for name,age,status in [('recent','10 minutes',200),('failed','30 minutes',500),('old','26 hours',200)]:
            c.execute("""INSERT INTO clover_telemetry.invocations(event_id,instance_id,ts,invocation_id,tenant,backend,n_inputs,status,duration_ms,model_version)
                VALUES(%s,'install',now()-%s::interval,%s,'local','extract',1,%s,100,'gliner')""",(name,age,name,status))
    for period,count,step in [('1h',61,60),('24h',25,3600),('7d',8,86400),('30d',31,86400)]:
        usage=system.telemetry(db,period);rows=usage['daily']
        assert len(rows)==count
        assert sum(r['calls'] for r in rows)==usage['summary']['calls']
        assert sum(r['errors'] for r in rows)==usage['summary']['errors']==1
        assert rows[0]['partial'] and rows[-1]['partial']
        assert any(r['calls']==0 for r in rows)
        assert all((b['bucket']-a['bucket']).total_seconds()==step for a,b in zip(rows,rows[1:]))
        assert all(r['start_at']<=r['end_at'] for r in rows)


def test_request_area_chart_real_runtime_hover_resize_theme_and_lifecycle(zen_page):
    page=zen_page
    root=Path(__file__).parent
    manifest=json.loads((root/'vendor/manifest.json').read_text())
    for name in ('vega','vega-lite','vega-embed'):
        page.add_script_tag(path=str(root/'vendor'/f"{name}@{manifest['libraries'][name]['version']}"/f'{name}.min.js'))
    import calliope
    page.add_script_tag(content=calliope._chart_interpreter_script())
    # Disallow Function/eval: the chart must also work under Calliope's CSP.
    page.evaluate("""() => {const m=document.createElement('meta');m.httpEquiv='Content-Security-Policy';m.content="script-src 'self' 'unsafe-inline'";document.head.append(m);} """)
    rows=[{'bucket':f'2026-10-11T{h:02}:00:00Z','calls':n,'errors':1 if h==12 else 0,'partial':False}
          for h,n in enumerate([0,0,1,2,1,0,4,8,2,0,0,1,1000,2,3,0,0,1,0,0,0,0,0,0])]
    mount_system(page,{'state':'available','summary':{'calls':sum(r['calls'] for r in rows),'errors':1},'daily':rows,'writers':[],'backends':[]})
    chart=page.locator('[data-system-activity]')
    page.wait_for_function("document.querySelector('[data-system-activity]')?.dataset.ready==='true'")
    assert chart.locator('svg').count()==1
    assert chart.locator('.mark-area path').count()==1
    for width in (1500,1000,390,1500):
        page.set_viewport_size({'width':width,'height':950})
        page.wait_for_timeout(180)
        assert chart.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    # The nearest-point overlay should expose the actual bucket values.
    target=chart.locator('.mark-path.role-mark path').nth(12)
    target.hover(force=True)
    page.wait_for_function("document.querySelector('[data-activity-readout]').textContent.includes('1,000 requests')")
    assert 'failed' not in page.locator('[data-activity-readout]').inner_text()
    assert chart.locator('.mark-line path').count()==1
    assert 'C' in chart.locator('.mark-line path').get_attribute('d')
    assert 'Failed' not in page.locator('.system-activity-key').inner_text()
    assert page.get_by_role('button',name='Failed calls ↗',exact=True).count()==1
    old=chart.locator('.mark-line path').first.get_attribute('stroke')
    page.evaluate("document.documentElement.style.setProperty('--workspace-accent','#f09922')")
    page.wait_for_function("document.querySelector('[data-system-activity] .mark-line path')?.getAttribute('stroke')==='rgb(240, 153, 34)'")
    assert chart.locator('svg').count()==1 and old!='rgb(240, 153, 34)'
    page.get_by_role('button',name='Capabilities',exact=True).click()
    assert chart.count()==0
    page.get_by_role('button',name='Overview',exact=True).click()
    page.wait_for_function("document.querySelector('[data-system-activity]')?.dataset.ready==='true'")
    assert chart.locator('svg').count()==1
    page.screenshot(path='/tmp/calliope-system-area.png')
