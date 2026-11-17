"""Real PostgreSQL lock/transaction regressions for migration 0403.

Run against a disposable rvbbit database with 0403 applied. Concurrent sessions
and an advisory gate exercise the refresh while its expensive load is in flight.
"""
from concurrent.futures import ThreadPoolExecutor
import time
import uuid

import psycopg
import pytest

from conftest import RVBBIT_DSN
from test_cube_refresh_autopilot import _drop_cube


@pytest.fixture
def cube_source(rvbbit, temp_table):
    cube = f"cube_lock_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int, amount int) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} VALUES (1, 10), (2, 20)")
    rvbbit.execute(
        "SELECT rvbbit.set_cube_refresh_policy(%s, p_refresh_variants => 'deferred')",
        (cube,),
    )
    rvbbit.execute(
        "SELECT rvbbit.define_cube(%s, %s, p_category => %s)",
        (cube, f"SELECT id, amount FROM {temp_table}", cube),
    )
    try:
        yield cube, temp_table
    finally:
        _drop_cube(rvbbit, cube)


def _trigger_oid(conn, source):
    return conn.execute(
        "SELECT oid FROM pg_trigger WHERE tgrelid = %s::regclass "
        "AND tgname = 'rvbbit_cube_source_write'",
        (source,),
    ).fetchone()[0]


def _sweep(conn, cube):
    conn.execute(
        "CALL rvbbit.refresh_all_cubes(p_category => %s, "
        "p_force => true, p_sleep_seconds => 0)", (cube,),
    )


def _refresh_state(conn, cube):
    return conn.execute(
        "SELECT refreshed_at, last_rows, last_error FROM rvbbit.cube_control "
        "WHERE cube_name = %s", (cube,),
    ).fetchone()


def test_refresh_leaves_source_readers_writers_and_metadata_unblocked(rvbbit, cube_source):
    cube, source = cube_source
    trigger_oid = _trigger_oid(rvbbit, source)
    with psycopg.connect(RVBBIT_DSN, autocommit=True) as other:
        other.execute("SET statement_timeout = '2s'")
        with rvbbit.transaction():
            assert rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,)).fetchone()[0] == 2
            # Refresh has returned, but ALL its transaction locks are still held.
            assert _trigger_oid(rvbbit, source) == trigger_oid
            assert other.execute(f"SELECT count(*) FROM {source}").fetchone()[0] == 2
            assert other.execute("SELECT pg_total_relation_size(%s::regclass)", (source,)).fetchone()[0] > 0
            other.execute(f"INSERT INTO {source} VALUES (3, 30)")
            other.execute(f"UPDATE {source} SET amount = 11 WHERE id = 1")
            other.execute(f"DELETE FROM {source} WHERE id = 2")
        assert rvbbit.execute(
            "SELECT source_dirty FROM rvbbit.cube_refresh_status WHERE name = %s", (cube,),
        ).fetchone()[0] is True


@pytest.mark.parametrize("enabled", ["ENABLE", "ENABLE ALWAYS"])
def test_valid_tracking_needs_no_ddl_even_with_active_readers(rvbbit, cube_source, enabled):
    _, source = cube_source
    rvbbit.execute(f"ALTER TABLE {source} {enabled} TRIGGER rvbbit_cube_source_write")
    oid = _trigger_oid(rvbbit, source)
    with psycopg.connect(RVBBIT_DSN) as reader:
        reader.execute(f"LOCK TABLE {source} IN ACCESS SHARE MODE")
        rvbbit.execute("SET statement_timeout = '2s'")
        try:
            assert rvbbit.execute(
                "SELECT rvbbit._install_cube_source_dirty_tracking(%s)",
                (f"SELECT id, amount FROM {source}",),
            ).fetchone()[0] == 0
            assert _trigger_oid(rvbbit, source) == oid
        finally:
            rvbbit.execute("RESET statement_timeout")


