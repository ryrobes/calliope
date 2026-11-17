"""Run the registered Work Graph view evolution, not just the 0411 branch.

Backing tables are deliberately minimal; the SELECT definitions and final Floor
projection are production SQL. PostgreSQL is private to this test process.
"""
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from pglast import ast, parse_sql
import pytest

import floor_data
from test_page_floor import projection

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'crates/pg_rvbbit/sql/migrations'
REPAIR = MIGRATIONS / '0466_work_graph_input_requests.sql'
OWNER = 'operator@example.com'

DDL = """
CREATE SCHEMA rvbbit;
CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,title text,
 archived boolean DEFAULT false,specialist_key text,team_mode boolean DEFAULT false,
 purpose text DEFAULT 'chat',channel text DEFAULT 'web',created_at timestamptz DEFAULT now(),
 updated_at timestamptz DEFAULT now());
CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,ordinal int,
 status text,turn_kind text DEFAULT 'chat',user_message text,assistant_message text,author_email text,
 created_at timestamptz DEFAULT now(),completed_at timestamptz,heartbeat_at timestamptz,lease_expires_at timestamptz);
CREATE TABLE rvbbit.calliope_session_synopses(session_id uuid,synopsis text,status text);
CREATE TABLE rvbbit.calliope_session_view_grants(session_id uuid,principal_email text,team_id uuid,
 granted_at timestamptz DEFAULT now());
CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,turn_id uuid,
 kind text,payload jsonb,created_at timestamptz DEFAULT now());
CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY,status text);
CREATE TABLE rvbbit.calliope_specialists(id uuid,specialist_key text);
CREATE TABLE rvbbit.calliope_specialist_threads(id uuid,source_session_id uuid,specialist_id uuid,
 owner_email text,status text,round_count int,objective text,root_turn_id uuid,created_at timestamptz,updated_at timestamptz);
CREATE TABLE rvbbit.calliope_specialist_runs(source_session_id uuid,thread_id uuid,status text,
 heartbeat_at timestamptz,completed_at timestamptz,started_at timestamptz,created_at timestamptz,lease_expires_at timestamptz);
CREATE TABLE rvbbit.calliope_work_items(id uuid,session_id uuid,title text,state text,owner_email text,
 kind text,source text,source_ref text,urgency text,summary text,action_prompt text,
 created_at timestamptz,updated_at timestamptz,engaged_session_id uuid);
CREATE TABLE rvbbit.calliope_workflow_runs(id uuid,session_id uuid,owner_email text,status text,
 result_summary text,workflow_id uuid,trigger_kind text,steps jsonb,started_at timestamptz,completed_at timestamptz);
CREATE TABLE rvbbit.calliope_decision_requests(id uuid,session_id uuid,owner_email text,question text,
 status text,decision_kind text,urgency text,recommended_option text,summary text,
 created_at timestamptz,updated_at timestamptz,answered_at timestamptz);
CREATE TABLE rvbbit.calliope_observations(id uuid,title text,state text,urgency numeric,
 owner_email text,kind text,confidence numeric,signal_count int,summary text,created_at timestamptz,updated_at timestamptz);
CREATE TABLE rvbbit.calliope_playbooks(id uuid,capability_name text,archived boolean,
 approved_version int,owner_email text,source_session_id uuid,playbook_kind text,capability_kind text,
 created_at timestamptz,updated_at timestamptz);
CREATE TABLE rvbbit.agent_overlays(overlay_id uuid,agent_key text,target_kind text,kind text,
 adoption_state text,target_key text,section text,rationale text,created_at timestamptz,updated_at timestamptz);
CREATE TABLE rvbbit.duty_cycle_health(profile_key text,job_id text,name text,last_status text,
 enabled boolean,last_run_at timestamptz,schedule text,failure_streak int,failures_14 int,
 pinned boolean,drift_skipped boolean,last_error text);
CREATE TABLE rvbbit.calliope_pages(id uuid,session_id uuid,owner_email text,title text,question text,
 updated_at timestamptz,status text);
CREATE TABLE rvbbit.calliope_page_runs(page_id uuid,status text);
CREATE TABLE rvbbit.calliope_dream_handoffs(session_id uuid,dream_id uuid,surface_id uuid,message_id uuid,owner_email text);
CREATE TABLE rvbbit.calliope_dreams(id uuid,owner_email text,title text,thesis text,status text,
 portfolio_state text,portfolio_rank int,updated_at timestamptz,impact text,confidence numeric,
 effort text,novelty numeric,rank_score numeric,scope_kind text);
CREATE TABLE rvbbit.calliope_dream_events(dream_id uuid,actor_email text,event_kind text,
 payload jsonb,created_at timestamptz,event_id bigint);
CREATE TABLE rvbbit.hermes_cron_jobs(profile_key text,job_id text,raw jsonb);
CREATE TABLE rvbbit.calliope_session_schedules(id uuid,enabled boolean,session_id uuid,hermes_job_id text,schedule text);
CREATE TABLE rvbbit.floor_piece_archives(item_id text,restored_at timestamptz,archived_by text);
CREATE TABLE rvbbit.capability_access_policies(capability_kind text,capability_name text,visibility text);
CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean
 LANGUAGE sql AS $$SELECT owner_email=$2 FROM rvbbit.calliope_sessions WHERE id=$1$$;
"""


