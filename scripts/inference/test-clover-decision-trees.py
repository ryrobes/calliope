#!/usr/bin/env python3
"""Disposable PG integration tests. Apply migrations 0432 and 0433 first.
Uses standard PG* connection env, no real inference or user data.
"""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get('CLOVER_DECISIONS_DISPOSABLE_TEST') != '1':
    raise SystemExit('Requires CLOVER_DECISIONS_DISPOSABLE_TEST=1 and a disposable database')
calls=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        inputs=json.loads(self.rfile.read(int(self.headers['Content-Length'])))['inputs']
        calls.append(inputs)
        if any(i['state']['evidence'].get('fail') for i in inputs if isinstance(i['state']['evidence'],dict)):
            self.send_response(502);self.end_headers();return
        outputs=[]
        for i in inputs:
            state=i['state']; e=state['evidence']; question=i['questions']['result']
            q=question['instructions'].split('Question: ',1)[1]
            if question['type']=='noul':
                value=e.get(q.rstrip('?'),False) if isinstance(e,dict) else e=='risky'
                answer={'type':'noul','noul':.8 if value else .2}
            else:
                choice=e['topic'];answer={'type':'choice','choice':choice,
                    'probabilities':{k:1 if k==choice else 0 for k in question['criteria']}}
            outputs.append({'model':'typesafe/jev-1.13-test','contract':i['contract'],
                'answers':{'result':answer},'usage':{}})
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'outputs':outputs}).encode())

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
def sql(query,fail=False):
    p=subprocess.run(['psql','-XAt','-v','ON_ERROR_STOP=1'],input=query,text=True,capture_output=True)
    assert bool(p.returncode)==fail, p.stderr or p.stdout
    return p.stdout.strip()
