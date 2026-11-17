"""Frozen Stage images are authorized copies, not mutable preview references."""
import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
import calliope
import desktop_tools
from test_zen_chat_layout import zen_page
from test_workspace_panel import setup
from test_sketch_drawer import open_panel


def png(color):
    out = BytesIO(); Image.new('RGB', (32, 20), color).save(out, format='PNG'); return out.getvalue()


@pytest.fixture
def storage(monkeypatch, tmp_path):
    sid, tid, kid = (str(uuid4()) for _ in range(3))
    saved = []
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, args):
            assert not sql.lstrip().startswith(('INSERT', 'UPDATE'))
            if 'calliope_turns' in sql: row = {'id': tid}
            else: row = {'id': kid, 'revision': 7} if args == (kid, sid) else None
            return SimpleNamespace(fetchone=lambda: row)
    monkeypatch.setattr(calliope.calliope_access, 'require_participant', lambda *args: None)
    def insert(conn, session_id, turn_id, rows):
        assert (session_id, turn_id) == (sid, tid)
        saved.extend(rows)
        return rows
    monkeypatch.setattr(calliope, '_insert_surfaces', insert)
    return DB, SimpleNamespace(file_root=tmp_path, desktop_enabled=True), sid, kid, saved


@pytest.mark.parametrize('source', ['desktop', 'sketch'])
def test_pixels_frozen_independently_of_live_source(storage, monkeypatch, source):
    db, config, sid, kid, saved = storage
    current = config.file_root / 'live.png'; current.write_bytes(png('red'))
    monkeypatch.setattr(desktop_tools, 'look_at_desktop', lambda *args: {'path': str(current)})
    body = {'source': source, 'sketch_id': kid, 'dirty': True,
            'data_url': 'data:image/png;base64,' + base64.b64encode(current.read_bytes()).decode()}
    first = calliope._freeze_workspace_snapshot(db, config, sid, 'owner', body)['surface']
    current.write_bytes(png('blue'))
    second = calliope._freeze_workspace_snapshot(db, config, sid, 'owner', body)['surface']
    assert Path(first['payload']['storage_path']).read_bytes() == png('red')
    assert first['lineage_key'] != second['lineage_key']
    assert first['payload']['frozen'] and first['payload']['width'] == 32
    assert first['presentation']['phase'] == 'published'
    if source == 'sketch': assert first['source']['includes_unsaved_changes']


def test_unauthorized_capture_never_observes_desktop(storage, monkeypatch):
    db, config, sid, kid, saved = storage
    def deny(*args): raise PermissionError('Not a participant')
    monkeypatch.setattr(calliope.calliope_access, 'require_participant', deny)
    monkeypatch.setattr(desktop_tools, 'look_at_desktop', lambda *args: pytest.fail('Unauthorized capture'))
    with pytest.raises(PermissionError): calliope._freeze_workspace_snapshot(db, config, sid, 'stranger', {'source': 'desktop'})
    assert not saved


def test_sketch_must_belong_to_target_notebook(storage):
    db, config, sid, kid, saved = storage
    with pytest.raises(ValueError, match='belong'):
        calliope._freeze_workspace_snapshot(db, config, sid, 'owner', {'source': 'sketch', 'sketch_id': str(uuid4())})
    assert not saved


def test_invalid_png_is_not_published(storage):
    db, config, sid, kid, saved = storage
    with pytest.raises(ValueError):
        calliope._freeze_workspace_snapshot(db, config, sid, 'owner', {'source': 'sketch', 'sketch_id': kid, 'data_url': 'data:image/svg+xml;base64,PHN2Zy8+'})
    assert not saved


@pytest.mark.parametrize('source', ['desktop', 'sketch'])
def test_snapshot_button_publishes_without_a_turn_or_switching_views(zen_page, source):
    page = zen_page; setup(page)
    requests = []
    def publish(route):
        assert route.request.headers['x-calliope-snapshot'] == '1'
        requests.append(route.request.post_data_json)
        route.fulfill(json={'surface': {'id': 'frozen', 'kind': 'image', 'title': 'Frozen snapshot',
            'turn_id': 'one', 'lineage_key': 'snapshot:one', 'tool_name': 'workspace_snapshot',
            'source': {'phase': 'published'}, 'presentation': {'phase': 'published'},
            'payload': {'frozen': True, 'image_url': '/api/calliope/surfaces/frozen/image'}}})
    page.route('**/api/calliope/sessions/*/snapshots', publish)
    page.route('**/api/calliope/surfaces/frozen/image', lambda r:r.fulfill(body=png('red'), content_type='image/png'))
    count = page.evaluate('qa.state.turns.length')
    if source == 'desktop':
        page.locator('#computer-open').click()
        page.locator('#computer-shelf').get_by_role('button', name='Snapshot', exact=True).click()
    else:
        open_panel(page)
        frame = page.frame_locator('#sketch-shelf iframe')
        frame.locator('body').evaluate('''() => addEventListener('message',e=>{
          if(e.data.type==='calliope.sketch.snapshot')parent.postMessage({type:'calliope.sketch.snapshot.result',
            sketch_id:'drawing',request_id:e.data.request_id,data_url:'data:image/png;base64,local-edit',dirty:true},location.origin);
        })''')
        page.locator('[data-snapshot-sketch]').click()
    page.wait_for_function("qa.state.surfaces.some(s=>s.id==='frozen')")
    assert requests[0]['source'] == source
    if source == 'sketch': assert requests[0]['dirty'] and requests[0]['data_url'].endswith('local-edit')
    assert page.evaluate('qa.state.turns.length') == count
    assert page.locator('#workspace-' + source).is_visible()
    assert page.locator('#stage [data-surface-id="frozen"]').is_visible()
    page.screenshot(path='/tmp/workspace-' + source + '-snapshot.png')


