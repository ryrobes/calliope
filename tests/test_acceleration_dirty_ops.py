import os
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

RVBBIT_DSN = os.environ.get(
    "RVBBIT_DSN", "postgresql://postgres:rvbbit@pg-rvbbit:5432/bench"
)


def _labels(rvbbit, table):
    return rvbbit.execute(
        f"SELECT count(*) AS n, string_agg(label, ',' ORDER BY label) AS labels FROM {table}"
    ).fetchone()


def _plan_text(rvbbit, sql, analyze=False):
    opts = "ANALYZE, COSTS OFF, TIMING OFF" if analyze else "FORMAT TEXT"
    rows = rvbbit.execute(f"EXPLAIN ({opts}) {sql}").fetchall()
    return "\n".join(row[0] for row in rows)


def _dirty_flags(rvbbit, table):
    return rvbbit.execute(
        f"""
        SELECT shadow_heap_dirty, dirty_has_insert, dirty_has_update,
               dirty_has_delete, dirty_has_truncate
        FROM rvbbit.tables
        WHERE table_oid = '{table}'::regclass
        """
    ).fetchone()


def _remove_one_identity_map_entry(rvbbit, table):
    rvbbit.execute(
        f"""
        DELETE FROM rvbbit.row_identity_map
        WHERE ctid IN (
            SELECT ctid
            FROM rvbbit.row_identity_map
            WHERE table_oid = '{table}'::regclass
            LIMIT 1
        )
        """
    )


def test_dirty_marker_fallback_does_not_wait_on_catalog_row(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (1, 'old')")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)")

    locker = psycopg.connect(RVBBIT_DSN, autocommit=False)
    writer = psycopg.connect(RVBBIT_DSN, autocommit=True)
    try:
        locker.execute(
            f"""
            SELECT 1
            FROM rvbbit.tables
            WHERE table_oid = '{temp_table}'::regclass
            FOR UPDATE
            """
        )

        writer.execute("SET lock_timeout = '250ms'")
        writer.execute(f"UPDATE {temp_table} SET label = 'new' WHERE id = 1")

        raw_dirty, effective_dirty, effective_update, markers = rvbbit.execute(
            f"""
            SELECT t.shadow_heap_dirty,
                   ds.shadow_heap_dirty,
                   ds.dirty_has_update,
                   (SELECT count(*)
                    FROM rvbbit.table_dirty_markers m
                    WHERE m.table_oid = '{temp_table}'::regclass
                      AND m.dirty_op = 'U')::int
            FROM rvbbit.tables t
            JOIN rvbbit.table_dirty_state ds ON ds.table_oid = t.table_oid
            WHERE t.table_oid = '{temp_table}'::regclass
            """
        ).fetchone()

        assert raw_dirty is False
        assert effective_dirty is True
        assert effective_update is True
        assert markers >= 1
    finally:
        locker.rollback()
        locker.close()
        writer.close()

    result = rvbbit.execute(
        f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
    ).fetchone()[0]
    assert result["status"] == "ok"
    assert _labels(rvbbit, temp_table) == (1, "new")
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)
    assert (
        rvbbit.execute(
            f"""
            SELECT count(*)::int
            FROM rvbbit.table_dirty_markers
            WHERE table_oid = '{temp_table}'::regclass
            """
        ).fetchone()[0]
        == 0
    )


