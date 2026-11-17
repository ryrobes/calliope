"""Regression coverage for publishing placeholders and live capture transport."""
from types import SimpleNamespace
import threading

import pytest
import server


class InspectedSource(Exception):
    pass


@pytest.mark.parametrize('empty_edits', [None, '', '  ', [], '[]', ' [] '])
@pytest.mark.parametrize('source_files', ['', None, {}])
def test_update_placeholder_arguments_preserve_source(monkeypatch, empty_edits, source_files):
    stored_sources = {'app.py': 'keep this file'}
    class Cursor:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, args):
            if 'FROM rvbbit.dashboards' in sql:
                return Cursor({'id':1,'name':'Example','latest_version':1,'runtime_kind':'html','app_kind':'dashboard','manifest':{},'description':''})
            if 'FROM rvbbit.dashboard_versions' in sql:
                return Cursor({'html':'<html>original</html>','manifest':{},'source_files':stored_sources})
            raise AssertionError('Unexpected write: '+sql)
    monkeypatch.setattr(server, '_ensure_dashboard_tables', lambda:None)
    monkeypatch.setattr(server, '_artifact_authorization', lambda:SimpleNamespace(subject='test@example.com'))
    monkeypatch.setattr(server, '_artifact_require_editor', lambda *a,**k:{'id': 1, 'editor_role': 'owner', 'home_is_room': False})
    monkeypatch.setattr(server, '_conn', Conn)
    monkeypatch.setattr(server, '_resolve_source', lambda html,handle:('<html>replacement</html>',None))
    def inspect(runtime,name,slug,html,sources):
        assert html == '<html>replacement</html>'
        assert sources == ({} if source_files == {} else stored_sources)
        raise InspectedSource
    monkeypatch.setattr(server, '_html_from_live_app', inspect)
    with pytest.raises(InspectedSource):
        server.tool_update_live_app('example',source_artifact_id='upload',html='',edits=empty_edits,manifest=' ',source_files=source_files)


@pytest.mark.parametrize('tool', [server.tool_update_live_app,server.tool_update_dashboard])
def test_real_edit_and_replacement_still_conflict(monkeypatch,tool):
    monkeypatch.setattr(server,'_resolve_source',lambda *a:('<html>replacement</html>',None))
    result=tool('example',source_artifact_id='upload',edits=[{'find':'old','replace':'new'}])
    assert result['error']['code']=='INVALID_ARGUMENT'
    assert 'not both' in result['error']['message']


@pytest.mark.parametrize('tool', [server.tool_update_live_app,server.tool_update_dashboard])
def test_malformed_edits_are_not_silently_omitted(tool):
    result=tool('example',edits='not json')
    assert result['error']['code']=='INVALID_ARGUMENT'


