"use client"

import type { QueryResultColumn } from "@/lib/db/types"

interface QueryOk {
  ok: true
  columns: QueryResultColumn[]
  rows: Record<string, unknown>[]
}
interface QueryErr {
  ok: false
  error: string
}

async function runQuery(connectionId: string, sql: string, rowLimit = 1000): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true, poolLane: "meta" }),
    })
    return (await response.json()) as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

export interface DltRun {
  run_id: string
  job_name: string
  connection_name: string
  connection_label: string
  dialect: string
  source_schema: string
  destination_schema: string
  trigger: string
  status: string
  attempt: number
  requested_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  tables_succeeded: number
  tables_failed: number
  rows_loaded: number
  error_code: string | null
  error_message: string | null
}

export interface DltTableRun {
  run_id: string
  job_name: string
  source_table: string
  destination_table: string
  destination_relation: string
  load_mode: string
  status: string
  rows_loaded: number
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  publication_action: string | null
  source_rows: number | null
  rows_inserted: number | null
  rows_updated: number | null
  rows_deleted: number | null
  published_at: string | null
  error_code: string | null
  error_message: string | null
}

export interface DltLineage {
  job_name: string
  connection_name: string
  connection_label: string
  dialect: string
  source_schema: string
  source_table: string
  destination_relation: string
  load_mode: string
  schedule_seconds: number | null
  next_run_at: string | null
  latest_run_status: string | null
  latest_finished_at: string | null
  latest_table_status: string | null
  latest_table_rows_loaded: number | null
  latest_error_message: string | null
}

const RUNS_SQL = `
SELECT r.run_id::text,
       r.job_name,
       j.connection_name,
       c.label AS connection_label,
       c.dialect,
       j.source_schema,
       j.destination_schema,
       r.trigger,
       r.status,
       r.attempt,
       r.requested_at,
       r.started_at,
       r.finished_at,
       CASE WHEN r.started_at IS NULL THEN NULL
            ELSE (extract(epoch FROM (coalesce(r.finished_at, clock_timestamp()) - r.started_at)) * 1000)::bigint
       END AS duration_ms,
       r.tables_succeeded,
       r.tables_failed,
       r.rows_loaded,
       r.error_code,
       r.error_message
FROM rvbbit.mirror_runs r
JOIN rvbbit.mirror_jobs j USING (job_name)
JOIN rvbbit.mirror_connections c ON c.connection_name = j.connection_name
ORDER BY r.requested_at DESC
LIMIT 180`

const TABLE_RUNS_SQL = `
SELECT tr.run_id::text,
       r.job_name,
       tr.source_table,
       tr.destination_table,
       format('%I.%I', j.destination_schema, tr.destination_table) AS destination_relation,
       tr.load_mode,
       tr.status,
       tr.rows_loaded,
       tr.started_at,
       tr.finished_at,
       CASE WHEN tr.started_at IS NULL THEN NULL
            ELSE (extract(epoch FROM (coalesce(tr.finished_at, clock_timestamp()) - tr.started_at)) * 1000)::bigint
       END AS duration_ms,
       p.action AS publication_action,
       p.source_rows,
       p.rows_inserted,
       p.rows_updated,
       p.rows_deleted,
       p.published_at,
       tr.error_code,
       tr.error_message
FROM rvbbit.mirror_table_runs tr
JOIN rvbbit.mirror_runs r USING (run_id)
JOIN rvbbit.mirror_jobs j USING (job_name)
LEFT JOIN LATERAL (
  SELECT publication.action,
         publication.source_rows,
         publication.rows_inserted,
         publication.rows_updated,
         publication.rows_deleted,
         publication.published_at
  FROM rvbbit.mirror_publications publication
  WHERE publication.target_oid = to_regclass(format('%I.%I', j.destination_schema, tr.destination_table))::oid
    AND publication.published_at >= tr.started_at
    AND publication.published_at <= coalesce(tr.finished_at, clock_timestamp()) + interval '2 seconds'
  ORDER BY publication.published_at DESC
  LIMIT 1
) p ON true
ORDER BY tr.started_at DESC
LIMIT 1200`

const LINEAGE_SQL = `
SELECT job_name,
       connection_name,
       connection_label,
       dialect,
       source_schema,
       source_table,
       destination_relation,
       load_mode,
       schedule_seconds,
       next_run_at,
       latest_run_status,
       latest_finished_at,
       latest_table_status,
       latest_table_rows_loaded,
       latest_error_message
FROM rvbbit.mirror_lineage
ORDER BY connection_name, job_name, source_table
LIMIT 1000`

export interface WorkspaceJob {
  id: string
  name: string
  owner_email: string
  sql: string
  revision: number
  interval_seconds: number
  enabled: boolean
  run_requested: boolean
  next_run_at: string | null
  last_status: string | null
  last_error: string | null
  running: boolean
  updated_at: string
}

