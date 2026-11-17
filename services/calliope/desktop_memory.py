"""Durable, provenance-linked computer feedback and scoped Hindsight recall.

Only explicit human feedback is retained. Agent-reported completion is context,
not confirmation. Accepted async work remains pending until Hindsight completes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from urllib.parse import quote
import uuid
from datetime import datetime, timezone

import httpx

import desktop_access as access

_worker = None
_lock = threading.Lock()
log = logging.getLogger(__name__)


def demonstrations(conn, run):
    """Bookend human takeovers using immutable control receipts, not inferred intent."""
    events = conn.execute(
        "SELECT id,actor_email,created_at,payload FROM rvbbit.calliope_workbench_events "
        "WHERE desktop_run_id=%s::uuid AND event_type='control.changed' ORDER BY sequence",
        (str(run['id']),),
    ).fetchall()
    result = []
    for index, event in enumerate(events):
        if event['payload'].get('mode') != 'human':
            continue
        next_event = events[index + 1] if index + 1 < len(events) else None
        ended = next_event['created_at'] if next_event else run.get('ended_at')
        result.append({'id': str(event['id']), 'actor': event['actor_email'],
                       'generation': event['payload'].get('generation'),
                       'started_at': event['created_at'].isoformat(),
                       'ended_at': ended.isoformat() if isinstance(ended, datetime) else ended,
                       'end_event_id': str(next_event['id']) if next_event else None})
    return result


def save_feedback(conn_factory, session_id, actor, run_id, feedback, request_id=None,
                  demonstration_id=None, human_generation=None):
    """Commit the lesson and its evidence span together, including retry validation."""
    run = access.run_for_session(conn_factory, session_id, actor, run_id)
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 12000:
        raise access.DesktopError('FEEDBACK_REQUIRED', 'Describe what Callie should learn in up to 12,000 characters.')
    feedback = feedback.strip()
    lid = access.uid(request_id) if request_id else str(uuid.uuid4())
    selected = access.uid(demonstration_id) if demonstration_id else None
    selection = {'demonstration_id': selected, 'human_generation': human_generation}
    with conn_factory() as conn, conn.transaction():
        # The same lock orders lesson insertion and event sequencing against handoff.
        conn.execute('SELECT id FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid FOR UPDATE', (run['id'],)).fetchone()
        previous = conn.execute(
            'SELECT l.*,e.payload FROM rvbbit.calliope_desktop_lessons l '
            'LEFT JOIN rvbbit.calliope_workbench_events e ON e.desktop_run_id=l.run_id '
            "AND e.idempotency_key=l.id::text AND e.event_type='feedback.received' WHERE l.id=%s::uuid", (lid,),
        ).fetchone()
        if previous:
            if (str(previous['run_id']) != run['id'] or previous['actor_email'] != actor
                    or previous['feedback'] != feedback
                    or ((previous.get('payload') or {}).get('selection') or
                        {'demonstration_id': None, 'human_generation': None}) != selection):
                raise access.DesktopError('IDEMPOTENCY_CONFLICT', 'Feedback identity was already used.', 409)
            return {'queued': True, 'lesson_id': lid, 'demonstration': (previous.get('payload') or {}).get('demonstration')}
        span = None
        if selected or human_generation is not None:
            spans = demonstrations(conn, run)
            span = next((item for item in reversed(spans) if
                         (item['id'] == selected if selected else item['generation'] == human_generation)), None)
            if not span:
                raise access.DesktopError('DEMONSTRATION_NOT_FOUND', 'That takeover is not part of this run.', 409)
            if human_generation is not None and (span['ended_at'] or span['actor'] != actor
                                                or span['generation'] != human_generation):
                raise access.DesktopError('CONTROL_CHANGED', 'That takeover has ended. Add feedback from History.', 409)
            span = {**span, 'through_at': span['ended_at'] or datetime.now(timezone.utc).isoformat(),
                    'basis': 'human_explanation', 'complete_takeover': bool(span['ended_at'])}
        conn.execute('INSERT INTO rvbbit.calliope_desktop_lessons(id,run_id,actor_email,feedback) '
                     'VALUES(%s::uuid,%s::uuid,%s,%s)', (lid, run['id'], actor, feedback))
        access.append_event_on_connection(conn, run['id'], actor, 'feedback.received',
            {'feedback': feedback, 'lesson_id': lid, 'selection': selection, 'demonstration': span}, lid)
    return {'queued': True, 'lesson_id': lid, 'demonstration': span}


# Feedback and its span are committed atomically. Legacy lessons simply have no span.
_LESSON_SOURCE = (
    "SELECT l.*,r.desktop_id,r.session_id,r.goal,r.outcome,e.payload->'demonstration' AS demonstration "
    "FROM rvbbit.calliope_desktop_lessons l JOIN rvbbit.calliope_desktop_runs r ON r.id=l.run_id "
    "LEFT JOIN rvbbit.calliope_workbench_events e ON e.desktop_run_id=l.run_id "
    "AND e.idempotency_key=l.id::text AND e.event_type='feedback.received' "
)


def bank_for(desktop_id, actor):
    scope = f"{desktop_id}\n{actor}".encode()
    return "calliope-computer-" + hashlib.sha256(scope).hexdigest()[:32]


def request(method, bank, path, body=None):
    base = os.environ.get("RVBBIT_HINDSIGHT_ENDPOINT", "http://hindsight:8888").rstrip("/")
    headers = {}
    key = os.environ.get("RVBBIT_HINDSIGHT_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.request(method, f"{base}/v1/default/banks/{quote(bank, safe='')}/{path}",
                                  json=body, headers=headers)
    if response.status_code == 404 and method == "POST" and path == "memories/recall":
        return {"results": []}  # Hindsight 1.x answers 404 for a bank nothing has been written to yet
    response.raise_for_status()
    return response.json()


def source_links(row):
    sid, rid = str(row["session_id"]), str(row["run_id"])
    return {"session_id": sid, "run_id": rid, "lesson_id": str(row["id"]),
            **({'demonstration': row['demonstration']} if row.get('demonstration') else {}),
            "events_url": f"/api/calliope/computer/runs/{rid}/events?session_id={sid}",
            "recording_url": f"/api/calliope/computer/runs/{rid}/manifest?session_id={sid}"}


def retention_body(row):
    source = source_links(row)
    stamp = row["created_at"].isoformat()
    return {"async": True, "operation_id": str(row["id"]), "items": [{
        "content": (f"Human computer-work feedback recorded at {stamp}.\n"
                    f"Task: {row['goal']}\n"
                    f"Human feedback (the authoritative correction or confirmation): {row['feedback']}\n"
                    f"Agent-reported outcome (not independently confirmed): {row.get('outcome') or 'not recorded'}\n"
                    "Apply this lesson only when the current task and application state match. "
                    "Later explicit corrections take precedence over earlier advice.\n"
                    f"Source: {json.dumps(source)}"),
        "context": "Human teaching for a persistent Calliope computer",
        "timestamp": stamp, "document_id": "computer-feedback:" + str(row["id"]),
        "metadata": {**source, **({'demonstration': json.dumps(row['demonstration'])} if row.get('demonstration') else {}),
                     "actor": row["actor_email"], "desktop_id": str(row["desktop_id"])},
        "tags": ["computer-feedback"], "update_mode": "replace",
    }]}


def advance(row, transport=request):
    """Return a durable state update; never equate an async ack with learning."""
    bank = bank_for(row["desktop_id"], row["actor_email"])
    prior = row.get("retention_result") or {}
    operation = prior.get("operation_id")
    if operation:
        result = transport("GET", bank, "operations/" + quote(operation, safe=""))
        status = result.get("status")
        if status == "completed":
            return "retained", {"bank_id": bank, "operation_id": operation, "status": status}, 0
        if status == "failed":
            # Retry the same extraction, preserving its identity and source.
            transport("POST", bank, "operations/" + quote(operation, safe="") + "/retry", {})
        elif status == "cancelled":
            return "failed", {**prior, "status": status}, 3600
        elif status == "not_found":
            operation = None
        elif status not in {"pending", "processing"}:
            raise ValueError("Unknown Hindsight operation status")
        if operation:
            return "pending", {"bank_id": bank, "operation_id": operation, "status": status}, 10
    result = transport("POST", bank, "memories", retention_body(row))
    if not result.get("success") or not result.get("async") or not result.get("operation_id"):
        raise ValueError("Hindsight did not acknowledge the feedback retention operation")
    return "pending", {"bank_id": bank, "operation_id": str(result["operation_id"]), "status": "accepted"}, 5


def drain_one(conn_factory, transport=request):
    # The row lock prevents two API processes from submitting concurrently. The
    # Hindsight operation ID also covers a server restart after a lost HTTP ack.
    with conn_factory() as conn, conn.transaction():
        row = conn.execute(
            _LESSON_SOURCE +
            "WHERE l.status IN ('pending','failed') AND l.next_attempt_at<=now() "
            "ORDER BY l.next_attempt_at LIMIT 1 FOR UPDATE OF l SKIP LOCKED"
        ).fetchone()
        if not row:
            return False
        try:
            status, result, delay = advance(row, transport)
        except Exception as exc:
            # Keep source and any accepted operation ID; never store a request
            # body, credentials, or third-party error payload as an error string.
            status = "pending"
            result = {**(row.get("retention_result") or {}), "error": type(exc).__name__}
            delay = min(3600, 10 * 2 ** min(int(row["attempts"]), 9))
        conn.execute(
            "UPDATE rvbbit.calliope_desktop_lessons SET status=%s,retention_result=%s::jsonb,"
            "attempts=attempts+1,next_attempt_at=now()+%s*interval '1 second' WHERE id=%s::uuid",
            (status, json.dumps(result), delay, str(row["id"])),
        )
    return True


def start_worker(conn_factory):
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        def work():
            while True:
                try:
                    for _ in range(8):
                        if not drain_one(conn_factory):
                            break
                except Exception as exc:
                    log.warning("Computer feedback queue unavailable: %s", type(exc).__name__)
                threading.Event().wait(5)
        _worker = threading.Thread(target=work, name="computer-feedback", daemon=True)
        _worker.start()


def recall(conn_factory, run, actor, transport=request):
    """Only return memories attributable to this actor's feedback on this desktop.

