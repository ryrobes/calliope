import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
import stage_charts

ROOT = Path(__file__).parent
SPEC = {'data': {'name': 'sales'}, 'mark': 'bar', 'transform': [{'filter': 'datum.revenue >= 10'}], 'encoding': {'x': {'field': 'channel', 'type': 'nominal'}, 'y': {'field': 'revenue', 'type': 'quantitative'}}}


def test_live_contract_validates_named_data_and_dimensions():
    payload = stage_charts.validate('Sales', {'sales': 'select 1'}, SPEC)
    assert payload['queries'] == {'sales': 'select 1'} and 'datasets' not in payload
    for mutation in ({'data': {'url': '/private'}}, {'data': {'values': []}}, {'height': 'container'}, {'datasets': {'sales': []}}):
        with pytest.raises(ValueError):
            stage_charts.validate('Sales', {'sales': 'select 1'}, {**SPEC, **mutation})
    with pytest.raises(ValueError, match='Unused'):
        stage_charts.validate('Sales', {'sales': 'select 1', 'unused': 'select 2'}, SPEC)
    stage_charts.validate('Sales', {'sales': 'select 1'}, {**SPEC, 'height': {'step': 22}})


@pytest.fixture
def chart_page():
    source = (ROOT / 'calliope/calliope.js').read_text()
    scripts = []
    manifest = json.loads((ROOT / 'vendor/manifest.json').read_text())
    for name in ('vega', 'vega-lite', 'vega-embed'):
        scripts.append(ROOT / 'vendor' / f"{name}@{manifest['libraries'][name]['version']}" / f'{name}.min.js')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1700, 'height': 1100})
        calls = []
        def route(r):
            if r.request.url.endswith('/chart-data'):
                calls.append(r.request.post_data_json)
                r.fulfill(json={'datasets': {'sales': [{'channel': x, 'revenue': (i+1)*10, 'cost': (i+1)*6} for i,x in enumerate('ABCD')]}, 'results': {'sales': {'truncated': False}}, 'render_attempt':'attempt-1'})
            elif r.request.url.endswith('/chart-render'):
                r.fulfill(json={'accepted':True})
            else:
                r.fulfill(content_type='text/html', headers={'Content-Security-Policy': "script-src 'self' 'unsafe-inline'"}, body='<html data-theme="dark"><body class="zen" data-warehouse-page="calliope"><div id="stageScroll"><div id="stage" class="stage"><div class="zen-renderings"><div class="zen-packed"></div></div></div></div></body></html>')
        page.route('**/*', route)
        page.goto('http://charts.test')
        page.add_style_tag(content=(ROOT/'calliope/calliope.css').read_text())
        page.add_style_tag(content=':root{--foreground:#eeeeee;--dim:#b5b5b5;--chart-1:#53bfd1;--accent:#53bfd1}body{display:block!important;margin:0!important}#stageScroll{height:1000px;overflow:auto}#stage{display:block;width:1500px;padding:0} .zen-renderings{display:block}.chart-body{box-sizing:border-box}.surface{margin:0}')
        for script in scripts: page.add_script_tag(path=str(script))
        import calliope
        page.add_script_tag(content=calliope._chart_interpreter_script())
        errors=[]
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.add_script_tag(content="""
          const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
          const els={stage:$('#stage'),stageScroll:$('#stageScroll')};const state={current:{id:'session'},surfaces:[]};
          function escapeHtml(s){return String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;')}
        """)
        page.add_script_tag(content=source[source.index('  const chartHistoryLineages ='):source.index('  function visibleStageSurfaces(')])
        page.add_script_tag(content=source[source.index('  const chartSpecs ='):source.index('  function renderImage(')])
        page.add_script_tag(content=source[source.index('  const stageRenderTrees ='):source.index('  function renderStage(')])
        page.add_script_tag(content="""
          window.addChart=(spec,size='wide',id='one')=>{
            const s={id,kind:'chart',payload:{spec,queries:{sales:'select 1'},content_sha256:id,size}};
            const card=document.createElement('article');card.className='surface kind-chart chart-size-'+size;
            card.innerHTML=renderVegaChart(s);document.querySelector('.zen-packed').append(card);
            syncStagePacking();hydrateChartSurfaces();
          };
        """)
        yield page,calls,errors
        assert not errors
        browser.close()


