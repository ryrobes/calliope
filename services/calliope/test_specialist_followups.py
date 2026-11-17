"""Real PostgreSQL queue, FIFO, retry and cross-turn telemetry regression checks."""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
import calliope
import server
import specialist_followups as followups


@pytest.fixture
def db(monkeypatch, request):
    dsn = os.environ.get('SPECIALIST_FOLLOWUP_TEST_DSN')
    if not dsn:
        pytest.skip('requires an empty disposable PostgreSQL database')
    factory = lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    with factory() as c:
        assert c.execute("SELECT to_regnamespace('rvbbit') AS ns").fetchone()['ns'] is None
        c.execute('''CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,hermes_session_id text,archived bool DEFAULT false);
            CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text,ordinal int DEFAULT 1,
                user_message text DEFAULT '',assistant_message text DEFAULT '',turn_kind text DEFAULT 'chat',
                author_email text DEFAULT 'owner@example.com',authoring_mode text DEFAULT 'html',
                design_profile_version_id uuid,selected_surface_id uuid,object_refs jsonb,evidence_refs jsonb,attachments jsonb,
                created_at timestamptz DEFAULT now());''')
        def cleanup():
            with factory() as conn: conn.execute('DROP SCHEMA IF EXISTS rvbbit CASCADE')
        request.addfinalizer(cleanup)
        migrations = Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations'
        for name in ['0302_calliope_specialists.sql','0306_calliope_specialist_execution.sql','0307_calliope_specialist_threads.sql']:
            c.execute((migrations/name).read_text())
        c.execute("ALTER TABLE rvbbit.calliope_specialist_runs ADD COLUMN delivery text DEFAULT 'mailbox'")
        c.execute('''CREATE TABLE rvbbit.calliope_agent_messages(
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_email text,kind text,from_kind text,
            run_id uuid,thread_id uuid,to_session_id uuid,body text,payload jsonb DEFAULT '{}'::jsonb,
            status text DEFAULT 'queued',not_before timestamptz DEFAULT now(),created_at timestamptz DEFAULT now(),
            delivered_at timestamptz,delivered_turn_id uuid);''')
        sid,tid,sp,thread,rid = [str(uuid4()) for _ in range(5)]
        c.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'owner@example.com','calliope_parent',false)",(sid,))
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",(tid,sid))
        c.execute("INSERT INTO rvbbit.calliope_specialists(id,specialist_key,profile_name,display_name) VALUES(%s,'iris','iris','Iris')",(sp,))
        c.execute("INSERT INTO rvbbit.calliope_specialist_threads(id,specialist_id,owner_email,source_session_id,root_turn_id,hermes_session_id,objective,round_count,last_run_id) VALUES(%s,%s,'owner@example.com',%s,%s,'child_session','Original',1,NULL)",(thread,sp,sid,tid))
        c.execute("INSERT INTO rvbbit.calliope_specialist_runs(id,specialist_id,owner_email,source_session_id,source_turn_id,hermes_session_id,status,task,thread_id,round_no,hermes_run_id) VALUES(%s,%s,'owner@example.com',%s,%s,'child_session','running','Original',%s,1,'original-hermes')",(rid,sp,sid,tid,thread))
        c.execute('UPDATE rvbbit.calliope_specialist_threads SET last_run_id=%s WHERE id=%s',(rid,thread))
    monkeypatch.setattr(server,'_conn',factory)
    monkeypatch.setattr(server,'_application_authorization_context',lambda ctx: SimpleNamespace(subject='owner@example.com'))
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda ctx:{'platform':'api_server','session_id':'calliope_parent'})
    monkeypatch.setattr(calliope.CalliopeConfig,'from_env',lambda:SimpleNamespace(enabled=True,hermes_url='http://hermes'))
    obj=SimpleNamespace(factory=factory,sid=sid,tid=tid,sp=sp,thread=thread,rid=rid)
    yield obj


def send(db, message='Use the corrected measurement', context=None):
    return asyncio.run(server._run_calliope_specialist('',message,context,thread_id=db.thread,delivery='mailbox'))


def finish(db, rid=None):
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete' WHERE id=%s",(rid or db.rid,))


def claim(db, runner='worker'):
    with db.factory() as c:return followups.claim(c,runner)


