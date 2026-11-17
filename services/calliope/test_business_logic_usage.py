"""Row costs distinguish fresh, cached, pending and subsequently settled calls."""
from decimal import Decimal
import uuid
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb
import business_logic_usage as usage
from app_actions import ActionError
from test_business_logic import database
from test_decision_tree_inputs import database as decision_database


def call(cache='miss', receipt_id='r1', **extra):
    return {'node':'root','question':'Ready?','cache':cache,'receipt_id':receipt_id,'calls':1,**extra}

def test_delayed_partial_settlement_and_cached_rows_do_not_double_charge():
    rows=[{'row':1,'steps':[call(),call('l1',None,cost_usd=99)]},
          {'row':2,'steps':[call('l2',None)]}, {'row':3,'steps':[]}]
    pending=usage.summarize(rows,{})
    assert pending['pending'] and pending['known_usd']==0
    assert pending['rows'][0]['pending'] and pending['rows'][0]['cached']==1
    assert not pending['rows'][1]['pending'] and pending['rows'][1]['known_usd']==0
    assert not pending['rows'][2]['pending'] and pending['rows'][2]['decisions']==0
    settled=usage.summarize(rows,{'r1':[{'status':'settled','cost_usd':Decimal('.001234'),'tokens_in':125,'tokens_out':8}]})
    assert not settled['pending'] and settled['known_usd']==.001234
    assert settled['rows'][0]['known_usd']==.001234
    assert settled['cached']==2 and settled['decisions']==3


def test_partial_subcalls_remain_pending_and_failed_cost_is_retained():
    rows=[{'row':1,'steps':[call(calls=2)]}]
    assert usage.summarize(rows,{'r1':[{'status':'settled','cost_usd':.003}]})['pending']
    result=usage.summarize(rows,{'r1':[{'status':'settled','cost_usd':.003},{'status':'error','cost_usd':.001}]})
    assert result['pending'] and result['known_usd']==.004
    assert usage.summarize([{'row':1,'steps':None}],{})['pending']
    assert not usage.summarize([{'row':1,'steps':[call(calls=0)]}],{})['pending']


def test_private_usage_refresh_reads_latest_cost_without_reexecuting_decisions(database):
    with database() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS rvbbit.cost_latest(receipt_id uuid,sub_call_index int,status text,cost_usd numeric,tokens_in int,tokens_out int)')
    receipt=str(uuid.uuid4())
    first=usage.record(database,'alice',[{'usage':[call(receipt_id=receipt)]}])
    assert first['pending']
    with pytest.raises(ActionError) as error:usage.read(database,'bob',first['id'])
    assert error.value.status==404
    with database() as conn:
        conn.execute('INSERT INTO rvbbit.cost_latest VALUES(%s,0,%s,%s,20,4)',(receipt,'pending',None))
    assert usage.read(database,'alice',first['id'])['pending']
    with database() as conn:
        conn.execute("UPDATE rvbbit.cost_latest SET status='settled',cost_usd=.000041 WHERE receipt_id=%s",(receipt,))
    final=usage.read(database,'alice',first['id'])
    assert not final['pending'] and final['known_usd']==.000041
    # Polling is a pure ledger read and leaves the original accounting references intact.
    with database() as conn:
        raw=conn.execute('SELECT rows FROM rvbbit.calliope_business_logic_usage WHERE id=%s',(first['id'],)).fetchone()['rows']
    assert raw[0]['steps'][0]['receipt_id']==receipt and 'input' not in raw[0]


def test_usage_never_enters_downstream_evidence_and_includes_failed_calls(decision_database):
    conn=decision_database
    from test_decision_tree_inputs import TREE, calls
    migration=Path(__file__).resolve().parents[2]/'crates/pg_rvbbit/sql/migrations/0472_decision_tree_usage.sql'
    conn.execute("""CREATE FUNCTION rvbbit._exec_op_jsonb_with_usage(text,jsonb,jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$
    BEGIN RETURN jsonb_build_object('output',rvbbit.clover_decide($2->'state',$2->'questions'),
      'usage',jsonb_build_object('cache','miss','receipt_id',gen_random_uuid(),'calls',1)); END $$;""")
    conn.execute('CREATE OR REPLACE FUNCTION rvbbit.decision_tree'+migration.read_text().split('CREATE OR REPLACE FUNCTION rvbbit.decision_tree',1)[1])
    nested={**TREE,'yes':TREE}
    with conn.transaction():
        conn.execute("SET LOCAL rvbbit.decision_usage='on'")
        conn.execute('TRUNCATE rvbbit.calls')
        result=conn.execute('SELECT rvbbit.decision_tree(%s,%s,4) d',(Jsonb({'message':'ready'}),Jsonb(nested))).fetchone()['d']
        assert [s['node'] for s in result['usage']]==['root','root/yes']
        assert result['usage'][0]['receipt_id']!=result['usage'][1]['receipt_id']
        for request in calls(conn):
            assert all('usage' not in j and 'receipt_id' not in j for j in request['prior_judgments'])
    ordinary=conn.execute('SELECT rvbbit.decision_tree(%s,%s,4) d',(Jsonb({'message':'ready'}),Jsonb(nested))).fetchone()['d']
    assert 'usage' not in ordinary
    assert {k:v for k,v in result.items() if k!='usage'}==ordinary
    conn.execute("CREATE OR REPLACE FUNCTION rvbbit._exec_op_jsonb_with_usage(text,jsonb,jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN RETURN jsonb_build_object('output',NULL,'usage',jsonb_build_object('cache','miss','receipt_id',gen_random_uuid(),'calls',1)); END $$;")
    with conn.transaction():
        conn.execute("SET LOCAL rvbbit.decision_usage='on'")
        failed=conn.execute('SELECT rvbbit.decision_tree(%s,%s,4) d',(Jsonb({'message':'ready'}),Jsonb(nested))).fetchone()['d']
    assert failed['status']=='unavailable' and failed['steps']==0 and len(failed['usage'])==1
