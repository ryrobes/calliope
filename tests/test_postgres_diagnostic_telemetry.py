import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "crates/pg_rvbbit/sql/migrations/0366_postgres_diagnostic_telemetry.sql"
REGISTRY = ROOT / "crates/pg_rvbbit/src/migrations.rs"
PROVIDER_CATALOG = ROOT / "crates/pg_rvbbit/src/provider_catalog.rs"
POSTGRES_DOCKERFILE = ROOT / "docker/Dockerfile.rvbbit"
INIT_SQL = ROOT / "docker/init/01-create-extension.sql"
BOOTSTRAP = ROOT / "docker/uber/bootstrap.sh"
FIRST_BOOT_SCHEDULES = ROOT / "docker/init/03-seed-scheduled-tasks.sh"


def test_postgres_diagnostic_migration_contract_is_registered_and_plan_only():
    sql = MIGRATION.read_text()
    registry = REGISTRY.read_text()
    provider_catalog = PROVIDER_CATALOG.read_text()

    assert '"0366_postgres_diagnostic_telemetry"' in registry
    assert "CREATE TABLE IF NOT EXISTS rvbbit.postgres_diagnostic_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS rvbbit.postgres_diagnostic_findings" in sql
    assert "CREATE TABLE IF NOT EXISTS rvbbit.postgres_index_advice" in sql
    assert "EXPLAIN (GENERIC_PLAN, FORMAT JSON)" in sql
    assert "EXPLAIN ANALYZE" not in sql.upper()
    assert "hypopg_create_index" in sql
    assert "hypopg_reset" in sql
    assert "raw_query_text_persisted',false" in sql
    assert "query text is never stored" in sql
    assert "rvbbit-postgres-diagnostics" in provider_catalog
    assert "SELECT rvbbit.collect_postgres_diagnostics();" in provider_catalog
    assert '"postgres_diagnostics_schedule": "11 * * * *"' in provider_catalog


def test_appliance_packages_and_installs_hypopg():
    assert "postgresql-18-hypopg" in POSTGRES_DOCKERFILE.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS hypopg" in INIT_SQL.read_text()
    bootstrap = BOOTSTRAP.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS hypopg" in bootstrap
    assert "cron.schedule_in_database('rvbbit-postgres-diagnostics','11 * * * *'" in bootstrap
    assert "command='SELECT rvbbit.collect_postgres_diagnostics();',active=true" in bootstrap


def test_fresh_monorepo_install_seeds_postgres_diagnostics_schedule():
    first_boot = FIRST_BOOT_SCHEDULES.read_text()
    assert "cron.schedule_in_database('rvbbit-postgres-diagnostics','11 * * * *'" in first_boot
    assert "command='SELECT rvbbit.collect_postgres_diagnostics();',active=true" in first_boot


