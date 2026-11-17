"""App commands: real PostgreSQL transactions plus direct/sandbox browser parity."""
import asyncio
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright, expect
from starlette.requests import Request

import app_actions as actions
import auth
import calliope
import server
from test_workspace_sql import db, execute as setup, SEED  # noqa: F401


def test_html_template_advertises_action_support_without_configuring_writes(monkeypatch):
    monkeypatch.setattr(server, 'tool_dashboard_template', lambda: {
        'template_html': '<html></html>', 'semantic_map_example': {},
    })
    template = server.tool_live_app_template()
    assert template['manifest']['capabilities']['sql_actions'] is True
    assert not template['manifest'].get('actions')
    assert template['app_actions_example']['manifest']['actions']


@pytest.mark.parametrize('runtime,supported', [('html', True), ('python-fastapi', False)])
def test_action_capability_tracks_runtime_not_app_definitions(runtime, supported):
    for definitions in ({}, actions.example()['manifest']):
        manifest = server._live_app_manifest(runtime, manifest={
            **definitions, 'capabilities': {'sql_actions': not supported, 'screenshots': True},
        })
        assert manifest['capabilities']['sql_actions'] is supported
        assert manifest['capabilities']['screenshots'] is True
        assert bool(manifest.get('actions')) is bool(definitions.get('actions'))


def run(db, name, inputs, key=None, manifest=None, version=1):
    return actions.execute(db,slug='tasks',version=version,name=name,actor='user@example.com',
        request_key=key or str(uuid.uuid4()),manifest=manifest or actions.example()['manifest'],
        inputs=inputs,seed=SEED,schema_allowed=lambda s:s in {'calliope','sales'})


@pytest.fixture
def tasks(db):
    setup(db,actions.example()['setup_sql'])
    return db


def test_insert_update_persist_and_audit_without_chat_receipts(tasks):
    task=run(tasks,'add_task',{'title':"Literal '); DROP TABLE sales.orders; -- 100%"})['rows'][0]
    result=run(tasks,'set_status',{'id':task['id'],'status':'done','version':1})
    assert result['committed'] and result['rows'][0]['version']==2
    with tasks() as c:
        assert c.execute('SELECT status FROM calliope_apps.app_tasks').fetchone()['status']=='done'
        assert c.execute('SELECT count(*) n FROM sales.orders').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM rvbbit.app_action_runs').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n']==0
    assert len(actions.recent(tasks,'tasks'))==2


def test_concurrent_duplicate_requests_write_once(tasks):
    key=str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,tasks,'add_task',{'title':'once'},key) for _ in range(2)]
        results=[f.result() for f in futures]
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert results[0]['run_id']==results[1]['run_id']
    with pytest.raises(actions.ActionError,match='different action inputs'):
        run(tasks,'add_task',{'title':'not once'},key)
    with tasks() as c:
        assert c.execute('SELECT count(*) n FROM calliope_apps.app_tasks').fetchone()['n']==1


def test_bound_actor_enforces_assignment_on_direct_calls_and_replays(tasks):
    setup(tasks, "CREATE TABLE calliope_apps.assigned_jobs(id text PRIMARY KEY, owner text, note text); INSERT INTO calliope_apps.assigned_jobs VALUES('A','tech@example.test','before'),('B','someone@example.test','private')")
    manifest={'actions':{'save':{'inputs':{'id':{'type':'string'},'note':{'type':'string'}},'steps':[
        {'sql':'UPDATE calliope_apps.assigned_jobs SET note=$2 WHERE id=$1 AND owner=$3 RETURNING id,note',
         'params':['id','note',{'actor':True}],'expect_rows':1}]}}}
    def submit(subject, ident, key, **extra):
        return actions.execute(tasks,slug='assigned',version=1,name='save',actor=subject,
            request_key=key,manifest=manifest,inputs={'id':ident,'note':'after',**extra},seed=SEED,schema_allowed=lambda s:True)
    key=str(uuid.uuid4())
    result=submit('tech@example.test','A',key)
    assert result['rows']==[{'id':'A','note':'after'}]
    assert submit('tech@example.test','A',key)['replayed'] is True
    for actor,ident in [('someone@example.test','A'),('tech@example.test','B')]:
        with pytest.raises(actions.ActionError) as error:submit(actor,ident,str(uuid.uuid4()))
        assert error.value.code=='CONFLICT'
    with pytest.raises(actions.ActionError) as error:
        submit('someone@example.test','A',str(uuid.uuid4()),actor='tech@example.test')
    with tasks() as conn:
        assert conn.execute("SELECT note FROM calliope_apps.assigned_jobs WHERE id='B'").fetchone()['note']=='private'
        assert conn.execute("SELECT count(*) n FROM rvbbit.app_action_runs WHERE slug='assigned' AND status='complete'").fetchone()['n']==1


