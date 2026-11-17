"use client"

export type McpIncomingRange = "24h" | "7d" | "30d"
export type McpIncomingSurface =
  | "all"
  | "calliope"
  | "google_chat"
  | "dashboard"
  | "gallery"
  | "direct_mcp"
  | "automation"
  | "other"

export interface McpIncomingOverview {
  calls: number
  callers: number
  tools: number
  objects: number
  errors: number
  sqlCalls: number
  rowsReturned: number
  avgMs: number | null
  p95Ms: number | null
  lastSeen: number | null
}

export interface McpIncomingActivity {
  id: number
  ts: number | null
  caller: string | null
  clientId: string | null
  actor: string
  channel: string
  clientApp: string
  surface: Exclude<McpIncomingSurface, "all">
  sessionRef: string | null
  provenance: Record<string, unknown>
  tool: string
  args: Record<string, unknown>
  ok: boolean | null
  error: Record<string, unknown> | null
  objects: string[]
  rows: number | null
  engine: string | null
  elapsedMs: number | null
  asOf: string | null
  resultSummary: Record<string, unknown> | null
  sql: string | null
  subject: string | null
  errorCode: string | null
  errorMessage: string | null
}

export interface McpIncomingCaller {
  actor: string
  caller: string | null
  clientId: string | null
  calls: number
  errors: number
  sqlCalls: number
  rowsReturned: number
  avgMs: number | null
  lastSeen: number | null
  topTool: string | null
  buckets: McpIncomingBucket[]
}

export type McpIncomingFlowKind = "caller" | "tool" | "object"

export interface McpIncomingFlowNode {
  kind: McpIncomingFlowKind
  key: string
  calls: number
  errors: number
}

export interface McpIncomingFlowEdge {
  fromKind: Exclude<McpIncomingFlowKind, "object">
  fromKey: string
  toKind: Exclude<McpIncomingFlowKind, "caller">
  toKey: string
  calls: number
  errors: number
}

export interface McpIncomingTool {
  tool: string
  calls: number
  errors: number
  sqlCalls: number
  rowsReturned: number
  avgMs: number | null
  p95Ms: number | null
  callers: number
  lastSeen: number | null
}

export interface McpIncomingObject {
  object: string
  touches: number
  callers: number
  tools: number
  errors: number
  lastTouch: number | null
}

export interface McpIncomingError {
  tool: string
  code: string
  message: string
  calls: number
  callers: number
  lastSeen: number | null
  sampleSql: string | null
}

export interface McpIncomingBucket {
  bucket: number | null
  calls: number
  errors: number
  sqlCalls: number
}

export interface McpIncomingSurfaceCount {
  surface: McpIncomingSurface
  calls: number
  callers: number
  errors: number
  lastSeen: number | null
}

export interface McpIncomingSnapshot {
  detected: boolean
  range: McpIncomingRange
  surface: McpIncomingSurface
  provenanceAvailable: boolean
  overview: McpIncomingOverview
  surfaces: McpIncomingSurfaceCount[]
  detailLimit: number
  recentActivities: McpIncomingActivity[]
  flowNodes: McpIncomingFlowNode[]
  flowEdges: McpIncomingFlowEdge[]
  callers: McpIncomingCaller[]
  tools: McpIncomingTool[]
  objects: McpIncomingObject[]
  errors: McpIncomingError[]
  buckets: McpIncomingBucket[]
  previousBuckets: McpIncomingBucket[]
  error?: string
}

const MCP_INCOMING_DETAIL_LIMIT = 200
const MCP_INCOMING_CALLER_BUCKETS = 18
const MCP_INCOMING_FLOW_CALLERS = 24
const MCP_INCOMING_FLOW_TOOLS = 32
const MCP_INCOMING_FLOW_OBJECTS = 48

interface QueryOk {
  ok: true
  columns: { name: string }[]
  rows: Array<Record<string, unknown>>
}

interface QueryErr {
  ok: false
  error: string
}

async function runQuery(connectionId: string, sql: string, rowLimit = 1000): Promise<QueryOk | QueryErr> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true, poolLane: "meta" }),
    })
    return (await res.json()) as QueryOk | QueryErr
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

function rangeSql(range: McpIncomingRange): string {
  switch (range) {
    case "24h": return "interval '24 hours'"
    case "7d": return "interval '168 hours'"
    case "30d": return "interval '720 hours'"
  }
}