def lit(v):return "'"+json.dumps(v).replace("'","''")+"'::jsonb"
def evaluate(state,tree,limit=16):return json.loads(sql(f'SELECT rvbbit.decision_tree({lit(state)},{tree},{limit});'))
def count():return sum(map(len,calls))
TREE="rvbbit.branch('risk?',yes=>rvbbit.branch('service?',rvbbit.leaf('service intervention'),rvbbit.leaf('relationship review')),no=>rvbbit.leaf('routine follow-up'))"
try:
    sql(f"SELECT rvbbit.register_backend('decisions','http://127.0.0.1:{server.server_port}/predict','rvbbit',8,1,10000); SELECT rvbbit.judgment_purge('clover_decide');")
    # Construction and a terminal tree never invoke a model.
    tree=json.loads(sql(f'SELECT {TREE}'))
    assert count()==0
    leaf=evaluate({'risk':True},"rvbbit.leaf('always')")
    assert leaf['status']=='complete' and leaf['steps']==0 and leaf['label']=='always'
    assert count()==0
    state={'risk':True,'service':True,'account':'A'}
    result=evaluate(state,TREE)
    assert result['label']=='service intervention' and result['steps']==2,result
    assert count()==2
    request=calls[-1][0]['state']
    assert request['evidence']==state
    assert request['prior_judgments']==[{'question':'risk?','answer':True,'score':.8,'threshold':.5}],request
    assert evaluate(state,TREE)==result and count()==2 # fresh connection L2
    assert evaluate({'risk':False,'account':'B'},TREE)['steps']==1
    assert count()==3 # did not ask service?
    assert evaluate('risky',TREE)['steps']==2 # text-like JSON context
    assert sql(f"SELECT rvbbit.decision_tree('risky'::text,{TREE}) = rvbbit.decision_tree('\"risky\"'::jsonb,{TREE});")=='t'
    before=count()
    changed=TREE.replace('routine follow-up','unused changed')
    changed_result=evaluate(state,changed)
    assert changed_result['label']==result['label'] and changed_result['tree_id']!=result['tree_id']
    assert count()==before
    high_threshold=TREE[:-1]+',threshold=>0.9)'
    assert evaluate(state,high_threshold)['label']=='routine follow-up'
    assert count()==before # threshold is outside inference
    lower_threshold=TREE[:-1]+',threshold=>0.4)'
    assert evaluate(state,lower_threshold)['label']=='service intervention'
    assert count()==before+1 # next node sees changed prior judgment threshold
    before=count()
    budget=evaluate(state,TREE,1)
    assert budget['status']=='budget_exhausted' and budget['steps']==1 and budget['label'] is None
    assert count()==before
    assert sql(f"SELECT rvbbit.decision_tree(NULL::text,{TREE}) IS NULL;")=='t'
    assert sql(f"SELECT rvbbit.decision_tree('null'::jsonb,{TREE}) IS NULL;")=='t'
    assert evaluate('x'*33000,TREE)['status']=='context_limit' and count()==before
    # A malformed off-path node is rejected before any paid work.
    invalid=json.loads(json.dumps(tree));invalid['no']['surprise']='ignored?'
    for expression in [f"rvbbit.decision_tree('{json.dumps(state)}'::jsonb,{lit(invalid)})",
                       "rvbbit.branch('q',NULL,rvbbit.leaf('n'))", "rvbbit.leaf(NULL)",
                       "rvbbit.branch('q',rvbbit.leaf('y'),rvbbit.leaf('n'),'NaN'::float8)",
                       f"rvbbit.decision_tree('{{}}'::jsonb,{TREE},0)"]:
        sql('SELECT '+expression,fail=True)
    assert count()==before
    deep={'kind':'leaf','label':'end'}
    for _ in range(17):deep={'kind':'branch','question':'risk?','threshold':.5,'yes':deep,'no':{'kind':'leaf','label':'n'}}
    sql(f"SELECT rvbbit.decision_tree('{{}}'::jsonb,{lit(deep)})",fail=True)
    # Multiway decisions carry definitions as well as the selected answer.
    multi="rvbbit.choice_branch('topic?', '{\"billing\":\"Invoices and charges\",\"service\":\"Service issues\"}'::jsonb,jsonb_build_object('billing',rvbbit.leaf('finance'),'service',rvbbit.branch('risk?',rvbbit.leaf('urgent'),rvbbit.leaf('normal'))))"
    assert evaluate({'topic':'billing'},multi)['label']=='finance'
    assert evaluate({'topic':'service','risk':True},multi)['label']=='urgent'
    assert calls[-1][0]['state']['prior_judgments'][0]['choices']['service']=='Service issues'
    sql("SELECT rvbbit.choice_branch('q','{\"a\":\"A\",\"b\":\"B\"}',jsonb_build_object('a',rvbbit.leaf('A')))",fail=True)
    # Wavefront batching, stable ordering, NULLs, duplicate-input deduplication.
    states=[{'risk':i%2==0,'service':i%3==0,'account':f'batch-{i}'} for i in range(17)]
    states += [states[0],None]
    calls.clear()
    results=json.loads(sql(f'SELECT rvbbit.decision_tree_batch({lit(states)},{TREE});'))
    assert len(results)==19 and results[-1] is None
    assert all(r['status']=='complete' for r in results[:-1])
    assert [r['label'] for r in results[:-1]]==[
        ('service intervention' if s['service'] else 'relationship review') if s['risk'] else 'routine follow-up' for s in states[:-1]]
    batch_sizes=list(map(len,calls));assert max(batch_sizes)==8,batch_sizes
    assert count()==26,count() # 17 unique roots + 9 selected service branches
    before=count()
    for state,result in zip(states[:-1],results):assert evaluate(state,TREE)==result
    assert count()==before # same node keys for bulk and scalar
    assert json.loads(sql(f"SELECT rvbbit.decision_tree_batch('[]'::jsonb,{TREE});"))==[]
    sql(f"SET rvbbit.cache_bypass=on; SELECT rvbbit.decision_tree_batch('[{{}}]'::jsonb,{TREE});",fail=True)
    # Failure never follows no, and failed batch prewarm is not silently retried.
    calls.clear()
    fail=evaluate({'fail':True},TREE)
    assert fail['status']=='unavailable' and fail['label'] is None and fail['steps']==0,fail
    assert count()==1
    calls.clear()
    failed=json.loads(sql(f"SELECT rvbbit.decision_tree_batch('[{{\"fail\":true}},{{\"risk\":true,\"account\":\"not-retried\"}}]'::jsonb,{TREE});"))
    assert all(r['status']=='unavailable' and r['error']=='batch_round_failed' for r in failed),failed
    assert count()==2,count()
    print('PASS: lazy branches, full validation, history, shared cache, threshold/unused-leaf reuse, multiway routes, bounds, failures, and batch/scalar parity')
    print('Batch sizes:',batch_sizes,'(26 unique decisions for 18 non-null rows)')
finally:
    server.shutdown()
