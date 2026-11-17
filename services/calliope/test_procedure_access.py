from __future__ import annotations

import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import procedure_access  # noqa: E402


def test_retired_planner_never_creates_an_action_run():
    def forbidden():
        pytest.fail('Retired planner touched the database')
    with pytest.raises(procedure_access.ProcedureError) as error:
        procedure_access.plan_run(forbidden, 'person@example.com', 'old', trigger_kind='schedule')
    assert error.value.status == 410
    assert 'read_calliope_playbook' in str(error.value)


def test_procedure_migration_reuses_capability_search_and_action_runs():
    migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" /
        "0356_calliope_procedures.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "ADD COLUMN IF NOT EXISTS playbook_kind" in migration
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_procedure_strategies" in migration
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_procedure_runs" in migration
    assert "REFERENCES rvbbit.calliope_action_runs" in migration
    assert "signature: run_calliope_procedure" in migration
    assert "calliope_procedure_observatory" in migration
    assert '"0356_calliope_procedures"' in registry


def test_procedure_observatory_views_have_a_forward_repair_migration():
    migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" /
        "0357_calliope_procedure_observatory_views.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "CREATE OR REPLACE VIEW rvbbit.calliope_procedure_observatory" in migration
    assert "CREATE OR REPLACE VIEW rvbbit.calliope_procedure_run_observatory" in migration
    assert '"0357_calliope_procedure_observatory_views"' in registry
