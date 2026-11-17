import base64
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import shared_files as files
from shared_file_paths import contained, relative_name

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'docker'/'desktop'))
from computer_files import SharedFiles
from computer_runtime import ComputerError

SID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


@pytest.mark.parametrize('name,mime', [('shared-files.js','text/javascript'), ('shared-files.css','text/css'),
    ('files-runtime.js','text/javascript'), ('files-runtime.css','text/css'), ('files-icons.woff2','font/woff2')])
def test_shared_files_browser_assets_are_registered_and_served(name,mime):
    import ast
    import asyncio
    import calliope
    from starlette.responses import Response
    root=Path(__file__).resolve().parent
    tree=ast.parse((root/'calliope.py').read_text())
    route='/calliope/'+name
    fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef)
            and any(isinstance(d,ast.Call) and d.args and isinstance(d.args[0],ast.Constant)
                    and d.args[0].value==route for d in n.decorator_list))
    fn.decorator_list=[]
    namespace={'_canonical_owner':lambda r:(r.owner,None),'Response':Response,
               'browser_asset':lambda request,asset,media_type:Response((root/'calliope'/asset).read_bytes(),media_type=media_type)}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<shared-files-asset-route>','exec'),namespace)
    request=SimpleNamespace(owner=None,url=SimpleNamespace(path=route))
    assert asyncio.run(namespace[fn.name](request)).status_code==401
    request.owner='viewer@example.com'
    response=asyncio.run(namespace[fn.name](request))
    assert response.status_code==200
    assert response.body==(root/'calliope'/name).read_bytes()
    assert response.headers['content-type'].startswith(mime)
    assert name in calliope._CACHE_VERSIONED_ASSETS


@pytest.mark.parametrize('name',['../secret','a/../../secret','/etc/passwd','a\\b','a//b','a/./b','a\x00b'])
def test_paths_reject_escape(name,tmp_path):
    with pytest.raises(ValueError):contained(tmp_path,name)


def test_guest_preserves_edits_on_retry_and_exposes_new_outputs(tmp_path):
    guest=SharedFiles(tmp_path/'Shared',tmp_path/'receipts')
    data=b'original\x00bytes';fid=str(uuid.uuid4());sha=hashlib.sha256(data).hexdigest()
    result=guest.put(SID,'batch/report.xlsx',fid,sha,io.BytesIO(data),len(data))
    assert result['status']=='ready'
    path=guest.path(SID,'batch/report.xlsx');path.write_bytes(b'edited')
    guest.put(SID,'batch/report.xlsx',fid,sha,io.BytesIO(data),len(data))
    assert path.read_bytes()==b'edited'
    guest.path(SID,'answer.txt').write_text('result')
    assert {f['name'] for f in guest.listing(SID)['files']}=={'batch','batch/report.xlsx','answer.txt'}
    assert guest.listing(OTHER)['files']==[]
    with pytest.raises(ComputerError,match='different desktop file'):
        guest.put(SID,'batch/report.xlsx',str(uuid.uuid4()),sha,io.BytesIO(data),len(data))
    outside=tmp_path/'secret';outside.write_text('private')
    (guest.folder(SID)/'link').symlink_to(outside)
    with pytest.raises(ValueError):guest.path(SID,'link')
    assert 'link' not in [f['name'] for f in guest.listing(SID)['files']]


def test_guest_does_not_publish_truncated_or_bad_checksum_upload(tmp_path):
    guest=SharedFiles(tmp_path/'Shared',tmp_path/'receipts')
    for data,size,sha in [(b'a',2,hashlib.sha256(b'a').hexdigest()),(b'a',1,'incorrect')]:
        with pytest.raises(ValueError):guest.put(SID,'bad.zip',str(uuid.uuid4()),sha,io.BytesIO(data),size)
    assert guest.listing(SID)['files']==[]
    guest.put(SID,'empty.txt',str(uuid.uuid4()),hashlib.sha256(b'').hexdigest(),io.BytesIO(b''),0)
    assert guest.path(SID,'empty.txt').read_bytes()==b''


