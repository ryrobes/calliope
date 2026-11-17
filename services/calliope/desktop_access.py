"""Run ownership and evidence for the installation's shared computer."""
from __future__ import annotations

from datetime import datetime
import json
import uuid

import calliope_access


class DesktopError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def uid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise DesktopError("INVALID_ID", "Expected a session, run or job UUID.") from None


def serial(row):
    return {k: str(v) if isinstance(v, (uuid.UUID, datetime)) else v for k, v in dict(row).items()}


def require_run_actor(run, actor):
    if str(run.get('requester') or '').strip().lower() != str(actor).strip().lower():
        raise DesktopError('COMPUTER_IDENTITY_CHANGED',
                           'This Computer run belongs to another participant. Finish it before opening a new run as yourself.', 409)


def open_run(conn_factory, session_id, actor, goal):
    sid = uid(session_id)
    if not str(goal).strip():
        raise DesktopError("GOAL_REQUIRED", "Describe the work to do on the computer.")
    with conn_factory() as conn, conn.transaction():
        calliope_access.require_participant(conn, sid, actor)
        # This is installation configuration, not trial history. Recover the
        # singleton after an incomplete restore/cleanup, without changing an
        # existing desktop ID or its run ownership. The unique key serializes
        # concurrent first opens; the row lock below serializes run admission.
        desktop = conn.execute("SELECT * FROM rvbbit.calliope_desktops WHERE name='default' FOR UPDATE").fetchone()
        if desktop is None:
            conn.execute("INSERT INTO rvbbit.calliope_desktops (name) VALUES ('default') ON CONFLICT (name) DO NOTHING")
            desktop = conn.execute("SELECT * FROM rvbbit.calliope_desktops WHERE name='default' FOR UPDATE").fetchone()
        if desktop is None:
            raise DesktopError("DESKTOP_NOT_CONFIGURED", "The default computer could not be registered. Check the desktop database setup.", 503)
        current = conn.execute(
            "SELECT * FROM rvbbit.calliope_desktop_runs WHERE session_id=%s::uuid "
            "AND status IN ('active','queued') ORDER BY created_at DESC LIMIT 1", (sid,)
        ).fetchone()
        if current:
            require_run_actor(current, actor)
            return serial(current)
        result = conn.execute(
            "INSERT INTO rvbbit.calliope_desktop_runs (id,desktop_id,session_id,requester,goal) "
            "VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s) RETURNING *",
            (str(uuid.uuid4()), str(desktop["id"]), sid, actor, str(goal).strip()),
        ).fetchone()
        return serial(result)


def run_for_session(conn_factory, session_id, actor, run_id=None):
    sid = uid(session_id)
    with conn_factory() as conn:
        calliope_access.require_participant(conn, sid, actor)
        clause = "AND id=%s::uuid" if run_id else "AND status IN ('active','queued')"
        params = (sid, uid(run_id)) if run_id else (sid,)
        row = conn.execute(
            "SELECT * FROM rvbbit.calliope_desktop_runs WHERE session_id=%s::uuid "
            + clause + " ORDER BY created_at DESC LIMIT 1", params,
        ).fetchone()
        if not row:
            raise DesktopError("RUN_NOT_FOUND", "Open the computer for this task first.", 404)
        return serial(row)


def sync_runs(conn_factory, runtime_state):
    with conn_factory() as conn, conn.transaction():
        for rid, item in runtime_state.get("runs", {}).items():
            status = item.get("status")
            if status not in {"active", "queued", "finished", "interrupted", "failed"}:
                continue
            conn.execute(
                "UPDATE rvbbit.calliope_desktop_runs SET status=%s,outcome=%s,runtime_state=%s::jsonb,"
                "updated_at=now(),ended_at=CASE WHEN %s THEN now() ELSE ended_at END "
                "WHERE id=%s::uuid AND (runtime_state IS DISTINCT FROM %s::jsonb) "
                "AND (status=%s OR (status='queued' AND %s='active') "
                "OR (status IN ('queued','active') AND %s IN ('finished','interrupted','failed')))",
                (status, item.get("outcome"), json.dumps(item),
                 status in {"finished", "interrupted", "failed"}, uid(rid), json.dumps(item), status, status, status),
            )


