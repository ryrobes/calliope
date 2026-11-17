# Concurrent cube refresh

Migration 0405 routes `refresh_cube()` and the existing `refresh_all_cubes()`
cron procedure through `snapshot_load_concurrent()`. Cube SQL, names, OIDs,
indexes, grants, dependencies, refresh policies and retained history stay intact.
The migration performs no cube rebuild and does not enable a paused cron job.

## Publication and locking

1. Acquire the existing per-cube and per-table generation advisory locks without
   waiting. Pin the destination schema with `ACCESS SHARE NOWAIT`.
2. Evaluate the source query into a session-private temporary heap. The existing
   cube remains queryable and destination writers are not blocked during this phase.
3. Acquire `EXCLUSIVE NOWAIT` on the destination. PostgreSQL EXCLUSIVE permits
   ordinary SELECT; it excludes writes, locking SELECTs and conflicting maintenance.
   If busy, abort this attempt, keep the prior cube, and let a later sweep retry.
4. Replace heap rows using transactional DELETE/INSERT, without TRUNCATE. Other
   transactions still see the old heap versions. Application triggers and
   constraints run normally; only RVBBIT's per-row snapshot tombstones are
   suppressed for this exact destination and operation.
5. Export the new uncommitted heap into a new acceleration generation. Actual
   destination CTIDs are retained in the identity map. Prepare synchronous
   variants if configured, set the snapshot floor and mark the heap clean.
6. Commit publishes the heap versions and accelerator metadata together. Retained
   generations remain available to AS OF. Current-only policy prunes superseded
   metadata using the existing deferred file-reaping machinery.

Ordinary readers never need to wait for an exclusive reader-blocking lock in a
routine refresh. Existing repeatable-read transactions retain their old snapshot
through commit. Destination writers wait during heap replacement and export;
cubes are derived tables, so concurrent edits are not merged into the new result.
Source ingestion remains independent. Source changes after the build starts keep
its dirty signal so a subsequent sweep refreshes again.

Busy publication, SQL/constraint errors and cancellation roll back the complete
attempt, including temporary staging. A retry evaluates the query again; staging
is not a durable job queue. Files from aborted exports use the existing orphan
recovery machinery. No old committed generation is overwritten.

MVCC replacement uses more transient disk and WAL than TRUNCATE and leaves old
heap tuples for normal autovacuum. Long reader transactions delay tuple reclamation.
Peak capacity must cover staging, old/new heap versions and new acceleration files.
This changes query availability, not the computational cost of the cube SQL.

## Dirty-tracking maintenance

The shadow-heap trigger installer now compares the complete expected trigger set
before doing DDL. Valid triggers keep their OIDs. Actual repairs use
`ACCESS EXCLUSIVE NOWAIT`, so maintenance cannot queue a trigger rewrite behind a
long reader. Migration 0409 adds `prepare_cube_refresh(name)` and restores the
canonical `compact(regclass, boolean)` wrapper on upgraded databases so compaction
cannot recreate the obsolete all-events trigger alongside modern tracking.

Scheduled sweeps and DataRabbit's Maintain/Refresh buttons commit the repair
before starting the build. Sweeps also retry damaged destination tracking and
known deadlock/tracking errors without requiring `p_retry_errors`; unknown errors
still require an explicit retry. Manual-mode cubes remain excluded from sweeps.
Busy repairs skip immediately and are eligible again on a later sweep.

For direct SQL callers, use **separate committed transactions**:

```sql
BEGIN;
SELECT rvbbit.prepare_cube_refresh('my_cube');
COMMIT;
SELECT rvbbit.refresh_cube('my_cube');
```

The low-level refresh still rejects damaged tracking. Do not put repair and
refresh inside one transaction: that would retain the DDL lock through the build.

Initial cube creation and schema-changing redefinition remain DDL operations.
They are not covered by the routine-refresh concurrency guarantee. The existing
`snapshot_load()` API for other consumers is unchanged.

## Engine and rollout

The PostgreSQL image also fixes the AS-OF planner path for an empty current
snapshot. An empty visible generation must not cause historical queries to fall
back to the current heap. Deploy the new engine along with migration 0405 for
complete empty-snapshot history behavior; a SQL-only hot patch cannot fix that
planner branch. Migration 0405 is registered in the normal additive ledger.

Keep a previously paused cube cron off until concurrent-read canaries pass on
the new engine. Then start one affected cube refresh, check ordinary heap and
accelerated queries while it runs, and restore its prior schedule. Do not rerun
the entire host bootstrap merely to enable that one job.

## Regression tests

`tests/test_cube_refresh_mvcc.py` uses independent PostgreSQL sessions and an
advisory gate inside generation publication, after new heap rows and files exist.
It checks reader visibility before/after commit, repeatable-read snapshots,
synchronous/deferred variants, cancellation and retry, empty retained snapshots,
normal CTID/primary-key mutation tracking, indexes, constraint failures,
current-only pruning, trigger repair and migration reapplication. Existing cube
policy and source-lock tests run alongside it.

`tests/test_cube_tracking_repair.py` checks legacy-trigger recovery, retry selection,
NOWAIT behavior and the compact wrapper. DataRabbit's
`scripts/test-cube-refresh-preflight.cjs` checks separate awaited requests,
repair failure handling and nonmutating dry runs.
