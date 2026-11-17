"""The accelerated scan's cost counts the row groups it opens.

Each row group costs a fixed amount to open however few rows it holds, and the
first one is opened and decoded before the first row comes out. Without that,
a table fragmented by delta refreshes looked as cheap as a compact one: on the
hub, `... WHERE company_id = 4 LIMIT 5` over 765 row groups of 32 rows picked
the accelerated scan (173 ms) over the primary-key index (0.04 ms).
"""
import json

ROW_GROUP_COST = 40.0


def _plan(rvbbit, sql):
    return "\n".join(r[0] for r in rvbbit.execute(f"EXPLAIN (COSTS OFF) {sql}").fetchall())


def _scan_cost(rvbbit, sql):
    """(startup, total) cost of the accelerated scan node in `sql`'s plan."""
    plan = rvbbit.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()[0]
    plan = plan if isinstance(plan, list) else json.loads(plan)
    stack = [plan[0]["Plan"]]
    while stack:
        node = stack.pop()
        if node.get("Custom Plan Provider") == "RvbbitParquetScan":
            return node["Startup Cost"], node["Total Cost"]
        stack.extend(node.get("Plans", []))
    raise AssertionError(f"no accelerated scan in {plan}")


def _row_groups(rvbbit, table):
    return rvbbit.execute(
        f"SELECT count(*) FROM rvbbit.row_groups_visible WHERE table_oid = '{table}'::regclass"
    ).fetchone()[0]


def _fragmented(rvbbit, table, *, batches=200, per_batch=25):
    """A retained table built by one refresh per small batch of inserts."""
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    rvbbit.execute(
        f"CREATE TABLE {table} (company_id int, id int, body text, "
        f"PRIMARY KEY (company_id, id)) USING rvbbit"
    )
    for b in range(batches):
        lo = b * per_batch + 1
        rvbbit.execute(
            f"INSERT INTO {table} SELECT g % 300, g, repeat(md5(g::text), 8) "
            f"FROM generate_series({lo}, {lo + per_batch - 1}) g"
        )
        rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{table}'::regclass)")
    rvbbit.execute(f"ANALYZE {table}")
    assert _row_groups(rvbbit, table) >= batches // 2
    return table


def _same_as_parquet(rvbbit, sql):
    planned = rvbbit.execute(sql).fetchall()
    rvbbit.execute("SET rvbbit.index_paths = off")
    try:
        assert "RvbbitParquetScan" in _plan(rvbbit, sql)
        parquet = rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute("RESET rvbbit.index_paths")
    assert sorted(map(str, planned)) == sorted(map(str, parquet)), sql
    return planned


def test_a_fragmented_table_answers_key_lookups_from_its_index(rvbbit, temp_table):
    t = _fragmented(rvbbit, temp_table)
    for sql in (
        f"SELECT * FROM {t} WHERE company_id = 4 LIMIT 5",
        f"SELECT * FROM {t} WHERE company_id = 4",
        f"SELECT company_id, id FROM {t} WHERE company_id BETWEEN 10 AND 12 ORDER BY company_id, id LIMIT 3",
    ):
        plan = _plan(rvbbit, sql)
        assert "Index" in plan and "RvbbitParquetScan" not in plan, (sql, plan)
        _same_as_parquet(rvbbit, sql)
    assert len(rvbbit.execute(f"SELECT * FROM {t} WHERE company_id = 4 LIMIT 5").fetchall()) == 5


def test_a_fragmented_table_still_reads_whole_scans_from_its_accelerated_copy(rvbbit, temp_table):
    t = _fragmented(rvbbit, temp_table)
    sql = f"SELECT count(*), max(length(body)) FROM {t} WHERE length(body) > 200"
    assert "RvbbitParquetScan" in _plan(rvbbit, sql)
    _same_as_parquet(rvbbit, sql)


def test_each_row_group_adds_its_open_cost(rvbbit, temp_table):
    t = _fragmented(rvbbit, temp_table)
    groups = _row_groups(rvbbit, t)
    sql = f"SELECT * FROM {t} WHERE length(body) > 200"
    rvbbit.execute("SET rvbbit.index_paths = off")
    try:
        startup, total = _scan_cost(rvbbit, sql)
        rvbbit.execute("SET rvbbit.row_group_cost = 0")
        free_startup, free_total = _scan_cost(rvbbit, sql)
    finally:
        rvbbit.execute("RESET rvbbit.row_group_cost")
        rvbbit.execute("RESET rvbbit.index_paths")
    assert abs((total - free_total) - ROW_GROUP_COST * groups) < 1.0, (total, free_total, groups)
    # The first row group is paid before the first row: a LIMIT can't skip it.
    assert abs((startup - free_startup) - ROW_GROUP_COST) < 0.01
    assert free_startup > 0
