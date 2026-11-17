"""Workspace navigation preserves live views and leaves the Stage usable."""
import pytest
from test_zen_chat_layout import zen_page
from test_sketch_drawer import setup as sketch_setup, open_panel, emit
from test_computer_modes import setup as computer_setup


def setup(page):
    computer_setup(page)
    sketch_setup(page)
    page.evaluate('qa.els.evidenceSearch.hidden=true')


def test_switching_preserves_draft_scroll_and_desktop_connection(zen_page):
    page=zen_page;setup(page)
    page.evaluate('''() => {
      qa.state.turns=Array.from({length:20},(_,i)=>({...qa.state.turns[0],id:'turn-'+i,ordinal:i,
        assistant_message:'A long answer to keep in the transcript. '.repeat(20)}));
      qa.renderChat();qa.composerSetValue('Keep this unfinished question');

    }''')
    page.locator('#messages').dispatch_event('wheel',{'deltaY':-1})
    page.evaluate('qa.els.messages.scrollTop=350;window.savedScroll=qa.els.messages.scrollTop')
    page.locator('#computer-open').click()
    frame=page.locator('#computer-shelf iframe');frame.wait_for()
    page.wait_for_function("document.querySelector('#computer-shelf iframe').contentDocument?.body?.textContent.includes('Live desktop')")
    page.evaluate("window.desktopFrame=document.querySelector('#computer-shelf iframe');desktopFrame.contentWindow.probe='alive'")
    open_panel(page)
    page.evaluate("window.sketchFrame=document.querySelector('#sketch-shelf iframe');window.sketchIdentity=sketchFrame.contentWindow.identity")
    emit(page,'dirty')
    page.evaluate("qa.state.turns.at(-1).assistant_message+=' New result';qa.renderChat()")
    assert 'has-unread' in page.locator('#workspace-tab-chat').get_attribute('class')
    assert page.locator('#workspace-sketch').is_visible()
    page.locator('#computer-open').click()
    assert page.evaluate("desktopFrame===document.querySelector('#computer-shelf iframe') && desktopFrame.contentWindow.probe==='alive'")
    page.locator('#workspace-tab-chat').click()
    page.wait_for_function('Math.abs(qa.els.messages.scrollTop-window.savedScroll)<1')
    assert page.evaluate('qa.composerValue()')=='Keep this unfinished question'
    assert 'has-unread' not in page.locator('#workspace-tab-chat').get_attribute('class')
    page.locator('#workspace-tab-sketch').click()
    assert page.evaluate("sketchFrame===document.querySelector('#sketch-shelf iframe') && sketchFrame.contentWindow.identity===sketchIdentity")
    assert page.locator('[data-sketch-status]').inner_text()=='Saving…'


@pytest.mark.parametrize('width',[1000,1500,2400])
def test_resize_reaches_half_window_with_separate_clickable_tabs(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':width,'height':1000});setup(page)
    page.evaluate('qa.setChatWidth(5000,false)')
    assert page.locator('#calliope-chat').bounding_box()['width']==pytest.approx(width/2,abs=1)
    assert page.locator('#chat-resizer').get_attribute('aria-valuemax')==str(width//2)
    tab=page.locator('#workspace-tab-chat').bounding_box()
    handle=page.locator('#chat-resizer').bounding_box()
    assert tab['x']+tab['width']<=handle['x']+1
    # Actually drag the separator: the tab hit area must not intercept it.
    page.mouse.move(handle['x']+handle['width']/2,handle['y']+35)
    page.mouse.down();page.mouse.move(handle['x']+80,handle['y']+35);page.mouse.up()
    assert page.locator('#calliope-chat').bounding_box()['width']<width/2
    page.locator('#workspace-tab-chat').focus();page.keyboard.press('End')
    assert page.locator('#workspace-tab-sketch').get_attribute('aria-selected')=='true'
    assert page.locator('#workspace-tab-sketch').evaluate('(n)=>n===document.activeElement')
    page.keyboard.press('ArrowUp')
    assert page.locator('#computer-open').get_attribute('aria-selected')=='true'
    page.keyboard.press('Home')
    assert page.locator('#workspace-chat').is_visible()
    page.screenshot(path=f'/tmp/workspace-resize-{width}.png')


@pytest.mark.parametrize("zen",[True,False])
def test_todos_and_stage_do_not_move_when_views_change(zen_page,zen):
    page=zen_page;setup(page)
    page.evaluate("zen=>{qa.state.zen=zen;document.body.classList.toggle('zen',zen)}",zen)
    page.evaluate('''() => {
      const turn=qa.state.turns[0];turn.status='running';
      const run={sessionId:'notebook',pending:turn,status:'running'};
      qa.state.activeRuns.set('notebook',run);
      qa.applyStagePlan(run,{confirmed:true,revision:1,todos:[{id:'check',content:'Check invoice totals',status:'pending'}]});
      qa.renderStageWorkingSet();
    }''')
    page.evaluate('''async () => { await Promise.all(document.getAnimations().filter(a=>Number.isFinite(a.effect.getComputedTiming().endTime)).map(a=>a.finished.catch(()=>{}))) }''')
    plan=page.locator('#stage-working-set').bounding_box()
    stage=page.locator('#stage-scroll').bounding_box()
    assert page.locator('.session-tools-rail,#computer-mini-slot,#computer-stage-slot').count()==0
    for view in ['desktop','sketch','chat']:
        page.locator(f'[data-workspace-view="{view}"]').click()
        assert page.locator('#stage-working-set').bounding_box()==pytest.approx(plan,abs=1)
        assert page.locator('#stage-scroll').bounding_box()==pytest.approx(stage,abs=1)
    assert plan['x']+plan['width']<=page.locator('#workspace-tabs').bounding_box()['x']
    page.screenshot(path='/tmp/workspace-with-todos.png')
