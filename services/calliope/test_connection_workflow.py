"""Connection forms and durable continuation, using a disposable control plane."""
import asyncio
import json
import uuid

import pytest
import calliope
import connection_forms as forms
import connection_workflow as flow
from test_mirror_creation import mirror_db, register_source
from test_workspace_sql import db, OWNER, SESSION
from test_zen_chat_layout import zen_page


def request_payload():
    return {'mode': 'secure_action_request', 'action_id': 'mirror.connect', 'server': 'Sales database',
            'missing_secret_names': ['SOURCE_DSN'], 'form_contract': forms.sql_contract('postgresql'),
            'inputs': {'connection_name': 'source', 'label': 'Sales database', 'dialect': 'postgresql'}}


@pytest.fixture
def card(mirror_db):
    sid = str(uuid.uuid4())
    with mirror_db() as conn:
        conn.execute('CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,kind text,title text,payload jsonb,created_at timestamptz DEFAULT now())')
        conn.execute('CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY,owner_email text,kind text,from_kind text,from_ref text,from_label text,to_session_id uuid,subject text,body text,payload jsonb)')
        conn.execute('INSERT INTO rvbbit.calliope_surfaces(id,session_id,kind,title,payload) VALUES(%s,%s,%s,%s,%s::jsonb)',
                     (sid, SESSION, 'action', 'Sales database', json.dumps(request_payload())))
    return sid


def read(factory, sid):
    with factory() as conn:
        return conn.execute('SELECT payload FROM rvbbit.calliope_surfaces WHERE id=%s', (sid,)).fetchone()['payload']


def test_projected_contract_preserves_conditional_options_and_repair_intent():
    source = {'secure_input_required': True, **request_payload(), 'inputs': {**request_payload()['inputs'], 'replace_credentials': True}}
    output = calliope._project_tool_result('connect_mirror_source', source, {'inputs': request_payload()['inputs']}, 'call')
    payload = output[0]['payload']
    assert payload['inputs']['replace_credentials'] is True
    schema = payload['form_contract']
    assert len(next(f for f in schema['fields'] if f['key'] == 'dialect')['options']) == 7
    assert next(f for f in schema['fields'] if f['key'] == 'ssh_private_key')['when']['transport'] == 'ssh'


def test_sql_recipe_encodes_reserved_characters_and_composes_transport():
    dialect, profile = forms.sql_profile({'dialect': 'postgresql', 'host': '2001:db8::1', 'database': 'company space',
        'username': 'a@b', 'password': 'p@:/?', 'transport': 'ssh', 'ssh_host': 'bastion.example',
        'ssh_username': 'reader', 'ssh_private_key': 'fake-private-key', 'driver_options': '{"sslmode":"require"}'}, 'postgresql')
    assert dialect == 'postgresql'
    assert 'a%40b:p%40%3A%2F%3F@[2001:db8::1]:5432/company%20space?sslmode=require' in profile['dsn']
    assert profile['ssh']['host'] == 'bastion.example'
    assert profile['ssh']['private_key'] == 'fake-private-key'


def test_canonical_mcp_fields_are_used_instead_of_a_fixed_password_box():
    action = {'title': 'Connect service', 'fields': [
        {'key': 'auth_mode', 'type': 'select', 'options': [{'value': 'key', 'label': 'API key'}]},
        {'key': 'secret:TENANT_KEY', 'secret_name': 'TENANT_KEY', 'type': 'secret', 'label': 'Tenant access key', 'visible_when': {'field': 'auth_mode', 'value': 'key'}},
        {'key': 'region', 'label': 'Service region', 'type': 'text'}]}
    contract = forms.contract(action, {'auth_mode': 'key', 'region': 'east'}, ['TENANT_KEY'])
    assert contract['fields'][1]['when'] == {'auth_mode': ['key']}
    assert contract['fields'][1]['required']
    assert contract['fields'][2]['default'] == 'east'


def test_failed_validation_keeps_the_form_and_error(mirror_db, card):
    result, code = asyncio.run(flow.execute(mirror_db, OWNER, 'mirror.connect', {
        'surface_id': card, 'session_id': SESSION, 'inputs': {'secret:SOURCE_DSN': 'not a database URL'}}))
    assert code == 502  # executor wraps its sanitized failure, retaining the durable run
    payload = read(mirror_db, card)
    assert payload['mode'] == 'secure_action_request'
    assert payload['revision'] == 1 and payload['error']
    assert 'not a database URL' not in json.dumps(payload)
    retry, status = asyncio.run(flow.execute(mirror_db, OWNER, 'mirror.connect', {
        'surface_id': card, 'session_id': SESSION, 'revision': 0, 'inputs': {'secret:SOURCE_DSN': 'still invalid'}}))
    assert status == 409 and retry['error']['code'] == 'CONNECTION_CHANGED'


