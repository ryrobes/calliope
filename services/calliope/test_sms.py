"""Real PostgreSQL SMS identity/queue lifecycle; all transports are local fakes."""
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sms
import calliope
import calliope_dreams
from test_correspondence import database, OWNER

ADMIN = 'admin@example.com'
OTHER = 'other@example.com'
PHONE = '+13215550123'
CONFIG = {'account_sid': 'AC'+'a'*32, 'phone_number': '+13215550999', 'webhook_url': 'https://callie.example.com/webhooks/twilio'}


@pytest.fixture(scope='module')
def schema(database):
    with database() as conn:
        conn.execute('''ALTER TABLE rvbbit.calliope_sessions ADD COLUMN channel text DEFAULT 'web', ADD COLUMN archived boolean DEFAULT false;
          ALTER TABLE rvbbit.calliope_turns ADD COLUMN client_turn_id uuid, ADD COLUMN assistant_message text,
          ADD COLUMN created_at timestamptz DEFAULT now(), ADD COLUMN response_receipt jsonb DEFAULT '{}';
          CREATE UNIQUE INDEX test_sms_turn_key ON rvbbit.calliope_turns(session_id,client_turn_id);
          CREATE FUNCTION rvbbit.resolve_credential(text,text,text) RETURNS text LANGUAGE sql AS $$SELECT 'test-token'::text$$;
          CREATE FUNCTION rvbbit.put_credential(text,text,text,text,text,jsonb) RETURNS text LANGUAGE sql AS $$SELECT 'test-ref'::text$$;
        ''')
        conn.execute(calliope._ACTION_DDL)
    sms.ensure(database)
    return database


@pytest.fixture
def env(schema, monkeypatch):
    with schema() as conn:
        conn.execute('TRUNCATE rvbbit.calliope_sms_bindings,rvbbit.calliope_sms_connection,rvbbit.calliope_sessions,rvbbit.calliope_turns,rvbbit.calliope_surfaces,rvbbit.calliope_action_runs,rvbbit.calliope_agent_messages CASCADE')
        conn.execute('INSERT INTO rvbbit.application_principals(email) VALUES(%s),(%s),(%s) ON CONFLICT DO NOTHING', (OWNER, OTHER, ADMIN))
        conn.execute('INSERT INTO rvbbit.calliope_sms_connection(id,settings,credential_ref,configured_by) VALUES(1,%s::jsonb,%s,%s)', (json.dumps(CONFIG),'test-ref',ADMIN))
    monkeypatch.setattr(calliope_dreams, 'is_company_admin', lambda factory, owner: owner == ADMIN)
    monkeypatch.setattr(calliope, '_action_runtime_inventory', lambda *_: {'company_admin': True})
    return schema


def bind(env, owner=OWNER, enabled=True):
    return sms.bind(env, ADMIN, {'phone_number':PHONE,'owner_email':owner,'enabled':enabled})


def params(n=1, **kw):
    return {'AccountSid':CONFIG['account_sid'],'From':PHONE,'To':CONFIG['phone_number'],
            'MessageSid':'SM'+f'{n:032x}', 'Body':'How are sales?', 'NumMedia':'0', **kw}


def bridge(action, payload):
    if action == 'validate':
        return {'valid': payload['signature'] == 'valid' and payload['params']['AccountSid']==CONFIG['account_sid'] and payload['params']['To']==CONFIG['phone_number']}
    if action == 'media':
        return {'attachments':[{'name':'plate.png','data_url':'data:image/png;base64,'+base64.b64encode(b'photo').decode()}]}
    if action == 'format_reply':
        return {'chunks': [payload['text']]}
    if action == 'send':
        return {'state':'sent','message_id':'SM'+'e'*32}
    if action == 'probe':
        return {'ok':True,'webhook_configured':False}
    raise AssertionError(action)


def ingest(env, n=1, **kw):
    return sms.ingest(env,params(n,**kw),'valid',bridge)


def new_session(config,factory,owner,title):
    with factory() as conn:
        return conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s) RETURNING *',(sms.uid(),owner)).fetchone()


def delivered(env, n=1, text='Sales are up.', **kw):
    ingest(env,n,**kw)
    item=sms.claim(env)
    prepared=sms.prepare_incoming(env,None,item,new_session,bridge)
    turn=sms.uid()
    with env() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_turns(id,session_id,client_turn_id,status,assistant_message) VALUES(%s,%s,%s,%s,%s)', (turn,prepared['session_id'],item['id'],'complete',text))
    sms.finish_incoming(env,item,turn)
    return item,prepared,turn


