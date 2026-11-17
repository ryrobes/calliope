"""Bind Computer work to the exact agent occurrence that requested it.

This is execution lifecycle bookkeeping, not an action approval layer. A stop
fences that occurrence across later tool calls without stopping another chat.
"""
from contextlib import contextmanager
import asyncio
from urllib.parse import quote

import desktop_access as access


def resolve(conn_factory, session_id, authorization, trusted):
    sid = access.uid(session_id)
    actor = authorization.subject
    platform, ref = authorization.platform, authorization.session_ref
    if not trusted or platform not in {'api_server', 'cron'}:
        if not actor:
            raise access.DesktopError('COMPUTER_IDENTITY_REQUIRED', 'Use a signed-in task or a managed assignment.', 403)
        return actor, None
    if not ref:
        raise access.DesktopError('COMPUTER_CONTEXT_REQUIRED', 'This agent has no task occurrence.', 403)
    with conn_factory() as conn:
        if platform == 'api_server':
            run_ref = getattr(authorization, 'run_ref', None)
            if not run_ref:
                raise access.DesktopError('COMPUTER_CONTEXT_REQUIRED', 'This agent has no exact run identity.', 403)
            row = conn.execute(
                "SELECT 'turn' AS kind,t.id,t.author_email AS actor,t.hermes_run_id,"
                "coalesce(p.display_name,'Callie') AS display_name,p.profile_name "
                "FROM rvbbit.calliope_turns t JOIN rvbbit.calliope_sessions s ON s.id=t.session_id "
                "LEFT JOIN rvbbit.calliope_specialists p ON p.specialist_key=s.specialist_key "
                "WHERE s.id=%s::uuid AND s.hermes_session_id=%s AND t.status='running' "
                "AND lower(t.author_email)=lower(%s) AND t.hermes_run_id=%s",
                (sid, ref, actor, run_ref)).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT 'specialist' AS kind,r.id,r.owner_email AS actor,r.hermes_run_id,s.profile_name,s.display_name "
                    "FROM rvbbit.calliope_specialist_runs r JOIN rvbbit.calliope_specialists s ON s.id=r.specialist_id "
                    "WHERE r.source_session_id=%s::uuid AND r.hermes_session_id=%s AND r.status='running' "
                    "AND lower(r.owner_email)=lower(%s) AND r.hermes_run_id=%s",
                    (sid, ref, actor, run_ref)).fetchone()
        else:
            # Only an occurrence already claimed through begin_*_run can act.
            # A cron job name, forwarded email, or dormant schedule is not one.
            row = conn.execute(
                "SELECT 'work_order' AS kind,id,owner_email AS actor,NULL::text AS hermes_run_id "
                "FROM rvbbit.calliope_work_order_runs WHERE session_id=%s::uuid "
                "AND hermes_session_id=%s AND status='running' "
                "AND NOT (context_snapshot ? 'computer_continuation') UNION ALL "
                "SELECT 'workflow' AS kind,id,owner_email AS actor,NULL::text AS hermes_run_id "
                "FROM rvbbit.calliope_workflow_runs WHERE session_id=%s::uuid "
                "AND hermes_session_id=%s AND status='running'", (sid, ref, sid, ref)).fetchall()
            row = row[0] if len(row) == 1 else None
    if not row or not row.get('actor'):
        raise access.DesktopError('COMPUTER_TASK_ENDED', 'This task is no longer active or belongs to another notebook.', 409)
    controller = {**dict(row), 'id': str(row['id']), 'session_id': sid, 'session_ref': ref}
    controller['key'] = controller['kind'] + ':' + controller['id']
    return row['actor'], controller


