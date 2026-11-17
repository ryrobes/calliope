"""Mirror Hermes' kanban boards into rvbbit.hermes_kanban_* (migration 0337).

Why this exists: the Hermes kanban is the appliance's work dispatcher —
tasks with typed blocked routing, per-card model/skills/effort overrides,
runs, events, comments — but its state lives in per-board sqlite files on
the Hermes volume, invisible to SQL. The Desk shell's Board view, Today's
decision queue (blocked/triage cards), and Calliope's own reasoning about
in-flight work all want it as tables. The Calliope server already mounts
the Hermes volume read-only (hermes_mirror, 0334); this worker follows the
same pattern for the kanban files:

  <root>/kanban.db                       — the default board
  <root>/kanban/boards/<slug>/kanban.db  — named boards

Design points (mirroring hermes_mirror's):
* sqlite opened ``mode=ro`` (sees the WAL) with an ``immutable=1`` fallback.
* Boards are small (hundreds of cards, not millions of rows): tasks, runs,
  and links are fully re-upserted per tick; events and comments are
  append-only upstream and land with ON CONFLICT DO NOTHING.
* Nothing is pruned: a board removed upstream keeps its last mirrored
  state, with hermes_kanban_boards.synced_at showing staleness.
* A missing table in an older board file is skipped, not fatal.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

_HERE = Path(__file__).resolve().parent
_MIGRATION_NAME = "0337_hermes_kanban_mirror.sql"
KANBAN_ROOT = Path(os.environ.get("WAREHOUSE_HERMES_KANBAN_ROOT", "/opt/data"))
DEFAULT_BOARD = "default"
_TICK_SECONDS = max(15, min(3600, int(os.environ.get("WAREHOUSE_KANBAN_MIRROR_TICK_SECONDS", "60") or 60)))
_ENABLED = os.environ.get("WAREHOUSE_KANBAN_MIRROR", "on").strip().lower() not in {"off", "0", "false", "no"}


def _log(message: str) -> None:
    print(f"kanban-mirror: {message}", file=sys.stderr, flush=True)


def _epoch_ts(value: Any) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _jsonb(value: Any) -> str | None:
    """Source TEXT columns that carry JSON (skills, run metadata, event
    payload) become jsonb; non-JSON text is wrapped so nothing is lost."""
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    text = str(value)
    try:
        json.loads(text)
        return text
    except (TypeError, ValueError):
        return json.dumps({"text": text})


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── row mappers ─────────────────────────────────────────────────────────────

TASK_COLUMNS = (
    "board", "id", "title", "body", "assignee", "status", "priority", "created_by",
    "created_at", "started_at", "completed_at", "tenant", "project_id", "result",
    "idempotency_key", "consecutive_failures", "last_failure_error",
    "max_runtime_seconds", "last_heartbeat_at", "current_run_id",
    "workflow_template_id", "current_step_key", "skills", "model_override",
    "provider_override", "reasoning_effort", "max_retries", "goal_mode",
    "goal_max_turns", "session_id", "block_kind", "block_recurrences",
)


def task_row(raw: dict[str, Any], board: str) -> dict[str, Any]:
    return {
        "board": board,
        "id": str(raw.get("id") or ""),
        "title": raw.get("title"),
        "body": raw.get("body"),
        "assignee": raw.get("assignee"),
        "status": raw.get("status"),
        "priority": _int(raw.get("priority")),
        "created_by": raw.get("created_by"),
        "created_at": _epoch_ts(raw.get("created_at")),
        "started_at": _epoch_ts(raw.get("started_at")),
        "completed_at": _epoch_ts(raw.get("completed_at")),
        "tenant": raw.get("tenant"),
        "project_id": raw.get("project_id"),
        "result": raw.get("result"),
        "idempotency_key": raw.get("idempotency_key"),
        "consecutive_failures": _int(raw.get("consecutive_failures")),
        "last_failure_error": raw.get("last_failure_error"),
        "max_runtime_seconds": _int(raw.get("max_runtime_seconds")),
        "last_heartbeat_at": _epoch_ts(raw.get("last_heartbeat_at")),
        "current_run_id": _int(raw.get("current_run_id")),
        "workflow_template_id": raw.get("workflow_template_id"),
        "current_step_key": raw.get("current_step_key"),
        "skills": _jsonb(raw.get("skills")),
        "model_override": raw.get("model_override"),
        "provider_override": raw.get("provider_override"),
        "reasoning_effort": raw.get("reasoning_effort"),
        "max_retries": _int(raw.get("max_retries")),
        "goal_mode": bool(_int(raw.get("goal_mode")) or 0),
        "goal_max_turns": _int(raw.get("goal_max_turns")),
        "session_id": raw.get("session_id"),
        "block_kind": raw.get("block_kind"),
        "block_recurrences": _int(raw.get("block_recurrences")),
    }


RUN_COLUMNS = (
    "board", "id", "task_id", "profile", "step_key", "status", "outcome",
    "worker_pid", "max_runtime_seconds", "last_heartbeat_at", "started_at",
    "ended_at", "summary", "metadata", "error",
)


def run_row(raw: dict[str, Any], board: str) -> dict[str, Any]:
    return {
        "board": board,
        "id": _int(raw.get("id")),
        "task_id": str(raw.get("task_id") or ""),
        "profile": raw.get("profile"),
        "step_key": raw.get("step_key"),
        "status": raw.get("status"),
        "outcome": raw.get("outcome"),
        "worker_pid": _int(raw.get("worker_pid")),
        "max_runtime_seconds": _int(raw.get("max_runtime_seconds")),
        "last_heartbeat_at": _epoch_ts(raw.get("last_heartbeat_at")),
        "started_at": _epoch_ts(raw.get("started_at")),
        "ended_at": _epoch_ts(raw.get("ended_at")),
        "summary": raw.get("summary"),
        "metadata": _jsonb(raw.get("metadata")),
        "error": raw.get("error"),
    }


EVENT_COLUMNS = ("board", "id", "task_id", "run_id", "kind", "payload", "created_at")


def event_row(raw: dict[str, Any], board: str) -> dict[str, Any]:
    return {
        "board": board,
        "id": _int(raw.get("id")),
        "task_id": str(raw.get("task_id") or ""),
        "run_id": _int(raw.get("run_id")),
        "kind": raw.get("kind"),
        "payload": _jsonb(raw.get("payload")),
        "created_at": _epoch_ts(raw.get("created_at")),
    }


COMMENT_COLUMNS = ("board", "id", "task_id", "author", "body", "created_at")


def comment_row(raw: dict[str, Any], board: str) -> dict[str, Any]:
    return {
        "board": board,
        "id": _int(raw.get("id")),
        "task_id": str(raw.get("task_id") or ""),
        "author": raw.get("author"),
        "body": raw.get("body"),
        "created_at": _epoch_ts(raw.get("created_at")),
    }


LINK_COLUMNS = ("board", "parent_id", "child_id")


# ── discovery + sqlite access ───────────────────────────────────────────────


def discover_boards(root: Path = KANBAN_ROOT) -> list[tuple[str, Path]]:
    boards: list[tuple[str, Path]] = []
    default_db = root / "kanban.db"
    if default_db.is_file():
        boards.append((DEFAULT_BOARD, default_db))
    boards_dir = root / "kanban" / "boards"
    if boards_dir.is_dir():
        for entry in sorted(boards_dir.iterdir()):
            db = entry / "kanban.db"
            if entry.is_dir() and db.is_file():
                boards.append((entry.name, db))
    return boards


def open_board_db(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.execute("SELECT 1")
    except sqlite3.OperationalError:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _read_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    except sqlite3.OperationalError:
        return []   # older board files may predate a table


# ── postgres upserts ────────────────────────────────────────────────────────


def _cursor(pg: Any) -> Any:
    return pg.cursor() if hasattr(pg, "cursor") else pg


def _upsert(pg: Any, table: str, columns: Iterable[str], key: Iterable[str],
            rows: list[dict[str, Any]], *, update: bool = True) -> int:
    if not rows:
        return 0
    columns = list(columns)
    key = list(key)
    placeholders = ",".join(["%s"] * len(columns))
    updates = ",".join(
        f"{col}=EXCLUDED.{col}" for col in columns if col not in key
    )
    conflict = (
        f"DO UPDATE SET {updates}, synced_at=now()" if update and updates else "DO NOTHING"
    )
    sql = (
        f"INSERT INTO rvbbit.{table} ({','.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({','.join(key)}) {conflict}"
    )
    cursor = _cursor(pg)
    for row in rows:
        cursor.execute(sql, tuple(row.get(col) for col in columns))
    return len(rows)


def sync_board(pg: Any, conn: sqlite3.Connection, board: str, db_path: Path) -> dict[str, int]:
    tasks = [task_row(raw, board) for raw in _read_table(conn, "tasks")]
    tasks = [row for row in tasks if row["id"]]
    runs = [run_row(raw, board) for raw in _read_table(conn, "task_runs")]
    runs = [row for row in runs if row["id"] is not None]
    events = [event_row(raw, board) for raw in _read_table(conn, "task_events")]
    events = [row for row in events if row["id"] is not None]
    comments = [comment_row(raw, board) for raw in _read_table(conn, "task_comments")]
    comments = [row for row in comments if row["id"] is not None]
    links = [
        {"board": board, "parent_id": str(raw.get("parent_id") or ""), "child_id": str(raw.get("child_id") or "")}
        for raw in _read_table(conn, "task_links")
    ]
    links = [row for row in links if row["parent_id"] and row["child_id"]]

    counts = {
        "tasks": _upsert(pg, "hermes_kanban_tasks", TASK_COLUMNS, ("board", "id"), tasks),
        "runs": _upsert(pg, "hermes_kanban_runs", RUN_COLUMNS, ("board", "id"), runs),
        "events": _upsert(pg, "hermes_kanban_events", EVENT_COLUMNS, ("board", "id"), events, update=False),
        "comments": _upsert(pg, "hermes_kanban_comments", COMMENT_COLUMNS, ("board", "id"), comments, update=False),
        "links": _upsert(pg, "hermes_kanban_links", LINK_COLUMNS, ("board", "parent_id", "child_id"), links, update=False),
    }
    _cursor(pg).execute(
        "INSERT INTO rvbbit.hermes_kanban_boards (board, db_path, task_count, synced_at) "
        "VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (board) DO UPDATE SET db_path=EXCLUDED.db_path, "
        "task_count=EXCLUDED.task_count, synced_at=now()",
        (board, str(db_path), len(tasks)),
    )
    return counts


def sync_once(conn_factory: Callable[[], Any], *, root: Path = KANBAN_ROOT) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    for board, db_path in discover_boards(root):
        sqlite_conn = open_board_db(db_path)
        try:
            with conn_factory() as pg:
                results[board] = sync_board(pg, sqlite_conn, board, db_path)
        finally:
            sqlite_conn.close()
    return results


# ── schema + worker ─────────────────────────────────────────────────────────


def _migration_path() -> Path:
    return _HERE / "sql" / _MIGRATION_NAME


def ensure(conn_factory: Callable[[], Any]) -> bool:
    """Apply 0337 (idempotent DDL) so the mirror works before the extension
    chain ships it; the extension re-applies the same file harmlessly."""
    path = _migration_path()
    if not path.is_file():
        return False
    with conn_factory() as pg:
        pg.execute(path.read_text(encoding="utf-8"))
    return True


_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_WAKE = threading.Event()


def _worker(conn_factory: Callable[[], Any]) -> None:
    misses = 0
    try:
        ensure(conn_factory)
    except Exception as exc:  # noqa: BLE001 — a read-only role gets the tables from the extension instead
        _log(f"schema ensure skipped ({type(exc).__name__}): {str(exc)[:200]}")
    while True:
        try:
            result = sync_once(conn_factory)
            moved = sum(sum(counts.values()) for counts in result.values())
            if moved:
                _log(f"synced {json.dumps(result, default=str)}")
            misses = 0
        except Exception as exc:  # noqa: BLE001 — a bad tick must not stop the mirror
            misses += 1
            if misses <= 3 or misses % 30 == 0:
                _log(f"tick failed ({type(exc).__name__}): {str(exc)[:300]}")
        _WAKE.wait(_TICK_SECONDS)
        _WAKE.clear()


def available() -> bool:
    """The volume root existing is enough: boards created later are picked
    up on subsequent ticks without a restart."""
    return _ENABLED and KANBAN_ROOT.is_dir()


def start_worker(conn_factory: Callable[[], Any]) -> bool:
    global _THREAD
    if not available():
        return False
    with _THREAD_LOCK:
        if _THREAD and _THREAD.is_alive():
            return True
        _THREAD = threading.Thread(
            target=_worker, args=(conn_factory,), name="kanban-mirror", daemon=True
        )
        _THREAD.start()
    return True


def wake() -> None:
    _WAKE.set()
