import contextlib
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace
from uuid import uuid4

import artifact_render as render
import artifact_work


def reset():
    with render._lock:
        assert render._worker is None
        render._states.clear(); render._pending.clear()


def wait():
    worker=render._worker
    if worker: worker.join(5)
    assert render._worker is None


def test_render_is_coalesced_and_scoped_to_viewer(tmp_path, monkeypatch):
    monkeypatch.setattr(render,'MIN_RENDER_INTERVAL',0)
    reset(); config=SimpleNamespace(file_root=tmp_path); sid=str(uuid4())
    artifact_work.folder(config,sid).mkdir(parents=True)
    item={'key':'turn.build','phase':'preview'};entered=threading.Event();release=threading.Event();seen=[]
    def capture(source,out,subject,owner,cache,*,on_frame,should_continue):
        seen.append((source,subject));entered.set();assert release.wait(5);out.write_bytes(source.encode())
        on_frame(1)
        if should_continue():
            out.write_bytes(source.encode());on_frame(2)
    monkeypatch.setattr(render,'_capture',capture)
    first=render.describe(config,sid,item,'first','viewer-a','a')
    assert first=={'render_status':'rendering'} and entered.wait(2)
    render.describe(config,sid,item,'second','viewer-a','a')
    render.describe(config,sid,item,'newest','viewer-a','a')
    release.set();wait()
    assert seen==[('first','viewer-a'),('newest','viewer-a')]
    assert render.image_path(config,sid,item,'viewer-a').read_text()=='newest'
    result=render.describe(config,sid,item,'newest','viewer-a','a')
    assert result['render_status']=='ready' and '?v=' in result['render_url']
    assert not render.image_path(config,sid,item,'viewer-b').exists()
    assert render.describe(config,sid,{**item,'phase':'stopped'},'newest','viewer-a','a')=={}


def test_failed_refresh_keeps_last_render(tmp_path,monkeypatch):
    monkeypatch.setattr(render,'MIN_RENDER_INTERVAL',0)
    reset();config=SimpleNamespace(file_root=tmp_path);sid=str(uuid4());artifact_work.folder(config,sid).mkdir(parents=True)
    item={'key':'turn.build','phase':'preview'}
    def capture(source,out,*args,on_frame,should_continue):
        if source=='bad':raise RuntimeError('incomplete draft')
        out.write_bytes(b'good screenshot');on_frame(2)
    monkeypatch.setattr(render,'_capture',capture)
    render.describe(config,sid,item,'good','viewer','owner');wait()
    url=render.describe(config,sid,item,'good','viewer','owner')['render_url']
    render.describe(config,sid,item,'bad','viewer','owner');wait()
    latest=render.describe(config,sid,item,'bad','viewer','owner')
    assert latest=={'render_status':'failed','render_url':url,'render_kind':'render'}
    assert render.image_path(config,sid,item,'viewer').read_bytes()==b'good screenshot'


def test_real_draft_renderer_runs_javascript_css_and_cached_governed_queries(tmp_path,monkeypatch):
    import server
    from PIL import Image
    calls=[]
    def query(sql,**kwargs):
        calls.append((sql,kwargs,server._SESSION_SUB.get()))
        return {'rows':[{'value':42}], 'columns':['value'], 'row_count':1}
    monkeypatch.setattr(server,'tool_run_sql',query)
    monkeypatch.setattr(server,'_record',lambda *a,**kw:None)
    html='''<!doctype html><html><head><style>body{margin:0;background:#15151b;color:white}#tile{position:absolute;top:0;left:0;width:400px;height:400px;background:red;font:64px Arial}</style></head><body><div id=tile>Before JavaScript</div><script>rvbbitQuery("select 42").then(r=>{let e=document.getElementById('tile');e.textContent='JS: '+r.rows[0].value;e.style.background='rgb(24,168,103)';});</script></body></html>'''
    cache=OrderedDict();out=tmp_path/'first.png'
    telemetry=render._capture(html,out,'test-viewer','owner@example.com',cache)
    assert telemetry['page_errors']==[]
    assert len(telemetry['queries'])==1 and telemetry['queries'][0]['status']=='settled'
    assert Image.open(out).convert('RGB').getpixel((100,200))==(24,168,103)
    # The next revision executes its new script/CSS and reuses the same data.
    telemetry=render._capture(html.replace('24,168,103','48,90,180'),tmp_path/'second.png','test-viewer','owner@example.com',cache)
    assert Image.open(tmp_path/'second.png').convert('RGB').getpixel((100,200))==(48,90,180)
    assert len(calls)==1 and calls[0][2]=='test-viewer'
    assert calls[0][1]['_statement_timeout_ms']==5000


