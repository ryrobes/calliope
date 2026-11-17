"""Delayed navigation must never move a New chat draft into an old thread."""
from urllib.parse import urlsplit

import pytest
from test_zen_chat_layout import zen_page


def test_pending_old_thread_response_cannot_exit_new_chat(zen_page):
    page = zen_page
    held = []
    page.route('**/api/calliope/sessions/notebook', lambda route: held.append(route))
    page.evaluate('''() => {
      qa.initializeComposerEditor();
      qa.state.composerEditor.setValue('A new question');
      window.oldLoad = qa.selectSession('notebook', {preserveActivity:true});
    }''')
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(10)
    assert held
    page.evaluate('qa.openIntake()')
    held.pop().fulfill(json={
        'session': {'id': 'notebook', 'title': 'Old notebook', 'owner_email': 'viewer@example.com'},
        'turns': [], 'surfaces': [],
    })
    assert page.evaluate('oldLoad') is False
    assert page.evaluate('qa.state.current') is None
    assert page.evaluate('Boolean(qa.state.intake)')
    assert page.evaluate('qa.composerValue()') == 'A new question'
    assert page.locator('body').evaluate('el=>el.classList.contains("intake")')


@pytest.mark.parametrize('floor_handoff', [False, True])
def test_new_chat_uses_created_id_even_when_list_only_contains_old_thread(zen_page, floor_handoff):
    page = zen_page
    new = {'id': 'new-notebook', 'title': 'New notebook', 'owner_email': 'viewer@example.com', 'agent_key': 'calliope'}
    old = {'id': 'notebook', 'title': 'Old notebook', 'owner_email': 'viewer@example.com'}
    sent, opened = [], []

    def api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/calliope/config':
            route.fulfill(json={'healthy': True, 'viewer': {'email': 'viewer@example.com'}, 'specialists': []})
        elif path == '/api/calliope/sessions':
            route.fulfill(json={'session': new} if route.request.method == 'POST' else {'sessions': [old]})
        elif path in ('/api/calliope/sessions/new-notebook', '/api/calliope/sessions/notebook'):
            opened.append(path)
            route.fulfill(json={'session': new if path.endswith('/new-notebook') else old, 'turns': [], 'surfaces': []})
        elif path.endswith('/turn'):
            sent.append((path, route.request.post_data_json))
            route.fulfill(status=400, json={'error': {'message': 'Local test stopped before inference'}})
        else:
            route.fulfill(json={})

    page.route('**/api/**', api)
    page.evaluate('qa.state.surfaces=[];qa.state.turns=[];qa.state.lastSessionId="notebook";qa.state.sessions=[qa.state.current]')
    if floor_handoff:
        page.evaluate('''() => {
          sessionStorage.setItem('calliope.floorIntake.v1', JSON.stringify({prompt:'A new question',created_at:Date.now(),team_mode:true}));
          history.replaceState({},'', '/calliope?start=1&session=notebook&view=zen');
          window.launchDone=false;qa.init().then(()=>{launchDone=true});
        }''')
        page.wait_for_function('window.launchDone && !qa.state.intakeStarting')
    else:
        page.evaluate('''async () => {
          qa.openIntake();qa.initializeComposerEditor();
          qa.state.composerEditor.setValue('A new question');
          await qa.beginIntakeTurn();
        }''')
    assert len(sent) == 1
    assert sent[0][0] == '/api/calliope/sessions/new-notebook/turn'
    assert sent[0][1]['message'] == 'A new question'
    assert '/api/calliope/sessions/notebook' not in opened
    assert page.evaluate('qa.state.current.id') == 'new-notebook'
    # The launch veil releases at send time; draft restoration happens after
    # the rejected request returns, independently of session setup finishing.
    page.wait_for_function("qa.composerValue() === 'A new question' && !qa.state.busy")
    assert page.evaluate('qa.composerValue()') == 'A new question'