def test_unknown_sender_and_invalid_signatures_do_not_create_sessions(env):
    with pytest.raises(PermissionError):
        sms.ingest(env,params(),'bad',bridge)
    assert not ingest(env)['accepted']
    bind(env)
    with pytest.raises(PermissionError):
        ingest(env,To='+13215550000')
    with env() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_sms_inbox').fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_sessions').fetchone()['n']==0


def test_mapping_is_admin_only_existing_login_and_explicit_reassignment(env):
    with pytest.raises(PermissionError):
        sms.bind(env,OWNER,{'phone_number':PHONE,'owner_email':OWNER})
    with pytest.raises(ValueError,match='does not exist'):
        bind(env,'invented@example.com')
    bind(env)
    bind(env)  # saving the same mapping does not fork the conversation
    with pytest.raises(ValueError,match='Revoke'):
        bind(env,OTHER)
    bind(env,enabled=False)
    bind(env,OTHER)
    with env() as conn:
        rows=conn.execute('SELECT * FROM rvbbit.calliope_sms_bindings ORDER BY created_at').fetchall()
        assert len(rows)==2 and not rows[0]['enabled'] and rows[1]['owner_email']==OTHER


def test_image_only_mms_dedupe_and_same_user_thread(env):
    bind(env)
    item,first,turn=delivered(env,Body='',NumMedia='1',MediaUrl0='https://api.twilio.com/a',MediaContentType0='image/png')
    assert first['owner']==OWNER and first['body']['attachments'][0]['name']=='plate.png'
    assert ingest(env)['duplicate']
    second=sms.prepare_incoming(env,None,(ingest(env,2) and sms.claim(env)),new_session,bridge)
    assert second['session_id']==first['session_id']
    with env() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_sessions').fetchone()['n']==1
        assert conn.execute('SELECT owner_email,channel FROM rvbbit.calliope_sessions').fetchone()=={'owner_email':OWNER,'channel':'sms'}


def test_revoked_pending_messages_and_replies_do_not_cross_users(env):
    bind(env)
    item,first,turn=delivered(env)
    assert sms.prepare_reply(env,bridge)
    ingest(env,2)
    bind(env,enabled=False)
    bind(env,OTHER)
    calls=[]
    def no_send(action,payload):
        calls.append(action)
        assert action!='send'
    assert sms.send_one(env,no_send)
    pending=sms.claim(env)
    assert sms.prepare_incoming(env,None,pending,new_session,bridge) is None
    _,second,_=delivered(env,3)
    assert first['session_id']!=second['session_id'] and second['owner']==OTHER
    with env() as conn:
        assert conn.execute('SELECT state FROM rvbbit.calliope_sms_outbox').fetchone()['state']=='cancelled'
        assert conn.execute('SELECT state FROM rvbbit.calliope_sms_inbox WHERE id=%s',(pending['id'],)).fetchone()['state']=='cancelled'


def test_busy_and_recovered_delivery_dont_repeat_turns(env):
    bind(env)
    item,prepared,turn=delivered(env)
    with env() as conn:
        conn.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s",(turn,))
    ingest(env,2)
    second=sms.claim(env)
    assert sms.prepare_incoming(env,None,second,new_session,bridge) is None
    with env() as conn:
        assert conn.execute('SELECT attempts FROM rvbbit.calliope_sms_inbox WHERE id=%s',(second['id'],)).fetchone()['attempts']==0
        conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state='delivering' WHERE id=%s",(item['id'],))
    assert sms.prepare_incoming(env,None,item,new_session,bridge) is None
    with env() as conn:
        assert conn.execute('SELECT state,turn_id FROM rvbbit.calliope_sms_inbox WHERE id=%s',(item['id'],)).fetchone()=={'state':'delivered','turn_id':__import__('uuid').UUID(turn)}


def test_archiving_starts_new_conversation(env):
    bind(env)
    _,first,_=delivered(env)
    with env() as conn:
        conn.execute('UPDATE rvbbit.calliope_sessions SET archived=true WHERE id=%s',(first['session_id'],))
    _,second,_=delivered(env,2)
    assert second['session_id']!=first['session_id']


