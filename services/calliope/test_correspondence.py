"""Reviewed mail: real PostgreSQL transactions and a local-only SMTP fixture."""
import base64
import importlib.util
import os
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest

import correspondence as mail
import server

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('reviewed_mail_bridge', ROOT / 'docker/calliope/reviewed_mail_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
OWNER = 'operator@example.com'


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root = tmp_path_factory.mktemp('reviewed-mail-pg')
    data, sock = root / 'db', root / 'socket'
    sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'], check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def factory():
        return psycopg.connect(host=str(sock),dbname='postgres',autocommit=True,row_factory=dict_row)
    with factory() as conn:
        conn.execute('''CREATE SCHEMA rvbbit;
          CREATE TABLE rvbbit.application_principals(email text primary key);
          CREATE TABLE rvbbit.calliope_sessions(id uuid primary key,owner_email text,hermes_session_id text);
          CREATE TABLE rvbbit.calliope_turns(id uuid primary key,session_id uuid,status text DEFAULT 'running',author_email text);
          CREATE TABLE rvbbit.calliope_specialist_runs(hermes_session_id text,owner_email text,source_session_id uuid,source_turn_id uuid,status text);
          CREATE TABLE rvbbit.calliope_surfaces(id uuid primary key,session_id uuid,turn_id uuid,kind text,title text,lineage_key text,payload jsonb);
          CREATE TABLE rvbbit.calliope_agent_messages(id uuid primary key,owner_email text,kind text,
            from_kind text,from_ref text,from_label text,to_session_id uuid references rvbbit.calliope_sessions(id),
            subject text,body text,payload jsonb,status text default 'queued');
        ''')
    mail.ensure(factory)
    try:
        yield factory
    finally:
        subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


@pytest.fixture
def env(database,tmp_path,monkeypatch):
    factory=database
    with factory() as conn:
        conn.execute('TRUNCATE rvbbit.calliope_contacts,rvbbit.calliope_sessions,rvbbit.calliope_turns,rvbbit.calliope_specialist_runs,rvbbit.calliope_surfaces,rvbbit.calliope_mailboxes CASCADE')
        session,turn=mail.uid(),mail.uid()
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(session,OWNER))
        conn.execute('INSERT INTO rvbbit.calliope_turns(id) VALUES(%s)',(turn,))
    config=SimpleNamespace(file_root=tmp_path)
    def publish(factory,session,turn,items):
        with factory() as conn:
            result=[]
            for item in items:
                ident=mail.uid()
                conn.execute('INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                             (ident,session,turn,item['kind'],item['title'],item['lineage_key'],mail.dump(item['payload'])))
                result.append({'id':ident})
            return result
    monkeypatch.setitem(sys.modules,'calliope',SimpleNamespace(_insert_surfaces=publish,
        CalliopeConfig=SimpleNamespace(from_env=lambda:config)))
    return factory,config,{'session_id':session,'turn_id':turn}


def prepared(env,recipients=None):
    factory,config,dest=env
    return mail.prepare(factory,dest,OWNER,'Weekly branch review',recipients or [{'email':'joe@example.com','name':'Joe'}],'weekly-1')


def ready(env,batch=None,index=0):
    factory,config,_=env
    batch=batch or prepared(env)
    draft=batch['drafts'][index]
    mail.save_draft(factory,config,OWNER,str(batch['id']),str(draft['id']),
                    {'revision':draft['revision'],'context_token':draft['context_token'],'subject':'This week','body':'Joe, please check the missing plate photo.'},agent=True)
    return mail.read_batch(factory,OWNER,str(batch['id']))


def connect(env):
    with env[0]() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_mailboxes(owner_email,address,settings,credential_ref) VALUES(%s,'callie@example.com','{}','test-ref')",(OWNER,))


def test_contact_identity_context_and_cross_owner_isolation(env):
    factory,_,_=env
    person=mail.save_contact(factory,OWNER,{'name':'Joe','role':'Warranty returns','note':'Prefers a short checklist.',
        'channels':[{'channel':'email','address':'joe@example.com'},{'channel':'sms','address':'+13215550123'}]})
    batch=prepared(env)
    ctx=batch['drafts'][0]['context']
    assert ctx['person']['id']==person['person']['id']
    assert len(ctx['person']['channels'])==2
    assert ctx['notes'][0]['body']=='Prefers a short checklist.'
    with pytest.raises(LookupError):mail.read_context(factory,'other@example.com',str(person['person']['id']))
    with pytest.raises(ValueError,match='already belongs'):
        mail.save_contact(factory,OWNER,{'name':'Different Joe','channels':[{'channel':'sms','address':'+13215550123'}]})
    assert len(mail.find_contacts(factory,OWNER,'Joe'))==1
    with pytest.raises(ValueError,match='does not exist'):
        mail.save_contact(factory,OWNER,{'name':'Client','principal_email':'not-a-user@example.com'})


def test_preparation_retries_and_context_required(env):
    factory,config,_=env
    batch=prepared(env);again=prepared(env)
    assert batch['id']==again['id'] and batch['surface_id']==again['surface_id']
    d=batch['drafts'][0]
    with pytest.raises(ValueError,match='Read this email'):
        mail.save_draft(factory,config,OWNER,str(batch['id']),str(d['id']),{'revision':0,'subject':'Hi','body':'Hi','context_token':d['context_token']},agent=True)
    batch=ready(env,again)
    assert batch['drafts'][0]['revision']==1
    with pytest.raises(ValueError,match='changed'):
        mail.save_draft(factory,config,OWNER,str(batch['id']),str(d['id']),{'revision':0,'subject':'Stale','body':'x'})
    with pytest.raises(LookupError):mail.read_batch(factory,'other@example.com',str(batch['id']))


@pytest.mark.parametrize('specialist', [False, True])
def test_registered_draft_tools_resolve_chat_inside_logged_scope(env, monkeypatch, specialist):
    import correspondence_routes
    factory,config,dest=env
    with factory() as conn:
        conn.execute("UPDATE rvbbit.calliope_sessions SET hermes_session_id='main-chat'")
        conn.execute("UPDATE rvbbit.calliope_turns SET session_id=%s,status=%s,author_email=%s",
                     (dest['session_id'],'complete' if specialist else 'running',OWNER))
        if specialist:
            conn.execute("INSERT INTO rvbbit.calliope_specialist_runs VALUES('specialist',%s,%s,%s,'running')",
                         (OWNER,dest['session_id'],dest['turn_id']))
    request_auth=server.ApplicationAuthorizationContext(actor='hermes',subject=OWNER,
        attributed_subject=OWNER,client_id='hermes-service',mode='delegated',assurance='verified',
        session_ref='specialist' if specialist else 'main-chat')
    monkeypatch.setattr(server,'_application_authorization_context',lambda:request_auth)
    monkeypatch.setattr(server,'_conn',factory)
    receipts=[]
    monkeypatch.setattr(server,'_record',lambda *args:receipts.append(args))
    tools={}
    def register(name):
        def add(fn):tools[name]=fn;return fn
        return add
    correspondence_routes.register_tools(SimpleNamespace(tool=register),factory,
        lambda:server._application_authorization_context().subject,server._image_stage_destination,server._logged)
    # No pre-seeded ContextVar: this is how FastMCP enters the public tool.
    assert server._AUTHORIZATION_CONTEXT.get() is None
    prepare=lambda:tools['prepare_calliope_email']('Weekly review',[{'email':'joe@example.com'}],'registered-test')
    batch=prepare()
    assert 'error' not in batch, batch
    assert str(batch['session_id'])==dest['session_id']
    assert str(batch['turn_id'])==dest['turn_id']
    retried=prepare()
    assert batch['surface_id'] and retried['id']==batch['id']
    batch=retried
    draft=batch['drafts'][0]
    saved=tools['save_calliope_email_draft'](str(batch['id']),str(draft['id']),draft['revision'],
        draft['context_token'],'Weekly review','Joe, please check the missing plate photo.')
    assert 'error' not in saved, saved
    assert mail.read_batch(factory,OWNER,str(batch['id']))['drafts'][0]['body'].startswith('Joe,')
    assert all(r[3] is None for r in receipts)
    assert server._AUTHORIZATION_CONTEXT.get() is None

    # A missing/untrusted destination must fail within the same logged scope,
    # without choosing an unrelated recent chat or creating an extra card.
    from dataclasses import replace
    for invalid in (replace(request_auth,session_ref='other-chat'),
                    replace(request_auth,client_id='static-key'),
                    replace(request_auth,subject='someone-else@example.com')):
        request_auth=invalid
        assert 'error' in prepare()
        assert receipts[-1][3] is not None
        assert server._AUTHORIZATION_CONTEXT.get() is None
    with factory() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_mail_batches').fetchone()['n']==1


def test_batch_review_is_atomic_and_retries_do_not_resend(env):
    factory,_,_=env
    batch=prepared(env,[{'email':'joe@example.com'},{'email':'ann@example.com'}])
    ready(env,batch,0);ready(env,batch,1)
    batch=mail.read_batch(factory,OWNER,str(batch['id']))
    selections=[{'id':str(d['id']),'revision':d['revision']} for d in batch['drafts']]
    with pytest.raises(ValueError,match='Connect'):mail.queue_send(factory,OWNER,str(batch['id']),selections)
    connect(env)
    with pytest.raises(ValueError,match='changed'):
        mail.queue_send(factory,OWNER,str(batch['id']),[selections[0],dict(selections[1],revision=0)])
    with factory() as conn:assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_mail_outbox').fetchone()['n']==0
    mail.hold(factory,OWNER,str(batch['id']),selections[1]['id'],1,True)
    with pytest.raises(ValueError,match='Held'):mail.queue_send(factory,OWNER,str(batch['id']),selections)
    mail.queue_send(factory,OWNER,str(batch['id']),selections[:1]);mail.queue_send(factory,OWNER,str(batch['id']),selections[:1])
    with factory() as conn:assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_mail_outbox').fetchone()['n']==1


def test_delivery_and_reply_route_to_exact_conversation(env,monkeypatch,tmp_path):
    factory,config,dest=env
    batch=ready(env);connect(env);draft=batch['drafts'][0]
    mail.queue_send(factory,OWNER,str(batch['id']),[{'id':str(draft['id']),'revision':1}])
    monkeypatch.setattr(mail,'transport_account',lambda c,o:{'address':'callie@example.com'})
    sent=[]
    class SMTP:
        def sendmail(self,sender,to,raw):sent.append(raw);return {}
        def close(self):pass
    monkeypatch.setattr(bridge,'_smtp',lambda a:SMTP())
    def transport(action,payload):return bridge.send(payload,spool=tmp_path/'spool.db')
    assert mail.deliver_one(factory,transport)
    assert not mail.deliver_one(factory,transport)
    assert len(sent)==1
    with factory() as conn:outgoing=conn.execute('SELECT * FROM rvbbit.calliope_mail_messages').fetchone()
    reply=EmailMessage();reply['From']='Joe <joe@example.com>';reply['To']='callie@example.com';reply['Subject']='Re: This week'
    reply['Message-ID']='<joes-reply@example.com>';reply['In-Reply-To']=outgoing['message_id'];reply.set_content('I attached a clearer plate. Can we submit it?')
    reply.add_attachment(b'fake-jpeg',maintype='image',subtype='jpeg',filename='plate.jpg')
    incoming=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.ingest(factory,config,OWNER,reply.as_bytes()) is None
    result=mail.read_message(factory,OWNER,incoming)
    assert result['batch_id']==batch['id'] and result['conversation_id']==outgoing['conversation_id']
    assert result['attachments'][0]['name']=='plate.jpg' and 'storage_path' not in result['attachments'][0]
    assert len(result['contact_context']['recent_messages'])==2
    with factory() as conn:
        notification=conn.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchone()
    assert str(notification['to_session_id'])==dest['session_id']
    assert notification['payload']['external_content'] is True and notification['status']=='queued'
    response=mail.prepare(factory,dest,OWNER,'Reply to Joe',[{'email':'joe@example.com'}],'reply-1',incoming)
    assert response['drafts'][0]['conversation_id']==outgoing['conversation_id']
    assert response['drafts'][0]['reply_message_id']=='<joes-reply@example.com>'
    # Same person, different subject: do not leak into the latest active batch.
    reply.replace_header('Message-ID','<unrelated@example.com>');del reply['In-Reply-To']
    unrelated=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.read_message(factory,OWNER,unrelated)['batch_id'] is None


@pytest.mark.parametrize('recipient,sender,imap_host,has_reference,expected', [
    ('pat+east@company.example', 'pat@company.example', 'imap.gmail.com', True, True),
    ('pat+east@company.example', 'pat+east@company.example', 'imap.example.com', True, True),
    ('pat+east@gmail.com', 'pat@gmail.com', 'imap.example.com', True, True),
    ('pat+east@googlemail.com', 'pat@googlemail.com', 'imap.example.com', True, True),
    ('pat+east@company.example', 'pat@company.example', 'imap.example.com', True, False),
    ('pat+east@company.example', 'pat@company.example', 'imap.gmail.com.evil.example', True, False),
    ('pat+east@other-workspace.example', 'pat@other-workspace.example', 'imap.gmail.com', True, False),
    ('pat+east@company.example', 'pat@company.example', 'imap.gmail.com', False, False),
    ('pat+east@company.example', 'pat+west@company.example', 'imap.gmail.com', True, False),
    ('pat@company.example', 'pat+east@company.example', 'imap.gmail.com', True, False),
    ('pat+east@company.example', 'other@company.example', 'imap.gmail.com', True, False),
    ('pat+east@company.example', 'pat@gmail.com', 'imap.gmail.com', True, False),
])
def test_tagged_reply_requires_referenced_correspondent(env,monkeypatch,recipient,sender,imap_host,has_reference,expected):
    factory,config,dest=env
    connect(env)
    with factory() as conn:
        conn.execute('UPDATE rvbbit.calliope_mailboxes SET address=%s,settings=%s::jsonb',
                     ('callie@company.example',mail.dump({'imap_host':imap_host})))
    batch=ready(env,prepared(env,[{'email':recipient,'name':'Riverside contractor'}]))
    draft=batch['drafts'][0]
    mail.queue_send(factory,OWNER,str(batch['id']),[{'id':str(draft['id']),'revision':1}])
    monkeypatch.setattr(mail,'transport_account',lambda c,o:{})
    mail.deliver_one(factory,lambda action,payload:{'state':'sent'})
    with factory() as conn:
        outgoing=conn.execute("SELECT * FROM rvbbit.calliope_mail_messages WHERE direction='outgoing'").fetchone()
    reply=EmailMessage();reply['From']=sender;reply['To']='callie@company.example'
    reply['Subject']='Re: This week';reply['Message-ID']='<alias-reply@example.com>'
    if has_reference:reply['In-Reply-To']=outgoing['message_id']
    reply.set_content('Please change the quantity to six.')
    incoming=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.ingest(factory,config,OWNER,reply.as_bytes()) is None
    result=mail.read_message(factory,OWNER,incoming)
    with factory() as conn:
        events=conn.execute("SELECT * FROM rvbbit.calliope_agent_messages WHERE from_label='Email'").fetchall()
    if expected:
        assert result['batch_id']==batch['id'] and result['contact_id']==draft['contact_id']
        assert result['conversation_id']==outgoing['conversation_id']
        assert len(events)==1 and str(events[0]['to_session_id'])==dest['session_id']
    else:
        assert result['batch_id'] is None and not events


def test_alias_conversations_preserve_separate_contacts_through_reply_drafts(env,monkeypatch):
    factory,config,dest=env;connect(env)
    monkeypatch.setattr(mail,'transport_account',lambda c,o:{})
    with factory() as conn:
        conn.execute("UPDATE rvbbit.calliope_mailboxes SET address='callie@company.example',settings=%s::jsonb",
                     (mail.dump({'imap_host':'imap.gmail.com'}),))
        second={'session_id':mail.uid(),'turn_id':mail.uid()}
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)',(second['session_id'],OWNER))
        conn.execute('INSERT INTO rvbbit.calliope_turns(id) VALUES(%s)',(second['turn_id'],))

    def send(address,name,destination):
        batch=mail.prepare(factory,destination,OWNER,'Order offer',[{'email':address,'name':name}],'offer-1')
        batch=ready(env,batch);draft=batch['drafts'][0]
        mail.queue_send(factory,OWNER,str(batch['id']),[{'id':str(draft['id']),'revision':1}])
        mail.deliver_one(factory,lambda action,payload:{'state':'sent'})
        with factory() as conn:
            message=conn.execute("SELECT * FROM rvbbit.calliope_mail_messages WHERE batch_id=%s AND direction='outgoing'",(batch['id'],)).fetchone()
        return batch,message

    riverside,first=send('pat+east@company.example','Riverside contractor',dest)
    lakeside,second_message=send('pat+west@company.example','Lakeside contractor',second)
    # These messages share a real mailbox, not a business identity.
    reply=EmailMessage();reply['From']='pat@company.example';reply['To']='callie@company.example'
    reply['Subject']='Re: Order offer';reply.set_content('Please change the quantity to six.')
    incoming_ids=[]
    for label,message in [('riverside',first),('lakeside',second_message)]:
        if 'Message-ID' in reply:del reply['Message-ID'];del reply['In-Reply-To']
        reply['Message-ID']=f'<{label}-reply@example.com>';reply['In-Reply-To']=message['message_id']
        incoming_ids.append(mail.ingest(factory,config,OWNER,reply.as_bytes()))
    with factory() as conn:
        events=conn.execute("SELECT * FROM rvbbit.calliope_agent_messages WHERE from_label='Email'").fetchall()
    assert {str(e['id']):str(e['to_session_id']) for e in events}==dict(zip(incoming_ids,[dest['session_id'],second['session_id']]))

    # Unrelated base-address mail must not be adopted by whichever alias next
    # prepares a reply, even if the base address has its own explicit contact.
    del reply['In-Reply-To'];reply.replace_header('Message-ID','<unrelated-base@example.com>')
    unrelated=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.read_message(factory,OWNER,unrelated)['contact_id'] is None
    for incoming,batch,destination in zip(incoming_ids,[riverside,lakeside],[dest,second]):
        response=mail.prepare(factory,destination,OWNER,'Re: Order offer',[{'email':'pat@company.example'}],'response-1',incoming)
        draft=response['drafts'][0]
        original=batch['drafts'][0]
        assert draft['recipient']=='pat@company.example'
        assert draft['contact_id']==original['contact_id']
        assert draft['conversation_id']==original['conversation_id']
        assert draft['context']['person']['name']==original['context']['person']['name']
        assert len(draft['context']['recent_messages'])==2
        assert mail.read_message(factory,OWNER,unrelated)['contact_id'] is None
    base=mail.save_contact(factory,OWNER,{'name':'Ryan','channels':[{'channel':'email','address':'pat@company.example'}]})
    response=mail.prepare(factory,dest,OWNER,'Another response',[{'email':'pat@company.example'}],'response-2',incoming_ids[0])
    assert response['drafts'][0]['contact_id']==riverside['drafts'][0]['contact_id']
    assert mail.read_message(factory,OWNER,unrelated)['contact_id']==base['person']['id']
    with factory() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_contacts').fetchone()['n']==3
        assert conn.execute("SELECT contact_id FROM rvbbit.calliope_contact_channels WHERE address='pat@company.example'").fetchone()['contact_id']==base['person']['id']


