"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { ComponentType } from "react"
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileCode2,
  Filter,
  Globe,
  Pause,
  Play,
  RefreshCw,
  Search,
  Table2,
  Users,
  X,
} from "@/lib/icons"
import {
  fetchMcpIncomingSnapshot,
  type McpIncomingActivity,
  type McpIncomingBucket,
  type McpIncomingCaller,
  type McpIncomingError,
  type McpIncomingFlowEdge,
  type McpIncomingFlowNode,
  type McpIncomingObject,
  type McpIncomingRange,
  type McpIncomingSnapshot,
  type McpIncomingSurface,
  type McpIncomingSurfaceCount,
  type McpIncomingTool,
} from "@/lib/rvbbit/mcp-incoming"
import { fmtAgo, fmtCount, fmtMs, Panel } from "./instruments"
import { CodePreview } from "./code-preview"
import { cn } from "@/lib/utils"

type StatusFilter = "all" | "ok" | "error"

interface McpIncomingWindowProps {
  activeConnectionId: string | null
  hasRvbbit: boolean
  workspaceActive: boolean
  onOpenSql: (sql: string, title: string) => void
}

const RANGES: { key: McpIncomingRange; label: string }[] = [
  { key: "24h", label: "24h" },
  { key: "7d", label: "7d" },
  { key: "30d", label: "30d" },
]

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "ok", label: "OK" },
  { key: "error", label: "Errors" },
]

const SURFACE_FILTERS: {
  key: McpIncomingSurface
  label: string
  description: string
  color: string
}[] = [
  { key: "all", label: "All", description: "Every observed surface", color: "oklch(71% 0.17 205)" },
  { key: "calliope", label: "Calliope", description: "Web notebook and stage", color: "oklch(70% 0.16 300)" },
  { key: "google_chat", label: "Google Chat", description: "Hermes chat turns", color: "oklch(73% 0.16 150)" },
  { key: "dashboard", label: "Dashboards", description: "Direct dashboard and app use", color: "oklch(76% 0.16 82)" },
  { key: "gallery", label: "Gallery", description: "Gallery and Home exploration", color: "oklch(72% 0.12 245)" },
  { key: "direct_mcp", label: "Direct MCP", description: "Codex, Claude, and other clients", color: "oklch(69% 0.18 27)" },
  { key: "automation", label: "Automation", description: "Cron and scheduled workflows", color: "oklch(76% 0.15 120)" },
  { key: "other", label: "Other", description: "Legacy or unresolved provenance", color: "oklch(68% 0.02 250)" },
]

const TOOL_COLORS = [
  "oklch(71% 0.17 205)",
  "oklch(73% 0.16 150)",
  "oklch(76% 0.16 82)",
  "oklch(69% 0.18 27)",
  "oklch(70% 0.16 300)",
  "oklch(72% 0.12 245)",
  "oklch(76% 0.15 120)",
]

function surfaceLabel(surface: McpIncomingSurface): string {
  return SURFACE_FILTERS.find((item) => item.key === surface)?.label ?? surface
}

function surfaceColor(surface: McpIncomingSurface): string {
  return SURFACE_FILTERS.find((item) => item.key === surface)?.color ?? "var(--rvbbit-accent)"
}

