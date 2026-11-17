"""Floating sketch chrome, canvas continuity, and save/notification behavior."""
import pytest
from test_zen_chat_layout import zen_page


def setup(page):
    page.route('**/calliope/sketches/**',lambda r:r.fulfill(content_type='text/html',body='''
      <style>body{margin:0;background:#0b2227;color:#b9e0de;font:18px system-ui}svg{width:100%;height:100vh}</style>
      <script>window.identity=Math.random();window.localEdit='keep';window.flushes=0;
      addEventListener('message',e=>{if(e.data.type==='calliope.sketch.flush')flushes++});</script>
      <svg viewBox="0 0 800 400"><g fill="none" stroke="#64bac3" stroke-width="2"><rect x="80" y="125" width="170" height="90" rx="8"/><path d="M250 170h110m-12-8 12 8-12 8"/><rect x="365" y="125" width="190" height="90" rx="8"/></g><g fill="#b9e0de" font-size="18"><text x="115" y="178">Observation</text><text x="410" y="178">Reasoning</text></g></svg>'''))
    page.evaluate('''async () => {
      if (!window.workspaceEventsReady) {qa.setupEvents();window.workspaceEventsReady=true;}
      qa.state.surfaces=[];qa.renderChat();qa.renderStage();
      // Measure after entry transitions and fonts settle, not midway through the page reveal.
      await document.fonts.ready;
      await Promise.all(document.getAnimations().filter(a=>Number.isFinite(a.effect.getComputedTiming().endTime))
        .map(a=>a.finished.catch(()=>{})));
      window.stageBefore=qa.els.stageScroll.getBoundingClientRect().toJSON();
      qa.state.surfaces=[{id:'sketch-surface',kind:'sketch',turn_id:'one',title:'Working model',
        payload:{sketch_id:'drawing',revision:2,element_count:4,last_actor:'calliope',default_collapsed:true}},
        {id:'image',kind:'image',turn_id:'one',title:'Stage content',payload:{}}];
      qa.renderStage();
    }''')


def emit(page, kind, **data):
    page.evaluate('''data => window.dispatchEvent(new MessageEvent('message',{
      origin:location.origin,source:document.querySelector('iframe[data-sketch-id]').contentWindow,
      data:{type:'calliope.sketch.'+data.kind,...data}}))''',{'kind':kind,**data})


def open_panel(page):
    page.locator('#workspace-tab-sketch').click()
    page.wait_for_function("document.querySelector('iframe[data-sketch-id]')?.contentWindow.identity")
    emit(page,'ready')
    page.wait_for_timeout(250)


@pytest.mark.parametrize('mode',['dark','light'])
def test_tabs_use_no_stage_row_and_canvas_fills_panel(zen_page,mode):
    page=zen_page
    page.evaluate("""mode=>{document.documentElement.dataset.theme=mode;
      if(mode==='light'){document.documentElement.style.setProperty('--panel','#eee8dc');document.documentElement.style.setProperty('--bone-bright','#202a26');document.documentElement.style.setProperty('--jade','#276c66');}}
    """,mode)
    setup(page)
    assert page.locator('#workspace-tab-sketch').is_visible()
    assert page.locator('#workspace-sketch').is_hidden()
    before=page.locator('#stage-scroll').bounding_box()
    assert abs(before['y']-page.evaluate('stageBefore.y'))<1
    open_panel(page)
    assert not page.locator('#sketch-workspace-dialog').evaluate('(n)=>n.open')
    assert page.locator('#stage-scroll').bounding_box()==pytest.approx(before,abs=1)
    assert page.locator('[data-sketch-card] iframe').bounding_box()['height']>600
    assert page.locator('#workspace-chat').is_hidden()
    page.screenshot(path=f'/tmp/workspace-sketch-{mode}.png')
    page.locator('#workspace-tab-chat').click()
    assert page.locator('#workspace-chat').is_visible()
    assert page.locator('#sketch-shelf iframe').count()==1
    assert page.locator('#stage-scroll').bounding_box()==pytest.approx(before,abs=1)
    page.screenshot(path=f'/tmp/workspace-chat-{mode}.png')


def test_large_round_trip_retains_live_document_and_unsaved_drawing(zen_page):
    page=zen_page;setup(page);open_panel(page)
    page.evaluate("window.canvas=document.querySelector('iframe[data-sketch-id]');window.identity=canvas.contentWindow.identity")
    emit(page,'dirty')
    page.get_by_role('button',name='Large',exact=True).click()
    assert page.locator('#sketch-workspace-dialog').evaluate('(n)=>n.open')
    assert page.evaluate("canvas===document.querySelector('iframe[data-sketch-id]') && canvas.contentWindow.identity===identity && canvas.contentWindow.localEdit==='keep'")
    page.keyboard.press('Escape')
    assert not page.locator('#sketch-workspace-dialog').evaluate('(n)=>n.open')
    assert page.evaluate("canvas===document.querySelector('#sketch-shelf iframe') && canvas.contentWindow.identity===identity")
    assert page.locator('[data-sketch-status]').inner_text()=='Saving…'


def test_tab_switch_preserves_dirty_canvas_and_save_errors(zen_page):
    page=zen_page;setup(page);open_panel(page)
    page.evaluate("window.canvas=document.querySelector('iframe[data-sketch-id]');window.identity=canvas.contentWindow.identity")
    emit(page,'dirty')
    page.locator('#workspace-tab-chat').click()
    emit(page,'error',message='Temporary failure')
    page.evaluate('qa.renderStage()')
    page.locator('#workspace-tab-sketch').click()
    assert page.evaluate("canvas===document.querySelector('iframe[data-sketch-id]') && canvas.contentWindow.identity===identity && canvas.contentWindow.localEdit==='keep'")
    assert page.locator('[data-sketch-status]').inner_text()=='Save needs attention'
    emit(page,'saved',revision=3,element_count=5,last_actor='human',changed=True)
    assert page.locator('[data-sketch-status]').inner_text()=='Saved'


def test_sketch_updates_badge_without_stealing_focus_and_disappears_safely(zen_page):
    page=zen_page;setup(page)
    page.evaluate('''() => {
      const incoming=structuredClone(qa.state.surfaces);incoming[0].payload.revision=3;
      qa.revealUpdatedCalliopeSketch(incoming);qa.state.surfaces=incoming;qa.renderStage();
    }''')
    assert page.locator('#workspace-tab-sketch').get_attribute('title')=='Sketch — updated'
    assert page.locator('#workspace-chat').is_visible()
    open_panel(page)
    assert page.locator('#workspace-tab-sketch').get_attribute('title')=='Sketch'
    page.evaluate('qa.state.surfaces=[];qa.renderStage()')
    assert page.locator('#workspace-tab-sketch').is_hidden()
    assert page.locator('#workspace-chat').is_visible()


@pytest.mark.parametrize('width',[390,760])
def test_narrow_workspace_and_reduced_motion(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':width,'height':700});page.emulate_media(reduced_motion='reduce')
    page.evaluate("document.body.classList.add('mobile-chat-open')")
    setup(page);open_panel(page)
    rect=page.locator('#sketch-shelf [data-sketch-card]').bounding_box()
    assert rect['x']>=0 and rect['x']+rect['width']<=width
    assert page.locator('#sketch-shelf [data-sketch-card]').evaluate('(n)=>getComputedStyle(n).animationName')=='none'
    assert page.locator('#workspace-tabs').get_attribute('aria-orientation')=='horizontal'