def test_busy_correction_is_durable_deduplicated_and_claimed_after_parent_ends(db):
    with ThreadPoolExecutor(3) as pool:
        results=list(pool.map(lambda _:send(db),range(3)))
    assert len({r['followup_id'] for r in results})==1
    assert all(r['accepted'] and not r['delivered'] and r['status']=='queued' and r['run_id'] is None for r in results)
    assert claim(db)==[]
    with db.factory() as c:
        assert c.execute('SELECT task FROM rvbbit.calliope_specialist_runs WHERE id=%s',(db.rid,)).fetchone()['task']=='Original'
        c.execute("UPDATE rvbbit.calliope_turns SET status='complete' WHERE id=%s",(db.tid,))
    finish(db)
    with ThreadPoolExecutor(2) as pool: claims=list(pool.map(lambda n:claim(db,str(n)),range(2)))
    runs=[r for batch in claims for r in batch]
    assert len(runs)==1
    with db.factory() as c:
        row=c.execute('SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s',(runs[0]['id'],)).fetchone()
        assert row['round_no']==2 and row['task']=='Use the corrected measurement'
        assert str(row['source_turn_id'])==db.tid and row['hermes_session_id']=='child_session'
        assert row['context']['followup_id']==results[0]['followup_id']
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_messages WHERE run_id=%s',(runs[0]['id'],)).fetchone()['n']==1
        c.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s",(db.tid,))
    retry=send(db)
    assert retry['run_id']==runs[0]['id'] and not retry['delivered']
    assert claim(db)==[]


def test_distinct_corrections_are_one_ordered_continuation(db):
    a=send(db,'First correction',{'scale':1});b=send(db,'Second correction',{'scale':2})
    assert a['followup_id']!=b['followup_id']
    finish(db);first=claim(db)[0];assert claim(db)==[]
    with db.factory() as c:
        row=c.execute('SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s',(first['id'],)).fetchone()
        assert row['task'].index('First correction')<row['task'].index('Second correction')
        assert row['context']['followup_ids']==[a['followup_id'],b['followup_id']]
        assert [x['provided']['scale'] for x in row['context']['followup_contexts']]==[1,2]
    assert send(db,'Second correction',{'scale':2})['run_id']==first['id']
    finish(db,first['id']);assert claim(db)==[]


@pytest.mark.parametrize('change',["UPDATE rvbbit.calliope_specialists SET status='disabled'",
                                   "UPDATE rvbbit.calliope_turns SET status='cancelled'",
                                   "UPDATE rvbbit.calliope_sessions SET archived=true",
                                   "UPDATE rvbbit.calliope_specialist_threads SET status='closed'",
                                   "UPDATE rvbbit.calliope_sessions SET owner_email='other@example.com'"])
def test_paused_or_inaccessible_thread_does_not_execute_or_discard_queue(db,change):
    send(db);finish(db)
    with db.factory() as c:c.execute(change)
    assert claim(db)==[]
    with db.factory() as c:
        assert len(c.execute('SELECT context FROM rvbbit.calliope_specialist_threads').fetchone()['context']['pending_followups'])==1


def test_other_notebook_cannot_queue_a_correction(db,monkeypatch):
    other=str(uuid4())
    with db.factory() as c:
        c.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'owner@example.com','calliope_other',false)",(other,))
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",(str(uuid4()),other))
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda ctx:{'platform':'api_server','session_id':'calliope_other'})
    with pytest.raises(ValueError,match='another notebook'):send(db)
    assert claim(db)==[]


def test_activity_counts_completed_and_running_tools_without_transcript_bodies(db):
    with db.factory() as c:
        for i,kind in enumerate(['tool_started','tool_completed','tool_started','tool_failed','tool_started'],1):
            c.execute("INSERT INTO rvbbit.calliope_specialist_messages(run_id,ordinal,role,kind,body,payload) VALUES(%s,%s,'tool',%s,'private args',%s::jsonb)",(db.rid,i,kind,json.dumps({'tool_name':'run_sql'})))
        activity=calliope._session_specialist_activity(c,db.sid)
    assert activity[0]['tool_count']==3 and activity[0]['active_count']==1
    assert activity[0]['active_tools']==['run_sql'] and 'private args' not in str(activity)
    finish(db)
    with db.factory() as c: assert calliope._session_specialist_activity(c,db.sid)[0]['active_count']==0


