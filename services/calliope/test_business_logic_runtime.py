"""Named SQL/tool parity and private publication on disposable PostgreSQL."""
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
import business_logic as logic
import business_logic_runtime as runtime
from app_actions import ActionError
from test_business_logic import database

DOC={'name':'Service triage','topic':'HVAC','key':'hvac.service_triage',
     'description':'Decide whether a service request is ready.',
     'inputs':{'message':{'type':'string','description':'Current service request text.'}},
     'tree':{'kind':'branch','question':'Ready?','inputs':['message'],'threshold':.5,
             'yes':{'kind':'leaf','label':'Ready'},'no':{'kind':'leaf','label':'Review'}},'examples':[]}

@pytest.fixture
def factory(database):
    migrations=Path(__file__).resolve().parents[2]/'crates/pg_rvbbit/sql/migrations'
    with database() as conn:
        conn.execute('TRUNCATE rvbbit.calliope_business_logic_versions,rvbbit.calliope_business_logic,rvbbit.calliope_business_logic_runs')
        conn.execute('CREATE TABLE IF NOT EXISTS rvbbit.operators(name text,cache_policy text)')
        conn.execute("""CREATE OR REPLACE FUNCTION rvbbit.clover_decide(state jsonb,questions jsonb) RETURNS jsonb LANGUAGE sql AS $$
          SELECT jsonb_build_object('answers',jsonb_build_object('result',jsonb_build_object('type','noul','noul',CASE WHEN state #>> '{evidence,message}'='ready' THEN .9 ELSE .1 END))) $$;
          CREATE OR REPLACE FUNCTION rvbbit.prewarm_operator(text,text) RETURNS TABLE(n_errors bigint) LANGUAGE sql AS $$ SELECT 0::bigint $$;""")
        source=(migrations/'0432_clover_decisions.sql').read_text()
        conn.execute(re.search(r'CREATE OR REPLACE FUNCTION rvbbit.choice_question\([\s\S]+?\$fn\$;',source)[0])
        conn.execute((migrations/'0433_clover_decision_trees.sql').read_text())
        conn.execute((migrations/'0471_decision_tree_inputs.sql').read_text())
    return database

def publish(factory):
    record=logic.save(factory,'alice',{'definition':DOC})
    return runtime.publish(factory,'alice',record['id'],1)

def test_publication_is_explicit_and_pins_saved_revision(factory):
    first=logic.save(factory,'alice',{'definition':DOC})
    with pytest.raises(ActionError):runtime.resolve(factory,'alice',DOC['key'])
    with pytest.raises(ActionError):runtime.publish(factory,'bob',first['id'],1)
    runtime.publish(factory,'alice',first['id'],1)
    draft=copy.deepcopy(DOC);draft['tree']['yes']['label']='Changed'
    logic.save(factory,'alice',{'id':first['id'],'version':1,'definition':draft})
    assert runtime.resolve(factory,'alice',DOC['key'])['version']==1
    with pytest.raises(ActionError):runtime.resolve(factory,'alice',DOC['key'],2)
    runtime.publish(factory,'alice',first['id'],2)
    assert runtime.resolve(factory,'alice',DOC['key'])['version']==2
    assert runtime.resolve(factory,'alice',DOC['key'],1)['definition']['tree']['yes']['label']=='Ready'
    with pytest.raises(ActionError):runtime.resolve(factory,'bob',DOC['key'])


def test_short_sql_call_follows_publication_and_receipts_keep_the_resolved_version(factory):
    record=publish(factory)
    def evaluate(version=None):
        capture=runtime.Capture('alice','sql')
        with factory() as conn:
            runtime.prepare(conn,factory,'alice',capture)
            conn.autocommit=False;conn.read_only=True
            # Exercise the actual two-argument SQL signature, not just resolve().
            query="SELECT rvbbit.business_logic('hvac.service_triage', '{\"message\":\"ready\"}'::jsonb"+(')' if version is None else f',{version})')+' AS d'
            d=conn.execute(query).fetchone()['d']
        receipt=capture.persist(factory)
        assert receipt['rules'][0]['version']==d['rule']['version']
        return d
    draft=copy.deepcopy(DOC);draft['tree']['yes']['label']='New version'
    logic.save(factory,'alice',{'id':record['id'],'version':1,'definition':draft})
    assert evaluate()['label']=='Ready'
    runtime.publish(factory,'alice',record['id'],2)
    current=evaluate()
    assert current['label']=='New version' and current['rule']['version']==2
    pinned=evaluate(1)
    assert pinned['label']=='Ready' and pinned['rule']['version']==1

