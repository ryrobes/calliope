"""Real PostgreSQL lifecycle tests. Use a disposable database only.

CALLIOPE_INPUT_TEST_DSN points at an EMPTY test database; this fixture owns its
rvbbit schema. Browser tests are in test_input_questions_ui.py.
"""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
import psycopg
from psycopg.rows import dict_row

import calliope_inputs as inputs

MIGRATION = Path(__file__).resolve().parents[2]/'crates/pg_rvbbit/sql/migrations/0411_calliope_input_requests.sql'
OWNER = 'viewer@example.com'
REQUEST = {'title': 'Audience', 'prompt': 'Who should see it?', 'fields': [
    {'key': 'audience', 'label': 'Audience', 'type': 'choice', 'required': True,
     'options': [{'value': 'leaders', 'label': 'Leadership'}, {'value': 'team', 'label': 'Team'}]},
    {'key': 'share', 'label': 'Share it', 'type': 'boolean', 'required': True},
    {'key': 'notes', 'label': 'Notes', 'type': 'textarea'}]}
ANSWERS = {'audience': 'leaders', 'share': False}


@pytest.fixture
def db():
    dsn = os.environ.get('CALLIOPE_INPUT_TEST_DSN')
    if not dsn:
        pytest.skip('Set CALLIOPE_INPUT_TEST_DSN to an empty disposable database')
    def factory():
        return psycopg.connect(dsn, row_factory=dict_row)
    with factory() as conn:
        # Refuse to overwrite an actual installation, even by a bad DSN.
        assert not conn.execute("SELECT to_regnamespace('rvbbit') AS n").fetchone()['n']
        conn.execute('''CREATE SCHEMA rvbbit;
        CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,channel text DEFAULT 'web',archived boolean DEFAULT false,specialist_key text,updated_at timestamptz DEFAULT now());
        CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text,client_turn_id uuid,ordinal integer DEFAULT 1,created_at timestamptz DEFAULT now());
        CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,turn_id uuid,kind text,payload jsonb);
        CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY,owner_email text,kind text,from_kind text,from_ref text,from_label text,to_session_id uuid,subject text,body text,payload jsonb,status text DEFAULT 'queued',not_before timestamptz DEFAULT now(),attempts int DEFAULT 0,last_error text,delivered_turn_id uuid,delivered_at timestamptz,created_at timestamptz DEFAULT now());''')
        import application_teams, calliope_access
        conn.execute(application_teams.DDL)
        conn.execute(calliope_access.DDL)
        inputs.ensure(conn)
        import calliope
        conn.execute(calliope._INBOX_DDL)
        # Exercise the exact new graph branch, including its SQL types and
        # parent session, independently of unrelated catalog dependencies.
        branch = MIGRATION.read_text().rsplit('UNION ALL', 1)[1]
        columns = 'item_id,item_kind,title,column_state,detail_state,owner_email,agents,session_id,created_at,updated_at,ref,summary,parent_id,last_activity_at,active_now,state_reason,default_hidden,attention_rank,surface_count,display_title,idle_seconds,child_thread_count,active_child_count,related_signal_count,route'
        conn.execute(f'CREATE VIEW rvbbit.work_graph ({columns}) AS ' + branch)
    try:
        yield factory
    finally:
        with factory() as conn:
            conn.execute('DROP SCHEMA rvbbit CASCADE')


def question(db, channel='web'):
    sid, tid, qid = [str(uuid.uuid4()) for _ in range(3)]
    with db() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email,channel) VALUES(%s,%s,%s)', (sid, OWNER, channel))
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'complete')", (tid,sid))
        conn.execute("INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,'input',%s::jsonb)", (qid,sid,tid,json.dumps(REQUEST)))
    return sid,tid,qid


def test_headless_capture_and_answer_receipt_survive_reload(db):
    sid,tid,qid = question(db)
    assert inputs.get(db, OWNER,qid)['status']=='open'
    with db() as conn:
        card=conn.execute('SELECT * FROM rvbbit.work_graph WHERE session_id=%s',(sid,)).fetchone()
        assert card['column_state']=='awaiting_you' and card['parent_id']=='chat:'+sid
    receipt=inputs.change(db,OWNER,qid,'answer',{'answers':ANSWERS,'revision':1})
    assert receipt['status']=='answered' and receipt['revision']==2
    assert receipt['answers']['share'] is False and receipt['answered_by']==OWNER
    assert receipt['delivery_status']=='queued'
    assert inputs.delivery(db,receipt['delivery_message_id'])['id']==uuid.UUID(qid)
    assert inputs.get(db,OWNER,qid)['delivery_message_id']==receipt['delivery_message_id']
    with db() as conn:
        assert conn.execute('SELECT column_state FROM rvbbit.work_graph').fetchone()['column_state']=='done'
        surface=conn.execute('SELECT payload FROM rvbbit.calliope_surfaces WHERE id=%s',(qid,)).fetchone()['payload']
        assert surface['request_state']['status']=='answered'
        assert surface['request_state']['answers']==ANSWERS