def test_optimistic_conflict_rolls_back_entire_batch(tasks):
    task=run(tasks,'add_task',{'title':'original'})['rows'][0]
    run(tasks,'set_status',{'id':task['id'],'status':'done','version':1})
    manifest=copy.deepcopy(actions.example()['manifest'])
    manifest['actions']['set_status']['steps'].insert(0,{'sql':"INSERT INTO calliope_apps.app_tasks(title) VALUES('must roll back')"})
    with pytest.raises(actions.ActionError) as e:
        run(tasks,'set_status',{'id':task['id'],'status':'open','version':1},manifest=manifest)
    assert e.value.code=='CONFLICT'
    with tasks() as c:
        assert c.execute('SELECT title,status,version FROM calliope_apps.app_tasks').fetchall()==[{'title':'original','status':'done','version':2}]
        assert c.execute("SELECT status FROM rvbbit.app_action_runs ORDER BY created_at DESC LIMIT 1").fetchone()['status']=='failed'


def test_constraint_failure_rolls_back_and_same_key_can_retry_after_repair(tasks):
    manifest={'actions':{'bad':{'inputs':{},'steps':[
        {'sql':"INSERT INTO calliope_apps.app_tasks(title) VALUES('first')"},
        {'sql':"INSERT INTO calliope_apps.app_tasks(title,status) VALUES('bad','invalid')"}]}}}
    key=str(uuid.uuid4())
    with pytest.raises(actions.ActionError):run(tasks,'bad',{},key,manifest)
    with tasks() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.app_tasks').fetchone()['n']==0
    # An unsuccessful key isn't a frozen contract; nothing committed.
    manifest['actions']['bad']['steps'].pop()
    assert run(tasks,'bad',{},key,manifest)['committed']


@pytest.mark.parametrize('sql',[
    'DELETE FROM sales.orders',
    'CREATE TABLE calliope.nope(id int)',
    'UPDATE calliope.x SET id=1; DELETE FROM calliope.x',
    'WITH x AS (DELETE FROM sales.orders RETURNING *) INSERT INTO calliope.x SELECT * FROM x',
])
def test_rejects_wrong_targets_ddl_and_multi_statements(sql):
    with pytest.raises(ValueError):actions.normalize({'a':{'inputs':{},'steps':[{'sql':sql}]}})


def test_parameter_remapping_json_and_percent_literals(tasks):
    manifest={'actions':{'a':{'inputs':{'unused':{'type':'string'},'data':{'type':'json'}},'steps':[
        {'sql':"INSERT INTO calliope_apps.app_tasks(title) VALUES(($2::jsonb)->>'title') RETURNING title || ' 50%' AS result",'params':['unused','data']}]}}}
    assert run(tasks,'a',{'unused':'unused','data':{'title':'bound'}},manifest=manifest)['rows']==[{'result':'bound 50%'}]
    with pytest.raises(actions.ActionError):run(tasks,'add_task',{'title':42})
    with pytest.raises(actions.ActionError):run(tasks,'set_status',{'id':'invalid','status':'bad','version':True})


def test_request_resolves_exact_published_version(tasks,monkeypatch):
    v1=actions.example()['manifest'];v2=copy.deepcopy(v1)
    v2['actions']['add_task']['steps'][0]['sql']="INSERT INTO calliope_apps.app_tasks(title) VALUES('v2:' || $1) RETURNING *"
    monkeypatch.setattr(server,'_conn',tasks)
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug,v:({'runtime_kind':'html'},{'manifest':{1:v1,2:v2}[v]}))
    monkeypatch.setattr(server,'_schema_allowed',lambda _:True)
    monkeypatch.setattr(auth,'JWT_SECRET',SEED)
    def request(v):return server._execute_app_action_request('tasks','add_task','actor',{'version':v,'inputs':{'title':'test'},'request_key':str(uuid.uuid4())})
    assert request(1)['rows'][0]['title']=='test'
    assert request(2)['rows'][0]['title']=='v2:test'
    with pytest.raises(actions.ActionError):server._execute_app_action_request('tasks','add_task','actor',{'inputs':{}})


