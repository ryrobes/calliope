"""Durable, evidence-backed observations shared by Calliope surfaces.

Dream cycles are one producer of observations, not their lifetime owner.  This
module keeps one stable current row per scope/fingerprint plus an append-only,
idempotent occurrence ledger.  The front door, Work, Dreaming, and future DLT
remediation can therefore consume the same grounded object without scraping a
particular Dream run.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


OBSERVATION_KINDS = {
    "friction", "repetition", "connection", "gap", "success", "change",
}
OBSERVATION_STATES = {"active", "resolved", "stale", "suppressed"}
SCOPE_KINDS = {"company", "personal"}
FEEDBACK_VERDICTS = {"relevant", "not_relevant", "following"}
_OBSERVATION_NAMESPACE = uuid.UUID("ca1100e0-0000-4000-8000-000000000201")
_COMPANY_AUDIENCE = "company"
_RANK_LIMIT = 120
_RANKER_MODEL = "rvbbit.clover_relevance"
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_WAKE = threading.Event()


DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_observations (
    id uuid PRIMARY KEY,
    fingerprint text NOT NULL,
    scope_kind text NOT NULL DEFAULT 'company',
    owner_email text,
    kind text NOT NULL,
    title text NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    entities jsonb NOT NULL DEFAULT '[]'::jsonb,
    signal_count integer NOT NULL DEFAULT 1,
    recurrence_count integer NOT NULL DEFAULT 1,
    confidence numeric(5,4) NOT NULL DEFAULT 0.5,
    urgency numeric(5,4) NOT NULL DEFAULT 0.5,
    state text NOT NULL DEFAULT 'active',
    state_revision integer NOT NULL DEFAULT 0,
    state_changed_by text,
    state_changed_at timestamptz,
    sensitivity text NOT NULL DEFAULT 'internal',
    first_cycle_id uuid REFERENCES rvbbit.calliope_dream_cycles(id) ON DELETE SET NULL,
    latest_cycle_id uuid REFERENCES rvbbit.calliope_dream_cycles(id) ON DELETE SET NULL,
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observations_scope_check CHECK (
        (scope_kind='company' AND owner_email IS NULL) OR
        (scope_kind='personal' AND owner_email IS NOT NULL
         AND owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%')
    ),
    CONSTRAINT calliope_observations_kind_check CHECK (
        kind IN ('friction','repetition','connection','gap','success','change')
    ),
    CONSTRAINT calliope_observations_state_check CHECK (
        state IN ('active','resolved','stale','suppressed')
    ),
    CONSTRAINT calliope_observations_evidence_check CHECK (jsonb_typeof(evidence)='array'),
    CONSTRAINT calliope_observations_entities_check CHECK (jsonb_typeof(entities)='array'),
    CONSTRAINT calliope_observations_counts_check CHECK (
        signal_count>=1 AND recurrence_count>=1
    ),
    CONSTRAINT calliope_observations_scores_check CHECK (
        confidence BETWEEN 0 AND 1 AND urgency BETWEEN 0 AND 1
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_observations_scope_fingerprint_key
    ON rvbbit.calliope_observations (scope_kind,coalesce(owner_email,''),fingerprint);
CREATE INDEX IF NOT EXISTS calliope_observations_active_idx
    ON rvbbit.calliope_observations
       (scope_kind,owner_email,state,urgency DESC,confidence DESC,last_observed_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_events (
    event_id bigserial PRIMARY KEY,
    observation_id uuid NOT NULL REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    source_kind text NOT NULL,
    source_ref text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observation_events_kind_check CHECK (
        event_kind IN ('observed','resolved','reopened','stale','suppressed')
    ),
    CONSTRAINT calliope_observation_events_payload_check CHECK (jsonb_typeof(payload)='object'),
    CONSTRAINT calliope_observation_events_source_key
        UNIQUE (observation_id,event_kind,source_kind,source_ref)
);
CREATE INDEX IF NOT EXISTS calliope_observation_events_observation_idx
    ON rvbbit.calliope_observation_events (observation_id,created_at DESC,event_id DESC);

ALTER TABLE rvbbit.calliope_observations
    ADD COLUMN IF NOT EXISTS state_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS state_changed_by text,
    ADD COLUMN IF NOT EXISTS state_changed_at timestamptz;

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_feedback (
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    verdict text NOT NULL,
    note text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id,owner_email),
    CONSTRAINT calliope_observation_feedback_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_observation_feedback_verdict_check CHECK (
        verdict IN ('relevant','not_relevant','following')
    ),
    CONSTRAINT calliope_observation_feedback_revision_check CHECK (revision>=1)
);
CREATE INDEX IF NOT EXISTS calliope_observation_feedback_owner_idx
    ON rvbbit.calliope_observation_feedback (owner_email,updated_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_feedback_events (
    event_id bigserial PRIMARY KEY,
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    event_kind text NOT NULL,
    note text NOT NULL DEFAULT '',
    revision integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observation_feedback_events_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_observation_feedback_events_kind_check CHECK (
        event_kind IN ('relevant','not_relevant','following','cleared')
    ),
    CONSTRAINT calliope_observation_feedback_events_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_observation_feedback_events_revision_key
        UNIQUE (observation_id,owner_email,revision)
);
CREATE INDEX IF NOT EXISTS calliope_observation_feedback_events_owner_idx
    ON rvbbit.calliope_observation_feedback_events
       (owner_email,created_at DESC,event_id DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_rankings (
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    audience_key text NOT NULL,
    relevance numeric(5,4) NOT NULL,
    priority numeric(5,4) NOT NULL,
    surface_role text NOT NULL,
    factors jsonb NOT NULL DEFAULT '{}'::jsonb,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    criterion_hash text NOT NULL,
    profile_version integer NOT NULL DEFAULT 0,
    ranker text NOT NULL,
    model text,
    ranked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id,audience_key),
    CONSTRAINT calliope_observation_rankings_audience_check CHECK (
        audience_key='company' OR
        (audience_key=lower(btrim(audience_key)) AND audience_key LIKE '%@%')
    ),
    CONSTRAINT calliope_observation_rankings_scores_check CHECK (
        relevance BETWEEN 0 AND 1 AND priority BETWEEN 0 AND 1
    ),
    CONSTRAINT calliope_observation_rankings_role_check CHECK (
        surface_role IN ('lead','supporting','background')
    ),
    CONSTRAINT calliope_observation_rankings_factors_check CHECK (
        jsonb_typeof(factors)='object' AND jsonb_typeof(reason_codes)='array'
    )
);
CREATE INDEX IF NOT EXISTS calliope_observation_rankings_audience_idx
    ON rvbbit.calliope_observation_rankings
       (audience_key,priority DESC,ranked_at DESC);

ALTER TABLE rvbbit.calliope_dream_observations
    ADD COLUMN IF NOT EXISTS stable_observation_id uuid
        REFERENCES rvbbit.calliope_observations(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS calliope_dream_observations_stable_idx
    ON rvbbit.calliope_dream_observations (stable_observation_id,created_at DESC)
    WHERE stable_observation_id IS NOT NULL;
"""


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit].rstrip()


