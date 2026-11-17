"use client"

interface QueryOk { ok: true; rows: Record<string, unknown>[] }
interface QueryErr { ok: false; error: string }

async function query(connectionId: string, sql: string, rowLimit = 1000, readOnly = true): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly }),
    })
    return await response.json() as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

function lit(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

function dateLit(s: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) throw new Error(`bad date: ${s}`)
  return `'${s}'::date`
}

export interface LedgerCredit {
  class: string
  label: string
  minutes_each: number
  sort: number
}

export interface LedgerRate {
  owner_email: string
  hourly_usd: number
  excluded: boolean
}

export interface LedgerClassRow {
  class: string
  label: string
  minutes_each: number
  count: number
  hours: number
}

export interface LedgerMemberRow {
  owner_email: string
  count: number
  hours: number
  hourly_usd: number
  value_usd: number
  rate_is_default: boolean
}

export interface LedgerSnapshot {
  id: string
  period_start: string
  period_end: string
  generated_by: string | null
  generated_at: string
  total_hours: number
  total_value_usd: number
  members: number
  report: Record<string, unknown>
}

export interface WorkLedgerState {
  ready: boolean
  error: string | null
  credits: LedgerCredit[]
  rates: LedgerRate[]
  classes: LedgerClassRow[]
  members: LedgerMemberRow[]
  totalHours: number
  totalValueUsd: number
  defaultHourlyUsd: number
  snapshots: LedgerSnapshot[]
}

const READY_SQL = "SELECT to_regclass('rvbbit.work_ledger_v') IS NOT NULL AS ready"

const CREDITS_SQL = `
  SELECT class, label, minutes_each, sort
  FROM rvbbit.work_ledger_credits ORDER BY sort`

const RATES_SQL = `
  SELECT owner_email, hourly_usd::float8 AS hourly_usd, excluded
  FROM rvbbit.work_ledger_rates ORDER BY (owner_email = '*') DESC, owner_email`

const SNAPSHOTS_SQL = `
  SELECT id::text, period_start::text, period_end::text, generated_by, generated_at::text,
         coalesce((report->>'total_hours')::float8, 0)      AS total_hours,
         coalesce((report->>'total_value_usd')::float8, 0)  AS total_value_usd,
         coalesce((report->>'members')::int, 0)             AS members,
         report
  FROM rvbbit.work_ledger_snapshots
  ORDER BY generated_at DESC LIMIT 40`

function classesSql(start: string, end: string): string {
  return `
  WITH scoped AS (
    SELECT v.* FROM rvbbit.work_ledger_v v
    LEFT JOIN rvbbit.work_ledger_rates r ON r.owner_email = v.owner_email
    WHERE v.occurred_at >= ${dateLit(start)} AND v.occurred_at < ${dateLit(end)} + 1
      AND NOT coalesce(r.excluded, false)
  )
  SELECT c.class, c.label, c.minutes_each, count(s.*)::int AS count,
         round(count(s.*) * c.minutes_each / 60.0, 1)::float8 AS hours
  FROM rvbbit.work_ledger_credits c
  LEFT JOIN scoped s ON s.class = c.class
  GROUP BY c.class, c.label, c.minutes_each, c.sort
  ORDER BY c.sort`
}

function membersSql(start: string, end: string): string {
  return `
  WITH scoped AS (
    SELECT v.* FROM rvbbit.work_ledger_v v
    LEFT JOIN rvbbit.work_ledger_rates r ON r.owner_email = v.owner_email
    WHERE v.occurred_at >= ${dateLit(start)} AND v.occurred_at < ${dateLit(end)} + 1
      AND NOT coalesce(r.excluded, false)
  ), default_rate AS (
    SELECT hourly_usd FROM rvbbit.work_ledger_rates WHERE owner_email = '*'
  )
  SELECT s.owner_email, count(*)::int AS count,
         round(sum(c.minutes_each) / 60.0, 1)::float8 AS hours,
         coalesce(r.hourly_usd, (SELECT hourly_usd FROM default_rate))::float8 AS hourly_usd,
         round(sum(c.minutes_each) / 60.0
               * coalesce(r.hourly_usd, (SELECT hourly_usd FROM default_rate)), 0)::float8 AS value_usd,
         (r.hourly_usd IS NULL) AS rate_is_default
  FROM scoped s
  JOIN rvbbit.work_ledger_credits c ON c.class = s.class
  LEFT JOIN rvbbit.work_ledger_rates r ON r.owner_email = s.owner_email
  GROUP BY s.owner_email, r.hourly_usd
  ORDER BY hours DESC`
}

