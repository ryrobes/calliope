"""Readers get the rewriter's fast paths over accelerated tables they may read whole.

A reader is a role with no rights on schema rvbbit (an app's read-only role).
When it may SELECT every table in its query, with no row level security and no
column-only grants, its query is rewritten exactly as a superuser's: the same
metadata answers and fast-path functions, read from rvbbit's catalog as its
owner. Anything less falls through to the plain accelerated scan. A role is
assumed with SET ROLE, so privileges are checked against it as for a login.
"""
import uuid

import psycopg
import pytest


QUERIES = (
    'SELECT count(*) FROM {t}',
    'SELECT count(DISTINCT "UserID") FROM {t}',
    'SELECT min("RegionID"), max("RegionID"), sum("RegionID") FROM {t}',
    'SELECT "RegionID", count(DISTINCT "UserID") AS u FROM {t} '
    'GROUP BY "RegionID" ORDER BY u DESC LIMIT 2',
    'SELECT "SearchPhrase", count(*) AS c FROM {t} '
    'WHERE "SearchPhrase" <> \'\' GROUP BY "SearchPhrase" ORDER BY c DESC LIMIT 3',
    'SELECT "RegionID", sum("Amount") AS s, avg("Amount") AS a, count(*) AS c FROM {t} '
    'GROUP BY "RegionID" ORDER BY "RegionID"',
    'SELECT count(*) FROM {t} WHERE "SearchPhrase" LIKE \'%alp%\'',
)


def _role(rvbbit):
    name = f"reader_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(f"CREATE ROLE {name} NOLOGIN")
    return name


@pytest.fixture
def roles(rvbbit):
    made = []

    def make():
        made.append(_role(rvbbit))
        return made[-1]

    yield make
    rvbbit.execute("RESET ROLE")
    for name in made:
        try:
            rvbbit.execute(f"DROP OWNED BY {name}")
            rvbbit.execute(f"DROP ROLE {name}")
        except psycopg.Error:
            pass


@pytest.fixture
def events(rvbbit, temp_table):
    rvbbit.execute(
        f'''
        CREATE TABLE {temp_table} (
            "RegionID" integer,
            "UserID" bigint,
            "SearchPhrase" text,
            "Amount" double precision
        ) USING rvbbit
        '''
    )
    rvbbit.execute(
        f"""
        INSERT INTO {temp_table} ("RegionID", "UserID", "SearchPhrase", "Amount")
        SELECT r, r * 1000 + (g / 4) % (5 * r),
               CASE WHEN g % 10 < 4 THEN 'alpha' WHEN g % 10 < 7 THEN 'beta'
                    WHEN g % 10 < 9 THEN 'gamma' ELSE '' END,
               (g % 50) * 1.25
        FROM generate_series(1, 400) g, LATERAL (SELECT 1 + g % 4 AS r) region
        """
    )
    rvbbit.execute(f"SELECT rvbbit.compact('{temp_table}'::regclass)")
    return temp_table


def _as(rvbbit, role, sql):
    rvbbit.execute(f"SET ROLE {role}")
    try:
        return rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute("RESET ROLE")


def _plan(rvbbit, role, sql):
    sql = f"EXPLAIN (COSTS OFF) {sql}"
    rows = _as(rvbbit, role, sql) if role else rvbbit.execute(sql).fetchall()
    return "\n".join(row[0] for row in rows)


def _heap_rows(rvbbit, sql):
    rvbbit.execute("SET rvbbit.force_heap_scan = on")
    try:
        return rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute("RESET rvbbit.force_heap_scan")


@pytest.fixture(autouse=True)
def native_routes_only(rvbbit):
    # External engines run the query text in-process with file access, so they
    # stay with roles that can use rvbbit; compare readers with native plans.
    rvbbit.execute("SET rvbbit.duck_backend = off")
    yield
    rvbbit.execute("RESET rvbbit.duck_backend")


