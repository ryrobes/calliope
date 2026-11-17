# Calliope data workspace

`run_workspace_sql(session_id, sql)` executes ordinary derived-data work in the
durable `calliope` (derived data) and `calliope_apps` (application state) schemas. Existing `administer_local_sql` calls automatically
take this path when they contain workspace writes. Neither path asks for SQL
approval within the supported workspace scope.

```sql
CREATE TABLE IF NOT EXISTS calliope.monthly_sales (
    month date PRIMARY KEY,
    revenue numeric NOT NULL
);
DELETE FROM calliope.monthly_sales;
INSERT INTO calliope.monthly_sales
SELECT date_trunc('month', created_at)::date, sum(amount)
FROM salesforce.orders
GROUP BY 1;
CREATE INDEX IF NOT EXISTS monthly_sales_revenue
    ON calliope.monthly_sales(revenue);
SELECT * FROM calliope.monthly_sales ORDER BY month;
```

The PostgreSQL batch commits or rolls back. A statement error identifies its position;
correct and retry without creating an approval request. Keep useful output in
`calliope` and join it from dashboards or other schemas normally. This is not a
temporary scratch area and does not require a promotion ceremony.

Supported operations: table creation/alteration, CTAS, inserts/updates/deletes,
upserts/MERGE, views and materialized views, materialized-view refresh, indexes,
sequences, object comments/renames, explicit ANALYZE, and SELECT verification.
All write targets must be explicitly qualified. Source reads use existing local
tables/views in the service's exposed schemas. PostgreSQL built-in transformation
functions, installed `rvbbit.operators` functions (including generated MCP
operators), and per-server SQL wrappers registered in `rvbbit.mcp_tools` are
resolved from live catalogs on every execution. No per-connector or per-tool
name list is compiled into Calliope. New registrations work immediately;
function definitions, unregistered custom functions and administration commands
remain on their existing paths. CASCADE is excluded because dependents
may live outside the workspace. No BEGIN/COMMIT is necessary or accepted.

Scheduled SQL jobs use the same executor, without a Workflow or automatic agent
invocation. An arbitrary Python/dlt source runner remains separate. This retains
the existing Admins Team and session-owner authorization; it does not grant shared-data
creation authority to ordinary users or implement per-user workspaces.

## Database setup and permissions

Setup happens on first use, under a database advisory lock. No extension binary upgrade
or PostgreSQL restart is needed. The service's existing administration connection
must be able to create a role/schema and grant source SELECT privileges.

Each database gets a dedicated `calliope_work_<database-name-hash>` login. It owns
both `calliope` and `calliope_apps`, receives SELECT on referenced source relations, and has no privileged
role memberships, superuser, role/database creation, replication or RLS bypass.
SQL uses a **new authenticated connection**, not SET ROLE on an admin connection.
Its startup options select native PostgreSQL execution (`rvbbit.force_heap_scan`)
so RVBBIT optimizer hooks do not run just to initialize the connection. Index
creation still needs read-only access to the small `rvbbit.tables` registry;
the login receives schema USAGE and SELECT on that registry. Older INSERT
prewarm hooks also resolve function names before checking the native-execution
switch, so the login receives SELECT on `rvbbit.operators.name` only. Operator
definitions, prompts and steps remain private.
Migration 0431 makes three older no-argument DDL housekeeping event triggers run
as their existing trusted owner with a fixed search_path. First use applies this
idempotent compatibility fix too, enabling server-only upgrades. The workspace
login receives no internal catalog write grants.
The password is derived from WAREHOUSE_JWT_SECRET with a workspace-specific HMAC;
it is never supplied by an agent or put in a receipt. Changing that secret updates
the login on its next use. Database authentication must permit this login through
the same host/port as WAREHOUSE_DSN (a peer-only local socket is insufficient).

Existing workspace schemas under a different owner are not silently adopted.
Unexpected elevated privileges or memberships on the workspace role stop setup.
The server's database identity gets schema USAGE and default SELECT on generated
tables. Nothing is granted to PUBLIC. Both workspace schemas are included alongside
any explicit WAREHOUSE_SCHEMAS allowlist in ordinary discovery tools.
Separately mapped reader roles still need the usual USAGE/SELECT grants.

The runtime is for trusted appliance agents, not arbitrary hostile SQL. Existing
source views, RLS policies, triggers and extensions remain administrator-installed
code. Database-role isolation does not make such code safe if an administrator
has independently granted it elevated effects. Other external explicit function calls,
dynamic SQL/configuration entry points and role changes are excluded from this
tool, without changing existing appliance-wide privileges.

Registered calls use administrator-owned adapters in `calliope_runtime`, built
from the installed PostgreSQL signatures. Named arguments, optional defaults,
NULL semantics, overloads and set-returning MCP wrappers are preserved. Adapter
families are versioned when signatures change so stale overloads do not affect
new calls. Only the workspace login receives USAGE/EXECUTE; it cannot read
private settings, secrets or runtime catalogs. Each adapter delegates to the
installed function and retains its normal caches and receipts. PostgreSQL output
rolls back on failure; external MCP effects and inference costs cannot be undone.
Inspect external outcomes before retrying a batch that invoked a mutating tool.

## Execution and audit

