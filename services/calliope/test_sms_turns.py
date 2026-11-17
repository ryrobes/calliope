"""Exercise the actual Calliope turn route for internal SMS and browser requests."""
import asyncio
import json
import sys
import uuid

import pytest
import calliope
import sms
from test_thread_sharing import shared_db, turn_api, db, OWNER, SESSION


@pytest.mark.parametrize('transport', ['twilio', 'simulator'])
@pytest.mark.parametrize('turn_kind', ['chat', 'agent_message'])
def test_sms_turn_uses_mapped_owner_and_http_cannot_spoof_delivery(turn_api,shared_db,monkeypatch,transport,turn_kind):
    handler,actor=turn_api;factory=shared_db
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_SMS_SIMULATOR_ENABLED', '1')
    with factory() as conn:
        conn.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN team_mode boolean DEFAULT false;
          ALTER TABLE rvbbit.calliope_turns ALTER COLUMN status SET DEFAULT 'running';
          ALTER TABLE rvbbit.calliope_turns ADD COLUMN request_fingerprint text,ADD COLUMN turn_kind text,
            ADD COLUMN user_message text,ADD COLUMN selected_surface_id uuid,ADD COLUMN design_profile_version_id uuid,
            ADD COLUMN evidence_refs jsonb,ADD COLUMN object_refs jsonb,ADD COLUMN origin_context jsonb,ADD COLUMN playbook_source_turn_id uuid,
            ADD COLUMN addressed_to text,ADD COLUMN routing jsonb DEFAULT '{}'::jsonb,ADD COLUMN queued_request jsonb;
          CREATE TABLE rvbbit.calliope_specialist_runs(id uuid,source_session_id uuid,status text,delivery text,context jsonb);
          CREATE TABLE rvbbit.calliope_specialist_threads(source_session_id uuid,context jsonb);
          ALTER TABLE rvbbit.calliope_agent_messages ADD COLUMN run_id uuid;
        """)
        conn.execute("UPDATE rvbbit.calliope_sessions SET channel='sms' WHERE id=%s",(SESSION,))
        conn.execute("INSERT INTO rvbbit.calliope_sms_connection VALUES(1,'{}','test-ref',true,%s,now())",(OWNER,))
        binding=sms.uid()
        conn.execute('INSERT INTO rvbbit.calliope_sms_bindings(id,phone_number,owner_email,configured_by,transport) VALUES(%s,%s,%s,%s,%s)',(binding,'+13215550123',OWNER,OWNER,transport))
        conn.execute('INSERT INTO rvbbit.calliope_sms_sessions(session_id,binding_id) VALUES(%s,%s)',(SESSION,binding))
    for name,value in {'_business_context_question_packet':{},'_term_bindings_note':'',
                       '_persist_attachments':[],'_insert_surfaces':[], '_compact_surface_context':([],None),
                       '_active_manual_workflow_run_id':None, '_composer_agent_mentions':[]}.items():
        monkeypatch.setattr(calliope,name,lambda *a,_value=value,**k:_value)
    monkeypatch.setattr(calliope.calliope_pages,'notebook_context',lambda *a:'')
    subjects=[]
    def subject(owner,via):
        subjects.append((owner,via));return ('mapped-user-role',True)
    monkeypatch.setattr(sys.modules['auth'],'session_subject',subject,raising=False)
    streams=[]
    def start(tid,stream,cf):
        # Check the actual constructed model payload, not just admission/auth.
        prompt=stream.ag_frame.f_locals['hermes_message']
        assert '[CALLIOPE SMS CHANNEL]' in prompt
        assert 'assume they cannot see the Stage' in prompt
        assert 'reply here by SMS' in prompt
        assert 'Approved Playbook candidates' in prompt
        assert 'delivery-note-canary' in prompt
        assert 'This request originates in the Calliope web notebook' not in prompt
        assert 'so those controls appear in chat and as a Floor question' not in prompt
        streams.append(stream)
        return 'test-broker'
    async def empty(*a):
        if False:yield b''
    async def wait(*a):return 'test-broker'
    monkeypatch.setattr(calliope,'_start_turn_stream',start)
    monkeypatch.setattr(calliope,'_subscribe_turn_stream',empty)
    monkeypatch.setattr(calliope,'_wait_for_turn_stream',wait)
    class Request:
        path_params={'session_id':SESSION};query_params={};headers={};cookies={}
        def __init__(self,cid):self.cid=cid;self.actor=OWNER
        async def json(self):return {'message':'How are sales?','client_turn_id':self.cid,'turn_kind':turn_kind,'delivered':{'owner':OWNER}}
    async def run():
        request=Request(str(uuid.uuid4()))
        external=await handler(request)
        assert external.status_code==409 and json.loads(external.body)['error']['code']=='CHANNEL_READ_ONLY'
        delivered={'owner':OWNER,'subject':'wrong-admin-role','author_display_name':OWNER,'agents':[], 'prompt_context':'delivery-note-canary'}
        first=await handler(request,delivered=delivered)
        assert first.status_code==200,first.body if hasattr(first,'body') else first
        assert (await handler(request,delivered=delivered)).status_code==200
        with factory() as conn:
            rows=conn.execute('SELECT author_email,user_message FROM rvbbit.calliope_turns').fetchall()
            assert rows==[{'author_email':OWNER,'user_message':'How are sales?'}]
            conn.execute('UPDATE rvbbit.calliope_sms_bindings SET enabled=false WHERE id=%s',(binding,))
        denied=await handler(Request(str(uuid.uuid4())),delivered=delivered)
        assert denied.status_code==403 and json.loads(denied.body)['error']['code']=='SMS_ACCESS_REVOKED'
        for stream in streams:await stream.aclose()
    asyncio.run(run())
    assert subjects and all(owner==OWNER and via=='sms' for owner,via in subjects)


def test_sms_channel_contract_is_also_in_system_instructions():
    instructions=calliope._instructions([],None,channel='sms')
    assert sms.CHANNEL_INSTRUCTIONS in instructions
    assert 'Do not create an email draft merely to deliver your answer' in instructions
    assert '[CALLIOPE SMS CHANNEL]' not in calliope._instructions([],None)
