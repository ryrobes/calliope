import "server-only"

/**
 * System Health — the maintenance X-ray for an rvbbit database.
 *
 * Surfaces the metadata-weight problems that are invisible in bits and
 * pieces: delete_log tombstone accrual (live tombstones only die via
 * rebuild/DROP — autovacuum cannot touch them), time-travel generation
 * buildup on refresh-in-place tables, append-only catalog snapshot
 * history, the orphaned-file reap backlog, and whether the maintenance
 * cron jobs are even installed.
 *
 * Every probe self-degrades (Promise.allSettled): a plain-Postgres
 * connection gets the vacuum section and nothing rvbbit-specific.
 */

import { getPool } from "./pool"

export interface MetaTableSize {
  name: string
  bytes: number
  rows: number
}

export interface TombstoneRow {
  oid: number
  schema: string | null
  table: string | null
  dropped: boolean
  tombstones: number
  /** reltuples estimate of the live table (null when dropped). */
  liveRows: number | null
  rowGroups: number
  dirty: boolean
  driftRatio: number | null
  retryAfter: string | null
  recentFinalLockFailures: number
  lastRebuildError: string | null
}

export interface GenerationRow {
  oid: number
  schema: string | null
  table: string | null
  dropped: boolean
  generations: number
  oldestAt: string | null
  newestAt: string | null
  generationSemantics: string | null
  historyPolicy: string | null
  minVisibleGeneration: number
  effectiveKeepDays: number
}

export interface VacuumRow {
  schema: string
  table: string
  dead: number
  live: number
  lastAutovacuum: string | null
  autovacuumCount: number
}

export interface CronJobRow {
  jobname: string
  schedule: string
  active: boolean
}

export interface PostgresDiagnosticFinding {
  findingKey: string
  objectClass: string
  objectIdentity: string
  severity: "info" | "warn" | "critical"
  exactness: string
  summary: string
  evidence: Record<string, unknown>
  recommendation: string | null
  safety: unknown[]
}

export interface PostgresIndexAdvice {
  candidateKey: string
  tableName: string
  columns: string[]
  queryId: string
  calls: number
  dbTimePct: number
  costBefore: number
  costAfter: number
  improvementPct: number
  estimatedBytes: number | null
  confidence: number
  indexDdl: string
  caveats: string[]
}

export interface SystemHealth {
  connectionId: string
  generatedAt: string
  hasRvbbit: boolean
  dbSizeBytes: number
  /** The rvbbit exhaust tables, largest first. */
  metaTables: MetaTableSize[]
  deleteLog: { totalRows: number; bytes: number; top: TombstoneRow[] } | null
  generations: { total: number; top: GenerationRow[] } | null
  catalog: {
    runs: number
    snapshotRows: number
    snapshotBytes: number
    scopes: number
    largestSnapshotBytes: number
    oversizedSnapshots: number
    oldestRunAt: string | null
    newestRunAt: string | null
    lastMaintenanceAt: string | null
    lastMaintenance: Record<string, unknown> | null
  } | null
  orphaned: {
    backlog: number
    reapable: number
    referenced: number
    erroring: number
    oldestQueuedAt: string | null
  } | null
  vacuum: { top: VacuumRow[]; running: number }
  /** cron.* lives only in the pg_cron home database — on the app database
   *  this reads as readable=false, which is itself a finding. */
  cron: { readable: boolean; home: string | null; jobs: CronJobRow[] }
  postgresDiagnostics: {
    runId: number
    startedAt: string
    finishedAt: string | null
    status: "ok" | "degraded"
    capabilities: Record<string, unknown>
    snapshot: Record<string, unknown>
    summary: Record<string, unknown>
    findings: PostgresDiagnosticFinding[]
    indexAdvice: PostgresIndexAdvice[]
  } | null
  /** Availability of the remedy functions on this extension version. */
  fns: Record<string, boolean>
}

