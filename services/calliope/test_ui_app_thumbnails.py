"""Company thumbnails use inert renders, caller data, and the existing viewer wall."""
import asyncio
import contextvars
from datetime import datetime, timezone
from types import SimpleNamespace

from PIL import Image
import pytest
from starlette.requests import Request

import auth
import server
import ui_app_captures as captures


@pytest.fixture
def host(tmp_path):
    return SimpleNamespace(_live_app_capture_root=lambda:tmp_path,
        _SESSION_SUB=contextvars.ContextVar('thumbnail-actor',default=None),
        _artifact_require_view=lambda *a,**k:None,
        _load_live_app_version=lambda slug:({'runtime_kind':'a2ui'},{'version':2}),
        _THUMB_W=800,_THUMB_H=500,_THUMB_Q=72)


@pytest.mark.parametrize('ready',[False,True])
def test_thumbnail_preserves_good_image_on_render_failure(host,monkeypatch,ready):
    path=captures.thumbnail_path(host,'app','visits','alice@example.test')
    path.parent.mkdir(parents=True);path.write_bytes(b'previous-good')
    def render(s,row,output,width,height):
        assert s._SESSION_SUB.get()=='alice@example.test'
        assert row['version']==2 and (width,height)==(1280,800)
        Image.new('RGB',(width,height),'navy').save(output)
        return {'ready':ready}
    monkeypatch.setattr(captures,'published',render)
    if ready:
        captures.thumbnail(host,{'version':2},'app','visits','alice@example.test')
        with Image.open(path) as image:assert image.size==(800,500) and image.format=='JPEG'
    else:
        with pytest.raises(RuntimeError,match='incomplete'):
            captures.thumbnail(host,{'version':2},'app','visits','alice@example.test')
        assert path.read_bytes()==b'previous-good'
    assert host._SESSION_SUB.get() is None
    assert not list(path.parent.glob('*.tmp.*'))


@pytest.mark.parametrize('change',['publication','revoked'])
def test_thumbnail_does_not_commit_after_version_or_access_change(host,monkeypatch,change):
    count=0
    def authorize(*args,**kw):
        nonlocal count
        count+=1
        if change=='revoked' and count==2:raise PermissionError('Revoked')
    host._artifact_require_view=authorize
    if change=='publication':host._load_live_app_version=lambda s:({'runtime_kind':'a2ui'},{'version':3})
    def render(s,row,output,*size):
        Image.new('RGB',(1280,800),'navy').save(output);return {'ready':True}
    monkeypatch.setattr(captures,'published',render)
    if change=='revoked':
        with pytest.raises(PermissionError):captures.thumbnail(host,{'version':2},'app','visits','alice')
    else:captures.thumbnail(host,{'version':2},'app','visits','alice')
    assert not captures.thumbnail_path(host,'app','visits','alice').exists()
    assert count==2 and host._SESSION_SUB.get() is None


def test_gallery_warms_native_and_html_thumbnails_without_cross_viewer_reuse(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'_live_app_capture_root',lambda:tmp_path)
    queued=[];monkeypatch.setattr(server,'_auto_thumb',lambda *a,**kw:queued.append((a,kw)))
    now=datetime.now(timezone.utc)
    rows=[{'slug':'visits','runtime_kind':'a2ui','app_kind':'app','updated_at':now},
          {'slug':'analysis','runtime_kind':'html','app_kind':'dashboard','updated_at':now}]
    server._warm_thumbs(rows,'alice')
    assert queued==[(('app','visits'),{'actor':'alice'}),(('dashboard','analysis'),{'actor':'alice'})]
    alice=captures.thumbnail_path(server,'app','visits','alice');alice.parent.mkdir(parents=True);alice.write_bytes(b'alice')
    assert not captures.thumbnail_stale(server,'app','visits','alice',now)
    assert captures.thumbnail_stale(server,'app','visits','bob',now)
    with pytest.raises(ValueError):captures.thumbnail_path(server,'app','visits',None)


