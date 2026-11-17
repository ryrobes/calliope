"""Request accounting invariants and central-view integration against a disposable DB."""
import json
import os
import uuid
from pathlib import Path

import pytest
import cost_accounting as costs

RATES={'openai/test':{'prompt':2e-6,'completion':8e-6,'input_cache_read':.2e-6,'input_cache_write':2.5e-6,'source':'test'}}

@pytest.fixture(autouse=True)
def prepaid_gateway(monkeypatch):
    monkeypatch.setenv('CALLIOPE_INCLUDED_INFERENCE_HOSTS','clover.example.com')

def event(**extra):
    return {'event':'model.completed','event_id':'event-1','attempt_id':'attempt-1','call_id':'hermes:attempt-1',
        'source':'hermes_ledger','ts':'2026-09-26T04:00:00Z','run_id':'run-test','model':'calliope','response_model':'openai/test',
        'provider':'custom','endpoint_host':'clover.example.com','usage':{'input_tokens':10,'cache_read_tokens':50,'cache_write_tokens':40,
        'prompt_tokens':100,'output_tokens':20,'reasoning_tokens':7},**extra}

def test_cache_buckets_and_reasoning_are_not_double_charged():
    row=costs.prepare(event(),RATES)
    assert row['tokens_in']==100 and row['tokens_out']==20
    assert row['cost_usd']==pytest.approx(10*2e-6+50*.2e-6+40*2.5e-6+20*8e-6)
    assert row['cost_kind']=='included' and row['model']=='openai/test' and row['requested_model']=='calliope'

def test_gateway_calls_are_real_spend_unless_the_host_is_prepaid(monkeypatch):
    monkeypatch.delenv('CALLIOPE_INCLUDED_INFERENCE_HOSTS')
    row=costs.prepare(event(),RATES)
    assert row['cost_kind']=='estimated' and row['cost_usd']>0

def test_provider_charge_zero_unknown_and_missing_cache_tariffs():
    row=costs.prepare(event(reported_cost_usd=0,endpoint_host='api.openai.com',provider='openai'),RATES)
    assert row['cost_usd']==0 and row['cost_kind']=='reported'
    row=costs.prepare(event(response_model='unpriced'),RATES)
    assert row['cost_usd'] is None and row['status']=='uncosted'
    assert costs.price_usage(event()['usage'],{'prompt':2e-6,'completion':8e-6},canonical=True)==pytest.approx(100*2e-6+20*8e-6)
    assert costs.price_usage({'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':50}},RATES['openai/test'])==pytest.approx(50*2e-6+50*.2e-6+20*8e-6)

@pytest.mark.parametrize('value',[True,-1,'nan','inf',None,'secret'])
def test_invalid_costs_do_not_become_money(value):
    assert costs.number(value) is None

@pytest.fixture
def db():
    import psycopg
    from psycopg.rows import dict_row
    dsn=os.environ.get('CALLIOPE_COST_TEST_DSN')
    if not dsn:pytest.skip('Set CALLIOPE_COST_TEST_DSN to a disposable appliance database')
    c=psycopg.connect(dsn,row_factory=dict_row)
    try:
        costs.ensure_schema(c)
        yield c
    finally:
        c.rollback();c.close()

def test_replay_partial_line_and_failed_transaction_never_duplicate(db,tmp_path):
    p=tmp_path/'2026-09-26.jsonl';r=event()
    p.write_text(json.dumps(r)+'\n'+json.dumps(event(event_id='event-2',attempt_id='attempt-2'))[:-2])
    assert costs.sync_ledger(db,tmp_path,RATES)==1
    assert costs.sync_ledger(db,tmp_path,RATES)==0
    assert db.execute('SELECT count(*) AS n FROM rvbbit.model_call_usage').fetchone()['n']==1
    # Complete the trailing append; resetting the cursor still deduplicates calls.
    p.write_text(json.dumps(r)+'\n'+json.dumps(event(event_id='event-2',attempt_id='attempt-2'))+'\n')
    assert costs.sync_ledger(db,tmp_path,RATES)==1
    db.execute('DELETE FROM rvbbit.model_usage_import_state')
    assert costs.sync_ledger(db,tmp_path,RATES)==2
    assert db.execute('SELECT count(*) AS n FROM rvbbit.model_call_usage').fetchone()['n']==2
    p.write_text(json.dumps(event(event_id='event-3',attempt_id='attempt-3'))+'\nnot json\n')
    db.execute('DELETE FROM rvbbit.model_usage_import_state')
    with pytest.raises(json.JSONDecodeError):
        with db.transaction():costs.sync_ledger(db,tmp_path,RATES)
    assert db.execute("SELECT count(*) AS n FROM rvbbit.model_call_usage WHERE call_id='hermes:attempt-3'").fetchone()['n']==0


