"""Terminal actions over real SQL decisions and the existing app-action runner."""
import copy
import json
import uuid

import pytest
from psycopg.types.json import Jsonb

import business_logic as logic
import business_logic_actions as actions
import business_logic_runtime as runtime
import workspace_sql
from app_actions import ActionError
from test_business_logic import database
from test_business_logic_runtime import factory, DOC

SEED='terminal-action-test-only'
SQL="INSERT INTO calliope_apps.logic_action_test(note) VALUES($1->'input'->>'message') RETURNING id,note"


@pytest.fixture
def saved(factory):
    workspace_sql.execute(factory,workspace_sql.plan('CREATE TABLE IF NOT EXISTS calliope_apps.logic_action_test(id bigint GENERATED ALWAYS AS IDENTITY,note text); DELETE FROM calliope_apps.logic_action_test'),seed=SEED,schema_allowed=lambda _:True)
    doc=copy.deepcopy(DOC)
    doc['tree']['yes']['action']={'kind':'sql','label':'Record request','sql':SQL,'external_effects':'read_only'}
    doc['tree']['no']['action']={'kind':'callie','label':'Review request','instruction':'Review the supplied request.'}
    return logic.save(factory,'alice',{'definition':doc})


def execute(factory,saved,message='ready',key=None,**kwargs):
    return actions.execute(factory,'alice',{'id':saved['id'],'version':1,'context':{'message':message},'request_key':key or str(uuid.uuid4())},seed=SEED,schema_allowed=lambda _:True,**kwargs)


def test_preview_and_named_sql_never_execute_actions(factory,saved):
    result=logic.evaluate(factory,'alice',{'id':saved['id'],'version':1,'context':{'message':'ready'}})
    assert result['decision']['label']=='Ready'
    runtime.publish(factory,'alice',saved['id'],1)
    assert runtime.run(factory,'alice',DOC['key'],{'message':'ready'})['decision']['label']=='Ready'
    sql,_=logic.compile_query({'tree':saved['definition']['tree'],'source_sql':"SELECT 'ready' AS message"})
    assert 'INSERT INTO' not in sql
    with factory() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.logic_action_test').fetchone()['n']==0
    card=runtime.capability(runtime.resolve(factory,'alice',DOC['key']))
    assert len(card['actions'])==2 and card['execution']['tool']=='execute_business_logic'


def test_sql_action_commits_and_receipt_replay_does_not_rerun(factory,saved,monkeypatch):
    key=str(uuid.uuid4());first=execute(factory,saved,key=key)
    assert first['receipt']['status']=='complete',first
    assert first['action']['result']['rows'][0]['note']=='ready'
    assert first['action']['node']=='root/yes'
    monkeypatch.setattr(actions.ui_decisions,'evaluate',lambda *a:pytest.fail('Replay must not rescore'))
    replay=execute(factory,saved,key=key)
    assert replay['replayed'] and replay['action']==first['action']
    with factory() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.logic_action_test').fetchone()['n']==1
    receipt=runtime.read_receipt(factory,'alice',key)
    assert receipt['action']['input']['input']=={'message':'ready'}
    assert receipt['definitions'][DOC['key']+'@1']['definition']['tree']['yes']['action']['sql']==SQL
    with pytest.raises(ActionError):execute(factory,saved,message='different',key=key)


def test_resume_after_sql_commit_recovers_existing_action(factory,saved,monkeypatch):
    class Crash(BaseException):pass
    key=str(uuid.uuid4())
    def crash(*args,**kwargs):
        actions.sql_action(*args,**kwargs)
        raise Crash()
    with pytest.raises(Crash):execute(factory,saved,key=key,dispatch_sql=crash)
    monkeypatch.setattr(actions.ui_decisions,'evaluate',lambda *a:pytest.fail('Resume must retain decision'))
    result=execute(factory,saved,key=key)
    assert result['receipt']['status']=='complete'
    assert result['action']['result']['replayed']
    with factory() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.logic_action_test').fetchone()['n']==1


def test_callie_branch_receives_only_explicit_instruction_and_recorded_packet(factory,saved):
    calls=[]
    def callie(factory,actor,snap,action,packet,key):
        calls.append((actor,action,packet,key))
        return {'status':'queued','session_id':'test-session','url':'/calliope?session=test-session'}
    result=execute(factory,saved,'unclear',dispatch_callie=callie)
    assert result['action']['status']=='queued' and result['action']['node']=='root/no'
    assert calls[0][2]['decision']['label']=='Review' and calls[0][2]['input']['message']=='unclear'
    with factory() as c:assert c.execute('SELECT count(*) n FROM calliope_apps.logic_action_test').fetchone()['n']==0


