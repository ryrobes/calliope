"""Final proposals execute separately from bounded read-only discovery."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import data_explorer as data
import calliope
import cost_accounting
import provider_settings
import server
from test_workspace_sql import db, OWNER, SESSION  # noqa:F401


@pytest.mark.parametrize('row', [{'amount': 42, 'region': None}, [None, 42]])
def test_context_samples_support_live_and_saved_row_formats(row):
    result = {'columns': [{'name': 'region'}, {'name': 'amount'}], 'rows': [row] * 10}
    assert data.sample_rows(result) == [{'region': None, 'amount': '42'}] * 5


@pytest.mark.parametrize('statement', [
    'DELETE FROM sales.orders',
    'SELECT 1; DELETE FROM sales.orders',
    'WITH x AS (DELETE FROM sales.orders RETURNING *) SELECT * FROM x',
    'SELECT * INTO sales.copy FROM sales.orders',
    'SELECT * FROM sales.orders FOR UPDATE',
    'SELECT * FROM sales.secret',
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT rvbbit.clover_llm_apply('a','b')",
    'SELECT public.sum(amount) FROM sales.orders',
    'WITH x AS (WITH secret AS (SELECT 1) SELECT * FROM secret) SELECT * FROM secret',
])
def test_reject_effects_extra_statements_and_unselected_sources(statement):
    with pytest.raises(ValueError):
        data.guard_sql(statement, ['sales.orders'])


def test_allow_aggregates_and_scoped_ctes():
    statement = 'WITH totals AS (SELECT sum(amount) AS total FROM sales.orders) SELECT * FROM totals;'
    assert data.guard_sql(statement, ['sales.orders']) == statement[:-1]
    assert data.guard_sql('SELECT date_trunc(\'month\',now()),count(*) FROM sales.orders', ['sales.orders'])


def test_clarification_never_contains_runnable_sql():
    p = data.proposal(json.dumps({'sql': 'DELETE FROM sales.orders', 'clarification': {
        'question': 'Orders or order lines?', 'options': ['Orders', 'Lines']}}), ['sales.orders'])
    assert p['sql'] == ''
    with pytest.raises(ValueError):
        data.proposal('not JSON', ['sales.orders'])


@pytest.fixture
def explorer(db, monkeypatch):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,payload jsonb,source jsonb,
          turn_id uuid,kind text,title text,created_at timestamptz DEFAULT now(),parent_surface_id uuid);
          ALTER TABLE rvbbit.calliope_sessions ADD COLUMN updated_at timestamptz;
          CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,ordinal int,user_message text,
          assistant_message text,status text,completed_at timestamptz,turn_kind text,author_email text);''')
    def view(conn,sid,owner):
        row=conn.execute('SELECT owner_email FROM rvbbit.calliope_sessions WHERE id=%s',(sid,)).fetchone()
        if not row or row['owner_email']!=owner:raise PermissionError('No access')
    monkeypatch.setattr(calliope.calliope_access,'require_view',view)
    data.ensure(db)
    operations, charges = [], []
    responses = [json.dumps({'title':'Order total', 'summary':'Sum all order amounts',
                            'sql':'SELECT sum(amount) AS total FROM sales.orders', 'assumptions':[]})]
    client = SimpleNamespace(complete_json=lambda **kwargs:(responses.pop(0),{'usage':{'prompt_tokens':10,'completion_tokens':20}}))
    def service(op, args, subject, owner):
        operations.append((op,args,subject,owner))
        if op=='context': return {'datasets':[{'relation':'sales.orders'}], 'observed_at':'test'}
        if op=='authorize': return {'allowed':True}
        if op=='memory': return {'matches':[{'text':'Use order date for sales reporting.'}]}
        if op=='validate':
            try:
                with db() as c: c.execute('EXPLAIN '+args['sql'])
            except Exception as exc:
                raise ValueError(str(exc)) from exc
            return {'valid':True,'safe_select':True}
        if op in ('run','lookup'):
            with db() as c:
                c.execute('BEGIN READ ONLY')
                cursor=c.execute(args['sql'])
                rows=cursor.fetchall()
                return {'columns':[{'name':x.name,'type':'numeric'} for x in cursor.description],
                        'rows':rows,'row_count':len(rows)}
    monkeypatch.setattr(cost_accounting,'record_completion',lambda *a,**kw:charges.append((a,kw)))
    return data.Explorer(db,service,lambda:client), operations,charges,responses


def build(explorer, **body):
    return explorer[0].build(OWNER,'reader',{'question':'Total order amount','sources':['sales.orders'],**body})


def test_build_run_history_ownership_and_immutable_results(explorer, db):
    exp,operations,charges,_=explorer
    draft=build(explorer)
    assert draft['status']=='ready' and draft['latest_run'] is None
    assert [x[0] for x in operations]==['context','validate']
    assert charges[0][0][2:4]==('Data exploration','data.generate')
    assert charges[0][1]['work_key']==str(draft['id'])
    with pytest.raises(LookupError):exp.get('other@example.com',draft['id'])
    with pytest.raises(LookupError):exp.run('other@example.com','other',draft['id'])
    assert exp.recent('other@example.com')==[]
    assert exp.recent(OWNER)[0]['run_summary'] is None
    run=exp.run(OWNER,'reader',draft['id'])
    assert run['result']['rows']==[{'total':'30'}]
    assert operations[-1][2:] == ('reader',OWNER)
    with db() as c:c.execute('UPDATE sales.orders SET amount=0')
    assert exp.get(OWNER,draft['id'])['latest_run']['result']['rows']==[{'total':'30'}]
    recent=exp.recent(OWNER)
    assert len(recent)==1
    assert recent[0]['run_summary']=={'status':'complete','row_count':1,'truncated':None}
    assert 'latest_run' not in recent[0]  # Listing tiles must not fetch saved grids.


def test_failed_generation_is_retained_and_never_executed(explorer):
    exp,operations,charges,responses=explorer
    responses[:]=[json.dumps({'sql':'SELECT bad FROM sales.orders'})]*2
    draft=build(explorer)
    assert draft['status']=='error' and len(charges)==2
    assert all(op[0]!='run' for op in operations)
    with pytest.raises(ValueError):exp.run(OWNER,'reader',draft['id'])


def test_clarification_and_previous_draft_access(explorer):
    exp,ops,charges,responses=explorer
    responses[:]=[json.dumps({'clarification':{'question':'Orders or lines?','options':['Orders','Lines']}})]
    draft=build(explorer)
    assert draft['status']=='clarification'
    assert [op[0] for op in ops]==['context']
    with pytest.raises(ValueError):exp.run(OWNER,'reader',draft['id'])
    with pytest.raises(LookupError):exp.build('other@example.com','other',{'question':'Revise','sources':['sales.orders'],'previous_id':str(draft['id'])})


def test_refinement_rewrites_a_complete_request_and_keeps_receipts(explorer):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    clarification={'question':'Which period?', 'options':['All dates','Last year']}
    responses[:]=[json.dumps({'clarification':clarification})]
    first=build(explorer,question='Total order amount, largest first')
    calls=[]
    def complete(**kwargs):
        calls.append(kwargs)
        return responses.pop(0),{'usage':{'prompt_tokens':10,'completion_tokens':20}}
    exp.client_factory=lambda:SimpleNamespace(complete_json=complete)
    rewritten='Total order amount across all dates, largest first'
    responses[:]=[json.dumps({'question':rewritten}),ready]
    refined=exp.refine(OWNER,'reader',first['id'],'The first one')
    assert refined['status']=='ready' and refined['question']==rewritten
    assert refined['previous_id']==first['id']
    assert exp.get(OWNER,first['id'])['question']=='Total order amount, largest first'
    rewrite_input=json.loads(calls[0]['user'])
    assert rewrite_input['clarification']==clarification and rewrite_input['answer']=='The first one'
    assert json.loads(calls[1]['user'])['question']==rewritten
    assert refined['context']['refinement']['answer']=='The first one'
    assert [r['operation'] for r in refined['receipts']]==['data.refine','data.generate']
    assert [c[0][3] for c in charges]==['data.generate','data.refine','data.generate']
    assert all(op[0]!='run' for op in ops)  # UI schedules execution after receiving a valid proposal.


def test_refinement_requires_ownership_and_a_pending_question(explorer):
    exp,ops,charges,responses=explorer
    draft=build(explorer)
    with pytest.raises(LookupError):exp.refine('other@example.com','other',draft['id'],'All dates')
    with pytest.raises(ValueError):exp.refine(OWNER,'reader',draft['id'],'All dates')
    assert len(charges)==1


def test_failed_rewrite_is_retained_but_never_generates_or_runs_sql(explorer):
    exp,ops,charges,responses=explorer
    responses[:]=[json.dumps({'clarification':{'question':'Which period?','options':['All dates']}})]
    first=build(explorer)
    responses[:]=['{"question": ""}']
    refined=exp.refine(OWNER,'reader',first['id'],'All dates')
    assert refined['status']=='error'
    assert refined['context']['refinement']['answer']=='All dates'
    assert [r['operation'] for r in refined['receipts']]==['data.refine']
    assert [c[0][3] for c in charges]==['data.generate','data.refine']
    assert all(op[0] not in ('validate','run') for op in ops)


@pytest.mark.parametrize('provider', [
    'custom', 'openrouter', 'openai-codex', 'nous', 'xai-oauth',
    'claude-subscription-directsdk-experimental', 'openai-api', 'gemini', 'xai',
])
def test_client_follows_live_calliope_selection_not_auxiliary_environment(monkeypatch, provider):
    monkeypatch.setenv('WAREHOUSE_HERMES_URL', 'http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY', 'internal-test-key')
    monkeypatch.setenv('WAREHOUSE_DATA_MODEL', 'cheap-model')
    monkeypatch.setenv('WAREHOUSE_DATA_BACKEND', 'clover_extract_llm')
    config = {'config_yaml': f'model:\n  provider: {provider}\n  default: stale-model\n'
              'platforms:\n  api_server:\n    extra:\n      model_name: selected-model\n'}
    monkeypatch.setattr(provider_settings, '_control', lambda *a, **kw: config)
    first = data.Explorer(None, None).client('first')
    assert first.model == 'selected-model' and first.selected_provider == provider
    assert first.endpoint_url == 'http://hermes:8642/v1/data/completion'
    config['config_yaml'] = 'model:\n  provider: openai-codex\n  default: newer-model\n'
    second = data.Explorer(None, None).client('second')
    assert second.model == 'newer-model' and first.model == 'selected-model'


@pytest.mark.parametrize('config', [
    {}, {'error':'unavailable'}, {'config_yaml':'model: {}'},
    {'config_yaml':'model:\n  provider: openai-codex'},
])
def test_model_resolution_never_falls_back_to_cheap_alias(monkeypatch, config):
    monkeypatch.setattr(provider_settings, '_control', lambda *a, **kw: config)
    with pytest.raises(ValueError): data.Explorer(None, None).client('draft')


def test_failed_model_answer_preserves_provider_usage_receipt(monkeypatch):
    from data_completion import CompletionError
    receipts, charges = [], []
    def fail(**kwargs):
        raise CompletionError('No usable answer', {'usage': {'completion_tokens': 20}, 'model_version': 'wire-model'})
    client = SimpleNamespace(complete_json=fail, model='selected-model', selected_provider='openai-codex')
    monkeypatch.setattr(cost_accounting, 'record_completion', lambda *a, **kw: charges.append((a, kw)))
    with pytest.raises(CompletionError):
        data.Explorer(None, None).complete(client, 'system', {}, 'draft', 'generate', 'Generate', receipts)
    assert receipts[0]['usage']['completion_tokens'] == 20
    assert receipts[0]['selected_provider'] == 'openai-codex'
    assert charges[0][1]['error'] is not None


def test_multiple_clarifications_and_followups_replay_the_private_branch(explorer):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    responses[:]=[json.dumps({'clarification':{'question':'Which period?', 'options':['2017','2016']}})]
    first=build(explorer,question='Total order amount by region, excluding returns, largest first')
    calls, selections=[],[]
    def complete(**kwargs):
        calls.append(json.loads(kwargs['user']))
        return responses.pop(0),{}
    def client():
        selections.append(True)
        return SimpleNamespace(complete_json=complete, model='calliope-grok', selected_provider='custom')
    exp.client_factory=client
    responses[:]=[json.dumps({'question':'Regional order totals for 2017 excluding returns, largest first'}),
                  json.dumps({'clarification':{'question':'Which date field?', 'options':['Order date','Ship date']}})]
    second=exp.refine(OWNER,'reader',first['id'],'2017')
    responses[:]=[json.dumps({'question':'Regional order totals for 2017 by order date excluding returns, largest first'}),ready]
    third=exp.refine(OWNER,'reader',second['id'],'Order date')
    assert len(selections)==2  # Rewrite and generation share one resolved model per revision.
    for payload in calls[2:4]:
        turns=payload['conversation']['turns']
        assert [t['draft_id'] for t in turns]==[str(first['id']),str(second['id'])]
        assert turns[0]['request']==first['question']
        assert turns[0]['assistant']['clarification']['question']=='Which period?'
        assert turns[1]['answer']=='2017'
    exp.run(OWNER,'reader',third['id'])
    responses[:]=[ready]
    fourth=build(explorer,question='Same thing, but for 2016',previous_id=str(third['id']))
    retrieval=json.loads([args['question'] for op,args,*_ in ops if op=='context'][-1])
    assert retrieval['original_request']==first['question']
    assert retrieval['previous_request']==third['question']
    assert retrieval['current_request']=='Same thing, but for 2016'
    turns=calls[-1]['conversation']['turns']
    assert turns[-1]['answer']=='Order date'
    assert turns[-1]['result']['status']=='complete' and turns[-1]['result']['row_count']==1
    assert 'rows' not in turns[-1]['result'] and 'sql' not in turns[-1]['assistant']
    assert calls[-1]['previous']['sql']  # Most recent query remains available in full.
    assert fourth['context']['conversation']['turns']==turns
    responses[:]=[ready]
    branch=build(explorer,question='Use 2015 instead',previous_id=str(second['id']))
    assert [t['draft_id'] for t in branch['context']['conversation']['turns']]==[str(first['id']),str(second['id'])]
    assert third['receipts'][0]['selected_model']=='calliope-grok'


def test_conversation_budget_keeps_original_and_recent_answers(explorer, monkeypatch):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    first=previous=build(explorer)
    for index in range(5):
        responses[:]=[ready]
        previous=build(explorer,question=f'Change {index}',previous_id=str(previous['id']))
    monkeypatch.setattr(data,'MAX_CONVERSATION_TURNS',3)
    history=exp.conversation(OWNER,previous)
    assert len(history['turns'])==3 and history['omitted_turns']==3
    assert history['turns'][0]['draft_id']==str(first['id'])
    assert [t['request'] for t in history['turns'][1:]]==['Change 3','Change 4']
    monkeypatch.setattr(data,'MAX_CONVERSATION_BYTES',1)
    history=exp.conversation(OWNER,previous)
    assert len(history['turns'])==2 and history['omitted_turns']==4


def test_history_rechecks_every_ancestor_owner_and_detects_cycles(explorer,db):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    first=build(explorer)
    responses[:]=[ready]
    second=build(explorer,previous_id=str(first['id']))
    with db() as c:c.execute('UPDATE rvbbit.calliope_data_drafts SET owner_email=%s WHERE id=%s',('other@example.com',first['id']))
    with pytest.raises(LookupError):exp.conversation(OWNER,second)
    with db() as c:
        c.execute('UPDATE rvbbit.calliope_data_drafts SET owner_email=%s,previous_id=%s WHERE id=%s',(OWNER,second['id'],first['id']))
    with pytest.raises(ValueError,match='revision history'):exp.conversation(OWNER,second)


def test_model_outage_keeps_answer_and_history_for_retry(explorer):
    exp,ops,charges,responses=explorer
    responses[:]=[json.dumps({'clarification':{'question':'Which period?','options':['2017']}})]
    first=build(explorer)
    def unavailable():raise ValueError('Selected model unavailable')
    exp.client_factory=unavailable
    failed=exp.refine(OWNER,'reader',first['id'],'2017')
    assert failed['status']=='error' and failed['context']['refinement']['answer']=='2017'
    assert failed['context']['conversation']['turns'][0]['draft_id']==str(first['id'])
    assert len(charges)==1


def test_discovery_reads_pivot_values_then_validates_value_named_columns(explorer,db):
    from psycopg import sql
    exp,ops,charges,responses=explorer
    with db() as c:
        c.execute('ALTER TABLE sales.orders ADD country text, ADD shape text')
        c.execute("UPDATE sales.orders SET country='us',shape=CASE WHEN id=1 THEN %s ELSE 'light' END",['O\'Brien "disk"'])
    calls=[]
    def complete(**kwargs):
        user=json.loads(kwargs['user']);calls.append(user)
        if len(calls)==1:
            answer={'lookup':{'tool':'query','sql':'SELECT shape,count(*) AS n FROM sales.orders GROUP BY shape ORDER BY n DESC,shape LIMIT 10'}}
        else:
            found=user['discovery'][0]['result']['rows']
            fields=sql.SQL(', ').join(sql.SQL('count(*) FILTER (WHERE shape={}) AS {}').format(
                sql.Literal(row['shape']),sql.Identifier(row['shape'])) for row in found)
            answer={'title':'Shapes by country','sql':sql.SQL('SELECT country,{} FROM sales.orders GROUP BY country').format(fields).as_string()}
        return json.dumps(answer),{}
    exp.client_factory=lambda:SimpleNamespace(complete_json=complete)
    draft=build(explorer,question='Pivot country by top shapes using the shape values as columns')
    assert draft['status']=='ready' and len(charges)==2
    assert [op[0] for op in ops]==['context','lookup','validate']
    trace=draft['context']['discovery'][0]
    assert trace['status']=='complete' and trace['result']['returned_rows']==2
    assert trace['id'].startswith('data:'+str(draft['id'])+':lookup:')
    assert trace['elapsed_ms']>=0
    assert calls[1]['lookups_remaining']==2
    run=exp.run(OWNER,'reader',draft['id'])
    assert run['result']['rows']==[{'country':'us','O\'Brien "disk"':1,'light':1}]


@pytest.mark.parametrize('lookup',[
    {'tool':'query','sql':'DELETE FROM sales.orders'},
    {'tool':'query','sql':'SELECT * FROM sales.secret'},
    {'tool':'query','sql':"SELECT pg_read_file('/etc/passwd')"},
    {'tool':'query','sql':'SELECT 1','limit':101},
    {'tool':'query','sql':'SELECT 1','limit':True},
    {'tool':'terminal','command':'anything'},
    {'tool':'memory','query':''},
])
def test_invalid_lookup_is_recorded_and_never_reaches_services(explorer,lookup):
    exp,ops,charges,responses=explorer
    ready=responses[0];responses[:]=[json.dumps({'lookup':lookup}),ready]
    draft=build(explorer)
    assert draft['status']=='ready'
    assert [op[0] for op in ops]==['context','validate']
    assert draft['context']['discovery'][0]['status']=='error'
    assert draft['context']['discovery'][0]['result']['error']


def test_lookup_budget_stops_without_a_query_or_agent_loop(explorer):
    exp,ops,charges,responses=explorer
    responses[:]=[json.dumps({'lookup':{'tool':'query','sql':'SELECT 1'}})]*10
    draft=build(explorer)
    assert draft['status']=='error' and 'three lookups' in draft['error']
    assert len(draft['context']['discovery'])==3
    assert [op[0] for op in ops].count('lookup')==3
    assert all(op[0] not in ('run','validate') for op in ops)
    assert len(charges)==4


def test_lookup_failures_and_sql_repair_share_a_bounded_receipted_flow(explorer):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    responses[:]=[json.dumps({'lookup':{'tool':'query','sql':'SELECT missing FROM sales.orders'}}),
                  json.dumps({'lookup':{'tool':'memory','query':'sales reporting date'}}),
                  json.dumps({'sql':'SELECT missing FROM sales.orders'}),ready]
    draft=build(explorer)
    assert draft['status']=='ready'
    traces=draft['context']['discovery']
    assert [t['status'] for t in traces]==['error','complete']
    assert traces[0]['error_type']=='UndefinedColumn'
    assert 'unavailable' in traces[0]['result']['error']
    assert 'order date' in traces[1]['result']['matches'][0]['text']
    assert draft['context']['validation_errors'][0]['error']
    assert len(charges)==4 and len(draft['receipts'])==4


def test_previous_result_preview_and_paging_preserve_exact_values(explorer,db):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    first=build(explorer);run=exp.run(OWNER,'reader',first['id'])
    large_value='long dimension ' * 100
    with db() as c:
        c.execute('UPDATE rvbbit.calliope_data_runs SET result=%s::jsonb WHERE id=%s',
                  (json.dumps({'columns':[{'name':'shape'}],'rows':[{'shape':large_value}]*12,'row_count':12}),run['id']))
    calls=[]
    def complete(**kwargs):
        calls.append(json.loads(kwargs['user']));return responses.pop(0),{}
    exp.client_factory=lambda:SimpleNamespace(complete_json=complete)
    responses[:]=[json.dumps({'lookup':{'tool':'result','draft_id':str(first['id']),'offset':10,'limit':2}}),ready]
    draft=build(explorer,previous_id=str(first['id']))
    preview=calls[0]['context']['latest_result']
    assert preview['rows'][0]['shape']==large_value
    assert preview['returned_rows']==10 and preview['next_offset']==10 and preview['truncated']
    trace=draft['context']['discovery'][0]
    assert trace['result']['rows']==[{'shape':large_value}]*2 and trace['result']['offset']==10
    assert trace['result']['next_offset'] is None
    assert [op[0] for op in ops].count('run')==1  # Saved result reads never execute SQL.
    assert ops[-2][0]=='authorize'


def test_saved_result_tool_rejects_siblings_other_owners_and_removed_sources(explorer,db):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    first=build(explorer);responses[:]=[ready];sibling=build(explorer)
    history=exp.conversation(OWNER,first)
    request={'tool':'result','draft_id':str(sibling['id'])}
    with pytest.raises(ValueError,match='history'):exp.lookup(OWNER,'reader',['sales.orders'],history,request,'d','l')
    request['draft_id']=str(first['id'])
    with pytest.raises(ValueError,match='selection'):exp.lookup(OWNER,'reader',['sales.other'],history,request,'d','l')
    with pytest.raises(LookupError):exp.lookup('other@example.com','other',['sales.orders'],history,request,'d','l')


def test_clarification_keeps_the_last_available_result_for_rewriting_and_generation(explorer):
    exp,ops,charges,responses=explorer
    ready=responses[0]
    first=build(explorer);run=exp.run(OWNER,'reader',first['id'])
    responses[:]=[json.dumps({'clarification':{'question':'Which period?', 'options':['All dates']}})]
    second=build(explorer,previous_id=str(first['id']))
    calls=[]
    def complete(**kwargs):
        calls.append(json.loads(kwargs['user']));return responses.pop(0),{}
    exp.client_factory=lambda:SimpleNamespace(complete_json=complete)
    responses[:]=[json.dumps({'question':'Total order amount across all dates'}),ready]
    third=exp.refine(OWNER,'reader',second['id'],'All dates')
    assert calls[0]['latest_result']['run_id']==str(run['id'])
    assert calls[1]['context']['latest_result']['rows']==[{'total':'30'}]
    assert third['context']['latest_result']['draft_id']==str(first['id'])


def test_evidence_byte_bound_never_clips_dimension_values(monkeypatch):
    result={'columns':[{'name':'shape'}],'rows':[{'shape':'x'*600},{'shape':'y'*600}],'row_count':2}
    monkeypatch.setattr(data,'MAX_EVIDENCE_BYTES',1500)
    packet=data.result_evidence(result)
    assert packet['rows']==[{'shape':'x'*600}] and packet['next_offset']==1
    assert packet['truncated']
    result['rows'][0]['shape']='x'*2000
    assert 'too wide' in data.result_evidence(result)['error']


def test_discovery_service_uses_current_subject_limits_memory_acl_and_activity(monkeypatch):
    seen=[]
    monkeypatch.setattr(data,'inventory',lambda factory,role,allowed:[{'relation':'sales.orders'}])
    monkeypatch.setattr(server,'_session_pg_role',lambda subject:'reader_role')
    monkeypatch.setattr(server,'_record',lambda *args,**kwargs:seen.append(('record',args,kwargs)))
    def run(statement,**kwargs):
        seen.append(('query',statement,kwargs,server._SESSION_SUB.get()))
        return {'rows':[{'n':2}],'row_count':1,'columns':[{'name':'n'}]}
    def memory(query,**kwargs):
        seen.append(('memory',query,kwargs));return {'matches':[]}
    monkeypatch.setattr(server,'tool_run_sql',run)
    monkeypatch.setattr(server,'tool_ask_brain',memory)
    args={'sources':['sales.orders'],'sql':'SELECT count(*) n FROM sales.orders','limit':10,'draft_id':'d','lookup_id':'l'}
    result=server._calliope_data_service('lookup',args,'reader',OWNER)
    assert result['row_count']==1
    assert seen[0][2]=={'limit':10,'timeout_ms':10000} and seen[0][3]=='reader'
    assert seen[1][1][0]=='data.lookup' and seen[1][2]['caller_override']==OWNER
    assert server._SESSION_SUB.get()!='reader'
    server._calliope_data_service('memory',{'sources':['sales.orders'],'query':'sales'},'reader',OWNER)
    assert seen[2][2]=={'k':3,'filters':{'type':['system_learning']},'caller_email':OWNER}
    assert seen[3][1][0]=='data.memory'
    with pytest.raises(PermissionError):server._calliope_data_service('lookup',dict(args,sources=['sales.secret']),'reader',OWNER)
    with pytest.raises(ValueError):server._calliope_data_service('lookup',dict(args,sql='DELETE FROM sales.orders'),'reader',OWNER)
    assert len(seen)==4


@pytest.mark.parametrize('origin_mode',[None,'same','other'])
def test_publication_is_idempotent_and_not_a_chat_turn(explorer,db,monkeypatch,origin_mode):
    exp,_,_,_=explorer
    seed = None
    target = SESSION
    if origin_mode:
        seed = saved_surface(db)
        imported = exp.from_surface(OWNER,'reader',seed)
        with pytest.raises(ValueError, match='Refresh or update'):
            # Existing snapshot cannot be misrepresented as a new execution.
            monkeypatch.setattr(calliope.calliope_access,'require_participant',calliope.calliope_access.require_view)
            calliope._publish_data_run(db,exp.service,OWNER,'reader',str(imported['latest_run']['id']),SESSION)
        revised=build(explorer,previous_id=str(imported['id']))
        assert revised['context']['origin']['surface_id']==seed
        run=exp.run(OWNER,'reader',revised['id'])
        if origin_mode=='other':
            target=str(uuid4())
            with db() as c:c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(target,OWNER))
    else:
        run=exp.run(OWNER,'reader',build(explorer)['id'])
        with db() as c:
            c.execute("UPDATE rvbbit.calliope_data_drafts SET context=context||%s::jsonb WHERE id=%s",
                      (data.encode({'meaning_area':{'key':'subject:sales','name':'Sales','published_at':'2026-09-28',
                                                    'gaps':['Internal context should not be copied to Stage']}}),run['draft_id']))
    def participant(conn,sid,owner):
        row=conn.execute('SELECT owner_email FROM rvbbit.calliope_sessions WHERE id=%s',(sid,)).fetchone()
        if not row or row['owner_email']!=owner:raise PermissionError('No access')
    monkeypatch.setattr(calliope.calliope_access,'require_participant',participant)
    def insert(factory,sid,tid,surfaces):
        surface=dict(surfaces[0],id=str(uuid4()),session_id=sid)
        with factory() as c:c.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,payload,source) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb)',
            (surface['id'],sid,surface['kind'],surface['title'],data.encode(surface['payload']),data.encode(surface['source'])))
        return [surface]
    monkeypatch.setattr(calliope,'_insert_surfaces',insert)
    monkeypatch.setattr(calliope,'_surface_json',lambda row:dict(row))
    first=calliope._publish_data_run(db,exp.service,OWNER,'reader',str(run['id']),target)
    second=calliope._publish_data_run(db,exp.service,OWNER,'reader',str(run['id']),target)
    if origin_mode:
        assert first['surface']['source']['derived_from']['surface_id']==seed
        assert first['surface']['parent_surface_id']==(seed if origin_mode=='same' else None)
    assert second['reused'] and str(second['surface']['id'])==first['surface']['id']
    with db() as c:
        turns=c.execute('SELECT * FROM rvbbit.calliope_turns').fetchall()
        assert len(turns)==1 and turns[0]['turn_kind']=='data_publish' and not turns[0]['assistant_message']
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_surfaces').fetchone()['n']==(2 if seed else 1)
    with pytest.raises(PermissionError):calliope._publish_data_run(db,exp.service,'other@example.com','other',str(run['id']),SESSION)
    if not origin_mode:
        expected={'key':'subject:sales','name':'Sales','published_at':'2026-09-28'}
        assert first['surface']['source']['meaning_area']==expected
        imported=exp.from_surface(OWNER,'reader',first['surface']['id'])
        assert imported['context']['meaning_area']==expected


@pytest.mark.parametrize('path',['drafts','resolve','surfaces/{surface_id}/explore','areas/handoff','sql/run','sql/explain'])
def test_routes_require_owner_and_same_origin_for_mutation(explorer,db,path):
    import asyncio
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            return lambda fn:routes.update({(path,methods[0]):fn})
    def response(value,status=200):return (status,value)
    data.register(MCP(),db,None,lambda r:(r.owner,None) if r.owner else (None,(401,{})),
        lambda r:(r.owner,{'sub':'reader'}),response,explorer[0].service,lambda *args:None,lambda *args:None)
    route=routes[('/api/calliope/data/'+path,'POST')]
    request=SimpleNamespace(owner=OWNER,method='POST',headers={'origin':'https://other.test','host':'app.test','x-calliope-data':'1'})
    assert asyncio.run(route(request))[0]==403
    request.owner=None
    assert asyncio.run(route(request))[0]==401


ENTRY_ITEMS=[{'relation':'sales.orders','title':'Orders','description':'Sales by region and date.'},
             {'relation':'sales.returns','title':'Returns','description':'Returned orders.'}]


def entry_decision(intent='query',source='dataset_0',score=.98):
    return {'answers':{'intent':{'choice':intent,'probabilities':{intent:score,'other':1-score}},
                       'source':{'choice':source,'probabilities':{source:score,'other':1-score}}}}


def test_entry_router_uses_bounded_choices_and_only_supplied_sources():
    calls=[]
    def decide(state,questions):
        calls.append((state,questions));return entry_decision()
    routed=data.route_entry('Total sales by region',ENTRY_ITEMS,decide)
    assert routed['action']=='query' and routed['sources']==[ENTRY_ITEMS[0]]
    assert routed['question']=='Total sales by region'
    state,questions=calls[0]
    assert state['datasets']==[{**item,'matched_fields':None} for item in ENTRY_ITEMS]
    assert set(questions)=={'intent','source'}
    assert 'choose_sources' in questions['source']['criteria']
    assert set(questions['intent']['criteria'])=={'query','search'}


@pytest.mark.parametrize('decision',[None,{},entry_decision(source='sales.secret'),
    entry_decision(intent='delete'),entry_decision(score=.5),entry_decision(score=float('nan')),
    entry_decision(source='choose_sources'),entry_decision(intent='search')])
def test_unavailable_ambiguous_or_invalid_decisions_never_authorize_query(decision):
    routed=data.route_entry('Total sales',ENTRY_ITEMS,lambda *a:decision)
    assert routed['action']=='search' and routed['sources']==[]


def test_entry_router_failure_leaves_discovery_available_and_records_reason():
    def unavailable(*args):raise TimeoutError('Private transport details')
    routed=data.route_entry('Sales',ENTRY_ITEMS,unavailable)
    assert routed['action']=='search' and routed['error_type']=='TimeoutError'
    assert 'Private' not in data.encode(routed)
    assert data.route_entry('unknown',[],unavailable)['action']=='search'


def test_entry_router_bounds_catalog_and_state_without_truncating_request():
    observed=[]
    items=[{'relation':f'sales.t{i}','title':str(i),'description':'x'*1800} for i in range(50)]
    data.route_entry('q'*4000,items,lambda state,questions:observed.append((state,questions)) or entry_decision())
    state,questions=observed[0]
    assert state['request']=='q'*4000 and len(data.encode(state).encode())<=28000
    assert len(state['datasets'])<=14 and len(questions['source']['criteria'])<=15
    assert all(len(item['description'])==1800 for item in state['datasets'])


def test_entry_route_is_correlated_with_draft_without_granting_access(explorer):
    route_id=str(uuid4())
    draft=build(explorer,entry_route_id=route_id)
    assert draft['context']['entry_route_id']==route_id
    assert explorer[1][0][0]=='context'


def test_entry_service_filters_sources_and_records_native_decision(monkeypatch):
    calls=[]
    monkeypatch.setattr(data,'inventory',lambda *a:ENTRY_ITEMS)
    monkeypatch.setattr(server,'_metric_catalog_snapshot',lambda *a,**kw:{'metrics':[]})
    monkeypatch.setattr(server,'_session_pg_role',lambda subject:'reader_role')
    monkeypatch.setattr(server,'tool_find',lambda *a,**kw:{'matches':[{'object':'sales.orders'},{'object':'secret.hidden'}]})
    monkeypatch.setattr(server,'_record',lambda *a,**kw:calls.append(('record',a,kw)))
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement,args=None):
            calls.append(('sql',statement,args,server._SESSION_SUB.get()));return self
        def fetchone(self):return {'decision':entry_decision()}
    monkeypatch.setattr(server,'_conn',lambda:Conn())
    value=server._calliope_data_service('resolve',{'q':'total sales by region'},'reader',OWNER)
    assert value['action']=='query' and value['sources']==[{**ENTRY_ITEMS[0],'matched_fields':[]}]
    assert calls[0][1]=="SET statement_timeout='20s'"
    assert calls[1][1]=='SELECT rvbbit.clover_decide(%s::jsonb,%s::jsonb) AS decision'
    assert 'secret.hidden' not in calls[1][2][0] and calls[1][3]=='reader'
    assert calls[2][1][0]=='data.route' and calls[2][2]['caller_override']==OWNER
    assert 'decision' not in value
    assert server._SESSION_SUB.get()!='reader'


def saved_surface(db, source=None, kind='query'):
    sid=str(uuid4())
    with db() as c:
        c.execute("""INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,source,payload)
          VALUES(%s,%s,%s,'Saved orders',%s::jsonb,%s::jsonb)""",(sid,SESSION,kind,
          data.encode(source if source is not None else {'sql':'WITH total AS (SELECT sum(amount) total FROM sales.orders) SELECT * FROM total',
            'question':'Original order total','query_id':str(uuid4()),'executed_at':'not a timestamp'}),
          data.encode({'columns':['total'],'rows':[[99]],'row_count':1,'truncated':True})))
    return sid


def test_import_is_private_snapshot_with_no_execution_or_private_history(explorer,db):
    exp,ops,charges,_=explorer
    sid=saved_surface(db)
    with db() as c:before=c.execute('SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s',(sid,)).fetchone()
    d=exp.from_surface(OWNER,'reader',sid)
    assert d['status']=='ready' and d['previous_id'] is None
    assert d['question']=='Original order total' and d['sources']==['sales.orders']
    assert d['context']=={'origin':d['context']['origin']}  # No private draft state imported.
    assert d['context']['origin']['surface_id']==sid
    assert d['latest_run']['result']['rows']==[[99]]
    assert d['latest_run']['result']['saved_snapshot']
    assert [x[0] for x in ops]==['authorize'] and charges==[]
    with pytest.raises(PermissionError):exp.from_surface('other@example.com','other',sid)
    with pytest.raises(LookupError):exp.get('other@example.com',d['id'])
    refreshed=exp.run(OWNER,'reader',d['id'])
    assert refreshed['result']['rows']==[{'total':'30'}] and not refreshed['result'].get('saved_snapshot')
    with db() as c:assert c.execute('SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s',(sid,)).fetchone()==before


@pytest.mark.parametrize('source',[{}, {'sql':'SELECT * FROM sales.orders','sql_truncated':True},
    {'sql':'DELETE FROM sales.orders'}, {'sql':'SELECT 1'}, {'sql':'SELECT * FROM sales.orders; DELETE FROM sales.orders'}])
def test_unsupported_import_still_shows_snapshot_but_cannot_execute(explorer,db,source):
    exp,ops,charges,_=explorer
    d=exp.from_surface(OWNER,'reader',saved_surface(db,source))
    assert d['context']['origin']['read_only_reason'] and d['status']=='error'
    assert d['latest_run']['result']['rows']==[[99]]
    with pytest.raises(ValueError):exp.run(OWNER,'reader',d['id'])
    with pytest.raises(ValueError):build(explorer,previous_id=str(d['id']))
    assert not ops and not charges


def test_import_rechecks_data_access_and_rejects_nonquery(explorer,db):
    exp,ops,charges,_=explorer
    def denied(*args):raise PermissionError('No dataset access')
    exp.service=denied
    d=exp.from_surface(OWNER,'reader',saved_surface(db))
    assert 'not currently available' in d['context']['origin']['read_only_reason']
    with pytest.raises(ValueError):exp.run(OWNER,'reader',d['id'])
    with pytest.raises(ValueError,match='saved query'):exp.from_surface(OWNER,'reader',saved_surface(db,kind='artifact'))
    assert not charges


def test_search_sections_keep_cubes_and_metrics_when_tables_fill_the_limit():
    calls=[]
    items=[{'relation':f'sales.t{i}','catalog_kind':'table'} for i in range(60)]
    items += [{'relation':'cubes.sales_rollup','catalog_kind':'cube'}]
    def catalog(owner,**kwargs):
        calls.append((owner,kwargs))
        return {'metrics':[{'name':'revenue','title':'Revenue'}],'categories':[{'category':'Sales'}]}
    result=data.search_sections(items,catalog,OWNER,'revenue')
    assert len(result['items'])==41 and result['items'][-1]['relation']=='cubes.sales_rollup'
    assert result['metrics'][0]['name']=='revenue' and result['metric_areas']==['Sales']
    assert calls==[(OWNER,{'search':'revenue','limit':24,'days':7})]
    def unavailable(*args,**kwargs):raise RuntimeError('private connection details')
    partial=data.search_sections(items,unavailable,OWNER,'revenue')
    assert partial['items']==result['items'] and partial['metrics']==[]
    assert partial['metrics_error']=='Metrics are temporarily unavailable.'


def test_named_metric_stays_in_discovery_without_generating_sql(monkeypatch):
    import data_areas
    monkeypatch.setattr(data_areas,'catalog',lambda *a:[])
    monkeypatch.setattr(data,'inventory',lambda *a:ENTRY_ITEMS)
    monkeypatch.setattr(server,'_session_pg_role',lambda subject:'reader_role')
    monkeypatch.setattr(server,'tool_find',lambda *a,**kw:{'matches':[]})
    monkeypatch.setattr(server,'_metric_catalog_snapshot',lambda *a,**kw:{'metrics':[{'name':'sales_total','title':'Total Sales'}]})
    monkeypatch.setattr(server,'_conn',lambda *a,**kw:pytest.fail('Discovery must not call Jev or execute SQL for a named metric'))
    records=[]
    monkeypatch.setattr(server,'_record',lambda *a,**kw:records.append(a))
    result=server._calliope_data_service('resolve',{'q':'total sales'},'reader',OWNER)
    assert result['action']=='search' and result['sources']==[]
    assert result['metrics'][0]['name']=='sales_total'
    assert records[0][2]['matched_metric']=='sales_total'


def test_cube_inventory_and_preview_preserve_select_permissions(db,monkeypatch):
    from contextlib import contextmanager
    from psycopg import sql
    role='data_cube_reader_'+uuid4().hex[:10]
    with db() as c:
        c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
        c.execute('CREATE SCHEMA cubes; CREATE VIEW cubes.sales_rollup AS SELECT * FROM sales.orders; '
                  'CREATE VIEW cubes.hidden_rollup AS SELECT * FROM sales.orders; '
                  'CREATE TABLE rvbbit.cube_catalog(name text,description text,grain text,category text); '
                  "INSERT INTO rvbbit.cube_catalog VALUES('sales_rollup','Curated sales','one order','Sales'),"
                  "('hidden_rollup','Private cube','one order','Secret')")
        c.execute(sql.SQL('GRANT USAGE ON SCHEMA cubes TO {}').format(sql.Identifier(role)))
        c.execute(sql.SQL('GRANT SELECT ON cubes.sales_rollup TO {}').format(sql.Identifier(role)))
    @contextmanager
    def factory(read_only=False,role=None):
        with db() as c:
            if role:c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
            yield c
    try:
        items=data.inventory(factory,role,lambda s:s in {'sales','cubes'})
        assert [i['relation'] for i in items]==['cubes.sales_rollup']
        cube=items[0]
        assert (cube['catalog_kind'],cube['description'],cube['grain'],cube['area'])==('cube','Curated sales','one order','Sales')
        monkeypatch.setattr(server,'_conn',factory)
        monkeypatch.setattr(server,'_session_pg_role',lambda subject:role)
        monkeypatch.setattr(server,'_schema_allowed',lambda s:s in {'sales','cubes'})
        monkeypatch.setattr(server,'_meaning_summary',lambda *a,**kw:{})
        queries=[]
        monkeypatch.setattr(server,'tool_run_sql',lambda statement,**kw:queries.append((statement,kw)) or {'rows':[]})
        result=server._calliope_data_service('preview',{'relation':'cubes.sales_rollup'},'reader',OWNER)
        assert result['dataset']['catalog_kind']=='cube'
        assert queries==[('SELECT * FROM "cubes"."sales_rollup"',{'limit':200,'timeout_ms':15000})]
        with pytest.raises(PermissionError):
            server._calliope_data_service('preview',{'relation':'cubes.hidden_rollup'},'reader',OWNER)
        assert len(queries)==1
    finally:
        with db() as c:
            c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_metric_search_matches_display_title_and_category_in_postgres(db,monkeypatch):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.metric_catalog(name text,version int,description text,params jsonb,
          grain text,labels jsonb,check_sql text,category text,subcategory text,created_at timestamptz DEFAULT now());
          INSERT INTO rvbbit.metric_catalog(name,version,description,params,labels,category,subcategory)
          VALUES('gm',1,'Profit after direct costs','{}','{"display":{"title":"Gross margin"}}','Finance','Revenue'),
                ('support',1,'Open tickets','{}','{}','Operations','Service')''')
    class CatalogConn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,args=None):
            rows=[]
            if 'FROM rvbbit.metric_catalog' in query and 'GROUP BY' not in query:
                with db() as c:rows=c.execute(query,args).fetchall()
            return SimpleNamespace(fetchall=lambda:rows)
    monkeypatch.setattr(server,'_conn',lambda:CatalogConn())
    monkeypatch.setattr(server,'_metric_user_relations',lambda owner:(set(),set()))
    for query in ['Gross margin','finance','Revenue']:
        result=server._metric_catalog_snapshot(OWNER,search=query,limit=24)
        assert [m['name'] for m in result['metrics']]==['gm']
        assert result['metrics'][0]['title']=='Gross margin'
        assert result['metrics'][0]['snapshot'] is None


def test_cube_shelf_revisions_publish_and_restore_exact_state_without_llm(explorer,db,monkeypatch):
    import cube_shelf
    exp,ops,charges,_=explorer
    with db() as c:
        c.execute('CREATE SCHEMA cubes; CREATE TABLE cubes.orders(region text,amount numeric)')
        c.execute("INSERT INTO cubes.orders VALUES('West',10),('East',20)")
    catalog=cube_shelf.metadata({'relation':'cubes.orders','title':'Orders','columns':[{'name':'region','type':'text'},{'name':'amount','type':'numeric'}]},
                               {'version':2,'sql':'SELECT region,sum(amount) AS amount FROM source GROUP BY region'})
    original=exp.service
    def service(op,args,subject,owner):
        if op=='cube_compile':
            assert args['relation']=='cubes.orders'
            q=cube_shelf.Query(catalog,args['spec'])
            return {'sql':q.compile(),'title':'Orders by region','catalog':catalog,'spec':q.spec}
        return original(op,args,subject,owner)
    exp.service=service
    spec={'groupBy':[{'column':{'name':'region'}}], 'measures':[{'id':'amount','column':{'name':'amount'},'agg':'sum','alias':'amount'}]}
    d=exp.shelf(OWNER,'reader',{'relation':'cubes.orders','spec':spec})
    assert d['latest_run']['status']=='complete' and not charges
    spec2={**spec,'filters':[{'column':{'name':'region'},'op':'eq','value':'West'}]}
    newer=exp.shelf(OWNER,'reader',{'relation':'cubes.orders','spec':spec2,'previous_id':str(d['id'])})
    assert newer['latest_run']['result']['rows']==[{'region':'West','amount':'10'}]
    assert len(exp.get(OWNER,d['id'])['latest_run']['result']['rows'])==2
    with pytest.raises(LookupError):exp.shelf('someone_else','reader',{'relation':'cubes.orders','spec':spec,'previous_id':str(d['id'])})
    monkeypatch.setattr(calliope.calliope_access,'require_participant',calliope.calliope_access.require_view)
    def insert(factory,sid,tid,surfaces):
        surface=dict(surfaces[0],id=str(uuid4()),session_id=sid)
        with factory() as c:c.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,payload,source,kind,title) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,%s)',
            (surface['id'],sid,data.encode(surface['payload']),data.encode(surface['source']),surface['kind'],surface['title']))
        return [surface]
    monkeypatch.setattr(calliope,'_insert_surfaces',insert)
    published=calliope._publish_data_run(db,exp.service,OWNER,'reader',str(newer['latest_run']['id']),SESSION)['surface']
    before=len([op for op in ops if op[0]=='run'])
    imported=exp.from_surface(OWNER,'reader',published['id'])
    assert imported['context']['cube_shelf']==newer['context']['cube_shelf']
    assert imported['latest_run']['result']['saved_snapshot']
    assert len([op for op in ops if op[0]=='run'])==before
    assert published['source']['cube_shelf']['spec']['filters']==spec2['filters']
    assert not charges


def test_cube_service_checks_source_access_before_reading_definition(monkeypatch):
    monkeypatch.setattr(data,'inventory',lambda *args:[])
    monkeypatch.setattr(server,'_session_pg_role',lambda *args:'reader')
    monkeypatch.setattr(server,'_conn',lambda **kwargs:pytest.fail('must authorize before metadata lookup'))
    for op in ('cube_metadata','cube_compile','cube_values'):
        with pytest.raises(PermissionError):
            server._calliope_data_service(op,{'relation':'cubes.secret','spec':{}},'reader',OWNER)