def test_both_ledger_roots_import_once(db, tmp_path, monkeypatch):
    shared, media = tmp_path/'hermes', tmp_path/'server'
    shared.mkdir(); media.mkdir()
    monkeypatch.setattr(costs, 'LEDGER', shared)
    monkeypatch.setattr(costs, 'MEDIA_LEDGER', media)
    (shared/'day.jsonl').write_text(json.dumps(event())+'\n')
    (media/'day.jsonl').write_text(json.dumps({
        'event':'media.completed', 'event_id':'tags-test', 'operation':'tts.emotion_tags',
        'model':'calliope-luna', 'ts':'2026-09-26T04:00:00Z',
        'usage':{'prompt_tokens':50,'completion_tokens':10,'cost':.001},
        'status':'ok'})+'\n')
    assert costs.sync_ledger(db, catalog=RATES) == 2
    assert costs.sync_ledger(db, catalog=RATES) == 0
    rows=db.execute("SELECT * FROM rvbbit.model_call_usage WHERE call_id='media:tags-test'").fetchall()
    assert len(rows)==1 and rows[0]['category']=='Text to speech'
    assert float(rows[0]['cost_usd']) == pytest.approx(.001)

def test_central_view_replaces_aggregate_but_keeps_uncovered_turn(db):
    turn=str(uuid.uuid4()); receipt=str(uuid.uuid4())
    session=str(uuid.uuid4())
    db.execute("INSERT INTO rvbbit.calliope_sessions(id,owner_email,hermes_session_id,title) VALUES (%s,'test@example.com','test','test')",(session,))
    db.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,user_message,author_email,hermes_run_id) VALUES (%s,%s,1,'test','test@example.com','run-test')",(turn,session))
    db.execute("INSERT INTO rvbbit.receipts(receipt_id,operator,model,inputs_hash,inputs) VALUES (%s,'hermes.calliope_turn','unknown',decode('aa','hex'),'{}')",(receipt,))
    db.execute("INSERT INTO rvbbit.cost_events(receipt_id,source,status,cost_source,model,raw) VALUES (%s,'hermes','uncosted','unknown_model_rate','unknown',%s::jsonb)",(receipt,json.dumps({'calliope_turn_id':turn})))
    assert db.execute('SELECT count(*) AS n FROM rvbbit.cost_activity').fetchone()['n']==1
    costs.insert(db,costs.prepare(event(),RATES))
    rows=db.execute('SELECT * FROM rvbbit.cost_activity').fetchall()
    assert len(rows)==1 and rows[0]['model']=='openai/test'
    db.execute("INSERT INTO rvbbit.cost_events(source,status,cost_source,model) VALUES ('hermes','uncosted','unknown_model_rate','unknown')")
    assert db.execute('SELECT count(*) AS n FROM rvbbit.cost_activity').fetchone()['n']==2