def test_dirty_marker_fallback_allows_parallel_same_table_writers(rvbbit, temp_table):
    writer_count = 8
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(
        f"""
        INSERT INTO {temp_table}
        SELECT g, 'old'
        FROM generate_series(1, {writer_count}) AS g
        """
    )
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)")

    locker = psycopg.connect(RVBBIT_DSN, autocommit=False)
    start = threading.Barrier(writer_count)

    def write_one(row_id):
        with psycopg.connect(RVBBIT_DSN, autocommit=True) as writer:
            writer.execute("SET lock_timeout = '250ms'")
            start.wait(timeout=5)
            writer.execute(
                f"UPDATE {temp_table} SET label = %s WHERE id = %s",
                (f"new {row_id}", row_id),
            )

    try:
        locker.execute(
            f"""
            SELECT 1
            FROM rvbbit.tables
            WHERE table_oid = '{temp_table}'::regclass
            FOR UPDATE
            """
        )

        with ThreadPoolExecutor(max_workers=writer_count) as pool:
            futures = [pool.submit(write_one, i) for i in range(1, writer_count + 1)]
            for future in futures:
                future.result(timeout=10)

        raw_dirty, effective_dirty, effective_update, markers = rvbbit.execute(
            f"""
            SELECT t.shadow_heap_dirty,
                   ds.shadow_heap_dirty,
                   ds.dirty_has_update,
                   (SELECT count(*)
                    FROM rvbbit.table_dirty_markers m
                    WHERE m.table_oid = '{temp_table}'::regclass
                      AND m.dirty_op = 'U')::int
            FROM rvbbit.tables t
            JOIN rvbbit.table_dirty_state ds ON ds.table_oid = t.table_oid
            WHERE t.table_oid = '{temp_table}'::regclass
            """
        ).fetchone()
        routed_updated_rows = rvbbit.execute(
            f"SELECT count(*)::int FROM {temp_table} WHERE label LIKE 'new %'"
        ).fetchone()[0]
        rvbbit.execute("SET rvbbit.force_heap_scan = on")
        try:
            heap_updated_rows = rvbbit.execute(
                f"SELECT count(*)::int FROM {temp_table} WHERE label LIKE 'new %'"
            ).fetchone()[0]
        finally:
            rvbbit.execute("RESET rvbbit.force_heap_scan")

        assert raw_dirty is False
        assert effective_dirty is True
        assert effective_update is True
        assert markers >= 1
        assert routed_updated_rows == writer_count
        assert heap_updated_rows == writer_count
    finally:
        locker.rollback()
        locker.close()

    result = rvbbit.execute(
        f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
    ).fetchone()[0]
    assert result["status"] == "ok"
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)
    assert (
        rvbbit.execute(
            f"""
            SELECT count(*)::int
            FROM rvbbit.table_dirty_markers
            WHERE table_oid = '{temp_table}'::regclass
            """
        ).fetchone()[0]
        == 0
    )


def test_update_dirty_episode_requires_rebuild_when_overlay_incomplete(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (1, 'old')")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)")
    _remove_one_identity_map_entry(rvbbit, temp_table)

    rvbbit.execute(f"UPDATE {temp_table} SET label = 'new' WHERE id = 1")
    assert _labels(rvbbit, temp_table) == (1, "new")
    assert _dirty_flags(rvbbit, temp_table) == (True, False, True, False, False)
    plan = _plan_text(rvbbit, f"SELECT label FROM {temp_table} WHERE id >= 1")
    assert "Custom Scan" not in plan

    with pytest.raises(Exception) as exc:
        rvbbit.execute(
            f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
        ).fetchone()
    assert "UPDATE/DELETE/TRUNCATE" in str(exc.value)

    assert _labels(rvbbit, temp_table) == (1, "new")

    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, false)")
    assert _labels(rvbbit, temp_table) == (1, "new")
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)


def test_delete_dirty_episode_requires_rebuild_when_overlay_incomplete(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (1, 'keep'), (2, 'delete_me')")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)")
    _remove_one_identity_map_entry(rvbbit, temp_table)

    rvbbit.execute(f"DELETE FROM {temp_table} WHERE id = 2")
    assert _labels(rvbbit, temp_table) == (1, "keep")
    assert _dirty_flags(rvbbit, temp_table) == (True, False, False, True, False)

    with pytest.raises(Exception) as exc:
        rvbbit.execute(
            f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
        ).fetchone()
    assert "UPDATE/DELETE/TRUNCATE" in str(exc.value)

    assert _labels(rvbbit, temp_table) == (1, "keep")

    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, false)")
    assert _labels(rvbbit, temp_table) == (1, "keep")
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)


