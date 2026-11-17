"""SMS ingress and delivery as existing Calliope login users.

Phone grants are versioned by immutable binding id. Pending messages/replies can
never cross to a new owner when an administrator reassigns a number.
"""
import asyncio
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from correspondence import clean, dump, email, uid

LOG = logging.getLogger(__name__)
WEBHOOK_PATH = '/webhooks/twilio'
TWI_ML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def simulator_enabled():
    return os.environ.get('WAREHOUSE_CALLIOPE_SMS_SIMULATOR_ENABLED', '').lower() in {'1', 'true', 'yes'}


def transport_account(conn, binding):
    if binding.get('transport') == 'simulator':
        if not simulator_enabled():
            raise PermissionError('SMS simulator is disabled.')
        # Only passed to Hermes' local format_reply operation. No credentials
        # are resolved and the simulator never invokes its network send path.
        return dict.fromkeys(('account_sid', 'auth_token', 'phone_number', 'webhook_url'), '')
    return account(conn)


def ensure(factory):
    with factory() as conn:
        conn.execute(Path(__file__).with_suffix('.sql').read_text())


def phone(value):
    value = clean(value, 16, True)
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
        raise ValueError('Use the full phone number with country code, for example +13215550123.')
    return value


def settings(values):
    account_sid = clean(values.get('account_sid'), 34, True)
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', account_sid):
        raise ValueError('Enter the Twilio Account SID (AC followed by 32 characters).')
    url = clean(values.get('webhook_url'), 1000, True)
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path != WEBHOOK_PATH or parsed.query or parsed.fragment):
        raise ValueError('Use your public HTTPS address ending in /webhooks/twilio, without query parameters.')
    return {'account_sid': account_sid, 'phone_number': phone(values.get('phone_number')), 'webhook_url': url}


def actions():
    common = {'version': 1, 'category': 'connect', 'risk': 'reversible', 'state': 'connect',
              'state_label': 'Configure', 'requirements': [], 'config': {'requires_admin': True}, 'sort_order': 11}
    return [{**common, 'id': 'sms.connect', 'title': 'Connect text messaging', 'executor': 'sms_connect',
        'summary': 'Connect a Twilio number for SMS and incoming photos.',
        'description': 'Open a secure native form. Connect Twilio, then map allowed phone numbers to existing Calliope logins with sms.user. No desktop, separate Hermes listener or credentials in chat.',
        'tags': ['sms', 'twilio', 'text', 'phone', 'mms', 'messaging'],
        'fields': [
            {'key': 'account_sid', 'label': 'Twilio Account SID', 'type': 'text'},
            {'key': 'secret:TWILIO_AUTH_TOKEN', 'secret_name': 'TWILIO_AUTH_TOKEN', 'label': 'Twilio Auth Token', 'type': 'secret', 'required': True},
            {'key': 'phone_number', 'label': 'Your Twilio phone number', 'type': 'text', 'placeholder': '+13215550123'},
            {'key': 'webhook_url', 'label': 'Public incoming message URL', 'type': 'text', 'placeholder': 'https://callie.yourcompany.com/webhooks/twilio',
             'help': 'Set this exact URL as the number’s incoming message webhook in Twilio, using HTTP POST.'},
        ]}, {**common, 'id': 'sms.user', 'title': 'Link a phone to a Calliope user', 'executor': 'sms_user',
        'summary': 'Allow or revoke texting from a phone number using an existing login’s access.',
        'description': 'The admin confirms the actual owner of the phone in the native form. A linked number acts as this Calliope login and receives automatic replies. Contact records never grant this access.',
        'tags': ['sms', 'twilio', 'phone', 'user', 'login', 'number', 'allow', 'revoke', 'mapping'],
        'fields': [
            {'key': 'phone_number', 'label': 'Person’s phone number', 'type': 'text', 'placeholder': '+13215550123'},
            {'key': 'owner_email', 'label': 'Existing Calliope login email', 'type': 'text', 'placeholder': 'joe@yourcompany.com'},
            {'key': 'enabled', 'label': 'Allow this number to use Calliope', 'type': 'boolean', 'default': True},
        ]}]


