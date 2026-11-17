"use client"

/**
 * Client-side model for rvbbit's adaptive query router — the system that
 * picks one of the configured execution paths for a SELECT against an
 * rvbbit-enabled table, based on the query's shape and a trained profile.
 *
 * Surfaces (see rvbbit docs/RVBBIT_ROUTING_UI.md):
 *   - route_decisions / route_executions — online telemetry (paths taken)
 *   - route_observations                 — forced benchmark timings (training input)
 *   - route_profiles / route_profile_entries / route_profile_points — the trained model
 *   - route_*_summary views              — pre-rolled aggregates
 *   - route_explain(sql)                 — explain a route without running the query
 */

// ── Engines ─────────────────────────────────────────────────────────

import { BUSY_RELATIONS_CTE, safeTotalSize } from "@/lib/db/relation-size"
export type EngineId =
  | "rvbbit_native"
  | "duck_vector"
  | "duck_vortex"
  | "duck_hive"
  | "datafusion_vector"
  | "datafusion_vortex"
  | "datafusion_hive"
  | "gpu_gqe"
  | "pg_rowstore"

export interface EngineMeta {
  id: EngineId
  label: string
  color: string
  blurb: string
}

/** The candidate engines, in router-display order. */
export const ENGINES: EngineMeta[] = [
  {
    id: "rvbbit_native",
    label: "native",
    color: "var(--viz-engine-native)",
    blurb: "PostgreSQL executor over rvbbit native rewrites & custom scans",
  },
  {
    id: "duck_vector",
    label: "duck",
    color: "var(--viz-engine-duck-vector)",
    blurb: "DuckDB over authoritative rvbbit parquet row groups",
  },
  {
    id: "duck_vortex",
    label: "duck vortex",
    color: "var(--viz-engine-duck-vortex)",
    blurb: "DuckDB over the Vortex columnar layout",
  },
  {
    id: "duck_hive",
    label: "duck hive",
    color: "var(--viz-engine-duck-hive)",
    blurb: "DuckDB over hive-partitioned parquet variants",
  },
  {
    id: "datafusion_vector",
    label: "datafusion",
    color: "var(--viz-engine-datafusion-vector)",
    blurb: "DataFusion over authoritative rvbbit parquet row groups",
  },
  {
    id: "datafusion_vortex",
    label: "datafusion vortex",
    color: "var(--viz-engine-datafusion-vortex)",
    blurb: "DataFusion over the Vortex columnar layout",
  },
  {
    id: "datafusion_hive",
    label: "datafusion hive",
    color: "var(--viz-engine-datafusion-hive)",
    blurb: "DataFusion over hive-partitioned parquet variants",
  },
  {
    id: "gpu_gqe",
    label: "gpu gqe",
    color: "var(--viz-engine-gpu-gqe)",
    blurb: "GPU/GQE over authoritative rvbbit parquet row groups",
  },
  {
    id: "pg_rowstore",
    label: "pg rowstore",
    color: "var(--viz-engine-pg-rowstore)",
    blurb: "PostgreSQL rowstore over a retained shadow heap",
  },
]

export function engineMeta(id: string): EngineMeta {
  if (id === "native_fallback") {
    return {
      id: id as EngineId,
      label: "native fallback",
      color: "var(--viz-engine-native)",
      blurb: "PostgreSQL/RVBBIT returned the exact result after an external engine could not serve the query",
    }
  }
  if (id === "duck_capsule") {
    // Not a routed candidate (the router never races it) — a TRANSPORT class:
    // the same DuckDB, reached through a capsule on a serverless hare. Gets
    // first-class card/flow identity without polluting the trainable set.
    return {
      id: id as EngineId,
      label: "hare capsule",
      color: "var(--viz-engine-duck-capsule, var(--main))",
      blurb: "DuckDB over presigned artifacts on a serverless hare — totals include fetch + wire",
    }
  }
  // Virtual "native·<path>" ids split the native family by the physical storage
  // actually read (heap/parquet/vortex/mixed) for the sankey + log chips. Same
  // native colour, tinted by path: heap dimmed (unaccelerated), vortex brighter.
  if (id.startsWith(NATIVE_PATH_PREFIX)) {
    const path = id.slice(NATIVE_PATH_PREFIX.length)
    const native = ENGINES.find((e) => e.id === "rvbbit_native")
    const base = native?.color ?? "var(--viz-engine-native)"
    return {
      id: id as EngineId,
      label: `native·${path}`,
      color:
        path === "heap"
          ? `color-mix(in oklch, ${base} 50%, var(--chrome-border))`
          : path === "vortex"
            ? `color-mix(in oklch, ${base} 75%, var(--foreground))`
            : base,
      blurb: native?.blurb ?? "",
    }
  }
  return (
    ENGINES.find((e) => e.id === id) ?? {
      id: id as EngineId,
      label: id || "unknown",
      color: "var(--chrome-text)",
      blurb: "",
    }
  )
}

const NATIVE_PATH_PREFIX = "native::"
const PHYS_PATH_ORDER = ["heap", "parquet", "vortex", "mixed", "hive", "mem"]

/** The native engine family (heap SeqScan, parquet/vortex custom scan) all log
 *  candidate "rvbbit_native" / "rvbbit_native_vortex"; physical_path tells them
 *  apart. Non-native engines' storage is implied by the engine itself. */
function isNativeFamily(candidate: string): boolean {
  return candidate === "rvbbit_native" || candidate === "rvbbit_native_vortex"
}

/** Sankey/chip target id: split the native family into native·heap/parquet/vortex
 *  by physical_path; everything else stays its candidate id. */
export function engineFlowTarget(candidate: string, physicalPath: string): string {
  return isNativeFamily(candidate) && physicalPath
    ? `${NATIVE_PATH_PREFIX}${physicalPath}`
    : candidate
}

/** Stable ordering for sankey target nodes: by base-engine order, native
 *  sub-paths grouped together in physical-path order. */
export function engineFlowOrder(id: string): [number, number] {
  if (id.startsWith(NATIVE_PATH_PREFIX)) {
    const nativeIdx = ENGINES.findIndex((e) => e.id === "rvbbit_native")
    const p = PHYS_PATH_ORDER.indexOf(id.slice(NATIVE_PATH_PREFIX.length))
    return [nativeIdx < 0 ? 999 : nativeIdx, p < 0 ? 99 : p + 1]
  }
  const idx = ENGINES.findIndex((e) => e.id === id)
  return [idx < 0 ? 999 : idx, 0]
}

/**
 * Older candidate aliases (`duck`, `datafusion`, `native`, `df_hive`,
 * `df_vortex`, `pg_heap`) may surface in legacy profile JSON or scripts.
 * Normalize to the canonical engine ids before storing in UI state.
 */
const CANDIDATE_ALIASES: Record<string, EngineId> = {
  duck: "duck_vector",
  datafusion: "datafusion_vector",
  "datafusion-vortex": "datafusion_vortex",
  df_vortex: "datafusion_vortex",
  vortex: "datafusion_vortex",
  gqe: "gpu_gqe",
  "gpu-gqe": "gpu_gqe",
  rvbbit_gpu_gqe: "gpu_gqe",
  gpu_gqe_forced: "gpu_gqe",
  rvbbit_gpu_gqe_forced: "gpu_gqe",
  native: "rvbbit_native",
  df_hive: "datafusion_hive",
  pg_heap: "pg_rowstore",
}

export function normalizeCandidate(s: string | null | undefined): string {
  if (!s) return ""
  return CANDIDATE_ALIASES[s] ?? s
}

/** A per-engine timing map. `null` = not measured / unsupported / failed. */
export type EngineMs = Record<EngineId, number | null>

function engineMsFromRow(r: Record<string, unknown>): EngineMs {
  return {
    rvbbit_native: numOrNull(r.native_ms),
    duck_vector: numOrNull(r.duck_ms),
    duck_vortex: numOrNull(r.duck_vortex_ms),
    duck_hive: numOrNull(r.duck_hive_ms),
    datafusion_vector: numOrNull(r.datafusion_ms),
    datafusion_vortex: numOrNull(r.datafusion_vortex_ms),
    datafusion_hive: numOrNull(r.datafusion_hive_ms),
    gpu_gqe: numOrNull(r.gpu_gqe_ms),
    pg_rowstore: numOrNull(r.pg_ms),
  }
}

function engineMediansFromRow(r: Record<string, unknown>): EngineMs {
  return {
    rvbbit_native: numOrNull(r.native_median_ms),
    duck_vector: numOrNull(r.duck_median_ms),
    duck_vortex: numOrNull(r.duck_vortex_median_ms),
    duck_hive: numOrNull(r.duck_hive_median_ms),
    datafusion_vector: numOrNull(r.datafusion_median_ms),
    datafusion_vortex: numOrNull(r.datafusion_vortex_median_ms),
    datafusion_hive: numOrNull(r.datafusion_hive_median_ms),
    gpu_gqe: numOrNull(r.gpu_gqe_median_ms),
    pg_rowstore: numOrNull(r.pg_median_ms),
  }
}

function engineObservationsFromRow(r: Record<string, unknown>): Record<EngineId, number | null> {
  return {
    rvbbit_native: numOrNull(r.native_observations),
    duck_vector: numOrNull(r.duck_observations),
    duck_vortex: numOrNull(r.duck_vortex_observations),
    duck_hive: numOrNull(r.duck_hive_observations),
    datafusion_vector: numOrNull(r.datafusion_observations),
    datafusion_vortex: numOrNull(r.datafusion_vortex_observations),
    datafusion_hive: numOrNull(r.datafusion_hive_observations),
    gpu_gqe: numOrNull(r.gpu_gqe_observations),
    pg_rowstore: numOrNull(r.pg_observations),
  }
}

// ── Row types ───────────────────────────────────────────────────────

export interface RouteExecution {
  executedAt: number
  /** Engine that actually returned rows. */
  candidate: string
  /** Candidate selected by the router before execution/fail-open. */
  requestedCandidate: string
  /** External-engine rejection detail when the actual path was native fallback. */
  fallbackReason: string
  /** Physical storage actually read (heap|parquet|vortex|hive|mem|mixed) — splits
   *  the native family that `candidate` collapses to "native". "" if unreported. */
  physicalPath: string
  routeSource: string
  elapsedMs: number
  rowsReturned: number
  cacheHit: boolean
  status: string
  shapeFamily: string
  reason: string
  /** Stable hash of the exact SQL — the apples-to-apples benchmark filter key. */
  queryHash: string
  /** Representative SQL: the shape's captured sample for routed executions,
   *  the literal statement for hare invocations. */
  sql: string
}

export interface DecisionSummaryRow {
  candidate: string
  /** Physical storage read (heap|parquet|vortex|…); splits native in the sankey. */
  physicalPath: string
  routeSource: string
  decisions: number
  cacheHits: number
  rewritten: number
}

export interface EngineRuntimeRow {
  candidate: string
  runs: number
  medianMs: number
  p95Ms: number
}

export interface LogStatus {
  enabled: boolean
  started: boolean
  scope: string
  backendPid: number
  queueLen: number
  queueCapacity: number | null
  enqueued: number | null
  dropped: number | null
  written: number | null
  writeErrors: number | null
  connectErrors: number | null
}

export interface RouteProfile {
  name: string
  active: boolean
  createdAt: string | null
  updatedAt: string | null
  version: string | null
  suite: string | null
  generatedAt: string | null
  minObservations: number | null
  minGainPct: number | null
  entryCount: number | null
  observationCount: number | null
  pointCount: number | null
  importedBy: string | null
}

export interface ProfileEntry {
  shapeKey: string
  choice: string
  confidence: number
  observations: number
  engineTimes: EngineMs
  reason: string
}

export interface ShapeSummaryRow {
  shapeFamily: string
  observations: number
  bestCandidate: string
  bestMedianMs: number | null
  observedGain: number | null
  needsExploration: boolean
  medianByEngine: EngineMs
  observationsByEngine: Record<EngineId, number | null>
}

export interface ProfilePoint {
  shapeFamily: string
  tableRows: number
  engineTimes: EngineMs
}

export interface ObservationGroup {
  source: string
  candidate: string
  count: number
  avgMs: number
}

export interface ColumnarTable {
  schema: string
  name: string
  estRows: number
}

export interface RouteExplainCandidate {
  name: string
  route: string
  reason: string
  selected: boolean
  available: boolean
}

export interface RouteExplainTable {
  table: string
  schema: string
  rows: number
  bytes: number
  rowGroups: number
  deleteCount: number
}

export interface RouteExplain {
  route: string
  reason: string
  routeSource: string
  chosenCandidate: string
  /** Physical storage actually read: heap | parquet | vortex | hive | mem | mixed.
   *  Splits the native family (heap SeqScan vs parquet/vortex custom scan) that
   *  the engine label alone collapses to "native". "" when not reported. */
  physicalPath: string
  confidence: number | null
  safeSelect: boolean
  candidates: RouteExplainCandidate[]
  features: Record<string, unknown>
  tables: RouteExplainTable[]
  postgresExplain: string
  postgresExplainRole: string
}

// ── Query plumbing ──────────────────────────────────────────────────

interface QueryOk {
  ok: true
  columns: { name: string }[]
  rows: Array<Record<string, unknown>>
}
interface QueryErr {
  ok: false
  error: string
}

interface QueryOpts {
  rowLimit?: number
  readOnly?: boolean
  statementTimeout?: number
  poolLane?: "interactive" | "meta"
}

async function runQuery(connectionId: string, sql: string, opts: QueryOpts = {}): Promise<QueryOk | QueryErr> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        connectionId,
        sql,
        rowLimit: opts.rowLimit ?? 5000,
        ...(opts.readOnly ? { readOnly: true } : {}),
        ...(opts.statementTimeout != null ? { statementTimeout: opts.statementTimeout } : {}),
        ...(opts.poolLane ? { poolLane: opts.poolLane } : {}),
      }),
    })
    return (await res.json()) as QueryOk | QueryErr
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

