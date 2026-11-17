"""A published page's queries: a few at a time, one more try after a timeout,
one timeout setting for the page, and a real answer when a query cannot finish."""
import json
import subprocess
import sys
from pathlib import Path

import psycopg

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


def run_node(script, payload):
    result = subprocess.run(['node', '-e', script], input=json.dumps(payload), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


HARNESS = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
let source='';process.stdin.on('data',d=>source+=d);process.stdin.on('end',async()=>{
  const {queue}=JSON.parse(source);
  const window={RVBBIT_DASHBOARD:{manifest:{}}};
  const calls=[];let active=0,peak=0;
  const outcomes={};   // sql -> list of behaviours, one per attempt
  window.rvbbitQuery=async(sql,opts)=>{
    calls.push({sql,opts});active++;peak=Math.max(peak,active);
    await new Promise(r=>setTimeout(r,15));
    active--;
    const next=(outcomes[sql]||[]).shift();
    if(next) throw Object.assign(new Error(next.message),next);
    return {rows:[{sql}]};
  };
  vm.runInNewContext(queue,{window,Promise,setTimeout,Number,Math,Object,String});
  const q=(sql,opts)=>window.rvbbitQuery(sql,opts);
  __BODY__
  console.log('ok');
});
'''


def test_a_burst_runs_a_few_at_a_time_and_every_query_still_answers():
    body = r'''
      const results=await Promise.all(Array.from({length:18},(_,i)=>q('q'+i)));
      assert.equal(results.length,18); assert.deepEqual(results[17].rows,[{sql:'q17'}]);
      assert.equal(peak,4,'four in flight at most by default');
      assert.deepEqual(calls.map(c=>c.sql),Array.from({length:18},(_,i)=>'q'+i),'in the order the page asked');
      assert.equal(window.RVBBIT_DASHBOARD.queryQueue.lanes,4); assert.equal(window.RVBBIT_DASHBOARD.queryQueue.waiting,0);
      // The page can widen or narrow it, within bounds.
      peak=0; window.RVBBIT_DASHBOARD.query={concurrency:2};
      await Promise.all(Array.from({length:6},(_,i)=>q('n'+i))); assert.equal(peak,2);
      peak=0; window.RVBBIT_DASHBOARD.query={concurrency:99};
      await Promise.all(Array.from({length:12},(_,i)=>q('w'+i))); assert.equal(peak,8,'never more than eight');
    '''
    assert run_node(HARNESS.replace('__BODY__', body), {"queue": server._DASH_SHIM_QUEUE}) == 'ok'


def test_a_timeout_is_tried_once_more_and_a_sql_error_is_not():
    body = r'''
      outcomes.slow=[{message:'This query was stopped after 30 s.',code:'QUERY_TIMEOUT',status:504}];
      outcomes.dead=[{message:'boom',status:500},{message:'boom again',status:500}];
      outcomes.wrong=[{message:'column nope does not exist',code:'SQL_ERROR',status:400}];
      outcomes.once=[{message:'This query was stopped after 30 s.',code:'QUERY_TIMEOUT',status:504}];
      const settled=await Promise.allSettled([q('slow'),q('fine'),q('dead'),q('wrong'),q('once',{retry:false})]);
      assert.equal(settled[0].status,'fulfilled','the retry answered');
      assert.equal(settled[1].status,'fulfilled');
      assert.equal(settled[2].reason.message,'boom again','a second failure is the answer');
      assert.equal(settled[3].reason.message,'column nope does not exist');
      assert.equal(settled[4].status,'rejected','a page can opt a query out of the retry');
      const count=sql=>calls.filter(c=>c.sql===sql).length;
      assert.deepEqual([count('slow'),count('fine'),count('dead'),count('wrong'),count('once')],[2,1,2,1,1]);
      assert.ok(calls.findIndex(c=>c.sql==='wrong')<calls.map(c=>c.sql).lastIndexOf('slow'),'the retry waits its turn behind the rest');
      assert.deepEqual({...window.RVBBIT_DASHBOARD.queryQueue.stats},{started:7,retried:2,failed:3});
    '''
    assert run_node(HARNESS.replace('__BODY__', body), {"queue": server._DASH_SHIM_QUEUE}) == 'ok'


def test_a_page_sets_one_timeout_for_all_of_its_queries():
    body = r'''
      await q('a'); assert.equal(calls.at(-1).opts.timeout_ms,undefined,'no setting: the server default');
      window.RVBBIT_DASHBOARD.manifest.query={timeout_ms:90000};
      await q('b'); assert.equal(calls.at(-1).opts.timeout_ms,90000,'from the manifest');
      window.RVBBIT_DASHBOARD.query={timeout_ms:120000};
      await q('c',{limit:5}); assert.deepEqual({...calls.at(-1).opts},{limit:5,timeout_ms:120000},'the page setting wins over the manifest');
      await q('d',{timeout_ms:5000}); assert.equal(calls.at(-1).opts.timeout_ms,5000,'a query keeps its own');
      window.RVBBIT_DASHBOARD.query={timeout_ms:999999};
      await q('e'); assert.equal(calls.at(-1).opts.timeout_ms,undefined,'an out-of-range setting is ignored, not sent');
    '''
    assert run_node(HARNESS.replace('__BODY__', body), {"queue": server._DASH_SHIM_QUEUE}) == 'ok'


def test_the_queue_wraps_the_transport_once_on_both_kinds_of_page():
    assert server._DASH_SHIM.index(server._DASH_SHIM_TRANSPORT) < server._DASH_SHIM.index(server._DASH_SHIM_QUEUE)
    assert server._DASH_SHIM_QUEUE in server._DASH_RUNTIME_SHIM, "the Stage embed queues too"
    assert server._DASH_SHIM.count("window.rvbbitQuery=queued") == 1
    # Loading the queue twice must not stack a second queue on the first.
    script = r'''
    const vm=require('node:vm'), assert=require('node:assert/strict');
    let source='';process.stdin.on('data',d=>source+=d);process.stdin.on('end',async()=>{
      const {queue,transport}=JSON.parse(source);
      const window={RVBBIT_DASHBOARD:{manifest:{},_recordQueryTrace(){}}};
      const fetch=async()=>({ok:false,status:504,json:async()=>({error:{code:'QUERY_TIMEOUT',message:'stopped after 30 s'}})});
      vm.runInNewContext(transport+queue+queue,{window,fetch,Promise,setTimeout,Number,Math,Object,String,JSON,Error,encodeURIComponent});
      let seen;
      try{await window.rvbbitQuery('select 1',{retry:false});}catch(error){seen=error;}
      assert.equal(seen.code,'QUERY_TIMEOUT'); assert.equal(seen.status,504); assert.equal(seen.message,'stopped after 30 s');
      assert.equal(window.RVBBIT_DASHBOARD.queryQueue.stats.started,1,'one queue, one start');
      console.log('ok');
    });
    '''
    transport = server._DASH_SHIM_TRANSPORT.replace("__SLUG__", '"demo"')
    assert run_node(script, {"queue": server._DASH_SHIM_QUEUE, "transport": transport}) == 'ok'


def test_a_query_that_cannot_finish_gets_an_answer_the_page_can_act_on():
    res, status = server._dashboard_query_failure(psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    assert status == 504 and res["error"]["code"] == "QUERY_TIMEOUT"
    assert "stopped after 30 s" in res["error"]["message"] and "RVBBIT_DASHBOARD.query" in res["error"]["message"]
    res, status = server._dashboard_query_failure(psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), 90000)
    assert "stopped after 90 s" in res["error"]["message"] and res["error"]["applied_timeout_ms"] == 90000
    res, status = server._dashboard_query_failure(psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"))
    assert status == 503 and res["error"]["code"] == "QUERY_UNAVAILABLE"
    res, status = server._dashboard_query_failure(RuntimeError('relation "secret_table" does not exist'))
    assert status == 500 and res["error"] == {"code": "QUERY_FAILED", "message": "This query failed on the server."}, "no statement text leaks"
    route = Path(server.__file__).read_text()
    assert "res, failure_status = _dashboard_query_failure(exc, timeout_ms)" in route
    assert "return _json(res, failure_status or (400 if res.get(\"error\") else 200))" in route, "and the failure is recorded like any other answer"


def test_the_template_and_the_stage_bridge_carry_the_timeout_through():
    template = (_HERE / "dashboard_template.html").read_text()
    assert "timeout_ms:parts[name].timeout_ms ?? opts.timeout_ms ?? null" in template, "composePayload used to drop it"
    page = (_HERE / "calliope" / "calliope.js").read_text()
    assert page.count("timeout_ms: data.opts?.timeout_ms ?? undefined") == 2, "single and batched bridge queries"
    bridge = (_HERE / "calliope.py").read_text()
    assert "Math.max(60000,asked+20000)" in bridge