@pytest.mark.parametrize('composition', ['single','layer','hconcat','vconcat','facet','repeat','step'])
def test_live_chart_natural_height_resize_and_cached_theme(chart_page, composition):
    page,calls,errors=chart_page
    spec=SPEC
    if composition=='layer':spec={'data':SPEC['data'],'layer':[{k:v for k,v in SPEC.items() if k!='data'},{'mark':'point','encoding':SPEC['encoding']}], 'height':320}
    elif composition in ('hconcat','vconcat'):spec={composition:[{**SPEC,'height':180},{**SPEC,'height':360}]}
    elif composition=='facet':spec={'data':SPEC['data'],'facet':{'field':'channel','type':'nominal'},'columns':2,'spec':{k:v for k,v in SPEC.items() if k!='data'}}
    elif composition=='repeat':spec={'data':SPEC['data'],'repeat':['revenue','cost'],'spec':{'mark':'bar','encoding':{'x':SPEC['encoding']['x'],'y':{'field':{'repeat':'repeat'},'type':'quantitative'}}}}
    elif composition=='step':spec={**SPEC,'height':{'step':22},'encoding':{'y':SPEC['encoding']['x'],'x':SPEC['encoding']['y']}}
    page.evaluate('(s)=>addChart(s)',spec)
    page.locator('.chart-body.is-ready, .chart-body.is-error').wait_for(timeout=15000)
    assert page.locator('.chart-body.is-error').count()==0,page.locator('.chart-status').inner_text()
    assert len(calls)==1
    first=page.locator('.chart-canvas svg').bounding_box()
    assert first['width']>150 and first['height']>40
    if composition=='vconcat':assert first['height']>540
    for width in (1100,650,1500):
        page.evaluate('(w)=>document.querySelector("#stage").style.width=w+"px"',width)
        page.wait_for_timeout(350)
        page.wait_for_function("() => !document.querySelector('.chart-body').classList.contains('is-loading')")
        rect=page.locator('.chart-canvas svg').bounding_box()
        card=page.locator('article').bounding_box()
        assert rect['width']>100 and rect['height']>30
        assert rect['x']+rect['width']<=card['x']+card['width']+2
        assert rect['y']+rect['height']<=card['y']+card['height']+2
        assert page.locator('.zen-packed').bounding_box()['height']>=card['height']-1
    assert len(calls)==1
    page.evaluate("document.documentElement.dataset.theme='light'")
    page.wait_for_timeout(300)
    assert len(calls)==1
    page.get_by_role('button',name='Refresh',exact=True).click()
    page.wait_for_function("() => !document.querySelector('.chart-body').classList.contains('is-loading')")
    assert len(calls)==2 and calls[-1]['refresh'] is True
    generation=page.evaluate("chartSpecs.get('one').generation")
    page.wait_for_timeout(400)
    assert page.evaluate("chartSpecs.get('one').generation")==generation
    page.screenshot(path=f'/tmp/calliope-live-chart-{composition}.png')

# Exercise publication against a disposable database, including trusted child routing.
from test_generated_image_delivery import db, seed, authorize
import server


