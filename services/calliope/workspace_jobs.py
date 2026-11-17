"""Saved workspace SQL, executed by a database-coordinated worker, without LLMs."""
from __future__ import annotations

import logging
from pathlib import Path
import re
import threading
import time
import uuid

import calliope
import workspace_sql

LOG = logging.getLogger(__name__)
# A session lock covers claim, execution and finalization, across all replicas.
# If that connection is lost, recovery never blindly replays the active batch.
_LOCK = 736193208432
_start_lock = threading.Lock()
_thread = None


def ensure(factory):
    with factory() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK + 1,))
        conn.execute((Path(__file__).parent / "sql/0432_workspace_jobs.sql").read_text())


def _owner(factory, session_id, authorized_owner=None, *, require_admin=True):
    owner = calliope._owner_for_calliope_session(factory, session_id).strip().lower()
    if authorized_owner and owner != authorized_owner.strip().lower():
        raise PermissionError("The workspace session belongs to another user")
    if require_admin and not calliope.calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError("Workspace jobs require an Admins Team session")
    return owner


def _name(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", value or ""):
        raise ValueError("job_name must be 1–120 letters, digits, underscores, dots or hyphens")
    return value


def _json(row):
    return {k: str(v) if isinstance(v, uuid.UUID) else v.isoformat()
            if hasattr(v, "isoformat") else v for k, v in dict(row).items()}


def _running(conn, job_id):
    return conn.execute("SELECT id FROM rvbbit.calliope_workspace_job_runs "
                        "WHERE job_id=%s AND status='running'", (job_id,)).fetchone()


def save(factory, session_id, job_name, sql, interval_seconds=0, *, authorized_owner=None, description=None):
    owner = _owner(factory, session_id, authorized_owner)
    if description is not None and (not isinstance(description, str) or len(description)>1000):
        raise ValueError("description must be at most 1000 characters")
    job_name = _name(job_name)
    if type(interval_seconds) is not int or not (interval_seconds == 0 or 60 <= interval_seconds <= 31536000):
        raise ValueError("interval_seconds must be 0 (manual) or 60–31536000; 12 hours is 43200")
    if calliope._LOCAL_ADMIN_SQL_SECRET_RE.search(sql or ""):
        raise ValueError("Credentials cannot be placed in workspace job SQL")
    batch = workspace_sql.plan(sql)
    ensure(factory)
    with factory() as conn, conn.transaction():
        # Serialize same-name upserts without a race on the unique constraint.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (owner + ":" + job_name,))
        old = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs "
                           "WHERE owner_email=%s AND name=%s FOR UPDATE", (owner, job_name)).fetchone()
        if old and _running(conn, old["id"]):
            raise ValueError("The job is running; pause future runs and wait for this bounded run before editing")
        # A repeated save neither restarts the clock nor resumes a paused job.
        if old and old["sql_sha256"] == batch["sql_sha256"] and old["interval_seconds"] == interval_seconds:
            import gallery_jobs
            gallery_jobs.describe(conn, "sql", old["id"], description)
            return {"job": _json(old), "changed": False, "approval_required": False}
        if old:
            row = conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET sql=%s,sql_sha256=%s,"
                               "revision=revision+1,session_id=%s,interval_seconds=%s,enabled=%s,"
                               "run_requested=false,next_run_at=CASE WHEN %s>0 THEN now()+make_interval(secs=>%s) END,"
                               "updated_at=now() WHERE id=%s RETURNING *",
                               (sql, batch["sql_sha256"], session_id, interval_seconds, interval_seconds > 0,
                                interval_seconds, interval_seconds, old["id"])).fetchone()
        else:
            row = conn.execute("INSERT INTO rvbbit.calliope_workspace_jobs "
                               "(id,owner_email,session_id,name,sql,sql_sha256,interval_seconds,enabled,next_run_at) "
                               "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s>0 THEN now()+make_interval(secs=>%s) END) RETURNING *",
                               (uuid.uuid4(), owner, session_id, job_name, sql, batch["sql_sha256"],
                                interval_seconds, interval_seconds > 0, interval_seconds, interval_seconds)).fetchone()
        import gallery_jobs
        gallery_jobs.describe(conn, "sql", row["id"], description)
    return {"job": _json(row), "changed": True, "approval_required": False,
            "execution": "direct_workspace_sql", "note": "Saved. First scheduled run is one interval from now; use run_now for an earlier test."}


