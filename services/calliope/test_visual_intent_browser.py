"""Exercise the modal with real markup functions; image inference is mocked."""
import re
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent / 'calliope'


def test_visual_intent_editor_browser(tmp_path):
    html = (ROOT / 'index.html').read_text()
    dialog = re.search(r'<dialog id="markup-dialog"[\s\S]*?</dialog>', html).group()
    viewer = re.search(r'<dialog id="surface-viewer-dialog"[\s\S]*?</dialog>', html).group()
    main = (ROOT / 'calliope.js').read_text()
    # Use the production canvas and Visual Intent integration, with only the
    # rest of the chat application and server/provider transport stubbed out.
    glue = main[main.index('  let visualIntentEditor = null;'):main.index('  function markupPoint(event)')]
    previews = main[main.index('  function setViewerHeader('):main.index('  function renderViewerQuery(')]
    document_helpers = main[main.index('  function documentPreviewKind('):main.index('  function documentPreviewLoading(')]
    render_document = main[main.index('  function renderDocument('):main.index('  function renderSelection(')]
    drawing = main[main.index('  function drawMarkupStroke('):main.index('  let visualIntentEditor = null;')]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror', lambda error:errors.append(str(error)))
        page.route('http://localhost/**', lambda route:route.fulfill(content_type='text/html',body='<html><body></body></html>'))
        page.goto('http://localhost/')
        page.set_content('<style>'+ (ROOT / 'calliope.css').read_text() + '</style><style>:root{--panel:#071e24;--bone:#dfeced;--dim:#8da5aa;--line:#ffffff22;--amber:#49c6dc;--amber-soft:#49c6dc22;--mono:monospace}body{background:#061216}</style>' + dialog + viewer + '<div id="test-stage"></div>')
        page.add_script_tag(content=(ROOT / 'visual-intent.js').read_text())
        page.add_script_tag(content='''
          const $=selector=>document.querySelector(selector), $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
          const els={markupDialog:$('#markup-dialog'),markupTitle:$('#markup-title'),markupToolbar:$('#markup-toolbar'),markupCanvas:$('#markup-canvas'),markupLoading:$('#markup-loading'),markupAttach:$('#markup-attach'),markupUndo:$('#markup-undo'),markupClear:$('#markup-clear')};
          const sample=document.createElement('canvas');sample.width=1000;sample.height=570;
          const ctx=sample.getContext('2d');ctx.fillStyle='#f4f1e8';ctx.fillRect(0,0,1000,570);ctx.fillStyle='#172831';ctx.font='bold 36px sans-serif';ctx.fillText('September performance',50,65);ctx.font='18px sans-serif';ctx.fillText('Revenue and supporting evidence',50,100);
          ctx.fillStyle='#0c6971';[150,210,190,250,320,290].forEach((h,i)=>ctx.fillRect(55+i*90,480-h,60,h));ctx.fillStyle='#172831';ctx.font='bold 48px sans-serif';ctx.fillText('$9.4M',700,200);ctx.font='18px sans-serif';ctx.fillText('Revenue this month',700,230);
          const image=sample.toDataURL();
          const source={id:'capture',kind:'image',title:'Capture · September',artifact_version:4,created_at:'2026-09-13T12:00:00Z',payload:{image_url:'/sample.png'}};
          const artifact={id:'page',kind:'artifact',title:'September performance',artifact_version:4,payload:{url:'/apps/september'}};
          const state={current:{id:'session'},surfaces:[artifact,source],markup:{ready:false,strokes:[],tool:'pen',color:'#ff4d4f',width:6}};
          let drafts=[], calls=[], saves=0, captures=0;
          const currentReadOnly=()=>false,isCurrentArtifactSurface=()=>true,isArtifactCaptureCompanion=s=>s.id.startsWith('capture'),artifactForCapture=s=>s.id.startsWith('capture')?artifact:null;
          const ensureArtifactCapture=async(id,force)=>{if(force){captures++;return {...source,id:'capture-'+captures}}return source};
          const toast=message=>{window.lastToast=message};const renderStage=()=>{};
          async function api(url,options={}){
            calls.push({url,...options});
            if(!options.method)return {drafts:structuredClone(drafts)};
            if(url.endsWith('/save')){const d=drafts.find(d=>url.includes(d.id));d.saved_surface_id='saved-'+d.id;saves++;return {surface:{id:d.saved_surface_id,kind:'image',payload:{image_url:image}}}}
            const b=JSON.parse(options.body);
            const d={id:b.id,source_surface_id:b.source_surface_id||drafts.find(d=>d.id===b.parent_id).source_surface_id,parent_id:b.parent_id,prompt:b.prompt||'',status:b.parent_id?'running':'original',image_url:b.parent_id?null:image};
            drafts.push(d);return {draft:structuredClone(d)};
          }
        '''+document_helpers+previews+render_document+drawing+glue+'''
          Object.assign(els,{viewerDialog:$('#surface-viewer-dialog'),viewerTitle:$('#surface-viewer-title'),viewerMeta:$('#surface-viewer-meta'),viewerExternal:$('#surface-viewer-external'),viewerContent:$('#surface-viewer-content')});
          const escapeHtml=text=>String(text||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const artifactSessionUrl=url=>url;const stopConversationFollow=()=>{};
          $('#viewer-visual-intent').onclick=event=>{const id=event.currentTarget.dataset.surfaceId;closeSurfaceViewer();void visualIntent().open(id)};
          $('#test-stage').onclick=event=>{const b=event.target.closest('[data-visual-intent-surface]');if(b)void visualIntent().open(b.dataset.visualIntentSurface)};
          document.querySelector('#markup-close').onclick=closeMarkup;
          window.testOpen=()=>visualIntent().open('page');
          window.testFinish=(failed=false)=>{drafts.filter(d=>d.status==='running').forEach(d=>{d.status=failed?'failed':'ready';d.image_url=failed?null:image;d.error=failed?'Provider failed; retry your edit.':null})};
          window.testMark=()=>{state.markup.strokes=[{tool:'arrow',color:'#ff4d4f',width:6,points:[{x:450,y:70},{x:550,y:230}]}];paintMarkupCanvas();syncMarkupControls()};
          window.testState=()=>({saves,captures,drafts,calls,strokes:state.markup.strokes.length});
        ''')
        sample_png=base64.b64decode(page.evaluate('image.split(",")[1]'))
        page.route('**/sample.png*', lambda route:route.fulfill(content_type='image/png',body=sample_png))
        page.evaluate('testOpen()')
        page.locator('#markup-canvas.ready').wait_for()
        assert page.locator('#visual-intent-controls').is_visible()
        assert page.locator('.markup-foot').is_hidden()
        assert page.locator('.visual-intent-variant').count()==1
        page.locator('#visual-intent-prompt').fill('Make revenue more prominent and move the chart below it.')
        page.evaluate('testMark()')
        page.screenshot(path=str(tmp_path/'visual-intent-editor.png'))
        page.locator('#visual-intent-generate').click()
        assert page.locator('#visual-intent-generate').text_content()=='Creating image…'
        assert page.evaluate('testState().drafts.length')==2
        page.locator('#markup-close').click()
        page.evaluate('testFinish()')
        page.evaluate('testOpen()')
        page.locator('#markup-canvas.ready').wait_for()
        assert page.locator('#visual-intent-prompt').input_value().startswith('Make revenue')
        assert page.evaluate('testState().strokes')==1
        page.locator('[data-draft]').nth(1).click()
        page.wait_for_function('!document.querySelector("#visual-intent-save").disabled')
        page.locator('#visual-intent-save').click()
        assert page.locator('#visual-intent-save').text_content()=='Saved to Stage'
        assert page.locator('#visual-intent-save').is_disabled()
        assert page.evaluate('testState().saves')==1
        # Branch from original; a failed generation retains its prompt and marks.
        page.locator('[data-draft]').first.click()
        page.locator('#visual-intent-prompt').fill('Try a compact two-column composition.')
        page.wait_for_function('!document.querySelector("#visual-intent-generate").disabled')
        page.locator('#visual-intent-generate').click()
        page.evaluate('testFinish(true)')
        page.wait_for_function('document.querySelector("#visual-intent-status").textContent.includes("Provider failed")')
        assert page.evaluate('testState().strokes')==1
        assert page.locator('#visual-intent-prompt').input_value()=='Try a compact two-column composition.'
        page.locator('#visual-intent-refresh').click()
        page.wait_for_function('testState().captures===1 && document.querySelector("#markup-canvas").classList.contains("ready")')
        assert page.locator('.visual-intent-variant').count()==4  # earlier edits survive refresh
        # Compact viewport still exposes the input and save controls.
        page.set_viewport_size({'width':600,'height':850})
        assert page.locator('#visual-intent-prompt').is_visible()
        page.screenshot(path=str(tmp_path/'visual-intent-editor-narrow.png'))
        # Agent mockups are document/file surfaces with no dashboard metadata.
        page.locator('#markup-close').click()
        page.evaluate("""() => {
          const doc={id:'agent-mockup',kind:'document',title:'mockup.png',payload:{filename:'mockup.png',mime_type:'image/png',download_url:'/sample.png'}};
          state.surfaces.push(doc);
          $('#test-stage').innerHTML=renderDocument(doc);
        }""")
        assert page.locator('#test-stage [data-visual-intent-surface]').count()==1
        page.locator('#test-stage [data-visual-intent-surface]').click()
        page.locator('#markup-canvas.ready').wait_for()
        assert page.locator('#visual-intent-target').input_value()=='agent-mockup'
        assert page.locator('#visual-intent-refresh').is_disabled()
        page.locator('#visual-intent-prompt').fill('Use a more compact header.')
        page.locator('#visual-intent-generate').click()
        assert page.evaluate('testState().drafts.at(-1).source_surface_id')=='agent-mockup'
        page.locator('#markup-close').click()
        page.evaluate('openFileSurface("agent-mockup")')
        assert page.locator('#viewer-visual-intent').is_visible()
        page.locator('#viewer-visual-intent').click()
        page.locator('#markup-canvas.ready').wait_for()
        assert page.locator('#visual-intent-target').input_value()=='agent-mockup'
        assert page.locator('#surface-viewer-dialog').is_hidden()
        assert not errors,errors
        browser.close()