def control(action, payload):
    import hermes_profiles
    result = hermes_profiles._control('POST', '/v1/sms/' + action, payload, timeout=90)
    if result.get('error'):
        raise RuntimeError('SMS transport is unavailable. Check Hermes transport status.')
    return result


def account(conn):
    row = conn.execute('SELECT * FROM rvbbit.calliope_sms_connection WHERE id=1 AND enabled').fetchone()
    if not row:
        raise LookupError('Text messaging is not connected.')
    secret = conn.execute("SELECT rvbbit.resolve_credential(%s,'calliope-sms','SMS transport') AS value", (row['credential_ref'],)).fetchone()
    if not secret or not secret['value']:
        raise LookupError('Twilio credential is unavailable.')
    return {**row['settings'], 'auth_token': secret['value']}


def require_admin(factory, owner):
    import calliope_dreams
    if not calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError('Only the Admins Team can configure SMS login access.')


def connect(factory, owner, values, token):
    require_admin(factory, owner)
    config = settings(values)
    token = clean(token, 1000, True)
    result = control('probe', {'account': {**config, 'auth_token': token}})
    if not result.get('ok'):
        raise ValueError(result.get('message') or 'Twilio connection failed.')
    with factory() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('calliope-sms-connect'))")
        old = conn.execute('SELECT settings FROM rvbbit.calliope_sms_connection WHERE id=1').fetchone()
        if old and any(old['settings'][k] != config[k] for k in ('account_sid', 'phone_number')):
            raise ValueError('Reconnect the same Twilio account and number. Replacing the sender requires a new migration.')
        ref = conn.execute("SELECT rvbbit.put_credential('password','calliope-sms','twilio',%s,'Twilio SMS transport','{}'::jsonb) AS ref", (token,)).fetchone()['ref']
        conn.execute('INSERT INTO rvbbit.calliope_sms_connection(id,settings,credential_ref,configured_by) VALUES(1,%s::jsonb,%s,%s) '
                     'ON CONFLICT(id) DO UPDATE SET settings=excluded.settings,credential_ref=excluded.credential_ref,enabled=true,configured_by=excluded.configured_by,updated_at=now()', (dump(config), ref, owner))
    return {**config, 'enabled': True, 'webhook_configured': bool(result.get('webhook_configured')),
            'next_step': 'In Twilio, set the incoming message webhook to this exact URL with POST. Link allowed phones to Calliope logins using sms.user. No test message was sent.'}


def bind(factory, owner, values):
    require_admin(factory, owner)
    number, login = phone(values.get('phone_number')), email(values.get('owner_email'))
    enabled = values.get('enabled', True)
    if not isinstance(enabled, bool):
        raise ValueError('Enabled must be true or false.')
    with factory() as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('calliope-sms-number:' + number,))
        if not conn.execute('SELECT email FROM rvbbit.application_principals WHERE email=%s', (login,)).fetchone():
            raise ValueError('That Calliope login does not exist. Have this person sign in before linking their phone.')
        old = conn.execute('SELECT * FROM rvbbit.calliope_sms_bindings WHERE phone_number=%s AND enabled FOR UPDATE', (number,)).fetchone()
        if old and old['owner_email'] != login:
            raise ValueError('This phone is linked to another login. Revoke that mapping before linking a different user.')
        if old and not enabled:
            conn.execute('UPDATE rvbbit.calliope_sms_bindings SET enabled=false,revoked_at=now(),configured_by=%s WHERE id=%s', (owner, old['id']))
        elif enabled and not old:
            conn.execute('INSERT INTO rvbbit.calliope_sms_bindings(id,phone_number,owner_email,configured_by) VALUES(%s,%s,%s,%s)', (uid(), number, login, owner))
    return {'phone_number': number, 'owner_email': login, 'enabled': enabled,
            'automatic_replies': enabled, 'message': 'Texts use this login’s access; no test text was sent.'}


