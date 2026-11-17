"""Loading chrome uses the room palette before runtime and data are available."""
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT=Path(__file__).parent


@pytest.mark.parametrize('embedded',[False,True])
@pytest.mark.parametrize('mode',['dark','light'])
def test_loading_theme_before_runtime_and_data(embedded,mode):
    from playwright.sync_api import sync_playwright
    tokens={'--void':'#261006' if mode=='dark' else '#fff2df','--bone-bright':'#ffe1b0' if mode=='dark' else '#321506',
            '--bone':'#edcfa0','--dim':'#ba8d68','--amber':'#ff692c','--line':'#815634'}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page();page.set_default_timeout(15000)
        held_runtime=[];held_data=[];errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        # Embedded mode must prefer the actual room over an obsolete cached palette.
        cache={'mode':mode,'tokens':tokens if not embedded else {'--amber':'#0000ff','--void':'#000033'}}
        page.add_init_script('localStorage.setItem("rvbbit-warehouse-adaptive-theme-v1",'+json.dumps(json.dumps(cache))+')')
        def route(r):
            path=urlsplit(r.request.url).path
            if path=='/':
                style=';'.join(k+':'+v for k,v in tokens.items())
                r.fulfill(content_type='text/html',body=f'<html data-warehouse-color-mode="{mode}" style="{style}"><iframe style="width:800px;height:600px" src="/calliope/sketch/one"></iframe></html>')
            elif path=='/calliope/sketch/one':
                html=(ROOT/'calliope/sketch.html').read_text().replace('__SKETCH_ID__','one').replace('__SKETCH_READ_ONLY__','false')
                html=re.sub(r'__[A-Z_]+__','',html)
                r.fulfill(content_type='text/html',body=html)
            elif path=='/calliope/sketch-runtime.js':held_runtime.append(r)
            elif path=='/api/calliope/sketches/one':held_data.append(r)
            else:
                f=ROOT/path.lstrip('/')
                if f.is_file() and f.resolve().is_relative_to(ROOT):r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
                else:r.fulfill(status=404)
        page.route('**/*',route)
        page.goto('http://sketch.test/' if embedded else 'http://sketch.test/calliope/sketch/one',wait_until='domcontentloaded')
        for _ in range(200):
            if held_runtime:break
            page.wait_for_timeout(25)
        assert held_runtime
        frame=page.frames[1] if embedded else page.main_frame
        def colors():
            return frame.evaluate('''() => {
              const probe=document.createElement('span');document.body.append(probe);
              const resolve=value=>{probe.style.color=value;return getComputedStyle(probe).color};
              const expected=[resolve(getComputedStyle(document.documentElement).getPropertyValue('--bone-bright')),resolve(getComputedStyle(document.documentElement).getPropertyValue('--amber'))];
              probe.remove();return {expected,actual:[getComputedStyle(document.querySelector('.sketch-loading strong')).color,getComputedStyle(document.querySelector('.sketch-loading i')).borderTopColor],mode:document.documentElement.dataset.theme};
            }''')
        result=colors();assert result['actual']==result['expected'];assert result['mode']==mode
        assert frame.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--amber').trim()")=='#ff692c'
        held_runtime.pop().fulfill(body=(ROOT/'calliope/sketch-runtime.js').read_bytes(),content_type='text/javascript')
        for _ in range(200):
            if held_data:break
            page.wait_for_timeout(25)
        assert held_data
        assert colors()==result
        frame.locator('.sketch-loading').screenshot(path=f'/tmp/sketch-loading-{mode}-{embedded}.png')
        held_data.pop().fulfill(json={'sketch':{'id':'one','title':'Theme check','revision':1,'element_count':0,'last_actor':'calliope','last_operation_count':0,'scene':{'elements':[]}},'read_only':True})
        frame.locator('.sketch-app').wait_for()
        assert colors()==result
        assert not errors
        browser.close()
