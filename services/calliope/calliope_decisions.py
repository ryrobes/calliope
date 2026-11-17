"""Evidence-backed human decisions for Calliope's Today and Board surfaces.

The decision is canonical.  Work Inbox and Hermes kanban remain independent
sources that can be projected into the same queue; this module never creates a
generic inbox item or a duplicate kanban card for a native decision.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


DECISION_KINDS = {"approval", "choice", "exception", "confirmation"}
DECISION_STATUSES = {"open", "answered", "cancelled", "expired"}
URGENCIES = {"low", "normal", "high", "critical"}
EVIDENCE_RELEVANCE_MIN = 0.72
_OPTION_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_decision_requests (
    id uuid PRIMARY KEY,
    owner_email text NOT NULL,
    session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE RESTRICT,
    decision_kind text NOT NULL DEFAULT 'choice',
    question text NOT NULL,
    summary text NOT NULL DEFAULT '',
    options jsonb NOT NULL,
    recommended_option text,
    urgency text NOT NULL DEFAULT 'normal',
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'open',
    selected_option text,
    response_note text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    created_by text NOT NULL,
    answered_by text,
    due_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz,
    closed_at timestamptz,
    CONSTRAINT calliope_decision_requests_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_decision_requests_kind_check CHECK (
        decision_kind IN ('approval','choice','exception','confirmation')
    ),
    CONSTRAINT calliope_decision_requests_urgency_check CHECK (
        urgency IN ('low','normal','high','critical')
    ),
    CONSTRAINT calliope_decision_requests_status_check CHECK (
        status IN ('open','answered','cancelled','expired')
    ),
    CONSTRAINT calliope_decision_requests_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_decision_requests_json_check CHECK (
        jsonb_typeof(options)='array' AND jsonb_array_length(options) BETWEEN 2 AND 6
        AND jsonb_typeof(evidence)='array' AND jsonb_typeof(context)='object'
    ),
    CONSTRAINT calliope_decision_requests_answer_check CHECK (
        (status='answered' AND selected_option IS NOT NULL AND answered_at IS NOT NULL)
        OR (status<>'answered' AND selected_option IS NULL AND answered_at IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_decision_requests_one_open_observation_idx
    ON rvbbit.calliope_decision_requests (owner_email,observation_id)
    WHERE status='open';
CREATE INDEX IF NOT EXISTS calliope_decision_requests_owner_status_idx
    ON rvbbit.calliope_decision_requests
       (owner_email,status,urgency,updated_at DESC);
CREATE INDEX IF NOT EXISTS calliope_decision_requests_due_idx
    ON rvbbit.calliope_decision_requests (coalesce(expires_at,due_at))
    WHERE status='open';

CREATE TABLE IF NOT EXISTS rvbbit.calliope_decision_events (
    event_id bigserial PRIMARY KEY,
    decision_id uuid NOT NULL
        REFERENCES rvbbit.calliope_decision_requests(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    actor_email text NOT NULL,
    revision integer NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_decision_events_kind_check CHECK (
        event_kind IN ('created','updated','answered','cancelled','expired')
    ),
    CONSTRAINT calliope_decision_events_actor_check CHECK (
        actor_email=lower(btrim(actor_email)) AND actor_email LIKE '%@%'
    ),
    CONSTRAINT calliope_decision_events_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_decision_events_payload_check CHECK (
        jsonb_typeof(payload)='object'
    ),
    CONSTRAINT calliope_decision_events_revision_key UNIQUE (decision_id,revision)
);
CREATE INDEX IF NOT EXISTS calliope_decision_events_decision_idx
    ON rvbbit.calliope_decision_events (decision_id,created_at,event_id);
"""


class DecisionConflict(RuntimeError):
    """A decision is no longer open for the requested mutation."""


class DecisionRevisionConflict(DecisionConflict):
    """The rendered decision revision is stale and must be reviewed again."""


def _text(value: Any, maximum: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _owner(value: Any) -> str:
    owner = str(value or "").strip().lower()
    if not owner or "@" not in owner:
        raise ValueError("a valid owner email is required")
    return owner


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
    """Keep model-authored receipts useful without accepting unbounded blobs."""
    if depth >= 5:
        return _text(value, 600)
    if isinstance(value, dict):
        return {
            _text(key, 120): _bounded_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:48]
            if _text(key, 120)
        }
    if isinstance(value, list):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:48]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value, 2_000)