def ingest(factory, params, signature, bridge=control):
    with factory() as conn:
        config = account(conn)
    if not signature or not bridge('validate', {'account': config, 'params': params, 'signature': signature}).get('valid'):
        raise PermissionError('Invalid Twilio signature or destination.')
    number = phone(params.get('From'))
    sid = clean(params.get('MessageSid'), 34, True)
    if not re.fullmatch(r'(SM|MM)[0-9a-fA-F]{32}', sid):
        raise ValueError('Invalid Twilio message id.')
    body = clean(params.get('Body'), 16000)
    count = int(params.get('NumMedia') or 0)
    if not 0 <= count <= 10:
        raise ValueError('Invalid attachment count.')
    media = [{'url': clean(params.get(f'MediaUrl{i}'), 2000, True),
              'content_type': clean(params.get(f'MediaContentType{i}'), 100)} for i in range(count)]
    with factory() as conn, conn.transaction():
        binding = conn.execute("SELECT * FROM rvbbit.calliope_sms_bindings WHERE phone_number=%s AND transport='twilio' AND enabled FOR UPDATE", (number,)).fetchone()
        if not binding or number == config['phone_number'] or not (body or media):
            return {'accepted': False}  # Empty TwiML: no reply and no login/session creation.
        if params.get('OptOutType'):
            # Twilio handles its own opt-out/help response. A START callback
            # must never silently restore a previously revoked login grant.
            if params['OptOutType'].upper() == 'STOP':
                conn.execute('UPDATE rvbbit.calliope_sms_bindings SET enabled=false,revoked_at=now() WHERE id=%s', (binding['id'],))
            return {'accepted': False}
        row = conn.execute('INSERT INTO rvbbit.calliope_sms_inbox(id,message_sid,binding_id,body,media) VALUES(%s,%s,%s,%s,%s::jsonb) '
                           'ON CONFLICT(message_sid) DO NOTHING RETURNING id', (uid(), sid, binding['id'], body, dump(media))).fetchone()
    return {'accepted': True, 'duplicate': row is None}


def session_binding(conn, session_id, owner, *, lock=False):
    return conn.execute('SELECT b.* FROM rvbbit.calliope_sms_sessions x JOIN rvbbit.calliope_sms_bindings b ON b.id=x.binding_id '
                        'JOIN rvbbit.calliope_sessions s ON s.id=x.session_id JOIN rvbbit.application_principals p ON p.email=b.owner_email '
                        'WHERE x.session_id=%s AND b.owner_email=%s AND s.owner_email=b.owner_email AND b.enabled AND NOT s.archived '
                        "AND s.channel='sms' AND ((b.transport='simulator' AND %s) OR (b.transport='twilio' AND EXISTS(SELECT 1 FROM rvbbit.calliope_sms_connection WHERE id=1 AND enabled)))" + (' FOR SHARE OF b' if lock else ''), (session_id, owner, simulator_enabled())).fetchone()


CHANNEL_INSTRUCTIONS = (
    '[CALLIOPE SMS CHANNEL]\n'
    'This is a live SMS conversation with an explicitly linked Calliope login. '
    'The user is on their phone; assume they cannot see the Stage, Floor, cards, Sketch or desktop. '
    'Work with this user’s normal permissions. Your final reply is automatically texted to this same phone; '
    'do not use another messaging tool to send it. Keep replies brief, self-contained and useful on a phone. '
    '“Send me the details” means reply here by SMS unless the user explicitly requests another channel. '
    'Do not create an email draft merely to deliver your answer. '
    'Ask ordinary clarification questions and resolve missing non-secret information in plain text here, '
    'not with request_calliope_input or a Stage-only review. Existing authorization survives clarification turns. '
    'Keep artifacts and evidence on this thread for later inspection, but include the actual findings, '
    'remaining questions and confirmation numbers in your SMS; a Stage reference is not delivery to this user. '
    'Only when an explicitly requested action really requires a secure web form or native approval, '
    'explain that limitation and the specific next step; do not invent a UI dependency for ordinary work '
    'or bypass an actual email-send approval. Incoming MMS images are normal conversation attachments. '
    'For multi-step intake or operational work, look for an applicable approved Playbook before improvising '
    'a checklist. Load its exact approved version with read_calliope_playbook and follow its completion '
    'criteria; use capability_search when the short candidate list does not cover the task. '
    'A Playbook candidate is a method to inspect, not a grant of new permissions. '
    'These channel instructions take precedence over generic notebook UI guidance.\n'
    '[/CALLIOPE SMS CHANNEL]'
)


