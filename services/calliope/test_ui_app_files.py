"""Attachment boundary checks; real SQL/phone round-trip is check_field.py."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import uuid
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import ui_app_files as files
from app_actions import ActionError


def body(**changes):
    return dict(name='visit.txt',mime='text/plain',data=base64.b64encode(b'Test reading').decode(),request_key=str(uuid.uuid4()),**{}) | changes

@pytest.mark.parametrize('changes',[
    {'name':'../secret.txt'},{'name':'x\r\n.txt'},{'name':'test.html'},
    {'mime':'text/html'},{'data':'%%%%'},{'data':''},{'data':base64.b64encode(b'\0').decode()},
    {'name':'fake.png','mime':'image/png'}, {'request_key':'not-an-id'},
    {'data':'A'*(7_000_000+1)},
])
def test_invalid_uploads(changes):
    with pytest.raises(ValueError):files.decode(body(**changes))


def test_supported_content_and_retry_key():
    for name,mime,data in [('visit.txt','text/plain',b'Warm air'),('photo.png','image/png',b'\x89PNG\r\n\x1a\nbody'),('photo.jpg','image/jpeg',b'\xff\xd8\xffbody'),('manual.pdf','application/pdf',b'%PDF-1.7')]:
        request=body(name=name,mime=mime,data=base64.b64encode(data).decode())
        result=files.decode(request)
        assert result==(name,mime,data,request['request_key'])


def test_reference_access_and_readonly_unavailable(monkeypatch):
    ident=str(uuid.uuid4())
    nodes=[{'component':'CAttachments','value':{'path':'/state/files'},'names':{'path':'/names'}}]
    data={'state':{'files':[ident]},'names':{}}
    def denied(*a,**kw):raise ActionError('NOT_FOUND','File not found',404)
    monkeypatch.setattr(files,'read',denied)
    with pytest.raises(ValueError,match='accessible'):files.references(None,'actor',nodes,data,check=True)
    nodes[0]['readOnly']=True
    files.references(None,'actor',nodes,data)
    assert data['names'][ident]=='Attachment unavailable'


@pytest.mark.parametrize('edited', [False, True])
def test_resolved_names_do_not_create_false_form_edits(monkeypatch, edited):
    import copy
    ident=str(uuid.uuid4())
    saved={'id':'visit-1','notes':'Saved','files':[ident],'names':{}}
    data={'record':saved,'draft':copy.deepcopy(saved)}
    if edited: data['draft']['notes']='Still editing'
    nodes=[{'component':'CAttachments','readOnly':True,
            'value':{'path':'/record/files'},'names':{'path':'/record/names'}}]
    monkeypatch.setattr(files,'read',lambda *a,**kw:{'name':'photo.jpg'})
    files.references(None,'actor',nodes,data,forms=[{'source':'/record','path':'/draft'}])
    assert data['draft']['names']==data['record']['names']=={ident:'photo.jpg'}
    assert data['draft']['notes']==('Still editing' if edited else 'Saved')
    assert (data['draft']==data['record']) is not edited


@pytest.mark.parametrize('draft', [
    {'id':'visit-2','names':{}},
    {'id':'visit-1','names':{'local:photo':'Unsubmitted photo.jpg'}},
])
def test_resolved_names_preserve_other_records_and_local_metadata(monkeypatch,draft):
    import copy
    ident=str(uuid.uuid4());before=copy.deepcopy(draft)
    data={'record':{'id':'visit-1','files':[ident],'names':{}},'draft':draft}
    nodes=[{'component':'CAttachments','readOnly':True,
            'value':{'path':'/record/files'},'names':{'path':'/record/names'}}]
    monkeypatch.setattr(files,'read',lambda *a,**kw:{'name':'photo.jpg'})
    files.references(None,'actor',nodes,data,forms=[{'source':'/record','path':'/draft'}])
    assert data['draft']==before


def test_routes_are_authenticated_bounded_and_download_only(monkeypatch):
    routes=[]
    class Registry:
        def custom_route(self,path,methods):
            def add(fn):routes.append(Route(path,fn,methods=methods));return fn
            return add
    def auth(req):
        return ('viewer',{},None) if req.headers.get('authorization')=='test' else (None,None,JSONResponse({},401))
    calls=[]
    def upload(server,slug,actor,body):calls.append((slug,actor));return {'id':'test'}
    def read(server,actor,ident,content=False):
        if ident=='denied':raise ActionError('NOT_FOUND','File not found',404)
        return {'name':'Visit photo.txt','mime':'text/plain',**({'content':b'private'} if content else {})}
    monkeypatch.setattr(files,'upload',upload);monkeypatch.setattr(files,'read',read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        files.register(Registry(),SimpleNamespace(_CONTROL_EXECUTOR=pool),auth,lambda v,status=200:JSONResponse(v,status))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)),base_url='https://calliope.test') as c:
                path='/api/apps/test/files';headers={'authorization':'test'}
                assert (await c.post(path,json=body())).status_code==401
                assert (await c.get('/api/calliope/app-files/test')).status_code==401
                assert (await c.post(path,headers=headers|{'origin':'https://evil.test'},json=body())).status_code==403
                assert (await c.post(path,headers=headers|{'content-type':'application/json'},content=b'x'*7_100_001)).status_code==413
                assert not calls
                assert (await c.post(path,headers=headers,json=body())).status_code==200
                assert calls==[('test','viewer')]
                assert (await c.get('/api/calliope/app-files/denied',headers=headers)).status_code==404
                r=await c.get('/api/calliope/app-files/test',headers=headers)
                assert r.content==b'private'
                assert r.headers['content-disposition'].startswith('attachment;')
                assert r.headers['x-content-type-options']=='nosniff'
                assert r.headers['cache-control']=='private,no-store'
        asyncio.run(check())


def test_readonly_repeated_attachments_accept_resolved_arrays(monkeypatch):
    import ui_apps
    ident=str(uuid.uuid4())
    doc={'schema':'calliope.app.v1','data':{'state':{},'names':{}},'components':[{'id':'root','component':'CAttachments','readOnly':True,'value':[ident],'names':{'path':'/names'}}]}
    ui_apps.validate(doc)
    monkeypatch.setattr(files,'read',lambda *a,**kw:{'name':'saved.txt'})
    files.references(None,'actor',doc['components'],doc['data'])
    assert doc['data']['names'][ident]=='saved.txt'
    doc['components'][0]['readOnly']=False
    with pytest.raises(ValueError):ui_apps.validate(doc)
