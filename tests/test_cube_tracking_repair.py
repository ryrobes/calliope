"""Repair and refresh use separate transactions; busy setup never queues readers."""
from pathlib import Path

import psycopg
import pytest
from conftest import RVBBIT_DSN
from test_cube_refresh_lock_isolation import cube_source

MIGRATION = Path(__file__).resolve().parents[1] / 'crates/pg_rvbbit/sql/migrations/0409_cube_tracking_self_repair.sql'

@pytest.fixture(autouse=True)
def migration(rvbbit, cube_source):
    rvbbit.execute(MIGRATION.read_text())


def sweep(conn, cube):
    conn.execute('CALL rvbbit.refresh_all_cubes(p_category=>%s,p_sleep_seconds=>0)', (cube,))


@pytest.mark.parametrize('legacy', [True, False])
def test_default_sweep_retries_deadlock_and_repairs_legacy_tracking(rvbbit, cube_source, legacy):
    cube, source = cube_source
    if legacy:
        rvbbit.execute(f'CREATE TRIGGER rvbbit_shadow_heap_dirty AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cubes.{cube} FOR EACH STATEMENT EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()')
    rvbbit.execute("UPDATE rvbbit.cube_control SET last_error='deadlock detected' WHERE cube_name=%s", (cube,))
    rvbbit.execute(f'UPDATE {source} SET amount=99')
    sweep(rvbbit, cube)
    assert rvbbit.execute('SELECT last_error FROM rvbbit.cube_control WHERE cube_name=%s', (cube,)).fetchone()[0] is None
    assert rvbbit.execute('SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)', ('cubes.'+cube,)).fetchone()[0]
    assert rvbbit.execute(f'SELECT amount FROM cubes.{cube} ORDER BY id').fetchall() == [(99,), (99,)]


def test_unknown_error_is_not_retried_by_default(rvbbit, cube_source):
    cube, _ = cube_source
    rvbbit.execute("UPDATE rvbbit.cube_control SET last_error='operator must fix definition' WHERE cube_name=%s", (cube,))
    before=rvbbit.execute('SELECT refreshed_at FROM rvbbit.cube_control WHERE cube_name=%s', (cube,)).fetchone()
    sweep(rvbbit, cube)
    assert rvbbit.execute('SELECT refreshed_at FROM rvbbit.cube_control WHERE cube_name=%s', (cube,)).fetchone() == before


def test_busy_repair_does_not_queue_and_next_sweep_recovers(rvbbit, cube_source):
    cube, source = cube_source
    rel='cubes.'+cube
    rvbbit.execute(f'DROP TRIGGER rvbbit_shadow_heap_dirty_insert ON {rel}')
    rvbbit.execute("UPDATE rvbbit.cube_control SET last_error='deadlock detected' WHERE cube_name=%s", (cube,))
    with psycopg.connect(RVBBIT_DSN) as reader, psycopg.connect(RVBBIT_DSN, autocommit=True) as other:
        reader.execute(f'LOCK TABLE {rel} IN ACCESS SHARE MODE')
        other.execute("SET statement_timeout='1500ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            other.execute('SELECT rvbbit.prepare_cube_refresh(%s)', (cube,))
        sweep(other,cube)
        assert other.execute(f'SELECT count(*) FROM {rel}').fetchone()[0] == 2
        other.execute(f'INSERT INTO {source} VALUES(3,30)')
    sweep(rvbbit,cube)
    assert rvbbit.execute(f'SELECT count(*) FROM {rel}').fetchone()[0] == 3
    assert rvbbit.execute('SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)', (rel,)).fetchone()[0]


def test_compact_does_not_reintroduce_legacy_trigger(rvbbit, cube_source):
    cube,_=cube_source
    rel='cubes.'+cube
    rvbbit.execute('SELECT * FROM rvbbit.compact(%s::regclass,true)',(rel,))
    assert rvbbit.execute('SELECT rvbbit._shadow_heap_dirty_tracking_matches(%s::regclass)', (rel,)).fetchone()[0]
    rvbbit.execute('SELECT rvbbit.prepare_cube_refresh(%s)',(cube,))
    with psycopg.connect(RVBBIT_DSN,autocommit=True) as reader:
        reader.execute("SET statement_timeout='1500ms'")
        assert reader.execute(f'SELECT count(*) FROM {rel}').fetchone()[0] == 2
    assert rvbbit.execute('SELECT rvbbit.refresh_cube(%s)',(cube,)).fetchone()[0] == 2