def test_meaning_reservation_and_charge_are_one_activity(db):
    call=str(uuid.uuid4())
    db.execute("INSERT INTO rvbbit.meaning_model_calls(call_id,event,kind,work_key,reason,model,cost_usd) VALUES (%s,'reserved','recognition','source','test','openai/test',.25)",(call,))
    row=db.execute("SELECT * FROM rvbbit.cost_activity WHERE category='Meaning'").fetchone()
    assert row['cost_kind']=='reserved' and row['status']=='pending'
    db.execute("INSERT INTO rvbbit.meaning_model_calls(call_id,event,kind,work_key,reason,model,cost_usd) VALUES (%s,'charged','recognition','source','test','openai/test',.01)",(call,))
    costs.insert(db,costs.prepare(event(call_id='meaning:'+call,source='direct',category='Meaning'),RATES))
    rows=db.execute("SELECT * FROM rvbbit.cost_activity WHERE category='Meaning'").fetchall()
    assert len(rows)==1 and rows[0]['cost_kind']=='included'

def test_auxiliary_task_visible_without_counting_main_session_twice(db):
    for task in ('','compression'):
        db.execute("INSERT INTO rvbbit.hermes_model_usage(session_id,model,task,api_call_count,estimated_cost_usd) VALUES ('s','openai/test',%s,2,.01)",(task,))
    rows=db.execute('SELECT * FROM rvbbit.cost_activity').fetchall()
    assert len(rows)==1 and rows[0]['operation']=='compression' and rows[0]['calls']==2

@pytest.mark.parametrize('charge',[0,0.000027384])
def test_batched_decision_receipt_recovers_tokens_and_cost_once(db,charge):
    receipt=str(uuid.uuid4())
    output={'model':'typesafe/jev-1.13-20260917','answers':{'private':'not copied'},
            'usage':{'cost':charge,'input_tokens':652,'output_tokens':57}}
    db.execute("""INSERT INTO rvbbit.receipts(receipt_id,operator,model,inputs_hash,inputs,output)
        VALUES (%s,'clover_decide','typesafe/jev-1.13',decode('ab','hex'),'{}',%s)""",(receipt,json.dumps(output)))
    db.execute("""INSERT INTO rvbbit.cost_events(receipt_id,source,backend,status,cost_source,model,tokens_in,tokens_out)
        VALUES (%s,'specialist','decisions','uncosted','none','decisions',0,0)""",(receipt,))
    original=db.execute('SELECT * FROM rvbbit.cost_latest WHERE receipt_id=%s',(receipt,)).fetchone()
    costs.settle_decisions(db)
    costs.settle_decisions(db)
    latest=db.execute('SELECT * FROM rvbbit.cost_latest WHERE receipt_id=%s',(receipt,)).fetchone()
    assert latest['cost_request_id']==original['cost_request_id']
    assert latest['status']=='settled' and float(latest['cost_usd'])==pytest.approx(charge)
    assert (latest['tokens_in'],latest['tokens_out'])==(652,57)
    assert 'private' not in json.dumps(latest['raw'])
    assert db.execute('SELECT count(*) AS n FROM rvbbit.cost_events WHERE receipt_id=%s',(receipt,)).fetchone()['n']==2
    rows=db.execute('SELECT * FROM rvbbit.cost_activity WHERE receipt_id=%s',(receipt,)).fetchall()
    assert len(rows)==1 and rows[0]['model']==output['model'] and rows[0]['cost_kind']=='reported'

def test_memory_trace_is_metadata_only_and_import_is_idempotent(db,monkeypatch):
    monkeypatch.setenv('CALLIOPE_HINDSIGHT_METADATA_ONLY','true')
    db.execute('CREATE SCHEMA IF NOT EXISTS hindsight')
    db.execute('''CREATE TABLE hindsight.llm_requests(id uuid PRIMARY KEY,operation text,provider text,model text,status text,
      started_at timestamptz,ended_at timestamptz,duration_ms int,input_tokens int,output_tokens int,cached_tokens int,
      input jsonb,output jsonb,error text,llm_info jsonb,metadata jsonb)''')
    costs.ensure_memory_metadata(db)
    rid=str(uuid.uuid4())
    db.execute('''INSERT INTO hindsight.llm_requests VALUES (%s,'retain','openai','openai/test','success',now(),now(),10,100,20,50,
      '{"private":"prompt"}','{"private":"answer"}','private error','{"secret":"key"}','{"private":"metadata"}')''',(rid,))
    row=db.execute('SELECT input,output,error,llm_info,metadata FROM hindsight.llm_requests').fetchone()
    assert row=={'input':None,'output':None,'error':'provider_error','llm_info':{},'metadata':{}}
    monkeypatch.setattr(costs,'prices',lambda _: RATES)
    assert costs.sync_memory(db)==1 and costs.sync_memory(db)==0
    row=db.execute("SELECT * FROM rvbbit.cost_activity WHERE category='Memory'").fetchone()
    assert row['tokens_in']==100 and row['cost_kind']=='estimated'
    assert 'private' not in json.dumps(row['evidence'])

