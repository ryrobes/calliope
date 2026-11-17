"""Computer placement shares the existing Stage and plan rail without owning input."""
import pytest
from test_zen_chat_layout import zen_page


def test_learned_method_history_shows_evidence_and_forget_without_guest(zen_page):
    page = zen_page
    setup(page)
    rid = '00000000-0000-0000-0000-000000000001'
    jid = '00000000-0000-0000-0000-000000000002'
    mid = '00000000-0000-0000-0000-000000000003'
    page.route('**/api/calliope/computer/runs?*', lambda r: r.fulfill(json={'runs': [{
        'id': rid, 'status': 'finished', 'goal': 'Check filtered invoices', 'created_at': '2026-09-24T12:00:00Z'}]}))
    page.route(f'**/runs/{rid}/manifest?*', lambda r: r.fulfill(json={'expired_at': '2026-09-24'}))
    page.route(f'**/runs/{rid}/events?*', lambda r: r.fulfill(status=503))
    page.route(f'**/runs/{rid}/demonstrations?*', lambda r: r.fulfill(json={'demonstrations': []}))
    page.route(f'**/runs/{rid}/receipts?*', lambda r: r.fulfill(json={'events': [{
        'event_type': 'method.learned', 'created_at': '2026-09-24T12:01:00Z', 'payload': {
            'id': mid, 'version': 1, 'status': 'tentative', 'title': 'Check invoice rows', 'successes': 1, 'failures': 0,
            'method': {'when_to_use': 'After filtering invoices', 'inputs': ['expected_rows'],
                       'steps': ['Read visible rows'], 'checks': ['Compare the row count'], 'pitfalls': ['Wait for loading'],
                       'example': '<img src=x onerror=alert(1)>'},
            'evidence': [{'source': jid + '/stdout.txt', 'quote': 'visible_rows: 5'}]}}]}))
    forgotten = []
    def forget(r):
        forgotten.append(r.request.post_data_json)
        r.fulfill(json={'status': 'retired'})
    page.route('**/api/calliope/computer/methods/forget', forget)
    page.locator('#computer-open').click()
    page.get_by_role('tab', name='History', exact=True).click()
    page.get_by_role('button', name='Check filtered invoices', exact=False).click()
    page.get_by_text('Learned: Check invoice rows', exact=False).click()
    assert page.get_by_text('Compare the row count', exact=True).is_visible()
    assert page.get_by_text('visible_rows: 5', exact=True).is_visible()
    assert page.locator('.computer-events img').count() == 0
    assert f'/jobs/{jid}/stdout.txt' in page.get_by_role('link', name='Source receipt').get_attribute('href')
    page.get_by_role('button', name='Forget method', exact=True).click()
    page.get_by_role('button', name='Forgotten', exact=True).wait_for()
    assert forgotten == [{'session_id': 'notebook', 'run_id': rid, 'method_id': mid}]


