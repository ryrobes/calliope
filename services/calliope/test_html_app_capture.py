import contextvars
import json
from types import SimpleNamespace

import pytest
import html_app_capture as capture
import ui_apps


def test_capture_reads_recheck_artifact_and_never_accept_events(monkeypatch):
    calls=[]
    server=SimpleNamespace(_artifact_require_view=lambda slug,**kw:calls.append((slug,kw)))
    monkeypatch.setattr(ui_apps,'snapshot',lambda *args:{'actor':args[2],'body':args[3]})
    assert capture.app_read(server,'tech','visits',{'record':{'id':'one'}})['actor']=='tech'
    assert calls==[('visits',{'caller_override':'tech'})]
    for payload in ({'event':'submit'},{'context':{}},{'request_key':'x'},{'drafts':[]},[]):
        with pytest.raises(ValueError):capture.app_read(server,'tech','visits',payload)
    def denied(*a,**kw):raise PermissionError()
    server._artifact_require_view=denied
    with pytest.raises(PermissionError):capture.app_read(server,'tech','dispatch',{})


def test_thumbnail_remains_viewer_owned_and_keeps_previous_on_failure(tmp_path,monkeypatch):
    import ui_app_captures
    actor=contextvars.ContextVar('capture-actor',default=None)
    server=SimpleNamespace(_SESSION_SUB=actor,_live_app_capture_root=lambda:tmp_path,
        _artifact_require_view=lambda *a,**kw:None,_capturable_dashboard_html=lambda *a:'<html></html>',
        _load_live_app_version=lambda slug:({}, {'version':2}),_THUMB_W=800,_THUMB_H=500,_THUMB_Q=72)
    path=ui_app_captures.thumbnail_path(server,'app','analysis','alice')
    path.parent.mkdir(parents=True);path.write_bytes(b'previous')
    def render(html,destination,**kwargs):
        assert actor.get()=='alice' and callable(kwargs['app_read_runner'])
        destination.write_bytes(b'new');return {'readiness':{'ready':False}}
    server._capture_html_with_playwright=render
    with pytest.raises(RuntimeError):capture.thumbnail(server,{'version':2},'app','analysis','alice')
    assert path.read_bytes()==b'previous' and actor.get() is None
    assert not ui_app_captures.thumbnail_path(server,'app','analysis','bob').exists()


def test_read_bridge_does_not_forward_credentials_or_enable_commands():
    routes={}
    page=SimpleNamespace(route=lambda glob,fn:routes.setdefault(glob,fn))
    def read(slug,body):
        if 'event' in body: raise ValueError('Command forbidden')
        return {'slug':slug,'body':body}
    capture.install(page,'http://127.0.0.1:8765/',read)
    handle=routes['http://127.0.0.1:8765/api/apps/*/ui']
    def request(method,payload):
        result={}
        route=SimpleNamespace(request=SimpleNamespace(method=method,url='http://127.0.0.1:8765/api/apps/visits/ui',post_data=json.dumps(payload)),fulfill=lambda **kw:result.update(kw))
        handle(route);return result
    assert request('OPTIONS',{})['status']==204
    assert request('POST',{})['status']==200
    assert request('POST',{'event':'submit'})['status']==403
    assert request('GET',{})['status']==403