export function McpIncomingWindow({
  activeConnectionId,
  hasRvbbit,
  workspaceActive,
  onOpenSql,
}: McpIncomingWindowProps) {
  const [range, setRange] = useState<McpIncomingRange>("24h")
  const [snapshot, setSnapshot] = useState<McpIncomingSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [callerFilter, setCallerFilter] = useState<string | null>(null)
  const [toolFilter, setToolFilter] = useState<string | null>(null)
  const [objectFilter, setObjectFilter] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all")
  const [surfaceFilter, setSurfaceFilter] = useState<McpIncomingSurface>("all")
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) {
      setSnapshot(null)
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const next = await fetchMcpIncomingSnapshot(activeConnectionId, range, surfaceFilter)
      setSnapshot(next)
      setError(next.error ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [activeConnectionId, hasRvbbit, range, surfaceFilter])

  useEffect(() => {
    if (!workspaceActive) return
    queueMicrotask(() => {
      void refresh()
    })
  }, [refresh, workspaceActive])

  useEffect(() => {
    if (!workspaceActive || paused || !activeConnectionId || !hasRvbbit) return
    const id = window.setInterval(() => {
      void refresh()
    }, 30_000)
    return () => window.clearInterval(id)
  }, [activeConnectionId, hasRvbbit, paused, refresh, workspaceActive])

  // This is intentionally the only row-bounded dataset in the app. Aggregate
  // panels consume the full-window server summaries on the snapshot instead.
  const recentDetails = useMemo(() => snapshot?.recentActivities ?? [], [snapshot])
  const statusScopedDetails = useMemo(
    () => recentDetails.filter((a) => statusMatches(a, statusFilter)),
    [recentDetails, statusFilter],
  )
  const filteredDetails = useMemo(
    () =>
      statusScopedDetails.filter((a) => {
        if (callerFilter && a.actor !== callerFilter) return false
        if (toolFilter && a.tool !== toolFilter) return false
        if (objectFilter && !a.objects.includes(objectFilter)) return false
        return true
      }),
    [callerFilter, objectFilter, statusScopedDetails, toolFilter],
  )

  const selectedDetail = useMemo(
    () => filteredDetails.find((a) => a.id === selectedId) ?? filteredDetails[0] ?? null,
    [filteredDetails, selectedId],
  )

  const hasFilters = !!callerFilter || !!toolFilter || !!objectFilter || statusFilter !== "all" || surfaceFilter !== "all"
  const clearFilters = () => {
    setCallerFilter(null)
    setToolFilter(null)
    setObjectFilter(null)
    setStatusFilter("all")
    setSurfaceFilter("all")
  }

  const openSelectedSql = useCallback(
    (activity: McpIncomingActivity) => {
      if (!activity.sql) return
      const title = `MCP SQL · ${activity.tool}`
      onOpenSql(activity.sql, title)
    },
    [onOpenSql],
  )

  const detected = !!snapshot?.detected
  const subtitle = !activeConnectionId
    ? "no active connection"
    : !hasRvbbit
      ? "rvbbit unavailable"
      : loading && !snapshot
        ? "loading activity"
        : detected
          ? `${fmtCount(snapshot.overview.calls)} calls${surfaceFilter === "all" ? "" : ` · ${surfaceLabel(surfaceFilter)}`} · ${snapshot.overview.lastSeen ? fmtAgo(snapshot.overview.lastSeen) : "no events"}`
          : "rvbbit.mcp_activity not found"

  return (
    <div className="flex h-full flex-col bg-doc-bg text-foreground group-data-[focused=false]/window:bg-doc-bg/70">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-chrome-border bg-chrome-bg/35 px-2">
        <div className="grid h-6 w-6 place-items-center rounded border border-chrome-border/60 bg-foreground/[0.04]">
          <Activity className="h-3.5 w-3.5 text-rvbbit-accent" />
        </div>
        <div className="min-w-0">
          <div className="text-[12px] font-semibold leading-tight text-foreground">MCP Incoming</div>
          <div className="truncate font-mono text-[9px] text-chrome-text/55">{subtitle}</div>
        </div>
        <div className="ml-auto flex min-w-0 items-center gap-1">
          <Segmented
            items={RANGES}
            value={range}
            onChange={(next) => setRange(next as McpIncomingRange)}
            title="Range"
          />
          <Segmented
            items={STATUS_FILTERS}
            value={statusFilter}
            onChange={(next) => setStatusFilter(next as StatusFilter)}
            title="Status"
          />
          {hasFilters ? (
            <button
              type="button"
              onClick={clearFilters}
              title="Clear filters"
              className="grid h-7 w-7 place-items-center rounded text-chrome-text transition-colors hover:bg-foreground/[0.06] hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            title={paused ? "Resume auto-refresh" : "Pause auto-refresh"}
            className="grid h-7 w-7 place-items-center rounded text-chrome-text transition-colors hover:bg-foreground/[0.06] hover:text-foreground"
          >
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading || !activeConnectionId || !hasRvbbit}
            title="Refresh"
            className="grid h-7 w-7 place-items-center rounded text-chrome-text transition-colors hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-45"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {!activeConnectionId ? (
          <Empty icon={Database} title="No active connection" detail="Open a connection to read MCP activity." />
        ) : !hasRvbbit ? (
          <Empty icon={Globe} title="rvbbit unavailable" detail="MCP incoming activity is stored in rvbbit metadata tables." />
        ) : loading && !snapshot ? (
          <Loading />
        ) : error ? (
          <Empty icon={AlertTriangle} title="MCP activity failed" detail={error} tone="danger" />
        ) : !detected || !snapshot ? (
          <Empty icon={Activity} title="No MCP activity log" detail="rvbbit.mcp_activity was not found on this connection." />
        ) : (
          <div className="flex min-h-full flex-col gap-3">
            <OverviewStats snapshot={snapshot} />
            <SurfaceOverview
              counts={snapshot.surfaces}
              buckets={snapshot.buckets}
              previousBuckets={snapshot.previousBuckets}
              range={range}
              selected={surfaceFilter}
              provenanceAvailable={snapshot.provenanceAvailable}
              loading={loading}
              onSelect={setSurfaceFilter}
            />
            <ActiveFilters
              caller={callerFilter}
              tool={toolFilter}
              object={objectFilter}
              status={statusFilter}
              surface={surfaceFilter}
              onClearCaller={() => setCallerFilter(null)}
              onClearTool={() => setToolFilter(null)}
              onClearObject={() => setObjectFilter(null)}
              onClearStatus={() => setStatusFilter("all")}
              onClearSurface={() => setSurfaceFilter("all")}
            />

            <div className="grid min-h-[540px] flex-1 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.8fr)]">
              <Panel
                icon={Filter}
                title="Flow"
                right={<BucketStrip buckets={snapshot.buckets} />}
                className="flex min-h-[420px] flex-col overflow-hidden"
              >
                <McpFlowGraph
                  nodes={snapshot.flowNodes}
                  edges={snapshot.flowEdges}
                  status={statusFilter}
                  callerFilter={callerFilter}
                  toolFilter={toolFilter}
                  objectFilter={objectFilter}
                  onCaller={(caller) => setCallerFilter((cur) => (cur === caller ? null : caller))}
                  onTool={(tool) => setToolFilter((cur) => (cur === tool ? null : tool))}
                  onObject={(object) => setObjectFilter((cur) => (cur === object ? null : object))}
                />
              </Panel>

              <div className="flex min-h-[420px] flex-col gap-3">
                <SelectedCall activity={selectedDetail} onOpenSql={openSelectedSql} />
                <PeoplePanel
                  callers={snapshot.callers}
                  status={statusFilter}
                  selected={callerFilter}
                  onSelect={(actor) => setCallerFilter((cur) => (cur === actor ? null : actor))}
                />
              </div>
            </div>

            <div className="grid min-h-[420px] grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.8fr)]">
              <Panel
                icon={Activity}
                title="Recent Detail"
                right={
                  <span
                    title={`Only this raw-call ledger is capped; summaries use all ${fmtCount(snapshot.overview.calls)} calls in the period.`}
                    className="font-mono text-[9px] text-chrome-text/60"
                  >
                    {fmtCount(filteredDetails.length)}/{fmtCount(recentDetails.length)} shown · newest {fmtCount(snapshot.detailLimit)} max
                  </span>
                }
                className="flex min-h-[360px] flex-col overflow-hidden"
              >
                <ActivityStream rows={filteredDetails} selectedId={selectedDetail?.id ?? null} onSelect={setSelectedId} onOpenSql={openSelectedSql} />
              </Panel>

              <div className="grid min-h-[360px] grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
                <ObjectsPanel
                  objects={snapshot.objects}
                  status={statusFilter}
                  selected={objectFilter}
                  onSelect={(object) => setObjectFilter((cur) => (cur === object ? null : object))}
                />
                <ErrorsPanel errors={snapshot.errors} status={statusFilter} onOpenSql={(sql) => onOpenSql(sql, "MCP Error SQL")} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <ToolsPanel
                tools={snapshot.tools}
                status={statusFilter}
                selected={toolFilter}
                onSelect={(tool) => setToolFilter((cur) => (cur === tool ? null : tool))}
              />
              <ResultShapePanel activity={selectedDetail} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function OverviewStats({ snapshot }: { snapshot: McpIncomingSnapshot }) {
  const o = snapshot.overview
  return (
    <div className="grid shrink-0 grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-9">
      <Stat label="calls" value={fmtCount(o.calls)} />
      <Stat label="avg" value={o.avgMs == null ? "0ms" : fmtMs(o.avgMs)} />
      <Stat label="people" value={fmtCount(o.callers)} />
      <Stat label="tools" value={fmtCount(o.tools)} />
      <Stat label="objects" value={fmtCount(o.objects)} />
      <Stat label="errors" value={fmtCount(o.errors)} tone={o.errors > 0 ? "danger" : "success"} />
      <Stat label="sql" value={fmtCount(o.sqlCalls)} />
      <Stat label="rows" value={fmtCount(o.rowsReturned)} />
      <Stat label="p95" value={o.p95Ms == null ? "0ms" : fmtMs(o.p95Ms)} />
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "danger" | "success" }) {
  return (
    <div className="rounded-md border border-chrome-border/60 bg-secondary-background/40 px-2.5 py-2">
      <div className={cn("truncate font-mono text-[16px] leading-none tabular-nums", tone === "danger" ? "text-danger" : tone === "success" ? "text-success" : "text-foreground")}>
        {value}
      </div>
      <div className="mt-1 truncate text-[9px] uppercase tracking-wider text-chrome-text/50">{label}</div>
    </div>
  )
}

function SurfaceOverview({
  counts,
  buckets,
  previousBuckets,
  range,
  selected,
  provenanceAvailable,
  loading,
  onSelect,
}: {
  counts: McpIncomingSurfaceCount[]
  buckets: McpIncomingBucket[]
  previousBuckets: McpIncomingBucket[]
  range: McpIncomingRange
  selected: McpIncomingSurface
  provenanceAvailable: boolean
  loading: boolean
  onSelect: (surface: McpIncomingSurface) => void
}) {
  const bySurface = new Map(counts.map((count) => [count.surface, count]))
  const allPeople = Math.max(1, bySurface.get("all")?.callers ?? 0)
  return (
    <div className="shrink-0 rounded-md border border-chrome-border/60 bg-secondary-background/25 p-2">
      <div className="mb-2 flex items-center gap-2 px-0.5">
        <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-chrome-text/55">People by surface</div>
        <div className="h-px min-w-4 flex-1 bg-chrome-border/45" />
        <div className="truncate text-right font-mono text-[9px] text-chrome-text/45">
          {provenanceAvailable ? "distinct people · verified request path" : "legacy rows inferred where possible"}
        </div>
      </div>
      <div className={cn("grid grid-cols-2 gap-1.5 sm:grid-cols-4 xl:grid-cols-8", loading && "opacity-70")}>
        {SURFACE_FILTERS.map((surface) => {
          const count = bySurface.get(surface.key)
          const calls = count?.calls ?? 0
          const callers = count?.callers ?? 0
          const active = selected === surface.key
          const reach = surface.key === "all" ? 100 : (callers / allPeople) * 100
          return (
            <button
              key={surface.key}
              type="button"
              data-mcp-surface={surface.key}
              aria-pressed={active}
              onClick={() => onSelect(surface.key)}
              title={`${surface.description} · ${fmtCount(callers)} people · ${fmtCount(calls)} MCP requests`}
              className={cn(
                "group/surface relative min-w-0 overflow-hidden rounded border px-2 py-2 text-left transition-colors",
                active
                  ? "border-rvbbit-accent/60 bg-rvbbit-accent/10"
                  : "border-chrome-border/55 bg-foreground/[0.025] hover:border-rvbbit-accent/35 hover:bg-foreground/[0.045]",
                calls === 0 && !active && "opacity-55",
              )}
            >
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: surface.color }} />
                <span className={cn("min-w-0 flex-1 truncate text-[9px] font-semibold uppercase tracking-wider", active ? "text-foreground" : "text-chrome-text/65")}>
                  {surface.label}
                </span>
              </div>
              <div className="mt-1.5 flex items-end justify-between gap-1">
                <span className="font-mono text-[16px] leading-none tabular-nums text-foreground">{fmtCount(callers)}</span>
                <span className="truncate font-mono text-[8px] text-chrome-text/45">{fmtCount(calls)} req</span>
              </div>
              <div className="absolute inset-x-0 bottom-0 h-0.5 bg-foreground/[0.04]">
                <div
                  className="h-full transition-[width] duration-300"
                  style={{ width: `${Math.max(callers > 0 ? 2 : 0, Math.min(100, reach))}%`, background: surface.color }}
                />
              </div>
            </button>
          )
        })}
      </div>
      <UsageTimeline
        buckets={buckets}
        previousBuckets={previousBuckets}
        range={range}
        surface={selected}
      />
    </div>
  )
}

function UsageTimeline({
  buckets,
  previousBuckets,
  range,
  surface,
}: {
  buckets: McpIncomingBucket[]
  previousBuckets: McpIncomingBucket[]
  range: McpIncomingRange
  surface: McpIncomingSurface
}) {
  const [hovered, setHovered] = useState<number | null>(null)
  const width = 1000
  const height = 58
  const top = 5
  const bottom = 4
  const currentTotal = buckets.reduce((sum, bucket) => sum + bucket.calls, 0)
  const previousTotal = previousBuckets.reduce((sum, bucket) => sum + bucket.calls, 0)
  const max = Math.max(1, ...buckets.map((bucket) => bucket.calls), ...previousBuckets.map((bucket) => bucket.calls))
  const currentPath = timelinePath(buckets, width, height, top, bottom, max)
  const previousPath = timelinePath(previousBuckets, width, height, top, bottom, max)
  const areaPath = timelineAreaPath(currentPath, width, height - bottom)
  const color = surfaceColor(surface)
  const pointCount = Math.max(buckets.length, previousBuckets.length)
  const hoverX = hovered == null ? null : timelineX(hovered, pointCount, width)
  const currentHover = hovered == null ? null : buckets[hovered] ?? null
  const previousHover = hovered == null ? null : previousBuckets[hovered] ?? null
  const firstBucket = buckets[0]?.bucket ?? null
  const middleBucket = buckets[Math.floor(buckets.length / 2)]?.bucket ?? null

  return (
    <div
      data-mcp-usage-timeline
      className="mt-2 border-t border-chrome-border/45 px-0.5 pt-2"
      onMouseLeave={() => setHovered(null)}
    >
      <div className="mb-1.5 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <div className="mr-auto min-w-0 truncate text-[9px] font-semibold uppercase tracking-[0.16em] text-chrome-text/55">
          MCP request volume · {surfaceLabel(surface)}
        </div>
        <TimelineLegend color={color} label="Current" value={currentTotal} />
        <TimelineLegend color="var(--chrome-text)" label="Previous" value={previousTotal} dotted />
        <div className="font-mono text-[8px] text-chrome-text/45">{periodComparison(currentTotal, previousTotal)}</div>
      </div>

      <div className="relative h-[58px] overflow-hidden rounded-sm bg-foreground/[0.018]">
        <svg
          className="absolute inset-0 h-full w-full overflow-visible"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${surfaceLabel(surface)} MCP request volume in the current ${range} compared with the previous ${range}`}
        >
          <title>{`${surfaceLabel(surface)} · current ${range}: ${currentTotal} calls · previous ${range}: ${previousTotal} calls`}</title>
          {[0.25, 0.5, 0.75].map((fraction) => (
            <line
              key={fraction}
              x1="0"
              x2={width}
              y1={top + (height - top - bottom) * fraction}
              y2={top + (height - top - bottom) * fraction}
              stroke="var(--chrome-border)"
              strokeOpacity="0.28"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {areaPath ? <path d={areaPath} fill={color} opacity="0.09" /> : null}
          {previousPath ? (
            <path
              data-period-series="previous"
              d={previousPath}
              fill="none"
              stroke="var(--chrome-text)"
              strokeDasharray="3 4"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeOpacity="0.46"
              strokeWidth="1.25"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {currentPath ? (
            <path
              data-period-series="current"
              d={currentPath}
              fill="none"
              stroke={color}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="1.8"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {hoverX != null ? (
            <>
              <line
                x1={hoverX}
                x2={hoverX}
                y1={top}
                y2={height - bottom}
                stroke="var(--foreground)"
                strokeDasharray="2 3"
                strokeOpacity="0.28"
                vectorEffect="non-scaling-stroke"
              />
              {currentHover ? (
                <circle
                  cx={hoverX}
                  cy={timelineY(currentHover.calls, max, height, top, bottom)}
                  r="3"
                  fill={color}
                  stroke="var(--chrome-bg)"
                  strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
              {previousHover ? (
                <circle
                  cx={hoverX}
                  cy={timelineY(previousHover.calls, max, height, top, bottom)}
                  r="2.5"
                  fill="var(--chrome-bg)"
                  stroke="var(--chrome-text)"
                  strokeOpacity="0.7"
                  strokeWidth="1.25"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
            </>
          ) : null}
        </svg>

        {pointCount > 0 ? (
          <div className="absolute inset-0 flex">
            {Array.from({ length: pointCount }, (_, index) => (
              <button
                key={index}
                type="button"
                aria-label={timelinePointLabel(buckets[index], previousBuckets[index], range)}
                className="h-full min-w-0 flex-1 cursor-crosshair focus-visible:bg-foreground/[0.04] focus-visible:outline-none"
                onMouseEnter={() => setHovered(index)}
                onFocus={() => setHovered(index)}
                onBlur={() => setHovered(null)}
              />
            ))}
          </div>
        ) : null}

        {hovered != null && (currentHover || previousHover) ? (
          <div
            className="pointer-events-none absolute top-1 z-10 rounded border border-chrome-border/70 bg-chrome-bg/95 px-2 py-1 font-mono text-[8px] leading-relaxed text-chrome-text shadow-md"
            style={timelineTooltipPosition(hovered, pointCount)}
          >
            <div className="flex justify-between gap-3 whitespace-nowrap">
              <span className="text-foreground">{formatTimelineBucket(currentHover?.bucket ?? null, range)}</span>
              <span style={{ color }}>current {fmtCount(currentHover?.calls ?? 0)}</span>
            </div>
            <div className="flex justify-between gap-3 whitespace-nowrap text-chrome-text/60">
              <span>{formatTimelineBucket(previousHover?.bucket ?? null, range)}</span>
              <span>previous {fmtCount(previousHover?.calls ?? 0)}</span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-1 flex justify-between font-mono text-[8px] text-chrome-text/35">
        <span>{formatTimelineAxis(firstBucket, range)}</span>
        <span>{formatTimelineAxis(middleBucket, range)}</span>
        <span>now</span>
      </div>
    </div>
  )
}

function TimelineLegend({ color, label, value, dotted = false }: { color: string; label: string; value: number; dotted?: boolean }) {
  return (
    <div className="flex items-center gap-1.5 font-mono text-[8px] text-chrome-text/55">
      <span
        className={cn("h-px w-4", dotted && "border-t border-dashed bg-transparent")}
        style={dotted ? { borderColor: color } : { background: color }}
      />
      <span>{label}</span>
      <span className="text-foreground/80">{fmtCount(value)}</span>
    </div>
  )
}

function ActiveFilters({
  caller,
  tool,
  object,
  status,
  surface,
  onClearCaller,
  onClearTool,
  onClearObject,
  onClearStatus,
  onClearSurface,
}: {
  caller: string | null
  tool: string | null
  object: string | null
  status: StatusFilter
  surface: McpIncomingSurface
  onClearCaller: () => void
  onClearTool: () => void
  onClearObject: () => void
  onClearStatus: () => void
  onClearSurface: () => void
}) {
  if (!caller && !tool && !object && status === "all" && surface === "all") return null
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1.5">
      {surface !== "all" ? <FilterChip label="surface" value={surfaceLabel(surface)} onClear={onClearSurface} /> : null}
      {caller ? <FilterChip label="person" value={caller} onClear={onClearCaller} /> : null}
      {tool ? <FilterChip label="tool" value={tool} onClear={onClearTool} /> : null}
      {object ? <FilterChip label="object" value={object} onClear={onClearObject} /> : null}
      {status !== "all" ? <FilterChip label="status" value={status} onClear={onClearStatus} /> : null}
    </div>
  )
}

function FilterChip({ label, value, onClear }: { label: string; value: string; onClear: () => void }) {
  return (
    <button
      type="button"
      onClick={onClear}
      title={`Clear ${label}`}
      className="inline-flex max-w-[260px] items-center gap-1 rounded border border-rvbbit-accent/30 bg-rvbbit-accent/10 px-2 py-1 text-[10px] text-rvbbit-accent"
    >
      <span className="shrink-0 uppercase tracking-wider opacity-70">{label}</span>
      <span className="truncate font-mono">{value}</span>
      <X className="h-3 w-3 shrink-0" />
    </button>
  )
}

function McpFlowGraph({
  nodes,
  edges,
  status,
  callerFilter,
  toolFilter,
  objectFilter,
  onCaller,
  onTool,
  onObject,
}: {
  nodes: McpIncomingFlowNode[]
  edges: McpIncomingFlowEdge[]
  status: StatusFilter
  callerFilter: string | null
  toolFilter: string | null
  objectFilter: string | null
  onCaller: (caller: string) => void
  onTool: (tool: string) => void
  onObject: (object: string) => void
}) {
  const [ref, size] = useElementSize<HTMLDivElement>()
  const graph = useMemo(
    () => buildFlow(nodes, edges, size.width, size.height, { callerFilter, toolFilter, objectFilter, status }),
    [callerFilter, edges, nodes, objectFilter, size.height, size.width, status, toolFilter],
  )

  return (
    <div ref={ref} className="relative min-h-[320px] flex-1 overflow-hidden">
      {size.width > 0 && size.height > 0 && graph.nodes.length > 0 ? (
        <>
          <svg className="absolute inset-0 h-full w-full" width={size.width} height={size.height} role="img">
            <defs>
              <linearGradient id="mcp-flow-ok" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0%" stopColor="var(--rvbbit-accent)" stopOpacity="0.30" />
                <stop offset="100%" stopColor="var(--success)" stopOpacity="0.60" />
              </linearGradient>
              <linearGradient id="mcp-flow-err" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0%" stopColor="var(--warning)" stopOpacity="0.35" />
                <stop offset="100%" stopColor="var(--danger)" stopOpacity="0.70" />
              </linearGradient>
            </defs>
            {graph.edges.map((e, i) => (
              <path
                key={`${e.from.key}:${e.to.key}:${i}`}
                d={curvePath(e.from.x, e.from.y, e.to.x, e.to.y)}
                fill="none"
                stroke={e.errors > 0 ? "url(#mcp-flow-err)" : "url(#mcp-flow-ok)"}
                strokeWidth={e.width}
                strokeLinecap="round"
                opacity={e.active ? 0.92 : 0.28}
              />
            ))}
          </svg>
          {graph.nodes.map((n) => (
            <button
              key={`${n.kind}:${n.key}`}
              type="button"
              onClick={() => {
                if (n.kind === "caller") onCaller(n.key)
                if (n.kind === "tool") onTool(n.key)
                if (n.kind === "object") onObject(n.key)
              }}
              title={`${n.label} · ${fmtCount(n.calls)} calls`}
              className={cn(
                "absolute flex h-[30px] items-center gap-1.5 rounded border px-2 text-left shadow-sm transition-colors",
                n.active
                  ? "border-rvbbit-accent/60 bg-rvbbit-accent/15 text-foreground"
                  : "border-chrome-border/70 bg-chrome-bg/90 text-chrome-text hover:border-rvbbit-accent/45 hover:text-foreground",
              )}
              style={{
                left: n.x - n.w / 2,
                top: n.y - 15,
                width: n.w,
              }}
            >
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: n.color }} />
              <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{n.label}</span>
              <span className="shrink-0 font-mono text-[9px] text-chrome-text/55">{fmtCount(n.calls)}</span>
            </button>
          ))}
          <AxisLabel x={graph.axis.callers} label="People" />
          <AxisLabel x={graph.axis.tools} label="Tools" />
          <AxisLabel x={graph.axis.objects} label="Objects" />
        </>
      ) : (
        <TinyEmpty label="No activity" />
      )}
    </div>
  )
}

function AxisLabel({ x, label }: { x: number; label: string }) {
  return (
    <div
      className="pointer-events-none absolute top-0 -translate-x-1/2 rounded-sm bg-doc-bg/80 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-chrome-text/45"
      style={{ left: x }}
    >
      {label}
    </div>
  )
}

function SelectedCall({ activity, onOpenSql }: { activity: McpIncomingActivity | null; onOpenSql: (activity: McpIncomingActivity) => void }) {
  return (
    <Panel icon={FileCode2} title="Selected Recent Call" className="flex min-h-[246px] flex-col overflow-hidden">
      {!activity ? (
        <TinyEmpty label="No matching call in recent detail" />
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-auto pr-1">
          <div className="flex items-start gap-2">
            <StatusPill ok={activity.ok} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono text-[12px] text-foreground">{activity.tool}</div>
              <div className="truncate font-mono text-[10px] text-chrome-text/55">{activity.actor}</div>
              <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[9px] text-chrome-text/45">
                <SurfaceBadge surface={activity.surface} />
                <span className="truncate font-mono">{activity.clientApp} · {activity.channel}</span>
              </div>
            </div>
            {activity.sql ? (
              <button
                type="button"
                onClick={() => onOpenSql(activity)}
                className="inline-flex shrink-0 items-center gap-1 rounded border border-rvbbit-accent/35 bg-rvbbit-accent/10 px-2 py-1 text-[10px] text-rvbbit-accent transition-colors hover:bg-rvbbit-accent/15"
              >
                <FileCode2 className="h-3 w-3" />
                Open SQL
              </button>
            ) : null}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <MiniMetric label="time" value={activity.ts ? fmtAgo(activity.ts) : "never"} />
            <MiniMetric label="latency" value={activity.elapsedMs == null ? "0ms" : fmtMs(activity.elapsedMs)} />
            <MiniMetric label="rows" value={activity.rows == null ? "-" : fmtCount(activity.rows)} />
          </div>

          {activity.subject ? (
            <div className="rounded border border-chrome-border/60 bg-foreground/[0.03] px-2 py-1.5">
              <div className="truncate font-mono text-[10px] text-foreground">{activity.subject}</div>
            </div>
          ) : null}

          {activity.objects.length ? (
            <div className="flex flex-wrap gap-1">
              {activity.objects.slice(0, 8).map((obj) => (
                <span key={obj} className="max-w-[220px] truncate rounded border border-chrome-border/60 bg-foreground/[0.03] px-1.5 py-0.5 font-mono text-[9px] text-chrome-text">
                  {obj}
                </span>
              ))}
            </div>
          ) : null}

          {activity.errorCode || activity.errorMessage ? (
            <div className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5">
              <div className="font-mono text-[10px] text-danger">{activity.errorCode ?? "ERROR"}</div>
              {activity.errorMessage ? <div className="mt-1 text-[10px] text-danger/85">{activity.errorMessage}</div> : null}
            </div>
          ) : null}

          {activity.sql ? (
            <CodePreview
              code={activity.sql}
              lang="sql"
              className="max-h-36 rounded border border-chrome-border/60 bg-foreground/[0.035] p-2 text-[10px]"
            />
          ) : (
            <KeyValueBlock title="args" value={activity.args} />
          )}
        </div>
      )}
    </Panel>
  )
}

function PeoplePanel({
  callers,
  status,
  selected,
  onSelect,
}: {
  callers: McpIncomingCaller[]
  status: StatusFilter
  selected: string | null
  onSelect: (actor: string) => void
}) {
  const rankedCallers = [...callers]
    .filter((caller) => statusCount(caller.calls, caller.errors, status) > 0)
    .sort((a, b) => statusCount(b.calls, b.errors, status) - statusCount(a.calls, a.errors, status) || a.actor.localeCompare(b.actor))
  return (
    <Panel icon={Users} title="People" className="flex min-h-[160px] flex-1 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
        {rankedCallers.length === 0 ? (
          <TinyEmpty label="No callers" />
        ) : (
          rankedCallers.slice(0, 14).map((caller) => (
            <button
              key={caller.actor}
              type="button"
              onClick={() => onSelect(caller.actor)}
              className={cn(
                "grid w-full grid-cols-[minmax(0,1fr)_110px_54px] items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors",
                selected === caller.actor
                  ? "border-rvbbit-accent/55 bg-rvbbit-accent/10"
                  : "border-chrome-border/55 bg-foreground/[0.025] hover:border-rvbbit-accent/35",
              )}
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-[10px] text-foreground">{caller.actor}</div>
                <div className="truncate text-[9px] text-chrome-text/55">{caller.topTool ?? "-"} · {caller.lastSeen ? fmtAgo(caller.lastSeen) : "never"}</div>
              </div>
              <ActivityTicks buckets={caller.buckets} status={status} />
              <div className={cn("text-right font-mono text-[10px] tabular-nums", status === "error" ? "text-danger" : "text-chrome-text")}>
                {fmtCount(statusCount(caller.calls, caller.errors, status))}
                {status === "all" && caller.errors > 0 ? <span className="ml-1 text-danger">{fmtCount(caller.errors)}</span> : null}
              </div>
            </button>
          ))
        )}
      </div>
    </Panel>
  )
}

function ActivityStream({
  rows,
  selectedId,
  onSelect,
  onOpenSql,
}: {
  rows: McpIncomingActivity[]
  selectedId: number | null
  onSelect: (id: number) => void
  onOpenSql: (activity: McpIncomingActivity) => void
}) {
  if (rows.length === 0) return <TinyEmpty label="No matching activity" />
  return (
    <div className="min-h-0 flex-1 overflow-auto pr-1">
      <div className="grid gap-1.5">
        {rows.map((r) => (
          <button
            key={r.id}
            type="button"
            onClick={() => onSelect(r.id)}
            className={cn(
              "grid w-full grid-cols-[86px_minmax(90px,0.8fr)_minmax(92px,0.75fr)_minmax(120px,1.2fr)_68px_32px] items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors",
              selectedId === r.id
                ? "border-rvbbit-accent/60 bg-rvbbit-accent/10"
                : "border-chrome-border/55 bg-foreground/[0.025] hover:border-rvbbit-accent/35",
            )}
          >
            <div className="truncate font-mono text-[10px] text-chrome-text/65">{r.ts ? fmtAgo(r.ts) : "never"}</div>
            <div className="min-w-0">
              <div className="truncate font-mono text-[10px] text-foreground">{r.actor}</div>
              <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[8px] text-chrome-text/45">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: surfaceColor(r.surface) }} />
                <span className="truncate">{surfaceLabel(r.surface)}</span>
              </div>
            </div>
            <div className="truncate font-mono text-[10px] text-rvbbit-accent">{r.tool}</div>
            <div className="truncate text-[10px] text-chrome-text">{r.subject ?? objectSummary(r.objects) ?? "-"}</div>
            <div className={cn("text-right font-mono text-[10px] tabular-nums", r.ok === false ? "text-danger" : "text-chrome-text/70")}>
              {r.elapsedMs == null ? "0ms" : fmtMs(r.elapsedMs)}
            </div>
            <div className="flex items-center justify-end gap-1">
              {r.sql ? (
                <span
                  role="button"
                  tabIndex={0}
                  title="Open SQL"
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenSql(r)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      e.stopPropagation()
                      onOpenSql(r)
                    }
                  }}
                  className="grid h-5 w-5 place-items-center rounded text-chrome-text hover:bg-foreground/[0.08] hover:text-rvbbit-accent"
                >
                  <FileCode2 className="h-3 w-3" />
                </span>
              ) : null}
              <span className={cn("h-2 w-2 rounded-full", r.ok === false ? "bg-danger" : "bg-success")} />
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function ObjectsPanel({
  objects,
  status,
  selected,
  onSelect,
}: {
  objects: McpIncomingObject[]
  status: StatusFilter
  selected: string | null
  onSelect: (object: string) => void
}) {
  const rankedObjects = [...objects]
    .filter((object) => statusCount(object.touches, object.errors, status) > 0)
    .sort((a, b) => statusCount(b.touches, b.errors, status) - statusCount(a.touches, a.errors, status) || a.object.localeCompare(b.object))
  const max = Math.max(1, ...rankedObjects.map((o) => statusCount(o.touches, o.errors, status)))
  return (
    <Panel icon={Table2} title="Objects" className="flex min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
        {rankedObjects.length === 0 ? (
          <TinyEmpty label="No object touches" />
        ) : (
          rankedObjects.slice(0, 18).map((obj) => (
            <button
              key={obj.object}
              type="button"
              onClick={() => onSelect(obj.object)}
              className={cn(
                "grid w-full grid-cols-[minmax(0,1fr)_70px] items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors",
                selected === obj.object
                  ? "border-rvbbit-accent/55 bg-rvbbit-accent/10"
                  : "border-chrome-border/55 bg-foreground/[0.025] hover:border-rvbbit-accent/35",
              )}
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-[10px] text-foreground">{obj.object}</div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-sm bg-foreground/[0.06]">
                  <div className="h-full rounded-sm bg-rvbbit-accent" style={{ width: `${Math.max(3, (statusCount(obj.touches, obj.errors, status) / max) * 100)}%` }} />
                </div>
              </div>
              <div className="text-right font-mono text-[10px] tabular-nums text-chrome-text">
                {fmtCount(statusCount(obj.touches, obj.errors, status))}
                <div className="text-[9px] text-chrome-text/45">{fmtCount(obj.callers)} ppl</div>
              </div>
            </button>
          ))
        )}
      </div>
    </Panel>
  )
}

function ErrorsPanel({ errors, status, onOpenSql }: { errors: McpIncomingError[]; status: StatusFilter; onOpenSql: (sql: string) => void }) {
  const visibleErrors = status === "ok" ? [] : errors
  return (
    <Panel icon={AlertTriangle} title="Errors" className="flex min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
        {visibleErrors.length === 0 ? (
          <TinyEmpty label="No errors" />
        ) : (
          visibleErrors.slice(0, 14).map((e) => (
            <div key={`${e.tool}:${e.code}:${e.message}`} className="rounded border border-danger/25 bg-danger/[0.06] px-2 py-1.5">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-[10px] text-danger">{e.code}</div>
                  <div className="truncate text-[10px] text-danger/85">{e.message}</div>
                  <div className="mt-1 truncate font-mono text-[9px] text-chrome-text/55">{e.tool} · {fmtCount(e.calls)} calls · {e.lastSeen ? fmtAgo(e.lastSeen) : "never"}</div>
                </div>
                {e.sampleSql ? (
                  <button
                    type="button"
                    title="Open SQL"
                    onClick={() => onOpenSql(e.sampleSql!)}
                    className="grid h-6 w-6 shrink-0 place-items-center rounded text-danger hover:bg-danger/10"
                  >
                    <FileCode2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            </div>
          ))
        )}
      </div>
    </Panel>
  )
}

function ToolsPanel({
  tools,
  status,
  selected,
  onSelect,
}: {
  tools: McpIncomingTool[]
  status: StatusFilter
  selected: string | null
  onSelect: (tool: string) => void
}) {
  const rankedTools = [...tools]
    .filter((tool) => statusCount(tool.calls, tool.errors, status) > 0)
    .sort((a, b) => statusCount(b.calls, b.errors, status) - statusCount(a.calls, a.errors, status) || a.tool.localeCompare(b.tool))
  const max = Math.max(1, ...rankedTools.map((t) => statusCount(t.calls, t.errors, status)))
  return (
    <Panel icon={Search} title="Tools">
      <div className="grid gap-1.5 md:grid-cols-2">
        {rankedTools.length === 0 ? (
          <TinyEmpty label="No tools" />
        ) : (
          rankedTools.slice(0, 16).map((tool, i) => (
            <button
              key={tool.tool}
              type="button"
              onClick={() => onSelect(tool.tool)}
              className={cn(
                "rounded border px-2 py-1.5 text-left transition-colors",
                selected === tool.tool
                  ? "border-rvbbit-accent/55 bg-rvbbit-accent/10"
                  : "border-chrome-border/55 bg-foreground/[0.025] hover:border-rvbbit-accent/35",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: TOOL_COLORS[i % TOOL_COLORS.length] }} />
                <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-foreground">{tool.tool}</span>
                <span className="font-mono text-[10px] text-chrome-text">{fmtCount(statusCount(tool.calls, tool.errors, status))}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-sm bg-foreground/[0.06]">
                <div className="h-full rounded-sm" style={{ width: `${Math.max(3, (statusCount(tool.calls, tool.errors, status) / max) * 100)}%`, background: TOOL_COLORS[i % TOOL_COLORS.length] }} />
              </div>
              <div className="mt-1 flex items-center justify-between font-mono text-[9px] text-chrome-text/55">
                <span>{fmtCount(tool.callers)} ppl</span>
                <span>{tool.errors > 0 ? `${fmtCount(tool.errors)} err` : "ok"}</span>
                <span>{tool.p95Ms == null ? "0ms" : fmtMs(tool.p95Ms)}</span>
              </div>
            </button>
          ))
        )}
      </div>
    </Panel>
  )
}

function ResultShapePanel({ activity }: { activity: McpIncomingActivity | null }) {
  return (
    <Panel icon={CheckCircle2} title="Result Shape">
      {!activity ? (
        <TinyEmpty label="No call selected" />
      ) : (
        <div className="grid gap-2 md:grid-cols-2">
          <KeyValueBlock title="summary" value={activity.resultSummary ?? {}} />
          <KeyValueBlock
            title="provenance"
            value={{
              channel: activity.channel,
              client_app: activity.clientApp,
              surface: activity.surface,
              session_ref: activity.sessionRef,
              ...activity.provenance,
            }}
          />
          <div className="md:col-span-2">
            <KeyValueBlock title="args" value={activity.args} />
          </div>
        </div>
      )}
    </Panel>
  )
}

function ActivityTicks({ buckets, status }: { buckets: McpIncomingBucket[]; status: StatusFilter }) {
  const maxCalls = Math.max(1, ...buckets.map((bucket) => statusCount(bucket.calls, bucket.errors, status)))
  return (
    <div className="flex h-4 items-center gap-px">
      {buckets.map((bucket, i) => {
        const calls = statusCount(bucket.calls, bucket.errors, status)
        const errors = status === "ok" ? 0 : bucket.errors
        return (
          <span
            key={`${bucket.bucket ?? i}:${i}`}
            title={`${bucket.bucket ? new Date(bucket.bucket).toLocaleString() : ""} · ${calls} calls · ${errors} errors`}
            className={cn(
              "h-3 flex-1 rounded-[1px]",
              calls === 0 ? "bg-foreground/[0.06]" : errors > 0 ? "bg-danger" : "bg-success",
            )}
            style={{ opacity: calls === 0 ? 1 : Math.min(1, 0.38 + calls / maxCalls) }}
          />
        )
      })}
    </div>
  )
}

function BucketStrip({ buckets }: { buckets: McpIncomingBucket[] }) {
  if (buckets.length === 0) return null
  const max = Math.max(1, ...buckets.map((b) => b.calls))
  return (
    <span className="flex h-3 w-28 items-end gap-px">
      {buckets.slice(-28).map((b, i) => (
        <span
          key={`${b.bucket ?? i}:${i}`}
          title={`${b.bucket ? new Date(b.bucket).toLocaleString() : ""} · ${b.calls} calls · ${b.errors} errors`}
          className={cn("w-1.5 rounded-[1px]", b.errors > 0 ? "bg-danger" : "bg-rvbbit-accent")}
          style={{
            height: `${Math.max(2, (b.calls / max) * 12)}px`,
            opacity: b.calls === 0 ? 0.25 : 0.85,
          }}
        />
      ))}
    </span>
  )
}

function KeyValueBlock({ title, value }: { title: string; value: Record<string, unknown> }) {
  const text = stringifyJson(value)
  return (
    <div className="min-w-0 rounded border border-chrome-border/60 bg-foreground/[0.025]">
      <div className="border-b border-chrome-border/45 px-2 py-1 text-[9px] uppercase tracking-wider text-chrome-text/55">{title}</div>
      <CodePreview code={text} lang="json" className="max-h-40 bg-transparent p-2 text-[10px]" />
    </div>
  )
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded border border-chrome-border/55 bg-foreground/[0.025] px-2 py-1.5">
      <div className="truncate font-mono text-[11px] tabular-nums text-foreground">{value}</div>
      <div className="truncate text-[9px] uppercase tracking-wider text-chrome-text/45">{label}</div>
    </div>
  )
}

function StatusPill({ ok }: { ok: boolean | null }) {
  const failed = ok === false
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[9px]", failed ? "border-danger/35 bg-danger/10 text-danger" : "border-success/35 bg-success/10 text-success")}>
      <span className={cn("h-1.5 w-1.5 rounded-full", failed ? "bg-danger" : "bg-success")} />
      {failed ? "ERR" : "OK"}
    </span>
  )
}

function SurfaceBadge({ surface }: { surface: Exclude<McpIncomingSurface, "all"> }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1 rounded border border-chrome-border/55 bg-foreground/[0.025] px-1.5 py-0.5">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: surfaceColor(surface) }} />
      <span className="truncate uppercase tracking-wider">{surfaceLabel(surface)}</span>
    </span>
  )
}

function Segmented<T extends string>({
  items,
  value,
  onChange,
  title,
}: {
  items: { key: T; label: string }[]
  value: T
  onChange: (value: T) => void
  title: string
}) {
  return (
    <div title={title} className="flex overflow-hidden rounded border border-chrome-border/70 bg-foreground/[0.03]">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => onChange(item.key)}
          className={cn(
            "px-2 py-1 font-mono text-[10px] transition-colors",
            value === item.key
              ? "bg-rvbbit-accent/15 text-rvbbit-accent"
              : "text-chrome-text/65 hover:bg-foreground/[0.05] hover:text-foreground",
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

function Empty({
  icon: Icon,
  title,
  detail,
  tone,
}: {
  icon: ComponentType<{ className?: string }>
  title: string
  detail: string
  tone?: "danger"
}) {
  return (
    <div className="grid min-h-full place-items-center p-6">
      <div className="max-w-md rounded-md border border-chrome-border bg-secondary-background/50 p-5 text-center">
        <div className={cn("mx-auto grid h-10 w-10 place-items-center rounded border", tone === "danger" ? "border-danger/30 bg-danger/10 text-danger" : "border-chrome-border/70 bg-foreground/[0.04] text-rvbbit-accent")}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="mt-3 text-sm font-semibold text-foreground">{title}</div>
        <div className="mt-1 text-[11px] text-chrome-text/65">{detail}</div>
      </div>
    </div>
  )
}

function Loading() {
  return (
    <div className="grid min-h-full place-items-center text-chrome-text/60">
      <div className="flex items-center gap-2 font-mono text-[11px]">
        <RefreshCw className="h-4 w-4 animate-spin text-rvbbit-accent" />
        loading MCP activity
      </div>
    </div>
  )
}

function TinyEmpty({ label }: { label: string }) {
  return (
    <div className="grid min-h-[120px] place-items-center rounded border border-dashed border-chrome-border/60 text-[10px] text-chrome-text/45">
      {label}
    </div>
  )
}

interface FlowNode {
  kind: "caller" | "tool" | "object"
  key: string
  label: string
  calls: number
  x: number
  y: number
  w: number
  color: string
  active: boolean
}

interface FlowEdge {
  from: FlowNode
  to: FlowNode
  calls: number
  errors: number
  width: number
  active: boolean
}

function buildFlow(
  aggregateNodes: McpIncomingFlowNode[],
  aggregateEdges: McpIncomingFlowEdge[],
  width: number,
  height: number,
  filters: {
    callerFilter: string | null
    toolFilter: string | null
    objectFilter: string | null
    status: StatusFilter
  },
): { nodes: FlowNode[]; edges: FlowEdge[]; axis: { callers: number; tools: number; objects: number } } {
  if (width <= 0 || height <= 0 || aggregateNodes.length === 0) {
    return { nodes: [], edges: [], axis: { callers: 0, tools: 0, objects: 0 } }
  }
  const nodeW = Math.max(104, Math.min(176, width * 0.22))
  const xCaller = Math.round(width * 0.14)
  const xTool = Math.round(width * 0.5)
  const xObject = Math.round(width * 0.86)
  const callers = ensureValue(topAggregateKeys(aggregateNodes, "caller", filters.status, 7), filters.callerFilter, 7)
  const tools = ensureValue(topAggregateKeys(aggregateNodes, "tool", filters.status, 8), filters.toolFilter, 8)
  const objects = ensureValue(topAggregateKeys(aggregateNodes, "object", filters.status, 9), filters.objectFilter, 9)
  const toolColor = new Map(tools.map((t, i) => [t, TOOL_COLORS[i % TOOL_COLORS.length]]))
  const aggregateByKey = new Map(aggregateNodes.map((node) => [`${node.kind}:${node.key}`, node]))

  const makeNodes = (items: string[], kind: FlowNode["kind"], x: number): FlowNode[] => {
    const ys = spread(items.length, height, 44, 34)
    return items.map((key, i) => {
      const aggregate = aggregateByKey.get(`${kind}:${key}`)
      const calls = aggregate ? statusCount(aggregate.calls, aggregate.errors, filters.status) : 0
      const active =
        (kind === "caller" && filters.callerFilter === key) ||
        (kind === "tool" && filters.toolFilter === key) ||
        (kind === "object" && filters.objectFilter === key)
      return {
        kind,
        key,
        label: key,
        calls,
        x,
        y: ys[i],
        w: nodeW,
        color: kind === "tool" ? toolColor.get(key) ?? "var(--rvbbit-accent)" : kind === "object" ? "oklch(74% 0.15 96)" : "oklch(70% 0.14 230)",
        active,
      }
    })
  }

  const nodes = [
    ...makeNodes(callers, "caller", xCaller),
    ...makeNodes(tools, "tool", xTool),
    ...makeNodes(objects, "object", xObject),
  ]
  const byKey = new Map(nodes.map((n) => [`${n.kind}:${n.key}`, n]))
  const visibleEdges = aggregateEdges
    .map((edge) => ({
      ...edge,
      visibleCalls: statusCount(edge.calls, edge.errors, filters.status),
      visibleErrors: filters.status === "ok" ? 0 : edge.errors,
    }))
    .filter((edge) => edge.visibleCalls > 0)
  const maxCalls = Math.max(1, ...visibleEdges.map((edge) => edge.visibleCalls))
  const edges: FlowEdge[] = []
  for (const edge of visibleEdges) {
    const from = byKey.get(`${edge.fromKind}:${edge.fromKey}`)
    const to = byKey.get(`${edge.toKind}:${edge.toKey}`)
    if (!from || !to) continue
    const active = from.active || to.active || !filters.callerFilter && !filters.toolFilter && !filters.objectFilter
    edges.push({
      from,
      to,
      calls: edge.visibleCalls,
      errors: edge.visibleErrors,
      width: Math.max(1.5, 1.5 + (edge.visibleCalls / maxCalls) * 8),
      active,
    })
  }

  return { nodes, edges, axis: { callers: xCaller, tools: xTool, objects: xObject } }
}

function curvePath(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.max(60, Math.abs(x2 - x1) * 0.45)
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
}

function spread(count: number, height: number, top: number, bottom: number): number[] {
  if (count <= 0) return []
  if (count === 1) return [Math.round(height / 2)]
  const usable = Math.max(1, height - top - bottom)
  return Array.from({ length: count }, (_, i) => Math.round(top + (usable * i) / (count - 1)))
}

function topAggregateKeys(nodes: McpIncomingFlowNode[], kind: FlowNode["kind"], status: StatusFilter, limit: number): string[] {
  return nodes
    .filter((node) => node.kind === kind && statusCount(node.calls, node.errors, status) > 0)
    .sort((a, b) => statusCount(b.calls, b.errors, status) - statusCount(a.calls, a.errors, status) || a.key.localeCompare(b.key))
    .slice(0, limit)
    .map((node) => node.key)
}

function ensureValue(values: string[], value: string | null, limit: number): string[] {
  if (!value || values.includes(value)) return values
  return [value, ...values].slice(0, limit)
}

function timelinePath(
  buckets: McpIncomingBucket[],
  width: number,
  height: number,
  top: number,
  bottom: number,
  max: number,
): string {
  if (buckets.length === 0) return ""
  return buckets
    .map((bucket, index) => {
      const x = timelineX(index, buckets.length, width)
      const y = timelineY(bucket.calls, max, height, top, bottom)
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(" ")
}

function timelineAreaPath(line: string, width: number, baseline: number): string {
  return line ? `${line} L ${width} ${baseline} L 0 ${baseline} Z` : ""
}

function timelineX(index: number, count: number, width: number): number {
  return count <= 1 ? width / 2 : (index / (count - 1)) * width
}

function timelineY(value: number, max: number, height: number, top: number, bottom: number): number {
  return top + (1 - value / Math.max(1, max)) * (height - top - bottom)
}

function timelineTooltipPosition(index: number, count: number) {
  const percent = count <= 1 ? 50 : (index / (count - 1)) * 100
  if (percent < 18) return { left: `${percent}%`, transform: "translateX(0)" }
  if (percent > 82) return { left: `${percent}%`, transform: "translateX(-100%)" }
  return { left: `${percent}%`, transform: "translateX(-50%)" }
}

function periodComparison(current: number, previous: number): string {
  if (current === 0 && previous === 0) return "quiet in both"
  if (previous === 0) return "new vs prior"
  const percent = Math.round(((current - previous) / previous) * 100)
  if (percent === 0) return "same as prior"
  return `${percent > 0 ? "+" : "-"}${fmtCount(Math.abs(percent))}% vs prior`
}

function formatTimelineBucket(bucket: number | null, range: McpIncomingRange): string {
  if (bucket == null) return `Current ${range}`
  const date = new Date(bucket)
  if (range === "24h") {
    return date.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })
  }
  if (range === "7d") {
    return date.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric" })
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

function formatTimelineAxis(bucket: number | null, range: McpIncomingRange): string {
  if (bucket == null) return range
  const date = new Date(bucket)
  if (range === "24h") return date.toLocaleString(undefined, { weekday: "short", hour: "numeric" })
  if (range === "7d") return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function timelinePointLabel(
  current: McpIncomingBucket | undefined,
  previous: McpIncomingBucket | undefined,
  range: McpIncomingRange,
): string {
  const currentLabel = formatTimelineBucket(current?.bucket ?? null, range)
  const previousLabel = formatTimelineBucket(previous?.bucket ?? null, range)
  return `${currentLabel}: ${current?.calls ?? 0} current calls; ${previousLabel}: ${previous?.calls ?? 0} previous calls`
}

function statusMatches(activity: McpIncomingActivity, filter: StatusFilter): boolean {
  if (filter === "all") return true
  if (filter === "error") return activity.ok === false
  return activity.ok !== false
}

function statusCount(calls: number, errors: number, filter: StatusFilter): number {
  if (filter === "error") return errors
  if (filter === "ok") return Math.max(0, calls - errors)
  return calls
}

function objectSummary(objects: string[]): string | null {
  if (objects.length === 0) return null
  if (objects.length === 1) return objects[0]
  return `${objects[0]} +${objects.length - 1}`
}

function stringifyJson(value: Record<string, unknown>): string {
  const text = JSON.stringify(value, null, 2)
  return text.length > 4000 ? `${text.slice(0, 4000)}\n...` : text
}

function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect
      if (cr) setSize({ width: cr.width, height: cr.height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}
