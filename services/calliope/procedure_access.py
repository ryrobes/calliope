"""Read historical Procedure run receipts; running Procedures is retired."""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable



class ProcedureError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ProcedureError(
            "APPLICATION_SUBJECT_REQUIRED",
            "Procedures require a verified signed-in user.",
            403,
        )
    return email


def _uuid(value: Any, code: str = "PROCEDURE_NOT_FOUND") -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (TypeError, ValueError, AttributeError):
        raise ProcedureError(code, "That Procedure reference is not available.", 404) from None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _row(value: Any) -> dict[str, Any]:
    result = dict(value or {})
    for key in ("action_snapshot", "input_values", "input_redacted", "plan", "result", "verification"):
        if key in result:
            result[key] = _json_object(result.get(key))
    if "steps" in result:
        result["steps"] = _json_array(result.get("steps"))
    return result


def plan_run(*args, **kwargs):
    raise ProcedureError('LEGACY_COMPUTER_RETIRED',
        'Desktop Lab execution is retired. Read the saved method with read_calliope_playbook '
        'and carry it out with the Computer directly.', 410)


def get_run(
    conn_factory: Callable[..., Any], subject: Any, run_id: Any, *, lock: bool = False
) -> dict[str, Any]:
    owner = _email(subject)
    rid = _uuid(run_id, "PROCEDURE_RUN_NOT_FOUND")
    suffix = " FOR UPDATE" if lock else ""
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT ar.*,pr.playbook_id::text,pr.playbook_version,pr.strategy_id::text,"
            "pr.strategy_revision,pr.trigger_kind,pr.trust_level,pr.state AS procedure_state,"
            "pr.workbench_id::text,pr.guided_run_id::text,pr.current_step "
            "FROM rvbbit.calliope_action_runs ar JOIN rvbbit.calliope_procedure_runs pr ON pr.run_id=ar.id "
            "WHERE ar.id=%s::uuid AND lower(ar.owner_email)=lower(%s)" + suffix,
            (rid, owner),
        ).fetchone()
    if not row:
        raise ProcedureError("PROCEDURE_RUN_NOT_FOUND", "Procedure run not found.", 404)
    return _row(row)