def manage(factory, session_id, job_name, operation, *, authorized_owner=None):
    # Losing execution authority must not take away the owner's off switch.
    owner = _owner(factory, session_id, authorized_owner, require_admin=operation != "pause")
    return _manage_owned(factory, owner, operation, job_name=_name(job_name))


def manage_by_id(factory, owner, job_id, operation):
    """Native UI controls use authenticated ownership, not a chat's lifetime."""
    job_id = str(uuid.UUID(str(job_id)))
    owner = str(owner or "").strip().lower()
    if not owner:
        raise PermissionError("Sign in to manage SQL jobs")
    if operation != "pause" and not calliope.calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError("Workspace job execution requires an Admins Team member")
    return _manage_owned(factory, owner, operation, job_id=job_id)


def _manage_owned(factory, owner, operation, *, job_name=None, job_id=None):
    if operation not in {"pause", "resume", "run_now"}:
        raise ValueError("operation must be pause, resume or run_now")
    ensure(factory)
    with factory() as conn, conn.transaction():
        job = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs "
                           "WHERE owner_email=%s AND (name=%s OR id=%s::uuid) FOR UPDATE",
                           (owner, job_name, job_id)).fetchone()
        if not job:
            raise LookupError("Workspace job not found")
        active = bool(_running(conn, job["id"]))
        if operation == "pause":
            conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET enabled=false,run_requested=false,"
                         "next_run_at=NULL,updated_at=now() WHERE id=%s", (job["id"],))
        elif operation == "resume":
            if not job["interval_seconds"]:
                raise ValueError("This is a manual job; save an interval before resuming a schedule")
            if not job["enabled"]:
                conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET enabled=true,"
                             "next_run_at=now()+make_interval(secs=>interval_seconds),updated_at=now() WHERE id=%s", (job["id"],))
        elif not active:
            conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET run_requested=true,updated_at=now() "
                         "WHERE id=%s", (job["id"],))
        row = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs WHERE id=%s", (job["id"],)).fetchone()
    return {"job": _json(row), "running": active, "approval_required": False,
            "note": "A running batch finishes within its execution budget; pause removes future/queued runs."}


def status(factory, session_id, job_name="", limit=10, *, authorized_owner=None):
    owner = _owner(factory, session_id, authorized_owner, require_admin=False)
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    ensure(factory)
    with factory() as conn:
        jobs = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs WHERE owner_email=%s "
                            "AND (%s='' OR name=%s) ORDER BY updated_at DESC,id LIMIT %s",
                            (owner, job_name, job_name, limit + 1)).fetchall()
        runs = conn.execute("SELECT r.*,j.name AS job_name FROM rvbbit.calliope_workspace_job_runs r "
                            "JOIN rvbbit.calliope_workspace_jobs j ON j.id=r.job_id "
                            "WHERE j.owner_email=%s AND (%s='' OR j.name=%s) "
                            "ORDER BY r.started_at DESC,r.id LIMIT %s", (owner, job_name, job_name, limit + 1)).fetchall()
        # Include the existing SQL receipt/results when inspecting one job.
        for run in runs[:limit]:
            if job_name and run["action_run_id"]:
                run["receipt"] = calliope._action_run_for_owner(factory, owner, run["action_run_id"])
    return {"jobs": [_json(j) for j in jobs[:limit]], "runs": [_json(r) for r in runs[:limit]],
            "jobs_truncated": len(jobs) > limit, "runs_truncated": len(runs) > limit,
            "execution": "direct_workspace_sql", "automatic_agent_invocation": False}


