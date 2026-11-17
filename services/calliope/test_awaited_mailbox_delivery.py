"""Real PostgreSQL ordering/authorization checks for direct result receipt."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
import server


@pytest.fixture
def delivery_db(monkeypatch):
    dsn=os.environ.get('MAILBOX_DELIVERY_TEST_DSN')
    if not dsn:
        pytest.skip('requires an empty disposable PostgreSQL database')
    with psycopg.connect(dsn,autocommit=True) as c:
        assert c.execute("SELECT to_regnamespace('rvbbit')").fetchone()[0] is None
        c.execute('''CREATE SCHEMA rvbbit;
          CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,hermes_session_id text);
          CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_specialists(id uuid PRIMARY KEY,specialist_key text,display_name text,profile_name text);
          CREATE TABLE rvbbit.calliope_specialist_runs(id uuid PRIMARY KEY,specialist_id uuid,source_session_id uuid,owner_email text,
            status text,delivery text,context jsonb DEFAULT '{}',thread_id uuid);
          CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_email text,kind text,
            from_kind text,from_ref text,from_label text,to_session_id uuid,thread_id uuid,run_id uuid,in_reply_to uuid,
            subject text,body text,payload jsonb DEFAULT '{}',status text DEFAULT 'queued',delivered_at timestamptz,delivered_turn_id uuid);
        ''')
        factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
        monkeypatch.setattr(server,'_conn',factory)
        monkeypatch.setattr(server,'_specialist_round_message',lambda run:('Reply','The result',{},'reply'))
        sid,tid,specialist,rid=[str(uuid4()) for _ in range(4)]
        c.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'owner@example.com','calliope_parent')",(sid,))
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",(tid,sid))
        c.execute("INSERT INTO rvbbit.calliope_specialists VALUES(%s,'iris','Iris','iris')",(specialist,))
        c.execute("INSERT INTO rvbbit.calliope_specialist_runs(id,specialist_id,source_session_id,owner_email,status,delivery) VALUES(%s,%s,%s,'owner@example.com','complete','mailbox')",(rid,specialist,sid))
        try:yield factory,rid,tid
        finally:c.execute('DROP SCHEMA rvbbit CASCADE')


@pytest.mark.parametrize('first',['queue','ack','race'])
def test_direct_result_cannot_be_delivered_again_in_either_order(delivery_db,first):
    factory,rid,tid=delivery_db
    ack=lambda:server._acknowledge_awaited_specialist(rid,'owner@example.com','calliope_parent')
    queue=lambda:server._queue_specialist_round_outcome(rid)
    if first=='queue':queue();assert ack()
    elif first=='ack':assert ack();queue()
    else:
        barrier=threading.Barrier(2)
        def concurrent(fn):barrier.wait();return fn()
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(concurrent,fn) for fn in (ack,queue)]
            for future in futures:future.result(timeout=10)
    # Retrying either side and recovery publication are idempotent.
    assert ack();queue()
    with factory() as c:
        run=c.execute('SELECT context FROM rvbbit.calliope_specialist_runs WHERE id=%s',(rid,)).fetchone()
        messages=c.execute('SELECT status,delivered_turn_id FROM rvbbit.calliope_agent_messages WHERE run_id=%s',(rid,)).fetchall()
    assert run['context']['awaited_delivery']['turn_id']==tid
    assert len(messages)<=1
    assert all(m['status']=='delivered' and str(m['delivered_turn_id'])==tid for m in messages)


@pytest.mark.parametrize('owner,session,status',[
    ('other@example.com','calliope_parent','running'),
    ('owner@example.com','another_notebook','running'),
    ('owner@example.com','calliope_specialist_child','running'),
    ('owner@example.com','calliope_parent','complete'),
])
def test_other_callers_and_finished_parents_cannot_consume_reply(delivery_db,owner,session,status):
    factory,rid,tid=delivery_db
    with factory() as c:c.execute('UPDATE rvbbit.calliope_turns SET status=%s',(status,))
    server._queue_specialist_round_outcome(rid)
    assert not server._acknowledge_awaited_specialist(rid,owner,session)
    with factory() as c:
        assert c.execute('SELECT status FROM rvbbit.calliope_agent_messages').fetchone()['status']=='queued'
