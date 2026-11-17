"""0497: application roles with no rights on rvbbit can write and read accelerated tables.

The writes go through rvbbit's dirty triggers, which used to run as the writing
role and failed with "permission denied for table table_dirty_markers". A role
is assumed with SET ROLE, so privileges and SECURITY INVOKER code are checked
against it exactly as for a login of that role.
"""
import uuid

import psycopg
import pytest


TRIGGER_FUNCTIONS = (
    "rvbbit.mark_shadow_heap_dirty()",
    "rvbbit.mark_shadow_heap_dirty_row()",
    "rvbbit.mark_shadow_heap_ctid_tombstone()",
    "rvbbit.mark_cube_source_write()",
)


@pytest.fixture
def app_role(rvbbit):
    """A role with no grants on schema rvbbit, like an application's."""
    name = f"app_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(f"CREATE ROLE {name} NOLOGIN")
    yield name
    rvbbit.execute("RESET ROLE")
    try:
        rvbbit.execute(f"DROP OWNED BY {name}")
        rvbbit.execute(f"DROP ROLE {name}")
    except psycopg.Error:
        pass


def _dirty_flags(rvbbit, table):
    return rvbbit.execute(
        f"""
        SELECT shadow_heap_dirty, dirty_has_insert, dirty_has_update,
               dirty_has_delete, dirty_has_truncate
        FROM rvbbit.tables
        WHERE table_oid = '{table}'::regclass
        """
    ).fetchone()


def _tombstones(rvbbit, table):
    return rvbbit.execute(f"SELECT rvbbit.tombstone_count('{table}'::regclass)").fetchone()[0]


def _as(rvbbit, role, sql):
    rvbbit.execute(f"SET ROLE {role}")
    try:
        return rvbbit.execute(sql)
    finally:
        rvbbit.execute("RESET ROLE")


def _built(rvbbit, table, ddl):
    rvbbit.execute(ddl)
    rvbbit.execute(f"INSERT INTO {table} VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{table}'::regclass, false)")
    assert _dirty_flags(rvbbit, table)[0] is False


def test_dirty_triggers_run_as_the_extension_owner(rvbbit):
    rows = rvbbit.execute(
        """
        SELECT p.oid::regprocedure::text, p.prosecdef, p.proconfig
        FROM pg_proc p
        WHERE p.oid = ANY (%s::regprocedure[])
        """,
        (list(TRIGGER_FUNCTIONS),),
    ).fetchall()
    assert {row[0] for row in rows} == set(TRIGGER_FUNCTIONS)
    for name, definer, config in rows:
        assert definer, f"{name} runs as the writing role"
        assert config == ["search_path=pg_catalog, rvbbit, pg_temp"], name


def test_app_role_writes_a_keyed_accelerated_table(rvbbit, temp_table, app_role):
    _built(rvbbit, temp_table, f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {temp_table} TO {app_role}")

    _as(rvbbit, app_role, f"INSERT INTO {temp_table} VALUES (4, 'four')")
    _as(rvbbit, app_role, f"UPDATE {temp_table} SET label = 'two_v2' WHERE id = 2")
    _as(rvbbit, app_role, f"DELETE FROM {temp_table} WHERE id = 3")

    assert _dirty_flags(rvbbit, temp_table) == (True, True, True, True, False)
    assert _tombstones(rvbbit, temp_table) == 2
    result = rvbbit.execute(
        f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
    ).fetchone()[0]
    assert result["status"] == "ok"
    assert _as(rvbbit, app_role, f"SELECT id, label FROM {temp_table} ORDER BY id").fetchall() == [
        (1, "one"), (2, "two_v2"), (4, "four"),
    ]


def test_app_role_truncates_an_accelerated_table(rvbbit, temp_table, app_role):
    _built(rvbbit, temp_table, f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(f"GRANT SELECT, INSERT, TRUNCATE ON {temp_table} TO {app_role}")

    _as(rvbbit, app_role, f"TRUNCATE {temp_table}")
    _as(rvbbit, app_role, f"INSERT INTO {temp_table} VALUES (9, 'nine')")

    dirty, *_, truncated = _dirty_flags(rvbbit, temp_table)
    assert (dirty, truncated) == (True, True)
    assert _as(rvbbit, app_role, f"SELECT id, label FROM {temp_table}").fetchall() == [(9, "nine")]


def test_app_role_keyless_writes_keep_the_no_change_shortcut(rvbbit, temp_table, app_role):
    # The ctid row trigger hands the statement trigger a set_config flag; both
    # are SECURITY DEFINER with a SET clause now, and the flag must survive it.
    rvbbit.execute("SET rvbbit.accel_identity_map = 'on'")
    _built(rvbbit, temp_table, f"CREATE TABLE {temp_table} (id int, label text) USING rvbbit")
    rvbbit.execute(f"GRANT SELECT, UPDATE ON {temp_table} TO {app_role}")

    _as(rvbbit, app_role, f"UPDATE {temp_table} SET label = 'x' WHERE id = 99")
    assert _dirty_flags(rvbbit, temp_table)[0] is False

    _as(rvbbit, app_role, f"UPDATE {temp_table} SET label = 'two_v2' WHERE id = 2")
    assert _dirty_flags(rvbbit, temp_table)[0:3] == (True, False, True)
    assert _tombstones(rvbbit, temp_table) == 1
    assert _as(rvbbit, app_role, f"SELECT id, label FROM {temp_table} ORDER BY id").fetchall() == [
        (1, "one"), (2, "two_v2"), (3, "three"),
    ]


def test_read_only_role_reads_the_accelerated_copy(rvbbit, temp_table, app_role):
    _built(rvbbit, temp_table, f"CREATE TABLE {temp_table} (id int PRIMARY KEY, label text) USING rvbbit")
    rvbbit.execute(f"GRANT SELECT ON {temp_table} TO {app_role}")

    rvbbit.execute("BEGIN READ ONLY")
    try:
        rvbbit.execute(f"SET LOCAL ROLE {app_role}")
        plan = "\n".join(
            row[0] for row in rvbbit.execute(f"EXPLAIN (COSTS OFF) SELECT * FROM {temp_table}").fetchall()
        )
        assert "RvbbitParquetScan" in plan
        assert rvbbit.execute(f"SELECT count(*) FROM {temp_table}").fetchone()[0] == 3
    finally:
        rvbbit.execute("ROLLBACK")
