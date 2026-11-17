// Operational data layer for Calliope Dreaming. This surface intentionally
// reads queue/timing metadata only; private dossiers and Dream content never
// enter DataRabbit's diagnostics payload.

export type DreamingRange = "7d" | "30d" | "90d"

export interface DreamingSettings {
  processingPaused: boolean
  companyEnabled: boolean
  personalEnabled: boolean
  activeWindowDays: number
  minChatTurns: number
  minToolCalls: number
  maxPersonalUsers: number
  telemetryRetentionDays: number
  updatedBy: string | null
  updatedAt: number | null
}

export interface DreamingOverview {
  sweeps: number
  successfulSweeps: number
  partialSweeps: number
  failedSweeps: number
  jobs: number
  completedJobs: number
  skippedJobs: number
  failedJobs: number
  queuedJobs: number
  runningJobs: number
  avgRuntimeMs: number | null
  p95RuntimeMs: number | null
  lastTriggeredAt: number | null
  lastCompletedAt: number | null
}

export interface DreamingDay {
  day: string
  jobs: number
  completed: number
  skipped: number
  failed: number
  medianRuntimeMs: number | null
  p95RuntimeMs: number | null
}

export interface DreamingUser {
  email: string
  displayName: string | null
  runs: number
  completed: number
  skipped: number
  failed: number
  avgRuntimeMs: number | null
  p95RuntimeMs: number | null
  lastStartedAt: number | null
  lastOutcome: string | null
}

export interface DreamingJob {
  id: string
  sweepId: string
  scope: "company" | "personal"
  owner: string | null
  displayName: string | null
  status: string
  outcome: string | null
  attempt: number
  queuedAt: number | null
  startedAt: number | null
  completedAt: number | null
  runtimeMs: number | null
  error: string | null
}

export interface DreamingSweep {
  id: string
  source: string
  status: string
  activeUsers: number
  planned: number
  completed: number
  skipped: number
  failed: number
  requestedAt: number | null
  startedAt: number | null
  completedAt: number | null
  runtimeMs: number | null
  error: string | null
}

export interface DreamingSnapshot {
  detected: boolean
  settings: DreamingSettings | null
  overview: DreamingOverview
  daily: DreamingDay[]
  users: DreamingUser[]
  jobs: DreamingJob[]
  sweeps: DreamingSweep[]
  error: string | null
}

interface QueryOk {
  ok: true
  rows: Array<Record<string, unknown>>
}
interface QueryErr {
  ok: false
  error: string
}

const RANGE_DAYS: Record<DreamingRange, number> = { "7d": 7, "30d": 30, "90d": 90 }

async function query(connectionId: string, sql: string, rowLimit = 500): Promise<QueryOk | QueryErr> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, poolLane: "meta" }),
    })
    return (await res.json()) as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

