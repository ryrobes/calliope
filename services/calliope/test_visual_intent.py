"""Visual Intent uses private drafts and atomically publishes without a turn."""
import base64
import contextlib
import io
import json
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import calliope
import visual_intent as vi


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (120, 80), color).save(stream, format='PNG')
    return stream.getvalue()


def uri(data):
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


@pytest.fixture
def database(tmp_path):
    data, sock = tmp_path / 'pg', tmp_path / 'sock'
    sock.mkdir()
    subprocess.run(['initdb', '-D', str(data), '-A', 'trust', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
    subprocess.run(['pg_ctl', '-D', str(data), '-l', str(tmp_path / 'pg.log'), '-o', f"-k {sock} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
    def connect():
        return psycopg.connect(f'host={sock} dbname=postgres', row_factory=dict_row)
    try:
        with connect() as conn:
            conn.execute('''CREATE SCHEMA rvbbit;
              CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text);
              CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,design_profile_version_id uuid);
              CREATE TABLE rvbbit.calliope_design_profiles(id uuid PRIMARY KEY,name text);
              CREATE TABLE rvbbit.calliope_design_profile_versions(id uuid PRIMARY KEY,profile_id uuid,version int,tokens jsonb);
              CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,turn_id uuid,ordinal int,
                kind text,title text,tool_name text,tool_call_id text,lineage_key text,parent_surface_id uuid,
                artifact_slug text,artifact_version int,payload jsonb,source jsonb,presentation jsonb,
                design_profile_version_id uuid,created_at timestamptz DEFAULT now(),
                UNIQUE(session_id,tool_call_id,lineage_key));''')
            conn.execute(vi.DDL)
        yield connect
    finally:
        subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


@pytest.fixture
def editor(database, monkeypatch, tmp_path):
    sid, turn, source = [str(uuid.uuid4()) for _ in range(3)]
    with database() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_sessions VALUES(%s,%s)', (sid, 'owner@test'))
        conn.execute('INSERT INTO rvbbit.calliope_turns VALUES(%s,%s,NULL)', (turn, sid))
        conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,ordinal,kind,title,artifact_slug,artifact_version,payload,source) VALUES(%s,%s,%s,1,'image','Capture · Sales','sales',7,'{}','{}')", (source,sid,turn))
    monkeypatch.setattr(calliope.CalliopeConfig, 'from_env', lambda: SimpleNamespace(file_root=tmp_path / 'files'))
    monkeypatch.setattr(vi, 'edit_config', lambda conn: {'model': 'test/image'})
    calls = []
    def generate(config, clean, annotated, prompt):
        calls.append((clean,annotated,prompt))
        return png('blue'), {'cost': .03}
    monkeypatch.setattr(vi, 'generate', generate)
    routes = []
    class MCP:
        def custom_route(self, path, methods):
            def register(fn):
                routes.append(Route(path, fn, methods=methods))
                return fn
            return register
    def auth(request):
        owner = request.headers.get('x-owner')
        return (owner, None) if owner else (None, JSONResponse({},status_code=401))
    vi.register(MCP(), database, auth, lambda obj,status=200:JSONResponse(json.loads(json.dumps(obj,default=str)),status_code=status))
    with TestClient(Starlette(routes=routes)) as client:
        client.headers['x-owner'] = 'owner@test'
        yield SimpleNamespace(client=client, url=f'/api/calliope/sessions/{sid}/visual-intent', source=source, sid=sid, turn=turn, calls=calls, conn=database)


def original(e):
    result=e.client.post(e.url,json={'id':str(uuid.uuid4()),'source_surface_id':e.source,'image':uri(png())})
    assert result.status_code==201,result.text
    return result.json()['draft']


def wait_for(e, draft_id):
    for _ in range(100):
        rows=e.client.get(e.url).json()['drafts']
        row=next(d for d in rows if d['id']==draft_id)
        if row['status']!='running': return row
        time.sleep(.02)
    raise AssertionError('Image edit did not settle')


@pytest.mark.parametrize('document', [False, True])
def test_edit_save_idempotency_and_no_chat_turn(editor, document):
    e=editor
    if document:
        with e.conn() as c:
            c.execute("UPDATE rvbbit.calliope_surfaces SET kind='document', title='mockup.png', artifact_slug=NULL,artifact_version=NULL,payload=%s::jsonb WHERE id=%s", (json.dumps({'filename':'mockup.png','mime_type':'image/png','download_url':'/api/calliope/files/mockup'}),e.source))
    base=original(e)
    job={'id':str(uuid.uuid4()),'parent_id':base['id'],'prompt':'Make the chart wider','annotation':uri(png('green'))}
    assert e.client.post(e.url,json=job).status_code==202
    ready=wait_for(e,job['id'])
    assert ready['status']=='ready'
    assert e.client.post(e.url,json=job).status_code==200
    assert len(e.calls)==1
    assert e.calls[0]==(png(),png('green'),'Make the chart wider')
    assert e.client.get(ready['image_url']).content==png('blue')
    # Drafts have not touched Stage or the chat, even after successful generation.
    with e.conn() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n']==1
    first=e.client.post(f"{e.url}/{ready['id']}/save")
    assert first.status_code==201,first.text
    surface=first.json()['surface']
    second=e.client.post(f"{e.url}/{ready['id']}/save")
    assert second.status_code==200
    assert second.json()['surface']['id']==surface['id']
    assert surface['source']['artifact_version']==(None if document else 7)
    assert surface['source']['instructions']==['Make the chart wider']
    assert surface['title']==('Visual intent · mockup.png' if document else 'Visual intent · Sales')
    assert surface['design_profile_version_id'] is None
    attached = vi.selected_attachment(e.conn, SimpleNamespace(max_image_bytes=8_000_000), e.sid, surface['id'], [])
    assert attached[0]['raw']==png('blue')
    assert vi.selected_attachment(e.conn, SimpleNamespace(max_image_bytes=8_000_000), str(uuid.uuid4()), surface['id'], [])==[]
    with pytest.raises(ValueError, match='allowance'):
        vi.selected_attachment(e.conn, SimpleNamespace(max_image_bytes=2), e.sid, surface['id'], [])
    assert surface['payload']['image_url'].endswith('/image')
    with e.conn() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_turns').fetchone()['n']==1
    assert 'image' not in e.client.get(e.url).json()['drafts'][-1]


def test_auth_source_and_parent_are_scoped(editor):
    e=editor; base=original(e)
    for path in [e.url,base['image_url']]:
        assert e.client.get(path,headers={'x-owner':'stranger@test'}).status_code==404
    assert e.client.post(e.url,headers={'x-owner':'stranger@test'},json={'id':str(uuid.uuid4()),'parent_id':base['id'],'prompt':'Edit'}).status_code==404
    assert e.client.post(e.url,json={'id':str(uuid.uuid4()),'source_surface_id':str(uuid.uuid4()),'image':uri(png())}).status_code==400
    assert e.client.post(e.url,json={'id':str(uuid.uuid4()),'parent_id':str(uuid.uuid4()),'prompt':'Edit'}).status_code==400
    assert e.client.post(f"{e.url}/{base['id']}/save").status_code==400
    assert not e.calls


def test_failure_keeps_instruction_and_markup(editor,monkeypatch):
    e=editor; base=original(e)
    def fail(*args):raise RuntimeError('secret provider token must not escape')
    monkeypatch.setattr(vi,'generate',fail)
    job={'id':str(uuid.uuid4()),'parent_id':base['id'],'prompt':'Change hierarchy','annotation':uri(png('green'))}
    e.client.post(e.url,json=job)
    failed=wait_for(e,job['id'])
    assert failed['status']=='failed' and failed['prompt']=='Change hierarchy'
    assert 'secret' not in failed['error']
    with e.conn() as c:
        assert bytes(c.execute('SELECT annotation FROM rvbbit.calliope_visual_intents WHERE id=%s',(job['id'],)).fetchone()['annotation'])==png('green')
    assert e.client.post(f"{e.url}/{job['id']}/save").status_code==400


def test_provider_gets_clean_and_marked_image_without_agent(monkeypatch):
    requests=[]
    def urlopen(request,timeout):
        requests.append(json.loads(request.data))
        return contextlib.closing(io.BytesIO(json.dumps({'choices':[{'message':{'images':[{'image_url':{'url':uri(png('blue'))}}]}}], 'usage':{'cost':.1}}).encode()))
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    data,usage=vi.generate({'model':'example/image','base_url':'https://openrouter.ai/api/v1','api_key':'test'},png(),png('green'),'Wider chart')
    assert data==png('blue') and usage=={'cost':.1}
    content=requests[0]['messages'][0]['content']
    assert content[1]['image_url']['url']==uri(png())
    assert content[2]['image_url']['url']==uri(png('green'))
    assert requests[0]['modalities']==['image','text']


def test_hermes_image_slot_uses_same_model_directly(monkeypatch):
    monkeypatch.setattr(vi.expert_portraits,'_config',lambda conn:{'lane':'hermes','model':'google/example-image','base_url':'http://hermes','api_key':'hermes-secret'})
    monkeypatch.setenv('OPENROUTER_API_KEY','image-secret')
    result=vi.edit_config(None)
    assert result['model']=='google/example-image' and result['api_key']=='image-secret'
    assert result['base_url']=='https://openrouter.ai/api/v1'
    monkeypatch.delenv('OPENROUTER_API_KEY')
    with pytest.raises(ValueError,match='Visual Intent needs'):vi.edit_config(None)


@pytest.mark.parametrize('value',['', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,bm90IGFuIGltYWdl', 'https://evil/image.png'])
def test_rejects_non_image_inputs(value):
    with pytest.raises(ValueError):vi.image_bytes(value)


@pytest.mark.parametrize('payload,expected', [
    ({'filename':'mock.PNG'},True),
    ({'mime_type':'image/webp'},True),
    ({'filename':'readme.md','mime_type':'text/markdown'},False),
    ({'filename':'rows.json'},False),
    ({'filename':'mock.png','provider':'google_docs'},False),
])
def test_document_source_requires_an_image(payload,expected):
    assert vi.is_image_source({'kind':'document','payload':payload}) is expected
