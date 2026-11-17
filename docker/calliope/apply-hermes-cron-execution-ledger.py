#!/usr/bin/env python3
"""Apply the small Calliope cron-ledger overlay to pinned Hermes sources.

Re-ported to v0.21.4 (d337b736) on 2026-09-23: upstream now carries the
``delivery_outcome`` column and ``finish_execution(delivery_outcome=...)``
natively (``add_column_if_missing`` in cron/executions.py), so the overlay
adds only ``output_path`` + ``hermes_session_id``, ``bind_execution_session``,
and the ``output_path`` receipt on ``finish_execution``. The scheduler's
bookkeeping tail became ``_finish_completed_run(d: _RunDelivery, ...)``, so the
saved output document is carried on ``_RunDelivery`` to reach the ledger.
"""

import os
from pathlib import Path


def replace_once(path, old, new):
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise SystemExit(f"Pinned Hermes seam changed in {path}: expected one match")
    path.write_text(source.replace(old, new), encoding="utf-8")


source_root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
executions = source_root / "cron" / "executions.py"
replace_once(
    executions,
    '    add_column_if_missing(conn, "executions", "delivery_outcome", "delivery_outcome TEXT")\n',
    '    add_column_if_missing(conn, "executions", "delivery_outcome", "delivery_outcome TEXT")\n'
    '    # Calliope ledger: additive because existing appliances already have durable executions.\n'
    '    add_column_if_missing(conn, "executions", "output_path", "output_path TEXT")\n'
    '    add_column_if_missing(conn, "executions", "hermes_session_id", "hermes_session_id TEXT")\n',
)
replace_once(
    executions,
    "    _emit_execution_state(record)\n"
    "    return record\n\n\n"
    "def finish_execution(\n",
    "    _emit_execution_state(record)\n"
    "    return record\n\n\n"
    "def bind_execution_session(execution_id: str, session_id: str):\n"
    "    \"\"\"Attach the exact model/tool session to a running attempt.\"\"\"\n"
    "    with _transaction() as conn:\n"
    "        conn.execute(\n"
    "            \"UPDATE executions SET hermes_session_id=? \"\n"
    "            \"WHERE id=? AND status IN ('claimed','running')\",\n"
    "            (str(session_id), str(execution_id)),\n"
    "        )\n"
    "        return _fetch(conn, execution_id)\n\n\n"
    "def finish_execution(\n",
)
replace_once(
    executions,
    "    execution_id: str, *, success: bool, error: Optional[str] = None,\n"
    "    delivery_outcome: Optional[str] = None,\n"
    ") -> Optional[Dict[str, Any]]:\n",
    "    execution_id: str, *, success: bool, error: Optional[str] = None,\n"
    "    delivery_outcome: Optional[str] = None,\n"
    "    output_path: Optional[str] = None,\n"
    ") -> Optional[Dict[str, Any]]:\n",
)
replace_once(
    executions,
    '            """UPDATE executions\n'
    "               SET status=?, finished_at=?, error=?, handoff_pending=0,\n"
    "                   handoff_started_at=NULL, delivery_outcome=?\n"
    "               WHERE id=? AND status IN ('claimed','running')\n"
    '                 AND process_id=? AND pid=?""",\n'
    "            (status, now, detail, delivery_outcome, execution_id, _PROCESS_ID, os.getpid()),\n",
    '            """UPDATE executions\n'
    "               SET status=?, finished_at=?, error=?, handoff_pending=0,\n"
    "                   handoff_started_at=NULL, delivery_outcome=?, output_path=?\n"
    "               WHERE id=? AND status IN ('claimed','running')\n"
    '                 AND process_id=? AND pid=?""",\n'
    "            (status, now, detail, delivery_outcome,\n"
    "             str(output_path) if output_path else None,\n"
    "             execution_id, _PROCESS_ID, os.getpid()),\n",
)

scheduler = source_root / "cron" / "scheduler.py"
replace_once(
    scheduler,
    "from cron.executions import (\n"
    "    _TERMINAL_STATES, HANDOFF_ADOPTION_GRACE_SECONDS, create_execution, finish_execution,\n"
    "    get_execution, mark_execution_handoff_pending, mark_execution_running,\n"
    "    recover_interrupted_executions)\n",
    "from cron.executions import (\n"
    "    _TERMINAL_STATES, HANDOFF_ADOPTION_GRACE_SECONDS, bind_execution_session, create_execution,\n"
    "    finish_execution, get_execution, mark_execution_handoff_pending, mark_execution_running,\n"
    "    recover_interrupted_executions)\n",
)
replace_once(
    scheduler,
    "    _cron_session_id = f\"cron_{job_id}_{_hermes_now().strftime('%Y%m%d_%H%M%S')}\"\n"
    "    logger.info(\"Running job '%s' (ID: %s)\", job_name, job_id)\n",
    "    _cron_session_id = f\"cron_{job_id}_{_hermes_now().strftime('%Y%m%d_%H%M%S')}\"\n"
    "    _ledger_execution_id = execution_id or job.get(\"execution_id\")\n"
    "    if _ledger_execution_id:\n"
    "        bind_execution_session(_ledger_execution_id, _cron_session_id)\n"
    "    logger.info(\"Running job '%s' (ID: %s)\", job_name, job_id)\n",
)
replace_once(
    scheduler,
    "    side_effect_ownership_lost: bool = False\n\n\n"
    "def _save_compose_deliver(\n",
    "    side_effect_ownership_lost: bool = False\n"
    "    # Calliope ledger: the saved output document, recorded on the execution row.\n"
    "    output_file: Optional[Any] = None\n\n\n"
    "def _save_compose_deliver(\n",
)
replace_once(
    scheduler,
    "    if verbose and output_file is not None:\n"
    "        logger.info(\"Output saved to: %s\", output_file)\n",
    "    d.output_file = output_file\n"
    "    if verbose and output_file is not None:\n"
    "        logger.info(\"Output saved to: %s\", output_file)\n",
)
replace_once(
    scheduler,
    "    finish_execution(\n"
    "        execution_id, success=d.success, error=d.error, delivery_outcome=delivery_outcome)\n"
    "    return True\n",
    "    finish_execution(\n"
    "        execution_id, success=d.success, error=d.error, delivery_outcome=delivery_outcome,\n"
    "        output_path=str(d.output_file) if d.output_file is not None else None)\n"
    "    return True\n",
)

print("Applied Calliope profile-local cron execution ledger overlay")