function num(v: unknown): number {
  return v == null ? 0 : Number(v)
}
function numOrNull(v: unknown): number | null {
  return v == null ? null : Number(v)
}
function strOrNull(v: unknown): string | null {
  return v == null || v === "" ? null : String(v)
}
function bool(v: unknown): boolean {
  return v === true || v === "t"
}
function compactCount(v: number): string {
  return Number.isFinite(v) ? new Intl.NumberFormat("en", { notation: "compact" }).format(v) : "0"
}
function epoch(v: unknown): number {
  return v ? new Date(String(v)).getTime() : 0
}
function sqlStr(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

const ACTIVE_PROFILE_SUBQUERY =
  "(SELECT name FROM rvbbit.route_profiles ORDER BY active DESC, updated_at DESC LIMIT 1)"

// ── Time window ─────────────────────────────────────────────────────
//
// The Flow tab is time-bounded, not row-capped: every chart aggregates
// the raw route_decisions / route_executions tables server-side within
// the selected window, so counts and percentiles stay accurate no matter
// how much telemetry has accrued.

export const ROUTE_WINDOW_OPTIONS = [
  { hours: 1, label: "1h" },
  { hours: 3, label: "3h" },
  { hours: 12, label: "12h" },
  { hours: 24, label: "24h" },
] as const

/** `col >= now() - interval 'N hours'`, with N clamped to a sane range. */
function windowClause(col: string, hours: number): string {
  const h = Math.max(1, Math.min(720, Math.floor(hours)))
  return `${col} >= now() - interval '${h} hours'`
}

/** Fleet-node filter fragment. "all" = every node; "local" = the brain's own
 *  engines (node IS NULL); anything else = a specific engine endpoint. Applied
 *  to route_decisions/route_executions, whose `node` column records which
 *  endpoint the dispatcher resolved (0134/0137). */
export type RoutingNodeFilter = string // "all" | "local" | endpoint
function nodeClause(filter: RoutingNodeFilter): string {
  if (!filter || filter === "all") return ""
  if (filter === "local") return " AND node IS NULL"
  return ` AND node = '${filter.replace(/'/g, "''")}'`
}

export interface RoutingNodeOption {
  node: string
  name: string | null
  healthy: boolean | null
}

/** Endpoints that can appear in the breadcrumbs: everything observed in the
 *  window plus everything registered in the fleet (tolerates pre-0136
 *  warehouses by falling back to observed-only). */
export async function fetchRoutingNodes(
  connectionId: string,
  windowHours: number,
): Promise<RoutingNodeOption[]> {
  // Observed placements come from EXECUTIONS (0141: decisions never carry a
  // node — placement is stamped at dispatch time, on the execution row).
  const observed =
    "SELECT DISTINCT node FROM rvbbit.route_executions WHERE node IS NOT NULL AND " +
    windowClause("executed_at", windowHours)
  const options: RoutingNodeOption[] = []
  const withFleet = await runQuery(
    connectionId,
    "SELECT n.node, f.name, (f.enabled AND f.last_probe_ok) AS healthy FROM (" +
      observed + " UNION SELECT endpoint FROM rvbbit.fleet_endpoints" +
      ") n LEFT JOIN rvbbit.fleet_endpoints f ON f.endpoint = n.node ORDER BY f.name NULLS LAST, n.node",
  )
  if (withFleet.ok) {
    options.push(
      ...withFleet.rows.map((r) => ({
        node: String(r.node ?? ""),
        name: r.name == null ? null : String(r.name),
        healthy: r.healthy == null ? null : Boolean(r.healthy),
      })),
    )
  } else {
    const bare = await runQuery(connectionId, observed + " ORDER BY node")
    if (bare.ok) {
      options.push(...bare.rows.map((r) => ({ node: String(r.node ?? ""), name: null, healthy: null })))
    }
  }
  // Hares are placements too — sourced from the invocation ledger, not the
  // registry (nothing persistent to register). Tolerate pre-0140 warehouses.
  const hares = await runQuery(
    connectionId,
    "SELECT DISTINCT endpoint FROM rvbbit.hare_invocations WHERE " +
      windowClause("invoked_at", windowHours) + " ORDER BY endpoint",
  )
  if (hares.ok) {
    options.push(
      ...hares.rows.map((r) => ({
        node: `hare:${String(r.endpoint ?? "")}`,
        name: hares.rows.length > 1 ? `hare ${String(r.endpoint ?? "").replace(/^https?:\/\//, "").slice(0, 18)}` : "hare",
        healthy: null,
      })),
    )
  }
  return options
}

/** `hare:<endpoint>` pills route every panel to the invocation ledger. */
function hareEndpoint(filter: RoutingNodeFilter): string | null {
  return filter && filter.startsWith("hare:") ? filter.slice(5) : null
}

/** Optional exact-query pin (apples-to-apples benchmarking): appended to any
 *  WHERE over route_executions/route_decisions (query_hash) or the hare
 *  ledger (sql_hash). */
function hashClause(column: string, queryHash?: string | null): string {
  if (!queryHash) return ""
  return ` AND ${column} = '${queryHash.replace(/'/g, "''")}'`
}

/** One row per (decision source → placement → engine) path with traffic in
 * the window — the 3-layer sankey behind the "all" pathways view. Execution-
 * weighted by necessity: only executions know where dispatch landed.
 * Placements resolve to fleet names where registered; hares fold in from the
 * invocation ledger (tolerated absent pre-0140). */
export interface FlowTripleRow {
  routeSource: string
  placement: string
  candidate: string
  physicalPath: string
  executions: number
}

export async function fetchFlowTriples(
  connectionId: string,
  windowHours: number,
  queryHash?: string | null,
): Promise<FlowTripleRow[]> {
  const res = await runQuery(
    connectionId,
    "SELECT e.route_source, " +
      "CASE WHEN e.node IS NULL THEN 'brain' ELSE coalesce(f.name, e.node) END AS placement, " +
      "coalesce(e.executed_engine, e.candidate) AS candidate, " +
      "CASE WHEN e.executed_engine = 'native_fallback' THEN '' ELSE e.route_doc->>'physical_path' END AS physical_path, count(*) AS n " +
      "FROM rvbbit.route_executions e LEFT JOIN rvbbit.fleet_endpoints f ON f.endpoint = e.node " +
      "WHERE " + windowClause("e.executed_at", windowHours) +
      " AND e.status = 'ok' AND e.candidate IS NOT NULL " +
      hashClause("e.query_hash", queryHash) +
      "GROUP BY 1, 2, 3, 4",
  )
  const rows: FlowTripleRow[] = res.ok
    ? res.rows.map((r) => ({
        routeSource: String(r.route_source ?? "unknown"),
        placement: String(r.placement ?? "brain"),
        candidate: normalizeCandidate(String(r.candidate ?? "")),
        physicalPath: String(r.physical_path ?? ""),
        executions: num(r.n),
      }))
    : []
  const hares = await runQuery(
    connectionId,
    "SELECT count(*) AS n FROM rvbbit.hare_invocations WHERE ok AND " +
      windowClause("invoked_at", windowHours) + hashClause("sql_hash", queryHash),
  )
  if (hares.ok && hares.rows.length > 0) {
    const n = num(hares.rows[0].n)
    if (n > 0) {
      rows.push({ routeSource: "hare_run", placement: "hare", candidate: "duck_capsule", physicalPath: "parquet", executions: n })
    }
  }
  return rows
}

// ── Live telemetry ──────────────────────────────────────────────────

export async function fetchRouteExecutions(
  connectionId: string,
  windowHours: number,
  nodeFilter: RoutingNodeFilter = "all",
  queryHash?: string | null,
): Promise<{ rows: RouteExecution[]; error?: string }> {
  const hare = hareEndpoint(nodeFilter)
  const res = await runQuery(
    connectionId,
    hare
      ? // Hare invocations, wearing the execution-row shape: candidate is the
        // capsule engine, route_source names the path, errors ride in reason.
        "SELECT invoked_at AS executed_at, 'duck_capsule' AS candidate, 'duck_capsule' AS requested_candidate, " +
        "'parquet' AS physical_path, 'hare_run' AS route_source, " +
        "total_ms AS elapsed_ms, row_count AS rows_returned, false AS cache_hit, " +
        "CASE WHEN ok THEN 'ok' ELSE 'error' END AS status, '' AS shape_family, " +
        "coalesce(error, '') AS reason, coalesce(error, '') AS fallback_reason, sql_hash AS query_hash, coalesce(sql, '') AS sql " +
        "FROM rvbbit.hare_invocations " +
        "WHERE " + windowClause("invoked_at", windowHours) +
        ` AND endpoint = '${hare.replace(/'/g, "''")}'` +
        hashClause("sql_hash", queryHash) +
        " ORDER BY invoked_at DESC LIMIT 500"
      : "SELECT e.executed_at, coalesce(e.executed_engine, e.candidate) AS candidate, " +
        "e.candidate AS requested_candidate, " +
        "CASE WHEN e.executed_engine = 'native_fallback' THEN '' ELSE e.route_doc->>'physical_path' END AS physical_path, " +
        "e.route_source, e.elapsed_ms, e.rows_returned, " +
        "e.cache_hit, e.status, e.shape_family, " +
        "CASE WHEN e.executed_engine = 'native_fallback' THEN coalesce(e.fallback_reason, e.reason) ELSE e.reason END AS reason, " +
        "coalesce(e.fallback_reason, '') AS fallback_reason, e.query_hash, " +
        "coalesce(s.sql, '') AS sql " +
        "FROM rvbbit.route_executions e " +
        "LEFT JOIN rvbbit.route_shape_samples s ON s.shape_key = e.shape_key " +
        "WHERE " + windowClause("e.executed_at", windowHours) + nodeClause(nodeFilter) +
        hashClause("e.query_hash", queryHash) +
        " ORDER BY e.executed_at DESC LIMIT 500",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    rows: res.rows.map((r) => ({
      executedAt: epoch(r.executed_at),
      candidate: normalizeCandidate(String(r.candidate ?? "")),
      requestedCandidate: normalizeCandidate(String(r.requested_candidate ?? r.candidate ?? "")),
      fallbackReason: String(r.fallback_reason ?? ""),
      physicalPath: String(r.physical_path ?? ""),
      routeSource: String(r.route_source ?? ""),
      elapsedMs: num(r.elapsed_ms),
      rowsReturned: num(r.rows_returned),
      cacheHit: bool(r.cache_hit),
      status: String(r.status ?? ""),
      shapeFamily: String(r.shape_family ?? ""),
      reason: String(r.reason ?? ""),
      queryHash: String(r.query_hash ?? ""),
      sql: String(r.sql ?? ""),
    })),
  }
}

/** Per-(candidate, source) decision counts within the window. Drives the
 *  flow diagram and each engine card's decision/cache totals. */
export async function fetchDecisionSummary(
  connectionId: string,
  windowHours: number,
  nodeFilter: RoutingNodeFilter = "all",
  queryHash?: string | null,
): Promise<DecisionSummaryRow[]> {
  // Decisions are made BEFORE dispatch, so they can't honestly claim a node
  // (since 0141 they no longer try — placement truth lives on executions,
  // stamped at dispatch time). "all" keeps the decision-weighted flow (the
  // whole brain's intent, cache hits included); a node pill switches the
  // SAME flow shape to execution-weighted rows from route_executions, i.e.
  // "of the work that actually ran here, how did it arrive". Both sides of
  // the source switch produce identical columns so the pathways viz is
  // agnostic to which story it's telling.
  const hare = hareEndpoint(nodeFilter)
  if (hare) {
    const r = await runQuery(
      connectionId,
      "SELECT count(*) AS decisions FROM rvbbit.hare_invocations WHERE ok AND " +
        windowClause("invoked_at", windowHours) +
        ` AND endpoint = '${hare.replace(/'/g, "''")}'`,
    )
    if (!r.ok || !r.rows.length) return []
    const n = Number(r.rows[0].decisions ?? 0)
    return n > 0
      ? [{ candidate: "duck_capsule", physicalPath: "parquet", routeSource: "hare_run", decisions: n, cacheHits: 0, rewritten: 0 }]
      : []
  }
  const filtered = nodeFilter !== "all"
  const source = filtered
    ? "FROM (SELECT *, coalesce(executed_engine, candidate) AS actual_candidate FROM rvbbit.route_executions) route_executions WHERE " + windowClause("executed_at", windowHours) + nodeClause(nodeFilter) + hashClause("query_hash", queryHash)
    : "FROM rvbbit.route_decisions WHERE " + windowClause("decided_at", windowHours) + hashClause("query_hash", queryHash)
  const res = await runQuery(
    connectionId,
    "SELECT " + (filtered ? "actual_candidate" : "candidate") + " AS candidate, " +
      "CASE WHEN " + (filtered ? "actual_candidate" : "candidate") + " = 'native_fallback' THEN '' ELSE route_doc->>'physical_path' END AS physical_path, route_source, " +
      "count(*) AS decisions, " +
      "count(*) FILTER (WHERE cache_hit) AS cache_hits, " +
      "count(*) FILTER (WHERE rewritten) AS rewritten " +
      source +
      " GROUP BY 1, 2, route_source",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    candidate: normalizeCandidate(String(r.candidate ?? "")),
    physicalPath: String(r.physical_path ?? ""),
    routeSource: String(r.route_source ?? "unknown"),
    decisions: num(r.decisions),
    cacheHits: num(r.cache_hits),
    rewritten: num(r.rewritten),
  }))
}

/** Per-engine execution count + latency percentiles within the window —
 *  computed server-side so they aren't distorted by any row cap. */