Recent original feedback is included even while extraction is pending. It is
labelled separately so memory downtime cannot silently erase a correction.
"""
    with conn_factory() as conn:
        rows = conn.execute(
            _LESSON_SOURCE +
            "WHERE r.desktop_id=%s::uuid AND l.actor_email=%s ORDER BY l.created_at DESC LIMIT 50",
            (str(run["desktop_id"]), actor),
        ).fetchall()
    if not rows:
        return {"items": [], "status": "no_feedback"}
    sources = {str(row["id"]): row for row in rows}
    bank = bank_for(run["desktop_id"], actor)
    items, included = [], set()
    status = "available"
    try:
        result = transport("POST", bank, "memories/recall", {
            "query": run["goal"], "budget": "low", "max_tokens": 1800,
            "tags": ["computer-feedback"], "tags_match": "all_strict",
        })
        # Semantic recall can find a lesson older than the recent feedback
        # window. Resolve those sources with the same actor/desktop scope.
        older = set()
        for fact in result.get("results", []):
            lid = (fact.get("metadata") or {}).get("lesson_id")
            if not lid and str(fact.get("document_id") or "").startswith("computer-feedback:"):
                lid = fact["document_id"].split(":", 1)[1]
            try:
                lid = str(uuid.UUID(str(lid)))
            except ValueError:
                continue
            if lid not in sources:
                older.add(lid)
        if older:
            with conn_factory() as conn:
                found = conn.execute(
                    _LESSON_SOURCE +
                    "WHERE r.desktop_id=%s::uuid AND l.actor_email=%s AND l.id=ANY(%s::uuid[])",
                    (str(run["desktop_id"]), actor, list(older)),
                ).fetchall()
                sources.update({str(row["id"]): row for row in found})
        for fact in result.get("results", []):
            lid = (fact.get("metadata") or {}).get("lesson_id")
            if not lid and str(fact.get("document_id") or "").startswith("computer-feedback:"):
                lid = fact["document_id"].split(":", 1)[1]
            row = sources.get(lid)
            if row and fact.get("text") and len(items) < 8:
                items.append({**source_links(row), "kind": "hindsight", "memory_id": fact["id"],
                              "text": fact["text"], "feedback_at": row["created_at"].isoformat()})
                included.add(lid)
    except Exception:
        status = "hindsight_unavailable"
    for row in rows[:3]:
        if str(row["id"]) not in included:
            items.append({**source_links(row), "kind": "original_feedback", "text": row["feedback"],
                          "task": row["goal"], "feedback_at": row["created_at"].isoformat(),
                          "retention_status": row["status"]})
    value = {"status": status, "bank_id": bank, "items": items,
             "guidance": "Use matching lessons as prior experience. Inspect current state; later explicit human corrections take precedence."}
    access.append_event(conn_factory, run["id"], actor, "lessons.recalled", value, "recall:" + str(uuid.uuid4()))
    return value
