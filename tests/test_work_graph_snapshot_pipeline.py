from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TIMELINE_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0387_work_graph_retained_timeline.sql"
)
CATALOG_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0388_work_graph_generation_catalog.sql"
)
REGISTRY = ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs"
FIRST_BOOT_SCHEDULES = ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh"
HOSTED_BOOTSTRAP = ROOT / "docker" / "uber" / "bootstrap.sh"


def test_work_graph_timeline_is_registered_and_route_keyed():
    sql = TIMELINE_MIGRATION.read_text(encoding="utf-8")
    registry = REGISTRY.read_text(encoding="utf-8")

    assert registry.index("0386_work_graph_snapshots") < registry.index(
        "0387_work_graph_retained_timeline"
    )
    assert registry.index("0387_work_graph_retained_timeline") < registry.index(
        "0388_work_graph_generation_catalog"
    )
    assert "CREATE OR REPLACE VIEW rvbbit.work_graph AS" in sql
    assert "CREATE TABLE IF NOT EXISTS rvbbit.work_graph_snapshots" in sql
    assert "route                text PRIMARY KEY" in sql
    assert ") USING rvbbit;" in sql
    assert "generation_semantics => 'cumulative'" in sql
    assert "history_policy => 'retained'" in sql


def test_work_graph_capture_uses_merge_then_incremental_publication():
    sql = TIMELINE_MIGRATION.read_text(encoding="utf-8")

    assert "ON CONFLICT (route) DO UPDATE" in sql
    assert "WHERE graph.route = snap.route" in sql
    assert "CREATE OR REPLACE PROCEDURE rvbbit.work_graph_snapshot_run()" in sql
    assert sql.index("COMMIT AND CHAIN;") < sql.index("rvbbit.refresh_acceleration(")
    assert "v_delete_generation > v_published_generation" in sql
    assert "delete_generation_rows" in sql
    assert "TRUNCATE TABLE rvbbit.work_graph_snapshots" not in sql
    assert "SELECT rvbbit.rebuild_acceleration(" not in sql


def test_generation_catalog_separates_queryable_counts_from_as_of_state():
    sql = CATALOG_MIGRATION.read_text(encoding="utf-8")

    assert "RENAME TO work_graph_timeline" in sql
    assert "CREATE TABLE rvbbit.work_graph_snapshots" in sql
    assert "counts_by_column_state" in sql
    assert "counts_by_item_kind" in sql
    assert "counts_by_item_kind_and_column_state" in sql
    assert "CREATE OR REPLACE VIEW rvbbit.work_graph_snapshot_counts AS" in sql
    assert "FROM rvbbit.work_graph_snapshots_0386" in sql
    assert "INSERT INTO rvbbit.work_graph_timeline AS snap" in sql
    assert "'rvbbit.work_graph_timeline'::regclass" in sql
    assert "work_graph_snapshot_catalog_tick" in sql
    assert "date_bin(" in sql
    assert "'15 minutes'" in sql
    assert ") USING rvbbit;" not in sql


def test_volatile_idle_seconds_does_not_force_timeline_generations():
    sql = CATALOG_MIGRATION.read_text(encoding="utf-8")
    distinct_clause = sql.split("WHERE ROW(", 1)[1].split("RETURNING 1", 1)[0]

    assert "snap.idle_seconds" not in distinct_clause
    assert "EXCLUDED.idle_seconds" not in distinct_clause
    assert "idle_seconds = EXCLUDED.idle_seconds" in sql


def test_every_install_reconciles_the_15_minute_capture_job():
    for schedule_source in (FIRST_BOOT_SCHEDULES, HOSTED_BOOTSTRAP):
        source = schedule_source.read_text(encoding="utf-8")
        assert "rvbbit_work_graph_snapshot" in source
        assert "'*/15 * * * *'" in source
        assert "CALL rvbbit.work_graph_snapshot_run();" in source
        assert "command='CALL rvbbit.work_graph_snapshot_run();',active=true" in source
