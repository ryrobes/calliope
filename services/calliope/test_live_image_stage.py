"""Live native file delivery, exact-image deduplication, and visible chronology."""
import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import calliope
from test_generated_image_delivery import db, seed
from test_zen_chat_layout import zen_page


@pytest.fixture
def files(tmp_path, monkeypatch):
    root = tmp_path / 'hermes'
    (root / 'outputs').mkdir(parents=True)
    config = calliope.CalliopeConfig(
        hermes_url='http://unused', hermes_api_key='unused', memory_key='test',
        file_root=tmp_path / 'stage', max_image_bytes=1024 * 1024,
        max_export_bytes=1024 * 1024, export_roots=(root,),
    )
    monkeypatch.setattr(calliope, '_capture_source_roots', lambda: (root,))
    return config, root / 'outputs' / 'mock.png'


def write_image(path, body=b'first', timestamp=1789290000):
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + body)
    os.utime(path, (timestamp, timestamp))


def deliver(db, destination, config, path, call_id='image-1', specialist=None):
    return calliope._insert_progressive_result_surfaces(
        db, config, 'owner@example.com', destination['session_id'], destination['turn_id'],
        'image_generate', {'path': str(path)}, {}, call_id, specialist=specialist,
    )


def capture(config, destination, path, call_id='capture-1'):
    return calliope._publish_local_files([{
        'kind': 'image', 'title': 'Capture · Revenue dashboard',
        'tool_name': 'capture_artifact', 'tool_call_id': call_id,
        'lineage_key': 'capture:revenue', 'artifact_slug': 'revenue', 'artifact_version': 2,
        'payload': {'path': str(path)}, 'source': {'origin': 'capture'},
        'presentation': {'phase': 'published'},
    }], [], '', config, **destination)


@pytest.mark.parametrize('specialist', [None, {'key': 'iris', 'run_id': 'iris-run', 'thread_id': 'iris-thread', 'round': 2}])
def test_native_images_arrive_before_turn_end_and_keep_overwritten_revisions(db, files, specialist):
    destination = seed(db)
    config, path = files
    write_image(path)
    first = deliver(db, destination, config, path, specialist=specialist)
    assert len(first) == 1
    assert first[0]['presentation']['phase'] == 'published'
    assert first[0]['payload']['file_modified_at'] == datetime.fromtimestamp(1789290000, timezone.utc).isoformat()
    if specialist:
        assert first[0]['source']['specialist_run_id'] == 'iris-run'
    with db() as conn:
        frozen = conn.execute('SELECT payload FROM rvbbit.calliope_surfaces').fetchone()['payload']['storage_path']
        assert conn.execute('SELECT status FROM rvbbit.calliope_turns').fetchone()['status'] == 'running'
    write_image(path, b'second', 1789290090)
    second = deliver(db, destination, config, path, call_id='image-2', specialist=specialist)
    assert len(second) == 1 and second[0]['id'] != first[0]['id']
    assert Path(frozen).read_bytes().endswith(b'first')
    # Transcript replay after completion picks up the last revision but cannot duplicate it.
    transcript = [{'role': 'tool', 'tool_name': 'image_generate', 'content': {'path': str(path)}}]
    recovered = calliope._publish_local_files([], transcript, '', config, **destination)
    assert calliope._insert_surfaces(db, **destination, projected=recovered) == []
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 2


@pytest.mark.parametrize('automatic_first', [True, False])
def test_explicit_and_automatic_identical_images_share_one_tile(db, files, automatic_first):
    destination = seed(db)
    config, path = files
    write_image(path)
    explicit = capture(config, destination, path)
    if automatic_first:
        first = deliver(db, destination, config, path)
        result = calliope._insert_surfaces(db, **destination, projected=explicit)
        assert result[0]['id'] == first[0]['id']
        assert result[0]['created_at'] == first[0]['created_at']
        assert result[0]['title'] == 'Capture · Revenue dashboard'
        assert result[0]['artifact_version'] == 2
    else:
        calliope._insert_surfaces(db, **destination, projected=explicit)
    assert deliver(db, destination, config, path) == []
    assert calliope._insert_surfaces(db, **destination, projected=explicit) == []
    with db() as conn:
        rows = conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchall()
        assert len(rows) == 1 and rows[0]['tool_name'] == 'capture_artifact'