def test_legacy_topology_cached_receipts_count_once(db,tmp_path,monkeypatch):
    receipt={'schema_version':'rvbbit.business-topology.hutch-receipt.v1','provider_request_id':'id-1',
      'endpoint_host':'clover.example.com','backend':'clover_llm','service_model':'calliope','model_version':'openrouter/openai/test',
      'usage':{'prompt_tokens':10,'completion_tokens':5,'cost':.003}}
    for name in ('original','cache-copy'):
        (tmp_path/f'{name}.json').write_text(json.dumps({'private':'not imported','receipt':receipt}))
    monkeypatch.setattr(costs,'prices',lambda _: RATES)
    costs.import_topology_receipts(db,tmp_path)
    rows=db.execute("SELECT * FROM rvbbit.cost_activity WHERE category='Business topology'").fetchall()
    assert len(rows)==1 and rows[0]['cost_kind']=='included'
    assert 'private' not in json.dumps(rows[0]['evidence'])

def test_later_price_enrichment_retains_original_usage(db,monkeypatch):
    row=costs.prepare(event(),{})
    costs.insert(db,row)
    monkeypatch.setattr(costs,'prices',lambda _:RATES)
    costs.enrich_unpriced(db)
    saved=db.execute('SELECT * FROM rvbbit.model_call_usage').fetchone()
    assert saved['cost_kind']=='included' and saved['cost_usd']>0
    assert saved['evidence']==row['evidence']


def route(db, alias='calliope',host='clover.example.com',model='openai/test',at='2026-09-26T01:00:00Z'):
    db.execute("INSERT INTO rvbbit.model_alias_snapshots(alias,endpoint_host,model,observed_at,evidence) VALUES(%s,%s,%s,%s,'{\"source\":\"test_probe\"}')",(alias,host,model,at))


def test_route_estimate_is_host_and_time_scoped(db):
    route(db)
    r=event(source='hindsight',response_model=None)
    costs.resolve_alias(db,r)
    row=costs.prepare(r,RATES)
    assert row['requested_model']=='calliope' and row['model']=='openai/test'
    assert row['pricing']['alias_resolution']['basis']=='last_observed_route_estimate'
    for extra in ({'endpoint_host':'other.host'},{'ts':'2026-09-25T00:00:00Z'}):
        r=event(source='hindsight',response_model=None,**extra);costs.resolve_alias(db,r)
        assert r['response_model'] is None
    r=event(response_model='openai/actual');costs.resolve_alias(db,r)
    assert r['response_model']=='openai/actual'