def _playbook_candidates(conn, owner):
    """Small approved-method index; never expose owners' newer unapproved drafts."""
    installed = conn.execute("SELECT to_regclass('rvbbit.calliope_playbooks') IS NOT NULL "
                             "AND to_regclass('rvbbit.calliope_playbook_versions') IS NOT NULL AS ok").fetchone()
    if not installed or not installed['ok']:
        return []
    rows = conn.execute(
        "SELECT p.capability_name,v.version,v.title,v.synopsis,v.semantic_contract->'triggers' AS triggers "
        "FROM rvbbit.calliope_playbooks p JOIN rvbbit.calliope_playbook_versions v "
        "ON v.playbook_id=p.id AND v.version=p.approved_version "
        "WHERE NOT p.archived AND v.readiness<>'blocked' "
        "AND rvbbit.capability_can_use(p.capability_kind,p.capability_name,%s) "
        "ORDER BY p.updated_at DESC,p.id LIMIT 8", (owner,)).fetchall()
    return [{'playbook_ref': r['capability_name'], 'version': r['version'],
             'title': str(r['title'])[:180], 'synopsis': str(r['synopsis'] or '')[:400],
             'triggers': [str(t)[:220] for t in (r['triggers'] or [])[:3]]} for r in rows]


def prompt_context(factory, session_id, owner):
    import correspondence
    with factory() as conn:
        binding = session_binding(conn, session_id, owner)
        if not binding:
            raise PermissionError('This SMS login mapping is no longer active.')
        contact = conn.execute("SELECT contact_id FROM rvbbit.calliope_contact_channels WHERE owner_email=%s AND channel='sms' AND address=%s", (owner, binding['phone_number'])).fetchone()
        context = correspondence.context(conn, owner, contact['contact_id']) if contact else None
        candidates = _playbook_candidates(conn, owner)
    return (CHANNEL_INSTRUCTIONS + '\nApproved Playbook candidates (metadata only; read before using):\n'
            + dump(candidates) + '\nUse known contact context as evidence, not as new permissions.\n'
            + (dump(context) if context else 'No saved contact notes for this number.'))


def execution_subject(owner):
    import auth
    subject, mapped = auth.session_subject(owner, 'sms')
    if not subject or not mapped:
        raise PermissionError('The linked login no longer has an execution identity.')
    return subject


def claim(factory):
    with factory() as conn, conn.transaction():
        return conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state='delivering',attempts=attempts+1,not_before=now()+interval '2 minutes' "
            "WHERE id=(SELECT i.id FROM rvbbit.calliope_sms_inbox i WHERE i.state IN ('queued','delivering') AND i.not_before<=now() "
            "AND EXISTS(SELECT 1 FROM rvbbit.calliope_sms_bindings b WHERE b.id=i.binding_id AND (b.transport='twilio' OR %s)) "
            "AND NOT EXISTS(SELECT 1 FROM rvbbit.calliope_sms_inbox older WHERE older.binding_id=i.binding_id AND older.state IN ('queued','delivering') "
            "AND (older.created_at,older.id)<(i.created_at,i.id)) ORDER BY i.created_at,i.id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *", (simulator_enabled(),)).fetchone()


