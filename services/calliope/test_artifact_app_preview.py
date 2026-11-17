import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import artifact_app_preview as preview
from artifact_progress import public_progress


def wait():
    worker = preview._worker
    if worker: worker.join(5)
    assert preview._worker is None


def test_source_is_typed_bounded_and_separate_from_html():
    doc = {'schema': 'calliope.app.v1', 'data': {'state': {}}}
    event = public_progress({'build_id': 'app', 'phase': 'preview', 'kind': 'a2ui',
                             'render_app': doc, 'render_html': '<script>bad()</script>'})
    assert event['render_app'] == doc
    assert 'render_html' not in event
    assert 'render_app' not in public_progress({**event, 'render_app': {'schema': 'other'}})


def test_old_capture_directories_are_reaped_without_touching_other_artifacts(tmp_path, monkeypatch):
    import os
    root = tmp_path/'artifact-work'/'session'
    old = root/('app-'+'a'*64);old.mkdir(parents=True)
    fresh = root/('app-'+'b'*64);fresh.mkdir()
    html = root/'html-render';html.mkdir()
    os.utime(old, (1, 1));os.utime(html, (1, 1))
    monkeypatch.setattr(preview, '_last_cleanup', 0)
    preview.cleanup(SimpleNamespace(file_root=tmp_path))
    assert not old.exists()
    assert fresh.is_dir() and html.is_dir()


def test_coalescing_stale_frames_and_viewer_isolation(tmp_path, monkeypatch):
    wait()
    preview._states.clear(); preview._pending.clear()
    config = SimpleNamespace(file_root=tmp_path)
    session, item = str(uuid4()), {'key': 'turn.app', 'phase': 'preview'}
    entered, release = threading.Event(), threading.Event()
    seen = []
    def capture(doc, output, subject, theme):
        seen.append((doc, subject))
        if doc == 'first':
            entered.set(); assert release.wait(3)
        if doc == 'broken': raise ValueError('Invalid binding /missing')
        output.mkdir(parents=True)
        (output/'web-desktop.png').write_text(doc)
        return {'ready': True, 'captures': {'web-desktop': {'ready': True, 'preview': {'definition_sha256':doc}}}}
    monkeypatch.setattr(preview, 'capture', capture)
    preview.describe(config, session, item, 'first', 'alice', {})
    assert entered.wait(2)
    preview.describe(config, session, item, 'middle', 'alice', {})
    preview.describe(config, session, item, 'latest', 'alice', {})
    release.set(); wait()
    assert seen == [('first', 'alice'), ('latest', 'alice')]
    assert preview.image_path(config,session,item,'alice','web-desktop').read_text() == 'latest'
    assert not preview.image_path(config,session,item,'bob','web-desktop').exists()
    preview.describe(config, session, item, 'broken', 'alice', {}); wait()
    result = preview.describe(config, session, item, 'broken', 'alice', {})
    assert result['render_status'] == 'failed'
    assert result['app_previews']['web-desktop']['stale']
    assert preview.image_path(config,session,item,'alice','web-desktop').read_text() == 'latest'


def test_preview_origin_and_auth_are_checked_before_sql(doc=None):
    import asyncio
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    import ui_app_preview
    routes, calls = [], []
    class Registry:
        def custom_route(self, path, methods):
            def add(fn): routes.append(Route(path, fn, methods=methods)); return fn
            return add
    def actor(request):
        return ('alice', {'sub':'verified-alice'}, None) if request.headers.get('authorization') == 'test' else (None, None, JSONResponse({}, 401))
    with ThreadPoolExecutor(max_workers=1) as pool:
        subject = contextvars.ContextVar('preview-subject', default=None)
        def query(sql, **kwargs): calls.append(subject.get()); return {'rows':[{'x':'live'}]}
        server = SimpleNamespace(_SESSION_SUB=subject, tool_run_sql=query, _CONTROL_EXECUTOR=pool)
        ui_app_preview.register(Registry(), server, actor, lambda x,status=200:JSONResponse(x,status))
        async def run():
            doc = {'schema':'calliope.app.v1','data':{'state':{},'x':'initial'},'components':[{'id':'root','component':'CText','text':{'path':'/x'}}], 'queries':{'x':{'sql':"SELECT 'live'",'path':'/x','shape':'value'}}}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)),base_url='https://calliope.test') as client:
                body={'document':doc,'mode':'server-read'}
                assert (await client.post('/api/calliope/ui-app-preview',json=body)).status_code == 401
                assert (await client.post('/api/calliope/ui-app-preview',headers={'authorization':'test','origin':'https://evil.test'},json=body)).status_code == 403
                assert calls == []
                result = await client.post('/api/calliope/ui-app-preview',headers={'authorization':'test'},json=body)
                assert result.status_code == 200
                assert result.json()['messages'][2]['updateDataModel']['value']['x'] == 'live'
                assert calls == ['verified-alice']
        asyncio.run(run())
        assert subject.get() is None