// The known exhaust tables, in rough size order on churny deployments.
const META_TABLES = [
  "delete_log",
  "catalog_snapshots",
  "route_decisions",
  "route_executions",
  "embedding_cache",
  "kg_evidence",
  "kg_nodes",
  "generations",
  "row_groups",
  "cost_events",
  "receipts",
  "operator_test_runs",
]

const REMEDY_FNS: Record<string, string> = {
  rebuild_acceleration: "rvbbit.rebuild_acceleration(regclass,boolean)",
  reap_generations: "rvbbit.reap_generations(regclass,integer)",
  reap_orphaned_files: "rvbbit.reap_orphaned_files(interval,integer)",
  prune_delete_log: "rvbbit.prune_delete_log(integer)",
  catalog_maintain: "rvbbit.catalog_maintain(integer)",
  install_maintenance_jobs: "rvbbit.install_maintenance_jobs(text,text,bigint)",
  maintenance_mode: "rvbbit.maintenance_mode(boolean)",
  collect_postgres_diagnostics:
    "rvbbit.collect_postgres_diagnostics(boolean,integer,double precision,double precision)",
}

function num(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}
function str(v: unknown): string | null {
  return v == null ? null : String(v)
}

export async function loadSystemHealth(connectionId: string): Promise<SystemHealth> {
  const { pool } = await getPool(connectionId, undefined, "meta")
  const client = await pool.connect()
  try {
    const q = (sql: string) =>
      client.query(sql).then(
        (r) => ({ ok: true as const, rows: r.rows as Array<Record<string, unknown>> }),
        () => ({ ok: false as const, rows: [] as Array<Record<string, unknown>> }),
      )

    const [ext, dbSize, meta, dlTotal, dlTop, gens, cat, orph, vac, vacRunning, cron, cronHome, diagnostics, fns] =
      await Promise.all([
        q(`SELECT 1 FROM pg_extension WHERE extname = 'pg_rvbbit'`),
        q(`SELECT pg_database_size(current_database())::int8 AS b`),
        q(`SELECT c.relname AS name,
                  pg_total_relation_size(c.oid)::int8 AS bytes,
                  GREATEST(c.reltuples, 0)::int8 AS rows
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'rvbbit'
              AND c.relname = ANY(ARRAY[${META_TABLES.map((t) => `'${t}'`).join(",")}])
            ORDER BY pg_total_relation_size(c.oid) DESC`),
        q(`SELECT count(*)::int8 AS rows FROM rvbbit.delete_log`),
        q(`WITH pressure AS (
                SELECT dl.table_oid, count(*)::int8 AS tombstones
                  FROM rvbbit.delete_log dl
                 GROUP BY dl.table_oid
             )
             SELECT p.table_oid::int8 AS oid,
                    n.nspname AS schema, c.relname AS table,
                    (c.oid IS NULL) AS dropped,
                    p.tombstones,
                    GREATEST(c.reltuples, 0)::int8 AS live_rows,
                    coalesce(f.row_groups, 0)::int8 AS row_groups,
                    coalesce(f.shadow_heap_dirty, false) AS dirty,
                    f.drift_ratio,
                    CASE WHEN latest.status = 'noop' AND latest.error = 'final lock busy'
                         THEN (latest.finished_at + interval '15 minutes')::text END AS retry_after,
                    coalesce(failures.n, 0)::int8 AS recent_final_lock_failures,
                    latest.error AS last_rebuild_error
               FROM pressure p
               LEFT JOIN pg_class c ON c.oid = p.table_oid
               LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
               LEFT JOIN rvbbit.accel_freshness f ON f.table_oid = p.table_oid
               LEFT JOIN LATERAL (
                   SELECT o.status, o.error, o.finished_at
                     FROM rvbbit.acceleration_operations o
                    WHERE o.table_oid = p.table_oid
                      AND o.operation = 'rebuild_acceleration'
                    ORDER BY o.started_at DESC, o.id DESC LIMIT 1
               ) latest ON true
               LEFT JOIN LATERAL (
                   SELECT count(*)::int8 AS n
                     FROM rvbbit.acceleration_operations o
                    WHERE o.table_oid = p.table_oid
                      AND o.operation = 'rebuild_acceleration'
                      AND o.status = 'noop' AND o.error = 'final lock busy'
                      AND o.started_at > clock_timestamp() - interval '1 hour'
               ) failures ON true
              ORDER BY p.tombstones DESC
            LIMIT 15`),
        q(`SELECT g.table_oid::int8 AS oid,
                  n.nspname AS schema, c.relname AS table,
                  (c.oid IS NULL) AS dropped,
                  count(*)::int4 AS generations,
                  min(g.committed_at)::text AS oldest_at,
                  max(g.committed_at)::text AS newest_at,
                  t.generation_semantics,
                  t.history_policy,
                  coalesce(t.min_visible_generation, 0)::int8 AS min_visible_generation,
                  coalesce(ap.time_travel_keep_days,
                    (SELECT (value #>> '{}')::int FROM rvbbit.settings
                      WHERE key = 'time_travel_keep_days'), 30)::int4 AS effective_keep_days
             FROM rvbbit.generations g
             LEFT JOIN pg_class c ON c.oid = g.table_oid
             LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
             LEFT JOIN rvbbit.tables t ON t.table_oid = g.table_oid
             LEFT JOIN rvbbit.accel_policy ap ON ap.table_oid = g.table_oid
            GROUP BY g.table_oid, n.nspname, c.relname, c.oid,
                     t.generation_semantics, t.history_policy,
                     t.min_visible_generation, ap.time_travel_keep_days
            ORDER BY count(*) DESC
            LIMIT 15`),
        q(`SELECT (SELECT count(*)::int4 FROM rvbbit.catalog_runs) AS runs,
                  (SELECT count(*)::int8 FROM rvbbit.catalog_snapshots) AS snapshot_rows,
                  (SELECT pg_total_relation_size('rvbbit.catalog_snapshots')::int8) AS snapshot_bytes,
                  (SELECT count(*)::int4 FROM (
                      SELECT DISTINCT graph_id, schemas FROM rvbbit.catalog_runs
                   ) scopes) AS scopes,
                  (SELECT coalesce(max(pg_column_size(fingerprint)), 0)::int8
                     FROM rvbbit.catalog_snapshots) AS largest_snapshot_bytes,
                  (SELECT count(*)::int8 FROM rvbbit.catalog_snapshots
                    WHERE pg_column_size(fingerprint) > 1048576) AS oversized_snapshots,
                  (SELECT min(finished_at)::text FROM rvbbit.catalog_runs) AS oldest_run,
                  (SELECT max(finished_at)::text FROM rvbbit.catalog_runs) AS newest_run,
                  (SELECT updated_at::text FROM rvbbit.settings
                    WHERE key = 'catalog_maintenance_status') AS last_maintenance_at,
                  (SELECT value FROM rvbbit.settings
                    WHERE key = 'catalog_maintenance_status') AS last_maintenance`),
        q(`WITH referenced_paths AS MATERIALIZED (
                SELECT path FROM rvbbit.row_groups
                UNION
                SELECT path FROM rvbbit.row_group_variants
                UNION
                SELECT path FROM rvbbit.text_dictionaries
             ), annotated AS (
                SELECT o.queued_at, o.attempts, o.last_error,
                       (r.path IS NOT NULL) AS referenced
                  FROM rvbbit.orphaned_files o
                  LEFT JOIN referenced_paths r USING (path)
             )
             SELECT count(*)::int8 AS backlog,
                    count(*) FILTER (
                      WHERE queued_at <= clock_timestamp() - interval '1 hour'
                        AND NOT referenced
                    )::int8 AS reapable,
                    count(*) FILTER (WHERE referenced)::int8 AS referenced,
                    count(*) FILTER (
                      WHERE attempts > 0 AND last_error IS NOT NULL
                    )::int8 AS erroring,
                    min(queued_at)::text AS oldest
               FROM annotated`),
        q(`SELECT schemaname AS schema, relname AS table,
                  n_dead_tup::int8 AS dead, n_live_tup::int8 AS live,
                  last_autovacuum::text AS last_av, autovacuum_count::int8 AS av_count
             FROM pg_stat_user_tables
            ORDER BY n_dead_tup DESC
            LIMIT 10`),
        q(`SELECT count(*)::int4 AS n FROM pg_stat_progress_vacuum`),
        q(`SELECT jobname, schedule, active FROM cron.job ORDER BY jobname`),
        q(`SELECT setting AS home FROM pg_settings WHERE name = 'cron.database_name'`),
        q(`WITH latest AS (
                SELECT run_id,started_at::text,finished_at::text,status,
                       capabilities,snapshot,summary
                  FROM rvbbit.postgres_diagnostic_status
                 LIMIT 1
             ), findings AS (
                SELECT coalesce(jsonb_agg(to_jsonb(f) ORDER BY
                           CASE f.severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                           f.finding_key,f.object_identity),'[]'::jsonb) AS rows
                  FROM rvbbit.postgres_diagnostic_current_findings f
             ), advice AS (
                SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY
                           a.db_time_pct DESC,a.improvement_pct DESC),'[]'::jsonb) AS rows
                  FROM rvbbit.postgres_index_advice_current a
             )
             SELECT latest.*,findings.rows AS findings,advice.rows AS advice
               FROM latest CROSS JOIN findings CROSS JOIN advice`),
        q(
          `SELECT ${Object.entries(REMEDY_FNS)
            .map(([k, sig]) => `(to_regprocedure('${sig}') IS NOT NULL) AS ${k}`)
            .join(", ")}`,
        ),
      ])

    const hasRvbbit = ext.ok && ext.rows.length > 0
    const fnRow = fns.ok ? (fns.rows[0] ?? {}) : {}

    return {
      connectionId,
      generatedAt: new Date().toISOString(),
      hasRvbbit,
      dbSizeBytes: dbSize.ok ? num(dbSize.rows[0]?.b) : 0,
      metaTables: meta.rows.map((r) => ({
        name: String(r.name),
        bytes: num(r.bytes),
        rows: num(r.rows),
      })),
      deleteLog: dlTotal.ok
        ? {
            totalRows: num(dlTotal.rows[0]?.rows),
            bytes: num(meta.rows.find((r) => r.name === "delete_log")?.bytes),
            top: dlTop.rows.map((r) => ({
              oid: num(r.oid),
              schema: str(r.schema),
              table: str(r.table),
              dropped: r.dropped === true,
              tombstones: num(r.tombstones),
              liveRows: r.dropped === true ? null : num(r.live_rows),
              rowGroups: num(r.row_groups),
              dirty: r.dirty === true,
              driftRatio: r.drift_ratio == null ? null : num(r.drift_ratio),
              retryAfter: str(r.retry_after),
              recentFinalLockFailures: num(r.recent_final_lock_failures),
              lastRebuildError: str(r.last_rebuild_error),
            })),
          }
        : null,
      generations: gens.ok
        ? {
            total: gens.rows.reduce((a, r) => a + num(r.generations), 0),
            top: gens.rows.map((r) => ({
              oid: num(r.oid),
              schema: str(r.schema),
              table: str(r.table),
              dropped: r.dropped === true,
              generations: num(r.generations),
              oldestAt: str(r.oldest_at),
              newestAt: str(r.newest_at),
              generationSemantics: str(r.generation_semantics),
              historyPolicy: str(r.history_policy),
              minVisibleGeneration: num(r.min_visible_generation),
              effectiveKeepDays: num(r.effective_keep_days),
            })),
          }
        : null,
      catalog: cat.ok
        ? {
            runs: num(cat.rows[0]?.runs),
            snapshotRows: num(cat.rows[0]?.snapshot_rows),
            snapshotBytes: num(cat.rows[0]?.snapshot_bytes),
            scopes: num(cat.rows[0]?.scopes),
            largestSnapshotBytes: num(cat.rows[0]?.largest_snapshot_bytes),
            oversizedSnapshots: num(cat.rows[0]?.oversized_snapshots),
            oldestRunAt: str(cat.rows[0]?.oldest_run),
            newestRunAt: str(cat.rows[0]?.newest_run),
            lastMaintenanceAt: str(cat.rows[0]?.last_maintenance_at),
            lastMaintenance:
              cat.rows[0]?.last_maintenance && typeof cat.rows[0].last_maintenance === "object"
                ? (cat.rows[0].last_maintenance as Record<string, unknown>)
                : null,
          }
        : null,
      orphaned: orph.ok
        ? {
            backlog: num(orph.rows[0]?.backlog),
            reapable: num(orph.rows[0]?.reapable),
            referenced: num(orph.rows[0]?.referenced),
            erroring: num(orph.rows[0]?.erroring),
            oldestQueuedAt: str(orph.rows[0]?.oldest),
          }
        : null,
      vacuum: {
        top: vac.rows.map((r) => ({
          schema: String(r.schema),
          table: String(r.table),
          dead: num(r.dead),
          live: num(r.live),
          lastAutovacuum: str(r.last_av),
          autovacuumCount: num(r.av_count),
        })),
        running: vacRunning.ok ? num(vacRunning.rows[0]?.n) : 0,
      },
      cron: {
        readable: cron.ok,
        home: cronHome.ok ? str(cronHome.rows[0]?.home) : null,
        jobs: cron.rows.map((r) => ({
          jobname: String(r.jobname),
          schedule: String(r.schedule),
          active: r.active === true || r.active === "t",
        })),
      },
      postgresDiagnostics: diagnostics.ok && diagnostics.rows[0]
        ? {
            runId: num(diagnostics.rows[0].run_id),
            startedAt: String(diagnostics.rows[0].started_at),
            finishedAt: str(diagnostics.rows[0].finished_at),
            status: diagnostics.rows[0].status === "degraded" ? "degraded" : "ok",
            capabilities:
              diagnostics.rows[0].capabilities && typeof diagnostics.rows[0].capabilities === "object"
                ? (diagnostics.rows[0].capabilities as Record<string, unknown>)
                : {},
            snapshot:
              diagnostics.rows[0].snapshot && typeof diagnostics.rows[0].snapshot === "object"
                ? (diagnostics.rows[0].snapshot as Record<string, unknown>)
                : {},
            summary:
              diagnostics.rows[0].summary && typeof diagnostics.rows[0].summary === "object"
                ? (diagnostics.rows[0].summary as Record<string, unknown>)
                : {},
            findings: (Array.isArray(diagnostics.rows[0].findings)
              ? diagnostics.rows[0].findings
              : []).map((raw: Record<string, unknown>) => ({
                findingKey: String(raw.finding_key),
                objectClass: String(raw.object_class),
                objectIdentity: String(raw.object_identity),
                severity: raw.severity === "critical" ? "critical" : raw.severity === "warn" ? "warn" : "info",
                exactness: String(raw.exactness),
                summary: String(raw.summary),
                evidence: raw.evidence && typeof raw.evidence === "object"
                  ? (raw.evidence as Record<string, unknown>) : {},
                recommendation: str(raw.recommendation),
                safety: Array.isArray(raw.safety) ? raw.safety : [],
              })),
            indexAdvice: (Array.isArray(diagnostics.rows[0].advice)
              ? diagnostics.rows[0].advice
              : []).map((raw: Record<string, unknown>) => ({
                candidateKey: String(raw.candidate_key),
                tableName: String(raw.table_name),
                columns: Array.isArray(raw.columns) ? raw.columns.map(String) : [],
                queryId: String(raw.query_id),
                calls: num(raw.calls),
                dbTimePct: num(raw.db_time_pct),
                costBefore: num(raw.cost_before),
                costAfter: num(raw.cost_after),
                improvementPct: num(raw.improvement_pct),
                estimatedBytes: raw.estimated_bytes == null ? null : num(raw.estimated_bytes),
                confidence: num(raw.confidence),
                indexDdl: String(raw.index_ddl),
                caveats: Array.isArray(raw.caveats) ? raw.caveats.map(String) : [],
              })),
          }
        : null,
      fns: Object.fromEntries(Object.keys(REMEDY_FNS).map((k) => [k, fnRow[k] === true])),
    }
  } finally {
    client.release()
  }
}

