#!/usr/bin/env python3
"""Build-time regression test for profile-local Hermes cron execution ledgers."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from cron.executions import (
    bind_execution_session,
    create_execution,
    finish_execution,
    mark_execution_running,
)
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def run(profile, output):
    profile.mkdir(parents=True, exist_ok=True)
    token = set_hermes_home_override(str(profile))
    try:
        record = create_execution("job-1", source="calliope-build-test")
        assert mark_execution_running(record["id"])["status"] == "running"
        session_id = f"cron_job-1_{record['id'][:8]}"
        assert bind_execution_session(record["id"], session_id)["hermes_session_id"] == session_id
        finished = finish_execution(
            record["id"], success=True, delivery_outcome="suppressed",
            output_path=str(output),
        )
        assert finished["output_path"] == str(output)
        assert finished["delivery_outcome"] == "suppressed"
        return record["id"]
    finally:
        reset_hermes_home_override(token)


with tempfile.TemporaryDirectory(prefix="calliope-cron-ledger-") as temporary:
    root = Path(temporary)
    first = root / "profiles" / "first"
    second = root / "profiles" / "second"
    first_id = run(first, first / "cron" / "output" / "job-1" / "first.md")
    second_id = run(second, second / "cron" / "output" / "job-1" / "second.md")

    for profile, expected in ((first, first_id), (second, second_id)):
        database = profile / "cron" / "executions.db"
        assert database.is_file(), database
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT id,status,output_path,delivery_outcome,hermes_session_id FROM executions"
            ).fetchone()
        assert row[0] == expected
        assert row[1] == "completed"
        assert row[2].endswith(".md")
        assert row[3] == "suppressed"
        assert row[4] == f"cron_job-1_{expected[:8]}"

print("Hermes cron execution ledger is profile-local and receipt-complete")