def _bounded_object(value: Any, label: str) -> dict[str, Any]:
    bounded = _bounded_json_value(_object(value))
    if len(json.dumps(bounded, default=str)) > 32_000:
        raise ValueError(f"{label} is too large")
    return bounded


def _bounded_array(value: Any, label: str, *, limit: int = 24) -> list[Any]:
    bounded = [_bounded_json_value(item) for item in _array(value)[:limit]]
    if len(json.dumps(bounded, default=str)) > 32_000:
        raise ValueError(f"{label} is too large")
    return bounded


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _timestamp(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_options(value: Any) -> list[dict[str, Any]]:
    raw_options = _array(value)
    if not 2 <= len(raw_options) <= 6:
        raise ValueError("decision options must contain 2 to 6 choices")
    options: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, raw in enumerate(raw_options, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"decision option {index} must be an object")
        key = str(raw.get("key") or raw.get("value") or raw.get("id") or "").strip().lower()
        if not _OPTION_KEY_RE.fullmatch(key):
            raise ValueError(
                f"decision option {index} key must start with a letter and use lowercase letters, numbers, or underscores"
            )
        if key in keys:
            raise ValueError(f"duplicate decision option key: {key}")
        label = _text(raw.get("label"), 160)
        if not label:
            raise ValueError(f"decision option {index} label is required")
        option = {"key": key, "label": label}
        summary = _text(raw.get("summary") or raw.get("description"), 600)
        consequence = _text(raw.get("consequence"), 600)
        if summary:
            option["summary"] = summary
        if consequence:
            option["consequence"] = consequence
        keys.add(key)
        options.append(option)
    return options


def observation_context(
    conn_factory: Callable[..., Any], session_id: Any, *, limit: Any = 12,
) -> dict[str, Any]:
    """Return a compact ranked decision context using session-derived identity."""
    sid = _uuid(session_id, "session id")
    try:
        bounded_limit = max(1, min(int(limit or 12), 24))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    with conn_factory() as conn:
        session = conn.execute(
            "SELECT id,owner_email FROM rvbbit.calliope_sessions WHERE id=%s::uuid",
            (sid,),
        ).fetchone()
    if not session:
        raise LookupError("Calliope session not found")
    owner = _owner(session.get("owner_email"))
    import calliope_observations

    snapshot = calliope_observations.snapshot(
        conn_factory,
        owner,
        scope_kind="all",
        state="active",
        limit=bounded_limit,
    )
    items = []
    for raw in snapshot.get("observations") or []:
        if not isinstance(raw, dict):
            continue
        feedback = _object(raw.get("viewer_feedback"))
        if feedback.get("verdict") == "not_relevant":
            continue
        ranking = _object(raw.get("ranking"))
        evidence = _bounded_array(raw.get("evidence"), "observation evidence", limit=8)
        role = str(ranking.get("surface_role") or "background")
        items.append({
            "id": str(raw.get("id") or ""),
            "scope_kind": str(raw.get("scope_kind") or "company"),
            "kind": str(raw.get("kind") or "observation"),
            "title": _text(raw.get("title"), 240),
            "summary": _text(raw.get("summary"), 1_200),
            "entities": [_text(item, 160) for item in _array(raw.get("entities"))[:16]],
            "confidence": raw.get("confidence"),
            "urgency": raw.get("urgency"),
            "ranking": ranking,
            "evidence": evidence,
            "decision_ready": role in {"lead", "supporting"} and bool(evidence),
            "last_observed_at": _iso(raw.get("last_observed_at")),
        })
    return {
        "session_id": sid,
        "observations": items,
        "counts": {
            "shown": len(items),
            "decision_ready": sum(bool(item["decision_ready"]) for item in items),
        },
        "ranking": snapshot.get("ranking") or {},
    }


def decision_public(row: Any) -> dict[str, Any]:
    raw = dict(row or {})
    options = [dict(item) for item in _array(raw.get("options")) if isinstance(item, dict)]
    status = str(raw.get("status") or "open")
    result = {
        "source": "decision",
        "id": str(raw.get("id") or ""),
        "owner_email": str(raw.get("owner_email") or ""),
        "session_id": str(raw["session_id"]) if raw.get("session_id") else None,
        "observation_id": str(raw.get("observation_id") or ""),
        "decision_kind": str(raw.get("decision_kind") or "choice"),
        "question": str(raw.get("question") or ""),
        "title": str(raw.get("question") or "Decision needed"),
        "summary": str(raw.get("summary") or ""),
        "options": options,
        "recommended_option": raw.get("recommended_option"),
        "urgency": str(raw.get("urgency") or "normal"),
        "evidence": _array(raw.get("evidence")),
        "context": _object(raw.get("context")),
        "status": status,
        "interaction": "choose" if status == "open" else "receipt",
        "selected_option": raw.get("selected_option"),
        "response_note": str(raw.get("response_note") or ""),
        "revision": int(raw.get("revision") or 1),
        "due_at": _iso(raw.get("due_at")),
        "expires_at": _iso(raw.get("expires_at")),
        "created_at": _iso(raw.get("created_at")),
        "updated_at": _iso(raw.get("updated_at")),
        "answered_at": _iso(raw.get("answered_at")),
        "closed_at": _iso(raw.get("closed_at")),
    }
    result["board_projection"] = {
        "board": "calliope-decisions",
        "card_id": f"decision:{result['id']}",
        "status": "blocked" if status == "open" else "done",
        "block_kind": "decision",
        "interaction": "choose" if status == "open" else "receipt",
    }
    return result


def _event(
    conn: Any,
    decision_id: str,
    event_kind: str,
    actor: str,
    revision: int,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO rvbbit.calliope_decision_events "
        "(decision_id,event_kind,actor_email,revision,payload) "
        "VALUES (%s::uuid,%s,%s,%s,%s::jsonb)",
        (decision_id, event_kind, actor, revision, json.dumps(payload, default=str)),
    )


def create_from_observation(
    conn_factory: Callable[..., Any],
    session_id: Any,
    observation_id: Any,
    question: Any,
    options: Any,
    *,
    summary: Any = "",
    decision_kind: Any = "choice",
    recommended_option: Any = None,
    urgency: Any = "normal",
    context: Any = None,
    due_at: Any = None,
    expires_at: Any = None,
    created_by: Any = "calliope@system",
) -> dict[str, Any]:
    """Create or revise one open owner decision grounded to an observation."""
    sid = _uuid(session_id, "session id")
    oid = _uuid(observation_id, "observation id")
    clean_question = _text(question, 500)
    if not clean_question:
        raise ValueError("decision question is required")
    clean_options = normalize_options(options)
    option_keys = {item["key"] for item in clean_options}
    recommended = str(recommended_option or "").strip().lower() or None
    if recommended is not None and recommended not in option_keys:
        raise ValueError("recommended_option must match one of the option keys")
    kind = str(decision_kind or "choice").strip().lower()
    if kind not in DECISION_KINDS:
        raise ValueError("decision_kind must be approval, choice, exception, or confirmation")
    clean_urgency = str(urgency or "normal").strip().lower()
    if clean_urgency not in URGENCIES:
        raise ValueError("urgency must be low, normal, high, or critical")
    clean_summary = _text(summary, 4_000)
    clean_context = _bounded_object(context, "decision context")
    parsed_due = _timestamp(due_at, "due_at")
    parsed_expiry = _timestamp(expires_at, "expires_at")
    if parsed_due and parsed_expiry and parsed_expiry < parsed_due:
        raise ValueError("expires_at cannot be earlier than due_at")
    actor = _owner(created_by)
    with conn_factory() as conn:
        with conn.transaction():
            session = conn.execute(
                "SELECT id,owner_email FROM rvbbit.calliope_sessions WHERE id=%s::uuid",
                (sid,),
            ).fetchone()
            if not session:
                raise LookupError("Calliope session not found")
            owner = _owner(session.get("owner_email"))
            observation = conn.execute(
                "SELECT * FROM rvbbit.calliope_observations WHERE id=%s::uuid "
                "AND state='active' AND (scope_kind='company' OR "
                "(scope_kind='personal' AND owner_email=%s))",
                (oid, owner),
            ).fetchone()
            if not observation:
                raise LookupError("active observation not found")
            evidence = [
                item for item in _bounded_array(
                    observation.get("evidence"), "observation evidence"
                )
                if isinstance(item, dict)
                and (
                    item.get("evidence_role") == "direct"
                    or (
                        item.get("admission_method") == "rvbbit.clover_relevance"
                        and float(item.get("relevance_score") or 0)
                        >= EVIDENCE_RELEVANCE_MIN
                    )
                )
            ]
            if not evidence:
                raise ValueError(
                    "observation has no independently admitted evidence"
                )
            merged_context = {
                **clean_context,
                "observation": {
                    "id": oid,
                    "fingerprint": observation.get("fingerprint"),
                    "scope_kind": observation.get("scope_kind"),
                    "kind": observation.get("kind"),
                    "title": observation.get("title"),
                    "state_revision": int(observation.get("state_revision") or 0),
                },
            }
            existing = conn.execute(
                "SELECT * FROM rvbbit.calliope_decision_requests "
                "WHERE owner_email=%s AND observation_id=%s::uuid AND status='open' FOR UPDATE",
                (owner, oid),
            ).fetchone()
            contract = {
                "session_id": sid,
                "decision_kind": kind,
                "question": clean_question,
                "summary": clean_summary,
                "options": clean_options,
                "recommended_option": recommended,
                "urgency": clean_urgency,
                "evidence": evidence,
                "context": merged_context,
                "due_at": parsed_due,
                "expires_at": parsed_expiry,
            }
            if existing:
                existing_contract = {
                    "session_id": str(existing.get("session_id") or ""),
                    "decision_kind": existing.get("decision_kind"),
                    "question": existing.get("question"),
                    "summary": existing.get("summary"),
                    "options": _array(existing.get("options")),
                    "recommended_option": existing.get("recommended_option"),
                    "urgency": existing.get("urgency"),
                    "evidence": _array(existing.get("evidence")),
                    "context": _object(existing.get("context")),
                    "due_at": _timestamp(existing.get("due_at"), "due_at"),
                    "expires_at": _timestamp(existing.get("expires_at"), "expires_at"),
                }
                unchanged = all(
                    existing_contract.get(key) == value
                    for key, value in contract.items()
                )
                if unchanged:
                    return {"changed": False, "decision": decision_public(existing)}
                revision = int(existing.get("revision") or 1) + 1
                row = conn.execute(
                    "UPDATE rvbbit.calliope_decision_requests SET "
                    "session_id=%s::uuid,decision_kind=%s,question=%s,summary=%s,"
                    "options=%s::jsonb,recommended_option=%s,urgency=%s,evidence=%s::jsonb,"
                    "context=%s::jsonb,due_at=%s,expires_at=%s,revision=%s,updated_at=now() "
                    "WHERE id=%s::uuid RETURNING *",
                    (
                        sid, kind, clean_question, clean_summary, json.dumps(clean_options),
                        recommended, clean_urgency, json.dumps(evidence, default=str),
                        json.dumps(merged_context, default=str), parsed_due, parsed_expiry,
                        revision, str(existing["id"]),
                    ),
                ).fetchone()
                event_kind = "updated"
            else:
                decision_id = str(uuid.uuid4())
                revision = 1
                row = conn.execute(
                    "INSERT INTO rvbbit.calliope_decision_requests "
                    "(id,owner_email,session_id,observation_id,decision_kind,question,summary,"
                    "options,recommended_option,urgency,evidence,context,revision,created_by,"
                    "due_at,expires_at) VALUES "
                    "(%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,"
                    "%s::jsonb,%s,%s,%s,%s) RETURNING *",
                    (
                        decision_id, owner, sid, oid, kind, clean_question, clean_summary,
                        json.dumps(clean_options), recommended, clean_urgency,
                        json.dumps(evidence, default=str),
                        json.dumps(merged_context, default=str), revision, actor,
                        parsed_due, parsed_expiry,
                    ),
                ).fetchone()
                event_kind = "created"
            _event(
                conn,
                str(row["id"]),
                event_kind,
                actor,
                revision,
                {
                    "observation_id": oid,
                    "observation_state_revision": int(observation.get("state_revision") or 0),
                    "option_keys": [item["key"] for item in clean_options],
                    "recommended_option": recommended,
                },
            )
    return {"changed": True, "decision": decision_public(row)}


def expire_due(conn_factory: Callable[..., Any], owner_email: Any = None) -> int:
    owner = _owner(owner_email) if owner_email else None
    with conn_factory() as conn:
        with conn.transaction():
            rows = conn.execute(
                "UPDATE rvbbit.calliope_decision_requests SET status='expired',"
                "revision=revision+1,updated_at=now(),closed_at=now() "
                "WHERE status='open' AND expires_at IS NOT NULL AND expires_at<=now() "
                + ("AND owner_email=%s " if owner else "")
                + "RETURNING id,owner_email,revision,expires_at",
                (owner,) if owner else (),
            ).fetchall()
            for row in rows:
                _event(
                    conn,
                    str(row["id"]),
                    "expired",
                    "calliope@system",
                    int(row["revision"]),
                    {"expires_at": _iso(row.get("expires_at"))},
                )
    return len(rows)


def answer(
    conn_factory: Callable[..., Any],
    owner_email: Any,
    decision_id: Any,
    selected_option: Any,
    *,
    note: Any = "",
    expected_revision: Any = None,
) -> dict[str, Any]:
    owner = _owner(owner_email)
    did = _uuid(decision_id, "decision id")
    choice = str(selected_option or "").strip().lower()
    clean_note = _text(note, 2_000)
    if expected_revision in (None, ""):
        clean_expected_revision = None
    else:
        try:
            clean_expected_revision = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("revision must be an integer") from exc
        if clean_expected_revision < 1:
            raise ValueError("revision must be positive")
    expire_due(conn_factory, owner)
    with conn_factory() as conn:
        with conn.transaction():
            current = conn.execute(
                "SELECT * FROM rvbbit.calliope_decision_requests "
                "WHERE id=%s::uuid AND owner_email=%s FOR UPDATE",
                (did, owner),
            ).fetchone()
            if not current:
                raise LookupError("decision not found")
            if current.get("status") == "answered":
                if current.get("selected_option") == choice and str(
                    current.get("response_note") or ""
                ) == clean_note:
                    return {"changed": False, "decision": decision_public(current)}
                raise DecisionConflict("decision has already been answered")
            if current.get("status") != "open":
                raise DecisionConflict(f"decision is {current.get('status')}")
            current_revision = int(current.get("revision") or 1)
            if (
                clean_expected_revision is not None
                and clean_expected_revision != current_revision
            ):
                raise DecisionRevisionConflict(
                    "decision changed after it was shown; review the latest choices"
                )
            option_keys = {
                str(item.get("key") or "")
                for item in _array(current.get("options")) if isinstance(item, dict)
            }
            if choice not in option_keys:
                raise ValueError("selected_option must match one of the decision option keys")
            revision = current_revision + 1
            row = conn.execute(
                "UPDATE rvbbit.calliope_decision_requests SET status='answered',"
                "selected_option=%s,response_note=%s,answered_by=%s,answered_at=now(),"
                "closed_at=now(),revision=%s,updated_at=now() WHERE id=%s::uuid RETURNING *",
                (choice, clean_note, owner, revision, did),
            ).fetchone()
            _event(
                conn,
                did,
                "answered",
                owner,
                revision,
                {"selected_option": choice, "note": clean_note},
            )
    return {"changed": True, "decision": decision_public(row)}


def cancel(
    conn_factory: Callable[..., Any],
    owner_email: Any,
    decision_id: Any,
    *,
    note: Any = "",
) -> dict[str, Any]:
    owner = _owner(owner_email)
    did = _uuid(decision_id, "decision id")
    clean_note = _text(note, 2_000)
    with conn_factory() as conn:
        with conn.transaction():
            current = conn.execute(
                "SELECT * FROM rvbbit.calliope_decision_requests "
                "WHERE id=%s::uuid AND owner_email=%s FOR UPDATE",
                (did, owner),
            ).fetchone()
            if not current:
                raise LookupError("decision not found")
            if current.get("status") == "cancelled":
                return {"changed": False, "decision": decision_public(current)}
            if current.get("status") != "open":
                raise DecisionConflict(f"decision is {current.get('status')}")
            revision = int(current.get("revision") or 1) + 1
            row = conn.execute(
                "UPDATE rvbbit.calliope_decision_requests SET status='cancelled',"
                "response_note=%s,closed_at=now(),revision=%s,updated_at=now() "
                "WHERE id=%s::uuid RETURNING *",
                (clean_note, revision, did),
            ).fetchone()
            _event(conn, did, "cancelled", owner, revision, {"note": clean_note})
    return {"changed": True, "decision": decision_public(row)}


def snapshot(
    conn_factory: Callable[..., Any],
    owner_email: Any,
    *,
    include_resolved: bool = False,
    limit: Any = 100,
) -> dict[str, Any]:
    owner = _owner(owner_email)
    try:
        bounded_limit = max(1, min(int(limit or 100), 300))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    expire_due(conn_factory, owner)
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT * FROM rvbbit.calliope_decision_requests WHERE owner_email=%s "
            + ("" if include_resolved else "AND status='open' ")
            + "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,"
            "CASE urgency WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'normal' THEN 2 ELSE 3 END,coalesce(due_at,'infinity'::timestamptz),"
            "updated_at DESC LIMIT %s",
            (owner, bounded_limit),
        ).fetchall()
        counts = conn.execute(
            "SELECT status,count(*)::int AS n FROM rvbbit.calliope_decision_requests "
            "WHERE owner_email=%s GROUP BY status",
            (owner,),
        ).fetchall()
    by_status = {str(row["status"]): int(row["n"]) for row in counts}
    return {
        "decisions": [decision_public(row) for row in rows],
        "counts": {"open": by_status.get("open", 0), "by_status": by_status},
    }


