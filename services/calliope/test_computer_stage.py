"""Temporary Stage desktop: real polling, iframe lifetime and measured packing."""
import time
from urllib.parse import parse_qs, urlsplit

from test_zen_chat_layout import zen_page
from test_stage_packing import prepare, populate, geometry, no_overlaps


def setup(page):
    snapshot = {'mode': 'agent', 'generation': 1, 'you_are_driving': False,
                'active_run': {'id': 'run', 'context': {'session_id': 'notebook'}},
                'active_agent': {'display_name': 'Iris'}, 'jobs': [], 'recording': True,
                'viewer_url': '/desktop-fixture', 'queue_length': 0}
    requests = []
    page.route('**/api/calliope/computer/status', lambda r: r.fulfill(json=snapshot))
    page.route('**/api/calliope/admin/computer-storage', lambda r: r.fulfill(status=403, json={}))
    def viewer(r):
        requests.append(r.request.url)
        r.fulfill(content_type='text/html', body='''<body style="margin:0;background:#10252d;color:#e1ecee;font:15px sans-serif">
          <header style="padding:18px;background:#20353d">Dashboard · Regional sales</header>
          <div style="padding:20px"><h2>Checking the filters</h2><p>Live desktop fixture</p></div>
          <script>window.identity=String(Math.random());
          parent.postMessage({type:'calliope-computer-size',width:1440,height:900},location.origin);
          parent.postMessage({type:'calliope-computer-ready'},location.origin);</script>''')
    page.route('**/desktop-fixture*', viewer)
    page.evaluate('''() => {
      qa.setupEvents();qa.state.surfaces=[];qa.renderChat();
      qa.state.config.computer_use={can_open:true};qa.renderStage();
    }''')
    page.wait_for_function('qa.computer()?.snapshot?.generation===1 && !qa.computer().polling')
    return snapshot, requests


def poll(page):
    page.wait_for_function('!qa.computer().polling')
    page.evaluate('qa.computer().poll()')
    page.wait_for_timeout(60)  # allow the coalesced Stage render to settle


def job(**kwargs):
    return {'id': 'job', 'run_id': 'run', 'status': 'running', 'started_at': time.time(),
            'intent': 'Checking dashboard filters', **kwargs}


def test_claim_and_terminal_do_not_open_but_execution_and_short_jobs_do(zen_page):
    page = zen_page
    state, requests = setup(page)
    host = page.locator('#stage-desktop')
    assert host.is_hidden() and not requests  # recording/ownership alone
    state['jobs'] = [job(terminal={'format': 'asciicast-v2'})]
    poll(page)
    assert host.is_hidden() and not requests
    state['jobs'] = [job()]
    poll(page)
    frame = host.locator('iframe')
    frame.wait_for()
    assert 'Iris · using the desktop' in host.inner_text()
    assert host.locator('.desktop-stage-caption').inner_text() == 'Checking dashboard filters'
    assert parse_qs(urlsplit(frame.get_attribute('src')).query)['path'] == ['api/calliope/computer/stream?view_only=1']
    assert frame.evaluate('(n)=>n.inert && n.tabIndex===-1 && getComputedStyle(n).pointerEvents==="none"')
    assert page.locator('#stage-empty').is_hidden()
    assert page.evaluate('qa.state.surfaces.length') == 0
    state['jobs'][0].update(status='completed', ended_at=time.time())
    poll(page)
    assert host.is_visible() and 'just used the desktop' in host.inner_text()
    state['jobs'][0]['ended_at'] = time.time() - 14.8
    poll(page)
    host.wait_for(state='hidden')  # timer expires without a new status response
    assert frame.count() == 0
    # A job that completed between polls must still appear briefly.
    state['jobs'] = [job(id='quick', status='completed', ended_at=time.time())]
    poll(page)
    assert host.is_visible()
    state.update(active_run=None, mode='idle')
    poll(page)
    assert host.is_hidden() and frame.count() == 0