def test_incoming_layouts_are_rasterized_and_rate_limited(tmp_path,monkeypatch):
    reset();config=SimpleNamespace(file_root=tmp_path);sid=str(uuid4());artifact_work.folder(config,sid).mkdir(parents=True)
    now=[100.0];seen=[]
    monkeypatch.setattr(render.time,'monotonic',lambda:now[0])
    def capture(source,out):
        seen.append(source);out.write_bytes(source.encode())
    monkeypatch.setattr(render,'_capture_layout',capture)
    item={'key':'turn.build','phase':'composing','preview_kind':'incoming','preview_html':'<h1>First layout</h1>'}
    render.describe(config,sid,item,None,'viewer','owner');wait()
    first=render.describe(config,sid,item,None,'viewer','owner')
    assert first['render_kind']=='layout' and first['render_status']=='ready'
    item['preview_html']='<h1>Next layout</h1>';now[0]+=1
    next_frame=render.describe(config,sid,item,None,'viewer','owner');wait()
    assert next_frame['render_url']==first['render_url'] and len(seen)==1
    now[0]+=4
    render.describe(config,sid,item,None,'viewer','owner');wait()
    assert render.describe(config,sid,item,None,'viewer','owner')['render_url']!=first['render_url']
    assert seen==['<h1>First layout</h1>','<h1>Next layout</h1>']


def test_continuous_edits_do_not_starve_completed_frames(tmp_path,monkeypatch):
    reset();config=SimpleNamespace(file_root=tmp_path);sid=str(uuid4());artifact_work.folder(config,sid).mkdir(parents=True)
    monkeypatch.setattr(render,'MIN_RENDER_INTERVAL',0)
    first=threading.Event();second=threading.Event();release_first=threading.Event();release_second=threading.Event()
    def capture(source,out,*args,on_frame,should_continue):
        entered,release=(first,release_first) if source=='first' else (second,release_second)
        entered.set();assert release.wait(5);out.write_bytes(source.encode())
        on_frame(1)
        if should_continue():
            out.write_bytes(source.encode());on_frame(2)
    monkeypatch.setattr(render,'_capture',capture)
    item={'key':'turn.build','phase':'preview'}
    try:
        render.describe(config,sid,item,'first','viewer','owner');assert first.wait(2)
        render.describe(config,sid,item,'second','viewer','owner');release_first.set();assert second.wait(2)
        live=render.describe(config,sid,item,'second','viewer','owner')
        assert live['render_status']=='rendering' and live.get('render_url')
        assert render.image_path(config,sid,item,'viewer').read_text()=='first'
    finally:
        release_first.set();release_second.set();wait()
    assert render.image_path(config,sid,item,'viewer').read_text()=='second'


def test_real_layout_capture_renders_css_without_scripts_or_queries(tmp_path,monkeypatch):
    import server
    from PIL import Image
    calls=[]
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**k:calls.append(a))
    source='''<style>body{margin:0;background:rgb(30,40,220)}</style><h1>Incoming layout</h1>
      <script>document.body.style.background='red';rvbbitQuery('select 42')</script>'''
    out=tmp_path/'layout.png';render._capture_layout(source,out)
    assert Image.open(out).size==(1040,650)
    assert Image.open(out).convert('RGB').getpixel((500,400))==(30,40,220)
    assert calls==[]


def test_two_frames_change_url_without_an_edit_and_do_not_repeat(tmp_path,monkeypatch):
    reset();config=SimpleNamespace(file_root=tmp_path);sid=str(uuid4());artifact_work.folder(config,sid).mkdir(parents=True)
    item={'key':'turn.build','phase':'preview'};first=threading.Event();release=threading.Event();calls=[]
    def capture(source,out,*args,on_frame,should_continue):
        calls.append(source)
        out.write_bytes(b'loading');on_frame(1);first.set();assert release.wait(5)
        assert should_continue()
        out.write_bytes(b'data loaded');on_frame(2)
    monkeypatch.setattr(render,'_capture',capture)
    try:
        render.describe(config,sid,item,'same source','viewer','owner');assert first.wait(2)
        loading=render.describe(config,sid,item,'same source','viewer','owner')
        assert loading['render_url'] and loading['render_status']=='rendering'
        assert render.image_path(config,sid,item,'viewer').read_bytes()==b'loading'
    finally:
        release.set();wait()
    loaded=render.describe(config,sid,item,'same source','viewer','owner');wait()
    assert loaded['render_url']!=loading['render_url'] and loaded['render_status']=='ready'
    assert render.image_path(config,sid,item,'viewer').read_bytes()==b'data loaded'
    assert calls==['same source']


