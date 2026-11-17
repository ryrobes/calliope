"""SQL composition and execution boundary for the graph workbench."""
import contextvars
import json
from types import SimpleNamespace

import pytest
import business_logic as logic
from test_decision_tree_inputs import database, db, TREE, calls

SOURCE="SELECT * FROM (VALUES ('one', 'ready'), ('two', 'ready')) AS requests(request_id, message)"

def body(**kw):return {'tree':TREE,'source_sql':SOURCE,'limit':12,**kw}


def test_query_executes_scalar_in_postgres_once_per_source_row(db):
    query,limit=logic.compile_query(body())
    assert 'rvbbit.branch(' in query and 'rvbbit.decision_tree(to_jsonb(source_rows)' in query
    results=db.execute(query).fetchall()
    assert len(results)==2 and all(r['outcome']=='Done' for r in results)
    assert [r['input']['request_id'] for r in results]==['one','two']
    assert len(calls(db))==2 and calls(db)[0]['evidence']=={'message':'ready'}
    assert all(r['decision']['path'][0]['inputs']==['message'] for r in results)


def test_limit_applies_before_inference_and_sql_literals_are_data(db):
    tree={**TREE,'question':"Is O'Brien ready? '); SELECT nope --",'yes':{'kind':'leaf','label':"O'Brien's outcome"}}
    query,_=logic.compile_query(body(tree=tree,limit=1))
    results=db.execute(query).fetchall()
    assert len(results)==1 and results[0]['outcome']=="O'Brien's outcome"
    assert len(calls(db))==1


def test_multiway_constructors_match_json_interpreter(db):
    tree={'kind':'choice','question':'Which?', 'inputs':['message'], 'choices':{'next':'Continue','stop':'Stop'}, 'routes':{'next':TREE,'stop':{'kind':'leaf','label':'Stop'}}}
    query,_=logic.compile_query(body(tree=tree,limit=1))
    composed=db.execute(query).fetchone()['decision']
    direct=db.execute('SELECT rvbbit.decision_tree(%s::jsonb,%s::jsonb,4) AS decision',(json.dumps({'request_id':'one','message':'ready'}),json.dumps(tree))).fetchone()['decision']
    assert composed==direct


def test_inline_export_runs_nested_bound_tree_without_outcome_actions(db):
    tree={'kind':'choice', 'question':"What's next for O'Brien?", 'inputs':['message'],
          'choices':{'next':'Continue','stop':'Stop'},
          'routes':{'next':{**TREE, 'yes':{'kind':'leaf', 'label':"O'Brien's result",
                                         'action':{'kind':'callie', 'instruction':'Follow up'}}},
                    'stop':{'kind':'leaf','label':'Stop'}}}
    exported=logic.query_rows(None,'alice',body(tree=tree))
    assert calls(db)==[]  # Exporting SQL never evaluates the tree.
    inline=exported['inline_sql']
    actual=db.execute(inline).fetchall()
    expected=db.execute(exported['sql']).fetchall()
    assert [r['decision'] for r in actual]==[r['decision'] for r in expected]
    assert all(r['decision']['label']=="O'Brien's result" for r in actual)
    assert 'Follow up' not in inline and '"action"' not in inline
    assert all(call['evidence']=={'message':'ready'} for call in calls(db))


def test_complete_inline_export_preserves_source_cte_comments_and_both_limits(db):
    source="""WITH requests AS (
  SELECT i AS request_id, 'ready'::text AS message
  FROM generate_series(1,25) AS ids(i)
)
SELECT *, 'a literal; semicolon' AS note
FROM requests ORDER BY request_id DESC LIMIT 12; -- retain this comment"""
    exported=logic.query_rows(None,'alice',body(source_sql=source,limit=3))
    actual=db.execute(exported['inline_sql']).fetchall()
    assert len(actual)==3 and len(calls(db))==3
    assert 'a literal; semicolon' in exported['inline_sql']
    assert '-- retain this comment' in exported['inline_sql']
    expected=db.execute(exported['sql']).fetchall()
    assert [r['input']['request_id'] for r in expected]==[25,24,23]
    assert [r['decision'] for r in actual]==[r['decision'] for r in expected]