export async function fetchEngineRuntime(
  connectionId: string,
  windowHours: number,
  nodeFilter: RoutingNodeFilter = "all",
  queryHash?: string | null,
): Promise<EngineRuntimeRow[]> {
  const hare = hareEndpoint(nodeFilter)
  const res = await runQuery(
    connectionId,
    hare
      ? "SELECT 'duck_capsule' AS candidate, count(*) AS runs, " +
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) AS median_ms, " +
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95_ms " +
        "FROM rvbbit.hare_invocations WHERE ok AND " + windowClause("invoked_at", windowHours) +
        ` AND endpoint = '${hare.replace(/'/g, "''")}'` +
        hashClause("sql_hash", queryHash)
      : "SELECT coalesce(executed_engine, candidate) AS candidate, count(*) AS runs, " +
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_ms) AS median_ms, " +
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY elapsed_ms) AS p95_ms " +
        "FROM rvbbit.route_executions WHERE " + windowClause("executed_at", windowHours) + nodeClause(nodeFilter) +
        hashClause("query_hash", queryHash) +
        " GROUP BY coalesce(executed_engine, candidate)",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    candidate: normalizeCandidate(String(r.candidate ?? "")),
    runs: num(r.runs),
    medianMs: num(r.median_ms),
    p95Ms: num(r.p95_ms),
  }))
}

/** Per-engine timeseries over the window: N buckets of (runs, p50, in-flight).
 * "In-flight" counts executions whose [start, end] interval OVERLAPS the
 * bucket — the honest parallelism read (an execution spanning three buckets
 * was concurrent work in all three). Runs/p50 count only completions. Honors
 * the node pill (hare pill reads the ledger) and the exact-query pin. */
export interface EngineTrendPoint {
  candidate: string
  bucket: number
  runs: number
  p50: number
  inflight: number
}

export const TREND_BUCKETS = 24

export async function fetchEngineTrends(
  connectionId: string,
  windowHours: number,
  nodeFilter: RoutingNodeFilter = "all",
  queryHash?: string | null,
): Promise<EngineTrendPoint[]> {
  const hare = hareEndpoint(nodeFilter)
  const n = TREND_BUCKETS
  const bucketSecs = (windowHours * 3600) / n
  const buckets =
    `(SELECT i, now() - make_interval(secs => ${windowHours * 3600}) + make_interval(secs => i * ${bucketSecs}) AS t0, ` +
    `now() - make_interval(secs => ${windowHours * 3600}) + make_interval(secs => (i + 1) * ${bucketSecs}) AS t1 ` +
    `FROM generate_series(0, ${n - 1}) AS i) b`
  const sql = hare
    ? "SELECT 'duck_capsule' AS candidate, b.i AS bucket, " +
      "count(*) FILTER (WHERE h.invoked_at >= b.t0 AND h.invoked_at < b.t1) AS runs, " +
      "percentile_cont(0.5) WITHIN GROUP (ORDER BY h.total_ms) " +
      "FILTER (WHERE h.invoked_at >= b.t0 AND h.invoked_at < b.t1) AS p50, " +
      "count(*) AS inflight " +
      "FROM " + buckets + " JOIN rvbbit.hare_invocations h ON h.ok AND " +
      windowClause("h.invoked_at", windowHours) +
      ` AND h.endpoint = '${hare.replace(/'/g, "''")}'` +
      hashClause("h.sql_hash", queryHash) +
      " AND tstzrange(h.invoked_at - make_interval(secs => greatest(h.total_ms, 0) / 1000.0), h.invoked_at) && tstzrange(b.t0, b.t1) " +
      "GROUP BY 1, 2"
    : "SELECT coalesce(e.executed_engine, e.candidate) AS candidate, b.i AS bucket, " +
      "count(*) FILTER (WHERE e.executed_at >= b.t0 AND e.executed_at < b.t1) AS runs, " +
      "percentile_cont(0.5) WITHIN GROUP (ORDER BY e.elapsed_ms) " +
      "FILTER (WHERE e.executed_at >= b.t0 AND e.executed_at < b.t1) AS p50, " +
      "count(*) AS inflight " +
      "FROM " + buckets + " JOIN rvbbit.route_executions e ON e.status = 'ok' AND " +
      windowClause("e.executed_at", windowHours) + nodeClause(nodeFilter).replace(/ AND node/g, " AND e.node") +
      hashClause("e.query_hash", queryHash) +
      " AND tstzrange(e.executed_at - make_interval(secs => greatest(e.elapsed_ms, 0) / 1000.0), e.executed_at) && tstzrange(b.t0, b.t1) " +
      "GROUP BY 1, 2"
  const res = await runQuery(connectionId, sql)
  if (!res.ok) return []
  return res.rows.map((r) => ({
    candidate: normalizeCandidate(String(r.candidate ?? "")),
    bucket: num(r.bucket),
    runs: num(r.runs),
    p50: num(r.p50),
    inflight: num(r.inflight),
  }))
}

export async function fetchLogStatus(connectionId: string): Promise<LogStatus | null> {
  const res = await runQuery(connectionId, "SELECT rvbbit.route_decision_log_status() AS s")
  if (!res.ok || res.rows.length === 0) return null
  const s = (res.rows[0].s ?? {}) as Record<string, unknown>
  return {
    enabled: bool(s.enabled),
    started: bool(s.started),
    scope: String(s.scope ?? "backend"),
    backendPid: num(s.backend_pid),
    queueLen: num(s.queue_len),
    queueCapacity: numOrNull(s.queue_capacity),
    enqueued: numOrNull(s.enqueued),
    dropped: numOrNull(s.dropped),
    written: numOrNull(s.written),
    writeErrors: numOrNull(s.write_errors),
    connectErrors: numOrNull(s.connect_errors),
  }
}

// ── Trained profile ─────────────────────────────────────────────────

export async function fetchRouteProfile(connectionId: string): Promise<RouteProfile | null> {
  const res = await runQuery(
    connectionId,
    "SELECT name, active, created_at, updated_at, " +
      "profile->>'version' AS version, profile->>'suite' AS suite, " +
      "profile->>'generated_at' AS generated_at, " +
      "profile->>'min_observations' AS min_observations, " +
      "profile->>'min_gain_pct' AS min_gain_pct, " +
      "profile->>'entry_count' AS entry_count, " +
      "profile->>'observation_count' AS observation_count, " +
      "profile->>'profile_point_count' AS point_count, " +
      "profile->>'imported_by' AS imported_by " +
      "FROM rvbbit.route_profiles ORDER BY active DESC, updated_at DESC LIMIT 1",
  )
  if (!res.ok || res.rows.length === 0) return null
  const r = res.rows[0]
  return {
    name: String(r.name ?? ""),
    active: bool(r.active),
    createdAt: r.created_at ? String(r.created_at) : null,
    updatedAt: r.updated_at ? String(r.updated_at) : null,
    version: r.version ? String(r.version) : null,
    suite: r.suite ? String(r.suite) : null,
    generatedAt: r.generated_at ? String(r.generated_at) : null,
    minObservations: numOrNull(r.min_observations),
    minGainPct: numOrNull(r.min_gain_pct),
    entryCount: numOrNull(r.entry_count),
    observationCount: numOrNull(r.observation_count),
    pointCount: numOrNull(r.point_count),
    importedBy: r.imported_by ? String(r.imported_by) : null,
  }
}

export async function fetchProfileEntries(connectionId: string): Promise<ProfileEntry[]> {
  const res = await runQuery(
    connectionId,
    "SELECT shape_key, choice, confidence, observations, native_ms, duck_ms, " +
      "duck_vortex_ms, duck_hive_ms, datafusion_ms, datafusion_vortex_ms, " +
      "datafusion_hive_ms, gpu_gqe_ms, pg_ms, reason " +
      "FROM rvbbit.route_profile_entries " +
      `WHERE profile_name = ${ACTIVE_PROFILE_SUBQUERY} ` +
      "ORDER BY confidence DESC NULLS LAST",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    shapeKey: String(r.shape_key ?? ""),
    choice: normalizeCandidate(String(r.choice ?? "")),
    confidence: num(r.confidence),
    observations: num(r.observations),
    engineTimes: engineMsFromRow(r),
    reason: String(r.reason ?? ""),
  }))
}

export async function fetchShapeSummary(connectionId: string): Promise<ShapeSummaryRow[]> {
  const res = await runQuery(
    connectionId,
    "SELECT shape_family, observations, best_candidate, best_median_ms, " +
      "native_median_ms, duck_median_ms, duck_vortex_median_ms, duck_hive_median_ms, " +
      "datafusion_median_ms, datafusion_vortex_median_ms, datafusion_hive_median_ms, " +
      "gpu_gqe_median_ms, pg_median_ms, " +
      "native_observations, duck_observations, duck_vortex_observations, " +
      "duck_hive_observations, " +
      "datafusion_observations, datafusion_vortex_observations, " +
      "datafusion_hive_observations, gpu_gqe_observations, pg_observations, " +
      "observed_gain, needs_exploration FROM rvbbit.route_shape_summary " +
      "ORDER BY observations DESC",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    shapeFamily: String(r.shape_family ?? ""),
    observations: num(r.observations),
    bestCandidate: normalizeCandidate(String(r.best_candidate ?? "")),
    bestMedianMs: numOrNull(r.best_median_ms),
    observedGain: numOrNull(r.observed_gain),
    needsExploration: bool(r.needs_exploration),
    medianByEngine: engineMediansFromRow(r),
    observationsByEngine: engineObservationsFromRow(r),
  }))
}

export async function fetchProfilePoints(connectionId: string): Promise<ProfilePoint[]> {
  const res = await runQuery(
    connectionId,
    "SELECT shape_family, table_rows, native_ms, duck_ms, duck_vortex_ms, duck_hive_ms, " +
      "datafusion_ms, datafusion_vortex_ms, datafusion_hive_ms, gpu_gqe_ms, pg_ms " +
      `FROM rvbbit.route_profile_points WHERE profile_name = ${ACTIVE_PROFILE_SUBQUERY}`,
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    shapeFamily: String(r.shape_family ?? ""),
    tableRows: num(r.table_rows),
    engineTimes: engineMsFromRow(r),
  }))
}

export async function fetchObservationGroups(
  connectionId: string,
): Promise<ObservationGroup[]> {
  const res = await runQuery(
    connectionId,
    "SELECT source, candidate, count(*) AS n, avg(elapsed_ms) AS avg_ms " +
      "FROM rvbbit.route_observations GROUP BY source, candidate ORDER BY n DESC",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    source: String(r.source ?? ""),
    candidate: normalizeCandidate(String(r.candidate ?? "")),
    count: num(r.n),
    avgMs: num(r.avg_ms),
  }))
}

export async function fetchColumnarTables(connectionId: string): Promise<ColumnarTable[]> {
  const res = await runQuery(
    connectionId,
    "SELECT n.nspname AS schema, c.relname AS name, c.reltuples::bigint AS est_rows " +
      "FROM rvbbit.tables t " +
      "JOIN pg_class c ON c.oid = t.table_oid " +
      "JOIN pg_namespace n ON n.oid = c.relnamespace " +
      "WHERE c.relkind IN ('r','p','m') " +
      "AND coalesce(t.acceleration_enabled, true) " +
      "ORDER BY n.nspname, c.relname",
  )
  if (!res.ok) return []
  return res.rows.map((r) => ({
    schema: String(r.schema ?? ""),
    name: String(r.name ?? ""),
    estRows: num(r.est_rows),
  }))
}

// ── Aggregate bundles (one per window data-load) ────────────────────

export interface FlowData {
  executions: RouteExecution[]
  decisionSummary: DecisionSummaryRow[]
  engineRuntime: EngineRuntimeRow[]
  logStatus: LogStatus | null
}

export interface ProfileData {
  profile: RouteProfile | null
  entries: ProfileEntry[]
  shapeSummary: ShapeSummaryRow[]
  points: ProfilePoint[]
  observations: ObservationGroup[]
}

// ── route_explain ───────────────────────────────────────────────────