- Up to 50 statements and 100,000 SQL characters; parsed with PostgreSQL's native
  grammar via pglast. The original SQL/hash is preserved. Supported RVBBIT operator
  calls are routed to private runtime adapters using the parsed syntax tree; the
  receipt also records the executed SQL when it differs.
- The existing admin SQL timeout (120 seconds by default) is a whole-batch budget,
  with a five-second lock timeout and 32 MB work_mem for workspace execution.
- Statement results retain up to 200 rows each, subject to the existing receipt
  payload bound. The receipt includes exact submitted SQL/hash, owner/session,
  command statuses, touched objects and commit outcome.
- Failed execution leaves no partial PostgreSQL output. A connection loss during COMMIT is
  reported as an unknown outcome requiring inspection, not a safe blind retry.
- Receipts use `data.workspace_sql` / executor `workspace_sql`; they are execution
  records, never pending frozen-SQL approvals or independently created inbox tasks.

Validation: run `test_workspace_sql.py` with ACTIVITY_TEST_ADMIN_DSN pointing at a
disposable PostgreSQL cluster. Tests create/drop isolated databases and roles.

## Recurring SQL ETL

After testing an idempotent batch with `run_workspace_sql`, call:

```text
save_workspace_job(session_id, "ads-dimensions", sql, interval_seconds=43200)
manage_workspace_job(session_id, "ads-dimensions", "run_now")
workspace_job_status(session_id, "ads-dimensions")
```

This is a native SQL job, not a database mirror or an agent Workflow. Registered
MCP/API functions work exactly as in an on-demand batch. No source connection or
additional approval is needed. The server worker executes the stored batch
directly; no Hermes session or orchestration inference is invoked. Explicit SQL
operators can still have their own API/inference costs.

- `interval_seconds=0` saves a manual job; 43200 is every 12 hours. First run is
  one interval after saving. `run_now` queues a coalesced immediate test without
  changing whether the recurring schedule is enabled.
- Same owner/name updates a job, not a new schedule. Identical saves preserve
  paused state and next run; changed SQL/interval starts the replacement schedule.
  SQL cannot be edited while its previous run is still active.
- `pause` removes queued and future runs. An active SQL batch finishes within its
  normal timeout. `resume` starts the interval again. The caller can manage their
  jobs from another thread; execution retains the originating session authority.
- The worker rechecks owner and current Admins Team membership on every execution.
  A database advisory lock coordinates replicas; this first slice runs one SQL
  job at a time. A unique running-job index provides a second overlap check.
- Missed intervals coalesce. Failed runs pause the job instead of automatically
  retrying. After a worker interruption, an already completed action receipt
  settles the run; an uncertain outcome pauses it for inspection, never replay.
- Job status includes SQL/hash/revision, next run and recent outcomes. Inspecting
  one job also returns the existing SQL receipts and result sets. Run history
  snapshots SQL and configuration revision; receipts do not create inbox tasks.
- Runtime migration `services/calliope/sql/0432_workspace_jobs.sql` is included in
  the server image and applied idempotently on startup/first use. Both fresh
  installs and server-only upgrades work without pg_cron or a PostgreSQL restart.

Validation: `test_workspace_jobs.py` uses disposable PostgreSQL to exercise the
actual saved-job worker, atomic materialization, repeat execution, scheduling,
pause/resume, failure, restart recovery and concurrent worker claims.

## DataRabbit operations panel

DataRabbit's **DLT & SQL Operations** panel includes SQL jobs alongside DLT
mirror runs, with All / SQL ETL / DLT mirrors filters. Job discovery checks the
two catalogs independently, including jobs with no runs yet. SQL job details
show the saved batch, schedule, next execution, revision, errors, and the latest
10 runs with their statement results and receipt IDs.

Reads use the selected DataRabbit database connection. Pause/resume/run-now
controls use the signed-in Calliope user's existing job authority through
`POST /api/calliope/workspace-jobs/control`; the UI does not update scheduler
tables directly or create agent sessions. A job from another Calliope instance
or owner is rejected rather than targeting a same-named job on this server.
The owner's off switch remains available without the originating chat. Native
controls require Calliope sign-in and a configured Calliope service URL, following
the same proxy arrangement as DataRabbit's appearance settings.

### Gallery job observability

Calliope administrators can browse mirror and SQL ETL jobs in Gallery → Data.
This view shows descriptions, direct table references, schedule and the latest
24 run durations, without returning SQL or result data to the browser.
`save_workspace_job` and `create_mirror_job` accept an optional `description`;
provide one sentence describing purpose and maintained data. Omission preserves
an existing description; no inference runs when someone opens the inventory.

Gallery pause/resume supports company administrators across owners and records
the actor in `rvbbit.calliope_job_controls`. It never changes execution ownership
or grants the creator new authority. Resume schedules the next run one interval
ahead; already queued/running mirror work may finish. Ask Calliope opens a new
thread with the exact job reference and a read-only snapshot on the Stage.

App tables default to `calliope_apps`; existing data in `calliope` is unchanged.
Both schemas have identical workspace ownership and default reader grants, are
created automatically on first workspace use, and support cross-schema joins.
`save_workspace_job` uses the same validator and executor for either schema.
Existing installations gain the second schema on their next workspace execution;
no PostgreSQL restart, extension update, or separate approval is needed.
