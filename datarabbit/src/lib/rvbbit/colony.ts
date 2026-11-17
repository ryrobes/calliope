/**
 * Data layer for the Colony window — reads rvbbit.peer_backends /
 * peer_backends_live (docs/PEER_CAPABILITIES_PLAN.md). Mirrors alerts.ts's
 * shape: a `run()` POST to /api/db/query, a `q()` literal builder,
 * `{ok,...}|{ok:false,error}` returns, no throws. Registration/pause/detach
 * go through their own dedicated /api/db/colony/* routes (they also touch
 * the local colony.json share file and the server-lifetime runner, not
 * just SQL).
 */

interface Ok {
  ok: true
  columns: { name: string }[]
  rows: Record<string, unknown>[]
}
interface Err {
  ok: false
  error: string
}

async function run(connectionId: string, sql: string, rowLimit = 1000, readOnly = true): Promise<Ok | Err> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly }),
    })
    return (await res.json()) as Ok | Err
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

function num(v: unknown): number {
  return v == null ? 0 : Number(v)
}
function str(v: unknown): string | null {
  return v == null ? null : String(v)
}

export interface PeerBackend {
  backendName: string
  kind: string
  template: string | null
  scopeRole: string
  sharedBy: string
  /** Self-reported hostname of the sharing client (0218); null from older
   *  clients — display falls back to @sharedBy. */
  clientHost: string | null
  description: string | null
  enabled: boolean
  createdMs: number | null
  live: boolean
  instanceCount: number
  minQueueDepth: number | null
  pending: number
  done: number
  failed: number
}

export async function fetchPeerBackends(connectionId: string): Promise<{ backends: PeerBackend[]; error: string | null }> {
  const r = await run(
    connectionId,
    `SELECT b.backend_name, b.kind, b.template, b.scope_role, b.shared_by,
            to_jsonb(b) ->> 'client_host' AS client_host,
            b.description, b.enabled,
            extract(epoch FROM b.created_at) * 1000 AS created_ms,
            l.instance_count, l.min_queue_depth,
            (SELECT count(*) FROM rvbbit.peer_capability_requests q WHERE q.backend_name = b.backend_name AND q.status = 'pending') AS pending,
            (SELECT count(*) FROM rvbbit.peer_capability_requests q WHERE q.backend_name = b.backend_name AND q.status = 'done') AS done,
            (SELECT count(*) FROM rvbbit.peer_capability_requests q WHERE q.backend_name = b.backend_name AND q.status = 'failed') AS failed
     FROM rvbbit.peer_backends b
     LEFT JOIN rvbbit.peer_backends_live l ON l.backend_name = b.backend_name
     ORDER BY b.created_at DESC`,
  )
  if (!r.ok) return { backends: [], error: r.error }
  const backends: PeerBackend[] = r.rows.map((row) => ({
    backendName: String(row.backend_name),
    kind: String(row.kind),
    template: str(row.template),
    scopeRole: String(row.scope_role),
    sharedBy: String(row.shared_by),
    clientHost: str(row.client_host),
    description: str(row.description),
    enabled: row.enabled === true || row.enabled === "t",
    createdMs: row.created_ms == null ? null : num(row.created_ms),
    live: row.instance_count != null && num(row.instance_count) > 0,
    instanceCount: num(row.instance_count),
    minQueueDepth: row.min_queue_depth == null ? null : num(row.min_queue_depth),
    pending: num(row.pending),
    done: num(row.done),
    failed: num(row.failed),
  }))
  return { backends, error: null }
}

export async function fetchScopeRoleOptions(connectionId: string): Promise<{ roles: string[]; error: string | null }> {
  const r = await run(
    connectionId,
    `SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolname = current_user ORDER BY rolname`,
  )
  if (!r.ok) return { roles: [], error: r.error }
  return { roles: r.rows.map((row) => String(row.rolname)), error: null }
}