def test_insert_only_dirty_episode_still_delta_refreshes(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (1, 'one')")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)")

    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (2, 'two')")
    assert _labels(rvbbit, temp_table) == (2, "one,two")
    assert _dirty_flags(rvbbit, temp_table) == (True, True, False, False, False)
    plan = _plan_text(
        rvbbit,
        f"SELECT label FROM {temp_table} WHERE id >= 1 ORDER BY label",
        analyze=True,
    )
    assert "Custom Scan (RvbbitParquetScan)" in plan
    assert "Heap Tail: true" in plan
    assert "Heap Tail Rows: 1" in plan

    result = rvbbit.execute(
        f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
    ).fetchone()[0]
    assert result["status"] == "ok"
    assert result["rows_written"] == 1

    assert _labels(rvbbit, temp_table) == (2, "one,two")
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)


@pytest.mark.parametrize("keyed", [True, False])
def test_update_or_delete_that_changes_no_rows_leaves_the_table_clean(rvbbit, temp_table, keyed):
    """0490: an incremental ETL's empty DELETE/UPDATE must not force a refresh
    (for a keyless table, a full rebuild), and real changes must still mark."""
    if not rvbbit.execute(
        "SELECT position('0490: a statement that changed no rows' IN "
        "pg_get_functiondef('rvbbit.mark_shadow_heap_dirty()'::regprocedure)) > 0"
    ).fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0490")
    key = " PRIMARY KEY" if keyed else ""
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int{key}, label text)")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'r' || g FROM generate_series(1, 50) g")
    rvbbit.execute(f"SELECT rvbbit.enable_table('{temp_table}'::regclass, true)")
    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, true)")
    assert _dirty_flags(rvbbit, temp_table)[0] is False

    rvbbit.execute(f"UPDATE {temp_table} SET label = 'x' WHERE id < 0")
    rvbbit.execute(f"DELETE FROM {temp_table} WHERE id < 0")
    assert _dirty_flags(rvbbit, temp_table) == (False, False, False, False, False)

    rvbbit.execute(f"UPDATE {temp_table} SET label = 'changed' WHERE id = 7")
    assert _dirty_flags(rvbbit, temp_table)[0:3:2] == (True, True)
    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, true)")
    rvbbit.execute(f"DELETE FROM {temp_table} WHERE id = 8")
    assert _dirty_flags(rvbbit, temp_table)[3] is True
    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, true)")
    assert _labels(rvbbit, temp_table)[0] == 49
    assert "changed" in _labels(rvbbit, temp_table)[1]


def test_trigger_reads_its_transition_table_in_a_new_session(rvbbit, temp_table):
    """pg_rvbbit's query rewriter re-parsed SQL without a trigger's transition
    tables, so the first such query in a session failed ("relation does not
    exist"). A fresh connection's first statement must see them."""
    log = f"{temp_table}_log"
    fn = f"{temp_table}_fn"
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY)")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT generate_series(1, 5)")
    rvbbit.execute(f"CREATE TABLE {log} (n bigint)")
    try:
        rvbbit.execute(
            f"""
            CREATE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE n bigint;
            BEGIN
                EXECUTE 'SELECT count(*) FROM gone' INTO n;
                INSERT INTO {log} VALUES (n);
                RETURN NULL;
            END $$
            """
        )
        rvbbit.execute(
            f"CREATE TRIGGER {fn} AFTER DELETE ON {temp_table} "
            f"REFERENCING OLD TABLE AS gone FOR EACH STATEMENT EXECUTE FUNCTION {fn}()"
        )
        with psycopg.connect(RVBBIT_DSN, autocommit=True) as fresh:
            fresh.execute(f"DELETE FROM {temp_table} WHERE id <= 2")
        assert rvbbit.execute(f"SELECT n FROM {log}").fetchall() == [(2,)]
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {log}")
        rvbbit.execute(f"DROP TABLE IF EXISTS {temp_table}")
        rvbbit.execute(f"DROP FUNCTION IF EXISTS {fn}()")