def test_terminal_is_live_read_only_and_replays_ansi_output(zen_page):
    page = zen_page
    setup(page)
    page.route('**/api/calliope/computer/status', lambda r: r.fulfill(json={
        'mode':'agent','generation':5,'you_are_driving':False,
        'active_run':{'id':'run','context':{'session_id':'notebook'}},
        'jobs':[{'id':'job','run_id':'run','intent':'Install a tool','status':'running','terminal':{'columns':100,'rows':24}}],
        'recording':True,'queue_length':0,'viewer_url':'/viewer-fixture'}))
    def terminal(r):
        if 'offset=0' in r.request.url:
            r.fulfill(json={'events':[{'version':2,'width':100,'height':24},[0,'o','\x1b[32mready café\x1b[0m\r\n']],
                           'offset':100,'bytes':100,'more':False,'complete':False,'status':'running',
                           'intent':'Install a tool','code':'echo "<img src=x onerror=alert(1)>"'})
        else:
            r.fulfill(json={'events':[[2.531,'o','finished\r\n']], 'offset':150,'bytes':150,
                           'more':False,'complete':True,'status':'completed'})
    page.route('**/api/calliope/computer/runs/run/jobs/job/terminal*', terminal)
    page.evaluate('''() => {
      const Original=window.CalliopeTerminal;
      window.CalliopeTerminal=class extends Original {constructor(...args){super(...args);window.terminalQA=this;}};
    }''')
    page.locator('#computer-open').click()
    assert page.locator('.computer-tabs').get_by_role('tab',name='History',exact=True).get_attribute('aria-selected') == 'true'
    assert page.locator('.computer-screen').is_visible()
    assert page.get_by_role('tab',name='Terminal',exact=True).get_attribute('title') == 'Terminal: job running'
    page.get_by_role('tab',name='Terminal',exact=True).click()
    page.wait_for_function("window.terminalQA?.events.length===2")
    page.wait_for_function("terminalQA.term.buffer.active.getLine(0).translateToString().includes('ready café')")
    assert page.evaluate('terminalQA.term.options.disableStdin')
    assert page.locator('.computer-terminal img').count() == 0
    assert page.locator('.computer-terminal button',has_text='Replay').is_enabled()
    # A fractional final timestamp rounds down in the range input. Replay
    # from that end position must restart, not instantly finish.
    page.locator('.computer-terminal button',has_text='Replay').click()
    page.wait_for_function("terminalQA.playing && Number(terminalQA.seek.value)<1")
    page.wait_for_function("!terminalQA.term.buffer.active.getLine(1).translateToString().includes('finished')")
    page.get_by_role('button',name='Pause replay',exact=True).click()
    page.evaluate("terminalQA.seek.value=0; terminalQA.seek.dispatchEvent(new Event('input'))")
    page.wait_for_function("!terminalQA.term.buffer.active.getLine(1).translateToString().includes('finished')")
    page.locator('.computer-terminal button',has_text='Replay').click()
    page.wait_for_function("terminalQA.term.buffer.active.getLine(1).translateToString().includes('finished')")
    page.locator('.computer-tabs').get_by_role('tab',name='History',exact=True).click()
    assert page.locator('.computer-screen').is_visible()
    assert page.evaluate('terminalQA.closed')


def setup(page):
    page.route('**/api/calliope/admin/computer-storage', lambda r:r.fulfill(status=403,json={}))
    page.route('**/api/calliope/computer/status', lambda r: r.fulfill(json={
        'mode': 'human', 'generation': 4, 'you_are_driving': True,
        'active_run': {'id': 'run', 'context': {'session_id': 'notebook'}},
        'recording': True, 'queue_length': 0, 'viewer_url': '/viewer-fixture'}))
    page.route('**/viewer-fixture*', lambda r: r.fulfill(content_type='text/html', body='''
      <body style="margin:0;background:#234"><p>Live desktop fixture</p>
      <script>parent.postMessage({type:'calliope-computer-size',width:1024,height:768},location.origin)</script>'''))
    page.evaluate('''() => {
      if (!window.workspaceEventsReady) { qa.setupEvents();window.workspaceEventsReady=true; }
      qa.state.config ||= {};
      qa.state.current ||= {id:'notebook'};
      qa.state.surfaces=[];qa.renderChat();
      qa.state.config.computer_use={can_open:true};qa.renderStage();
    }''')


def test_preview_names_the_active_specialist_and_updates_on_handoff(zen_page):
    page = zen_page
    setup(page)
    snapshot = {'mode': 'agent', 'you_are_driving': False, 'active_run': {'id': 'run'},
                'active_agent': {'kind': 'specialist', 'display_name': 'Cassandra'},
                'jobs': [], 'recording': True, 'queue_length': 1, 'viewer_url': '/viewer-fixture'}
    page.route('**/api/calliope/computer/status', lambda r: r.fulfill(json=snapshot))
    page.locator('#computer-open').click()
    title = page.locator('#computer-shelf > header strong')
    title.get_by_text('Cassandra', exact=True).wait_for()
    assert '1 queued' in page.locator('.computer-status').inner_text()
    snapshot.update(mode='human', you_are_driving=True)
    page.get_by_role('button', name='Let Cassandra work', exact=True).wait_for()
    snapshot.update(mode='agent', you_are_driving=False, active_agent={'display_name': 'Iris'})
    title.get_by_text('Iris', exact=True).wait_for()
    snapshot.update(mode='idle', active_run=None, active_agent=None, recording=False, queue_length=0)
    title.get_by_text('Computer', exact=True).wait_for()