def test_reply_headers_take_precedence_over_arrival_time_and_other_owners(env):
    factory,config,dest=env;connect(env)
    batch=prepared(env);contact=batch['drafts'][0]['contact_id']
    with factory() as conn:
        for mid,owner,sender in [('<parent@example.com>',OWNER,'joe@example.com'),
                                 ('<older-ancestor@example.com>',OWNER,'joe@example.com'),
                                 ('<foreign@example.com>','other@example.com','joe@example.com')]:
            conn.execute('INSERT INTO rvbbit.calliope_mail_messages(id,owner_email,contact_id,batch_id,conversation_id,direction,message_id,sender,recipient,subject,body) '
                "VALUES(%s,%s,%s,%s,%s,'incoming',%s,%s,'callie@example.com','Reply','Original')",
                (mail.uid(),owner,contact,batch['id'] if mid=='<parent@example.com>' else None,mail.uid(),mid,sender))
    reply=EmailMessage();reply['From']='joe@example.com';reply['Subject']='Re: Offer'
    reply['Message-ID']='<new-reply@example.com>';reply['References']='<older-ancestor@example.com>'
    reply['In-Reply-To']='<parent@example.com>';reply.set_content('Thank you.')
    incoming=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.read_message(factory,OWNER,incoming)['batch_id']==batch['id']
    del reply['References'];reply.replace_header('In-Reply-To','<foreign@example.com>')
    reply.replace_header('Message-ID','<other-owner-reply@example.com>')
    incoming=mail.ingest(factory,config,OWNER,reply.as_bytes())
    assert mail.read_message(factory,OWNER,incoming)['batch_id'] is None