def test_originals_keep_names_bytes_and_session_boundary(tmp_path):
    config=SimpleNamespace(file_root=tmp_path,desktop_enabled=False)
    source=tmp_path/'source';source.write_bytes(b'\x00\xff\x01')
    first=files.store(config,SID,'folder/unknown.weird',source)
    second=files.store(config,SID,'folder/unknown.weird',source)
    assert first['name']=='folder/unknown.weird'
    assert second['name']!=first['name']
    assert Path(first['storage_path']).read_bytes()==source.read_bytes()
    assert 'storage_path' not in files.public(first,SID)
    with pytest.raises(ValueError):files.record(config,OTHER,first['shared_file_id'])
    assert files.sync(config,SID)['status']=='unavailable'


def test_files_routes_auth_upload_folder_original_and_size_limit(tmp_path,monkeypatch):
    config=SimpleNamespace(file_root=tmp_path,desktop_enabled=False)
    routes=[]
    class MCP:
        def custom_route(self,path,methods):
            def register(fn):routes.append(Route(path,fn,methods=methods));return fn
            return register
    def check(conn,sid,actor):
        if sid!=SID or actor=='stranger':
            raise files.calliope_access.CalliopeAccessError('SESSION_NOT_FOUND','Unavailable',404)
    def write(conn,sid,actor):
        check(conn,sid,actor)
        if actor=='viewer':raise files.calliope_access.CalliopeAccessError('THREAD_READ_ONLY','Read only',403)
    monkeypatch.setattr(files.calliope_access,'require_view',check)
    monkeypatch.setattr(files.calliope_access,'require_participant',write)
    from contextlib import nullcontext
    from starlette.responses import JSONResponse
    def owner(r):
        return (r.headers.get('x-user'),None) if r.headers.get('x-user') else (None,JSONResponse({},status_code=401))
    files.register(MCP(),lambda:nullcontext(None),config,owner)
    client=TestClient(Starlette(routes=routes));url=f'/api/calliope/sessions/{SID}/files'
    assert client.get(url).status_code==401
    assert client.get(url,headers={'x-user':'stranger'}).status_code==404
    headers={'x-user':'owner','x-calliope-files':'1','x-file-name':'batch%2Freport.xlsx','content-type':'application/octet-stream'}
    assert client.post(url,headers={**headers,'x-user':'viewer'},content=b'abc').status_code==403
    assert client.post(url,headers={**headers,'origin':'https://evil.example'},content=b'abc').status_code==403
    response=client.post(url,headers=headers,content=b'abc');assert response.status_code==201,response.text
    item=response.json()['file'];assert item['name']=='batch/report.xlsx'
    result=client.get(item['url'],headers={'x-user':'viewer'})
    assert result.content==b'abc';assert result.headers['content-disposition'].startswith('attachment;')
    assert client.get(item['url'].replace(SID,OTHER),headers={'x-user':'owner'}).status_code==404
    assert client.post(url,headers={**headers,'x-file-name':'..%2Fescape'},content=b'abc').status_code==400
    operation={'operation':'rename','path':'batch/report.xlsx','target':'batch/renamed.xlsx'}
    assert client.post(url+'/action',headers={**headers,'x-user':'viewer'},json=operation).status_code==403
    assert client.post(url+'/action',headers={**headers,'origin':'https://evil.example'},json=operation).status_code==403
    assert client.post(url+'/action',headers=headers,json={**operation,'target':'../escape'}).status_code==400
    calls=[]
    class Runtime:
        def __init__(self,config):pass
        def request(self,method,path,payload):
            calls.append((method,path,payload))
            if payload['path']=='missing':raise files.DesktopError('FILE_NOT_FOUND','Missing file',404)
            return {'name':payload.get('target')}
    monkeypatch.setattr(files,'DesktopRuntime',Runtime)
    assert client.post(url+'/action',headers=headers,json=operation).status_code==200
    assert calls[-1]==('POST','shared/action',{'session_id':SID,**operation})
    assert client.post(url+'/action',headers=headers,json={'operation':'trash','path':'missing'}).status_code==404
    assert client.post(url+'/action',headers=headers,json={'operation':'open','path':'batch/report.xlsx'}).status_code==200
    assert calls[-1][1]=='shared/open'
    monkeypatch.setattr(files,'MAX_BYTES',2)
    assert client.post(url,headers=headers,content=b'abc').status_code==413