def test_workspace_keeps_viewer_alive_and_independent_of_thread_plans(zen_page):
    page=zen_page;setup(page)
    page.locator('#computer-open').click()
    host=page.locator('#workspace-desktop #computer-shelf')
    frame=host.locator('iframe');frame.wait_for()
    page.evaluate("window.liveFrame=document.querySelector('#computer-shelf iframe');window.viewerWindow=liveFrame.contentWindow")
    assert 'view_only' not in frame.get_attribute('src')
    assert host.get_by_role('button',name='Let Callie work',exact=True).is_visible()
    host.get_by_role('button',name='Full screen',exact=True).click()
    page.wait_for_function("document.fullscreenElement?.id === 'computer-shelf'")
    page.evaluate('document.exitFullscreen()')
    page.locator('#workspace-tab-chat').click()
    assert host.is_hidden()
    assert frame.count()==1
    page.evaluate("qa.composerSetValue('Keep my draft')")
    page.locator('#computer-open').click()
    assert frame.evaluate('(el)=>el===window.liveFrame && el.contentWindow===window.viewerWindow')
    page.evaluate("qa.state.current={id:'another'};qa.renderStage()")
    assert frame.evaluate('(el)=>el===window.liveFrame')
    page.evaluate("qa.state.config.computer_use.can_open=false;qa.renderStage()")
    assert page.locator('#computer-open').is_hidden()
    assert page.locator('#workspace-chat').is_visible()
    assert page.evaluate('qa.composerValue()')=='Keep my draft'
    assert frame.count()==0


def test_tabs_preserve_selection_through_jobs_modes_and_reload(zen_page):
    page = zen_page
    setup(page)
    snapshot = {'mode':'agent','generation':5,'you_are_driving':False,
        'active_run':{'id':'run','context':{'session_id':'notebook'}},
        'jobs':[], 'recording':True,'queue_length':0,'viewer_url':'/viewer-fixture'}
    page.route('**/api/calliope/computer/status', lambda r:r.fulfill(json=snapshot))
    page.route('**/api/calliope/computer/runs?*', lambda r:r.fulfill(json={'runs':[]}))
    page.route('**/api/calliope/admin/computer-storage', lambda r:r.fulfill(status=403,json={}))
    page.route('**/api/calliope/computer/runs/run/jobs/*/terminal*',lambda r:r.fulfill(json={
        'events':[],'offset':0,'bytes':0,'more':False,'complete':False,'status':'running'}))
    page.locator('#computer-open').click()
    terminal=page.get_by_role('tab',name='Terminal',exact=True)
    history=page.get_by_role('tab',name='History',exact=True)
    assert page.locator('.computer-tabs').get_by_role('tab',name='Desktop',exact=True).count()==0
    snapshot['jobs']=[{'id':'job','run_id':'run','status':'running','terminal':{}}]
    # A nonempty metadata object represents a shell job.
    snapshot['jobs'][0]['terminal']={'format':'asciicast-v2'}
    page.wait_for_function("document.querySelector('#computer-tab-terminal').classList.contains('has-activity')")
    assert history.get_attribute('aria-selected')=='true'
    assert page.locator('.computer-screen').is_visible()
    terminal.click()
    snapshot['jobs']=[{'id':'python','run_id':'run','status':'running'}]
    page.wait_for_function("document.querySelector('.computer-screen').classList.contains('has-activity')")
    assert terminal.get_attribute('aria-selected')=='true'
    page.locator('#workspace-tab-chat').click()
    assert page.locator('.computer-screen').is_hidden()
    page.locator('#computer-open').click()
    assert terminal.get_attribute('aria-selected')=='true'
    history.click()
    page.get_by_text('No computer runs in this thread yet.',exact=True).wait_for()
    assert page.locator('.computer-screen').is_visible()
    assert page.locator('#computer-panel-terminal').is_hidden()
    page.locator('#workspace-tab-chat').click()
    page.locator('#computer-open').click()
    assert history.get_attribute('aria-selected')=='true'
    history.press('Home')
    assert history.get_attribute('aria-selected')=='true'
    history.press('End')
    assert terminal.get_attribute('aria-selected')=='true'
    terminal.click()
    page.reload(); setup(page)
    assert page.locator('#workspace-chat').is_visible()
    page.locator('#computer-open').click()
    assert terminal.get_attribute('aria-selected')=='true'
    assert page.locator('.computer-screen').is_visible()