@pytest.fixture
def db(tmp_path):
    data, sock = tmp_path/'db', tmp_path/'socket'
    sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(tmp_path/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def factory():
        return psycopg.connect(host=str(sock),dbname='postgres',row_factory=dict_row)
    try:
        with factory() as c:
            c.execute(DDL)
            c.execute((MIGRATIONS/'0411_calliope_input_requests.sql').read_text().split('-- Work Graph projection:')[0])
        yield factory
    finally:
        subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


def graph_migrations():
    # Use registration order so a later full-view replacement cannot silently
    # make this a test of only a historical fragment again. Parse statements:
    # semicolons occur in SQL strings, and cannot delimit them with split(';').
    registry=(ROOT/'crates/pg_rvbbit/src/migrations.rs').read_text()
    names=re.findall(r'include_str!\("\.\./sql/migrations/([^"/]+)"\)',registry)
    assert REPAIR.name in names
    for name in names:
        if name < '0387':
            continue  # Start with the evolved, complete 25-column view.
        path=MIGRATIONS/name
        source=path.read_text()
        if not re.search(r'CREATE (?:OR REPLACE )?VIEW rvbbit\.work_graph\b',source):
            continue
        for stmt in parse_sql(source):
            if isinstance(stmt.stmt,ast.ViewStmt):
                if stmt.stmt.view.schemaname!='rvbbit' or stmt.stmt.view.relname!='work_graph':
                    continue
            elif not isinstance(stmt.stmt,ast.DoStmt):
                continue
            # pglast maps PostgreSQL byte locations back to Python string offsets.
            sql=source[stmt.stmt_location:stmt.stmt_location+stmt.stmt_len] if stmt.stmt_len else source[stmt.stmt_location:]
            if re.search(r'CREATE (?:OR REPLACE )?VIEW rvbbit\.work_graph\b',sql):
                yield name,sql


def seed(c):
    sid,tid,qid=[uuid4() for _ in range(3)]
    c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email,title) VALUES(%s,%s,%s)',(sid,OWNER,'Branch review'))
    c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,status,user_message,assistant_message) VALUES(%s,%s,8,'complete','Set a schedule','Please choose a time')",(tid,sid))
    c.execute("INSERT INTO rvbbit.calliope_session_synopses VALUES(%s,'Whole-thread business brief','ready')",(sid,))
    c.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,payload) VALUES(%s,%s,%s,'input',%s::jsonb)",
              (qid,sid,tid,json.dumps({'title':'Monday branch review schedule','prompt':'When should it run?','fields':[{'key':'time','label':'Time','type':'text'}]})))
    return sid,tid,qid


