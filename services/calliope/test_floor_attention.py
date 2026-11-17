"""Attention retires from source state; genuine decisions and history survive."""
from uuid import uuid4
import json
import floor_data
import pytest
from test_generated_image_delivery import db
from test_page_floor import projection, dream_tables

OWNER='owner@example.com'


@pytest.fixture
def attention_db(db):
    with db() as c:
        c.execute('''
        ALTER TABLE rvbbit.calliope_turns ADD COLUMN ordinal int, ADD COLUMN error text,
            ADD COLUMN created_at timestamptz DEFAULT now(), ADD COLUMN completed_at timestamptz;
        CREATE TABLE rvbbit.calliope_work_items(id uuid PRIMARY KEY,owner_email text,session_id uuid,
            source text,source_ref text,state text DEFAULT 'unread',kind text DEFAULT 'blocked',
            expires_at timestamptz,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),
            resolved_at timestamptz,engaged_session_id uuid,engaged_at timestamptz,context jsonb DEFAULT '{}');
        CREATE TABLE rvbbit.calliope_workflows(id uuid PRIMARY KEY,archived boolean DEFAULT false,
            schedule_enabled boolean DEFAULT true,scheduled_version int DEFAULT 1);
        CREATE TABLE rvbbit.calliope_workflow_runs(id uuid PRIMARY KEY,workflow_id uuid,owner_email text,
            session_id uuid,workflow_version int DEFAULT 1,trigger_kind text DEFAULT 'scheduled',
            status text DEFAULT 'blocked',started_at timestamptz DEFAULT now());
        CREATE TABLE rvbbit.calliope_work_orders(id uuid PRIMARY KEY,status text DEFAULT 'active',definition_version int DEFAULT 1);
        CREATE TABLE rvbbit.calliope_work_order_runs(id uuid PRIMARY KEY,work_order_id uuid,session_id uuid,
            definition_version int DEFAULT 1,status text DEFAULT 'blocked',started_at timestamptz DEFAULT now());
        ''')
    return db


def item(c,source='hermes',ref=None,session=None,owner=OWNER):
    id=uuid4()
    c.execute('INSERT INTO rvbbit.calliope_work_items(id,owner_email,session_id,source,source_ref) VALUES(%s,%s,%s,%s,%s)',
              (id,owner,session,source,str(ref) if ref else None))
    return id


def test_retire_only_obsolete_attention_keep_latest_failure_and_manual_decisions(attention_db):
    db=attention_db;retire=set();keep=set()
    with db() as c:
        keep.add(item(c))  # Explicit unlinked human blocker never expires by guesswork.
        expired=item(c);retire.add(expired)
        c.execute("UPDATE rvbbit.calliope_work_items SET expires_at=now()-interval '1 day' WHERE id=%s",(expired,))
        for enabled in (True,False):
            workflow=uuid4();c.execute('INSERT INTO rvbbit.calliope_workflows(id,schedule_enabled) VALUES(%s,%s)',(workflow,enabled))
            for i in range(20):
                run=uuid4();c.execute("INSERT INTO rvbbit.calliope_workflow_runs(id,workflow_id,owner_email,started_at) VALUES(%s,%s,%s,now()+%s*interval '1 minute')",(run,workflow,OWNER,i))
                id=item(c,'calliope_workflow',run)
                (keep if enabled and i==19 else retire).add(id)
            # Manual runs remain separately actionable even when a schedule is paused.
            run=uuid4();c.execute("INSERT INTO rvbbit.calliope_workflow_runs(id,workflow_id,owner_email,trigger_kind) VALUES(%s,%s,%s,'manual')",(run,workflow,OWNER))
            keep.add(item(c,'calliope_workflow',run))
        order=uuid4();run=uuid4()
        c.execute("INSERT INTO rvbbit.calliope_work_orders(id,status) VALUES(%s,'paused')",(order,))
        c.execute('INSERT INTO rvbbit.calliope_work_order_runs(id,work_order_id) VALUES(%s,%s)',(run,order))
        retire.add(item(c,'calliope_work_order',run))
        # An expired item owned by somebody else is not changed.
        foreign=item(c,owner='other@example.com');keep.add(foreign)
        c.execute("UPDATE rvbbit.calliope_work_items SET expires_at=now()-interval '1 day' WHERE id=%s",(foreign,))
        changed=floor_data.reconcile_attention(c,OWNER)
        assert {r['id'] for r in changed}==retire
        assert not floor_data.reconcile_attention(c,OWNER)
        assert {r['id'] for r in c.execute("SELECT id FROM rvbbit.calliope_work_items WHERE state='unread'").fetchall()}==keep
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_workflow_runs').fetchone()['n']==42
        assert c.execute("SELECT count(*) AS n FROM rvbbit.calliope_work_items WHERE context ? 'attention_retired'").fetchone()['n']==len(retire)