def test_tool_sql_parity_aggregate_receipt_and_readonly(factory):
    record=publish(factory)
    tool=runtime.run(factory,'alice',DOC['key'],{'message':'ready'},1)
    assert tool['decision']['status']=='complete' and tool['decision']['label']=='Ready'
    receipt=runtime.read_receipt(factory,'alice',tool['receipt']['id'])
    assert receipt['rows'][0]['input']=={'message':'ready'}
    assert receipt['definitions'][DOC['key']+'@1']['definition']['tree']==DOC['tree']
    capture=runtime.Capture('alice','sql','SELECT aggregate example')
    with factory() as conn:
        runtime.prepare(conn,factory,'alice',capture)
        conn.autocommit=False;conn.read_only=True
        query="""WITH decisions AS MATERIALIZED (
          SELECT rvbbit.business_logic('hvac.service_triage',jsonb_build_object('message',message),1) AS d
          FROM (VALUES ('ready'),('unclear')) r(message))
          SELECT d->>'label' AS outcome,count(*) FROM decisions GROUP BY 1 ORDER BY 1"""
        rows=conn.execute(query).fetchall()
        assert [r['outcome'] for r in rows]==['Ready','Review']
        assert capture.rows[0]['decision']==tool['decision']
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE pg_temp.calliope_logic_bindings SET snapshot='{}'")
        conn.rollback()
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute('ALTER TABLE pg_temp.calliope_logic_bindings DISABLE TRIGGER ALL')
        conn.rollback()
    batch=capture.persist(factory)
    assert batch['evaluations']==2 and batch['outcomes']=={'Ready':1,'Review':1}
    assert len(runtime.read_receipt(factory,'alice',batch['id'])['rows'])==2
    runtime.publish(factory,'alice',record['id'],None)
    with pytest.raises(ActionError):runtime.run(factory,'alice',DOC['key'],{'message':'ready'},1)
    assert runtime.read_receipt(factory,'alice',tool['receipt']['id'])['rows'][0]['decision']['label']=='Ready'
    with pytest.raises(ActionError):runtime.read_receipt(factory,'bob',tool['receipt']['id'])

def test_sql_does_not_trust_guc_or_leak_private_rules(factory):
    publish(factory)
    with factory() as conn:
        conn.execute("SELECT set_config('rvbbit.request_user','alice',false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT rvbbit.business_logic('hvac.service_triage','{}')")
        capture=runtime.Capture('bob','sql');runtime.prepare(conn,factory,'bob',capture)
        assert conn.execute('SELECT count(*) n FROM pg_temp.calliope_logic_bindings').fetchone()['n']==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT rvbbit.business_logic('hvac.service_triage','{}')")
        assert capture.total==0

def test_missing_and_invalid_inputs_are_not_negative_outcomes(factory):
    publish(factory)
    missing=runtime.run(factory,'alice',DOC['key'],{})
    assert missing['decision']['status']=='missing_input' and missing['decision']['label'] is None
    invalid=runtime.run(factory,'alice',DOC['key'],{'message':42})
    assert invalid['decision']['status']=='invalid_input' and invalid['decision']['label'] is None
    assert invalid['receipt']['status']=='partial'
    null=runtime.run(factory,'alice',DOC['key'],{'message':None})
    assert null['decision']['status']=='complete'

def test_capture_bounds_and_unrecognized_notices(factory):
    publish(factory);row=runtime.resolve(factory,'alice',DOC['key'],1);snap=runtime.snapshot(row)
    cap=runtime.Capture('alice','sql');cap.allowed[(DOC['key'],1)]=snap
    decision={'status':'complete','label':'Ready','path':[],'rule':{k:v for k,v in snap.items() if k!='definition'}}
    msg={'run_id':cap.id,'input':{'message':'ready'},'decision':decision}
    for _ in range(210):cap.notice(SimpleNamespace(message_primary='calliope_logic:'+json.dumps(msg)))
    cap.notice(SimpleNamespace(message_primary='calliope_logic:'+json.dumps({**msg,'run_id':'wrong'})))
    assert cap.total==210 and len(cap.rows)==200
    assert cap.persist(factory)['details_truncated']

