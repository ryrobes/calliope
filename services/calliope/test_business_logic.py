"""Real Postgres persistence, actor isolation, revision conflicts and bounded evaluation."""
import copy
import json
import subprocess
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import business_logic as logic
import business_logic_web as web
from app_actions import ActionError

DOC={'name':'Follow-up','topic':'Service','tree':{'kind':'branch','question':'Same issue?',
     'yes':{'kind':'leaf','label':'Follow-up'},'no':{'kind':'leaf','label':'New work'}},
     'examples':[{'name':'Repeat','context':{'message':'Still broken'},'expected':'Follow-up'}]}

@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root=tmp_path_factory.mktemp('logic-pg');sock=root/'socket';sock.mkdir()
    subprocess.run(['initdb','-D',str(root/'db'),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(root/'db'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def factory():return psycopg.connect(host=str(sock),dbname='postgres',autocommit=True,row_factory=dict_row)
    try:
        with factory() as conn:conn.execute('CREATE SCHEMA rvbbit')
        logic.ensure(factory)
        yield factory
    finally:
        subprocess.run(['pg_ctl','-D',str(root/'db'),'-m','immediate','-w','stop'],check=True,capture_output=True)

@pytest.fixture
def factory(database):
    with database() as conn:conn.execute('TRUNCATE rvbbit.calliope_business_logic_versions,rvbbit.calliope_business_logic')
    return database


def test_save_read_search_isolation_and_versions(factory):
    first=logic.save(factory,'alice',{'definition':DOC})
    assert first['version']==1 and first['definition']['tree']['threshold']==.5
    assert 'threshold' not in DOC['tree']
    assert logic.catalog(factory,'alice','service')['items'][0]['id']==first['id']
    assert not logic.catalog(factory,'bob')['items']
    for action in [lambda:logic.read(factory,'bob',first['id']),lambda:logic.save(factory,'bob',{'id':first['id'],'version':1,'definition':DOC}),
                   lambda:logic.evaluate(factory,'bob',{'id':first['id'],'context':{}})]:
        with pytest.raises(ActionError) as e:action()
        assert e.value.status==404
    changed=copy.deepcopy(DOC);changed['tree']['yes']['label']='Revisit'
    second=logic.save(factory,'alice',{'id':first['id'],'version':1,'definition':changed})
    assert second['version']==2
    old=logic.read(factory,'alice',first['id'],1)
    assert old['definition']['tree']['yes']['label']=='Follow-up' and old['current_version']==2
    with pytest.raises(ActionError) as e:logic.save(factory,'alice',{'id':first['id'],'version':1,'definition':DOC})
    assert e.value.status==409
    assert logic.read(factory,'alice',first['id'])['definition']['tree']['yes']['label']=='Revisit'
    with factory() as conn:assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_business_logic_versions').fetchone()['n']==2


def test_pinned_evaluation_and_inline_unsaved_keep_definitions_unchanged(factory,monkeypatch):
    first=logic.save(factory,'alice',{'definition':DOC})
    changed=copy.deepcopy(DOC);changed['tree']['yes']['label']='New version'
    logic.save(factory,'alice',{'id':first['id'],'version':1,'definition':changed})
    calls=[]
    def evaluate(conn,body,*,capture_usage=False):
        assert capture_usage
        calls.append(body)
        return {'decision':{'status':'complete','label':body['tree']['yes']['label'],'path':[]},'elapsed_ms':3}
    monkeypatch.setattr(logic.ui_decisions,'evaluate',evaluate)
    result=logic.evaluate(factory,'alice',{'id':first['id'],'version':1,'context':{'note':'old'}})
    assert result['version']==1 and result['decision']['label']=='Follow-up'
    assert calls[-1]['state']=={'note':'old'}
    result=logic.evaluate(factory,'alice',{'tree':DOC['tree'],'context':{}})
    assert result['id'] is None and len(logic.catalog(factory,'alice')['items'])==1
    monkeypatch.setattr(logic.ui_decisions,'evaluate',lambda *a,**k:{'decision':{'status':'unavailable','label':None},'elapsed_ms':1})
    assert logic.evaluate(factory,'alice',{'tree':DOC['tree'],'context':{}})['decision']['label'] is None


@pytest.mark.parametrize('change',[
 {'sql':'SELECT secret'}, {'name':''}, {'topic':[]}, {'tree':{'kind':'sql','sql':'SELECT 1'}},
 {'tree':{'kind':'branch','question':'q','yes':{'kind':'leaf','label':'yes'},'no':{'kind':'leaf','label':'no'},'lookup':'SELECT 1'}},
 {'examples':[{'name':'bad','context':[]}]}, {'examples':[{'name':'bad','context':{'x':'a'*24000}}]},
 {'examples':[{'name':'bad','context':{},'expected':1}]},
])
def test_definition_bounds(change):
    with pytest.raises((ValueError,TypeError)):logic.definition({**DOC,**change})


@pytest.mark.parametrize('body',[
 {'context':{},'tree':DOC['tree'],'id':'a'}, {'context':{}}, {'tree':DOC['tree'],'context':{},'sql':'SELECT 1'},
 {'tree':DOC['tree'],'context':{},'version':1},
])
def test_evaluator_rejects_ambiguous_calls(body):
    with pytest.raises(ValueError):logic.evaluate(lambda:None,'alice',body)


def test_routes_auth_origin_size_and_errors(factory,monkeypatch):
    import auth,warehouse_theme
    monkeypatch.setattr(auth,'background_layer',lambda *a,**k:'')
    monkeypatch.setattr(warehouse_theme,'account_control',lambda *a,**k:'<span>Account</span>')
    routes=[]
    class Router:
        def custom_route(self,path,methods):
            def wrap(fn):routes.append(Route(path,fn,methods=methods));return fn
            return wrap
    def actor(req):
        who=req.headers.get('x-user')
        return (who,{'identity':who},None) if who else (None,None,JSONResponse({'error':'auth'},401))
    web.register(Router(),factory,actor)
    client=TestClient(Starlette(routes=routes));headers={'x-user':'alice'}
    assert client.get('/meaning/logic',follow_redirects=False).status_code==302
    assert client.get('/api/calliope/business-logic').status_code==401
    query={'tree':DOC['tree'],'source_sql':"SELECT 'Still broken' AS message",'run':False}
    assert client.post('/api/calliope/business-logic/sql',json=query).status_code==401
    assert client.post('/api/calliope/business-logic/sql',json=query,headers={**headers,'Origin':'https://evil.example'}).status_code==403
    prepared=client.post('/api/calliope/business-logic/sql',json=query,headers=headers)
    assert prepared.status_code==200 and 'rvbbit.decision_tree' in prepared.json()['sql']
    assert client.get('/meaning/logic/business-logic.js').status_code==401
    page=client.get('/meaning/logic',headers=headers)
    assert page.status_code==200 and '__LOGIC_' not in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert client.post('/api/calliope/business-logic',json={'definition':DOC},headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/calliope/business-logic',content='{}',headers=headers).status_code==403
    assert client.post('/api/calliope/business-logic',json={'data':'x'*180000},headers=headers).status_code==413
    saved=client.post('/api/calliope/business-logic',json={'definition':DOC},headers=headers).json()
    assert saved['version']==1
    assert client.get('/api/calliope/business-logic/'+saved['id'],headers={'x-user':'bob'}).status_code==404
    assert client.get('/api/calliope/business-logic/'+saved['id']+'?version=banana',headers=headers).status_code==400
    usage=logic.usage.record(factory,'alice',[{'usage':[{'node':'root','question':'Ready?','cache':'l2','receipt_id':None}]}])
    cost_url='/api/calliope/business-logic/usage/'+usage['id']
    assert client.get(cost_url).status_code==401
    assert client.get(cost_url,headers={'x-user':'bob'}).status_code==404
    measured=client.get(cost_url,headers=headers)
    assert measured.status_code==200 and measured.json()['rows'][0]['cached']==1
    assert measured.json()['known_usd']==0 and not measured.json()['pending']
    monkeypatch.setattr(logic,'evaluate',lambda *a:(_ for _ in ()).throw(RuntimeError('password=secret')))
    response=client.post('/api/calliope/business-logic/test',json={'context':{},'tree':DOC['tree']},headers=headers)
    assert response.status_code==503 and 'secret' not in response.text


def test_tools_use_caller_and_explicit_scopes(factory,monkeypatch):
    tools={}
    class Mcp:
        def tool(self,name):
            def register(fn):tools[name]=fn;return fn
            return register
    logic.register_tools(Mcp(),factory,lambda:'alice')
    saved=tools['save_business_logic'](DOC)
    assert tools['read_business_logic']('Service')['items'][0]['id']==saved['id']
    assert tools['read_business_logic'](logic_id=saved['id'])['definition']['name']==DOC['name']
    denied={}
    class Anonymous:
        def tool(self,name):
            def register(fn):denied[name]=fn;return fn
            return register
    logic.register_tools(Anonymous(),factory,lambda:None)
    with pytest.raises(ActionError):denied['read_business_logic']()


def test_named_inputs_survive_saved_revisions(factory):
    doc=copy.deepcopy(DOC);doc['tree']['inputs']=['message'];doc['source_sql']="SELECT 'ready' AS message"
    first=logic.save(factory,'alice',{'definition':doc})
    assert first['definition']['tree']['inputs']==['message']
    assert logic.read(factory,'alice',first['id'])['definition']['source_sql']==doc['source_sql']
    doc['tree']['inputs']=['message','visit_notes']
    second=logic.save(factory,'alice',{'id':first['id'],'version':1,'definition':doc})
    assert second['definition']['tree']['inputs']==['message','visit_notes']
    assert logic.read(factory,'alice',first['id'],1)['definition']['tree']['inputs']==['message']