def test_api_requires_existing_artifact_access_and_same_origin(monkeypatch):
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def decorator(f):routes[path]=f;return f
            return decorator
    server.register_dashboard_routes(MCP())
    handler=routes['/api/d/{slug}/actions/{action}']
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(server,'_conn',Conn)
    session=[None];allowed=[False];calls=[]
    monkeypatch.setattr(auth,'read_session_full',lambda r:session[0])
    monkeypatch.setattr(server.artifact_access,'can_view',lambda *a,**kw:allowed[0])
    monkeypatch.setattr(server,'_execute_app_action_request',lambda *a:calls.append(a) or {'committed':True})
    def req(origin='http://app.test',body=None):
        async def receive():return {'type':'http.request','body':json.dumps(body or {'version':1,'inputs':{},'request_key':str(uuid.uuid4())}).encode()}
        return Request({'type':'http','method':'POST','path':'/api/d/tasks/actions/a','scheme':'http','server':('app.test',80),
            'headers':[(b'content-type',b'application/json'),(b'origin',origin.encode())],
            'path_params':{'slug':'tasks','action':'a'}},receive)
    assert asyncio.run(handler(req())).status_code==401
    session[0]={'identity':'real@actor.com','sub':'other','mapped':True}
    assert asyncio.run(handler(req())).status_code==404
    allowed[0]=True
    assert asyncio.run(handler(req('https://evil.test'))).status_code==403
    assert asyncio.run(handler(req())).status_code==200
    assert len(calls)==1 and calls[0][2]=='real@actor.com'


@pytest.mark.parametrize('embedded',[False,True])
def test_browser_bridge_coalesces_retries_propagates_errors_and_disables_capture(embedded,monkeypatch):
    monkeypatch.setattr(server,'_conn',lambda: (_ for _ in ()).throw(RuntimeError('offline')))
    shim=server._dash_runtime_shim('tasks',4,actions.example()['manifest']) if embedded else server._dash_shim('tasks',4,actions.example()['manifest'])
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page()
        def serve(r):
            if r.request.url=='http://app.test/':r.fulfill(content_type='text/html',body=shim+'<p>App</p>')
            else:r.fulfill(status=404)
        page.route('**/*',serve);page.goto('http://app.test/')
        page.evaluate('''embedded=>{
          window.calls=[]; window.fail=false;
          const respond=async body=>{calls.push(body);await new Promise(r=>setTimeout(r,30));
            if(fail)throw Object.assign(new Error('Changed'),{code:'CONFLICT'});return {committed:true,rows:[]};};
          if(embedded)window.__rvbbitActionTransport=respond;
          else window.fetch=async(url,opts)=>{const data=await respond(JSON.parse(opts.body));return{ok:true,json:async()=>data};};
        }''',embedded)
        page.evaluate("Promise.all([rvbbitAction('add_task',{title:'a'}),rvbbitAction('add_task',{title:'a'})])")
        assert page.evaluate('calls.length')==1
        assert page.evaluate('calls[0].version')==4
        page.evaluate('window.fail=true')
        error=page.evaluate("rvbbitAction('add_task',{title:'b'}).catch(e=>({code:e.code,key:e.requestKey}))")
        assert error['code']=='CONFLICT';uuid.UUID(error['key'])
        page.evaluate('window.fail=false')
        page.evaluate("key=>rvbbitAction('add_task',{title:'b'},{requestKey:key})",error['key'])
        assert page.evaluate('calls[1].request_key===calls[2].request_key')
        page.evaluate('window.RVBBIT_ACTIONS_DISABLED=true')
        assert page.evaluate("rvbbitAction('add_task',{title:'c'}).catch(e=>e.code)")=='READ_ONLY_VIEW'
        assert page.evaluate('calls.length')==3
        browser.close()

from test_zen_chat_layout import zen_page  # noqa: E402,F401