@pytest.mark.parametrize('source',["DELETE FROM customers", "SELECT 1; SELECT 2", "SELECT 1 INTO stolen", "WITH gone AS (DELETE FROM customers RETURNING *) SELECT * FROM gone", "COPY customers TO '/tmp/x'", ""])
def test_rejects_nonselect_or_multiple_source_statements(source):
    with pytest.raises(ValueError):logic.compile_query(body(source_sql=source))


@pytest.mark.parametrize('change',[{'limit':0},{'limit':100001},{'limit':1.5},{'limit':True},{'run':'true'},{'source_sql':'x'*16001},{'unknown':True}])
def test_bounded_request(change):
    with pytest.raises(ValueError):logic.compile_query(body(**change))


def test_governed_runner_uses_actor_restores_context_and_returns_exact_sql():
    subject=contextvars.ContextVar('subject',default='previous');seen=[]
    def run(query,**kwargs):
        seen.append((subject.get(),query,kwargs))
        return {'rows':[],'columns':[],'elapsed_ms':3}
    server=SimpleNamespace(_SESSION_SUB=subject,tool_run_sql=run,_record=lambda *a,**k:None)
    prepared=logic.query_rows(server,'alice',body())
    assert not seen
    result=logic.query_rows(server,'alice',body(run=True))
    assert seen[0][0]=='alice' and subject.get()=='previous'
    assert result['sql']==prepared['sql']==seen[0][1]
    assert seen[0][2]=={'limit':12,'timeout_ms':600000,'_decision_usage':True}
    def fail(*a,**k):raise RuntimeError('unavailable')
    server.tool_run_sql=fail
    with pytest.raises(RuntimeError):logic.query_rows(server,'alice',body(run=True))
    assert subject.get()=='previous'


def test_hundred_row_batch_reaches_every_input_once(db):
    source="SELECT i AS request_id, 'ready'::text AS message FROM generate_series(1,120) AS s(i)"
    query,_=logic.compile_query(body(source_sql=source,limit=100),max_rows=1000)
    rows=db.execute(query).fetchall()
    assert len(rows)==100 and len(calls(db))==100
    assert all(r['outcome']=='Done' for r in rows)


def test_workbench_uses_the_configured_sql_row_limit():
    server=SimpleNamespace(ROW_CAP=500)
    assert logic.query_rows(server,'alice',body(limit=100))['limit']==100
    assert logic.query_rows(server,'alice',body(limit=500))['limit']==500
    with pytest.raises(ValueError,match='SQL row limit'):
        logic.query_rows(server,'alice',body(limit=501))


def test_example_source_preserves_sql_field_types(db):
    import subprocess
    from pathlib import Path
    module=(Path(__file__).parent/'calliope/business-logic-model.mjs').resolve().as_uri()
    examples=[{'name':'One','context':{'request_id':'external-1','case_name':'Custom','amount':12,'ready':True,'detail':{'part':'capacitor'},'mixed':1}},
              {'name':'Two','context':{'amount':23,'ready':False,'detail':{'part':'fan'},'mixed':'pending'}}]
    js="import {sourceSql} from "+json.dumps(module)+"; console.log(sourceSql("+json.dumps(examples)+"));"
    source=subprocess.check_output(['node','--input-type=module','-e',js],text=True)
    result=db.execute('SELECT to_jsonb(source) AS input FROM ('+source+') source').fetchall()
    assert result[0]['input']=={'request_id':'external-1','case_name':'Custom','amount':12,'ready':True,'detail':{'part':'capacitor'},'mixed':1}
    assert result[1]['input']['mixed']=='pending'
