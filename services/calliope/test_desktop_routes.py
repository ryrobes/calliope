"""Exercise the ownership boundary and an established stream across handoff."""
import asyncio
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import desktop_routes as routes
from desktop_access import DesktopError


def test_stream_route_attaches_to_the_actual_starlette_application():
    from starlette.applications import Starlette
    gateway = SimpleNamespace(stream=lambda websocket: None)
    app = Starlette()
    routes.attach_websocket(app, SimpleNamespace(_calliope_computer_gateway=gateway))
    assert app.routes[-1].path == routes.BASE + '/stream'


def viewer_request(asset):
    return Request({'type': 'http', 'method': 'GET', 'headers': [],
                    'path': routes.BASE + '/viewer/' + asset, 'path_params': {'asset': asset}})


def test_disconnected_page_is_local_authenticated_and_not_cached(gateway, monkeypatch):
    monkeypatch.setattr(routes.httpx, 'AsyncClient', lambda **kw: pytest.fail('Must work with guest offline'))
    result = asyncio.run(gateway.viewer_asset(viewer_request('disconnected.html')))
    assert result.status_code == 200
    assert result.headers['content-type'].startswith('text/html')
    assert result.headers['cache-control'] == 'no-store'
    assert b'data-calliope-disconnected="true"' in result.body
    gateway.owner_for_request = lambda request: (None, None)
    with pytest.raises(DesktopError, match='Sign in'):
        asyncio.run(gateway.viewer_asset(viewer_request('disconnected.html')))


@pytest.mark.parametrize('asset', ['missing.html', '../disconnected.html', '/disconnected.html'])
def test_unknown_viewer_assets_still_rejected(gateway, asset):
    with pytest.raises(DesktopError, match='Unknown viewer asset'):
        asyncio.run(gateway.viewer_asset(viewer_request(asset)))


def test_viewer_adapter_applied_and_cached_bundle_invalidated(gateway, monkeypatch):
    import desktop_viewer as adapter
    bodies = {'vnc.html': b'<head><script src="./assets/ui-D357AMxM.js"></script></head>',
              'assets/ui-D357AMxM.js': adapter.IDLE_GUARD, 'assets/other.js': b'unmodified'}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            assert headers['Authorization'] == gateway.config.desktop_vnc_view_basic
            return routes.httpx.Response(200, content=bodies[url.removeprefix(gateway.vnc_base + '/')])
    monkeypatch.setattr(routes.httpx, 'AsyncClient', Client)
    def get(asset): return asyncio.run(gateway.viewer_asset(viewer_request(asset)))
    html = get('vnc.html')
    assert b'ui-D357AMxM.js?adapter=' + adapter.REVISION in html.body
    assert routes.VIEWER_SHELL in html.body
    assert html.headers['cache-control'] == 'no-store'
    assert get('assets/ui-D357AMxM.js').body == adapter.OWNED_GUARD
    assert get('assets/other.js').body == b'unmodified'
    bodies['assets/ui-D357AMxM.js'] = b'changed upstream watchdog'
    with pytest.raises(DesktopError, match='compatibility update'):
        get('assets/ui-D357AMxM.js')
    bodies['vnc.html'] = b'<head></head>'
    with pytest.raises(DesktopError, match='compatibility update'):
        get('vnc.html')


@pytest.fixture
def gateway(monkeypatch):
    config = SimpleNamespace(desktop_url='http://guest:9000', desktop_enabled=True,
                             desktop_vnc_basic='Basic ZnVsbA==', desktop_vnc_view_basic='Basic dmlldw==')
    result = routes.ComputerGateway(None, config, lambda request: ('person@example.com', {'mapped': True}))
    result.snapshot = {'mode': 'human', 'human_actor': 'person@example.com', 'generation': 4,
                       'active_run': 'run', 'runs': {'run': {'id': 'run'}}, 'queue': [], 'jobs': {},
                       'recording': {'recording': True}}
    result.calls = []

    async def runtime(method, path, payload=None):
        result.calls.append((method, path, payload))
        if path == 'control':
            result.snapshot.update(mode=payload['mode'], human_actor=payload['actor'] if payload['mode'] == 'human' else None,
                                   generation=result.snapshot['generation'] + 1)
        return deepcopy(result.snapshot)

    result.runtime = runtime
    monkeypatch.setattr(routes.access, 'sync_runs', lambda *args: None)
    monkeypatch.setattr(routes.access, 'append_event', lambda *args: None)
    monkeypatch.setattr(routes.access, 'ready_message', lambda *args: None)
    monkeypatch.setattr(routes.desktop_agents, 'active_agent', lambda *args: None)
    return result


