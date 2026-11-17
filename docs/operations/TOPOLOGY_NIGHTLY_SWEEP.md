# Nightly Business Topology sweep

Migration 0404 adds a durable warehouse sweep. It snapshots all caller-readable
business relations accepted by `_business_topology_relation_allowed`, then splits
each schema into batches of at most 24 relations. The existing worker performs
the excavation and keeps its normal model budgets, source sampling, cache reuse,
and single-running-job constraint. Schemas and batches are execution scopes;
they do not define semantic business-domain identity.

Both canonical scheduler seeds install:

| Job | Schedule | Action |
| --- | --- | --- |
| `rvbbit_topology_nightly_sweep` | `17 2 * * *` | Start a new sweep or continue the active one |
| `rvbbit_topology_sweep_continue` | `*/5 * * * *` | Fill available queue slots and reconcile completion |

Schedules use `cron.timezone` (UTC in the appliance defaults). 02:17 UTC is
22:17 the preceding evening in US Eastern daylight time, 21:17 in standard time.
The cron jobs live in the scheduler database (normally `postgres`) and target
the application database (normally `rvbbit`). The 15-minute knowledge-loop tick
continues to maintain adoption and wiki state independently.

At most eight jobs are queued, alongside at most one running excavation.
Round-robin batch order gives each schema a turn before the next batch of a
large schema. An overlapping queued/running/cancelling manual run defers that
batch until it finishes. A sweep spanning multiple days is continued, never
replaced by the next nightly trigger. Newly registered relations join the next
sweep. Source changes after the snapshot are checked before queueing.

Failed/cancelled jobs and disappeared/ineligible sources count as failed
batches. They keep their receipts and do not retry indefinitely within the same
sweep. A completed sweep with any failed batch is `completed_with_errors`;
the next night's sweep attempts current coverage again. Completion means all
batches reached terminal states; it is not a guarantee of useful proposals.
Inspect each workflow's result/error for proposal quality and coverage.

## Inspect or start

```sql
SELECT * FROM rvbbit.business_topology_sweep_status ORDER BY requested_at DESC;
SELECT b.schema_name,b.batch_number,cardinality(b.relations) AS relations,
       b.run_id,r.status,coalesce(b.error,r.error) AS error
FROM rvbbit.business_topology_sweep_batches b
LEFT JOIN rvbbit.business_topology_workflow_runs r USING(run_id)
WHERE b.sweep_id = '<sweep UUID>'::uuid
ORDER BY b.batch_number,b.schema_name;

-- Start now; safe to repeat while a sweep is active.
SELECT rvbbit.business_topology_start_nightly_sweep();
-- Optional explicitly scoped sweep, only when no sweep is active:
SELECT rvbbit.business_topology_start_nightly_sweep(ARRAY['crm'],24);
```

Disable both cron jobs to stop automatic scheduling/refilling. Already queued
or running excavations retain their normal cancellation controls. A sweep can
be administratively marked `cancelled` to stop its pending batches from being
queued; that does not silently cancel its existing workflow jobs.

## Existing-host SQL hot patch

Apply 0404 in one transaction using `ON_ERROR_STOP`, a short `lock_timeout`,
and `LOCK TABLE rvbbit.schema_migrations IN SHARE ROW EXCLUSIVE MODE`. Set
`rvbbit.force_heap_scan=on` locally, as the normal migration runner does.
Record `0404_business_topology_nightly_sweep` in `rvbbit.schema_migrations` in the
same transaction only after the SQL succeeds. Save the exact SQL checksum in the
host's patch receipt. The next PostgreSQL image embeds the identical migration
and skips its already-recorded ledger entry.

Install just the two cron definitions above in the scheduler database, using
`cron.schedule_in_database` and the target database name. Do not rerun the full
bootstrap on a customized host merely to add these jobs. No server, worker, or
PostgreSQL restart is needed. The next normal release must rebuild Postgres and
ship the bootstrap source changes; it does not need a new server image.