def test_browser_feedback_does_not_intercept_input_or_change_values(zen_page):
    from pathlib import Path
    page = zen_page
    script = (Path(__file__).parents[2] / 'docker/desktop/browser_activity.js').read_text()
    page.route('http://pointer.test/**', lambda r:r.fulfill(content_type='text/html', body='<input aria-label="Note"><button onclick="this.textContent=\'Clicked\'">Click</button>'))
    page.add_init_script(script=script)
    page.goto('http://pointer.test/')
    page.get_by_role('textbox',name='Note').fill('unchanged value')
    assert page.get_by_role('textbox',name='Note').input_value()=='unchanged value'
    assert page.locator('[aria-hidden=true]').count()==1
    page.evaluate(script)
    page.get_by_role('button',name='Click',exact=True).click()
    assert page.get_by_role('button',name='Clicked',exact=True).is_visible()
    assert page.locator('[aria-hidden=true]').count()==1
    assert page.get_by_role('textbox',name='Note').input_value()=='unchanged value'
    assert page.locator('[aria-hidden=true]').evaluate("el => getComputedStyle(el).pointerEvents")=='none'
    page.reload()
    page.get_by_role('button',name='Click',exact=True).click()
    assert page.locator('[aria-hidden=true]').count()==1


def test_completed_replay_has_one_video_and_receipts_seek_original_time(zen_page):
    import json
    page = zen_page
    setup(page)
    rid = '00000000-0000-0000-0000-000000000001'
    page.route('**/api/calliope/computer/runs?*', lambda r:r.fulfill(json={'runs':[
        {'id':rid,'status':'finished','goal':'Replay check','created_at':'2026-09-25T12:00:00Z'}]}))
    page.route(f'**/runs/{rid}/manifest?*', lambda r:r.fulfill(json={
        'started_at':1000,'segments':[{'file':'000000.mp4','start':0,'end':30}],
        'replay':{'status':'ready','duration':4,'skipped_seconds':26,'timeline':[
            {'source_start':0,'source_end':2,'replay_start':0,'replay_end':2},
            {'source_start':28,'source_end':30,'replay_start':2,'replay_end':4}]}}))
    page.route(f'**/runs/{rid}/events?*', lambda r:r.fulfill(body=json.dumps({
        'type':'execution.finished','at':1029,'payload':{'intent':'Check completed workbook'}})+'\n'))
    page.route(f'**/runs/{rid}/receipts?*', lambda r:r.fulfill(json={'events':[]}))
    page.route(f'**/runs/{rid}/demonstrations?*', lambda r:r.fulfill(json={'demonstrations':[]}))
    page.route(f'**/runs/{rid}/video/replay.mp4?*', lambda r:r.fulfill(status=204))
    page.locator('#computer-open').click()
    page.get_by_role('tab',name='History',exact=True).click()
    page.get_by_role('button',name='Replay check',exact=False).click()
    player = page.locator('.computer-history video')
    player.wait_for()
    assert player.count()==1 and '/video/replay.mp4?' in player.get_attribute('src')
    assert page.get_by_label('Recording segment',exact=True).count()==0
    assert page.get_by_role('link',name='Download replay',exact=True).is_visible()
    # Observe seek commands without asking Chromium to decode the mocked asset.
    player.evaluate('''video => {
      Object.defineProperty(video,'readyState',{get:()=>1});
      Object.defineProperty(video,'currentTime',{set:value=>window.replaySeek=value});
      video.play=()=>Promise.resolve();
    }''')
    page.get_by_text('Check completed workbook',exact=False).click()
    page.get_by_role('button',name='Watch this moment',exact=True).click()
    assert page.evaluate('window.replaySeek')==3


