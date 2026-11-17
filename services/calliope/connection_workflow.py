"""Durable native connection cards and credential-free conversation continuations.

Uses existing Stage surfaces, action runs and the durable agent mailbox. A form
revision and per-surface advisory lock prevent duplicate applies. OAuth and mirror
loads reconcile without depending on an open browser.
"""
import asyncio
import json
import uuid


def _public_error(result):
    run = result.get('run') or {}
    verification = run.get('verification') or {}
    message = str((result.get('error') or {}).get('message') or run.get('error') or verification.get('error') or '')[:1200]
    lowered = message.lower()
    if 'ssh' not in lowered:
        if any(term in lowered for term in ('password authentication failed', 'access denied for user', 'login failed for user')):
            return 'The database rejected the username or password. Check the database login, then update and test again.'
        if any(term in lowered for term in ('certificate verify failed', 'certificate verification failed', 'certificate validation failed')):
            return 'The database certificate could not be verified. Check the trusted CA certificate and server name in connection options.'
        if any(term in lowered for term in ('connection refused', 'connection timed out', 'could not translate host name')):
            return 'The database could not be reached. Check its server address, port and network access, then test again.'
    return message


def _event(conn, row, result, phase):
    run = result.get('run') or {}
    if phase == 'loaded' and run.get('id'):
        # A fast copy may already have been read by the active model turn.
        # Serialize with that receipt so completion does not start a duplicate turn.
        current = conn.execute('SELECT result FROM rvbbit.calliope_action_runs WHERE id=%s::uuid FOR UPDATE', (run['id'],)).fetchone()
        if current and (current.get('result') or {}).get('completion_observed'):
            return
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"calliope-connection:{row['id']}:{run.get('id', 'form')}:{phase}"))
    # Minimal installations/test fixtures may not have the mailbox yet.
    if not (conn.execute("SELECT to_regclass('rvbbit.calliope_agent_messages') IS NOT NULL AS ready").fetchone() or {}).get('ready'):
        return
    summary = {'phase': phase, 'action_id': (run.get('action_snapshot') or {}).get('id') or row['payload'].get('action_id'),
               'surface_id': str(row['id']), 'run_id': run.get('id'), 'error': _public_error(result),
               'verification': run.get('verification') or {}, 'mirror': result.get('mirror') or {}}
    if phase == 'connected':
        if str(summary['action_id'] or '').startswith('sms.'):
            text = 'The SMS setup or login mapping was saved. Read the verification details: Twilio needs the exact public POST webhook, and permitted phones must be linked to existing Calliope users. No test SMS was sent. Continue here without opening the desktop or starting a separate Hermes listener.'
        elif summary['action_id'] == 'email.connect':
            text = 'The mailbox passed its IMAP and SMTP authentication tests and is connected. Continue the original request here; no desktop or further sign-in is needed. Incoming mail collection starts at connection time. Outgoing drafts still require the operator to send from the review card. No email was sent by the connection test.'
        else:
            text = 'The secure connection passed its test. Continue the original request now. For SQL, discover the available schemas/tables and help the user choose what to copy before creating the mirror. No data is loaded just because the connection succeeded.'
    elif phase == 'loaded':
        text = 'The mirror load finished. Inspect the recorded table outcomes, query the local copied data, and continue the original business question. Be explicit about partial or failed tables and data freshness.'
    elif phase == 'cancelled':
        text = 'The user cancelled this connection request. Do not connect it or ask for credentials again unless they choose to resume.'
    else:
        text = 'The connection needs attention. Explain the credential-free result and one useful next step. The secure card remains available for correction; do not ask for secrets in chat or create duplicate connections.'
    conn.execute(
        "INSERT INTO rvbbit.calliope_agent_messages "
        "(id,owner_email,kind,from_kind,from_ref,from_label,to_session_id,subject,body,payload) "
        "VALUES(%s::uuid,%s,'note','system',%s,'Connection',%s::uuid,%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING",
        (event_id, row['owner_email'], 'connection:' + str(row['id']), str(row['session_id']),
         'Connection update', text + '\n' + json.dumps(summary, default=str),
         json.dumps({'connection_event': summary}, default=str)),
    )


