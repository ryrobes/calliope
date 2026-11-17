"""Dashboard schema checks stay out of concurrent publication traffic."""
from __future__ import annotations

import sys
import threading
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, statements, *, ready):
        self.statements = statements
        self.ready = ready

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _Transaction()

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        if statement == server._DASHBOARD_SCHEMA_READINESS_SQL:
            return _Result({"ready": self.ready})
        if statement == server._DASHBOARDS_DDL:
            self.ready = True
        return _Result()


def test_current_schema_skips_ddl_and_latches_process_ready(monkeypatch):
    statements = []
    connections = []

    def connect():
        connections.append(True)
        return _Connection(statements, ready=True)

    monkeypatch.setattr(server, "_DASHBOARD_SCHEMA_READY", False)
    monkeypatch.setattr(server, "_conn", connect)

    assert server._ensure_dashboard_tables() is True
    assert server._ensure_dashboard_tables() is True
    assert len(connections) == 1
    assert [statement for statement, _params in statements].count(
        server._DASHBOARDS_DDL
    ) == 0
    assert "pg_advisory_xact_lock" in statements[0][0]


def test_missing_schema_runs_ddl_only_once_under_concurrent_calls(monkeypatch):
    statements = []

    monkeypatch.setattr(server, "_DASHBOARD_SCHEMA_READY", False)
    monkeypatch.setattr(
        server,
        "_conn",
        lambda: _Connection(statements, ready=False),
    )

    barrier = threading.Barrier(8)
    results = []

    def ensure():
        barrier.wait()
        results.append(server._ensure_dashboard_tables())

    threads = [threading.Thread(target=ensure) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True] * 8
    assert [statement for statement, _params in statements].count(
        server._DASHBOARDS_DDL
    ) == 1
    assert [statement for statement, _params in statements].count(
        server._DASHBOARD_SCHEMA_READINESS_SQL
    ) == 2