@pytest.mark.parametrize('action,expected', [('answer', 'done'), ('cancel', 'dismissed')])
def test_answer_resolves_matching_inbox_blocker_and_late_republish(db, action, expected):
    import calliope
    sid, tid, qid = question(db)
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s", (tid,))
    publish = lambda **kw: calliope.publish_work_item(
        db, sid, 'blocked', 'Approval open', dedupe_key='approval', **kw)
    item = publish()
    assert item['context']['input_request_id'] == qid
    unrelated = calliope.publish_work_item(db, sid, 'blocked', 'Independent blocker',
        context={'input_request_id': None})
    receipt = inputs.change(db, OWNER, qid, action, {'answers': ANSWERS, 'revision': 1})
    with db() as conn:
        rows = conn.execute('SELECT id,state,resolved_at FROM rvbbit.calliope_work_items').fetchall()
        states = {str(row['id']): row for row in rows}
        assert states[item['id']]['state'] == expected
        assert states[item['id']]['resolved_at'] is not None
        assert states[unrelated['id']]['state'] == 'unread'
        # An old alert's retry arrives after the parent has moved on.
        conn.execute("UPDATE rvbbit.calliope_turns SET status='complete' WHERE id=%s", (tid,))
        next_turn, next_question = str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,ordinal) VALUES(%s,%s,'running',2)",
                     (next_turn, sid))
        conn.execute("INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,'input',%s::jsonb)",
                     (next_question, sid, next_turn, json.dumps(REQUEST)))
    assert publish()['state'] == expected
    assert publish()['context']['input_request_id'] == qid
    retry = inputs.change(db, OWNER, qid, action, {'answers': ANSWERS, 'revision': 1})
    assert retry['delivery_message_id'] == receipt['delivery_message_id']
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 1


def test_inbox_linking_is_exact_scoped_and_handles_alert_before_question(db):
    import calliope
    sid, tid, qid = question(db)
    other_sid, _, other_qid = question(db)
    with pytest.raises(ValueError, match='belong'):
        calliope.publish_work_item(db, sid, 'blocked', 'Wrong chat', context={'input_request_id': other_qid})
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s", (tid,))
        second = str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,'input',%s::jsonb)",
                     (second, sid, tid, json.dumps(REQUEST)))
    ambiguous = calliope.publish_work_item(db, sid, 'blocked', 'Two open questions')
    assert 'input_request_id' not in ambiguous['context']
    explicit = calliope.publish_work_item(db, sid, 'blocked', 'Only first question', context={'input_request_id': qid})
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_input_requests SET request=request || %s::jsonb WHERE id=%s",
                     (json.dumps({'work_item_ids': [ambiguous['id']]}), second))
        inputs.link_work_items(conn, second)
    inputs.change(db, OWNER, qid, 'answer', {'answers': ANSWERS, 'revision': 1})
    with db() as conn:
        assert conn.execute('SELECT state FROM rvbbit.calliope_work_items WHERE id=%s', (explicit['id'],)).fetchone()['state'] == 'done'
        assert conn.execute('SELECT state FROM rvbbit.calliope_work_items WHERE id=%s', (ambiguous['id'],)).fetchone()['state'] == 'unread'
    inputs.change(db, OWNER, second, 'cancel', {'revision': 1})
    with db() as conn:
        assert conn.execute('SELECT state FROM rvbbit.calliope_work_items WHERE id=%s', (ambiguous['id'],)).fetchone()['state'] == 'dismissed'


