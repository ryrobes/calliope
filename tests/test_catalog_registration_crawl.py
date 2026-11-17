"""A newly registered table reaches the catalog KG without waiting for the
nightly crawl (migration 0323).

Registration (``USING rvbbit`` / enable_table) enqueues a debounced,
schema-scoped crawl request; ``catalog_crawl_requests_tick`` waits for quiet
and for rows to exist, then runs the existing ``catalog_crawl`` for that
schema. ``rvbbit.accelerated_tables`` names registered relations for agents
that cannot see pg_class through run_sql.
"""
from __future__ import annotations

import pytest


def _installed(rvbbit) -> bool:
    row = rvbbit.execute(
        "SELECT to_regprocedure('rvbbit.catalog_crawl_requests_tick(interval,interval)') IS NOT NULL"
    ).fetchone()
    return bool(row and row[0])


def test_registration_enqueues_a_debounced_schema_crawl(rvbbit, temp_table):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0323_catalog_registration_crawl")
    rvbbit.execute("DELETE FROM rvbbit.catalog_crawl_requests WHERE schema_name='public' AND status='pending'")

    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")
    req = rvbbit.execute(
        "SELECT request_id, schema_name, table_name, reason, status FROM rvbbit.catalog_crawl_requests "
        "WHERE schema_name='public' AND status='pending'"
    ).fetchone()
    assert req is not None
    assert req[1] == "public" and req[3] == "table_registered"

    # A second registration in the same schema extends the same pending request
    # (debounce) instead of queueing a duplicate.
    other = f"{temp_table}_b"
    try:
        rvbbit.execute(f"CREATE TABLE {other} (id int) USING rvbbit")
        pending = rvbbit.execute(
            "SELECT count(*), max(table_name) FROM rvbbit.catalog_crawl_requests "
            "WHERE schema_name='public' AND status='pending'"
        ).fetchone()
        assert pending[0] == 1
        assert pending[1] == other
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {other} CASCADE")

    # The view names the relation and reports it as not yet cataloged.
    row = rvbbit.execute(
        "SELECT schema_name, table_name, relation, acceleration_enabled, cataloged "
        "FROM rvbbit.accelerated_tables WHERE table_name=%s",
        (temp_table,),
    ).fetchone()
    assert row is not None
    assert row[2] == f"public.{temp_table}"
    assert row[4] is False


def test_tick_waits_for_quiet_and_rows_then_crawls_the_schema(rvbbit, temp_table):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0323_catalog_registration_crawl")
    rvbbit.execute("DELETE FROM rvbbit.catalog_crawl_requests WHERE schema_name='public' AND status='pending'")
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")

    # Inside the quiet window nothing happens.
    quiet = rvbbit.execute("SELECT rvbbit.catalog_crawl_requests_tick(interval '1 hour')").fetchone()[0]
    assert quiet["status"] == "idle"

    # Past the quiet window but with an empty relation the tick waits (bounded).
    waiting = rvbbit.execute(
        "SELECT rvbbit.catalog_crawl_requests_tick(interval '0 seconds', interval '1 hour')"
    ).fetchone()[0]
    assert waiting["status"] in {"waiting_for_rows", "crawled", "idle"}

    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'row '||g FROM generate_series(1,50) g")
    rvbbit.execute(f"ANALYZE {temp_table}")
    result = rvbbit.execute(
        "SELECT rvbbit.catalog_crawl_requests_tick(interval '0 seconds', interval '0 seconds')"
    ).fetchone()[0]
    assert result["status"] in {"crawled", "idle"}, result
    if result["status"] == "crawled":
        assert result["schema"] == "public"
        done = rvbbit.execute(
            "SELECT status, attempts, result IS NOT NULL FROM rvbbit.catalog_crawl_requests "
            "WHERE request_id=%s", (result["request_id"],)
        ).fetchone()
        assert done[0] == "done" and done[1] == 1 and done[2] is True
    cataloged = rvbbit.execute(
        "SELECT cataloged FROM rvbbit.accelerated_tables WHERE table_name=%s", (temp_table,)
    ).fetchone()[0]
    assert cataloged is True


def test_operational_schemas_are_never_requested(rvbbit):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0323_catalog_registration_crawl")
    for schema in ("rvbbit", "pg_catalog", "information_schema", "cron"):
        assert rvbbit.execute(
            "SELECT rvbbit.request_catalog_crawl(%s, 'x', 'test')", (schema,)
        ).fetchone()[0] is None
