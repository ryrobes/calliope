"""SQL copies keep saved runs immutable and use the governed reader path."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
import data_explorer as data
import data_sql
import server
from test_data_explorer import explorer, build  # noqa:F401
from test_workspace_sql import db, OWNER  # noqa:F401

@pytest.mark.parametrize('sql', [
 'DELETE FROM sales.orders','SELECT 1; SELECT 2',
 'WITH x AS (DELETE FROM sales.orders RETURNING *) SELECT * FROM x',
 'SELECT * FROM sales.secret','SELECT * FROM pg_catalog.pg_authid',
 "SELECT pg_read_file('/etc/passwd')","SELECT rvbbit.unregistered('x')",
 "SELECT public.judge('x')","SELECT judge('x')",
 'WITH x AS (WITH secret AS (SELECT 1) SELECT * FROM secret) SELECT * FROM secret',
])
def test_manual_boundary(sql):
    with pytest.raises(ValueError):data_sql.prepare(sql,{'sales.orders':{}},[{'name':'judge'}])

def test_manual_sources_ctes_and_registered_operators():
    assert data_sql.prepare('SELECT 42;',{},[])=={'sql':'SELECT 42','sources':[]}
    result=data_sql.prepare("WITH x AS (SELECT * FROM sales.orders) SELECT rvbbit.judge(amount::text) FROM x",{'sales.orders':{}},[{'name':'judge'}])
    assert result['sources']==['sales.orders']
    with pytest.raises(ValueError):data.guard_sql(result['sql'],result['sources'])

def test_manual_branches_immutable_runs_and_rechecks_access(explorer):
    exp,ops,_,_=explorer
    original=build(explorer);exp.run(OWNER,'reader',original['id'])
    frozen=exp.get(OWNER,original['id']);normal=exp.service;allowed={'sales.orders':{}}
    def service(op,args,subject,owner):
        if op=='sql_prepare':return data_sql.prepare(args['sql'],allowed,[])
        if op=='sql_run':
            data_sql.prepare(args['sql'],allowed,[])
            return normal('run',args,subject,owner)
        if op=='sql_explain':return {'plan':{'Plan':{'Node Type':'Aggregate'}}}
        return normal(op,args,subject,owner)
    exp.service=service
    result=exp.manual(OWNER,'reader',{'previous_id':str(original['id']),'sql':'SELECT count(*) AS n FROM sales.orders','title':'Count'})
    assert result['context']['sql_editor'] is True
    assert result['previous_id']==original['id']
    assert result['latest_run']['result']['rows']==[{'n':2}]
    assert exp.get(OWNER,original['id'])==frozen
    with pytest.raises(LookupError):exp.manual('other@example.com','other',{'previous_id':str(original['id']),'sql':'SELECT 1'})
    before=len(exp.recent(OWNER))
    assert exp.explain(OWNER,'reader',{'sql':'SELECT 1'})['plan']['Plan']['Node Type']=='Aggregate'
    assert len(exp.recent(OWNER))==before
    allowed.clear()
    with pytest.raises(ValueError):exp.manual(OWNER,'reader',{'sql':'SELECT * FROM sales.orders'})

def test_sql_service_role_readonly_explain_and_no_execution(monkeypatch):
    executed=[];records=[];connections=[]
    class Connection:
        autocommit=True
        read_only=False
        def execute(self,sql,*args):
            executed.append((sql,self.read_only))
            return SimpleNamespace(fetchone=lambda:{'QUERY PLAN':[{'Plan':{'Node Type':'Result'}}]})
    @contextmanager
    def connect(**kw):
        connections.append(kw);yield Connection()
    monkeypatch.setattr(server,'_conn',connect)
    monkeypatch.setattr(server,'_session_pg_role',lambda subject:'reader_role' if subject=='reader' else None)
    monkeypatch.setattr(data,'inventory',lambda *args:[{'relation':'sales.orders'}])
    monkeypatch.setattr(data_sql,'operators',lambda factory,role:[])
    monkeypatch.setattr(server,'tool_validate_sql',lambda *args,**kw:{'valid':True,'safe_select':True,'engine':'postgres'})
    monkeypatch.setattr(server,'_postgres_fallback_needs_heap',lambda *args:False)
    monkeypatch.setattr(server,'_record',lambda *args,**kw:records.append(args))
    runs=[]
    monkeypatch.setattr(server,'tool_run_sql',lambda sql,**kw:runs.append((sql,kw,server._SESSION_SUB.get())) or {'rows':[]})
    result=server._calliope_data_service('sql_explain',{'sql':'SELECT 1'},'reader',OWNER)
    assert result['plan']['Plan']['Node Type']=='Result'
    assert connections==[{'role':'reader_role'}]
    assert executed[-1]==('EXPLAIN (SEMANTIC, FORMAT JSON) SELECT 1',True)
    assert not runs and records[-1][0]=='data.sql_explain'
    server._calliope_data_service('sql_run',{'sql':'SELECT 1'},'reader',OWNER)
    assert runs==[('SELECT 1',{'as_of':None,'limit':1000,'timeout_ms':30000},'reader')]
    with pytest.raises(ValueError):server._calliope_data_service('sql_run',{'sql':'SELECT * FROM sales.secret'},'reader',OWNER)
    assert len(runs)==1


def test_sql_publication_and_reimport_keep_exact_query_and_result(explorer,db,monkeypatch):
    import calliope
    from uuid import uuid4
    from test_workspace_sql import SESSION
    exp,ops,_,_=explorer
    normal=exp.service
    def service(op,args,subject,owner):
        if op in ('sql_prepare','sql_validate'):return data_sql.prepare(args['sql'],{},[])
        return normal('run' if op=='sql_run' else op,args,subject,owner)
    exp.service=service
    draft=exp.manual(OWNER,'reader',{'sql':'SELECT 42 AS n','title':'Answer'})
    monkeypatch.setattr(calliope.calliope_access,'require_participant',calliope.calliope_access.require_view)
    def insert(factory,sid,tid,surfaces):
        surface=dict(surfaces[0],id=str(uuid4()),session_id=sid)
        with factory() as c:c.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,payload,source,kind,title) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,%s)',
            (surface['id'],sid,data.encode(surface['payload']),data.encode(surface['source']),surface['kind'],surface['title']))
        return [surface]
    monkeypatch.setattr(calliope,'_insert_surfaces',insert)
    published=calliope._publish_data_run(db,exp.service,OWNER,'reader',str(draft['latest_run']['id']),SESSION)['surface']
    assert published['source']['sql_editor'] is True
    assert published['source']['sql']=='SELECT 42 AS n'
    assert published['payload']['rows']==[{'n':42}]
    before=len([op for op in ops if op[0]=='run'])
    imported=exp.from_surface(OWNER,'reader',published['id'])
    assert imported['context']['sql_editor'] is True
    assert imported['status']=='ready' and imported['latest_run']['result']['saved_snapshot']
    assert len([op for op in ops if op[0]=='run'])==before