def test_publish_retry_revision_and_no_stored_rows(db, monkeypatch):
    destination=seed(db)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':True})
    monkeypatch.setattr(stage_charts,'compile_spec',lambda *args:None)
    token=authorize(monkeypatch)
    try:
        first=server.tool_publish_chart('Sales',{'sales':'select 1'},SPEC,caller_email='owner@example.com')
        assert 'error' not in first,first
        retry=server.tool_publish_chart('Sales',{'sales':'select 1'},SPEC,caller_email='owner@example.com')
        assert retry['surface_id']==first['surface_id']
        second=server.tool_publish_chart('Revised',{'sales':'select 2'},SPEC,replaces_surface_id=first['surface_id'],caller_email='owner@example.com')
        assert 'error' not in second,second
        with db() as conn:
            rows=conn.execute('select * from rvbbit.calliope_surfaces order by ordinal').fetchall()
            assert len(rows)==2
            assert str(rows[1]['parent_surface_id'])==first['surface_id']
            assert rows[0]['payload']['queries']=={'sales':'select 1'}
            assert 'datasets' not in rows[0]['payload'] and 'values' not in json.dumps(rows[0]['payload'])
        invalid=server.tool_publish_chart('X',{'sales':'select 1'},SPEC,replaces_surface_id='00000000-0000-0000-0000-000000000001',caller_email='owner@example.com')
        assert invalid['error']['code']=='CHART_PUBLICATION_FAILED'
        monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':False})
        assert 'error' in server.tool_publish_chart('Denied',{'sales':'delete from x'},SPEC,caller_email='owner@example.com')
    finally:server._AUTHORIZATION_CONTEXT.reset(token)
    with db() as conn:
        conn.execute("insert into rvbbit.calliope_specialist_runs values('child','owner@example.com',%s,%s,'running')",(destination['session_id'],destination['turn_id']))
    monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':True})
    token=authorize(monkeypatch,session='child')
    try:
        child=server.tool_publish_chart('Child',{'sales':'select 3'},SPEC,caller_email='owner@example.com')
        assert 'error' not in child,child
        with db() as conn:
            row=conn.execute('select session_id,turn_id from rvbbit.calliope_surfaces where id=%s',(child['surface_id'],)).fetchone()
            assert str(row['session_id'])==destination['session_id']
            assert str(row['turn_id'])==destination['turn_id']
    finally:server._AUTHORIZATION_CONTEXT.reset(token)
    token=authorize(monkeypatch,trusted=False)
    try:assert server.tool_publish_chart('Denied',{'sales':'select 1'},SPEC,caller_email='owner@example.com')['error']['code']=='NO_STAGE_CONTEXT'
    finally:server._AUTHORIZATION_CONTEXT.reset(token)


def test_compile_rejects_bad_vega_and_accepts_composition(monkeypatch):
    monkeypatch.setenv('CHROMIUM_PATH','/usr/bin/chromium')
    stage_charts.compile_spec({'vconcat':[SPEC,SPEC]},ROOT/'vendor')
    with pytest.raises(Exception):
        stage_charts.compile_spec({'mark':'this-mark-does-not-exist','data':{'name':'sales'}},ROOT/'vendor')


def test_typed_spec_repair_preserves_arbitrary_metadata_and_is_persisted(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(server, '_conn', db)
    monkeypatch.setattr(server, 'tool_validate_sql', lambda sql: {'valid': True, 'safe_select': True})
    seen = []
    monkeypatch.setattr(stage_charts, 'compile_spec', lambda spec, root: seen.append(spec))
    spec = {'data': SPEC['data'], 'height': '320', 'layer': {'item': {'item': [
        {'mark': {'type': 'point', 'size': '150', 'filled': 'true'}, 'encoding': SPEC['encoding']}
    ]}}, 'usermeta': {'item': ['literal metadata']}}
    token = authorize(monkeypatch)
    try:
        result = server.tool_publish_chart('Typed', {'sales': 'select 1'}, spec, caller_email='owner@example.com')
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)
    assert 'error' not in result, result
    assert seen[0]['height'] == 320
    assert seen[0]['layer'][0]['mark']['size'] == 150
    assert seen[0]['layer'][0]['mark']['filled'] is True
    assert seen[0]['usermeta'] == spec['usermeta']
    with db() as conn:
        stored = conn.execute('SELECT payload FROM rvbbit.calliope_surfaces').fetchone()['payload']['spec']
    assert stored == seen[0]


