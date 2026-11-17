# Cube refresh locking

Migration `0403_cube_refresh_lock_isolation` removes source-trigger DDL from
routine `rvbbit.refresh_cube(name)` calls. It is embedded in the normal migration
chain for both fresh installs and upgrades. Applying it replaces functions and
the cron procedure; it does not rebuild cubes, touch source triggers, or enable
any paused cron job.

Previously, each refresh dropped and recreated `rvbbit_cube_source_write` on
every tracked source table. PostgreSQL held those source-table DDL locks through
the subsequent snapshot load and compaction. A slow cube could therefore block
ordinary source reads, metadata inspection, and writes for the entire build.

The scheduled `CALL rvbbit.refresh_all_cubes()` now installs or repairs tracking
in a separate transaction and commits before refreshing each selected cube.
Correct, enabled triggers require no DDL. Repair checks the trigger function,
events, statement timing, enabled state, and absence of filters/arguments. Setup
uses `ACCESS EXCLUSIVE NOWAIT`; if that lock is unavailable, the sweep skips the
cube and leaves its refresh/error state intact for a later attempt.

Routine refresh validates tracking instead of repairing it. Refresh also uses
the existing cube definition advisory-lock namespace with a nonblocking attempt,
and acquires the destination lock required by `TRUNCATE` with `NOWAIT`. A busy
cube is skipped by the sweep; a direct refresh returns SQLSTATE `55P03`. Other
refresh errors retain the existing error-reporting behavior. These guards do not
impose a runtime limit on a successfully started scan or compaction, nor do they
eliminate all possible catalog, source-query, or storage-maintenance lock waits.

If a direct refresh reports missing or invalid tracking, commit setup separately:

```sql
BEGIN;
SELECT rvbbit._install_cube_source_dirty_tracking(sql)
FROM rvbbit.cube_catalog
WHERE name = 'my_cube';
COMMIT;

SELECT rvbbit.refresh_cube('my_cube');
```

`define_cube` and `redefine_cube` keep their existing atomic setup-and-build API.
They explicitly install missing tracking before the initial build. Consequently,
first-time setup on a new source can still hold DDL locks through that build.
For a busy source, call the installer with the proposed SQL and commit **before**
defining the cube. Reusing a valid trigger does not acquire that DDL lock.

The destination cube is still locked throughout `snapshot_load`: that function
truncates the cube, inserts its new rows, and compacts them in one transaction.
Readers arriving after the refresh acquires the destination lock must still wait.
Keeping the previous cube readable throughout a refresh requires a separate
staged-generation publication change; this migration does not implement that.

Acceleration rebuild is a different path. `accel_rebuild` stages its expensive
baseline scan and derived variants before taking its final `SHARE` handoff lock,
which allows ordinary reads but temporarily blocks writes. Legacy
`compact(rel, keep_heap => false)` explicitly truncates its target, while cube
snapshot loading calls `compact(..., keep_heap => true)` after its own truncate.
Expensive acceleration work is therefore not, by itself, evidence of a long
reader-blocking lock.

The regression suite is `tests/test_cube_refresh_lock_isolation.py`, alongside
`tests/test_cube_refresh_autopilot.py`. It uses independent PostgreSQL sessions,
holds refresh transactions open, and pauses a cron refresh inside the source
query to verify that trigger repair has committed and source reads, writes, and
metadata access remain available. It also checks that writes during a refresh
leave the cube dirty for a subsequent sweep.