/** Explain a query's route without executing it. Safe — plan only. */
export async function routeExplain(
  connectionId: string,
  sql: string,
): Promise<{ explain: RouteExplain | null; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.route_explain(${sqlStr(sql)}) AS r`)
  if (!res.ok) return { explain: null, error: res.error }
  if (res.rows.length === 0) return { explain: null, error: "route_explain returned nothing" }
  const r = (res.rows[0].r ?? {}) as Record<string, unknown>
  const cands = Array.isArray(r.candidates) ? (r.candidates as Record<string, unknown>[]) : []
  const tables = Array.isArray(r.rvbbit_tables)
    ? (r.rvbbit_tables as Record<string, unknown>[])
    : []
  return {
    explain: {
      route: String(r.route ?? ""),
      reason: String(r.reason ?? ""),
      routeSource: String(r.route_source ?? ""),
      chosenCandidate: normalizeCandidate(String(r.chosen_candidate ?? "")),
      physicalPath: String(r.physical_path ?? ""),
      confidence: numOrNull(r.confidence),
      safeSelect: bool(r.safe_select),
      candidates: cands.map((c) => ({
        name: normalizeCandidate(String(c.name ?? "")),
        route: String(c.route ?? ""),
        reason: String(c.reason ?? ""),
        selected: bool(c.selected),
        available: bool(c.available),
      })),
      features: (r.features as Record<string, unknown>) ?? {},
      tables: tables.map((t) => ({
        table: String(t.table ?? ""),
        schema: String(t.schema ?? ""),
        rows: num(t.rows),
        bytes: num(t.bytes),
        rowGroups: num(t.row_groups),
        deleteCount: num(t.delete_count),
      })),
      postgresExplain: String(r.postgres_explain ?? ""),
      postgresExplainRole: String(r.postgres_explain_role ?? "PostgreSQL plan"),
    },
  }
}

// ── Shape-string helpers ────────────────────────────────────────────

/** Split a `k=v|k=v|…` shape string into tokens. */
export function parseShapeTokens(shape: string): { k: string; v: string }[] {
  if (!shape) return []
  return shape.split("|").map((t) => {
    const i = t.indexOf("=")
    return i < 0 ? { k: t, v: "" } : { k: t.slice(0, i), v: t.slice(i + 1) }
  })
}

const BORING_SHAPE_VALUES = new Set(["<=0", "0", "none", "", "unknown"])

/**
 * The non-default tokens of a shape — what actually distinguishes it.
 * Drops empty/zero buckets and opaque expression-signature hashes.
 */
export function shapeHighlights(shape: string): { k: string; v: string }[] {
  return parseShapeTokens(shape).filter(
    (t) => !BORING_SHAPE_VALUES.has(t.v) && !t.k.endsWith("_sig"),
  )
}

/** Human-friendly single token, e.g. `tables ≤2`. */
export function prettyToken(k: string, v: string): string {
  return `${k} ${v.replace("<=", "≤")}`
}

/** Pull the "Nx faster" multiplier out of a route reason, if present. */
export function routeSpeedup(reason: string | null | undefined): number | null {
  if (!reason) return null
  const m = reason.match(/([\d.]+)x faster/)
  return m ? Number(m[1]) : null
}

// ── Accelerator freshness (the cockpit) ─────────────────────────────
//
// One lane per accelerated rvbbit table, fusing the freshness rollup
// (rvbbit.accel_freshness) with the effective policy (accel_policy_effective).
// Staleness is correctness-safe (a dirty table just falls back to a slow heap
// scan), so this is a value-vs-cost view, not a coherence one.

export type AccelStrategy = "manual" | "scheduled" | "target" | "demand" | "continuous"
export const ACCEL_STRATEGIES: AccelStrategy[] = [
  "manual",
  "scheduled",
  "target",
  "demand",
  "continuous",
]

export interface AccelFreshnessRow {
  tableName: string
  schema: string
  dirty: boolean
  authoritative: boolean
  opRunning: boolean
  lance: boolean
  secondsDirty: number | null
  secondsSinceRefresh: number | null
  lastRefreshAt: number
  parquetRows: number
  rowGroups: number
  heapLiveTuples: number
  baselineMissing: boolean
  tombstones: number
  driftRows: number
  driftRatio: number | null
  heapSeqScans: number
  /** Days out of the last 7 a client SELECT read the table (0486+). */
  readDays: number
  lastReadAt: number | null
  lastRebuildMs: number | null
  lastRebuildRows: number | null
  lastOperation: string | null
  lastOperationStatus: string | null
  lastOperationAt: number | null
  lastOperationError: string | null
  lastOperationSwap: string | null
  lastFinalLockAttempts: number | null
  lastQueuedOrphanFiles: number | null
  lastCatchupRows: number | null
  lastRemappedTombstones: number | null
  lastRowsWritten: number | null
  // policy
  strategy: AccelStrategy
  targetSecs: number | null
  minIntervalSecs: number
  maxRowGroupsBeforeRebuild: number | null
  maxTombstonesBeforeRebuild: number | null
  explicit: boolean
  active: boolean
  deniedEngines: string[]
  deniedLayouts: string[]
}

// Engines + layouts that can be toggled per table. native/pg_rowstore are the
// correctness floor and are intentionally absent (never deniable).
export const TOGGLE_ENGINES = ["duck", "datafusion"] as const
export const TOGGLE_LAYOUTS = ["vortex", "hive"] as const

function pgArray(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x))
  if (v == null) return []
  const s = String(v).trim()
  if (s.startsWith("{") && s.endsWith("}")) {
    const inner = s.slice(1, -1).trim()
    if (!inner) return []
    return inner.split(",").map((x) => x.replace(/^"|"$/g, "").trim()).filter(Boolean)
  }
  return s ? [s] : []
}

export interface AccelTickPlanRow {
  tableName: string
  action: string // delta | full | skip
  reason: string
  status: string // planned | deferred | skip
  driftRows: number
  driftRatio: number | null
  secondsDirty: number | null
}

/** Which read evidence the connected rvbbit has: per-day (0486), last read only (0484), or none. */
type ReadEvidence = "days" | "last" | "none"

const READ_EVIDENCE_PROBE_SQL =
  "SELECT to_regclass('rvbbit.accel_read_evidence') IS NOT NULL AS has_last, " +
  "to_regclass('rvbbit.accel_read_days') IS NOT NULL AS has_days"

async function readEvidenceKind(connectionId: string): Promise<ReadEvidence> {
  const res = await runQuery(connectionId, READ_EVIDENCE_PROBE_SQL)
  if (!res.ok) return "none"
  if (bool(res.rows[0]?.has_days)) return "days"
  return bool(res.rows[0]?.has_last) ? "last" : "none"
}

function readDaysSql(oidExpr: string): string {
  return (
    "coalesce((SELECT count(*) FROM rvbbit.accel_read_days d WHERE d.table_oid = " + oidExpr +
    " AND d.day > (now() AT TIME ZONE 'UTC')::date - 7), 0)::bigint"
  )
}

function accelFreshnessSql(evidence: ReadEvidence): string {
  const observerSelect =
    (evidence === "days" ? readDaysSql("f.table_oid") : "0::bigint") + " AS read_days, " +
    (evidence === "none" ? "NULL::timestamptz" : "ev.last_read_at") + " AS last_read_at, "
  const observerJoin = evidence === "none"
    ? ""
    : "LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = f.table_oid "

  return "SELECT f.table_name, n.nspname AS schema, f.shadow_heap_dirty, f.parquet_authoritative, f.op_running, " +
  "f.lance_accelerated, f.seconds_dirty, f.seconds_since_refresh, f.last_refresh_at, " +
  "f.parquet_rows, f.row_groups, f.heap_live_tuples, f.tombstones, f.drift_rows, f.drift_ratio, f.heap_seq_scans, " +
  observerSelect +
  "f.last_rebuild_ms, f.last_rebuild_rows, e.strategy, e.freshness_target_secs, " +
  "e.min_interval_secs, e.max_row_groups_before_rebuild, e.max_tombstones_before_rebuild, " +
  "e.explicit, e.active, e.denied_engines, e.denied_layouts, " +
  "op.operation AS last_operation, op.status AS last_operation_status, op.started_at AS last_operation_at, " +
  "op.error AS last_operation_error, op.rows_written AS last_rows_written, " +
  "op.settings->>'metadata_swap' AS last_operation_swap, " +
  "(op.settings->>'final_lock_attempts')::bigint AS last_final_lock_attempts, " +
  "(op.settings->>'queued_orphan_files')::bigint AS last_queued_orphan_files, " +
  "(op.settings->>'catchup_rows')::bigint AS last_catchup_rows, " +
  "(op.settings->>'remapped_tombstones')::bigint AS last_remapped_tombstones " +
  "FROM rvbbit.accel_freshness f " +
  "JOIN rvbbit.accel_policy_effective e ON e.table_oid = f.table_oid " +
  "LEFT JOIN pg_class c ON c.oid = f.table_oid " +
  "LEFT JOIN pg_namespace n ON n.oid = c.relnamespace " +
  observerJoin +
  "LEFT JOIN LATERAL ( " +
  "SELECT o.operation, o.status, o.started_at, o.error, o.rows_written, o.settings " +
  "FROM rvbbit.acceleration_operations o " +
  "WHERE o.table_oid = f.table_oid " +
  "ORDER BY o.started_at DESC, o.id DESC LIMIT 1 " +
  ") op ON true " +
  "ORDER BY n.nspname, (f.drift_rows * (1 + f.heap_seq_scans)) DESC, f.table_name"
}

export async function fetchAccelFreshness(
  connectionId: string,
): Promise<{ rows: AccelFreshnessRow[]; error?: string }> {
  const res = await runQuery(connectionId, accelFreshnessSql(await readEvidenceKind(connectionId)))
  if (!res.ok) return { rows: [], error: res.error }
  return {
    rows: res.rows.map((r) => ({
      tableName: String(r.table_name ?? ""),
      schema: String(r.schema ?? ""),
      dirty: bool(r.shadow_heap_dirty),
      authoritative: bool(r.parquet_authoritative),
      opRunning: bool(r.op_running),
      lance: bool(r.lance_accelerated),
      secondsDirty: numOrNull(r.seconds_dirty),
      secondsSinceRefresh: numOrNull(r.seconds_since_refresh),
      lastRefreshAt: epoch(r.last_refresh_at),
      parquetRows: num(r.parquet_rows),
      rowGroups: num(r.row_groups),
      heapLiveTuples: num(r.heap_live_tuples),
      baselineMissing: num(r.row_groups) === 0 && num(r.heap_live_tuples) > 0,
      tombstones: num(r.tombstones),
      driftRows: num(r.drift_rows),
      driftRatio: numOrNull(r.drift_ratio),
      heapSeqScans: num(r.heap_seq_scans),
      readDays: num(r.read_days),
      lastReadAt: r.last_read_at ? epoch(r.last_read_at) : null,
      lastRebuildMs: numOrNull(r.last_rebuild_ms),
      lastRebuildRows: numOrNull(r.last_rebuild_rows),
      lastOperation: strOrNull(r.last_operation),
      lastOperationStatus: strOrNull(r.last_operation_status),
      lastOperationAt: r.last_operation_at ? epoch(r.last_operation_at) : null,
      lastOperationError: strOrNull(r.last_operation_error),
      lastOperationSwap: strOrNull(r.last_operation_swap),
      lastFinalLockAttempts: numOrNull(r.last_final_lock_attempts),
      lastQueuedOrphanFiles: numOrNull(r.last_queued_orphan_files),
      lastCatchupRows: numOrNull(r.last_catchup_rows),
      lastRemappedTombstones: numOrNull(r.last_remapped_tombstones),
      lastRowsWritten: numOrNull(r.last_rows_written),
      strategy: (String(r.strategy ?? "manual") as AccelStrategy) ?? "manual",
      targetSecs: numOrNull(r.freshness_target_secs),
      minIntervalSecs: num(r.min_interval_secs),
      maxRowGroupsBeforeRebuild: numOrNull(r.max_row_groups_before_rebuild),
      maxTombstonesBeforeRebuild: numOrNull(r.max_tombstones_before_rebuild),
      explicit: bool(r.explicit),
      active: bool(r.active),
      deniedEngines: pgArray(r.denied_engines),
      deniedLayouts: pgArray(r.denied_layouts),
    })),
  }
}

/** Dry-run plan = the "projected consequence" of a tick (no execution). */
export async function fetchAccelTickPlan(
  connectionId: string,
  budget: number | null,
): Promise<{ rows: AccelTickPlanRow[]; error?: string }> {
  const b = budget == null ? "NULL" : String(Math.max(0, Math.floor(budget)))
  const res = await runQuery(
    connectionId,
    "SELECT table_name, action, reason, status, drift_rows, drift_ratio, seconds_dirty " +
      `FROM rvbbit.accel_tick(${b}, true)`,
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    rows: res.rows.map((r) => ({
      tableName: String(r.table_name ?? ""),
      action: String(r.action ?? ""),
      reason: String(r.reason ?? ""),
      status: String(r.status ?? ""),
      driftRows: num(r.drift_rows),
      driftRatio: numOrNull(r.drift_ratio),
      secondsDirty: numOrNull(r.seconds_dirty),
    })),
  }
}

/** Run an accelerator mutation; returns ok/err for a toast. */
// ---------------------------------------------------------------------------
// Acceleration observatory (rvbbit.accel_observatory_* views, extension 0329).
// One fetch returns every panel; the views carry the shared definitions of
// eligible / current / fresh / synced so this tab, the Callie system dashboard
// and the autopilot agree.
// ---------------------------------------------------------------------------

export interface ObservatoryCoverage {
  eligibleTables: number
  awaitingBaseline: number
  currentTables: number
  unacceleratedTables: number
  beyondTarget: number
  deltaCapableTables: number
  autopilotManaged: number
  autopilotParked: number
  coveragePct: number | null
  /** current OR dirty within target (0330); null on older extensions */
  healthyPct: number | null
  withinTargetTables: number
  healthyTables: number
  behindTables: number
  runningNow: number
  orphanedFiles: number
  autopilotMode: string | null
}

export interface ObservatoryBucket {
  bucket: string
  order: number
  tables: number
}

export interface ObservatoryStrategy {
  strategy: string
  tables: number
  current: number
  other: number
  deltaCapable: number
}

export interface ObservatoryLayoutFamily {
  family: string
  layouts: number
  fresh: number
  synced: number
  waitingOnParent: number
  tables: number
  files: number
  oldestRefresh: number
  freshPct: number | null
}

export interface ObservatoryLayoutRow {
  tableName: string
  layout: string
  family: string
  status: string
  gate: string
  expectedRows: number | null
  actualRows: number | null
  currentRows: number | null
  fileCount: number
  refreshedAt: number
  parentCurrent: boolean
  layoutSynced: boolean
  isFresh: boolean
  statusMessage: string | null
}

export interface ObservatoryLiveRow {
  pid: number
  lane: string | null
  tableName: string | null
  action: string | null
  startedAt: number
  elapsedMs: number
  state: string | null
  waitEvent: string | null
  p95Ms: number | null
  beyondP95: boolean
  implausible: boolean
}

export interface ObservatoryTimelineRow {
  tableName: string
  lane: string
  action: string
  status: string
  startedAt: number
  finishedAt: number
  elapsedMs: number
  rowsWritten: number
}

export interface ObservatoryAttentionRow {
  tableName: string
  strategy: string
  state: string
  secondsSinceRefresh: number | null
  secondsDirty: number | null
  targetSecs: number | null
  targetDistance: number | null
  blockedReason: string | null
  deltaCapable: boolean
  rowGroups: number
  tombstones: number
  budgetSpent: number | null
  dailyBudget: number | null
  priority: number
  /** dirty within target — in flight as promised, not a problem (0330) */
  healthy: boolean
}

export interface ObservatoryRebuildCostRow {
  rank: number
  tableName: string
  strategy: string
  samples: number
  avgMs: number
  p50Ms: number | null
  p95Ms: number | null
  maxMs: number | null
  avgRows: number
  dailyCostMs: number
  lastFinishedAt: number
  commonReason: string | null
  isCurrent: boolean
  deltaCapable: boolean
  managedBy: string | null
}

export interface ObservatoryAutopilotEvent {
  at: number
  action: string
  status: string
  tableName: string | null
  reason: string | null
}

export interface ObservatorySnapshot {
  installed: boolean
  coverage: ObservatoryCoverage | null
  buckets: ObservatoryBucket[]
  strategies: ObservatoryStrategy[]
  layoutFamilies: ObservatoryLayoutFamily[]
  layoutAttention: ObservatoryLayoutRow[]
  live: ObservatoryLiveRow[]
  timeline: ObservatoryTimelineRow[]
  attention: ObservatoryAttentionRow[]
  rebuildCost: ObservatoryRebuildCostRow[]
  autopilotEvents: ObservatoryAutopilotEvent[]
  windowStart: number
  windowEnd: number
  completedRuns: number
  /** Wall-clock time of this snapshot; timelines are drawn relative to it. */
  fetchedAt: number
}

export const OBSERVATORY_QUERIES = {
  coverage: "SELECT * FROM rvbbit.accel_observatory_coverage",
  buckets: "SELECT target_bucket, bucket_order, tables FROM rvbbit.accel_observatory_target_distance ORDER BY bucket_order",
  strategies: "SELECT * FROM rvbbit.accel_observatory_strategy ORDER BY tables DESC",
  layoutFamilies: "SELECT * FROM rvbbit.accel_observatory_layout_summary ORDER BY family",
  layoutAttention:
    "SELECT table_name, layout, family, status, gate, expected_rows, actual_rows, current_rows, file_count, refreshed_at, parent_current, layout_synced, is_fresh, status_message " +
    "FROM rvbbit.accel_observatory_layouts WHERE NOT is_fresh ORDER BY family, table_name, layout LIMIT 200",
  live: "SELECT * FROM rvbbit.accel_observatory_live ORDER BY current_work_elapsed_ms DESC",
  timeline: (hours: number) => `SELECT * FROM rvbbit.accel_observatory_timeline(${Math.max(1, Math.floor(hours))}, 600)`,
  attention:
    "SELECT * FROM rvbbit.accel_observatory_attention ORDER BY priority, seconds_since_refresh DESC NULLS LAST LIMIT 100",
  rebuildCost: "SELECT * FROM rvbbit.accel_observatory_rebuild_cost ORDER BY rank LIMIT 25",
  autopilotEvents:
    "SELECT event_at, action, status, table_name, reason FROM rvbbit.accel_observatory_autopilot_events ORDER BY event_at DESC LIMIT 40",
} as const

export async function fetchAccelObservatory(
  connectionId: string,
  opts: { timelineHours?: number } = {},
): Promise<{ snapshot: ObservatorySnapshot; error?: string }> {
  const empty: ObservatorySnapshot = {
    installed: false,
    coverage: null,
    buckets: [],
    strategies: [],
    layoutFamilies: [],
    layoutAttention: [],
    live: [],
    timeline: [],
    attention: [],
    rebuildCost: [],
    autopilotEvents: [],
    windowStart: 0,
    windowEnd: 0,
    completedRuns: 0,
    fetchedAt: Date.now(),
  }
  const probe = await runQuery(
    connectionId,
    "SELECT to_regclass('rvbbit.accel_observatory_coverage') IS NOT NULL AS installed",
  )
  if (!probe.ok) return { snapshot: empty, error: probe.error }
  if (!bool(probe.rows[0]?.installed)) return { snapshot: empty }

  const q = (sql: string) => runQuery(connectionId, sql, { readOnly: true, poolLane: "meta" })
  const [coverage, buckets, strategies, families, layoutAttention, live, timeline, attention, rebuild, autopilot] =
    await Promise.all([
      q(OBSERVATORY_QUERIES.coverage),
      q(OBSERVATORY_QUERIES.buckets),
      q(OBSERVATORY_QUERIES.strategies),
      q(OBSERVATORY_QUERIES.layoutFamilies),
      q(OBSERVATORY_QUERIES.layoutAttention),
      q(OBSERVATORY_QUERIES.live),
      q(OBSERVATORY_QUERIES.timeline(opts.timelineHours ?? 3)),
      q(OBSERVATORY_QUERIES.attention),
      q(OBSERVATORY_QUERIES.rebuildCost),
      q(OBSERVATORY_QUERIES.autopilotEvents),
    ])
  const failed = [coverage, buckets, strategies, families, layoutAttention, live, timeline, attention, rebuild, autopilot].find(
    (r) => !r.ok,
  ) as QueryErr | undefined
  const rows = (r: QueryOk | QueryErr) => (r.ok ? r.rows : [])
  const c = rows(coverage)[0]
  const rebuildRows = rows(rebuild)
  const snapshot: ObservatorySnapshot = {
    installed: true,
    coverage: c
      ? {
          eligibleTables: num(c.eligible_tables),
          awaitingBaseline: num(c.awaiting_baseline),
          currentTables: num(c.current_tables),
          unacceleratedTables: num(c.unaccelerated_tables),
          beyondTarget: num(c.beyond_target),
          deltaCapableTables: num(c.delta_capable_tables),
          autopilotManaged: num(c.autopilot_managed),
          autopilotParked: num(c.autopilot_parked),
          coveragePct: numOrNull(c.coverage_pct),
          healthyPct: numOrNull(c.healthy_pct),
          withinTargetTables: num(c.within_target_tables),
          healthyTables: c.healthy_tables == null ? num(c.current_tables) : num(c.healthy_tables),
          behindTables: c.behind_tables == null ? num(c.unaccelerated_tables) : num(c.behind_tables),
          runningNow: num(c.running_now),
          orphanedFiles: num(c.orphaned_files),
          autopilotMode: strOrNull(c.autopilot_mode),
        }
      : null,
    buckets: rows(buckets).map((r) => ({
      bucket: String(r.target_bucket ?? ""),
      order: num(r.bucket_order),
      tables: num(r.tables),
    })),
    strategies: rows(strategies).map((r) => ({
      strategy: String(r.strategy ?? ""),
      tables: num(r.tables),
      current: num(r.current_tables),
      other: num(r.other_tables),
      deltaCapable: num(r.delta_capable_tables),
    })),
    layoutFamilies: rows(families).map((r) => ({
      family: String(r.family ?? ""),
      layouts: num(r.layouts),
      fresh: num(r.fresh_layouts),
      synced: num(r.synced_layouts),
      waitingOnParent: num(r.waiting_on_parent),
      tables: num(r.tables),
      files: num(r.files),
      oldestRefresh: epoch(r.oldest_refresh),
      freshPct: numOrNull(r.fresh_pct),
    })),
    layoutAttention: rows(layoutAttention).map((r) => ({
      tableName: String(r.table_name ?? ""),
      layout: String(r.layout ?? ""),
      family: String(r.family ?? ""),
      status: String(r.status ?? ""),
      gate: String(r.gate ?? ""),
      expectedRows: numOrNull(r.expected_rows),
      actualRows: numOrNull(r.actual_rows),
      currentRows: numOrNull(r.current_rows),
      fileCount: num(r.file_count),
      refreshedAt: epoch(r.refreshed_at),
      parentCurrent: bool(r.parent_current),
      layoutSynced: bool(r.layout_synced),
      isFresh: bool(r.is_fresh),
      statusMessage: strOrNull(r.status_message),
    })),
    live: rows(live).map((r) => ({
      pid: num(r.pid),
      lane: strOrNull(r.lane),
      tableName: strOrNull(r.table_name),
      action: strOrNull(r.action),
      startedAt: epoch(r.current_work_started_at),
      elapsedMs: num(r.current_work_elapsed_ms),
      state: strOrNull(r.state),
      waitEvent: strOrNull(r.wait_event),
      p95Ms: numOrNull(r.p95_elapsed_ms),
      beyondP95: bool(r.beyond_p95),
      implausible: bool(r.implausible),
    })),
    timeline: rows(timeline).map((r) => ({
      tableName: String(r.table_name ?? ""),
      lane: String(r.lane ?? ""),
      action: String(r.action ?? ""),
      status: String(r.status ?? ""),
      startedAt: epoch(r.started_at),
      finishedAt: epoch(r.finished_at),
      elapsedMs: num(r.elapsed_ms),
      rowsWritten: num(r.rows_written),
    })),
    attention: rows(attention).map((r) => ({
      tableName: String(r.table_name ?? ""),
      strategy: String(r.strategy ?? ""),
      state: String(r.state ?? ""),
      secondsSinceRefresh: numOrNull(r.seconds_since_refresh),
      secondsDirty: numOrNull(r.seconds_dirty),
      targetSecs: numOrNull(r.effective_target_secs),
      targetDistance: numOrNull(r.target_distance),
      blockedReason: strOrNull(r.blocked_reason),
      deltaCapable: bool(r.delta_capable),
      rowGroups: num(r.row_groups),
      tombstones: num(r.tombstones),
      budgetSpent: numOrNull(r.budget_spent),
      dailyBudget: numOrNull(r.daily_refresh_budget),
      priority: num(r.priority),
      healthy: bool(r.healthy),
    })),
    rebuildCost: rebuildRows.map((r) => ({
      rank: num(r.rank),
      tableName: String(r.table_name ?? ""),
      strategy: String(r.strategy ?? ""),
      samples: num(r.samples),
      avgMs: num(r.avg_elapsed_ms),
      p50Ms: numOrNull(r.p50_elapsed_ms),
      p95Ms: numOrNull(r.p95_elapsed_ms),
      maxMs: numOrNull(r.max_elapsed_ms),
      avgRows: num(r.avg_rows_written),
      dailyCostMs: num(r.daily_cost_ms),
      lastFinishedAt: epoch(r.last_finished_at),
      commonReason: strOrNull(r.common_reason),
      isCurrent: bool(r.is_current),
      deltaCapable: bool(r.delta_capable),
      managedBy: strOrNull(r.managed_by),
    })),
    autopilotEvents: rows(autopilot).map((r) => ({
      at: epoch(r.event_at),
      action: String(r.action ?? ""),
      status: String(r.status ?? ""),
      tableName: strOrNull(r.table_name),
      reason: strOrNull(r.reason),
    })),
    windowStart: rebuildRows.length ? epoch(rebuildRows[0].window_start) : 0,
    windowEnd: rebuildRows.length ? epoch(rebuildRows[0].window_end) : 0,
    completedRuns: rebuildRows.length ? num(rebuildRows[0].completed_runs) : 0,
    fetchedAt: Date.now(),
  }
  return failed ? { snapshot, error: failed.error } : { snapshot }
}

export const runAutopilotActSql = () => "SELECT rvbbit.accel_autopilot_act('datarabbit')"
export const reconcileStaleVortexSql = () => "SELECT rvbbit.reconcile_stale_vortex_layouts(20)"

/**
 * Fire one accelerator maintenance statement on a dedicated connection with no
 * statement_timeout and return as soon as it has started.
 *
 * The pooled path arms every connection with the 30-minute default
 * statement_timeout, which is right for interactive queries and wrong for a
 * baseline build of a large table: the build was being cancelled by the pool
 * it ran on. A detached run outlives the request, never pins a pool slot, and
 * cannot poison one. Watch it settle with waitForAccelSettle; closing
 * DataRabbit ends the Node process and cancels the statement, which rolls back
 * because these are single-transaction functions.
 */
export async function execAccel(
  connectionId: string,
  sql: string,
): Promise<{ ok: boolean; started?: boolean; error?: string }> {
  try {
    const res = await fetch("/api/db/run-detached", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql }),
    })
    const j = (await res.json()) as { ok?: boolean; started?: boolean; error?: string }
    return j.ok ? { ok: true, started: true } : { ok: false, error: j.error ?? "failed to start" }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

export interface AccelSettleOpts {
  /** Poll cadence in ms. */
  intervalMs?: number
  /** Give up after this long; the operation itself keeps running. */
  maxWaitMs?: number
  /** Each poll's rows, so a caller can keep its view live while waiting. */
  onRows?: (rows: AccelFreshnessRow[]) => void
  /** Abort the wait (not the operation). */
  signal?: AbortSignal
}

/**
 * Wait for a detached accelerator operation on `table` to settle, judged from
 * the registry the operation itself writes: a newer lastOperationAt than the
 * one captured before firing, no longer running. Returns the settled row, or
 * null when the wait was abandoned or timed out.
 */
export async function waitForAccelSettle(
  connectionId: string,
  table: string,
  sinceAt: number | null,
  opts: AccelSettleOpts = {},
): Promise<{ row: AccelFreshnessRow | null; timedOut: boolean }> {
  const intervalMs = Math.max(1000, opts.intervalMs ?? 3000)
  const maxWaitMs = Math.max(intervalMs, opts.maxWaitMs ?? 6 * 60 * 60 * 1000)
  const started = Date.now()
  let sawRunning = false
  while (!opts.signal?.aborted) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
    if (opts.signal?.aborted) break
    const fresh = await fetchAccelFreshness(connectionId)
    if (fresh.error) continue
    opts.onRows?.(fresh.rows)
    const row = fresh.rows.find((r) => r.tableName === table) ?? null
    if (row) {
      const running = row.opRunning || row.lastOperationStatus === "running"
      if (running) sawRunning = true
      const advanced = (row.lastOperationAt ?? 0) > (sinceAt ?? 0)
      // Settled once the registry shows a newer, finished operation. A very
      // fast op can finish between polls; `advanced` alone covers that.
      if (!running && (advanced || (sawRunning && !row.opRunning))) return { row, timedOut: false }
    }
    if (Date.now() - started > maxWaitMs) return { row, timedOut: true }
  }
  return { row: null, timedOut: false }
}

const qIdent = (table: string) => `'${table.replace(/'/g, "''")}'::regclass`