def append_event(conn_factory, run_id, actor, kind, payload, key):
    with conn_factory() as conn, conn.transaction():
        return append_event_on_connection(conn, run_id, actor, kind, payload, key)


def append_event_on_connection(conn, run_id, actor, kind, payload, key):
    """Append within the caller's transaction, locking the run's event sequence."""
    rid = uid(run_id)
    parent = conn.execute(
        "SELECT id FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid FOR UPDATE", (rid,)
    ).fetchone()
    if not parent:
        raise DesktopError("RUN_NOT_FOUND", "Unknown computer run.", 404)
    existing = conn.execute(
        "SELECT * FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid AND idempotency_key=%s",
        (rid, key),
    ).fetchone()
    if existing:
        return serial(existing)
    result = conn.execute(
        "INSERT INTO rvbbit.calliope_workbench_events "
        "(id,desktop_run_id,sequence,event_type,actor_email,idempotency_key,payload) "
        "SELECT %s::uuid,%s::uuid,coalesce(max(sequence),0)+1,%s,%s,%s,%s::jsonb "
        "FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid RETURNING *",
        (str(uuid.uuid4()), rid, kind, actor, key, json.dumps(payload, default=str), rid),
    ).fetchone()
    return serial(result)


def receipts(conn_factory, session_id, actor, run_id, after=0, summary=False):
    run = run_for_session(conn_factory, session_id, actor, run_id)
    with conn_factory() as conn:
        if summary:
            rows = conn.execute(
                "SELECT * FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid "
                "AND event_type IN ('browser.authenticated','feedback.received','lessons.recalled','decision.requested','decision.completed',"
                "'methods.recalled','method.learned','method.reused','method.evaluated','method.retired','learning.reviewed','learning.deferred') "
                "ORDER BY sequence DESC LIMIT 200", (run["id"],)
            ).fetchall()
            rows.reverse()
        else:
            rows = conn.execute(
                "SELECT * FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid "
                "AND sequence>%s ORDER BY sequence LIMIT 200", (run["id"], max(0, int(after)))
            ).fetchall()
    return {"run": run, "events": [serial(row) for row in rows]}