function bucketSeconds(range: McpIncomingRange): number {
  switch (range) {
    case "24h": return 60 * 60
    case "7d": return 6 * 60 * 60
    case "30d": return 24 * 60 * 60
  }
}

function bucketCount(range: McpIncomingRange): number {
  switch (range) {
    case "24h": return 24
    case "7d": return 28
    case "30d": return 30
  }
}

export async function fetchMcpIncomingSnapshot(
  connectionId: string,
  range: McpIncomingRange = "24h",
  surface: McpIncomingSurface = "all",
): Promise<McpIncomingSnapshot> {
  const capabilities = await fetchActivityCapabilities(connectionId)
  if (!capabilities.ok) return emptySnapshot(range, surface, false, capabilities.error)
  if (!capabilities.value.detected) return emptySnapshot(range, surface, false)

  const interval = rangeSql(range)
  const step = bucketSeconds(range)
  const count = bucketCount(range)
  const shape = activitySqlShape(capabilities.value)
  const filter = surfacePredicate(shape.surface, surface)
  const [overview, surfaces, activities, flow, callers, callerBuckets, tools, objects, errors, timeline] = await Promise.all([
    fetchOverview(connectionId, interval, filter),
    fetchSurfaceCounts(connectionId, interval, shape.surface),
    fetchActivities(connectionId, interval, filter, shape),
    fetchFlow(connectionId, interval, filter),
    fetchCallers(connectionId, interval, filter),
    fetchCallerBuckets(connectionId, interval, filter),
    fetchTools(connectionId, interval, filter),
    fetchObjects(connectionId, interval, filter),
    fetchErrors(connectionId, interval, filter),
    fetchBuckets(connectionId, interval, step, count, filter),
  ])

  const firstErr = [overview, surfaces, activities, flow, callers, callerBuckets, tools, objects, errors, timeline].find((r) => !r.ok)
  const bucketsByCaller = callerBuckets.ok ? callerBuckets.value : new Map<string, McpIncomingBucket[]>()
  return {
    detected: true,
    range,
    surface,
    provenanceAvailable: capabilities.value.channel && capabilities.value.clientApp,
    overview: overview.ok ? overview.value : emptyOverview(),
    surfaces: surfaces.ok ? surfaces.value : [],
    detailLimit: MCP_INCOMING_DETAIL_LIMIT,
    recentActivities: activities.ok ? activities.value : [],
    flowNodes: flow.ok ? flow.value.nodes : [],
    flowEdges: flow.ok ? flow.value.edges : [],
    callers: callers.ok
      ? callers.value.map((caller) => ({ ...caller, buckets: bucketsByCaller.get(caller.actor) ?? [] }))
      : [],
    tools: tools.ok ? tools.value : [],
    objects: objects.ok ? objects.value : [],
    errors: errors.ok ? errors.value : [],
    buckets: timeline.ok ? timeline.value.current : [],
    previousBuckets: timeline.ok ? timeline.value.previous : [],
    error: firstErr && !firstErr.ok ? firstErr.error : undefined,
  }
}

type FetchResult<T> = { ok: true; value: T } | { ok: false; error: string }

interface McpActivityCapabilities {
  detected: boolean
  channel: boolean
  clientApp: boolean
  sessionRef: boolean
  provenance: boolean
}

interface McpActivitySqlShape {
  channel: string
  clientApp: string
  sessionRef: string
  provenance: string
  surface: string
}

async function fetchActivityCapabilities(
  connectionId: string,
): Promise<FetchResult<McpActivityCapabilities>> {
  const res = await runQuery(
    connectionId,
    `SELECT to_regclass('rvbbit.mcp_activity') IS NOT NULL AS detected,
            EXISTS (
              SELECT 1 FROM pg_attribute
              WHERE attrelid=to_regclass('rvbbit.mcp_activity')
                AND attname='channel' AND NOT attisdropped
            ) AS has_channel,
            EXISTS (
              SELECT 1 FROM pg_attribute
              WHERE attrelid=to_regclass('rvbbit.mcp_activity')
                AND attname='client_app' AND NOT attisdropped
            ) AS has_client_app,
            EXISTS (
              SELECT 1 FROM pg_attribute
              WHERE attrelid=to_regclass('rvbbit.mcp_activity')
                AND attname='session_ref' AND NOT attisdropped
            ) AS has_session_ref,
            EXISTS (
              SELECT 1 FROM pg_attribute
              WHERE attrelid=to_regclass('rvbbit.mcp_activity')
                AND attname='provenance' AND NOT attisdropped
            ) AS has_provenance`,
    1,
  )
  if (!res.ok) return res
  const row = res.rows[0] ?? {}
  return {
    ok: true,
    value: {
      detected: bool(row.detected),
      channel: bool(row.has_channel),
      clientApp: bool(row.has_client_app),
      sessionRef: bool(row.has_session_ref),
      provenance: bool(row.has_provenance),
    },
  }
}