// ─── Remediation script generation ──────────────────────────────────
//
// Scripts are REVIEWABLE artifacts: one statement per line, ordered by
// impact, with the numbers that justified each line in a trailing
// comment. The lens SQL window runs multi-statement scripts serially,
// which is exactly right for rebuilds — no giant wrapping transaction.

export type MaintenanceScriptKind =
  | "rebuild"
  | "reap-generations"
  | "snapshots-retention"
  | "orphaned-files"
  | "vacuum-metadata"
  | "install-jobs"

function qlit(s: string): string {
  return `'${s.replace(/'/g, "''")}'`
}

const REVIEW_HEADER = (title: string) =>
  `-- RVBBIT maintenance · ${title}
-- Generated by System Health. REVIEW before running.
-- Statements run one at a time in this window — safe to stop midway.
`

export async function buildMaintenanceScript(
  connectionId: string,
  kind: MaintenanceScriptKind,
): Promise<{ sql: string; statements: number }> {
  const { pool } = await getPool(connectionId, undefined, "meta")
  const client = await pool.connect()
  try {
    if (kind === "rebuild") {
      const sql = `${REVIEW_HEADER("accelerator maintenance pressure")}
-- Read-only. Automatic freshness/fold lanes own corrective action.
-- retry_after explains why a hot table is deliberately not being hammered.

SELECT table_name, strategy, shadow_heap_dirty, heap_live_tuples,
       row_groups, tombstones, drift_ratio,
       max_row_groups_before_rebuild, max_tombstones_before_rebuild,
       retry_after, retry_seconds, recent_final_lock_failures,
       fold_due, blocked_reason, last_rebuild_status, last_rebuild_error
  FROM rvbbit.accel_maintenance_pressure
 ORDER BY tombstones DESC, row_groups DESC;

-- Exceptional operator override (normally leave this to the engine):
-- SELECT rvbbit.rebuild_acceleration('schema.table'::regclass, true);
`
      return { sql, statements: 1 }
    }

    if (kind === "reap-generations") {
      const sql = `${REVIEW_HEADER("generation retention status")}
-- Read-only. Snapshot generations below min_visible_generation are reaped
-- automatically after their effective keep-days policy. Cumulative
-- generations contain live rows and are bounded by the accelerator fold lane.

SELECT p.table_name,
       p.generation_semantics,
       p.history_policy,
       p.retained_generations,
       retained.oldest_retained_at,
       retained.newest_retained_at,
       p.min_visible_generation,
       coalesce(ap.time_travel_keep_days,
         (SELECT (value #>> '{}')::int FROM rvbbit.settings
           WHERE key = 'time_travel_keep_days'), 30) AS effective_keep_days
  FROM rvbbit.acceleration_storage_policy p
  LEFT JOIN rvbbit.accel_policy ap ON ap.table_oid = p.table_oid
  LEFT JOIN LATERAL (
      SELECT min(g.committed_at) AS oldest_retained_at,
             max(g.committed_at) AS newest_retained_at
        FROM rvbbit.generations g
       WHERE g.table_oid = p.table_oid
  ) retained ON true
 ORDER BY p.retained_generations DESC, p.table_name;

-- Exceptional run-now operation (normally invoked by rvbbit.maintain):
-- SELECT * FROM rvbbit.reap_generations_by_policy();
`
      return { sql, statements: 1 }
    }

    if (kind === "snapshots-retention") {
      const sql = `${REVIEW_HEADER("run catalog hygiene now")}
-- This is the same bounded operation invoked automatically by
-- rvbbit.maintain(). It retains the newest 15 runs PER graph/schema scope,
-- deletes matching snapshots + progress + run rows together, and purges
-- leading-underscore relations from the search catalog.

SELECT rvbbit.catalog_maintain(15);
VACUUM (ANALYZE) rvbbit.catalog_snapshots;
-- To hand disk back to the OS (ACCESS EXCLUSIVE lock — quiet window only):
-- VACUUM FULL rvbbit.catalog_snapshots;
`
      return { sql, statements: 2 }
    }

    if (kind === "orphaned-files") {
      const sql = `${REVIEW_HEADER("reap orphaned accelerator files")}
-- Files queued by reaps/compactions that still exist on disk. Reaping
-- unlinks them; rows clear as files are removed.

SELECT rvbbit.reap_orphaned_files(interval '1 hour', 100000);
`
      return { sql, statements: 1 }
    }

    if (kind === "vacuum-metadata") {
      const lines = META_TABLES.map((t) => `VACUUM (ANALYZE) rvbbit.${t};`)
      const sql = `${REVIEW_HEADER("vacuum the rvbbit metadata tables")}
${lines.join("\n")}

-- VACUUM FULL variants reclaim disk for the OS but take ACCESS EXCLUSIVE:
${META_TABLES.map((t) => `-- VACUUM FULL rvbbit.${t};`).join("\n")}
`
      return { sql, statements: lines.length }
    }

    // install-jobs — cron.* is only callable in pg_cron's home database.
    // The home is a server-wide GUC readable from anywhere, so build the
    // right script for where the user actually is instead of letting
    // install_maintenance_jobs() bounce with pg_cron_not_home_db.
    const info = await client.query<{ db: string; cron_home: string | null }>(
      `SELECT current_database() AS db,
              (SELECT setting FROM pg_settings WHERE name = 'cron.database_name') AS cron_home`,
    )
    const db = String(info.rows[0]?.db ?? "")
    const cronHome = info.rows[0]?.cron_home ?? null

    if (cronHome && cronHome !== db) {
      const sql = `${REVIEW_HEADER("install the metadata maintenance cron jobs (cross-database)")}
-- pg_cron's home database is ${qlit(cronHome)} — cron.* is ONLY callable there.
--
--   >>> RUN THIS SCRIPT CONNECTED TO THE ${qlit(cronHome)} DATABASE <<<
--
-- (In Data Rabbit: switch to / add a connection for ${qlit(cronHome)} on this
--  server, open a SQL window there, and paste this. Or: psql -d ${cronHome})
-- The jobs it schedules will EXECUTE in ${qlit(db)} — that part is correct.
--
-- Symptom decoder:
--   "schema cron does not exist"  -> you're still connected to ${qlit(db)}; switch.
--   "can only be created in ..."  -> wrong database for the extension; switch.

CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule_in_database('rvbbit-maintain', '*/15 * * * *', 'SELECT rvbbit.maintain();', ${qlit(db)});
SELECT cron.schedule_in_database('rvbbit-storage-maintain', '0 * * * *', 'SELECT rvbbit.maintain(storage_tables => 2);', ${qlit(db)});
SELECT cron.schedule_in_database('rvbbit-accel-fold', '17 3 * * 0', 'SELECT rvbbit.accel_fold_tick(1, false);', ${qlit(db)});

-- Runnable right now in ${qlit(db)} (one manual maintenance pass, no cron):
-- SELECT rvbbit.maintain();
`
      return { sql, statements: 3 }
    }

    const sql = `${REVIEW_HEADER("install the metadata maintenance cron jobs")}
-- Schedules the recurring catalog/log hygiene heartbeat, storage/layout
-- sweep, and one bounded weekly accelerator fold.

SELECT rvbbit.install_maintenance_jobs();
`
    return { sql, statements: 1 }
  } finally {
    client.release()
  }
}
