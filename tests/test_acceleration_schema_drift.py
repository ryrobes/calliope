"""0332: column-level DDL on an accelerated table invalidates its Parquet.

Reproduces the live-source chaos finding: ALTER TABLE ADD COLUMN on a mirrored
destination left the accelerator 'authoritative and clean' while its Parquet
lacked the column, so every read failed with 'column … not found in parquet
schema'. Column DDL must mark the table dirty (truncate level) so reads use the
heap until the tick rebuilds; reloption changes must not."""
import uuid

import pytest


def _flags(conn, name):
    return conn.execute(
        "SELECT t.shadow_heap_dirty, t.dirty_has_truncate, f.parquet_authoritative "
        "FROM rvbbit.tables t JOIN rvbbit.accel_freshness f ON f.table_oid = t.table_oid "
        f"WHERE t.table_oid = '{name}'::regclass"
    ).fetchone()


def test_column_ddl_marks_accelerated_table_dirty_and_reads_stay_correct(rvbbit):
    if not rvbbit.execute("SELECT to_regprocedure('rvbbit._accel_schema_drifted(oid)') IS NOT NULL").fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0332")
    name = f"public.drift_{uuid.uuid4().hex[:8]}"
    try:
        rvbbit.execute(f"CREATE TABLE {name} (id int PRIMARY KEY, v text) USING rvbbit")
        rvbbit.execute(f"INSERT INTO {name} SELECT g, md5(g::text) FROM generate_series(1, 300) g")
        rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{name}'::regclass, true)")
        assert _flags(rvbbit, name) == (False, False, True)

        # ADD COLUMN → dirty at truncate level, Parquet no longer authoritative, reads keep working.
        rvbbit.execute(f"ALTER TABLE {name} ADD COLUMN extra_1 text")
        assert _flags(rvbbit, name) == (True, True, False)
        rvbbit.execute(f"UPDATE {name} SET extra_1 = 'x' WHERE id <= 7")
        assert rvbbit.execute(f"SELECT count(*) FROM {name} WHERE extra_1 = 'x'").fetchone()[0] == 7
        assert rvbbit.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 300
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.accel_activity_log WHERE action = 'schema_change' "
            f"AND table_oid = '{name}'::regclass::oid"
        ).fetchone()[0] >= 1

        # A rebuild carries the new shape and Parquet is authoritative again — with the column.
        rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{name}'::regclass, true)")
        assert _flags(rvbbit, name) == (False, False, True)
        assert rvbbit.execute(f"SELECT count(*) FROM {name} WHERE extra_1 = 'x'").fetchone()[0] == 7

        # Reloption / constraint DDL does not trip it.
        rvbbit.execute(f"ALTER TABLE {name} SET (autovacuum_vacuum_scale_factor = 0.05)")
        assert _flags(rvbbit, name) == (False, False, True)

        # RENAME and DROP COLUMN do.
        rvbbit.execute(f"ALTER TABLE {name} RENAME COLUMN extra_1 TO extra_2")
        assert _flags(rvbbit, name)[0] is True
        rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{name}'::regclass, true)")
        assert _flags(rvbbit, name) == (False, False, True)
        rvbbit.execute(f"ALTER TABLE {name} DROP COLUMN extra_2")
        assert _flags(rvbbit, name) == (True, True, False)
        assert rvbbit.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 300
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