@contextmanager
def guard(conn_factory, controller):
    if not controller:
        yield
        return
    # Serialize this occurrence's dispatch with its stop fence across workers.
    with conn_factory() as conn, conn.transaction():
        if controller.get('session_id'):
            # Agents in one notebook share its run. Serialize dispatch while
            # attach() enforces ownership between calls, not just within a job.
            lock(conn, 'session:' + controller['session_id'])
        lock(conn, controller['key'])
        stopped = conn.execute(
            "SELECT 1 FROM rvbbit.calliope_workbench_events WHERE event_type='agent.stop.requested' "
            "AND payload->>'key'=%s LIMIT 1", (controller['key'],)).fetchone()
        if stopped:
            raise access.DesktopError('COMPUTER_STOPPED', 'The person stopped this Computer task. Do not reopen it.', 409)
        yield


def lock(conn, key):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('computer-agent:' + key,))


def current(conn, run_id):
    row = conn.execute(
        "SELECT e.payload,e.sequence,p.display_name FROM rvbbit.calliope_workbench_events e "
        "LEFT JOIN rvbbit.calliope_specialists p ON p.profile_name=e.payload->>'profile_name' "
        "WHERE e.desktop_run_id=%s::uuid AND e.event_type='agent.attached' "
        "ORDER BY e.sequence DESC LIMIT 1", (run_id,)).fetchone()
    if not row:
        return None
    owner = dict(row['payload'])
    owner['attachment_sequence'] = row.get('sequence', 0)
    owner['display_name'] = row.get('display_name') or owner.get('display_name') or (
        (owner.get('profile_name') or 'Specialist') if owner.get('kind') == 'specialist' else 'Callie')
    return owner


def active_agent(conn_factory, run_id):
    if not run_id:
        return None
    with conn_factory() as conn:
        owner = current(conn, run_id)
    return {k: owner.get(k) for k in ('kind', 'display_name')} if owner else None


def attach(conn_factory, run_id, actor, controller):
    if not controller:
        return  # Human UI operations retain their existing control path.
    with conn_factory() as conn, conn.transaction():
        row = conn.execute("SELECT id,status FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid FOR UPDATE",
                           (run_id,)).fetchone()
        if not row or row['status'] not in {'active', 'queued'}:
            raise access.DesktopError('RUN_NOT_ACTIVE', 'Open a new desktop run for this task.', 409)
        owner = current(conn, run_id)
        if owner and owner['key'] == controller['key']:
            return
        if owner and owner['key'] != controller['key']:
            table = {'turn': 'calliope_turns', 'specialist': 'calliope_specialist_runs',
                     'work_order': 'calliope_work_order_runs', 'workflow': 'calliope_workflow_runs'}.get(owner['kind'])
            prior = conn.execute(f"SELECT status FROM rvbbit.{table} WHERE id=%s::uuid",
                                 (owner['id'],)).fetchone() if table else {'status': 'running'}
            if prior and prior['status'] in {'running', 'queued', 'blocked'}:
                raise access.DesktopError('DESKTOP_BUSY',
                    f"{owner['display_name']} owns this chat's desktop run. Wait for that task to finish, "
                    "then retry open_desktop. For a delegated handoff, ask the owner to call finish_desktop first. "
                    "Do not execute, pause, or finish its run.", 409)
        access.append_event_on_connection(conn, run_id, actor, 'agent.attached', controller,
            'agent:' + controller['key'] + ':after:' + str(owner['attachment_sequence'] if owner else 0))


def fence(conn_factory, run_id, actor):
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT payload FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid "
            "AND event_type='agent.attached' ORDER BY sequence", (run_id,)).fetchall()
    for row in rows:
        controller = row['payload']
        with conn_factory() as conn, conn.transaction():
            lock(conn, controller['key'])
            access.append_event(conn_factory, run_id, actor, 'agent.stop.requested', controller,
                                'agent-stop:' + controller['key'])