def test_aux_valuation_counts_cache_once_and_freezes_route_rate(db,monkeypatch):
    route(db);monkeypatch.setattr(costs,'prices',lambda _:RATES)
    db.execute("""INSERT INTO rvbbit.hermes_model_usage(session_id,model,billing_provider,billing_base_url,task,
      api_call_count,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,first_seen,last_seen)
      VALUES('aux','calliope','custom','https://clover.example.com/v1','vision',2,10,20,50,40,
      '2026-09-26T02:00:00Z','2026-09-26T03:00:00Z')""")
    assert costs.sync_auxiliary(db)==1 and costs.sync_auxiliary(db)==0
    row=db.execute("SELECT * FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchone()
    assert row['calls']==2 and row['tokens_in']==100 and row['cost_kind']=='included'
    expected=10*2e-6+20*8e-6+50*.2e-6+40*2.5e-6
    assert float(row['cost_usd'])==pytest.approx(expected)
    route(db,model='other/model',at='2026-09-26T04:00:00Z')
    monkeypatch.setattr(costs,'prices',lambda _: {'openai/test':{'prompt':1,'completion':1}})
    db.execute("UPDATE rvbbit.hermes_model_usage SET output_tokens=40,api_call_count=3,last_seen='2026-09-26T05:00:00Z' WHERE session_id='aux'")
    costs.sync_auxiliary(db)
    rows=db.execute("SELECT * FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchall()
    assert len(rows)==1 and rows[0]['calls']==3 and rows[0]['model']=='openai/test'
    assert float(rows[0]['cost_usd'])==pytest.approx(expected+20*8e-6)


def test_memory_alias_estimate_retains_usage_and_included_value(db,monkeypatch):
    monkeypatch.setenv('CALLIOPE_MEMORY_INFERENCE_BASE_URL','https://clover.example.com/v1')
    monkeypatch.setattr(costs,'prices',lambda _:RATES);route(db)
    db.execute('CREATE SCHEMA IF NOT EXISTS hindsight')
    db.execute('''CREATE TABLE hindsight.llm_requests(id uuid PRIMARY KEY,operation text,provider text,model text,status text,
      started_at timestamptz,ended_at timestamptz,duration_ms int,input_tokens int,output_tokens int,cached_tokens int)''')
    db.execute("""INSERT INTO hindsight.llm_requests VALUES(%s,'retain','openai','calliope','success',
       '2026-09-26T02:00:00Z','2026-09-26T02:00:01Z',1000,100,20,50)""",(str(uuid.uuid4()),))
    assert costs.sync_memory(db)==1 and costs.sync_memory(db)==0
    row=db.execute("SELECT * FROM rvbbit.cost_activity WHERE category='Memory'").fetchone()
    assert row['requested_model']=='calliope' and row['model']=='openai/test' and row['cost_kind']=='included'
    assert float(row['cost_usd'])==pytest.approx(50*2e-6+50*.2e-6+20*8e-6)
    assert row['pricing']['alias_resolution']['endpoint_host']=='clover.example.com'


def test_alias_probe_uses_hutch_resolved_model_header(db,monkeypatch):
    import httpx
    monkeypatch.setenv('RVBBIT_CLOVER_KEY','test-only')
    monkeypatch.setenv('RVBBIT_CLOVER_OPENAI_BASE_URL','https://clover.example.com/v1')
    def handle(request):
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'calliope'},{'id':'calliope-unavailable'}]})
        if json.loads(request.content)['model']=='calliope-unavailable': return httpx.Response(502,json={'error':'unavailable'})
        return httpx.Response(200,headers={'x-hutch-model-version':'openrouter/openai/test'},
            json={'model':'calliope','id':'provider-request','usage':{'cost':0.00001},'choices':[{'private':'not stored'}]})
    client=httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx,'Client',lambda **kw:client)
    assert costs.probe_aliases(db)==['calliope-unavailable']
    row=db.execute('SELECT * FROM rvbbit.model_alias_snapshots').fetchone()
    assert row['model']=='openai/test' and row['evidence']['model_basis']=='x-hutch-model-version'
    assert 'private' not in json.dumps(row['evidence']) and 'test-only' not in str(row)