function activitySqlShape(capabilities: McpActivityCapabilities): McpActivitySqlShape {
  const args = "coalesce(args, '{}'::jsonb)"
  const legacyChannel = "CASE WHEN tool IN ('artifact_view','dashboard_query') THEN 'web' ELSE 'legacy_unknown' END"
  const channel = capabilities.channel
    ? `coalesce(nullif(channel, ''), ${legacyChannel})`
    : legacyChannel
  const legacyClientApp = `CASE
    WHEN tool='artifact_view' AND ${args}->>'surface'='calliope' THEN 'calliope'
    WHEN tool='artifact_view' AND ${args}->>'surface'='gallery' THEN 'gallery'
    WHEN tool IN ('artifact_view','dashboard_query') THEN 'dashboard'
    ELSE 'unknown'
  END`
  const clientApp = capabilities.clientApp
    ? `coalesce(nullif(client_app, ''), ${legacyClientApp})`
    : legacyClientApp
  const surface = `CASE
    WHEN tool='artifact_view' AND ${args}->>'surface'='calliope' THEN 'calliope'
    WHEN tool='artifact_view' AND ${args}->>'surface'='gallery' THEN 'gallery'
    WHEN tool='artifact_view' THEN 'dashboard'
    WHEN (${channel})='google_chat' THEN 'google_chat'
    WHEN (${channel})='automation' THEN 'automation'
    WHEN (${channel})='direct_mcp' THEN 'direct_mcp'
    WHEN (${channel})='web' AND (${clientApp}) IN ('calliope','calliope_workflow') THEN 'calliope'
    WHEN (${channel})='web' AND (${clientApp}) IN ('gallery','home') THEN 'gallery'
    WHEN tool='dashboard_query' OR ((${channel})='web' AND (${clientApp})='dashboard') THEN 'dashboard'
    ELSE 'other'
  END`
  return {
    channel,
    clientApp,
    sessionRef: capabilities.sessionRef ? "session_ref" : "NULL::text",
    provenance: capabilities.provenance ? "coalesce(provenance, '{}'::jsonb)" : "'{}'::jsonb",
    surface,
  }
}

function surfacePredicate(expression: string, surface: McpIncomingSurface): string {
  if (surface === "all") return ""
  return `AND (${expression})='${surface}'`
}

async function fetchOverview(connectionId: string, interval: string, filter: string): Promise<FetchResult<McpIncomingOverview>> {
  const res = await runQuery(
    connectionId,
    `WITH base AS (
       SELECT *
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
       ${filter}
     )
     SELECT count(*)::bigint AS calls,
            count(DISTINCT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous'))::bigint AS callers,
            count(DISTINCT tool)::bigint AS tools,
            coalesce((
              SELECT count(DISTINCT obj)::bigint
              FROM base b
              CROSS JOIN LATERAL unnest(coalesce(b.objects, ARRAY[]::text[])) AS obj
            ), 0)::bigint AS objects,
            count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors,
            count(*) FILTER (WHERE coalesce(args, '{}'::jsonb) ? 'sql')::bigint AS sql_calls,
            coalesce(sum(rows), 0)::bigint AS rows_returned,
            round(avg(elapsed_ms))::bigint AS avg_ms,
            percentile_disc(0.95) WITHIN GROUP (ORDER BY elapsed_ms) FILTER (WHERE elapsed_ms IS NOT NULL) AS p95_ms,
            max(ts) AS last_seen
     FROM base`,
    1,
  )
  if (!res.ok) return res
  const r = res.rows[0] ?? {}
  return {
    ok: true,
    value: {
      calls: num(r.calls),
      callers: num(r.callers),
      tools: num(r.tools),
      objects: num(r.objects),
      errors: num(r.errors),
      sqlCalls: num(r.sql_calls),
      rowsReturned: num(r.rows_returned),
      avgMs: numOrNull(r.avg_ms),
      p95Ms: numOrNull(r.p95_ms),
      lastSeen: epoch(r.last_seen),
    },
  }
}