def test_contract_and_named_composition(factory):
    publish(factory)
    row=runtime.resolve(factory,'alice',DOC['key']);card=runtime.capability(row)
    assert card['invocation']['arguments']=={'rule':DOC['key'],'version':1}
    assert card['inputs']['properties']['message']['type']==['string','null']
    assert 'required' not in card['inputs']
    sql,_=logic.compile_query({'tree':DOC['tree'],'source_sql':"SELECT 'ready' AS message"},(DOC['key'],1))
    assert 'rvbbit.business_logic(' in sql and 'decision_tree(' not in sql and 'decision_definition' not in sql


def test_named_and_draft_exports_retain_their_own_definitions(factory):
    record=publish(factory)
    draft=copy.deepcopy(DOC);draft['tree']['yes']['label']='Draft outcome'
    logic.save(factory,'alice',{'id':record['id'],'version':1,'definition':draft})
    server=SimpleNamespace(_conn=factory)
    source={'source_sql':"SELECT 'ready' AS message"}
    published=logic.query_rows(server,'alice',{'rule':DOC['key'],**source})
    editing=logic.query_rows(server,'alice',{'tree':draft['tree'],**source})
    assert 'rvbbit.business_logic(' in published['sql']
    with factory() as conn:
        # Both exports work independently of saved-rule bindings.
        for exported,outcome in [(published,'Ready'),(editing,'Draft outcome')]:
            inline=exported['inline_sql']
            assert conn.execute(inline).fetchone()['decision']['label']==outcome


def test_named_ui_exports_execute_as_copied_with_current_source_and_limit(factory):
    import subprocess
    record=publish(factory)
    draft=copy.deepcopy(DOC);draft['tree']['yes']['label']='Updated'
    logic.save(factory,'alice',{'id':record['id'],'version':1,'definition':draft})
    runtime.publish(factory,'alice',record['id'],2)
    exported=logic.query_rows(SimpleNamespace(_conn=factory),'alice',{
        'tree':draft['tree'], 'limit':1,
        'source_sql':"SELECT 'ready' AS message FROM generate_series(1,3); -- source"})
    module=(Path(__file__).parent/'calliope/business-logic-model.mjs').resolve().as_uri()
    script="import {namedSql} from "+json.dumps(module)+"; const [key,source]=JSON.parse(process.argv[1]); console.log(JSON.stringify([namedSql(key,source),namedSql(key,source,1)]));"
    queries=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,
        json.dumps([DOC['key'],exported['source_clause_sql']])],text=True))
    for query,version,label in zip(queries,[2,1],['Updated','Ready']):
        capture=runtime.Capture('alice','sql')
        with factory() as conn:
            runtime.prepare(conn,factory,'alice',capture)
            rows=conn.execute(query).fetchall()
        assert len(rows)==1 and len(capture.rows)==1
        assert rows[0]['decision']['rule']['version']==version
        assert rows[0]['decision']['label']==label


def test_stable_key_metadata_and_uniqueness(factory):
    record=logic.save(factory,'alice',{'definition':DOC})
    with pytest.raises(ValueError):logic.save(factory,'alice',{'id':record['id'],'version':1,'definition':{**DOC,'key':'new.key'}})
    with pytest.raises(ValueError):logic.save(factory,'bob',{'definition':DOC})
    for change in [{'key':"bad'); sql"},{'inputs':{'message':{'type':'executable'}}},{'description':[]}]:
        with pytest.raises(ValueError):logic.definition({**DOC,**change})


def test_receipt_stage_projection_preserves_query_and_decision(factory):
    import calliope
    publish(factory)
    value=runtime.run(factory,'alice',DOC['key'],{'message':'ready'})
    card=calliope._project_tool_result('run_business_logic',value,{},'call-1')
    assert len(card)==1 and card[0]['kind']=='action'
    assert card[0]['payload']['mode']=='business_logic_receipt'
    assert card[0]['payload']['receipt']['id']==value['receipt']['id']
    query={'columns':[{'name':'outcome','type':'text'}],'rows':[{'outcome':'Ready'}],'row_count':1,
           'business_logic_receipt':value['receipt']}
    surfaces=calliope._project_tool_result('run_sql',query,{'sql':'SELECT ...'},'call-2')
    assert [s['kind'] for s in surfaces]==['query','action']
    assert surfaces[1]['lineage_key']==card[0]['lineage_key']
    failed=calliope._project_tool_result('run_sql',{'error':{'code':'SQL_ERROR'},'business_logic_receipt':value['receipt']},{},'call-3')
    assert len(failed)==1 and failed[0]['kind']=='action'