def test_failed_second_shot_preserves_the_first_frame(tmp_path,monkeypatch):
    reset();config=SimpleNamespace(file_root=tmp_path);sid=str(uuid4());artifact_work.folder(config,sid).mkdir(parents=True)
    def capture(source,out,*args,on_frame,should_continue):
        out.write_bytes(b'first shot');on_frame(1)
        raise RuntimeError('follow-up screenshot failed')
    monkeypatch.setattr(render,'_capture',capture)
    item={'key':'turn.build','phase':'preview'}
    render.describe(config,sid,item,'source','viewer','owner');wait()
    latest=render.describe(config,sid,item,'source','viewer','owner')
    assert latest['render_url'] and latest['render_status']=='failed'
    assert render.image_path(config,sid,item,'viewer').read_bytes()==b'first shot'


def test_real_followup_keeps_page_alive_for_delayed_data(tmp_path,monkeypatch):
    import server
    from PIL import Image
    import io
    release=threading.Event();calls=[];frames=[]
    def query(sql,**kwargs):
        calls.append(sql);assert release.wait(5)
        return {'rows':[{'value':42}], 'row_count':1}
    original=server._capture_html_with_playwright
    def quick_initial(*args,**kwargs):
        kwargs['data_timeout_ms']=500
        return original(*args,**kwargs)
    monkeypatch.setattr(server,'_capture_html_with_playwright',quick_initial)
    monkeypatch.setattr(server,'tool_run_sql',query)
    monkeypatch.setattr(server,'_record',lambda *a,**kw:None)
    monkeypatch.setattr(render,'FOLLOWUP_DELAY_MS',500)
    html='''<html><head><style>body{margin:0;background:rgb(220,30,40)}</style></head><body><script>
      rvbbitQuery('select 42').then(r=>{document.body.textContent=r.rows[0].value;document.body.style.background='rgb(24,168,103)';});
    </script></body></html>'''
    out=tmp_path/'frame.png'
    def frame(number):
        frames.append((number,Image.open(io.BytesIO(out.read_bytes())).convert('RGB').getpixel((100,200))))
        release.set()
    try:
        telemetry=render._capture(html,out,'viewer','owner',OrderedDict(),on_frame=frame)
    finally:
        release.set()
    assert frames==[(1,(220,30,40)),(2,(24,168,103))]
    assert calls==['select 42']
    assert telemetry['readiness']['ready'] and telemetry['annotation'] is None
    # The first shot's yellow diagnostic was removed from the settled image.
    assert Image.open(out).convert('RGB').getpixel((100,25))==(24,168,103)


def test_followup_wait_pumps_browser_and_cancels_superseded_edits(tmp_path,monkeypatch):
    import server
    frames=[];waits=[]
    class Page:
        def wait_for_timeout(self,ms):waits.append(ms)
        def evaluate(self,*a):pass
        def screenshot(self,**kw):pass
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda _,value:value['html'])
    monkeypatch.setattr(server,'_wait_for_capture_bridge',lambda *a:{'ready':True})
    monkeypatch.setattr(server,'_capture_theme_state',lambda *a:{})
    monkeypatch.setattr(server,'_capture_render_issues',lambda *a:[])
    monkeypatch.setattr(server,'_annotate_incomplete_capture',lambda *a:None)
    def capture(*args,**kwargs):kwargs['after_capture'](Page(),{},{} )
    monkeypatch.setattr(server,'_capture_html_with_playwright',capture)
    render._capture('source',tmp_path/'a.png','viewer','owner',OrderedDict(),on_frame=frames.append)
    assert frames==[1,2] and sum(waits)==5000 and max(waits)<=100
    frames.clear();waits.clear()
    render._capture('old',tmp_path/'a.png','viewer','owner',OrderedDict(),on_frame=frames.append,
                    should_continue=lambda:len(waits)<2)
    assert frames==[1] and sum(waits)==200