def test_aux_receipts_reconcile_late_and_zero_charges_without_double_counting(db,tmp_path,monkeypatch):
    monkeypatch.setattr(costs,'prices',lambda _: {})
    db.execute("""INSERT INTO rvbbit.hermes_model_usage(session_id,model,billing_provider,billing_base_url,task,
      api_call_count,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,first_seen,last_seen)
      VALUES('routed','typesafe/jev-router','custom','https://clover.example.com/v1','vision',2,20,4,0,0,now(),now())""")
    def aux(id,cost):
        return event(event='aux.completed',event_id=id,session_id='routed',task_id='vision',
          model='calliope-jrouter',response_model='typesafe/jev-router',reported_cost_usd=cost,
          usage={'input_tokens':10,'output_tokens':2,'cache_read_tokens':0,'cache_write_tokens':0},
          prompt='never store',response={'secret':'never store'})
    p=tmp_path/'events.jsonl';p.write_text(json.dumps(aux('first',.125))+'\n')
    # This old importer consumed all bytes while ignoring auxiliary events.
    st=p.stat()
    db.execute('INSERT INTO rvbbit.model_usage_import_state(path,identity,byte_offset) VALUES(%s,%s,%s)',
               (str(p),f'{st.st_dev}:{st.st_ino}',st.st_size))
    assert costs.sync_ledger(db,tmp_path,{})==1
    costs.sync_auxiliary(db)
    row=db.execute("SELECT * FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchone()
    assert row['cost_usd'] is None and row['evidence']['receipt_coverage']['status']=='partial'
    with p.open('a') as f:f.write(json.dumps(aux('second',0))+'\n')
    assert costs.sync_ledger(db,tmp_path,{})==1
    assert costs.sync_auxiliary(db)==1 and costs.sync_auxiliary(db)==0
    rows=db.execute("SELECT * FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchall()
    assert len(rows)==1 and rows[0]['calls']==2 and float(rows[0]['cost_usd'])==.125
    assert rows[0]['pricing']['source']=='upstream_usage'
    assert rows[0]['evidence']['receipt_coverage']['status']=='complete'
    assert len(db.execute('SELECT * FROM rvbbit.model_aux_receipts').fetchall())==2
    assert 'never store' not in str(db.execute('SELECT * FROM rvbbit.model_aux_receipts').fetchall())
    db.execute('DELETE FROM rvbbit.model_usage_import_state')
    costs.sync_ledger(db,tmp_path,{});costs.sync_auxiliary(db)
    assert db.execute("SELECT count(*) AS n FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchone()['n']==1
    # A distinct billing path on the same host cannot reuse these charges.
    db.execute("""INSERT INTO rvbbit.hermes_model_usage(session_id,model,billing_provider,billing_base_url,task,
      api_call_count,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens)
      VALUES('routed','typesafe/jev-router','custom','https://clover.example.com/other','vision',2,20,4,0,0)""")
    costs.sync_auxiliary(db)
    rows=db.execute("SELECT * FROM rvbbit.cost_activity WHERE source='hermes_aux'").fetchall()
    assert len(rows)==2 and all(r['cost_usd'] is None for r in rows)
    assert all(r['evidence']['receipt_coverage']['status']=='ambiguous_billing_buckets' for r in rows)


def test_aux_coverage_rejects_partial_tokens_and_missing_prices():
    native={'api_call_count':1,'input_tokens':10,'output_tokens':2,'cache_read_tokens':3,'cache_write_tokens':0}
    receipt={'usage':{k:v for k,v in native.items() if k!='api_call_count'},'reported_cost_usd':.1}
    assert costs.auxiliary_receipt_coverage(native,[receipt])['reported_cost_usd']==.1
    assert costs.auxiliary_receipt_coverage(native,[{**receipt,'usage':{**receipt['usage'],'cache_read_tokens':0}}])['reported_cost_usd'] is None
    assert costs.auxiliary_receipt_coverage(native,[{**receipt,'reported_cost_usd':None}])['reported_cost_usd'] is None


def test_native_codex_name_gets_cached_catalog_equivalent_without_changing_billing():
    rates=costs.catalog_rates({'prompt':'0.000002','completion':'0.00001','input_cache_read':'0.0000002'})
    catalog={'openai/gpt-6-sol':{**rates,'source':'openrouter_catalog','fetched_at':'2026-09-29T00:00:00Z'}}
    row=costs.prepare(event(model='gpt-6-sol',response_model='gpt-6-sol',provider='openai-codex',endpoint_host='chatgpt.com',
        usage={'input_tokens':100,'cache_read_tokens':900,'cache_write_tokens':0,'output_tokens':50}),catalog)
    assert row['cost_usd']==pytest.approx(.00088)
    assert row['cost_kind']=='included'
    assert row['pricing']['model']=='openai/gpt-6-sol'
    assert row['pricing']['relative_cost_usd']==row['cost_usd']
    assert row['pricing']['billing_mode']=='subscription_included'
    assert not row['pricing']['cache_rates_missing']
    assert row['pricing']['rates']['fetched_at']=='2026-09-29T00:00:00Z'
    # A reported charge remains authoritative and has a separate comparison value.
    charged=costs.prepare(event(provider='openai',endpoint_host='api.openai.com',reported_cost_usd=0),RATES)
    assert charged['cost_usd']==0 and charged['pricing']['relative_cost_usd']>0


def test_model_identity_is_exact_and_ambiguous_names_remain_unknown():
    catalog={'openai/example':{},'other/example':{},'openai/gpt-6-sol':{}}
    assert costs.resolve_price_model('example','openai-codex',catalog)=='openai/example'
    assert costs.resolve_price_model('example','custom',catalog) is None
    assert costs.resolve_price_model('gpt-6.1-sol','openai-codex',catalog) is None
    assert costs.resolve_price_model('other/gpt-6-sol','custom',catalog) is None


def test_context_tiers_use_total_request_including_cache_and_strict_boundary():
    rate=costs.catalog_rates({'prompt':'0.000002','completion':'0.00001','input_cache_read':'0.0000002',
        'overrides':[{'min_prompt_tokens':272000,'prompt':'0.000004','completion':'0.000015','input_cache_read':'0.0000004'}]})
    usage={'input_tokens':1000,'cache_read_tokens':271000,'output_tokens':1}
    assert costs.request_rates(usage,rate,canonical=True)['prompt']==2e-6
    usage['cache_read_tokens']+=1
    chosen=costs.request_rates(usage,rate,canonical=True)
    assert chosen['prompt']==4e-6 and chosen['input_cache_read']==.4e-6
    assert costs.request_rates(usage,rate,canonical=True,aggregate=True)['prompt']==2e-6
    assert costs.price_usage(usage,chosen,canonical=True)==pytest.approx(1000*4e-6+271001*.4e-6+15e-6)


def test_time_condition_is_not_silently_applied_all_day_and_later_tier_keys_win():
    rates=costs.catalog_rates({'prompt':'1','completion':'1','overrides':[
        {'min_prompt_tokens':100,'prompt':'3'}, {'min_prompt_tokens':50,'completion':'2'},
        {'min_prompt_tokens':10,'utc_start':100,'utc_end':400,'prompt':'99'}]})
    effective=costs.request_rates({'prompt_tokens':101},rates)
    assert effective['prompt']==3 and effective['completion']==2
    assert rates['unsupported_conditions']==['utc_start','utc_end']


def test_saved_catalog_basis_does_not_turn_request_tier_into_aggregate_base_rate():
    rate=costs.catalog_rates({'prompt':'0.000002','completion':'0.00001',
        'overrides':[{'min_prompt_tokens':100,'prompt':'0.000004'}]})
    row=event(source='hermes_aux',usage={'input_tokens':101,'output_tokens':1})
    first=costs.prepare(row,{'openai/test':rate})
    assert first['pricing']['rates']['prompt']==4e-6
    basis={first['pricing']['model']:first['pricing']['catalog_rates']}
    later=costs.prepare({**row,'calls':2,'usage':{'input_tokens':151,'output_tokens':2}},basis)
    assert later['pricing']['rates']['prompt']==2e-6
    assert later['pricing']['tier_basis']=='aggregate_base_rate_only'


def test_missing_cache_rate_is_explicit_even_for_openai_usage():
    row=costs.prepare(event(source='direct',usage={'prompt_tokens':100,'completion_tokens':1,
        'prompt_tokens_details':{'cached_tokens':80}}),{'openai/test':{'prompt':2e-6,'completion':10e-6}})
    assert row['pricing']['cache_rates_missing']==['input_cache_read']
    assert row['cost_usd']==pytest.approx(.00021)
