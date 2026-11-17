"""Exercise the real SQL harness with synthetic SQL, no provider requests."""
import json
import uuid
from pathlib import Path

import pytest
import system_checks as checks
from test_system_console import db, insert, OWNER, mount_system, zen_page

ROOT=Path(__file__).parents[2]


def test_execution_models_follow_the_relevant_step_or_managed_route():
    backends={'clover_llm':{'source_model':'openai/gpt-6-luna','transport_opts':{
        'model':'clover','model_policy':'managed','model_aliases':['gemma4'],
        'clover_route':{'model_version':'openrouter/openai/gpt-6-luna','source':'hosted-provider'}}},
        'extract':{'source_model':'gliner-medium','transport_opts':{}},'openrouter':{'transport_opts':{}}}
    def models(item):return checks.execution_models(item,'clover_llm',backends)
    assert models({'model':'','steps':None})[0]['model']=='openrouter/openai/gpt-6-luna'
    assert models({'model':'gemma4','steps':None})[0]['model']=='openrouter/openai/gpt-6-luna'
    assert models({'model':'calliope-luna','steps':None})[0]['model']=='calliope-luna'
    op={'model':'unused-llm-default','steps':[{'kind':'specialist','specialist':'extract'}]}
    assert models(op)[0]['model']=='gliner-medium'
    op['steps']=[{'kind':'llm','model':'custom/model','provider':'openrouter'}]
    assert models(op)[0]['model']=='custom/model'
    op['steps']=[{'kind':'code','fn':'math'}]
    assert models(op)==[]
    assert models({'model':'unused','steps':None,'opts_default':{'model':'clover'}})[0]['model']=='openrouter/openai/gpt-6-luna'

