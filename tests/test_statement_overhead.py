"""Statements rvbbit can't speed up don't pay for it.

The rewriter and the router inspected every SELECT: a point lookup on a plain
table ran dozens of queries over rvbbit's catalog, plus an EXPLAIN of itself, to
learn that nothing applied (about 8 ms a statement, 100 times the lookup). A
statement that reads no accelerated table and calls no extension function now
skips them. Planning reads an accelerated table's state once, not once per
question the planner hooks ask.
"""
import psycopg
import pytest


ROWS = 5_000


def _rvbbit_catalog_scans(conn):
    """Scans of rvbbit's catalog tables and indexes so far in this transaction."""
    return conn.execute(
        "SELECT coalesce(sum(pg_stat_get_xact_numscans(c.oid)), 0) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'rvbbit' AND c.relkind IN ('r', 'i', 'm')"
    ).fetchone()[0]


@pytest.fixture
def plain_table(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, v text)")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v' || g FROM generate_series(1, {ROWS}) g")
    return temp_table


@pytest.fixture
def accelerated_table(rvbbit, temp_table):
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, v text) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, 'v' || g FROM generate_series(1, {ROWS}) g")
    rvbbit.execute(f"SELECT rvbbit.compact('{temp_table}'::regclass)")
    return temp_table


def test_plain_table_reads_leave_the_rvbbit_catalog_alone(rvbbit, plain_table):
    # The first read of a table learns (once per backend) that it isn't accelerated.
    rvbbit.execute(f"SELECT v FROM {plain_table} WHERE id = 1").fetchall()
    with rvbbit.transaction():
        before = _rvbbit_catalog_scans(rvbbit)
        for i in range(2, 22):
            # Literals, not parameters: every statement is new text.
            assert rvbbit.execute(f"SELECT v FROM {plain_table} WHERE id = {i}").fetchall() == [(f"v{i}",)]
            rvbbit.execute(f"SELECT count(*) FROM {plain_table} WHERE id < {i}").fetchone()
        assert _rvbbit_catalog_scans(rvbbit) == before


def test_accelerated_reads_are_still_rewritten(rvbbit, accelerated_table):
    t = accelerated_table
    # count(*) is answered from row-group metadata: no scan node at all.
    plan = "\n".join(r[0] for r in rvbbit.execute(f"EXPLAIN SELECT count(*) FROM {t}"))
    assert "Scan" not in plan, plan
    assert rvbbit.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == ROWS


def test_a_view_over_an_accelerated_table_still_reads_the_accelerated_copy(rvbbit, accelerated_table):
    t = accelerated_table
    view = f"{t}_view"
    rvbbit.execute(f"CREATE VIEW {view} AS SELECT id, v FROM {t}")
    try:
        plan = "\n".join(r[0] for r in rvbbit.execute(f"EXPLAIN SELECT v FROM {view} WHERE v LIKE 'v1%'"))
        assert "RvbbitParquetScan" in plan, plan
        assert rvbbit.execute(f"SELECT count(*) FROM {view} WHERE v LIKE 'v1%'").fetchone()[0] == 1111
    finally:
        rvbbit.execute(f"DROP VIEW IF EXISTS {view}")


def test_planning_sees_a_write_made_earlier_in_the_transaction(rvbbit, accelerated_table):
    # Each planning reads the table's state afresh: an insert earlier in the
    # same transaction makes the next read add the heap tail.
    t = accelerated_table
    with rvbbit.transaction():
        assert rvbbit.execute(f"SELECT count(*) FROM {t} WHERE v LIKE 'v%'").fetchone()[0] == ROWS
        rvbbit.execute(f"INSERT INTO {t} VALUES ({ROWS + 1}, 'v-new')")
        assert rvbbit.execute(f"SELECT count(*) FROM {t} WHERE v LIKE 'v%'").fetchone()[0] == ROWS + 1
        assert rvbbit.execute(f"SELECT v FROM {t} WHERE id = {ROWS + 1}").fetchall() == [("v-new",)]