def test_native_scene_survives_source_changes_and_is_private(storage):
    import json
    db, config, sid, kid, saved = storage
    scene = {'elements': [{'id':'box','type':'rectangle','x':1,'y':2,'width':50,'height':60,
                           'link':'https://untrusted.example'}], 'files': {}, 'appState': {'viewBackgroundColor':'#ffffff'}}
    body = {'source':'sketch','sketch_id':kid,'scene':scene,
            'data_url':'data:image/png;base64,' + base64.b64encode(png('red')).decode()}
    surface = calliope._freeze_workspace_snapshot(db, config, sid, 'owner', body)['surface']
    scene['elements'][0]['width'] = 999
    row = dict(surface, id=str(uuid4()), session_id=sid)
    class Reader(db):
        def execute(self, sql, args):
            assert 'calliope_session_can_view' in sql and args[1] == 'owner'
            return SimpleNamespace(fetchone=lambda: row)
    result = calliope._read_frozen_sketch(Reader, config, row['id'], 'owner')
    assert result['read_only'] and result['presentation'] and result['frozen_snapshot']
    assert result['sketch']['scene']['elements'][0]['width'] == 50
    assert result['sketch']['scene']['elements'][0]['link'] is None
    # A substituted scene must never be served under the original frozen receipt.
    path = Path(surface['payload']['storage_path']).with_suffix('.excalidraw')
    path.write_text(json.dumps(scene))
    with pytest.raises(LookupError): calliope._read_frozen_sketch(Reader, config, row['id'], 'owner')


def test_embedded_assets_are_self_contained_and_remote_assets_rejected():
    import json
    scene = {'elements':[{'id':'photo','type':'image','fileId':'asset'}],
             'files':{'asset':{'dataURL':'data:image/png;base64,'+base64.b64encode(png('blue')).decode()}}}
    frozen = json.loads(calliope._frozen_sketch_scene(scene))
    assert frozen['files']['asset']['dataURL'] == scene['files']['asset']['dataURL']
    scene['files']['asset']['dataURL'] = 'https://example.com/mutable.png'
    with pytest.raises(ValueError, match='embedded'): calliope._frozen_sketch_scene(scene)


def test_snapshot_reader_denies_missing_access_before_reading_files(storage):
    db, config, sid, kid, saved = storage
    class Denied(db):
        def execute(self, sql, args):
            assert 'calliope_session_can_view' in sql
            return SimpleNamespace(fetchone=lambda: None)
    with pytest.raises(LookupError): calliope._read_frozen_sketch(Denied, config, str(uuid4()), 'stranger')


def test_multiple_native_snapshots_stay_on_stage_and_live_sketch_stays_in_tab(zen_page):
    page=zen_page;setup(page)
    page.evaluate('''() => {
      const snapshots=['a','b'].map(id=>({id,kind:'image',title:'Frozen '+id,turn_id:'one',
        lineage_key:'snapshot:'+id,source:{phase:'published'},presentation:{phase:'published'},
        payload:{frozen:true,scene_format:'excalidraw',image_url:'/preview.png'}}));
      qa.state.surfaces.push(...snapshots);qa.renderStage();
    }''')
    assert page.locator('#stage iframe[data-frozen-sketch]').count()==2
    page.locator('#workspace-tab-sketch').click()
    page.wait_for_function("document.querySelector('#sketch-shelf iframe')?.contentWindow.identity")
    assert page.locator('#sketch-shelf iframe[data-sketch-id="drawing"]').count()==1
    assert page.locator('#stage iframe[data-frozen-sketch]').count()==2


def test_frozen_viewer_receives_ready_and_theme_messages_without_editor_card(zen_page):
    page=zen_page;setup(page)
    page.evaluate('''() => {
      qa.state.surfaces.push({id:'snap',kind:'image',turn_id:'one',title:'Snapshot',
        source:{phase:'published'},payload:{scene_format:'excalidraw',image_url:'/preview.png'}});
      qa.renderStage();
      const frame=document.querySelector('iframe[data-frozen-sketch]');
      for(const type of ['calliope.sketch.ready','calliope.sketch.theme.request']) {
        dispatchEvent(new MessageEvent('message',{origin:location.origin,source:frame.contentWindow,data:{type}}));
      }
    }''')
    assert page.locator('#stage [data-sketch-frame]').get_attribute('data-sketch-ready')=='true'
    page.wait_for_timeout(100)