def test_deterministic_findings_are_durable_current_and_agent_visible(rvbbit):
    rvbbit.execute("SELECT rvbbit.migrate()")
    table = f"diagnostic_autovacuum_{uuid.uuid4().hex[:8]}"
    run_id = None
    try:
        rvbbit.execute(
            f"CREATE TABLE {table}(id integer) WITH (autovacuum_enabled=false)"
        )
        result = rvbbit.execute(
            "SELECT rvbbit.collect_postgres_diagnostics(false)"
        ).fetchone()[0]
        run_id = result["run_id"]

        assert result["status"] == "ok"
        assert result["findings"] >= 1
        assert result["index_candidates"] == 0

        finding = rvbbit.execute(
            "SELECT finding_key,object_class,object_identity,severity,exactness,"
            "       evidence->>'table',recommendation IS NOT NULL "
            "FROM rvbbit.postgres_diagnostic_current_findings "
            "WHERE finding_key='autovacuum_disabled_on_table' "
            "  AND object_identity=%s",
            (f"public.{table}",),
        ).fetchone()
        assert finding == (
            "autovacuum_disabled_on_table",
            "relation",
            f"public.{table}",
            "critical",
            "scraped",
            f"public.{table}",
            True,
        )

        learned = rvbbit.execute(
            "SELECT props->>'object_type',props->>'finding_key',"
            "       props->>'object_identity',props->>'exactness' "
            "FROM rvbbit.system_learning_postgres_items "
            "WHERE props->>'object_identity'=%s",
            (f"public.{table}",),
        ).fetchone()
        assert learned == (
            "postgres_finding",
            "autovacuum_disabled_on_table",
            f"public.{table}",
            "scraped",
        )

        persisted = rvbbit.execute(
            "SELECT summary->>'raw_query_text_persisted',status "
            "FROM rvbbit.postgres_diagnostic_runs WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        assert persisted == ("false", "ok")
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {table}")
        if run_id is not None:
            rvbbit.execute(
                "DELETE FROM rvbbit.postgres_diagnostic_runs WHERE run_id=%s",
                (run_id,),
            )


def test_index_advice_degrades_explicitly_when_optional_capabilities_are_missing(rvbbit):
    rvbbit.execute("SELECT rvbbit.migrate()")
    result = rvbbit.execute(
        "SELECT rvbbit.collect_postgres_diagnostics(true,1,0,0.5)"
    ).fetchone()[0]
    try:
        capabilities = result["capabilities"]
        ready = (
            capabilities["pg_stat_statements"]
            and capabilities["hypopg"]
            and capabilities["generic_plan"]
        )
        assert result["status"] == ("ok" if ready else "degraded")
        assert isinstance(result["index_candidates"], int)
        assert capabilities["index_advice_requested"] is True
    finally:
        rvbbit.execute(
            "DELETE FROM rvbbit.postgres_diagnostic_runs WHERE run_id=%s",
            (result["run_id"],),
        )


def test_hypopg_advice_keeps_only_planner_selected_cost_reductions(rvbbit):
    rvbbit.execute("SELECT rvbbit.migrate()")
    ready = rvbbit.execute(
        "SELECT current_setting('server_version_num')::int>=160000 "
        "AND EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements') "
        "AND EXISTS (SELECT 1 FROM pg_extension WHERE extname='hypopg')"
    ).fetchone()[0]
    if not ready:
        pytest.skip("HypoPG advice requires PostgreSQL 16+, pg_stat_statements, and hypopg")

    table = f"diagnostic_hypopg_{uuid.uuid4().hex[:8]}"
    run_id = None
    try:
        rvbbit.execute(f"CREATE TABLE {table}(id bigint, tenant_id integer, payload text)")
        rvbbit.execute(
            f"INSERT INTO {table} "
            "SELECT g,g%1000,repeat('x',80) FROM generate_series(1,100000) g"
        )
        # Separate command so pg_stat_user_tables has observed the analyze reset
        # before the collector applies its stale-statistics guard.
        rvbbit.execute(f"ANALYZE {table}")
        for _ in range(3):
            rvbbit.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (42,)
            ).fetchone()

        result = rvbbit.execute(
            "SELECT rvbbit.collect_postgres_diagnostics(true,100,0,0.2)"
        ).fetchone()[0]
        run_id = result["run_id"]
        advice = rvbbit.execute(
            "SELECT columns,cost_before,cost_after,improvement_pct,index_ddl,"
            "       exactness,confidence,caveats "
            "FROM rvbbit.postgres_index_advice "
            "WHERE run_id=%s AND table_oid=%s::regclass",
            (run_id, table),
        ).fetchone()

        assert result["status"] == "ok"
        assert result["candidates_tested"] >= 1
        assert advice is not None
        assert advice[0] == ["tenant_id"]
        assert advice[2] < advice[1]
        assert advice[3] >= 20
        assert advice[4] == f"CREATE INDEX ON public.{table} (tenant_id)"
        assert advice[5] == "planner_estimate"
        assert float(advice[6]) == 0.6
        assert any("not a measured speedup" in caveat for caveat in advice[7])
        assert rvbbit.execute(
            "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "WHERE i.indrelid=%s::regclass",
            (table,),
        ).fetchone() == (0,)
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {table}")
        if run_id is not None:
            rvbbit.execute(
                "DELETE FROM rvbbit.postgres_diagnostic_runs WHERE run_id=%s",
                (run_id,),
            )