def test_cached_plan_reads_the_heap_after_a_rewrite_it_cannot_overlay(rvbbit, temp_table):
    """A plan made while the table was clean, reused after another session
    rewrote it (DELETE all + INSERT on a keyless table: no overlay), used to
    fail with "latest accelerated plan is stale". It now reads the heap."""
    fn = f"{temp_table}_read"
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, label text)")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v1' FROM generate_series(1, 500) g")
    rvbbit.execute(f"ANALYZE {temp_table}")
    rvbbit.execute(f"SELECT rvbbit.enable_table('{temp_table}'::regclass, true)")
    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, true)")
    rvbbit.execute(
        f"""CREATE FUNCTION {fn}() RETURNS text LANGUAGE plpgsql AS $$
            DECLARE n bigint; labels text;
            BEGIN
                SELECT count(*), string_agg(DISTINCT label, ',') INTO n, labels
                  FROM {temp_table} WHERE id > 0;
                RETURN n || ':' || labels;
            END $$"""
    )
    try:
        assert "RvbbitParquetScan" in _plan_text(rvbbit, f"SELECT count(*) FROM {temp_table} WHERE id > 0")
        with psycopg.connect(RVBBIT_DSN, autocommit=True) as reader:
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "500:v1"  # plan cached while clean
            with rvbbit.transaction():
                rvbbit.execute(f"DELETE FROM {temp_table}")
                rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v2' FROM generate_series(1, 300) g")
            assert _dirty_flags(rvbbit, temp_table)[0] is True
            assert rvbbit.execute(
                f"SELECT rvbbit.accel_overlay_ready('{temp_table}'::regclass)"
            ).fetchone()[0] is False
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "300:v2"
    finally:
        rvbbit.execute(f"DROP FUNCTION IF EXISTS {fn}()")


def test_cached_plan_reads_the_heap_after_its_accelerator_is_retired(rvbbit, temp_table):
    """retire_acceleration removes the copy but changes nothing about the table,
    so a cached plan still runs the custom scan. It used to find no row groups
    and return no rows; after a later INSERT it failed with "dirty table has no
    acceleration watermark". It now reads the heap."""
    if not rvbbit.execute("SELECT to_regprocedure('rvbbit.scan_epoch_bump_on_commit()') IS NOT NULL").fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0485")
    fn = f"{temp_table}_read"
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text)")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v1' FROM generate_series(1, 500) g")
    rvbbit.execute(f"ANALYZE {temp_table}")
    rvbbit.execute(f"SELECT rvbbit.enable_table('{temp_table}'::regclass, true)")
    rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{temp_table}'::regclass, false)")
    rvbbit.execute(
        f"""CREATE FUNCTION {fn}() RETURNS text LANGUAGE plpgsql AS $$
            DECLARE n bigint; labels text;
            BEGIN
                SELECT count(*), string_agg(DISTINCT label, ',' ORDER BY label) INTO n, labels
                  FROM {temp_table} WHERE id > 0;
                RETURN n || ':' || labels;
            END $$"""
    )
    try:
        assert "RvbbitParquetScan" in _plan_text(rvbbit, f"SELECT count(*) FROM {temp_table} WHERE id > 0")
        with psycopg.connect(RVBBIT_DSN, autocommit=True) as reader:
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "500:v1"  # plan cached while built
            retired = rvbbit.execute(f"SELECT rvbbit.retire_acceleration('{temp_table}'::regclass)").fetchone()[0]
            assert retired["status"] == "retired"
            assert rvbbit.execute(
                f"SELECT count(*) FROM rvbbit.row_groups WHERE table_oid = '{temp_table}'::regclass"
            ).fetchone()[0] == 0
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "500:v1"
            # Any rebuild elsewhere moves the scan epoch and flushes the reader's
            # cached row-group paths; the retired copy then has none at all.
            rvbbit.execute("SELECT rvbbit.scan_epoch_bump_on_commit()")
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "500:v1"
            rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v2' FROM generate_series(501, 520) g")
            assert rvbbit.execute(
                f"SELECT shadow_heap_dirty FROM rvbbit.table_dirty_state WHERE table_oid = '{temp_table}'::regclass"
            ).fetchone()[0] is True
            assert reader.execute(f"SELECT {fn}()").fetchone()[0] == "520:v1,v2"
    finally:
        rvbbit.execute(f"DROP FUNCTION IF EXISTS {fn}()")
