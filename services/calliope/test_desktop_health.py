import asyncio
from types import SimpleNamespace

import httpx
import pytest

import desktop_health


def config(**changes):
    return SimpleNamespace(**dict({
        'desktop_debug_enabled': True, 'desktop_url': 'http://guest:9000',
        'desktop_control_key': 'private-control', 'desktop_vnc_basic': 'private-drive',
        'desktop_vnc_view_basic': 'private-view',
    }, **changes))


@pytest.mark.parametrize('changes,state', [
    ({'desktop_debug_enabled': False}, 'not_enabled'),
    ({'desktop_control_key': ''}, 'needs_setup'),
    ({'desktop_vnc_view_basic': ''}, 'needs_setup'),
])
def test_incomplete_or_disabled_never_contacts_desktop(monkeypatch, changes, state):
    def unexpected(**kwargs):
        pytest.fail('No connection should be attempted')
    monkeypatch.setattr(desktop_health.httpx, 'AsyncClient', unexpected)
    result = asyncio.run(desktop_health.inspect(config(**changes)))
    assert result['state'] == state
    assert 'private-' not in str(result)


@pytest.mark.parametrize('failure,message', [
    (None, 'Control API, display and authenticated viewer responded'),
    ('display', 'display is not ready'),
    ('control_auth', 'Control API rejected'),
    ('viewer_auth', 'viewer rejected'),
    ('timeout', 'connection'),
    ('invalid', 'valid health response'),
    ('not_ready', 'did not report readiness'),
    ('viewer_redirect', 'viewer could not be reached'),
])
def test_read_only_checks_require_real_readiness(monkeypatch, failure, message):
    client = httpx.AsyncClient
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == 'GET'
        if request.url.path == '/health':
            assert request.headers['X-Calliope-Desktop-Key'] == 'private-control'
            assert 'Authorization' not in request.headers
            if failure == 'control_auth': return httpx.Response(401)
            if failure == 'timeout': raise httpx.ConnectTimeout('secret URL', request=request)
            if failure == 'invalid': return httpx.Response(200, text='<html>Login</html>')
            return httpx.Response(200, json={'ok': failure != 'not_ready', 'vnc_ready': True,
                                           'display_ready': failure != 'display', 'secret': 'not-for-clients'})
        assert str(request.url) == 'https://viewer:6901/vnc.html'
        assert request.headers['Authorization'] == 'private-view'
        assert 'X-Calliope-Desktop-Key' not in request.headers
        if failure == 'viewer_auth': return httpx.Response(403)
        if failure == 'viewer_redirect': return httpx.Response(302, headers={'location': 'https://elsewhere/'})
        return httpx.Response(200, text='<html>Viewer</html>', headers={'content-type': 'text/html'})
    monkeypatch.setenv('CALLIOPE_DESKTOP_VNC_URL', 'https://viewer:6901/')
    monkeypatch.setattr(desktop_health.httpx, 'AsyncClient', lambda **kw:
                        client(**kw, transport=httpx.MockTransport(handle)))
    result = asyncio.run(desktop_health.inspect(config()))
    assert len(calls) == 2
    assert result['state'] == ('unavailable' if failure else 'reachable')
    assert message in result['detail']
    assert 'private-' not in str(result) and 'secret' not in str(result)