async function fetchSurfaceCounts(
  connectionId: string,
  interval: string,
  surfaceExpression: string,
): Promise<FetchResult<McpIncomingSurfaceCount[]>> {
  const res = await runQuery(
    connectionId,
    `WITH classified AS (
       SELECT ${surfaceExpression} AS surface,
              coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS actor,
              ok,
              ts
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
     )
     SELECT CASE WHEN GROUPING(surface)=1 THEN 'all' ELSE surface END AS surface,
            count(*)::bigint AS calls,
            count(DISTINCT actor)::bigint AS callers,
            count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors,
            max(ts) AS last_seen
     FROM classified
     GROUP BY GROUPING SETS ((surface), ())
     ORDER BY calls DESC`,
    20,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((row) => ({
      surface: normalizeSurface(row.surface),
      calls: num(row.calls),
      callers: num(row.callers),
      errors: num(row.errors),
      lastSeen: epoch(row.last_seen),
    })),
  }
}

async function fetchActivities(
  connectionId: string,
  interval: string,
  filter: string,
  shape: McpActivitySqlShape,
): Promise<FetchResult<McpIncomingActivity[]>> {
  const res = await runQuery(
    connectionId,
    `SELECT id,
            ts,
            caller,
            client_id,
            coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS actor,
            ${shape.channel} AS channel,
            ${shape.clientApp} AS client_app,
            ${shape.surface} AS surface,
            ${shape.sessionRef} AS session_ref,
            ${shape.provenance} AS provenance,
            tool,
            coalesce(args, '{}'::jsonb) AS args,
            ok,
            error,
            coalesce(objects, ARRAY[]::text[]) AS objects,
            rows,
            engine,
            elapsed_ms,
            as_of,
            result_summary,
            args->>'sql' AS sql,
            CASE
              WHEN coalesce(args, '{}'::jsonb) ? 'query' THEN args->>'query'
              WHEN coalesce(args, '{}'::jsonb) ? 'metric' THEN args->>'metric'
              WHEN coalesce(args, '{}'::jsonb) ? 'name' THEN args->>'name'
              WHEN coalesce(args, '{}'::jsonb) ? 'table' THEN args->>'table'
              WHEN coalesce(args, '{}'::jsonb) ? 'dashboard' THEN args->>'dashboard'
              WHEN coalesce(args, '{}'::jsonb) ? 'subject' THEN args->>'subject'
              WHEN coalesce(args, '{}'::jsonb) ? 'sql' THEN left(regexp_replace(args->>'sql', '\\s+', ' ', 'g'), 240)
              WHEN array_length(objects, 1) > 0 THEN array_to_string(objects, ', ')
              ELSE NULL
            END AS subject,
            error->>'code' AS error_code,
            error->>'message' AS error_message
     FROM rvbbit.mcp_activity
     WHERE ts >= now() - ${interval}
     ${filter}
     ORDER BY ts DESC NULLS LAST, id DESC
     LIMIT ${MCP_INCOMING_DETAIL_LIMIT}`,
    MCP_INCOMING_DETAIL_LIMIT,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((r) => ({
      id: num(r.id),
      ts: epoch(r.ts),
      caller: strOrNull(r.caller),
      clientId: strOrNull(r.client_id),
      actor: str(r.actor) || "anonymous",
      channel: str(r.channel) || "legacy_unknown",
      clientApp: str(r.client_app) || "unknown",
      surface: normalizeDetailSurface(r.surface),
      sessionRef: strOrNull(r.session_ref),
      provenance: asObject(r.provenance),
      tool: str(r.tool) || "(unknown)",
      args: asObject(r.args),
      ok: boolOrNull(r.ok),
      error: r.error == null ? null : asObject(r.error),
      objects: asStringArray(r.objects),
      rows: numOrNull(r.rows),
      engine: strOrNull(r.engine),
      elapsedMs: numOrNull(r.elapsed_ms),
      asOf: strOrNull(r.as_of),
      resultSummary: r.result_summary == null ? null : asObject(r.result_summary),
      sql: strOrNull(r.sql),
      subject: strOrNull(r.subject),
      errorCode: strOrNull(r.error_code),
      errorMessage: strOrNull(r.error_message),
    })),
  }
}

