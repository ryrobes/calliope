"""Exercise real shared runtime/capture with deterministic Mapbox error events."""
import json

import pytest
from playwright.sync_api import sync_playwright

import server

FAKE_MAPBOX = """<script>
window.mapboxgl = {supported:()=>true, Map:class {
  constructor(opts) {this.node=opts.container;this.layers={};this.events={};this.canvas=document.createElement('canvas');
    this.canvas.className='mapboxgl-canvas';this.node.appendChild(this.canvas);}
  on(name,fn) {(this.events[name]??=[]).push(fn);return this;}
  once(name,fn) {return this.on(name,fn);}
  emit(name,error) {for(const fn of this.events[name]||[])fn({error});}
  getCanvas() {return this.canvas;}
  loaded() {return true;}
  getLayer(id) {return this.layers[id];}
  addLayer(layer) {if(layer.invalid){this.emit('error',new Error(
    'layers.sky-cells.paint.circle-radius: Expected an even number of arguments. https://map.test?access_token=PRIVATE access_token=PRIVATE pk.PRIVATE'
  ));}else this.layers[layer.id]=layer;return this;}
  removeLayer(id) {delete this.layers[id];return this;}
  remove() {this.emit('remove');}
}};
</script>"""


def document(monkeypatch):
    monkeypatch.setattr(server,'_maps_runtime_config',lambda:{'enabled':True,'token':'not-used','styles':{}})
    return '<html><head>'+FAKE_MAPBOX+server._dash_runtime_shim('verification',7,{})+'</head><body><div id="map"></div></body></html>'


@pytest.fixture
def page():
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=b.new_page()
        yield page
        b.close()


def test_style_errors_and_missing_layers_are_visible_without_credentials(page,monkeypatch):
    page.set_content(document(monkeypatch))
    page.evaluate("async()=>{window.map=await rvbbitMaps.map(document.querySelector('#map'));map.addLayer({id:'sky-cells',invalid:true});map.emit('idle');}")
    issues=server._capture_render_issues(page)
    assert {x['code'] for x in issues}=={'map_style_error','missing_map_layers'}
    assert 'Expected an even number' in issues[0]['message']
    assert 'PRIVATE' not in json.dumps(issues)
    assert page.evaluate('rvbbitMaps.pending')==0  # Settled never implies healthy.
    assert not page.evaluate('rvbbitMaps.diagnostics().healthy')
    from computer_browser import browser_diagnostics
    diagnostics=browser_diagnostics(page)
    assert diagnostics['needs_attention'] and diagnostics['artifact_version']==7
    assert 'PRIVATE' not in json.dumps(diagnostics)
    # Deliberate recovery creates a clean map; idle alone cannot erase an error.
    page.evaluate("async()=>{map.remove();window.map=await rvbbitMaps.map(document.querySelector('#map'));map.addLayer({id:'sky-cells'});map.emit('idle');}")
    assert not server._capture_render_issues(page)


def test_missing_layer_without_error_and_deliberate_removal(page,monkeypatch):
    page.set_content(document(monkeypatch))
    page.evaluate("async()=>{window.map=await rvbbitMaps.map(document.querySelector('#map'));map.addLayer({id:'points'});delete map.layers.points;}")
    assert server._capture_render_issues(page)[0]['code']=='missing_map_layers'
    page.evaluate("map.removeLayer('points')")
    assert not server._capture_render_issues(page)


def test_resource_error_never_discloses_provider_urls(page,monkeypatch):
    page.set_content(document(monkeypatch))
    page.evaluate("async()=>{window.map=await rvbbitMaps.map(document.querySelector('#map'));map.emit('error',new Error('https://user:PRIVATE@map.test?token=PRIVATE'));}")
    issues=server._capture_render_issues(page)
    assert issues[0]['code']=='map_resource_error'
    assert 'PRIVATE' not in json.dumps(issues)


def test_query_success_with_map_failure_produces_diagnostic_capture(monkeypatch,tmp_path):
    html=document(monkeypatch).replace('</body>',"""<script>
      (async()=>{const map=await rvbbitMaps.map(document.querySelector('#map'));
        map.addLayer({id:'sky-cells',invalid:true});await rvbbitQuery('select 1');map.emit('idle');})();
    </script></body>""")
    monkeypatch.setattr(server,'_capture_extra_headers',lambda:{})
    monkeypatch.setattr(server,'_launch_playwright_chromium',lambda p:p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']))
    monkeypatch.setenv('WAREHOUSE_CAPTURE_QUERY_QUIET_MS','50')
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**k:{'rows':[{'n':1}],'row_count':1})
    telemetry=server._capture_html_with_playwright(html,tmp_path/'failure.png',800,600,True,0,data_timeout_ms=3000)
    assert telemetry['readiness']['ready'] and telemetry['readiness']['failed']==0
    assert telemetry['render_issues'][0]['code']=='map_style_error'
    assert telemetry['annotation'].startswith('RENDER ERROR')
    # Test the public receipt as well as low-level telemetry.
    monkeypatch.setattr(server,'_load_live_app_version',lambda *a,**k:({'runtime_kind':'html'},{'version':7,'manifest':{}}))
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda *a,**k:html)
    monkeypatch.setattr(server,'_capture_html_with_playwright',lambda *a,**k:telemetry)
    monkeypatch.setattr(server,'_live_app_capture_root',lambda:tmp_path)
    monkeypatch.setattr(server,'_active_calliope_design_profile',lambda:None)
    receipt=server.tool_capture_live_app('verification')
    assert receipt['verification']['status']=='render_failed'
    assert not receipt['verification']['healthy']
    assert not receipt['verification']['visual_review_required']
    assert receipt['bridge']['render_issues'][0]['code']=='map_style_error'


def test_publication_receipt_provides_version_aware_verification():
    receipt=server._artifact_browser_check('example',7)
    assert receipt['expected_version']==7
    assert 'expected_version=7' in receipt['code']
    compile(receipt['code'],'browser-check','exec')


def test_real_browser_reload_checks_version_and_preserves_matching_state(page):
    from computer_browser import browser_page, BrowserNeedsAttention
    version=[7];requests=[]
    def serve(route):
        requests.append(version[0])
        route.fulfill(content_type='text/html',body=f'<script>window.RVBBIT_DASHBOARD={{slug:"example",version:{version[0]}}};window.filter="all"</script>')
    page.route('https://artifact.test/**',serve)
    url='https://artifact.test/apps/example'
    page.goto(url)
    page.evaluate('window.filter="July"')
    browser_page(page.context.browser,url,expected_version=7)
    assert page.evaluate('filter')=='July'
    version[0]=8
    browser_page(page.context.browser,url,expected_version=8)
    assert page.evaluate('RVBBIT_DASHBOARD.version')==8
    page.evaluate('window.filter="August"')
    browser_page(page.context.browser,url,expected_version=8)
    assert page.evaluate('filter')=='August'
    assert requests==[7,8]
    with pytest.raises(BrowserNeedsAttention,match='expected version 9'):
        browser_page(page.context.browser,url,expected_version=9)
    assert requests==[7,8,8]  # One recovery navigation, no reload loop.