def _finish(conn, run, status, error=None):
    with conn.transaction():
        conn.execute("UPDATE rvbbit.calliope_workspace_job_runs SET status=%s,error=%s,finished_at=now() WHERE id=%s",
                     (status, error, run["id"]))
        conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET last_status=%s,last_error=%s,"
                     "enabled=CASE WHEN %s THEN enabled ELSE false END,"
                     "run_requested=CASE WHEN %s THEN run_requested ELSE false END,"
                     "next_run_at=CASE WHEN %s THEN CASE WHEN next_run_at<=now() "
                     "THEN now()+make_interval(secs=>interval_seconds) ELSE next_run_at END ELSE NULL END,"
                     "updated_at=now() WHERE id=%s",
                     (status, error, status == "complete", status == "complete", status == "complete", run["job_id"]))


def tick(factory):
    """Run at most one due batch. The caller has applied ensure()."""
    with factory() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (_LOCK,)).fetchone()["locked"]:
            return False
        try:
            # With the global execution lock held, no other worker is live.
            # A prior process may have died after COMMIT but before finalization.
            abandoned = conn.execute("SELECT r.*,j.owner_email FROM rvbbit.calliope_workspace_job_runs r "
                                     "JOIN rvbbit.calliope_workspace_jobs j ON j.id=r.job_id WHERE r.status='running'").fetchall()
            for run in abandoned:
                receipt = calliope._action_run_for_owner(factory, run["owner_email"], run["action_run_id"]) if run["action_run_id"] else None
                state = (receipt or {}).get("status")
                if state in {"complete", "failed"}:
                    _finish(conn, run, state, (receipt or {}).get("error"))
                else:
                    _finish(conn, run, "interrupted", "Worker interrupted; outcome may be unknown. Inspect the SQL receipt and external effects before run_now/resume.")
            with conn.transaction():
                job = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs "
                                   "WHERE run_requested OR (enabled AND next_run_at<=now()) "
                                   "ORDER BY COALESCE(next_run_at,updated_at),id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
                if not job:
                    return False
                run = conn.execute("INSERT INTO rvbbit.calliope_workspace_job_runs "
                                   "(id,job_id,revision,session_id,sql,sql_sha256,trigger,status) "
                                   "VALUES(%s,%s,%s,%s,%s,%s,%s,'running') RETURNING *",
                                   (uuid.uuid4(), job["id"], job["revision"], job["session_id"], job["sql"], job["sql_sha256"],
                                    "manual" if job["run_requested"] else "schedule")).fetchone()
                conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET run_requested=false,last_status='running',last_error=NULL,"
                             "next_run_at=CASE WHEN enabled THEN now()+make_interval(secs=>interval_seconds) END WHERE id=%s", (job["id"],))

            def link_receipt(receipt_id):
                # This also verifies the lock-owning connection before execution.
                conn.execute("UPDATE rvbbit.calliope_workspace_job_runs SET action_run_id=%s WHERE id=%s", (receipt_id, run["id"]))

            try:
                result = calliope.run_workspace_sql_for_session(
                    factory, str(job["session_id"]), job["sql"], authorized_owner=job["owner_email"],
                    on_run_created=link_receipt,
                )
                receipt = result["run"]
                state = "complete" if receipt["status"] == "complete" else "failed"
                error = receipt.get("error")
            except Exception as exc:
                state, error = "failed", calliope._action_error_text(f"{type(exc).__name__}: {exc}")
            _finish(conn, run, state, error)
            return True
        finally:
            # A closed connection already released its session locks.
            if not conn.closed:
                conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK,))


def start_worker(factory):
    global _thread
    with _start_lock:
        if _thread and _thread.is_alive():
            return False

        def loop():
            ready = False
            while True:
                try:
                    if not ready:
                        ensure(factory)
                        ready = True
                    worked = tick(factory)
                    time.sleep(1 if worked else 5)
                except Exception:
                    LOG.exception("Workspace SQL job worker failed; retrying coordination in 30 seconds")
                    time.sleep(30)

        _thread = threading.Thread(target=loop, name="workspace-sql-jobs", daemon=True)
        _thread.start()
        return True