@pytest.fixture
def harness(db,monkeypatch):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.backends(name text PRIMARY KEY,transport text,endpoint_url text,batch_size int,max_concurrent int,timeout_ms int,auth_header_env text,transport_opts jsonb,description text,source_provider text,source_model text,source_revision text,install_manifest jsonb,created_at timestamptz);
        CREATE TABLE rvbbit.warren_deployments(backend_name text,updated_at timestamptz,created_at timestamptz,deployment_id uuid,node_name text,kind text,name text,status text,error text,health jsonb,stopped_at timestamptz);
        CREATE TABLE rvbbit.operators(name text PRIMARY KEY,tests jsonb,steps jsonb,model text,opts_default jsonb DEFAULT '{}');
        CREATE FUNCTION rvbbit.default_provider() RETURNS text LANGUAGE sql AS $$ SELECT 'worker'::text $$;''')
        c.execute((ROOT/'crates/pg_rvbbit/sql/migrations/0144_operator_test_runs.sql').read_text())
        c.execute((ROOT/'crates/pg_rvbbit/sql/migrations/0145_test_cache_bypass.sql').read_text())
        c.execute((ROOT/'crates/pg_rvbbit/sql/migrations/0477_test_failure_details.sql').read_text())
        c.execute((ROOT/'crates/pg_rvbbit/sql/migrations/0473_operator_availability.sql').read_text())
        for name,source,enabled in [('worker','worker',True),('legacy','legacy-clover',True),('missing','unconfigured',False)]:
            c.execute("INSERT INTO rvbbit.backends(name,auth_header_env,transport_opts,source_model) VALUES(%s,'RVBBIT_CLOVER_KEY',%s::jsonb,'test-model')",(name,json.dumps({'clover_route':{'id':name,'source':source,'enabled':enabled}})))
        for name,backend in [('good','worker'),('old','legacy'),('absent','missing')]:
            tests=[{'name':'works','sql':"SELECT 'correct'",'expect':{'type':'exact','value':'correct'}},
                   {'name':'cache','sql':"SELECT current_setting('rvbbit.cache_bypass')",'expect':{'type':'exact','value':'on'}}]
            if name=='good':tests += [{'name':'wrong','sql':"SELECT 'nope'",'expect':{'type':'exact','value':'yes'}},
                                      {'name':'error','sql':'SELECT 1/0','expect':{'type':'not_empty'}}]
            c.execute('INSERT INTO rvbbit.operators(name,tests,steps,model) VALUES(%s,%s::jsonb,%s::jsonb,\'model-v1\')',(name,json.dumps(tests),json.dumps([{'kind':'specialist','specialist':backend}])))
    monkeypatch.setattr(checks,'launch',lambda *a:None)
    return db


def test_availability_controls_runtime_and_discovery(harness):
    with harness() as c:
        before={r['name']:r['callable'] for r in c.execute('SELECT name,callable FROM rvbbit.warren_backend_status')}
        assert before=={'worker':True,'legacy':True,'missing':False}
        rows=checks.discovery(c,[{'kind':'cap_operator','name':'absent'},{'kind':'cap_mcp_tool','name':'untouched'}])
        assert rows==[{'kind':'cap_mcp_tool','name':'untouched'}]
        c.execute("UPDATE rvbbit.backends SET transport_opts=jsonb_set(transport_opts,'{clover_route,enabled}','true') WHERE name='missing'")
        assert c.execute("SELECT callable FROM rvbbit.operator_availability WHERE operator='absent'").fetchone()['callable']
        assert checks.discovery(c,[{'kind':'cap_operator','name':'rvbbit.absent'}])[0]['sql_availability']['callable']


def test_harness_logs_incrementally_and_preserves_definitions(harness):
    sid=str(insert(harness)['id'])
    rid=checks.start(harness,OWNER,sid)['run_id']
    assert checks.start(harness,OWNER,sid)['run_id']==rid
    checks.execute(harness,rid)
    state=checks.read(harness,rid,'good')
    run=state['runs'][0]
    assert (run['status'],run['passed'],run['total'],run['skipped'])==('complete',4,6,1)
    assert state['detail']['definition_at_run']
    assert len(state['detail']['definition']['tests'])==4
    assert any(r['error'] and 'division by zero' in r['error'] for r in state['detail']['results'])
    assert next(r for r in state['detail']['results'] if r['test_name']=='cache')['actual']=='on'
    with harness() as c:c.execute("UPDATE rvbbit.operators SET model='model-v2',tests='[]' WHERE name='good'")
    saved=checks.read(harness,rid,'good')
    assert saved['detail']['definition']['model']=='model-v1'
    assert len(saved['detail']['definition']['tests'])==4
    old=checks.read(harness,rid,'old')
    assert old['detail']['definition']['model']=='model-v1'
    assert 'operator_test_runs' in checks.detail_query(rid,'old')['sql']


def test_sql_warning_is_saved_even_when_operator_returns_null(harness):
    with harness() as c:
        c.execute("""CREATE FUNCTION warning_result() RETURNS text LANGUAGE plpgsql AS $$ BEGIN
          RAISE WARNING 'worker HTTP 503: model is not loaded'; RETURN NULL; END $$""")
        c.execute("UPDATE rvbbit.operators SET tests=%s::jsonb WHERE name='good'", (json.dumps([
            {'name':'warning','sql':'SELECT warning_result()','expect':{'type':'not_empty'}}]),))
    sid=str(insert(harness)['id'])
    rid=checks.start(harness,OWNER,sid,'good')['run_id']
    checks.execute(harness,rid)
    value=checks.read(harness,rid,'good')
    assert value['detail']['results'][0]['passed'] is False
    assert 'worker HTTP 503: model is not loaded' in value['observations'][0]['detail']
    import system_console as system
    snap=system.save_snapshot(harness,OWNER,{'id':str(uuid.uuid4()),'session_id':sid,
        'observed_at':'2026-10-11T18:00:00Z','window':'24h','capabilities':[],'usage':{},'services':[],'warnings':[]})
    context=system.hydrate(harness,OWNER,{'snapshot_id':snap['id'],'tab':'checks',
        'check_run_id':rid,'check_operator':'good','check_search':'good'},sid)
    assert 'worker HTTP 503: model is not loaded' in context['checks']['detail']['observation']['detail']
    assert context['checks']['visible_functions']==['good']
    assert context['checks']['observed_at']
    assert context['checks']['run']['run_id']==rid
    assert 'function_diagnostics' in checks.detail_query(rid,'good')['sql']


def test_validation_error_and_sql_details_do_not_abort_following_tests(harness):
    with harness() as c:
        c.execute("""CREATE FUNCTION detailed_error() RETURNS text LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'Unavailable' USING DETAIL='Worker is offline', HINT='Start the service'; END $$""")
        c.execute("UPDATE rvbbit.operators SET tests=%s::jsonb WHERE name='good'",(json.dumps([
            {'name':'bad_number','sql':"SELECT 'not a number'",'expect':{'type':'min','value':'2'}},
            {'name':'failure','sql':'SELECT detailed_error()','expect':{'type':'not_empty'}},
            {'name':'after','sql':"SELECT 'ok'",'expect':{'type':'exact','value':'ok'}}]),))
        rows={r['test_name']:r for r in c.execute("SELECT * FROM rvbbit.run_tests('good')")}
    assert 'invalid input syntax' in rows['bad_number']['error']
    assert 'Detail: Worker is offline' in rows['failure']['error']
    assert 'Hint: Start the service' in rows['failure']['error']
    assert rows['after']['passed']


def test_new_coverage_is_embedded_and_preserves_custom_tests(harness):
    migration=(ROOT/'crates/pg_rvbbit/sql/migrations/0476_system_check_coverage.sql').read_text()
    with harness() as c:
        c.execute("INSERT INTO rvbbit.operators(name,tests) VALUES('clover_web_scrape','[]'),('clover_web_watch','[]')")
        c.execute(migration)
        tests=c.execute("SELECT tests FROM rvbbit.operators WHERE name='clover_web_scrape'").fetchone()['tests']
        assert 'https://example.com' in tests[0]['sql']
        custom=[{'name':'custom','sql':"SELECT 'custom'",'expect':{'type':'not_empty'}}]
        c.execute("UPDATE rvbbit.operators SET tests=%s::jsonb WHERE name='clover_web_scrape'",(json.dumps(custom),))
        c.execute(migration)
        assert c.execute("SELECT tests FROM rvbbit.operators WHERE name='clover_web_scrape'").fetchone()['tests']==custom
    manifest=json.loads((ROOT/'capabilities/packs/managed/clover/capability.json').read_text())
    assert 'public_page_content' in json.dumps(manifest)
    seed=json.loads((ROOT/'crates/pg_rvbbit/src/capability_catalog_seed.json').read_text())
    saved=next(c['capability_manifest'] for c in seed['capabilities'] if c['capability_manifest'].get('name')=='clover')
    assert 'public_page_content' in json.dumps(saved)


def test_explicit_legacy_run_and_interrupted_run(harness):
    sid=str(insert(harness)['id']);rid=checks.start(harness,OWNER,sid,'old')['run_id']
    checks.execute(harness,rid)
    assert checks.read(harness,rid,'old')['runs'][0]['passed']==2
    rid2=checks.start(harness,OWNER,sid,'good')['run_id']
    with harness() as c:c.execute("UPDATE rvbbit.operator_test_batches SET status='running' WHERE run_id=%s",(rid2,))
    checks.execute(harness,rid2)
    state=checks.read(harness,rid2,'good')
    assert state['runs'][0]['status']=='interrupted'
    assert not state['detail']['results']


def test_check_results_open_in_admin_data_workspace(harness):
    import data_sql
    from test_system_console import MEMBER
    sid=str(insert(harness)['id']);rid=checks.start(harness,OWNER,sid,'good')['run_id']
    checks.execute(harness,rid)
    statement=checks.detail_query(rid,'good')['sql']
    def factory(**kwargs):return harness()
    allowed=data_sql.system_relations(factory,None,OWNER,statement)
    assert set(allowed)=={'rvbbit.operator_test_runs','rvbbit.operator_test_observations'}
    prepared=data_sql.prepare(statement,allowed,[])
    with harness() as c:
        rows=c.execute(prepared['sql']).fetchall()
    assert len(rows)==4 and all(r['routes'][0]['backend']=='worker' for r in rows)
    with pytest.raises(PermissionError):data_sql.system_relations(factory,None,MEMBER,statement)
    with pytest.raises(ValueError):data_sql.prepare('SELECT * FROM rvbbit.backends',allowed,[])


def test_default_upgrade_preserves_custom_models_and_run_snapshots(harness):
    sid=str(insert(harness)['id']);rid=checks.start(harness,OWNER,sid,'good')['run_id']
    checks.execute(harness,rid)
    with harness() as c:
        c.execute("""CREATE FUNCTION rvbbit.create_operator(op_model text DEFAULT 'openai/gpt-5.4-mini')
          RETURNS text LANGUAGE sql AS $$ SELECT op_model $$;
          UPDATE rvbbit.operators SET model='openai/gpt-5.4-mini',
            steps='[{"kind":"llm","model":"openai/gpt-5.4-mini","provider":"openrouter"}]',
            opts_default='{"model":"openai/gpt-5.6-luna"}' WHERE name='good';""")
        c.execute((ROOT/'crates/pg_rvbbit/sql/migrations/0474_luna_model_defaults.sql').read_text())
        assert c.execute('SELECT rvbbit.create_operator() AS model').fetchone()['model']=='openai/gpt-6-luna'
        row=c.execute("SELECT * FROM rvbbit.operators WHERE name='good'").fetchone()
        assert row['model']==row['steps'][0]['model']==row['opts_default']['model']=='openai/gpt-6-luna'
        assert c.execute("SELECT model FROM rvbbit.operators WHERE name='old'").fetchone()['model']=='model-v1'
    state=checks.read(harness,rid,'good')
    assert state['detail']['definition']['execution'][0]['model']=='test-model'
    assert state['detail']['current_execution'][0]['model']=='openai/gpt-6-luna'


def test_relevance_contract_checks_order_and_bounds_without_rewriting_history(harness):
    migration=(ROOT/'crates/pg_rvbbit/sql/migrations/0475_relevance_test_contract.sql').read_text()
    legacy=json.loads(migration.split('$old$')[1])
    with harness() as c:
        c.execute("INSERT INTO rvbbit.operators(name,tests,steps,model) VALUES('clover_relevance',%s::jsonb,'[]','unused')",(json.dumps(legacy),))
        c.execute('CREATE TABLE rvbbit.capability_catalog(id text,manifest jsonb,catalog_entry jsonb)')
        instruction=migration.split('$old_instruction$')[1]
        c.execute("INSERT INTO rvbbit.capability_catalog VALUES('managed/clover',%s::jsonb,%s::jsonb)",(json.dumps({'install':[instruction]}),json.dumps({'manifest':{'install':[instruction]}})))
        c.execute("""CREATE FUNCTION rvbbit.clover_relevance(t text,criterion text) RETURNS float8 LANGUAGE sql AS $$
          SELECT CASE WHEN t='quick sql engine' THEN 0.59765625 ELSE 0.341796875 END::float8 $$""")
    sid=str(insert(harness)['id']);old=checks.start(harness,OWNER,sid,'clover_relevance')['run_id'];checks.execute(harness,old)
    with harness() as c:
        c.execute(migration)
        assert 'relevance_order' in c.execute('SELECT manifest::text AS text FROM rvbbit.capability_catalog').fetchone()['text']
    new=checks.start(harness,OWNER,sid,'clover_relevance')['run_id'];checks.execute(harness,new)
    result=checks.read(harness,new,'clover_relevance')
    assert all(r['passed'] for r in result['detail']['results'])
    assert checks.read(harness,old,'clover_relevance')['detail']['definition']['tests']==legacy
    with harness() as c:
        c.execute("UPDATE rvbbit.operators SET tests='[]' WHERE name='clover_relevance'")
        c.execute(migration)
        assert c.execute("SELECT tests FROM rvbbit.operators WHERE name='clover_relevance'").fetchone()['tests']==[]


def test_changed_definition_is_skipped_and_unknown_inputs_rejected(harness):
    sid=str(insert(harness)['id']);rid=checks.start(harness,OWNER,sid,'good')['run_id']
    with harness() as c:c.execute("UPDATE rvbbit.operators SET model='changed' WHERE name='good'")
    checks.execute(harness,rid)
    state=checks.read(harness,rid,'good')
    assert state['runs'][0]['skipped']==1
    assert 'Definition changed' in state['observations'][0]['detail']
    with pytest.raises(ValueError):checks.start(harness,OWNER,sid,"x');drop schema rvbbit;--")
    with pytest.raises(ValueError):checks.read(harness,9999)
    with pytest.raises(ValueError):checks.detail_query('garbage')


def test_checks_ui_uses_harness_results_and_data_panel(zen_page):
    page=zen_page
    op={'operator':'clover_test','callable':True,'test_count':1,'model':'test-model','dependencies':[{'backend':'embed','callable':True,'route':{'source':'hosted-provider','model_version':'voyage'}}]}
    definition={**op,'model':'openai/gpt-5.4-mini','execution':[{'kind':'specialist','provider':'embed','model':'voyageai/voyage-4'}],
                'tests':[{'name':'vector','sql':"SELECT '<script>not markup</script>'",'expect':{'type':'not_empty'}}]}
    state={'available':True,'operators':[op],'runs':[{'run_id':n,'status':'complete','total':1,'passed':0,'skipped':0} for n in range(107,100,-1)],
           'matrix':[{'operator':op['operator'],'run_id':7,'total':1,'passed':0}],
           'observations':[],'run_id':7,'detail':{'definition':definition,'definition_at_run':True,'results':[{'test_name':'vector','passed':False,'actual':'[]','error':None}]}}
    requests=[]; unavailable=[False]
    def route(r):
        if unavailable[0]:
            r.fulfill(status=503,json={'error':{'message':'Checks temporarily unavailable'}});return
        if r.request.method=='POST':requests.append(r.request.post_data_json);r.fulfill(json={'run_id':7})
        else:r.fulfill(json=state)
    page.route('**/api/calliope/system/checks*',route)
    page.route('**/api/calliope/system/detail-query?*',lambda r:r.fulfill(json=checks.detail_query(7,'clover_test')))
    # Production serves this shared bundle under Meaning, not /calliope.
    page.route('**/calliope/business-logic-code-editor.mjs',lambda r:r.fulfill(status=404))
    page.route('**/meaning/logic/business-logic-code-editor.mjs',lambda r:r.fulfill(
        body=(Path(__file__).parent/'calliope/business-logic-code-editor.mjs').read_bytes(),content_type='text/javascript'))
    mount_system(page)
    page.get_by_role('button',name='Checks',exact=True).click()
    page.get_by_role('button',name='Run this function',exact=True).wait_for()
    page.get_by_role('textbox',name='Test SQL · vector',exact=True).wait_for()
    assert page.locator('[data-check-history]').count()==5
    assert page.locator('[data-check-history]').all_text_contents()==['#103','#104','#105','#106','#107']
    assert 'openai/gpt-5.4-mini' not in page.locator('.system-check-detail').inner_text()
    assert 'voyageai/voyage-4' in page.locator('.system-check-detail').inner_text()
    assert 'clover_test · run #7' in page.locator('#system-stage-context').inner_text()
    search=page.get_by_role('searchbox',name='Find a SQL function')
    search.fill('no-match')
    assert page.locator('[data-check-empty]').is_visible()
    search.fill('')
    assert page.locator('[data-check-search-row="clover_test"]').is_visible()
    search.fill('clover')
    assert page.evaluate('CalliopeSystemStage.origin().check_search')=='clover'
    search.fill('')
    assert page.locator('.system-check-sql .cm-content').get_attribute('contenteditable')=='false'
    assert page.locator('.system-check-sql .cm-line span').count()>0
    assert 'not markup' in page.locator('.system-check-case').inner_text()
    assert page.locator('.system-check-case script').count()==0
    assert page.evaluate('CalliopeSystemStage.origin().check_operator')=='clover_test'
    page.evaluate('qa.setupEvents()')
    page.route('**/calliope/data-panel.html',lambda r:r.fulfill(body='<main>Data workspace</main>',content_type='text/html'))
    page.get_by_role('button',name='Open vector in Data',exact=True).click()
    assert page.get_by_role('tab',name='Data',exact=True).get_attribute('aria-selected')=='true'
    page.get_by_role('button',name='Ask Callie',exact=True).click()
    assert page.get_by_role('tab',name='Chat',exact=True).get_attribute('aria-selected')=='true'
    assert 'clover_test' in page.evaluate('qa.composerValue()')
    page.get_by_role('button',name='Run this function',exact=True).click()
    page.wait_for_function('!document.querySelector("[data-check-run-one]").disabled')
    assert requests[0]['operator']=='clover_test' and 'sql' not in requests[0]
    unavailable[0]=True
    page.get_by_role('button',name='Refresh system',exact=True).click()
    page.get_by_text('Checks temporarily unavailable',exact=True).wait_for()
    unavailable[0]=False
    page.get_by_role('button',name='Refresh system',exact=True).click()
    page.get_by_text('Checks temporarily unavailable',exact=True).wait_for(state='hidden')
    for width in (1500,1280,1000,390):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert page.locator('.system-check-workspace').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        if width>=1000:
            widths=page.locator('.system-check-matrix').evaluate('''el=>({width:el.clientWidth,scroll:el.scrollWidth,
                cells:[...el.querySelectorAll('thead th')].map(c=>({width:c.clientWidth,scroll:c.scrollWidth,button:c.firstElementChild?.clientWidth}))})''')
            assert all(c['scroll']<=c['width']+1 for c in widths['cells']), widths
            if width==1280:assert widths['scroll']<=widths['width']+1, widths


def test_worker_lock_prevents_duplicate_or_false_interruption(harness):
    sid=str(insert(harness)['id']);rid=checks.start(harness,OWNER,sid,'good')['run_id']
    with harness() as c:
        c.execute('SELECT pg_advisory_lock(%s)',(checks.LOCK,))
        checks.execute(harness,rid)
        assert c.execute('SELECT status FROM rvbbit.operator_test_batches WHERE run_id=%s',(rid,)).fetchone()['status']=='queued'
    checks.execute(harness,rid)
    checks.execute(harness,rid)
    assert checks.read(harness,rid,'good')['runs'][0]['total']==4


def test_open_selects_recorded_results_and_operator_uses_its_own_latest_run(harness):
    sid=str(insert(harness)['id'])
    first=checks.start(harness,OWNER,sid,'good')['run_id']; checks.execute(harness,first)
    last=checks.start(harness,OWNER,sid,'old')['run_id']; checks.execute(harness,last)
    opened=checks.read(harness)
    assert (opened['run_id'],opened['operator'])==(last,'old')
    assert len(opened['detail']['results'])==2
    selected=checks.read(harness,operator='good')
    assert selected['run_id']==first and len(selected['detail']['results'])==4
    explicit=checks.read(harness,last,'good')
    assert explicit['detail']['state']=='not_in_run' and explicit['detail']['results']==[]
    # Recorded details remain accessible even when the latest global run list
    # no longer includes that operator's most recent result.
    with harness() as c:
        for n in range(20):
            c.execute("INSERT INTO rvbbit.operator_test_runs(run_id,operator,test_name,passed) VALUES(%s,'old','works',true)",(100+n,))
    selected=checks.read(harness,operator='good')
    assert selected['selected_run']['run_id']==first
    assert selected['selected_run']['total']==4 and selected['detail']['state']=='complete'
    assert any(o['run_id']==first for o in selected['observations'])
    # Ask Callie carries the same older run shown in the detail panel.
    import system_console as system
    snap=system.save_snapshot(harness,OWNER,{'id':'ad4b3910-7757-4e93-89de-5a825c8ea179','session_id':sid,
        'observed_at':'2026-10-11T18:00:00Z','window':'24h','capabilities':[],'usage':{},'services':[],'warnings':[]})
    context=system.hydrate(harness,OWNER,{'snapshot_id':snap['id'],'tab':'checks','check_run_id':first,'check_operator':'good'},sid)
    assert context['checks']['run']['run_id']==first and context['checks']['run']['total']==4


def test_open_recovers_inflight_selection_without_starting_a_new_batch(harness):
    sid=str(insert(harness)['id'])
    rid=checks.start(harness,OWNER,sid,'good')['run_id']
    opened=checks.read(harness)
    assert (opened['run_id'],opened['operator'],opened['detail']['state'])==(rid,'good','queued')
    with harness() as c:
        c.execute("UPDATE rvbbit.operator_test_batches SET status='running',active_operator='good' WHERE run_id=%s",(rid,))
    assert checks.read(harness)['detail']['state']=='running'
    never=checks.read(harness,operator='absent')
    assert never['run_id'] is None and never['detail']['state']=='never_run'
    with harness() as c:assert c.execute('SELECT count(*) AS n FROM rvbbit.operator_test_batches').fetchone()['n']==1


def test_discovery_temporarily_excludes_recently_failed_routes(harness):
    sid=str(insert(harness)['id'])
    rows=[{'kind':'cap_operator','name':'good'},{'kind':'cap_mcp_tool','name':'unrelated'}]
    with harness() as c:
        assert len(checks.discovery(c,rows))==2
        c.execute("INSERT INTO rvbbit.calliope_system_snapshots(id,session_id,owner_email,payload) VALUES(%s,%s,%s,%s::jsonb)",
                  (str(uuid.uuid4()),sid,OWNER,json.dumps({'probed':True,'capabilities':[{'id':'worker','state':'unavailable'}]})))
        assert checks.discovery(c,rows)==[rows[1]]
        c.execute("UPDATE rvbbit.calliope_system_snapshots SET payload=jsonb_set(payload,'{capabilities,0,state}','\"ready\"')")
        assert len(checks.discovery(c,rows))==2
        c.execute("UPDATE rvbbit.calliope_system_snapshots SET payload=jsonb_set(payload,'{capabilities,0,state}','\"unavailable\"'),observed_at=now()-interval '3 minutes'")
        assert len(checks.discovery(c,rows))==2