def test_compile_only_false_positive_now_fails_before_publication(monkeypatch):
    from copy import deepcopy
    monkeypatch.setenv('CHROMIUM_PATH', '/usr/bin/chromium')
    spec = deepcopy(SPEC)
    spec['encoding']['color'] = {'field': 'channel', 'type': 'nominal', 'scale': {
        'range': {'item': ['red', 'blue']}, 'domain': {'item': ['A', 'B']},
    }}
    # This exact shape compiled successfully in the failed trial, then crashed
    # in the browser. Runtime preflight must catch it even with zero data rows.
    with pytest.raises(ValueError, match='Undefined data set name'):
        stage_charts.compile_spec(spec, ROOT/'vendor')
    payload = stage_charts.validate('Repaired', {'sales': 'select 1'}, spec)
    assert payload['spec']['encoding']['color']['scale']['range'] == ['red', 'blue']
    stage_charts.compile_spec(payload['spec'], ROOT/'vendor')


def test_preflight_timeout_reaps_worker_group(monkeypatch):
    import subprocess
    import os
    from unittest.mock import Mock
    worker = Mock(pid=12345)
    worker.communicate.side_effect = [subprocess.TimeoutExpired('preflight', 20), ('', '')]
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: worker)
    killed = []
    monkeypatch.setattr(os, 'killpg', lambda *args: killed.append(args))
    with pytest.raises(ValueError, match='exceeded 20 seconds'):
        stage_charts.compile_spec(SPEC, ROOT/'vendor')
    assert killed and killed[0][0] == worker.pid
    assert worker.communicate.call_count == 2


def test_bad_composition_reports_argument_path():
    with pytest.raises(ValueError, match=r'spec.layer must be a nonempty array'):
        stage_charts.validate('Bad', {'sales': 'select 1'}, {'data': SPEC['data'], 'layer': {'unexpected': []}})
    with pytest.raises(ValueError, match=r'spec.layer\[0\].encoding.mark'):
        stage_charts.validate('Bad', {'sales': 'select 1'}, {'data': SPEC['data'], 'layer': [{'encoding': {'mark': 'bar'}}]})


def test_dataset_queries_use_viewer_identity_and_propagate_errors(monkeypatch):
    import asyncio
    from concurrent.futures import Future
    observed=[]
    def run(sql,**kwargs):
        observed.append((server._SESSION_SUB.get(),sql,kwargs))
        return {'rows':[{'value':3}], 'row_count':1, 'truncated':False} if sql!='bad' else {'error':{'message':'missing relation'}}
    def submit(key,execute,refresh=False):
        f=Future()
        try:f.set_result(execute())
        except Exception as e:f.set_exception(e)
        return f,'refresh' if refresh else 'miss'
    monkeypatch.setattr(server,'tool_run_sql',run)
    monkeypatch.setattr(server,'_record',lambda *a,**kw:None)
    monkeypatch.setattr(server,'_dashboard_query_submit',submit)
    surface={'id':'surface','payload':{'queries':{'sales':'select 3'},'row_limit':70}}
    data=asyncio.run(server.stage_chart_data(surface,'viewer@example.com'))
    assert data['datasets']['sales']==[{'value':3}]
    assert observed[0][0]=='viewer@example.com' and observed[0][2]['limit']==70
    assert server._SESSION_SUB.get()!='viewer@example.com'
    surface['payload']['queries']['sales']='bad'
    with pytest.raises(ValueError,match='sales: missing relation'):
        asyncio.run(server.stage_chart_data(surface,'viewer@example.com',True))