def test_frozen_attachments_and_mime_are_stable(env,tmp_path):
    factory,config,dest=env
    batch=prepared(env);draft=batch['drafts'][0]
    files=config.file_root/'files';files.mkdir();source=files/'one-sheet.pdf';source.write_bytes(b'%PDF-original')
    ref=mail.uid()
    with factory() as conn:conn.execute('INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
        (ref,dest['session_id'],dest['turn_id'],'document','One sheet','pdf:1',mail.dump({'storage_path':str(source),'original_name':'one-sheet.pdf'})))
    mail.save_draft(factory,config,OWNER,str(batch['id']),str(draft['id']),{'revision':0,'subject':'Brief','body':'Attached','attachment_refs':[ref]})
    source.write_bytes(b'%PDF-changed');connect(env)
    mail.queue_send(factory,OWNER,str(batch['id']),[{'id':str(draft['id']),'revision':1}])
    with factory() as conn:out=conn.execute('SELECT * FROM rvbbit.calliope_mail_outbox').fetchone()
    raw=mail.mime_message(out['snapshot'],out['message_id'])
    assert raw==mail.mime_message(out['snapshot'],out['message_id'])
    assert b'JVBERi1vcmlnaW5hbA==' in raw
    Path(out['snapshot']['attachments'][0]['storage_path']).write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='changed after review'):mail.mime_message(out['snapshot'],out['message_id'])