def test_real_stage_app_persists_tasks_and_pins_action_version(tasks,zen_page,monkeypatch):
    page=zen_page
    example=actions.example();manifest=example['manifest']
    monkeypatch.setattr(server,'_conn',tasks)
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug,v:({'runtime_kind':'html'}, {'manifest':manifest} if v==4 else None))
    monkeypatch.setattr(server,'_schema_allowed',lambda _:True)
    monkeypatch.setattr(auth,'JWT_SECRET',SEED)
    monkeypatch.setattr(auth,'read_session_full',lambda r:{'identity':'user@example.com'})
    monkeypatch.setattr(server.artifact_access,'can_view',lambda *a,**k:True)
    # Skip overlay catalog lookup in this isolated DB; keep the real serving shims.
    monkeypatch.setattr(server,'_effective_artifact_manifest',lambda _id,_v,m:m)
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def register(f):routes[path]=f;return f
            return register
    server.register_dashboard_routes(MCP())
    document=calliope._artifact_version_document('tasks',4,example['template_html'],server._dash_shim,True,manifest,server._dash_runtime_shim)
    page.route('**/apps/tasks*',lambda r:r.fulfill(content_type='text/html',body=document))
    queries=[];commands=[]
    def read(r):
        body=r.request.post_data_json;queries.append(body)
        with tasks() as c:rows=c.execute(body['sql']).fetchall()
        r.fulfill(json={'rows':json.loads(json.dumps(rows,default=str))})
    page.route('**/api/d/tasks/q',read)
    def command(r):
        from urllib.parse import urlsplit
        body=r.request.post_data_json;commands.append(body)
        async def receive():return {'type':'http.request','body':json.dumps(body).encode()}
        request=Request({'type':'http','method':'POST','path':urlsplit(r.request.url).path,'scheme':'http','server':('zen.test',80),
            'headers':[(b'content-type',b'application/json'),(b'origin',b'http://zen.test')],
            'path_params':{'slug':'tasks','action':urlsplit(r.request.url).path.split('/')[-1]}},receive)
        with ThreadPoolExecutor(max_workers=1) as pool:
            response=pool.submit(asyncio.run,routes['/api/d/{slug}/actions/{action}'](request)).result()
        r.fulfill(status=response.status_code,content_type='application/json',body=response.body)
    page.route('**/api/d/tasks/actions/*',command)
    page.evaluate('''()=>{
      qa.setupEvents();document.body.classList.remove('zen');qa.state.zen=false;
      qa.state.surfaces=[{id:'app',kind:'artifact',turn_id:'one',title:'Team tasks',artifact_slug:'tasks',artifact_version:4,payload:{url:'/apps/tasks?v=4'}}];
      qa.renderChat();qa.renderStage();
    }''')
    frame=page.frame_locator('iframe[data-artifact-slug="tasks"]')
    frame.locator('#title').fill('Confirm the forecast')
    # Submit with the keyboard while the stage's initial packing animation settles.
    frame.locator('#title').press('Enter')
    expect(frame.locator('#message')).to_have_text('Saved.', timeout=5000)
    assert frame.locator('#tasks').inner_text()=='Confirm the forecast\nComplete'
    frame.get_by_role('button',name='Complete',exact=True).click()
    frame.get_by_role('button',name='Reopen',exact=True).wait_for()
    assert len(commands)==2 and all(c['version']==4 for c in commands)
    assert all(q['cache']=='no-store' for q in queries)
    with tasks() as c:
        row=c.execute('SELECT title,status,version FROM calliope_apps.app_tasks').fetchone()
        assert row=={'title':'Confirm the forecast','status':'done','version':2}
    # A sandbox message cannot override the version of its owning stage frame.
    iframe=page.locator('iframe[data-artifact-slug="tasks"]')
    child=iframe.element_handle().content_frame()
    child.evaluate("window.__rvbbitActionTransport({action:'add_task',version:999,inputs:{title:'Pinned version'},request_key:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'})")
    assert commands[-1]['version']==4
    assert page.evaluate("document.querySelector('iframe').dataset.appActionVersion")=='4'


def test_app_reads_bypass_pre_write_inflight_and_result_cache(monkeypatch):
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def register(f):routes[path]=f;return f
            return register
    server.register_dashboard_routes(MCP())
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(server,'_conn',Conn)
    monkeypatch.setattr(auth,'read_session_full',lambda r:{'identity':'actor'})
    monkeypatch.setattr(server.artifact_access,'can_view',lambda *a,**k:True)
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**k:{'rows':[{'value':'fresh'}]})
    monkeypatch.setattr(server,'_dashboard_query_submit',lambda *a,**k:pytest.fail('Must not join pre-write cached/inflight query'))
    monkeypatch.setattr(server,'_record',lambda *a,**k:None)
    async def receive():return {'type':'http.request','body':b'{"sql":"SELECT 1", "cache":"no-store"}'}
    request=Request({'type':'http','method':'POST','path':'/api/d/tasks/q','headers':[], 'path_params':{'slug':'tasks'}},receive)
    response=asyncio.run(routes['/api/d/{slug}/q'](request))
    assert response.status_code==200
    data=json.loads(response.body)
    assert data['rows']==[{'value':'fresh'}] and data['bridge_cache']['status']=='bypass'


