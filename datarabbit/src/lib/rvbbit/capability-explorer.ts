/**
 * Data layer for the Capability Explorer window — browses every entry in
 * the rvbbit_capabilities knowledge graph (the same graph
 * rvbbit.capability_search() searches): operators, metrics, cubes, packs,
 * document brains, alerts, MCP connector tools, Colony peer backends, and
 * curated doctrine/pattern entries. Mirrors alerts.ts/colony.ts's shape: a
 * run() POST to /api/db/query, a q() literal builder, {ok,...}|{ok:false,
 * error} returns, no throws.
 *
 * Plain browsing reads rvbbit.kg_nodes directly (kind, label, properties)
 * — capability_search()/data_search() only return {kind,name,score,doc},
 * never the rich per-kind properties, and require a query string (they're
 * a similarity/KNN search, not a "list everything" call). Semantic search
 * mode uses capability_search() itself, the thing this graph exists for.
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

async function run(connectionId: string, sql: string, rowLimit = 2000): Promise<Ok | Err> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true }),
    })
    return (await res.json()) as Ok | Err
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/** Postgres single-quoted literal (the query API has no bind params). */
function q(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

function num(v: unknown): number | null {
  return v == null ? null : Number(v)
}
function obj(v: unknown): Record<string, unknown> {
  if (v == null) return {}
  if (typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>
  if (typeof v === "string") {
    try {
      const p = JSON.parse(v)
      return p && typeof p === "object" ? (p as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }
  return {}
}

// Real entries only — db_table_ref/model/provider are internal lineage-edge
// nodes (operator→model, metric→table), not user-facing capabilities.
export const CAP_KINDS = [
  "cap_syntax",
  "cap_operator",
  "cap_metric",
  "cap_cube",
  "cap_pack",
  "cap_brain",
  "cap_alert",
  "cap_mcp_tool",
  "cap_peer_backend",
] as const
export type CapKind = (typeof CAP_KINDS)[number]

export const KIND_LABELS: Record<CapKind, string> = {
  cap_syntax: "Patterns & Doctrine",
  cap_operator: "Semantic Operators",
  cap_metric: "Metrics",
  cap_cube: "Cubes",
  cap_pack: "Capability Packs",
  cap_brain: "Document Brains",
  cap_alert: "Alerts",
  cap_mcp_tool: "MCP Tools (external)",
  cap_peer_backend: "Peer Capabilities (Colony)",
}

export interface CapabilityNode {
  kind: CapKind
  name: string
  properties: Record<string, unknown>
  confidence: number
  createdMs: number | null
  updatedMs: number | null
}

export async function fetchCapabilityNodes(
  connectionId: string,
): Promise<{ nodes: CapabilityNode[]; error: string | null }> {
  const kindsList = CAP_KINDS.map((k) => q(k)).join(", ")
  const r = await run(
    connectionId,
    `SELECT kind, label AS name, properties, confidence,
            extract(epoch FROM created_at) * 1000 AS created_ms,
            extract(epoch FROM updated_at) * 1000 AS updated_ms
     FROM rvbbit.kg_nodes
     WHERE graph_id = 'rvbbit_capabilities' AND kind IN (${kindsList})
     ORDER BY kind, label`,
  )
  if (!r.ok) return { nodes: [], error: r.error }
  const nodes: CapabilityNode[] = r.rows.map((row) => ({
    kind: String(row.kind) as CapKind,
    name: String(row.name),
    properties: obj(row.properties),
    confidence: num(row.confidence) ?? 1,
    createdMs: num(row.created_ms),
    updatedMs: num(row.updated_ms),
  }))
  return { nodes, error: null }
}

export interface CapabilitySearchHit {
  kind: string
  name: string
  score: number
  doc: string
}

export async function searchCapabilities(
  connectionId: string,
  query: string,
  k = 20,
): Promise<{ hits: CapabilitySearchHit[]; error: string | null }> {
  const r = await run(
    connectionId,
    `SELECT kind, name, score, doc FROM rvbbit.capability_search(${q(query)}, ${Math.max(1, Math.min(100, k))}, NULL)`,
  )
  if (!r.ok) return { hits: [], error: r.error }
  const hits: CapabilitySearchHit[] = r.rows.map((row) => ({
    kind: String(row.kind),
    name: String(row.name),
    score: num(row.score) ?? 0,
    doc: String(row.doc ?? ""),
  }))
  return { hits, error: null }
}

/** Pull the `example: ...` tail rvbbit.capability_doc() appends as the LAST
 *  section when an example was supplied — reliable because that function's
 *  format is fixed (crates/pg_rvbbit/sql/migrations/0147_capability_kg.sql). */
function extractExample(doc: string): string | null {
  const m = doc.match(/\nexample:\s*([\s\S]*)$/)
  const text = m?.[1]?.trim()
  return text ? text : null
}

function asSql(text: string): string {
  return text.endsWith(";") ? text : `${text};`
}

/** Double-quoted Postgres identifier — cube names are used directly as a
 *  FROM target, and quote_ident-style safety costs nothing here. */
function quoteIdent(name: string): string {
  return `"${name.replace(/"/g, '""')}"`
}

export type StatusTone = "good" | "warn" | "neutral"
export interface CapabilityStatus {
  label: string
  tone: StatusTone
}

/** Configured/enabled state, for kinds that genuinely have one — a pack can
 *  be installed or merely cataloged (e.g. "Apollo" is a real pack entry but
 *  nothing on this system has installed/configured it yet), a peer backend
 *  can be paused or offline, a brain or alert can be disabled/paused. Kinds
 *  that are always "just there" once they exist (operator/metric/cube/
 *  syntax/mcp_tool — no install step, no on/off switch) return null: no
 *  badge is more honest than a redundant "active" tag on everything. */
export function deriveStatus(node: CapabilityNode): CapabilityStatus | null {
  switch (node.kind) {
    case "cap_pack":
      return node.properties.install_state === "installed"
        ? { label: "Installed", tone: "good" }
        : { label: "Not installed", tone: "neutral" }
    case "cap_peer_backend": {
      if (node.properties.enabled === false) return { label: "Paused", tone: "neutral" }
      return node.properties.live === true ? { label: "Live", tone: "good" } : { label: "Offline", tone: "warn" }
    }
    case "cap_brain":
      return node.properties.enabled === false
        ? { label: "Disabled", tone: "neutral" }
        : { label: "Enabled", tone: "good" }
    case "cap_alert":
      return node.properties.enabled === false
        ? { label: "Paused", tone: "neutral" }
        : { label: "Active", tone: "good" }
    default:
      return null
  }
}

export type TryIt = { sql: string } | { note: string }

/** What clicking "Try it" should do for this node, per kind — a real
 *  runnable statement where one exists (metric/cube are always exact; an
 *  extracted `example:` covers infix operators and every cap_syntax
 *  pattern), a clearly-a-template placeholder where a real call needs
 *  values only the caller knows (operator args, an MCP tool name), or a
 *  plain explanation for kinds that were never meant to be called ad hoc
 *  (packs, alerts, external MCP connector tools). */
export function resolveTryIt(node: CapabilityNode): TryIt | null {
  const doc = typeof node.properties.search_doc === "string" ? (node.properties.search_doc as string) : ""
  switch (node.kind) {
    case "cap_peer_backend": {
      const isMcp = node.properties.peer_kind === "mcp"
      const payload = isMcp ? '\'{"tool": "REPLACE_WITH_TOOL_NAME", "args": {}}\'' : '\'{"user": "hello"}\''
      return { sql: `SELECT rvbbit.call_specialist(${q(node.name)}, ${payload}::jsonb);` }
    }
    case "cap_metric":
      return { sql: `SELECT rvbbit.metric(${q(node.name)});` }
    case "cap_cube":
      return { sql: `SELECT * FROM ${quoteIdent(node.name)} LIMIT 50;` }
    case "cap_operator": {
      const example = extractExample(doc)
      if (example) return { sql: asSql(example) }
      const signature = typeof node.properties.signature === "string" ? (node.properties.signature as string) : node.name
      return { sql: `-- ${signature}\nSELECT rvbbit.${node.name}(...);` }
    }
    case "cap_syntax": {
      const example = extractExample(doc)
      return example ? { sql: asSql(example) } : null
    }
    case "cap_brain":
      return { sql: `SELECT rvbbit.ask_brain('you@example.com', 'your question about ${node.name.replace(/'/g, "\\'")}');` }
    case "cap_pack":
      return { note: "Packs are install units, not a single call — open the Capabilities window to install or inspect it." }
    case "cap_alert":
      return { note: "Alerts run on a schedule via the sweeper — open the Alerts window to inspect or test its condition." }
    case "cap_mcp_tool":
      return { note: "External-agent only — reachable via the Warehouse MCP connector, not a local SQL call." }
    default:
      return null
  }
}
