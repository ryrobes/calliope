"""Actual React/Excalidraw runtime: viewing, explicit editing, save and permissions."""
import copy
import json
import mimetypes
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parent


@pytest.fixture(scope='module')
def runtime(tmp_path_factory):
    bundle = tmp_path_factory.mktemp('sketch-view')/'runtime.js'
    source = (ROOT/'calliope-sketch/src/main.tsx').read_text().replace(
        'apiRef.current = api;', 'apiRef.current = api; (window as any).sketchTestApi = api;')
    subprocess.run(['node','--input-type=module','-e',f'''
      import {{build}} from 'esbuild';
      import {{readFile}} from 'node:fs/promises';
      import {{patchSuperiorFont}} from './scripts/superior-font.mjs';
      await build({{stdin:{{contents:{json.dumps(source)},resolveDir:process.cwd()+'/src',loader:'tsx'}},
        bundle:true,conditions:['production'],format:'iife',outfile:{json.dumps(str(bundle))},
        define:{{'process.env.NODE_ENV':'"production"'}},loader:{{'.woff2':'dataurl','.css':'empty'}},
        plugins:[{{name:'superior',setup(build){{
          build.onLoad({{filter:/[\\/]@excalidraw[\\/]excalidraw[\\/]dist[\\/].*\\.js$/}},async args=>{{
            const source=await readFile(args.path,'utf8');
            return {{contents:patchSuperiorFont(source)??source,loader:'js'}};
          }});
        }}}}]}});
    '''],cwd=ROOT/'calliope-sketch',check=True)
    return bundle


@pytest.fixture
def canvas(runtime):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1100,'height':750})
        page.set_default_timeout(8000)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        model={'sketch':{'id':'one','title':'Shared diagram','revision':1,'element_count':1,'last_actor':'calliope',
                        'last_operation_count':1,'can_undo_calliope':True,
                        'scene':{'elements':[{'id':'box','type':'rectangle','x':200,'y':200,'width':240,'height':140,
                                              'strokeColor':'#ffffff','backgroundColor':'transparent','fillStyle':'solid',
                                              'strokeWidth':2,'roughness':0,'opacity':100,'angle':0,'seed':1,'version':1,
                                              'versionNonce':1,'isDeleted':False,'boundElements':None,'groupIds':[]}] }},
               'read_only':False,'presentation':False}
        writes=[];control={'fail':False}
        def route(r):
            path=urlsplit(r.request.url).path
            if path=='/':
                r.fulfill(content_type='text/html',body='''<link rel="stylesheet" href="/theme/superior-fonts.css">
                <link rel="stylesheet" href="/calliope/workspace-ui.css">
                <link rel="stylesheet" href="/calliope/sketch-runtime.css">
                <div id="sketch-root" data-sketch-id="one"></div>
                <script>window.EXCALIDRAW_ASSET_PATH='/calliope/sketch-assets/';</script><script src="/runtime.js"></script>''')
            elif path=='/runtime.js':r.fulfill(body=runtime.read_bytes(),content_type='text/javascript')
            elif path=='/api/calliope/sketches/one':
                if r.request.method=='PUT':
                    body=r.request.post_data_json;writes.append(body)
                    if control['fail'] and not body.get('preview_only'):
                        r.fulfill(status=503,json={'error':'Save temporarily unavailable'});return
                    if not body.get('preview_only'):
                        model['sketch']['scene']['elements']=copy.deepcopy(body['elements'])
                        model['sketch']['revision']+=1
                        model['sketch']['last_actor']='human'
                        model['sketch']['element_count']=len(body['elements'])
                    r.fulfill(json={'sketch':model['sketch'],'changed':not body.get('preview_only')})
                else:r.fulfill(json=model)
            else:
                asset=ROOT/path.lstrip('/')
                if asset.is_file() and asset.resolve().is_relative_to(ROOT):
                    r.fulfill(body=asset.read_bytes(),content_type=mimetypes.guess_type(asset)[0] or 'application/octet-stream')
                else:r.fulfill(status=404)
        page.route('**/*',route)
        def open_canvas():
            page.goto('http://sketch.test/?theme=dark')
            page.wait_for_function('window.sketchTestApi && document.querySelector(".sketch-loading.done")')
            page.wait_for_timeout(650)  # initial preview/fit completes before user gestures
        yield page,model,writes,control,open_canvas
        browser.close()
        assert not errors


