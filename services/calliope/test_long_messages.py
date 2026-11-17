"""Long drafts survive paste, Floor handoff, and sending without truncation."""
import pytest
from playwright.sync_api import expect
from test_zen_chat_layout import zen_page
from test_floor_veil import floor_page, ORIGIN, PAYLOAD

LONG = ('Supplier Desk: preserve originals; inspect orders and attach evidence.\n' * 750) + 'END OF COMPLETE REQUEST'


def paste(editor, text, with_image=False):
    editor.evaluate('''(element, {text, with_image}) => {
      const data = new DataTransfer();
      data.setData('text/plain', text);
      if (with_image) data.items.add(new File(['image'], 'preview.png', {type:'image/png'}));
      element.dispatchEvent(new ClipboardEvent('paste', {clipboardData:data, bubbles:true, cancelable:true}));
    }''', {'text': text, 'with_image': with_image})


@pytest.mark.parametrize('with_image', [False, True])
def test_chat_pastes_and_sends_complete_long_message(zen_page, with_image):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.state.turns=[];qa.state.config.healthy=true;
      qa.els.input.disabled=false;qa.initializeComposerEditor();qa.setupEvents();
      qa.renderChat(true);
    }''')
    # Use the mounted editor rather than the hidden fallback textarea.
    editor = page.locator('#composer .cm-content')
    editor.click()
    paste(editor, LONG, with_image)
    assert page.evaluate('qa.composerValue()') == LONG
    assert page.evaluate('qa.state.attachments.length') == 0
    sent = []
    def receive(route):
        sent.append(route.request.post_data_json)
        route.fulfill(status=400, json={'error': {'message': 'Test transport: draft preserved'}})
    page.route('**/api/calliope/sessions/notebook/turn', receive)
    page.evaluate('qa.sendTurn()')
    assert len(sent) == 1
    assert sent[0]['message'] == LONG
    assert page.evaluate('qa.composerValue()') == LONG


def test_oversized_chat_draft_is_preserved_with_explicit_send_error(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.state.turns=[];qa.state.config.healthy=true;
      qa.els.input.disabled=false;qa.initializeComposerEditor();qa.renderChat(true);
    }''')
    text = 'x' * 200_001
    paste(page.locator('#composer .cm-content'), text)
    assert page.evaluate('qa.composerValue()') == text
    sent = []
    page.on('request', lambda request: sent.append(request.url) if request.method == 'POST' else None)
    page.evaluate('qa.sendTurn()')
    assert not sent
    assert page.evaluate('qa.composerValue()') == text
    expect(page.get_by_role('status').filter(has_text='Your draft is preserved')).to_be_visible()


def test_floor_long_paste_and_handoff(floor_page):
    page, held = floor_page
    page.goto(ORIGIN + '/floor', wait_until='domcontentloaded')
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json=PAYLOAD)
    page.wait_for_selector('#floorInitialVeil', state='detached')
    page.locator('#floorIntakeOpen').click()
    editor = page.locator('#floorIntakeEditor .cm-content')
    paste(editor, LONG, with_image=True)
    assert page.locator('#floorIntakeInput').input_value() == LONG
    # Freeze the destination; inspect the actual IndexedDB handoff written by Send.
    page.route('**/calliope?**', lambda route: route.fulfill(body='<p>Handoff received</p>', content_type='text/html'))
    page.locator('#floorIntakeSend').click()
    page.wait_for_url('**/calliope?**')
    handoff = page.evaluate('''async () => {
      const key = 'calliope.floorIntake.v1';
      const id = sessionStorage.getItem(key);
      const dbs = await indexedDB.databases();
      const info = dbs.find(d => d.name.includes('floor-intake'));
      const db = await new Promise(resolve => {const r=indexedDB.open(info.name);r.onsuccess=()=>resolve(r.result)});
      return await new Promise(resolve => {const r=db.transaction(db.objectStoreNames[0]).objectStore(db.objectStoreNames[0]).get(id);r.onsuccess=()=>resolve(r.result)});
    }''')
    assert handoff['prompt'] == LONG
    assert handoff['attachments'] == []


@pytest.mark.parametrize('length', [50_000, 200_000, 200_001])
def test_server_message_admission(length, monkeypatch):
    """Execute the real HTTP handler through message validation, before agent work."""
    import ast
    import asyncio
    import contextlib
    import json
    from pathlib import Path
    from types import SimpleNamespace
    import calliope

    source = ast.parse(Path(calliope.__file__).read_text())
    handler = next(n for n in ast.walk(source) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run_turn')
    handler.decorator_list = []
    class Admitted(Exception):
        pass
    def slash_name(message):
        assert message == 'x' * length
        raise Admitted()
    monkeypatch.setattr(calliope.calliope_access, 'require_participant', lambda *_: {'id':'session', 'channel':'web', 'owner_email':'viewer@example.com'})
    monkeypatch.setattr(calliope.calliope_inputs, 'blocking_request', lambda *_, **__: None)
    namespace = dict(vars(calliope), api_owner=lambda _:('viewer@example.com', None),
        auth=SimpleNamespace(read_session_full=lambda _: {}), observe_browser_principal=lambda _: {'name': 'Viewer'},
        conn_factory=lambda:contextlib.nullcontext(None), _calliope_slash_name=slash_name,
        json_response=lambda payload,status=200:(status,payload))
    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<run_turn>', 'exec'), namespace)
    async def body():
        return {'message':'x' * length}
    request = SimpleNamespace(path_params={'session_id':'session'}, json=body)
    if length <= 200_000:
        with pytest.raises(Admitted):
            asyncio.run(namespace['run_turn'](request))
    else:
        status, payload = asyncio.run(namespace['run_turn'](request))
        assert status == 400
        assert payload['error']['code'] == 'MESSAGE_TOO_LONG'
        assert '200,000' in payload['error']['message']