def test_successful_resume_retires_reminder_without_resolving_a_real_blocker(attention_db):
    with attention_db() as c:
        session=uuid4();failed=uuid4();later=uuid4()
        c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(session,OWNER))
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,ordinal) VALUES(%s,%s,'failed',1)",(failed,session))
        reminder=item(c,'calliope_turn',failed,session);blocker=item(c,session=session)
        assert not floor_data.reconcile_attention(c,OWNER)
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,ordinal) VALUES(%s,%s,'complete',2)",(later,session))
        assert [r['id'] for r in floor_data.reconcile_attention(c,OWNER)]==[reminder]
        assert c.execute('SELECT state FROM rvbbit.calliope_work_items WHERE id=%s',(blocker,)).fetchone()['state']=='unread'


def test_floor_classifies_real_decisions_separately_from_notices(attention_db):
    db=attention_db
    with db() as c:
        dream_tables(c)
        c.execute('''
        ALTER TABLE rvbbit.calliope_sessions ADD COLUMN channel text;
        ALTER TABLE rvbbit.calliope_turns ADD COLUMN turn_kind text;
        CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$ SELECT false $$;
        CREATE TABLE rvbbit.calliope_playbooks(id uuid,approved_version int,capability_name text);
        CREATE TABLE rvbbit.capability_access_policies(capability_kind text,capability_name text,visibility text);
        CREATE TABLE rvbbit.hermes_cron_jobs(raw jsonb,profile_key text,job_id text);
        CREATE TABLE rvbbit.calliope_session_schedules(id uuid,enabled boolean,session_id uuid,hermes_job_id text,schedule text);
        CREATE TABLE rvbbit.calliope_pages(id uuid,session_id uuid,owner_email text,title text,question text,updated_at timestamptz,status text);
        CREATE TABLE rvbbit.calliope_page_runs(page_id uuid,status text);
        CREATE TABLE rvbbit.floor_piece_archives(item_id text,restored_at timestamptz,archived_by text);
        CREATE TABLE rvbbit.work_graph(last_activity_at timestamptz DEFAULT now(),item_id text,item_kind text DEFAULT 'work_item',session_id uuid,
            display_title text,column_state text DEFAULT 'awaiting_you',detail_state text DEFAULT 'unread',owner_email text,
            agents text[] DEFAULT ARRAY['calliope'],summary text,state_reason text,active_now boolean DEFAULT false,
            default_hidden boolean DEFAULT false,idle_seconds bigint DEFAULT 0,child_thread_count int DEFAULT 0,
            active_child_count int DEFAULT 0,related_signal_count int DEFAULT 0,surface_count int DEFAULT 0,
            attention_rank int DEFAULT 0,route text,ref jsonb DEFAULT '{}');
        ''')
        expected={}
        for source,kind,lane in [('hermes','result','noticed'),('hermes','suggestion','noticed'),
                ('hermes','blocked','awaiting_you'),('calliope_turn','blocked','noticed'),
                ('calliope_playbook','suggestion','awaiting_you'),('hermes','goal','standing')]:
            id=item(c,source);expected['work_item:'+str(id)]=lane
            c.execute('UPDATE rvbbit.calliope_work_items SET kind=%s WHERE id=%s',(kind,id))
            c.execute("INSERT INTO rvbbit.work_graph(item_id,owner_email,ref) VALUES(%s,%s,%s)",
                      ('work_item:'+str(id),OWNER,json.dumps({'urgency':'high'})))
        c.execute("INSERT INTO rvbbit.work_graph(item_id,item_kind,owner_email,column_state) VALUES('dream:raw','dream',NULL,'noticed'),('decision:real','decision',%s,'awaiting_you')",(OWNER,))
        expected['decision:real']='awaiting_you'
    data=floor_data.read_page(db,projection(),OWNER,floor_data.request_options({}))
    assert {r['item_id']:r['column_state'] for r in data['cards']}==expected
    assert data['total']==7 and sum(l['pieces'] for l in data['lanes'])==7