@pytest.fixture
def capture_env(monkeypatch):
    monkeypatch.setattr(server,'_capture_extra_headers',lambda:{})
    monkeypatch.setattr(server,'_launch_playwright_chromium',lambda p:p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']))
    monkeypatch.setenv('WAREHOUSE_CAPTURE_QUERY_QUIET_MS','50')


def test_capture_batch_is_concurrent_and_retains_success_after_error(monkeypatch,tmp_path,capture_env):
    rendezvous=threading.Barrier(2,timeout=3)
    calls=[]
    def query(sql,*args,**kwargs):
        calls.append(sql)
        rendezvous.wait()  # A sequential shim cannot finish this batch.
        return {'error':{'message':'SQL rejected'}} if sql=='bad' else {'rows':[{'value':7}],'row_count':1}
    monkeypatch.setattr(server,'tool_run_sql',query)
    html='''<html><head></head><body><script>
    cowork.callMcpTool('mcp__test__run_sql_multi',{queries:{good:'good',bad:'bad'}}).then(r=>{
      if(r.structuredContent.results.good.rows[0].value!==7) throw Error('lost good result');
      if(r.structuredContent.results.bad.error.message!=='SQL rejected') throw Error('lost SQL error');
      document.body.textContent='successful section: 7';
    });
    </script></body></html>'''
    result=server._capture_html_with_playwright(html,tmp_path/'batch.png',800,600,True,0,data_timeout_ms=5000)
    assert sorted(calls)==['bad','good']
    assert result['readiness']['status']=='settled_with_errors'
    assert result['readiness']['failed']==1
    assert not result['page_errors']
    assert [q['status'] for q in result['queries']].count('settled')==1


def test_capture_timeout_is_per_request(monkeypatch,tmp_path,capture_env):
    def query(*a,**k):
        threading.Event().wait(.9)
        return {'rows':[{'value':1}],'row_count':1}
    monkeypatch.setattr(server,'tool_run_sql',query)
    monkeypatch.setenv('WAREHOUSE_CAPTURE_DATA_TIMEOUT_MS','500')
    result=server._capture_html_with_playwright('<html><head></head><body><script>rvbbitQuery("slow")</script></body></html>',tmp_path/'wait.png',800,600,True,0,data_timeout_ms=2500)
    assert result['readiness']['ready']
    assert result['readiness']['timeout_ms']==2500


@pytest.mark.parametrize('kind,expected',[('failed','query_failed'),('pending','data_pending'),('console','render_failed'),('static','no_queries'),('ready','ready')])
def test_capture_verdict_reports_failure_class(monkeypatch,tmp_path,kind,expected):
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug:({'runtime_kind':'html'}, {'version':5,'html':'<html/>','manifest':{}}))
    monkeypatch.setattr(server,'_active_calliope_design_profile',lambda:None)
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda *a:'<html/>')
    query={'error':'timeout'} if kind=='failed' else {'status':'pending'} if kind=='pending' else {'status':'settled'}
    monkeypatch.setenv('WAREHOUSE_LIVE_APP_CAPTURE_DIR', str(tmp_path/'managed'))
    def capture(*a,**k):
        a[1].write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        assert k['data_timeout_ms']==60000
        return {'queries':[] if kind in ('static','console') else [query], 'console_errors':[], 'page_errors':[],
                'runtime_errors':['broken bootstrap'] if kind=='console' else [], 'readiness':{'ready':kind!='pending'},'theme':{'healthy':True}}
    monkeypatch.setattr(server,'_capture_html_with_playwright',capture)
    result=server.tool_capture_live_app('example',path=str(tmp_path/'capture.png'),data_timeout_ms=90000)
    assert result['verification']['status']==expected
    assert result['verification']['healthy']==(kind in ('static','ready'))


def test_hosted_batch_retains_successes_like_capture():
    import json
    import subprocess
    script = r'''
    const vm=require('node:vm'), assert=require('node:assert/strict');
    let source='';process.stdin.on('data',d=>source+=d);process.stdin.on('end',async()=>{
      const window={};vm.runInNewContext(JSON.parse(source),{window});
      const started=[];let release;
      window.rvbbitQuery=async sql=>{
        started.push(sql);
        if(sql==='bad'){await new Promise(r=>release=r);throw Error('SQL failed');}
        return {rows:[{value:7}]};
      };
      const pending=window.cowork.callMcpTool('test_run_sql_multi',{queries:{good:'good',bad:'bad'}});
      assert.deepEqual(started,['good','bad']);release();
      const result=await pending;
      assert.equal(result.structuredContent.results.good.rows[0].value,7);
      assert.equal(result.structuredContent.results.bad.error.message,'SQL failed');
    });
    '''
    subprocess.run(['node','-e',script],input=json.dumps(server._DASH_SHIM_TRANSPORT),text=True,check=True,capture_output=True)


def _charts_runtime():
    start = server._DASH_SHIM_RUNTIME.index("(()=>{const cfg=window.RVBBIT_CHARTS")
    end = server._DASH_SHIM_RUNTIME.index("window.rvbbitCharts=api;})();", start)
    return server._DASH_SHIM_RUNTIME[start:end + len("window.rvbbitCharts=api;})();")]