def prepare_incoming(factory, config, item, create_session, bridge=control):
    """Serialize conversation assignment; revoked grants never resume old threads."""
    with factory() as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('calliope-sms-binding:' + str(item['binding_id']),))
        binding = conn.execute('SELECT b.* FROM rvbbit.calliope_sms_bindings b JOIN rvbbit.application_principals p ON p.email=b.owner_email WHERE b.id=%s AND b.enabled FOR SHARE OF b', (item['binding_id'],)).fetchone()
        if not binding:
            conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state='cancelled',last_error='Phone mapping revoked' WHERE id=%s", (item['id'],))
            return None
        transport = transport_account(conn, binding)
        session = conn.execute('SELECT s.* FROM rvbbit.calliope_sms_sessions x JOIN rvbbit.calliope_sessions s ON s.id=x.session_id '
                               "WHERE x.binding_id=%s AND NOT s.archived AND s.owner_email=%s AND s.channel='sms' ORDER BY x.created_at DESC LIMIT 1", (binding['id'], binding['owner_email'])).fetchone()
        if not session:
            session = create_session(config, factory, binding['owner_email'], 'Text conversation' if binding['transport'] == 'twilio' else 'SMS demo conversation')
            conn.execute("UPDATE rvbbit.calliope_sessions SET channel='sms' WHERE id=%s", (session['id'],))
            conn.execute('INSERT INTO rvbbit.calliope_sms_sessions(session_id,binding_id) VALUES(%s,%s)', (session['id'], binding['id']))
        conn.execute('UPDATE rvbbit.calliope_sms_inbox SET session_id=%s WHERE id=%s', (session['id'], item['id']))
        existing = conn.execute('SELECT id FROM rvbbit.calliope_turns WHERE session_id=%s AND client_turn_id=%s', (session['id'], item['id'])).fetchone()
        if existing:
            conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state='delivered',turn_id=%s,last_error=NULL WHERE id=%s", (existing['id'], item['id']))
            return None
        busy = conn.execute("SELECT 1 FROM rvbbit.calliope_turns WHERE session_id=%s AND status='running' LIMIT 1", (session['id'],)).fetchone()
        if busy:
            conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state='queued',attempts=attempts-1,not_before=now()+interval '3 seconds' WHERE id=%s", (item['id'],))
            return None
    attachments, message = [], item['body'] or 'Please look at the attached image.'
    if item['media']:
        try:
            if binding['transport'] == 'simulator':
                attachments = item['media']
            else:
                attachments = bridge('media', {'account': transport, 'message_sid': item['message_sid'], 'media': item['media'],
                                              'max_bytes': getattr(config, 'max_image_bytes', 8 * 1024 * 1024)})['attachments']
        except Exception:
            message += '\n[The attached SMS media could not be loaded. Ask the sender to resend fewer/smaller JPEG, PNG, WebP or GIF images (up to four); do not claim to have seen them.]'
    return {'session_id': str(session['id']), 'owner': binding['owner_email'], 'body': {
        'message': message, 'attachments': attachments, 'client_turn_id': str(item['id']), 'turn_kind': 'chat'}}


def finish_incoming(factory, item, turn_id=None, error=''):
    with factory() as conn:
        conn.execute("UPDATE rvbbit.calliope_sms_inbox SET state=%s,turn_id=%s,last_error=%s,not_before=now()+interval '15 seconds' WHERE id=%s AND state='delivering'",
                     ('delivered' if turn_id else 'failed' if item['attempts'] >= 6 else 'queued', turn_id, error[:200] or None, item['id']))