export const accelRefreshSql = (table: string) =>
  `SELECT rvbbit.refresh_acceleration(${qIdent(table)}, true)`
export const accelCanonicalFoldSql = (table: string) =>
  `SELECT rvbbit.rebuild_acceleration(${qIdent(table)}, false)`
export const accelRebuildSql = (table: string) =>
  `SELECT rvbbit.rebuild_acceleration(${qIdent(table)}, true)`
export const retireAccelerationSql = (table: string) =>
  `SELECT rvbbit.retire_acceleration(${qIdent(table)})`
// Refresh-after-load policies use 'continuous': the tick then honors their
// min_interval. 'scheduled' paces refreshes by query evidence (0446), which
// leaves a table nobody has measurably read waiting 24 h between folds.
export function accelBuildAndScheduleSql(table: string): string {
  return (
    "WITH baseline AS MATERIALIZED (" +
    `SELECT rvbbit.rebuild_acceleration(${qIdent(table)}, true) AS result` +
    "), policy AS MATERIALIZED (" +
    `SELECT rvbbit.set_accel_policy(${qIdent(table)}, ` +
    "strategy => 'continuous', freshness_target_secs => NULL, " +
    "min_interval_secs => 900, daily_refresh_budget => NULL, " +
    "full_rebuild_drift_ratio => 0.5, lance_separate => true, active => true, " +
    "note => 'Auto-refresh after committed loads (DataRabbit OLAP Maint)') AS result " +
    "FROM baseline" +
    ") SELECT baseline.result AS baseline, policy.result AS policy " +
    "FROM baseline CROSS JOIN policy"
  )
}
export const clearAccelPolicySql = (table: string) =>
  `SELECT rvbbit.clear_accel_policy(${qIdent(table)})`