@pytest.mark.parametrize('with_recall', [False, True])
def test_queued_round_executes_in_same_hermes_session_with_saved_context(db,monkeypatch,with_recall):
    import stage_context
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', 'select' if with_recall else 'off')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_OWNERS', '')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_SESSIONS', '')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake')
    send(db,context={'scale':'corrected','include_sketch':True});finish(db)
    with db.factory() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    run=claim(db)[0];seen={}
    if with_recall:
        pool = [{'surface_id':str(i),'kind':'query','title':f'Query {i}'} for i in range(12)]
        def load(factory, session_id, selected, **kwargs):
            assert session_id == db.sid and kwargs['candidate_limit'] == 120
            return pool, None
        async def decide(task, history, items, selected, key):
            seen['recall_task'] = task
            return [items[6]], {'usage': {'cost': .0001}}
        monkeypatch.setattr(calliope,'_compact_surface_context',load)
        monkeypatch.setattr(stage_context,'decide',decide)
    monkeypatch.setattr(server,'_specialist_turn_image_parts',lambda *a:[])
    monkeypatch.setattr(calliope,'_session_sketch_image_part',lambda *a,**kw:seen.update(sketch_age=kw['max_age_minutes']))
    monkeypatch.setattr(calliope,'project_messages',lambda *a:[])
    async def stream(client,config,conn,turn,session,**kw):
        seen.update(session=session,message=kw['message'])
        assert kw['execution_key']=='specialist:'+run['id']
        if with_recall:
            # The snapshot must be durable before Hermes gets a new message.
            with db.factory() as c:
                snapshot=c.execute("SELECT context->'_stage_recall' AS s FROM rvbbit.calliope_specialist_runs WHERE id=%s",(run['id'],)).fetchone()['s']
            assert snapshot['selected_ids'] == ['6']
        kw['bind_run']('correction-hermes',0)
        yield 'assistant.completed',{'content':'Correction applied'}
        yield 'run.completed',{'messages':[]}
    monkeypatch.setattr(calliope,'_iter_hermes_run',stream)
    monkeypatch.setattr(server,'_queue_specialist_round_outcome',lambda rid:seen.update(outcome=rid))
    monkeypatch.setattr(server,'_specialist_result_payload',lambda rid:{'status':'complete'})
    asyncio.run(server._execute_specialist_round(run['id'],run['runner_id']))
    assert seen['session']=='child_session' and '"scale": "corrected"' in seen['message']
    assert seen['sketch_age'] is None and seen['outcome']==run['id']
    if with_recall:
        assert seen['recall_task'] == 'Use the corrected measurement'
        assert '"stage_references"' in seen['message'] and '"id": "6"' in seen['message']
        assert '_stage_recall' not in seen['message'] and '"cost"' not in seen['message']
    with db.factory() as c:
        receipt=followups.receipt({'id':'request','thread_id':db.thread},run=c.execute('SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s',(run['id'],)).fetchone())
    assert receipt['delivered'] and receipt['status']=='complete'


def test_stage_recall_snapshot_is_write_once_and_rejects_stale_or_bound_workers(db):
    import specialist_stage_context as recall
    send(db);finish(db);run=claim(db)[0]
    first={'mode':'select','items':[{'id':'original'}]}
    assert recall._freeze(db.factory,run,first)==first
    assert recall._freeze(db.factory,run,{'items':[{'id':'changed'}]})==first
    assert recall._freeze(db.factory,{**run,'runner_id':'stale'},first) is None
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET hermes_run_id='bound' WHERE id=%s",(run['id'],))
    assert recall._freeze(db.factory,run,first) is None


def test_failed_claim_transaction_preserves_pending_request(db,monkeypatch):
    send(db);finish(db)
    with db.factory() as c:
        with pytest.raises(RuntimeError):
            with c.transaction():
                assert followups.claim(c,'interrupted')
                raise RuntimeError('crash before commit')
    assert len(claim(db,'replacement'))==1


def test_stale_launcher_does_not_execute_another_workers_round(db,monkeypatch):
    send(db);finish(db);run=claim(db,'owner-worker')[0]
    assert asyncio.run(server._execute_specialist_round(run['id'],'stale-worker')) is None
    with db.factory() as c:
        row=c.execute('SELECT status,hermes_run_id FROM rvbbit.calliope_specialist_runs WHERE id=%s',(run['id'],)).fetchone()
    assert row['status']=='running' and row['hermes_run_id'] is None


def test_failed_delivery_receipt_does_not_promise_another_round():
    item={'id':'followup','thread_id':'thread'}
    result=followups.receipt(item,run={'id':'run','status':'failed','error':'Unconfirmed delivery'})
    assert not result['delivered'] and result['error']=='Unconfirmed delivery'
    assert 'Inspect' in result['next'] and 'saved for the next round' not in result['next']