def test_success_is_idempotent_and_resumes_exactly_once(mirror_db, card, monkeypatch):
    register_source(mirror_db)
    async def worker(*a, **kw):
        return {'ok': True}
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', worker)
    body = {'surface_id': card, 'session_id': SESSION, 'inputs': {}}
    first, code = asyncio.run(flow.execute(mirror_db, OWNER, 'mirror.connect', body))
    second, again = asyncio.run(flow.execute(mirror_db, OWNER, 'mirror.connect', body))
    assert code == again == 200 and str(first['run']['id']) == str(second['run']['id'])
    with mirror_db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n'] == 1
        messages = conn.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchall()
    assert len(messages) == 1
    assert messages[0]['payload']['connection_event']['phase'] == 'connected'
    assert 'CONNECTION UPDATE' in calliope._agent_mailbox_prompt_context(messages)


def test_probe_failure_keeps_repair_and_does_not_claim_loaded(mirror_db, card, monkeypatch):
    register_source(mirror_db)
    async def worker(*a, **kw):
        raise RuntimeError('Connection refused at database host')
    monkeypatch.setattr(calliope, '_setup_mirror_worker_request', worker)
    result, status = asyncio.run(flow.execute(mirror_db, OWNER, 'mirror.connect', {'surface_id': card, 'session_id': SESSION, 'inputs': {}}))
    assert status == 200 and result['run']['verification']['source_reachable'] is False
    payload = read(mirror_db, card)
    assert payload['mode'] == 'secure_action_request'
    assert payload['inputs']['replace_credentials'] is True
    with mirror_db() as conn:
        message = conn.execute('SELECT payload FROM rvbbit.calliope_agent_messages').fetchone()['payload']
    assert message['connection_event']['phase'] == 'needs_attention'


def test_cancel_is_durable_and_rejects_foreign_owner(mirror_db, card):
    with pytest.raises(PermissionError):
        flow.cancel(mirror_db, 'other@example.com', card, 0)
    result = flow.cancel(mirror_db, OWNER, card, 0)
    assert result['status'] == 'cancelled'
    assert flow.cancel(mirror_db, OWNER, card, 0)['status'] == 'cancelled'
    with mirror_db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n'] == 0


def test_mirror_progress_and_completion_resume_without_browser(mirror_db, card):
    register_source(mirror_db)
    result = asyncio.run(calliope.administer_action_for_session(mirror_db, SESSION, 'mirror.create', {
        'connection_name': 'source', 'job_name': 'orders_copy', 'source_schema': 'public',
        'destination_schema': 'orders_copy', 'tables': json.dumps([{'source_table': 'orders'}]), 'run_now': True}))
    projected = calliope._project_tool_result('create_mirror_job', result, {}, 'load')
    with mirror_db() as conn:
        conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s', (json.dumps(projected[0]['payload'], default=str), card))
    flow.sync_pending(mirror_db)
    assert read(mirror_db, card)['mirror']['status'] == 'queued'
    with mirror_db() as conn:
        conn.execute("UPDATE rvbbit.mirror_runs SET status='succeeded',tables_succeeded=1,rows_loaded=12 WHERE run_id=%s", (result['run']['result']['queued_run_id'],))
    flow.sync_pending(mirror_db)
    flow.sync_pending(mirror_db)
    payload = read(mirror_db, card)
    assert payload['mirror']['rows_loaded'] == 12 and payload['connection_tracking'] is False
    with mirror_db() as conn:
        messages = conn.execute('SELECT payload FROM rvbbit.calliope_agent_messages').fetchall()
    assert len(messages) == 1 and messages[0]['payload']['connection_event']['phase'] == 'loaded'


def test_oauth_completion_refreshes_receipt_and_resumes_once(mirror_db, card):
    run = calliope.plan_action(mirror_db, OWNER, 'mirror.connect', request_payload()['inputs'], SESSION)
    snapshot = {**run['action_snapshot'], 'id': 'mcp.custom.connect', 'executor': 'mcp_connect'}
    pending = {'mode': 'action_receipt', 'connection_tracking': True, 'status': 'awaiting_authorization',
               'run': {'id': str(run['id']), 'status': 'running'},
               'connection_request': {**request_payload(), 'action_id': 'mcp.custom.connect'}}
    with mirror_db() as conn:
        conn.execute("UPDATE rvbbit.calliope_action_runs SET status='running',action_snapshot=%s::jsonb WHERE id=%s", (json.dumps(snapshot), run['id']))
        conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s', (json.dumps(pending), card))
    flow.sync_pending(mirror_db)
    assert read(mirror_db, card)['status'] == 'awaiting_authorization'
    calliope._finish_action_run(mirror_db, str(run['id']), 'complete', verification={'reachable': True})
    flow.sync_pending(mirror_db)
    flow.sync_pending(mirror_db)
    assert read(mirror_db, card)['run']['status'] == 'complete'
    assert read(mirror_db, card)['connection_tracking'] is False
    with mirror_db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 1