def test_dedup_keeps_distinct_content_explicit_receipts_and_other_turns(db, files):
    destination = seed(db)
    config, path = files
    write_image(path)
    calliope._insert_surfaces(db, **destination, projected=capture(config, destination, path))
    assert calliope._insert_surfaces(db, **destination, projected=capture(config, destination, path, 'capture-2'))
    write_image(path, b'revised')
    assert deliver(db, destination, config, path)
    other = {**destination, 'turn_id': str(uuid4())}
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",
                     (other['turn_id'], other['session_id']))
    assert deliver(db, other, config, path)
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 4


def test_parallel_automatic_and_explicit_delivery_converges(db, files):
    destination = seed(db)
    config, path = files
    write_image(path)
    explicit = capture(config, destination, path)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [workers.submit(deliver, db, destination, config, path),
                   workers.submit(calliope._insert_surfaces, db, **destination, projected=explicit)]
        for result in results:
            result.result(timeout=10)
    with db() as conn:
        rows = conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchall()
        assert len(rows) == 1 and rows[0]['artifact_slug'] == 'revenue'


def test_same_file_aliases_in_one_receipt_only_publish_once(files):
    config, path = files
    write_image(path)
    alias = path.parent / 'alias.png'
    alias.symlink_to(path)
    output = calliope._publish_local_files([], [], f'[Mock]({alias}) [Mock]({path})', config, 'session', 'turn')
    assert len(output) == 1


def test_specialist_live_stream_emits_in_place_publication_upgrade():
    snapshot = {'run': {'id': '0cd38f1d-1ffc-4c40-b59d-aac0de777ef4', 'status': 'running'}, 'messages': [],
                'surfaces': [{'id': 'one', 'kind': 'document', 'title': 'mock'}]}
    state = {}
    assert b'event: calliope.surfaces' in b''.join(calliope._specialist_live_frames(snapshot, state, 'turn'))
    assert calliope._specialist_live_frames(snapshot, state, 'turn') == []
    changed = copy.deepcopy(snapshot)
    changed['surfaces'][0].update(kind='image', title='Capture · Revenue')
    frames = b''.join(calliope._specialist_live_frames(changed, state, 'turn'))
    assert b'event: calliope.surfaces' in frames and b'Capture' in frames
    assert calliope._specialist_live_frames(changed, state, 'turn') == []


@pytest.mark.parametrize('zen', [True, False])
def test_batched_mocks_sort_by_saved_time_and_display_seconds(zen_page, zen):
    page = zen_page
    page.evaluate('''zen => {
      const q=qa; q.state.zen=zen;
      q.state.current={id:'session',title:'Mocks'};
      q.state.turns=[{id:'turn',ordinal:1,status:'complete',turn_kind:'chat'}];
      const base={turn_id:'turn',kind:'document',tool_name:'hermes_local_file',
        created_at:'2026-09-13T12:00:00Z'};
      q.state.surfaces=[
        {...base,id:'old',ordinal:2,title:'Older mock',payload:{mime_type:'image/png',filename:'old.png',
          file_modified_at:'2026-09-13T11:10:05Z'}},
        {...base,id:'new',ordinal:1,title:'Latest mock',payload:{mime_type:'image/png',filename:'new.png',
          file_modified_at:'2026-09-13T11:12:45Z'}}];
      q.renderStage();
    }''', zen)
    ids = page.locator('#stage .surface[data-surface-id]').evaluate_all('(els)=>els.map(e=>e.dataset.surfaceId)')
    assert ids == ['new', 'old']
    expected = page.evaluate("new Date('2026-09-13T11:12:45Z').toLocaleTimeString([], {hour:'numeric',minute:'2-digit',second:'2-digit'})")
    assert expected in page.locator('#stage [data-surface-id=new]').inner_text()


