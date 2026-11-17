"use client"

// Spark host observability (0396): the Floor arrival band's substrate as
// warehouse data. Read-only eyeball surface — sparks are minted by
// rvbbit.spark_host_tick() (pg_cron) and dismissed/entered from the Floor.

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

export interface SparkKindStats {
  kind: string
  live: number
  entered: number
  hidden: number
  expired: number
  retired: number
  impressions: number
}

export interface SparkRow {
  id: string
  kind: string
  owner_email: string | null
  kicker: string
  title: string
  state: string
  impressions: number
  created_by: string
  created_at: string | null
  valid_until: string | null
  hidden_reason: string | null
}

export interface SparkOwnerRow {
  owner_email: string
  live: number
  entered: number
  hidden: number
}

export interface SparkSnapshot {
  ready: boolean
  kinds: SparkKindStats[]
  recent: SparkRow[]
  owners: SparkOwnerRow[]
  error?: string
}

const n = (v: unknown) => (v == null ? 0 : Number(v))

export async function fetchSparkSnapshot(connectionId: string): Promise<SparkSnapshot> {
  const probe = await query(
    connectionId,
    "SELECT to_regclass('rvbbit.calliope_floor_sparks') IS NOT NULL AS ready",
    1,
  )
  if (!probe.ok) return { ready: false, kinds: [], recent: [], owners: [], error: probe.error }
  if (!probe.rows[0]?.ready) return { ready: false, kinds: [], recent: [], owners: [] }

  const [kinds, recent, owners] = await Promise.all([
    query(connectionId, `
      SELECT kind,
             count(*) FILTER (WHERE state='live')    AS live,
             count(*) FILTER (WHERE state='entered') AS entered,
             count(*) FILTER (WHERE state='hidden')  AS hidden,
             count(*) FILTER (WHERE state='expired') AS expired,
             count(*) FILTER (WHERE state='retired') AS retired,
             coalesce(sum(impressions),0)            AS impressions
        FROM rvbbit.calliope_floor_sparks
       GROUP BY kind ORDER BY kind`),
    query(connectionId, `
      SELECT id::text, kind, owner_email, kicker, title, state, impressions,
             created_by, created_at::text, valid_until::text, hidden_reason
        FROM rvbbit.calliope_floor_sparks
       ORDER BY created_at DESC LIMIT 80`),
    query(connectionId, `
      SELECT lower(owner_email) AS owner_email,
             count(*) FILTER (WHERE state='live')    AS live,
             count(*) FILTER (WHERE state='entered') AS entered,
             count(*) FILTER (WHERE state='hidden')  AS hidden
        FROM rvbbit.calliope_floor_sparks
       WHERE owner_email IS NOT NULL
       GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 40`),
  ])
  const error = [kinds, recent, owners].find((r): r is QueryErr => !r.ok)?.error
  return {
    ready: true,
    error,
    kinds: kinds.ok ? kinds.rows.map((row) => ({
      kind: String(row.kind || ""),
      live: n(row.live), entered: n(row.entered), hidden: n(row.hidden),
      expired: n(row.expired), retired: n(row.retired), impressions: n(row.impressions),
    })) : [],
    recent: recent.ok ? recent.rows.map((row) => ({
      id: String(row.id || ""),
      kind: String(row.kind || ""),
      owner_email: row.owner_email == null ? null : String(row.owner_email),
      kicker: String(row.kicker || ""),
      title: String(row.title || ""),
      state: String(row.state || ""),
      impressions: n(row.impressions),
      created_by: String(row.created_by || ""),
      created_at: row.created_at == null ? null : String(row.created_at),
      valid_until: row.valid_until == null ? null : String(row.valid_until),
      hidden_reason: row.hidden_reason == null ? null : String(row.hidden_reason),
    })) : [],
    owners: owners.ok ? owners.rows.map((row) => ({
      owner_email: String(row.owner_email || ""),
      live: n(row.live), entered: n(row.entered), hidden: n(row.hidden),
    })) : [],
  }
}
