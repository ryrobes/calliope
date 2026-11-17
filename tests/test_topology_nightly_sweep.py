"""Run with TOPOLOGY_SWEEP_TEST_DSN pointing at a disposable database with 0404.

No excavation worker is used: tests drain its durable queue explicitly.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

DSN = os.environ.get('TOPOLOGY_SWEEP_TEST_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires disposable topology sweep database')


@pytest.fixture
def db():
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as conn:
        # This fixture requires a dedicated disposable DB, never a hosted DSN.
        assert conn.execute('SELECT count(*) n FROM rvbbit.business_topology_workflow_workers').fetchone()['n'] == 0
        conn.execute('TRUNCATE rvbbit.business_topology_sweep_batches, rvbbit.business_topology_sweeps, rvbbit.business_topology_workflow_runs CASCADE')
        yield conn
        conn.execute('TRUNCATE rvbbit.business_topology_sweep_batches, rvbbit.business_topology_sweeps, rvbbit.business_topology_workflow_runs CASCADE')


@pytest.fixture
def scopes(db):
    names = ['sweep_a_' + uuid.uuid4().hex[:8], 'sweep_b_' + uuid.uuid4().hex[:8]]
    for name, count in zip(names, [105, 4]):
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
        for i in range(count):
            db.execute(sql.SQL('CREATE TABLE {}.{} (id int) USING heap').format(sql.Identifier(name), sql.Identifier(f't{i:03}')))
        db.execute(sql.SQL('CREATE TABLE {}._dlt_control (id int) USING heap').format(sql.Identifier(name)))
    try:
        yield names
    finally:
        for name in names:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


def start(db, scopes, size=24):
    return db.execute('SELECT rvbbit.business_topology_start_nightly_sweep(%s,%s) id', (scopes, size)).fetchone()['id']


def tick(db):
    return db.execute('SELECT rvbbit.business_topology_sweep_tick() result').fetchone()['result']


def status(db, sweep):
    return db.execute('SELECT * FROM rvbbit.business_topology_sweep_status WHERE sweep_id=%s', (sweep,)).fetchone()


def drain(db):
    db.execute("UPDATE rvbbit.business_topology_workflow_runs SET status='completed',phase='completed',finished_at=clock_timestamp() WHERE status='queued'")


def test_exact_coverage_wide_schema_exclusions_and_bounded_queue(db, scopes):
    sweep = start(db, scopes, 10)
    s = status(db, sweep)
    assert (s['relations'], s['schemas'], s['batches'], s['queued_batches'], s['pending_batches']) == (109, 2, 12, 8, 4)
    assert db.execute('SELECT max(cardinality(relations)) n FROM rvbbit.business_topology_sweep_batches').fetchone()['n'] == 10
    relations = db.execute('SELECT unnest(relations) rel FROM rvbbit.business_topology_sweep_batches').fetchall()
    assert len({r['rel'] for r in relations}) == 109
    assert not any('_dlt_control' in r['rel'] for r in relations)
    assert tick(db)['enqueued'] == 0
    assert start(db, scopes, 10) == sweep
    assert db.execute('SELECT count(*) n FROM rvbbit.business_topology_workflow_runs').fetchone()['n'] == 8
    drain(db)
    assert tick(db)['enqueued'] == 4
    drain(db)
    assert tick(db)['status'] == 'completed'
    assert status(db, sweep)['completed_relations'] == 109
    assert tick(db)['status'] == 'idle'


def test_failure_receipt_and_no_automatic_retry_in_same_sweep(db, scopes):
    sweep = start(db, scopes)
    db.execute("UPDATE rvbbit.business_topology_workflow_runs SET status='failed',phase='failed',error='test timeout' WHERE run_id=(SELECT run_id FROM rvbbit.business_topology_workflow_runs LIMIT 1)")
    drain(db)
    assert tick(db)['status'] == 'completed_with_errors'
    s = status(db, sweep)
    assert s['failed_batches'] == 1 and s['pending_batches'] == 0
    assert tick(db)['status'] == 'idle'
    assert start(db, scopes) != sweep


def test_overlapping_manual_job_defers_only_covered_schema(db, scopes):
    manual = db.execute('SELECT rvbbit.business_topology_queue_schema(%s) id', (scopes[1],)).fetchone()['id']
    sweep = start(db, scopes)
    assert status(db, sweep)['pending_batches'] == 1
    assert db.execute('SELECT count(*) n FROM rvbbit.business_topology_sweep_batches WHERE schema_name=%s AND run_id IS NOT NULL', (scopes[1],)).fetchone()['n'] == 0
    db.execute("UPDATE rvbbit.business_topology_workflow_runs SET status='completed' WHERE run_id=%s", (manual,))
    assert tick(db)['enqueued'] == 1


def test_missing_source_is_recorded_without_stopping_other_batches(db, scopes):
    sweep = start(db, scopes, 10)
    pending = db.execute('SELECT relations FROM rvbbit.business_topology_sweep_batches WHERE run_id IS NULL LIMIT 1').fetchone()['relations']
    db.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(*pending[0].split('.'))))
    drain(db)
    tick(db)
    drain(db)
    assert tick(db)['status'] == 'completed_with_errors'
    s = status(db, sweep)
    assert s['failed_batches'] == 1 and s['completed_batches'] == 11


def test_concurrent_nightly_starts_share_one_sweep_and_queue(db, scopes):
    def call():
        with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as conn:
            return start(conn, scopes, 10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: call(), range(4)))
    assert len(set(ids)) == 1
    assert status(db, ids[0])['queued_batches'] == 8


def test_nightly_call_finalizes_finished_sweep_then_starts_new_one(db, scopes):
    old = start(db, scopes)
    drain(db)
    new = start(db, scopes)
    assert new != old
    assert status(db, old)['status'] == 'completed'


def test_empty_scope_and_invalid_batch_size(db):
    sweep = start(db, [])
    assert status(db, sweep)['status'] == 'completed'
    assert status(db, sweep)['relations'] == 0
    for size in [0, 101, None]:
        with pytest.raises(psycopg.Error):
            start(db, [], size)


def test_migration_reapply_preserves_inflight_sweep(db, scopes):
    from pathlib import Path
    sweep = start(db, scopes)
    migration = Path(__file__).resolve().parents[1] / 'crates/pg_rvbbit/sql/migrations/0404_business_topology_nightly_sweep.sql'
    db.execute(migration.read_text())
    assert start(db, scopes) == sweep
    assert status(db, sweep)['relations'] == 109


def test_worker_still_runs_one_job_while_coordinator_refills_queue(db, scopes):
    sweep = start(db, scopes, 10)
    claimed = db.execute("SELECT * FROM rvbbit.business_topology_claim_workflow('sweep-test-worker',300)").fetchall()
    assert len(claimed) == 1
    assert claimed[0]['parameters']['sweep_id'] == str(sweep)
    assert claimed[0]['parameters']['backend'] == 'clover_llm'
    assert db.execute("SELECT * FROM rvbbit.business_topology_claim_workflow('another-worker',300)").fetchall() == []
    assert tick(db)['enqueued'] == 1
    s = status(db, sweep)
    assert s['running_batches'] == 1 and s['queued_batches'] == 8


def test_partition_children_and_views_are_not_independent_sources(db, scopes):
    schema = sql.Identifier(scopes[1])
    db.execute(sql.SQL('CREATE TABLE {}.parent (id int) PARTITION BY RANGE(id)').format(schema))
    db.execute(sql.SQL('CREATE TABLE {}.child PARTITION OF {}.parent FOR VALUES FROM (0) TO (10)').format(schema, schema))
    db.execute(sql.SQL('CREATE VIEW {}.v AS SELECT 1 AS id').format(schema))
    sweep = start(db, [scopes[1]])
    assert status(db, sweep)['relations'] == 5
    relations = db.execute('SELECT unnest(relations) rel FROM rvbbit.business_topology_sweep_batches').fetchall()
    assert any(r['rel'].endswith('.parent') for r in relations)
    assert not any(r['rel'].endswith(('.child', '.v')) for r in relations)


def test_busy_coordinator_does_not_wait_or_duplicate(db, scopes):
    sweep = start(db, scopes, 10)
    with psycopg.connect(DSN) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtextextended('rvbbit.business-topology-sweep',0))")
        assert tick(db)['status'] == 'busy'
    assert status(db, sweep)['queued_batches'] == 8
