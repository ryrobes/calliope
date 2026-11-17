"""Page refresh continuity, provenance and failure boundaries on disposable PostgreSQL."""
import asyncio
import ast
import json
from pathlib import Path
from uuid import uuid4

import pytest
import calliope
import calliope_pages as pages
import server
from test_generated_image_delivery import db

OWNER = 'owner@example.com'
ROOT = Path(__file__).parent


@pytest.fixture
def page_db(db, monkeypatch):
    with db() as c:
        c.execute("""
        ALTER TABLE rvbbit.calliope_sessions ADD COLUMN archived boolean DEFAULT false;
        ALTER TABLE rvbbit.calliope_turns ADD COLUMN client_turn_id uuid;
        CREATE TABLE rvbbit.brain_documents(doc_id bigint PRIMARY KEY,body text,content_hash text);
        CREATE TABLE rvbbit.test_visible(doc_id bigint,owner text);
        CREATE FUNCTION rvbbit.brain_visible_docs(text) RETURNS TABLE(doc_id bigint) LANGUAGE sql AS $$
          SELECT doc_id FROM rvbbit.test_visible WHERE owner=$1 $$;
        INSERT INTO rvbbit.brain_documents VALUES(1,'Launch is Friday.','one');
        INSERT INTO rvbbit.test_visible VALUES(1,'owner@example.com');
        CREATE TABLE rvbbit.work_ledger_credits(class text PRIMARY KEY,label text,minutes_each int,sort int);
        INSERT INTO rvbbit.work_ledger_credits VALUES('document','Documents',22,40);
        CREATE VIEW rvbbit.work_ledger_v AS SELECT 'question'::text AS class, 'unused'::text AS owner_email,
            now() AS occurred_at, 'unused'::text AS ref, 'unused'::text AS session_ref WHERE false;
        """)
        pages.ensure(c)
        pages.ensure(c)  # service restart and extension migration are additive/idempotent
    evidence = [{'doc_id':1,'chunk_id':1,'ordinal':1,'content_hash':'one','excerpt':'Launch is Friday.',
                 'title':'Project notes','source':'Tickets','doc_type':'ticket','entities':[]}]
    monkeypatch.setattr(pages, '_collect_evidence', lambda *_: evidence)
    return db, evidence


def prepare(page_db):
    db, evidence = page_db
    page = pages.create_page(db, OWNER, 'Project X', 'What is its current status?')
    created = []
    def create(owner, title):
        sid = str(uuid4())
        with db() as c:
            c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email,hermes_session_id) VALUES(%s,%s,%s)', (sid,owner,'hermes_'+sid))
        created.append(sid)
        return {'id':sid}
    return page, create, created


def launch(db, page, create):
    run = pages.begin_refresh(db, OWNER, page['id'], create)
    turn = str(uuid4())
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,client_turn_id) VALUES(%s,%s,'running',%s)", (turn,run['session_id'],run['client_turn_id']))
    return run, {'session_id':run['session_id'],'turn_id':turn}


def finish(db, run, dest, body='## Status\n\nLaunch is Friday. The release date is supported by the project notes [1].'):
    return pages.finish_refresh(db, OWNER, run['id'], dest, 'updated', body, 'Established launch status.')


def test_continuity_brief_and_source_change(page_db):
    db, evidence = page_db
    page, create, created = prepare(page_db)
    run, dest = launch(db, page, create)
    first = finish(db,run,dest)
    second = finish(db,run,dest)
    assert second['already_finished'] and first['revision_id']==second['revision_id']
    pages.update_brief(db,OWNER,page['id'],'Always emphasize dependencies; keep the status section.',1)
    evidence[0].update(content_hash='two',excerpt='Launch moved to Monday.')
    with db() as c:
        c.execute("UPDATE rvbbit.brain_documents SET content_hash='two',body='Launch moved to Monday.'")
        c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    run2,dest2=launch(db,page,create)
    packet=pages.read_refresh(db,OWNER,run2['id'],dest2)
    assert packet['standing_brief'].startswith('Always emphasize')
    assert 'Friday' in packet['previous_body'] and 'Monday' in packet['evidence'][0]['excerpt']
    result=finish(db,run2,dest2,'## Status\n\nLaunch moved to Monday. Technical dependencies still need confirmation [1].')
    saved=pages.get_page(db,OWNER,page['id'])
    assert len(created)==1 and str(saved['session_id'])==created[0]
    assert saved['revision']['version']==2 and len(saved['revisions'])==2
    assert saved['revision']['generation_receipt']['definition_version']==2
    assert result['revision_id']!=first['revision_id']
    with db() as c:
        assert c.execute("SELECT count(*) AS n FROM rvbbit.work_ledger_v WHERE class='page_revision'").fetchone()['n']==2
        assert c.execute("SELECT minutes_each FROM rvbbit.work_ledger_credits WHERE class='page_revision'").fetchone()['minutes_each']==22


