"""kanban_mirror: Hermes' kanban board sqlite files → rvbbit.hermes_kanban_* (0337)."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import kanban_mirror as km  # noqa: E402


def _seed_board(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT,
            status TEXT NOT NULL, priority INTEGER DEFAULT 0, created_by TEXT,
            created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
            tenant TEXT, project_id TEXT, result TEXT, idempotency_key TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0, last_failure_error TEXT,
            max_runtime_seconds INTEGER, last_heartbeat_at INTEGER, current_run_id INTEGER,
            workflow_template_id TEXT, current_step_key TEXT, skills TEXT,
            model_override TEXT, provider_override TEXT, reasoning_effort TEXT,
            max_retries INTEGER, goal_mode INTEGER NOT NULL DEFAULT 0,
            goal_max_turns INTEGER, session_id TEXT, block_kind TEXT,
            block_recurrences INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, profile TEXT,
            step_key TEXT, status TEXT NOT NULL, claim_lock TEXT, claim_expires INTEGER,
            worker_pid INTEGER, max_runtime_seconds INTEGER, last_heartbeat_at INTEGER,
            started_at INTEGER NOT NULL, ended_at INTEGER, outcome TEXT,
            summary TEXT, metadata TEXT, error TEXT
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER,
            kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL
        );
        CREATE TABLE task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            author TEXT NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE task_links (
            parent_id TEXT NOT NULL, child_id TEXT NOT NULL,
            PRIMARY KEY (parent_id, child_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks (id,title,assignee,status,created_at,session_id,block_kind,"
        "skills,goal_mode,reasoning_effort,idempotency_key) "
        "VALUES ('AA-114','Approve vendor invoices','rounds','blocked',1787200000,"
        "'calliope_abc123','decision','[\"invoice-runbook\"]',1,'high','inv-2026-08')"
    )
    conn.execute(
        "INSERT INTO tasks (id,title,status,created_at,goal_mode) "
        "VALUES ('AA-115','Q3 dashboard rebuild','running',1787210000,0)"
    )
    conn.execute(
        "INSERT INTO task_runs (task_id,profile,status,started_at,ended_at,outcome,summary,metadata) "
        "VALUES ('AA-115','dashboard-studio','done',1787210100,1787213000,'completed',"
        "'built and verified','{\"artifacts\": 2}')"
    )
    conn.execute(
        "INSERT INTO task_events (task_id,run_id,kind,payload,created_at) "
        "VALUES ('AA-115',1,'completed','not json',1787213000)"
    )
    conn.execute(
        "INSERT INTO task_comments (task_id,author,body,created_at) "
        "VALUES ('AA-114','michael','the 13th invoice is from a new vendor',1787214000)"
    )
    conn.execute("INSERT INTO task_links (parent_id,child_id) VALUES ('AA-115','AA-114')")
    conn.commit()
    conn.close()


class _CapturePg:
    """Stands in for a psycopg connection: records every execute."""

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        return self


def test_discovery_finds_default_and_named_boards(tmp_path):
    (tmp_path / "kanban.db").write_bytes(b"")
    named = tmp_path / "kanban" / "boards" / "ops"
    named.mkdir(parents=True)
    (named / "kanban.db").write_bytes(b"")
    (tmp_path / "kanban" / "boards" / "empty-dir").mkdir()
    boards = km.discover_boards(tmp_path)
    assert boards == [
        ("default", tmp_path / "kanban.db"),
        ("ops", named / "kanban.db"),
    ]


def test_sync_board_mirrors_every_table_with_the_right_shapes(tmp_path):
    db = tmp_path / "kanban.db"
    _seed_board(db)
    pg = _CapturePg()
    conn = km.open_board_db(db)
    try:
        counts = km.sync_board(pg, conn, "default", db)
    finally:
        conn.close()
    assert counts == {"tasks": 2, "runs": 1, "events": 1, "comments": 1, "links": 1}

    task_sql, task_params = next(
        (sql, params) for sql, params in pg.statements
        if "hermes_kanban_tasks" in sql and params and params[1] == "AA-114"
    )
    assert "ON CONFLICT (board,id) DO UPDATE" in task_sql
    row = dict(zip(km.TASK_COLUMNS, task_params))
    assert row["board"] == "default" and row["status"] == "blocked"
    assert row["block_kind"] == "decision" and row["session_id"] == "calliope_abc123"
    assert row["goal_mode"] is True and row["reasoning_effort"] == "high"
    assert row["idempotency_key"] == "inv-2026-08"
    assert row["created_at"] == datetime.fromtimestamp(1787200000, tz=timezone.utc)
    assert row["skills"] == '["invoice-runbook"]'          # valid JSON passes through

    run_sql, run_params = next(
        (sql, params) for sql, params in pg.statements if "hermes_kanban_runs" in sql
    )
    run = dict(zip(km.RUN_COLUMNS, run_params))
    assert run["outcome"] == "completed" and run["metadata"] == '{"artifacts": 2}'

    event_sql, event_params = next(
        (sql, params) for sql, params in pg.statements if "hermes_kanban_events" in sql
    )
    assert "DO NOTHING" in event_sql                        # append-only upstream
    event = dict(zip(km.EVENT_COLUMNS, event_params))
    assert event["payload"] == '{"text": "not json"}'       # non-JSON text is wrapped

    board_sql, board_params = next(
        (sql, params) for sql, params in pg.statements if "hermes_kanban_boards" in sql
    )
    assert board_params[0] == "default" and board_params[2] == 2


def test_missing_tables_are_skipped_not_fatal(tmp_path):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, created_at INTEGER)"
    )
    conn.execute("INSERT INTO tasks VALUES ('T-1','old board','todo',1787000000)")
    conn.commit()
    conn.close()
    pg = _CapturePg()
    sqlite_conn = km.open_board_db(db)
    try:
        counts = km.sync_board(pg, sqlite_conn, "old", db)
    finally:
        sqlite_conn.close()
    assert counts == {"tasks": 1, "runs": 0, "events": 0, "comments": 0, "links": 0}
