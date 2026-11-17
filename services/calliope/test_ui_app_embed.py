"""Native embeds may share an origin only when serving the platform renderer."""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import ui_apps


def test_native_embed_authorizes_pins_version_and_never_serves_authored_html(monkeypatch):
    import meaning_graph
    monkeypatch.setattr(meaning_graph, 'register', lambda *args: None)
    monkeypatch.setattr(ui_apps, 'shell', lambda: '<main>Platform host</main>')
    monkeypatch.setattr(ui_apps, 'archive_assets', lambda: None)
    routes, loads = [], []
    class Registry:
        def custom_route(self, path, methods):
            def add(fn):
                routes.append(Route(path, fn, methods=methods))
                return fn
            return add
    def load(slug, version):
        loads.append((slug, version))
        return {}, {'kind': 'html'} if slug == 'html-app' else {'kind': 'a2ui'}
    # A truthy app and malicious stored HTML deliberately challenge the boundary.
    def loaded(slug, version):
        _, row = load(slug, version)
        return {'name': 'App'}, {**row, 'html': '<script>parent.evil()</script>'}
    def authorize(request):
        if request.headers.get('authorization') != 'test':
            return None, None, JSONResponse({'error': {'code': 'UNAUTHORIZED'}}, 401)
        if request.path_params['slug'] == 'private-app':
            return None, None, JSONResponse({'error': {'code': 'NOT_FOUND'}}, 404)
        return 'viewer', {}, None
    with ThreadPoolExecutor(max_workers=1) as pool:
        server = SimpleNamespace(_CONTROL_EXECUTOR=pool, _SESSION_SUB=contextvars.ContextVar('viewer'),
                                 _load_live_app_version=loaded)
        ui_apps.register(Registry(), server, authorize, lambda value, status=200: JSONResponse(value, status))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url='https://calliope.test') as client:
                assert (await client.get('/api/apps/test/embed/3')).status_code == 401
                assert (await client.get('/api/apps/private-app/embed/3', headers={'authorization': 'test'})).status_code == 404
                assert loads == []
                for version in ('0', '-1', 'nope'):
                    assert (await client.get('/api/apps/test/embed/' + version, headers={'authorization': 'test'})).status_code == 404
                assert (await client.get('/api/apps/html-app/embed/3', headers={'authorization': 'test'})).status_code == 404
                response = await client.get('/api/apps/test/embed/3', headers={'authorization': 'test'})
                assert response.status_code == 200 and response.text == '<main>Platform host</main>'
                assert loads[-1] == ('test', 3)
                assert "frame-ancestors 'self'" in response.headers['content-security-policy']
                assert 'no-store' in response.headers['cache-control']
                assert 'parent.evil' not in response.text
        asyncio.run(check())
