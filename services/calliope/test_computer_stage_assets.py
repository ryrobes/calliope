"""Exercise the real authenticated routes, not a browser fixture's file server."""
import sys
from types import SimpleNamespace

import calliope
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient


@pytest.mark.parametrize('name,media_type', [('computer-stage.js', 'text/javascript'), ('computer-stage.css', 'text/css')])
def test_desktop_preview_assets_are_registered_authenticated_and_versioned(monkeypatch, tmp_path, name, media_type):
    routes = []

    class MCP:
        def custom_route(self, path, methods):
            def register(handler):
                routes.append(Route(path, handler, methods=methods))
                return handler
            return register

    identity = {'identity': 'viewer@example.com', 'sub': 'viewer', 'mapped': True, 'via': 'password'}
    monkeypatch.setitem(sys.modules, 'auth', SimpleNamespace(
        read_session_full=lambda request: identity if request.headers.get('x-test-session') else None))
    monkeypatch.setattr(calliope, 'ensure_tables', lambda _: None)
    for module in (calliope.hermes_docs, calliope.hermes_config, calliope.provider_settings):
        monkeypatch.setattr(module, 'ensure', lambda _: None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL', 'http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY', 'test-key')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR', str(tmp_path))
    assert calliope.register_calliope_routes(MCP(), lambda: None, '', lambda document: document)
    with TestClient(Starlette(routes=routes)) as client:
        path = '/calliope/' + name
        assert client.get(path).status_code == 401
        response = client.get(path + '?v=' + calliope._ASSET_VERSION, headers={'x-test-session': 'yes'})
        assert response.status_code == 200
        assert response.headers['content-type'].startswith(media_type)
        assert 'immutable' in response.headers['cache-control']
        assert response.content == (calliope._ASSET_DIR / name).read_bytes()
        assert name in calliope._CACHE_VERSIONED_ASSETS
