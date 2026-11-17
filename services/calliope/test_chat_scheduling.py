"""A chat request creates one live assignment; no separate UI approval step."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import calliope
import server
from test_correspondence import database, OWNER


@pytest.fixture
def env(database, monkeypatch):
    with database() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS rvbbit.calliope_workflows(id uuid PRIMARY KEY)')
        conn.execute(calliope._WORK_ORDER_DDL)
        conn.execute('TRUNCATE rvbbit.calliope_sessions,rvbbit.calliope_work_orders CASCADE')
        sid=str(uuid4())
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(sid,OWNER))
    state={'jobs':{},'calls':[],'fail_create':False}
    async def hermes(config, method, path, body=None):
        state['calls'].append((method,path,body))
        jobs=state['jobs']
        if method=='POST' and path=='/api/jobs':
            if state['fail_create']:raise RuntimeError('Hermes unavailable')
            assert body['paused'] is True
            job={'id':'test-job-'+str(len(jobs)+1),'enabled':False,'state':'paused',**body,'next_run_at':'2026-10-12T08:00:00-04:00'}
            jobs[job['id']]=job
        else:
            jid=path.split('/')[3]
            if jid not in jobs:raise RuntimeError('Hermes (404)')
            job=jobs[jid]
            if method=='DELETE':del jobs[jid];return {}
            if method=='PATCH':job.update(body)
            elif path.endswith('/resume'):
                # Native cron must not start an unbound/unauthorized occurrence.
                with database() as conn:
                    row=conn.execute('SELECT status FROM rvbbit.calliope_work_orders WHERE hermes_job_id=%s',(jid,)).fetchone()
                    assert row and row['status'] in {'active','paused'}
                job.update(enabled=True,state='scheduled')
            elif path.endswith('/pause'):job.update(enabled=False,state='paused')
        return {'job':dict(job)}
    monkeypatch.setattr(calliope,'_hermes_json',hermes)
    monkeypatch.setattr(server,'_conn',database)
    authorization=server.ApplicationAuthorizationContext(actor=OWNER,subject=OWNER,
        attributed_subject=OWNER,client_id='test-user',mode='direct_oauth',assurance='oauth_access_token')
    monkeypatch.setattr(server,'_application_authorization_context',lambda *args,**kwargs:authorization)
    receipts=[]
    monkeypatch.setattr(server,'_record',lambda *args:receipts.append(args))
    return database,sid,state,receipts


def schedule(env,**changes):
    args={'session_id':env[1],'title':'Weekly branch review','instruction':'Build a dashboard and PDF from the approved Playbook.',
          'schedule':'0 8 * * 1','request_key':'weekly-branch-review','timezone_name':'America/New_York'}
    args.update(changes)
    return asyncio.run(server._mcp_schedule_calliope_work_order(**args))


def test_chat_schedule_activates_and_retry_keeps_the_same_job(env):
    factory,sid,state,receipts=env
    result=schedule(env);assert 'error' not in result,result
    work=result['work_order'];assert work['status']=='active'
    assert work['source_session_id']==sid and work['notification_policy']=='attention'
    job=state['jobs'][work['schedule']['job_id']]
    assert job['schedule']=='CRON_TZ=America/New_York 0 8 * * 1'
    assert job['enabled'] and work['id'] in job['prompt'] and job['id'] in job['prompt']
    before=len(state['calls'])
    assert schedule(env)['work_order']['id']==work['id']
    assert len(state['calls'])==before and len(state['jobs'])==1
    assert all(r[3] is None for r in receipts)
    assert server._AUTHORIZATION_CONTEXT.get() is None
    card=calliope._project_tool_result('schedule_calliope_work_order',result,{},'schedule-call')[0]
    assert card['kind']=='work_order' and card['payload']['status']=='active'
    with factory() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_work_orders').fetchone()['n']==1


def test_clarification_completes_existing_draft_without_another_approval(env):
    factory,sid,state,_=env
    draft=calliope.draft_work_order(factory,sid,'Old draft','Old workaround','0 12,13 * * 1')
    result=schedule(env,work_order_id=draft['id'])
    assert 'error' not in result,result
    work=result['work_order'];assert work['id']==draft['id'] and work['status']=='active'
    assert work['schedule']['value']=='0 8 * * 1' and work['schedule']['timezone']=='America/New_York'
    assert 'Old workaround' not in work['instruction']
    assert len(state['jobs'])==1


def test_chat_lifecycle_controls_and_retry_do_not_revive_paused_work(env):
    work=schedule(env)['work_order'];wid=work['id']
    pause=asyncio.run(server._mcp_manage_calliope_work_order(wid,'pause'))
    assert pause['work_order']['status']=='paused'
    assert schedule(env)['work_order']['status']=='paused'
    resumed=asyncio.run(server._mcp_manage_calliope_work_order(wid,'resume'))
    assert resumed['work_order']['status']=='active'
    cancelled=asyncio.run(server._mcp_manage_calliope_work_order(wid,'cancel'))
    assert cancelled['work_order']['status']=='cancelled' and not env[2]['jobs']


def test_failed_activation_is_visible_and_retry_reuses_saved_assignment(env):
    factory,sid,state,_=env
    state['fail_create']=True
    result=schedule(env);assert 'error' in result
    with factory() as conn:
        row=conn.execute('SELECT id,status,schedule_error FROM rvbbit.calliope_work_orders').fetchone()
    assert row['status']=='error' and 'Hermes unavailable' in row['schedule_error']
    state['fail_create']=False
    result=schedule(env)
    assert result['work_order']['id']==str(row['id']) and result['work_order']['status']=='active'
    assert len(state['jobs'])==1


def test_assignment_owner_is_not_taken_from_untrusted_tool_arguments(env):
    factory,sid,state,_=env
    with factory() as conn:
        conn.execute('UPDATE rvbbit.calliope_sessions SET owner_email=%s WHERE id=%s',('other@example.com',sid))
    assert 'error' in schedule(env)
    assert not state['jobs']
    other=calliope.draft_work_order(factory,sid,'Other task','Read a report','every 1h')
    assert 'error' in asyncio.run(server._mcp_manage_calliope_work_order(other['id'],'activate'))
    assert not state['jobs']


def test_invalid_zone_does_not_create_a_draft_or_cron_job(env):
    assert 'error' in schedule(env,timezone_name='Imaginary/Place')
    with env[0]() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_work_orders').fetchone()['n']==0
    assert not env[2]['jobs']


def test_parallel_activation_does_not_create_two_jobs(env,monkeypatch):
    factory,sid,state,_=env
    draft=calliope.draft_work_order(factory,sid,'Parallel','Prepare a report','every 1h')
    original=calliope._hermes_json
    async def delayed(*args,**kwargs):
        await asyncio.sleep(0.01)
        return await original(*args,**kwargs)
    monkeypatch.setattr(calliope,'_hermes_json',delayed)
    async def run():
        return await asyncio.gather(*(server._mcp_manage_calliope_work_order(draft['id'],'activate') for _ in range(2)))
    results=asyncio.run(run())
    assert sum('error' not in r for r in results)==1
    assert len(state['jobs'])==1