export function setAccelPolicySql(
  table: string,
  strategy: AccelStrategy,
  targetSecs: number | null,
  maxRowGroupsBeforeRebuild: number | null = null,
  maxTombstonesBeforeRebuild: number | null = null,
  minIntervalSecs = 60,
): string {
  const t = targetSecs == null ? "NULL" : String(Math.max(1, Math.floor(targetSecs)))
  // A target shorter than the table's minimum interval could never be met:
  // the tick never refreshes more often than min_interval_secs.
  const cappedInterval = targetSecs == null ? minIntervalSecs : Math.min(minIntervalSecs, targetSecs)
  const minInterval = String(Math.max(0, Math.floor(cappedInterval)))
  const maxRgs =
    maxRowGroupsBeforeRebuild == null ? "NULL" : String(Math.max(1, Math.floor(maxRowGroupsBeforeRebuild)))
  const maxTombs =
    maxTombstonesBeforeRebuild == null ? "NULL" : String(Math.max(1, Math.floor(maxTombstonesBeforeRebuild)))
  return (
    `SELECT rvbbit.set_accel_policy(${qIdent(table)}, ` +
    `strategy => ${sqlStr(strategy)}, ` +
    `freshness_target_secs => ${t}, ` +
    `min_interval_secs => ${minInterval}, ` +
    `max_row_groups_before_rebuild => ${maxRgs}, ` +
    `max_tombstones_before_rebuild => ${maxTombs})`
  )
}
/** Run the executor for real (budgeted). */
export const runAccelTickSql = (budget: number) =>
  `SELECT count(*) FROM rvbbit.accel_tick(${Math.max(1, Math.floor(budget))}, false)`

/** Toggle one engine/layout on or off for a table (reduces routing pathways +
 *  stops the rebuilder materializing a denied layout). */
export const setTableEngineSql = (table: string, target: string, enabled: boolean) =>
  `SELECT rvbbit.set_table_engine(${qIdent(table)}, ${sqlStr(target)}, ${enabled ? "true" : "false"})`

export interface PolicyRecommendation {
  strategy: AccelStrategy
  targetSecs: number | null
  why: string
}

/**
 * Suggest a policy from the value signals. Hot + cheap-to-keep-fresh → a tight
 * freshness target; never-queried → leave manual; expensive (Lance) → relax.
 */
export function recommendPolicy(r: AccelFreshnessRow): PolicyRecommendation {
  if (r.baselineMissing) {
    if (r.heapSeqScans === 0) {
      return { strategy: "manual", targetSecs: null, why: "registered but not built, with no observed slow-path demand yet" }
    }
    if (r.heapSeqScans >= 5) {
      return { strategy: "scheduled", targetSecs: null, why: "baseline missing and repeatedly queried on the heap — build now and refresh when dirty" }
    }
    return { strategy: "demand", targetSecs: null, why: "baseline missing with observed heap demand — build when the demand policy runs" }
  }
  if (r.heapSeqScans === 0 && !r.dirty) {
    return { strategy: "manual", targetSecs: null, why: "never queried on the slow path — not worth auto-refreshing" }
  }
  if (r.lance) {
    return { strategy: "target", targetSecs: 1800, why: "Lance is a full overwrite — keep fresh, but on a relaxed 30m target" }
  }
  const hot = r.heapSeqScans >= 5
  const cheapDelta = (r.driftRatio ?? 1) < 0.5
  if (hot && cheapDelta) {
    return { strategy: "target", targetSecs: 300, why: "queried on the slow path and cheap to minor-refresh — keep within ~5 min" }
  }
  if (hot) {
    return { strategy: "scheduled", targetSecs: null, why: "queried on the slow path — refresh whenever dirty" }
  }
  return { strategy: "demand", targetSecs: null, why: "low traffic — only refresh when it's actually being hit" }
}

// ── workload layout advisor ─────────────────────────────────────────

export type WorkloadLayoutKind = "cluster" | "hive"
export type WorkloadLayoutStatus = "candidate" | "accepted" | "rejected" | "retired"

export interface WorkloadLayoutCatalog {
  catalogPresent: boolean
  statusViewPresent: boolean
  advisorPresent: boolean
  buildHelperPresent: boolean
}

export interface WorkloadLayoutTable {
  tableName: string
  schema: string
  name: string
  parquetRows: number
  rowGroups: number
  heapSeqScans: number
  dirty: boolean
  opRunning: boolean
  recommendations: number
  accepted: number
  ready: number
}

export interface WorkloadRoleCounts {
  where: number
  groupBy: number
  orderBy: number
  countDistinct: number
}

export interface WorkloadLayoutRecommendation {
  tableName: string
  tableOid: string
  layoutKind: WorkloadLayoutKind
  columnName: string
  layout: string
  status: WorkloadLayoutStatus
  score: number
  observations: number
  weightedMs: number
  roleCounts: WorkloadRoleCounts
  sampleShapes: string[]
  layoutStatus: string | null
  layoutRows: number | null
  layoutFiles: number | null
  reason: string
  details: Record<string, unknown>
  recommendedAt: number
  updatedAt: number
}

export interface WorkloadAdvisorRun {
  table: string
  ok: boolean
  recommendations: number
  matchedShapes: number
  error?: string
}

export interface WorkloadLayoutBuildRun {
  table: string
  ok: boolean
  status: string
  acceptedLayouts: number | null
  readyLayouts: number | null
  layoutRows: number | null
  baseAction: string | null
  message: string
  error?: string
}

export interface AccelerationCandidate {
  tableName: string
  schema: string
  name: string
  rowEstimate: number
  sizeBytes: number
  seqScans: number
  seqRows: number
  idxScans: number
  writes: number
  inserts: number
  updates: number
  deletes: number
  modSinceAnalyze: number
  heapBlocksRead: number
  heapBlocksHit: number
  slowQueries: number
  queryCalls: number
  totalMs: number
  maxMeanMs: number | null
  querySamples: string[]
  mutationRatio: number
  readWriteRatio: number
  score: number
  recommendation: "strong" | "watch" | "low"
  writeProfile: string
  reason: string
  registered: boolean
  accelerationEnabled: boolean
  rowGroups: number
  baselineMissing: boolean
  lastMaintenanceAt: number | null
  /** The autopilot's classification (rvbbit 0486+); null when it has none for this table. */
  observerStatus: "observing" | "ready" | "held" | "managed" | "parked" | "released" | null
  /** Days out of the last 7 a client SELECT read the table. */
  observerReadDays: number
  observerLastReadAt: number | null
  observerLastObservedAt: number | null
  observerReasons: string[]
}

export interface AccelerationObserverStatus {
  installed: boolean
  mode: "off" | "observe" | "act" | "unknown"
  /** False when the pg_rvbbit library predates read counting. */
  readCounterAvailable: boolean
  readsCountedSince: number | null
  /** A table is ready once read on this many of the last 7 days. */
  minReadDays: number
  counts: {
    total: number
    ready: number
    observing: number
    held: number
    managed: number
    parked: number
    /** Released by the autopilot: rebuilt from scratch too often to pay off (rvbbit 0493+). */
    released: number
  }
  latestRun: {
    runId: number
    status: string
    startedAt: number | null
    finishedAt: number | null
    tablesObserved: number
    readyCount: number
    message: string
    error: string | null
  } | null
}

export async function fetchWorkloadLayoutCatalog(
  connectionId: string,
): Promise<{ catalog: WorkloadLayoutCatalog | null; error?: string }> {
  const res = await runQuery(
    connectionId,
    "SELECT " +
      "to_regclass('rvbbit.workload_layout_recommendations') IS NOT NULL AS catalog_present, " +
      "to_regclass('rvbbit.workload_layout_recommendation_status') IS NOT NULL AS status_view_present, " +
      "EXISTS ( " +
      "  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace " +
      "  WHERE n.nspname = 'rvbbit' AND p.proname = 'recommend_workload_layouts' " +
      ") AS advisor_present, " +
      "EXISTS ( " +
      "  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace " +
      "  WHERE n.nspname = 'rvbbit' AND p.proname = 'build_accepted_workload_layouts' " +
      ") AS build_helper_present",
  )
  if (!res.ok) return { catalog: null, error: res.error }
  const r = res.rows[0] ?? {}
  return {
    catalog: {
      catalogPresent: bool(r.catalog_present),
      statusViewPresent: bool(r.status_view_present),
      advisorPresent: bool(r.advisor_present),
      buildHelperPresent: bool(r.build_helper_present),
    },
  }
}