def test_error_receipts_and_unpublished_working_files_do_not_go_live(files):
    config, path = files
    write_image(path)
    def unexpected_connection():
        raise AssertionError('No Stage write is expected')
    destination = {'session_id': 'session', 'turn_id': 'turn'}
    assert calliope._insert_progressive_result_surfaces(
        unexpected_connection, config, 'owner@example.com', **destination,
        tool_name='image_generate', result={'error': 'failed', 'path': str(path)},
        args={}, tool_call_id='failed-image',
    ) == []
    working = path.parent.parent / 'internal-test.json'
    working.write_text('{}')
    assert calliope._insert_progressive_result_surfaces(
        unexpected_connection, config, 'owner@example.com', **destination,
        tool_name='terminal', result={'path': str(working)}, args={}, tool_call_id='test',
    ) == []


def test_upgraded_capture_keeps_original_download_url_and_authorization(db, files, monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    config, path = files
    destination = seed(db)
    write_image(path)
    first = deliver(db, destination, config, path)[0]
    calliope._insert_surfaces(db, **destination, projected=capture(config, destination, path))
    with db() as conn:
        conn.execute('''CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean)
          RETURNS boolean LANGUAGE sql AS $$ SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions
          WHERE id=$1 AND owner_email=$2) $$''')
    routes = {}
    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[(path, tuple(methods))] = handler
                return handler
            return register
    monkeypatch.setitem(sys.modules, 'auth', SimpleNamespace(
        read_session_full=lambda request: request.session, google_enabled=lambda: False,
        background_layer=lambda *a, **kw: '',
    ))
    for name in ['ensure_tables', '_start_session_synopsis_worker']:
        monkeypatch.setattr(calliope, name, lambda *a: None)
    for module in [calliope.hermes_docs, calliope.hermes_config, calliope.provider_settings]:
        monkeypatch.setattr(module, 'ensure', lambda *a: None)
    monkeypatch.setattr(calliope.CalliopeConfig, 'from_env', classmethod(lambda cls: config))
    calliope.register_calliope_routes(MCP(), db, '', lambda doc: doc)
    request = SimpleNamespace(
        session={'identity': 'owner@example.com', 'sub': 'analyst_role', 'mapped': True, 'via': 'google'},
        path_params={'surface_id': first['id']}, query_params={},
    )
    handler = routes[('/api/calliope/files/{surface_id}', ('GET',))]
    response = asyncio.run(handler(request))
    assert response.status_code == 200
    assert Path(response.path).read_bytes() == path.read_bytes()
    request.session['identity'] = 'someone-else@example.com'
    assert asyncio.run(handler(request)).status_code == 404


def test_upgrade_keeps_capture_lineage_and_legacy_replay_is_safe(db, files):
    destination = seed(db)
    config, path = files
    write_image(path)
    first = calliope._insert_surfaces(db, **destination, projected=capture(config, destination, path))[0]
    write_image(path, b'revised')
    automatic = deliver(db, destination, config, path)[0]
    explicit = capture(config, destination, path, 'capture-2')
    upgraded = calliope._insert_surfaces(db, **destination, projected=explicit)[0]
    assert upgraded['id'] == automatic['id']
    assert upgraded['parent_surface_id'] == first['id']
    # Simulate a duplicate from an older build, then replay the explicit receipt.
    with db() as conn:
        conn.execute('''INSERT INTO rvbbit.calliope_surfaces
          (id,session_id,turn_id,ordinal,kind,title,tool_name,tool_call_id,lineage_key,payload,source,presentation)
          SELECT gen_random_uuid(),session_id,turn_id,3,'document','legacy copy',
          'hermes_local_file','legacy-file','legacy-lineage',payload,'{}','{}'
          FROM rvbbit.calliope_surfaces WHERE id=%s''', (upgraded['id'],))
    assert calliope._insert_surfaces(db, **destination, projected=explicit) == []
