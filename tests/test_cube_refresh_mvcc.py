"""Real concurrent PostgreSQL sessions: cube MVCC publication (migration 0405).

Use a disposable database. Gates pause the actual source evaluation or generation
publication, including AFTER the replacement heap and parquet files exist.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import uuid

import psycopg
import pytest

from conftest import RVBBIT_DSN
from test_cube_refresh_lock_isolation import cube_source, _sweep

MIGRATION = Path(__file__).resolve().parents[1] / "crates/pg_rvbbit/sql/migrations/0405_cube_refresh_mvcc_publication.sql"


def rows(conn, cube, heap=False):
    conn.execute("SELECT set_config('rvbbit.force_heap_scan', %s, false)", ("on" if heap else "off",))
    # Each assertion intentionally changes planner-only routing GUCs.
    # Replan so a cached heap plan cannot defeat the AS OF/heap selection.
    return conn.execute(f"SELECT id, amount FROM cubes.{cube} ORDER BY id", prepare=False).fetchall()


def generation(conn, cube):
    return conn.execute(
        "SELECT min_visible_generation FROM rvbbit.tables WHERE table_oid = %s::regclass",
        (f"cubes.{cube}",),
    ).fetchone()[0]


def history(conn, cube, gen):
    conn.execute("SET rvbbit.force_heap_scan = off")
    with conn.transaction():
        conn.execute("SELECT set_config('rvbbit.as_of_generation', %s, true)", (str(gen),))
        return conn.execute(f"SELECT id, amount FROM cubes.{cube} ORDER BY id", prepare=False).fetchall()


def wait_gate(observer, runner, future):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if future.done():
            future.result()
            pytest.fail("refresh returned before the gate")
        state = observer.execute("SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (runner.info.backend_pid,)).fetchone()
        if state and state[0] == "advisory":
            return
        time.sleep(0.02)
    pytest.fail("refresh never reached gate")

@pytest.fixture
def publication_gate(rvbbit, cube_source):
    cube, source = cube_source
    key = uuid.uuid4().int % (2**62)
    fn = f"test_cube_publish_{uuid.uuid4().hex[:8]}"
    oid = rvbbit.execute("SELECT %s::regclass::oid", (f"cubes.{cube}",)).fetchone()[0]
    rvbbit.execute(f"""
        CREATE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $gate$
        BEGIN
            IF NEW.table_oid = {oid} AND current_setting('rvbbit.test_publish_gate', true) = 'on' THEN
                PERFORM pg_advisory_xact_lock({key}::bigint);
            END IF;
            RETURN NEW;
        END $gate$;
        CREATE TRIGGER {fn} BEFORE INSERT ON rvbbit.generations
            FOR EACH ROW EXECUTE FUNCTION {fn}();
    """)
    try:
        yield key
    finally:
        rvbbit.execute(f"DROP TRIGGER {fn} ON rvbbit.generations")
        rvbbit.execute(f"DROP FUNCTION {fn}()")


@pytest.mark.parametrize("variants", ["deferred", "sync"])
def test_readers_keep_complete_old_snapshot_through_export_and_commit(rvbbit, cube_source, publication_gate, variants):
    cube, source = cube_source
    old = [(1, 10), (2, 20)]
    new = [(1, 100), (2, 200), (3, 300)]
    rvbbit.execute("SELECT rvbbit.set_cube_refresh_policy(%s, p_refresh_variants => %s)", (cube, variants))
    gen = generation(rvbbit, cube)
    rvbbit.execute(f"UPDATE {source} SET amount = amount * 10")
    rvbbit.execute(f"INSERT INTO {source} VALUES (3, 300)")
    with psycopg.connect(RVBBIT_DSN) as gate, \
            psycopg.connect(RVBBIT_DSN, autocommit=True) as runner, \
            psycopg.connect(RVBBIT_DSN, autocommit=True) as reader, \
            psycopg.connect(RVBBIT_DSN) as long_reader, \
            ThreadPoolExecutor(max_workers=1) as pool:
        reader.execute("SET statement_timeout = '1500ms'")
        long_reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        long_reader.execute("SET statement_timeout = '1500ms'")
        assert rows(long_reader, cube) == old
        assert rows(reader, cube) == old  # Warm the pooled scan cache.
        runner.execute("SET statement_timeout = '20s'")
        runner.execute("SET rvbbit.test_publish_gate = on")
        gate.execute("SELECT pg_advisory_xact_lock(%s)", (publication_gate,))
        future = pool.submit(runner.execute, "SELECT rvbbit.refresh_cube(%s)", (cube,))
        try:
            wait_gate(rvbbit, runner, future)
            for _ in range(3):
                assert rows(reader, cube) == old
                assert rows(reader, cube, heap=True) == old
            locks = rvbbit.execute(
                "SELECT mode FROM pg_locks WHERE pid=%s AND relation=%s::regclass AND granted",
                (runner.info.backend_pid, f"cubes.{cube}"),
            ).fetchall()
            assert ("AccessExclusiveLock",) not in locks
            assert history(reader, cube, gen) == old
            # Ordinary source ingestion also remains free during cube export.
            rvbbit.execute(f"INSERT INTO {source} VALUES (4, 400)")
        finally:
            gate.rollback()
        assert future.result(timeout=25).fetchone()[0] == 3
        assert rows(reader, cube) == new
        assert rows(reader, cube, heap=True) == new
        assert rows(long_reader, cube, heap=True) == old
        assert rows(long_reader, cube) == old
        assert history(reader, cube, gen) == old
        assert rvbbit.execute("SELECT source_dirty FROM rvbbit.cube_refresh_status WHERE name=%s", (cube,)).fetchone()[0]


def test_cancel_after_export_preserves_heap_history_and_allows_retry(rvbbit, cube_source, publication_gate):
    cube, source = cube_source
    old = rows(rvbbit, cube)
    gen = generation(rvbbit, cube)
    rvbbit.execute(f"UPDATE {source} SET amount=amount+1")
    with psycopg.connect(RVBBIT_DSN) as gate, \
            psycopg.connect(RVBBIT_DSN, autocommit=True) as runner, \
            ThreadPoolExecutor(max_workers=1) as pool:
        runner.execute("SET statement_timeout = '20s'")
        runner.execute("SET rvbbit.test_publish_gate = on")
        gate.execute("SELECT pg_advisory_xact_lock(%s)", (publication_gate,))
        future = pool.submit(runner.execute, "SELECT rvbbit.refresh_cube(%s)", (cube,))
        try:
            wait_gate(rvbbit, runner, future)
            rvbbit.execute("SELECT pg_cancel_backend(%s)", (runner.info.backend_pid,))
            with pytest.raises(psycopg.errors.QueryCanceled):
                future.result(timeout=5)
        finally:
            gate.rollback()
        assert generation(rvbbit, cube) == gen
        assert rows(rvbbit, cube) == old
        assert rows(rvbbit, cube, heap=True) == old
        assert history(rvbbit, cube, gen) == old
        assert runner.execute("SELECT count(*) FROM pg_class WHERE relnamespace=pg_my_temp_schema() AND relname LIKE '_rvbbit_snapshot_%'").fetchone()[0] == 0
        runner.execute("SET rvbbit.test_publish_gate = off")
        runner.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
        assert rows(rvbbit, cube) == [(1, 11), (2, 21)]
        assert history(rvbbit, cube, gen) == old


@pytest.mark.parametrize("primary_key", [False, True])
def test_history_empty_snapshot_and_normal_write_tracking(rvbbit, cube_source, primary_key):
    cube, source = cube_source
    rel = f"cubes.{cube}"
    if primary_key:
        rvbbit.execute(f"ALTER TABLE {rel} ADD PRIMARY KEY (id)")
        rvbbit.execute("SELECT rvbbit.install_shadow_heap_dirty_triggers(%s::regclass)", (rel,))
        rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    old = rows(rvbbit, cube)
    gen = generation(rvbbit, cube)
    oid = rvbbit.execute("SELECT %s::regclass::oid", (rel,)).fetchone()[0]
    rvbbit.execute(f"CREATE INDEX ON {rel}(amount)")
    idx = rvbbit.execute("SELECT indexrelid FROM pg_index WHERE indrelid=%s ORDER BY indexrelid", (oid,)).fetchall()
    rvbbit.execute(f"DELETE FROM {source}")
    assert rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,)).fetchone()[0] == 0
    assert rows(rvbbit, cube) == []
    assert rows(rvbbit, cube, heap=True) == []
    assert history(rvbbit, cube, gen) == old
    assert rvbbit.execute("SELECT count(*) FROM rvbbit.delete_log WHERE table_oid=%s", (oid,)).fetchone()[0] == 0
    assert rvbbit.execute("SELECT indexrelid FROM pg_index WHERE indrelid=%s ORDER BY indexrelid", (oid,)).fetchall() == idx
    rvbbit.execute(f"INSERT INTO {source} VALUES (3, 30), (4, 40)")
    rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert rows(rvbbit, cube) == [(3, 30), (4, 40)]
    rvbbit.execute(f"UPDATE {rel} SET amount=31 WHERE id=3")
    assert rows(rvbbit, cube) == [(3, 31), (4, 40)]
    rvbbit.execute(f"DELETE FROM {rel} WHERE id=4")
    assert rows(rvbbit, cube) == [(3, 31)]
    assert rvbbit.execute("SELECT shadow_heap_dirty FROM rvbbit.table_dirty_state WHERE table_oid=%s", (oid,)).fetchone()[0]
    assert history(rvbbit, cube, gen) == old


def test_trigger_setup_is_idempotent_and_busy_repairs_never_queue(rvbbit, cube_source):
    cube, _ = cube_source
    rel = f"cubes.{cube}"
    before = rvbbit.execute("SELECT oid FROM pg_trigger WHERE tgrelid=%s::regclass ORDER BY oid", (rel,)).fetchall()
    with psycopg.connect(RVBBIT_DSN) as reader:
        reader.execute(f"SELECT * FROM {rel}")
        rvbbit.execute("SET statement_timeout = '1500ms'")
        rvbbit.execute("SELECT rvbbit.install_shadow_heap_dirty_triggers(%s::regclass)", (rel,))
        assert rvbbit.execute("SELECT oid FROM pg_trigger WHERE tgrelid=%s::regclass ORDER BY oid", (rel,)).fetchall() == before
    rvbbit.execute(f"ALTER TABLE {rel} DISABLE TRIGGER rvbbit_shadow_heap_dirty_insert")
    with psycopg.connect(RVBBIT_DSN) as reader:
        reader.execute(f"SELECT * FROM {rel}")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            rvbbit.execute("SELECT rvbbit.install_shadow_heap_dirty_triggers(%s::regclass)", (rel,))
    rvbbit.execute("SELECT rvbbit.install_shadow_heap_dirty_triggers(%s::regclass)", (rel,))
    assert rvbbit.execute("SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)", (rel,)).fetchone()[0]


def test_migration_reapply_keeps_snapshot_and_trigger_identity(rvbbit, cube_source):
    cube, _ = cube_source
    gen = generation(rvbbit, cube)
    old = rows(rvbbit, cube)
    rel = f"cubes.{cube}"
    before = rvbbit.execute("SELECT oid FROM pg_trigger WHERE tgrelid=%s::regclass ORDER BY oid", (rel,)).fetchall()
    rvbbit.execute(MIGRATION.read_text())
    assert generation(rvbbit, cube) == gen
    assert rows(rvbbit, cube) == old
    assert rvbbit.execute("SELECT oid FROM pg_trigger WHERE tgrelid=%s::regclass ORDER BY oid", (rel,)).fetchall() == before


def test_constraint_failure_is_atomic_and_publication_state_is_restored(rvbbit, cube_source):
    cube, source = cube_source
    old = rows(rvbbit, cube)
    gen = generation(rvbbit, cube)
    rvbbit.execute(f"ALTER TABLE cubes.{cube} ADD CONSTRAINT positive_amount CHECK (amount>0)")
    rvbbit.execute(f"UPDATE {source} SET amount=-1")
    with pytest.raises(psycopg.errors.CheckViolation):
        rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert rows(rvbbit, cube) == old
    assert rows(rvbbit, cube, heap=True) == old
    assert generation(rvbbit, cube) == gen
    assert not rvbbit.execute("SELECT nullif(current_setting('rvbbit.snapshot_replace_target', true), '')").fetchone()[0]
    rvbbit.execute(f"UPDATE {source} SET amount=25")
    rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert rows(rvbbit, cube) == [(1, 25), (2, 25)]


def test_current_policy_prunes_only_after_success(rvbbit, cube_source):
    cube, source = cube_source
    rel = f"cubes.{cube}"
    rvbbit.execute("SELECT rvbbit.set_acceleration_storage_policy(%s::regclass, history_policy=>'current')", (rel,))
    rvbbit.execute(f"UPDATE {source} SET amount=77")
    rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert rows(rvbbit, cube) == [(1, 77), (2, 77)]
    assert rvbbit.execute("SELECT count(*) FROM rvbbit.generations WHERE table_oid=%s::regclass", (rel,)).fetchone()[0] == 1
    rvbbit.execute(f"DELETE FROM {source}")
    rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
    assert rows(rvbbit, cube) == []
    assert rvbbit.execute("SELECT n_rows FROM rvbbit.generations WHERE table_oid=%s::regclass", (rel,)).fetchall() == [(0,)]


@pytest.mark.parametrize('damage', ['function', 'events', 'row', 'when', 'old_rows'])
def test_malformed_shadow_trigger_is_repaired(rvbbit, cube_source, damage):
    cube, _ = cube_source
    rel = f"cubes.{cube}"
    trg = 'rvbbit_shadow_heap_dirty_delete'
    rvbbit.execute(f"DROP TRIGGER {trg} ON {rel}")
    function = 'rvbbit.mark_shadow_heap_ctid_tombstone' if damage == 'function' else 'rvbbit.mark_shadow_heap_dirty'
    events = 'UPDATE' if damage == 'events' else 'DELETE'
    kind = 'ROW' if damage in ('row', 'when') else 'STATEMENT'
    when = 'WHEN (false)' if damage == 'when' else ''
    transition = 'REFERENCING OLD TABLE AS unexpected_rows' if damage == 'old_rows' else ''
    rvbbit.execute(f"CREATE TRIGGER {trg} AFTER {events} ON {rel} {transition} FOR EACH {kind} {when} EXECUTE FUNCTION {function}()")
    rvbbit.execute(f"ALTER TABLE {rel} ENABLE ALWAYS TRIGGER {trg}")
    assert not rvbbit.execute("SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)", (rel,)).fetchone()[0]
    rvbbit.execute("SELECT rvbbit.install_shadow_heap_dirty_triggers(%s::regclass)", (rel,))
    assert rvbbit.execute("SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)", (rel,)).fetchone()[0]


def test_prepared_reader_reuses_connection_across_publications(rvbbit, cube_source):
    cube, source = cube_source
    query = f"SELECT id, amount FROM cubes.{cube} ORDER BY id"
    with psycopg.connect(RVBBIT_DSN, autocommit=True, prepare_threshold=0) as reader:
        assert reader.execute(query).fetchall() == [(1, 10), (2, 20)]
        for values in ([(3, 30)], [], [(4, 40), (5, 50)]):
            rvbbit.execute(f"DELETE FROM {source}")
            for value in values:
                rvbbit.execute(f"INSERT INTO {source} VALUES (%s,%s)", value)
            rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
            assert reader.execute(query).fetchall() == values


def test_historical_view_can_read_current_unaccelerated_lookup(rvbbit, cube_source):
    cube, source = cube_source
    gen = generation(rvbbit, cube)
    lookup = 'test_historical_lookup_' + uuid.uuid4().hex[:8]
    # Historical application functions inline live lookup scans during planning,
    # after the explicit top-level AS OF policy check has run.
    rvbbit.execute(f"CREATE FUNCTION {lookup}() RETURNS TABLE(id int,amount int) LANGUAGE sql STABLE AS 'SELECT id,amount FROM {source}'")
    try:
        rvbbit.execute(f"UPDATE {source} SET amount=50")
        rvbbit.execute("SELECT rvbbit.refresh_cube(%s)", (cube,))
        with rvbbit.transaction():
            rvbbit.execute("SELECT set_config('rvbbit.as_of_generation',%s,true)", (str(gen),))
            result=rvbbit.execute(
                f"SELECT c.id,c.amount,s.amount FROM cubes.{cube} c JOIN {lookup}() s USING(id) ORDER BY c.id",
                prepare=False,
            ).fetchall()
        assert result == [(1,10,50),(2,20,50)]
    finally:
        rvbbit.execute(f"DROP FUNCTION {lookup}()")