def test_contact_context_stays_with_mapped_login(env):
    import correspondence
    bind(env)
    _,prepared,_=delivered(env)
    correspondence.save_contact(env,ADMIN,{'name':'Private admin note','channels':[{'channel':'sms','address':PHONE}],'note':'private-admin-canary'})
    person=correspondence.save_contact(env,OWNER,{'name':'Joe','channels':[{'channel':'sms','address':PHONE}],'note':'Handles warranty returns'})
    context=sms.prompt_context(env,prepared['session_id'],OWNER)
    assert 'Handles warranty returns' in context and 'private-admin-canary' not in context
    with pytest.raises(PermissionError):
        sms.prompt_context(env,prepared['session_id'],OTHER)


def test_playbook_candidates_only_expose_accessible_approved_versions(env):
    # Exercise the discovery SQL against real PostgreSQL, including a newer
    # private draft on a shared, approved playbook and excluded entries.
    with env() as conn, conn.transaction(force_rollback=True):
        conn.execute('''CREATE TABLE rvbbit.calliope_playbooks (
          id uuid PRIMARY KEY, capability_kind text DEFAULT 'cap_playbook',
          capability_name text, approved_version int, archived boolean DEFAULT false,
          updated_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_playbook_versions (
          playbook_id uuid,version int,title text,synopsis text,readiness text,
          semantic_contract jsonb);
          CREATE TABLE rvbbit.test_sms_playbook_grants(name text,subject text);
          CREATE FUNCTION rvbbit.capability_can_use(text,text,text) RETURNS boolean
          LANGUAGE sql AS $$SELECT EXISTS(SELECT 1 FROM rvbbit.test_sms_playbook_grants WHERE name=$2 AND subject=$3)$$;
        ''')
        for name,approved,archived,readiness,granted in [
            ('shared',1,False,'ready',True),('private',1,False,'ready',False),
            ('draft',None,False,'ready',True),('archived',1,True,'ready',True),
            ('blocked',1,False,'blocked',True)]:
            ident=sms.uid()
            conn.execute('INSERT INTO rvbbit.calliope_playbooks(id,capability_name,approved_version,archived) VALUES(%s,%s,%s,%s)',(ident,name,approved,archived))
            conn.execute("INSERT INTO rvbbit.calliope_playbook_versions VALUES(%s,1,%s,'summary',%s,'{\"triggers\":[\"on site\"]}')",(ident,name,readiness))
            if granted:conn.execute('INSERT INTO rvbbit.test_sms_playbook_grants VALUES(%s,%s)',(name,OWNER))
            if name=='shared':
                conn.execute("INSERT INTO rvbbit.calliope_playbook_versions VALUES(%s,2,'UNAPPROVED CANARY','private draft','ready','{}')",(ident,))
        candidates=sms._playbook_candidates(conn,OWNER)
        assert candidates==[{'playbook_ref':'shared','version':1,'title':'shared','synopsis':'summary','triggers':['on site']}]
        assert sms._playbook_candidates(conn,OTHER)==[]


def test_reply_chunks_order_idempotence_and_uncertain_delivery(env):
    bind(env)
    _,prepared,turn=delivered(env)
    calls=[]
    def transport(action,payload):
        if action=='format_reply':return {'chunks':['one','two','three']}
        if action=='send':
            calls.append(payload)
            return {'state':'sent' if payload['text']=='one' else 'uncertain'}
        raise AssertionError(action)
    assert sms.prepare_reply(env,transport)
    assert not sms.prepare_reply(env,transport)
    for _ in range(3):assert sms.send_one(env,transport)
    assert not sms.send_one(env,transport)
    assert [c['text'] for c in calls]==['one','two']
    assert all(c['to']==PHONE for c in calls)
    with env() as conn:
        assert [r['state'] for r in conn.execute('SELECT state FROM rvbbit.calliope_sms_outbox ORDER BY chunk_index').fetchall()]==['sent','uncertain','cancelled']


