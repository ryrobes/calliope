"""Job observability uses real catalogs and receipts without exposing execution text."""
import json
import uuid
import pytest
import calliope
import gallery_jobs as gallery
import workspace_jobs
from test_workspace_sql import db, OWNER, SESSION  # noqa:F401
from test_workspace_jobs import SQL


def test_sql_history_description_lineage_and_no_sql_or_results(db):
    saved = workspace_jobs.save(db, SESSION, 'rollup', SQL, 43200, description='Maintains the sales rollup.')['job']
    workspace_jobs.manage(db, SESSION,'rollup','run_now')
    assert workspace_jobs.tick(db)
    row = gallery.snapshot(db, OWNER)['jobs'][0]
    assert row['description']=='Maintains the sales rollup.'
    assert row['sources']==['sales.orders']
    assert 'calliope.job_rollup' in row['destinations']
    assert row['history'][0]['rows_affected']==2
    assert row['last_success']
    assert row['history'][0]['status']=='complete'
    assert 'SELECT' not in json.dumps(row)
    assert 'sql' not in row and 'receipt' not in row['history'][0]
    gallery.control(db,OWNER,row['ref'],'pause')
    paused = gallery.snapshot(db,OWNER,row['ref'])['jobs'][0]
    assert not paused['enabled'] and paused['can_resume']
    workspace_jobs.save(db,SESSION,'rollup',SQL,43200,description='New explanation.')
    updated = gallery.snapshot(db,OWNER,row['ref'])['jobs'][0]
    assert not updated['enabled'] and updated['revision']==saved['revision']
    assert updated['description']=='New explanation.'


def test_admin_can_pause_other_owner_audited_without_transferring_execution(db,monkeypatch):
    row=workspace_jobs.save(db,SESSION,'job','SELECT 42',43200)['job']
    other='second-admin@example.com'
    monkeypatch.setattr(calliope.calliope_dreams,'is_company_admin',lambda f,o:o in {OWNER,other})
    gallery.control(db,other,'sql:'+row['id'],'pause')
    gallery.control(db,other,'sql:'+row['id'],'pause')
    gallery.control(db,other,'sql:'+row['id'],'resume')
    with db() as conn:
        audit=conn.execute('SELECT * FROM rvbbit.calliope_job_controls ORDER BY id').fetchall()
        job=conn.execute('SELECT * FROM rvbbit.calliope_workspace_jobs').fetchone()
    assert [a['operation'] for a in audit]==['pause','resume']
    assert all(a['actor']==other for a in audit)
    assert job['owner_email']==OWNER and str(job['session_id'])==SESSION
    assert job['next_run_at']>job['updated_at']
    for action in ['run_now','delete','schedule']:
        with pytest.raises(ValueError):gallery.control(db,other,'sql:'+row['id'],action)
    for fn in [lambda:gallery.snapshot(db,'nobody'),lambda:gallery.control(db,'nobody','sql:'+row['id'],'pause')]:
        with pytest.raises(PermissionError):fn()
    monkeypatch.setattr(calliope.calliope_dreams,'is_company_admin',lambda f,o:o==other)
    gallery.control(db,other,'sql:'+row['id'],'pause')
    with pytest.raises(ValueError,match='owner'):gallery.control(db,other,'sql:'+row['id'],'resume')


