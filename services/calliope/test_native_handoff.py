"""Native sign-in delegates identity to existing login, not incoming app URLs."""
from html import unescape
import re
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient
import auth
import native_handoff as handoff


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, 'JWT_SECRET', 'native-handoff-test-key-with-enough-entropy')
    monkeypatch.setattr(auth, 'ACCOUNTS', None)
    monkeypatch.delenv('WAREHOUSE_NATIVE_PUBLIC_URL', raising=False)
    routes = []
    class MCP:
        def custom_route(self, path, methods):
            def register(fn):
                routes.append(Route(path, fn, methods=methods)); return fn
            return register
    handoff.register(MCP(), SimpleNamespace(public='https://instance.test'))
    return TestClient(Starlette(routes=routes), base_url='https://instance.test', follow_redirects=False)


def sign_in(client, via='google', identity='tech@example.test'):
    response = Response()
    auth.set_session(response, identity, True, via=via)
    client.cookies.set(auth.SESSION_COOKIE, response.headers['set-cookie'].split(';')[0].split('=', 1)[1])


def begin(client):
    response = client.post('/auth/native/start', json={'device': 'Calliope Android'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    return response.json()


def approve(client, flow):
    page = client.get(flow['verification_path'])
    assert flow['device_secret'] not in page.text and flow['code'] in page.text
    assert page.headers['referrer-policy'] == 'same-origin'
    csrf = unescape(re.search(r'name="csrf" value="([^"]+)"', page.text)[1])
    return client.post(flow['verification_path'], data={'csrf': csrf, 'choice': 'approve'}, headers={'origin': 'https://instance.test'})


def test_google_identity_is_collected_once_and_never_in_callback(client):
    flow = begin(client)
    assert client.get(flow['verification_path']).headers['location'].startswith('/login?next=')
    sign_in(client)
    response = approve(client, flow)
    assert response.status_code == 200 and 'calliope://auth/complete' in response.text
    assert flow['device_secret'] not in response.text
    credentials = {k: flow[k] for k in ('request_id', 'device_secret')}
    assert client.post('/auth/native/poll', json={**credentials, 'device_secret': 'wrong'}).status_code == 400
    result = client.post('/auth/native/poll', json=credentials).json()
    assert result['status'] == 'approved' and result['email'] == 'tech@example.test'
    assert auth.jwt.decode(result['session_cookie'], auth.JWT_SECRET, algorithms=[auth.JWT_ALG])['via'] == 'google'
    assert client.post('/auth/native/poll', json=credentials).status_code == 400


def test_cross_site_confirmation_and_changed_browser_session_are_rejected(client):
    flow = begin(client); sign_in(client)
    page = client.get(flow['verification_path'])
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    body = {'csrf': csrf, 'choice': 'approve'}
    assert client.post(flow['verification_path'], data=body, headers={'origin': 'https://evil.test'}).status_code == 403
    sign_in(client, identity='someone-else@example.test')
    assert client.post(flow['verification_path'], data=body).status_code == 403


def test_revoked_account_cannot_collect_previously_approved_session(client, monkeypatch):
    flow = begin(client); sign_in(client); assert approve(client, flow).status_code == 200
    monkeypatch.setattr(auth, '_account_valid', lambda *args: False)
    response = client.post('/auth/native/poll', json=flow)
    assert response.status_code == 401 and 'session_cookie' not in response.text


def test_expiry_cancel_denial_and_bounded_pending_state():
    now = [100]
    pending = handoff.Pending(lambda: now[0])
    flow = pending.start('ip', 'Phone')
    assert pending.poll(flow['request_id'], flow['device_secret']) == {'status': 'pending'}
    pending.approve(flow['request_id'], {}, False)
    assert pending.poll(flow['request_id'], flow['device_secret']) == {'status': 'denied'}
    flow = pending.start('ip', 'Phone')
    assert pending.poll(flow['request_id'], flow['device_secret'], True) == {'status': 'cancelled'}
    with pytest.raises(LookupError): pending.approve(flow['request_id'], {}, True)
    flow = pending.start('ip', 'Phone'); now[0] += 601
    with pytest.raises(LookupError): pending.poll(flow['request_id'], flow['device_secret'])
    for _ in range(12): pending.start('ip', 'Phone')
    with pytest.raises(ValueError): pending.start('ip', 'Phone')


def test_public_connection_link_carries_only_navigation(client):
    response = client.get('/connect?app=visits&record=visit&id=A%202')
    assert response.status_code == 200 and 'calliope://open?' in response.text
    assert 'does not grant access' in response.text
    assert 'session_cookie' not in response.text and 'device_secret' not in response.text
    assert client.get('/connect/qr?app=visits').headers['content-type'] == 'image/png'
    assert client.get('/connect?app=../evil').status_code == 400
    assert client.get('/connect?record=visit&id=1').status_code == 400


def test_native_api_rejects_cross_origin_and_oversized_requests(client):
    assert client.post('/auth/native/start', json={}, headers={'origin': 'https://evil.test'}).status_code == 400
    assert client.post('/auth/native/start', content='x'*4097, headers={'content-type': 'application/json'}).status_code == 400
    assert client.post('/auth/native/start', json=[]).status_code == 400


def test_configured_lan_origin_survives_tls_proxy_scheme(client, monkeypatch):
    monkeypatch.setenv('WAREHOUSE_NATIVE_PUBLIC_URL', 'https://calliope.local')
    flow = begin(client); sign_in(client)
    page = client.get(flow['verification_path'])
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    response = client.post(flow['verification_path'], data={'csrf':csrf,'choice':'approve'},
        headers={'origin':'https://calliope.local','sec-fetch-site':'same-origin'})
    assert response.status_code == 200