function accelerationCandidateSql(includePgStatStatements: boolean, includeObserver: boolean): string {
  const matched = includePgStatStatements
    ? "pgss AS ( " +
      "SELECT queryid::text AS query_id, calls::bigint AS calls, " +
      "total_exec_time::double precision AS total_ms, mean_exec_time::double precision AS mean_ms, " +
      "rows::bigint AS rows_returned, query " +
      "FROM pg_stat_statements " +
      "WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) " +
      "AND query IS NOT NULL " +
      "AND query ~* '^\\s*(select|with|table)\\M' " +
      "ORDER BY total_exec_time DESC LIMIT 250 " +
      "), matched AS ( " +
      "SELECT h.oid, " +
      "count(*) FILTER (WHERE p.mean_ms >= 50 OR p.total_ms >= 1000) AS slow_queries, " +
      "coalesce(sum(p.calls), 0)::bigint AS query_calls, " +
      "coalesce(sum(p.total_ms), 0)::double precision AS total_ms, " +
      "max(p.mean_ms)::double precision AS max_mean_ms, " +
      "(array_agg(left(regexp_replace(p.query, '\\s+', ' ', 'g'), 180) ORDER BY p.total_ms DESC))[1:3] AS query_samples " +
      "FROM heap_tables h " +
      "JOIN pgss p ON position(lower(h.name) in lower(p.query)) > 0 " +
      "  OR position(lower(h.table_name) in lower(p.query)) > 0 " +
      "GROUP BY h.oid " +
      ") "
    : "matched AS ( " +
      "SELECT oid, 0::bigint AS slow_queries, 0::bigint AS query_calls, " +
      "0::double precision AS total_ms, NULL::double precision AS max_mean_ms, " +
      "ARRAY[]::text[] AS query_samples FROM heap_tables " +
      ") "

  const observerSelect = includeObserver
    ? ", o.status AS observer_status, " + readDaysSql("c.oid") + " AS observer_read_days, " +
      "ev.last_read_at AS observer_last_read_at, " +
      "o.last_observed_at AS observer_last_observed_at, o.reasons AS observer_reasons "
    : ", NULL::text AS observer_status, 0::bigint AS observer_read_days, " +
      "NULL::timestamptz AS observer_last_read_at, " +
      "NULL::timestamptz AS observer_last_observed_at, ARRAY[]::text[] AS observer_reasons "
  const observerJoin = includeObserver
    ? "LEFT JOIN rvbbit.accel_autopilot_candidates o ON o.table_oid = c.oid " +
      "LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = c.oid "
    : ""

  return (
    "WITH " + BUSY_RELATIONS_CTE + ", materialized AS ( " +
    "SELECT table_oid, count(*)::bigint AS row_groups FROM rvbbit.row_groups GROUP BY table_oid " +
    "), heap_tables AS ( " +
    "SELECT c.oid::int8 AS oid, c.oid::regclass::text AS table_name, n.nspname AS schema, c.relname AS name, " +
    "GREATEST(c.reltuples, 0)::bigint AS row_estimate, " + safeTotalSize("c") + " AS size_bytes, " +
    "coalesce(s.seq_scan, 0)::bigint AS seq_scans, coalesce(s.seq_tup_read, 0)::bigint AS seq_rows, " +
    "coalesce(s.idx_scan, 0)::bigint AS idx_scans, " +
    "(coalesce(s.n_tup_ins, 0) + coalesce(s.n_tup_upd, 0) + coalesce(s.n_tup_del, 0))::bigint AS writes, " +
    "coalesce(s.n_tup_ins, 0)::bigint AS inserts, coalesce(s.n_tup_upd, 0)::bigint AS updates, " +
    "coalesce(s.n_tup_del, 0)::bigint AS deletes, coalesce(s.n_mod_since_analyze, 0)::bigint AS mod_since_analyze, " +
    "NULLIF(GREATEST(coalesce(s.last_vacuum, 'epoch'::timestamptz), coalesce(s.last_autovacuum, 'epoch'::timestamptz), " +
    "coalesce(s.last_analyze, 'epoch'::timestamptz), coalesce(s.last_autoanalyze, 'epoch'::timestamptz)), 'epoch'::timestamptz) AS last_maintenance_at, " +
    "coalesce(io.heap_blks_read, 0)::bigint AS heap_blks_read, coalesce(io.heap_blks_hit, 0)::bigint AS heap_blks_hit, " +
    "t.table_oid IS NOT NULL AS registered, coalesce(t.acceleration_enabled, false) AS acceleration_enabled, " +
    "coalesce(a.row_groups, 0)::bigint AS row_groups, " +
    "(coalesce(t.acceleration_enabled, false) AND coalesce(a.row_groups, 0) = 0 " +
    " AND GREATEST(coalesce(s.n_live_tup, 0), GREATEST(c.reltuples, 0)) > 0) AS baseline_missing " +
    observerSelect +
    "FROM pg_class c " +
    "JOIN pg_namespace n ON n.oid = c.relnamespace " +
    "LEFT JOIN rvbbit.tables t ON t.table_oid = c.oid " +
    "LEFT JOIN materialized a ON a.table_oid = c.oid " +
    observerJoin +
    "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid " +
    "LEFT JOIN pg_statio_user_tables io ON io.relid = c.oid " +
    "WHERE c.relkind IN ('r','p','m') " +
    "AND n.nspname NOT IN ('pg_catalog','information_schema','rvbbit') " +
    "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%' " +
    "AND (NOT coalesce(t.acceleration_enabled, false) OR (coalesce(a.row_groups, 0) = 0 " +
    "AND GREATEST(coalesce(s.n_live_tup, 0), GREATEST(c.reltuples, 0)) > 0)) " +
    "), " +
    matched +
    ", scored AS ( " +
    "SELECT h.*, coalesce(m.slow_queries, 0)::bigint AS slow_queries, coalesce(m.query_calls, 0)::bigint AS query_calls, " +
    "coalesce(m.total_ms, 0)::double precision AS total_ms, m.max_mean_ms, coalesce(m.query_samples, ARRAY[]::text[]) AS query_samples, " +
    "(h.writes::double precision / GREATEST(h.row_estimate, 1)) AS mutation_ratio, " +
    "(h.seq_rows::double precision / GREATEST(h.writes, 1)) AS read_write_ratio, " +
    "LEAST(95.0, GREATEST(0.0, " +
    "LEAST(28.0, ln(1 + GREATEST(coalesce(m.query_calls, 0), 0)) * 5.0) + " +
    "LEAST(32.0, coalesce(m.total_ms, 0) / 1000.0) + " +
    "LEAST(20.0, coalesce(m.slow_queries, 0) * 5.0) + " +
    "CASE WHEN h.row_estimate >= 100000 THEN 8.0 WHEN h.row_estimate >= 10000 THEN 4.0 ELSE 0.0 END - " +
    "LEAST(45.0, (h.writes::double precision / GREATEST(h.row_estimate, 1)) * 100.0) - " +
    "CASE WHEN h.mod_since_analyze::double precision > GREATEST(h.row_estimate, 1) * 0.25 THEN 12.0 ELSE 0.0 END " +
    "))::double precision AS score " +
    "FROM heap_tables h LEFT JOIN matched m ON m.oid = h.oid " +
    ") " +
    "SELECT table_name, schema, name, row_estimate, size_bytes, seq_scans, seq_rows, idx_scans, writes, " +
    "inserts, updates, deletes, mod_since_analyze, heap_blks_read, heap_blks_hit, slow_queries, query_calls, " +
    "total_ms, max_mean_ms, query_samples, mutation_ratio, read_write_ratio, score, registered, acceleration_enabled, " +
    "row_groups, baseline_missing, last_maintenance_at, observer_status, observer_read_days, " +
    "observer_last_read_at, observer_last_observed_at, observer_reasons, " +
    "CASE WHEN score >= 70 THEN 'strong' WHEN score >= 35 THEN 'watch' ELSE 'low' END AS recommendation, " +
    "CASE WHEN writes = 0 AND mod_since_analyze = 0 THEN 'stable' " +
    "WHEN mutation_ratio < 0.02 AND mod_since_analyze::double precision < GREATEST(row_estimate, 1) * 0.05 THEN 'low churn' " +
    "WHEN mutation_ratio < 0.15 THEN 'moderate churn' ELSE 'high churn' END AS write_profile, " +
    "concat_ws(' · ', " +
    "CASE WHEN baseline_missing THEN 'registered, no accelerator baseline' " +
    "WHEN registered AND NOT acceleration_enabled THEN 'registered but disabled' END, " +
    "CASE WHEN query_calls > 0 THEN query_calls || ' sampled SELECT call' || CASE WHEN query_calls = 1 THEN '' ELSE 's' END END, " +
    "CASE WHEN slow_queries > 0 THEN slow_queries || ' slow SELECT shape' || CASE WHEN slow_queries = 1 THEN '' ELSE 's' END END, " +
    "CASE WHEN total_ms > 0 THEN round(total_ms::numeric) || ' ms sampled SELECT time' END, " +
    "CASE WHEN writes = 0 THEN 'no observed writes' WHEN mutation_ratio < 0.02 THEN 'low write ratio' ELSE 'write-heavy' END " +
    ") AS reason " +
    "FROM scored " +
    "WHERE query_calls > 0 OR observer_read_days > 0 OR baseline_missing " +
    "ORDER BY score DESC, total_ms DESC, seq_rows DESC LIMIT 80"
  )
}

export async function fetchAccelerationCandidates(
  connectionId: string,
): Promise<{ rows: AccelerationCandidate[]; error?: string; pgStatStatements: boolean }> {
  const catalog = await runQuery(
    connectionId,
    "SELECT to_regclass('rvbbit.tables') IS NOT NULL AS rvbbit_present, " +
      "to_regclass('pg_stat_statements') IS NOT NULL AS pgss_present, " +
      "to_regclass('rvbbit.accel_read_days') IS NOT NULL AS observer_present",
  )
  if (!catalog.ok) return { rows: [], error: catalog.error, pgStatStatements: false }
  if (!bool(catalog.rows[0]?.rvbbit_present)) {
    return { rows: [], error: "rvbbit registry is not installed on this connection", pgStatStatements: false }
  }

  const wantsPgss = bool(catalog.rows[0]?.pgss_present)
  const hasObserver = bool(catalog.rows[0]?.observer_present)
  let res = await runQuery(connectionId, accelerationCandidateSql(wantsPgss, hasObserver))
  let usedPgss = wantsPgss
  if (!res.ok && wantsPgss) {
    res = await runQuery(connectionId, accelerationCandidateSql(false, hasObserver))
    usedPgss = false
  }
  if (!res.ok) return { rows: [], error: res.error, pgStatStatements: usedPgss }

  return {
    pgStatStatements: usedPgss,
    rows: res.rows.map((r) => ({
      tableName: String(r.table_name ?? ""),
      schema: String(r.schema ?? ""),
      name: String(r.name ?? ""),
      rowEstimate: num(r.row_estimate),
      sizeBytes: num(r.size_bytes),
      seqScans: num(r.seq_scans),
      seqRows: num(r.seq_rows),
      idxScans: num(r.idx_scans),
      writes: num(r.writes),
      inserts: num(r.inserts),
      updates: num(r.updates),
      deletes: num(r.deletes),
      modSinceAnalyze: num(r.mod_since_analyze),
      heapBlocksRead: num(r.heap_blks_read),
      heapBlocksHit: num(r.heap_blks_hit),
      slowQueries: num(r.slow_queries),
      queryCalls: num(r.query_calls),
      totalMs: num(r.total_ms),
      maxMeanMs: numOrNull(r.max_mean_ms),
      querySamples: pgArray(r.query_samples),
      mutationRatio: num(r.mutation_ratio),
      readWriteRatio: num(r.read_write_ratio),
      score: num(r.score),
      recommendation: String(r.recommendation ?? "low") as AccelerationCandidate["recommendation"],
      writeProfile: String(r.write_profile ?? ""),
      reason: String(r.reason ?? ""),
      registered: bool(r.registered),
      accelerationEnabled: bool(r.acceleration_enabled),
      rowGroups: num(r.row_groups),
      baselineMissing: bool(r.baseline_missing),
      lastMaintenanceAt: r.last_maintenance_at ? epoch(r.last_maintenance_at) : null,
      observerStatus: r.observer_status
        ? (String(r.observer_status) as AccelerationCandidate["observerStatus"])
        : null,
      observerReadDays: num(r.observer_read_days),
      observerLastReadAt: r.observer_last_read_at ? epoch(r.observer_last_read_at) : null,
      observerLastObservedAt: r.observer_last_observed_at ? epoch(r.observer_last_observed_at) : null,
      observerReasons: pgArray(r.observer_reasons),
    })),
  }
}

/** Load the acceleration autopilot's state (read-based since rvbbit 0486). */
export async function fetchAccelerationObserverStatus(
  connectionId: string,
): Promise<{ status: AccelerationObserverStatus; error?: string }> {
  const unavailable: AccelerationObserverStatus = {
    installed: false,
    mode: "unknown",
    readCounterAvailable: false,
    readsCountedSince: null,
    minReadDays: 3,
    counts: { total: 0, ready: 0, observing: 0, held: 0, managed: 0, parked: 0, released: 0 },
    latestRun: null,
  }
  const check = await runQuery(
    connectionId,
    "SELECT to_regprocedure('rvbbit.accel_autopilot_status()') IS NOT NULL AS installed",
  )
  if (!check.ok) return { status: unavailable, error: check.error }
  if (!bool(check.rows[0]?.installed)) return { status: unavailable }

  const res = await runQuery(connectionId, "SELECT rvbbit.accel_autopilot_status() AS status")
  if (!res.ok) return { status: unavailable, error: res.error }
  const doc = (res.rows[0]?.status as Record<string, unknown> | null) ?? {}
  const config = (doc.config as Record<string, unknown> | null) ?? {}
  const counts = (doc.counts as Record<string, unknown> | null) ?? {}
  const latest = (doc.latest_run as Record<string, unknown> | null) ?? null
  return {
    status: {
      installed: bool(doc.installed),
      mode: (doc.mode === "off" || doc.mode === "observe" || doc.mode === "act") ? doc.mode : "unknown",
      readCounterAvailable: bool(doc.read_counter_available),
      readsCountedSince: doc.reads_counted_since ? epoch(doc.reads_counted_since) : null,
      minReadDays: num(config.act_promote_min_read_days) || 3,
      counts: {
        total: num(counts.total),
        ready: num(counts.ready),
        observing: num(counts.observing),
        held: num(counts.held),
        managed: num(counts.managed),
        parked: num(counts.parked),
        released: num(counts.released),
      },
      latestRun: latest
        ? {
            runId: num(latest.run_id),
            status: String(latest.status ?? "unknown"),
            startedAt: latest.started_at ? epoch(latest.started_at) : null,
            finishedAt: latest.finished_at ? epoch(latest.finished_at) : null,
            tablesObserved: num(latest.tables_observed),
            readyCount: num(latest.ready_count),
            message: String(latest.message ?? ""),
            error: latest.error == null ? null : String(latest.error),
          }
        : null,
    },
  }
}

/** Run one autopilot observe pass: capture reads and classify tables. It never changes a table. */
export async function runAccelerationObserver(
  connectionId: string,
): Promise<{ ok: boolean; message: string; error?: string }> {
  const res = await runQuery(
    connectionId,
    "SELECT rvbbit.accel_autopilot_observe('datarabbit') AS result",
  )
  if (!res.ok) return { ok: false, message: "observer failed", error: res.error }
  const doc = (res.rows[0]?.result as Record<string, unknown> | null) ?? {}
  const status = String(doc.status ?? "unknown")
  if (status === "failed") {
    return { ok: false, message: "observer failed", error: String(doc.error ?? "unknown observer error") }
  }
  if (status === "busy" || status === "skipped") {
    return { ok: status === "skipped", message: String(doc.message ?? status) }
  }
  return {
    ok: true,
    message: String(
      doc.message ?? `${compactCount(num(doc.tables_observed))} tables classified · ${compactCount(num(doc.ready))} ready`,
    ),
  }
}

export async function enableAccelerationCandidate(
  connectionId: string,
  tableName: string,
  build: boolean,
  autoRefresh = false,
): Promise<{ ok: boolean; message: string; error?: string }> {
  const sql = build && autoRefresh
    ? `WITH enabled AS MATERIALIZED (` +
      `SELECT rvbbit.enable_table(${qIdent(tableName)}) AS enabled` +
      `), built AS MATERIALIZED (` +
      `SELECT enabled.enabled AS enabled, ` +
      `rvbbit.rebuild_acceleration(${qIdent(tableName)}, true) AS build FROM enabled` +
      `), policy AS MATERIALIZED (` +
      `SELECT rvbbit.set_accel_policy(${qIdent(tableName)}, ` +
      `strategy => 'continuous', freshness_target_secs => NULL, ` +
      `min_interval_secs => 900, daily_refresh_budget => NULL, ` +
      `full_rebuild_drift_ratio => 0.5, lance_separate => true, active => true, ` +
      `note => 'Auto-refresh after committed loads (DataRabbit Accelerate)') AS policy ` +
      `FROM built` +
      `) SELECT built.enabled, built.build, policy.policy FROM built CROSS JOIN policy`
    : build
      ? `WITH enabled AS MATERIALIZED (SELECT rvbbit.enable_table(${qIdent(tableName)}) AS enabled) ` +
        `SELECT enabled.enabled AS enabled, rvbbit.rebuild_acceleration(${qIdent(tableName)}, true) AS build FROM enabled`
      : `SELECT rvbbit.enable_table(${qIdent(tableName)}) AS enabled`
  const res = await runQuery(connectionId, sql)
  if (!res.ok) return { ok: false, message: "failed", error: res.error }
  const row = res.rows[0] ?? {}
  const enabled = (row.enabled as Record<string, unknown> | null) ?? {}
  const buildResult = (row.build as Record<string, unknown> | null) ?? null
  const policy = (row.policy as Record<string, unknown> | null) ?? null
  const enableStatus = String(enabled.status ?? "enabled")
  const buildStatus = buildResult ? String(buildResult.status ?? "ok") : null
  const rowsWritten = buildResult ? numOrNull(buildResult.rows_written ?? buildResult.visible_rows_estimate) : null
  const message = build
    ? `${enableStatus} · build ${buildStatus}${rowsWritten != null ? ` · ${compactCount(rowsWritten)} rows` : ""}` +
      (policy ? ` · ${String(policy.strategy ?? "scheduled")} auto-refresh` : "")
    : enableStatus
  return { ok: true, message }
}