def resolve(factory, owner, surface_id, action_id, result):
    import calliope as c
    sid = c._uuid(surface_id)
    if not sid:
        return None
    with factory() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT f.id,f.session_id,f.payload,s.owner_email FROM rvbbit.calliope_surfaces f "
                "JOIN rvbbit.calliope_sessions s ON s.id=f.session_id WHERE f.id=%s::uuid "
                "AND lower(s.owner_email)=lower(%s) FOR UPDATE OF f", (sid, owner)).fetchone()
            if not row:
                return None
            payload = row['payload']
            request = payload if payload.get('mode') == 'secure_action_request' else payload.get('connection_request')
            if request and request.get('action_id') != action_id:
                raise ValueError('This connection card belongs to a different service.')
            run = result.get('run') or {}
            verification = run.get('verification') or {}
            pending = result.get('status') == 'awaiting_authorization' or (run.get('status') == 'running' and not result.get('error'))
            failed = bool(result.get('error') or run.get('status') == 'failed' or verification.get('source_reachable') is False)
            mirror = result.get('mirror') or {}
            if failed and request:
                updated = {**request, 'revision': int(request.get('revision') or 0) + 1,
                           'last_attempt': result, 'error': _public_error(result) or 'The connection could not be reached.',
                           'connection_tracking': False}
                if verification.get('credential_configured') and action_id == 'mirror.connect':
                    updated['inputs'] = {**(updated.get('inputs') or {}), 'replace_credentials': True}
                # Do not recursively retain historical attempts in the form snapshot.
                updated.pop('connection_request', None)
            else:
                updated = {'mode': 'action_receipt', **result, 'connection_tracking': pending}
                if request:
                    clean_request = {k: v for k, v in request.items() if k not in {'last_attempt', 'error', 'connection_request'}}
                    updated['connection_request'] = clean_request
            title = (run.get('action_snapshot') or {}).get('title') or 'Connect a source or service'
            conn.execute("UPDATE rvbbit.calliope_surfaces SET title=%s,payload=%s::jsonb WHERE id=%s::uuid",
                         (title, json.dumps(updated, default=str), sid))
            if not pending and (run or mirror):
                phase = 'loaded' if mirror else 'needs_attention' if failed else 'connected'
                _event(conn, row, result, phase)
            return updated


