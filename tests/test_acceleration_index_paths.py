"""A retained heap's indexes compete with the accelerated copy on cost.

A retained heap holds every row (writes land there; the parquet copy follows),
so an index scan on it reads the same latest answer as the custom scan. For a
key lookup, a narrow range or a top-N on an index, that is orders of magnitude
cheaper than reading whole row groups. Full reads still go to parquet: only
index and bitmap heap paths are kept. An accelerated-only table (heap emptied)
and AS OF reads never use the heap.
"""


def _plan(rvbbit, sql):
    return "\n".join(r[0] for r in rvbbit.execute(f"EXPLAIN (COSTS OFF) {sql}").fetchall())


def _uses_index(plan):
    return "Index" in plan and "RvbbitParquetScan" not in plan


def _accelerated(rvbbit, table, *, keep_heap=True, rows=50_000):
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    rvbbit.execute(
        f"CREATE TABLE {table} (id int PRIMARY KEY, ts timestamptz NOT NULL, "
        f"cat int NOT NULL, v text) USING rvbbit"
    )
    rvbbit.execute(f"CREATE INDEX ON {table} (ts)")
    rvbbit.execute(f"CREATE INDEX ON {table} (cat, ts)")
    rvbbit.execute(
        f"INSERT INTO {table} SELECT g, timestamptz '2025-01-01' + g * interval '1 minute', "
        f"g % 50, 'v' || g FROM generate_series(1, {rows}) g"
    )
    if keep_heap:
        status = rvbbit.execute(
            f"SELECT rvbbit.refresh_acceleration('{table}'::regclass, false)"
        ).fetchone()[0]["status"]
        assert status == "ok"
    else:
        rvbbit.execute(f"SELECT rvbbit.compact('{table}'::regclass, false)")
    rvbbit.execute(f"ANALYZE {table}")
    retained = rvbbit.execute(
        f"SELECT shadow_heap_retained FROM rvbbit.table_dirty_state "
        f"WHERE table_oid = '{table}'::regclass"
    ).fetchone()[0]
    assert retained is keep_heap
    return table


def _same_as_parquet(rvbbit, sql):
    """The rows `sql` returns as planned, and with index paths turned off."""
    planned = rvbbit.execute(sql).fetchall()
    rvbbit.execute("SET rvbbit.index_paths = off")
    try:
        assert "RvbbitParquetScan" in _plan(rvbbit, sql)
        parquet = rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute("RESET rvbbit.index_paths")
    assert sorted(map(str, planned)) == sorted(map(str, parquet)), sql
    return planned


SELECTIVE = (
    "SELECT * FROM {t} WHERE id = 777",
    "SELECT * FROM {t} WHERE id IN (5, 500, 5000)",
    "SELECT id FROM {t} WHERE ts >= '2025-01-10 00:00+00' AND ts < '2025-01-10 01:00+00'",
    "SELECT id, v FROM {t} WHERE cat = 7 ORDER BY ts DESC LIMIT 20",
)


def test_selective_reads_use_a_retained_heaps_index(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table)
    for sql in SELECTIVE:
        sql = sql.format(t=t)
        plan = _plan(rvbbit, sql)
        assert _uses_index(plan), f"{sql}\n{plan}"
        assert _same_as_parquet(rvbbit, sql), sql


def test_full_reads_still_use_the_accelerated_copy(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table)
    for sql in (
        f"SELECT max(length(v)) FROM {t}",
        f"SELECT count(*) FROM {t} WHERE v LIKE '%99%'",
        f"SELECT id FROM {t} WHERE ts >= '2025-01-01 00:00+00'",
    ):
        plan = _plan(rvbbit, sql)
        assert "Seq Scan" not in plan, f"{sql}\n{plan}"
        assert "Index" not in plan, f"{sql}\n{plan}"


def test_index_paths_can_be_turned_off(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table)
    rvbbit.execute("SET rvbbit.index_paths = off")
    plan = _plan(rvbbit, f"SELECT * FROM {t} WHERE id = 777")
    assert "RvbbitParquetScan" in plan and "Index" not in plan, plan


def test_an_accelerated_only_heap_never_uses_its_index(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table, keep_heap=False)
    sql = f"SELECT id, v FROM {t} WHERE id = 777"
    plan = _plan(rvbbit, sql)
    assert "RvbbitParquetScan" in plan and "Index" not in plan, plan
    assert rvbbit.execute(sql).fetchall() == [(777, "v777")]


def test_writes_before_a_refresh_are_found_through_the_index(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table)
    rvbbit.execute(f"INSERT INTO {t} VALUES (60001, now(), 3, 'fresh')")
    rvbbit.execute(f"UPDATE {t} SET v = 'changed' WHERE id = 10")
    rvbbit.execute(f"DELETE FROM {t} WHERE id = 20")
    assert rvbbit.execute(
        f"SELECT shadow_heap_dirty FROM rvbbit.table_dirty_state "
        f"WHERE table_oid = '{t}'::regclass"
    ).fetchone()[0]
    for sql, expected in (
        (f"SELECT v FROM {t} WHERE id = 60001", [("fresh",)]),
        (f"SELECT v FROM {t} WHERE id = 10", [("changed",)]),
        (f"SELECT v FROM {t} WHERE id = 20", []),
    ):
        plan = _plan(rvbbit, sql)
        assert _uses_index(plan), f"{sql}\n{plan}"
        assert rvbbit.execute(sql).fetchall() == expected, sql


def test_join_lookups_use_a_parameterized_index(rvbbit, temp_table):
    t = _accelerated(rvbbit, temp_table)
    rvbbit.execute("CREATE TEMP TABLE wanted (id int)")
    rvbbit.execute("INSERT INTO wanted VALUES (3), (300), (30000)")
    rvbbit.execute("ANALYZE wanted")
    sql = f"SELECT w.id, x.v FROM wanted w JOIN {t} x ON x.id = w.id"
    plan = _plan(rvbbit, sql)
    assert "Nested Loop" in plan and "Index Scan" in plan, plan
    assert sorted(rvbbit.execute(sql).fetchall()) == [(3, "v3"), (300, "v300"), (30000, "v30000")]


def test_as_of_reads_never_use_the_heap(rvbbit, temp_table):
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(
        f"SELECT rvbbit.set_acceleration_storage_policy('{temp_table}'::regclass, "
        f"history_policy => 'retained')"
    )
    load = (
        f"SELECT generation FROM rvbbit.snapshot_load('{temp_table}'::regclass, %s)"
    )
    gen1 = rvbbit.execute(
        load, ("SELECT * FROM (VALUES (1, 'old one'), (2, 'old two')) AS src(id, label)",)
    ).fetchone()[0]
    rvbbit.execute(
        load, ("SELECT g, 'new ' || g FROM generate_series(1, 50000) g",)
    ).fetchone()
    rvbbit.execute(f"ANALYZE {temp_table}")
    sql = f"SELECT label FROM {temp_table} WHERE id = 1"
    assert rvbbit.execute(sql).fetchall() == [("new 1",)]
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute(f"SET LOCAL rvbbit.as_of_generation = '{gen1}'")
        plan = _plan(rvbbit, sql)
        old = rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute("COMMIT")
    assert "Index" not in plan, plan
    assert old == [("old one",)]