def test_status_uses_the_active_run_agent_not_the_viewing_chat(gateway, monkeypatch):
    calls = []
    def active_agent(factory, rid):
        calls.append(rid)
        return {'kind': 'specialist', 'display_name': 'Cassandra'}
    monkeypatch.setattr(routes.desktop_agents, 'active_agent', active_agent)
    state = asyncio.run(gateway.state('viewer@example.com'))
    assert state['active_agent']['display_name'] == 'Cassandra'
    assert calls == ['run']


def request(headers):
    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers':
                    [(key.encode(), value.encode()) for key, value in headers.items()]})


def test_gateway_requires_mapped_identity_and_same_origin_mutation(gateway):
    valid = {'host': 'callie.example', 'origin': 'https://callie.example', 'x-calliope-computer': '1'}
    assert gateway.owner(request(valid), mutation=True) == 'person@example.com'
    with pytest.raises(DesktopError, match='installation'):
        gateway.owner(request({**valid, 'origin': 'https://elsewhere.example'}), mutation=True)
    with pytest.raises(DesktopError, match='Reload'):
        gateway.owner(request({'host': 'callie.example'}), mutation=True)
    with pytest.raises(DesktopError, match='origin'):
        gateway.owner(request({'host': 'callie.example'}), websocket=True)
    gateway.owner_for_request = lambda request: ('person@example.com', {'mapped': False})
    with pytest.raises(DesktopError, match='Sign in'):
        gateway.owner(request(valid))


def test_terminal_route_checks_run_participation_and_job_ownership(gateway, monkeypatch, tmp_path):
    rid, jid, sid = [str(uuid.uuid4()) for _ in range(3)]
    gateway.config.file_root = tmp_path
    observed = []
    def authorize(*args):
        observed.append(args[1:])
        return {'id': rid, 'status': 'active'}
    monkeypatch.setattr(routes.access, 'run_for_session', authorize)
    class Runtime:
        def __init__(self, config): pass
        def request(self, method, path):
            return {'run_id': str(uuid.uuid4()), 'terminal': {}}
    monkeypatch.setattr(routes, 'DesktopRuntime', Runtime)
    req = Request({'type':'http','method':'GET','path':routes.BASE+f'/runs/{rid}/jobs/{jid}/terminal',
                   'query_string':('session_id='+sid).encode(),'headers':[]})
    with pytest.raises(DesktopError, match='does not belong'):
        asyncio.run(gateway.run_evidence(req, 'person@example.com'))
    assert observed == [(sid, 'person@example.com', rid)]


def test_archived_terminal_replays_with_guest_offline(gateway, monkeypatch, tmp_path):
    rid, jid, sid = [str(uuid.uuid4()) for _ in range(3)]
    gateway.config.file_root = tmp_path
    monkeypatch.setattr(routes.access, 'run_for_session', lambda *args: {'id':rid,'status':'finished'})
    folder = tmp_path / 'computer-evidence' / rid / 'jobs' / jid
    folder.mkdir(parents=True)
    (folder/'archived.json').write_text(json.dumps({'terminal':{'format':'asciicast-v2'},'status':'completed','intent':'Check'}))
    (folder/'code.sh').write_text('echo checked')
    (folder/'terminal.cast').write_text('{"version":2}\n[0,"o","checked"]\n')
    monkeypatch.setattr(routes, 'DesktopRuntime', lambda *args: pytest.fail('Archive must not contact the guest'))
    req = Request({'type':'http','method':'GET','path':routes.BASE+f'/runs/{rid}/jobs/{jid}/terminal',
                   'query_string':('session_id='+sid).encode(),'headers':[]})
    result = asyncio.run(gateway.run_evidence(req, 'person@example.com'))
    value = json.loads(result.body)
    assert value['complete'] and value['events'][-1] == [0,'o','checked']