export async function fetchWorkloadLayoutTables(
  connectionId: string,
): Promise<{ rows: WorkloadLayoutTable[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    "WITH rec AS ( " +
      "SELECT table_oid, count(*) AS recommendations, " +
      "count(*) FILTER (WHERE status = 'accepted') AS accepted, " +
      "count(*) FILTER (WHERE layout_status = 'ready') AS ready " +
      "FROM rvbbit.workload_layout_recommendation_status GROUP BY table_oid " +
      ") " +
      "SELECT c.oid::regclass::text AS table_name, n.nspname AS schema, c.relname AS name, " +
      "coalesce(f.parquet_rows, 0) AS parquet_rows, coalesce(f.row_groups, 0) AS row_groups, " +
      "coalesce(f.heap_seq_scans, 0) AS heap_seq_scans, " +
      "coalesce(f.shadow_heap_dirty, false) AS dirty, coalesce(f.op_running, false) AS op_running, " +
      "coalesce(rec.recommendations, 0) AS recommendations, " +
      "coalesce(rec.accepted, 0) AS accepted, coalesce(rec.ready, 0) AS ready " +
      "FROM rvbbit.tables t " +
      "JOIN pg_class c ON c.oid = t.table_oid " +
      "JOIN pg_namespace n ON n.oid = c.relnamespace " +
      "LEFT JOIN rvbbit.accel_freshness f ON f.table_oid = t.table_oid " +
      "LEFT JOIN rec ON rec.table_oid = t.table_oid " +
      "WHERE coalesce(t.acceleration_enabled, true) " +
      "ORDER BY n.nspname, c.relname",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    rows: res.rows.map((r) => ({
      tableName: String(r.table_name ?? ""),
      schema: String(r.schema ?? ""),
      name: String(r.name ?? ""),
      parquetRows: num(r.parquet_rows),
      rowGroups: num(r.row_groups),
      heapSeqScans: num(r.heap_seq_scans),
      dirty: bool(r.dirty),
      opRunning: bool(r.op_running),
      recommendations: num(r.recommendations),
      accepted: num(r.accepted),
      ready: num(r.ready),
    })),
  }
}

export async function fetchWorkloadLayoutRecommendations(
  connectionId: string,
): Promise<{ rows: WorkloadLayoutRecommendation[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    "SELECT table_name, table_oid::text AS table_oid, layout_kind, column_name, layout, status, " +
      "score, observations, weighted_ms, role_counts, sample_shapes, layout_status, " +
      "layout_rows, layout_files, reason, details, recommended_at, updated_at " +
      "FROM rvbbit.workload_layout_recommendation_status " +
      "ORDER BY table_name, " +
      "CASE status WHEN 'accepted' THEN 0 WHEN 'candidate' THEN 1 WHEN 'rejected' THEN 2 ELSE 3 END, " +
      "score DESC, updated_at DESC",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    rows: res.rows.map((r) => {
      const roles = (r.role_counts as Record<string, unknown> | null) ?? {}
      return {
        tableName: String(r.table_name ?? ""),
        tableOid: String(r.table_oid ?? ""),
        layoutKind: normalizeLayoutKind(r.layout_kind),
        columnName: String(r.column_name ?? ""),
        layout: String(r.layout ?? ""),
        status: normalizeLayoutStatus(r.status),
        score: num(r.score),
        observations: num(r.observations),
        weightedMs: num(r.weighted_ms),
        roleCounts: {
          where: num(roles.where),
          groupBy: num(roles.group_by),
          orderBy: num(roles.order_by),
          countDistinct: num(roles.count_distinct),
        },
        sampleShapes: pgArray(r.sample_shapes),
        layoutStatus: strOrNull(r.layout_status),
        layoutRows: numOrNull(r.layout_rows),
        layoutFiles: numOrNull(r.layout_files),
        reason: String(r.reason ?? ""),
        details: (r.details as Record<string, unknown> | null) ?? {},
        recommendedAt: epoch(r.recommended_at),
        updatedAt: epoch(r.updated_at),
      }
    }),
  }
}

export async function runWorkloadLayoutAdvisor(
  connectionId: string,
  tableName: string,
  lookbackHours: number,
  minObservations: number,
  maxRecommendations = 8,
): Promise<WorkloadAdvisorRun> {
  const res = await runQuery(
    connectionId,
    `SELECT rvbbit.recommend_workload_layouts(${qIdent(tableName)}, ` +
      `${Math.max(1, Math.floor(lookbackHours))}, ` +
      `${Math.max(1, Math.floor(minObservations))}, ` +
      `${Math.max(1, Math.floor(maxRecommendations))}, true) AS r`,
  )
  if (!res.ok) return { table: tableName, ok: false, recommendations: 0, matchedShapes: 0, error: res.error }
  const doc = (res.rows[0]?.r as Record<string, unknown> | null) ?? {}
  return {
    table: tableName,
    ok: bool(doc.ok),
    recommendations: Array.isArray(doc.recommendations) ? doc.recommendations.length : 0,
    matchedShapes: num(doc.sample_shapes_matched),
    error: bool(doc.ok) ? undefined : String(doc.reason ?? "advisor returned ok=false"),
  }
}

export async function setWorkloadLayoutRecommendationStatus(
  connectionId: string,
  tableName: string,
  layoutKind: WorkloadLayoutKind,
  columnName: string,
  status: "accepted" | "rejected",
): Promise<{ ok: boolean; error?: string }> {
  const fn = status === "accepted" ? "accept_workload_layout" : "reject_workload_layout"
  const res = await runQuery(
    connectionId,
    `SELECT rvbbit.${fn}(${qIdent(tableName)}, ${sqlStr(layoutKind)}, ${sqlStr(columnName)}) AS r`,
  )
  if (!res.ok) return { ok: false, error: res.error }
  const doc = (res.rows[0]?.r as Record<string, unknown> | null) ?? {}
  return bool(doc.ok) ? { ok: true } : { ok: false, error: String(doc.reason ?? "status update failed") }
}

export async function refreshAcceptedWorkloadLayouts(
  connectionId: string,
  tableName: string,
): Promise<WorkloadLayoutBuildRun> {
  const helper = await runQuery(
    connectionId,
    "SELECT EXISTS ( " +
      "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace " +
      "WHERE n.nspname = 'rvbbit' AND p.proname = 'build_accepted_workload_layouts' " +
      ") AS present",
  )
  const helperPresent = helper.ok && bool(helper.rows[0]?.present)
  const sql = helperPresent
    ? `SELECT rvbbit.build_accepted_workload_layouts(${qIdent(tableName)}) AS r`
    : `SELECT rvbbit.refresh_acceleration(${qIdent(tableName)}, true) AS r`
  const res = await runQuery(connectionId, sql)
  if (!res.ok) {
    return {
      table: tableName,
      ok: false,
      status: "failed",
      acceptedLayouts: null,
      readyLayouts: null,
      layoutRows: null,
      baseAction: null,
      message: "layout build failed",
      error: res.error,
    }
  }
  const doc = (res.rows[0]?.r as Record<string, unknown> | null) ?? {}
  const status = String(doc.status ?? "ok")
  const acceptedLayouts = numOrNull(doc.accepted_layouts)
  const readyLayouts = numOrNull(doc.ready_layouts)
  const layoutRows = numOrNull(doc.layout_rows ?? doc.variants_rows)
  const baseAction = strOrNull(doc.base_action ?? doc.operation)
  const ok = helperPresent
    ? status === "ok" || status === "partial"
    : status === "ok" || status === "noop"
  const message =
    readyLayouts != null && acceptedLayouts != null
      ? `${compactCount(readyLayouts)}/${compactCount(acceptedLayouts)} ready · ${compactCount(layoutRows ?? 0)} layout rows`
      : `${status} · ${compactCount(layoutRows ?? 0)} layout rows`
  return {
    table: tableName,
    ok,
    status,
    acceptedLayouts,
    readyLayouts,
    layoutRows,
    baseAction,
    message,
    error: ok ? undefined : String(doc.reason ?? "no accepted layouts were built"),
  }
}

export async function runRvbbitMigrate(connectionId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await runQuery(connectionId, "SELECT rvbbit.migrate()")
  return res.ok ? { ok: true } : { ok: false, error: res.error }
}

function normalizeLayoutKind(v: unknown): WorkloadLayoutKind {
  return String(v ?? "") === "hive" ? "hive" : "cluster"
}

function normalizeLayoutStatus(v: unknown): WorkloadLayoutStatus {
  const s = String(v ?? "")
  return s === "accepted" || s === "rejected" || s === "retired" ? s : "candidate"
}

// ── routing overlay (tested pins) + auto-optimizer ──────────────────────
// The overlay supersedes the named-profile model: a flat set of tested
// shape→engine pins layered on the base rules, grown by the benchmarker.

export interface OverlayPin {
  shapeKey: string
  shapeFamily: string
  engine: string
  baseEngine: string
  marginPct: number
  source: string
  testedAt: number
  sampleMs: Record<string, number> | null
}

export async function fetchOverlayPins(
  connectionId: string,
): Promise<{ rows: OverlayPin[]; error: string | null }> {
  const res = await runQuery(
    connectionId,
    "SELECT shape_key, shape_family, engine, base_engine, margin_pct, source, " +
      "tested_at, sample_ms FROM rvbbit.route_overlay WHERE enabled " +
      "ORDER BY margin_pct DESC NULLS LAST",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    error: null,
    rows: res.rows.map((r) => ({
      shapeKey: String(r.shape_key ?? ""),
      shapeFamily: String(r.shape_family ?? ""),
      engine: normalizeCandidate(String(r.engine ?? "")),
      baseEngine: normalizeCandidate(String(r.base_engine ?? "")),
      marginPct: num(r.margin_pct),
      source: String(r.source ?? ""),
      testedAt: epoch(r.tested_at),
      sampleMs: (r.sample_ms as Record<string, number> | null) ?? null,
    })),
  }
}

export interface OptimizeCandidate {
  shapeKey: string
  shapeFamily: string
  executions: number
  avgMs: number
  potentialMs: number
  lastSeen: number
  /** Engine this shape currently routes to (the base-rule target you'd be trying to beat). */
  engine: string
}

export async function fetchOptimizationCandidates(
  connectionId: string,
): Promise<{ rows: OptimizeCandidate[]; error: string | null }> {
  // Enrich each candidate with the engine it predominantly runs on today. Routing is
  // deterministic per shape, so this is effectively the base-rule target — the engine a
  // benchmark would have to beat. The candidate set itself stays sourced from the view.
  const res = await runQuery(
    connectionId,
    "SELECT c.shape_key, c.shape_family, c.executions, c.avg_ms, c.potential_ms, c.last_seen, " +
      "(SELECT mode() WITHIN GROUP (ORDER BY e.candidate) FROM rvbbit.route_executions e " +
      "WHERE e.shape_key = c.shape_key AND e.executed_at > now() - interval '1 day' " +
      "AND e.status = 'ok' AND e.candidate IS NOT NULL) AS engine " +
      "FROM rvbbit.route_optimization_candidates c LIMIT 50",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    error: null,
    rows: res.rows.map((r) => ({
      shapeKey: String(r.shape_key ?? ""),
      shapeFamily: String(r.shape_family ?? ""),
      executions: num(r.executions),
      avgMs: num(r.avg_ms),
      potentialMs: num(r.potential_ms),
      lastSeen: epoch(r.last_seen),
      engine: normalizeCandidate(String(r.engine ?? "")),
    })),
  }
}

export interface OptimizeRunDetail {
  shape_key: string
  pinned: boolean
  winner: string | null
  margin_pct: number | null
}

export interface OptimizeRun {
  runId: number
  startedAt: number
  finishedAt: number | null
  trigger: string
  shapesTested: number
  pinned: number
  errors: number
  elapsedSec: number | null
  detail: OptimizeRunDetail[] | null
}

export async function fetchOptimizeRuns(
  connectionId: string,
): Promise<{ rows: OptimizeRun[]; error: string | null }> {
  const res = await runQuery(
    connectionId,
    "SELECT run_id, started_at, finished_at, trigger, shapes_tested, pinned, errors, " +
      "elapsed_sec, detail FROM rvbbit.route_optimize_runs ORDER BY started_at DESC LIMIT 30",
  )
  if (!res.ok) return { rows: [], error: res.error }
  return {
    error: null,
    rows: res.rows.map((r) => ({
      runId: num(r.run_id),
      startedAt: epoch(r.started_at),
      finishedAt: r.finished_at == null ? null : epoch(r.finished_at),
      trigger: String(r.trigger ?? ""),
      shapesTested: num(r.shapes_tested),
      pinned: num(r.pinned),
      errors: num(r.errors),
      elapsedSec: numOrNull(r.elapsed_sec),
      detail: (r.detail as OptimizeRunDetail[] | null) ?? null,
    })),
  }
}

/** Trigger an auto-optimizer pass now (manual). Returns the function's JSON summary. */
export async function runOptimizeAuto(
  connectionId: string,
  topK: number,
  maxSeconds: number,
): Promise<{ ok: boolean; result: Record<string, unknown> | null; error: string | null }> {
  const res = await runQuery(
    connectionId,
    `SELECT rvbbit.route_optimize_auto(${Math.max(1, Math.trunc(topK))}, ${Math.max(1, Math.trunc(maxSeconds))}) AS r`,
  )
  if (!res.ok) return { ok: false, result: null, error: res.error }
  return { ok: true, result: (res.rows[0]?.r as Record<string, unknown>) ?? null, error: null }
}

/** Benchmark one query across all engines and pin it if a non-base engine wins. */
export async function runOptimizeQuery(
  connectionId: string,
  sql: string,
): Promise<{ ok: boolean; result: Record<string, unknown> | null; error: string | null }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.route_optimize_query(${sqlStr(sql)}) AS r`)
  if (!res.ok) return { ok: false, result: null, error: res.error }
  return { ok: true, result: (res.rows[0]?.r as Record<string, unknown>) ?? null, error: null }
}
