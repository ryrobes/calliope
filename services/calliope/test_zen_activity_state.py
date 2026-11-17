"""Session-wide activity through parent completion, delivery and reconnection."""
import pytest
from test_zen_chat_layout import zen_page  # noqa: F401


def test_rail_and_clock_wait_for_the_whole_team_without_locking_composer(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.config.healthy=true;qa.state.surfaces=[];qa.state.surfaces=[];
      const turn=qa.state.turns[0];
      qa.acceptAgentWork({pending:[{run_id:'iris',thread_id:'thread',specialist_name:'Iris',status:'running'}],
        queued_messages:0,activity_state:{phase:'team_working'}});
      qa.retainTeamActivity({sessionId:'notebook',pending:turn,status:'complete',startedAt:Date.now()-60000,
        stageTools:[]},turn,{});
      qa.syncCurrentRunControls();qa.renderAgentWork();qa.renderChat();
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Team is working'
    assert page.locator('[data-turn-phase]').inner_text() == 'Iris is working'
    assert page.evaluate('qa.els.send.disabled') is False
    assert page.evaluate('qa.state.busy') is False
    for phase, extra, label in [
        ('delivering', {'pending_publications': [{'run_id': 'iris'}]}, 'Delivering team replies'),
        ('waiting', {'queued_messages': 1, 'ready_messages': [{'name': 'Iris', 'status': 'queued'}]}, 'Waiting for team replies'),
        ('delivering', {'delivering_messages': 1}, 'Delivering team replies'),
        ('responding', {'parent_turns': [{'turn_id': 'response', 'turn_kind': 'agent_message'}]}, 'Responding to the team'),
    ]:
        page.evaluate('''([phase,extra]) => {
          qa.acceptAgentWork({pending:[],queued_messages:0,...extra,activity_state:{phase}});
          qa.maybeFinishTeamActivity();qa.renderAgentWork();qa.renderChat();
        }''', [phase, extra])
        assert page.locator('#calliope-status span').text_content() == label
        assert page.locator('.turn-clock').count() == 1
        assert page.evaluate('Boolean(qa.state.teamActivityFinish)')
        if phase in ('waiting', 'delivering'):
            assert page.locator('#agent-work').is_visible()
    page.evaluate('''() => {
      qa.acceptAgentWork({pending:[],queued_messages:0,activity_state:{phase:'ready'}});
      qa.maybeFinishTeamActivity();qa.renderAgentWork();
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Ready'
    assert page.locator('.turn-clock').count() == 0
    assert page.evaluate('qa.state.teamActivityFinish') is None


def test_stale_or_failed_receipts_never_finish_the_clock(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.config.healthy=true;qa.state.surfaces=[];
      const turn=qa.state.turns[0];
      window.work={session_id:'notebook',observed_at:'2026-09-11T10:00:00Z',available:true,
        pending:[{run_id:'iris',thread_id:'thread',specialist_name:'Iris',status:'running'}],
        activity_state:{phase:'team_working'}};
      qa.acceptAgentWork(work);
      qa.retainTeamActivity({sessionId:'notebook',pending:turn,status:'complete',startedAt:Date.now()-60000,
        stageTools:[]},turn,{});
      qa.acceptAgentWork({session_id:'notebook',available:false});
      qa.maybeFinishTeamActivity();qa.renderChat();
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    assert 'updates delayed' in page.locator('[data-turn-phase]').inner_text()
    assert page.evaluate('qa.state.agentWork.pending.length') == 1
    # An older success and a different session's receipt cannot erase this state.
    page.evaluate('''() => {
      qa.acceptAgentWork({session_id:'notebook',observed_at:'2026-09-11T09:59:59Z',pending:[],activity_state:{phase:'ready'}});
      qa.acceptAgentWork({session_id:'elsewhere',pending:[],activity_state:{phase:'ready'}});
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    page.evaluate('''() => {
      qa.acceptAgentWork({...work,observed_at:'2026-09-11T10:00:01Z',
        pending:work.pending.map(r=>({...r,stale:true})),activity_state:{phase:'unknown',stale_run_ids:['iris']}});
      qa.renderChat();
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    assert 'Activity update delayed' in page.locator('.zen-team-conversation').inner_text()
    page.evaluate('''() => {
      qa.acceptAgentWork({...work,observed_at:'2026-09-11T10:00:02Z'});
      qa.state.agentWorkReceivedAt=Date.now()-31000;
      qa.syncNotebookActivityStatus();qa.maybeFinishTeamActivity();
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    assert page.evaluate('Boolean(qa.state.teamActivityFinish)')


def test_idle_polling_discovers_new_work_and_recovers_after_failure(zen_page):
    page = zen_page
    response = {'body': {'session_id': 'notebook', 'pending': [], 'queued_messages': 0,
                         'activity_state': {'phase': 'ready'}}, 'status': 200}
    requests = []

    def route(r):
        requests.append(r.request.url)
        r.fulfill(json=response['body'], status=response['status'])

    page.route('**/api/calliope/sessions/notebook/agent-work', route)
    page.evaluate('''async () => {
      qa.state.config.healthy=true;qa.state.surfaces=[];
      await qa.pollAgentWork();await qa.pollAgentWork();
    }''')
    assert len(requests) == 1  # idle uses the slower cadence, but still polls
    response['body'] = {'pending': [{'run_id': 'iris', 'specialist_name': 'Iris'}],
                        'activity_state': {'phase': 'team_working'}}
    page.evaluate('''async () => {
      window.realNow=Date.now;window.offset=21000;Date.now=()=>realNow()+offset;
      await qa.pollAgentWork();
    }''')
    assert len(requests) == 2
    assert page.locator('#calliope-status span').text_content() == 'Team is working'
    response['status'] = 503
    page.evaluate('qa.pollAgentWork()')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    assert page.evaluate('qa.state.agentWork.pending.length') == 1
    response.update(status=200, body={'pending': [], 'queued_messages': 0, 'activity_state': {'phase': 'ready'}})
    page.evaluate('qa.pollAgentWork()')
    assert len(requests) == 4
    assert page.locator('#calliope-status span').text_content() == 'Ready'
    page.evaluate('Date.now=realNow')


def test_new_session_and_late_receipts_cannot_reuse_previous_roster(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.acceptAgentWork({session_id:'notebook',pending:[{run_id:'iris'}],activity_state:{phase:'team_working'}});
      qa.state.current={...qa.state.current,id:'other'};
      qa.acceptAgentWork({session_id:'other',available:false});
      qa.acceptAgentWork({session_id:'notebook',pending:[],activity_state:{phase:'ready'}});
    }''')
    assert page.locator('#calliope-status span').text_content() == 'Checking activity…'
    assert page.evaluate('qa.state.agentWork.pending || []') == []


@pytest.mark.parametrize('width', [1500, 390])
def test_activity_labels_fit_the_chat_rail(zen_page, width):
    page = zen_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate('''() => {
      document.body.classList.add('mobile-chat-open');
      qa.state.config.healthy=true;qa.state.surfaces=[];qa.state.surfaces=[];
      qa.acceptAgentWork({pending:[],delivering_messages:1,activity_state:{phase:'delivering'}});
      qa.renderAgentWork();qa.renderChat();
    }''')
    for phase in ['delivering', 'unknown', 'team_working']:
        page.evaluate('''phase => {
          qa.acceptAgentWork({pending:phase==='team_working' ? [{specialist_name:'Iris',status:'running'}] : [],
            delivering_messages:phase==='delivering' ? 1 : 0,activity_state:{phase}});
          qa.renderAgentWork();qa.renderChat();
        }''', phase)
        page.wait_for_timeout(250)
        assert page.locator('#calliope-status').evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
        assert page.locator('.chat-column').evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
    page.locator('.chat-column').screenshot(path=f'/tmp/zen-session-activity-{width}.png')
