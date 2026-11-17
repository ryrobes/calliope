"""Real Mapbox style fetch + worker under the chat route's actual CSP."""
import ast
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parent


def chat_policy():
    tree = ast.parse((ROOT/'calliope.py').read_text())
    route = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                 and n.name == 'render_calliope_page')
    for node in ast.walk(route):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == 'content-security-policy':
                    return ast.literal_eval(value)
    raise AssertionError('Chat route CSP missing')


@pytest.mark.parametrize('style_error', [False, True])
def test_stage_map_external_style_and_worker_with_chat_policy(style_error):
    from playwright.sync_api import sync_playwright
    token = 'pk.regression-token-must-not-appear-in-receipts'
    spec = {'sources':{'places':{'dataset':'places','longitude':'lon','latitude':'lat'}},
            'queries':{'places':'select lon,lat'},'layers':[{'id':'places','source':'places','type':'circle'}],
            'viewport':{'center':[0,0],'zoom':2},'height':420}
    data = {'sources':{'places':{'type':'FeatureCollection','features':[
                {'type':'Feature','geometry':{'type':'Point','coordinates':[0,0]},'properties':{}}]}},
            'source_stats':{'places':{'features':1,'invalid_geometry_rows':0}},'results':{},
            'render_attempt':'attempt', 'runtime':{'enabled':True,'token':token,
            'styles':{'dark':'mapbox://styles/mapbox/dark-v11','light':'mapbox://styles/mapbox/light-v11'},
            'gl_js_url':'/vendor/mapbox-gl@3.15.0/mapbox-gl.js',
            'gl_css_url':'/vendor/mapbox-gl@3.15.0/mapbox-gl.css'}}
    boot = '''window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective));
      const root=document.querySelector('main');const maps=CalliopeStageMaps.create({root,scroll:null,
        escapeHtml:s=>String(s),toast:()=>{}});
      root.innerHTML=maps.render(SURFACE);maps.hydrate();'''.replace('SURFACE', json.dumps(
        {'id':'map','title':'Locations','payload':spec}))
    requests=[];reports=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1000,'height':700})
        page.on('pageerror', lambda e:errors.append(str(e)))
        def route(r):
            url=urlsplit(r.request.url)
            if url.netloc.endswith('mapbox.com'):
                requests.append(url.path)
                if '/styles/v1/' in url.path:
                    if style_error:
                        r.fulfill(status=403,headers={'Access-Control-Allow-Origin':'*'},json={'message':'Forbidden'})
                    else:
                        r.fulfill(headers={'Access-Control-Allow-Origin':'*'},json={'version':8,'sources':{},
                            'layers':[{'id':'bg','type':'background','paint':{'background-color':'#152229'}}]})
                else:r.fulfill(status=204,headers={'Access-Control-Allow-Origin':'*'})
            elif url.path=='/':
                r.fulfill(content_type='text/html',headers={'Content-Security-Policy':chat_policy()},body='''
                  <html data-theme="dark"><main></main><script src="/calliope/stage-maps.js"></script>
                  <script src="/boot.js"></script></html>''')
            elif url.path=='/boot.js':r.fulfill(content_type='text/javascript',body=boot)
            elif url.path.endswith('/map-data'):r.fulfill(json=data)
            elif url.path.endswith('/map-render'):
                reports.append(r.request.post_data_json);r.fulfill(json={'accepted':True})
            else:
                f=ROOT/url.path.lstrip('/')
                if f.is_file() and f.resolve().is_relative_to(ROOT):
                    r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
                else:r.fulfill(status=404)
        page.route('**/*',route)
        page.goto('http://map.test/')
        if style_error:
            page.locator('.stage-map.is-error').wait_for()
            page.wait_for_timeout(150)
            assert reports and reports[-1]['status']=='error'
            assert token not in reports[-1]['message']
            assert token not in page.locator('.stage-map-status').inner_text()
        else:
            page.locator('.stage-map.is-ready').wait_for(timeout=10000)
            assert reports and reports[-1]['status']=='ready'
            assert page.locator('.mapboxgl-canvas').count()==1
            assert not errors
        assert '/styles/v1/mapbox/dark-v11' in requests
        assert page.evaluate('violations')==[]
        browser.close()