def test_native_connect_and_mapping_cards_use_reviewed_form_and_no_secret_receipts(env,monkeypatch):
    import connection_workflow
    session,turn=sms.uid(),sms.uid()
    with env() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(session,ADMIN))
        conn.execute('INSERT INTO rvbbit.calliope_turns(id) VALUES(%s)',(turn,))
    monkeypatch.setattr(calliope,'_session_for_participant',lambda *_:{'id':session,'owner_email':ADMIN})
    monkeypatch.setattr(sms,'control',bridge)
    for action,inputs in [('sms.connect',{**CONFIG,'secret:TWILIO_AUTH_TOKEN':'secret-test-canary'}),('sms.user',{'phone_number':PHONE,'owner_email':OWNER})]:
        public={k:v for k,v in inputs.items() if not k.startswith('secret:')}
        result=asyncio.run(calliope.administer_action_for_session(env,session,action,public,authorized_owner=ADMIN))
        surface=calliope._project_tool_result('administer_calliope_action',result,{'action_id':action},'connect')[0]
        sid=sms.uid()
        with env() as conn:
            conn.execute('INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',(sid,session,turn,surface['kind'],surface['title'],surface['lineage_key'],json.dumps(surface['payload'])))
        result,status=asyncio.run(connection_workflow.execute(env,ADMIN,action,{'surface_id':sid,'session_id':session,'revision':0,'inputs':inputs}))
        assert status==200, result
        assert result['run']['status']=='complete'
        assert 'secret-test-canary' not in json.dumps(result,default=str)
    with env() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_sms_bindings WHERE enabled').fetchone()['n']==1
        events=conn.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchall()
    assert len(events)==2 and 'SMS' in calliope._agent_mailbox_prompt_context(events)
    with pytest.raises(PermissionError):
        calliope._require_direct_administration_action(env,OWNER,'sms.connect')


def test_no_guest_identity_fallback(monkeypatch):
    import auth
    monkeypatch.setattr(auth,'session_subject',lambda *_:('rvbbit_guest',False))
    with pytest.raises(PermissionError):sms.execution_subject(OWNER)
    monkeypatch.setattr(auth,'session_subject',lambda *_:('joes_role',True))
    assert sms.execution_subject(OWNER)=='joes_role'


def test_signature_route_rejects_large_malformed_and_authless_requests(env,monkeypatch):
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from starlette.responses import JSONResponse
    routes=[]
    class Routes:
        def custom_route(self,path,methods):
            def register(fn):routes.append(Route(path,fn,methods=methods));return fn
            return register
    original = sms.ingest
    monkeypatch.setattr(sms, 'ingest', lambda factory, params, signature: original(factory, params, signature, bridge))
    sms.register(Routes(),env,lambda request:(OWNER,None),lambda value: JSONResponse(json.loads(sms.dump(value))))
    bind(env)
    with TestClient(Starlette(routes=routes)) as client:
        assert client.post('/webhooks/twilio',data=params()).status_code==403
        assert client.post('/webhooks/twilio',data=params(),headers={'X-Twilio-Signature':'valid'}).status_code==200
        assert client.post('/webhooks/twilio',content=b'x='+b'a'*65536,headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==413
        assert client.post('/webhooks/twilio',content='From=x&From=y',headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==400
        assert client.post('/webhooks/twilio',json=params()).status_code==415
        status=client.get('/api/calliope/sms').json()
        assert status['connection'] is None and len(status['bindings'])==1



def test_twilio_opt_out_revokes_mapping_and_start_does_not_regrant(env):
    bind(env)
    assert not ingest(env,OptOutType='STOP',Body='STOP')['accepted']
    assert not ingest(env,2,OptOutType='START',Body='START')['accepted']
    with env() as conn:
        assert not conn.execute('SELECT enabled FROM rvbbit.calliope_sms_bindings').fetchone()['enabled']
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_sms_inbox').fetchone()['n']==0


def test_sms_owner_can_use_tools_and_review_native_cards_but_viewers_cannot(env,monkeypatch):
    import calliope_access as access
    bind(env)
    _,prepared,_=delivered(env)
    with env() as conn:
        conn.execute('''ALTER TABLE rvbbit.application_principals ADD COLUMN IF NOT EXISTS display_name text, ADD COLUMN IF NOT EXISTS avatar_key uuid;
            CREATE TABLE IF NOT EXISTS rvbbit.calliope_session_view_grants(session_id uuid,principal_email text);
            CREATE OR REPLACE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$
            SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions WHERE id=$1 AND (owner_email=$2 OR $2='other@example.com'))$$;''')
        conn.execute(access.PARTICIPATION_DDL)
        session=access.require_participant(conn,prepared['session_id'],OWNER)
        assert session['access_role']=='owner'
        assert conn.execute('SELECT rvbbit.calliope_session_can_participate(%s,%s) v',(prepared['session_id'],OWNER)).fetchone()['v']
        with pytest.raises(access.CalliopeAccessError):access.require_participant(conn,prepared['session_id'],OTHER)
        conn.execute("UPDATE rvbbit.calliope_sessions SET channel='google_chat' WHERE id=%s",(prepared['session_id'],))
        with pytest.raises(access.CalliopeAccessError):access.require_participant(conn,prepared['session_id'],OWNER)