@pytest.mark.parametrize('width', [320, 420, 640])
def test_desktop_sits_above_lazy_recording_cards_and_keeps_its_frame(zen_page, width):
    from pathlib import Path
    page=zen_page;setup(page)
    page.evaluate('(width)=>qa.setChatWidth(width)',width)
    requests=[]
    page.on('request',lambda r:requests.append(r.url))
    rid='00000000-0000-0000-0000-000000000001'
    page.route('**/api/calliope/computer/runs?*',lambda r:r.fulfill(json={'runs':[
        {'id':rid,'status':'finished','goal':'Verify the dashboard filters','created_at':'2026-09-28T12:00:00Z'}]}))
    page.route(f'**/runs/{rid}/thumbnail.jpg?*',lambda r:r.fulfill(content_type='image/jpeg',
        body=(Path(__file__).parent/'calliope/callie-avatar-day.jpg').read_bytes()))
    page.route(f'**/runs/{rid}/manifest?*',lambda r:r.fulfill(json={'started_at':1000,'replay':{
        'status':'ready','duration':4,'skipped_seconds':0,'timeline':[]}}))
    page.route(f'**/runs/{rid}/events?*',lambda r:r.fulfill(body=''))
    page.route(f'**/runs/{rid}/receipts?*',lambda r:r.fulfill(json={'events':[]}))
    page.route(f'**/runs/{rid}/demonstrations?*',lambda r:r.fulfill(json={'demonstrations':[]}))
    page.route(f'**/runs/{rid}/video/replay.mp4?*',lambda r:r.fulfill(status=204))
    page.locator('#computer-open').click()
    card=page.get_by_role('button',name='Verify the dashboard filters',exact=False)
    card.wait_for()
    page.locator('.computer-run-preview img').evaluate('(img)=>img.decode()')
    screen=page.locator('.computer-screen');tabs=page.locator('.computer-tabs')
    assert tabs.bounding_box()['y'] >= screen.bounding_box()['y']+screen.bounding_box()['height']
    assert abs(screen.bounding_box()['width']/screen.bounding_box()['height']-4/3)<.03
    assert screen.locator('iframe').count()==1
    page.evaluate("window.retainedDesktop=document.querySelector('.computer-screen iframe')")
    assert page.locator('.computer-history video').count()==0
    assert not any('/manifest?' in url or '/video/' in url or '/receipts?' in url for url in requests)
    assert page.locator('.computer-history').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    if width == 420: page.locator('#workspace-desktop').screenshot(path='/tmp/desktop-panel-layout-420.png')
    card.click()
    page.locator('.computer-history video').wait_for()
    assert card.get_attribute('aria-expanded')=='true'
    assert any('/manifest?' in url for url in requests)
    assert page.locator('.computer-history').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    card.click()
    assert card.get_attribute('aria-expanded')=='false'
    assert page.locator('.computer-history video').count()==0
    assert screen.locator('iframe').evaluate('(el)=>el===window.retainedDesktop')
    held=[]
    page.route(f'**/runs/{rid}/manifest?*',lambda r:held.append(r))
    with page.expect_request(f'**/runs/{rid}/manifest?*'):
        card.click()
    card.click()
    for route in held: route.fulfill(json={'replay':{'status':'ready','duration':4,'timeline':[]}})
    page.wait_for_timeout(50)
    assert page.locator('.computer-history video').count()==0
    page.get_by_role('tab',name='Terminal',exact=True).click()
    assert screen.is_visible()
    assert screen.locator('iframe').evaluate('(el)=>el===window.retainedDesktop')
