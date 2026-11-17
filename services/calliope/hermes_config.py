"""Durable desired-state control for the pinned hosted Hermes gateway.

Calliope records secret-free YAML and credential references in Postgres.  A
background controller resolves credentials only for the private supervisor
request, applies the candidate, and lets the supervisor verify or roll back
without depending on the agent process being healthy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

import yaml


_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1] if len(_HERE.parents) > 1 else _HERE
_MIGRATION = (
    _HERE / "sql/0313_hermes_config_history.sql"
    if (_HERE / "sql/0313_hermes_config_history.sql").exists()
    else _REPO / "crates/pg_rvbbit/sql/migrations/0313_hermes_config_history.sql"
)
_SOURCE = (
    _HERE / "hermes-docs/source.json"
    if (_HERE / "hermes-docs/source.json").exists()
    else _REPO / "docker/calliope/hermes-docs/source.json"
)
_CONTROL_URL = os.environ.get(
    "WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643"
).rstrip("/")
_CONTROL_KEY = os.environ.get("HERMES_CONTROL_KEY") or os.environ.get(
    "WAREHOUSE_HERMES_API_KEY", ""
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CREDENTIAL_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{2,511}$")
_PROTECTED_RUNTIME_ENV_NAMES = {
    "API_SERVER_KEY",
    "HERMES_CONTROL_KEY",
    "HERMES_HOME",
    "HOME",
    "PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "WAREHOUSE_HERMES_API_KEY",
    "WAREHOUSE_HERMES_CONTROL_URL",
    "WAREHOUSE_HERMES_MCP_KEY",
}
_PROTECTED_RUNTIME_ENV_PREFIXES = ("LD_", "PYTHON", "S6_", "CALLIOPE_HERMES_CONTROL_")
_WORKER_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_WAKE = threading.Event()
_CONN_FACTORY: Callable[..., Any] | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _hash(config_yaml: str) -> str:
    return hashlib.sha256(config_yaml.encode("utf-8")).hexdigest()


def _source() -> dict[str, Any]:
    return json.loads(_SOURCE.read_text(encoding="utf-8"))


def _control(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    if not _CONTROL_KEY:
        raise RuntimeError("WAREHOUSE_HERMES_API_KEY is not configured")
    body = None if payload is None else _json(payload).encode("utf-8")
    request = urllib.request.Request(
        _CONTROL_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {_CONTROL_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
        except Exception:
            result = {"error": f"Hermes control returned HTTP {exc.code}"}
        result.setdefault("http_status", exc.code)
    if not isinstance(result, dict):
        raise RuntimeError("Hermes control returned a non-object response")
    return result


def _clean_bindings(value: Any, config_yaml: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("secret_bindings must be an object with at most 64 entries")
    clean: dict[str, str] = {}
    for raw_name, raw_ref in value.items():
        name = str(raw_name or "").strip()
        credential_ref = str(raw_ref or "").strip()
        if not _ENV_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid Hermes secret environment name: {name!r}")
        if not _CREDENTIAL_REF_RE.fullmatch(credential_ref):
            raise ValueError(f"invalid canonical credential reference for {name}")
        referenced_in_yaml = f"${{env:{name}}}" in config_yaml
        if not referenced_in_yaml and (
            name in _PROTECTED_RUNTIME_ENV_NAMES
            or any(name.startswith(prefix) for prefix in _PROTECTED_RUNTIME_ENV_PREFIXES)
        ):
            raise ValueError(
                f"Hermes control environment variable cannot be changed by a config revision: {name}"
            )
        # Hermes consumes messaging credentials (for example
        # TELEGRAM_BOT_TOKEN) directly from its process environment rather than
        # from config.yaml.  The separate canonical-reference ledger is the
        # intentional declaration for those bindings, so they need not be
        # duplicated as inert YAML text.
        clean[name] = credential_ref
    return clean


def _config_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        out: set[str] = set()
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            nested = _config_paths(child, path)
            out.update(nested or {path})
        return out
    if isinstance(value, list):
        return {prefix}
    return {prefix} if prefix else set()


def _redacted_diff(previous_yaml: str, candidate_yaml: str) -> list[dict[str, Any]]:
    try:
        previous = yaml.safe_load(previous_yaml) or {}
        candidate = yaml.safe_load(candidate_yaml) or {}
    except Exception:
        return []
    old_paths = _config_paths(previous)
    new_paths = _config_paths(candidate)
    return [
        {"operation": "removed", "path": path}
        for path in sorted(old_paths - new_paths)
    ] + [
        {"operation": "added", "path": path}
        for path in sorted(new_paths - old_paths)
    ] + [
        {"operation": "changed", "path": path}
        for path in sorted(old_paths & new_paths)
        if _path_value(previous, path) != _path_value(candidate, path)
    ]


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    # The diff records the path, never a value that might be secret-adjacent.
    return json.dumps(current, sort_keys=True, default=str)


def ensure(conn_factory: Callable[..., Any]) -> None:
    global _CONN_FACTORY
    with conn_factory() as conn:
        conn.execute(_MIGRATION.read_text(encoding="utf-8"))
    _CONN_FACTORY = conn_factory
    start_worker(conn_factory)


def validate(config_yaml: str, secret_bindings: Any = None) -> dict[str, Any]:
    config_yaml = str(config_yaml or "")
    bindings = _clean_bindings(secret_bindings, config_yaml)
    result = _control("POST", "/v1/validate", {"config_yaml": config_yaml}, timeout=20)
    result["secret_bindings"] = bindings
    return result


def _current_row(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT r.* FROM rvbbit.hermes_config_controller_state s "
        "LEFT JOIN rvbbit.hermes_config_revisions r ON r.id=s.active_revision_id "
        "WHERE s.singleton"
    ).fetchone()
    return dict(row) if row and row.get("id") else None


def _seed_baseline(conn_factory: Callable[..., Any], current: dict[str, Any]) -> None:
    config_yaml = str(current["config_yaml"])
    config_hash = str(current["config_hash"])
    source = _source()
    with conn_factory() as conn:
        state = conn.execute(
            "SELECT active_revision_id FROM rvbbit.hermes_config_controller_state "
            "WHERE singleton FOR UPDATE"
        ).fetchone()
        if state and state.get("active_revision_id"):
            return
        existing = conn.execute(
            "SELECT id FROM rvbbit.hermes_config_revisions "
            "WHERE config_hash=%s ORDER BY revision DESC LIMIT 1",
            (config_hash,),
        ).fetchone()
        revision_id = existing.get("id") if existing else uuid.uuid4()
        if existing:
            conn.execute(
                "UPDATE rvbbit.hermes_config_revisions SET state='active',"
                "applied_at=coalesce(applied_at,clock_timestamp()),"
                "completed_at=coalesce(completed_at,clock_timestamp()) WHERE id=%s",
                (revision_id,),
            )
        else:
            conn.execute(
                "INSERT INTO rvbbit.hermes_config_revisions "
                "(id,state,config_yaml,config_hash,reason,hermes_revision,"
                "hermes_image_digest,validation,verification,created_by,applied_at,completed_at) "
                "VALUES (%s,'active',%s,%s,'shipping baseline',%s,%s,%s::jsonb,%s::jsonb,"
                "'calliope-controller',clock_timestamp(),clock_timestamp())",
                (
                    revision_id,
                    config_yaml,
                    config_hash,
                    source["revision"],
                    source["image_digest"],
                    _json(current.get("validation") or {}),
                    _json({"source": "live_gateway_baseline"}),
                ),
            )
        conn.execute(
            "UPDATE rvbbit.hermes_config_controller_state SET "
            "active_revision_id=%s,last_good_revision_id=%s,active_config_hash=%s,"
            "controller_status='ready',gateway_status='healthy',"
            "gateway_checked_at=clock_timestamp(),updated_at=clock_timestamp() "
            "WHERE singleton",
            (revision_id, revision_id, config_hash),
        )


def queue_apply(
    conn_factory: Callable[..., Any],
    config_yaml: str,
    *,
    reason: str,
    actor: str,
    secret_bindings: Any = None,
) -> dict[str, Any]:
    config_yaml = str(config_yaml or "")
    if not config_yaml or len(config_yaml.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Hermes config is empty or too large")
    clean_bindings = _clean_bindings(secret_bindings, config_yaml)
    validation = validate(config_yaml, clean_bindings)
    if not validation.get("valid"):
        return {"queued": False, "validation": validation}
    source = _source()
    config_hash = _hash(config_yaml)
    with conn_factory() as conn:
        active = _current_row(conn)
        diff = _redacted_diff(str((active or {}).get("config_yaml") or ""), config_yaml)
        revision_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO rvbbit.hermes_config_revisions "
            "(id,parent_id,state,config_yaml,config_hash,reason,hermes_revision,"
            "hermes_image_digest,validation,secret_bindings,redacted_diff,created_by) "
            "VALUES (%s,%s,'validated',%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)",
            (
                revision_id,
                (active or {}).get("id"),
                config_yaml,
                config_hash,
                str(reason or "")[:2000],
                source["revision"],
                source["image_digest"],
                _json(validation),
                _json(clean_bindings),
                _json(diff),
                str(actor or "calliope")[:254],
            ),
        )
        conn.execute(
            "INSERT INTO rvbbit.hermes_config_events "
            "(revision_id,event_type,actor,details) VALUES "
            "(%s,'created',%s,%s::jsonb),(%s,'validation_passed',%s,%s::jsonb)",
            (
                revision_id,
                str(actor or "calliope")[:254],
                _json({"reason": reason, "config_hash": config_hash}),
                revision_id,
                str(actor or "calliope")[:254],
                _json(validation),
            ),
        )
        conn.execute(
            "UPDATE rvbbit.hermes_config_controller_state SET desired_revision_id=%s,"
            "controller_status='ready',updated_at=clock_timestamp() WHERE singleton",
            (revision_id,),
        )
    _WAKE.set()
    return {
        "queued": True,
        "revision_id": str(revision_id),
        "config_hash": config_hash,
        "state": "validated",
        "validation": validation,
        "redacted_diff": diff,
        "message": "Validated and queued. The independent controller will apply, verify, and roll back automatically if needed.",
    }


def restore(
    conn_factory: Callable[..., Any], revision_id: str, *, actor: str, reason: str = ""
) -> dict[str, Any]:
    try:
        parsed = uuid.UUID(str(revision_id))
    except ValueError as exc:
        raise ValueError("invalid Hermes revision id") from exc
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT config_yaml,secret_bindings,revision FROM rvbbit.hermes_config_revisions "
            "WHERE id=%s",
            (parsed,),
        ).fetchone()
    if not row:
        raise ValueError("Hermes revision was not found")
    return queue_apply(
        conn_factory,
        row["config_yaml"],
        reason=reason or f"restore revision {row['revision']}",
        actor=actor,
        secret_bindings=row.get("secret_bindings") or {},
    )


def history(conn_factory: Callable[..., Any], limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(int(limit or 30), 100))
    with conn_factory() as conn:
        state = conn.execute(
            "SELECT * FROM rvbbit.hermes_config_controller_state WHERE singleton"
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM rvbbit.hermes_config_history ORDER BY revision DESC LIMIT %s",
            (limit,),
        ).fetchall()
    supervisor: dict[str, Any]
    try:
        supervisor = _control("GET", "/health", timeout=5)
    except Exception as exc:
        supervisor = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "state": dict(state) if state else {},
        "supervisor": supervisor,
        "revisions": [dict(row) for row in rows],
    }


def current(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """Return the active secret-free YAML to an already-authorized admin."""
    with conn_factory() as conn:
        row = _current_row(conn)
    if not row:
        raise RuntimeError("Hermes configuration baseline has not been recorded yet")
    return {
        "revision_id": str(row["id"]),
        "revision": row["revision"],
        "config_yaml": row["config_yaml"],
        "config_hash": row["config_hash"],
        "secret_bindings": row.get("secret_bindings") or {},
        "validation": row.get("validation") or {},
    }


def restart(conn_factory: Callable[..., Any], *, actor: str, reason: str = "") -> dict[str, Any]:
    active = current(conn_factory)
    return queue_apply(
        conn_factory,
        active["config_yaml"],
        reason=reason or "restart Hermes gateway with the active configuration",
        actor=actor,
        secret_bindings=active["secret_bindings"],
    )


def docs_search(
    conn_factory: Callable[..., Any], query: str, limit: int = 8
) -> list[dict[str, Any]]:
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT source_kind,path,title,heading,score,lexical_score,semantic_score,"
            "chunk_text,metadata FROM rvbbit.hermes_docs_search(%s,%s)",
            (str(query or ""), max(1, min(int(limit or 8), 20))),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_bindings(conn_factory: Callable[..., Any], bindings: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    with conn_factory() as conn:
        for name, credential_ref in bindings.items():
            row = conn.execute(
                "SELECT rvbbit.resolve_credential(%s,'calliope-hermes-controller',"
                "'gateway_config_apply') AS value",
                (credential_ref,),
            ).fetchone()
            if not row or not row.get("value"):
                raise RuntimeError(f"canonical credential is unavailable: {credential_ref}")
            resolved[name] = row["value"]
    return resolved


def _current_row_factory(conn_factory: Callable[..., Any]) -> dict[str, Any] | None:
    with conn_factory() as conn:
        return _current_row(conn)


def _adopt_live_config(
    conn_factory: Callable[..., Any],
    live: dict[str, Any],
    active: dict[str, Any] | None,
) -> str | None:
    """Record the gateway's live config as the active revision (no restart).

    Only called when no revision is pending and the live hash differs from the
    active one — i.e. the config changed underneath the controller (the boot
    reconciler). Secrets never travel: /v1/config is the secret-free view and
    the previous revision's credential bindings are carried forward."""
    config_yaml = str(live.get("config_yaml") or "")
    config_hash = str(live.get("config_hash") or "")
    if not config_yaml or not config_hash:
        return None
    source = _source()
    with conn_factory() as conn:
        existing = conn.execute(
            "SELECT id FROM rvbbit.hermes_config_revisions WHERE config_hash=%s "
            "ORDER BY revision DESC LIMIT 1",
            (config_hash,),
        ).fetchone()
        revision_id = existing.get("id") if existing else uuid.uuid4()
        # One active row at a time (hermes_config_revisions_one_active_idx):
        # retire the stale revision before the adopted one becomes active.
        if active and active.get("id") and str(active["id"]) != str(revision_id):
            conn.execute(
                "UPDATE rvbbit.hermes_config_revisions SET state='superseded' "
                "WHERE id=%s AND state='active'",
                (active["id"],),
            )
            conn.execute(
                "INSERT INTO rvbbit.hermes_config_events (revision_id,event_type,actor,details) "
                "VALUES (%s,'superseded','calliope-controller',%s::jsonb)",
                (active["id"], _json({"by": str(revision_id), "reason": "live config adopted"})),
            )
        if existing:
            conn.execute(
                "UPDATE rvbbit.hermes_config_revisions SET state='active',"
                "applied_at=coalesce(applied_at,clock_timestamp()),"
                "completed_at=coalesce(completed_at,clock_timestamp()) WHERE id=%s",
                (revision_id,),
            )
        else:
            conn.execute(
                "INSERT INTO rvbbit.hermes_config_revisions "
                "(id,state,config_yaml,config_hash,reason,hermes_revision,hermes_image_digest,"
                "secret_bindings,validation,verification,created_by,applied_at,completed_at) "
                "VALUES (%s,'active',%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,"
                "'calliope-controller',clock_timestamp(),clock_timestamp())",
                (
                    revision_id,
                    config_yaml,
                    config_hash,
                    "adopted live gateway config (changed outside the controller, e.g. the boot reconciler)",
                    source["revision"],
                    source["image_digest"],
                    _json(dict((active or {}).get("secret_bindings") or {})),
                    _json(live.get("validation") or {}),
                    _json({"source": "live_gateway_adopted", "previous_revision_id": str((active or {}).get("id") or "")}),
                ),
            )
        conn.execute(
            "UPDATE rvbbit.hermes_config_controller_state SET "
            "active_revision_id=%s,last_good_revision_id=%s,active_config_hash=%s,"
            "gateway_status='healthy',gateway_checked_at=clock_timestamp(),updated_at=clock_timestamp() "
            "WHERE singleton",
            (revision_id, revision_id, config_hash),
        )
        conn.execute(
            "INSERT INTO rvbbit.hermes_config_events (revision_id,event_type,actor,details) "
            "VALUES (%s,'activated','calliope-controller',%s::jsonb)",
            (revision_id, _json({"reason": "adopted live gateway config", "config_hash": config_hash})),
        )
    return str(revision_id)