@pytest.mark.parametrize('fail_load', [False, True])
def test_chart_pending_covers_library_loading_and_rejection(monkeypatch, tmp_path, capture_env, fail_load):
    import json
    monkeypatch.setattr(server, 'tool_run_sql', lambda *a, **k: {'rows': [{'value': 7}]})
    # Load the real helper with a controlled script loader. SQL finishes first;
    # library initialization remains pending beyond the capture quiet period.
    setup = '''
    window.rvbbitTheme={tokens:()=>({palette:[]}),mode:()=> 'dark',onModeChange:()=>{}};
    window.RVBBIT_CHARTS={urls:['delayed-vega.js']};
    const append=document.head.appendChild.bind(document.head);
    document.head.appendChild=node=>{
      if(node.tagName!=='SCRIPT')return append(node);
      setTimeout(()=>{
        if(FAIL_LOAD){node.onerror();return;}
        window.vegaEmbed=async()=>({view:{runAsync:async()=>{document.querySelector('#chart').textContent='Rendered 7';}}});
        node.onload();
      },1400);
      return node;
    };
    '''.replace('FAIL_LOAD', json.dumps(fail_load))
    html = '<html><head></head><body><div id="chart"></div><script>' + setup + _charts_runtime() + '''
    rvbbitQuery('good').then(()=>rvbbitCharts.embed('#chart',{})).catch(e=>console.error(e.message));
    </script></body></html>'''
    result = server._capture_html_with_playwright(html, tmp_path/'chart.png', 800, 600, True, 0, data_timeout_ms=4000)
    assert result['readiness']['ready']
    assert result['readiness']['charts_pending'] == 0
    assert result['readiness']['elapsed_ms'] >= 700
    assert bool(result['runtime_errors']) == fail_load
    assert bool(result['annotation']) == fail_load


def test_partial_capture_is_annotated_without_restarting_queries(monkeypatch, tmp_path, capture_env):
    calls = []
    release = threading.Event()
    def query(sql, *a, **k):
        calls.append(sql)
        release.wait(5)
        return {'rows': []}
    monkeypatch.setattr(server, 'tool_run_sql', query)
    try:
        result = server._capture_html_with_playwright(
            '<html><head></head><body><script>rvbbitQuery("slow")</script></body></html>',
            tmp_path/'partial.png', 800, 600, True, 0, data_timeout_ms=500)
        assert result['readiness']['status'] == 'timeout'
        assert result['annotation'].startswith('INCOMPLETE CAPTURE')
        assert calls == ['slow']
    finally:
        release.set()


def test_agent_wait_default_and_explicit_override(monkeypatch):
    monkeypatch.delenv('WAREHOUSE_CAPTURE_DATA_TIMEOUT_MS', raising=False)
    assert server._agent_capture_data_timeout_ms() == 60000
    monkeypatch.setenv('WAREHOUSE_CAPTURE_DATA_TIMEOUT_MS', '2500')
    assert server._agent_capture_data_timeout_ms() == 2500


@pytest.mark.parametrize('spec_width', [None, 'container', 320])
def test_real_vega_container_chart_has_width(monkeypatch, tmp_path, capture_env, spec_width):
    """Exercise actual pinned Vega: promises resolve even for a zero-width SVG."""
    import json
    import os
    from pathlib import Path
    from urllib.parse import urlsplit
    assets=Path(os.environ.get('CALLIOPE_TEST_VENDOR_DIR', server._VENDOR_DIR))
    names=['vega@6.4.0/vega.min.js','vega-lite@6.4.3/vega-lite.min.js','vega-embed@7.1.0/vega-embed.min.js']
    if not all((assets/name).is_file() for name in names):
        pytest.skip('Pinned Vega assets required; set CALLIOPE_TEST_VENDOR_DIR')
    measured=[]
    def launch(p):
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        new=browser.new_page
        def new_page(**kwargs):
            page=new(**kwargs)
            def asset_route(route):
                name=urlsplit(route.request.url).path.removeprefix('/vendor/')
                assert name in names
                route.fulfill(body=(assets/name).read_bytes(),content_type='text/javascript')
            page.route('**/vendor/**',asset_route)
            return page
        browser.new_page=new_page
        return browser
    monkeypatch.setattr(server,'_launch_playwright_chromium',launch)
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**kw: {'rows':[{'x':'A','y':10},{'x':'B','y':15}],'row_count':2})
    original=server._capture_render_issues
    def measure(page):
        measured.append(page.locator('#chart svg.marks').bounding_box())
        return original(page)
    monkeypatch.setattr(server,'_capture_render_issues',measure)
    spec={'height':200,'mark':'bar','encoding':{'x':{'field':'x','type':'nominal'},'y':{'field':'y','type':'quantitative'}}}
    if spec_width is not None: spec['width']=spec_width
    html='''<html><head><base href="http://capture.test/"><style>
    .layout{display:grid;grid-template-columns:1.28fr 1fr;gap:22px}.chart{height:260px;min-width:0}
    </style></head><body><div class="layout"><div><h2>Monthly chart</h2><div id="chart" class="chart"></div></div><div>Other evidence</div></div><script>
    window.rvbbitTheme={tokens:()=>({palette:[]}),mode:()=> 'dark',onModeChange:()=>{}};
    window.RVBBIT_CHARTS={urls:URLS};
    '''.replace('URLS',json.dumps(['/vendor/'+name for name in names])) + _charts_runtime() + '''
    rvbbitQuery('monthly').then(r=>rvbbitCharts.embed('#chart',{...SPEC,data:{values:r.rows}}));
    </script></body></html>'''.replace('SPEC',json.dumps(spec))
    result=server._capture_html_with_playwright(html,tmp_path/'chart.png',1200,800,False,0,data_timeout_ms=5000)
    assert result['readiness']['ready'] and not result['render_issues']
    assert not result['page_errors'] and not result['runtime_errors']
    assert measured[0]['width'] > (500 if spec_width != 320 else 300)
    assert measured[0]['height'] >= 200