@pytest.mark.parametrize('queued', [False, True])
def test_preparation_failure_marks_run_failed_and_delivers_outcome(db, monkeypatch, queued):
    outcomes = []
    if queued:
        send(db); finish(db)
        run = claim(db)[0]
    else:
        run = {'id': db.rid, 'runner_id': 'first-round-worker'}
        with db.factory() as c:
            c.execute('UPDATE rvbbit.calliope_specialist_runs SET runner_id=%s,hermes_run_id=NULL WHERE id=%s',
                      (run['runner_id'], run['id']))
    def bad_images(*args):
        raise RuntimeError('Image preparation failed')
    monkeypatch.setattr(server, '_specialist_turn_image_parts', bad_images)
    monkeypatch.setattr(server, '_queue_specialist_round_outcome', outcomes.append)
    with pytest.raises(RuntimeError, match='Image preparation failed'):
        asyncio.run(server._launch_specialist_round(run['id'], run['runner_id']))
    with db.factory() as c:
        row = c.execute('SELECT status,error,runner_id FROM rvbbit.calliope_specialist_runs WHERE id=%s',
                        (run['id'],)).fetchone()
    assert row['status'] == 'failed' and row['error'] == 'Image preparation failed'
    assert row['runner_id'] is None and outcomes == [run['id']]


