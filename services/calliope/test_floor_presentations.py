"""Run against a disposable database: FLOOR_TEST_ADMIN_DSN must allow CREATE DATABASE."""
import os
from pathlib import Path
import threading
import time
import uuid

import pytest
import floor_presentations as fp

MIGRATION = Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0407_floor_presentations.sql'

@pytest.fixture
def db():
    psycopg = pytest.importorskip('psycopg')
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    admin = os.environ.get('FLOOR_TEST_ADMIN_DSN')
    if not admin:
        pytest.skip('FLOOR_TEST_ADMIN_DSN required')
    name = 'floor_labels_' + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE {name}')
    dsn = make_conninfo(admin, dbname=name)
    def factory():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        with factory() as c:
            c.execute('''CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.operators(name text,model text,system_prompt text,user_prompt text,steps jsonb);
            INSERT INTO rvbbit.operators VALUES('clover_llm_extract','clover','system','user','[]');
            CREATE TABLE rvbbit.calliope_sessions(id text,owner_email text);
            CREATE FUNCTION rvbbit.calliope_session_can_view(text,text,boolean) RETURNS boolean
                LANGUAGE sql AS $$ SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions WHERE id=$1 AND owner_email=$2) $$;
            CREATE TABLE rvbbit.calliope_turns(id text,session_id text,ordinal int,turn_kind text,user_message text,assistant_message text);
            CREATE TABLE rvbbit.calliope_playbooks(id text,capability_name text,approved_version int);
            CREATE TABLE rvbbit.capability_access_policies(capability_kind text,capability_name text,visibility text);
            CREATE TABLE rvbbit.work_graph(item_id text,item_kind text,column_state text,attention_rank int,
                last_activity_at timestamptz,ref jsonb,owner_email text,display_title text,state_reason text,
                summary text,active_now boolean,active_child_count int,related_signal_count int,surface_count int);
            CREATE TABLE rvbbit.test_calls(input text);
            CREATE FUNCTION rvbbit.clover_llm_extract(text,text) RETURNS jsonb LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO rvbbit.test_calls VALUES($1);
                PERFORM pg_sleep(coalesce(nullif(current_setting('floor.test_sleep',true),''),'0')::float);
                IF $1 LIKE '%bad%' THEN RETURN NULL; END IF;
                RETURN '{"title":"Enriched project title","status":"Ready to review"}'::jsonb;
            END $$;
            INSERT INTO rvbbit.work_graph
            SELECT 'signal:'||g,'work_item','done',100-g,now(),'{}',
                CASE WHEN g<=30 THEN 'alice' ELSE 'bob' END,'Title '||g,'Done','Summary',false,0,0,0
            FROM generate_series(1,60) g;
            ''')
            c.execute(MIGRATION.read_text())
        yield factory
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE {name} WITH (FORCE)')


def candidate(c, owner='alice'):
    return c.execute('SELECT * FROM rvbbit.floor_presentation_candidates(%s) LIMIT 1',(owner,)).fetchone()


def warm(c, row):
    return c.execute('SELECT rvbbit.floor_presentation_refresh(%s,%s,%s) AS ok',
                     (row['item_id'],row['source_text'],row['source_hash'])).fetchone()['ok']


def test_cached_reads_never_infer_and_are_viewer_scoped(db):
    with db() as c:
        assert c.execute(fp.CACHED_SQL,('alice',)).fetchall()==[]
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==0
        row=candidate(c);assert warm(c,row)
        assert not warm(c,row)
        assert len(c.execute(fp.CACHED_SQL,('alice',)).fetchall())==1
        assert c.execute(fp.CACHED_SQL,('bob',)).fetchall()==[]
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==1
        c.execute("UPDATE rvbbit.work_graph SET summary='Changed' WHERE item_id=%s",(row['item_id'],))
        assert c.execute(fp.CACHED_SQL,('alice',)).fetchall()==[]
        assert warm(c,candidate(c))
        c.execute("UPDATE rvbbit.operators SET model='different'")
        assert c.execute(fp.CACHED_SQL,('alice',)).fetchall()==[]


def test_cron_covers_each_owner_commits_and_skips_unchanged(db):
    with db() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.floor_presentation_candidates(NULL)').fetchone()['n']==50
        c.execute('CALL rvbbit.floor_presentations_warm()')
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==50
        c.execute('CALL rvbbit.floor_presentations_warm()')
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==50
        assert len(c.execute(fp.CACHED_SQL,('bob',)).fetchall())==25
        c.execute(MIGRATION.read_text())
        assert len(c.execute(fp.CACHED_SQL,('alice',)).fetchall())==25


def test_failed_labels_back_off_without_hiding_cards(db):
    with db() as c:
        c.execute("UPDATE rvbbit.work_graph SET summary='bad'")
        row=candidate(c);assert not warm(c,row);assert not warm(c,row)
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==1
        assert len(c.execute(fp.PENDING_SQL,('alice',)).fetchall())==24
        assert c.execute(fp.CACHED_SQL,('alice',)).fetchall()==[]