def test_answer_and_alert_publication_race_cannot_leave_an_open_blocker(db):
    import calliope
    sid, tid, qid = question(db)
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s", (tid,))
    def run(action):
        if action == 'answer':
            return inputs.change(db, OWNER, qid, 'answer', {'answers': ANSWERS, 'revision': 1})
        return calliope.publish_work_item(db, sid, 'blocked', 'Approval pending', dedupe_key='race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ['answer', 'publish']))
    with db() as conn:
        assert conn.execute('SELECT state FROM rvbbit.calliope_work_items').fetchone()['state'] == 'done'
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 1


def test_failed_inbox_resolution_rolls_back_answer_and_continuation(db):
    import calliope
    sid, _, qid = question(db)
    calliope.publish_work_item(db, sid, 'blocked', 'Approval pending', context={'input_request_id': qid})
    with db() as conn:
        conn.execute("ALTER TABLE rvbbit.calliope_work_items ADD CONSTRAINT reject_resolution CHECK (state='unread')")
    with pytest.raises(psycopg.errors.CheckViolation):
        inputs.change(db, OWNER, qid, 'answer', {'answers': ANSWERS, 'revision': 1})
    assert inputs.get(db, OWNER, qid)['status'] == 'open'
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 0


def test_question_tool_preserves_explicit_work_item_links():
    import calliope
    import server
    item_id = str(uuid.uuid4())
    result = server._mcp_request_calliope_input('Approve change', 'Proceed?', [
        server._CalliopeInputField(key='approve', label='Approve', type='boolean', required=True),
    ], work_item_ids=[item_id])
    surfaces = calliope._project_tool_result('request_calliope_input', result, {}, 'call-1')
    assert surfaces[0]['payload']['work_item_ids'] == [item_id]


def test_concurrent_retry_creates_one_continuation(db):
    _,_,qid=question(db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts=list(executor.map(lambda _:inputs.change(db,OWNER,qid,'answer',{'answers':ANSWERS,'revision':1}),range(2)))
    assert receipts[0]['delivery_message_id']==receipts[1]['delivery_message_id']
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==1
    with pytest.raises(inputs.InputConflict):
        inputs.change(db,OWNER,qid,'answer',{'answers':{**ANSWERS,'audience':'team'},'revision':1})


def test_authorization_validation_cancel_and_channel(db):
    _,_,qid=question(db)
    for action in ('answer','cancel'):
        with pytest.raises(LookupError):
            inputs.change(db,'stranger@example.com',qid,action,{'answers':ANSWERS,'revision':1})
    for answers in ({'audience':'bogus','share':False},{'audience':'leaders','share':'false'}, {'audience':'leaders'}, {**ANSWERS,'unknown':1}, {**ANSWERS,'notes':'x'*4001}):
        with pytest.raises(ValueError):
            inputs.change(db,OWNER,qid,'answer',{'answers':answers,'revision':1})
    with pytest.raises(inputs.InputConflict):
        inputs.change(db,OWNER,qid,'answer',{'answers':ANSWERS,'revision':2})
    assert inputs.change(db,OWNER,qid,'cancel',{'revision':1})['status']=='cancelled'
    assert inputs.change(db,OWNER,qid,'cancel',{'revision':1})['revision']==2
    with pytest.raises(inputs.InputConflict):
        inputs.change(db,OWNER,qid,'answer',{'answers':ANSWERS,'revision':1})
    _,_,external=question(db,'google_chat')
    assert not inputs.get(db,OWNER,external)['can_answer']
    with pytest.raises(inputs.InputConflict):
        inputs.change(db,OWNER,external,'answer',{'answers':ANSWERS,'revision':1})
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==1


def test_no_backfill_and_transaction_rollback(db):
    _,_,qid=question(db)
    with db() as conn:
        conn.execute('DELETE FROM rvbbit.calliope_input_requests WHERE id=%s',(qid,))
        conn.execute("UPDATE rvbbit.calliope_surfaces SET payload=payload-'request_state' WHERE id=%s",(qid,))
        conn.execute(MIGRATION.read_text().split('-- Work Graph projection:')[0])
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_input_requests').fetchone()['n']==0
    _,_,fresh=question(db)
    with db() as conn:
        conn.execute("ALTER TABLE rvbbit.calliope_input_requests ADD CONSTRAINT test_fail CHECK(status='open')")
    with pytest.raises(psycopg.errors.CheckViolation):
        inputs.change(db,OWNER,fresh,'answer',{'answers':ANSWERS,'revision':1})
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==0
    assert inputs.get(db,OWNER,fresh)['status']=='open'


def test_mailbox_isolates_answers_and_recovers_lease(db):
    import calliope
    sid,_,qid=question(db)
    receipt=inputs.change(db,OWNER,qid,'answer',{'answers':ANSWERS,'revision':1})
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_agent_messages(id,to_session_id,owner_email,payload) VALUES(%s,%s,%s,'{}')",(str(uuid.uuid4()),sid,OWNER))
    batch=calliope._claim_agent_mailbox_batch(db)
    assert len(batch)==1 and str(batch[0]['id'])==receipt['delivery_message_id']
    # Crash between claiming and starting: reclaim the same persisted answer.
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_agent_messages SET not_before=now()-interval '1 second' WHERE id=%s",(receipt['delivery_message_id'],))
    again=calliope._claim_agent_mailbox_batch(db)
    assert len(again)==1 and again[0]['id']==batch[0]['id'] and again[0]['attempts']==2
    calliope._mark_agent_messages_delivered(db,[receipt['delivery_message_id']],None)
    ordinary=calliope._claim_agent_mailbox_batch(db)
    assert len(ordinary)==1 and ordinary[0]['id']!=batch[0]['id']


def test_bootstrap_handles_a_shallow_installed_path(monkeypatch, tmp_path):
    # /app/module.py has fewer parents than the source checkout layout.
    # Missing packaged SQL should give an actionable error, not IndexError.
    monkeypatch.setattr(inputs, '__file__', '/installed-module.py')
    real_path = inputs.Path
    monkeypatch.setattr(inputs, 'Path', lambda value: tmp_path/'missing.sql' if value == '/app/sql/0411_calliope_input_requests.sql' else real_path(value))
    class Connection:
        def execute(self, sql):
            return self
        def fetchone(self):
            return {'relation': None}
    with pytest.raises(RuntimeError, match='Missing canonical input request migration'):
        inputs.ensure(Connection())


def test_required_gate_is_durable_and_optional_requests_do_not_block(db):
    sid,tid,qid=question(db)
    with db() as conn:
        assert inputs.blocking_request(conn,OWNER,sid)==qid
        assert inputs.blocking_request(conn,'stranger@example.com',sid) is None
        optional=str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,'input',%s::jsonb)",
                     (optional,sid,tid,json.dumps({**REQUEST,'blocking':False})))
    receipt=inputs.change(db,OWNER,qid,'cancel',{'revision':1})
    with db() as conn:
        assert inputs.blocking_request(conn,OWNER,sid) is None
    assert inputs.get(db,OWNER,optional)['status']=='open'
    delivered=inputs.delivery(db,receipt['delivery_message_id'])
    assert delivered['status']=='cancelled' and delivered['answered_by']==OWNER
    assert 'No answer or approval was provided' in delivered['body']
    again=inputs.change(db,OWNER,qid,'cancel',{'revision':1})
    assert again['delivery_message_id']==receipt['delivery_message_id']
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==1