@pytest.mark.parametrize('width', [1500, 880, 390])
def test_native_sql_card_conditions_secure_submit_and_error_repair(zen_page, width):
    page = zen_page
    page.set_viewport_size({'width': width, 'height': 1000})
    posted = []
    payload = request_payload()
    def execute(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=400, json={'error': {'message': 'Check the database name.'},
            'surface_payload': {**payload, 'revision': 1, 'error': 'Check the database name.',
                'last_attempt': {'run': {'verification': {'credential_configured': True}}}}})
    page.route('**/api/calliope/actions/mirror.connect/execute', execute)
    page.evaluate('''payload => {
      qa.state.surfaces=[{id:'connection',turn_id:'one',kind:'action',title:'Sales',presentation:{phase:'exploration'},payload}];
      qa.setupEvents();qa.renderChat(true);qa.renderStage();
      document.body.classList.remove('mobile-chat-open');
    }''', payload)
    form = page.locator('[data-stage-secure-action-form]')
    assert form.is_visible()
    assert form.locator('[name=ssh_host]').is_hidden()
    form.locator('[name=host]').fill('db.example')
    form.locator('[name=database]').fill('company')
    form.locator('[name=username]').fill('reader')
    form.locator('[name=password]').fill('native-secret-canary')
    form.locator('[name=transport]').select_option('ssh')
    assert form.locator('[name=ssh_host]').is_visible()
    form.locator('[name=ssh_host]').fill('ssh.example')
    form.locator('[name=ssh_username]').fill('reader')
    form.locator('[name=ssh_private_key]').fill('key-canary')
    form.locator('[name=transport]').select_option('direct')
    assert form.locator('[name=ssh_private_key]').input_value() == ''
    page.evaluate("qa.submitStageSecureAction(document.querySelector('[data-stage-secure-action-form]'))")
    assert len(posted) == 1
    submitted = json.loads(posted[0]['inputs']['secret:CONNECTION_INPUTS'])
    assert submitted['password'] == 'native-secret-canary' and 'ssh_private_key' not in submitted
    assert 'native-secret-canary' not in page.evaluate('JSON.stringify(qa.state.surfaces)')
    assert page.locator('[data-stage-secure-action-form]').count() == 1
    assert 'Check the database name.' in form.inner_text()
    assert form.locator('[name=password]').input_value() == ''
    assert form.get_by_role('button', name='Test again', exact=True).count() == 1
    assert form.get_by_role('button', name='Update & test', exact=True).count() == 1


