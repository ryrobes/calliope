"""Native Page tools retain caller identity, evidence checks and one durable run."""
from uuid import uuid4
import pytest
import server
import calliope
import calliope_pages as pages
from test_page_refresh import db, page_db, prepare, finish, OWNER


def active_turn(db, create, owner=OWNER):
    session=create(owner,'Requesting conversation')
    dest={'session_id':session['id'],'turn_id':str(uuid4())}
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,client_turn_id) VALUES(%s,%s,'running',%s)",
                  (dest['turn_id'],dest['session_id'],str(uuid4())))
    return dest


def test_tool_creation_is_idempotent_and_keeps_dedicated_notebook(page_db, monkeypatch):
    db,_=page_db;_,create,created=prepare(page_db)
    dest=active_turn(db,create)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'_caller',lambda:(OWNER,None))
    monkeypatch.setattr(server,'_image_stage_destination',lambda owner:dest)
    monkeypatch.setattr(calliope,'_create_session_record_sync',lambda _,__,who,title:create(who,title))
    one=server._calliope_page_tool('create',title='Launch notes',question='What changed about launch?',standing_brief='Keep dates visible.')
    two=server._calliope_page_tool('create',title='Launch notes',question='What changed about launch?',standing_brief='Keep dates visible.')
    assert 'error' not in one and 'refresh_error' not in one
    assert one['page']['id']==two['page']['id']
    assert one['refresh_contract']['run_id']==two['refresh_contract']['run_id']
    assert str(one['page']['session_id'])!=dest['session_id'] and len(created)==2
    assert one['open_url'].startswith('/calliope?session='+created[-1])
    run={'id':one['refresh_contract']['run_id']}
    with pytest.raises(pages.PageNotFound):
        finish(db,run,{**dest,'turn_id':str(uuid4())})
    done=finish(db,run,dest)
    saved=pages.get_page(db,OWNER,one['page']['id'])
    assert saved['revision']['version']==1 and saved['revision']['generation_receipt']['turn_id']==dest['turn_id']
    assert pages.read_revision(db,OWNER,saved['id'],1)['evidence'][0]['doc_id']==1
    repeated=server._calliope_page_tool('refresh',page_id=saved['id'])
    assert repeated['refresh_contract']['status']=='complete'
    with db() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_page_runs WHERE page_id=%s',(saved['id'],)).fetchone()['n']==1
        assert c.execute("SELECT count(*) AS n FROM rvbbit.work_ledger_v WHERE class='page_revision'").fetchone()['n']==1


def test_tool_missing_evidence_leaves_findable_page(page_db,monkeypatch):
    db,evidence=page_db;_,create,_=prepare(page_db);dest=active_turn(db,create)
    monkeypatch.setattr(server,'_conn',db)
    monkeypatch.setattr(server,'_caller',lambda:(OWNER,None))
    monkeypatch.setattr(server,'_image_stage_destination',lambda _:dest)
    monkeypatch.setattr(calliope,'_create_session_record_sync',lambda _,__,who,title:create(who,title))
    evidence.clear()
    result=server._calliope_page_tool('create',title='Missing topic',question='What happened to the project?')
    assert result['page']['id'] and result['open_url'] and result['refresh_error']
    assert pages.get_page(db,OWNER,result['page']['id'])['last_run']['status']=='failed'
    assert server._calliope_page_tool('list',query='Missing')['pages'][0]['id']==result['page']['id']


def test_page_definition_and_archive_are_scoped_versioned_and_block_active_refresh(page_db):
    db,_=page_db;page,create,_=prepare(page_db);dest=active_turn(db,create)
    updated=pages.update_definition(db,OWNER,page['id'],1,title='Project X update',standing_brief='Emphasize owners.')
    assert updated['definition_version']==2 and updated['question']==page['question']
    with pytest.raises(pages.PageBusy):pages.update_definition(db,OWNER,page['id'],1,title='Stale change')
    with pytest.raises(pages.PageNotFound):pages.update_definition(db,'other@example.com',page['id'],2,title='Steal')
    with pytest.raises(pages.PageNotFound):pages.create_from_tool(db,'other@example.com',dest,'Steal','What is the status?')
    run=pages.begin_refresh(db,OWNER,page['id'],create,dest)
    with pytest.raises(pages.PageBusy):pages.update_definition(db,OWNER,page['id'],2,title='During refresh')
    with pytest.raises(pages.PageBusy):pages.archive_page(db,OWNER,page['id'],2)
    finish(db,run,dest)
    with db() as c:c.execute('DELETE FROM rvbbit.test_visible')
    revision=pages.read_revision(db,OWNER,page['id'],1)
    assert revision['acl_stale'] and revision['body'] is None and revision['evidence']==[]
    with pytest.raises(pages.PageNotFound):pages.read_revision(db,'other@example.com',page['id'],1)
    with pytest.raises(pages.PageBusy):pages.archive_page(db,OWNER,page['id'],1)
    pages.archive_page(db,OWNER,page['id'],2)
    assert pages.list_pages(db,OWNER)==[]
    with db() as c:
        assert c.execute('SELECT archived FROM rvbbit.calliope_sessions WHERE id=%s',(run['session_id'],)).fetchone()['archived']
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_page_revisions').fetchone()['n']==1


def test_current_turn_failure_reconciles_all_its_unfinished_pages(page_db):
    db,_=page_db;page,create,_=prepare(page_db);dest=active_turn(db,create)
    other=pages.create_from_tool(db,OWNER,dest,'Other question','What needs attention next?')
    runs=[pages.begin_refresh(db,OWNER,p['id'],create,dest) for p in [page,other]]
    with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='interrupted' WHERE id=%s",(dest['turn_id'],))
    pages.finish_unsaved_turn(db,dest['turn_id'])
    for p in [page,other]:assert pages.get_page(db,OWNER,p['id'])['last_run']['status']=='failed'
    next_dest=active_turn(db,create)
    retry=pages.begin_refresh(db,OWNER,page['id'],create,next_dest)
    assert retry['id']!=runs[0]['id']


def test_open_legacy_page_creates_one_notebook_without_generation(page_db):
    db,_=page_db;page,create,created=prepare(page_db)
    one=pages.ensure_notebook(db,OWNER,page['id'],create)
    two=pages.ensure_notebook(db,OWNER,page['id'],create)
    assert one['session_id']==two['session_id'] and len(created)==1
    assert one['last_run'] is None
    with db() as c:c.execute('UPDATE rvbbit.calliope_sessions SET archived=true WHERE id=%s',(one['session_id'],))
    pages.ensure_notebook(db,OWNER,page['id'],create)
    with db() as c:assert not c.execute('SELECT archived FROM rvbbit.calliope_sessions WHERE id=%s',(one['session_id'],)).fetchone()['archived']
    with pytest.raises(pages.PageNotFound):pages.ensure_notebook(db,'other@example.com',page['id'],create)


def test_page_tool_catalog_has_explicit_schemas_and_no_owner_override(page_db):
    from mcp.server.fastmcp import FastMCP
    registry=FastMCP('pages');server._register(registry)
    tools=registry._tool_manager._tools
    for name in ['create_calliope_page','list_calliope_pages','read_calliope_page','update_calliope_page','refresh_calliope_page','archive_calliope_page']:
        assert name in tools and 'owner' not in tools[name].parameters.get('properties',{})
    assert tools['update_calliope_page'].parameters['required']==['page_id','expected_version']
