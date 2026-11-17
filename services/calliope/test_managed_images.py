import io
import sys
import types
from contextlib import contextmanager
from pathlib import Path
import importlib.util
import pytest
from PIL import Image
import managed_images as media

SID='33bcab2e-1a89-49e9-97a4-e11f2f82f3df'
def png():
    out=io.BytesIO();Image.new('RGB',(8,8)).save(out,format='PNG');return out.getvalue()

def test_portable_paths_are_jailed_and_decode_as_images(tmp_path):
    p=tmp_path/'test.png';p.write_bytes(png())
    assert media.reference('/captures/test.png',tmp_path)==('capture',p)
    assert media.reference(str(p),tmp_path)==('capture',p)
    assert media.reference('calliope-image:'+SID,tmp_path)==('generated',SID)
    assert media.reference('/home/kasm-user/Shared/'+SID+'/nested/shot.png',tmp_path)==('desktop',(SID,'nested/shot.png'))
    for bad in ('/etc/passwd','/captures/../private.png','/home/kasm-user/Shared/'+SID+'/../secret','https://external/image.png'):
        with pytest.raises(ValueError):media.reference(bad,tmp_path)
    (tmp_path/'link.png').symlink_to(p)
    with pytest.raises(ValueError):media.reference('/captures/link.png',tmp_path)
    assert media.read(str(p),capture_root=tmp_path,conn_factory=None,config=None,actor='test')==(png(),'png')
    with pytest.raises(Exception):media.validate(b'<html>login</html>')

def test_generated_image_is_owner_scoped(tmp_path):
    calls=[]
    class Conn:
        def execute(self,sql,args):calls.append((sql,args));return self
        def fetchone(self):return None
    @contextmanager
    def conn():yield Conn()
    with pytest.raises(ValueError,match='unavailable'):
        media.read('calliope-image:'+SID,capture_root=tmp_path,conn_factory=conn,config=None,actor='owner@example.com')
    assert 'owner_email' in calls[0][0] and calls[0][1]==(SID,'owner@example.com')

def test_computer_access_is_checked_before_download(tmp_path,monkeypatch):
    import calliope_access
    @contextmanager
    def conn():yield object()
    def denied(*args):raise PermissionError('No access')
    monkeypatch.setattr(calliope_access,'require_view',denied)
    with pytest.raises(PermissionError):media.read('/home/kasm-user/Shared/'+SID+'/shot.png',capture_root=tmp_path,conn_factory=conn,config=None,actor='other')

def test_hermes_materializes_normal_mcp_image_tag_without_http_credentials(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[2]/'docker/calliope/hermes-managed-images.py'
    spec=importlib.util.spec_from_file_location('managed',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    image=tmp_path/'image.png';image.write_bytes(png());calls=[]
    def handle(*args,**kwargs):calls.append((args,kwargs));return 'Image result\nMEDIA:'+str(image)
    monkeypatch.setitem(sys.modules,'model_tools',types.SimpleNamespace(handle_function_call=handle))
    assert module.materialize('/captures/test.png','task')==str(image)
    assert calls==[(('mcp__Warehouse__read_calliope_image',{'reference':'/captures/test.png'}),{'task_id':'task'})]
    assert not module.managed_reference('https://arbitrary.example/image')
    monkeypatch.setattr(sys.modules['model_tools'],'handle_function_call',lambda *a,**k:'{"error":"not allowed"}')
    with pytest.raises(ValueError,match='Do not regenerate'):module.materialize('/captures/test.png')
