"""An accelerated-only table takes no writes.

The legacy rvbbit.compact(rel, keep_heap => false) moves a table's rows into its
accelerated copy and empties the heap. Nothing records a write after that, and
every reader treats a non-empty heap that was not retained as the whole table,
so one INSERT made reads return only the new rows. Writes are refused instead,
and the empty heap can't be made the table's source again (refresh, keep-heap
compact, disable); reads keep returning every row.
"""
import psycopg
import pytest


ROWS = 5_000


def _accelerated_only(rvbbit, table):
    rvbbit.execute(f"CREATE TABLE {table} (id int PRIMARY KEY, v text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {table} SELECT g, 'v' || g FROM generate_series(1, {ROWS}) g")
    rvbbit.execute(f"SELECT rvbbit.compact('{table}'::regclass, false)")
    assert rvbbit.execute(f"SELECT rvbbit.is_accelerated_only('{table}'::regclass)").fetchone()[0]
    return table


def _reads_every_row(rvbbit, table, rows=ROWS):
    assert rvbbit.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == rows
    assert rvbbit.execute(f"SELECT v FROM {table} WHERE id = 777").fetchall() == [("v777",)]
    assert rvbbit.execute(f"SELECT count(*) FROM {table} WHERE id < 100").fetchone()[0] == 99


def _retained(rvbbit, table):
    return rvbbit.execute(
        f"SELECT shadow_heap_retained FROM rvbbit.tables WHERE table_oid = '{table}'::regclass"
    ).fetchone()[0]


@pytest.mark.parametrize(
    "write",
    (
        "INSERT INTO {t} VALUES ({n}, 'new')",
        "INSERT INTO {t} VALUES (1, 'x') ON CONFLICT (id) DO NOTHING",
        "UPDATE {t} SET v = 'changed' WHERE id = 10",
        "DELETE FROM {t} WHERE id = 20",
        "TRUNCATE {t}",
        "MERGE INTO {t} x USING (VALUES (1)) s(id) ON x.id = s.id "
        "WHEN MATCHED THEN UPDATE SET v = 'merged'",
    ),
)
def test_writes_are_refused_and_reads_keep_every_row(rvbbit, temp_table, write):
    t = _accelerated_only(rvbbit, temp_table)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="accelerated-only"):
        rvbbit.execute(write.format(t=t, n=ROWS + 1))
    _reads_every_row(rvbbit, t)


def test_copy_is_refused(rvbbit, temp_table):
    t = _accelerated_only(rvbbit, temp_table)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="accelerated-only"):
        with rvbbit.cursor().copy(f"COPY {t} FROM STDIN") as copy:
            copy.write_row((ROWS + 1, "copied"))
    _reads_every_row(rvbbit, t)


def test_replication_apply_mode_is_refused_too(rvbbit, temp_table):
    t = _accelerated_only(rvbbit, temp_table)
    rvbbit.execute("SET session_replication_role = replica")
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 1}, 'new')")
    finally:
        rvbbit.execute("RESET session_replication_role")
    _reads_every_row(rvbbit, t)


@pytest.mark.parametrize(
    "operation",
    (
        "SELECT rvbbit.refresh_acceleration('{t}'::regclass, false)",
        "SELECT rvbbit.refresh_acceleration('{t}'::regclass)",
        "SELECT * FROM rvbbit.compact('{t}'::regclass, true)",
        "SELECT rvbbit.disable_table('{t}'::regclass, false)",
        "SELECT rvbbit.disable_table('{t}'::regclass)",
        "SELECT rvbbit.rebuild_acceleration('{t}'::regclass)",
    ),
)
def test_the_empty_heap_cannot_become_its_source(rvbbit, temp_table, operation):
    t = _accelerated_only(rvbbit, temp_table)
    with pytest.raises(psycopg.Error, match="accelerated-only|non-authoritative"):
        rvbbit.execute(operation.format(t=t))
    assert _retained(rvbbit, t) is False
    _reads_every_row(rvbbit, t)


def test_compacting_it_again_keeps_it_guarded(rvbbit, temp_table):
    t = _accelerated_only(rvbbit, temp_table)
    rvbbit.execute(f"SELECT rvbbit.compact('{t}'::regclass, false)")
    assert rvbbit.execute(f"SELECT rvbbit.is_accelerated_only('{t}'::regclass)").fetchone()[0]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 1}, 'new')")
    _reads_every_row(rvbbit, t)


def test_copying_it_out_gives_a_writable_table(rvbbit, temp_table):
    t = _accelerated_only(rvbbit, temp_table)
    copy = f"{t}_copy"
    rvbbit.execute(f"CREATE TABLE {copy} AS SELECT * FROM {t}")
    try:
        rvbbit.execute(f"INSERT INTO {copy} VALUES ({ROWS + 1}, 'new')")
        assert rvbbit.execute(f"SELECT count(*) FROM {copy}").fetchone()[0] == ROWS + 1
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {copy}")


def test_writes_made_before_the_guard_fold_in_with_another_compact(rvbbit, temp_table):
    # A table compacted this way before 0500 had no guard; inserts after it
    # were all its reads returned. Compacting again moves them into the copy.
    t = _accelerated_only(rvbbit, temp_table)
    rvbbit.execute(f"DROP TRIGGER rvbbit_accelerated_only ON {t}")
    rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 1}, 'late')")
    assert rvbbit.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 1
    rvbbit.execute(f"SELECT rvbbit.compact('{t}'::regclass, false)")
    _reads_every_row(rvbbit, t, rows=ROWS + 1)
    assert rvbbit.execute(f"SELECT v FROM {t} WHERE id = {ROWS + 1}").fetchall() == [("late",)]
    assert rvbbit.execute(f"SELECT rvbbit.is_accelerated_only('{t}'::regclass)").fetchone()[0]


def test_tables_that_keep_their_heap_still_take_writes(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, v text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v' || g FROM generate_series(1, {ROWS}) g")
    rvbbit.execute(f"SELECT rvbbit.compact('{temp_table}'::regclass)")
    assert not rvbbit.execute(
        f"SELECT rvbbit.is_accelerated_only('{temp_table}'::regclass)"
    ).fetchone()[0]
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES ({ROWS + 1}, 'new')")
    rvbbit.execute(f"DELETE FROM {temp_table} WHERE id = 20")
    assert rvbbit.execute(f"SELECT count(*) FROM {temp_table}").fetchone()[0] == ROWS
    assert rvbbit.execute(f"SELECT rvbbit.disable_table('{temp_table}'::regclass, false)").fetchone()