// The graph is time-bounded, not activity-row-bounded. Keep the raw ledger
// small for inspection, but aggregate every matching call before applying the
// display-oriented node caps below.
async function fetchFlow(
  connectionId: string,
  interval: string,
  filter: string,
): Promise<FetchResult<{ nodes: McpIncomingFlowNode[]; edges: McpIncomingFlowEdge[] }>> {
  const res = await runQuery(
    connectionId,
    `WITH base AS (
       SELECT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS actor,
              coalesce(nullif(tool, ''), '(unknown)') AS tool,
              coalesce(objects, ARRAY[]::text[]) AS objects,
              ok
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
       ${filter}
     ),
     object_events AS (
       SELECT b.tool, b.ok, obj AS object
       FROM base b
       CROSS JOIN LATERAL unnest(b.objects) AS x(obj)
       WHERE obj IS NOT NULL AND obj <> ''
     ),
     caller_nodes AS (
       SELECT actor AS key,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors
       FROM base
       GROUP BY actor
     ),
     tool_nodes AS (
       SELECT tool AS key,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors
       FROM base
       GROUP BY tool
     ),
     object_nodes AS (
       SELECT object AS key,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors
       FROM object_events
       GROUP BY object
     ),
     ranked_callers AS (
       SELECT * FROM caller_nodes
       ORDER BY calls DESC, key
       LIMIT ${MCP_INCOMING_FLOW_CALLERS}
     ),
     ranked_tools AS (
       SELECT * FROM tool_nodes
       ORDER BY calls DESC, key
       LIMIT ${MCP_INCOMING_FLOW_TOOLS}
     ),
     ranked_objects AS (
       SELECT * FROM object_nodes
       ORDER BY calls DESC, key
       LIMIT ${MCP_INCOMING_FLOW_OBJECTS}
     ),
     caller_tool_edges AS (
       SELECT b.actor AS from_key,
              b.tool AS to_key,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE b.ok IS FALSE)::bigint AS errors
       FROM base b
       JOIN ranked_callers c ON c.key = b.actor
       JOIN ranked_tools t ON t.key = b.tool
       GROUP BY b.actor, b.tool
     ),
     tool_object_edges AS (
       SELECT e.tool AS from_key,
              e.object AS to_key,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE e.ok IS FALSE)::bigint AS errors
       FROM object_events e
       JOIN ranked_tools t ON t.key = e.tool
       JOIN ranked_objects o ON o.key = e.object
       GROUP BY e.tool, e.object
     ),
     records AS (
       SELECT 'node'::text AS record_kind,
              'caller'::text AS from_kind,
              key AS from_key,
              NULL::text AS to_kind,
              NULL::text AS to_key,
              calls,
              errors
       FROM ranked_callers
       UNION ALL
       SELECT 'node', 'tool', key, NULL::text, NULL::text, calls, errors
       FROM ranked_tools
       UNION ALL
       SELECT 'node', 'object', key, NULL::text, NULL::text, calls, errors
       FROM ranked_objects
       UNION ALL
       SELECT 'edge', 'caller', from_key, 'tool', to_key, calls, errors
       FROM caller_tool_edges
       UNION ALL
       SELECT 'edge', 'tool', from_key, 'object', to_key, calls, errors
       FROM tool_object_edges
     )
     SELECT record_kind, from_kind, from_key, to_kind, to_key, calls, errors
     FROM records
     ORDER BY record_kind DESC, calls DESC, from_kind, from_key, to_key`,
    3000,
  )
  if (!res.ok) return res
  const nodes: McpIncomingFlowNode[] = []
  const edges: McpIncomingFlowEdge[] = []
  for (const row of res.rows) {
    const fromKind = flowKind(row.from_kind)
    if (!fromKind) continue
    if (str(row.record_kind) === "node") {
      nodes.push({
        kind: fromKind,
        key: str(row.from_key),
        calls: num(row.calls),
        errors: num(row.errors),
      })
      continue
    }
    const toKind = flowKind(row.to_kind)
    if ((fromKind !== "caller" && fromKind !== "tool") || (toKind !== "tool" && toKind !== "object")) continue
    edges.push({
      fromKind,
      fromKey: str(row.from_key),
      toKind,
      toKey: str(row.to_key),
      calls: num(row.calls),
      errors: num(row.errors),
    })
  }
  return { ok: true, value: { nodes, edges } }
}

