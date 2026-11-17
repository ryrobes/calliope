#!/usr/bin/env python3
"""Integration test against a DISPOSABLE pg_rvbbit database (standard PG* env).

Run after applying 0432_clover_decisions.sql. Replaces its decisions backend,
clears clover_decide receipts and mutates its contract for invalidation tests.
No external inference, credentials, or user data. HTTP fixture binds loopback.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get('CLOVER_DECISIONS_DISPOSABLE_TEST') != '1':
    raise SystemExit('Set CLOVER_DECISIONS_DISPOSABLE_TEST=1 only for a disposable database')

calls = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        calls.append(body['inputs'])
        if any(item['state'] == 'provider failure' for item in body['inputs']):
            self.send_response(502); self.end_headers(); self.wfile.write(b'provider unavailable'); return
        outputs=[]
        for item in body['inputs']:
            assert isinstance(item['state'], (str, dict, list)), item
            assert isinstance(item['questions'], dict), item
            answers={}
            for key, q in item['questions'].items():
                if q['type']=='noul': answers[key]={'type':'noul','noul':.8}
                else:
                    labels=sorted(q['criteria']); answers[key]={'type':'choice','choice':labels[0],
                        'probabilities':{v: 1 if v==labels[0] else 0 for v in labels}}
            outputs.append({'model':'typesafe/jev-1.13-20260917','contract':item['contract'],
                            'answers':answers,'usage':{'input_tokens':10,'output_tokens':4,'cost':.00001}})
        data=json.dumps({'outputs':outputs}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()

def sql(query, fail=False):
    result=subprocess.run(['psql','-XAt','-v','ON_ERROR_STOP=1'],input=query,text=True,capture_output=True)
    if fail:
        assert result.returncode, result.stdout
    else:
        assert result.returncode==0, result.stderr
    return result.stdout.strip()

try:
    sql(f"SELECT rvbbit.register_backend('decisions','http://127.0.0.1:{server.server_port}/predict','rvbbit',8,2,10000); SELECT rvbbit.judgment_purge('clover_decide');")
    # Same connection: scores and alternate thresholds reuse the one result.
    assert sql("SELECT rvbbit.truth('cancel please','Customer asks to cancel'); SELECT rvbbit.truth_score('cancel please','Customer asks to cancel'); SELECT rvbbit.truth('cancel please','Customer asks to cancel',.9);")== 't\n0.8\nf'
    assert len(calls)==1, calls
    # Fresh connections must hit durable receipts too.
    assert sql("SELECT rvbbit.truth('cancel please','Customer asks to cancel')")=='t'
    assert len(calls)==1
    assert sql("SELECT rvbbit.clover_decide('cancel please',rvbbit.truth_question('Customer asks to cancel')) #>> '{answers,result,noul}'")=='0.8'
    assert len(calls)==1
    assert sql("SELECT rvbbit.choose('{\"body\":\"cancel please\"}'::jsonb,ARRAY['cancel','other']); SELECT rvbbit.choose('{\"body\":\"cancel please\"}'::jsonb,'{\"cancel\":\"cancel\",\"other\":\"other\"}'::jsonb);")=='cancel\ncancel'
    assert len(calls)==2
    # Definition, predicate and state changes all invalidate the cache.
    sql("SELECT rvbbit.choose('{\"body\":\"cancel please\"}'::jsonb,'{\"cancel\":\"Cancel appointment\",\"other\":\"Other request\"}'::jsonb); SELECT rvbbit.truth('cancel please','Customer requests billing help'); SELECT rvbbit.truth('different input','Customer asks to cancel');")
    assert len(calls)==5
    before=len(calls)
    assert sql("SELECT rvbbit.truth(NULL::text,'test') IS NULL, rvbbit.truth('test',NULL) IS NULL, rvbbit.choose(NULL::jsonb,ARRAY['a','b']) IS NULL, rvbbit.truth('test','test',NULL) IS NULL;")=='t|t|t|t'
    for query in ["SELECT rvbbit.truth('test','')", "SELECT rvbbit.truth('test','ok',-1)", "SELECT rvbbit.truth('test','ok','NaN'::float8)", "SELECT rvbbit.choose('test',ARRAY['a','a'])", "SELECT rvbbit.choose('test','{\"a\":1,\"b\":\"b\"}'::jsonb)"]:
        sql(query,fail=True)
    assert len(calls)==before
    # Provider errors remain SQL NULL, aren't memoized as false or success.
    assert sql("SELECT rvbbit.truth('provider failure','test') IS NULL")=='t'
    failed_calls=len(calls)
    assert sql("SELECT rvbbit.truth('provider failure','test') IS NULL")=='t'
    assert len(calls)>failed_calls
    # Explicit bulk path: full decisions warm the exact cache used by truth().
    before=len(calls)
    stats=sql("SELECT n_inputs,n_executed,n_errors FROM rvbbit.prewarm_operator('clover_decide',$q$ SELECT to_jsonb('batch '||i) AS state,rvbbit.truth_question('Batch predicate') AS questions FROM generate_series(1,17) i $q$);")
    assert stats=='17|17|0', stats
    batch_calls=calls[before:]
    assert any(len(batch)>1 for batch in batch_calls), [len(v) for v in batch_calls]
    assert sum(map(len,batch_calls))==17
    before=len(calls)
    assert sql("SELECT count(*) FROM generate_series(1,17) i WHERE rvbbit.truth('batch '||i,'Batch predicate');")=='17'
    assert len(calls)==before
    # The shared prewarmer must preserve native types, not just JSONB.
    sql("""SELECT rvbbit.create_operator('_decision_prewarm_types', ARRAY['t','n','b','a','j'], 'jsonb',
        op_arg_types:=ARRAY['text','integer','boolean','text[]','jsonb'], op_parser:='json',
        op_steps:='[{"name":"copy","kind":"code","fn":"json_get","inputs":{"value":{"t":"{{t}}","n":"{{n}}","b":"{{b}}","a":"{{a}}","j":"{{j}}"}}}]'::jsonb);""")
    typed_stats=sql("""SELECT n_inputs,n_executed,n_errors FROM rvbbit.prewarm_operator('_decision_prewarm_types',
        $q$ SELECT 'hello'::text,42,true,ARRAY['a','b'],'{"x":1}'::jsonb
        UNION ALL SELECT NULL,42,true,ARRAY['a','b'],'{"x":1}'::jsonb $q$);""")
    assert typed_stats=='1|1|0', typed_stats
    typed = json.loads(sql("SELECT rvbbit._decision_prewarm_types('hello',42,true,ARRAY['a','b'],'{\"x\":1}'::jsonb);"))
    assert typed=={'t':'hello','n':42,'b':True,'a':['a','b'],'j':{'x':1}},typed
    assert sql("SELECT count(DISTINCT inputs_hash) FROM rvbbit.receipts WHERE operator='_decision_prewarm_types'")=="1"
    # Unnamed input-query columns map positionally; malformed queries now error.
    sql("SELECT * FROM rvbbit.prewarm_operator('clover_decide','SELECT no_such_column');",fail=True)
    # Step/contract and operator model identity both move the cache key.
    sql("UPDATE rvbbit.operators SET steps=jsonb_set(steps,'{0,inputs,contract}','\"clover-decisions-v1-test\"') WHERE name='clover_decide'; SELECT rvbbit.flush_cache();")
    assert sql("SELECT rvbbit.truth('cancel please','Customer asks to cancel')")=='t'
    assert len(calls)==before+1
    sql("UPDATE rvbbit.operators SET model='cache-identity-test' WHERE name='clover_decide'; SELECT rvbbit.flush_cache();")
    assert sql("SELECT rvbbit.truth('cancel please','Customer asks to cancel')")=='t'
    assert len(calls)==before+2
    print('PASS: text/JSONB, full results, shared L1/L2, changed inputs/contracts/models, NULL/error handling, and 17-row batch prewarm')
    print('Prewarm HTTP batch sizes:', [len(v) for v in batch_calls])
finally:
    # Reapply definition so the disposable DB remains useful for follow-up tests.
    sql(Path('crates/pg_rvbbit/sql/migrations/0432_clover_decisions.sql').read_text())
    server.shutdown()