@pytest.fixture
def operators(tasks):
    # Sequence increments survive rollback: a stand-in for a remote request,
    # without contacting or mutating any actual external service.
    with tasks() as c:
        c.execute("""
            CREATE TABLE rvbbit.operators(name text PRIMARY KEY);
            CREATE SEQUENCE rvbbit.remote_calls;
            CREATE FUNCTION rvbbit.remote_change(label text) RETURNS text LANGUAGE plpgsql AS $$
              BEGIN PERFORM nextval('rvbbit.remote_calls'); RETURN label; END $$;
            INSERT INTO rvbbit.operators VALUES('remote_change');
            CREATE TABLE rvbbit.mcp_tools(server text,name text);
            CREATE SCHEMA fresh_connector;
            CREATE FUNCTION fresh_connector.rows(n integer DEFAULT 2) RETURNS SETOF jsonb
              LANGUAGE sql AS $$SELECT jsonb_build_object('n',x) FROM generate_series(1,n) x$$;
            INSERT INTO rvbbit.mcp_tools VALUES('fresh_connector','rows');
        """)
    return tasks


def remote_manifest(*, steps=None, effects=None):
    action={'inputs':{'label':{'type':'string'}},'steps':steps or [
        {'sql':'SELECT rvbbit.remote_change($1) AS label','params':['label']}]}
    if effects:action['external_effects']=effects
    return {'actions':{'remote':action}}


def call_count(db):
    with db() as c:
        row=c.execute('SELECT last_value,is_called FROM rvbbit.remote_calls').fetchone()
        return row['last_value'] if row['is_called'] else 0


def test_registered_external_select_replays_without_dispatching_again(operators):
    manifest=remote_manifest();key=str(uuid.uuid4())
    first=run(operators,'remote',{'label':'bound 50% value'},key,manifest)
    replay=run(operators,'remote',{'label':'bound 50% value'},key,manifest)
    assert first['rows']==[{'label':'bound 50% value'}]
    assert replay['replayed'] and replay['run_id']==first['run_id']
    assert call_count(operators)==1


def test_multiple_scalar_and_set_returning_calls_use_live_adapters(operators):
    manifest=remote_manifest(steps=[{
        'sql':"SELECT rvbbit.remote_change($1) AS a,rvbbit.remote_change('other') AS b,r FROM fresh_connector.rows(2) r",
        'params':['label']}])
    result=run(operators,'remote',{'label':'first'},manifest=manifest)
    assert len(result['rows'])==2 and result['rows'][0]['a']=='first'
    assert result['rows'][1]['r']=={'n':2} and call_count(operators)==4
    # A newly registered function is immediately callable without code changes.
    with operators() as c:
        c.execute("CREATE FUNCTION fresh_connector.new_tool(n int) RETURNS int LANGUAGE sql AS $$SELECT n+8$$")
        c.execute("INSERT INTO rvbbit.mcp_tools VALUES('fresh_connector','new_tool')")
    manifest=remote_manifest(steps=[{'sql':'SELECT fresh_connector.new_tool(2) AS n'}],effects='read_only')
    assert run(operators,'remote',{'label':'unused'},manifest=manifest)['rows']==[{'n':10}]


def test_external_effect_survives_local_rollback_and_same_key_never_repeats(operators):
    manifest=remote_manifest(steps=[
        {'sql':'INSERT INTO calliope_apps.app_tasks(title) VALUES($1)','params':['label']},
        {'sql':'SELECT rvbbit.remote_change($1)','params':['label']},
        {'sql':'SELECT 1/0'},
    ])
    key=str(uuid.uuid4())
    for _ in range(2):
        with pytest.raises(actions.ActionError) as e:run(operators,'remote',{'label':'remote'},key,manifest)
        assert e.value.code=='EXTERNAL_OUTCOME_UNKNOWN'
    assert call_count(operators)==1
    with operators() as c:
        assert c.execute('SELECT count(*) n FROM calliope_apps.app_tasks').fetchone()['n']==0
    assert actions.recent(operators,'tasks')[0]['status']=='unknown'


