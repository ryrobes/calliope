"""Mailbox onboarding through the real connection workflow, without external mail."""
import asyncio
import json

import pytest
import calliope
import correspondence as mail
import connection_forms
import connection_workflow
from test_correspondence import database, OWNER
from test_reviewed_mail_transport import bridge, transport
from test_zen_chat_layout import zen_page


@pytest.fixture
def mailbox_db(database, monkeypatch):
    with database() as conn:
        conn.execute(calliope._ACTION_DDL)
        conn.execute('TRUNCATE rvbbit.calliope_sessions,rvbbit.calliope_turns,rvbbit.calliope_surfaces,rvbbit.calliope_mailboxes,rvbbit.calliope_action_runs,rvbbit.calliope_agent_messages CASCADE')
        session, turn = mail.uid(), mail.uid()
        conn.execute('INSERT INTO rvbbit.calliope_sessions VALUES(%s,%s)', (session, OWNER))
        conn.execute('INSERT INTO rvbbit.calliope_turns VALUES(%s)', (turn,))
        # Only the secret-store primitive and mail transport are simulated.
        conn.execute("CREATE OR REPLACE FUNCTION rvbbit.put_credential(text,text,text,text,text,jsonb) RETURNS text LANGUAGE sql AS $$SELECT 'mail-test-ref'::text$$")
    monkeypatch.setattr(calliope, '_action_runtime_inventory', lambda *_: {'company_admin': False})
    monkeypatch.setattr(calliope.calliope_dreams, 'is_company_admin', lambda *_: False)
    monkeypatch.setattr(calliope, '_session_for_participant', lambda *_: {'id': session, 'owner_email': OWNER})
    return database, session, turn


def open_card(env, address='callie@example.com'):
    factory, session, turn = env
    result = asyncio.run(calliope.administer_action_for_session(factory, session, 'email.connect', {'address': address}, authorized_owner=OWNER))
    surface = calliope._project_tool_result('administer_calliope_action', result, {'action_id': 'email.connect'}, 'connect')[0]
    sid = mail.uid()
    with factory() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_surfaces VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                     (sid, session, turn, surface['kind'], surface['title'], surface['lineage_key'], json.dumps(surface['payload'])))
    return sid, surface['payload']


def submit(env, sid, **updates):
    return asyncio.run(connection_workflow.execute(env[0], OWNER, 'email.connect', {
        'surface_id': sid, 'session_id': env[1], 'revision': 0,
        'inputs': {'address': 'callie@example.com', 'secret:MAIL_PASSWORD': 'private-mail-canary'}, **updates}))


def test_non_admin_can_discover_and_open_setup_without_drafts(mailbox_db):
    factory, session, _ = mailbox_db
    actions = calliope.search_actions_for_session(factory, session, 'configure email Google mailbox', authorized_owner=OWNER)
    assert actions['actions'][0]['id'] == 'email.connect'
    sid, payload = open_card(mailbox_db)
    assert payload['mode'] == 'secure_action_request'
    assert payload['form_contract']['values']['address'] == 'callie@example.com'
    assert next(f for f in payload['form_contract']['fields'] if f['key'] == 'secret:MAIL_PASSWORD')['type'] == 'secret'
    with factory() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_mail_batches').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n'] == 0
    with pytest.raises(PermissionError):
        calliope._require_direct_administration_action(factory, OWNER, 'mirror.connect')
    with pytest.raises(ValueError, match='Secrets cannot'):
        asyncio.run(calliope.administer_action_for_session(factory, session, 'email.connect', {'secret:MAIL_PASSWORD': 'model-secret'}, authorized_owner=OWNER))


def test_connect_once_saves_and_resumes_same_chat_without_secret_or_send(mailbox_db, monkeypatch):
    factory, session, _ = mailbox_db
    sid, _ = open_card(mailbox_db)
    probes = []
    def control(action, payload):
        assert action == 'probe'
        probes.append(json.loads(json.dumps(payload)))
        return {'ok': True, 'cursor': {'uidvalidity': '1', 'last_uid': 42}}
    monkeypatch.setattr(mail, 'control', control)
    first, status = submit(mailbox_db, sid)
    again, again_status = submit(mailbox_db, sid)
    assert status == again_status == 200
    assert str(first['run']['id']) == str(again['run']['id'])
    assert len(probes) == 1 and probes[0]['account']['password'] == 'private-mail-canary'
    assert first['run']['verification']['smtp_authenticated']
    with factory() as conn:
        account = conn.execute('SELECT * FROM rvbbit.calliope_mailboxes').fetchone()
        events = conn.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchall()
        surfaces = conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchall()
        runs = conn.execute('SELECT * FROM rvbbit.calliope_action_runs').fetchall()
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_mail_outbox').fetchone()['n'] == 0
    assert account['credential_ref'] == 'mail-test-ref' and account['cursor']['last_uid'] == 42
    assert len(events) == 1 and str(events[0]['to_session_id']) == session
    assert 'mailbox passed' in events[0]['body'] and 'choose what to copy' not in events[0]['body']
    context = calliope._agent_mailbox_prompt_context(events)
    assert 'IMAP and SMTP' in context and 'Discover source metadata' not in context
    assert 'private-mail-canary' not in json.dumps([account, events, surfaces, runs], default=str)