def test_partial_terminal_archive_survives_guest_outage(gateway, monkeypatch, tmp_path):
    rid, jid, sid = [str(uuid.uuid4()) for _ in range(3)]
    gateway.config.file_root = tmp_path
    monkeypatch.setattr(routes.access, 'run_for_session', lambda *args: {'id': rid, 'status': 'active'})
    folder = tmp_path / 'computer-evidence' / rid / 'jobs' / jid
    folder.mkdir(parents=True)
    (folder / 'terminal-state.json').write_text(json.dumps({'id': jid, 'run_id': rid,
        'status': 'running', 'intent': 'Install app', 'code': 'sudo -n apt-get install tree'}))
    (folder / 'terminal.cast').write_text('{"version":2}\n[0,"o","installing"]\n')
    class Offline:
        def __init__(self, config): pass
        def request(self, *args): raise ConnectionError('Guest unavailable')
    monkeypatch.setattr(routes, 'DesktopRuntime', Offline)
    req = Request({'type': 'http', 'method': 'GET',
        'path': routes.BASE + f'/runs/{rid}/jobs/{jid}/terminal',
        'query_string': ('session_id=' + sid).encode(), 'headers': []})
    value = json.loads(asyncio.run(gateway.run_evidence(req, 'person@example.com')).body)
    assert value['complete'] and value['offline']
    assert value['events'][-1] == [0, 'o', 'installing']
    assert value['intent'] == 'Install app'


def test_run_history_paginates_after_authorizing_thread(gateway,monkeypatch):
    sid=str(uuid.uuid4());calls=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def execute(self,sql,params):
            calls.append(params)
            return self
        def fetchall(self):return [{'id':str(uuid.uuid4()),'status':'finished'} for _ in range(31)]
    gateway.conn_factory=Connection
    def authorize(conn,session,actor):calls.append((session,actor))
    monkeypatch.setattr(routes.calliope_access,'require_participant',authorize)
    req=Request({'type':'http','method':'GET','path':routes.BASE+'/runs',
                 'query_string':('session_id='+sid+'&offset=30').encode(),'headers':[]})
    result=asyncio.run(gateway.route(req))
    value=json.loads(result.body)
    assert len(value['runs'])==30 and value['next_offset']==60
    assert calls==[(sid,'person@example.com'),(sid,30)]


def test_thumbnail_requires_run_access_before_reading_pixels(gateway,monkeypatch):
    import desktop_replay
    rid,sid=[str(uuid.uuid4()) for _ in range(2)]
    calls=[]
    def authorize(*args):
        calls.append('authorize')
        assert args[1:]==(sid,'person@example.com',rid)
        return {'id':rid,'status':'finished'}
    monkeypatch.setattr(routes.access,'run_for_session',authorize)
    def image(*args):
        calls.append('image')
        return b'jpeg'
    monkeypatch.setattr(desktop_replay,'thumbnail',image)
    req=Request({'type':'http','method':'GET','path':routes.BASE+f'/runs/{rid}/thumbnail.jpg',
                 'query_string':('session_id='+sid).encode(),'headers':[]})
    result=asyncio.run(gateway.run_evidence(req,'person@example.com'))
    assert calls==['authorize','image'] and result.body==b'jpeg'
    assert result.headers['content-type']=='image/jpeg'
    assert result.headers['cache-control']=='private, no-store'
    def denied(*args):raise DesktopError('FORBIDDEN','No access',403)
    monkeypatch.setattr(routes.access,'run_for_session',denied)
    calls.clear()
    with pytest.raises(DesktopError,match='No access'):
        asyncio.run(gateway.run_evidence(req,'person@example.com'))
    assert calls==[]


class Upstream:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.queue = asyncio.Queue()

    async def send(self, value):
        assert not self.closed
        self.sent.append(value)

    async def close(self):
        self.closed = True
        await self.queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return value


class Socket:
    headers = {'host': 'callie.example', 'origin': 'https://callie.example'}
    query_params = {}

    def __init__(self):
        self.accepted = asyncio.Event()
        self.queue = asyncio.Queue()
        self.closed = False

    async def accept(self, **kwargs):
        self.accepted.set()

    async def receive(self):
        return await self.queue.get()

    async def close(self, **kwargs):
        self.closed = True
        await self.queue.put({'type': 'websocket.disconnect'})


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