def test_final_migrated_graph_and_floor_keep_open_questions(db):
    with db() as c:
        sid,tid,qid=seed(c)
        for name,sql in graph_migrations():
            c.execute(sql)
            if name.startswith('0411'):
                assert c.execute("SELECT count(*) AS n FROM rvbbit.work_graph WHERE item_kind='input_request'").fetchone()['n']==1
            if name.startswith('0422'):
                # Reproduce the real regression before the forward repair.
                assert c.execute("SELECT count(*) AS n FROM rvbbit.work_graph WHERE item_kind='input_request'").fetchone()['n']==0
        row=c.execute("SELECT * FROM rvbbit.work_graph WHERE item_kind='input_request'").fetchone()
        assert row['column_state']=='awaiting_you' and row['parent_item_id']=='chat:'+str(sid)
        assert row['route']=='input_request:'+str(qid) and row['attention_rank']==110
        assert c.execute("SELECT summary FROM rvbbit.work_graph WHERE item_kind='chat'").fetchone()['summary']=='Whole-thread business brief'
    options=floor_data.request_options({'lane':'awaiting_you'})
    page=floor_data.read_page(db,projection(),OWNER,options)
    assert len(page['cards'])==1 and page['cards'][0]['item_id']=='input_request:'+str(qid)
    assert next(lane for lane in page['lanes'] if lane['column_state']=='awaiting_you')['pieces']==1
    assert floor_data.read_page(db,projection(),'someone-else@example.com',options)['matched_total']==0
    with db() as c:
        # Closing a request removes it from attention; migration replay must not
        # re-open it, change its stored answer, or create a continuation.
        c.execute("UPDATE rvbbit.calliope_input_requests SET status='answered',answers='{\"time\":\"8am\"}',answered_by=%s,answered_at=now(),closed_at=now() WHERE id=%s",(OWNER,qid))
        c.execute(REPAIR.read_text())
        assert c.execute("SELECT column_state FROM rvbbit.work_graph WHERE item_kind='input_request'").fetchone()['column_state']=='done'
        assert c.execute('SELECT answers FROM rvbbit.calliope_input_requests').fetchone()['answers']=={'time':'8am'}
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n']==0
    assert floor_data.read_page(db,projection(),OWNER,options)['cards']==[]


def test_repair_is_additive_preserves_dependents_and_is_idempotent_when_empty(db):
    with db() as c:
        for name,sql in graph_migrations():
            if name==REPAIR.name:
                break
            c.execute(sql)
        c.execute('GRANT SELECT ON rvbbit.work_graph TO PUBLIC')
        c.execute('CREATE VIEW rvbbit.graph_consumer AS SELECT item_id,column_state FROM rvbbit.work_graph')
        before=c.execute("SELECT oid,relacl FROM pg_class WHERE oid='rvbbit.work_graph'::regclass").fetchone()
        c.execute(REPAIR.read_text())
        first=c.execute("SELECT pg_get_viewdef('rvbbit.work_graph'::regclass) AS definition").fetchone()
        c.execute(REPAIR.read_text())
        assert c.execute("SELECT pg_get_viewdef('rvbbit.work_graph'::regclass) AS definition").fetchone()==first
        assert c.execute("SELECT oid,relacl FROM pg_class WHERE oid='rvbbit.work_graph'::regclass").fetchone()==before
        sid,tid,qid=seed(c)
        assert c.execute("SELECT count(*) AS n FROM rvbbit.graph_consumer WHERE column_state='awaiting_you'").fetchone()['n']==1
        c.execute("UPDATE rvbbit.calliope_input_requests SET status='cancelled',closed_at=now() WHERE id=%s",(qid,))
        assert c.execute("SELECT column_state FROM rvbbit.graph_consumer WHERE item_id=%s",('input_request:'+str(qid),)).fetchone()['column_state']=='done'