@pytest.mark.parametrize("damage", ["missing", "disabled", "replica", "events", "row", "when"])
def test_tracking_repairs_invalid_triggers_and_is_idempotent(rvbbit, cube_source, damage):
    cube, source = cube_source
    query = f"SELECT id, amount FROM {source}"
    if damage in ("disabled", "replica"):
        mode = "DISABLE" if damage == "disabled" else "ENABLE REPLICA"
        rvbbit.execute(f"ALTER TABLE {source} {mode} TRIGGER rvbbit_cube_source_write")
    else:
        rvbbit.execute(f"DROP TRIGGER rvbbit_cube_source_write ON {source}")
        if damage != "missing":
            events = "INSERT" if damage == "events" else "INSERT OR UPDATE OR DELETE OR TRUNCATE"
            level = "STATEMENT"
            condition = ""
            if damage == "row":
                events, level = "INSERT OR UPDATE OR DELETE", "ROW"
            if damage == "when":
                condition = "WHEN (false)"
            rvbbit.execute(
                f"CREATE TRIGGER rvbbit_cube_source_write AFTER {events} ON {source} "
                f"FOR EACH {level} {condition} EXECUTE FUNCTION rvbbit.mark_cube_source_write()"
            )
    before = _refresh_state(rvbbit, cube)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="tracking needs repair"):
        rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert _refresh_state(rvbbit, cube) == before
    assert rvbbit.execute("SELECT rvbbit._install_cube_source_dirty_tracking(%s)", (query,)).fetchone()[0] == 1
    oid = _trigger_oid(rvbbit, source)
    assert rvbbit.execute("SELECT rvbbit._install_cube_source_dirty_tracking(%s)", (query,)).fetchone()[0] == 0
    assert _trigger_oid(rvbbit, source) == oid
    rvbbit.execute(f"INSERT INTO {source} VALUES (3, 30)")
    assert rvbbit.execute(
        "SELECT source_dirty FROM rvbbit.cube_refresh_status WHERE name = %s", (cube,),
    ).fetchone()[0] is True
    assert rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,)).fetchone()[0] == 3


@pytest.mark.parametrize("busy", ["destination", "advisory", "source_setup"])
def test_sweep_skips_busy_cube_without_error_and_retries(rvbbit, cube_source, busy):
    cube, source = cube_source
    if busy == "source_setup":
        rvbbit.execute(f"DROP TRIGGER rvbbit_cube_source_write ON {source}")
    before = _refresh_state(rvbbit, cube)
    with psycopg.connect(RVBBIT_DSN) as blocker:
        if busy == "advisory":
            blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended('rvbbit.cube:' || %s, 0))", (cube,))
        else:
            rel = source if busy == "source_setup" else f"cubes.{cube}"
            mode = "ACCESS SHARE" if busy == "source_setup" else "ROW EXCLUSIVE"
            blocker.execute(f"LOCK TABLE {rel} IN {mode} MODE")
        # A blocking implementation fails with QueryCanceled, rather than
        # passing because the test eventually releases the conflicting lock.
        rvbbit.execute("SET statement_timeout = '2s'")
        try:
            if busy != "source_setup":
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
            _sweep(rvbbit, cube)
            assert _refresh_state(rvbbit, cube) == before
        finally:
            rvbbit.execute("RESET statement_timeout")
    _sweep(rvbbit, cube)
    after = _refresh_state(rvbbit, cube)
    assert after[0] > before[0]
    assert after[1:] == (2, None)


def test_sweep_releases_partial_setup_and_continues_to_other_cubes(rvbbit, cube_source):
    cube, source = cube_source
    other_source = f"test_cube_source_{uuid.uuid4().hex[:8]}"
    other_cube = f"cube_other_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(f"CREATE TABLE {other_source} (id int, amount int) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {other_source} VALUES (3, 30)")
    try:
        rvbbit.execute(
            "SELECT rvbbit.redefine_cube(%s, %s)",
            (cube, f"SELECT * FROM {source} UNION ALL SELECT * FROM {other_source}"),
        )
        rvbbit.execute(
            "SELECT rvbbit.define_cube(%s, %s, p_category => %s)",
            (other_cube, f"SELECT * FROM {source}", cube),
        )
        # The installer orders locks by OID. Lock its second source so the
        # first repair succeeds locally before the second NOWAIT fails.
        ordered = rvbbit.execute(
            "SELECT unnest(ARRAY[%s::regclass, %s::regclass])::oid ORDER BY 1",
            (source, other_source),
        ).fetchall()
        first, second = (row[0] for row in ordered)
        second_name = rvbbit.execute("SELECT %s::oid::regclass::text", (second,)).fetchone()[0]
        for rel in (source, other_source):
            rvbbit.execute(f"DROP TRIGGER rvbbit_cube_source_write ON {rel}")
        before = _refresh_state(rvbbit, cube)
        with psycopg.connect(RVBBIT_DSN) as blocker:
            blocker.execute(f"LOCK TABLE {second_name} IN ACCESS SHARE MODE")
            rvbbit.execute("SET statement_timeout = '2s'")
            try:
                # Isolate the failed setup attempt to inspect its rollback.
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    rvbbit.execute(
                        "SELECT rvbbit._install_cube_source_dirty_tracking(%s)",
                        (f"SELECT * FROM {source} UNION ALL SELECT * FROM {other_source}",),
                    )
                assert rvbbit.execute(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::oid "
                    "AND tgname = 'rvbbit_cube_source_write'", (first,),
                ).fetchone()[0] == 0
                # The sweep still processes the independent single-source cube.
                other_before = _refresh_state(rvbbit, other_cube)
                _sweep(rvbbit, cube)
                assert _refresh_state(rvbbit, cube) == before
                assert _refresh_state(rvbbit, other_cube)[0] > other_before[0]
                # No partial setup DDL lock remains in the sweep backend.
                assert blocker.execute(f"SELECT count(*) FROM {source}").fetchone()[0] == 2
            finally:
                rvbbit.execute("RESET statement_timeout")
    finally:
        _drop_cube(rvbbit, other_cube)
        _drop_cube(rvbbit, cube)
        rvbbit.execute(f"DROP TABLE {other_source} CASCADE")