@pytest.mark.parametrize('failure',['acl','source','brief','wrong_turn','wrong_owner','invalid_citation'])
def test_stale_or_unauthorized_save_preserves_revision(page_db,failure):
    db,evidence=page_db
    page,create,_=prepare(page_db)
    first,dest=launch(db,page,create);finish(db,first,dest)
    with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    run,dest=launch(db,page,create)
    owner=OWNER;body='## Status\n\nAn updated supported statement of sufficient length with a citation [1].'
    with db() as c:
        if failure=='acl':c.execute('DELETE FROM rvbbit.test_visible')
        if failure=='source':c.execute("UPDATE rvbbit.brain_documents SET content_hash='changed'")
    if failure=='brief':pages.update_brief(db,OWNER,page['id'],'Keep deadlines prominent',1)
    if failure=='wrong_turn':dest={**dest,'turn_id':str(uuid4())}
    if failure=='wrong_owner':owner='stranger@example.com'
    if failure=='invalid_citation':body=body.replace('[1]','[999]')
    with pytest.raises(pages.PageError):pages.finish_refresh(db,owner,run['id'],dest,'updated',body,'Updated status')
    with db() as c:assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_page_revisions').fetchone()['n']==1


@pytest.mark.parametrize('outcome',['unchanged','incomplete','missing_save','stopped'])
def test_refresh_outcomes_and_ledger(page_db,outcome):
    db,_=page_db
    page,create,_=prepare(page_db)
    run,dest=launch(db,page,create);first=finish(db,run,dest)
    with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    run,dest=launch(db,page,create)
    if outcome in {'unchanged','incomplete'}:
        pages.finish_refresh(db,OWNER,run['id'],dest,outcome,summary='No material change.' if outcome=='unchanged' else 'Missing current meeting notes.')
    else:
        with db() as c:c.execute('UPDATE rvbbit.calliope_turns SET status=%s WHERE id=%s', ('interrupted' if outcome=='stopped' else 'complete',dest['turn_id']))
    saved=pages.get_page(db,OWNER,page['id'])
    assert str(saved['revision']['id'])==first['revision_id']
    assert saved['last_run']['status']==('unchanged' if outcome=='unchanged' else 'failed')
    with db() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.work_ledger_v').fetchone()['n']==(2 if outcome=='unchanged' else 1)


def test_duplicate_refresh_and_brief_conflict(page_db):
    db,_=page_db;page,create,created=prepare(page_db)
    launch(db,page,create)
    with pytest.raises(pages.PageBusy):pages.begin_refresh(db,OWNER,page['id'],create)
    assert len(created)==1
    pages.update_brief(db,OWNER,page['id'],'First edit',1)
    with pytest.raises(pages.PageBusy):pages.update_brief(db,OWNER,page['id'],'Stale edit',1)


def test_endpoint_starts_normal_owner_turn_and_preserves_cookie(page_db):
    db,_=page_db;page,create,_=prepare(page_db)
    from starlette.responses import Response
    tree=ast.parse((ROOT/'calliope.py').read_text())
    names={'_PageTurnRequest','start_page_refresh','refresh_calliope_page'}
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.ClassDef,ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:node.decorator_list=[]
    seen=[]
    async def run_turn(req):
        assert req.owner==OWNER and req.cookies=={'session':'original'}
        payload=await req.json();seen.append(payload)
        assert payload['message'].startswith('[Page refresh]')
        with db() as c:c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,client_turn_id) VALUES(%s,%s,'running',%s)",
                                (str(uuid4()),req.path_params['session_id'],payload['client_turn_id']))
        return Response(status_code=200)
    ns={'asyncio':asyncio,'calliope_pages':pages,'conn_factory':db,'config':None,
        '_create_session_record_sync':lambda _,__,owner,title:create(owner,title),'run_turn':run_turn,
        'api_owner':lambda req:(req.owner,None),'json_response':lambda value,status=200:Response(json.dumps(value,default=str),status_code=status),
        'page_error_response':lambda exc:Response(str(exc),status_code=exc.status)}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<page-routes>','exec'),ns)
    class Request:
        owner=OWNER;cookies={'session':'original'};path_params={'page_id':str(page['id'])}
    response=asyncio.run(ns['refresh_calliope_page'](Request()))
    assert response.status_code==202 and len(seen)==1
    assert json.loads(response.body)['page']['last_run']['status']=='running'


