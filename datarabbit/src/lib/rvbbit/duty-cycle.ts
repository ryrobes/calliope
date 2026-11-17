"use client"

// Duty-cycle observability (0379): the Hermes cron mirror as warehouse data.
// Read-only eyeball surface — remediation happens through Calliope or the
// hermes CLI, never from here.

interface QueryOk { ok: true; rows: Record<string, unknown>[] }
interface QueryErr { ok: false; error: string }

async function query(connectionId: string, sql: string, rowLimit = 500): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true }),
    })
    return await response.json() as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

function lit(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

export interface DutyCycleJob {
  profile_key: string
  job_id: string
  name: string | null
  schedule: string | null
  enabled: boolean
  provider: string | null
  model: string | null
  pinned: boolean
  last_status: string | null
  last_run_at: string | null
  last_error: string | null
  failure_streak: number
  failures_14: number
  drift_skipped: boolean
}

export interface DutyCycleExecution {
  execution_id: string
  status: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  delivery_outcome: string | null
}

export interface DutyCycleAlertItem {
  id: string
  title: string
  summary: string | null
  urgency: string
  state: string
  created_at: string
}

export interface DutyCycleState {
  ready: boolean
  error: string | null
  jobs: DutyCycleJob[]
  alerts: DutyCycleAlertItem[]
}

export async function fetchDutyCycles(connectionId: string): Promise<DutyCycleState> {
  const probe = await query(connectionId, "SELECT to_regclass('rvbbit.duty_cycle_health') IS NOT NULL AS ready", 1)
  if (!probe.ok) return { ready: false, error: probe.error, jobs: [], alerts: [] }
  if (!(probe.rows[0]?.ready === true)) return { ready: false, error: null, jobs: [], alerts: [] }
  const [health, items] = await Promise.all([
    query(connectionId, `
      SELECT profile_key, job_id, name, schedule, enabled, provider, model, pinned,
             last_status, last_run_at::text, last_error,
             failure_streak::int, failures_14::int, drift_skipped
      FROM rvbbit.duty_cycle_health
      ORDER BY failure_streak DESC, profile_key, job_id`),
    query(connectionId, `
      SELECT id::text, title, summary, urgency, state, created_at::text
      FROM rvbbit.calliope_work_items
      WHERE source = 'duty_cycle_alert' AND state IN ('unread', 'seen')
      ORDER BY created_at DESC`, 50),
  ])
  if (!health.ok) return { ready: true, error: health.error, jobs: [], alerts: [] }
  return {
    ready: true,
    error: items.ok ? null : items.error,
    jobs: health.rows as unknown as DutyCycleJob[],
    alerts: (items.ok ? items.rows : []) as unknown as DutyCycleAlertItem[],
  }
}

export async function fetchExecutions(connectionId: string, profileKey: string, jobId: string): Promise<DutyCycleExecution[]> {
  const result = await query(connectionId, `
    SELECT execution_id, status, started_at::text, finished_at::text,
           left(error, 800) AS error, delivery_outcome
    FROM rvbbit.hermes_cron_executions
    WHERE profile_key = ${lit(profileKey)} AND job_id = ${lit(jobId)}
    ORDER BY started_at DESC NULLS LAST
    LIMIT 30`)
  return result.ok ? (result.rows as unknown as DutyCycleExecution[]) : []
}