def draw_rectangle(page):
    tool = page.locator('[data-testid="toolbar-rectangle"]')
    if tool.count():
        tool.locator("..").click()
    else:
        page.mouse.click(650,400)
        page.keyboard.press('r')
    page.mouse.move(650,440);page.mouse.down();page.mouse.move(830,570,steps=6);page.mouse.up()


def test_view_default_explicit_edit_done_and_live_agent_update(canvas):
    page,model,writes,control,open_canvas=canvas;open_canvas()
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled')
    page.get_by_role('button',name='Edit canvas',exact=True).wait_for(state='visible')
    assert not page.get_by_role('button',name='Undo Callie',exact=True).count()
    draw_rectangle(page)
    assert page.evaluate('sketchTestApi.getSceneElements().length')==1
    assert not [w for w in writes if not w.get('preview_only')]
    page.evaluate('window.keptCanvas=document.querySelector("canvas");window.keptApi=sketchTestApi')
    page.get_by_role('button',name='Edit canvas',exact=True).click()
    page.wait_for_function('!sketchTestApi.getAppState().viewModeEnabled')
    draw_rectangle(page)
    assert page.evaluate('sketchTestApi.getSceneElements().length') == 2, page.evaluate('sketchTestApi.getSceneElements().map(e=>({type:e.type,id:e.id,isDeleted:e.isDeleted}))')
    page.get_by_role('button',name='Done editing',exact=True).click()
    page.get_by_role('button',name='Edit canvas',exact=True).wait_for(state='visible')
    assert len([w for w in writes if not w.get('preview_only')])==1
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled && sketchTestApi===keptApi && document.querySelector("canvas")===keptCanvas')
    assert len(model['sketch']['scene']['elements'])==2
    model['sketch']['revision']+=1;model['sketch']['last_actor']='calliope'
    model['sketch']['scene']['elements'][0]['x']=999
    page.wait_for_function('sketchTestApi.getSceneElements().find(e=>e.id==="box").x===999')
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled && sketchTestApi===keptApi')
    assert not page.get_by_role('button',name='Done editing',exact=True).count()
    page.get_by_role('button',name='Edit canvas',exact=True).wait_for(state='visible')
    page.screenshot(path='/tmp/sketch-view-only.png')
    open_canvas()
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled')


def test_failed_done_keeps_editing_and_retry_preserves_changes(canvas):
    page,model,writes,control,open_canvas=canvas;open_canvas()
    control['fail']=True
    page.get_by_role('button',name='Edit canvas',exact=True).click();draw_rectangle(page)
    assert page.evaluate('sketchTestApi.getSceneElements().length') == 2, page.evaluate('sketchTestApi.getSceneElements().map(e=>({type:e.type,id:e.id,isDeleted:e.isDeleted}))')
    page.get_by_role('button',name='Done editing',exact=True).click()
    page.wait_for_function('document.querySelector(".sketch-tools .error")')
    assert not page.evaluate('sketchTestApi.getAppState().viewModeEnabled')
    assert page.evaluate('sketchTestApi.getSceneElements().length')==2
    control['fail']=False
    page.get_by_role('button',name='Done editing',exact=True).click()
    page.get_by_role('button',name='Edit canvas',exact=True).wait_for(state='visible')
    assert len(model['sketch']['scene']['elements'])==2