def test_chart_data_route_checks_surface_access_and_uses_saved_sql(db,monkeypatch):
    import ast,asyncio,uuid
    from starlette.responses import Response
    import calliope
    destination=seed(db)
    sid=str(uuid.uuid4())
    with db() as conn:
        conn.execute("CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$ SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions WHERE id=$1 AND owner_email=$2) $$")
        conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,payload) VALUES(%s,%s,%s,'chart',%s::jsonb)",
                     (sid,destination['session_id'],destination['turn_id'],json.dumps({'queries':{'sales':'select 3'},'row_limit':70})))
    monkeypatch.setattr(server,'_conn',db)
    assert server.tool_read_chart(sid,'stranger@example.com')['error']['code']=='NOT_FOUND'
    assert server.tool_read_chart(sid,'owner@example.com')['queries']=={'sales':'select 3'}
    tree=ast.parse((ROOT/'calliope.py').read_text())
    fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_stage_chart_data')
    fn.decorator_list=[]
    seen=[]
    async def load(row,viewer,refresh=False):
        seen.append((row['payload']['queries'],viewer,refresh))
        return {'datasets':{'sales':[]}}
    monkeypatch.setattr(server,'stage_chart_data',load)
    namespace={'api_owner':lambda request:(request.owner,None),'_uuid':calliope._uuid,'Response':Response,
               'conn_factory':db,'asyncio':asyncio,'json_response':lambda value,status=200:Response(json.dumps(value),status_code=status)}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<chart-route>','exec'),namespace)
    class Request:
        owner='stranger@example.com'
        path_params={'surface_id':sid}
        async def json(self):return {'sql':'select malicious_override','refresh':True}
    request=Request()
    response=asyncio.run(namespace['get_stage_chart_data'](request))
    assert response.status_code==404 and seen==[]
    request.owner='owner@example.com'
    response=asyncio.run(namespace['get_stage_chart_data'](request))
    assert response.status_code==200
    assert seen==[({'sales':'select 3'},'owner@example.com',True)]


def test_published_chart_stream_emits_existing_surface_only(db,monkeypatch):
    import calliope
    destination=seed(db)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':True})
    monkeypatch.setattr(stage_charts,'compile_spec',lambda *args:None)
    token=authorize(monkeypatch)
    try:result=server.tool_publish_chart('Sales',{'sales':'select 1'},SPEC,caller_email='owner@example.com')
    finally:server._AUTHORIZATION_CONTEXT.reset(token)
    rows=calliope._insert_progressive_result_surfaces(db,None,'owner@example.com',destination['session_id'],destination['turn_id'],'publish_chart',result,{},'tool-call')
    assert len(rows)==1 and rows[0]['id']==result['surface_id']
    assert rows[0]['payload']['runtime']['scripts']
    with db() as conn:assert conn.execute('select count(*) as n from rvbbit.calliope_surfaces').fetchone()['n']==1
    assert calliope._insert_progressive_result_surfaces(db,None,'owner@example.com','00000000-0000-0000-0000-000000000001',destination['turn_id'],'publish_chart',result,{},'tool-call')==[]


def test_reconciliation_failed_refresh_and_sizes(chart_page):
    page,calls,_=chart_page
    page.evaluate('''spec => {
      window.chartModel={id:'one',kind:'chart',payload:{spec,queries:{sales:'select 1'},content_sha256:'one',size:'wide'}};
      window.drawChart=title=>{
        const root=document.querySelector('.zen-packed');
        reconcileStageMarkup(root,`<article class="surface kind-chart chart-size-wide" data-surface-id="one"><header>${title}</header>${renderVegaChart(chartModel)}</article>`, 'session');
        syncStagePacking();hydrateChartSurfaces();
      };
      drawChart('First title');
    }''',SPEC)
    page.locator('.chart-body.is-ready').wait_for()
    page.evaluate("window.originalSvg=document.querySelector('.chart-canvas svg');drawChart('Changed title')")
    page.wait_for_timeout(150)
    assert page.evaluate("originalSvg===document.querySelector('.chart-canvas svg')")
    assert len(calls)==1
    page.route('**/chart-data',lambda r:r.fulfill(status=400,json={'error':{'message':'sales: relation removed'}}))
    page.get_by_role('button',name='Refresh',exact=True).click()
    page.locator('.chart-body.is-error').wait_for()
    assert page.evaluate("originalSvg===document.querySelector('.chart-canvas svg')")
    assert 'relation removed' in page.locator('.chart-status').inner_text()
    page.unroute('**/chart-data')
    page.get_by_role('button',name='Refresh',exact=True).click()
    page.wait_for_function("() => !document.querySelector('.chart-body').classList.contains('is-loading')")
    assert page.locator('.chart-body.is-error').count()==0
    page.evaluate("s=>{addChart(s,'compact','two');addChart({vconcat:[s,s]},'full','three')}",SPEC)
    page.locator('.chart-body.is-ready').nth(2).wait_for()
    page.wait_for_timeout(150)
    for width,columns in ((1500,3),(1100,2),(650,1)):
        page.evaluate('(w)=>document.querySelector("#stage").style.width=w+"px"',width)
        page.wait_for_timeout(400)
        rects=[page.locator('article').nth(i).bounding_box() for i in range(3)]
        assert abs(rects[2]['width']-width)<2
        assert abs(rects[1]['width']-(width-10*(columns-1))/columns)<2
        for i,a in enumerate(rects):
            for b in rects[i+1:]:
                assert a['x']+a['width']<=b['x']+1 or b['x']+b['width']<=a['x']+1 or a['y']+a['height']<=b['y']+1 or b['y']+b['height']<=a['y']+1
    assert len(calls)==4 # initial, successful refresh, two additional charts


