"""Real database permissions, identity, and browser participation contracts."""
import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
import calliope
import calliope_access as access
import application_teams
import server
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401

PERSON = 'michael@example.com'
READER = 'reader@example.com'


@pytest.fixture
def shared_db(db):
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text DEFAULT 'Shared analysis',
          ADD COLUMN channel text DEFAULT 'web', ADD COLUMN archived boolean DEFAULT false,
          ADD COLUMN created_at timestamptz DEFAULT now(), ADD COLUMN updated_at timestamptz DEFAULT now(),
          ADD COLUMN hermes_session_id text DEFAULT 'hermes-sharing';
          CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text,ordinal integer,
            client_turn_id uuid,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,turn_id uuid,source jsonb,kind text,payload jsonb);
          CREATE TABLE rvbbit.calliope_briefs(session_id uuid);
          CREATE TABLE rvbbit.calliope_workflow_runs(session_id uuid);
          CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY,owner_email text,kind text,from_kind text,
            from_ref text,from_label text,to_session_id uuid,subject text,body text,payload jsonb,status text DEFAULT 'queued',
            not_before timestamptz DEFAULT now(),attempts int DEFAULT 0,last_error text,delivered_turn_id uuid,
            delivered_at timestamptz,created_at timestamptz DEFAULT now());""")
        c.execute(application_teams.DDL)
        c.execute(access.DDL)
        c.execute('INSERT INTO rvbbit.application_principals(email,display_name) VALUES(%s,%s),(%s,%s),(%s,%s)',
                  (OWNER,'Owner',PERSON,'Michael',READER,'Reader'))
    return db


def share(db, participants=(PERSON,), revision=1):
    return access.replace_access(db, {'subject':OWNER},SESSION,revision,
                                 people=[PERSON,READER],participants=list(participants))


def test_every_grant_is_a_membership_and_revoke_is_audited(shared_db):
    db=shared_db
    shared=access.replace_access(db,{'subject':OWNER},SESSION,1,people=[PERSON,READER])
    assert shared['room'] is True
    with db() as c:
        # No spectators: everyone added to a chat is a member of the room.
        assert access.require_view(c,SESSION,PERSON)['access_role']=='member'
        assert access.require_participant(c,SESSION,READER.upper())['access_role']=='member'
        assert access.require_participant(c,SESSION,OWNER)['access_role']=='owner'
        # A grant written as view-only before rooms is a membership too.
        c.execute('UPDATE rvbbit.calliope_session_view_grants SET can_participate=false')
        assert access.require_participant(c,SESSION,READER)['access_role']=='member'
        with pytest.raises(access.CalliopeAccessError):access.require_view(c,SESSION,'stranger@example.com')
        with pytest.raises(access.CalliopeAccessError):access.require_owner(c,SESSION,PERSON)
    # Members talk; only the owner changes who is in the room.
    with pytest.raises(access.CalliopeAccessError):
        access.replace_access(db,{'subject':PERSON},SESSION,shared['session']['access_revision'],people=[PERSON])
    # An old client's participants list can't make anyone a spectator.
    retained=access.replace_access(db,{'subject':OWNER},SESSION,shared['session']['access_revision'],
                                   people=[PERSON,READER],participants=[PERSON])
    with db() as c:
        assert access.require_participant(c,SESSION,READER)['access_role']=='member'
    with pytest.raises(access.CalliopeAccessError) as stale:
        access.replace_access(db,{'subject':OWNER},SESSION,1,people=[PERSON])
    assert stale.value.code=='SESSION_ACCESS_CONFLICT'
    narrowed=access.replace_access(db,{'subject':OWNER},SESSION,retained['session']['access_revision'],people=[READER])
    removed=access.replace_access(db,{'subject':OWNER},SESSION,narrowed['session']['access_revision'],people=[])
    with db() as c:
        with pytest.raises(access.CalliopeAccessError):access.require_view(c,SESSION,PERSON)
        events=c.execute('SELECT before_state,after_state FROM rvbbit.calliope_session_access_events ORDER BY created_at').fetchall()
        assert events[0]['after_state']['people']==sorted([PERSON,READER])
        assert events[-2]['after_state']['people']==[READER]
        assert events[-1]['before_state']['people']==[READER]
        assert events[-1]['after_state']['people']==[]
    assert removed['private'] and not removed['room']


def test_caller_is_turn_author_not_owner_and_sql_keeps_their_authority(shared_db):
    db=shared_db;share(db)
    with db() as c:
        # Activity inventory branches are intentionally absent in this fixture.
        c.execute('DROP TABLE rvbbit.calliope_workflow_runs')
        c.execute("ALTER TABLE rvbbit.calliope_turns ADD COLUMN turn_kind text DEFAULT 'chat'")
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,ordinal,author_email) VALUES(%s,%s,'running',1,%s)",
                  (uuid.uuid4(),SESSION,PERSON))
        result=server._calliope_activity_for_hermes_session(c,'hermes-sharing')
        assert result['owner']==PERSON
    assert calliope._execution_subject_for_session(db,SESSION,PERSON)==PERSON
    with pytest.raises(PermissionError,match='Admins'):
        calliope.run_workspace_sql_for_session(db,SESSION,'CREATE TABLE calliope.nope(id int)',authorized_owner=PERSON)
    with db() as c:
        assert c.execute("SELECT to_regclass('calliope.nope') AS t").fetchone()['t'] is None
        c.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
        # Background followups still belong to that author after the parent finishes.
        assert server._calliope_activity_for_hermes_session(c,'hermes-sharing')['owner']==PERSON


def test_participation_is_not_management_or_a_channel_backdoor(shared_db):
    db=shared_db;share(db)
    with db() as c:
        assert calliope._session_json(access.require_view(c,SESSION,PERSON))['read_only'] is False
        assert calliope._session_json(access.require_view(c,SESSION,PERSON))['can_manage'] is False
        c.execute("UPDATE rvbbit.calliope_sessions SET channel='google_chat'")
        with pytest.raises(access.CalliopeAccessError):access.require_participant(c,SESSION,PERSON)
        c.execute("UPDATE rvbbit.calliope_sessions SET channel='web',archived=true")
        with pytest.raises(access.CalliopeAccessError):access.require_participant(c,SESSION,PERSON)


def test_participant_answers_keep_respondent_identity(shared_db):
    import calliope_inputs as inputs
    db=shared_db;share(db)
    with db() as c:
        inputs.ensure(c)
        c.execute(calliope._INBOX_DDL)
        tid,qid=uuid.uuid4(),uuid.uuid4()
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status,ordinal,author_email) VALUES(%s,%s,'complete',1,%s)",(tid,SESSION,OWNER))
        c.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,payload) VALUES(%s,%s,%s,'input',%s::jsonb)",
                  (qid,SESSION,tid,json.dumps({'title':'Choose','prompt':'Pick one','fields':[{'key':'direction','label':'Direction','type':'text','required':True}]})))
    assert inputs.get(db,PERSON,qid)['can_answer'] is True
    assert inputs.get(db,READER,qid)['can_answer'] is True
    with pytest.raises(LookupError):
        inputs.change(db,'stranger@example.com',qid,'answer',{'revision':1,'answers':{'direction':'Go'}})
    answer=inputs.change(db,PERSON,qid,'answer',{'revision':1,'answers':{'direction':'Go'}})
    delivered=inputs.delivery(db,answer['delivery_message_id'])
    assert delivered['answered_by']==PERSON
    with db() as c:
        assert c.execute('SELECT owner_email FROM rvbbit.calliope_agent_messages').fetchone()['owner_email']==PERSON


@pytest.fixture
def turn_api(shared_db,monkeypatch,tmp_path):
    db=shared_db;share(db)
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def register(handler):routes[(path,tuple(methods))]=handler;return handler
            return register
    actor=['stranger@example.com']
    monkeypatch.setitem(sys.modules,'auth',SimpleNamespace(read_session_full=lambda r:{'identity':getattr(r,'actor',actor[0]),'sub':getattr(r,'actor',actor[0])},google_enabled=lambda:False,background_layer=lambda *a,**k:'',
        _google_config=lambda:{'client_id':'','client_secret':''}))
    for name in ('ensure_tables',):monkeypatch.setattr(calliope,name,lambda _:None)
    for module in (calliope.hermes_docs,calliope.hermes_config,calliope.provider_settings):monkeypatch.setattr(module,'ensure',lambda _:None)
    thread_start=calliope.threading.Thread.start
    monkeypatch.setattr(calliope.threading.Thread,'start',lambda _:None)
    monkeypatch.setattr(application_teams,'observe_principal_on_connection',lambda *a,**k:None)
    monkeypatch.setattr(calliope.calliope_inputs,'blocking_request',lambda *a,**k:None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes.invalid:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test-key')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    calliope.register_calliope_routes(MCP(),db,'',lambda value:value)
    monkeypatch.setattr(calliope.threading.Thread,'start',thread_start)
    handler=routes[('/api/calliope/sessions/{session_id}/turn',('POST',))]
    return handler, actor


def test_http_admission_lets_every_member_talk(turn_api,shared_db):
    handler, actor=turn_api
    class Request:
        path_params={'session_id':SESSION};query_params={};headers={};cookies={}
        async def json(self):return {'message':'hello','client_turn_id':'invalid'}
    assert asyncio.run(handler(Request())).status_code==404
    for member in (PERSON,READER,OWNER):
        actor[0]=member
        result=asyncio.run(handler(Request()))
        assert result.status_code==400 and json.loads(result.body)['error']['code']=='INVALID_CLIENT_TURN_ID'
    # A chat that continues in another channel is read-only here, even for members.
    with shared_db() as c:c.execute("UPDATE rvbbit.calliope_sessions SET channel='google_chat'")
    actor[0]=PERSON
    assert asyncio.run(handler(Request())).status_code==403


def test_room_messages_keep_their_authors_wait_their_turn_and_reattach(turn_api,shared_db,monkeypatch):
    import calliope_inputs as inputs
    handler,actor=turn_api; db=shared_db
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN team_mode boolean DEFAULT false;
          ALTER TABLE rvbbit.calliope_turns ALTER COLUMN status SET DEFAULT 'running';
          ALTER TABLE rvbbit.calliope_turns ADD COLUMN request_fingerprint text,ADD COLUMN turn_kind text,
            ADD COLUMN user_message text,ADD COLUMN selected_surface_id uuid,ADD COLUMN design_profile_version_id uuid,
            ADD COLUMN evidence_refs jsonb,ADD COLUMN object_refs jsonb,ADD COLUMN origin_context jsonb,ADD COLUMN playbook_source_turn_id uuid,
            ADD COLUMN addressed_to text,ADD COLUMN routing jsonb DEFAULT '{}'::jsonb,ADD COLUMN queued_request jsonb,
            ADD COLUMN completed_at timestamptz,ADD COLUMN error text;
          CREATE TABLE rvbbit.calliope_specialist_runs(id uuid,source_session_id uuid,status text,delivery text,context jsonb);
          CREATE TABLE rvbbit.calliope_specialist_threads(source_session_id uuid,context jsonb);
          ALTER TABLE rvbbit.calliope_agent_messages ADD COLUMN run_id uuid;""")
        inputs.ensure(c)
    for name,value in {'_business_context_question_packet':{},'_term_bindings_note':'',
                       '_persist_attachments':[],'_insert_surfaces':[], '_compact_surface_context':([],None),
                       '_active_manual_workflow_run_id':None, '_composer_agent_mentions':[],
                       '_notify_room_mentions':None,'_room_turn_context':{'notes':'','instructions':''}}.items():
        monkeypatch.setattr(calliope,name,lambda *a,_value=value,**k:_value)
    monkeypatch.setattr(calliope.calliope_pages,'notebook_context',lambda *a:'')
    streams=[]
    def start(tid,stream,cf):streams.append(stream);return 'test-broker'
    async def empty(*a):
        if False:yield b''
    async def wait(*a):return 'test-broker'
    monkeypatch.setattr(calliope,'_start_turn_stream',start)
    monkeypatch.setattr(calliope,'_subscribe_turn_stream',empty)
    monkeypatch.setattr(calliope,'_wait_for_turn_stream',wait)
    class Request:
        path_params={'session_id':SESSION};query_params={};headers={};cookies={}
        def __init__(self,cid,author=None,message='Analyze the same question'):
            self.cid=cid
            self.actor=author or actor[0]
            self.message=message
        async def json(self):return {'message':self.message,'client_turn_id':self.cid,'addressed_to':'callie'}
    def finish_running():
        with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='complete' WHERE status='running'")
    first_id,second_id,third_id,fourth_id=(str(uuid.uuid4()) for _ in range(4))
    async def run():
        actor[0]=PERSON
        first=await asyncio.wait_for(handler(Request(first_id)),5)
        assert first.status_code==200, getattr(first,'body',None)
        # Retry attaches to the accepted turn without inserting another message.
        assert (await handler(Request(first_id))).status_code==200
        # Callie is busy: the owner's message waits its turn instead of failing.
        actor[0]=OWNER
        waiting=await handler(Request(second_id))
        assert waiting.status_code==202, waiting.body
        body=json.loads(waiting.body)
        assert body['queued'] and body['reason']=='callie_busy' and body['position']==1
        # A retried post of a waiting message doesn't add another.
        assert (await handler(Request(second_id))).status_code==202
        finish_running()
        # Callie is free, but someone is already waiting: this goes behind them.
        behind=await handler(Request(third_id,OWNER,'and split it by region'))
        assert behind.status_code==202 and json.loads(behind.body)['reason']=='waiting_line'
        later=await handler(Request(fourth_id,PERSON,'thanks!'))
        assert json.loads(later.body)['position']==3
        # The owner's two consecutive messages become one turn, run as the owner.
        candidate=calliope._queued_turn_candidate(db,SESSION)
        assert candidate['author_email']==OWNER and len(candidate['followers'])==1
        merged_text='Analyze the same question\n\nand split it by region'
        promoted=await handler(Request(second_id,OWNER,merged_text),delivered={
            'owner':OWNER,'subject':None,'author_display_name':'Owner','agents':[],'prompt_context':'',
            'queued_turn_id':candidate['id'],'merged_ids':[f['id'] for f in candidate['followers']]})
        assert promoted.status_code==200, promoted.body
        # While it runs, the next one stays put.
        assert calliope._queued_turn_candidate(db,SESSION) is None
        finish_running()
        assert calliope._queued_turn_candidate(db,SESSION)['author_email']==PERSON
        # Access can change while request preparation runs: admission must recheck under lock.
        def revoke(*a):
            snapshot=access.get_access(db,{'subject':OWNER},SESSION)
            access.replace_access(db,{'subject':OWNER},SESSION,snapshot['session']['access_revision'],people=[READER])
            return []
        monkeypatch.setattr(calliope,'_resolve_composer_objects',revoke)
        with db() as c:c.execute("UPDATE rvbbit.calliope_turns SET status='withdrawn' WHERE status='queued'")
        revoked=await handler(Request(str(uuid.uuid4()),PERSON))
        assert revoked.status_code==404, revoked.body
        for stream in streams:await stream.aclose()
    asyncio.run(run())
    with db() as c:
        rows=c.execute('SELECT author_email,ordinal,status,origin_context FROM rvbbit.calliope_turns ORDER BY ordinal').fetchall()
        assert [(r['author_email'],r['status']) for r in rows]==[
            (PERSON,'complete'),(OWNER,'merged'),(PERSON,'withdrawn'),(OWNER,'complete')]
        # The promoted message keeps its author and remembers it waited.
        assert rows[-1]['ordinal']==5 and rows[-1]['origin_context']['waited_in_queue'] is True