@pytest.mark.parametrize('restriction',['read_only','presentation'])
def test_permission_and_pinned_presentation_cannot_enable_edits(canvas,restriction):
    page,model,writes,control,open_canvas=canvas;model[restriction]=True;open_canvas()
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled')
    assert not page.get_by_role('button',name='Edit canvas',exact=True).count()
    draw_rectangle(page)
    assert page.evaluate('sketchTestApi.getSceneElements().length')==1
    assert not writes


@pytest.mark.parametrize('width',[420,700,1100])
def test_edit_controls_remain_reachable_in_zen_and_narrow_canvases(canvas,width):
    page,model,writes,control,open_canvas=canvas
    page.set_viewport_size({'width':width,'height':750});open_canvas()
    before=page.evaluate('JSON.stringify(sketchTestApi.getSceneElements())')
    page.get_by_role('button',name='Edit canvas',exact=True).click()
    page.wait_for_function('!sketchTestApi.getAppState().viewModeEnabled')
    page.evaluate('sketchTestApi.updateScene({appState:{zenModeEnabled:true}})')
    done=page.get_by_role('button',name='Done editing',exact=True)
    done.wait_for(state='visible')
    bounds=done.bounding_box()
    assert bounds['x']>=0 and bounds['x']+bounds['width']<=width
    page.screenshot(path=f'/tmp/sketch-explicit-edit-{width}.png')
    done.click()
    page.get_by_role('button',name='Edit canvas',exact=True).wait_for(state='visible')
    assert page.evaluate('sketchTestApi.getAppState().viewModeEnabled')
    assert not [w for w in writes if not w.get('preview_only')], (before, page.evaluate('JSON.stringify(sketchTestApi.getSceneElements())'))


def apply_color_theme(page, mode, background=None):
    page.evaluate('''({mode,background}) => window.postMessage({
      type:'calliope.sketch.theme.apply', mode,
      snapshot:{tokens:{'--void':background || (mode==='dark'?'#111b24':'#f5f8f5'),
        '--panel':mode==='dark'?'#09121b':'#ffffff'}}
    },location.origin)''', {'mode':mode,'background':background})
    page.wait_for_timeout(150)


def ink_contrast(page):
    return page.evaluate('''() => {
      const rgb=color=>color.match(/[a-f0-9]{2}/gi).map(n=>parseInt(n,16)/255);
      const lum=color=>rgb(color).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4)
        .reduce((s,n,i)=>s+n*[.2126,.7152,.0722][i],0);
      const ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
      const elements=sketchTestApi.getSceneElements();
      const box=elements.find(e=>e.id==='box');
      const text=elements.find(e=>e.type==='text');
      return {stroke:ratio(box.strokeColor,box.backgroundColor),
        text:ratio(text.strokeColor,box.backgroundColor),color:box.strokeColor};
    }''')


