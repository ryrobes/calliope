"""Activity snapshots against PostgreSQL; ACTIVITY_TEST_ADMIN_DSN must allow CREATE DATABASE."""
import os
import uuid

import pytest
import calliope

SESSION = '00000000-0000-0000-0000-000000000001'
RUN = '00000000-0000-0000-0000-000000000002'
PARENT = '00000000-0000-0000-0000-000000000003'
SPECIALIST = '00000000-0000-0000-0000-000000000004'


@pytest.fixture
def activity_db():
    psycopg = pytest.importorskip('psycopg')
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    admin = os.environ.get('ACTIVITY_TEST_ADMIN_DSN')
    if not admin:
        pytest.skip('ACTIVITY_TEST_ADMIN_DSN required')
    name = 'calliope_activity_' + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8'")
    dsn = make_conninfo(admin, dbname=name)

    def factory():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)

    try:
        with factory() as conn:
            conn.execute('''CREATE SCHEMA rvbbit;
                CREATE TABLE rvbbit.calliope_turns(id uuid,session_id uuid,turn_kind text,status text,
                    created_at timestamptz DEFAULT now(),heartbeat_at timestamptz,lease_expires_at timestamptz);
                CREATE TABLE rvbbit.calliope_specialists(id uuid,specialist_key text,display_name text);
                CREATE TABLE rvbbit.calliope_specialist_threads(id uuid,root_turn_id uuid,source_session_id uuid,context jsonb);
                CREATE TABLE rvbbit.calliope_specialist_runs(id uuid,thread_id uuid,source_session_id uuid,
                    source_turn_id uuid,specialist_id uuid,round_no int,status text,delivery text,context jsonb,
                    started_at timestamptz DEFAULT now(),created_at timestamptz DEFAULT now(),
                    completed_at timestamptz,heartbeat_at timestamptz,lease_expires_at timestamptz);
                CREATE TABLE rvbbit.calliope_specialist_messages(run_id uuid,role text,kind text,payload jsonb,created_at timestamptz);
                CREATE TABLE rvbbit.calliope_agent_messages(run_id uuid,to_session_id uuid,kind text,
                    status text,from_label text,created_at timestamptz DEFAULT now());''')
            conn.execute('INSERT INTO rvbbit.calliope_specialists VALUES(%s,\'iris\',\'Iris\')', (SPECIALIST,))
        yield factory
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE {name} WITH (FORCE)')


def test_snapshot_covers_publication_claim_response_and_awaited_results(activity_db):
    with activity_db() as conn:
        conn.execute('''INSERT INTO rvbbit.calliope_specialist_runs
            (id,source_session_id,specialist_id,round_no,status,delivery,heartbeat_at)
            VALUES(%s,%s,%s,1,'running','mailbox',now())''', (RUN, SESSION, SPECIALIST))
        read = lambda: calliope._session_agent_work(activity_db, SESSION)
        assert read()['activity_state']['phase'] == 'team_working'
        conn.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete',completed_at=now()")
        work = read()
        assert work['activity_state']['phase'] == 'delivering'
        assert work['pending_publications'][0]['run_id'] == RUN
        conn.execute('''INSERT INTO rvbbit.calliope_agent_messages(run_id,to_session_id,kind,status)
            VALUES(%s,%s,'reply','queued')''', (RUN, SESSION))
        assert read()['activity_state']['phase'] == 'waiting'
        conn.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivering'")
        work = read()
        assert work['queued_messages'] == 0 and work['delivering_messages'] == 1
        assert work['activity_state']['phase'] == 'delivering'
        conn.execute('''INSERT INTO rvbbit.calliope_turns(id,session_id,turn_kind,status,heartbeat_at)
            VALUES(%s,%s,'agent_message','running',now())''', (PARENT, SESSION))
        conn.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivered'")
        assert read()['activity_state']['phase'] == 'responding'
        conn.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
        assert read()['activity_state']['phase'] == 'ready'
        # An awaited result bypasses the mailbox. Sync results do too.
        conn.execute('DELETE FROM rvbbit.calliope_agent_messages')
        conn.execute('''UPDATE rvbbit.calliope_specialist_runs SET context='{"awaited_delivery":{"turn_id":"parent"}}' ''')
        assert read()['activity_state']['phase'] == 'ready'
        conn.execute("UPDATE rvbbit.calliope_specialist_runs SET context='{}',delivery='sync'")
        assert read()['activity_state']['phase'] == 'ready'
        # A publication that never arrives becomes uncertain, not done.
        conn.execute("UPDATE rvbbit.calliope_specialist_runs SET delivery='mailbox',completed_at=now()-interval '2 minutes'")
        assert read()['activity_state']['phase'] == 'unknown'


def test_snapshot_cannot_mix_mailbox_before_and_parent_after_a_claim(activity_db, monkeypatch):
    with activity_db() as conn:
        conn.execute('''INSERT INTO rvbbit.calliope_agent_messages(to_session_id,kind,status)
            VALUES(%s,'reply','queued')''', (SESSION,))
    original = calliope._session_specialist_activity

    def transition_while_snapshot_is_open(conn, session_id):
        with activity_db() as writer:
            with writer.transaction():
                writer.execute('''INSERT INTO rvbbit.calliope_turns(id,session_id,turn_kind,status,heartbeat_at)
                    VALUES(%s,%s,'agent_message','running',now())''', (PARENT, SESSION))
                writer.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivered'")
        return original(conn, session_id)

    monkeypatch.setattr(calliope, '_session_specialist_activity', transition_while_snapshot_is_open)
    work = calliope._session_agent_work(activity_db, SESSION)
    assert work['activity_state']['phase'] == 'waiting'
    assert work['queued_messages'] == 1 and not work['parent_turns']
    monkeypatch.setattr(calliope, '_session_specialist_activity', original)
    assert calliope._session_agent_work(activity_db, SESSION)['activity_state']['phase'] == 'responding'
