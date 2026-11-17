"""HTTP contract tests: python tests/media_http.py (after cargo build). No paid providers."""
import hashlib, http.server, json, os, pathlib, socket, sqlite3, subprocess, tempfile, threading, time, urllib.request, urllib.error
BINARY=pathlib.Path(__file__).resolve().parents[1]/'target/debug/rvbbit-hutch'
def port():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]
seen=[]
class Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']));seen.append((self.path,body,self.headers))
        if b'reject-fixture' in body:
            self.send_response(500);self.end_headers();self.wfile.write(b'secret provider details');return
        if self.path=='/images':
            request=json.loads(body);assert request['model']=='real-image';assert request['provider']['data_collection']=='deny'
            response={'data':[{'b64_json':'aGVsbG8='}],'usage':{'prompt_tokens':5,'completion_tokens':9,'cost':.002}}
        else:
            assert b'real-transcribe' in body and b'audio-fixture' in body
            response={'text':'Hello world','usage':{'seconds':2,'cost':.001}}
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('x-generation-id','provider-123');self.end_headers();self.wfile.write(json.dumps(response).encode())
p=port();up=http.server.ThreadingHTTPServer(('127.0.0.1',p),Provider);threading.Thread(target=up.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory() as td:
    root=pathlib.Path(td);listen=port()
    tenants={'tenants':[{'id':name,'key_sha256':hashlib.sha256(name.encode()).hexdigest(),'lanes':2,'status':status,'entitlements':ent} for name,status,ent in [('test','active',['clover']),('expired','expired',['clover']),('denied','active',[])]]}
    (root/'tenants.json').write_text(json.dumps(tenants))
    cfg={'bind':f'127.0.0.1:{listen}','tenants_file':str(root/'tenants.json'),'meter_db':str(root/'meter.db'),'upstream':{'mode':'mock'},'backends':[{'name':'embed','entitlement':'clover','model_version':'mock'}],'media':[]}
    for alias,cap,model,path in [('calliope-image','image','real-image','/images'),('calliope-stt','transcription','real-transcribe','/audio/transcriptions')]:
        cfg['media'].append(dict(name=alias,capability=cap,entitlement='clover',provider='openrouter',upstream_base=f'http://127.0.0.1:{p}',upstream_path=path,upstream_model=model,model_version='test-v1',upstream_bearer_token_env='MEDIA_TEST_KEY',request_overrides={'provider':{'data_collection':'deny'}}))
    (root/'config.json').write_text(json.dumps(cfg))
    proc=subprocess.Popen([str(BINARY),'--config',str(root/'config.json')],env={**os.environ,'MEDIA_TEST_KEY':'upstream-secret'},stdout=subprocess.DEVNULL)
    def request(path,body=None,key='test',mime='application/json'):
        req=urllib.request.Request(f'http://127.0.0.1:{listen}'+path,data=body,headers={'Authorization':'Bearer '+key,'Content-Type':mime})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,r.headers,r.read()
        except urllib.error.HTTPError as e:return e.code,e.headers,e.read()
    try:
        for _ in range(100):
            try: request('/health');break
            except OSError:time.sleep(.05)
        assert request('/v1/media/models')[0]==200
        assert request('/v1/media/models',key='unknown')[0]==401
        image=json.dumps({'model':'calliope-image','prompt':'private prompt','n':1}).encode()
        code,headers,body=request('/v1/images/generations',image)
        assert code==200 and headers['x-hutch-upstream-model']=='real-image' and headers['x-hutch-request-id']
        assert headers['x-request-id']=='provider-123'
        assert request('/v1/images/generations',image,key='denied')[0]==403
        assert request('/v1/images/generations',image,key='expired')[0] in (402,403)
        assert request('/v1/images/generations',json.dumps({'model':'calliope-image','prompt':'x','provider':{}}).encode())[0]==400
        def multipart(extra=''):
            boundary='test-boundary'
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\ncalliope-stt\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.wav"\r\nContent-Type: audio/wav\r\n\r\naudio-fixture'.encode()+b'x'*(3*1024*1024)+f'\r\n--{boundary}--\r\n'.encode())
            return body,'multipart/form-data; boundary='+boundary
        body,mime=multipart();code,headers,response=request('/v1/audio/transcriptions',body,mime=mime)
        assert code==200,(code,response[:200]);assert json.loads(response)['text']=='Hello world'
        before=len(seen);code,headers,response=request('/v1/images/generations',image.replace(b'private prompt',b'reject-fixture'))
        assert code==502 and len(seen)==before+1 and b'secret provider' not in response
        db=sqlite3.connect(root/'meter.db');rows=db.execute('select capability,input_bytes,usage_json from media_usage').fetchall()
        assert len(rows)==3 and any(x[1]>3*1024*1024 for x in rows)
        dump='\n'.join(db.iterdump());assert 'private prompt' not in dump and 'audio-fixture' not in dump and 'upstream-secret' not in dump
        print('PASS: auth, entitlements, routing, 3 MiB multipart, metadata receipts, sanitized errors and no automatic retry')
    finally:proc.terminate();proc.wait();up.shutdown()