def _array(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _score(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in {float("inf"), float("-inf")}:
        number = default
    return round(max(0.0, min(number, 1.0)), 4)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


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


def _owner(value: Any) -> str:
    owner = str(value or "").strip().lower()
    if not owner or "@" not in owner:
        raise ValueError("a valid owner email is required")
    return owner


def _uuid(value: Any, label: str = "observation id") -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_scope(scope_kind: Any, owner_email: Any = None) -> tuple[str, str | None]:
    scope = str(scope_kind or "company").strip().lower()
    if scope not in SCOPE_KINDS:
        raise ValueError("observation scope must be company or personal")
    owner = str(owner_email or "").strip().lower() or None
    if scope == "personal":
        if not owner or "@" not in owner:
            raise ValueError("personal observations require an owner email")
        return scope, owner
    return scope, None


def stable_id(scope_kind: str, owner_email: str | None, fingerprint: str) -> str:
    return str(uuid.uuid5(
        _OBSERVATION_NAMESPACE,
        f"{scope_kind}:{owner_email or ''}:{fingerprint}",
    ))


def observation_public(row: Any) -> dict[str, Any]:
    item = dict(row or {})
    result = {
        "id": str(item.get("id") or ""),
        "fingerprint": str(item.get("fingerprint") or ""),
        "scope_kind": str(item.get("scope_kind") or "company"),
        "kind": str(item.get("kind") or "change"),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "evidence": _array(item.get("evidence")),
        "entities": _array(item.get("entities")),
        "signal_count": int(item.get("signal_count") or 0),
        "recurrence_count": int(item.get("recurrence_count") or 0),
        "confidence": float(item.get("confidence") or 0),
        "urgency": float(item.get("urgency") or 0),
        "state": str(item.get("state") or "active"),
        "state_revision": int(item.get("state_revision") or 0),
        "state_changed_at": _iso(item.get("state_changed_at")),
        "sensitivity": str(item.get("sensitivity") or "internal"),
        "first_observed_at": _iso(item.get("first_observed_at")),
        "last_observed_at": _iso(item.get("last_observed_at")),
        "updated_at": _iso(item.get("updated_at")),
    }
    feedback = str(item.get("viewer_feedback_verdict") or "").strip()
    if feedback:
        result["viewer_feedback"] = {
            "verdict": feedback,
            "note": str(item.get("viewer_feedback_note") or ""),
            "revision": int(item.get("viewer_feedback_revision") or 0),
            "updated_at": _iso(item.get("viewer_feedback_updated_at")),
        }
    else:
        result["viewer_feedback"] = None
    audience = str(item.get("ranking_audience_key") or "").strip()
    if audience:
        result["ranking"] = {
            "audience": "personal" if "@" in audience else "company",
            "relevance": float(item.get("ranking_relevance") or 0),
            "priority": float(item.get("ranking_priority") or 0),
            "surface_role": str(item.get("ranking_surface_role") or "background"),
            "factors": _object(item.get("ranking_factors")),
            "reason_codes": _array(item.get("ranking_reason_codes")),
            "ranker": str(item.get("ranking_ranker") or "deterministic"),
            "model": item.get("ranking_model"),
            "ranked_at": _iso(item.get("ranking_ranked_at")),
        }
    else:
        result["ranking"] = None
    return result


def persist_cycle_observations(
    conn: Any,
    *,
    cycle_id: str,
    scope_kind: str,
    owner_email: str | None,
    observations: list[dict[str, Any]],
    observed_at: datetime | None = None,
) -> dict[str, str]:
    """Upsert one Dream cycle's observations and append idempotent occurrences."""
    scope_kind, owner_email = normalize_scope(scope_kind, owner_email)
    seen_at = observed_at or datetime.now(timezone.utc)
    result: dict[str, str] = {}
    for item in observations:
        fingerprint = _text(item.get("fingerprint"), 160)
        title = _text(item.get("title"), 220)
        summary = _text(item.get("summary"), 1_200)
        kind = str(item.get("kind") or "change").strip().lower()
        if not fingerprint or not title or not summary:
            continue
        if kind not in OBSERVATION_KINDS:
            kind = "change"
        observation_id = stable_id(scope_kind, owner_email, fingerprint)
        evidence = _array(item.get("evidence"))[:12]
        entities = list(dict.fromkeys(
            _text(value, 160) for value in _array(item.get("entities")) if _text(value, 160)
        ))[:12]
        signal_count = max(1, min(int(item.get("signal_count") or len(evidence) or 1), 9999))
        confidence = _score(item.get("confidence"), 0.5)
        previous = conn.execute(
            "SELECT state,state_revision FROM rvbbit.calliope_observations "
            "WHERE scope_kind=%s AND owner_email IS NOT DISTINCT FROM %s AND fingerprint=%s",
            (scope_kind, owner_email, fingerprint),
        ).fetchone() or {}
        row = conn.execute(
            "INSERT INTO rvbbit.calliope_observations "
            "(id,fingerprint,scope_kind,owner_email,kind,title,summary,evidence,entities,"
            "signal_count,confidence,sensitivity,first_cycle_id,latest_cycle_id,"
            "first_observed_at,last_observed_at) "
            "VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,"
            "%s::uuid,%s::uuid,%s,%s) "
            "ON CONFLICT (scope_kind,(coalesce(owner_email,'')),fingerprint) DO UPDATE SET "
            "kind=EXCLUDED.kind,title=EXCLUDED.title,summary=EXCLUDED.summary,"
            "evidence=EXCLUDED.evidence,entities=EXCLUDED.entities,"
            "signal_count=EXCLUDED.signal_count,confidence=EXCLUDED.confidence,"
            "recurrence_count=rvbbit.calliope_observations.recurrence_count+CASE WHEN "
            "rvbbit.calliope_observations.latest_cycle_id IS DISTINCT FROM EXCLUDED.latest_cycle_id "
            "THEN 1 ELSE 0 END,latest_cycle_id=EXCLUDED.latest_cycle_id,"
            "last_observed_at=GREATEST(rvbbit.calliope_observations.last_observed_at,"
            "EXCLUDED.last_observed_at),state=CASE WHEN rvbbit.calliope_observations.state='suppressed' "
            "THEN 'suppressed' ELSE 'active' END,"
            "state_revision=rvbbit.calliope_observations.state_revision+CASE WHEN "
            "rvbbit.calliope_observations.state IN ('resolved','stale') THEN 1 ELSE 0 END,"
            "state_changed_by=CASE WHEN rvbbit.calliope_observations.state IN ('resolved','stale') "
            "THEN 'calliope@system' ELSE rvbbit.calliope_observations.state_changed_by END,"
            "state_changed_at=CASE WHEN rvbbit.calliope_observations.state IN ('resolved','stale') "
            "THEN now() ELSE rvbbit.calliope_observations.state_changed_at END,"
            "updated_at=now() RETURNING *",
            (
                observation_id, fingerprint, scope_kind, owner_email, kind, title, summary,
                json.dumps(evidence, default=str), json.dumps(entities, default=str),
                signal_count, confidence, "private" if scope_kind == "personal" else "internal",
                cycle_id, cycle_id, seen_at, seen_at,
            ),
        ).fetchone()
        if not row:
            continue
        stable = str(row["id"])
        local_id = str(item.get("id") or fingerprint)
        result[local_id] = stable
        conn.execute(
            "INSERT INTO rvbbit.calliope_observation_events "
            "(observation_id,event_kind,source_kind,source_ref,payload) "
            "VALUES (%s::uuid,'observed','dream_cycle',%s,%s::jsonb) "
            "ON CONFLICT (observation_id,event_kind,source_kind,source_ref) DO NOTHING",
            (
                stable, cycle_id, json.dumps({
                    "kind": kind,
                    "title": title,
                    "summary": summary,
                    "evidence": evidence,
                    "entities": entities,
                    "signal_count": signal_count,
                    "confidence": confidence,
                }, default=str),
            ),
        )
        if str(previous.get("state") or "") in {"resolved", "stale"}:
            conn.execute(
                "INSERT INTO rvbbit.calliope_observation_events "
                "(observation_id,event_kind,source_kind,source_ref,payload) "
                "VALUES (%s::uuid,'reopened','dream_cycle',%s,%s::jsonb) "
                "ON CONFLICT (observation_id,event_kind,source_kind,source_ref) DO NOTHING",
                (
                    stable,
                    cycle_id,
                    json.dumps({
                        "previous_state": str(previous.get("state")),
                        "state": "active",
                        "state_revision": int(row.get("state_revision") or 0),
                        "reason": "observation recurred in a later Dream cycle",
                    }),
                ),
            )
    return result


def backfill_dream_observations(conn: Any) -> int:
    """Link legacy Dream observations into the stable ledger, oldest first.

    The stable foreign key is the completion marker, so the backfill is cheap
    after its first successful run and safe to retry inside startup's schema
    transaction.
    """
    rows = conn.execute(
        "SELECT d.id AS dream_observation_id,d.cycle_id,d.fingerprint,d.kind,d.title,"
        "d.summary,d.evidence,d.entities,d.signal_count,d.confidence,c.scope_kind,"
        "c.owner_email,coalesce(c.completed_at,d.created_at,c.started_at) AS observed_at "
        "FROM rvbbit.calliope_dream_observations d "
        "JOIN rvbbit.calliope_dream_cycles c ON c.id=d.cycle_id "
        "WHERE d.stable_observation_id IS NULL "
        "ORDER BY c.started_at,d.created_at,d.id"
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        cycle_id = str(row.get("cycle_id") or "")
        if not cycle_id:
            continue
        group = grouped.setdefault(cycle_id, {
            "scope_kind": row.get("scope_kind") or "company",
            "owner_email": row.get("owner_email"),
            "observed_at": row.get("observed_at"),
            "items": [],
        })
        group["items"].append({
            "id": str(row.get("dream_observation_id") or ""),
            "fingerprint": row.get("fingerprint"),
            "kind": row.get("kind"),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "evidence": row.get("evidence"),
            "entities": row.get("entities"),
            "signal_count": row.get("signal_count"),
            "confidence": row.get("confidence"),
        })
    linked = 0
    for cycle_id, group in grouped.items():
        stable_ids = persist_cycle_observations(
            conn,
            cycle_id=cycle_id,
            scope_kind=group["scope_kind"],
            owner_email=group["owner_email"],
            observations=group["items"],
            observed_at=group["observed_at"],
        )
        for item in group["items"]:
            local_id = str(item["id"])
            stable = stable_ids.get(local_id)
            if not stable:
                continue
            conn.execute(
                "UPDATE rvbbit.calliope_dream_observations "
                "SET stable_observation_id=%s::uuid "
                "WHERE id=%s::uuid AND stable_observation_id IS NULL",
                (stable, local_id),
            )
            linked += 1
    return linked


def _visible_observation(
    conn: Any, observation_id: str, owner_email: str, *, for_update: bool = False,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM rvbbit.calliope_observations WHERE id=%s::uuid "
        "AND (scope_kind='company' OR (scope_kind='personal' AND owner_email=%s))"
        + (" FOR UPDATE" if for_update else ""),
        (observation_id, owner_email),
    ).fetchone()
    return dict(row) if row else None


def record_feedback(
    conn_factory: Callable[..., Any],
    owner_email: Any,
    observation_id: Any,
    verdict: Any,
    *,
    note: Any = "",
) -> dict[str, Any]:
    """Record owner-scoped relevance feedback without mutating shared truth."""
    owner = _owner(owner_email)
    oid = _uuid(observation_id)
    action = str(verdict or "").strip().lower()
    if action not in {*FEEDBACK_VERDICTS, "clear"}:
        raise ValueError("feedback must be relevant, not_relevant, following, or clear")
    clean_note = _text(note, 1_000)
    with conn_factory() as conn:
        with conn.transaction():
            observation = _visible_observation(conn, oid, owner)
            if not observation:
                raise LookupError("observation not found")
            current = conn.execute(
                "SELECT * FROM rvbbit.calliope_observation_feedback "
                "WHERE observation_id=%s::uuid AND owner_email=%s FOR UPDATE",
                (oid, owner),
            ).fetchone()
            current = dict(current) if current else {}
            if action != "clear" and (
                str(current.get("verdict") or "") == action
                and str(current.get("note") or "") == clean_note
            ):
                return {
                    "changed": False,
                    "observation": observation_public(observation),
                    "feedback": {
                        "verdict": action,
                        "note": clean_note,
                        "revision": int(current.get("revision") or 1),
                        "updated_at": _iso(current.get("updated_at")),
                    },
                }
            if action == "clear" and not current:
                return {
                    "changed": False,
                    "observation": observation_public(observation),
                    "feedback": None,
                }
            last = conn.execute(
                "SELECT coalesce(max(revision),0) AS revision "
                "FROM rvbbit.calliope_observation_feedback_events "
                "WHERE observation_id=%s::uuid AND owner_email=%s",
                (oid, owner),
            ).fetchone() or {}
            revision = int(last.get("revision") or 0) + 1
            if action == "clear":
                conn.execute(
                    "DELETE FROM rvbbit.calliope_observation_feedback "
                    "WHERE observation_id=%s::uuid AND owner_email=%s",
                    (oid, owner),
                )
                feedback = None
            else:
                row = conn.execute(
                    "INSERT INTO rvbbit.calliope_observation_feedback "
                    "(observation_id,owner_email,verdict,note,revision) "
                    "VALUES (%s::uuid,%s,%s,%s,%s) "
                    "ON CONFLICT (observation_id,owner_email) DO UPDATE SET "
                    "verdict=EXCLUDED.verdict,note=EXCLUDED.note,revision=EXCLUDED.revision,"
                    "updated_at=now() RETURNING *",
                    (oid, owner, action, clean_note, revision),
                ).fetchone()
                feedback = {
                    "verdict": str(row.get("verdict") or action),
                    "note": str(row.get("note") or ""),
                    "revision": int(row.get("revision") or revision),
                    "updated_at": _iso(row.get("updated_at")),
                }
            conn.execute(
                "INSERT INTO rvbbit.calliope_observation_feedback_events "
                "(observation_id,owner_email,event_kind,note,revision) "
                "VALUES (%s::uuid,%s,%s,%s,%s)",
                (oid, owner, "cleared" if action == "clear" else action, clean_note, revision),
            )
            conn.execute(
                "DELETE FROM rvbbit.calliope_observation_rankings "
                "WHERE observation_id=%s::uuid AND audience_key=%s",
                (oid, owner),
            )
    wake_worker()
    return {
        "changed": True,
        "observation": observation_public(observation),
        "feedback": feedback,
    }


def transition_state(
    conn_factory: Callable[..., Any],
    actor_email: Any,
    observation_id: Any,
    action: Any,
    *,
    note: Any = "",
    can_manage_company: bool = False,
) -> dict[str, Any]:
    """Apply an authorized observation lifecycle transition with a receipt."""
    actor = _owner(actor_email)
    oid = _uuid(observation_id)
    clean_action = str(action or "").strip().lower()
    target_by_action = {"resolve": "resolved", "suppress": "suppressed", "reopen": "active"}
    if clean_action not in target_by_action:
        raise ValueError("action must be resolve, suppress, or reopen")
    target = target_by_action[clean_action]
    clean_note = _text(note, 1_000)
    with conn_factory() as conn:
        with conn.transaction():
            observation = _visible_observation(conn, oid, actor, for_update=True)
            if not observation:
                raise LookupError("observation not found")
            if observation.get("scope_kind") == "company" and not can_manage_company:
                raise PermissionError("company observations may only be managed by an administrator")
            if (
                observation.get("scope_kind") == "personal"
                and str(observation.get("owner_email") or "").lower() != actor
            ):
                raise LookupError("observation not found")
            previous = str(observation.get("state") or "active")
            if previous == target:
                return {"changed": False, "observation": observation_public(observation)}
            revision = int(observation.get("state_revision") or 0) + 1
            row = conn.execute(
                "UPDATE rvbbit.calliope_observations SET state=%s,state_revision=%s,"
                "state_changed_by=%s,state_changed_at=now(),updated_at=now() "
                "WHERE id=%s::uuid RETURNING *",
                (target, revision, actor, oid),
            ).fetchone()
            event_kind = "reopened" if target == "active" else target
            conn.execute(
                "INSERT INTO rvbbit.calliope_observation_events "
                "(observation_id,event_kind,source_kind,source_ref,payload) "
                "VALUES (%s::uuid,%s,'human',%s,%s::jsonb)",
                (
                    oid,
                    event_kind,
                    f"{actor}:{revision}",
                    json.dumps({
                        "actor": actor,
                        "action": clean_action,
                        "previous_state": previous,
                        "state": target,
                        "state_revision": revision,
                        "note": clean_note,
                    }),
                ),
            )
            conn.execute(
                "DELETE FROM rvbbit.calliope_observation_rankings WHERE observation_id=%s::uuid",
                (oid,),
            )
    wake_worker()
    return {"changed": True, "observation": observation_public(row)}


def _criterion(conn: Any, audience_key: str) -> tuple[str, int]:
    generic = (
        "Material current business activity, operational changes, customer or revenue impact, "
        "active work, repeated friction, useful opportunities, and decisions worth attention."
    )
    if audience_key == _COMPANY_AUDIENCE:
        return generic, 0
    row = conn.execute(
        "SELECT version,context,user_guidance FROM rvbbit.calliope_user_dossiers "
        "WHERE owner_email=%s AND NOT paused",
        (audience_key,),
    ).fetchone()
    if not row:
        return generic, 0
    context = _object(row.get("context"))
    parts = [_text(context.get("summary"), 600)]
    for field in (
        "focus_areas", "active_threads", "recurring_questions", "frictions",
        "successful_patterns", "preferences", "open_loops",
    ):
        for raw in _array(context.get(field))[:6]:
            item = _object(raw)
            parts.append(" · ".join(filter(None, (
                _text(item.get("label"), 180), _text(item.get("detail"), 500)
            ))))
    guidance = _text(row.get("user_guidance"), 600)
    if guidance:
        parts.append(f"Explicit guidance: {guidance}")
    criterion = _text(". ".join(part for part in parts if part), 4_000)
    return criterion or generic, int(row.get("version") or 0)


def _rank_candidates(conn: Any, audience_key: str) -> list[dict[str, Any]]:
    if audience_key == _COMPANY_AUDIENCE:
        scope_sql = "o.scope_kind='company'"
        params: tuple[Any, ...] = ()
        feedback_sql = "NULL::text"
    else:
        scope_sql = "(o.scope_kind='company' OR (o.scope_kind='personal' AND o.owner_email=%s))"
        params = (audience_key, audience_key)
        feedback_sql = "f.verdict"
    return [dict(row) for row in conn.execute(
        "SELECT o.*," + feedback_sql + " AS viewer_feedback FROM rvbbit.calliope_observations o "
        + (
            "LEFT JOIN rvbbit.calliope_observation_feedback f ON f.observation_id=o.id "
            "AND f.owner_email=%s " if audience_key != _COMPANY_AUDIENCE else ""
        )
        + "WHERE o.state='active' AND " + scope_sql
        + " ORDER BY o.urgency DESC,o.confidence DESC,o.last_observed_at DESC LIMIT %s",
        (*params, _RANK_LIMIT),
    ).fetchall()]


def _clover_relevance(
    conn: Any, candidates: list[dict[str, Any]], criterion: str,
) -> dict[str, float]:
    available = conn.execute(
        "SELECT to_regprocedure('rvbbit.clover_relevance(text,text,jsonb)') IS NOT NULL AS ok"
    ).fetchone() or {}
    if not available.get("ok") or not candidates:
        return {}
    payload = [{
        "id": str(item["id"]),
        "body": _text(
            " ".join((
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                " ".join(str(value) for value in _array(item.get("entities"))),
            )),
            2_000,
        ),
    } for item in candidates]
    try:
        with conn.transaction():
            rows = conn.execute(
                "SELECT item.id,rvbbit.clover_relevance(item.body,%s,'{}'::jsonb) AS score "
                "FROM jsonb_to_recordset(%s::jsonb) AS item(id uuid,body text)",
                (criterion, json.dumps(payload)),
            ).fetchall()
    except Exception:
        return {}
    return {
        str(row["id"]): _score(row.get("score"), 0.5)
        for row in rows if row.get("score") is not None
    }


def _calibrate_relevance(scores: dict[str, float]) -> dict[str, float]:
    """Blend raw reranker scores with tie-aware within-audience percentiles."""
    if len(scores) <= 1:
        return dict(scores)
    ordered = sorted(scores.values())
    denominator = len(ordered) - 1
    calibrated: dict[str, float] = {}
    for observation_id, raw in scores.items():
        first = ordered.index(raw)
        last = len(ordered) - 1 - ordered[::-1].index(raw)
        percentile = ((first + last) / 2) / denominator
        calibrated[observation_id] = _score(raw * 0.30 + percentile * 0.70, 0.5)
    return calibrated


def _surface_role(position: int, total: int, feedback: str = "") -> str:
    """Keep front-door roles selective even when model scores are compressed."""
    if feedback == "not_relevant":
        return "background"
    if feedback == "following":
        return "lead"
    lead_limit = min(6, max(1, math.ceil(total * 0.10)))
    supporting_limit = min(24, max(lead_limit, math.ceil(total * 0.30)))
    if position < lead_limit:
        return "lead"
    if position < supporting_limit or feedback == "relevant":
        return "supporting"
    return "background"


def _ranking(
    item: dict[str, Any], relevance: float, *, used_clover: bool,
) -> tuple[float, str, dict[str, float], list[str]]:
    now = datetime.now(timezone.utc)
    observed_at = _datetime(item.get("last_observed_at")) or now
    age_days = max(0.0, (now - observed_at).total_seconds() / 86_400)
    recency = 1.0 / (1.0 + age_days / 14.0)
    recurrence = min(1.0, math.log1p(int(item.get("recurrence_count") or 1)) / math.log(9))
    signals = min(1.0, math.log1p(int(item.get("signal_count") or 1)) / math.log(21))
    urgency = _score(item.get("urgency"), 0.5)
    confidence = _score(item.get("confidence"), 0.5)
    feedback = str(item.get("viewer_feedback") or "")
    feedback_delta = {"following": 0.15, "relevant": 0.08, "not_relevant": -0.55}.get(
        feedback, 0.0
    )
    priority = _score(
        relevance * 0.35 + urgency * 0.20 + confidence * 0.20
        + recurrence * 0.10 + recency * 0.10 + signals * 0.05 + feedback_delta,
        0.5,
    )
    role = "lead" if priority >= 0.72 else "supporting" if priority >= 0.48 else "background"
    reasons = []
    if used_clover and relevance >= 0.7:
        reasons.append("profile_match")
    if int(item.get("recurrence_count") or 1) > 1:
        reasons.append("recurring")
    if urgency >= 0.7:
        reasons.append("urgent")
    if confidence >= 0.8:
        reasons.append("well_supported")
    if recency >= 0.8:
        reasons.append("recent")
    if feedback:
        reasons.append(f"feedback:{feedback}")
    factors = {
        "relevance": relevance,
        "urgency": urgency,
        "confidence": confidence,
        "recurrence": round(recurrence, 4),
        "recency": round(recency, 4),
        "signals": round(signals, 4),
        "feedback_delta": feedback_delta,
    }
    return priority, role, factors, reasons


def refresh_rankings(
    conn_factory: Callable[..., Any], audience_key: Any = _COMPANY_AUDIENCE,
) -> dict[str, Any]:
    audience = str(audience_key or _COMPANY_AUDIENCE).strip().lower()
    if audience != _COMPANY_AUDIENCE:
        audience = _owner(audience)
    with conn_factory() as conn:
        criterion, profile_version = _criterion(conn, audience)
        criterion_hash = hashlib.sha256(criterion.encode("utf-8")).hexdigest()
        candidates = _rank_candidates(conn, audience)
        raw_scores = _clover_relevance(conn, candidates, criterion)
        scores = _calibrate_relevance(raw_scores)
        used_clover = bool(raw_scores)
        ids = [str(item["id"]) for item in candidates]
        ranked_candidates = []
        for item in candidates:
            observation_id = str(item["id"])
            relevance = scores.get(observation_id, 0.5)
            priority, _, factors, reasons = _ranking(
                item, relevance, used_clover=used_clover,
            )
            if observation_id in raw_scores:
                factors["semantic_raw"] = round(raw_scores[observation_id], 4)
            ranked_candidates.append((priority, item, factors, reasons))
        ranked_candidates.sort(
            key=lambda entry: (
                entry[0],
                _score(entry[1].get("urgency"), 0.5),
                _score(entry[1].get("confidence"), 0.5),
                str(entry[1].get("last_observed_at") or ""),
            ),
            reverse=True,
        )
        with conn.transaction():
            if ids:
                conn.execute(
                    "DELETE FROM rvbbit.calliope_observation_rankings "
                    "WHERE audience_key=%s AND NOT (observation_id=ANY(%s::uuid[]))",
                    (audience, ids),
                )
            else:
                conn.execute(
                    "DELETE FROM rvbbit.calliope_observation_rankings WHERE audience_key=%s",
                    (audience,),
                )
            for position, (priority, item, factors, reasons) in enumerate(
                ranked_candidates
            ):
                observation_id = str(item["id"])
                relevance = scores.get(observation_id, 0.5)
                role = _surface_role(
                    position, len(ranked_candidates), str(item.get("viewer_feedback") or ""),
                )
                factors["rank_position"] = position + 1
                if role == "lead":
                    reasons.append("top_ranked")
                conn.execute(
                    "INSERT INTO rvbbit.calliope_observation_rankings "
                    "(observation_id,audience_key,relevance,priority,surface_role,factors,"
                    "reason_codes,criterion_hash,profile_version,ranker,model,ranked_at) "
                    "VALUES (%s::uuid,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,now()) "
                    "ON CONFLICT (observation_id,audience_key) DO UPDATE SET "
                    "relevance=EXCLUDED.relevance,priority=EXCLUDED.priority,"
                    "surface_role=EXCLUDED.surface_role,factors=EXCLUDED.factors,"
                    "reason_codes=EXCLUDED.reason_codes,criterion_hash=EXCLUDED.criterion_hash,"
                    "profile_version=EXCLUDED.profile_version,ranker=EXCLUDED.ranker,"
                    "model=EXCLUDED.model,ranked_at=now()",
                    (
                        observation_id, audience, relevance, priority, role,
                        json.dumps(factors), json.dumps(reasons), criterion_hash,
                        profile_version, "clover" if used_clover else "deterministic",
                        _RANKER_MODEL if used_clover else None,
                    ),
                )
    return {
        "audience": "personal" if audience != _COMPANY_AUDIENCE else "company",
        "ranked": len(candidates),
        "ranker": "clover" if used_clover else "deterministic",
        "model": _RANKER_MODEL if used_clover else None,
        "profile_version": profile_version,
        "criterion_hash": criterion_hash,
    }


def refresh_all_rankings(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    lock_key = 8_252_026_825
    with conn_factory() as lock_conn:
        acquired = lock_conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key,)
        ).fetchone() or {}
        if not acquired.get("acquired"):
            return {"skipped": True, "reason": "already_running", "audiences": []}
        try:
            owners = lock_conn.execute(
                "SELECT owner_email FROM rvbbit.calliope_user_dossiers WHERE NOT paused "
                "UNION SELECT owner_email FROM rvbbit.calliope_observations "
                "WHERE scope_kind='personal' AND owner_email IS NOT NULL ORDER BY owner_email"
            ).fetchall()
            audiences = [_COMPANY_AUDIENCE, *(
                str(row.get("owner_email") or "").strip().lower() for row in owners
                if row.get("owner_email")
            )]
            receipts = []
            for audience in audiences:
                try:
                    receipts.append(refresh_rankings(conn_factory, audience))
                except Exception as exc:
                    receipts.append({
                        "audience": (
                            "company" if audience == _COMPANY_AUDIENCE else "personal"
                        ),
                        "audience_key": audience,
                        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    })
            return {"skipped": False, "audiences": receipts}
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))