def test_chart_tools_are_discoverable_with_structured_schemas():
    from mcp.server.fastmcp import FastMCP
    mcp=FastMCP('stage-chart-contract')
    server._register(mcp)
    tool=mcp._tool_manager._tools['publish_chart']
    assert tool.parameters['properties']['queries']['additionalProperties']['type']=='string'
    assert tool.parameters['properties']['spec']['type']=='object'
    assert {'title','queries','spec'} <= set(tool.parameters['required'])
    assert 'height' in tool.description and 'replaces_surface_id' in tool.description
    assert {'read_chart','update_chart'} <= set(mcp._tool_manager._tools)


@pytest.fixture
def feedback_db(db):
    with db() as conn:
        conn.execute("""
            CREATE TABLE rvbbit.calliope_agent_messages(
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_email text,kind text,from_kind text,
                from_ref text,from_label text,to_session_id uuid,subject text,body text,payload jsonb,
                not_before timestamptz,created_at timestamptz DEFAULT now(),status text DEFAULT 'queued');
            CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql
                AS $$ SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions WHERE id=$1 AND owner_email=$2) $$;
        """)
    return db


def test_render_receipts_dedupe_supersession_repair_budget_and_viewer_boundary(feedback_db, monkeypatch):
    db=feedback_db
    destination=seed(db)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':True})
    monkeypatch.setattr(stage_charts,'compile_spec',lambda *args:None)
    token=authorize(monkeypatch)
    try:
        chart=server.tool_publish_chart('Sales',{'sales':'select 1'},SPEC,caller_email='owner@example.com')
        sid=chart['surface_id']
        assert stage_charts.begin_render(db,sid,'stranger@example.com') is None
        attempt=stage_charts.begin_render(db,sid,'owner@example.com')
        report={'attempt':attempt,'sequence':1,'status':'error','phase':'render','message':'Unrecognized signal: x'}
        assert stage_charts.record_render(db,sid,'stranger@example.com',report)['reason']=='not_owner'
        receipt=stage_charts.record_render(db,sid,'owner@example.com',report)
        assert receipt['feedback']=='queued'
        assert stage_charts.record_render(db,sid,'owner@example.com',report)['reason']=='stale_report'
        again=stage_charts.record_render(db,sid,'owner@example.com',{**report,'sequence':2})
        assert again['feedback']=='already_reported'
        saved=server.tool_read_chart(sid,'owner@example.com')
        assert saved['render_receipt']['message']=='Unrecognized signal: x'
        with db() as conn:
            notes=conn.execute('select * from rvbbit.calliope_agent_messages').fetchall()
            assert len(notes)==1 and 'update_chart' in notes[0]['body']
            assert notes[0]['not_before']>notes[0]['created_at']
        fresh=stage_charts.begin_render(db,sid,'owner@example.com')
        assert stage_charts.record_render(db,sid,'owner@example.com',{**report,'sequence':3})['reason']=='stale_attempt'
        stage_charts.record_render(db,sid,'owner@example.com',{'attempt':fresh,'sequence':4,'phase':'render','status':'ready'})
        with db() as conn:assert conn.execute('select status from rvbbit.calliope_agent_messages').fetchone()['status']=='cancelled'
        assert server.tool_read_chart(sid,'owner@example.com')['last_render_error']['message']=='Unrecognized signal: x'
        # Updates retain omitted query/style fields and hide behind one lineage.
        revised=server.tool_update_chart(sid,spec={**SPEC,'height':320},caller_email='owner@example.com')
        assert 'error' not in revised,revised
        new_id=revised['surface_id']
        assert new_id!=sid
        import calliope
        streamed=calliope._insert_progressive_result_surfaces(db,None,'owner@example.com',destination['session_id'],destination['turn_id'],'update_chart',revised,{},'update-call')
        assert len(streamed)==1 and streamed[0]['id']==new_id
        assert server.tool_read_chart(new_id,'owner@example.com')['queries']=={'sales':'select 1'}
        assert server.tool_read_chart(new_id,'owner@example.com')['render_receipt'] is None
        assert server.tool_read_chart(sid,'owner@example.com')['latest_surface_id']==new_id
        assert server.tool_update_chart(sid,title='Stale write',caller_email='owner@example.com')['error']['code']=='CHART_SUPERSEDED'
        # Old tabs may retain historical diagnostics but must not queue repair.
        stage_charts.record_render(db,sid,'owner@example.com',{**report,'attempt':fresh,'sequence':5})
        # Across new revisions the hourly budget stays attached to the lineage.
        for n in range(3):
            attempt=stage_charts.begin_render(db,new_id,'owner@example.com')
            saved=stage_charts.record_render(db,new_id,'owner@example.com',{**report,'attempt':attempt})
            assert saved['feedback']==('queued' if n<2 else 'limit_reached')
            if n<2:
                new_id=server.tool_update_chart(new_id,title=f'Repair {n}',caller_email='owner@example.com')['surface_id']
        with db() as conn:assert conn.execute('select count(*) AS n from rvbbit.calliope_agent_messages').fetchone()['n']==3
    finally:server._AUTHORIZATION_CONTEXT.reset(token)