def test_zero_size_chart_is_diagnostic_and_hidden_charts_are_ignored(monkeypatch,tmp_path,capture_env):
    html='''<html><head></head><body>
      <div id="broken" class="vega-embed" style="height:200px"><svg class="marks" width="0" height="180"></svg></div>
      <div hidden><div class="vega-embed" style="height:200px"><svg class="marks" width="0" height="180"></svg></div></div>
    </body></html>'''
    monkeypatch.setenv('WAREHOUSE_CAPTURE_QUERY_START_WAIT_MS','100')
    result=server._capture_html_with_playwright(html,tmp_path/'broken.png',800,600,True,0)
    assert result['readiness']['ready']  # Queries settled does not certify rendering.
    assert [issue['container'] for issue in result['render_issues']] == ['#broken']
    assert result['annotation'].startswith('RENDER ERROR')


@pytest.mark.parametrize('healthy', [False, True])
def test_auto_thumbnail_does_not_replace_good_preview_with_partial_capture(monkeypatch,tmp_path,healthy):
    from pathlib import Path
    path=tmp_path/'thumb.jpg';path.write_bytes(b'previous good image')
    monkeypatch.setattr(server,'_THUMBS_IN_FLIGHT',set())
    import ui_app_captures
    monkeypatch.setattr(ui_app_captures,'thumbnail_path',lambda *a:path)
    monkeypatch.setattr(server,'_artifact_require_view',lambda *a,**kw:None)
    monkeypatch.setattr(server,'_load_live_app_version',lambda *a:({'runtime_kind':'html'},{'version':1}))
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda *a:'<html/>')
    def capture(html,output,**kwargs):
        Path(output).write_bytes(b'new image')
        return {'readiness':{'ready':healthy}}
    monkeypatch.setattr(server,'_capture_html_with_playwright',capture)
    class InlineThread:
        def __init__(self,*,target,**kwargs):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(server.threading,'Thread',InlineThread)
    server._auto_thumb('dashboard','test',actor='viewer@example.test')
    assert path.read_bytes() == (b'new image' if healthy else b'previous good image')
    assert not path.with_suffix('.tmp.jpg').exists()


def test_capture_waits_for_data_while_background_requests_continue(monkeypatch,tmp_path,capture_env):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import time
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(.7)  # Overlapping polling prevents networkidle forever.
            try:
                self.send_response(200);self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(b'ok')
            except BrokenPipeError:pass
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def query(*a,**k):
        time.sleep(.4)
        return {'rows':[{'value':42}],'row_count':1}
    monkeypatch.setattr(server,'tool_run_sql',query)
    try:
        html=f'''<html><head></head><body><script>
          setInterval(()=>fetch('http://127.0.0.1:{http.server_port}/ping').catch(()=>{{}}),50);
          rvbbitQuery('SELECT 42').then(r=>document.body.dataset.result=r.rows[0].value);
        </script></body></html>'''
        start=time.monotonic()
        result=server._capture_html_with_playwright(html,tmp_path/'polling.png',800,600,False,0,data_timeout_ms=4000)
        assert time.monotonic()-start<10
        assert result['readiness']['ready'] and result['queries'][0]['rows']==1
        assert (tmp_path/'polling.png').stat().st_size>0
    finally:http.shutdown();http.server_close()