def queue(
    conn_factory: Callable[..., Any], owner_email: Any, *, limit: Any = 100,
) -> dict[str, Any]:
    """Project native decisions and provenanced external blockers into one queue."""
    owner = _owner(owner_email)
    try:
        bounded_limit = max(1, min(int(limit or 100), 300))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    projected_decisions = snapshot(
        conn_factory, owner, include_resolved=True, limit=min(300, bounded_limit * 2),
    )["decisions"]
    native = [item for item in projected_decisions if item.get("status") == "open"]
    receipts = [item for item in projected_decisions if item.get("status") != "open"][
        :min(50, bounded_limit)
    ]
    external: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    try:
        with conn_factory() as conn:
            work_rows = conn.execute(
                "SELECT id,title,summary,urgency,session_id,source,source_ref,context,"
                "created_at,updated_at FROM rvbbit.calliope_work_items "
                "WHERE owner_email=%s AND kind='blocked' AND state IN ('unread','seen') "
                "ORDER BY updated_at DESC LIMIT %s",
                (owner, bounded_limit),
            ).fetchall()
        external.extend({
            "source": "work",
            "id": str(row["id"]),
            "title": str(row.get("title") or "Blocked work"),
            "summary": str(row.get("summary") or ""),
            "urgency": str(row.get("urgency") or "normal"),
            "status": "blocked",
            "interaction": "investigate",
            "session_id": str(row["session_id"]) if row.get("session_id") else None,
            "context": _object(row.get("context")),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
            "board_projection": {
                "board": "calliope-work",
                "card_id": f"work:{row['id']}",
                "status": "blocked",
                "block_kind": "investigation",
                "interaction": "investigate",
            },
        } for row in work_rows)
    except Exception as exc:  # mixed-version installs may not have this source yet
        warnings.append({"source": "work", "message": _text(exc, 300)})
    try:
        with conn_factory() as conn:
            kanban_rows = conn.execute(
                "SELECT DISTINCT ON (t.board,t.id) t.board,t.id,t.title,t.body,t.status,"
                "t.block_kind,t.assignee,t.priority,t.session_id,t.created_at,t.synced_at "
                "FROM rvbbit.hermes_kanban_tasks t JOIN rvbbit.calliope_sessions s "
                "ON s.hermes_session_id::text=t.session_id WHERE lower(s.owner_email)=lower(%s) "
                "AND t.status IN ('blocked','triage') "
                "ORDER BY t.board,t.id,t.synced_at DESC LIMIT %s",
                (owner, bounded_limit),
            ).fetchall()
        external.extend({
            "source": "kanban",
            "id": f"{row.get('board')}:{row.get('id')}",
            "title": str(row.get("title") or "Blocked card"),
            "summary": _text(row.get("body"), 2_000),
            "urgency": "high" if row.get("status") == "triage" else "normal",
            "status": str(row.get("status") or "blocked"),
            "interaction": "investigate",
            "session_id": str(row["session_id"]) if row.get("session_id") else None,
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("synced_at")),
            "board_projection": {
                "board": row.get("board"),
                "card_id": row.get("id"),
                "status": row.get("status"),
                "block_kind": row.get("block_kind") or "external",
                "interaction": "investigate",
            },
        } for row in kanban_rows)
    except Exception as exc:  # Hermes kanban mirror is optional
        warnings.append({"source": "kanban", "message": _text(exc, 300)})
    urgency_rank = {"critical": 4, "high": 3, "normal": 2, "low": 1}
    items = [*native, *external]
    items.sort(
        key=lambda item: (
            urgency_rank.get(str(item.get("urgency") or "normal"), 2),
            str(item.get("updated_at") or item.get("created_at") or ""),
        ),
        reverse=True,
    )
    items = items[:bounded_limit]
    by_source: dict[str, int] = {}
    for item in items:
        source = str(item.get("source") or "other")
        by_source[source] = by_source.get(source, 0) + 1
    result = {
        "items": items,
        "receipts": receipts,
        "counts": {
            "open": len(items),
            "actionable": sum(item.get("interaction") == "choose" for item in items),
            "receipts": len(receipts),
            "by_source": by_source,
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result