def test_tool_schema_and_private_notebook(page_db,monkeypatch):
    from mcp.server.fastmcp import FastMCP
    import calliope_access
    db,_=page_db;page,create,_=prepare(page_db)
    run,dest=launch(db,page,create)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'_caller',lambda:(OWNER,None))
    monkeypatch.setattr(server,'_image_stage_destination',lambda owner:dest)
    assert server._page_refresh_tool(run['id'])['run_id']==run['id']
    monkeypatch.setattr(server,'_image_stage_destination',lambda owner:None)
    assert server._page_refresh_tool(run['id'])['error']['code']=='PAGE_CONTEXT_REQUIRED'
    registry=FastMCP('pages');server._register(registry)
    tools=registry._tool_manager._tools
    assert tools['finish_page_refresh'].parameters['required']==['run_id','outcome','summary']
    with db() as c:
        c.execute("ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text,ADD COLUMN access_revision int,ADD COLUMN created_at timestamptz,ADD COLUMN updated_at timestamptz")
        monkeypatch.setattr(calliope_access,'_session_kind',lambda *_:'chat')
        with pytest.raises(calliope_access.CalliopeAccessError) as error:
            calliope_access._owner_session(c,dest['session_id'],OWNER,require_shareable=True)
        assert error.value.code=='SESSION_NOT_SHAREABLE'


from test_zen_chat_layout import zen_page



def test_revoked_evidence_hides_body_and_material_change_summaries(page_db):
    db,_=page_db;page,create,_=prepare(page_db)
    run,dest=launch(db,page,create);finish(db,run,dest)
    assert pages.list_pages(db,OWNER)[0]['change_summary']
    with db() as c:c.execute('DELETE FROM rvbbit.test_visible')
    saved=pages.get_page(db,OWNER,page['id'])
    assert saved['acl_stale'] and saved['revision']['body'] is None
    assert saved['revision']['change_summary'] is None
    assert saved['last_run']['outcome_summary'] is None
    assert saved['revisions'][0]['change_summary'] is None
    assert pages.list_pages(db,OWNER)[0]['change_summary'] is None


def test_notebook_page_projection_context_and_privacy(page_db):
    db, _ = page_db
    page, create, _ = prepare(page_db)
    run, dest = launch(db, page, create)
    finish(db, run, dest)
    sid = dest['session_id']
    projected = pages.page_for_session(db, OWNER, sid)
    assert str(projected['id']) == str(page['id'])
    assert 'Friday' in pages.notebook_context(db, OWNER, sid)
    assert pages.page_for_session(db, 'stranger@example.com', sid) is None
    assert pages.notebook_context(db, 'stranger@example.com', sid) == ''
    assert pages.page_for_session(db, OWNER, str(uuid4())) is None
    with db() as c:
        c.execute('DELETE FROM rvbbit.test_visible')
    projected = pages.page_for_session(db, OWNER, sid)
    assert projected['acl_stale'] and projected['revision']['body'] is None
    assert 'Friday' not in pages.notebook_context(db, OWNER, sid)
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_pages SET status='archived' WHERE id=%s", (page['id'],))
    assert pages.page_for_session(db, OWNER, sid) is None


def test_create_with_standing_brief(page_db):
    db, _ = page_db
    page = pages.create_page(db, OWNER, 'Launch', 'What is the launch status?',
                             standing_brief='Separate confirmed dates from estimates.')
    assert page['standing_brief'] == 'Separate confirmed dates from estimates.'


def test_autocommit_parallel_refreshes_create_only_one_thread(page_db):
    from concurrent.futures import ThreadPoolExecutor
    db,_=page_db;page,create,created=prepare(page_db)
    def autocommit_db():
        c=db();c.autocommit=True;return c
    def start():
        try:return pages.begin_refresh(autocommit_db,OWNER,page['id'],create)
        except pages.PageBusy:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:start(),range(2)))
    assert sum(r is not None for r in results)==1 and len(created)==1


def test_completion_without_save_and_abandoned_launch_are_recoverable(page_db):
    db,_=page_db;page,create,_=prepare(page_db)
    run,dest=launch(db,page,create)
    with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    pages.finish_unsaved_turn(db,dest['turn_id'])
    with db() as c:assert c.execute('SELECT status FROM rvbbit.calliope_page_runs WHERE id=%s',(run['id'],)).fetchone()['status']=='failed'
    run=pages.begin_refresh(db,OWNER,page['id'],create)
    with db() as c:c.execute("UPDATE rvbbit.calliope_page_runs SET started_at=now()-interval '11 minutes' WHERE id=%s",(run['id'],))
    pages.begin_refresh(db,OWNER,page['id'],create)
    with db() as c:assert c.execute('SELECT status FROM rvbbit.calliope_page_runs WHERE id=%s',(run['id'],)).fetchone()['status']=='failed'