def test_bridge_uncertain_timeout_is_never_resent(tmp_path,monkeypatch):
    calls=[]
    class SMTP:
        def sendmail(self,*args):calls.append(1);raise TimeoutError()
        def close(self):pass
    monkeypatch.setattr(bridge,'_smtp',lambda _:SMTP())
    raw=mail.mime_message({'sender':'callie@example.com','recipient':'joe@example.com','subject':'Hello','body':'Hi','mail_date':'Sun, 04 Oct 2026 12:00:00 +0000','attachments':[]},'<stable@example.com>')
    body={'account':{'address':'callie@example.com'},'attempt_id':mail.uid(),'mime':base64.b64encode(raw).decode()}
    assert bridge.send(body,spool=tmp_path/'spool')['state']=='uncertain'
    assert bridge.send(body,spool=tmp_path/'spool')['state']=='uncertain'
    assert len(calls)==1


def test_mail_fetch_cursor_never_marks_read_or_skips_restart(monkeypatch):
    seen=[]
    class IMAP:
        def response(self,key):return (key,[b'7' if key=='UIDVALIDITY' else b'14'])
        def uid(self,op,*args):
            seen.append((op,args))
            if op=='search':return 'OK',[b'11 12 13']
            return 'OK',[(b'BODY[]',b'From: joe@example.com\r\n\r\nhello')]
        def logout(self):pass
    monkeypatch.setattr(bridge,'_imap',lambda _:IMAP())
    result=bridge.poll({'account':{},'cursor':{'uidvalidity':7,'last_uid':11}})
    assert [m['uid'] for m in result['messages']]==[12,13]
    assert result['cursor']=={'uidvalidity':7,'last_uid':13}
    assert all(op!='store' for op,args in seen)
    assert all('BODY.PEEK[]' in args[1] for op,args in seen if op=='fetch')