export interface WorkspaceStatement {
  statement: number
  status: string
  columns: string[]
  rows: Record<string, unknown>[]
  rows_affected: number | null
  truncated: boolean
}

export interface WorkspaceJobRun {
  id: string
  revision: number
  sql: string
  status: string
  trigger: string
  started_at: string
  finished_at: string | null
  error: string | null
  action_run_id: string | null
  result: { statements?: WorkspaceStatement[]; elapsed_ms?: number; committed?: boolean } | null
}

const WORKSPACE_JOBS_SQL = `
SELECT j.id::text, j.name, j.owner_email, j.sql, j.revision, j.interval_seconds,
       j.enabled, j.run_requested, j.next_run_at, j.last_status, j.last_error, j.updated_at,
       EXISTS(SELECT 1 FROM rvbbit.calliope_workspace_job_runs r
              WHERE r.job_id=j.id AND r.status='running') AS running
FROM rvbbit.calliope_workspace_jobs j ORDER BY j.updated_at DESC, j.id LIMIT 500`

export async function fetchWorkspaceJobRuns(connectionId: string, jobId: string): Promise<WorkspaceJobRun[]> {
  if (!/^[0-9a-f-]{36}$/i.test(jobId)) throw new Error("Invalid SQL job ID")
  const result = await runQuery(connectionId, `
    SELECT r.id::text, r.revision, r.sql, r.status, r.trigger, r.started_at,
           r.finished_at, r.error, r.action_run_id::text, a.result
    FROM rvbbit.calliope_workspace_job_runs r
    LEFT JOIN rvbbit.calliope_action_runs a ON a.id=r.action_run_id
    WHERE r.job_id='${jobId}'::uuid ORDER BY r.started_at DESC,r.id LIMIT 10`, 10)
  if (!result.ok) throw new Error(result.error)
  return result.rows as unknown as WorkspaceJobRun[]
}

export async function controlWorkspaceJob(jobId: string, operation: "pause" | "resume" | "run_now"): Promise<void> {
  const response = await fetch("/api/rvbbit/workspace-jobs/control", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jobId, operation }),
  })
  const result = await response.json().catch(() => null)
  if (!response.ok || result?.error) throw new Error(result?.error?.message ?? "Could not confirm the job control. Refresh before trying again.")
}

export async function fetchDltOperations(connectionId: string): Promise<{
  ready: boolean
  mirrorReady: boolean
  workspaceReady: boolean
  runs: DltRun[]
  tableRuns: DltTableRun[]
  lineage: DltLineage[]
  workspaceJobs: WorkspaceJob[]
  error?: string
}> {
  const empty = { ready: false, mirrorReady: false, workspaceReady: false, runs: [], tableRuns: [], lineage: [], workspaceJobs: [] }
  const probe = await runQuery(connectionId, `SELECT
    to_regclass('rvbbit.mirror_runs') IS NOT NULL AND to_regclass('rvbbit.mirror_publications') IS NOT NULL AS mirror_ready,
    to_regclass('rvbbit.calliope_workspace_jobs') IS NOT NULL AND to_regclass('rvbbit.calliope_workspace_job_runs') IS NOT NULL AS workspace_ready`, 1)
  if (!probe.ok) return { ...empty, error: probe.error }
  const mirrorReady = Boolean(probe.rows[0]?.mirror_ready)
  const workspaceReady = Boolean(probe.rows[0]?.workspace_ready)
  const noRows: QueryOk = { ok: true, columns: [], rows: [] }
  const [runs, tableRuns, lineage, jobs] = await Promise.all([
    mirrorReady ? runQuery(connectionId, RUNS_SQL, 180) : noRows,
    mirrorReady ? runQuery(connectionId, TABLE_RUNS_SQL, 1200) : noRows,
    mirrorReady ? runQuery(connectionId, LINEAGE_SQL, 1000) : noRows,
    workspaceReady ? runQuery(connectionId, WORKSPACE_JOBS_SQL, 500) : noRows,
  ])
  const failures = [runs, tableRuns, lineage, jobs].filter((result): result is QueryErr => !result.ok)
  return {
    ready: mirrorReady || workspaceReady, mirrorReady, workspaceReady,
    runs: runs.ok ? runs.rows as unknown as DltRun[] : [],
    tableRuns: tableRuns.ok ? tableRuns.rows as unknown as DltTableRun[] : [],
    lineage: lineage.ok ? lineage.rows as unknown as DltLineage[] : [],
    workspaceJobs: jobs.ok ? jobs.rows as unknown as WorkspaceJob[] : [],
    error: failures.map((result) => result.error).join("; ") || undefined,
  }
}