async function fetchCallers(connectionId: string, interval: string, filter: string): Promise<FetchResult<McpIncomingCaller[]>> {
  const res = await runQuery(
    connectionId,
    `WITH base AS (
       SELECT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS effective_actor,
              caller,
              client_id,
              ok,
              args,
              rows,
              elapsed_ms,
              ts,
              tool
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
       ${filter}
     ),
     grouped AS (
       SELECT effective_actor AS actor,
              max(caller) FILTER (WHERE caller IS NOT NULL AND caller <> '') AS caller,
              max(client_id) FILTER (WHERE client_id IS NOT NULL AND client_id <> '') AS client_id,
              count(*)::bigint AS calls,
              count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors,
              count(*) FILTER (WHERE coalesce(args, '{}'::jsonb) ? 'sql')::bigint AS sql_calls,
              coalesce(sum(rows), 0)::bigint AS rows_returned,
              round(avg(elapsed_ms))::bigint AS avg_ms,
              max(ts) AS last_seen
       FROM base
       GROUP BY effective_actor
     ),
     top_tool AS (
       SELECT DISTINCT ON (effective_actor) effective_actor AS actor, tool
       FROM (
         SELECT effective_actor, tool, count(*) AS calls
         FROM base
         GROUP BY effective_actor, tool
       ) x
       ORDER BY effective_actor, calls DESC, tool
     )
     SELECT grouped.*, top_tool.tool AS top_tool
     FROM grouped
     LEFT JOIN top_tool USING (actor)
     ORDER BY grouped.calls DESC, grouped.last_seen DESC NULLS LAST
     LIMIT 50`,
    50,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((r) => ({
      actor: str(r.actor) || "anonymous",
      caller: strOrNull(r.caller),
      clientId: strOrNull(r.client_id),
      calls: num(r.calls),
      errors: num(r.errors),
      sqlCalls: num(r.sql_calls),
      rowsReturned: num(r.rows_returned),
      avgMs: numOrNull(r.avg_ms),
      lastSeen: epoch(r.last_seen),
      topTool: strOrNull(r.top_tool),
      buckets: [],
    })),
  }
}

async function fetchCallerBuckets(
  connectionId: string,
  interval: string,
  filter: string,
): Promise<FetchResult<Map<string, McpIncomingBucket[]>>> {
  const res = await runQuery(
    connectionId,
    `WITH bounds AS (
       SELECT now() - ${interval} AS start_at,
              now() AS end_at,
              (${interval}) / ${MCP_INCOMING_CALLER_BUCKETS} AS step
     ),
     base AS (
       SELECT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS actor,
              ts,
              ok,
              args
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
       ${filter}
     ),
     ranked AS (
       SELECT actor, count(*)::bigint AS calls
       FROM base
       GROUP BY actor
       ORDER BY calls DESC, actor
       LIMIT 50
     ),
     slots AS (
       SELECT n AS bucket_index,
              b.start_at + n * b.step AS bucket_start,
              b.start_at + (n + 1) * b.step AS bucket_end
       FROM bounds b
       CROSS JOIN generate_series(0, ${MCP_INCOMING_CALLER_BUCKETS - 1}) AS g(n)
     )
     SELECT r.actor,
            s.bucket_index,
            s.bucket_start AS bucket,
            count(a.ts)::bigint AS calls,
            count(a.ts) FILTER (WHERE a.ok IS FALSE)::bigint AS errors,
            count(a.ts) FILTER (WHERE coalesce(a.args, '{}'::jsonb) ? 'sql')::bigint AS sql_calls
     FROM ranked r
     CROSS JOIN slots s
     LEFT JOIN base a
       ON a.actor = r.actor
      AND a.ts >= s.bucket_start
      AND a.ts < s.bucket_end
     GROUP BY r.actor, r.calls, s.bucket_index, s.bucket_start
     ORDER BY r.calls DESC, r.actor, s.bucket_index`,
    1000,
  )
  if (!res.ok) return res
  const buckets = new Map<string, McpIncomingBucket[]>()
  for (const row of res.rows) {
    const actor = str(row.actor) || "anonymous"
    const values = buckets.get(actor) ?? []
    values.push({
      bucket: epoch(row.bucket),
      calls: num(row.calls),
      errors: num(row.errors),
      sqlCalls: num(row.sql_calls),
    })
    buckets.set(actor, values)
  }
  return { ok: true, value: buckets }
}