def test_mirror_and_manual_never_run_jobs_are_present(db):
    workspace_jobs.save(db,SESSION,'never-run','SELECT 42')
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.mirror_jobs(job_name text PRIMARY KEY,created_by text,connection_name text,source_schema text,destination_schema text,enabled boolean,schedule_seconds int,next_run_at timestamptz,updated_at timestamptz,updated_by text);
        CREATE TABLE rvbbit.mirror_runs(run_id uuid,job_name text,status text,requested_at timestamptz,started_at timestamptz,finished_at timestamptz,rows_loaded int,error_message text);
        CREATE TABLE rvbbit.mirror_tables(job_name text,source_table text,destination_table text,enabled boolean);
        INSERT INTO rvbbit.mirror_jobs VALUES('source-mirror','system','source','public','mirrored',true,3600,now(),now(),'system');
        INSERT INTO rvbbit.mirror_tables VALUES('source-mirror','orders','orders',true);''')
        c.execute("INSERT INTO rvbbit.mirror_runs VALUES(%s,'source-mirror','partial',now(),now()-interval '1 minute',now(),123,'secret SQL VALUES password')",(uuid.uuid4(),))
    items=gallery.snapshot(db,OWNER)['jobs']
    mirror=next(j for j in items if j['kind']=='mirror')
    assert mirror['status']=='failed' and mirror['history'][0]['rows_loaded']==123
    assert mirror['destinations']==['mirrored.orders']
    assert 'password' not in json.dumps(mirror)
    assert next(j for j in items if j['kind']=='sql')['status']=='manual'
    gallery.control(db,OWNER,mirror['ref'],'pause')
    gallery.control(db,OWNER,mirror['ref'],'resume')
    with db() as c:
        row=c.execute('SELECT * FROM rvbbit.mirror_jobs').fetchone()
    assert row['enabled'] and row['updated_by']==OWNER
    assert row['next_run_at']>row['updated_at']


def test_reference_and_description_validation(db):
    workspace_jobs.ensure(db)
    with pytest.raises(ValueError):gallery.snapshot(db,OWNER,'madeup:1')
    with pytest.raises(LookupError):gallery.snapshot(db,OWNER,'sql:'+str(uuid.uuid4()))
    with pytest.raises(ValueError):workspace_jobs.save(db,SESSION,'x','SELECT 1',description='x'*1001)


def test_ask_handoff_carries_exact_identity_and_question_without_running_agent(db):
    import ast, asyncio, inspect
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlencode, urlsplit, parse_qs
    saved=workspace_jobs.save(db,SESSION,'named-job','SELECT 42')['job']
    source=ast.parse(Path(calliope.__file__).read_text())
    node=next(n for n in ast.walk(source) if isinstance(n,ast.AsyncFunctionDef) and n.name=='gallery_job_ask')
    node.decorator_list=[]
    captured=[];new_id=str(uuid.uuid4())
    async def create(*args):return {'id':new_id}
    def surfaces(factory,sid,tid,payload):
        captured.extend(payload);return [{'id':'pinned-job'}]
    with db() as c:
        c.execute('CREATE TABLE rvbbit.calliope_turns(id uuid,session_id uuid,ordinal int,user_message text,assistant_message text,status text,completed_at timestamptz,turn_kind text)')
    scope=dict(calliope.__dict__,conn_factory=db,config=None,api_owner=lambda request:(OWNER,None),
               json_response=lambda data,status=200:dict(status=status,data=data),_create_session_record=create,
               _insert_surfaces=surfaces,urlencode=urlencode)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'handoff','exec'),scope)
    async def body():return {'ref':'sql:'+saved['id'],'question':'Why is this slow?'}
    result=asyncio.run(scope['gallery_job_ask'](SimpleNamespace(json=body)))
    assert result['status']==201
    query=parse_qs(urlsplit(result['data']['url']).query)
    assert query['session']==[new_id] and query['surface']==['pinned-job']
    assert 'Why is this slow?' in query['prompt'][0] and saved['id'] in query['prompt'][0]
    assert captured[0]['payload']['items'][0]['ref']=='sql:'+saved['id']
    assert captured[0]['payload']['summary']['state_label']=='manual'
    assert captured[0]['payload']['items'][0]['facts'][1]['value']=='No successful runs yet'
    assert 'SELECT 42' not in json.dumps(captured)
    assert workspace_jobs.status(db,SESSION)['runs']==[]


def test_manual_job_queued_state_is_independent_of_schedule(db):
    j=workspace_jobs.save(db,SESSION,'manual','SELECT 42')['job']
    workspace_jobs.manage(db,SESSION,'manual','run_now')
    state=gallery.snapshot(db,OWNER,'sql:'+j['id'])['jobs'][0]
    assert state['status']=='queued' and not state['enabled']
    gallery.control(db,OWNER,state['ref'],'pause')
    assert gallery.snapshot(db,OWNER,state['ref'])['jobs'][0]['status']=='manual'


from test_zen_chat_layout import zen_page  # noqa: F401,E402


def test_stage_inventory_preserves_job_state_instead_of_claiming_healthy(zen_page):
    zen_page.evaluate('''() => {
      qa.els.stage.innerHTML=qa.surfaceCard({id:'job',kind:'inventory',title:'Job',payload:{
        summary:{selected:1,state_label:'manual',sections:['Data jobs']},
        items:[{ref:'sql:one',kind:'workspace_job',label:'Manual job',state:'ready',summary:'A saved task.'}]}});
    }''')
    assert zen_page.locator('.inventory-surface-summary b').inner_text().lower()=='manual'
    assert 'Observed healthy' not in zen_page.locator('.inventory-surface').inner_text()
