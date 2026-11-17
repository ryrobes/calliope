"""Ops reads real records without widening access or inventing business outcomes."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import ops_work


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root=tmp_path_factory.mktemp('ops-pg'); sock=root/'socket';sock.mkdir()
    subprocess.run(['initdb','-D',str(root/'db'),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(root/'db'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def factory():return psycopg.connect(host=str(sock),dbname='postgres',autocommit=True,row_factory=dict_row)
    with factory() as conn:
        conn.execute('''CREATE SCHEMA rvbbit;
          CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,title text,owner_email text,updated_at timestamptz DEFAULT now(),purpose text DEFAULT 'chat',archived boolean DEFAULT false);
          CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,ordinal int,status text DEFAULT 'complete');
          CREATE TABLE rvbbit.calliope_session_synopses(session_id uuid PRIMARY KEY,synopsis text,through_ordinal int,updated_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,kind text,title text,lineage_key text,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_input_requests(id uuid PRIMARY KEY,session_id uuid,request jsonb,status text,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.test_shares(session_id uuid,email text);
          CREATE FUNCTION rvbbit.calliope_session_can_view(sid uuid,viewer text,unused boolean) RETURNS boolean LANGUAGE sql AS $$
            SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions WHERE id=sid AND owner_email=viewer)
              OR EXISTS(SELECT 1 FROM rvbbit.test_shares WHERE session_id=sid AND email=viewer) $$;
        ''')
    yield factory
    subprocess.run(['pg_ctl','-D',str(root/'db'),'-m','immediate','-w','stop'],check=True,capture_output=True)


@pytest.fixture
def seeded(database):
    with database() as conn:
        conn.execute('TRUNCATE rvbbit.calliope_sessions,rvbbit.calliope_turns,rvbbit.calliope_session_synopses,rvbbit.calliope_surfaces,rvbbit.calliope_input_requests,rvbbit.test_shares')
        ids={key:str(uuid4()) for key in ['own','shared','private','archived','setup','old','empty']}
        for key,sid in ids.items():
            conn.execute('INSERT INTO rvbbit.calliope_sessions(id,title,owner_email,archived,purpose) VALUES(%s,%s,%s,%s,%s)',
                         (sid,key,'someone@example.com' if key in ('shared','private') else 'admin@example.com',key=='archived','setup' if key=='setup' else 'chat'))
            if key!='empty':conn.execute('INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal) VALUES(%s,%s,1)',(uuid4(),sid))
        conn.execute("UPDATE rvbbit.calliope_sessions SET updated_at=now()-interval '60 days' WHERE id=%s",(ids['old'],))
        conn.execute("INSERT INTO rvbbit.test_shares VALUES(%s,'admin@example.com')",(ids['shared'],))
        conn.execute("INSERT INTO rvbbit.calliope_session_synopses VALUES(%s,'Check stock before contacting customers',1,now())",(ids['own'],))
        for kind,key in [('artifact','dash'),('artifact','dash'),('image','photo'),('query','q1'),('query','q2')]:
            conn.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,lineage_key) VALUES(%s,%s,%s,%s,%s)',(uuid4(),ids['own'],kind,key,key))
        conn.execute("INSERT INTO rvbbit.calliope_input_requests VALUES(%s,%s,'{\"title\":\"Approve order\",\"answers\":\"secret\"}','open',now())",(uuid4(),ids['own']))
        conn.execute("INSERT INTO rvbbit.calliope_input_requests VALUES(%s,%s,'{}','answered',now())",(uuid4(),ids['own']))
    return database,ids


def test_access_version_counts_and_request_projection(seeded):
    factory,ids=seeded
    result=ops_work.snapshot(factory,'admin@example.com')
    assert {x['id'] for x in result['observations']}=={ids['own'],ids['shared']}
    assert result['totals']=={'observations':2,'results':2,'open_requests':1}
    item=next(x for x in result['observations'] if x['id']==ids['own'])
    assert item['summary_is_current'] is True
    assert item['requests'][0]['title']=='Approve order'
    assert 'secret' not in json.dumps(result)
    assert 'revenue' not in result['totals'] and 'completed' not in result['totals']


def test_period_stale_summary_and_empty_state(seeded):
    factory,ids=seeded
    with factory() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,status) VALUES(%s,%s,2,'running')",(uuid4(),ids['own']))
    result=ops_work.snapshot(factory,'admin@example.com',90)
    assert len(result['observations'])==3
    item=next(x for x in result['observations'] if x['id']==ids['own'])
    assert item['active'] is True and item['summary_is_current'] is False
    assert ops_work.snapshot(factory,'nobody@example.com')['totals']=={'observations':0,'results':0,'open_requests':0}
    with pytest.raises(ValueError):ops_work.snapshot(factory,'admin@example.com',-1)


def test_bounded_observations_report_truncation(seeded,monkeypatch):
    monkeypatch.setattr(ops_work,'LIMIT',1)
    result=ops_work.snapshot(seeded[0],'admin@example.com')
    assert result['has_more'] is True and len(result['observations'])==1
    assert result['totals']['results']==result['observations'][0]['result_count']


def test_routes_require_admin_and_do_not_mask_data_failure(monkeypatch,seeded):
    routes=[]
    class Mcp:
        def custom_route(self,path,methods):
            def register(fn):routes.append(Route(path,fn,methods=methods));return fn
            return register
    def canonical(request):return request.headers.get('x-user'),{'mapped':True}
    def api_owner(request):
        owner=canonical(request)[0]
        return (owner,None) if owner else (None,JSONResponse({'error':{'code':'UNAUTHORIZED'}},401))
    monkeypatch.setattr(ops_work.calliope_dreams,'is_company_admin',lambda factory,owner:owner=='admin@example.com')
    auth=SimpleNamespace(background_layer=lambda opacity,veil,scene_key:'')
    theme=SimpleNamespace(account_control=lambda *args,**kwargs:'<span>Account</span>',
                          workspace_navigation=lambda active,**kwargs:f'<a href="/ops" data-active="{active}">Ops</a>')
    ops_work.register(Mcp(),seeded[0],api_owner,JSONResponse,canonical,lambda *args:JSONResponse({}),auth,theme,Path(__file__).parent/'calliope','test')
    with TestClient(Starlette(routes=routes)) as client:
        assert client.get('/ops',follow_redirects=False).status_code==302
        for url in ('/api/calliope/ops','/ops/ops.js','/ops/ops.css'):
            assert client.get(url).status_code==401
        for url in ('/ops','/api/calliope/ops','/ops/ops.js','/ops/ops.css'):
            assert client.get(url,headers={'x-user':'member@example.com'}).status_code==403
        headers={'x-user':'admin@example.com'}
        page=client.get('/ops',headers=headers)
        assert page.status_code==200 and '__CALLIOPE_' not in page.text
        assert 'Business, in motion.' in page.text and 'data-active="ops"' in page.text
        assert client.get('/api/calliope/ops',headers=headers).json()['totals']['observations']==2
        assert client.get('/api/calliope/ops?days=2',headers=headers).status_code==400
        def broken(*args):raise RuntimeError('database password must never leak')
        monkeypatch.setattr(ops_work,'snapshot',broken)
        failure=client.get('/api/calliope/ops',headers=headers)
        assert failure.status_code==503 and 'password' not in failure.text
