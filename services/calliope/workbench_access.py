"""Read-only access to historical Desktop Lab evidence.

New Computer work uses desktop_access and desktop_tools. The retired proposal,
lease, replay and promotion executors are deliberately absent; old receipts
remain owner-scoped and readable without rewriting their historical meaning.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable



class WorkbenchError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status



def _uuid(value: Any, code: str = "INVALID_WORKBENCH") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise WorkbenchError(code, "Choose a valid desktop workbench.", 404) from None



def _email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email or len(email) > 320:
        raise WorkbenchError(
            "APPLICATION_SUBJECT_REQUIRED",
            "Desktop workbenches require a verified signed-in user.",
            403,
        )
    return email



def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}



def _row(value: Any) -> dict[str, Any]:
    item = dict(value or {})
    for key in ("id", "session_id", "lease_id", "workbench_id", "request_event_id"):
        if item.get(key) is not None:
            item[key] = str(item[key])
    for key, value in list(item.items()):
        if isinstance(value, datetime):
            item[key] = value.isoformat()
    if "config" in item:
        item["config"] = _json(item.get("config"))
    if "payload" in item:
        item["payload"] = _json(item.get("payload"))
    return item



def _owned(
    conn: Any,
    workbench_id: Any,
    owner: Any,
) -> dict[str, Any]:
    wid = _uuid(workbench_id)
    email = _email(owner)
    row = conn.execute(
        "SELECT * FROM rvbbit.calliope_workbenches "
        "WHERE id=%s::uuid AND owner_email=%s",
        (wid, email),
    ).fetchone()
    if not row:
        # Do not reveal another user's workbench identity.
        raise WorkbenchError("WORKBENCH_NOT_FOUND", "Desktop workbench not found.", 404)
    return dict(row)



def get(conn_factory: Callable[..., Any], owner: Any, workbench_id: Any) -> dict[str, Any]:
    with conn_factory() as conn:
        return _row(_owned(conn, workbench_id, owner))



def list_for_session(
    conn_factory: Callable[..., Any], owner: Any, session_id: Any
) -> list[dict[str, Any]]:
    email = _email(owner)
    sid = _uuid(session_id, "INVALID_SESSION")
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT * FROM rvbbit.calliope_workbenches "
            "WHERE owner_email=%s AND session_id=%s::uuid "
            "ORDER BY created_at DESC LIMIT 20",
            (email, sid),
        ).fetchall()
    return [_row(item) for item in rows]



def events(
    conn_factory: Callable[..., Any], owner: Any, workbench_id: Any, *, after: int = 0
) -> list[dict[str, Any]]:
    email = _email(owner)
    with conn_factory() as conn:
        item = _owned(conn, workbench_id, email)
        rows = conn.execute(
            "SELECT * FROM rvbbit.calliope_workbench_events "
            "WHERE workbench_id=%s::uuid AND sequence>%s ORDER BY sequence LIMIT 500",
            (str(item["id"]), max(0, int(after))),
        ).fetchall()
        return [_row(event) for event in rows]



def _guided_events(conn: Any, workbench_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM rvbbit.calliope_workbench_events "
        "WHERE workbench_id=%s::uuid AND event_type LIKE 'guided.%%' "
        "ORDER BY sequence LIMIT 1000",
        (workbench_id,),
    ).fetchall()
    return [_row(event) for event in rows]



def _guided_snapshot_from_events(items: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [item for item in items if item.get("event_type") == "guided.run.started"]
    if not starts:
        return {"phase": "idle", "run": None, "proposal": None, "steps": [], "adaptations": []}
    start = starts[-1]
    run_id = str((start.get("payload") or {}).get("run_id") or "")
    current = [
        item for item in items
        if str((item.get("payload") or {}).get("run_id") or "") == run_id
    ]
    terminal = next(
        (
            item for item in reversed(current)
            if item.get("event_type") in {
                "guided.run.saved",
                "guided.run.stopped",
                "guided.replay.completed",
                "guided.replay.paused",
            }
        ),
        None,
    )
    proposals = [
        item for item in current
        if item.get("event_type") in {"guided.action.proposed", "guided.action.corrected"}
    ]
    decisions = {
        str((item.get("payload") or {}).get("proposal_id") or ""): item
        for item in current
        if item.get("event_type") in {"guided.action.approved", "guided.action.rejected"}
    }
    for item in proposals:
        replaced = str((item.get("payload") or {}).get("replaces_proposal_id") or "")
        if replaced:
            decisions.setdefault(replaced, item)
    executions = {
        str((item.get("payload") or {}).get("proposal_id") or ""): item
        for item in current if item.get("event_type") == "guided.action.executed"
    }
    verifications = {
        str((item.get("payload") or {}).get("proposal_id") or ""): item
        for item in current if item.get("event_type") == "guided.step.verified"
    }
    steps: list[dict[str, Any]] = []
    for proposal in proposals:
        payload = proposal.get("payload") or {}
        proposal_id = str(payload.get("proposal_id") or proposal.get("id") or "")
        decision = decisions.get(proposal_id)
        execution = executions.get(proposal_id)
        verification = verifications.get(proposal_id)
        if execution or verification:
            steps.append(
                {
                    "proposal": proposal,
                    "decision": decision,
                    "execution": execution,
                    "verification": verification,
                }
            )
    pending = None
    for proposal in reversed(proposals):
        proposal_id = str((proposal.get("payload") or {}).get("proposal_id") or proposal.get("id"))
        if proposal_id not in decisions:
            pending = proposal
            break
        if proposal_id in executions and proposal_id not in verifications:
            pending = proposal
            break
    observations = [item for item in current if item.get("event_type") == "guided.observed"]
    adaptations = [item for item in current if item.get("event_type") == "guided.recovery.noted"]
    recovery_requests = [item for item in current if item.get("event_type") == "guided.recovery.requested"]
    run_payload = start.get("payload") or {}
    run = {
        "id": run_id,
        "procedure_run_id": run_payload.get("procedure_run_id"),
        "workflow_id": run_payload.get("workflow_id"),
        "revision": run_payload.get("revision", 1),
        "goal": run_payload.get("goal", ""),
        "mode": run_payload.get("mode", "teaching"),
        "replay_steps": run_payload.get("replay_steps") or [],
        "started_at": start.get("created_at"),
        "terminal": terminal,
        "latest_observation": observations[-1] if observations else None,
        "recovery_request": recovery_requests[-1] if recovery_requests else None,
    }
    if terminal:
        phase = {
            "guided.run.saved": "saved",
            "guided.run.stopped": "stopped",
            "guided.replay.completed": "replay_complete",
            "guided.replay.paused": "replay_paused",
        }[terminal.get("event_type")]
    elif pending:
        proposal_id = str((pending.get("payload") or {}).get("proposal_id") or pending.get("id"))
        phase = "verify" if proposal_id in executions else "approve"
    else:
        phase = "observe"
    return {"phase": phase, "run": run, "proposal": pending, "steps": steps, "adaptations": adaptations}



def guided_snapshot(
    conn_factory: Callable[..., Any], owner: Any, workbench_id: Any
) -> dict[str, Any]:
    email = _email(owner)
    with conn_factory() as conn:
        item = _owned(conn, workbench_id, email)
        return _guided_snapshot_from_events(_guided_events(conn, str(item["id"])))



def _guided_drafts_in_conn(conn: Any, email: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.*,w.id AS ledger_workbench_id,w.session_id AS ledger_session_id "
        "FROM rvbbit.calliope_workbench_events e "
        "JOIN rvbbit.calliope_workbenches w ON w.id=e.workbench_id "
        "WHERE w.owner_email=%s AND e.event_type IN "
        "('guided.run.saved','guided.replay.completed','guided.workflow.promoted') "
        "ORDER BY e.created_at,e.sequence",
        (email,),
    ).fetchall()
    saved: dict[tuple[str, int], dict[str, Any]] = {}
    replay_workbenches: dict[tuple[str, int], set[str]] = {}
    promoted: set[tuple[str, int]] = set()
    procedures: dict[tuple[str, int], dict[str, Any]] = {}
    try:
        procedure_rows = conn.execute(
            "SELECT s.source_workflow_id::text AS workflow_id,s.source_workflow_revision AS revision,"
            "p.id::text AS procedure_id,p.capability_name,p.approved_version "
            "FROM rvbbit.calliope_procedure_strategies s "
            "JOIN rvbbit.calliope_playbooks p ON p.id=s.playbook_id "
            "WHERE lower(p.owner_email)=lower(%s)",
            (email,),
        ).fetchall()
        procedures = {
            (str(row["workflow_id"]), int(row["revision"])): {
                "id": str(row["procedure_id"]),
                "capability_name": str(row["capability_name"]),
                "approved_version": row.get("approved_version"),
            }
            for row in procedure_rows
            if row.get("workflow_id") and row.get("revision") is not None
        }
    except Exception:
        # Rolling upgrades may briefly serve the workbench UI before migration
        # 0356 is present. The guided draft remains usable and the next refresh
        # attaches its Procedure metadata after migration.
        procedures = {}
    for raw in rows:
        row = _row(raw)
        payload = row.get("payload") or {}
        workflow_id = str(payload.get("workflow_id") or "")
        try:
            revision = int(payload.get("revision") or 1)
            workflow_id = str(uuid.UUID(workflow_id))
        except (TypeError, ValueError):
            continue
        key = (workflow_id, revision)
        if row.get("event_type") == "guided.run.saved":
            row["ledger_workbench_id"] = str(raw.get("ledger_workbench_id") or "")
            row["ledger_session_id"] = str(raw.get("ledger_session_id") or "")
            saved[key] = row
        elif row.get("event_type") == "guided.replay.completed" and payload.get("passed"):
            replay_workbenches.setdefault(key, set()).add(
                str(raw.get("ledger_workbench_id") or "")
            )
        elif row.get("event_type") == "guided.workflow.promoted":
            promoted.add(key)
    drafts = []
    for key, event in saved.items():
        payload = event.get("payload") or {}
        successful_replays = len(replay_workbenches.get(key, set()))
        is_promoted = key in promoted
        drafts.append(
            {
                "workflow_id": key[0],
                "revision": key[1],
                "goal": str(payload.get("goal") or ""),
                "steps": payload.get("steps") if isinstance(payload.get("steps"), list) else [],
                "adaptations": payload.get("adaptations") if isinstance(payload.get("adaptations"), list) else [],
                "source_workbench_id": event.get("ledger_workbench_id"),
                "source_session_id": event.get("ledger_session_id"),
                "saved_at": event.get("created_at"),
                "successful_replays": successful_replays,
                "required_replays": 2,
                "promotable": successful_replays >= 2 and not is_promoted,
                "promoted": is_promoted,
                "procedure": procedures.get(key),
                "status": "promoted" if is_promoted else (
                    "validated" if successful_replays >= 2 else "draft"
                ),
            }
        )
    drafts.sort(key=lambda item: str(item.get("saved_at") or ""), reverse=True)
    return drafts



def guided_drafts(
    conn_factory: Callable[..., Any], owner: Any
) -> list[dict[str, Any]]:
    email = _email(owner)
    with conn_factory() as conn:
        return _guided_drafts_in_conn(conn, email)



def recordings(
    conn_factory: Callable[..., Any], owner: Any, workbench_id: Any
) -> list[dict[str, Any]]:
    email = _email(owner)
    with conn_factory() as conn:
        item = _owned(conn, workbench_id, email)
        rows = conn.execute(
            "SELECT * FROM rvbbit.calliope_workbench_recordings "
            "WHERE workbench_id=%s::uuid ORDER BY created_at DESC LIMIT 50",
            (str(item["id"]),),
        ).fetchall()
    return [_row(recording) for recording in rows]



def recording(
    conn_factory: Callable[..., Any], owner: Any, workbench_id: Any, recording_id: Any
) -> dict[str, Any]:
    email = _email(owner)
    rid = _uuid(recording_id, "RECORDING_NOT_FOUND")
    with conn_factory() as conn:
        item = _owned(conn, workbench_id, email)
        row = conn.execute(
            "SELECT * FROM rvbbit.calliope_workbench_recordings "
            "WHERE id=%s::uuid AND workbench_id=%s::uuid",
            (rid, str(item["id"])),
        ).fetchone()
    if not row:
        raise WorkbenchError("RECORDING_NOT_FOUND", "Recording not found.", 404)
    return _row(row)