def test_migration_runtime_schema_match():
    assert (ROOT/'services/calliope/correspondence.sql').read_text()==(ROOT/'crates/pg_rvbbit/sql/migrations/0465_reviewed_correspondence.sql').read_text()


def test_html_only_incoming_mail_uses_shipped_dependencies(env):
    factory,config,_=env;connect(env)
    incoming=EmailMessage();incoming['From']='joe@example.com';incoming['Subject']='Plate photos'
    incoming.set_content('<style>hidden CSS</style><p>Here &amp; ready.</p><p>Next step?</p><script>hidden JS</script>',subtype='html')
    ident=mail.ingest(factory,config,OWNER,incoming.as_bytes())
    assert mail.read_message(factory,OWNER,ident)['body']=='Here & ready.\nNext step?'


def test_new_recipient_recovers_prior_mail_and_bad_sender_does_not_wedge_inbox(env,monkeypatch):
    factory,config,dest=env;connect(env)
    incoming=EmailMessage();incoming['From']='Joe <joe@example.com>';incoming['Subject']='Missing plate'
    incoming['Message-ID']='<first-contact@example.com>';incoming.set_content('Use my Riverside branch address.')
    first=mail.ingest(factory,config,OWNER,incoming.as_bytes())
    assert mail.read_message(factory,OWNER,first)['contact_id'] is None
    batch=prepared(env)
    assert str(batch['drafts'][0]['context']['recent_messages'][0]['id'])==first
    broken=b'Subject: malformed\r\nMessage-ID: <broken@example.com>\r\n\r\nbody\x00'
    monkeypatch.setattr(mail,'transport_account',lambda c,o:{})
    mail.sync_inbox(factory,config,OWNER,lambda a,p:{'ok':True,'cursor':{'last_uid':5,'uidvalidity':1},
        'messages':[{'raw':base64.b64encode(broken).decode()}]})
    with factory() as conn:
        assert conn.execute('SELECT cursor FROM rvbbit.calliope_mailboxes').fetchone()['cursor']['last_uid']==5
        assert conn.execute("SELECT sender FROM rvbbit.calliope_mail_messages WHERE message_id='<broken@example.com>'").fetchone()['sender']=='(unrecognized sender)'