def test_real_vega_error_is_reported_and_recovery_is_saved(chart_page):
    page,calls,_=chart_page
    reports=[]
    page.route('**/chart-render',lambda r:(reports.append(r.request.post_data_json),r.fulfill(json={'accepted':True})))
    bad={**SPEC,'transform':[{'calculate':'missingFunction(datum.revenue)','as':'broken'}]}
    page.evaluate('(s)=>addChart(s)',bad)
    page.locator('.chart-body.is-error').wait_for()
    page.wait_for_timeout(100)
    assert len(reports)==1
    assert reports[0]['status']=='error' and 'missingFunction' in reports[0]['message']
    assert reports[0]['attempt']=='attempt-1'
    assert page.get_by_role('button',name='Copy error').is_visible()
    # Resize cannot spam an identical error, then a real successful rerender clears it.
    page.evaluate("document.querySelector('#stage').style.width='1100px'")
    page.wait_for_timeout(300)
    assert len(reports)==1
    page.evaluate('(s)=>{chartSpecs.get("one").spec=s;}',SPEC)
    page.get_by_role('button',name='Refresh',exact=True).click()
    page.locator('.chart-body.is-ready').wait_for()
    page.wait_for_timeout(100)
    assert reports[-1]['status']=='ready'
    assert page.get_by_role('button',name='Copy error').is_hidden()