def _ranking_enabled() -> bool:
    return os.environ.get("WAREHOUSE_CALLIOPE_OBSERVATION_RANKING", "1").strip().lower() not in {
        "0", "false", "no", "off", "disabled",
    }


def _ranking_interval() -> int:
    try:
        value = int(os.environ.get("WAREHOUSE_CALLIOPE_OBSERVATION_RANKING_SECONDS", "3600"))
    except (TypeError, ValueError):
        value = 3600
    return max(300, min(value, 86_400))


def _ranking_worker(conn_factory: Callable[..., Any]) -> None:
    while True:
        try:
            receipt = refresh_all_rankings(conn_factory)
            failures = [
                item for item in receipt.get("audiences", []) if item.get("error")
            ]
            if failures:
                print(
                    "WARNING: Calliope observation ranking skipped "
                    f"{len(failures)} audience(s): "
                    + "; ".join(
                        f"{item.get('audience_key')}: {item.get('error')}"
                        for item in failures[:5]
                    ),
                    file=os.sys.stderr,
                )
        except Exception as exc:
            print(
                f"WARNING: Calliope observation ranking failed: {type(exc).__name__}: {str(exc)[:500]}",
                file=os.sys.stderr,
            )
        _WAKE.wait(_ranking_interval())
        _WAKE.clear()


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _THREAD
    if not _ranking_enabled():
        return False
    with _THREAD_LOCK:
        if _THREAD and _THREAD.is_alive():
            return True
        _THREAD = threading.Thread(
            target=_ranking_worker,
            args=(conn_factory,),
            name="calliope-observation-ranking",
            daemon=True,
        )
        _THREAD.start()
    return True