def test_browser_disconnect_during_cleanup_is_normal(gateway, monkeypatch):
    async def scenario():
        class DisconnectedSocket(Socket):
            async def close(self, **kwargs):
                raise routes.WebSocketDisconnect(code=1006)
        socket, upstream = DisconnectedSocket(), Upstream()
        async def connect(*args, **kwargs): return upstream
        monkeypatch.setattr(routes, 'connect', connect)
        running = asyncio.create_task(gateway.stream(socket))
        await asyncio.wait_for(socket.accepted.wait(), 2)
        await socket.queue.put({'type': 'websocket.disconnect'})
        await asyncio.wait_for(running, 2)
        assert upstream.closed and not gateway.viewers
    asyncio.run(scenario())


def test_handoff_revokes_existing_control_socket_before_agent_admission(gateway, monkeypatch):
    async def scenario():
        socket, upstream = Socket(), Upstream()
        credentials = []

        async def connect(*args, **kwargs):
            credentials.append(kwargs['additional_headers']['Authorization'])
            assert kwargs['ping_interval'] is None  # Kasm does not send pongs.
            return upstream

        monkeypatch.setattr(routes, 'connect', connect)
        running = asyncio.create_task(gateway.stream(socket))
        await asyncio.wait_for(socket.accepted.wait(), 2)
        assert credentials == ['Basic ZnVsbA==']
        # Handshake bytes must not be logged as gestures. After framebuffer
        # request, retain both click and immediate button release.
        frames = [b'RFB 003.008\n', bytes([3]) + bytes(9), bytes([5, 1, 0, 12, 0, 24]),
                  bytes([5, 0, 0, 12, 0, 24]), bytes([4, 1, 0, 0, 0, 0, 0, 65])]
        for frame in frames:
            await socket.queue.put({'type': 'websocket.receive', 'bytes': frame})
        await eventually(lambda: len(upstream.sent) == len(frames))
        inputs = [call[2]['event'] for call in gateway.calls if call[1] == 'human-input']
        assert [item['buttons'] for item in inputs if item['kind'] == 'pointer'] == [1, 0]
        assert inputs[-1] == {'kind': 'key', 'down': True, 'value_omitted': True}
        original = gateway.runtime

        async def assert_closed(method, path, payload=None):
            if path == 'control' and payload['mode'] == 'agent':
                assert socket.closed and upstream.closed
            return await original(method, path, payload)

        gateway.runtime = assert_closed
        result = await gateway.change_control('person@example.com', {'mode': 'agent', 'generation': 4})
        assert result['mode'] == 'agent'
        await asyncio.wait_for(running, 2)
        assert not gateway.viewers

    asyncio.run(scenario())


def test_other_viewer_receives_only_view_credential_and_generation_closes_stream(gateway, monkeypatch):
    async def scenario():
        socket, upstream = Socket(), Upstream()
        gateway.owner_for_request = lambda request: ('other@example.com', {'mapped': True})

        async def connect(*args, **kwargs):
            assert kwargs['additional_headers']['Authorization'] == 'Basic dmlldw=='
            return upstream

        monkeypatch.setattr(routes, 'connect', connect)
        running = asyncio.create_task(gateway.stream(socket))
        await asyncio.wait_for(socket.accepted.wait(), 2)
        gateway.snapshot['generation'] += 1
        await asyncio.wait_for(running, 2)
        assert socket.closed and upstream.closed

    asyncio.run(scenario())


def test_stale_control_does_not_stop_a_new_run(gateway):
    with pytest.raises(DesktopError, match='Control changed'):
        asyncio.run(gateway.change_control('person@example.com', {'mode': 'finish', 'generation': 3}))
    assert all(method != 'POST' for method, _, _ in gateway.calls)


def test_small_view_is_read_only_even_for_the_current_human_driver(gateway, monkeypatch):
    async def scenario():
        socket, upstream = Socket(), Upstream()
        socket.query_params = {'view_only': '1'}

        async def connect(*args, **kwargs):
            assert kwargs['additional_headers']['Authorization'] == 'Basic dmlldw=='
            return upstream

        monkeypatch.setattr(routes, 'connect', connect)
        running = asyncio.create_task(gateway.stream(socket))
        await asyncio.wait_for(socket.accepted.wait(), 2)
        for frame in [bytes([3]) + bytes(9), bytes([5, 1, 0, 12, 0, 24])]:
            await socket.queue.put({'type': 'websocket.receive', 'bytes': frame})
        await eventually(lambda: len(upstream.sent) == 2)
        assert not [call for call in gateway.calls if call[1] in {'human-input', 'control'}]
        assert gateway.snapshot['mode'] == 'human'
        await socket.close()
        await asyncio.wait_for(running, 2)

    asyncio.run(scenario())