def test_known_read_only_operator_can_retry_failed_transaction(operators):
    manifest=remote_manifest(steps=[{'sql':'SELECT rvbbit.remote_change($1)','params':['label']},{'sql':'SELECT 1/0'}],effects='read_only')
    key=str(uuid.uuid4())
    for _ in range(2):
        with pytest.raises(actions.ActionError) as e:run(operators,'remote',{'label':'read request count'},key,manifest)
        assert e.value.code=='ACTION_FAILED'
    assert call_count(operators)==2
    with operators() as c:assert c.execute('SELECT count(*) n FROM rvbbit.app_action_external_attempts').fetchone()['n']==0


def test_local_failure_before_external_dispatch_is_retryable(operators):
    setup(operators,"CREATE TABLE calliope_apps.unique_ids(id int PRIMARY KEY); INSERT INTO calliope_apps.unique_ids VALUES(1)")
    manifest=remote_manifest(steps=[{'sql':'INSERT INTO calliope_apps.unique_ids VALUES(1)'},
        {'sql':'SELECT rvbbit.remote_change($1) AS label','params':['label']}])
    key=str(uuid.uuid4())
    with pytest.raises(actions.ActionError) as e:run(operators,'remote',{'label':'once'},key,manifest)
    assert e.value.code=='ACTION_FAILED' and call_count(operators)==0
    setup(operators,'DELETE FROM calliope_apps.unique_ids')
    assert run(operators,'remote',{'label':'once'},key,manifest)['committed']
    assert call_count(operators)==1


def test_process_loss_after_claim_is_visible_and_does_not_redispatch(operators,monkeypatch):
    class Crash(BaseException):pass
    original=actions.psycopg.RawCursor.execute
    def crash(self,query,*a,**kw):
        if 'calliope_runtime' in query:raise Crash()
        return original(self,query,*a,**kw)
    manifest=remote_manifest();key=str(uuid.uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(actions.psycopg.RawCursor,'execute',crash)
        with pytest.raises(Crash):run(operators,'remote',{'label':'uncertain'},key,manifest)
    assert actions.recent(operators,'tasks')[0]['status']=='unconfirmed'
    with pytest.raises(actions.ActionError) as e:run(operators,'remote',{'label':'uncertain'},key,manifest)
    assert e.value.code=='EXTERNAL_OUTCOME_UNKNOWN' and call_count(operators)==0


def test_unregistered_function_still_fails_before_dispatch(operators):
    with operators() as c:c.execute('DELETE FROM rvbbit.operators')
    with pytest.raises(ValueError,match='not a registered'):
        run(operators,'remote',{'label':'never'},manifest=remote_manifest())
    assert call_count(operators)==0


def test_existing_app_manifest_can_still_write_original_schema(tasks):
    setup(tasks,'CREATE TABLE calliope.legacy(id int)')
    manifest={'actions':{'legacy':{'inputs':{},'steps':[{'sql':'INSERT INTO calliope.legacy VALUES(1) RETURNING id'}]}}}
    assert run(tasks,'legacy',{},manifest=manifest)['rows']==[{'id':1}]


def test_sql_operator_results_materialize_in_app_state(operators):
    manifest={'actions':{'load':{'external_effects':'read_only','inputs':{'n':{'type':'integer'}},'steps':[
        {'sql':"INSERT INTO calliope_apps.app_tasks(title) SELECT 'Item ' || (r->>'n') FROM fresh_connector.rows($1) r RETURNING title",'params':['n']} ]}}}
    key=str(uuid.uuid4())
    result=run(operators,'load',{'n':3},key,manifest)
    assert result['rows']==[{'title':f'Item {i}'} for i in range(1,4)]
    assert run(operators,'load',{'n':3},key,manifest)['replayed']
    with operators() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.app_tasks').fetchone()['n']==3


def test_concurrent_external_requests_dispatch_once(operators):
    key=str(uuid.uuid4());manifest=remote_manifest()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,operators,'remote',{'label':'once'},key,manifest) for _ in range(2)]
        results=[f.result() for f in futures]
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert call_count(operators)==1