def test_theme_contrast_is_display_only_and_human_colors_survive(canvas):
    page,model,writes,control,open_canvas=canvas
    box=model['sketch']['scene']['elements'][0]
    box.update(strokeColor='#8aa4b8',backgroundColor='#163b3b',opacity=35,
               label={'text':'Readable in both modes'},customData={'purpose':'keep me'})
    open_canvas()
    page.get_by_role('button',name='Edit canvas',exact=True).click()
    # PNGs use the same projected colors, with no second dark-mode inversion.
    import base64
    from io import BytesIO
    from PIL import Image
    preview=next(w for w in writes if w.get('preview_only'))
    png=Image.open(BytesIO(base64.b64decode(preview['preview_data_url'].split(',')[1])))
    assert png.getpixel((0,0))[:3]==(17,27,36)
    assert page.evaluate('sketchTestApi.getAppState().theme')=='light'
    colors=[]
    for mode in ['light','dark','light','dark']:
        apply_color_theme(page,mode)
        result=ink_contrast(page)
        assert result['stroke']>=4.5 and result['text']>=7, result
        colors.append(result['color'])
        page.screenshot(path=f'/tmp/sketch-readable-{mode}.png')
    assert colors[0]==colors[2] and colors[1]==colors[3] and colors[0]!=colors[1]
    # Same-mode palette handoffs must also update the drawing, without a save.
    apply_color_theme(page,'dark','#ffffff')
    assert ink_contrast(page)['color']!=colors[1]
    apply_color_theme(page,'dark')
    page.wait_for_timeout(1100)
    assert not [w for w in writes if not w.get('preview_only')]
    # Moving an element must retain its authored colors/opacity, not the tint.
    page.evaluate('''() => sketchTestApi.updateScene({elements:sketchTestApi.getSceneElements().map(e=>
      e.id==='box'?{...e,x:e.x+40,version:e.version+1}:e)})''')
    page.wait_for_timeout(1400)
    saved=model['sketch']['scene']['elements'][0]
    assert saved['strokeColor']=='#8aa4b8' and saved['backgroundColor']=='#163b3b'
    assert saved['opacity']==35 and saved['customData']=={'purpose':'keep me'}
    # An explicit human color edit becomes the new canonical color.
    page.evaluate('''() => sketchTestApi.updateScene({elements:sketchTestApi.getSceneElements().map(e=>
      e.id==='box'?{...e,strokeColor:'#fe00fe',version:e.version+1}:e)})''')
    page.wait_for_timeout(1400)
    assert model['sketch']['scene']['elements'][0]['strokeColor']=='#fe00fe'
    assert all('calliopeDisplayColors' not in e.get('customData',{})
               for w in writes for e in w['elements'])


def test_theme_switch_preserves_drawing_undo(canvas):
    page,model,writes,control,open_canvas=canvas;open_canvas()
    page.get_by_role('button',name='Edit canvas',exact=True).click()
    draw_rectangle(page)
    page.wait_for_timeout(1400)
    assert len(page.evaluate('sketchTestApi.getSceneElements()'))==2
    apply_color_theme(page,'light')
    page.keyboard.press('Control+z')
    page.wait_for_timeout(1400)
    assert len(page.evaluate('sketchTestApi.getSceneElements()'))==1
    assert model['sketch']['scene']['elements'][0]['strokeColor']=='#ffffff'


def test_frozen_export_uses_live_unsaved_scene_in_embedded_canvas(canvas):
    page, model, writes, control, _ = canvas
    control['fail'] = True  # Saved preview must not be substituted for live ink.
    page.route('**/snapshot-host', lambda r:r.fulfill(content_type='text/html', body='''
      <script>window.exports=[];addEventListener('message',e=>{
        if(e.data.type==='calliope.sketch.snapshot.result')exports.push(e.data);
      });</script><iframe src="/" style="width:1000px;height:650px"></iframe>'''))
    page.goto('http://sketch.test/snapshot-host')
    page.wait_for_function('document.querySelector("iframe").contentWindow.sketchTestApi')
    frame = page.frames[1]
    frame.get_by_role('button', name='Edit canvas', exact=True).click()
    frame.evaluate('''() => {const api=sketchTestApi;api.updateScene({elements:
      api.getSceneElements().map(e=>({...e,width:500,version:e.version+1}))});}''')
    page.wait_for_timeout(100)
    page.evaluate('''document.querySelector('iframe').contentWindow.postMessage(
      {type:'calliope.sketch.snapshot',request_id:'first'},location.origin)''')
    page.wait_for_function('exports.length===1')
    first = page.evaluate('exports[0]')
    assert first['dirty'] and first['data_url'].startswith('data:image/png;base64,')
    assert first['scene']['elements'][0]['width'] == 500
    assert model['sketch']['scene']['elements'][0]['width'] == 240
    frame.evaluate('''() => {const api=sketchTestApi;api.updateScene({elements:
      api.getSceneElements().map(e=>({...e,width:700,version:e.version+1}))});}''')
    page.evaluate('''document.querySelector('iframe').contentWindow.postMessage(
      {type:'calliope.sketch.snapshot',request_id:'second'},location.origin)''')
    page.wait_for_function('exports.length===2')
    assert page.evaluate('exports[0].data_url') == first['data_url']
    assert page.evaluate('exports[1].data_url') != first['data_url']
    assert page.evaluate('exports[0].scene.elements[0].width') == 500
    assert page.evaluate('exports[1].scene.elements[0].width') == 700


