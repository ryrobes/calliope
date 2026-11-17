"""A successful data refresh cannot hide a broken map renderer."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import server


@pytest.mark.parametrize('failure', ['unsupported', 'constructor', 'resource', 'context'])
def test_map_failure_survives_status_refresh_without_token_leak(monkeypatch, failure):
    pw = pytest.importorskip('playwright.sync_api')
    monkeypatch.setattr(server, 'MAPBOX_TOKEN', 'pk.private-test-token')
    shim = server._dash_runtime_shim('test', None, {})
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        try:
            page = b.new_page()
            page.set_content('<div id="map" style="width:500px;height:300px"></div><div id="status"></div>')
            page.evaluate('''failure => {
              window.mapboxgl={supported:()=>failure!=='unsupported',Map:class {
                constructor(){if(failure==='constructor')throw new Error('Failed to initialize WebGL');
                  this.handlers={};this.canvas=document.createElement('canvas');document.querySelector('#map').append(this.canvas);
                  window.testMap=this;}
                on(event,fn){this.handlers[event]=fn;} once(){} getCanvas(){return this.canvas;}
              }};
            }''', failure)
            page.add_script_tag(content=shim.split('<script>')[1].split('</script>')[0])
            page.evaluate('''async failure => {
              try {await rvbbitMaps.map('map');} catch(e) {}
              if(failure==='resource')testMap.handlers.error({error:new Error('401 https://api.mapbox.com?access_token=pk.private-test-token')});
              if(failure==='context')testMap.canvas.dispatchEvent(new Event('webglcontextlost'));
              document.querySelector('#status').textContent='69,000 reports · click a cell';
            }''', failure)
            alert = page.locator('[data-rvbbit-map-error]')
            assert alert.is_visible()
            assert 'Map unavailable' in alert.inner_text()
            assert 'pk.private-test-token' not in alert.inner_text()
            assert page.evaluate('rvbbitMaps.pending') >= 0
        finally:
            b.close()