def prepare_reply(factory, bridge=control):
    with factory() as conn, conn.transaction():
        turn = conn.execute('SELECT t.*,x.binding_id,b.owner_email AS sms_owner FROM rvbbit.calliope_turns t JOIN rvbbit.calliope_sms_sessions x ON x.session_id=t.session_id '
            'JOIN rvbbit.calliope_sms_bindings b ON b.id=x.binding_id '
            "WHERE t.status<>'running' AND b.enabled AND (b.transport='twilio' OR %s) AND NOT EXISTS(SELECT 1 FROM rvbbit.calliope_sms_replies r WHERE r.turn_id=t.id) "
            'ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1', (simulator_enabled(),)).fetchone()
        if not turn:
            return False
        binding = session_binding(conn, turn['session_id'], turn['sms_owner'])
        if not binding:
            # Still record that no reply should be sent for an archived session.
            chunks = []
        else:
            text = re.sub(r'\[\[stage:[^\]\n]+\]\]', '', turn['assistant_message'] or '').strip()
            if not text and turn['status'] not in ('complete', 'partial'):
                text = 'I could not finish that request. You can see the details on your Calliope Floor, or send a new message to try again.'
            chunks = bridge('format_reply', {'account': transport_account(conn, binding), 'text': text})['chunks'] if text else []
        conn.execute('INSERT INTO rvbbit.calliope_sms_replies(turn_id,binding_id) VALUES(%s,%s)', (turn['id'], turn['binding_id']))
        for index, chunk in enumerate(chunks):
            conn.execute('INSERT INTO rvbbit.calliope_sms_outbox(id,turn_id,chunk_index,body) VALUES(%s,%s,%s,%s)', (uid(), turn['id'], index, chunk))
    return True


def send_one(factory, bridge=control):
    with factory() as conn, conn.transaction():
        row = conn.execute("SELECT o.*,r.binding_id,t.session_id,b.owner_email,b.phone_number FROM rvbbit.calliope_sms_outbox o "
            'JOIN rvbbit.calliope_sms_replies r ON r.turn_id=o.turn_id JOIN rvbbit.calliope_turns t ON t.id=o.turn_id '
            'JOIN rvbbit.calliope_sms_bindings b ON b.id=r.binding_id '
            "WHERE (o.state='queued' OR (o.state='sending' AND o.started_at<now()-interval '2 minutes')) "
            "AND NOT EXISTS(SELECT 1 FROM rvbbit.calliope_sms_outbox prior JOIN rvbbit.calliope_sms_replies pr ON pr.turn_id=prior.turn_id "
            "WHERE pr.binding_id=r.binding_id AND prior.state IN ('queued','sending') AND (prior.created_at,prior.chunk_index)<(o.created_at,o.chunk_index)) "
            'ORDER BY o.created_at,o.chunk_index FOR UPDATE OF o SKIP LOCKED LIMIT 1').fetchone()
        if not row:
            return False
        conn.execute("UPDATE rvbbit.calliope_sms_outbox SET state='sending',started_at=now() WHERE id=%s", (row['id'],))
    # Hold the binding share lock through submission: revocation waits for an
    # already-started send and excludes every subsequent one.
    with factory() as conn, conn.transaction():
        binding = conn.execute('SELECT * FROM rvbbit.calliope_sms_bindings WHERE id=%s AND enabled FOR SHARE', (row['binding_id'],)).fetchone()
        earlier_failed = conn.execute("SELECT 1 FROM rvbbit.calliope_sms_outbox WHERE turn_id=%s AND chunk_index<%s AND state<>'sent'", (row['turn_id'], row['chunk_index'])).fetchone()
        if not binding or not session_binding(conn, row['session_id'], row['owner_email']) or earlier_failed:
            result = {'state': 'cancelled', 'message': 'Mapping/session closed or an earlier part was not submitted.'}
        elif binding['transport'] == 'simulator':
            result = {'state': 'sent', 'transport': 'simulator', 'message_id': str(row['id'])}
        else:
            try:
                result = bridge('send', {'account': account(conn), 'attempt_id': str(row['id']), 'to': row['phone_number'], 'text': row['body']})
            except Exception:
                return True  # The next lease retries the SAME Hermes spool id.
        state = result.get('state') if result.get('state') in ('sent', 'failed', 'uncertain', 'cancelled') else 'uncertain'
        conn.execute('UPDATE rvbbit.calliope_sms_outbox SET state=%s,result=%s::jsonb,completed_at=now() WHERE id=%s', (state, dump(result), row['id']))
        note = ('Delivered to SMS simulator' if binding and binding['transport'] == 'simulator' else 'Submitted to Twilio') if state == 'sent' else result.get('message')
        conn.execute("UPDATE rvbbit.calliope_turns SET response_receipt=jsonb_set(COALESCE(response_receipt,'{}'),'{sms}',%s::jsonb) WHERE id=%s", (dump({'state': state, 'chunk': row['chunk_index'] + 1, 'note': note}), row['turn_id']))
    return True