def test_failed_probe_retry_and_wrong_owner(mailbox_db, monkeypatch):
    factory, session, _ = mailbox_db
    sid, _ = open_card(mailbox_db)
    monkeypatch.setattr(mail, 'control', lambda *a: {'ok': False, 'message': 'Rejected private-mail-canary'})
    result, status = submit(mailbox_db, sid)
    assert status == 400 and result['surface_payload']['mode'] == 'secure_action_request'
    assert result['surface_payload']['revision'] == 1
    assert 'private-mail-canary' not in json.dumps(result, default=str)
    with factory() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_mailboxes').fetchone()['n'] == 0
    assert submit(mailbox_db, sid)[1] == 409
    with pytest.raises(PermissionError):
        asyncio.run(connection_workflow.execute(factory, 'someone@example.com', 'email.connect', {'surface_id': sid, 'session_id': session}))
    monkeypatch.setattr(mail, 'control', lambda *a: {'ok': True, 'cursor': {}})
    result, status = submit(mailbox_db, sid, revision=1)
    assert status == 200 and result['surface_payload']['mode'] == 'action_receipt'


def test_protocol_failure_saved_in_native_receipt_then_retried(mailbox_db, transport, monkeypatch):
    events, failures = transport
    failures['smtp.login'] = bridge.smtplib.SMTPAuthenticationError(535, b'rejected private-mail-canary')
    monkeypatch.setattr(mail, 'control', lambda action, payload: bridge.probe(payload))
    sid, _ = open_card(mailbox_db)
    result, status = submit(mailbox_db, sid)
    assert status == 400
    run = result['run']
    assert run['verification']['stage'] == 'smtp.login'
    assert run['verification']['checks'] == {'imap_authenticated': True, 'inbox_opened': True, 'smtp_authenticated': False}
    assert 'SMTP server rejected' in result['surface_payload']['error']
    assert 'private-mail-canary' not in json.dumps(result, default=str)
    with mailbox_db[0]() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_mailboxes').fetchone()['n'] == 0
    failures.clear()
    result, status = submit(mailbox_db, sid, revision=1)
    assert status == 200 and result['run']['verification']['smtp_authenticated']


def test_unknown_mailbox_and_cancel(mailbox_db):
    factory, _, _ = mailbox_db
    sid, payload = open_card(mailbox_db, '')
    assert payload['form_contract']['values']['address'] == ''
    assert connection_workflow.cancel(factory, OWNER, sid, 0)['status'] == 'cancelled'
    with factory() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_mailboxes').fetchone()['n'] == 0


def test_email_docs_explain_native_connection_before_upstream_gateway(monkeypatch):
    import server
    monkeypatch.setattr(server, '_logged', lambda name, args, fn: fn())
    monkeypatch.setattr(server.hermes_config, 'docs_search', lambda *a: [{'source_kind': 'upstream'}])
    result = server._mcp_search_hermes_docs('configure Gmail')
    assert result['calliope_setup']['action_id'] == 'email.connect'
    assert result['results'][0]['source_kind'] == 'upstream'
    assert 'calliope_setup' not in server._mcp_search_hermes_docs('compression')


@pytest.mark.parametrize('width', [1500, 880, 390])
def test_native_mailbox_form_secure_submit_and_repair(zen_page, width):
    page = zen_page
    page.set_viewport_size({'width': width, 'height': 1100})
    action = mail.connection_action()
    handoff = calliope._secure_action_handoff(action, 'callie@example.com', ['MAIL_PASSWORD'], {'address': 'callie@example.com'})
    payload = calliope._project_tool_result('administer_calliope_action', handoff, {}, 'mail')[0]['payload']
    posted = []
    def execute(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=400, json={'error': {'message': 'Check the app password.'},
            'surface_payload': {**payload, 'revision': 1, 'error': 'Check the app password.'}})
    page.route('**/api/calliope/actions/email.connect/execute', execute)
    page.evaluate('''payload => {
      qa.state.surfaces=[{id:'mail-connect',turn_id:'one',kind:'action',title:'Connect email',payload}];
      qa.setupEvents();qa.renderChat(true);qa.renderStage();document.body.classList.remove('mobile-chat-open');
    }''', payload)
    form = page.locator('[data-stage-secure-action-form]')
    assert form.is_visible()
    assert form.locator('[name=address]').input_value() == 'callie@example.com'
    assert form.locator('[name=imap_host]').is_hidden()
    secret = form.locator('[name="secret:MAIL_PASSWORD"]')
    secret.fill('private-ui-canary')
    page.evaluate('qa.renderStage()')
    assert secret.input_value() == 'private-ui-canary'
    assert form.evaluate('n=>n.scrollWidth<=n.clientWidth+1')
    form.get_by_role('button', name='Test and connect', exact=True).click()
    page.wait_for_function("qa.state.surfaces[0].payload.revision===1")
    assert len(posted) == 1
    assert posted[0]['inputs']['secret:MAIL_PASSWORD'] == 'private-ui-canary'
    assert posted[0]['inputs']['imap_host'] == 'imap.gmail.com'
    assert secret.input_value() == ''
    assert 'Check the app password.' in form.inner_text()
    assert 'private-ui-canary' not in page.evaluate('JSON.stringify(qa.state.surfaces)')
    form.screenshot(path=f'/tmp/mailbox-setup-{width}.png')