async def cancel_pending(conn_factory, config, run_id=None):
    """Retry exact API/scheduler cancellation; the durable fence already blocks input."""
    import calliope
    with conn_factory() as conn:
        pending = conn.execute(
            "SELECT e.* FROM rvbbit.calliope_workbench_events e WHERE e.event_type='agent.stop.requested' "
            "AND (%s::uuid IS NULL OR e.desktop_run_id=%s::uuid) "
            "AND e.payload->>'kind' IN ('turn','specialist','work_order','workflow') AND NOT EXISTS "
            "(SELECT 1 FROM rvbbit.calliope_workbench_events done WHERE done.desktop_run_id=e.desktop_run_id "
            "AND done.idempotency_key='agent-stopped:' || (e.payload->>'key')) ORDER BY e.created_at LIMIT 8",
            (run_id, run_id),
        ).fetchall()
    for event in pending:
        controller = event['payload']
        try:
            if controller['kind'] in {'work_order', 'workflow'}:
                if not await cancel_assignment(conn_factory, config, controller, event):
                    continue
                access.append_event(conn_factory, str(event['desktop_run_id']), event['actor_email'],
                                    'agent.stopped', {'key': controller['key']}, 'agent-stopped:' + controller['key'])
                continue
            with conn_factory() as conn:
                if controller['kind'] == 'turn':
                    row = conn.execute("SELECT * FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid",
                                       (controller['id'], controller['session_id'])).fetchone()
                    session = conn.execute("SELECT * FROM rvbbit.calliope_sessions WHERE id=%s::uuid",
                                           (controller['session_id'],)).fetchone()
                    base = calliope._session_hermes_base_url(config, conn_factory, session) if session else None
                else:
                    row = conn.execute("SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s::uuid AND hermes_session_id=%s",
                                       (controller['id'], controller['session_ref'])).fetchone()
                    base = config.hermes_url + '/p/' + quote(controller['profile_name'], safe='')
            if row and row.get('hermes_run_id') and (row['status'] == 'running' or row.get('error') == calliope._USER_STOPPED_TURN_ERROR):
                if controller['kind'] == 'turn':
                    await asyncio.to_thread(calliope._mark_turn_user_stopped, conn_factory, controller['id'])
                try:
                    await calliope._hermes_json(config, 'POST', '/v1/runs/' + quote(row['hermes_run_id'], safe='') + '/stop',
                                               {}, timeout_seconds=12, base_url=base)
                except RuntimeError as exc:
                    if '(404)' not in str(exc):
                        raise
            elif row and row['status'] == 'running':
                continue  # Hermes binding has not been published yet.
            access.append_event(conn_factory, str(event['desktop_run_id']), event['actor_email'],
                                'agent.stopped', {'key': controller['key']}, 'agent-stopped:' + controller['key'])
        except Exception:
            # The next evidence-worker pass retries; do not clear the fence.
            continue


async def cancel_assignment(conn_factory, config, controller, event):
    import calliope
    kind = controller['kind']
    table = {'work_order': 'calliope_work_order_runs', 'workflow': 'calliope_workflow_runs'}[kind]
    with conn_factory() as conn:
        row = conn.execute(
            f"SELECT * FROM rvbbit.{table} WHERE id=%s::uuid AND session_id=%s::uuid AND hermes_session_id=%s",
            (controller['id'], controller['session_id'], controller['session_ref']),
        ).fetchone()
    if not row:
        return True
    result = await calliope._hermes_json(
        config, 'POST', '/v1/cron/executions/stop',
        {'session_id': controller['session_ref'], 'job_id': row['hermes_job_id']}, timeout_seconds=12)
    if row['status'] in {'running', 'blocked'}:
        finish = calliope.finish_work_order_run if kind == 'work_order' else calliope.finish_workflow_run
        await asyncio.to_thread(finish, conn_factory, controller['id'], 'failed', 'Stopped by the person using Computer.',
            details={'computer_run_id': str(event['desktop_run_id']), 'stopped_by': event['actor_email'],
                     'previous_status': row['status'], 'previous_summary': row.get('result_summary')},
            stop_requested=True)
    # Acceptance of the request is not proof that the scheduler stopped. Its
    # existing watchdog consumes the durable flag; the next pass checks again.
    return result.get('terminal') is True