def test_chat_deletion_preserves_correspondence_without_blocking_cleanup(env):
    factory,config,dest=env;batch=ready(env)
    with factory() as conn:
        conn.execute('DELETE FROM rvbbit.calliope_surfaces WHERE id=%s',(batch['surface_id'],))
        conn.execute('DELETE FROM rvbbit.calliope_turns WHERE id=%s',(dest['turn_id'],))
        conn.execute('DELETE FROM rvbbit.calliope_sessions WHERE id=%s',(dest['session_id'],))
    retained=mail.read_batch(factory,OWNER,str(batch['id']))
    assert retained['session_id'] is None and retained['surface_id'] is None and retained['turn_id'] is None
    assert retained['drafts'][0]['body']==batch['drafts'][0]['body']


def test_contact_summary_uses_notes_and_actual_mail_not_drafts(env,monkeypatch):
    factory,config,_=env
    person=mail.save_contact(factory,OWNER,{'name':'Joe','note':'Send short checklists.',
        'channels':[{'channel':'email','address':'joe@example.com'}]})
    batch=ready(env)
    captured=[]
    def generate(config,phase,instructions,packet):
        captured.append(packet)
        return {'summary':'Joe prefers short checklists.'},{'model':'test-only'}
    monkeypatch.setitem(sys.modules,'calliope_dreams',SimpleNamespace(_generate=generate))
    mail.refresh_contact_summary(factory,config)
    assert captured[0]['notes'][0]['body']=='Send short checklists.'
    assert captured[0]['recent_messages']==[]
    assert mail.read_context(factory,OWNER,str(person['person']['id']))['person']['summary']=='Joe prefers short checklists.'
    connect(env)
    mail.queue_send(factory,OWNER,str(batch['id']),[{'id':str(batch['drafts'][0]['id']),'revision':1}])
    before=mail.read_batch(factory,OWNER,str(batch['id']))['drafts'][0]['context']
    mail.save_contact(factory,OWNER,{'id':str(person['person']['id']),'note':'New note after approval.'})
    assert mail.read_batch(factory,OWNER,str(batch['id']),agent=True)['drafts'][0]['context']==before