def test_item_lock_deduplicates_concurrent_refresh(db):
    with db() as first, db() as second:
        row=candidate(first)
        first.execute('BEGIN')
        assert warm(first,row)
        started=time.monotonic()
        assert not warm(second,row)
        assert time.monotonic()-started<1
        first.execute('COMMIT')
        assert not warm(second,row)


def test_background_returns_before_inference_and_deduplicates(db):
    entered=threading.Event();release=threading.Event()
    refresher=fp.Refresher(db)
    original=refresher._refresh
    def gated(rows):
        entered.set();release.wait(5);original(rows)
    refresher._refresh=gated
    try:
        started=time.monotonic();result=refresher.read('alice')
        assert result=={'titles':[],'pending':True}
        assert time.monotonic()-started<1
        assert entered.wait(2)
        assert refresher.read('alice')['pending']
    finally:
        release.set()
    deadline=time.monotonic()+5
    while refresher.lock.locked() and time.monotonic()<deadline:time.sleep(.02)
    result=refresher.read('alice')
    assert len(result['titles'])==25 and not result['pending']
    with db() as c:assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==25


def test_cron_keeps_committed_labels_if_a_later_item_fails(db):
    with db() as c:
        c.execute('''CREATE OR REPLACE FUNCTION rvbbit.clover_llm_extract(text,text) RETURNS jsonb
            LANGUAGE plpgsql AS $$ BEGIN
            IF (SELECT count(*) FROM rvbbit.test_calls)>0 THEN RAISE EXCEPTION 'test failure'; END IF;
            INSERT INTO rvbbit.test_calls VALUES($1);
            RETURN '{"title":"Completed first item","status":"Ready to review"}'::jsonb;
            END $$''')
        with pytest.raises(Exception,match='test failure'):
            c.execute('CALL rvbbit.floor_presentations_warm()')
        assert c.execute('SELECT count(*) AS n FROM rvbbit.floor_presentations').fetchone()['n']==1
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==1


def test_authored_notifications_never_trigger_label_inference_even_from_stale_queue(db):
    with db() as c:
        queued=candidate(c)
        c.execute(fp.NOTIFICATION_LABEL_POLICY_SQL)
        c.execute(fp.NOTIFICATION_LABEL_POLICY_SQL) # bootstrap is idempotent
        assert c.execute('SELECT * FROM rvbbit.floor_presentation_candidates(NULL)').fetchall()==[]
        assert c.execute(fp.PENDING_SQL,('alice',)).fetchall()==[]
        assert not c.execute('SELECT rvbbit.floor_presentation_refresh(%s,%s,%s) AS ok',('work_item:old',queued['source_text'],queued['source_hash'])).fetchone()['ok']
        c.execute('CALL rvbbit.floor_presentations_warm()')
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n']==0
    migration=MIGRATION.with_name('0429_floor_attention_labels.sql').read_text()
    assert fp.NOTIFICATION_LABEL_POLICY_SQL.strip()==migration.strip()


def test_chat_cache_reads_latest_saved_labels_without_inference(db):
    session_id = str(uuid.uuid4())
    with db() as c:
        c.execute('''ALTER TABLE rvbbit.calliope_sessions ALTER COLUMN id TYPE uuid USING id::uuid;
            ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text, ADD COLUMN title_source text;
            CREATE TABLE rvbbit.calliope_session_synopses(session_id uuid PRIMARY KEY, synopsis text, status text)''')
        c.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'alice','Initial title','generated')", (session_id,))
        c.execute("INSERT INTO rvbbit.floor_presentations(item_id,source_hash,semantic_title,semantic_status) VALUES(%s,'old-hash','Current Floor title','Short status')", ('chat:' + session_id,))
        c.execute("INSERT INTO rvbbit.calliope_session_synopses VALUES(%s,'Saved whole-thread summary','pending')", (session_id,))
    assert fp.read_thread(db, session_id) == {'title': 'Current Floor title', 'summary': 'Saved whole-thread summary'}
    with db() as c:
        c.execute("UPDATE rvbbit.floor_presentations SET semantic_title='Updated topic',source_hash='new-hash'")
        c.execute("UPDATE rvbbit.calliope_session_synopses SET synopsis='New summary',status='ready'")
    assert fp.read_thread(db, session_id) == {'title': 'Updated topic', 'summary': 'New summary'}
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_sessions SET title='My manual title',title_source='manual'")
        c.execute("DELETE FROM rvbbit.calliope_session_synopses")
    assert fp.read_thread(db, session_id) == {'title': 'My manual title', 'summary': 'Short status'}
    with db() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.test_calls').fetchone()['n'] == 0
        c.execute('DROP TABLE rvbbit.calliope_session_synopses')
    assert fp.read_thread(db, session_id) is None
