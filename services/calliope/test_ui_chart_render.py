"""Native chart transport: real geometry, bounded input and authenticated routes."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import ui_chart_render as charts


def body(width=360):
    return {'width': width, 'height': 260, 'spec': {
        'data': {'values': [{'job': 'Repair', 'count': 4}, {'job': 'Install', 'count': 2}]},
        'mark': {'type': 'bar', 'tooltip': True},
        'encoding': {'x': {'field': 'job', 'type': 'nominal'}, 'y': {'field': 'count', 'type': 'quantitative'}}}}


def test_actual_png_and_picking_at_two_viewports():
    pytest.importorskip('vl_convert')
    for width in (360, 1000):
        result = charts.render('chart-test', body(width))
        png = base64.b64decode(result['png'])
        assert int.from_bytes(png[16:20], 'big') == width*2
        assert len(result['scene']['hits']) == 2
        assert {h['datum']['job'] for h in result['scene']['hits']} == {'Repair', 'Install'}
        assert all(h['tooltip'] for h in result['scene']['hits'])
        assert charts.render('chart-test', body(width)) is result
        assert charts.render('another-actor', body(width)) is not result


@pytest.mark.parametrize('value', [0, 99999, True, '360', None])
def test_invalid_dimensions(value):
    with pytest.raises(ValueError, match='viewport'):
        charts.render('viewer', body(value))


def test_external_io_rejected_before_conversion():
    request = body()
    request['spec']['data'] = {'url': 'file:///etc/passwd'}
    with pytest.raises(ValueError, match='External'):
        charts.render('viewer', request)


def test_route_authorization_origin_and_body_limits(monkeypatch):
    routes, calls = [], []
    class Registry:
        def custom_route(self, path, methods):
            def add(fn):
                routes.append(Route(path, fn, methods=methods))
                return fn
            return add
    def authorize(request):
        if request.headers.get('authorization') != 'test':
            return None, None, JSONResponse({'error': 'unauthorized'}, 401)
        return 'viewer', {}, None
    def render(actor, request):
        calls.append(actor)
        return {'png': 'bounded output', 'scene': {}}
    monkeypatch.setattr(charts, 'render', render)
    with ThreadPoolExecutor(max_workers=2) as pool:
        charts.register(Registry(), SimpleNamespace(_CONTROL_EXECUTOR=pool), authorize,
                        lambda value, status=200: JSONResponse(value, status))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url='https://calliope.test') as client:
                path = '/api/calliope/ui-charts/render'
                assert (await client.post(path, json=body())).status_code == 401
                auth = {'authorization': 'test'}
                assert (await client.post(path, headers={**auth, 'origin': 'https://other.test'}, json=body())).status_code == 403
                assert (await client.post(path, headers={**auth, 'sec-fetch-site': 'cross-site'}, json=body())).status_code == 403
                assert (await client.post(path, headers={**auth, 'content-type': 'application/json'}, content=b' '*1_100_001)).status_code == 413
                assert not calls
                response = await client.post(path, headers=auth, json=body())
                assert response.status_code == 200
                assert response.headers['cache-control'] == 'no-store'
                assert calls == ['viewer']
        asyncio.run(check())
