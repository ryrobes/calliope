"""Completion races from the live Sonnet trial, using real disposable PostgreSQL."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import server
import specialist_followups as followups
import specialist_notices as notices
from test_specialist_followups import db, finish, claim, NativeSteering, steer


@pytest.fixture(autouse=True)
def tool_logging(monkeypatch):
    async def logged(name,args,fn): return await fn()
    monkeypatch.setattr(server, '_logged_async', logged)


@pytest.fixture
def completed(db, monkeypatch):
    with db.factory() as c:
        c.execute('ALTER TABLE rvbbit.calliope_sessions ADD COLUMN specialist_key text')
        c.execute("ALTER TABLE rvbbit.calliope_turns ADD COLUMN hermes_run_id text DEFAULT 'parent-native'")
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete',result=%s WHERE id=%s",
                  ('Audit finding. '*1500, db.rid))
    monkeypatch.setattr(server, '_require_application_subject', lambda: SimpleNamespace(subject='owner@example.com'))
    monkeypatch.setattr(server, '_hermes_mcp_envelope', lambda ctx=None: {'platform':'api_server','session_id':'calliope_parent'})
    monkeypatch.setattr(server, '_logged', lambda name,args,fn:fn())
    async def logged(name,args,fn): return await fn()
    monkeypatch.setattr(server, '_logged_async', logged)
    # Keep this test about delivery, not UI transcript assembly.
    def result(rid):
        with db.factory() as c:
            r=c.execute('SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s',(rid,)).fetchone()
            return {'run_id':rid,'thread_id':str(r['thread_id']),'status':r['status'],'response':r['result']}
    monkeypatch.setattr(server, '_specialist_result_payload', result)
    return db


def queue_reply(db):
    with db.factory() as c:
        return str(c.execute("INSERT INTO rvbbit.calliope_agent_messages(owner_email,kind,from_kind,run_id,thread_id,to_session_id,body) "
                             "VALUES('owner@example.com','reply','specialist',%s,%s,%s,'full answer') RETURNING id",
                             (db.rid,db.thread,db.sid)).fetchone()['id'])


def continue_round(db, intent='continue'):
    return asyncio.run(server._mcp_continue_calliope_specialist(db.thread,'Send findings now and stop',mode='async',intent=intent))


@pytest.mark.parametrize('published',[False,True])
def test_completed_answer_blocks_repeat_before_and_after_mailbox_publication(completed,published):
    db=completed
    if published: queue_reply(db)
    result=continue_round(db)
    assert result['run_id']==db.rid and not result['followup_started'] and result['needs_result_read']
    assert result['response'].startswith('Audit finding.') and result['read_more']['arguments']['run_id']==db.rid
    with db.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_runs').fetchone()['n']==1
    page=server._mcp_read_calliope_specialist_result(db.rid)
    assert page['mailbox_acknowledged'] and page['next_offset']==8000
    with db.factory() as c:
        assert followups.unread_result(c,db.thread) is None
        if published:
            assert c.execute('SELECT status FROM rvbbit.calliope_agent_messages').fetchone()['status']=='delivered'
    # Explicit finish is still read-only after the answer was acknowledged.
    result=continue_round(db,'finish')
    assert result['run_id']==db.rid and not result['followup_started']


def test_reading_other_notebooks_answer_does_not_acknowledge_mailbox(completed,monkeypatch):
    queue_reply(completed)
    monkeypatch.setattr(server,'_hermes_mcp_envelope',lambda ctx=None:{'platform':'api_server','session_id':'calliope_other'})
    assert not server._mcp_read_calliope_specialist_result(completed.rid)['mailbox_acknowledged']
    with completed.factory() as c:
        assert c.execute('SELECT status FROM rvbbit.calliope_agent_messages').fetchone()['status']=='queued'


def test_thread_await_returns_unread_earlier_round_instead_of_waiting_for_new_round(completed):
    db=completed;queue_reply(db)
    with db.factory() as c:
        c.execute("INSERT INTO rvbbit.calliope_specialist_runs(id,specialist_id,owner_email,source_session_id,source_turn_id,hermes_session_id,status,task,thread_id,round_no) "
                  "VALUES(%s,%s,'owner@example.com',%s,%s,'child_session','running','New work',%s,2)",
                  (str(uuid4()),db.sp,db.sid,db.tid,db.thread))
    result=asyncio.run(server._mcp_await_calliope_specialists(thread_ids=[db.thread],timeout_seconds=5))
    assert result['status']=='complete' and result['results'][0]['run_id']==db.rid
    assert result['results'][0]['mailbox_acknowledged']


@pytest.mark.parametrize('accepted_before_finish',[False,True])
def test_finish_guidance_never_replays_as_new_round(db,accepted_before_finish):
    result=continue_round(db,'finish')
    assert result['run_id']==db.rid and not result['followup_started']
    native=NativeSteering()
    if accepted_before_finish: steer(db,native)
    native.status='completed';native.pending='\n'.join(x['input'] for x in native.posts)
    finish(db)
    # Even a claiming worker running before the steering reconciler cannot replay it.
    assert claim(db)==[]
    steer(db,native)
    assert claim(db)==[] and continue_round(db,'finish')['status']=='round_ended'
    with db.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_runs').fetchone()['n']==1


def test_finish_delivery_stays_bound_when_a_new_round_exists(db):
    continue_round(db,'finish');finish(db)
    with db.factory() as c:
        c.execute("INSERT INTO rvbbit.calliope_specialist_runs(id,specialist_id,owner_email,source_session_id,source_turn_id,hermes_session_id,status,task,thread_id,round_no,hermes_run_id) "
                  "VALUES(%s,%s,'owner@example.com',%s,%s,'child_session','running','New work',%s,2,'new-native')",
                  (str(uuid4()),db.sp,db.sid,db.tid,db.thread))
    native=NativeSteering();steer(db,native)
    assert native.posts==[] and continue_round(db,'finish')['status']=='round_ended'


def test_active_parent_notice_is_idempotent_and_keeps_full_answer_queued(completed):
    db=completed;queue_reply(db);sent=[]
    async def request(method,path,body):
        sent.append((path,body));return 200,{'accepted':True}
    assert asyncio.run(notices.notify_active_parents(db.factory,request))==1
    assert asyncio.run(notices.notify_active_parents(db.factory,request))==0
    assert sent[0][0]=='/v1/runs/parent-native/steer' and db.rid in sent[0][1]['input']
    with db.factory() as c:
        row=c.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchone()
        assert row['status']=='queued' and row['body']=='full answer'
    assert server._mcp_read_calliope_specialist_result(db.rid)['mailbox_acknowledged']
    assert asyncio.run(notices.notify_active_parents(db.factory,request))==0


def test_notice_lost_ack_retries_exact_request_and_new_parent_gets_new_notice(completed):
    db=completed;queue_reply(db);sent=[]
    async def request(method,path,body):
        sent.append(body)
        if len(sent)==1: raise TimeoutError('Accepted upstream, HTTP reply lost')
        return 200,{'accepted':True}
    asyncio.run(notices.notify_active_parents(db.factory,request))
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_agent_messages SET payload=payload #- '{active_notice,retry_after}'")
    asyncio.run(notices.notify_active_parents(db.factory,request))
    assert sent[0]==sent[1]
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_turns SET hermes_run_id='next-parent'")
    asyncio.run(notices.notify_active_parents(db.factory,request))
    assert sent[2]['request_id']!=sent[0]['request_id'] and sent[2]['input']==sent[0]['input']


@pytest.mark.parametrize('change',[
    "UPDATE rvbbit.calliope_sessions SET owner_email='other@example.com'",
    "UPDATE rvbbit.calliope_sessions SET archived=true",
    "UPDATE rvbbit.calliope_turns SET author_email='other@example.com'",
    "UPDATE rvbbit.calliope_turns SET status='interrupted'",
    "UPDATE rvbbit.calliope_specialist_runs SET owner_email='other@example.com'",
    "UPDATE rvbbit.calliope_agent_messages SET status='cancelled'",
    "UPDATE rvbbit.calliope_agent_messages SET kind='question'",
])
def test_notice_respects_recipient_owner_and_lifecycle(completed,change):
    db=completed;queue_reply(db)
    with db.factory() as c:c.execute(change)
    async def request(*args):pytest.fail('Must not steer this parent')
    assert asyncio.run(notices.notify_active_parents(db.factory,request))==0


def test_parent_profile_route_is_used(completed):
    db=completed;queue_reply(db);paths=[]
    with db.factory() as c:c.execute("UPDATE rvbbit.calliope_sessions SET specialist_key='iris'")
    async def request(method,path,body):paths.append(path);return 200,{'accepted':True}
    asyncio.run(notices.notify_active_parents(db.factory,request))
    assert paths==['/p/iris/v1/runs/parent-native/steer']


def test_sql_recall_keywords_recover_original_write_and_fallback_stays_scoped(completed,monkeypatch):
    db=completed
    monkeypatch.setattr(server,'ACTIVITY_TABLE','rvbbit.recall_test')
    monkeypatch.setattr(server,'_calliope_activity_for_hermes_session',lambda c,s:{'owner':'owner@example.com','calliope_session_id':db.sid})
    sql='CREATE TABLE sw_ufo AS SELECT zd.* FROM sw_zip_dens zd'
    with db.factory() as c:
        c.execute('''CREATE TABLE rvbbit.recall_test(id bigint,ts text DEFAULT 'today',tool text,args jsonb,
                  ok bool DEFAULT true,error jsonb,result_summary jsonb,subject text,provenance jsonb)''')
        for rid,owner,sid in [(3662,'owner@example.com',db.sid),(4000,'other@example.com',db.sid),(3999,'owner@example.com',str(uuid4()))]:
            c.execute("INSERT INTO rvbbit.recall_test(id,tool,args,subject,provenance) VALUES(%s,'run_workspace_sql',%s,%s,%s)",
                      (rid,json.dumps({'sql':sql}),owner,json.dumps({'calliope_session_id':sid})))
    result=server._mcp_recall_calliope_sql(query='sw_ufo CREATE TABLE zd sw_zip_dens')
    assert result['match_mode']=='all_keywords' and [r['receipt_id'] for r in result['receipts']]==[3662]
    assert server._mcp_recall_calliope_sql(receipt_id=3662)['text']==sql
    result=server._mcp_recall_calliope_sql(query='not_found_%')
    assert result['match_mode']=='recent_fallback' and result['next_query']==''
    assert [r['receipt_id'] for r in result['receipts']]==[3662]
    assert 'No keyword match' in result['next']
    with pytest.raises(PermissionError):server._mcp_recall_calliope_sql(receipt_id=4000)
    assert server._mcp_recall_calliope_sql(query='sw_ufo',before_id=3662)['receipts']==[]


def test_new_question_can_start_after_reading_completed_answer(completed,monkeypatch):
    db=completed;queue_reply(db)
    server._mcp_read_calliope_specialist_result(db.rid)
    started=[]
    monkeypatch.setattr(server,'_schedule_specialist_background_round',lambda rid,execute:started.append(rid))
    monkeypatch.setattr(server,'_specialist_queued_payload',lambda rid,**kw:{'status':'running','run_id':rid})
    result=continue_round(db)
    assert result['run_id']!=db.rid and started==[result['run_id']]
    with db.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_runs').fetchone()['n']==2


def test_notice_ack_racing_with_reader_does_not_requeue_read_result(completed):
    db=completed;queue_reply(db)
    async def request(method,path,body):
        assert server._mcp_read_calliope_specialist_result(db.rid)['mailbox_acknowledged']
        return 200,{'accepted':True}
    asyncio.run(notices.notify_active_parents(db.factory,request))
    with db.factory() as c:
        row=c.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchone()
        assert row['status']=='delivered' and row['payload']['delivery_receipt']


def test_parallel_continue_and_await_cannot_hide_original_answer(completed):
    db=completed;queue_reply(db)
    async def run():
        return await asyncio.gather(
            server._mcp_continue_calliope_specialist(db.thread,'Findings now',mode='async'),
            server._mcp_await_calliope_specialists(thread_ids=[db.thread],timeout_seconds=5),
        )
    continued,waited=asyncio.run(run())
    assert not continued['followup_started'] and waited['results'][0]['run_id']==db.rid
    with db.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_specialist_runs').fetchone()['n']==1