def test_http_approval_and_mcp_preparation_have_separate_authority(env,monkeypatch):
    import json
    import correspondence_routes
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route
    from starlette.testclient import TestClient
    factory,config,dest=env;batch=ready(env);connect(env)
    class Registry:
        def __init__(self):self.routes=[];self.tools={}
        def custom_route(self,path,methods):
            def register(fn):self.routes.append(Route(path,fn,methods=methods));return fn
            return register
        def tool(self,name):
            def register(fn):self.tools[name]=fn;return fn
            return register
    registry=Registry()
    def response(data,status=200):return Response(json.dumps(data,default=str),status_code=status,media_type='application/json')
    def owner(request):return (request.headers.get('x-test-owner'),None) if request.headers.get('x-test-owner') else (None,response({},401))
    monkeypatch.setattr(mail,'start_worker',lambda *a:None)
    correspondence_routes.register(registry,factory,config,owner,response)
    correspondence_routes.register_tools(registry,factory,lambda:None,lambda o:dest,lambda n,a,fn:fn())
    assert not any('send' in name or 'approve' in name for name in registry.tools)
    with pytest.raises(PermissionError):registry.tools['list_calliope_email']()
    with TestClient(Starlette(routes=registry.routes)) as client:
        path=f'/api/calliope/mail/batches/{batch["id"]}'
        assert client.get(path).status_code==401
        assert client.get(path,headers={'X-Test-Owner':'someone-else@example.com'}).status_code==404
        headers={'X-Test-Owner':OWNER,'X-Calliope-Mail':'1'}
        assert client.get(path,headers=headers).json()['drafts'][0]['body'].startswith('Joe,')
        selections={'drafts':[{'id':str(batch['drafts'][0]['id']),'revision':1}]}
        assert client.post(path+'/send',headers={'X-Test-Owner':OWNER},json=selections).status_code==403
        assert client.post(path+'/send',headers=dict(headers,Origin='https://elsewhere.example'),json=selections).status_code==403
        queued=client.post(path+'/send',headers=headers,json=selections)
        assert queued.status_code==200 and queued.json()['drafts'][0]['status']=='queued'