def test_agent_work_route_checks_visibility_before_reading_counters(monkeypatch,tmp_path):
    import sys
    routes={};reads=[]
    class MCP:
        def custom_route(self,path,methods):
            def save(fn):routes[(path,tuple(methods))]=fn;return fn
            return save
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,*a):return self
        def fetchall(self):return []
        def fetchone(self):return None
    monkeypatch.setitem(sys.modules,'auth',SimpleNamespace(
        read_session_full=lambda request:request.session,google_enabled=lambda:False,
        background_layer=lambda *a,**kw:''))
    monkeypatch.setattr(calliope,'ensure_tables',lambda f:None)
    monkeypatch.setattr(calliope.hermes_docs,'ensure',lambda f:None)
    monkeypatch.setattr(calliope.hermes_config,'ensure',lambda f:None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test-key')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    def view(conn,sid,owner):
        if sid!='allowed':raise calliope.calliope_access.CalliopeAccessError('FORBIDDEN','No access',403)
        return {'id':sid}
    monkeypatch.setattr(calliope.calliope_access,'require_view',view)
    monkeypatch.setattr(calliope,'_session_agent_work',lambda f,sid:reads.append(sid) or {'pending':[]})
    calliope.register_calliope_routes(MCP(),Conn,'',lambda doc:doc)
    handler=routes[('/api/calliope/sessions/{session_id}/agent-work',('GET',))]
    req=SimpleNamespace(session={},path_params={'session_id':'allowed'})
    assert asyncio.run(handler(req)).status_code==401 and not reads
    req.session={'identity':'viewer@example.com','mapped':True,'via':'google'}
    req.path_params['session_id']='denied'
    assert asyncio.run(handler(req)).status_code==403 and not reads
    req.path_params['session_id']='allowed'
    assert asyncio.run(handler(req)).status_code==200 and reads==['allowed']


class NativeSteering:
    def __init__(self):
        self.status='running';self.receipts={};self.posts=[];self.pending='';self.lose_reply=False
    async def request(self,method,path,body):
        assert path.startswith('/p/iris/v1/runs/original-hermes')
        if method=='GET':return 200,{'status':self.status,'steer_receipts':self.receipts,'pending_steer':self.pending}
        self.posts.append(body);self.receipts[body['request_id']]={'sha256':'test'}
        if self.lose_reply:raise TimeoutError('reply lost after acceptance')
        return 200,{'accepted':True}


def steer(db,native):return asyncio.run(followups.steer_pending(db.factory,native.request))

def test_active_steer_survives_lost_reply_without_repeating_or_starting_round(db):
    result=send(db);native=NativeSteering();native.lose_reply=True
    steer(db,native);steer(db,native)
    assert len(native.posts)==1 and result['followup_id'] in native.posts[0]['input']
    assert send(db)['status']=='steering'
    finish(db);assert claim(db)==[]  # Native delivery must be reconciled first.
    native.status='completed';steer(db,native)
    assert claim(db)==[] and send(db)['delivered']
    with db.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_runs').fetchone()['n']==1
        assert c.execute('SELECT task FROM rvbbit.calliope_specialist_runs').fetchone()['task']=='Original'


def test_late_unconsumed_steering_becomes_one_continuation(db):
    send(db,'first');send(db,'second');native=NativeSteering();steer(db,native)
    native.status='completed';native.pending='\n'.join(x['input'] for x in native.posts)
    finish(db);steer(db,native);runs=claim(db)
    assert len(runs)==1
    with db.factory() as c:
        row=c.execute('SELECT context FROM rvbbit.calliope_specialist_runs WHERE id=%s',(runs[0]['id'],)).fetchone()
        assert len(row['context']['followup_ids'])==2


def test_interrupted_accepted_steering_is_visible_and_not_blindly_replayed(db):
    send(db);native=NativeSteering();steer(db,native)
    native.status='interrupted';finish(db);steer(db,native)
    assert claim(db)==[] and 'needs review' in send(db)['error']


@pytest.mark.parametrize('change',["UPDATE rvbbit.calliope_specialists SET status='disabled'",
                                   "UPDATE rvbbit.calliope_turns SET status='cancelled'",
                                   "UPDATE rvbbit.calliope_sessions SET archived=true",
                                   "UPDATE rvbbit.calliope_specialist_threads SET status='closed'",
                                   "UPDATE rvbbit.calliope_sessions SET owner_email='other@example.com'"])
def test_steering_respects_cancellation_and_access(db,change):
    send(db);native=NativeSteering()
    with db.factory() as c:c.execute(change)
    assert steer(db,native)==0 and native.posts==[]


def test_persisted_steer_row_settles_before_parent_reply(db):
    send(db);native=NativeSteering();steer(db,native)
    with db.factory() as c:
        followups.settle_from_transcript(c,db.rid,[{'role':'user','content':native.posts[0]['input']}])
    assert send(db)['delivered'] and claim(db)==[]


def test_native_start_carries_durable_execution_key(monkeypatch):
    seen={}
    async def count(*args,**kw):return 0
    monkeypatch.setattr(calliope,'_hermes_session_message_count',count)
    monkeypatch.setattr(calliope,'_hermes_headers',lambda config:{'Authorization':'test'})
    class Client:
        async def post(self,url,**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(status_code=400,text='stop after observing request')
    async def run():
        async for _ in calliope._iter_hermes_run(Client(),SimpleNamespace(hermes_url='http://test'),None,None,'session',
            message='assignment',execution_key='specialist:stable-id'):pass
    with pytest.raises(RuntimeError,match='failed to start'):asyncio.run(run())
    assert seen['headers']['Idempotency-Key']=='specialist:stable-id'


def test_live_recovery_worker_reaches_steering_transport(monkeypatch):
    """Run a whole production worker cycle, including local imports and HTTP wiring."""
    seen=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):return self
        def fetchall(self):return []
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,method,url,**kw):
            seen.append((method,url,kw))
            return SimpleNamespace(status_code=200,json=lambda:{'accepted':True})
    class Thread:
        def __init__(self,target,**kw):self.target=target
        def start(self):
            try:self.target()
            except asyncio.CancelledError:pass
    async def sleep(delay):
        if delay==5:raise asyncio.CancelledError()
    async def deliver(factory,request):
        assert await request('POST','/p/iris/v1/runs/native/steer',{'request_id':'stable','input':'correction'})==(200,{'accepted':True})
    monkeypatch.setattr(server,'_SPECIALIST_RECOVERY_THREAD',None)
    monkeypatch.setattr(server,'_conn',Connection)
    monkeypatch.setattr(server.threading,'Thread',Thread)
    monkeypatch.setattr(server.asyncio,'sleep',sleep)
    monkeypatch.setattr(server.httpx,'AsyncClient',Client)
    monkeypatch.setattr(calliope.CalliopeConfig,'from_env',lambda:SimpleNamespace(hermes_url='http://hermes:8642'))
    monkeypatch.setattr(calliope,'_hermes_headers',lambda config:{'Authorization':'test-only'})
    import specialist_notices
    monkeypatch.setattr(specialist_notices,'notify_active_parents',deliver)
    monkeypatch.setattr(followups,'steer_pending',deliver)
    monkeypatch.setattr(followups,'claim',lambda *args,**kw:[])
    server._start_specialist_recovery_worker()
    assert seen==[('POST','http://hermes:8642/p/iris/v1/runs/native/steer',
                  {'headers':{'Authorization':'test-only'},'json':{'request_id':'stable','input':'correction'}})]*2