def test_expired_recording_never_falls_back_to_guest_even_if_local_file_remains(gateway, tmp_path, monkeypatch):
    import json
    import uuid
    rid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    gateway.config.file_root = tmp_path
    folder = tmp_path / 'computer-evidence' / rid
    (folder / 'video').mkdir(parents=True)
    (folder / 'manifest.json').write_text(json.dumps({'expired_at': '2026-09-24', 'segments': []}))
    (folder / 'video/000000.mp4').write_bytes(b'cleanup was interrupted')
    monkeypatch.setattr(routes.access, 'run_for_session', lambda *args: {'id': rid, 'status': 'finished'})
    req = Request({'type': 'http', 'method': 'GET', 'path': routes.BASE + f'/runs/{rid}/video/000000.mp4',
                   'query_string': f'session_id={sid}'.encode(), 'headers': []})
    with pytest.raises(DesktopError) as error:
        asyncio.run(gateway.run_evidence(req, 'person@example.com'))
    assert error.value.status == 410


def test_live_explanation_is_fenced_to_current_human_and_generation(gateway, monkeypatch):
    import json
    saved = []
    monkeypatch.setattr(routes.desktop_memory, 'save_feedback', lambda *args: saved.append(args) or {'queued': True})
    async def submit(generation):
        body = json.dumps({'human_generation': generation, 'run_id': 'run', 'feedback': 'Use export date.'}).encode()
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        req = Request({'type': 'http', 'method': 'POST', 'path': routes.BASE + '/feedback',
                       'headers': [(b'host', b'callie.example'), (b'x-calliope-computer', b'1')]}, receive)
        return await gateway.route(req)
    assert asyncio.run(submit(3)).status_code == 409
    assert asyncio.run(submit(True)).status_code == 409
    gateway.snapshot['human_actor'] = 'another@example.com'
    assert asyncio.run(submit(4)).status_code == 409
    assert not saved
    gateway.snapshot['human_actor'] = 'person@example.com'
    assert asyncio.run(submit(4)).status_code == 202
    assert saved[0][2] == 'person@example.com' and saved[0][-1] == 4


def test_takeover_receipt_pins_the_admitted_generation(gateway, monkeypatch):
    receipts = []
    monkeypatch.setattr(routes.access, 'append_event', lambda *args: receipts.append(args))
    result = asyncio.run(gateway.change_control('person@example.com', {'mode': 'human', 'generation': 4}))
    assert result['generation'] == 6
    assert receipts[0][4] == {'mode': 'human', 'generation': 6}


def test_replay_manifest_and_video_use_owned_archive_with_guest_offline(gateway, monkeypatch, tmp_path):
    rid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    gateway.config.file_root = tmp_path
    checked = []
    def authorize(*args):
        checked.append(args[1:])
        return {'id':rid,'status':'finished'}
    monkeypatch.setattr(routes.access, 'run_for_session', authorize)
    monkeypatch.setattr(routes, 'DesktopRuntime', lambda *_: pytest.fail('Replay must not contact guest'))
    folder = tmp_path/'computer-evidence'/rid
    (folder/'video').mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'segments':[]}))
    (folder/'video/replay.mp4').write_bytes(b'video')
    metadata = {'status':'ready','file':'replay.mp4','timeline':[]}
    (folder/'replay.json').write_text(json.dumps(metadata))
    def get(tail):
        req = Request({'type':'http','method':'GET','path':routes.BASE+f'/runs/{rid}/{tail}',
                       'query_string':('session_id='+sid).encode(),'headers':[]})
        return asyncio.run(gateway.run_evidence(req, 'person@example.com'))
    assert json.loads(get('manifest').body)['replay'] == metadata
    assert get('video/replay.mp4').path == folder/'video/replay.mp4'
    assert checked == [(sid,'person@example.com',rid)]*2
    (folder/'manifest.json').write_text(json.dumps({'expired_at':'now','segments':[]}))
    assert 'replay' not in json.loads(get('manifest').body)
    with pytest.raises(DesktopError) as error:
        get('video/replay.mp4')
    assert error.value.status == 410