def test_hermes_control_round_trip_is_authenticated_and_idempotent(tmp_path,monkeypatch):
    import httpx
    import threading
    import hermes_profiles
    from http.server import ThreadingHTTPServer
    monkeypatch.setenv('HERMES_HOME',str(tmp_path/'hermes'))
    original_spec=importlib.util.spec_from_file_location
    def local_spec(name,path,*args,**kwargs):
        if str(path)=='/opt/hermes/calliope/reconcile-hermes-config.py':
            path=ROOT/'docker/calliope/reconcile-hermes-config.py'
        return original_spec(name,path,*args,**kwargs)
    monkeypatch.setattr(importlib.util,'spec_from_file_location',local_spec)
    spec=importlib.util.spec_from_file_location('reviewed_mail_test_supervisor',ROOT/'docker/calliope/hermes-gateway-supervisor.py')
    supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
    monkeypatch.setattr(supervisor,'CONTROL_KEY','fixture-control-key')
    monkeypatch.setitem(sys.modules,'reviewed_mail_bridge',bridge)
    delivered=[]
    class SMTP:
        def sendmail(self,sender,recipients,raw):delivered.append((sender,recipients,raw));return {}
        def close(self):pass
    monkeypatch.setattr(bridge,'_smtp',lambda a:SMTP())
    http=ThreadingHTTPServer(('127.0.0.1',0),supervisor.Handler)
    worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
    monkeypatch.setattr(hermes_profiles,'_CONTROL_URL',f'http://127.0.0.1:{http.server_port}')
    monkeypatch.setattr(hermes_profiles,'_CONTROL_KEY','fixture-control-key')
    try:
        raw=mail.mime_message({'sender':'callie@example.com','recipient':'joe@example.com','subject':'Reviewed subject',
            'body':'The exact reviewed text.','mail_date':'Sun, 04 Oct 2026 12:00:00 +0000','attachments':[]},'<local-only@example.com>')
        payload={'account':{'address':'callie@example.com'},'attempt_id':mail.uid(),'mime':base64.b64encode(raw).decode()}
        assert httpx.post(hermes_profiles._CONTROL_URL+'/v1/reviewed-mail/send',json=payload).status_code==401
        assert mail.control('send',payload)['state']=='sent'
        assert mail.control('send',payload)['state']=='sent'
        assert len(delivered)==1 and delivered[0][2]==raw
    finally:
        http.shutdown();http.server_close();worker.join(timeout=3)
