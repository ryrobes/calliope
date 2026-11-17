"""Real canvas, status lifecycle, theme and compact header checks."""
import pytest
from test_zen_chat_layout import zen_page  # noqa: F401


@pytest.mark.parametrize('width', [1500, 390])
def test_header_uses_only_compact_shapes_and_preserves_animation_between_polls(zen_page, width):
    page = zen_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate('''() => {
      document.body.classList.add('mobile-chat-open');qa.state.config.healthy=true;
      qa.state.surfaces=[];window.originalRandom=Math.random;
      window.headerWork={pending:[{specialist_name:'Iris',status:'running',thread_id:'iris'}],
        queued_messages:0,activity_state:{phase:'team_working'}};
    }''')
    canvas = page.locator('.agent-status-particles')
    for random, shape in [(0, 'ray'), (.5, 'bird'), (.999, 'wing')]:
        page.evaluate('''value => {
          const canvas=document.querySelector('.agent-status-particles');
          CalliopeParametricRays.unmount(canvas);delete canvas.dataset.calliopeRay;
          Math.random=()=>value;qa.acceptAgentWork(headerWork);qa.renderChat();
        }''', random)
        assert canvas.get_attribute('data-calliope-ray') == shape
        page.wait_for_timeout(350)
        assert canvas.is_visible()
        assert page.locator('#zen-new-session').count() == 0
        assert page.locator('#calliope-status').get_attribute('title') == 'Team is working'
        assert page.locator('.agent-status-label').text_content() == 'Team is working'
        assert page.locator('.agent-status-label').evaluate('(el)=>el.getBoundingClientRect().width') == 1
        assert canvas.evaluate('''el => {
          const pixels=el.getContext('2d').getImageData(0,0,el.width,el.height).data;
          return pixels.some((value,index)=>index%4===3 && value>0);
        }''')
        assert page.locator('.calliope-head').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.evaluate('''() => {
          window.headerCanvas=document.querySelector('.agent-status-particles');
          window.frameBefore=headerCanvas.toDataURL();
          Math.random=()=>.8;
          for(let i=0;i<4;i++) qa.acceptAgentWork(headerWork);
        }''')
        assert canvas.get_attribute('data-calliope-ray') == shape
        assert page.evaluate('headerCanvas===document.querySelector(".agent-status-particles")')
        page.wait_for_timeout(180)
        assert page.evaluate('headerCanvas.toDataURL()!==frameBefore')
        page.screenshot(path=f'/tmp/chat-header-particles-{shape}-{width}.png')
    page.evaluate('''() => {
      Math.random=originalRandom;
      qa.acceptAgentWork({pending:[],activity_state:{phase:'ready'}});
      window.finalFrame=headerCanvas.toDataURL();
    }''')
    assert canvas.is_visible()
    assert canvas.get_attribute('data-ray-speed') == '0'
    assert page.locator('#calliope-status span').inner_text() == 'Ready'
    assert not page.locator('.calliope-head').evaluate('(el)=>el.classList.contains("has-active-particles")')
    # Ready comes to rest: it keeps gliding for a moment, then the canvas
    # stops redrawing altogether and holds its last frame.
    page.wait_for_timeout(150)
    assert page.evaluate('headerCanvas.toDataURL()!==finalFrame')
    page.wait_for_timeout(5500)
    page.evaluate('''() => {
      window.restFrame=headerCanvas.toDataURL();window.restDraws=0;
      const context=headerCanvas.getContext('2d'),clear=context.clearRect.bind(context);
      context.clearRect=(...args)=>{window.restDraws++;return clear(...args)};
    }''')
    page.wait_for_timeout(700)
    assert page.evaluate('restDraws') == 0
    assert page.evaluate('headerCanvas.toDataURL()===restFrame')
    assert canvas.evaluate('''el => {
      const pixels=el.getContext('2d').getImageData(0,0,el.width,el.height).data;
      return pixels.some((value,index)=>index%4===3 && value>0);
    }''')
    # Activity polls re-project the same status; they must not wake it.
    page.evaluate('for(let i=0;i<3;i++) qa.acceptAgentWork({pending:[],activity_state:{phase:"ready"}})')
    page.wait_for_timeout(300)
    assert page.evaluate('restDraws') == 0
    # Work wakes it, in the same canvas and from the pose it rested in.
    page.evaluate('qa.acceptAgentWork(headerWork)')
    assert canvas.get_attribute('data-ray-speed') == '1'
    page.wait_for_timeout(600)
    assert page.evaluate('restDraws') > 3
    assert page.evaluate('headerCanvas.toDataURL()!==restFrame')
    page.evaluate('qa.acceptAgentWork({pending:[],activity_state:{phase:"ready"}})')
    assert canvas.get_attribute('data-ray-speed') == '0'
    page.evaluate('qa.acceptAgentWork({available:false})')
    assert canvas.is_visible()
    assert page.locator('#calliope-status span').inner_text() == 'Checking activity…'
    page.evaluate('qa.state.headerUnknownSince=Date.now()-16000;qa.syncNotebookActivityStatus()')
    assert canvas.is_hidden()