def test_minimize_survives_new_jobs_reclaim_and_chat_navigation(zen_page):
    page = zen_page
    state, requests = setup(page)
    state['jobs'] = [job()]
    poll(page)
    host = page.locator('#stage-desktop')
    page.get_by_role('button', name='Minimize desktop preview').click()
    assert host.is_visible() and host.locator('iframe').count() == 0
    assert page.get_by_role('button', name='Show desktop preview').get_attribute('aria-expanded') == 'false'
    count = len(requests)
    state['generation'] += 1
    state['active_run']['id'] = 'new-run'
    state['jobs'] = [job(id='next', run_id='new-run')]
    poll(page)
    assert len(requests) == count and host.locator('iframe').count() == 0
    page.evaluate("qa.state.current={id:'other'};qa.renderStage()")
    assert host.is_hidden()
    page.evaluate("qa.state.current={id:'notebook'};qa.desktopStage().minimized.clear();qa.renderStage()")
    assert host.is_visible() and host.locator('iframe').count() == 0
    assert page.evaluate("sessionStorage.getItem('calliope-stage-desktop-minimized:notebook')") == 'true'
    page.get_by_role('button', name='Show desktop preview').click()
    host.locator('iframe').wait_for()
    assert len(requests) == count + 1
    # A different chat gets its own preference, and never shows this chat's job.
    page.evaluate("qa.state.current={id:'other'};qa.renderStage()")
    assert host.is_hidden()
    state['active_run']['context']['session_id'] = 'other'
    poll(page)
    host.locator('iframe').wait_for()


def test_sidebar_takeover_stop_and_stale_status_remove_preview(zen_page):
    page = zen_page
    state, requests = setup(page)
    state['jobs'] = [job()]
    poll(page)
    host = page.locator('#stage-desktop')
    page.get_by_role('button', name='Open Desktop panel').click()
    page.locator('#computer-shelf iframe').wait_for()
    assert host.is_hidden() and host.locator('iframe').count() == 0
    page.locator('#workspace-tab-chat').click()
    host.locator('iframe').wait_for()
    state.update(mode='human', you_are_driving=True)
    poll(page)
    assert host.is_hidden()
    state.update(mode='agent', you_are_driving=False)
    poll(page)
    host.locator('iframe').wait_for()
    # Expire an unrefreshed running job rather than leaving an orphan forever.
    page.evaluate('''() => {
      clearTimeout(qa.computer().timer);qa.computer().snapshotAt=Date.now()-19900;qa.renderStage();
    }''')
    host.wait_for(state='hidden')
    assert host.locator('iframe').count() == 0
    poll(page)
    host.locator('iframe').wait_for()
    state['active_run'] = None
    poll(page)
    assert host.is_hidden()


def test_viewer_survives_repacking_and_scales_with_stage_columns(zen_page):
    page = zen_page
    state, requests = setup(page)
    prepare(page, 1500)
    populate(page, [220, 160, 120, 100])
    state['jobs'] = [job()]
    poll(page)
    page.wait_for_function("document.querySelector('#stage-desktop iframe')?.contentWindow.identity")
    page.evaluate("window.keptFrame=document.querySelector('#stage-desktop iframe');window.keptIdentity=keptFrame.contentWindow.identity")
    for width, columns in [(1500, 3), (1100, 2), (650, 1), (1450, 3)]:
        page.evaluate("w=>document.body.style.setProperty('--test-stage-width',w+'px')", width)
        page.wait_for_timeout(350)
        boxes = geometry(page)
        no_overlaps(boxes)
        desktop = page.locator('.desktop-stage-card').bounding_box()
        inner = page.locator('.zen-packed').evaluate('(n)=>n.clientWidth')
        assert abs(desktop['width'] - (inner - (columns-1)*10)/columns) < 2
        assert page.locator('#stage-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
        page.evaluate('qa.renderStage();qa.renderStage()')
        assert page.evaluate("keptFrame===document.querySelector('#stage-desktop iframe') && keptFrame.contentWindow.identity===keptIdentity")
    assert len(requests) == 1
    page.locator('#stage-scroll').screenshot(path='/tmp/stage-desktop-preview.png')
    before = page.locator('.desktop-stage-card').bounding_box()['height']
    page.get_by_role('button', name='Minimize desktop preview').click()
    page.wait_for_timeout(350)
    assert page.locator('.desktop-stage-card').bounding_box()['height'] < before/2
    no_overlaps(geometry(page))
    page.locator('#stage-scroll').screenshot(path='/tmp/stage-desktop-minimized.png')
    state['active_run'] = None
    poll(page)
    page.wait_for_timeout(350)
    no_overlaps(geometry(page))
    assert min(box['y'] for box in geometry(page)) == 0