def test_oauth_waiting_renders_signin_without_success_claim(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'connection',turn_id:'one',kind:'action',title:'Company service',presentation:{phase:'exploration'},payload:{
        mode:'action_receipt',status:'awaiting_authorization',connection_tracking:true,
        run:{id:'pending',status:'running',action_snapshot:{id:'mcp.custom.connect',executor:'mcp_connect',title:'Company service'},steps:[
          {id:'authorize',status:'running',result:{authorization_url:'https://example.test/authorize'}}]}}}];
      qa.renderChat(true);qa.renderStage();
    }''')
    assert page.locator('a[href="https://example.test/authorize"]').count() == 1
    assert 'Waiting for sign-in' in page.locator('#stage').inner_text()
    assert 'connected and tested' not in page.locator('body').inner_text()


def test_custom_mcp_handoff_preserves_details_and_expands_declared_secret_group():
    action = next(a for a in calliope._ACTION_LIBRARY_SEED if a['id'] == 'mcp.connect_custom')
    values = {'server_name': 'company_tools', 'transport': 'http', 'url': 'http://fixture/mcp',
              'auth_mode': 'bearer', 'auth_token_name': 'SERVICE_KEY', 'timeout_ms': '30000'}
    result = calliope._secure_action_handoff(action, 'company_tools', ['SERVICE_KEY'], values)
    projected = calliope._project_tool_result('administer_calliope_action', result, {'action_id': action['id']}, 'connect')
    spec = projected[0]['payload']['form_contract']
    assert spec['values']['url'] == values['url']
    assert spec['values']['auth_token_name'] == 'SERVICE_KEY'
    secret = next(f for f in spec['fields'] if f['key'] == 'secret:entry:SERVICE_KEY')
    assert secret['required'] and secret['type'] == 'secret'
    assert spec['secret_groups'] == [{'key': 'secret:MCP_SECRET_VALUES', 'members': {'secret:entry:SERVICE_KEY': 'SERVICE_KEY'}}]
    assert not any(f['key'] == 'secret:MCP_SECRET_VALUES' for f in spec['fields'])


def test_mirror_repair_tool_has_a_boolean_schema():
    import server
    assert server._mcp_connect_mirror_source.__annotations__['replace_credentials'] is bool


def test_connection_failure_explains_database_login_without_driver_noise():
    assert flow._public_error({'run': {'verification': {'error': '(psycopg2.OperationalError) FATAL: password authentication failed for user reader'}}}) == 'The database rejected the username or password. Check the database login, then update and test again.'


def test_custom_mcp_card_posts_named_credentials_in_canonical_private_envelope(zen_page):
    page = zen_page
    action = next(a for a in calliope._ACTION_LIBRARY_SEED if a['id'] == 'mcp.connect_custom')
    values = {'server_name': 'company_tools', 'transport': 'http', 'url': 'http://fixture/mcp',
              'auth_mode': 'bearer', 'auth_token_name': 'SERVICE_KEY', 'timeout_ms': '30000'}
    handoff = calliope._secure_action_handoff(action, 'company_tools', ['SERVICE_KEY'], values)
    payload = calliope._project_tool_result('administer_calliope_action', handoff, {}, 'key')[0]['payload']
    posted = []
    def execute(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=400, json={'error': {'message': 'Try again'}, 'surface_payload': {**payload, 'revision': 1, 'error': 'Try again'}})
    page.route('**/api/calliope/actions/mcp.connect_custom/execute', execute)
    page.evaluate('''payload => {qa.state.surfaces=[{id:'mcp',turn_id:'one',kind:'action',title:'Tools',payload}];qa.setupEvents();qa.renderChat(true);qa.renderStage();}''', payload)
    form = page.locator('[data-stage-secure-action-form]')
    form.locator('[name="secret:entry:SERVICE_KEY"]').fill('mcp-private-canary')
    page.evaluate("qa.renderStage()")  # ordinary live refresh must not erase the draft
    assert form.locator('[name="secret:entry:SERVICE_KEY"]').input_value() == 'mcp-private-canary'
    page.evaluate("qa.submitStageSecureAction(document.querySelector('[data-stage-secure-action-form]'))")
    assert len(posted) == 1
    sent = posted[0]['inputs']
    assert json.loads(sent['secret:MCP_SECRET_VALUES']) == {'SERVICE_KEY':'mcp-private-canary'}
    assert sent['url'] == values['url'] and sent['auth_token_name'] == 'SERVICE_KEY'
    assert 'secret:entry:SERVICE_KEY' not in sent
    assert form.locator('[name="secret:entry:SERVICE_KEY"]').input_value() == ''
    assert 'mcp-private-canary' not in page.evaluate('JSON.stringify(qa.state.surfaces)')


def test_observed_fast_copy_does_not_start_a_duplicate_continuation(mirror_db, card):
    register_source(mirror_db)
    result = asyncio.run(calliope.administer_action_for_session(mirror_db, SESSION, 'mirror.create', {
        'connection_name':'source','job_name':'quick_copy','source_schema':'public','destination_schema':'quick_copy',
        'tables':json.dumps([{'source_table':'orders'}]),'run_now':True}))
    run_id = result['run']['result']['queued_run_id']
    turn_id = str(uuid.uuid4())
    with mirror_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text,created_at timestamptz DEFAULT now())')
        conn.execute('ALTER TABLE rvbbit.calliope_agent_messages ADD COLUMN status text DEFAULT \'queued\', ADD COLUMN delivered_at timestamptz, ADD COLUMN delivered_turn_id uuid')
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')", (turn_id,SESSION))
        conn.execute("UPDATE rvbbit.mirror_runs SET status='succeeded',tables_succeeded=1,rows_loaded=3 WHERE run_id=%s", (run_id,))
        payload = calliope._project_tool_result('create_mirror_job', result, {}, 'fast')[0]['payload']
        conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s', (json.dumps(payload,default=str),card))
    flow.acknowledge_mirror_status(mirror_db, SESSION, {'runs':[{'run_id':run_id,'status':'succeeded'}]})
    flow.sync_pending(mirror_db)
    with mirror_db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 0
    assert read(mirror_db,card)['mirror']['rows_loaded'] == 3