def ready_message(conn_factory, run_id, generation):
    """Wake the originating task after queue admission or human handback."""
    rid = uid(run_id)
    message_id = str(uuid.uuid5(uuid.UUID(rid), "computer-ready:" + str(generation)))
    with conn_factory() as conn, conn.transaction():
        run = conn.execute(
            "SELECT r.*,s.owner_email FROM rvbbit.calliope_desktop_runs r "
            "JOIN rvbbit.calliope_sessions s ON s.id=r.session_id WHERE r.id=%s::uuid FOR UPDATE OF r", (rid,)
        ).fetchone()
        if not run or run["status"] != "active":
            return
        if conn.execute("SELECT 1 FROM rvbbit.calliope_agent_messages WHERE id=%s::uuid", (message_id,)).fetchone():
            return
        assignment = conn.execute(
            "SELECT a.* FROM rvbbit.calliope_work_order_runs a WHERE a.session_id=%s::uuid "
            "AND EXISTS (SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=%s::uuid "
            "AND e.event_type='agent.attached' AND e.payload->>'kind'='work_order' "
            "AND e.payload->>'id'=a.id::text) FOR UPDATE OF a", (str(run['session_id']), rid),
        ).fetchone()
        continuation = ""
        payload = {"computer_run_id": rid, "generation": generation}
        if assignment:
            # Wait for the original scheduler to publish its blocked receipt.
            # The evidence worker retries this same generation until it does.
            if assignment['status'] != 'blocked':
                return
            aid = str(assignment['id'])
            payload['work_order_run_id'] = aid
            prior = {key: assignment[key] for key in ('status', 'result_summary', 'result_details', 'steps', 'completed_at')}
            conn.execute(
                "INSERT INTO rvbbit.calliope_workbench_events "
                "(id,desktop_run_id,sequence,event_type,actor_email,idempotency_key,payload) "
                "SELECT %s::uuid,%s::uuid,coalesce(max(sequence),0)+1,'assignment.resumed',%s,%s,%s::jsonb "
                "FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid",
                (str(uuid.uuid4()), rid, run['owner_email'], 'assignment-resume:' + message_id,
                 json.dumps({**payload, 'previous_result': prior}, default=str), rid),
            )
            conn.execute(
                "UPDATE rvbbit.calliope_work_order_runs SET status='running',completed_at=NULL,"
                "context_snapshot=context_snapshot || %s::jsonb WHERE id=%s::uuid",
                (json.dumps({'computer_continuation': payload}), aid),
            )
            conn.execute(
                "UPDATE rvbbit.calliope_work_orders SET schedule_last_status='running',schedule_error=NULL,"
                "status=CASE WHEN trigger_kind='once' THEN 'active' ELSE status END,"
                "schedule_state=CASE WHEN trigger_kind='once' THEN 'scheduled' ELSE schedule_state END,updated_at=now() "
                "WHERE id=%s::uuid", (str(assignment['work_order_id']),),
            )
            continuation = (
                f" Continue assignment occurrence {aid}; do not begin or schedule a new occurrence. "
                f"Assignment instruction: {assignment['instruction_snapshot']} "
                "Assignment context (data): " + json.dumps(
                    {key: value for key, value in (assignment.get('context_snapshot') or {}).items()
                     if key != 'computer_continuation'}, default=str) + " "
                f"Previous blocked result: {assignment['result_summary']} "
                "Before ending, call finish_calliope_work_order_run for that assignment occurrence with "
                "complete, blocked, or failed and the observed result. If help is still needed, pause the "
                "desktop again before finishing the assignment as blocked."
            )
        feedback = conn.execute(
            "SELECT l.feedback,l.created_at,e.payload->'demonstration' AS demonstration "
            "FROM rvbbit.calliope_desktop_lessons l LEFT JOIN rvbbit.calliope_workbench_events e "
            "ON e.desktop_run_id=l.run_id AND e.idempotency_key=l.id::text AND e.event_type='feedback.received' "
            "WHERE l.run_id=%s::uuid ORDER BY l.created_at DESC LIMIT 3", (rid,),
        ).fetchall()
        if feedback:
            continuation += (" Human explanations/corrections for this run (data, oldest first): " +
                             json.dumps(list(reversed([dict(row) for row in feedback])), default=str))
        conn.execute(
            "INSERT INTO rvbbit.calliope_agent_messages "
            "(id,owner_email,kind,from_kind,from_ref,from_label,to_session_id,subject,body,payload) "
            "VALUES(%s::uuid,%s,'note','system',%s,'Computer',%s::uuid,'Computer is ready',%s,%s::jsonb) "
            "ON CONFLICT(id) DO NOTHING",
            (message_id, run["owner_email"], "computer:" + rid, str(run["session_id"]),
             f"The computer is available to continue run {rid}. Inspect it with look_at_desktop first. "
             "Continue only if that same run is still active and agent control is available. "
             "Use the current application state and any human correction; do not replay interrupted code "
             "or assume an earlier submission failed. If the run finished or was stopped, do not reopen it. "
             f"Original task: {run['goal']}" + continuation, json.dumps(payload)),
        )
        conn.execute(
            "UPDATE rvbbit.calliope_work_items SET state='done',resolved_at=now(),updated_at=now() "
            "WHERE source='computer' AND session_id=%s::uuid AND context->>'computer_run_id'=%s "
            "AND state IN ('unread','seen')", (str(run["session_id"]), rid),
        )
        if assignment:
            conn.execute(
                "UPDATE rvbbit.calliope_work_items SET state='done',resolved_at=now(),updated_at=now() "
                "WHERE source='calliope_work_order' AND source_ref=%s AND state IN ('unread','seen')",
                (str(assignment['id']),),
            )