def test_stage_shows_current_chart_and_can_reveal_history(chart_page):
    page,_,_=chart_page
    source=(ROOT/'calliope/calliope.js').read_text()
    page.add_script_tag(content="const SETUP_MODE=false;function isArtifactCaptureCompanion(){return false}")
    page.add_script_tag(content=source[source.index('  function visibleStageSurfaces('):source.index('  function activeSketchSurface(')])
    page.evaluate('''() => {state.surfaces=[
      {id:'old',kind:'chart',lineage_key:'sales',created_at:'2026-09-13T01:00:00Z',payload:{live:true}},
      {id:'new',kind:'chart',lineage_key:'sales',created_at:'2026-09-13T02:00:00Z',payload:{live:true}},
      {id:'independent',kind:'chart',lineage_key:'other',payload:{live:true}},
      {id:'legacy',kind:'chart',payload:{}}];}''')
    assert page.evaluate('visibleStageSurfaces().map(s=>s.id)')==['new','independent','legacy']
    page.evaluate("chartHistoryLineages.add('sales')")
    assert page.evaluate('visibleStageSurfaces().map(s=>s.id)')==['old','new','independent','legacy']


def test_vega_logger_errors_after_render_are_not_silently_lost(chart_page):
    page,_,_=chart_page
    reports=[]
    page.route('**/chart-render',lambda r:(reports.append(r.request.post_data_json),r.fulfill(json={'accepted':True,'feedback':'queued'})))
    page.evaluate('(s)=>addChart(s)',SPEC)
    page.locator('.chart-body.is-ready').wait_for()
    page.wait_for_timeout(100)
    assert reports[-1]['status']=='ready'
    page.evaluate("chartViews.get('one').logger().error(new Error('Interaction dataflow failed'))")
    page.locator('.chart-body.is-error').wait_for()
    page.wait_for_timeout(100)
    assert reports[-1]['status']=='error' and reports[-1]['message']=='Interaction dataflow failed'
    assert reports[-1]['sequence']>reports[0]['sequence']
    assert 'Repair queued' in page.locator('[data-chart-freshness]').inner_text()


def test_http_query_failure_is_saved_and_browser_report_requires_owner(feedback_db, monkeypatch):
    import ast,asyncio,uuid,calliope
    from starlette.responses import Response
    db=feedback_db
    destination=seed(db)
    sid=str(uuid.uuid4())
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,lineage_key,payload) VALUES(%s,%s,%s,'chart','query-fail',%s::jsonb)",
                     (sid,destination['session_id'],destination['turn_id'],json.dumps({'queries':{'sales':'select bad'},'live':True})))
    async def load(*args,**kwargs):raise ValueError('sales: missing relation')
    monkeypatch.setattr(server,'stage_chart_data',load)
    tree=ast.parse((ROOT/'calliope.py').read_text())
    namespace={'api_owner':lambda request:(request.owner,None),'_uuid':calliope._uuid,'Response':Response,
               'conn_factory':db,'asyncio':asyncio,'json':json,'os':__import__('os'),
               'json_response':lambda value,status=200:Response(json.dumps(value),status_code=status)}
    for name in ('get_stage_chart_data','report_stage_chart_render'):
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        fn.decorator_list=[]
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<chart-route>','exec'),namespace)
    class Request:
        owner='owner@example.com'
        path_params={'surface_id':sid}
        body={}
        async def json(self):return self.body
    request=Request()
    response=asyncio.run(namespace['get_stage_chart_data'](request))
    assert response.status_code==400
    with db() as conn:
        payload=conn.execute('select payload from rvbbit.calliope_surfaces where id=%s',(sid,)).fetchone()['payload']
        assert payload['render_receipt']['phase']=='query'
        assert payload['render_receipt']['message']=='sales: missing relation'
        assert conn.execute('select count(*) AS n from rvbbit.calliope_agent_messages').fetchone()['n']==1
    request.body={'status':'ready','phase':'render','sequence':1,'attempt':payload['render_attempt']}
    request.owner='stranger@example.com'
    assert asyncio.run(namespace['report_stage_chart_render'](request)).status_code==403
    request.owner='owner@example.com'
    assert asyncio.run(namespace['report_stage_chart_render'](request)).status_code==200
    with db() as conn:
        assert conn.execute('select status from rvbbit.calliope_agent_messages').fetchone()['status']=='cancelled'
    request.body={'status':'ready','phase':'render','sequence':True}
    assert asyncio.run(namespace['report_stage_chart_render'](request)).status_code==400