def test_background_capture_dispatches_native_with_viewer(monkeypatch):
    monkeypatch.setattr(server,'_THUMBS_IN_FLIGHT',set())
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug:({'runtime_kind':'a2ui'},{'version':2}))
    calls=[]
    monkeypatch.setattr(captures,'thumbnail',lambda *a:calls.append(a[1:]))
    class InlineThread:
        def __init__(self,*,target,**kwargs):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(server.threading,'Thread',InlineThread)
    server._auto_thumb('app','visits',actor='alice')
    server._auto_thumb('app','visits')
    assert calls==[({'version':2},'app','visits','alice')]
    assert not server._THUMBS_IN_FLIGHT


def test_thumbnail_route_enforces_viewer_cache_and_access(tmp_path,monkeypatch):
    import ui_apps
    monkeypatch.setattr(ui_apps,'register',lambda *a:None)
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path/'calliope'))
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def register(f):routes[path]=f;return f
            return register
    server.register_dashboard_routes(MCP())
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):
            return SimpleNamespace(fetchone=lambda:{'runtime_kind':'a2ui','app_kind':'app','updated_at':datetime(2000,1,1,tzinfo=timezone.utc)})
    monkeypatch.setattr(server,'_conn',Conn)
    monkeypatch.setattr(server,'_live_app_capture_root',lambda:tmp_path)
    monkeypatch.setattr(auth,'read_session_full',lambda req:{'identity':req.headers['viewer']} if req.headers.get('viewer') else None)
    monkeypatch.setattr(server.artifact_access,'can_view',lambda conn,slug,viewer,**kw:viewer!='denied')
    queued=[];monkeypatch.setattr(server,'_auto_thumb',lambda *a,**kw:queued.append((a,kw)))
    shared=server._thumb_path('app','visits');shared.parent.mkdir(parents=True);shared.write_bytes(b'shared must never leak')
    alice=captures.thumbnail_path(server,'app','visits','alice');alice.parent.mkdir(parents=True);alice.write_bytes(b'alice')
    def get(viewer=None,etag=None):
        headers=[]
        if viewer:headers.append((b'viewer',viewer.encode()))
        if etag:headers.append((b'if-none-match',etag.encode()))
        r=Request({'type':'http','method':'GET','path':'/thumbs/app/visits.png','headers':headers,'path_params':{'kind':'app','slug':'visits'}})
        return asyncio.run(routes['/thumbs/{kind}/{slug}.png'](r))
    assert get().status_code==401 and get('denied').status_code==404
    assert not queued
    a=get('alice');assert a.status_code==200 and a.body==b'alice'
    assert a.headers['cache-control'].startswith('private')
    assert get('alice',a.headers['etag']).status_code==304
    assert get('bob',a.headers['etag']).status_code==404
    assert queued==[(('app','visits'),{'actor':'bob'})]
    bob=captures.thumbnail_path(server,'app','visits','bob');bob.parent.mkdir(parents=True);bob.write_bytes(b'alice')
    b=get('bob',a.headers['etag']);assert b.status_code==200 and b.headers['etag']!=a.headers['etag']


@pytest.mark.parametrize('runtime,archived,expected',[
    ('a2ui',False,True),('html',False,True),('a2ui',True,False),('python-fastapi',False,False),
])
def test_gallery_cards_include_company_app_images(monkeypatch,runtime,archived,expected):
    import calliope
    monkeypatch.setattr(calliope,'is_enabled',lambda:False)
    monkeypatch.setattr(auth,'background_layer',lambda *a,**kw:'')
    html=server._landing_html([{'slug':'my-visits','name':'My visits','app_kind':'app',
        'runtime_kind':runtime,'archived':archived,'latest_version':2,
        'updated_at':datetime.now(timezone.utc)}],'alice@example.test')
    assert ('<img src="/thumbs/app/my-visits.png"' in html) is expected