def test_missing_and_invalid_inputs_stop_before_action(factory,saved):
    for context,status in [({},'missing_input'),({'message':42},'invalid_input')]:
        result=actions.execute(factory,'alice',{'id':saved['id'],'version':1,'context':context,'request_key':str(uuid.uuid4())},seed=SEED,schema_allowed=lambda _:True)
        assert result['decision']['status']==status and result['receipt']['status']=='stopped'
        assert result['action'] is None


def test_action_failure_keeps_decision_and_failed_receipt(factory,saved):
    def fail(*a,**k):raise ActionError('EXTERNAL_OUTCOME_UNKNOWN','Inspect the service result',409)
    result=execute(factory,saved,dispatch_sql=fail)
    assert result['decision']['label']=='Ready'
    assert result['receipt']['status']=='unknown' and result['action']['code']=='EXTERNAL_OUTCOME_UNKNOWN'
    assert runtime.read_receipt(factory,'alice',result['receipt']['id'])['error']=='Inspect the service result'


@pytest.mark.parametrize('action',[
 {'kind':'sql','sql':'DELETE FROM private.customer'},
 {'kind':'sql','sql':'CREATE TABLE calliope_apps.x(id int)'},
 {'kind':'sql','sql':'DELETE FROM calliope_apps.x; DELETE FROM calliope_apps.y'},
 {'kind':'sql','sql':SQL.replace('$1','$2')},
 {'kind':'callie','instruction':''},
 {'kind':'callie','instruction':'hi','tool':'anything'},
])
def test_action_definition_reuses_existing_limits(action):
    with pytest.raises(ValueError):actions.trees({'kind':'leaf','label':'x','action':action})


def test_action_ownership_and_exact_version(factory,saved):
    with pytest.raises(ActionError):
        actions.execute(factory,'bob',{'id':saved['id'],'version':1,'context':{},'request_key':str(uuid.uuid4())},seed=SEED,schema_allowed=lambda _:True)
    altered=copy.deepcopy(saved['definition']);altered['tree']['yes']['action']['sql']=SQL.replace("->>'message'","->>'missing'")
    logic.save(factory,'alice',{'id':saved['id'],'version':1,'definition':altered})
    assert execute(factory,saved)['action']['result']['rows'][0]['note']=='ready'


def test_callie_handoff_uses_existing_mailbox_once(factory,saved,monkeypatch):
    import calliope
    sid=str(uuid.uuid4());created=[]
    monkeypatch.setattr(calliope,'_create_session_record_sync',lambda *a:created.append(a) or {'id':sid})
    with factory() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS rvbbit.calliope_agent_messages(
          id uuid PRIMARY KEY,owner_email text,kind text,from_kind text,from_ref text,from_label text,
          to_session_id uuid,subject text,body text,payload jsonb)''')
    key=str(uuid.uuid4());result=execute(factory,saved,'review',key=key)
    assert result['action']['status']=='queued' and len(created)==1
    with factory() as c:
        message=c.execute('SELECT * FROM rvbbit.calliope_agent_messages WHERE id=%s',(key,)).fetchone()
    assert message['owner_email']=='alice' and message['payload']['business_logic_execute']
    assert 'Review the supplied request.' in message['body'] and '"message": "review"' in message['body']
    assert execute(factory,saved,'review',key=key)['replayed'] and len(created)==1


def test_published_tool_uses_same_executor_and_stage_receipt(factory,saved):
    import calliope
    runtime.publish(factory,'alice',saved['id'],1)
    tools={};logged=[]
    class MCP:
        def tool(self,name):
            def register(fn):tools[name]=fn;return fn
            return register
    def log(name,args,fn):logged.append(name);return fn()
    logic.register_tools(MCP(),factory,lambda:'alice',log,
        lambda actor,body:actions.execute(factory,actor,body,seed=SEED,schema_allowed=lambda _:True))
    result=tools['execute_business_logic'](DOC['key'],{'message':'ready'},str(uuid.uuid4()),1)
    assert result['action']['result']['rows'][0]['note']=='ready'
    assert logged==['execute_business_logic']
    surfaces=calliope._project_tool_result('execute_business_logic',result,{},'action-test')
    assert surfaces[0]['payload']['receipt']['action']['status']=='complete'
    assert 'input' not in surfaces[0]['payload']['receipt']['action']
