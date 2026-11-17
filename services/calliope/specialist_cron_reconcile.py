"""Project Hermes profile-local cron executions into Calliope's SQL ledger.

Hermes owns execution and raw-output durability.  RVBBIT owns the searchable
control-plane view used by Calliope, DataRabbit, and operators.  Reconciliation
is therefore read-only on the Hermes volume and idempotent in Postgres.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


_RUN_NAMESPACE = uuid.UUID("f2d8d707-f7fc-5f35-93d4-2ff35563f6f9")
_TERMINAL = {"completed", "failed", "unknown"}
_MAX_RESULT_CHARS = 48_000
_MAX_USAGE_BYTES = 2_000_000
_MAX_USAGE_RECORDS = 2_000


def _timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jobs(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def _executions(path):
    if not path.is_file():
        return []
    try:
        # The Hermes volume is deliberately mounted read-only into Calliope.
        # SQLite otherwise tries to establish WAL/SHM write access even for a
        # mode=ro reader and reports the existing database as unopenable.
        # Connections are short-lived snapshots, so immutable is appropriate;
        # the next reconciliation pass opens a fresh snapshot.
        connection = sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1", uri=True, timeout=1
        )
        connection.row_factory = sqlite3.Row
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(executions)")
            }
            if not {"id", "job_id", "status", "claimed_at"}.issubset(columns):
                return []
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM executions ORDER BY claimed_at,id"
                ).fetchall()
            ]
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return []


def _usage_records(path):
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > _MAX_USAGE_BYTES:
                handle.seek(size - _MAX_USAGE_BYTES)
                handle.readline()
            lines = handle.readlines()
    except OSError:
        return []
    records = []
    for raw in lines[-_MAX_USAGE_RECORDS:]:
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            continue
        if isinstance(row, dict) and row.get("job_id"):
            row["_timestamp"] = _timestamp(row.get("ts"))
            records.append(row)
    return records


def _matching_usage(records, execution):
    if str(execution.get("status") or "") not in _TERMINAL:
        return {}
    job_id = str(execution.get("job_id") or "")
    claimed = _timestamp(execution.get("claimed_at"))
    finished = _timestamp(execution.get("finished_at"))
    matches = []
    for row in records:
        if str(row.get("job_id") or "") != job_id or row.get("_timestamp") is None:
            continue
        stamp = row["_timestamp"]
        if claimed and stamp < claimed:
            continue
        if finished and stamp > finished.replace(microsecond=999999):
            continue
        matches.append(row)
    if not matches:
        return {}
    target = finished or claimed or datetime.now(timezone.utc)
    row = min(matches, key=lambda item: abs((item["_timestamp"] - target).total_seconds()))
    return {
        key: row[key]
        for key in (
            "fire_id", "model", "prompt_tokens", "completion_tokens",
            "total_tokens", "duration_ms", "response_silent", "deliver_target",
        )
        if row.get(key) is not None
    }


def _safe_output_path(root, profile, job_id, execution):
    output_root = (root / "profiles" / profile / "cron" / "output" / job_id).resolve()
    requested = str(execution.get("output_path") or "").strip()
    if requested:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to(output_root)
            if candidate.is_file():
                return candidate
        except (OSError, RuntimeError, ValueError):
            pass
    if not output_root.is_dir() or str(execution.get("status") or "") != "completed":
        return None
    claimed = _timestamp(execution.get("claimed_at"))
    finished = _timestamp(execution.get("finished_at"))
    candidates = []
    try:
        paths = list(output_root.glob("*.md"))
    except OSError:
        return None
    for path in paths:
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if claimed and stamp < claimed:
            continue
        if finished and stamp.timestamp() > finished.timestamp() + 5:
            continue
        candidates.append((abs((stamp - (finished or claimed or stamp)).total_seconds()), path))
    return min(candidates, default=(None, None), key=lambda item: item[0])[1]


def _response(path):
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marker = "\n## Response\n"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip()[:_MAX_RESULT_CHARS]


def _session_id(execution):
    exact = str(execution.get("hermes_session_id") or "").strip()
    if exact:
        return exact
    claimed = _timestamp(execution.get("claimed_at"))
    suffix = claimed.strftime("%Y%m%d_%H%M%S") if claimed else str(execution["id"])[:16]
    return f"cron_{execution['job_id']}_{suffix}"


def _projection(root, specialist, job, execution, usage_records, *, legacy=False):
    profile = str(specialist["profile_name"])
    job_id = str(execution["job_id"])
    output_path = _safe_output_path(root, profile, job_id, execution)
    state = str(execution.get("status") or "unknown")
    status = "complete" if state == "completed" else "running" if state in {"claimed", "running"} else "failed"
    result = _response(output_path) if status == "complete" else ""
    error = str(execution.get("error") or "").strip()[:4_000] or None
    if status == "failed" and not error:
        error = "Hermes cron execution ended without a confirmed successful terminal state."
    schedule = job.get("schedule")
    context = {
        "origin": "hermes_cron",
        "profile": profile,
        "cron_job_id": job_id,
        "cron_execution_id": str(execution["id"]),
        "cron_job_name": str(job.get("name") or job_id),
        "schedule": schedule,
    }
    runtime = {
        "source": str(execution.get("source") or "cron"),
        "process_id": str(execution.get("process_id") or ""),
        "pid": execution.get("pid"),
        "process_started_at": execution.get("process_started_at"),
        "claimed_at": execution.get("claimed_at"),
        "started_at": execution.get("started_at"),
        "finished_at": execution.get("finished_at"),
        "delivery_outcome": execution.get("delivery_outcome"),
        "output_path": str(output_path) if output_path else None,
        "ledger": "legacy_shared" if legacy else "profile_local",
        "reconciler": "calliope_specialist_cron_v1",
    }
    return {
        "id": str(uuid.uuid5(_RUN_NAMESPACE, f"hermes-cron:{profile}:{execution['id']}")),
        "specialist_id": str(specialist["id"]),
        "specialist_key": str(specialist["specialist_key"]),
        "profile": profile,
        "hermes_session_id": _session_id(execution),
        "status": status,
        "task": str(job.get("prompt") or job.get("name") or job_id)[:24_000],
        "context": context,
        "result": result,
        "error": error,
        "usage": _matching_usage(usage_records, execution),
        "runtime": runtime,
        "started_at": execution.get("started_at") or execution.get("claimed_at"),
        "completed_at": execution.get("finished_at") if status in {"complete", "failed"} else None,
        "created_at": execution.get("claimed_at"),
    }


def scan(root, specialists):
    """Return deterministic projections from profile-local and legacy ledgers."""
    root = Path(root)
    index = {}
    usage_by_profile = {}
    projections = {}
    for specialist in specialists:
        profile = str(specialist["profile_name"])
        cron_root = root / "profiles" / profile / "cron"
        jobs = _jobs(cron_root / "jobs.json")
        usage = _usage_records(cron_root / "usage_audit.jsonl")
        usage_by_profile[profile] = usage
        for job in jobs:
            index[str(job["id"])] = (specialist, job)
        for execution in _executions(cron_root / "executions.db"):
            match = index.get(str(execution.get("job_id") or ""))
            if match and str(match[0]["profile_name"]) == profile:
                item = _projection(root, match[0], match[1], execution, usage)
                projections[item["id"]] = item
    for execution in _executions(root / "cron" / "executions.db"):
        match = index.get(str(execution.get("job_id") or ""))
        if not match:
            continue
        profile = str(match[0]["profile_name"])
        item = _projection(
            root, match[0], match[1], execution,
            usage_by_profile.get(profile, []), legacy=True,
        )
        projections.setdefault(item["id"], item)
    return sorted(projections.values(), key=lambda item: (str(item["created_at"]), item["id"]))


def _upsert(conn, item, owner_email):
    conn.execute(
        "INSERT INTO rvbbit.calliope_specialist_runs ("
        "id,specialist_id,owner_email,trigger_kind,hermes_session_id,status,task,"
        "context,result,error,usage,runtime,started_at,completed_at,created_at) "
        "VALUES (%s::uuid,%s::uuid,%s,'scheduled',%s,%s,%s,%s::jsonb,%s,%s,"
        "%s::jsonb,%s::jsonb,%s::timestamptz,%s::timestamptz,%s::timestamptz) "
        "ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,task=EXCLUDED.task,"
        "context=EXCLUDED.context,result=EXCLUDED.result,error=EXCLUDED.error,"
        "usage=EXCLUDED.usage,runtime=EXCLUDED.runtime,started_at=EXCLUDED.started_at,"
        "completed_at=EXCLUDED.completed_at,hermes_session_id=EXCLUDED.hermes_session_id",
        (
            item["id"], item["specialist_id"], owner_email,
            item["hermes_session_id"], item["status"], item["task"],
            json.dumps(item["context"], default=str), item["result"], item["error"],
            json.dumps(item["usage"], default=str), json.dumps(item["runtime"], default=str),
            item["started_at"], item["completed_at"], item["created_at"],
        ),
    )
    handoff = {
        "profile": item["profile"],
        "specialist_key": item["specialist_key"],
        "trigger_kind": "scheduled",
        "cron_job_id": item["context"]["cron_job_id"],
        "cron_execution_id": item["context"]["cron_execution_id"],
        "schedule": item["context"]["schedule"],
    }
    conn.execute(
        "INSERT INTO rvbbit.calliope_specialist_messages "
        "(run_id,ordinal,role,kind,body,payload,created_at) "
        "VALUES (%s::uuid,1,'system','handoff',%s,%s::jsonb,%s::timestamptz) "
        "ON CONFLICT (run_id,ordinal) DO UPDATE SET body=EXCLUDED.body,payload=EXCLUDED.payload",
        (item["id"], item["task"], json.dumps(handoff, default=str), item["created_at"]),
    )
    if item["status"] in {"complete", "failed"}:
        body = item["result"] if item["status"] == "complete" else item["error"]
        role = "specialist" if item["status"] == "complete" else "system"
        kind = "assistant" if item["status"] == "complete" else "system"
        payload = {
            "profile": item["profile"],
            "specialist_key": item["specialist_key"],
            "trigger_kind": "scheduled",
            "status": item["status"],
            "usage": item["usage"],
            "runtime": item["runtime"],
        }
        conn.execute(
            "INSERT INTO rvbbit.calliope_specialist_messages "
            "(run_id,ordinal,role,kind,body,payload,created_at) "
            "VALUES (%s::uuid,2,%s,%s,%s,%s::jsonb,%s::timestamptz) "
            "ON CONFLICT (run_id,ordinal) DO UPDATE SET role=EXCLUDED.role,"
            "kind=EXCLUDED.kind,body=EXCLUDED.body,payload=EXCLUDED.payload",
            (
                item["id"], role, kind, body or "", json.dumps(payload, default=str),
                item["completed_at"] or item["created_at"],
            ),
        )


def reconcile_once(conn_factory, root, owner_email):
    """Reconcile one bounded filesystem snapshot; return observability counts."""
    with conn_factory() as conn:
        specialists = [
            dict(row) for row in conn.execute(
                "SELECT id,specialist_key,profile_name FROM rvbbit.calliope_specialists "
                "WHERE status='active' AND managed ORDER BY profile_name"
            ).fetchall()
        ]
    items = scan(root, specialists)
    for item in items:
        with conn_factory() as conn:
            with conn.transaction():
                _upsert(conn, item, owner_email)
    return {
        "profiles": len(specialists),
        "executions": len(items),
        "running": sum(item["status"] == "running" for item in items),
        "terminal": sum(item["status"] in {"complete", "failed"} for item in items),
    }