export async function fetchWorkLedger(
  connectionId: string,
  periodStart: string,
  periodEnd: string,
): Promise<WorkLedgerState> {
  const empty: WorkLedgerState = {
    ready: false, error: null, credits: [], rates: [], classes: [], members: [],
    totalHours: 0, totalValueUsd: 0, defaultHourlyUsd: 45, snapshots: [],
  }
  const probe = await query(connectionId, READY_SQL, 1)
  if (!probe.ok) return { ...empty, error: probe.error }
  if (!(probe.rows[0]?.ready === true)) return empty

  const [credits, rates, classes, members, snapshots] = await Promise.all([
    query(connectionId, CREDITS_SQL, 100),
    query(connectionId, RATES_SQL, 500),
    query(connectionId, classesSql(periodStart, periodEnd), 100),
    query(connectionId, membersSql(periodStart, periodEnd), 500),
    query(connectionId, SNAPSHOTS_SQL, 40),
  ])
  const firstError = [credits, rates, classes, members, snapshots]
    .find((r): r is QueryErr => !r.ok)
  if (firstError) return { ...empty, ready: true, error: firstError.error }

  const memberRows = (members as QueryOk).rows as unknown as LedgerMemberRow[]
  const rateRows = (rates as QueryOk).rows as unknown as LedgerRate[]
  return {
    ready: true,
    error: null,
    credits: (credits as QueryOk).rows as unknown as LedgerCredit[],
    rates: rateRows,
    classes: (classes as QueryOk).rows as unknown as LedgerClassRow[],
    members: memberRows,
    totalHours: memberRows.reduce((a, m) => a + Number(m.hours || 0), 0),
    totalValueUsd: memberRows.reduce((a, m) => a + Number(m.value_usd || 0), 0),
    defaultHourlyUsd: Number(rateRows.find((r) => r.owner_email === "*")?.hourly_usd ?? 45),
    snapshots: (snapshots as QueryOk).rows as unknown as LedgerSnapshot[],
  }
}

export async function saveSnapshot(
  connectionId: string,
  periodStart: string,
  periodEnd: string,
  generatedBy: string,
): Promise<{ ok: true; report: Record<string, unknown> } | { ok: false; error: string }> {
  const result = await query(
    connectionId,
    `SELECT rvbbit.work_ledger_snapshot(${dateLit(periodStart)}, ${dateLit(periodEnd)}, ${lit(generatedBy)}) AS report`,
    1,
    false,
  )
  if (!result.ok) return { ok: false, error: result.error }
  const raw = result.rows[0]?.report
  const report = (typeof raw === "string" ? JSON.parse(raw) : raw) as Record<string, unknown>
  return { ok: true, report }
}

export async function setCreditMinutes(
  connectionId: string,
  cls: string,
  minutes: number,
): Promise<{ ok: boolean; error?: string }> {
  if (!Number.isFinite(minutes) || minutes < 0) return { ok: false, error: "minutes must be >= 0" }
  const result = await query(
    connectionId,
    `UPDATE rvbbit.work_ledger_credits
     SET minutes_each = ${Math.round(minutes)}, updated_at = now()
     WHERE class = ${lit(cls)}`,
    1,
    false,
  )
  return result.ok ? { ok: true } : { ok: false, error: result.error }
}

export async function setMemberRate(
  connectionId: string,
  ownerEmail: string,
  hourlyUsd: number,
  excluded: boolean,
): Promise<{ ok: boolean; error?: string }> {
  if (!Number.isFinite(hourlyUsd) || hourlyUsd < 0) return { ok: false, error: "rate must be >= 0" }
  const result = await query(
    connectionId,
    `INSERT INTO rvbbit.work_ledger_rates (owner_email, hourly_usd, excluded)
     VALUES (${lit(ownerEmail)}, ${hourlyUsd}, ${excluded})
     ON CONFLICT (owner_email)
     DO UPDATE SET hourly_usd = EXCLUDED.hourly_usd, excluded = EXCLUDED.excluded, updated_at = now()`,
    1,
    false,
  )
  return result.ok ? { ok: true } : { ok: false, error: result.error }
}

export async function clearMemberRate(
  connectionId: string,
  ownerEmail: string,
): Promise<{ ok: boolean; error?: string }> {
  if (ownerEmail === "*") return { ok: false, error: "the default rate row cannot be removed" }
  const result = await query(
    connectionId,
    `DELETE FROM rvbbit.work_ledger_rates WHERE owner_email = ${lit(ownerEmail)}`,
    1,
    false,
  )
  return result.ok ? { ok: true } : { ok: false, error: result.error }
}