def test_header_reduced_motion_and_accent_changes(zen_page):
    page = zen_page
    page.emulate_media(reduced_motion='reduce')
    page.evaluate('''() => {
      qa.state.surfaces=[];document.documentElement.style.setProperty('--jade','#fb735c');
      qa.acceptAgentWork({pending:[],delivering_messages:1,activity_state:{phase:'delivering'}});
    }''')
    canvas = page.locator('.agent-status-particles')
    page.wait_for_timeout(200)
    before = canvas.evaluate('(el)=>el.toDataURL()')
    page.wait_for_timeout(180)
    assert canvas.evaluate('(el)=>el.toDataURL()') == before
    assert page.locator('.calliope-head').evaluate('(el)=>getComputedStyle(el,"::before").animationName') == 'none'
    assert canvas.evaluate('(el)=>getComputedStyle(el).color') == 'rgb(251, 115, 92)'
    page.evaluate("document.documentElement.style.setProperty('--jade','#70c7b6')")
    page.wait_for_timeout(100)
    assert canvas.evaluate('(el)=>el.toDataURL()') != before
    assert canvas.get_attribute('aria-hidden') == 'true'
    assert canvas.get_attribute('aria-label') is None


@pytest.mark.parametrize('width', [1500, 390])
def test_header_keeps_transport_action_and_input_states_across_activity_polls(zen_page, width):
    page=zen_page
    page.set_viewport_size({'width':width,'height':900})
    page.evaluate("""() => {
      document.body.classList.add('mobile-chat-open');qa.state.config.healthy=true;
      window.question=qa.state.surfaces[0];qa.state.surfaces=[];
      const pending=qa.state.turns[0];pending.status='running';
      window.run={sessionId:'notebook',status:'running',pending,reconnectingSince:0};
      qa.state.activeRuns.set('notebook',run);
      window.work={pending:[],activity_state:{phase:'working'}};
      qa.acceptAgentWork(work);window.keptCanvas=document.querySelector('.agent-status-particles');
    }""")
    status=page.locator('#calliope-status')
    canvas=page.locator('.agent-status-particles')
    page.wait_for_timeout(800) # allow the notebook entrance animation to settle
    bounds=status.bounding_box()
    shape=canvas.get_attribute('data-calliope-ray')
    page.evaluate('run.reconnectingSince=Date.now();qa.acceptAgentWork(work)')
    assert status.inner_text()=='Reconnecting…'
    assert canvas.is_visible()
    page.evaluate('qa.acceptAgentWork(work)')
    assert status.inner_text()=='Reconnecting…'
    page.evaluate('run.reconnectingSince=Date.now()-16000;qa.syncNotebookActivityStatus()')
    assert status.inner_text()=='Connection lost'
    assert canvas.is_hidden()
    page.evaluate('qa.setTurnConnection(run,false)')
    assert status.get_attribute('title')=='Calliope is working'
    assert canvas.is_visible()
    page.evaluate('run.cancelRequested=true;qa.acceptAgentWork(work)')
    assert status.inner_text()=='Stopping…'
    page.evaluate('run.cancelRequested=false;qa.state.surfaces=[question];qa.renderChatDecisions()')
    assert status.inner_text()=='Your input needed'
    page.evaluate('qa.acceptAgentWork(work)')
    assert status.inner_text()=='Your input needed'
    page.evaluate("question.payload.request_state.status='answered';qa.renderChatDecisions()")
    assert status.get_attribute('class')=='agent-status working'
    page.evaluate("run.status='complete';qa.acceptAgentWork({pending:[],activity_state:{phase:'ready'}})")
    assert status.inner_text()=='Ready'
    assert canvas.get_attribute('data-calliope-ray')==shape
    assert page.evaluate('keptCanvas===document.querySelector(".agent-status-particles")')
    assert all(abs(status.bounding_box()[key]-bounds[key])<.5 for key in bounds)
    assert status.locator('i').count()==0
    assert page.locator('.calliope-head').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    page.locator('.calliope-head').screenshot(path=f'/tmp/chat-header-idle-{width}.png')
    page.evaluate("qa.acceptAgentWork({pending:[],activity_state:{phase:'working'}})")
    page.wait_for_timeout(500)
    page.locator('.calliope-head').screenshot(path=f'/tmp/chat-header-active-{width}.png')
