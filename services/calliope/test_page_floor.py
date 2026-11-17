"""Run the production Floor projection, including Page privacy and pre-limit filtering."""
import ast
from pathlib import Path
from uuid import uuid4
import calliope
import calliope_pages as pages
import floor_data
import dream_handoffs
from test_page_refresh import db, page_db, prepare, OWNER
from test_page_tools import active_turn


def projection():
    tree=ast.parse((Path(__file__).parent/'calliope.py').read_text())
    assignments={target.id:node for node in ast.walk(tree) if isinstance(node,ast.Assign)
                 for target in node.targets if isinstance(target,ast.Name)
                 and target.id in {'floor_visibility_sql','floor_card_columns'}}
    fn=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='floor_projection')
    ns={"floor_data": floor_data}
    exec(compile(ast.Module(body=[*assignments.values(),fn],type_ignores=[]),'<floor-projection>','exec'),ns)
    return ns['floor_projection'](False)


def dream_tables(c):
    c.execute("""CREATE TABLE rvbbit.calliope_dreams(id uuid PRIMARY KEY,owner_email text,
        scope_kind text DEFAULT 'personal',portfolio_state text DEFAULT 'promoted',
        status text DEFAULT 'proposed',title text,thesis text,updated_at timestamptz DEFAULT now(),
        portfolio_rank int DEFAULT 1,impact text DEFAULT 'high',confidence numeric DEFAULT .78,
        effort text DEFAULT 'small',novelty numeric DEFAULT .6,rank_score numeric DEFAULT .8);
        CREATE TABLE rvbbit.calliope_dream_events(event_id bigserial PRIMARY KEY,dream_id uuid,
        actor_email text,event_kind text,payload jsonb DEFAULT '{}',created_at timestamptz DEFAULT now());""")
    c.execute("ALTER TABLE rvbbit.calliope_turns ADD COLUMN IF NOT EXISTS ordinal int")
    c.execute(dream_handoffs.DDL)
    c.execute("""CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_email text,kind text,from_kind text,from_ref text,from_label text,to_session_id uuid,
        subject text,body text,payload jsonb,status text DEFAULT 'queued');""")


def test_pages_are_private_filterable_standing_then_active_then_archived(page_db):
    db,_=page_db;page,create,_=prepare(page_db)
    linked=pages.ensure_notebook(db,OWNER,page['id'],create)
    with db() as c:
        dream_tables(c)
        c.execute('''ALTER TABLE rvbbit.calliope_sessions ADD COLUMN channel text;
          ALTER TABLE rvbbit.calliope_turns ADD COLUMN turn_kind text DEFAULT 'chat';
          CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$SELECT true$$;
          CREATE TABLE rvbbit.calliope_playbooks(id uuid,approved_version int,capability_name text);
          CREATE TABLE rvbbit.capability_access_policies(capability_kind text,capability_name text,visibility text);
          CREATE TABLE rvbbit.hermes_cron_jobs(raw jsonb,profile_key text,job_id text);
          CREATE TABLE rvbbit.calliope_session_schedules(id uuid,enabled boolean,session_id uuid,hermes_job_id text,schedule text);
          CREATE TABLE rvbbit.calliope_work_items(id uuid,engaged_session_id uuid,state text,kind text,source text);
          CREATE TABLE rvbbit.floor_piece_archives(item_id text,restored_at timestamptz,archived_by text);
          CREATE TABLE rvbbit.work_graph(last_activity_at timestamptz DEFAULT now(),item_id text,item_kind text DEFAULT 'chat',session_id uuid,
            display_title text,column_state text DEFAULT 'done',detail_state text DEFAULT 'complete',owner_email text,
            agents text[] DEFAULT ARRAY['calliope'],summary text,state_reason text,active_now boolean DEFAULT false,
            default_hidden boolean DEFAULT false,idle_seconds bigint DEFAULT 0,child_thread_count int DEFAULT 0,
            active_child_count int DEFAULT 0,related_signal_count int DEFAULT 0,surface_count int DEFAULT 0,
            attention_rank int DEFAULT 0,route text,ref jsonb DEFAULT '{}');''')
        c.execute("INSERT INTO rvbbit.work_graph(item_id,session_id,display_title,owner_email) VALUES(%s,%s,'Old chat title',%s)",
                  ('chat:'+str(linked['session_id']),linked['session_id'],OWNER))
    sql=projection()
    result=floor_data.read_page(db,sql,OWNER,floor_data.request_options({'kind':'page'}))
    assert result['matched_total']==1 and result['facets']['kinds']==['page']
    card=calliope._floor_card_json(result['cards'][0])
    assert card['column_state']=='standing' and card['display_title']==page['title']
    assert card['summary']==page['question'] and 'session='+str(linked['session_id']) in card['open_url']
    assert floor_data.read_page(db,sql,'stranger@example.com',floor_data.request_options({}))['matched_total']==0
    with db() as c:c.execute("UPDATE rvbbit.work_graph SET column_state='in_motion',state_reason='Recently active'")
    assert floor_data.read_page(db,sql,OWNER,floor_data.request_options({}))['cards'][0]['column_state']=='standing'
    dest=active_turn(db,create)
    run=pages.begin_refresh(db,OWNER,page['id'],create,dest)
    result=floor_data.read_page(db,sql,OWNER,floor_data.request_options({'kind':'page'}))
    assert result['cards'][0]['column_state']=='in_motion' and result['cards'][0]['active_now']
    pages.finish_refresh(db,OWNER,run['id'],dest,'incomplete',summary='Not enough evidence.')
    pages.archive_page(db,OWNER,page['id'])
    result=floor_data.read_page(db,sql,OWNER,floor_data.request_options({'kind':'page','lane':'archived'}))
    assert result['cards'][0]['column_state']=='archived'