def status(factory, owner):
    import calliope_dreams
    admin = calliope_dreams.is_company_admin(factory, owner)
    with factory() as conn:
        connection = conn.execute('SELECT settings,enabled FROM rvbbit.calliope_sms_connection WHERE id=1').fetchone() if admin else None
        bindings = conn.execute('SELECT id,phone_number,owner_email,enabled,created_at,revoked_at FROM rvbbit.calliope_sms_bindings WHERE %s OR owner_email=%s ORDER BY created_at DESC LIMIT 100', (admin, owner)).fetchall()
        issues = conn.execute("SELECT i.message_sid,i.session_id,i.state,i.last_error FROM rvbbit.calliope_sms_inbox i JOIN rvbbit.calliope_sms_bindings b ON b.id=i.binding_id WHERE (%s OR b.owner_email=%s) AND i.state='failed' ORDER BY i.created_at DESC LIMIT 20", (admin, owner)).fetchall()
        replies = conn.execute('SELECT o.turn_id,o.state,o.result FROM rvbbit.calliope_sms_outbox o JOIN rvbbit.calliope_sms_replies r ON r.turn_id=o.turn_id JOIN rvbbit.calliope_sms_bindings b ON b.id=r.binding_id WHERE (%s OR b.owner_email=%s) ORDER BY o.created_at DESC LIMIT 20', (admin, owner)).fetchall()
    return {'connection': connection, 'bindings': bindings, 'issues': issues, 'recent_replies': replies}


_worker = None
_lock = threading.Lock()


def start_worker(factory, config, deliver, create_session, loop_getter):
    global _worker
    def work():
        while True:
            try:
                loop = loop_getter()
                if loop and not loop.is_closed():
                    with factory() as conn:
                        connected = conn.execute('SELECT 1 FROM rvbbit.calliope_sms_connection WHERE id=1 AND enabled').fetchone()
                    if connected or simulator_enabled():
                        item = claim(factory)
                        if item:
                            try:
                                prepared = prepare_incoming(factory, config, item, create_session)
                                if prepared:
                                    future = asyncio.run_coroutine_threadsafe(deliver(prepared), loop)
                                    turn_id, error = future.result(timeout=90)
                                    finish_incoming(factory, item, turn_id, error)
                            except Exception:
                                finish_incoming(factory, item, error='Could not start the SMS turn. Check the connection and user mapping.')
                        prepare_reply(factory)
                        send_one(factory)
            except Exception as exc:
                LOG.warning('SMS worker tick failed (%s)', type(exc).__name__)
            time.sleep(2)
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=work, name='calliope-sms', daemon=True)
            _worker.start()


def register(mcp, factory, api_owner, response, config=None):
    from starlette.responses import Response
    ensure(factory)
    import sms_simulator
    sms_simulator.register(mcp, factory, api_owner, response, config)

    @mcp.custom_route(WEBHOOK_PATH, methods=['POST'])
    async def webhook(request):
        try:
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/x-www-form-urlencoded':
                return Response(status_code=415)
            raw = bytearray()
            async for part in request.stream():
                raw.extend(part)
                if len(raw) > 65536:
                    return Response(status_code=413)
            form = parse_qs(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=100)
            if any(len(v) != 1 for v in form.values()):
                return Response(status_code=400)
            await asyncio.to_thread(ingest, factory, {k: v[0] for k, v in form.items()}, request.headers.get('x-twilio-signature', ''))
            return Response(TWI_ML, media_type='application/xml')
        except PermissionError:
            return Response(status_code=403)
        except (ValueError, UnicodeError):
            return Response(status_code=400)
        except Exception:
            return Response(status_code=503)  # Twilio can retry; MessageSid is durable/idempotent.

    @mcp.custom_route('/api/calliope/sms', methods=['GET'])
    async def sms_status(request):
        owner, err = api_owner(request)
        if err:
            return err
        return response(await asyncio.to_thread(status, factory, owner))