async function fetchTools(connectionId: string, interval: string, filter: string): Promise<FetchResult<McpIncomingTool[]>> {
  const res = await runQuery(
    connectionId,
    `SELECT tool,
            count(*)::bigint AS calls,
            count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors,
            count(*) FILTER (WHERE coalesce(args, '{}'::jsonb) ? 'sql')::bigint AS sql_calls,
            coalesce(sum(rows), 0)::bigint AS rows_returned,
            round(avg(elapsed_ms))::bigint AS avg_ms,
            percentile_disc(0.95) WITHIN GROUP (ORDER BY elapsed_ms) FILTER (WHERE elapsed_ms IS NOT NULL) AS p95_ms,
            count(DISTINCT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous'))::bigint AS callers,
            max(ts) AS last_seen
     FROM rvbbit.mcp_activity
     WHERE ts >= now() - ${interval}
     ${filter}
     GROUP BY tool
     ORDER BY calls DESC, last_seen DESC NULLS LAST
     LIMIT 50`,
    50,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((r) => ({
      tool: str(r.tool) || "(unknown)",
      calls: num(r.calls),
      errors: num(r.errors),
      sqlCalls: num(r.sql_calls),
      rowsReturned: num(r.rows_returned),
      avgMs: numOrNull(r.avg_ms),
      p95Ms: numOrNull(r.p95_ms),
      callers: num(r.callers),
      lastSeen: epoch(r.last_seen),
    })),
  }
}

async function fetchObjects(connectionId: string, interval: string, filter: string): Promise<FetchResult<McpIncomingObject[]>> {
  const res = await runQuery(
    connectionId,
    `WITH base AS (
       SELECT *
       FROM rvbbit.mcp_activity
       WHERE ts >= now() - ${interval}
       ${filter}
     ),
     exploded AS (
       SELECT obj AS object,
              coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous') AS actor,
              tool,
              ok,
              ts
       FROM base
       CROSS JOIN LATERAL unnest(coalesce(objects, ARRAY[]::text[])) AS obj
     )
     SELECT object,
            count(*)::bigint AS touches,
            count(DISTINCT actor)::bigint AS callers,
            count(DISTINCT tool)::bigint AS tools,
            count(*) FILTER (WHERE ok IS FALSE)::bigint AS errors,
            max(ts) AS last_touch
     FROM exploded
     GROUP BY object
     ORDER BY touches DESC, last_touch DESC NULLS LAST
     LIMIT 80`,
    80,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((r) => ({
      object: str(r.object),
      touches: num(r.touches),
      callers: num(r.callers),
      tools: num(r.tools),
      errors: num(r.errors),
      lastTouch: epoch(r.last_touch),
    })),
  }
}

async function fetchErrors(connectionId: string, interval: string, filter: string): Promise<FetchResult<McpIncomingError[]>> {
  const res = await runQuery(
    connectionId,
    `SELECT tool,
            coalesce(nullif(error->>'code', ''), 'ERROR') AS code,
            left(coalesce(nullif(error->>'message', ''), error::text, 'call failed'), 280) AS message,
            count(*)::bigint AS calls,
            count(DISTINCT coalesce(nullif(caller, ''), nullif(client_id, ''), 'anonymous'))::bigint AS callers,
            max(ts) AS last_seen,
            (array_agg(args->>'sql' ORDER BY ts DESC NULLS LAST, id DESC) FILTER (WHERE args ? 'sql'))[1] AS sample_sql
     FROM rvbbit.mcp_activity
     WHERE ts >= now() - ${interval}
       ${filter}
       AND ok IS FALSE
     GROUP BY tool, code, message
     ORDER BY calls DESC, last_seen DESC NULLS LAST
     LIMIT 50`,
    50,
  )
  if (!res.ok) return res
  return {
    ok: true,
    value: res.rows.map((r) => ({
      tool: str(r.tool) || "(unknown)",
      code: str(r.code) || "ERROR",
      message: str(r.message) || "call failed",
      calls: num(r.calls),
      callers: num(r.callers),
      lastSeen: epoch(r.last_seen),
      sampleSql: strOrNull(r.sample_sql),
    })),
  }
}

