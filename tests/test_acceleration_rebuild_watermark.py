"""A rebuild's watermark stops below the transactions open at its snapshot.

rebuild_acceleration recorded watermark = pg_snapshot_xmax - 1, and the heap
tail and delta refreshes read only rows whose xmin is above it. A transaction
open across the rebuild that wrote the table afterwards (its earlier statements
had given it an xid) fell below the watermark: its new row versions were in
neither the accelerated copy nor the tail. On the hub, rwx_etl.run_log read
11776 of its 11777 rows that way. The watermark is now pg_snapshot_xmin - 1,
and acceleration_state.covered_snapshot keeps the committed transactions above
it whose rows the rebuild already wrote, so they aren't read twice.
"""
import psycopg
import pytest

from conftest import RVBBIT_DSN

ROWS = 2_000


def _plan(conn, sql):
    return "\n".join(r[0] for r in conn.execute(f"EXPLAIN (COSTS OFF) {sql}").fetchall())


def _rows(conn, table, *, heap):
    """Every row, read from the heap or through the accelerated scan."""
    sql = f"SELECT id, v FROM {table} ORDER BY id"
    if heap:
        conn.execute("SET rvbbit.force_heap_scan = on")
    else:
        conn.execute("SET rvbbit.index_paths = off")
        plan = _plan(conn, sql)
        assert "RvbbitParquetScan" in plan and "Heap Only" not in plan, plan
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.execute("RESET rvbbit.force_heap_scan")
        conn.execute("RESET rvbbit.index_paths")


def _accelerated_reads_match_the_heap(conn, table):
    accelerated = _rows(conn, table, heap=False)
    assert accelerated == _rows(conn, table, heap=True)
    return accelerated


def _covered_snapshot(conn, table):
    return conn.execute(
        f"SELECT covered_snapshot::text FROM rvbbit.acceleration_state "
        f"WHERE table_oid = '{table}'::regclass"
    ).fetchone()[0]


@pytest.fixture
def tables(rvbbit, temp_table):
    t, side = temp_table, f"{temp_table}_side"
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    # Row identity lets reads overlay updates; without it a table with updates
    # since its refresh is read from the heap alone, and the bug never shows.
    rvbbit.execute("SET rvbbit.accel_identity_map = 'on'")
    rvbbit.execute(f"CREATE TABLE {t} (id int PRIMARY KEY, v text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {t} SELECT g, 'v' || g FROM generate_series(1, {ROWS}) g")
    assert rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{t}'::regclass, false)").fetchone()[0]["status"] == "ok"
    rvbbit.execute(f"CREATE TABLE {side} (n int)")
    yield t, side
    rvbbit.execute(f"DROP TABLE IF EXISTS {side}")


@pytest.fixture
def other():
    """A second session, in a transaction. Ask for it after `tables`, so it
    closes (and rolls back) before their teardown drops what it holds locks on."""
    with psycopg.connect(RVBBIT_DSN) as conn:
        conn.execute("SET rvbbit.accel_identity_map = 'on'")
        conn.commit()
        yield conn


def test_a_rebuild_records_its_snapshot_only_when_something_was_in_flight(rvbbit, tables):
    t, _ = tables
    assert rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{t}'::regclass, false)").fetchone()[0]["status"] == "ok"
    # Usually nothing else is running (a background job may be): with nothing
    # in progress the watermark alone says what the copy holds.
    snapshot = _covered_snapshot(rvbbit, t)
    assert snapshot is None or snapshot.split(":")[2] != "", snapshot
    assert len(_accelerated_reads_match_the_heap(rvbbit, t)) == ROWS


def test_writes_from_a_transaction_open_across_the_rebuild_are_read(rvbbit, tables, other):
    t, side = tables
    other.execute(f"INSERT INTO {side} VALUES (1)")  # takes its xid now
    # A newer transaction commits, as on any busy server: the rebuild's snapshot
    # xmax (one past the latest completed xid) moves above the open one.
    rvbbit.execute(f"INSERT INTO {side} VALUES (2)")
    assert rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{t}'::regclass, false)").fetchone()[0]["status"] == "ok"
    other.execute(f"UPDATE {t} SET v = 'updated' WHERE id = 5")
    other.execute(f"INSERT INTO {t} VALUES ({ROWS + 1}, 'late')")
    other.commit()

    assert rvbbit.execute(f"SELECT rvbbit.accel_overlay_ready('{t}'::regclass)").fetchone()[0]
    rows = _accelerated_reads_match_the_heap(rvbbit, t)
    assert len(rows) == ROWS + 1
    assert (5, "updated") in rows and (ROWS + 1, "late") in rows

    assert rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{t}'::regclass, false)").fetchone()[0]["status"] == "ok"
    assert len(_accelerated_reads_match_the_heap(rvbbit, t)) == ROWS + 1
    assert _covered_snapshot(rvbbit, t) is None


@pytest.mark.parametrize("variants", (False, True), ids=("canonical", "with_vortex"))
def test_rows_committed_above_the_watermark_are_not_read_twice(rvbbit, tables, other, variants):
    t, side = tables
    if variants:
        rvbbit.execute("RESET rvbbit.compact_vortex_layout")
    v = "true" if variants else "false"
    other.execute(f"INSERT INTO {side} VALUES (1)")  # an older transaction holds the watermark down
    rvbbit.execute(f"INSERT INTO {t} SELECT g, 'new' || g FROM generate_series({ROWS + 1}, {ROWS + 50}) g")
    assert rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{t}'::regclass, {v})").fetchone()[0]["status"] == "ok"
    assert _covered_snapshot(rvbbit, t) is not None

    # Dirty the table so reads add the heap tail, which holds the 50 rows too.
    rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 51}, 'tail')")
    assert len(_accelerated_reads_match_the_heap(rvbbit, t)) == ROWS + 51

    # A refresh while the older transaction is open can't move the watermark past it.
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{t}'::regclass, {v})")
    assert len(_accelerated_reads_match_the_heap(rvbbit, t)) == ROWS + 51
    assert _covered_snapshot(rvbbit, t) is not None

    other.commit()
    rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 52}, 'after')")
    assert rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{t}'::regclass, {v})").fetchone()[0]["status"] == "ok"
    assert len(_accelerated_reads_match_the_heap(rvbbit, t)) == ROWS + 52
    assert _covered_snapshot(rvbbit, t) is None
    exported = rvbbit.execute(
        f"SELECT coalesce(sum(n_rows), 0) FROM rvbbit.row_groups_visible WHERE table_oid = '{t}'::regclass"
    ).fetchone()[0]
    assert exported == ROWS + 52
    if variants:
        vortex = rvbbit.execute(
            f"SELECT coalesce(sum(n_rows), 0) FROM rvbbit.row_group_variants "
            f"WHERE table_oid = '{t}'::regclass AND layout = 'vortex_scan'"
        ).fetchone()[0]
        assert vortex == ROWS + 52