function bool(value: unknown): boolean {
  return value === true || value === "t" || value === "true"
}
function num(value: unknown): number {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}
function nullableNum(value: unknown): number | null {
  if (value == null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
function epoch(value: unknown): number | null {
  if (value == null) return null
  const parsed = new Date(String(value)).getTime()
  return Number.isFinite(parsed) ? parsed : null
}
function text(value: unknown): string | null {
  if (value == null) return null
  const result = String(value).trim()
  return result || null
}

const EMPTY_OVERVIEW: DreamingOverview = {
  sweeps: 0,
  successfulSweeps: 0,
  partialSweeps: 0,
  failedSweeps: 0,
  jobs: 0,
  completedJobs: 0,
  skippedJobs: 0,
  failedJobs: 0,
  queuedJobs: 0,
  runningJobs: 0,
  avgRuntimeMs: null,
  p95RuntimeMs: null,
  lastTriggeredAt: null,
  lastCompletedAt: null,
}

export async function fetchDreamingSnapshot(
  connectionId: string,
  range: DreamingRange,
): Promise<DreamingSnapshot> {
  const detected = await query(
    connectionId,
    "SELECT to_regclass('rvbbit.calliope_dream_sweeps') IS NOT NULL AS detected",
    1,
  )
  if (!detected.ok) return emptySnapshot(detected.error)
  if (!bool(detected.rows[0]?.detected)) return emptySnapshot(null)

  const days = RANGE_DAYS[range]
  const since = `now()-interval '${days} days'`
  const [settingsResult, overviewResult, dailyResult, usersResult, jobsResult, sweepsResult] = await Promise.all([
    query(connectionId, "SELECT * FROM rvbbit.calliope_dream_settings WHERE singleton", 1),
    query(connectionId, `
      WITH sweep_stats AS (
        SELECT count(*)::int AS sweeps,
               count(*) FILTER (WHERE status='complete')::int AS successful_sweeps,
               count(*) FILTER (WHERE status='partial')::int AS partial_sweeps,
               count(*) FILTER (WHERE status='failed')::int AS failed_sweeps,
               max(requested_at) AS last_triggered_at,
               max(completed_at) AS last_completed_at
          FROM rvbbit.calliope_dream_sweeps WHERE requested_at>=${since}
      ), job_stats AS (
        SELECT count(*)::int AS jobs,
               count(*) FILTER (WHERE status='complete')::int AS completed_jobs,
               count(*) FILTER (WHERE status='skipped')::int AS skipped_jobs,
               count(*) FILTER (WHERE status='failed')::int AS failed_jobs,
               count(*) FILTER (WHERE status='pending')::int AS queued_jobs,
               count(*) FILTER (WHERE status='running')::int AS running_jobs,
               avg(extract(epoch FROM (completed_at-started_at))*1000)
                 FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS avg_runtime_ms,
               percentile_cont(0.95) WITHIN GROUP (
                 ORDER BY extract(epoch FROM (completed_at-started_at))*1000
               ) FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS p95_runtime_ms
          FROM rvbbit.calliope_dream_jobs WHERE queued_at>=${since}
      ) SELECT * FROM sweep_stats CROSS JOIN job_stats`, 1),
    query(connectionId, `
      SELECT date_trunc('day',queued_at)::date::text AS day,
             count(*)::int AS jobs,
             count(*) FILTER (WHERE status='complete')::int AS completed,
             count(*) FILTER (WHERE status='skipped')::int AS skipped,
             count(*) FILTER (WHERE status='failed')::int AS failed,
             percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM (completed_at-started_at))*1000
             ) FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS median_runtime_ms,
             percentile_cont(0.95) WITHIN GROUP (
               ORDER BY extract(epoch FROM (completed_at-started_at))*1000
             ) FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS p95_runtime_ms
        FROM rvbbit.calliope_dream_jobs WHERE queued_at>=${since}
       GROUP BY 1 ORDER BY 1`, 100),
    query(connectionId, `
      SELECT owner_email AS email,
             max(nullif(metadata->>'display_name','')) AS display_name,
             count(*)::int AS runs,
             count(*) FILTER (WHERE status='complete')::int AS completed,
             count(*) FILTER (WHERE status='skipped')::int AS skipped,
             count(*) FILTER (WHERE status='failed')::int AS failed,
             avg(extract(epoch FROM (completed_at-started_at))*1000)
               FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS avg_runtime_ms,
             percentile_cont(0.95) WITHIN GROUP (
               ORDER BY extract(epoch FROM (completed_at-started_at))*1000
             ) FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL) AS p95_runtime_ms,
             max(started_at) AS last_started_at,
             (array_agg(outcome ORDER BY queued_at DESC) FILTER (WHERE outcome IS NOT NULL))[1] AS last_outcome
        FROM rvbbit.calliope_dream_jobs
       WHERE scope_kind='personal' AND queued_at>=${since}
       GROUP BY owner_email ORDER BY avg_runtime_ms DESC NULLS LAST,owner_email`, 500),
    query(connectionId, `
      SELECT id::text,sweep_id::text,scope_kind,owner_email,
             nullif(metadata->>'display_name','') AS display_name,status,outcome,attempt,
             queued_at,started_at,completed_at,
             extract(epoch FROM (completed_at-started_at))*1000 AS runtime_ms,
             left(error,500) AS error
        FROM rvbbit.calliope_dream_jobs WHERE queued_at>=${since}
       ORDER BY queued_at DESC LIMIT 200`, 200),
    query(connectionId, `
      SELECT id::text,source,status,active_user_count,planned_job_count,
             completed_job_count,skipped_job_count,failed_job_count,
             requested_at,started_at,completed_at,
             extract(epoch FROM (completed_at-started_at))*1000 AS runtime_ms,
             left(error,500) AS error
        FROM rvbbit.calliope_dream_sweeps WHERE requested_at>=${since}
       ORDER BY requested_at DESC LIMIT 50`, 50),
  ])

  const errors = [settingsResult, overviewResult, dailyResult, usersResult, jobsResult, sweepsResult]
    .filter((result): result is QueryErr => !result.ok)
    .map((result) => result.error)
  const settingsRow = settingsResult.ok ? settingsResult.rows[0] : null
  const overviewRow = overviewResult.ok ? overviewResult.rows[0] : null

  return {
    detected: true,
    error: errors[0] ?? null,
    settings: settingsRow ? {
      processingPaused: bool(settingsRow.processing_paused),
      companyEnabled: bool(settingsRow.company_enabled),
      personalEnabled: bool(settingsRow.personal_enabled),
      activeWindowDays: num(settingsRow.active_window_days),
      minChatTurns: num(settingsRow.min_chat_turns),
      minToolCalls: num(settingsRow.min_tool_calls),
      maxPersonalUsers: num(settingsRow.max_personal_users),
      telemetryRetentionDays: num(settingsRow.telemetry_retention_days),
      updatedBy: text(settingsRow.updated_by),
      updatedAt: epoch(settingsRow.updated_at),
    } : null,
    overview: overviewRow ? {
      sweeps: num(overviewRow.sweeps),
      successfulSweeps: num(overviewRow.successful_sweeps),
      partialSweeps: num(overviewRow.partial_sweeps),
      failedSweeps: num(overviewRow.failed_sweeps),
      jobs: num(overviewRow.jobs),
      completedJobs: num(overviewRow.completed_jobs),
      skippedJobs: num(overviewRow.skipped_jobs),
      failedJobs: num(overviewRow.failed_jobs),
      queuedJobs: num(overviewRow.queued_jobs),
      runningJobs: num(overviewRow.running_jobs),
      avgRuntimeMs: nullableNum(overviewRow.avg_runtime_ms),
      p95RuntimeMs: nullableNum(overviewRow.p95_runtime_ms),
      lastTriggeredAt: epoch(overviewRow.last_triggered_at),
      lastCompletedAt: epoch(overviewRow.last_completed_at),
    } : EMPTY_OVERVIEW,
    daily: dailyResult.ok ? dailyResult.rows.map((row) => ({
      day: String(row.day ?? ""), jobs: num(row.jobs), completed: num(row.completed),
      skipped: num(row.skipped), failed: num(row.failed),
      medianRuntimeMs: nullableNum(row.median_runtime_ms), p95RuntimeMs: nullableNum(row.p95_runtime_ms),
    })) : [],
    users: usersResult.ok ? usersResult.rows.map((row) => ({
      email: String(row.email ?? ""), displayName: text(row.display_name), runs: num(row.runs),
      completed: num(row.completed), skipped: num(row.skipped), failed: num(row.failed),
      avgRuntimeMs: nullableNum(row.avg_runtime_ms), p95RuntimeMs: nullableNum(row.p95_runtime_ms),
      lastStartedAt: epoch(row.last_started_at), lastOutcome: text(row.last_outcome),
    })) : [],
    jobs: jobsResult.ok ? jobsResult.rows.map((row) => ({
      id: String(row.id ?? ""), sweepId: String(row.sweep_id ?? ""),
      scope: String(row.scope_kind) === "personal" ? "personal" : "company",
      owner: text(row.owner_email), displayName: text(row.display_name), status: String(row.status ?? ""),
      outcome: text(row.outcome), attempt: num(row.attempt), queuedAt: epoch(row.queued_at),
      startedAt: epoch(row.started_at), completedAt: epoch(row.completed_at),
      runtimeMs: nullableNum(row.runtime_ms), error: text(row.error),
    })) : [],
    sweeps: sweepsResult.ok ? sweepsResult.rows.map((row) => ({
      id: String(row.id ?? ""), source: String(row.source ?? ""), status: String(row.status ?? ""),
      activeUsers: num(row.active_user_count), planned: num(row.planned_job_count),
      completed: num(row.completed_job_count), skipped: num(row.skipped_job_count),
      failed: num(row.failed_job_count), requestedAt: epoch(row.requested_at),
      startedAt: epoch(row.started_at), completedAt: epoch(row.completed_at),
      runtimeMs: nullableNum(row.runtime_ms), error: text(row.error),
    })) : [],
  }
}

function emptySnapshot(error: string | null): DreamingSnapshot {
  return { detected: false, settings: null, overview: EMPTY_OVERVIEW, daily: [], users: [], jobs: [], sweeps: [], error }
}

export async function saveDreamingSettings(
  connectionId: string,
  settings: DreamingSettings,
): Promise<{ ok: boolean; error: string | null }> {
  const sql = `UPDATE rvbbit.calliope_dream_settings SET
    processing_paused=${settings.processingPaused ? "true" : "false"},
    company_enabled=${settings.companyEnabled ? "true" : "false"},
    personal_enabled=${settings.personalEnabled ? "true" : "false"},
    active_window_days=${Math.max(7, Math.min(90, Math.round(settings.activeWindowDays)))},
    min_chat_turns=${Math.max(1, Math.min(20, Math.round(settings.minChatTurns)))},
    min_tool_calls=${Math.max(1, Math.min(50, Math.round(settings.minToolCalls)))},
    max_personal_users=${Math.max(1, Math.min(500, Math.round(settings.maxPersonalUsers)))},
    telemetry_retention_days=${Math.max(14, Math.min(730, Math.round(settings.telemetryRetentionDays)))},
    updated_by='datarabbit',updated_at=now()
  WHERE singleton`
  const result = await query(connectionId, sql, 1)
  return result.ok ? { ok: true, error: null } : { ok: false, error: result.error }
}

export async function enqueueDreamSweep(
  connectionId: string,
): Promise<{ ok: boolean; id: string | null; error: string | null }> {
  const result = await query(
    connectionId,
    "SELECT rvbbit.calliope_dream_enqueue('manual','datarabbit',true)::text AS id",
    1,
  )
  if (!result.ok) return { ok: false, id: null, error: result.error }
  return { ok: true, id: text(result.rows[0]?.id), error: null }
}
