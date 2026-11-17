from pathlib import Path
import pytest
import dashboard_mode as mode

ROOT=Path(__file__).parent

@pytest.mark.parametrize('active', ['dashboard', 'html', None])
def test_publication_uses_html_and_preserves_legacy_widgets(active):
    prior={'authoring_mode':'dashboard','dashboard_runtime':mode.VERSION}
    legacy=mode.publication(mode.template(),prior,active,prior)
    assert legacy['authoring_mode']=='html'
    assert legacy['dashboard_runtime']==mode.VERSION
    changed=mode.publication('<html>custom</html>',prior,active,prior)
    assert changed=={'authoring_mode':'html'}
    assert mode.publication('<script>new Chart(canvas,{})</script>',{},active)['authoring_mode']=='html'
    assert 'HOSTED BUILD MODE: HTML' in mode.instruction(active)

def test_language_requires_explicit_switch():
    assert mode.explicit_html('Please switch to raw HTML')
    assert mode.explicit_html('use HTML mode')
    for message in ['make it weirder','do not use raw HTML','someone said "use raw HTML"','keep Dashboard mode']:
        assert not mode.explicit_html(message)

@pytest.fixture
def dashboard_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1000,'height':800})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/*',lambda route:route.fulfill(body='<!doctype html><html><head></head><body><div id="table"></div><div id="chart"></div><div id="kpi"></div><div id="filter"></div></body></html>',content_type='text/html'))
        page.goto('http://dashboard.test')
        for name in ['rvbbit-tanstack-charts-0.16.2.js','rvbbit-tanstack-table-core-9.2.4.js','rvbbit-tanstack-virtual-core-3.17.10.js',mode.ASSET.rsplit('/',1)[1]]:
            page.add_script_tag(path=str(ROOT/'charts'/name))
        yield page
        assert not errors
        browser.close()

def test_widgets_sort_page_refresh_and_theme(dashboard_page):
    p=dashboard_page
    p.evaluate('''() => {
      window.rows=[{label:'A',value:20},{label:'B',value:10},{label:'C',value:30}];
      window.tbl=rvbbitDashboard.table({el:'#table',id:'table',rows,columns:[{key:'label',label:'Category'},{key:'value',label:'Value'}],page:2,metadata:{truncated:true,applied_limit:3}});
      window.chart=rvbbitDashboard.chart({el:'#chart',id:'chart',data:rows,type:'bar',x:'label',y:'value',query:'comparison'});
      rvbbitDashboard.kpi({el:'#kpi',id:'kpi',label:'Count',value:3});
      rvbbitDashboard.filter({el:'#filter',id:'filter',field:'region',value:'a',choices:['a','b'],onChange:v=>window.changed=v});
    }''')
    assert p.locator('#table tbody tr').count()==2
    p.get_by_role('button',name='Value',exact=True).click()
    assert p.locator('#table tbody tr').first.inner_text()=='B\t10'
    p.get_by_role('button',name='Next',exact=True).click()
    assert p.locator('#table tbody tr').count()==1
    assert 'Partial results' in p.locator('#table').inner_text()
    p.locator('#filter select').select_option('b');assert p.evaluate('changed')=='b'
    p.wait_for_selector('#chart [data-rvbbit-key]')
    assert p.locator('#chart svg').count()==1
    before=p.locator('#chart [data-rvbbit-key]').first.get_attribute('fill')
    p.evaluate("document.documentElement.style.setProperty('--artifact-accent','#ef1234');window.dispatchEvent(new Event('rvbbit:adaptive-theme'))")
    p.wait_for_timeout(100)
    assert p.locator('#chart svg').count()==1
    assert p.locator('#chart [data-rvbbit-key]').first.get_attribute('fill')!=before
    assert p.evaluate('rvbbitDashboard.check().healthy')
    p.screenshot(path='/tmp/dashboard-widgets.png')

def test_virtual_table_and_clipping(dashboard_page):
    p=dashboard_page
    p.evaluate('''() => {window.tbl=rvbbitDashboard.table({el:'#table',id:'table',rows:Array.from({length:1000},(_,i)=>({name:'Row '+i,value:i})),columns:[{key:'name',label:'Name'},{key:'value',label:'Value'}],viewportHeight:300});}''')
    p.wait_for_timeout(100)
    assert 2<p.locator('#table tbody tr').count()<50
    assert p.evaluate('rvbbitDashboard.check().healthy')
    p.locator('.rvd-scroll').evaluate('(el)=>el.scrollTop=20000');p.wait_for_timeout(100)
    assert 'Row 500' in p.locator('#table').inner_text()
    p.evaluate("document.querySelector('#table').style.cssText='height:30px;overflow:hidden'")
    assert not p.evaluate('rvbbitDashboard.check().healthy')

@pytest.fixture
def mode_db(tmp_path):
    """Isolated PostgreSQL instance; never touches the appliance or developer databases."""
    import subprocess
    import psycopg
    from psycopg.rows import dict_row
    data=tmp_path/'pg';sock=tmp_path/'socket';sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(tmp_path/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def connect():return psycopg.connect(host=str(sock),dbname='postgres',autocommit=True,row_factory=dict_row)
    try:
        with connect() as c:
            c.execute('''CREATE SCHEMA rvbbit;
              CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,hermes_session_id text);
              CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text);
              CREATE TABLE rvbbit.calliope_specialist_runs(hermes_session_id text,source_turn_id uuid,status text);
              INSERT INTO rvbbit.calliope_sessions VALUES ('00000000-0000-0000-0000-000000000001','parent');
            ''')
            c.execute(mode.DDL);c.execute(mode.DDL)
        yield connect
    finally:subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)

def test_html_normalization_reaches_specialists_and_preserves_history(mode_db,monkeypatch):
    import server
    sid='00000000-0000-0000-0000-000000000001';tid='00000000-0000-0000-0000-000000000002'
    with mode_db() as c:
        assert c.execute('SELECT authoring_mode FROM rvbbit.calliope_sessions').fetchone()['authoring_mode']=='html'
        c.execute("UPDATE rvbbit.calliope_sessions SET authoring_mode='dashboard'")
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",(tid,sid))
        assert c.execute('SELECT authoring_mode FROM rvbbit.calliope_turns').fetchone()['authoring_mode']=='html'
        c.execute("UPDATE rvbbit.calliope_turns SET authoring_mode='dashboard'")  # Historical snapshot
        c.execute(mode.DDL)
        assert c.execute('SELECT authoring_mode FROM rvbbit.calliope_turns').fetchone()['authoring_mode']=='dashboard'
        assert c.execute('SELECT authoring_mode FROM rvbbit.calliope_sessions').fetchone()['authoring_mode']=='html'
        c.execute("INSERT INTO rvbbit.calliope_specialist_runs VALUES('child',%s,'running')",(tid,))
    monkeypatch.setattr(server,'_conn',mode_db)
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda:{'session_id':'parent'})
    assert server._active_authoring_mode()=='html'
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda:{'session_id':'child'})
    assert server._active_authoring_mode()=='html'
    monkeypatch.setattr(server,'_design_profile_package',lambda:None)
    template=server.tool_live_app_template()
    assert mode.ASSET not in template['template_html']
    assert mode.ASSET not in server.tool_tanstack_chart_template()['template_html']
    with mode_db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    assert server._active_authoring_mode()=='html'  # child remains bound after parent settles
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda:{})
    assert server._active_authoring_mode() is None
    assert mode.ASSET not in server.tool_dashboard_template()['template_html']

@pytest.mark.parametrize('kind',['bar','line','area','scatter'])
def test_chart_kinds_selection_and_resize(dashboard_page,kind):
    p=dashboard_page
    p.evaluate('''kind=>{window.h=rvbbitDashboard.chart({el:'#chart',id:'trend',type:kind,data:[{x:1,y:2},{x:2,y:3},{x:3,y:1}],x:'x',y:'y',query:'trend'});}''',kind)
    mark=p.locator('#chart [data-rvbbit-key]').first
    mark.dispatch_event('click')
    assert p.evaluate("Boolean(RVBBIT_CHARTS.selection('trend'))")
    key=p.evaluate("RVBBIT_CHARTS.selection('trend').key")
    p.evaluate("document.documentElement.style.setProperty('--artifact-chart-1','oklch(70% 0.2 30)')")
    p.wait_for_timeout(120)
    assert p.evaluate("RVBBIT_CHARTS.selection('trend')?.key")==key
    p.set_viewport_size({'width':420,'height':800});p.wait_for_timeout(150)
    assert p.locator('#chart svg').bounding_box()['width']<=420
    assert p.evaluate('rvbbitDashboard.check().healthy')

def test_hosted_template_real_capture(monkeypatch,tmp_path):
    import server
    monkeypatch.setattr(server,'_capture_extra_headers',lambda:{})
    monkeypatch.setattr(server,'_launch_playwright_chromium',lambda p:p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']))
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**k:{'columns':['label','value'],'rows':[['A',12],['B',25]],'row_count':2,'truncated':True,'applied_limit':2})
    result=server._capture_html_with_playwright(mode.template(),tmp_path/'dashboard.png',1000,800,True,200)
    assert result['readiness']['ready']
    assert not result['page_errors']
    assert result['dashboard']['widgets']==3
    assert result['dashboard']['healthy']
    assert len(result['queries'])==1

def test_template_drops_late_results(dashboard_page):
    p=dashboard_page
    # Load the real template with vendored assets inlined; resolve requests out of order.
    import warehouse_theme
    html=warehouse_theme.inline_chart_runtime(mode.template())
    html=html.replace('<head>','<head><script>window.calls=[];window.rvbbitQuery=()=>new Promise(resolve=>calls.push(resolve));</script>',1)
    p.set_content(html)
    p.wait_for_function('calls.length===1')
    p.evaluate('void refresh()');p.wait_for_function('calls.length===2')
    p.evaluate("calls[1]({rows:[{label:'New scope',value:12}]})")
    p.wait_for_function("document.querySelector('#details').textContent.includes('New scope')")
    p.evaluate("calls[0]({rows:[{label:'Old scope',value:99}]})")
    p.wait_for_timeout(50)
    assert 'New scope' in p.locator('#details').inner_text()
    assert 'Old scope' not in p.locator('#details').inner_text()


def test_preflight_accepts_template_function_references_without_comment_escape():
    import re
    html=re.sub(r'// Optional filter:.*?\nrefresh\(\);', 'refresh();', mode.template(), flags=re.S)
    assert 'rvbbitDashboard.filter(' not in html
    assert mode.publication(html,{},'dashboard')['dashboard_runtime']==mode.VERSION
    legacy=html.replace(mode.ASSET,'/charts/rvbbit-dashboard-1.0.0.js')
    assert mode.publication(legacy,{},'dashboard')['dashboard_runtime']=='1.0.0'


def test_invalid_widget_options_are_visible_and_recoverable(dashboard_page):
    p=dashboard_page
    assert not p.evaluate('rvbbitDashboard.check().healthy')
    error=p.evaluate('''() => {try{rvbbitDashboard.kpi({el:'#kpi',id:'count',items:[{label:'Leads',value:12}]})}catch(e){return e.message}}''')
    assert 'no items API' in error
    assert 'no items API' in p.locator('#kpi [role=alert]').inner_text()
    assert not p.evaluate('rvbbitDashboard.check().healthy')
    p.evaluate("rvbbitDashboard.kpi({el:'#kpi',id:'count',label:'Leads',value:12})")
    assert p.evaluate('rvbbitDashboard.check().healthy')
    for options in ["y:['a','b'],data:[{week:'Sep',a:12,b:3}]", "y:'value',data:[{week:'Sep',wrong:12}]"]:
        error=p.evaluate("() => {try{rvbbitDashboard.chart({el:'#chart',id:'bad',type:'line',x:'week',"+options+"})}catch(e){return e.message}}")
        assert error
        assert not p.evaluate('rvbbitDashboard.check().healthy')
    p.evaluate("rvbbitDashboard.chart({el:'#chart',id:'bad',type:'line',x:'week',y:'value',data:[]})")
    assert 'No data' in p.locator('#chart').inner_text()
    assert p.evaluate('rvbbitDashboard.check().healthy')


def test_series_bars_fit_grid_and_keep_independent_selection(dashboard_page):
    p=dashboard_page
    p.evaluate('''() => {
      document.body.innerHTML='<main><section class="grid"><article class="card"><h2>Weekly flow</h2><p>Comparison across weeks</p><div id="chart"></div></article><article class="card"><h2>Context</h2><p>Reading notes</p></article></section><section>Next section</section></main>';
      const css=document.createElement('style');css.textContent='*{box-sizing:border-box}body{margin:0;font:16px/1.45 sans-serif}main{padding:24px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:20px}.card{padding:16px;border:1px solid #555;min-width:0} @media(max-width:800px){.grid{grid-template-columns:1fr}}';document.head.append(css);
      window.chart=rvbbitDashboard.chart({el:'#chart',id:'mix',type:'bar',x:'week',y:'count',color:'channel',query:'weekly',data:[{week:'Sep 6',channel:'Paid',count:80},{week:'Sep 6',channel:'Organic',count:40},{week:'Sep 13',channel:'Paid',count:65},{week:'Sep 13',channel:'Organic',count:35}]});
    }''')
    for width in [1200,760,390]:
        p.set_viewport_size({'width':width,'height':800});p.wait_for_timeout(150)
        svg=p.locator('#chart svg').bounding_box();card=p.locator('.card').first.bounding_box()
        assert abs(svg['height']-280)<1
        assert svg['y']+svg['height']<=card['y']+card['height']-15
        assert p.evaluate('rvbbitDashboard.check().healthy')
        bars=p.locator('#chart rect[data-rvbbit-key]')
        assert bars.count()==4
        paid=p.locator('#chart rect[data-rvbbit-key*="Paid"]').first
        organic=p.locator('#chart rect[data-rvbbit-key*="Organic"]').first
        first,second=paid.bounding_box(),organic.bounding_box()
        assert first['x']+first['width']<=second['x'] or second['x']+second['width']<=first['x']
        keys=[]
        for mark,channel in [(paid,'Paid'),(organic,'Organic')]:
            mark.dispatch_event('click')
            selected=p.evaluate("RVBBIT_CHARTS.selection('mix')")
            assert selected['query']=='weekly'
            assert selected['context']['channel']==channel
            assert selected['datum']['week']=='Sep 6'
            keys.append(selected['key'])
        assert keys[0]!=keys[1]
    key=p.evaluate("RVBBIT_CHARTS.selection('mix').key")
    p.evaluate("document.documentElement.style.setProperty('--artifact-chart-1','#ff1234')")
    p.wait_for_timeout(150)
    assert p.evaluate("RVBBIT_CHARTS.selection('mix').key")==key
    p.evaluate("document.querySelector('.card').style.height='100px'")
    assert any('extends outside' in i['message'] for i in p.evaluate('rvbbitDashboard.check().issues'))


@pytest.mark.parametrize('path',['/','/apps/my-dashboard','/d/my-dashboard','https://example.com/app.png','capture.txt'])
def test_capture_rejects_page_routes_before_render(path,monkeypatch):
    import server
    monkeypatch.setattr(server,'_load_live_app_version',lambda *a:pytest.fail('invalid path reached the loader'))
    result=server.tool_capture_live_app('example',path=path)
    assert result['error']['code']=='INVALID_ARGUMENT'
    assert 'Omit path' in result['error']['message']


def test_capture_verdict_binds_current_version_and_failed_widgets(monkeypatch,tmp_path):
    import server
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug:({'runtime_kind':'html'},{'version':6,'html':mode.template(),'manifest':{'authoring_mode':'dashboard'}}))
    monkeypatch.setattr(server,'_active_calliope_design_profile',lambda:None)
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda slug,row:row['html'])
    telemetry={'queries':[],'readiness':{'ready':True},'console_errors':[],'page_errors':[], 'dashboard':{'widgets':0,'healthy':False,'issues':[{'message':'No Dashboard widgets mounted'}]}}
    monkeypatch.setenv('WAREHOUSE_LIVE_APP_CAPTURE_DIR', str(tmp_path/'managed'))
    def capture(html, out, *args, **kwargs):
        out.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        return telemetry
    monkeypatch.setattr(server,'_capture_html_with_playwright',capture)
    result=server.tool_capture_live_app('example',path=str(tmp_path/'capture.png'))
    verdict=result['verification']
    assert verdict['version']==6 and verdict['healthy'] is False
    assert verdict['status']=='render_failed' and verdict['visual_review_required'] is False
    assert 'Diagnostic capture' in verdict['next']
    telemetry['dashboard']={'widgets':7,'healthy':True,'issues':[]}
    result=server.tool_capture_live_app('example',path=str(tmp_path/'capture.png'))
    assert result['verification']['healthy']