async function fetchBuckets(
  connectionId: string,
  interval: string,
  seconds: number,
  count: number,
  filter: string,
): Promise<FetchResult<{ current: McpIncomingBucket[]; previous: McpIncomingBucket[] }>> {
  const res = await runQuery(
    connectionId,
    `WITH bounds AS (
       SELECT now() - ${interval} AS current_start,
              now() - (${interval} * 2) AS previous_start,
              ${seconds} * interval '1 second' AS step
     ),
     slots AS (
       SELECT 'current'::text AS period,
              n AS bucket_index,
              b.current_start + n * b.step AS bucket_start,
              b.current_start + (n + 1) * b.step AS bucket_end
       FROM bounds b
       CROSS JOIN generate_series(0, ${count - 1}) AS g(n)
       UNION ALL
       SELECT 'previous'::text AS period,
              n AS bucket_index,
              b.previous_start + n * b.step AS bucket_start,
              b.previous_start + (n + 1) * b.step AS bucket_end
       FROM bounds b
       CROSS JOIN generate_series(0, ${count - 1}) AS g(n)
     )
     SELECT s.period,
            s.bucket_index,
            s.bucket_start AS bucket,
            count(a.id)::bigint AS calls,
            count(a.id) FILTER (WHERE a.ok IS FALSE)::bigint AS errors,
            count(a.id) FILTER (WHERE coalesce(a.args, '{}'::jsonb) ? 'sql')::bigint AS sql_calls
     FROM slots s
     LEFT JOIN rvbbit.mcp_activity a
       ON a.ts >= s.bucket_start
      AND a.ts < s.bucket_end
      ${filter}
     GROUP BY s.period, s.bucket_index, s.bucket_start
     ORDER BY s.period, s.bucket_index`,
    1000,
  )
  if (!res.ok) return res
  const current: McpIncomingBucket[] = []
  const previous: McpIncomingBucket[] = []
  for (const r of res.rows) {
    const bucket = {
      bucket: epoch(r.bucket),
      calls: num(r.calls),
      errors: num(r.errors),
      sqlCalls: num(r.sql_calls),
    }
    if (str(r.period) === "previous") previous.push(bucket)
    else current.push(bucket)
  }
  return {
    ok: true,
    value: { current, previous },
  }
}

function emptySnapshot(
  range: McpIncomingRange,
  surface: McpIncomingSurface,
  detected: boolean,
  error?: string,
): McpIncomingSnapshot {
  return {
    detected,
    range,
    surface,
    provenanceAvailable: false,
    overview: emptyOverview(),
    surfaces: [],
    detailLimit: MCP_INCOMING_DETAIL_LIMIT,
    recentActivities: [],
    flowNodes: [],
    flowEdges: [],
    callers: [],
    tools: [],
    objects: [],
    errors: [],
    buckets: [],
    previousBuckets: [],
    error,
  }
}

const SURFACE_KEYS = new Set<McpIncomingSurface>([
  "all",
  "calliope",
  "google_chat",
  "dashboard",
  "gallery",
  "direct_mcp",
  "automation",
  "other",
])

function normalizeSurface(value: unknown): McpIncomingSurface {
  const key = str(value) as McpIncomingSurface
  return SURFACE_KEYS.has(key) ? key : "other"
}

function normalizeDetailSurface(value: unknown): Exclude<McpIncomingSurface, "all"> {
  const key = normalizeSurface(value)
  return key === "all" ? "other" : key
}

function flowKind(value: unknown): McpIncomingFlowKind | null {
  const key = str(value)
  return key === "caller" || key === "tool" || key === "object" ? key : null
}

function emptyOverview(): McpIncomingOverview {
  return {
    calls: 0,
    callers: 0,
    tools: 0,
    objects: 0,
    errors: 0,
    sqlCalls: 0,
    rowsReturned: 0,
    avgMs: null,
    p95Ms: null,
    lastSeen: null,
  }
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value) return {}
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value) as unknown
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }
  return typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (typeof value === "string") {
    return value
      .replace(/^\{|\}$/g, "")
      .split(",")
      .map((s) => s.replace(/^"|"$/g, "").trim())
      .filter(Boolean)
  }
  return []
}

function epoch(value: unknown): number | null {
  if (value == null) return null
  if (typeof value === "number") {
    const ms = value > 10_000_000_000 ? value : value * 1000
    return Number.isFinite(ms) ? ms : null
  }
  const t = new Date(String(value)).getTime()
  return Number.isFinite(t) ? t : null
}

function num(value: unknown): number {
  const n = Number(value ?? 0)
  return Number.isFinite(n) ? n : 0
}

function numOrNull(value: unknown): number | null {
  if (value == null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function str(value: unknown): string {
  return value == null ? "" : String(value)
}

function strOrNull(value: unknown): string | null {
  if (value == null) return null
  const s = String(value)
  return s.length ? s : null
}

function bool(value: unknown): boolean {
  return value === true || value === "true" || value === "t" || value === 1 || value === "1"
}

function boolOrNull(value: unknown): boolean | null {
  if (value == null) return null
  return bool(value)
}
