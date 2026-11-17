# Mirror inspection and repair

Start with `mcp__Warehouse__mirror_status(session_id)`. It returns installation and worker health separately, a credential-free connection/job index, schedules, destination schemas, table counts, recent run receipts, row counts, and errors. Do not rediscover those facts through bespoke SQL or catalog searches.

- Add `job_name` to inspect configured tables, load modes, primary keys, cursors, change detection, and recent runs for that job.
- Add `run_id` to inspect a specific run's table outcomes and errors. Run receipts remain readable when the worker is offline.
- `limit` bounds recent runs (default 10, maximum 100); inspect a specific run for its table details. Truncation flags identify bounded lists.
- Zero configured jobs does not mean DLT is uninstalled. Check runtime registration and live worker health; a capability graph's install label can be older than the actual runtime.

This surface covers managed SQL mirrors. New SQL sources and jobs can be created from any ordinary chat; existing jobs have `mirror.manage` actions discoverable through `search_calliope_actions`. It is not an arbitrary Python/dlt collector runner. `run_workspace_sql` executes local SQL ETL, including registered MCP/API SQL calls. For recurring materializers use `save_workspace_job(session_id, job_name, sql, interval_seconds)` (43200 = every 12 hours). This runs the tested SQL directly with workspace write authority, no Hermes reasoning session and no DLT connection or approval contract. `manage_workspace_job` supports `pause`, `resume`, `run_now`; `workspace_job_status` lists jobs, next runs, errors and SQL receipts/results. Failed/interrupted jobs pause for inspection; external MCP effects cannot roll back. Do not force these jobs into SQL-source mirrors or read-only agent Workflows.

## Create a source and an on-demand job

1. `connect_mirror_source(session_id, connection_name, dialect, label, replace_credentials=false)` reuses a working SQL connection or opens its secure connection card. Choose the internal connection name yourself. Help identify the database type if the user only knows a host and login; do not ask them to construct a connection string. The connector-defined form accepts ordinary host/login details, SSH key/password access, certificates, driver options, or an advanced connection URL. Secrets go only into that native form, never chat, SQL, tools or scripts. Use `replace_credentials=true` to update a saved connection. Submission tests the connection and resumes this conversation; wait for that result instead of asking the user to confirm that they clicked the button. This works from any chat, including after installation.
2. `discover_mirror_source(session_id, connection_name)` lists source schemas. Add `source_schema` to discover tables, columns, primary keys and destination names via the worker. This is a SQL source, not an MCP registration.
3. `create_mirror_job(session_id, connection_name, source_schema, destination_schema, tables, job_name, run_now)` registers typed table mappings. Defaults: snapshot, change_detection=always, no schedule, run_now=false. Set run_now=true for one first load. Incremental upsert requires primary_key and cursor_column. One destination schema belongs to one job.
4. The load receipt updates as tables are copied and resumes this conversation when it settles. Inspect `mirror_status(job_name=...)` for details, then verify loaded rows through local SQL. Registration and queuing are not proof of a successful load. Explain partial copies; do not mark a new source ready because another source has succeeded previously.

## A first conversation about data

Start with what the person wants to understand and what source they have access to. After connecting, use the discovered catalog to suggest a small useful set of tables. If the choice matters, present a short native `request_calliope_input` choice with plain labels; keep schema names, refresh internals and cursor mechanics out of the default questions. Keep their original question in view and deliver a first answer from the copied local data.

Open the secure card as soon as the source type is known. The typed action validates names and prerequisites; do not delay this first step with catalog searches, mirror inventory checks, or a specialist safety review, including in team mode.

When asked why copying is necessary: Calliope runs analysis against a controlled local copy so exploratory questions do not repeatedly query production. The initial copy and chosen refresh schedule still read the source. Use a read-only source account and review what to copy and how often. Do not promise zero source traffic.

For connection failures, distinguish address/firewall, SSH login/host identity, database authentication, TLS, and table permissions from the credential-free receipt. Reuse the existing secure card for correction or retesting. Ask for external administrator help only when the user cannot resolve access; offer a short credential-free explanation to share. The worker opens and closes SSH tunnels itself; never install an agent-managed background tunnel or fetch credentials from its encrypted store.

An identical creation retry reuses the existing job, preserving its pause/schedule and without queuing another load. Different mappings under an existing name fail explicitly rather than overwrite it. Use the job's `mirror.manage` action for subsequent runs, pause/resume, and an explicitly requested schedule. This path uses the existing DLT worker; it does not launch a Hermes reasoning session per tick.

Classify each job as:

- healthy: controller ready, worker reachable, latest run succeeded, next run scheduled;
- active: current run has a fresh heartbeat;
- stale: active heartbeat or scheduled run is overdue;
- failed: latest run has an error code/message or failed tables;
- never run: configured without a completed run.

For repair:

1. Inspect the specific job/run receipt and worker error.
2. Validate source connection through the mirror controller, never through analysis SQL.
3. Preserve one job per destination schema and the original source table names.
4. Ensure copied user tables are created with `USING rvbbit`; storage remains ordinary Postgres heap semantics.
5. Retry only the affected job after correcting configuration. Verify loaded rows and local relations.
6. Trigger a catalog crawl after a successful new mirror; recurring catalog work can continue on schedule.

For snapshot tables, choose change detection deliberately:

- `always` is the correctness-first default for mutable rows. Every scheduled run
  performs the full replacement and RVBBIT correctly treats its `TRUNCATE` as a
  new accelerator baseline.
- `append_only_key` is an explicit no-UPDATE/no-DELETE contract. The worker asks
  the source only for row count plus first/last primary key and skips DLT entirely
  when that signature and the local destination identity still match. Configure it
  with `rvbbit.set_mirror_table_change_detection(job, source_table,
  'append_only_key')`; never infer it merely because a primary key exists.
- Prefer `incremental_upsert` when the source has a trustworthy monotonic update
  cursor and primary key. It is the right default for large append-heavy tables.

Inspect `rvbbit.mirror_change_detection_status` for the last source row count,
publish/check timestamps, and consecutive unchanged skips. A `skipped` per-table
run receipt means no source rows crossed the wire and the destination was untouched.

Do not introduce staging tables merely because the source is messy. Mirrored tables are the reporting copies.