def test_old_interactive_receipts_retire_without_touching_blockers_or_managed_delivery(attention_db):
    with attention_db() as c:
        retire=[]
        for source,kind in [('hermes','result'),('hermes','suggestion'),('hermes','scheduled'),('hermes','goal'),('calliope_playbook','result')]:
            id=item(c,source);retire.append(id)
            c.execute('UPDATE rvbbit.calliope_work_items SET kind=%s WHERE id=%s',(kind,id))
        blocker=item(c);review=item(c,'calliope_playbook');managed=item(c,'calliope_work_order')
        c.execute("UPDATE rvbbit.calliope_work_items SET kind='suggestion' WHERE id=%s",(review,))
        c.execute("UPDATE rvbbit.calliope_work_items SET kind='result' WHERE id=%s",(managed,))
        assert {r['id'] for r in floor_data.reconcile_attention(c,OWNER)}==set(retire)
        assert {r['id'] for r in c.execute("SELECT id FROM rvbbit.calliope_work_items WHERE state='unread'").fetchall()}=={blocker,review,managed}


def test_curated_dreams_match_personal_portfolio_and_viewer_feedback(attention_db):
    # Create the same production projection fixture, then exercise real paging.
    test_floor_classifies_real_decisions_separately_from_notices(attention_db)
    expected=[]
    with attention_db() as c:
        for who,scope,portfolio,status,event in [
            (OWNER,'personal','promoted','proposed',None),
            (OWNER,'personal','promoted','exploring',None),
            (OWNER,'personal','backlog','proposed',None),
            (OWNER,'personal','promoted','adopted',None),
            (OWNER,'personal','promoted','proposed','dismissed'),
            (OWNER,'personal','promoted','proposed','sleeping'),
            ('other@example.com','personal','promoted','proposed',None),
            (None,'company','promoted','proposed',None)]:
            id=uuid4()
            c.execute('INSERT INTO rvbbit.calliope_dreams(id,owner_email,scope_kind,portfolio_state,status,title,thesis) VALUES(%s,%s,%s,%s,%s,%s,%s)',(id,who,scope,portfolio,status,'Find overlooked retention opportunities','An authored hypothesis'))
            if event:c.execute("INSERT INTO rvbbit.calliope_dream_events(dream_id,actor_email,event_kind,payload) VALUES(%s,%s,%s,jsonb_build_object('wake_at',now()+interval '1 day'))",(id,who,event))
            if who==OWNER and status in ('proposed','exploring') and event is None:expected.append('curated_dream:'+str(id))
    data=floor_data.read_page(attention_db,projection(),OWNER,floor_data.request_options({'kind':'curated_dream','lane':'noticed','limit':'1'}))
    assert data['matched_total']==3 and len(data['cards'])==1
    assert data['cards'][0]['display_title']=='Find overlooked retention opportunities'
    assert data['cards'][0]['item_id'] in expected
    with attention_db() as c:
        c.execute("UPDATE rvbbit.calliope_dream_events SET payload=jsonb_build_object('wake_at',now()-interval '1 day') WHERE event_kind='sleeping'")
    data=floor_data.read_page(attention_db,projection(),OWNER,floor_data.request_options({'kind':'curated_dream'}))
    assert data['matched_total']==4
    assert any(r['dream']['portfolio_state']=='backlog' for r in data['cards'])
    assert all(r['dream']['impact']=='high' and float(r['dream']['confidence'])==.78 for r in data['cards'])