def test_sweep_commits_source_repair_before_expensive_load(rvbbit, cube_source):
    cube, source = cube_source
    gate = uuid.uuid4().int % (2**62)
    fn = f"test_cube_gate_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(f"""
        CREATE FUNCTION {fn}(n int) RETURNS int LANGUAGE plpgsql AS $fn$
        BEGIN
            IF current_setting('rvbbit.test_cube_pause', true) = 'on' THEN
                PERFORM pg_advisory_xact_lock({gate}::bigint);
            END IF;
            RETURN n;
        END $fn$
    """)
    try:
        rvbbit.execute(
            "SELECT rvbbit.redefine_cube(%s, %s)",
            (cube, f"SELECT {fn}(id) AS id, amount FROM {source}"),
        )
        old_trigger = _trigger_oid(rvbbit, source)
        rvbbit.execute(f"DROP TRIGGER rvbbit_cube_source_write ON {source}")
        with psycopg.connect(RVBBIT_DSN) as blocker, \
                psycopg.connect(RVBBIT_DSN, autocommit=True) as runner, \
                ThreadPoolExecutor(max_workers=1) as pool:
            runner.execute("SET statement_timeout = '15s'")
            runner.execute("SET rvbbit.force_heap_scan = on")
            runner.execute("SET rvbbit.test_cube_pause = 'on'")
            pid = runner.info.backend_pid
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (gate,))
            future = pool.submit(_sweep, runner, cube)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    state = rvbbit.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid = %s", (pid,),
                    ).fetchone()
                    if state and state[0] == "advisory":
                        break
                    if future.done():
                        future.result()
                        pytest.fail("refresh did not reach the load gate")
                    time.sleep(0.02)
                else:
                    pytest.fail("refresh did not reach the load gate")
                # A different session can already see the repaired trigger:
                # its setup committed even though snapshot loading is paused.
                assert _trigger_oid(rvbbit, source) != old_trigger
                locks = rvbbit.execute(
                    "SELECT mode FROM pg_locks WHERE pid = %s AND relation = %s::regclass AND granted",
                    (pid, source),
                ).fetchall()
                assert all(mode == "AccessShareLock" for (mode,) in locks)
                rvbbit.execute("SET statement_timeout = '2s'")
                assert rvbbit.execute(f"SELECT count(*) FROM {source}").fetchone()[0] == 2
                # 0405 also leaves the live destination readable while its
                # replacement source query is still being evaluated.
                assert rvbbit.execute(f"SELECT count(*) FROM cubes.{cube}").fetchone()[0] == 2
                rvbbit.execute("SELECT pg_total_relation_size(%s::regclass)", (source,))
                rvbbit.execute(f"INSERT INTO {source} VALUES (3, 30)")
            finally:
                blocker.rollback()
                rvbbit.execute("RESET statement_timeout")
            future.result(timeout=20)
        assert _refresh_state(rvbbit, cube)[1:] == (2, None)
        # A write during the build must still schedule another refresh.
        assert rvbbit.execute(
            "SELECT source_dirty FROM rvbbit.cube_refresh_status WHERE name = %s", (cube,),
        ).fetchone()[0] is True
    finally:
        _drop_cube(rvbbit, cube)
        rvbbit.execute(f"DROP FUNCTION {fn}(int)")