def test_frozen_viewer_pans_zooms_and_exports_current_theme_without_writes(canvas, tmp_path):
    import json
    from PIL import Image
    page,model,writes,control,open_canvas=canvas
    model.update(read_only=True,presentation=True,frozen_snapshot=True)
    open_canvas()
    assert not page.get_by_role('button',name='Edit canvas',exact=True).count()
    initial=page.evaluate('sketchTestApi.getAppState().scrollX')
    page.mouse.move(550,350);page.keyboard.down('Space');page.mouse.down()
    page.mouse.move(650,410,steps=5);page.mouse.up();page.keyboard.up('Space')
    assert page.evaluate('sketchTestApi.getAppState().scrollX') != initial
    zoom=page.evaluate('sketchTestApi.getAppState().zoom.value')
    page.keyboard.down('Control');page.mouse.wheel(0,-200);page.keyboard.up('Control')
    page.wait_for_function('(z)=>sketchTestApi.getAppState().zoom.value!==z',arg=zoom)
    page.get_by_role('button',name='Fit',exact=True).click()
    page.get_by_role('button',name='Full screen',exact=True).click()
    page.wait_for_function('!!document.fullscreenElement')
    page.get_by_role('button',name='Full screen',exact=True).click()
    page.wait_for_function('!document.fullscreenElement')
    # Saved/live scene changes cannot replace the captured scene (no polling).
    model['sketch']['scene']['elements'][0]['width']=900
    page.wait_for_timeout(2800)
    assert page.evaluate('sketchTestApi.getSceneElements()[0].width') == 240
    backgrounds=[]
    for mode in ['dark','light']:
        apply_color_theme(page,mode)
        for label,suffix in [('PNG','png'),('SVG','svg'),('Sketch file','excalidraw')]:
            with page.expect_download() as result:
                page.get_by_role('button',name=label,exact=True).click()
            path=tmp_path/(mode+'.'+suffix);result.value.save_as(path)
            if suffix=='png': backgrounds.append(Image.open(path).getpixel((0,0))[:3])
            elif suffix=='svg': assert '<svg' in path.read_text() and '<path' in path.read_text()
            else:
                native=json.loads(path.read_text())
                assert native['type']=='excalidraw' and native['elements'][0]['width']==240
    assert backgrounds[0] != backgrounds[1]
    assert not writes


@pytest.mark.parametrize('width', [280, 480, 720, 1440])
@pytest.mark.parametrize('zen', [True, False])
def test_snapshot_toolbar_reserves_space_below_native_controls(canvas, width, zen):
    page,model,writes,control,open_canvas=canvas
    model.update(read_only=True,presentation=True,frozen_snapshot=True)
    page.set_viewport_size({'width':width,'height':600})
    open_canvas()
    page.evaluate('(zen)=>sketchTestApi.updateScene({appState:{zenModeEnabled:zen}})',zen)
    toolbar=page.get_by_role('toolbar',name='Frozen Sketch controls')
    box=toolbar.bounding_box()
    canvas_box=page.locator('.sketch-canvas').bounding_box()
    assert canvas_box['y']+canvas_box['height'] <= box['y']+1
    assert box['y']+box['height'] <= 600
    for button in toolbar.get_by_role('button').all():
        rect=button.bounding_box()
        assert 0 <= rect['x'] and rect['x']+rect['width'] <= width
        assert box['y'] <= rect['y'] and rect['y']+rect['height'] <= box['y']+box['height']
        assert button.evaluate('(node)=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}')
    page.screenshot(path=f'/tmp/snapshot-toolbar-{width}-{zen}.png')
    assert not writes