async def execute(factory, owner, action_id, body):
    import calliope as c
    import connection_forms
    sid = c._uuid(body.get('surface_id'))
    if not sid:
        raise ValueError('The connection card is missing. Ask Callie to open it again.')
    # Recheck admin access on every submit, even a replay of a completed card.
    action = c._require_direct_administration_action(factory, owner, action_id)
    with factory() as conn:
        lock = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked", ('connection-card:' + sid,)).fetchone()
        if not lock or not lock['locked']:
            return {'error': {'code': 'CONNECTION_BUSY', 'message': 'This connection is already being tested.'}}, 409
        try:
            row = conn.execute(
                "SELECT f.id,f.session_id,f.payload,s.owner_email FROM rvbbit.calliope_surfaces f "
                "JOIN rvbbit.calliope_sessions s ON s.id=f.session_id WHERE f.id=%s::uuid "
                "AND lower(s.owner_email)=lower(%s)", (sid, owner)).fetchone()
            if not row or str(row['session_id']) != str(body.get('session_id')):
                raise PermissionError('This connection card belongs to another conversation.')
            payload = row['payload']
            request = payload if payload.get('mode') == 'secure_action_request' else payload.get('connection_request')
            if not request or request.get('action_id') != action_id:
                raise ValueError('This connection card no longer accepts input.')
            if payload.get('mode') != 'secure_action_request':
                return {k: v for k, v in payload.items() if k not in {'mode', 'connection_request'}}, 200
            if int(body.get('revision') or 0) != int(payload.get('revision') or 0):
                return {'error': {'code': 'CONNECTION_CHANGED', 'message': 'This connection card changed. Review the current details.'}, 'surface_payload': payload}, 409
            supplied = dict(body.get('inputs') or {})
            # Canonical source identity comes from the stored card, not the browser.
            if action.get('executor') == 'mirror_connect':
                supplied = {**(request.get('inputs') or {}), **{k: v for k, v in supplied.items() if k.startswith('secret:')}}
                if supplied.get('secret:CONNECTION_INPUTS'):
                    try:
                        draft = json.loads(supplied['secret:CONNECTION_INPUTS'])
                        if isinstance(draft, dict) and draft.get('dialect') in connection_forms.NAMES:
                            supplied['dialect'] = draft['dialect']
                    except ValueError:
                        pass
                if body.get('retest'):
                    supplied.pop('secret:CONNECTION_INPUTS', None)
                    supplied.pop('secret:SOURCE_DSN', None)
                    supplied['replace_credentials'] = False
            try:
                if action.get('executor') == 'mirror_connect' and supplied.get('secret:CONNECTION_INPUTS'):
                    # Preserve only validated non-secret answers for repair/reload.
                    draft = json.loads(supplied['secret:CONNECTION_INPUTS'])
                    connection_forms.sql_profile(draft, supplied['dialect'])
                    spec = connection_forms.sql_contract(supplied['dialect'])
                    ordinary = {f['key']: draft[f['key']] for f in spec['fields'] if f['type'] != 'secret' and f['key'] in draft}
                    payload['form_contract'] = connection_forms.sql_contract(supplied['dialect'], defaults=ordinary)
                    payload['inputs'] = {**(payload.get('inputs') or {}), 'dialect': supplied['dialect']}
                    conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s::uuid', (json.dumps(payload), sid))
                elif action.get('executor') != 'mirror_connect':
                    ordinary, _, _ = c._normalize_action_inputs(action, supplied)
                    c._validate_administration_action_inputs(action, ordinary)
                    payload['inputs'] = ordinary
                    payload['form_contract'] = connection_forms.contract(action, ordinary, payload.get('missing_secret_names'))
                    conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s::uuid', (json.dumps(payload), sid))
                result = await c.administer_action_with_secure_inputs(factory, owner, action_id, supplied, str(row['session_id']))
                status = 200
            except Exception as exc:
                raw = supplied.get('secret:CONNECTION_INPUTS') or ''
                try:
                    private = connection_forms.private_values(json.loads(raw)) if raw else []
                except ValueError:
                    private = [raw]
                message = c._action_error_text(exc, [*private, *[v for k, v in supplied.items() if k.startswith('secret:')]])[:1200]
                result = {'error': {'code': 'CONNECTION_FAILED', 'message': message}}
                run_id = getattr(exc, 'action_run_id', None)
                if run_id:
                    result['run'] = c._action_run_for_owner(factory, owner, run_id)
                status = 400 if isinstance(exc, ValueError) else 502
            updated = resolve(factory, owner, sid, action_id, result)
            return {**result, 'surface_payload': updated}, status
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ('connection-card:' + sid,))


