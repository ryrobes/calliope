from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0389_work_item_active_dedupe.sql"
)
REGISTRY = ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs"


def test_active_work_item_dedupe_is_registered_after_generation_catalog():
    registry = REGISTRY.read_text(encoding="utf-8")

    assert registry.index("0388_work_graph_generation_catalog") < registry.index(
        "0389_work_item_active_dedupe"
    )


def test_duplicate_cleanup_is_deterministic_and_precedes_unique_index():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "PARTITION BY dedupe_key" in sql
    assert "ORDER BY created_at ASC, id ASC" in sql
    assert "duplicate_rank > 1" in sql
    assert sql.index("DELETE FROM rvbbit.calliope_work_items") < sql.index(
        "CREATE UNIQUE INDEX"
    )


def test_partial_unique_index_only_covers_active_non_null_keys():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS work_items_active_dedupe_idx" in sql
    assert "ON rvbbit.calliope_work_items (dedupe_key)" in sql
    assert "dedupe_key IS NOT NULL" in sql
    assert "state IN ('unread', 'seen')" in sql