def test_documents_decode_without_becoming_model_images():
    import calliope
    cfg=SimpleNamespace(max_image_bytes=1024)
    for mime,raw in [('application/pdf',b'%PDF-1.7'),('application/octet-stream',b'\x00\xff'),('text/plain',b'')]:
        result=calliope._decode_attachments([{'name':'sample.file','data_url':f'data:{mime};base64,'+base64.b64encode(raw).decode()}],cfg)
        assert result[0]['raw']==raw and result[0]['mime']==mime


def test_guest_folder_operations_preserve_original_identity_and_never_overwrite(tmp_path):
    guest=SharedFiles(tmp_path/'Shared',tmp_path/'receipts')
    raw=b'office document';fid=str(uuid.uuid4());sha=hashlib.sha256(raw).hexdigest()
    guest.put(SID,'batch/report.docx',fid,sha,io.BytesIO(raw),len(raw))
    guest.mutate(SID,'mkdir','empty')
    assert next(f for f in guest.listing(SID)['files'] if f['name']=='empty')['type']=='folder'
    guest.mutate(SID,'rename','batch','renamed')
    assert next(f for f in guest.listing(SID)['files'] if f['name']=='renamed/report.docx')['original_id']==fid
    guest.put(SID,'batch/report.docx',fid,sha,io.BytesIO(raw),len(raw))
    assert not guest.path(SID,'batch/report.docx').exists()
    guest.mutate(SID,'copy','renamed/report.docx','empty/copy.docx')
    guest.path(SID,'empty/copy.docx').write_bytes(b'edited')
    with pytest.raises(ComputerError,match='already exists'):
        guest.mutate(SID,'move','renamed/report.docx','empty/copy.docx')
    assert guest.path(SID,'empty/copy.docx').read_bytes()==b'edited'
    assert guest.path(SID,'renamed/report.docx').read_bytes()==raw
    with pytest.raises(ValueError):guest.mutate(SID,'move','renamed','renamed/inside')
    with pytest.raises(ValueError):guest.mutate(SID,'rename','renamed','../escape')
    guest.mutate(SID,'move','renamed/report.docx','empty/report.docx')
    guest.mutate(SID,'trash','empty')
    assert not guest.path(SID,'empty').exists()
    trashed=list((tmp_path/'receipts'/'trash'/SID).glob('*/content/report.docx'))
    assert len(trashed)==1 and trashed[0].read_bytes()==raw
    guest.put(SID,'batch/report.docx',fid,sha,io.BytesIO(raw),len(raw))
    assert not guest.path(SID,'batch/report.docx').exists()
    assert guest.listing(OTHER)['files']==[]


def test_copy_rejects_nested_symlinks(tmp_path):
    guest=SharedFiles(tmp_path/'Shared',tmp_path/'receipts')
    guest.mutate(SID,'mkdir','folder')
    guest.path(SID,'folder/link').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError,match='symlinks'):
        guest.mutate(SID,'copy','folder','copy')
    assert not guest.path(SID,'copy').exists()


def test_trash_needs_only_owned_upload_directory(tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    receipts=runtime/'shared-uploads';receipts.mkdir()
    guest=SharedFiles(tmp_path/'Shared',receipts)
    raw=b'keep me';fid=str(uuid.uuid4())
    guest.put(SID,'input.txt',fid,hashlib.sha256(raw).hexdigest(),io.BytesIO(raw),len(raw))
    runtime.chmod(0o555)
    try:
        guest.mutate(SID,'trash','input.txt')
        assert next((receipts/'trash'/SID).glob('*/content')).read_bytes()==raw
    finally:runtime.chmod(0o755)