def test_an_open_question_lets_the_room_talk_but_holds_messages_for_callie(turn_api,shared_db,monkeypatch):
    handler,actor=turn_api; db=shared_db
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN team_mode boolean DEFAULT false;
          ALTER TABLE rvbbit.calliope_turns ALTER COLUMN status SET DEFAULT 'running';
          ALTER TABLE rvbbit.calliope_turns ADD COLUMN request_fingerprint text,ADD COLUMN turn_kind text,
            ADD COLUMN user_message text,ADD COLUMN selected_surface_id uuid,ADD COLUMN design_profile_version_id uuid,
            ADD COLUMN evidence_refs jsonb,ADD COLUMN object_refs jsonb,ADD COLUMN origin_context jsonb,ADD COLUMN playbook_source_turn_id uuid,
            ADD COLUMN addressed_to text,ADD COLUMN routing jsonb DEFAULT '{}'::jsonb,ADD COLUMN queued_request jsonb,
            ADD COLUMN completed_at timestamptz,ADD COLUMN error text,ADD COLUMN assistant_message text;
          CREATE TABLE rvbbit.calliope_specialist_runs(id uuid,source_session_id uuid,status text,delivery text,context jsonb);
          CREATE TABLE rvbbit.calliope_specialist_threads(source_session_id uuid,context jsonb);
          ALTER TABLE rvbbit.calliope_agent_messages ADD COLUMN run_id uuid;""")
    for name,value in {'_business_context_question_packet':{},'_term_bindings_note':'',
                       '_persist_attachments':[],'_insert_surfaces':[], '_compact_surface_context':([],None),
                       '_active_manual_workflow_run_id':None, '_composer_agent_mentions':[],
                       '_notify_room_mentions':None,'_room_turn_context':{'notes':'','instructions':''}}.items():
        monkeypatch.setattr(calliope,name,lambda *a,_value=value,**k:_value)
    monkeypatch.setattr(calliope.calliope_pages,'notebook_context',lambda *a:'')
    question=str(uuid.uuid4())
    monkeypatch.setattr(calliope.calliope_inputs,'blocking_request',lambda *a,**k:question)
    class Request:
        path_params={'session_id':SESSION};query_params={};headers={};cookies={}
        def __init__(self,message,to):
            self.actor=PERSON; self.body={'message':message,'client_turn_id':str(uuid.uuid4()),'addressed_to':to}
        async def json(self):return self.body
    actor[0]=PERSON
    talk=asyncio.run(handler(Request('A or B, what do you think?','people')))
    assert talk.status_code==201 and json.loads(talk.body)['note'] is True
    held=asyncio.run(handler(Request('Callie, go with B','callie')))
    error=json.loads(held.body)['error']
    assert held.status_code==409 and error['code']=='INPUT_REQUIRED' and error['request_id']==question
    assert 'keep talking with the room' in error['message']
    with db() as c:
        assert [r['turn_kind'] for r in c.execute('SELECT turn_kind FROM rvbbit.calliope_turns ORDER BY ordinal').fetchall()]==['note']