def reconcile_once(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    current = _control("GET", "/v1/config", timeout=10)
    _seed_baseline(conn_factory, current)
    with conn_factory() as conn:
        desired = conn.execute(
            "SELECT r.*,s.active_config_hash FROM rvbbit.hermes_config_controller_state s "
            "JOIN rvbbit.hermes_config_revisions r ON r.id=s.desired_revision_id "
            "WHERE s.singleton AND r.state IN ('validated','applying')"
        ).fetchone()
        active = _current_row(conn)
    if not desired:
        # Rehydrate runtime-only secrets after a supervisor/container restart.
        bindings = dict((active or {}).get("secret_bindings") or {})
        status = _control("GET", "/health", timeout=5)
        if bindings and not set(bindings).issubset(set(status.get("runtime_env_names") or [])):
            desired = active
        else:
            # The boot hook's reconciler rewrites /opt/data/config.yaml when
            # appliance invariants move (context budget, MCP lanes, curator).
            # Until the store learns about that, every candidate the UI builds
            # from the stale active revision fails validation on invariants the
            # operator never touched ("Hermes rejected the model route
            # configuration", 2026-08-17). Adopt the live, secret-free config
            # as the active revision so edits start from reality.
            if (
                status.get("gateway_running")
                and active
                and current.get("config_yaml")
                and current.get("config_hash")
                and current.get("config_hash") != active.get("config_hash")
            ):
                _adopt_live_config(conn_factory, current, active)
                active = _current_row_factory(conn_factory)
            matching_gateway = bool(
                status.get("gateway_running")
                and active
                and current.get("config_hash") == active.get("config_hash")
            )
            with conn_factory() as conn:
                conn.execute(
                    "UPDATE rvbbit.hermes_config_controller_state SET "
                    "controller_status='ready',gateway_status=%s,gateway_checked_at=clock_timestamp(),"
                    "active_config_hash=%s,controller_heartbeat_at=clock_timestamp(),"
                    "updated_at=clock_timestamp() WHERE singleton",
                    (
                        "healthy" if matching_gateway else "degraded",
                        current.get("config_hash"),
                    ),
                )
            return {"changed": False, "active_config_hash": current.get("config_hash")}

    revision_id = desired["id"]
    with conn_factory() as conn:
        conn.execute(
            "UPDATE rvbbit.hermes_config_revisions SET state='applying',applied_at=clock_timestamp() "
            "WHERE id=%s AND state IN ('validated','active','applying')",
            (revision_id,),
        )
        conn.execute(
            "UPDATE rvbbit.hermes_config_controller_state SET controller_status='applying',"
            "gateway_status='starting',controller_heartbeat_at=clock_timestamp(),"
            "updated_at=clock_timestamp() WHERE singleton"
        )
        conn.execute(
            "INSERT INTO rvbbit.hermes_config_events (revision_id,event_type,actor,details) "
            "VALUES (%s,'apply_started','calliope-controller',%s::jsonb)",
            (revision_id, _json({"rehydrate": desired is active})),
        )
    try:
        resolved = _resolve_bindings(
            conn_factory, dict(desired.get("secret_bindings") or {})
        )
        result = _control(
            "POST",
            "/v1/apply",
            {
                "config_yaml": desired["config_yaml"],
                "expected_hash": current.get("config_hash"),
                "revision_id": str(revision_id),
                "resolved_env": resolved,
            },
            # The supervisor may spend up to 360 seconds verifying a candidate
            # (0.21.4 boots stay busy for minutes while multiplexed profiles start)
            # and another 360 seconds verifying its automatic rollback, plus drains.
            timeout=900,
        )
    except Exception as exc:
        result = {"ok": False, "phase": "controller", "error": f"{type(exc).__name__}: {exc}"}

    with conn_factory() as conn:
        if result.get("ok"):
            prior = conn.execute(
                "SELECT active_revision_id FROM rvbbit.hermes_config_controller_state "
                "WHERE singleton FOR UPDATE"
            ).fetchone()
            prior_id = prior.get("active_revision_id") if prior else None
            if prior_id and prior_id != revision_id:
                conn.execute(
                    "UPDATE rvbbit.hermes_config_revisions SET state='superseded',"
                    "completed_at=coalesce(completed_at,clock_timestamp()) WHERE id=%s",
                    (prior_id,),
                )
                conn.execute(
                    "INSERT INTO rvbbit.hermes_config_events "
                    "(revision_id,event_type,actor,details) VALUES "
                    "(%s,'superseded','calliope-controller','{}'::jsonb)",
                    (prior_id,),
                )
            conn.execute(
                "UPDATE rvbbit.hermes_config_revisions SET state='active',verification=%s::jsonb,"
                "completed_at=clock_timestamp() WHERE id=%s",
                (_json(result.get("verification") or result), revision_id),
            )
            conn.execute(
                "UPDATE rvbbit.hermes_config_controller_state SET active_revision_id=%s,"
                "desired_revision_id=NULL,last_good_revision_id=%s,controller_status='ready',"
                "gateway_status='healthy',active_config_hash=%s,gateway_checked_at=clock_timestamp(),"
                "details=%s::jsonb,updated_at=clock_timestamp() WHERE singleton",
                (
                    revision_id,
                    revision_id,
                    result.get("active_config_hash"),
                    _json({"last_apply": result}),
                ),
            )
            conn.execute(
                "INSERT INTO rvbbit.hermes_config_events "
                "(revision_id,event_type,actor,details) VALUES "
                "(%s,'verification_passed','calliope-controller',%s::jsonb),"
                "(%s,'activated','calliope-controller',%s::jsonb)",
                (
                    revision_id,
                    _json(result.get("verification") or {}),
                    revision_id,
                    _json({"config_hash": result.get("active_config_hash")}),
                ),
            )
        else:
            rolled_back = bool(result.get("rolled_back"))
            conn.execute(
                "UPDATE rvbbit.hermes_config_revisions SET state=%s,error=%s,verification=%s::jsonb,"
                "completed_at=clock_timestamp() WHERE id=%s",
                (
                    "rolled_back" if rolled_back else "failed",
                    str(result.get("error") or "Hermes config apply failed")[:8000],
                    _json(result),
                    revision_id,
                ),
            )
            conn.execute(
                "UPDATE rvbbit.hermes_config_controller_state SET desired_revision_id=NULL,"
                "controller_status=%s,gateway_status=%s,details=%s::jsonb,"
                "updated_at=clock_timestamp() WHERE singleton",
                (
                    "ready" if rolled_back else "failed",
                    "healthy" if result.get("rollback", {}).get("healthy") else "failed",
                    _json({"last_apply": result}),
                ),
            )
            conn.execute(
                "INSERT INTO rvbbit.hermes_config_events "
                "(revision_id,event_type,actor,details) VALUES "
                "(%s,'apply_failed','calliope-controller',%s::jsonb)",
                (revision_id, _json(result)),
            )
            if rolled_back:
                conn.execute(
                    "INSERT INTO rvbbit.hermes_config_events "
                    "(revision_id,event_type,actor,details) VALUES "
                    "(%s,'rollback_completed','calliope-controller',%s::jsonb)",
                    (revision_id, _json(result.get("rollback") or {})),
                )
    return {"changed": True, "revision_id": str(revision_id), "result": result}


def _worker(conn_factory: Callable[..., Any]) -> None:
    while True:
        try:
            reconcile_once(conn_factory)
        except Exception as exc:
            # Keep the web/MCP server available while Hermes is starting or a
            # provider is degraded. The durable desired revision is retried.
            print(
                f"WARNING: Hermes config controller retrying: {type(exc).__name__}: {exc}",
                flush=True,
            )
        _WAKE.wait(10)
        _WAKE.clear()


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return False
        _WORKER = threading.Thread(
            target=_worker,
            args=(conn_factory,),
            name="calliope-hermes-config-controller",
            daemon=True,
        )
        _WORKER.start()
        return True