def cancel(factory, owner, surface_id, revision):
    import calliope as c
    sid = c._uuid(surface_id)
    with factory() as conn:
        with conn.transaction():
            # Share the execute lock so cancellation cannot overtake an in-flight apply.
            if not (conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked", ('connection-card:' + str(sid),)).fetchone() or {}).get('locked'):
                raise ValueError('The connection test is in progress. Wait for its result before cancelling.')
            row = conn.execute("SELECT f.id,f.session_id,f.payload,s.owner_email FROM rvbbit.calliope_surfaces f "
                               "JOIN rvbbit.calliope_sessions s ON s.id=f.session_id WHERE f.id=%s::uuid AND lower(s.owner_email)=lower(%s) FOR UPDATE OF f",
                               (sid, owner)).fetchone()
            if not row:
                raise PermissionError('Connection card not found for this account.')
            payload = row['payload']
            if payload.get('status') == 'cancelled':
                return payload
            if payload.get('mode') != 'secure_action_request' or int(revision or 0) != int(payload.get('revision') or 0):
                raise ValueError('This connection card changed. Refresh it first.')
            c._require_direct_administration_action(factory, owner, payload['action_id'])
            updated = {'mode': 'action_receipt', 'status': 'cancelled', 'message': 'Connection request cancelled.', 'connection_tracking': False}
            conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s::uuid', (json.dumps(updated), sid))
            _event(conn, row, updated, 'cancelled')
            return updated


def sync_pending(factory):
    """Called by the existing durable worker; completion survives browser closure."""
    import calliope as c
    with factory() as conn:
        if not (conn.execute("SELECT to_regclass('rvbbit.calliope_surfaces') IS NOT NULL AS ready").fetchone() or {}).get('ready'):
            return
        rows = conn.execute("SELECT f.id,f.session_id,f.payload,s.owner_email FROM rvbbit.calliope_surfaces f "
                            "JOIN rvbbit.calliope_sessions s ON s.id=f.session_id "
                            "WHERE f.kind='action' AND f.payload->>'connection_tracking'='true' ORDER BY f.created_at LIMIT 30").fetchall()
    for row in rows:
        previous = row['payload']
        old_run = previous.get('run') or {}
        run = c._action_run_for_owner(factory, row['owner_email'], old_run.get('id'))
        if not run:
            continue
        result = {'run': run}
        action = run.get('action_snapshot') or {}
        queued = (run.get('result') or {}).get('queued_run_id')
        if action.get('executor') == 'mirror_create' and queued:
            with factory() as conn:
                mirror = conn.execute("SELECT r.run_id,r.status,r.tables_succeeded,r.tables_failed,r.rows_loaded,r.error_code,r.error_message,"
                                      "(SELECT count(*) FROM rvbbit.mirror_tables t WHERE t.job_name=r.job_name AND t.enabled) AS tables_total "
                                      "FROM rvbbit.mirror_runs r WHERE r.run_id=%s::uuid", (queued,)).fetchone()
            if not mirror:
                continue
            result['mirror'] = c._row_json(mirror)
            pending = mirror['status'] in {'queued', 'running'}
        else:
            pending = run.get('status') not in {'complete', 'failed'}
            if pending:
                result['status'] = 'awaiting_authorization'
        if pending:
            updated = {**previous, **result, 'connection_tracking': True}
            if json.dumps(updated, default=str, sort_keys=True) != json.dumps(previous, default=str, sort_keys=True):
                with factory() as conn:
                    conn.execute("UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s::uuid AND payload->>'connection_tracking'='true'",
                                 (json.dumps(updated, default=str), str(row['id'])))
        else:
            resolve(factory, row['owner_email'], str(row['id']), action.get('id'), result)


def acknowledge_mirror_status(factory, session_id, snapshot):
    """A terminal status returned to the active model is already a continuation."""
    runs = [str(run['run_id']) for run in snapshot.get('runs', [])
            if run.get('run_id') and run.get('status') not in {'queued', 'running'}]
    if not runs:
        return
    with factory() as conn:
        with conn.transaction():
            turn = conn.execute("SELECT id FROM rvbbit.calliope_turns WHERE session_id=%s::uuid AND status='running' ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
            if not turn:
                return
            actions = conn.execute("SELECT id FROM rvbbit.calliope_action_runs WHERE session_id=%s::uuid "
                                   "AND action_snapshot->>'executor'='mirror_create' AND result->>'queued_run_id'=ANY(%s) FOR UPDATE",
                                   (session_id, runs)).fetchall()
            for action in actions:
                conn.execute("UPDATE rvbbit.calliope_action_runs SET result=result || jsonb_build_object('completion_observed',%s::jsonb) WHERE id=%s::uuid",
                             (json.dumps({'turn_id': str(turn['id']), 'via': 'mirror_status'}), str(action['id'])))
                conn.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivered',delivered_at=clock_timestamp(),delivered_turn_id=%s::uuid "
                             "WHERE to_session_id=%s::uuid AND status='queued' AND payload->'connection_event'->>'phase'='loaded' "
                             "AND payload->'connection_event'->>'run_id'=%s",
                             (str(turn['id']), session_id, str(action['id'])))