def test_a_reader_has_no_rights_on_rvbbit(rvbbit, events, roles):
    reader = roles()
    rvbbit.execute(f"GRANT SELECT ON {events} TO {reader}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as(rvbbit, reader, "SELECT count(*) FROM rvbbit.tables")


@pytest.mark.parametrize("query", QUERIES)
def test_a_reader_gets_the_superusers_plan_and_the_heaps_rows(rvbbit, events, roles, query):
    reader = roles()
    rvbbit.execute(f"GRANT SELECT ON {events} TO {reader}")
    sql = query.format(t=events)

    superuser_plan = _plan(rvbbit, None, sql)
    assert "RvbbitParquetScan" not in superuser_plan, superuser_plan
    assert _plan(rvbbit, reader, sql) == superuser_plan
    assert _as(rvbbit, reader, sql) == _heap_rows(rvbbit, sql)


def test_a_reader_reads_through_a_read_only_transaction(rvbbit, events, roles):
    reader = roles()
    rvbbit.execute(f"GRANT SELECT ON {events} TO {reader}")
    sql = QUERIES[4].format(t=events)
    expected = _heap_rows(rvbbit, sql)
    rvbbit.execute("BEGIN READ ONLY")
    try:
        rvbbit.execute(f"SET LOCAL ROLE {reader}")
        assert rvbbit.execute(sql).fetchall() == expected
    finally:
        rvbbit.execute("ROLLBACK")


def test_a_role_without_select_is_still_refused(rvbbit, events, roles):
    outsider = roles()
    for query in QUERIES:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(rvbbit, outsider, query.format(t=events))


def test_row_level_security_keeps_the_plain_path(rvbbit, events, roles):
    reader = roles()
    rvbbit.execute(f"GRANT SELECT ON {events} TO {reader}")
    rvbbit.execute(f"ALTER TABLE {events} ENABLE ROW LEVEL SECURITY")
    rvbbit.execute(f'CREATE POLICY region_one ON {events} TO {reader} USING ("RegionID" = 1)')
    expected = _heap_rows(rvbbit, f'SELECT count(*) FROM {events} WHERE "RegionID" = 1')
    for query in QUERIES[:2]:
        sql = query.format(t=events)
        assert "RvbbitParquetScan" in _plan(rvbbit, reader, sql)
    assert _as(rvbbit, reader, QUERIES[0].format(t=events)) == expected


def test_column_only_grants_keep_the_plain_path(rvbbit, events, roles):
    reader = roles()
    rvbbit.execute(f'GRANT SELECT ("RegionID", "UserID") ON {events} TO {reader}')
    for query in QUERIES[:4]:
        sql = query.format(t=events)
        assert "RvbbitParquetScan" in _plan(rvbbit, reader, sql), sql
        assert _as(rvbbit, reader, sql) == _heap_rows(rvbbit, sql)


@pytest.mark.parametrize("query", (QUERIES[0], QUERIES[3]))
def test_a_cached_plan_is_checked_for_the_role_running_it(rvbbit, events, roles, query):
    # A metadata answer keeps the table in its range table and a fast-path
    # function checks the table itself, so a plan prepared by a reader still
    # refuses a role without SELECT that executes it in the same session.
    reader, outsider = roles(), roles()
    rvbbit.execute(f"GRANT SELECT ON {events} TO {reader}")
    sql = query.format(t=events)
    rvbbit.execute(f"SET ROLE {reader}")
    try:
        rvbbit.execute(f"PREPARE reader_plan AS {sql}")
        assert rvbbit.execute("EXECUTE reader_plan").fetchall() == _heap_rows(rvbbit, sql)
        rvbbit.execute(f"SET ROLE {outsider}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rvbbit.execute("EXECUTE reader_plan")
    finally:
        rvbbit.execute("RESET ROLE")
        rvbbit.execute("DEALLOCATE ALL")


def test_a_reader_of_a_view_still_gets_the_right_rows(rvbbit, events, roles):
    # SELECT on the view alone: the table is read with the view owner's rights.
    reader = roles()
    view = f"{events}_v"
    rvbbit.execute(f'CREATE VIEW {view} AS SELECT "RegionID", "SearchPhrase" FROM {events}')
    rvbbit.execute(f"GRANT SELECT ON {view} TO {reader}")
    try:
        sql = f'SELECT "SearchPhrase", count(*) AS c FROM {view} GROUP BY 1 ORDER BY c DESC'
        assert _as(rvbbit, reader, sql) == _heap_rows(rvbbit, sql)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(rvbbit, reader, f"SELECT count(*) FROM {events}")
    finally:
        rvbbit.execute(f"DROP VIEW IF EXISTS {view}")
