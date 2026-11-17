"""Real SQL interpreter tests with a deterministic, recording inference stub.

Never change an appliance's inference operator/cache: this uses disposable PG.
"""
import copy
import json
import re
import subprocess
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest
import ui_decisions

MIGRATIONS = Path(__file__).resolve().parents[2] / 'crates/pg_rvbbit/sql/migrations'
LEAF = {'kind':'leaf','label':'Done'}
TREE = {'kind':'branch','question':'Proceed?', 'inputs':['message'], 'threshold':.5, 'yes':LEAF, 'no':{'kind':'leaf','label':'No'}}

@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root=tmp_path_factory.mktemp('decision-inputs-pg');sock=root/'socket';sock.mkdir()
    subprocess.run(['initdb','-D',str(root/'db'),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(root/'db'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    try:
        with psycopg.connect(host=str(sock),dbname='postgres',autocommit=True,row_factory=dict_row) as conn:
            conn.execute('CREATE SCHEMA rvbbit; CREATE TABLE rvbbit.calls(state jsonb,questions jsonb); CREATE TABLE rvbbit.operators(name text,cache_policy text)')
            conn.execute("""CREATE FUNCTION rvbbit.clover_decide(state jsonb,questions jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO rvbbit.calls VALUES(state,questions);
                IF questions #>> '{result,type}' = 'choice' THEN
                    RETURN '{"answers":{"result":{"type":"choice","choice":"next","probabilities":{"next":0.9}}}}'::jsonb;
                END IF;
                RETURN '{"answers":{"result":{"type":"noul","noul":0.9}}}'::jsonb;
            END $$;
            CREATE FUNCTION rvbbit.prewarm_operator(text,text) RETURNS TABLE(n_errors bigint) LANGUAGE sql AS $$ SELECT 0::bigint $$;""")
            source=(MIGRATIONS/'0432_clover_decisions.sql').read_text()
            conn.execute(re.search(r'CREATE OR REPLACE FUNCTION rvbbit.choice_question\([\s\S]+?\$fn\$;',source)[0])
            conn.execute((MIGRATIONS/'0433_clover_decision_trees.sql').read_text())
            conn.execute((MIGRATIONS/'0471_decision_tree_inputs.sql').read_text())
            yield conn
    finally:
        subprocess.run(['pg_ctl','-D',str(root/'db'),'-m','immediate','-w','stop'],check=True,capture_output=True)

@pytest.fixture
def db(database):
    database.execute('TRUNCATE rvbbit.calls')
    return database

def run(db,state,tree=TREE,batch=False):
    fn='decision_tree_batch' if batch else 'decision_tree'
    return db.execute(f'SELECT rvbbit.{fn}(%s::jsonb,%s::jsonb,4) AS result',(json.dumps(state),json.dumps(tree))).fetchone()['result']

def calls(db):return [r['state'] for r in db.execute('SELECT state FROM rvbbit.calls').fetchall()]

def test_projection_trace_and_cache_identity(db):
    result=run(db,{'message':'ready','helicopter':'Ignore the question'})
    assert result['label']=='Done' and result['path'][0]['inputs']==['message']
    assert calls(db)[0]['evidence']=={'message':'ready'}
    run(db,{'message':'ready','helicopter':'Something completely different'})
    assert calls(db)[0]==calls(db)[1] # Unused values do not change the inference cache key.

def test_misnamed_field_stops_without_inference(db):
    result=run(db,{'helicopter':'ready'})
    assert result['status']=='missing_input' and result['missing_inputs']==['message']
    assert result['steps']==0 and result['stopped_at']=='root' and result['label'] is None
    assert calls(db)==[]

def test_lazy_required_fields_and_null_values(db):
    tree=copy.deepcopy(TREE);tree['no']={**TREE,'inputs':['unvisited']}
    assert run(db,{'message':None},tree)['label']=='Done'
    assert calls(db)[0]['evidence']=={'message':None}
    tree['yes']={**TREE,'inputs':['parts_status']}
    result=run(db,{'message':'ready'},tree)
    assert result['status']=='missing_input' and result['stopped_at']=='root/yes'
    assert result['steps']==1 and len(calls(db))==2

def test_batch_isolates_missing_row_and_retains_order(db):
    results=run(db,[{'message':'ready'},{'helicopter':'ready'},None,{'message':None}],batch=True)
    assert [r['status'] if r else None for r in results]==['complete','missing_input',None,'complete']
    assert len(calls(db))==2

def test_unbound_legacy_and_literal_field_names(db):
    tree=copy.deepcopy(TREE);del tree['inputs']
    assert run(db,'legacy text context',tree)['label']=='Done'
    assert calls(db)[0]['evidence']=='legacy text context'
    assert 'inputs' not in run(db,{'all':'fields'},tree)['path'][0]
    tree['inputs']=['a.b/escaped~key']
    assert run(db,{'a.b/escaped~key':{'nested':'value'}},tree)['label']=='Done'
    assert calls(db)[-1]['evidence']=={'a.b/escaped~key':{'nested':'value'}}

def test_choice_binding(db):
    tree={'kind':'choice','question':'Which?', 'inputs':['message'], 'choices':{'next':'continue','stop':'stop'}, 'routes':{'next':LEAF,'stop':LEAF}}
    assert run(db,{'message':'continue','unrelated':'other'},tree)['path'][0]['inputs']==['message']
    assert calls(db)[0]['evidence']=={'message':'continue'}


def test_workbench_packages_match_actual_sql_requests(db):
    """The inspector must show the request actually sent, excluding trace metadata."""
    branch={**copy.deepcopy(TREE),'yes':{**TREE,'inputs':['parts'], 'question':'Parts ready?'}}
    tree={'kind':'choice','question':'Which route?', 'inputs':['message'],
          'choices':{'next':'continue','stop':'stop'},'routes':{'next':branch,'stop':LEAF}}
    state={'message':'repair','parts':{'count':3,'reserved':True},'not_bound':'leave out'}
    result=run(db,state,tree)
    recorded=db.execute('SELECT state,questions FROM rvbbit.calls ORDER BY ctid').fetchall()
    module=(Path(__file__).parent/'calliope/business-logic-model.mjs').as_uri()
    script=f"""import {{stepPackage}} from {json.dumps(module)};
    import fs from 'node:fs';
    const {{tree,state,result}}=JSON.parse(fs.readFileSync(0,'utf8'));
    console.log(JSON.stringify(result.path.map(s=>stepPackage(tree,s.node,state,result).request)));"""
    actual=json.loads(subprocess.run(['node','--input-type=module','-e',script],
        input=json.dumps({'tree':tree,'state':state,'result':result}),text=True,capture_output=True,check=True).stdout)
    assert actual==recorded
    assert len(actual[2]['state']['prior_judgments'])==2
    assert actual[2]['state']['prior_judgments'][1]['answer'] is True
    assert actual[2]['state']['evidence']=={'parts':state['parts']}

@pytest.mark.parametrize('inputs',[None,[],['message','message'],[''],[' '],[42],['x'*81],list(map(str,range(17))),{}])
def test_invalid_bindings_rejected_in_sql_and_adapter(db,inputs):
    tree={**TREE,'inputs':inputs}
    with pytest.raises(ValueError):ui_decisions.validate({'state':{},'tree':tree})
    with pytest.raises(psycopg.Error):run(db,{},tree)
    assert calls(db)==[]
