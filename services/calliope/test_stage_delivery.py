"""Live gateway receipts reach the real Stage insertion/reconciliation boundary."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import calliope
import stage_delivery
from test_generated_image_delivery import db, seed


def gateway_fixture():
    if not os.environ.get('HERMES_SOURCE_ROOT'):
        pytest.skip('requires the isolated patched Hermes source (also tested at image build)')
    path = Path(__file__).resolve().parents[2] / 'docker/calliope/test-hermes-stage-events.py'
    spec = importlib.util.spec_from_file_location('stage_gateway_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('specialist', [False, True])
def test_native_gateway_image_reaches_stage_before_completion_and_reconciles(db, tmp_path, specialist):
    gateway = gateway_fixture()
    destination = seed(db)
    path = tmp_path / 'image.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test',
                                   file_root=tmp_path/'stage', max_image_bytes=1024*1024, export_roots=(tmp_path,))
    spec = {'key': 'iris', 'run_id': str(uuid4()), 'thread_id': str(uuid4()), 'round': 1} if specialist else None
    with gateway.contextlib.closing(gateway.StageEvents()) as store:
        _, events, progress, complete = gateway.native_callbacks(store)
        progress('tool.result_ready', 'image_generate', args={}, tool_call_id='image-1',
                 result=json.dumps({'success': True, 'image': str(path)}))
        event = events[-1]
        assert event['event'] == 'tool.result_ready'
        first = calliope._insert_progressive_result_surfaces(
            db, config, 'owner@example.com', **destination, tool_name=event['tool'],
            result=event['result'], args=event['arguments'], tool_call_id=event['call_id'], specialist=spec)
        assert len(first) == 1
        assert first[0]['presentation']['phase'] == 'published'
        if spec:
            assert first[0]['source']['specialist_run_id'] == spec['run_id']
        with db() as conn:
            assert conn.execute('SELECT status FROM rvbbit.calliope_turns').fetchone()['status'] == 'running'
            assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 1
        # Ordered completion and full-transcript reconciliation repeat the same
        # evidence. Neither may publish another card or lose the frozen file.
        complete('image-1', 'image_generate', {}, json.dumps({'image': str(path)}))
        event = events[-1]
        assert calliope._insert_progressive_result_surfaces(
            db, config, 'owner@example.com', **destination, tool_name=event['tool'],
            result=event['result'], args=event['arguments'], tool_call_id=event['call_id'], specialist=spec) == []
        transcript = [{'role': 'tool', 'tool_name': 'image_generate', 'tool_call_id': 'image-1',
                       'content': json.dumps({'image': str(path)})}]
        final = calliope._publish_local_files([], transcript, '', config, **destination)
        assert calliope._insert_surfaces(db, **destination, projected=final) == []
        with db() as conn:
            row = conn.execute('SELECT payload FROM rvbbit.calliope_surfaces').fetchone()
            assert Path(row['payload']['storage_path']).read_bytes() == path.read_bytes()


@pytest.mark.asyncio
async def test_stream_reader_delivers_ready_before_run_end(monkeypatch, tmp_path):
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test', file_root=tmp_path, max_image_bytes=1024*1024)
    ready = {'event': 'tool.result_ready', 'tool': 'image_generate', 'call_id': 'image-1',
             'result': {'image': '/opt/data/cache/images/one.png'}, 'arguments': {}}
    async def transcript(*a, **kw): return []
    monkeypatch.setattr(calliope, '_hermes_messages_since', transcript)
    def handler(request):
        assert request.url.path.endswith('/events')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=(
            'event: tool.result_ready\ndata: ' + json.dumps(ready) + '\n\n'
            'event: run.completed\ndata: {"event":"run.completed"}\n\n'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = calliope._iter_hermes_run(client, config, None, None, 'session', existing_run_id='run-test')
        event, data = await anext(stream)
        assert event == 'tool.result_ready'
        assert data['call_id'] == 'image-1'
        assert data['result'] == ready['result']
        rest = [item async for item in stream]
        assert rest[-1][0] == 'run.completed'


@pytest.mark.parametrize('memory', [None, {'text': 'what did we decide?', 'audience': ['person:a@example.com']}])
def test_a_new_run_tells_hermes_what_memory_may_read_and_who_may_see_it(monkeypatch, tmp_path, memory):
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test', file_root=tmp_path, max_image_bytes=1024*1024)
    posted = []
    async def count(*a, **kw): return 0
    async def transcript(*a, **kw): return []
    monkeypatch.setattr(calliope, '_hermes_session_message_count', count)
    monkeypatch.setattr(calliope, '_hermes_messages_since', transcript)
    def handler(request):
        if request.method == 'POST':
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={'run_id': 'run-new'})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content='event: run.completed\ndata: {"event":"run.completed"}\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [e async for e in calliope._iter_hermes_run(
                client, config, None, None, 'session', message='hello', memory=memory, bind_run=lambda *a: None)]
    events = asyncio.run(run())
    assert events[-1][0] == 'run.completed'
    assert posted[0]['input'] == 'hello'
    assert ('calliope_memory' in posted[0]) is (memory is not None)
    if memory is not None:
        assert posted[0]['calliope_memory'] == memory


@pytest.mark.asyncio
@pytest.mark.parametrize('first_status', ['running', 'completed'])
async def test_disconnect_polling_drains_receipts_before_terminal_state(monkeypatch, tmp_path, first_status):
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test', file_root=tmp_path, max_image_bytes=1024*1024)
    cursors = []
    def ready(i):
        return {'event': 'tool.result_ready', 'tool': 'run_sql', 'call_id': str(i),
                'arguments': {'sql': 'SELECT 1'}, 'result': {'rows': [[1]]}, 'stage_sequence': i}
    def handler(request):
        if request.url.path.endswith('/events'):
            return httpx.Response(404)
        cursor = int(request.url.params['stage_after']); cursors.append(cursor)
        # Exercise both a still-running producer and terminal backfill paging.
        return httpx.Response(200, json={'status': first_status if cursor == 0 else 'completed',
                                       'stage_events': [ready(cursor + 1)], 'stage_cursor': cursor + 1,
                                       'stage_has_more': cursor == 0 and first_status == 'completed'})
    async def transcript(*a, **kw): return []
    monkeypatch.setattr(calliope, '_hermes_messages_since', transcript)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [e async for e in calliope._iter_hermes_run(
            client, config, None, None, 'session', existing_run_id='run-test')]
    assert cursors == [0, 1]
    assert [data['call_id'] for event, data in events if event == 'tool.result_ready'] == ['1', '2']
    assert events[-1][0] == 'run.completed'


def test_shared_stage_contract_is_used_by_both_services():
    assert calliope._PROGRESSIVE_TURN_TOOLS is stage_delivery.STAGE_TOOLS
    assert calliope._ARTIFACT_TOOLS is stage_delivery.ARTIFACT_TOOLS
    root = Path(__file__).resolve().parents[2]
    assert 'COPY services/calliope/stage_delivery.py /opt/hermes/stage_delivery.py' in (root/'docker/Dockerfile.calliope-hermes').read_text()


@pytest.mark.parametrize('kind,tool', [('image', 'generate_image'), ('chart', 'publish_chart')])
@pytest.mark.parametrize('specialist', [False, True])
def test_already_committed_publications_are_emitted_and_attributed_without_duplication(db, kind, tool, specialist):
    destination = seed(db)
    image_id = str(uuid4())
    published = calliope._insert_surfaces(db, **destination, projected=[{
        'kind': kind, 'title': 'Published', 'tool_name': tool, 'tool_call_id': 'committed-1',
        'lineage_key': 'published-1', 'payload': {'image_id': image_id},
        'source': {'origin': tool}, 'presentation': {'phase': 'published'},
    }])[0]
    result = ({'image_id': image_id, 'url': f'/api/calliope/generated-images/{image_id}'}
              if kind == 'image' else {'surface_id': published['id']})
    fields = stage_delivery.receipt_fields(tool, {}, result)
    spec = {'key': 'iris', 'run_id': str(uuid4()), 'thread_id': str(uuid4()), 'round': 1} if specialist else None
    emitted = calliope._insert_progressive_result_surfaces(
        db, None, 'owner@example.com', **destination, tool_name=tool, result=fields['result'],
        args=fields['arguments'], tool_call_id='native-1', specialist=spec)
    assert emitted[0]['id'] == published['id']
    if spec:
        assert emitted[0]['source']['specialist_run_id'] == spec['run_id']
    if kind == 'image':
        assert emitted[0]['source']['receipt_call_id'] == 'native-1'
        assert emitted[0]['source']['receipt_tool'] == tool
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 1
    other = seed(db)
    assert calliope._insert_progressive_result_surfaces(
        db, None, 'owner@example.com', **other, tool_name=tool, result=fields['result'],
        args=fields['arguments'], tool_call_id='native-1') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('reconnect', [False, True])
@pytest.mark.parametrize('image', [False, True])
async def test_build_progress_crosses_sse_and_status_replay_without_becoming_a_tool_result(monkeypatch, tmp_path, reconnect, image):
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test', file_root=tmp_path, max_image_bytes=1024)
    draft = {'build_id':'draft', 'phase':'composing', 'revision':1, 'updated_at':100, 'filename':'app.html', 'received_bytes':1500, 'source':'PRIVATE'}
    if image:
        draft.update(kind='image', phase='generating', call_id='image-one', prompt='Night sky')
    async def transcript(*a, **kw): return []
    monkeypatch.setattr(calliope, '_hermes_messages_since', transcript)
    def handler(request):
        if not request.url.path.endswith('/events'):
            return httpx.Response(200, json={'status':'completed','artifact_progress':[draft], 'stage_events':[]})
        if reconnect: return httpx.Response(404)
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=(
            'event: artifact.progress\ndata: '+json.dumps({'progress':draft})+'\n\n'
            'event: run.completed\ndata: {"event":"run.completed"}\n\n'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events=[e async for e in calliope._iter_hermes_run(client,config,None,None,'session',existing_run_id='run-test')]
    progress=[data['progress'] for event,data in events if event=='artifact.progress']
    assert len(progress)==1 and progress[0]['received_bytes']==1500
    if image:
        assert progress[0]['kind'] == 'image' and progress[0]['prompt'] == 'Night sky'
    assert 'source' not in progress[0]
    assert all(event!='tool.result_ready' for event,_ in events)
