"""Immutable, artifact-authorized app attachments in Calliope's Postgres.

Uploading does not submit a business record. Apps retain returned UUIDs through
versioned SQL actions. Removing a draft reference never deletes a saved receipt.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import re
import uuid
from threading import Lock
from app_actions import ActionError

MAX_FILE = 5 * 1024 * 1024
DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.ui_app_files (
 id uuid PRIMARY KEY, artifact_id bigint NOT NULL REFERENCES rvbbit.dashboards(id),
 actor text NOT NULL, request_key uuid NOT NULL, name text NOT NULL, mime text NOT NULL,
 sha256 text NOT NULL, content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 5242880),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(artifact_id,actor,request_key)
);
CREATE INDEX IF NOT EXISTS ui_app_files_actor_time ON rvbbit.ui_app_files(actor,created_at);
REVOKE ALL ON rvbbit.ui_app_files FROM PUBLIC;
"""
_lock, _ready = Lock(), set()

def prepare(server):
    with _lock:
        if server._conn not in _ready:
            with server._conn() as conn:
                conn.execute(DDL)
                conn.commit()
            _ready.add(server._conn)

def decode(body):
    if not isinstance(body, dict) or set(body) != {'name','mime','data','request_key'}:
        raise ValueError('Use name, mime, data and request_key')
    name, mime, encoded = body['name'], body['mime'], body['data']
    if not isinstance(name,str) or not 1 <= len(name) <= 180 or re.search(r'[\x00-\x1f\x7f/\\]',name) or name in {'.','..'}:
        raise ValueError('Invalid file name')
    key = str(uuid.UUID(body['request_key']))
    if not isinstance(encoded,str) or len(encoded) > ((MAX_FILE+2)//3)*4:
        raise ValueError('Files must be at most 5 MB')
    try: content=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise ValueError('Invalid file encoding') from None
    if not 1 <= len(content) <= MAX_FILE: raise ValueError('Files must contain 1 byte to 5 MB')
    suffix=name.rsplit('.',1)[-1].lower()
    valid = ((mime=='image/png' and suffix=='png' and content.startswith(b'\x89PNG\r\n\x1a\n'))
        or (mime=='image/jpeg' and suffix in {'jpg','jpeg'} and content.startswith(b'\xff\xd8\xff'))
        or (mime=='application/pdf' and suffix=='pdf' and content.startswith(b'%PDF-')))
    if mime=='text/plain' and suffix=='txt':
        try: valid='\x00' not in content.decode('utf-8')
        except UnicodeError: valid=False
    if not valid: raise ValueError('Choose a PNG, JPEG, PDF or UTF-8 .txt file with matching content')
    return name,mime,content,key

def upload(server,slug,actor,body):
    name,mime,content,key=decode(body)
    prepare(server)
    digest=hashlib.sha256(content).hexdigest()
    with server._conn() as conn:
        # Authorization is repeated inside the transaction, including archived apps.
        app=conn.execute("SELECT id FROM rvbbit.dashboards WHERE slug=%s AND runtime_kind='a2ui' AND NOT archived AND rvbbit.artifact_can_view(id,%s,false)",(slug,actor)).fetchone()
        if not app: raise ActionError('NOT_FOUND','App not found',404)
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('app-files:'+actor,))
        old=conn.execute('SELECT id,name,mime,sha256 FROM rvbbit.ui_app_files WHERE artifact_id=%s AND actor=%s AND request_key=%s',(app['id'],actor,key)).fetchone()
        if old:
            if old['sha256']!=digest or old['name']!=name or old['mime']!=mime: raise ActionError('CONFLICT','Upload key already used for another file',409)
            return {**old,'id':str(old['id'])}
        size=conn.execute('SELECT coalesce(sum(octet_length(content)),0) AS bytes FROM rvbbit.ui_app_files WHERE actor=%s AND created_at>now()-interval \'24 hours\'',(actor,)).fetchone()['bytes']
        if size+len(content)>100*1024*1024: raise ActionError('QUOTA','Daily app file limit reached',429)
        ident=str(uuid.uuid4())
        conn.execute('INSERT INTO rvbbit.ui_app_files(id,artifact_id,actor,request_key,name,mime,sha256,content) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(ident,app['id'],actor,key,name,mime,digest,content))
        conn.commit()
        return {'id':ident,'name':name,'mime':mime,'sha256':digest}

def read(server,actor,ident,*,content=False):
    try: ident=str(uuid.UUID(ident))
    except (ValueError,TypeError,AttributeError): raise ActionError('NOT_FOUND','File not found',404) from None
    prepare(server)
    with server._conn() as conn:
        row=conn.execute('SELECT f.id,f.name,f.mime,f.sha256,octet_length(f.content) AS size'+(',f.content' if content else '')+' FROM rvbbit.ui_app_files f JOIN rvbbit.dashboards d ON d.id=f.artifact_id WHERE f.id=%s AND NOT d.archived AND rvbbit.artifact_can_view(d.id,%s,false)',(ident,actor)).fetchone()
    if not row: raise ActionError('NOT_FOUND','File not found',404)
    return {**row,'id':str(row['id'])}

def references(server,actor,nodes,data,*,check=False,forms=()):
    from ui_apps import pointer, put
    for node in nodes:
        if node.get('component')!='CAttachments': continue
        binding=node.get('value')
        ids=pointer(data,binding['path']) if isinstance(binding,dict) else binding
        if not isinstance(ids,list) or len(ids)>5 or any(not isinstance(v,str) for v in ids): raise ValueError('Attachments require at most five file IDs')
        names={}
        for ident in ids:
            try: names[ident]=read(server,actor,ident)['name']
            except ActionError:
                if check and not node.get('readOnly'): raise ValueError('An attachment is missing or no longer accessible') from None
                names[ident]='Attachment unavailable'
        if not check and node.get('names'):
            path = node['names']['path']
            old = pointer(data,path)
            resolved = {**(old if isinstance(old,dict) else {}),**names}
            put(data,path,resolved)
            # Forms hydrate before attachment metadata is resolved. Keep unchanged
            # copies of this field in sync so a filename does not create an edit.
            for form in forms:
                source = form['source']
                if not path.startswith(source+'/'): continue
                saved, local = pointer(data,source), pointer(data,form['path'])
                if not isinstance(saved,dict) or not isinstance(local,dict): continue
                key = form.get('key','id')
                target = form['path']+path[len(source):]
                if local.get(key)==saved.get(key) and pointer(data,target)==old:
                    put(data,target,copy.deepcopy(resolved))

def register(m,server,session_actor,respond):
    import asyncio,contextvars,json,os
    from starlette.responses import Response
    from urllib.parse import quote
    async def run(fn,*args):
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR,contextvars.copy_context().run,fn,*args)
    @m.custom_route('/api/apps/{slug}/files',methods=['POST'])
    async def add(request):
        allowed={str(request.base_url).rstrip('/'),os.environ.get('WAREHOUSE_PUBLIC_URL','').rstrip('/')}
        if request.headers.get('sec-fetch-site')=='cross-site' or (request.headers.get('origin') and request.headers['origin'] not in allowed) or request.headers.get('content-type','').split(';')[0]!='application/json':
            return respond({'error':{'code':'INVALID_ORIGIN'}},403)
        actor,_,error=await run(session_actor,request)
        if error:return error
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>7_100_000:return respond({'error':{'code':'TOO_LARGE'}},413)
        try: result=await run(upload,server,request.path_params['slug'],actor,json.loads(raw))
        except ActionError as e:return respond({'error':{'code':e.code,'message':str(e)}},e.status)
        except (ValueError,TypeError,KeyError,AttributeError):return respond({'error':{'code':'INVALID_FILE','message':'Choose a PNG, JPEG, PDF or UTF-8 text file, up to 5 MB.'}},400)
        response=respond(result);response.headers['cache-control']='no-store';return response
    async def get(request):
        actor,_,error=await run(session_actor,request)
        if error:return error
        metadata=request.url.path.endswith('/meta')
        try: row=await run(lambda:read(server,actor,request.path_params['ident'],content=not metadata))
        except ActionError as e:return respond({'error':{'code':e.code}},e.status)
        if metadata: response=respond(row)
        else: response=Response(bytes(row['content']),media_type=row['mime'],headers={'content-disposition':"attachment; filename*=UTF-8''"+quote(row['name'],safe=''),'x-content-type-options':'nosniff'})
        response.headers['cache-control']='private,no-store';return response
    m.custom_route('/api/calliope/app-files/{ident}',methods=['GET'])(get)
    m.custom_route('/api/calliope/app-files/{ident}/meta',methods=['GET'])(get)