def wake_worker() -> None:
    _WAKE.set()


def snapshot(
    conn_factory: Callable[..., Any],
    owner_email: Any,
    *,
    scope_kind: str = "all",
    state: str = "active",
    limit: int = 40,
) -> dict[str, Any]:
    owner = _owner(owner_email)
    scope = str(scope_kind or "all").strip().lower()
    if scope not in {"all", *SCOPE_KINDS}:
        raise ValueError("observation scope must be all, company, or personal")
    clean_state = str(state or "active").strip().lower()
    if clean_state not in OBSERVATION_STATES:
        raise ValueError("unknown observation state")
    clean_limit = max(1, min(int(limit), 200))
    where = ["o.state=%s"]
    where_params: list[Any] = [clean_state]
    if scope == "company":
        where.append("o.scope_kind='company'")
    elif scope == "personal":
        where.extend(["o.scope_kind='personal'", "o.owner_email=%s"])
        where_params.append(owner)
    else:
        where.append(
            "(o.scope_kind='company' OR (o.scope_kind='personal' AND o.owner_email=%s))"
        )
        where_params.append(owner)
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT o.*,f.verdict AS viewer_feedback_verdict,f.note AS viewer_feedback_note,"
            "f.revision AS viewer_feedback_revision,f.updated_at AS viewer_feedback_updated_at,"
            "r.audience_key AS ranking_audience_key,r.relevance AS ranking_relevance,"
            "r.priority AS ranking_priority,r.surface_role AS ranking_surface_role,"
            "r.factors AS ranking_factors,r.reason_codes AS ranking_reason_codes,"
            "r.ranker AS ranking_ranker,r.model AS ranking_model,r.ranked_at AS ranking_ranked_at "
            "FROM rvbbit.calliope_observations o "
            "LEFT JOIN rvbbit.calliope_observation_feedback f ON f.observation_id=o.id "
            "AND f.owner_email=%s "
            "LEFT JOIN LATERAL (SELECT rr.* FROM rvbbit.calliope_observation_rankings rr "
            "WHERE rr.observation_id=o.id AND rr.audience_key IN (%s,'company') "
            "ORDER BY (rr.audience_key='company') ASC,rr.ranked_at DESC LIMIT 1) r ON true "
            "WHERE " + " AND ".join(where)
            + " ORDER BY (f.verdict='not_relevant') ASC,"
            "coalesce(r.priority,o.urgency*0.5+o.confidence*0.5) DESC,"
            "o.last_observed_at DESC LIMIT %s",
            (owner, owner, *where_params, clean_limit),
        ).fetchall()
    observations = [observation_public(row) for row in rows]
    counts: dict[str, int] = {}
    for item in observations:
        counts[item["scope_kind"]] = counts.get(item["scope_kind"], 0) + 1
    ranked = [item["ranking"] for item in observations if item.get("ranking")]
    return {
        "observations": observations,
        "counts": counts,
        "scope_kind": scope,
        "state": clean_state,
        "ranking": {
            "available": bool(ranked),
            "ranked_count": len(ranked),
            "latest_at": max(
                (str(item.get("ranked_at") or "") for item in ranked), default=""
            ) or None,
        },
    }
