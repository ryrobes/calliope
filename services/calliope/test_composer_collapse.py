"""The typing lock folds the input without losing a draft or the Stop action."""
import pytest
from test_zen_chat_layout import zen_page  # noqa: F401


@pytest.mark.parametrize('rich', [False, True])
def test_locked_input_animates_reversibly_and_retains_the_draft(zen_page, rich):
    page = zen_page
    page.evaluate('''rich => {
      qa.state.surfaces=[];qa.renderChat();qa.syncCurrentRunControls();
      if(rich)qa.initializeComposerEditor();
    }''', rich)
    editor = page.locator('.cm-content' if rich else '#message-input')
    editor.fill('Keep this draft while the turn plays.')
    page.evaluate('''() => {
      window.savedEditor=document.querySelector('.cm-editor') || qa.els.input;
      qa.state.activeRuns.set('notebook',{sessionId:'notebook',status:'running',pending:qa.state.turns[0]});
      qa.syncCurrentRunControls();
    }''')
    entry = page.locator('.composer-entry')
    # Inspect and scrub the actual CSS transition, avoiding timing-dependent
    # assertions about a screenshot or an arbitrary sleep midpoint.
    sizes = entry.evaluate('''el => {
      const motion=el.getAnimations().find(a=>a.transitionProperty==='grid-template-rows');
      if(!motion)throw new Error('Missing height transition');
      motion.pause();motion.currentTime=0;
      const start=el.getBoundingClientRect().height;
      motion.currentTime=150;const middle=el.getBoundingClientRect().height;
      motion.currentTime=380;const end=el.getBoundingClientRect().height;
      motion.play();return {start,middle,end};
    }''')
    assert sizes['start'] > sizes['middle'] > sizes['end']
    assert sizes['end'] < 1
    assert entry.evaluate('(el)=>el.inert')
    assert page.locator('#stop-message').is_visible()
    assert page.locator('#stop-message').is_enabled()
    assert page.evaluate('qa.els.input.value') == 'Keep this draft while the turn plays.'
    # A still-busy specialist doesn't lock the composer after the parent ends.
    page.evaluate('''() => {
      qa.state.activeRuns.clear();
      qa.acceptAgentWork({pending:[{specialist_name:'Iris',status:'running'}]});
      qa.syncCurrentRunControls();
    }''')
    page.wait_for_timeout(450)
    assert entry.bounding_box()['height'] > 100
    assert not entry.evaluate('(el)=>el.inert')
    assert page.evaluate("savedEditor===(document.querySelector('.cm-editor') || qa.els.input)")
    assert page.evaluate('qa.els.input.value') == 'Keep this draft while the turn plays.'
    # Reverse in mid-transition: no old timer may hide the restored input.
    page.evaluate('''() => {
      qa.state.activeRuns.set('notebook',{status:'running'});qa.syncCurrentRunControls();
    }''')
    page.wait_for_timeout(60)
    page.evaluate('qa.state.activeRuns.clear();qa.syncCurrentRunControls()')
    page.wait_for_timeout(450)
    assert entry.bounding_box()['height'] > 100
    assert editor.is_editable()


def test_composer_collapse_respects_reduced_motion_on_mobile(zen_page):
    page = zen_page
    page.set_viewport_size({'width': 390, 'height': 900})
    page.emulate_media(reduced_motion='reduce')
    page.evaluate('''() => {
      document.body.classList.add('mobile-chat-open');
      qa.state.surfaces=[];qa.renderChat();qa.syncCurrentRunControls();
      qa.state.activeRuns.set('notebook',{status:'running'});qa.syncCurrentRunControls();
    }''')
    assert page.locator('.composer-entry').bounding_box()['height'] < 1
    assert page.locator('.composer-entry').evaluate('(el)=>el.getAnimations().length') == 0
    assert page.locator('#stop-message').is_visible()
    page.locator('.chat-column').screenshot(path='/tmp/composer-collapsed-mobile.png')
    page.evaluate('qa.state.activeRuns.clear();qa.syncCurrentRunControls()')
    assert page.locator('.composer-entry').bounding_box()['height'] > 100