def test_cancel_and_answer_race_have_one_resolution_and_one_delivery(db):
    _,_,qid=question(db)
    def close(action):
        try:return inputs.change(db,OWNER,qid,action,{'revision':1,'answers':ANSWERS})
        except inputs.InputConflict:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(close,['answer','cancel']))
    assert sum(result is not None for result in results)==1
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==1
    assert inputs.get(db,OWNER,qid)['status'] in ('answered','cancelled')


def test_http_turn_rejects_unresolved_question_before_starting_work(db,monkeypatch,tmp_path):
    import asyncio
    import sys
    from types import SimpleNamespace
    import calliope
    sid,_,qid=question(db)
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def register(handler):routes[(path,tuple(methods))]=handler;return handler
            return register
    monkeypatch.setitem(sys.modules,'auth',SimpleNamespace(read_session_full=lambda r:{'identity':OWNER,'sub':OWNER},google_enabled=lambda:False,background_layer=lambda *a,**k:''))
    monkeypatch.setattr(calliope,'ensure_tables',lambda _:None)
    monkeypatch.setattr(calliope.hermes_docs,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.hermes_config,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.provider_settings,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.threading.Thread,'start',lambda _:None)
    monkeypatch.setattr(calliope.application_teams,'observe_principal_on_connection',lambda *a,**k:None)
    monkeypatch.setattr(calliope,'_session_for_owner',lambda *a:{'id':sid,'owner_email':OWNER,'channel':'web'})
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes.invalid:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test-only')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    assert calliope.register_calliope_routes(MCP(),db,'',lambda value:value)
    class Request:
        path_params={'session_id':sid}
        async def json(self):return {'message':'Ignore the pending question','turn_kind':'chat'}
    response=asyncio.run(routes[('/api/calliope/sessions/{session_id}/turn',('POST',))](Request()))
    assert response.status_code==409
    assert json.loads(response.body)['error']=={'code':'INPUT_REQUIRED','message':'Answer or cancel the pending request before sending another message.','request_id':qid}
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_turns').fetchone()['n']==1
