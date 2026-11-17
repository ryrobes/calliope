"""Team TODO stores are observations, never lifecycle authority."""
import json
import calliope
from test_session_activity_db import activity_db, SESSION, RUN, PARENT, SPECIALIST

RUN = '10000000-0000-4000-8000-000000000002'
THREAD = '00000000-0000-0000-0000-000000000005'
REPLY = '00000000-0000-0000-0000-000000000006'
OTHER = '00000000-0000-0000-0000-000000000007'


def snapshot(revision=2, content='Validate results'):
    return {'todos': [{'id': 'check', 'content': content, 'status': 'in_progress'}],
            'revision': revision, 'confirmed': True}


def test_completed_tool_with_bad_result_cannot_confirm_partial_arguments():
    assert calliope._hermes_todo_snapshot('todo', result={'error': 'failed'},
        arguments=snapshot(), confirmed=True) is None
    assert calliope._hermes_todo_snapshot('todo', arguments=snapshot())['confirmed'] is False


def test_specialist_plan_stream_retains_store_and_identity_once():
    snap = {'run': {'id': RUN, 'thread_id': THREAD, 'round': 3,
                   'specialist': {'key': 'iris', 'name': 'Iris'}, 'status': 'running'},
            'messages': [{'kind': 'tool_completed', 'ordinal': 7, 'plan': snapshot()}]}
    state = {}
    frames = calliope._specialist_live_frames(snap, state, PARENT)
    payloads = [json.loads(frame.decode().split('data: ', 1)[1]) for frame in frames]
    plan = next(p for p in payloads if p['type'] == 'plan')
    assert plan['plan']['revision'] == 2 and plan['run_id'] == RUN
    assert plan['thread_id'] == THREAD and plan['round'] == 3
    assert calliope._specialist_live_frames(snap, state, PARENT) == []


def test_durable_plans_survive_handoff_delivery_and_reload_without_leaking_scope(activity_db):
    with activity_db() as c:
        c.execute('''ALTER TABLE rvbbit.calliope_specialist_messages ADD COLUMN ordinal bigserial;
            ALTER TABLE rvbbit.calliope_agent_messages ADD COLUMN delivered_turn_id uuid;
            CREATE TABLE rvbbit.calliope_turn_events(turn_id uuid,sequence int,event_type text,payload jsonb,
                created_at timestamptz DEFAULT now());''')
        c.execute('''INSERT INTO rvbbit.calliope_turns(id,session_id,status,turn_kind,heartbeat_at)
            VALUES(%s,%s,'complete','chat',now())''', (PARENT, SESSION))
        c.execute('INSERT INTO rvbbit.calliope_specialist_threads VALUES(%s,%s,%s,\'{}\')', (THREAD, PARENT, SESSION))
        c.execute('''INSERT INTO rvbbit.calliope_specialist_runs
            (id,thread_id,source_turn_id,source_session_id,specialist_id,status,delivery,round_no,heartbeat_at)
            VALUES(%s,%s,%s,%s,%s,'running','mailbox',1,now())''', (RUN, THREAD, PARENT, SESSION, SPECIALIST))
        c.execute("INSERT INTO rvbbit.calliope_turn_events VALUES(%s,1,'calliope.plan',%s,now())", (PARENT, json.dumps(snapshot())))
        for rev in [2, 1]:  # A late lower revision must not become current on reload.
            c.execute("INSERT INTO rvbbit.calliope_specialist_messages(run_id,role,kind,payload) VALUES(%s,'tool','tool_completed',%s)", (RUN, json.dumps({'plan':snapshot(rev)})))
        # A start preview is not a confirmed store.
        c.execute("INSERT INTO rvbbit.calliope_turn_events VALUES(%s,2,'calliope.plan',%s,now())", (PARENT, json.dumps({**snapshot(8), 'confirmed': False})))
        read = lambda: calliope._session_agent_work(activity_db, SESSION)
        work = read()
        assert work['plans_available']
        plans = {p['key']: p for p in work['plans']}
        assert plans['turn:'+PARENT]['phase'] == 'waiting'
        assert plans['run:'+RUN]['phase'] == 'running'
        assert all(p['active'] and p['revision'] == 2 for p in plans.values())
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET heartbeat_at=now()-interval '5 minutes'")
        assert all(p['phase'] == 'unknown' for p in read()['plans'])
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete',completed_at=now()")
        assert all(p['active'] for p in read()['plans'])  # Publication gap.
        c.execute("INSERT INTO rvbbit.calliope_agent_messages(run_id,to_session_id,kind,status) VALUES(%s,%s,'reply','queued')", (RUN, SESSION))
        assert all(p['active'] for p in read()['plans'])
        c.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivering'")
        assert all(p['active'] for p in read()['plans'])
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,turn_kind,heartbeat_at) VALUES(%s,%s,'running','agent_message',now())", (REPLY, SESSION))
        c.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivered',delivered_turn_id=%s", (REPLY,))
        assert all(p['active'] for p in read()['plans'])
        c.execute("UPDATE rvbbit.calliope_turns SET status='complete' WHERE id=%s", (REPLY,))
        work = read()
        assert work['activity_state']['phase'] == 'ready'
        assert len(work['plans']) == 2 and not any(p['active'] for p in work['plans'])
        assert all(p['todos'][0]['status'] == 'in_progress' for p in work['plans'])
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,turn_kind,heartbeat_at) VALUES(%s,%s,'running','chat',now())", (OTHER, SESSION))
        assert not any(p['active'] for p in read()['plans'])
        assert calliope._session_agent_work(activity_db, OTHER)['plans'] == []
        # A projection outage is not a runtime outage.
        c.execute('DROP TABLE rvbbit.calliope_turn_events')
        work = read()
        assert work['available'] and not work['plans_available']
        assert work['activity_state']['phase'] == 'working'
