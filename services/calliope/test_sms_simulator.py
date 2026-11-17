"""Virtual transport with real SMS queues and ownership; never contact Twilio."""
import base64
import json
from types import SimpleNamespace

import pytest
import sms
import sms_simulator as sim
from test_sms import database, schema, env, new_session, OWNER, OTHER, bridge, bind, ingest

CONFIG = SimpleNamespace(max_image_bytes=1024)
PHOTO = {'name': 'plate.png', 'data_url': 'data:image/png;base64,' + base64.b64encode(b'fixture-image').decode()}


@pytest.fixture
def demo(env, monkeypatch):
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_SMS_SIMULATOR_ENABLED', '1')
    with env() as conn:
        conn.execute('DELETE FROM rvbbit.calliope_sms_connection')
    return env


def test_simulator_photo_turn_and_reply_without_twilio(demo):
    factory = demo
    ident = sim.create(factory, OWNER)['id']
    payload = {'client_id': sms.uid(), 'message': '', 'attachments': [PHOTO]}
    sim.enqueue(factory, CONFIG, OWNER, ident, payload)
    assert sim.enqueue(factory, CONFIG, OWNER, ident, payload)['state'] == 'queued'
    item = sms.claim(factory)
    def no_transport(*args):
        pytest.fail('Simulator called a real transport operation')
    prepared = sms.prepare_incoming(factory, CONFIG, item, new_session, no_transport)
    assert prepared['owner'] == OWNER and prepared['body']['attachments'] == [PHOTO]
    assert 'attached image' in prepared['body']['message']
    assert 'live SMS conversation' in sms.prompt_context(factory, prepared['session_id'], OWNER)
    turn = sms.uid()
    with factory() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_turns(id,session_id,client_turn_id,status,assistant_message) VALUES(%s,%s,%s,%s,%s)',
                     (turn, prepared['session_id'], item['id'], 'complete', 'Please send a clearer plate photo. [[stage:test]]'))
    sms.finish_incoming(factory, item, turn)
    def formatter(action, data):
        assert action == 'format_reply' and not any(data['account'].values())
        assert data['text'] == 'Please send a clearer plate photo.'
        return {'chunks': [data['text']]}
    assert sms.prepare_reply(factory, formatter)
    assert sms.send_one(factory, no_transport)
    assert not sms.send_one(factory, no_transport)
    data = sim.read(factory, OWNER, ident)
    assert len(data['messages']) == 2 and data['session_id'] == __import__('uuid').UUID(prepared['session_id'])
    assert data['messages'][0]['state'] == 'delivered' and data['messages'][1]['body'].startswith('Please send')
    assert sim.image(factory, OWNER, ident, payload['client_id'], 0) == (b'fixture-image', 'image/png')
    with factory() as conn:
        assert conn.execute('SELECT result FROM rvbbit.calliope_sms_outbox').fetchone()['result']['transport'] == 'simulator'
    # Another text resumes the exact same conversation.
    sim.enqueue(factory, CONFIG, OWNER, ident, {'client_id': sms.uid(), 'message': 'Here is the better one.'})
    second = sms.prepare_incoming(factory, CONFIG, sms.claim(factory), new_session, no_transport)
    assert second['session_id'] == prepared['session_id']


def test_access_disable_and_transport_boundary(demo, monkeypatch):
    ident = sim.create(demo, OWNER)['id']
    assert sim.conversations(demo, OTHER)['conversations'] == []
    for operation in [lambda: sim.read(demo, OTHER, ident),
                      lambda: sim.enqueue(demo, CONFIG, OTHER, ident, {'client_id': sms.uid(), 'message': 'spoof'}),
                      lambda: sim.image(demo, OTHER, ident, sms.uid(), 0)]:
        with pytest.raises(LookupError): operation()
    bind(demo)
    with demo() as conn:
        real = str(conn.execute("SELECT id FROM rvbbit.calliope_sms_bindings WHERE transport='twilio'").fetchone()['id'])
    with pytest.raises(LookupError): sim.enqueue(demo, CONFIG, OWNER, real, {'client_id': sms.uid(), 'message': 'no carrier send'})
    sim.enqueue(demo, CONFIG, OWNER, ident, {'client_id': sms.uid(), 'message': 'pending'})
    monkeypatch.delenv('WAREHOUSE_CALLIOPE_SMS_SIMULATOR_ENABLED')
    with pytest.raises(LookupError): sim.read(demo, OWNER, ident)
    assert sms.claim(demo) is None


def test_validation_retry_and_distinct_new_conversations(demo):
    a, b = sim.create(demo, OWNER)['id'], sim.create(demo, OWNER)['id']
    body = {'client_id': sms.uid(), 'message': 'Original'}
    sim.enqueue(demo, CONFIG, OWNER, a, body)
    with pytest.raises(ValueError): sim.enqueue(demo, CONFIG, OWNER, b, body)
    with pytest.raises(ValueError): sim.enqueue(demo, CONFIG, OWNER, a, {**body, 'message': 'Changed'})
    for attachments in [[PHOTO] * 5, [{'name': 'bad', 'data_url': 'https://example.com/file'}],
                        [{'name': 'bad', 'data_url': 'data:text/html;base64,aGk='}],
                        [{'name': 'huge.png', 'data_url': 'data:image/png;base64,' + base64.b64encode(b'x' * 1025).decode()}]]:
        with pytest.raises(ValueError): sim.enqueue(demo, CONFIG, OWNER, a, {'client_id': sms.uid(), 'attachments': attachments})
    first = sms.prepare_incoming(demo, CONFIG, sms.claim(demo), new_session)
    sim.enqueue(demo, CONFIG, OWNER, b, {'client_id': sms.uid(), 'message': 'New conversation'})
    second = sms.prepare_incoming(demo, CONFIG, sms.claim(demo), new_session)
    assert first['session_id'] != second['session_id']


def test_routes_require_login_flag_and_browser_submission(demo):
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient
    routes = []
    class Routes:
        def custom_route(self, path, methods):
            def add(fn): routes.append(Route(path, fn, methods=methods)); return fn
            return add
    def owner(request):
        return (request.headers['x-test-owner'], None) if request.headers.get('x-test-owner') else (None, JSONResponse({}, 401))
    sim.register(Routes(), demo, owner, lambda value, status=200: JSONResponse(json.loads(sms.dump(value)), status_code=status), CONFIG)
    with TestClient(Starlette(routes=routes)) as client:
        assert client.get(sim.BASE).status_code == 401
        headers = {'x-test-owner': OWNER, 'x-calliope-simulator': '1'}
        assert client.get(sim.PAGE, headers=headers).status_code == 200
        assert client.post(sim.BASE + '/conversations', json={}, headers={'x-test-owner': OWNER}).status_code == 403
        ident = client.post(sim.BASE + '/conversations', json={'owner_email': OTHER}, headers=headers).json()['id']
        assert sim.read(demo, OWNER, ident)['owner'] == OWNER  # supplied identity is never trusted
        url = sim.BASE + '/conversations/' + ident
        payload = {'client_id': sms.uid(), 'attachments': [PHOTO]}
        assert client.post(url + '/messages', json=payload, headers=headers).status_code == 200
        image_url = client.get(url, headers=headers).json()['messages'][0]['images'][0]
        assert client.get(image_url, headers=headers).content == b'fixture-image'
        assert client.get(image_url, headers={'x-test-owner': OTHER}).status_code == 404
        assert client.get(image_url).status_code == 401
