"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  AlertTriangle,
  Activity,
  Box,
  CheckCircle2,
  Clock,
  DollarSign,
  Database,
  Eye,
  FlowArrow,
  GitBranch,
  Hash,
  Layers,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  Rocket,
  Save,
  Search,
  Sigma,
  Sparkles,
  TrendingUp,
  TreeStructure,
  X,
} from "@/lib/icons"
import {
  cancelBusinessTopologyWorkflow,
  decideBusinessSemanticFamilyRelationship,
  fetchBusinessValueGraphs,
  fetchBusinessTopology,
  fetchBusinessTopologyWorkflow,
  promoteBusinessValueGraph,
  refreshBusinessValuePromotion,
  runBusinessSemanticWatches,
  saveBusinessTopologyCorrection,
  startBusinessTopologyWorkflow,
  type BusinessTopologyState,
  type BusinessSemanticDiffItem,
  type BusinessSemanticDimensionCensus,
  type BusinessSemanticFamilyRelationship,
  type BusinessValueGraph,
  type BusinessValueGraphState,
  type BusinessValueChangeItem,
  type BusinessValueMetric,
  type BusinessValueMetricEvaluation,
  type TopologyBinding,
  type TopologyBindingPatch,
  type TopologyBundle,
  type TopologyBundleCorrection,
  type TopologyEdge,
  type TopologyNode,
  type TopologyNodePatch,
  type TopologyNodeKind,
  type TopologyPopulationContext,
  type TopologySource,
  type TopologyWorkflowRun,
  type TopologyWorkflowState,
} from "@/lib/rvbbit/business-topology"
import { cn } from "@/lib/utils"

interface Props {
  activeConnectionId: string | null
  workspaceActive: boolean
}

type InspectorTab = "object" | "evidence" | "review" | "receipt"
type BundleFilter = "open" | "all" | "needs_revision" | "rejected"
type TopologySurface = "value" | "objects"
type TopologyScope = "current" | "run"
type ValueMode = "topology" | "dossier" | "change" | "contribution" | "definition"

const KIND_TONES: Record<TopologyNodeKind, string> = {
  object: "oklch(76% 0.14 190)",
  facet: "oklch(73% 0.13 250)",
  lifecycle: "oklch(75% 0.15 75)",
  event: "oklch(72% 0.16 25)",
  measure: "oklch(78% 0.14 105)",
  category: "oklch(73% 0.14 320)",
}

const NODE_KINDS = Object.keys(KIND_TONES) as TopologyNodeKind[]
const BINDING_ROLES = [
  "identity",
  "attribute",
  "event",
  "measure",
  "category",
  "status",
  "time",
  "geography",
  "evidence",
  "context",
] as const
const AUTHORITY_HINTS = ["unknown", "primary", "secondary", "derived", "conflicting"] as const

function correctionDraft(bundle: TopologyBundle): TopologyBundleCorrection {
  const correction = bundle.correction
  return {
    schema_version: "rvbbit.business-topology.bundle-correction.v1",
    canonical_name: correction?.canonical_name ?? bundle.canonicalName,
    node_patches: correction?.node_patches.map((patch) => ({ ...patch })) ?? [],
    binding_patches: correction?.binding_patches.map((patch) => ({ ...patch })) ?? [],
    relationship_suggestions:
      correction?.relationship_suggestions.map((suggestion) => ({
        ...suggestion,
        node_keys: [...suggestion.node_keys],
      })) ?? [],
    review_note: correction?.review_note ?? "",
  }
}

function bindingPatchKey(binding: Pick<TopologyBinding, "node_key" | "population_id" | "binding_role">): string {
  return `${binding.node_key}\u0000${binding.population_id}\u0000${binding.binding_role}`
}

function formatMoney(value: number): string {
  if (value === 0) return "$0"
  if (value < 0.01) return `$${value.toFixed(4)}`
  return `$${value.toFixed(2)}`
}

function formatTime(value: string): string {
  if (!value) return "unknown time"
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(date)
}

function sourceLabel(source: TopologySource): string {
  const locator = source.source ?? {}
  return [locator.schema, locator.relation].filter(Boolean).join(".") || source.name
}

function bundleTitle(bundle: TopologyBundle): string {
  return bundle.canonicalName
}

function workflowResultKey(workflow: TopologyWorkflowState): string {
  const run = workflow.run
  const displayRunId = workflow.displayRunId ?? run?.runId ?? "none"
  if (!run || displayRunId !== run.runId) return `${displayRunId}:background`
  const valueGraphsReady = Number(run.progress.value_graphs_ready ?? 0)
  const semanticCacheHits = Number(run.progress.semantic_cache_hits ?? run.result?.semantic_cache_hits ?? 0)
  const terminalRevision = ["completed", "failed", "cancelled"].includes(run.status)
    ? run.updatedAt
    : "active"
  return [
    displayRunId,
    run.status,
    run.bundlesStaged,
    valueGraphsReady,
    semanticCacheHits,
    terminalRevision,
  ].join(":")
}

function statusTone(status: TopologyBundle["status"]): string {
  if (status === "proposed") return "bg-cyan-400"
  if (status === "needs_revision") return "bg-amber-400"
  if (status === "rejected") return "bg-danger"
  return "bg-chrome-text/35"
}

export function BusinessTopologyWindow({ activeConnectionId, workspaceActive }: Props) {
  const [state, setState] = useState<BusinessTopologyState | null>(null)
  const [valueState, setValueState] = useState<BusinessValueGraphState | null>(null)
  const [workflow, setWorkflow] = useState<TopologyWorkflowState | null>(null)
  const [loading, setLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [selectedBundleId, setSelectedBundleId] = useState<string | null>(null)
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<BundleFilter>("open")
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("object")
  const [surface, setSurface] = useState<TopologySurface>("value")
  const [scope, setScope] = useState<TopologyScope>("current")
  const [targetSchema, setTargetSchema] = useState("")
  const [selectedGraphId, setSelectedGraphId] = useState<string | null>(null)
  const [selectedMetricKey, setSelectedMetricKey] = useState<string | null>(null)
  const [valueMode, setValueMode] = useState<ValueMode>("topology")
  const resultRefreshRef = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    if (!activeConnectionId) {
      setState({ available: false, correctionsAvailable: false, bundles: [] })
      setValueState({ available: false, graphs: [] })
      setWorkflow({ available: false, workerOnline: false, run: null, displayRunId: null, workers: [], queuedRuns: [], candidateSchemas: [] })
      return
    }
    setLoading(true)
    try {
      const nextWorkflow = await fetchBusinessTopologyWorkflow(activeConnectionId)
      const runId = scope === "run"
        ? nextWorkflow.displayRunId ?? nextWorkflow.run?.runId ?? null
        : null
      const [next, nextValues] = await Promise.all([
        fetchBusinessTopology(activeConnectionId, runId),
        fetchBusinessValueGraphs(activeConnectionId, runId),
      ])
      setState(next)
      setWorkflow(nextWorkflow)
      setTargetSchema((current) => current && nextWorkflow.candidateSchemas.includes(current)
        ? current
        : nextWorkflow.candidateSchemas.includes("crime_feeds")
          ? "crime_feeds"
          : nextWorkflow.candidateSchemas[0] ?? "")
      resultRefreshRef.current = workflowResultKey(nextWorkflow)
      setValueState(nextValues)
      setSelectedBundleId((current) =>
        current && next.bundles.some((bundle) => bundle.bundleId === current)
          ? current
          : next.bundles[0]?.bundleId ?? null,
      )
      setSelectedGraphId((current) =>
        current && nextValues.graphs.some((graph) => graph.graphId === current)
          ? current
          : nextValues.graphs[0]?.graphId ?? null,
      )
    } finally {
      setLoading(false)
    }
  }, [activeConnectionId, scope])

  const pollWorkflow = useCallback(async () => {
    if (!activeConnectionId) return
    const next = await fetchBusinessTopologyWorkflow(activeConnectionId)
    setWorkflow(next)
    const runId = scope === "run"
      ? next.displayRunId ?? next.run?.runId ?? null
      : null
    const resultKey = workflowResultKey(next)
    if ((scope === "run" && !runId) || resultRefreshRef.current === resultKey) return
    resultRefreshRef.current = resultKey
    const nextBundles = await fetchBusinessTopology(activeConnectionId, runId)
    const nextValues = await fetchBusinessValueGraphs(activeConnectionId, runId)
    setState(nextBundles)
    setValueState(nextValues)
    setSelectedBundleId((current) =>
      current && nextBundles.bundles.some((bundle) => bundle.bundleId === current)
        ? current
        : nextBundles.bundles[0]?.bundleId ?? null,
    )
    setSelectedGraphId((current) =>
      current && nextValues.graphs.some((graph) => graph.graphId === current)
        ? current
        : nextValues.graphs[0]?.graphId ?? null,
    )
  }, [activeConnectionId, scope])

  useEffect(() => {
    if (!workspaceActive) return
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [workspaceActive, refresh])

  const activeRun = workflow?.run
  const workflowActive = !!activeRun
    && ["queued", "running", "cancel_requested"].includes(activeRun.status)

  useEffect(() => {
    if (!workspaceActive || !workflowActive) return
    const timer = window.setInterval(() => void pollWorkflow(), 2_000)
    return () => window.clearInterval(timer)
  }, [pollWorkflow, workflowActive, workspaceActive])

  useEffect(() => {
    resultRefreshRef.current = null
  }, [activeConnectionId])

  const startExcavation = useCallback(async () => {
    if (!activeConnectionId || starting || !targetSchema) return
    setStarting(true)
    setActionError(null)
    setActionMessage(null)
    try {
      const result = await startBusinessTopologyWorkflow(activeConnectionId, targetSchema)
      if (!result.ok) {
        setActionError(result.error)
        return
      }
      setActionMessage(`${workflowActive ? "Queued" : "Started"} ${targetSchema} excavation · ${result.runId.slice(0, 8)}`)
      resultRefreshRef.current = null
      await refresh()
    } finally {
      setStarting(false)
    }
  }, [activeConnectionId, refresh, starting, targetSchema, workflowActive])

  const cancelExcavation = useCallback(async () => {
    if (!activeConnectionId || !activeRun || cancelling || !workflowActive) return
    setCancelling(true)
    setActionError(null)
    try {
      const result = await cancelBusinessTopologyWorkflow(activeConnectionId, activeRun.runId)
      if (!result.ok) {
        setActionError(result.error)
        return
      }
      await pollWorkflow()
    } finally {
      setCancelling(false)
    }
  }, [activeConnectionId, activeRun, cancelling, pollWorkflow, workflowActive])

  const bundles = useMemo(() => state?.bundles ?? [], [state])
  const visibleBundles = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return bundles.filter((bundle) => {
      if (filter === "open" && !["proposed", "needs_revision"].includes(bundle.status)) return false
      if (filter !== "all" && filter !== "open" && bundle.status !== filter) return false
      if (!needle) return true
      return [
        bundleTitle(bundle),
        bundle.scopeId,
        bundle.workId,
        ...bundle.sources.map(sourceLabel),
        ...bundle.result.nodes.map((node) => node.name),
      ].some((value) => value.toLowerCase().includes(needle))
    })
  }, [bundles, filter, query])

  const selectedBundle =
    visibleBundles.find((bundle) => bundle.bundleId === selectedBundleId) ?? visibleBundles[0] ?? null
  const selectedNode = selectedBundle?.result.nodes.find((node) => node.node_key === selectedNodeKey)
    ?? selectedBundle?.result.nodes[0]
    ?? null
  const valueGraphs = valueState?.graphs ?? []
  const selectedGraph = valueGraphs.find((graph) => graph.graphId === selectedGraphId)
    ?? valueGraphs[0]
    ?? null
  const selectedMetric = selectedGraph?.metrics.find((metric) => metric.metric_key === selectedMetricKey)
    ?? selectedGraph?.metrics.find((metric) => metric.metric_key === selectedGraph.primaryMetricKey)
    ?? selectedGraph?.metrics[0]
    ?? null

  if (!state || !valueState) return <LoadingState label="assembling business meaning" />
  if (!activeConnectionId) return <EmptyState title="No warehouse connected" body="Choose a connection to inspect its Business Topology proposals." />
  if (!state.available) {
    return (
      <EmptyState
        title="Business Topology is not initialized"
        body="This warehouse does not have the proposal-bundle migration yet. The debugger appears after the receipt-backed bundle ledger is installed."
        error={state.error}
      />
    )
  }

  const openCount = bundles.filter((bundle) => ["proposed", "needs_revision"].includes(bundle.status)).length
  const totalNodes = bundles.reduce((sum, bundle) => sum + bundle.nodeCount, 0)
  const totalSources = new Set(bundles.flatMap((bundle) => bundle.sourceKeys)).size
  const totalMetrics = valueGraphs.reduce((sum, graph) => sum + graph.metrics.length, 0)
  const totalBusinessDomains = new Set(
    valueGraphs.map((graph) => graph.businessDomain?.domain_key ?? graph.domainKey),
  ).size
  const valueErrors = Array.isArray(activeRun?.progress.value_graph_errors)
    ? activeRun.progress.value_graph_errors as Array<Record<string, unknown>>
    : []
  const emptyValueBody = workflowActive
    ? "Calliope is reconciling compact source findings into coherent business domains, then proving each domain's metrics in SQL."
    : activeRun?.status === "completed"
      ? `${activeRun.bundlesStaged} object bundles were staged, but this run produced no value systems.${valueErrors.length > 0
        ? ` ${valueErrors.map((error) => `${String(error.domain_key ?? "domain")}: ${String(error.error ?? "failed")}`).join(" · ")}`
        : ""}`
      : "Run a new excavation to build executable outcome, contribution, and change models above the object topology."

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-doc-bg text-foreground group-data-[focused=false]/window:bg-doc-bg/75">
      <header className="shrink-0 border-b border-chrome-border/60 bg-chrome-bg/30 px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-cyan-400/35 bg-cyan-400/10">
            {surface === "value" ? <FlowArrow className="h-4 w-4 text-cyan-300" /> : <TreeStructure className="h-4 w-4 text-cyan-300" />}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-2">
              <h2 className="text-sm font-semibold">Business Topology</h2>
              <span className="rounded-full border border-amber-400/35 bg-amber-400/8 px-1.5 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-amber-300">
                proposals + governed
              </span>
            </div>
            <div className="font-mono text-[9px] text-chrome-text/55">
              {surface === "value"
                ? `${totalBusinessDomains} domains · ${valueGraphs.length} value systems · ${totalMetrics} executable metrics · ${totalSources} sources`
                : `${openCount} review bundles · ${totalNodes} proposed concepts · ${totalSources} sources`}
            </div>
          </div>
          <div className="ml-2 flex h-7 items-center rounded-sm border border-chrome-border/55 bg-doc-bg/30 p-0.5">
            <button
              type="button"
              onClick={() => setSurface("value")}
              className={cn(
                "inline-flex h-5 items-center gap-1 rounded-[2px] px-2 text-[9px] transition",
                surface === "value" ? "bg-cyan-400/12 text-cyan-200" : "text-chrome-text/45 hover:text-foreground",
              )}
            >
              <TrendingUp className="h-3 w-3" /> Value systems
            </button>
            <button
              type="button"
              onClick={() => setSurface("objects")}
              className={cn(
                "inline-flex h-5 items-center gap-1 rounded-[2px] px-2 text-[9px] transition",
                surface === "objects" ? "bg-cyan-400/12 text-cyan-200" : "text-chrome-text/45 hover:text-foreground",
              )}
            >
              <TreeStructure className="h-3 w-3" /> Object map
            </button>
          </div>
          <div className="flex h-7 items-center rounded-sm border border-chrome-border/55 bg-doc-bg/30 p-0.5">
            <button
              type="button"
              onClick={() => setScope("current")}
              className={cn(
                "h-5 rounded-[2px] px-2 text-[9px] transition",
                scope === "current" ? "bg-emerald-400/12 text-emerald-200" : "text-chrome-text/45 hover:text-foreground",
              )}
            >
              Current topology
            </button>
            <button
              type="button"
              onClick={() => setScope("run")}
              className={cn(
                "h-5 rounded-[2px] px-2 text-[9px] transition",
                scope === "run" ? "bg-amber-400/12 text-amber-200" : "text-chrome-text/45 hover:text-foreground",
              )}
            >
              Run inspector
            </button>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-1.5 text-[9px] text-chrome-text/45 md:flex">
              <Eye className="h-3 w-3" /> inspect proof · promote only what should become canon
            </div>
            {workflow?.available ? (
              <div className="flex h-7 items-center overflow-hidden rounded-sm border border-cyan-400/35 bg-cyan-400/8">
                <select
                  value={targetSchema}
                  onChange={(event) => setTargetSchema(event.target.value)}
                  className="h-full max-w-36 border-0 bg-transparent px-2 font-mono text-[9px] text-cyan-100 outline-none"
                  title="Target schema"
                >
                  {workflow.candidateSchemas.map((schema) => (
                    <option key={schema} value={schema} className="bg-doc-bg text-foreground">{schema}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => void startExcavation()}
                  disabled={starting || !targetSchema}
                  className="inline-flex h-full items-center gap-1.5 border-l border-cyan-400/25 px-2.5 text-[10px] font-medium text-cyan-200 transition hover:bg-cyan-400/18 disabled:cursor-default disabled:opacity-55"
                  title={workflowActive ? "Queue this schema behind the active excavation" : "Start a targeted excavation"}
                >
                  {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                  {starting ? "Queueing…" : workflowActive ? "Queue next" : "Excavate"}
                </button>
              </div>
            ) : null}
            {workflowActive ? (
              <button
                type="button"
                onClick={() => void cancelExcavation()}
                disabled={cancelling || activeRun?.status === "cancel_requested"}
                className="grid h-7 w-7 place-items-center rounded-sm border border-chrome-border/60 text-chrome-text transition hover:border-danger/45 hover:bg-danger/8 hover:text-danger disabled:opacity-40"
                title="Cancel excavation after the current bounded operation"
              >
                {cancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="grid h-7 w-7 place-items-center rounded-sm border border-chrome-border/60 text-chrome-text transition hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-45"
              title="Refresh proposal bundles"
            >
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
            </button>
          </div>
        </div>
        {state.error || valueState.error || workflow?.error || actionError ? (
          <div className="mt-1 font-mono text-[9px] text-danger/75">
            {actionError ?? workflow?.error ?? valueState.error ?? state.error}
          </div>
        ) : null}
        {actionMessage ? (
          <div className="mt-1 font-mono text-[9px] text-emerald-300/75">{actionMessage}</div>
        ) : null}
      </header>

      {workflow?.available && activeRun ? (
        <WorkflowStatusBar workflow={workflow} run={activeRun} currentTopology={scope === "current"} />
      ) : workflow?.available && !workflow.workerOnline ? (
        <div className="shrink-0 border-b border-amber-400/20 bg-amber-400/[0.045] px-3 py-1.5 font-mono text-[9px] text-amber-200/75">
          Excavations can be queued, but no recent warehouse-mcp topology worker heartbeat is visible.
        </div>
      ) : null}
      {workflow?.available ? <WorkflowWorkerBar workflow={workflow} /> : null}

      {surface === "value" ? (
        valueGraphs.length === 0 ? (
          <EmptyState
            title={valueState.available ? "No value systems have been proven yet" : "Business Value Graph is not initialized"}
            body={emptyValueBody}
            error={valueState.error}
          />
        ) : selectedGraph ? (
          <ValueGraphWorkspace
            connectionId={activeConnectionId}
            graphs={valueGraphs}
            graph={selectedGraph}
            metric={selectedMetric}
            mode={valueMode}
            onMode={setValueMode}
            onGraph={(graphId) => {
              setSelectedGraphId(graphId)
              setSelectedMetricKey(null)
            }}
            onMetric={setSelectedMetricKey}
            onChanged={refresh}
          />
        ) : null
      ) : bundles.length === 0 ? (
        <EmptyState
          title="No shadow skeletons staged yet"
          body={workflowActive
            ? "A fresh bounded excavation is running. Validated proposal bundles will appear here automatically when staging completes."
            : "Start a fresh excavation above. It profiles the current warehouse, discovers source neighborhoods, composes candidate business objects, and stages them here for review."}
        />
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(390px,1fr)_minmax(300px,0.72fr)] overflow-hidden max-[900px]:grid-cols-[225px_minmax(420px,1fr)]">
          <BundleRail
            bundles={visibleBundles}
            allCount={bundles.length}
            selectedId={selectedBundle?.bundleId ?? null}
            query={query}
            filter={filter}
            onQuery={setQuery}
            onFilter={setFilter}
            onSelect={(bundleId) => {
              setSelectedBundleId(bundleId)
              setSelectedNodeKey(null)
              setInspectorTab("object")
            }}
          />
          {selectedBundle ? (
            <>
              <SkeletonCanvas
                bundle={selectedBundle}
                selectedNodeKey={selectedNode?.node_key ?? null}
                onSelectNode={(nodeKey) => {
                  setSelectedNodeKey(nodeKey)
                  setInspectorTab("object")
                }}
              />
              <Inspector
                connectionId={activeConnectionId}
                correctionsAvailable={state.correctionsAvailable}
                bundle={selectedBundle}
                node={selectedNode}
                tab={inspectorTab}
                onTab={setInspectorTab}
                onSaved={refresh}
              />
            </>
          ) : (
            <div className="col-span-2 grid place-items-center text-xs text-chrome-text/45">No bundle matches this filter.</div>
          )}
        </main>
      )}
    </div>
  )
}

function formatBusinessValue(value: number, unit: BusinessValueMetric["unit"], compact = false): string {
  if (!Number.isFinite(value)) return "—"
  const notation = compact ? "compact" : "standard"
  if (unit === "currency") {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      notation,
      maximumFractionDigits: compact ? 1 : 2,
    }).format(value)
  }
  if (unit === "ratio") {
    return new Intl.NumberFormat(undefined, {
      style: "percent",
      maximumFractionDigits: 1,
    }).format(value)
  }
  return new Intl.NumberFormat(undefined, {
    notation,
    maximumFractionDigits: compact ? 1 : 2,
  }).format(value)
}

function periodLabel(value: string | undefined, bucket: string): string {
  if (!value) return "unknown period"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  if (bucket === "year") return String(date.getUTCFullYear())
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    year: "numeric",
    ...(bucket === "day" || bucket === "week" ? { day: "numeric" } : {}),
    timeZone: "UTC",
  }).format(date)
}

function metricEvaluation(
  graph: BusinessValueGraph,
  metric: BusinessValueMetric | null,
): BusinessValueMetricEvaluation | null {
  if (!metric) return null
  return graph.evaluations.find((evaluation) => evaluation.metric_key === metric.metric_key) ?? null
}

function ValueGraphWorkspace({
  connectionId,
  graphs,
  graph,
  metric,
  mode,
  onMode,
  onGraph,
  onMetric,
  onChanged,
}: {
  connectionId: string
  graphs: BusinessValueGraph[]
  graph: BusinessValueGraph
  metric: BusinessValueMetric | null
  mode: ValueMode
  onMode: (mode: ValueMode) => void
  onGraph: (graphId: string) => void
  onMetric: (metricKey: string) => void
  onChanged: () => Promise<void>
}) {
  const evaluation = metricEvaluation(graph, metric)
  const dimensionKey = metric?.primary_dimension_key
    ?? metric?.dimensions[0]?.key
    ?? null
  const dimension = metric?.dimensions.find((item) => item.key === dimensionKey)
  const changeItems = dimensionKey ? evaluation?.changes[dimensionKey] ?? [] : []
  const dimensionCensus = metric && dimensionKey
    ? graph.semanticDossier?.dimensionCensuses.find(
      (census) => census.metricKey === metric.metric_key && census.dimensionKey === dimensionKey,
    ) ?? null
    : null

  return (
    <main className="grid min-h-0 flex-1 grid-cols-[245px_minmax(460px,1fr)_315px] overflow-hidden max-[980px]:grid-cols-[220px_minmax(460px,1fr)]">
      <aside className="flex min-h-0 flex-col border-r border-chrome-border/55 bg-secondary-background/16">
        <div className="border-b border-chrome-border/45 px-3 py-2.5">
          <div className="font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/42">Value systems</div>
          <div className="mt-1 text-[10px] leading-relaxed text-chrome-text/55">Overlapping analytical systems grouped under durable business domains.</div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
          {graphs.map((candidate) => (
            <button
              key={candidate.graphId}
              type="button"
              onClick={() => onGraph(candidate.graphId)}
              className={cn(
                "w-full border-l-2 px-3 py-2.5 text-left transition",
                candidate.graphId === graph.graphId
                  ? "border-l-cyan-300 bg-cyan-400/[0.075]"
                  : "border-l-transparent hover:bg-foreground/[0.035]",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">{candidate.name}</span>
                {candidate.reconciled ? <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-300/75" /> : <AlertTriangle className="h-3 w-3 shrink-0 text-amber-300/70" />}
              </div>
              <div className="mt-0.5 truncate text-[8.5px] text-cyan-200/48">
                {candidate.businessDomain?.name ?? candidate.domainKey}
              </div>
              <div className="mt-1 flex gap-2 font-mono text-[8px] text-chrome-text/42">
                <span>{candidate.metrics.length} metrics</span>
                <span>{candidate.sourceRelations.length} relations</span>
                {candidate.semanticDossier?.familyRelationships.length ? (
                  <span className="text-violet-200/60">{candidate.semanticDossier.familyRelationships.length} family links</span>
                ) : null}
                {candidate.fallback ? <span className="text-amber-300/65">fallback</span> : null}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-chrome-border/55 max-[980px]:border-r-0">
        <div className="shrink-0 border-b border-chrome-border/45 bg-secondary-background/10 px-3 py-2.5">
          <div className="flex min-w-0 items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-mono text-[8px] uppercase tracking-[0.18em] text-cyan-200/58">
                {graph.businessDomain?.name ?? graph.domainKey} · {graph.valueSystemKey} · value system
              </div>
              <h3 className="mt-0.5 truncate text-[13px] font-semibold">{graph.name}</h3>
              <p className="mt-1 line-clamp-2 max-w-3xl text-[10px] leading-relaxed text-chrome-text/55">{graph.summary}</p>
            </div>
            <div className="flex h-7 shrink-0 items-center rounded-sm border border-chrome-border/55 bg-doc-bg/35 p-0.5">
              {(["topology", "dossier", "change", "contribution", "definition"] as ValueMode[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => onMode(value)}
                  className={cn(
                    "h-5 rounded-[2px] px-2 font-mono text-[8px] uppercase tracking-wide transition",
                    mode === value ? "bg-cyan-400/12 text-cyan-200" : "text-chrome-text/42 hover:text-foreground",
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
          {graph.metrics.length > 1 ? (
            <div className="mt-2 flex gap-1 overflow-x-auto">
              {graph.metrics.map((candidate) => (
                <button
                  key={candidate.metric_key}
                  type="button"
                  onClick={() => onMetric(candidate.metric_key)}
                  className={cn(
                    "whitespace-nowrap rounded-full border px-2 py-0.5 text-[8px] transition",
                    candidate.metric_key === metric?.metric_key
                      ? "border-cyan-300/35 bg-cyan-400/10 text-cyan-100"
                      : "border-chrome-border/45 text-chrome-text/45 hover:text-foreground",
                  )}
                >
                  {candidate.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {mode === "topology" ? (
            <SemanticTopology graph={graph} />
          ) : mode === "dossier" ? (
            <SemanticDossier connectionId={connectionId} graph={graph} onChanged={onChanged} />
          ) : !metric || !evaluation ? (
            <div className="grid min-h-[260px] place-items-center rounded-md border border-dashed border-chrome-border/55 text-center">
              <div className="max-w-sm">
                <Sigma className="mx-auto h-6 w-6 text-chrome-text/28" />
                <div className="mt-2 text-[11px] text-chrome-text/55">No executable outcome survived validation for this domain.</div>
                <div className="mt-1 text-[9px] leading-relaxed text-chrome-text/38">The object topology remains available, but Calliope did not invent a metric merely to fill the screen.</div>
              </div>
            </div>
          ) : mode === "definition" ? (
            <ValueDefinition graph={graph} metric={metric} evaluation={evaluation} onMetric={onMetric} />
          ) : (
            <ValueRiver
              metric={metric}
              evaluation={evaluation}
              dimensionName={dimension?.name ?? "Business dimension"}
              items={changeItems}
              census={dimensionCensus}
              mode={mode}
            />
          )}
        </div>
      </section>

      <ValueInspector
        connectionId={connectionId}
        graph={graph}
        metric={metric}
        evaluation={evaluation}
        onChanged={onChanged}
      />
    </main>
  )
}

function SemanticDossier({ connectionId, graph, onChanged }: {
  connectionId: string
  graph: BusinessValueGraph
  onChanged: () => Promise<void>
}) {
  const [runningChecks, setRunningChecks] = useState(false)
  const [checkMessage, setCheckMessage] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  const dossier = graph.semanticDossier
  if (!dossier) {
    return (
      <div className="grid min-h-[280px] place-items-center rounded-lg border border-dashed border-chrome-border/55 text-center">
        <div className="max-w-md px-6">
          <Clock className="mx-auto h-6 w-6 text-chrome-text/28" />
          <div className="mt-2 text-[11px] text-chrome-text/58">This graph predates the semantic ledger.</div>
          <div className="mt-1 text-[9px] leading-relaxed text-chrome-text/40">The migration backfill or the next reconciled topology run will create its assertions, diff, and watch plan.</div>
        </div>
      </div>
    )
  }
  const diff = dossier.diff
  const added = diff.added ?? []
  const removed = diff.removed ?? []
  const changed = diff.changed ?? []
  const coverage = Math.round(dossier.automatedWatchCoverage * 100)
  const stateTone = (state: string) => state === "passing"
    ? "border-emerald-300/22 bg-emerald-400/[0.045] text-emerald-100/75"
    : state === "failing"
      ? "border-rose-300/22 bg-rose-400/[0.045] text-rose-100/75"
      : "border-amber-300/20 bg-amber-400/[0.035] text-amber-100/65"
  const runChecks = async () => {
    setRunningChecks(true)
    setCheckMessage(null)
    setCheckError(null)
    const result = await runBusinessSemanticWatches(connectionId, graph.graphId)
    if (!result.ok) {
      setCheckError(result.error)
      setRunningChecks(false)
      return
    }
    const receipt = result.receipt
    setCheckMessage(
      `${receipt.observed ?? 0} observed · ${receipt.passing ?? 0} passing · ${receipt.failing ?? 0} failing · ${receipt.unknown ?? 0} unknown`,
    )
    await onChanged()
    setRunningChecks(false)
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <section className="grid gap-2 sm:grid-cols-4">
        <DossierStat label="family revision" value={`r${dossier.revision}`} detail={dossier.predecessorGraphId ? "has predecessor" : "baseline"} />
        <DossierStat label="typed assertions" value={String(dossier.assertionCount)} detail={`${Object.keys(dossier.assertionKinds).length} kinds`} />
        <DossierStat label="watch plan" value={String(dossier.watchCount)} detail={`${coverage}% deterministic coverage`} />
        <DossierStat
          label="last semantic delta"
          value={diff.change_kind?.replaceAll("_", " ") ?? "unavailable"}
          detail={`${added.length} added · ${changed.length} changed · ${removed.length} removed`}
        />
      </section>

      <FamilyConsolidation
        connectionId={connectionId}
        graph={graph}
        relationships={dossier.familyRelationships}
        onChanged={onChanged}
      />

      <section className="rounded-lg border border-chrome-border/50 bg-secondary-background/[0.13] p-4">
        <div className="flex items-center gap-2">
          <GitBranch className="h-3.5 w-3.5 text-cyan-200/65" />
          <div className="font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/48">What changed</div>
          <span className="ml-auto font-mono text-[8px] text-chrome-text/35">{diff.unchanged_count ?? 0} unchanged</span>
        </div>
        {added.length + removed.length + changed.length === 0 ? (
          <div className="mt-3 rounded-md border border-emerald-300/18 bg-emerald-400/[0.035] px-3 py-2 text-[9px] text-emerald-100/65">
            No assertion changed. This revision confirms the existing semantic model.
          </div>
        ) : (
          <div className="mt-3 grid gap-2 lg:grid-cols-3">
            <DiffColumn title="Added" tone="cyan" items={added} />
            <DiffColumn title="Changed" tone="amber" items={changed} />
            <DiffColumn title="Removed" tone="rose" items={removed} />
          </div>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2">
          <div className="flex items-center gap-2 font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/45">
            <Eye className="h-3.5 w-3.5" /> Continuous validation
          </div>
          <div className="ml-auto flex items-center gap-2">
            {dossier.needsDecisionCount > 0 ? (
              <span className="font-mono text-[7.5px] text-rose-200/70">{dossier.needsDecisionCount} need decision</span>
            ) : dossier.needsRevisionCount > 0 ? (
              <span className="font-mono text-[7.5px] text-amber-200/70">{dossier.needsRevisionCount} need revision</span>
            ) : null}
            <button
              type="button"
              onClick={() => void runChecks()}
              disabled={runningChecks || dossier.watchCount === 0}
              className="flex h-7 items-center gap-1.5 rounded-sm border border-cyan-300/25 bg-cyan-400/[0.055] px-2.5 font-mono text-[8px] uppercase tracking-wide text-cyan-100/75 transition hover:bg-cyan-400/[0.1] disabled:opacity-40"
            >
              {runningChecks ? <Loader2 className="h-3 w-3 animate-spin" /> : <Activity className="h-3 w-3" />}
              {runningChecks ? "Checking…" : "Run checks"}
            </button>
          </div>
        </div>
        {checkMessage ? <div className="mb-2 font-mono text-[8px] text-emerald-200/65">{checkMessage}</div> : null}
        {checkError ? <div className="mb-2 font-mono text-[8px] text-rose-200/70">{checkError}</div> : null}
        <div className="grid gap-2 md:grid-cols-2">
          {dossier.watches.map((watch) => {
            const specification = watch.specification
            const relation = String(specification.relation ?? specification.left_relation ?? specification.metric_key ?? watch.watchKey)
            const otherRelation = specification.right_relation ? ` ↔ ${String(specification.right_relation)}` : ""
            const checks = Array.isArray(specification.checks) ? specification.checks.map(String) : []
            return (
              <div key={watch.watchId} className={cn("rounded-md border px-3 py-2", stateTone(watch.state))}>
                <div className="flex items-center gap-2">
                  {watch.state === "passing" ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
                  <span className="min-w-0 flex-1 truncate text-[9px] font-medium" title={`${relation}${otherRelation}`}>{relation}{otherRelation}</span>
                  <span className="font-mono text-[7px] uppercase tracking-wide opacity-65">{watch.state}</span>
                </div>
                <div className="mt-1 font-mono text-[7.5px] uppercase tracking-wide opacity-55">{watch.kind.replaceAll("_", " ")}</div>
                <div className="mt-1 text-[7.5px] leading-relaxed opacity-55">{checks.map((check) => check.replaceAll("_", " ")).join(" · ")}</div>
                <div className="mt-2 border-t border-current/10 pt-1.5 text-[7.5px] leading-relaxed opacity-65">
                  {watchEvidenceSummary(watch.kind, watch.evidence)}
                </div>
                <div className="mt-1 flex gap-2 font-mono text-[7px] opacity-45">
                  <span>{watch.observationCount} observations</span>
                  <span>{watch.lastObservedAt ? formatTime(watch.lastObservedAt) : "not executed"}</span>
                  {watch.consecutiveFailures > 0 ? <span>{watch.consecutiveFailures} failing in a row</span> : null}
                </div>
              </div>
            )
          })}
          {dossier.watches.length === 0 ? (
            <div className="col-span-2 rounded-md border border-dashed border-chrome-border/50 px-3 py-6 text-center text-[9px] text-chrome-text/42">No assertions in this graph have a deterministic watch yet.</div>
          ) : null}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/45">
          <Hash className="h-3.5 w-3.5" /> Dimension evidence
        </div>
        {dossier.dimensionCensuses.length > 0 ? (
          <div className="grid gap-2 md:grid-cols-2">
            {dossier.dimensionCensuses.map((census) => {
              const complete = census.coverageComplete && census.memberListComplete
              return (
                <div key={`${census.metricKey}:${census.dimensionKey}`} className={cn(
                  "rounded-md border p-3",
                  census.taxonomyChanged
                    ? "border-amber-300/24 bg-amber-400/[0.04]"
                    : "border-chrome-border/45 bg-doc-bg/26",
                )}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[9.5px] font-medium">{census.dimensionKey.replaceAll("_", " ")}</span>
                    <span className={cn(
                      "rounded-full border px-1.5 py-0.5 font-mono text-[7px] uppercase tracking-wide",
                      complete
                        ? "border-emerald-300/22 text-emerald-200/68"
                        : "border-amber-300/22 text-amber-200/68",
                    )}>{complete ? "exact" : "sampled"}</span>
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[7px] text-chrome-text/35">{census.metricKey}</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {census.members.slice(0, 12).map((member) => (
                      <span key={member.value} className="rounded-full border border-chrome-border/45 px-1.5 py-0.5 text-[7.5px] text-chrome-text/58" title={`${member.row_count.toLocaleString()} rows`}>
                        {member.value} <span className="font-mono text-cyan-200/55">{member.row_count.toLocaleString()}</span>
                      </span>
                    ))}
                    {census.members.length > 12 ? <span className="px-1 py-0.5 font-mono text-[7px] text-chrome-text/30">+{census.members.length - 12} more</span> : null}
                  </div>
                  <div className="mt-2 text-[7.5px] leading-relaxed text-chrome-text/42">
                    {census.rowsObserved.toLocaleString()} rows · {census.memberCount.toLocaleString()} observed members
                    {!complete ? ` · not a complete dictionary; ${census.rowLimit.toLocaleString()}-row bound` : ""}
                  </div>
                  {census.taxonomyChanged ? (
                    <div className="mt-1.5 font-mono text-[7px] text-amber-200/68">
                      taxonomy changed · +{census.addedValues.join(", ") || "none"} · −{census.removedValues.join(", ") || "none"}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-chrome-border/50 px-3 py-5 text-center text-[9px] text-chrome-text/42">
            Run checks to build exact or explicitly bounded dimension dictionaries.
          </div>
        )}
      </section>

      <section>
        <div className="mb-2 font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/45">Assertion inventory</div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(dossier.assertionKinds).sort(([left], [right]) => left.localeCompare(right)).map(([kind, count]) => (
            <div key={kind} className="rounded-full border border-chrome-border/50 bg-doc-bg/30 px-2 py-1 text-[8px] text-chrome-text/58">
              <span>{kind.replaceAll("_", " ")}</span><span className="ml-1.5 font-mono text-cyan-200/58">{count}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function FamilyConsolidation({ connectionId, graph, relationships, onChanged }: {
  connectionId: string
  graph: BusinessValueGraph
  relationships: BusinessSemanticFamilyRelationship[]
  onChanged: () => Promise<void>
}) {
  const [decidingRelationshipId, setDecidingRelationshipId] = useState<number | null>(null)
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const familyId = graph.semanticDossier?.familyId ?? ""
  const relationshipTone = (kind: BusinessSemanticFamilyRelationship["relationshipKind"]) => {
    if (kind === "same_family" || kind === "extension") {
      return "border-cyan-300/24 bg-cyan-400/[0.045] text-cyan-100/75"
    }
    if (kind === "parallel_population") {
      return "border-violet-300/24 bg-violet-400/[0.045] text-violet-100/75"
    }
    if (kind === "material_contradiction") {
      return "border-rose-300/24 bg-rose-400/[0.045] text-rose-100/75"
    }
    if (kind === "alternative_definition") {
      return "border-amber-300/24 bg-amber-400/[0.04] text-amber-100/72"
    }
    return "border-chrome-border/48 bg-doc-bg/28 text-chrome-text/65"
  }
  const disposition = (kind: BusinessSemanticFamilyRelationship["relationshipKind"]) => {
    if (kind === "same_family") return "merge identity"
    if (kind === "extension") return "attach extension"
    if (kind === "parallel_population") return "link populations"
    if (kind === "alternative_definition") return "keep definition explicit"
    if (kind === "material_contradiction") return "decision required"
    if (kind === "distinct_family") return "keep separate"
    return "insufficient evidence"
  }
  const decide = async (
    relationship: BusinessSemanticFamilyRelationship,
    decision: "accepted" | "rejected",
  ) => {
    setDecidingRelationshipId(relationship.relationshipId)
    setDecisionMessage(null)
    setDecisionError(null)
    const result = await decideBusinessSemanticFamilyRelationship(
      connectionId,
      relationship.relationshipId,
      decision,
      decision === "accepted"
        ? "Accepted from the Business Topology semantic dossier."
        : "Rejected from the Business Topology semantic dossier.",
    )
    if (!result.ok) {
      setDecisionError(result.error)
      setDecidingRelationshipId(null)
      return
    }
    setDecisionMessage(
      decision === "accepted"
        ? "Classification accepted. Future topology runs will treat it as a semantic constraint."
        : "Classification rejected. Future topology runs will not reuse it as consolidation guidance.",
    )
    await onChanged()
    setDecidingRelationshipId(null)
  }

  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex items-center gap-2 font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/45">
          <Layers className="h-3.5 w-3.5" /> Cross-family consolidation
        </div>
        <span className="ml-auto font-mono text-[7.5px] text-chrome-text/34">
          compact dossiers · no automatic merge
        </span>
      </div>
      {relationships.length > 0 ? (
        <div className="grid gap-2 md:grid-cols-2">
          {relationships.map((relationship) => {
            const sourceIsCurrent = relationship.sourceFamilyId === familyId
              || relationship.sourceGraphId === graph.graphId
            const counterpartName = sourceIsCurrent
              ? relationship.targetValueSystemName
              : relationship.sourceValueSystemName
            const counterpartDomain = sourceIsCurrent
              ? relationship.targetBusinessDomainName
              : relationship.sourceBusinessDomainName
            const evidence = relationship.evidence
            const sharedConcepts = Array.isArray(evidence.shared_concepts)
              ? evidence.shared_concepts.map(String)
              : []
            const sharedSources = Array.isArray(evidence.shared_sources)
              ? evidence.shared_sources.map(String)
              : []
            const score = typeof evidence.candidate_score === "number"
              ? Math.round(evidence.candidate_score * 100)
              : null
            return (
              <div key={relationship.relationshipId} className={cn(
                "rounded-md border p-3",
                relationshipTone(relationship.relationshipKind),
              )}>
                <div className="flex items-start gap-2">
                  <GitBranch className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-70" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[9.5px] font-medium" title={counterpartName}>{counterpartName}</span>
                      <span className="shrink-0 font-mono text-[7px] uppercase tracking-wide opacity-65">
                        {Math.round(relationship.confidence * 100)}%
                      </span>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[7px] opacity-48">
                      {counterpartDomain} · {relationship.relationshipKind.replaceAll("_", " ")}
                    </div>
                  </div>
                </div>
                <p className="mt-2 text-[8px] leading-relaxed opacity-72">{relationship.rationale}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  <span className="rounded-full border border-current/18 px-1.5 py-0.5 font-mono text-[7px] uppercase tracking-wide opacity-72">
                    {disposition(relationship.relationshipKind)}
                  </span>
                  <span className="rounded-full border border-current/14 px-1.5 py-0.5 font-mono text-[7px] opacity-55">
                    {relationship.reviewState}
                  </span>
                  {score !== null ? <span className="rounded-full border border-current/14 px-1.5 py-0.5 font-mono text-[7px] opacity-55">block {score}%</span> : null}
                  {sharedConcepts.length > 0 ? <span className="rounded-full border border-current/14 px-1.5 py-0.5 text-[7px] opacity-55">concepts {sharedConcepts.slice(0, 3).join(", ")}</span> : null}
                  {sharedSources.length > 0 ? <span className="rounded-full border border-current/14 px-1.5 py-0.5 text-[7px] opacity-55">{sharedSources.length} shared sources</span> : null}
                </div>
                {relationship.proposedFamilyName || relationship.proposedBusinessDomainName ? (
                  <div className="mt-2 border-t border-current/10 pt-1.5 font-mono text-[7px] opacity-55">
                    proposed · {relationship.proposedBusinessDomainName ?? counterpartDomain}
                    {relationship.proposedFamilyName ? ` / ${relationship.proposedFamilyName}` : ""}
                  </div>
                ) : null}
                {relationship.reviewState === "proposed" ? (
                  <div className="mt-2 flex items-center gap-1.5 border-t border-current/10 pt-2">
                    <button
                      type="button"
                      disabled={decidingRelationshipId !== null}
                      onClick={() => void decide(relationship, "accepted")}
                      className="inline-flex items-center gap-1 rounded border border-emerald-200/22 bg-emerald-300/[0.07] px-2 py-1 font-mono text-[7px] uppercase tracking-wide text-emerald-100/75 transition hover:bg-emerald-300/[0.12] disabled:cursor-wait disabled:opacity-45"
                    >
                      {decidingRelationshipId === relationship.relationshipId
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <CheckCircle2 className="h-3 w-3" />}
                      Accept classification
                    </button>
                    <button
                      type="button"
                      disabled={decidingRelationshipId !== null}
                      onClick={() => void decide(relationship, "rejected")}
                      className="inline-flex items-center gap-1 rounded border border-current/15 px-2 py-1 font-mono text-[7px] uppercase tracking-wide opacity-62 transition hover:opacity-90 disabled:cursor-wait disabled:opacity-35"
                    >
                      <X className="h-3 w-3" /> Reject
                    </button>
                  </div>
                ) : (
                  <div className="mt-2 border-t border-current/10 pt-1.5 font-mono text-[7px] opacity-58">
                    {relationship.reviewState} by {relationship.decidedBy ?? "operator"}
                    {relationship.decidedAt ? ` · ${new Date(relationship.decidedAt).toLocaleString()}` : ""}
                    {relationship.reviewNote ? ` · ${relationship.reviewNote}` : ""}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-chrome-border/50 px-3 py-5 text-center text-[9px] text-chrome-text/42">
          No cross-family candidate has been adjudicated for this semantic revision. Stable unrelated systems incur no model call.
        </div>
      )}
      {decisionMessage ? (
        <div className="mt-2 rounded-md border border-emerald-300/18 bg-emerald-400/[0.035] px-3 py-2 text-[8px] text-emerald-100/68">
          {decisionMessage} No source data or family identity was rewritten.
        </div>
      ) : null}
      {decisionError ? (
        <div className="mt-2 rounded-md border border-rose-300/18 bg-rose-400/[0.035] px-3 py-2 text-[8px] text-rose-100/68">
          {decisionError}
        </div>
      ) : null}
    </section>
  )
}

function watchEvidenceSummary(
  kind: "source_presence" | "join_coverage" | "metric_reconciliation",
  evidence: Record<string, unknown>,
): string {
  if (kind === "source_presence") {
    if (evidence.relation_exists === false) return "Relation is missing from the current catalog."
    if (evidence.relation_allowed === false) return "Relation exists but is excluded from topology discovery."
    return evidence.relation_exists === true ? "Relation exists and remains eligible for discovery." : "Awaiting a catalog observation."
  }
  if (kind === "join_coverage") {
    const rate = typeof evidence.left_match_rate === "number"
      ? `${Math.round(evidence.left_match_rate * 1000) / 10}% matched`
      : "match rate unavailable"
    const fanout = evidence.maximum_fanout == null ? "fanout unavailable" : `max fanout ${String(evidence.maximum_fanout)}`
    const duplicates = evidence.duplicate_right_key_groups == null
      ? "uniqueness unavailable"
      : `${String(evidence.duplicate_right_key_groups)} duplicate key groups`
    return `${rate} · ${fanout} · ${duplicates}`
  }
  if (evidence.projection_valid === false) return "The metric event projection is no longer executable."
  if (evidence.event_rows != null) {
    return `${Number(evidence.event_rows).toLocaleString()} sampled events · ${Number(evidence.valued_rows ?? 0).toLocaleString()} valued · dimensions ${evidence.dimensions_reconciled === false ? "do not reconcile" : "reconcile"}`
  }
  return "Awaiting a bounded metric execution."
}

function DossierStat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-md border border-chrome-border/50 bg-doc-bg/30 px-3 py-2.5">
      <div className="font-mono text-[7.5px] uppercase tracking-[0.16em] text-chrome-text/38">{label}</div>
      <div className="mt-1 truncate text-[13px] font-semibold text-foreground/82">{value}</div>
      <div className="mt-0.5 truncate text-[7.5px] text-chrome-text/38">{detail}</div>
    </div>
  )
}

function DiffColumn({ title, tone, items }: {
  title: string
  tone: "cyan" | "amber" | "rose"
  items: BusinessSemanticDiffItem[]
}) {
  const color = tone === "cyan" ? "text-cyan-200/65" : tone === "amber" ? "text-amber-200/65" : "text-rose-200/65"
  return (
    <div className="min-w-0 rounded-md border border-chrome-border/42 bg-doc-bg/24 p-2.5">
      <div className={cn("font-mono text-[7.5px] uppercase tracking-[0.16em]", color)}>{title} · {items.length}</div>
      <div className="mt-2 space-y-1.5">
        {items.slice(0, 8).map((item) => (
          <div key={item.assertion_key} className="min-w-0">
            <div className="truncate text-[8.5px] text-foreground/68" title={item.subject_key}>{item.subject_key}</div>
            <div className="truncate font-mono text-[7px] text-chrome-text/35">{item.kind.replaceAll("_", " ")} · {item.predicate.replaceAll("_", " ")}</div>
          </div>
        ))}
        {items.length > 8 ? <div className="font-mono text-[7px] text-chrome-text/30">+ {items.length - 8} more</div> : null}
        {items.length === 0 ? <div className="text-[8px] text-chrome-text/30">None</div> : null}
      </div>
    </div>
  )
}

function SemanticTopology({ graph }: { graph: BusinessValueGraph }) {
  const conceptByKey = new Map(graph.concepts.map((concept) => [concept.concept_key, concept]))
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <section className="rounded-lg border border-cyan-300/25 bg-[radial-gradient(circle_at_12%_10%,oklch(76%_0.14_190/0.12),transparent_46%)] p-4">
        <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-cyan-200/58">Business domain</div>
        <h4 className="mt-1 text-[16px] font-semibold">{graph.businessDomain?.name ?? graph.name}</h4>
        <p className="mt-1 max-w-3xl text-[10px] leading-relaxed text-chrome-text/55">
          {graph.businessDomain?.summary || graph.summary}
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {graph.concepts.map((concept) => (
            <div key={concept.concept_key} className="rounded-md border border-chrome-border/50 bg-doc-bg/45 px-2.5 py-1.5" title={concept.description}>
              <div className="text-[9.5px] font-medium">{concept.name}</div>
              <div className="font-mono text-[7.5px] uppercase tracking-wide text-chrome-text/38">{concept.kind}</div>
            </div>
          ))}
        </div>
      </section>

      {graph.processes.length > 0 ? (
        <section>
          <div className="mb-2 flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.17em] text-chrome-text/42"><FlowArrow className="h-3 w-3" /> Processes</div>
          <div className="grid gap-2 md:grid-cols-2">
            {graph.processes.map((process) => (
              <div key={process.process_key} className="rounded-md border border-chrome-border/45 bg-secondary-background/15 p-3">
                <div className="text-[10.5px] font-medium">{process.name}</div>
                <p className="mt-1 text-[8.5px] leading-relaxed text-chrome-text/48">{process.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {process.concept_keys.map((key) => <span key={key} className="rounded-full border border-cyan-300/18 px-1.5 py-0.5 text-[7.5px] text-cyan-100/58">{conceptByKey.get(key)?.name ?? key}</span>)}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section>
        <div className="mb-2 flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.17em] text-chrome-text/42"><Database className="h-3 w-3" /> Source memberships</div>
        <div className="space-y-1.5">
          {graph.sourceMemberships.map((membership) => (
            <div key={`${membership.relation}:${membership.role}`} className="grid grid-cols-[minmax(150px,0.8fr)_90px_minmax(180px,1.2fr)] items-center gap-3 rounded-md border border-chrome-border/45 bg-doc-bg/28 px-3 py-2">
              <div className="truncate font-mono text-[8.5px] text-foreground/72" title={membership.relation}>{membership.relation}</div>
              <span className="w-fit rounded-full border border-cyan-300/20 bg-cyan-400/6 px-1.5 py-0.5 font-mono text-[7px] text-cyan-100/62">{membership.role.replaceAll("_", " ")}</span>
              <div className="min-w-0">
                <div className="truncate text-[8.5px] text-chrome-text/52">{membership.concept_keys.map((key) => conceptByKey.get(key)?.name ?? key).join(" · ")}</div>
                <div className="truncate text-[7.5px] text-chrome-text/32" title={membership.rationale}>{membership.rationale}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {graph.semanticRelationships.length > 0 ? (
        <section>
          <div className="mb-2 flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.17em] text-chrome-text/42"><GitBranch className="h-3 w-3" /> Relationships</div>
          <div className="space-y-1.5">
            {graph.semanticRelationships.map((relationship, index) => {
              const left = relationship.left_relation
                ?? conceptByKey.get(relationship.subject_concept_key ?? "")?.name
                ?? relationship.subject_concept_key
              const right = relationship.right_relation
                ?? conceptByKey.get(relationship.object_concept_key ?? "")?.name
                ?? relationship.object_concept_key
              const exactJoin = relationship.relationship === "joins_to"
              return (
                <div key={`${relationship.relationship}:${left}:${right}:${index}`} className="rounded-md border border-chrome-border/45 bg-secondary-background/12 px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2 text-[8.5px]">
                    <span className="min-w-0 truncate text-chrome-text/62">{left}</span>
                    <span className={cn("shrink-0 rounded-full border px-2 py-0.5 font-mono text-[7px]", exactJoin ? "border-emerald-300/25 text-emerald-200/72" : "border-amber-300/22 text-amber-200/65")}>{relationship.relationship.replaceAll("_", " ")}</span>
                    <span className="min-w-0 truncate text-chrome-text/62">{right}</span>
                  </div>
                  <div className="mt-1 text-[7.5px] leading-relaxed text-chrome-text/38">
                    {exactJoin && relationship.left_columns.length > 0 ? `${relationship.left_columns.join(" + ")} ↔ ${relationship.right_columns.join(" + ")} · ` : ""}{relationship.rationale}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        <SemanticList title="Questions this enables" values={graph.analyticalQuestions} tone="cyan" />
        <SemanticList title="Known gaps and limits" values={graph.gaps} tone="amber" />
      </div>
      {graph.promotion ? (
        <div className="rounded-md border border-emerald-300/22 bg-emerald-400/[0.04] px-3 py-2 text-[9px] text-emerald-100/68">
          <b>cubes.{graph.promotion.cubeName}</b> is a derived materialization of this Value System—not another business domain.
        </div>
      ) : null}
    </div>
  )
}

function SemanticList({ title, values, tone }: { title: string; values: string[]; tone: "cyan" | "amber" }) {
  if (values.length === 0) return null
  return (
    <section className={cn("rounded-md border p-3", tone === "cyan" ? "border-cyan-300/18 bg-cyan-400/[0.035]" : "border-amber-300/18 bg-amber-400/[0.035]")}>
      <div className={cn("font-mono text-[8px] uppercase tracking-[0.16em]", tone === "cyan" ? "text-cyan-200/58" : "text-amber-200/58")}>{title}</div>
      <ul className="mt-2 space-y-1 text-[8.5px] leading-relaxed text-chrome-text/52">
        {values.map((value) => <li key={value}>• {value}</li>)}
      </ul>
    </section>
  )
}

function ValueRiver({
  metric,
  evaluation,
  dimensionName,
  items,
  census,
  mode,
}: {
  metric: BusinessValueMetric
  evaluation: BusinessValueMetricEvaluation
  dimensionName: string
  items: BusinessValueChangeItem[]
  census: BusinessSemanticDimensionCensus | null
  mode: "change" | "contribution"
}) {
  const prior = evaluation.prior_period
  const current = evaluation.current_period
  const totalChange = evaluation.total_change ?? 0
  const contributionMembers = census?.members ?? []
  const visibleItems = mode === "change" ? items : contributionMembers
  const scale = Math.max(
    ...visibleItems.map((item) => Math.abs(mode === "change"
      ? (item as BusinessValueChangeItem).change
      : Number((item as BusinessSemanticDimensionCensus["members"][number]).metric_value))),
    1,
  )
  const contributionTotal = census?.totalMetricValue ?? evaluation.total_value
  const contributionIsExact = census?.coverageComplete === true && census.memberListComplete === true

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-3 flex items-end justify-between gap-4">
        <div>
          <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-chrome-text/40">
            {mode === "change" ? "What moved the outcome" : "What makes up the outcome"}
          </div>
          <div className="mt-1 text-[11px] text-chrome-text/55">
            {dimensionName} · {mode === "change"
              ? `${periodLabel(current?.period_start, evaluation.bucket)} period-local movement`
              : census ? "All observed history" : "Census not run yet"}
          </div>
        </div>
        {mode === "change" && prior && current ? (
          <div className="flex items-baseline gap-2 text-right">
            <span className="font-mono text-[9px] text-chrome-text/42">{periodLabel(prior.period_start, evaluation.bucket)}</span>
            <span className="text-[11px] text-chrome-text/55">→</span>
            <span className="font-mono text-[9px] text-chrome-text/65">{periodLabel(current.period_start, evaluation.bucket)}</span>
          </div>
        ) : mode === "contribution" && census ? (
          <div className={cn(
            "rounded-full border px-2 py-0.5 font-mono text-[8px] uppercase tracking-wide",
            contributionIsExact
              ? "border-emerald-400/25 bg-emerald-400/7 text-emerald-200/75"
              : "border-amber-400/25 bg-amber-400/7 text-amber-200/75",
          )}>
            {contributionIsExact ? "exact dictionary" : "bounded sample"}
          </div>
        ) : null}
      </div>

      <div className="relative grid grid-cols-[minmax(170px,0.72fr)_minmax(260px,1.35fr)] gap-10 rounded-lg border border-chrome-border/45 bg-secondary-background/[0.13] p-4 before:absolute before:bottom-4 before:left-[calc(41%-1px)] before:top-4 before:w-px before:bg-gradient-to-b before:from-transparent before:via-cyan-300/30 before:to-transparent">
        <div className="flex min-h-[340px] items-center">
          <div className="w-full rounded-lg border border-cyan-300/35 bg-[radial-gradient(circle_at_20%_15%,oklch(76%_0.14_190/0.16),transparent_58%)] p-4 shadow-[0_18px_55px_rgba(0,0,0,0.16)]">
            <div className="flex items-center gap-2 font-mono text-[8px] uppercase tracking-[0.17em] text-cyan-200/65">
              <DollarSign className="h-3.5 w-3.5" /> outcome
            </div>
            <h4 className="mt-3 text-[15px] font-semibold leading-tight">{metric.name}</h4>
            <div className="mt-3 font-mono text-[25px] tracking-tight text-foreground">
              {formatBusinessValue(
                mode === "contribution" ? contributionTotal : current?.value ?? evaluation.total_value,
                metric.unit,
                true,
              )}
            </div>
            {mode === "change" && prior ? (
              <div className={cn(
                "mt-2 inline-flex rounded-full border px-2 py-0.5 font-mono text-[9px]",
                totalChange >= 0
                  ? "border-emerald-400/25 bg-emerald-400/7 text-emerald-200"
                  : "border-rose-400/25 bg-rose-400/7 text-rose-200",
              )}>
                {totalChange >= 0 ? "+" : ""}{formatBusinessValue(totalChange, metric.unit, true)} change
              </div>
            ) : null}
            <p className="mt-3 text-[9px] leading-relaxed text-chrome-text/52">{metric.description}</p>
            {mode === "contribution" && census ? (
              <div className="mt-3 border-t border-chrome-border/35 pt-2 font-mono text-[7.5px] leading-relaxed text-chrome-text/38">
                {census.rowsObserved.toLocaleString()} rows observed · {census.memberCount.toLocaleString()} members
                {!contributionIsExact ? ` · capped at ${census.rowLimit.toLocaleString()} rows` : ""}
              </div>
            ) : null}
          </div>
        </div>

        <div className="space-y-2.5">
          {visibleItems.length === 0 ? (
            <div className="grid min-h-[340px] place-items-center text-center text-[10px] text-chrome-text/42">
              {mode === "change"
                ? "No two-period dimensional change is available yet."
                : "Run dossier checks to build an all-time dimension census."}
            </div>
          ) : visibleItems.map((item) => {
            const changeItem = item as BusinessValueChangeItem
            const member = item as BusinessSemanticDimensionCensus["members"][number]
            const itemKey = mode === "change" ? changeItem.dimension_value : member.value
            const amount = mode === "change" ? changeItem.change : Number(member.metric_value)
            const positive = amount >= 0
            const width = Math.max(8, Math.abs(amount) / scale * 100)
            return (
              <div key={itemKey} className="group relative rounded-md border border-chrome-border/45 bg-doc-bg/28 px-3 py-2 before:absolute before:-left-10 before:top-1/2 before:h-px before:w-10 before:bg-chrome-border/55">
                <div className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate text-[10px] font-medium" title={itemKey}>{itemKey}</span>
                  <span className={cn("shrink-0 font-mono text-[9px]", positive ? "text-emerald-200/80" : "text-rose-200/80")}>
                    {mode === "change" && positive ? "+" : ""}{formatBusinessValue(amount, metric.unit, true)}
                  </span>
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-foreground/[0.055]">
                  <div
                    className={cn("h-full rounded-full", positive ? "bg-emerald-400/60" : "bg-rose-400/60")}
                    style={{ width: `${width}%` }}
                  />
                </div>
                {mode === "change" ? (
                  <div className="mt-1 flex justify-between font-mono text-[7.5px] text-chrome-text/35">
                    <span>{formatBusinessValue(changeItem.prior_value, metric.unit, true)}</span>
                    <span>→ {formatBusinessValue(changeItem.current_value, metric.unit, true)}</span>
                  </div>
                ) : (
                  <div className="mt-1 flex justify-between gap-3 font-mono text-[7.5px] text-chrome-text/35">
                    <span>{member.row_count.toLocaleString()} rows · {(member.share_of_rows * 100).toFixed(member.share_of_rows < 0.01 ? 2 : 1)}%</span>
                    <span>{member.first_seen && member.last_seen
                      ? `${periodLabel(member.first_seen, "year")}–${periodLabel(member.last_seen, "year")}`
                      : "undated"}</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function ValueDefinition({
  graph,
  metric,
  evaluation,
  onMetric,
}: {
  graph: BusinessValueGraph
  metric: BusinessValueMetric
  evaluation: BusinessValueMetricEvaluation
  onMetric: (metricKey: string) => void
}) {
  const childEdges = graph.edges.filter((edge) => edge.parent_metric_key === metric.metric_key)
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="rounded-lg border border-cyan-300/30 bg-cyan-400/[0.055] p-4">
        <div className="flex items-start gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-cyan-300/30 bg-cyan-400/8"><Sigma className="h-4 w-4 text-cyan-200" /></div>
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[8px] uppercase tracking-[0.17em] text-cyan-200/58">Executable definition</div>
            <h4 className="mt-1 text-[15px] font-semibold">{metric.name}</h4>
            <p className="mt-1 text-[10px] leading-relaxed text-chrome-text/58">{metric.description}</p>
            <div className="mt-2 inline-flex rounded-sm border border-cyan-300/20 bg-doc-bg/35 px-2 py-1 font-mono text-[9px] text-cyan-100/72">
              {metric.formula}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-[20px]">{formatBusinessValue(evaluation.current_period?.value ?? evaluation.total_value, metric.unit, true)}</div>
            <div className="font-mono text-[8px] text-chrome-text/38">latest {evaluation.bucket}</div>
          </div>
        </div>
      </div>
      {childEdges.length > 0 ? (
        <div className="space-y-2">
          {childEdges.map((edge) => {
            const child = graph.metrics.find((candidate) => candidate.metric_key === edge.child_metric_key)
            if (!child) return null
            return (
              <button key={`${edge.relationship}:${edge.child_metric_key}`} type="button" onClick={() => onMetric(child.metric_key)} className="flex w-full items-center gap-3 rounded-md border border-chrome-border/45 bg-secondary-background/15 p-3 text-left hover:border-cyan-300/25">
                <span className="rounded-full border border-chrome-border/50 px-2 py-0.5 font-mono text-[8px] text-chrome-text/52">{edge.relationship}</span>
                <span className="text-[11px] font-medium">{child.name}</span>
                <span className="ml-auto text-[9px] text-chrome-text/42">{edge.rationale}</span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-chrome-border/50 p-4 text-[10px] leading-relaxed text-chrome-text/48">This is currently an atomic outcome definition. Contribution and change views still decompose it through its business dimensions.</div>
      )}
    </div>
  )
}

function ValueInspector({
  connectionId,
  graph,
  metric,
  evaluation,
  onChanged,
}: {
  connectionId: string
  graph: BusinessValueGraph
  metric: BusinessValueMetric | null
  evaluation: BusinessValueMetricEvaluation | null
  onChanged: () => Promise<void>
}) {
  const defaultCubeName = `${graph.domainKey.toLowerCase().replaceAll(/[^a-z0-9]+/g, "_").replaceAll(/^_+|_+$/g, "") || "value"}_value`
    .slice(0, 63)
    .replace(/_+$/, "")
  const defaultGrain = evaluation?.bucket === "day" || evaluation?.bucket === "week"
    ? evaluation.bucket
    : "month"
  const [cubeName, setCubeName] = useState(defaultCubeName)
  const [grain, setGrain] = useState(defaultGrain)
  const [promoting, setPromoting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [promotionError, setPromotionError] = useState<string | null>(null)

  useEffect(() => {
    setCubeName(defaultCubeName)
    setGrain(defaultGrain)
    setPromotionError(null)
  }, [defaultCubeName, defaultGrain, graph.graphId])

  const promote = useCallback(async () => {
    if (promoting || graph.promotion) return
    setPromoting(true)
    setPromotionError(null)
    try {
      const result = await promoteBusinessValueGraph(connectionId, graph.graphId, {
        cubeName,
        grain,
        reviewReason: "Reviewed and promoted from the Business Value Graph inspector.",
      })
      if (!result.ok) {
        setPromotionError(result.error)
        return
      }
      await onChanged()
    } finally {
      setPromoting(false)
    }
  }, [connectionId, cubeName, grain, graph.graphId, graph.promotion, onChanged, promoting])

  const refreshPromotion = useCallback(async () => {
    if (refreshing || !graph.promotion) return
    setRefreshing(true)
    setPromotionError(null)
    try {
      const result = await refreshBusinessValuePromotion(connectionId, graph.graphId)
      if (!result.ok) {
        setPromotionError(result.error)
        return
      }
      await onChanged()
    } finally {
      setRefreshing(false)
    }
  }, [connectionId, graph.graphId, graph.promotion, onChanged, refreshing])

  return (
    <aside className="min-h-0 overflow-y-auto bg-secondary-background/10 p-3 max-[980px]:hidden">
      <div className="font-mono text-[8px] uppercase tracking-[0.18em] text-chrome-text/42">Meaning & proof</div>
      {metric && evaluation ? (
        <div className="mt-3 space-y-3">
          <div>
            <div className="text-[12px] font-semibold">{metric.name}</div>
            <div className="mt-1 text-[9px] leading-relaxed text-chrome-text/55">{metric.description}</div>
            <div className="mt-2 rounded-sm border border-cyan-300/18 bg-cyan-400/[0.035] px-2 py-1.5 font-mono text-[9px] leading-relaxed text-cyan-100/65">{metric.formula}</div>
          </div>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-chrome-border/45 bg-chrome-border/45">
            <ValueFact label="grain" value={metric.grain} />
            <ValueFact label="time means" value={metric.time_semantics} />
            <ValueFact label="events checked" value={evaluation.event_rows.toLocaleString()} />
            <ValueFact label="confidence" value={`${Math.round(metric.confidence * 100)}%`} />
          </div>
          <div className="rounded-md border border-emerald-400/20 bg-emerald-400/[0.045] p-2.5">
            <div className="flex items-center gap-1.5 text-[9px] font-medium text-emerald-200/80"><CheckCircle2 className="h-3 w-3" /> Reconciled contribution paths</div>
            <div className="mt-1 text-[8px] leading-relaxed text-chrome-text/45">Every full dimensional split was checked against its parent period total before this proposal was shown.</div>
          </div>
          <div>
            <div className="flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-wider text-chrome-text/42"><Box className="h-3 w-3" /> Cube recommendation</div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              <span className="rounded-sm border border-cyan-300/20 bg-cyan-400/6 px-1.5 py-0.5 font-mono text-[8px] text-cyan-100/70">{evaluation.cube_recommendation.time_grain} time</span>
              {evaluation.cube_recommendation.dimensions.map((dimension) => (
                <span key={dimension} className="rounded-sm border border-chrome-border/45 px-1.5 py-0.5 font-mono text-[8px] text-chrome-text/55">{dimension}</span>
              ))}
            </div>
          </div>
          <ValuePromotionPanel
            graph={graph}
            cubeName={cubeName}
            grain={grain}
            promoting={promoting}
            refreshing={refreshing}
            error={promotionError}
            onCubeName={setCubeName}
            onGrain={setGrain}
            onPromote={() => void promote()}
            onRefresh={() => void refreshPromotion()}
          />
          <details className="rounded-md border border-chrome-border/45 bg-doc-bg/25">
            <summary className="cursor-pointer px-2.5 py-2 font-mono text-[8px] uppercase tracking-wider text-chrome-text/48">Canonical event SQL</summary>
            <pre className="max-h-64 overflow-auto border-t border-chrome-border/40 p-2.5 font-mono text-[8px] leading-relaxed text-chrome-text/58">{metric.event_sql}</pre>
          </details>
          {graph.openQuestions.length > 0 ? (
            <div className="rounded-md border border-amber-400/20 bg-amber-400/[0.04] p-2.5">
              <div className="font-mono text-[8px] uppercase tracking-wider text-amber-200/65">Needs business confirmation</div>
              <ul className="mt-1.5 space-y-1 text-[8.5px] leading-relaxed text-chrome-text/50">
                {graph.openQuestions.map((question) => <li key={question}>• {question}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 text-[10px] leading-relaxed text-chrome-text/45">Select a validated metric to inspect its meaning, grain, SQL, and cube recommendation.</div>
      )}
    </aside>
  )
}

function ValuePromotionPanel({
  graph,
  cubeName,
  grain,
  promoting,
  refreshing,
  error,
  onCubeName,
  onGrain,
  onPromote,
  onRefresh,
}: {
  graph: BusinessValueGraph
  cubeName: string
  grain: string
  promoting: boolean
  refreshing: boolean
  error: string | null
  onCubeName: (value: string) => void
  onGrain: (value: string) => void
  onPromote: () => void
  onRefresh: () => void
}) {
  const promotion = graph.promotion
  const originalRows = Number(promotion?.receipt.original_event_rows ?? 0)
  const aggregateRows = Number(promotion?.receipt.aggregate_rows ?? 0)
  const reduction = originalRows > 0 && aggregateRows > 0
    ? Math.max(1, Math.round(originalRows / aggregateRows))
    : null
  const promotable = graph.reconciled && !graph.fallback && graph.metrics.length > 0

  if (promotion) {
    return (
      <section className="overflow-hidden rounded-md border border-emerald-300/25 bg-emerald-400/[0.045]">
        <div className="flex items-center gap-2 border-b border-emerald-300/15 px-3 py-2.5">
          <div className="grid h-7 w-7 place-items-center rounded-full border border-emerald-300/25 bg-emerald-300/8">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-200" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[10px] font-semibold text-emerald-100/90">Governed value system</div>
            <div className="truncate font-mono text-[9px] text-emerald-100/52">cubes.{promotion.cubeName} · v{promotion.cubeVersion}</div>
          </div>
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-1 px-3 py-3 text-center">
          <PromotionFact value={originalRows ? originalRows.toLocaleString() : "source"} label="events" />
          <span className="text-cyan-200/45">→</span>
          <PromotionFact value={aggregateRows ? aggregateRows.toLocaleString() : "compact"} label={`${promotion.materializationGrain} rows`} />
          <span className="text-cyan-200/45">→</span>
          <PromotionFact value={String(Object.keys(promotion.metricVersions).length)} label="metrics" />
        </div>
        <div className="flex items-center gap-2 border-t border-emerald-300/15 px-3 py-2">
          <span className="min-w-0 flex-1 text-[9px] leading-snug text-chrome-text/52">
            {reduction ? `${reduction.toLocaleString()}× fewer rows for change analysis` : "Compact aggregate is active"} · source SQL and reconciliation retained
          </span>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-sm border border-emerald-300/25 bg-emerald-300/7 px-2 text-[9px] font-medium text-emerald-100/80 hover:bg-emerald-300/12 disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />
            {refreshing ? "Refreshing" : "Refresh cube"}
          </button>
        </div>
        {error ? <div className="border-t border-danger/20 px-3 py-2 font-mono text-[9px] leading-relaxed text-danger/80">{error}</div> : null}
      </section>
    )
  }

  return (
    <section className="overflow-hidden rounded-md border border-cyan-300/25 bg-[linear-gradient(135deg,oklch(76%_0.14_190/0.07),transparent_62%)]">
      <div className="flex items-start gap-2.5 px-3 pt-3">
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-cyan-300/25 bg-cyan-300/7">
          <Database className="h-3.5 w-3.5 text-cyan-200" />
        </div>
        <div>
          <div className="text-[10.5px] font-semibold">Make this reusable company logic</div>
          <p className="mt-0.5 text-[9px] leading-relaxed text-chrome-text/52">Materialize the proven event logic once, then publish its metrics into Calliope’s governed catalog.</p>
        </div>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_88px] gap-2 px-3 py-3">
        <label className="min-w-0">
          <span className="font-mono text-[8px] uppercase tracking-wider text-chrome-text/42">Cube name</span>
          <input
            value={cubeName}
            onChange={(event) => onCubeName(event.target.value.toLowerCase().replaceAll(/[^a-z0-9_]/g, "_").slice(0, 63))}
            className="mt-1 h-8 w-full rounded-sm border border-chrome-border/60 bg-doc-bg/55 px-2 font-mono text-[10px] text-foreground outline-none focus:border-cyan-300/45"
          />
        </label>
        <label>
          <span className="font-mono text-[8px] uppercase tracking-wider text-chrome-text/42">Time grain</span>
          <select
            value={grain}
            onChange={(event) => onGrain(event.target.value)}
            className="mt-1 h-8 w-full rounded-sm border border-chrome-border/60 bg-doc-bg/55 px-1.5 font-mono text-[10px] text-foreground outline-none focus:border-cyan-300/45"
          >
            {(["day", "week", "month", "quarter", "year"] as const).map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-2 border-t border-cyan-300/15 px-3 py-2.5">
        <div className="min-w-0 flex-1 font-mono text-[8px] leading-relaxed text-chrome-text/45">
          {promotable
            ? `${graph.metrics.length} metrics · ${graph.sourceRelations.length} source relations · exact reconciliation passed`
            : graph.fallback
              ? "Fallback models must be replaced by an evidence-backed proposal first."
              : "Every metric and dimension path must reconcile before promotion."}
        </div>
        <button
          type="button"
          onClick={onPromote}
          disabled={!promotable || promoting || !cubeName}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-sm border border-cyan-300/35 bg-cyan-300/12 px-2.5 text-[9.5px] font-semibold text-cyan-100 transition hover:bg-cyan-300/18 disabled:cursor-default disabled:opacity-40"
        >
          {promoting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
          {promoting ? "Building cube…" : "Promote to RVBBIT"}
        </button>
      </div>
      {error ? <div className="border-t border-danger/20 px-3 py-2 font-mono text-[9px] leading-relaxed text-danger/80">{error}</div> : null}
    </section>
  )
}

function PromotionFact({ value, label }: { value: string; label: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate font-mono text-[12px] text-foreground/85">{value}</div>
      <div className="mt-0.5 font-mono text-[7.5px] uppercase tracking-wider text-chrome-text/38">{label}</div>
    </div>
  )
}

function ValueFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-doc-bg/50 p-2">
      <div className="font-mono text-[7px] uppercase tracking-wider text-chrome-text/35">{label}</div>
      <div className="mt-1 text-[8.5px] leading-snug text-chrome-text/62">{value}</div>
    </div>
  )
}

const WORKFLOW_PHASE_PROGRESS: Record<string, number> = {
  queued: 1,
  claimed: 3,
  resuming: 4,
  profiling: 12,
  embedding_sources: 24,
  partitioning: 32,
  embedding_populations: 44,
  evidence: 56,
  planning: 66,
  executing: 70,
  staging: 92,
  value_modeling: 96,
  completed: 100,
  failed: 100,
  cancelled: 100,
  cancelling: 100,
}

function workflowProgress(run: TopologyWorkflowRun): number {
  if (run.phase === "executing" && run.workItemCount > 0) {
    return Math.min(96, 70 + (run.completedWorkItems / run.workItemCount) * 26)
  }
  return WORKFLOW_PHASE_PROGRESS[run.phase] ?? (run.status === "running" ? 8 : 100)
}

function workflowPhaseLabel(value: string): string {
  return value.replaceAll("_", " ")
}

function WorkflowStatusBar({
  workflow,
  run,
  currentTopology,
}: {
  workflow: TopologyWorkflowState
  run: TopologyWorkflowRun
  currentTopology: boolean
}) {
  const active = ["queued", "running", "cancel_requested"].includes(run.status)
  const failed = run.status === "failed"
  const complete = run.status === "completed"
  const progress = workflowProgress(run)
  const currentStep = typeof run.progress.current_step === "string"
    ? run.progress.current_step
    : null
  const valueDomainCount = Number(run.progress.value_domain_count ?? 0)
  const valueGraphsReady = Number(run.progress.value_graphs_ready ?? 0)
  const semanticCacheHits = Number(
    run.progress.semantic_cache_hits ?? run.result?.semantic_cache_hits ?? 0,
  )
  const crossSystemCandidates = Number(
    run.progress.cross_system_candidate_count
      ?? run.result?.cross_system_candidate_count
      ?? 0,
  )
  const familyCandidates = Number(
    run.progress.family_consolidation_candidates
      ?? run.result?.family_consolidation_candidates
      ?? 0,
  )
  const familyProposals = Number(
    run.progress.family_consolidation_proposals
      ?? run.result?.family_consolidation_proposals
      ?? 0,
  )
  const familyState = typeof run.progress.family_consolidation_state === "string"
    ? run.progress.family_consolidation_state
    : null
  const familyPacketIndex = Number(run.progress.family_consolidation_index ?? 0)
  const familyPacketCount = Number(run.progress.family_consolidation_packet_count ?? 0)
  const familyRetryIndex = Number(run.progress.family_consolidation_retry_index ?? 0)
  const familyRetryPackets = Number(
    run.progress.family_consolidation_retry_packets
      ?? run.result?.family_consolidation_retry_packets
      ?? 0,
  )
  const familyRetryCompleted = Number(
    run.progress.family_consolidation_retry_completed
      ?? run.result?.family_consolidation_retry_completed
      ?? 0,
  )
  const familyError = typeof run.progress.family_consolidation_error === "string"
    ? run.progress.family_consolidation_error
    : null
  const valueDomainError = typeof run.progress.current_value_domain_error === "string"
    ? run.progress.current_value_domain_error
    : null
  const planningError = typeof run.progress.value_domain_planning_error === "string"
    ? run.progress.value_domain_planning_error
    : null
  const familyErrorRows = run.progress.family_consolidation_errors
    ?? run.result?.family_consolidation_errors
  const familyErrors = Array.isArray(familyErrorRows) ? familyErrorRows.length : 0
  const valueErrors = Array.isArray(run.progress.value_graph_errors)
    ? run.progress.value_graph_errors.length
    : 0
  const showingPreviousResult = active
    && workflow.displayRunId !== null
    && workflow.displayRunId !== run.runId
  const familyDetail = familyState && familyPacketCount > 0
    ? `${currentStep ?? "Comparing Value System families"} · ${familyState.replaceAll("_", " ")} · ${familyState.startsWith("retry_")
      ? `retry ${familyRetryIndex}/${familyRetryPackets}`
      : `packet ${familyPacketIndex}/${familyPacketCount}`}${familyError ? ` · ${familyError}` : ""}`
    : null
  const valueDetail = valueDomainError && run.progress.current_value_domain
    ? `${currentStep ?? "Explaining Value System"} · ${String(run.progress.current_value_domain)} · ${valueDomainError}`
    : null
  const planningDetail = planningError
    ? `${currentStep ?? "Reconciling source findings"} · ${planningError}`
    : null
  const activeDetail = familyDetail
    ?? valueDetail
    ?? planningDetail
    ?? currentStep
    ?? (failed
      ? run.error ?? "Excavation failed"
      : `${run.relationCount} relations · ${run.populationCount || "…"} populations`)
  const statusColor = failed
    ? "text-danger"
    : complete
      ? "text-emerald-300"
      : run.status === "cancelled"
        ? "text-chrome-text/55"
        : "text-cyan-200"

  return (
    <section className="shrink-0 border-b border-chrome-border/55 bg-secondary-background/22 px-3 py-2">
      <div className="flex min-w-0 items-center gap-2.5">
        <div className={cn(
          "grid h-7 w-7 shrink-0 place-items-center rounded-full border",
          failed
            ? "border-danger/35 bg-danger/8 text-danger"
            : complete
              ? "border-emerald-400/35 bg-emerald-400/8 text-emerald-300"
              : "border-cyan-400/30 bg-cyan-400/8 text-cyan-200",
        )}>
          {failed ? (
            <AlertTriangle className="h-3.5 w-3.5" />
          ) : complete ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : active ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Clock className="h-3.5 w-3.5" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline gap-2">
            <span className={cn("truncate text-[10.5px] font-medium", statusColor)}>
              {run.runName}
            </span>
            <span className="shrink-0 font-mono text-[8px] uppercase tracking-[0.14em] text-chrome-text/45">
              {workflowPhaseLabel(run.phase)}
            </span>
            {run.attempts > 1 ? (
              <span className="shrink-0 font-mono text-[8px] text-amber-300/65">
                resume {run.attempts}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 truncate font-mono text-[8.5px] text-chrome-text/52">
            {complete
              ? `${run.bundlesStaged} object bundles · ${valueGraphsReady}/${valueDomainCount} value systems · ${crossSystemCandidates} weak cross-system links · ${familyProposals}/${familyCandidates} family decisions${familyRetryPackets ? ` · ${familyRetryCompleted}/${familyRetryPackets} packet retries` : ""}${valueErrors || familyErrors ? ` · ${valueErrors + familyErrors} failed` : ""}`
              : `${activeDetail}${currentTopology
                ? " · current topology remains usable while this runs"
                : showingPreviousResult ? " · inspecting last completed run" : ""}`}
          </div>
        </div>
        <div className="hidden shrink-0 items-center gap-3 font-mono text-[8.5px] text-chrome-text/48 sm:flex">
          <span>{run.relationCount} sources</span>
          {run.excavationUnitCount > 0 ? <span>{run.excavationUnitCount} areas</span> : null}
          {run.workItemCount > 0 ? (
            <span>{run.completedWorkItems}/{run.workItemCount} work</span>
          ) : null}
          {run.llmAttempts > 0 ? <span>{run.llmAttempts} model calls</span> : null}
          {semanticCacheHits > 0 ? <span className="text-cyan-200/60">{semanticCacheHits} cached</span> : null}
          {crossSystemCandidates > 0 ? <span className="text-amber-200/65">{crossSystemCandidates} weak links</span> : null}
          {familyCandidates > 0 ? <span className="text-violet-200/60">{familyProposals}/{familyCandidates} family</span> : null}
          <span className={workflow.workerOnline ? "text-emerald-300/65" : "text-amber-300/75"}>
            {workflow.workerOnline ? "worker online" : "worker offline"}
          </span>
        </div>
      </div>
      <div className="mt-1.5 h-0.5 overflow-hidden rounded-full bg-foreground/[0.06]">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500",
            failed
              ? "bg-danger/75"
              : complete
                ? "bg-emerald-400/75"
                : "bg-gradient-to-r from-cyan-500/70 to-cyan-300/90",
          )}
          style={{ width: `${Math.max(progress, active ? 1 : 0)}%` }}
        />
      </div>
    </section>
  )
}

function WorkflowWorkerBar({ workflow }: { workflow: TopologyWorkflowState }) {
  const online = workflow.workers.filter((worker) => worker.online)
  const stale = workflow.workers.filter((worker) => !worker.online)
  return (
    <section className="shrink-0 border-b border-chrome-border/45 bg-doc-bg/45 px-3 py-1.5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[8.5px] text-chrome-text/55">
        <span className="uppercase tracking-[0.14em] text-chrome-text/38">workers</span>
        {online.length > 0 ? online.map((worker) => (
          <span key={worker.workerId} className="inline-flex min-w-0 items-center gap-1.5">
            <i className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              worker.status === "busy" ? "bg-cyan-300" : "bg-emerald-300",
            )} />
            <span className="max-w-52 truncate text-chrome-text/70">
              {worker.workerId.split(":").slice(-2).join(":")}
            </span>
            <span>{worker.status}</span>
            {worker.scopeSchema ? <span className="text-cyan-200/70">{worker.scopeSchema}</span> : null}
          </span>
        )) : <span className="text-amber-300/75">none online</span>}
        {workflow.queuedRuns.length > 0 ? (
          <span className="text-amber-200/75">
            queue {workflow.queuedRuns.map((run) => run.scopeSchema ?? run.runName).join(" → ")}
          </span>
        ) : <span>queue empty</span>}
        {stale.length > 0 ? <span>{stale.length} stale registration{stale.length === 1 ? "" : "s"}</span> : null}
      </div>
    </section>
  )
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="grid h-full place-items-center bg-doc-bg text-chrome-text/50">
      <div className="text-center">
        <Loader2 className="mx-auto h-5 w-5 animate-spin text-cyan-300" />
        <div className="mt-2 font-mono text-[10px] uppercase tracking-wider">{label}</div>
      </div>
    </div>
  )
}

function EmptyState({ title, body, error }: { title: string; body: string; error?: string }) {
  return (
    <div className="grid h-full flex-1 place-items-center bg-doc-bg p-8 text-center">
      <div className="max-w-md">
        <TreeStructure className="mx-auto h-8 w-8 text-chrome-text/30" />
        <h3 className="mt-3 text-sm font-medium">{title}</h3>
        <p className="mt-1.5 text-[11px] leading-relaxed text-chrome-text/55">{body}</p>
        {error ? <p className="mt-3 font-mono text-[9px] text-danger/70">{error}</p> : null}
      </div>
    </div>
  )
}

function BundleRail({
  bundles,
  allCount,
  selectedId,
  query,
  filter,
  onQuery,
  onFilter,
  onSelect,
}: {
  bundles: TopologyBundle[]
  allCount: number
  selectedId: string | null
  query: string
  filter: BundleFilter
  onQuery: (value: string) => void
  onFilter: (value: BundleFilter) => void
  onSelect: (bundleId: string) => void
}) {
  return (
    <aside className="flex min-h-0 flex-col border-r border-chrome-border/55 bg-secondary-background/18">
      <div className="shrink-0 space-y-2 border-b border-chrome-border/45 p-2.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-chrome-text/35" />
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Find a tree, source, or object"
            className="h-7 w-full rounded-sm border border-chrome-border/55 bg-doc-bg/45 pl-7 pr-2 font-mono text-[9px] outline-none placeholder:text-chrome-text/30 focus:border-cyan-400/40"
          />
        </div>
        <div className="flex gap-1 overflow-x-auto">
          {(["open", "all", "needs_revision", "rejected"] as BundleFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => onFilter(value)}
              className={cn(
                "whitespace-nowrap rounded-sm px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wide",
                filter === value
                  ? "bg-cyan-400/12 text-cyan-200"
                  : "text-chrome-text/45 hover:bg-foreground/[0.05] hover:text-foreground",
              )}
            >
              {value.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {bundles.length === 0 ? (
          <div className="p-4 text-center text-[10px] text-chrome-text/40">No matching bundles out of {allCount}.</div>
        ) : bundles.map((bundle) => (
          <button
            key={bundle.bundleId}
            type="button"
            onClick={() => onSelect(bundle.bundleId)}
            className={cn(
              "w-full border-l-2 px-2.5 py-2 text-left transition",
              selectedId === bundle.bundleId
                ? "border-l-cyan-300 bg-cyan-400/8"
                : "border-l-transparent hover:bg-foreground/[0.035]",
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", statusTone(bundle.status))} />
              <span
                className="min-w-0 flex-1 truncate text-[11px] font-medium"
                title={`${bundle.canonicalName} · sources: ${bundle.sources.map(sourceLabel).join(" + ")}`}
              >
                {bundleTitle(bundle)}
              </span>
              {bundle.correctionRevision > 0 ? (
                <span
                  className={cn(
                    "shrink-0 rounded-full border px-1 py-px font-mono text-[7px] uppercase tracking-wide",
                    bundle.correctionState === "complete"
                      ? "border-emerald-400/25 text-emerald-200/70"
                      : "border-amber-400/25 text-amber-200/70",
                  )}
                  title={`Correction revision ${bundle.correctionRevision} · ${bundle.correctionState ?? "draft"}`}
                >
                  r{bundle.correctionRevision}
                </span>
              ) : null}
              <span className="shrink-0 font-mono text-[8px] tabular-nums text-chrome-text/45">
                {bundle.averageConfidence == null ? "—" : `${Math.round(bundle.averageConfidence * 100)}%`}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[8px] text-chrome-text/43">
              <span>{bundle.nodeCount} objects</span>
              <span>{bundle.bindingCount} bindings</span>
              {bundle.unboundPopulationCount > 0 ? <span className="text-amber-300/75">{bundle.unboundPopulationCount} unbound</span> : null}
            </div>
            <div className="mt-1 truncate text-[8px] text-chrome-text/35">{formatTime(bundle.completedAt || bundle.updatedAt)}</div>
          </button>
        ))}
      </div>
    </aside>
  )
}

function SkeletonCanvas({
  bundle,
  selectedNodeKey,
  onSelectNode,
}: {
  bundle: TopologyBundle
  selectedNodeKey: string | null
  onSelectNode: (nodeKey: string) => void
}) {
  const nodes = bundle.result.nodes
  const bindingsByNode = useMemo(() => {
    const result = new Map<string, TopologyBinding[]>()
    for (const binding of bundle.result.bindings) {
      const bucket = result.get(binding.node_key) ?? []
      bucket.push(binding)
      result.set(binding.node_key, bucket)
    }
    return result
  }, [bundle])
  const children = useMemo(() => {
    const result = new Map<string | null, TopologyNode[]>()
    const keys = new Set(nodes.map((node) => node.node_key))
    for (const node of nodes) {
      const parent = node.parent_node_key && keys.has(node.parent_node_key) ? node.parent_node_key : null
      const bucket = result.get(parent) ?? []
      bucket.push(node)
      result.set(parent, bucket)
    }
    for (const bucket of result.values()) bucket.sort((left, right) => left.name.localeCompare(right.name))
    return result
  }, [nodes])
  const populationMap = useMemo(
    () => new Map((bundle.context.populations ?? []).map((population) => [population.population_id, population])),
    [bundle],
  )

  return (
    <section className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-chrome-border/55 max-[900px]:border-r-0">
      <div className="shrink-0 border-b border-chrome-border/45 bg-secondary-background/12 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-xs font-medium"
              title={`${bundle.canonicalName} · sources: ${bundle.sources.map(sourceLabel).join(" + ")}`}
            >
              {bundleTitle(bundle)}
            </div>
            <div className="mt-0.5 truncate font-mono text-[8px] text-chrome-text/40">{bundle.scopeId}</div>
          </div>
          <MetricPill label="sources" value={bundle.sourceCount} />
          <MetricPill label="nodes" value={bundle.nodeCount} />
          <MetricPill label="edges" value={bundle.edgeCount} />
          <MetricPill label="cost" value={formatMoney(bundle.cost)} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {bundle.sources.map((source) => (
            <span key={source.source_key} className="rounded-full border border-chrome-border/50 bg-doc-bg/35 px-1.5 py-0.5 font-mono text-[8px] text-chrome-text/60">
              {sourceLabel(source)}
            </span>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {nodes.length === 0 ? (
          <div className="grid min-h-[220px] place-items-center rounded-md border border-dashed border-chrome-border/55 text-center">
            <div>
              <Sparkles className="mx-auto h-5 w-5 text-chrome-text/30" />
              <div className="mt-2 text-[11px] text-chrome-text/55">The model proposed no grounded objects for this unit.</div>
              <div className="mt-1 font-mono text-[8px] text-chrome-text/35">abstention is a valid shadow result</div>
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-2">
            {(children.get(null) ?? []).map((node) => (
              <TopologyBranch
                key={node.node_key}
                node={node}
                depth={0}
                childMap={children}
                bindingsByNode={bindingsByNode}
                populationMap={populationMap}
                selectedNodeKey={selectedNodeKey}
                onSelectNode={onSelectNode}
              />
            ))}
          </div>
        )}

        {bundle.result.edges.length > 0 ? (
          <div className="mx-auto mt-4 max-w-4xl rounded-md border border-chrome-border/50 bg-secondary-background/18">
            <div className="flex items-center gap-1.5 border-b border-chrome-border/40 px-2.5 py-1.5 font-mono text-[8px] uppercase tracking-wider text-chrome-text/50">
              <GitBranch className="h-3 w-3" /> cross-cutting relationships
            </div>
            <div className="divide-y divide-chrome-border/30">
              {bundle.result.edges.map((edge, index) => (
                <EdgeRow key={`${edge.subject_node_key}:${edge.predicate}:${edge.object_node_key}:${index}`} edge={edge} nodes={nodes} />
              ))}
            </div>
          </div>
        ) : null}

        {bundle.result.unbound_population_ids.length > 0 ? (
          <div className="mx-auto mt-3 max-w-4xl rounded-md border border-amber-400/25 bg-amber-400/5 p-2.5">
            <div className="flex items-center gap-1.5 text-[10px] text-amber-200/85">
              <AlertTriangle className="h-3.5 w-3.5" /> {bundle.result.unbound_population_ids.length} populations were deliberately left unbound
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {bundle.result.unbound_population_ids.map((populationId) => (
                <span key={populationId} className="rounded-sm border border-amber-400/20 bg-doc-bg/30 px-1.5 py-0.5 font-mono text-[8px] text-chrome-text/55">
                  {populationMap.get(populationId)?.display_name ?? populationId}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  )
}

function MetricPill({ label, value }: { label: string; value: string | number }) {
  return (
    <span className="rounded-sm border border-chrome-border/45 bg-doc-bg/30 px-1.5 py-0.5 font-mono text-[8px] text-chrome-text/55">
      <span className="text-foreground/75">{value}</span> {label}
    </span>
  )
}

function TopologyBranch({
  node,
  depth,
  childMap,
  bindingsByNode,
  populationMap,
  selectedNodeKey,
  onSelectNode,
}: {
  node: TopologyNode
  depth: number
  childMap: Map<string | null, TopologyNode[]>
  bindingsByNode: Map<string, TopologyBinding[]>
  populationMap: Map<string, TopologyPopulationContext>
  selectedNodeKey: string | null
  onSelectNode: (nodeKey: string) => void
}) {
  const bindings = bindingsByNode.get(node.node_key) ?? []
  const descendants = childMap.get(node.node_key) ?? []
  const sources = new Set(
    bindings.map((binding) => populationMap.get(binding.population_id)?.source_key).filter(Boolean),
  )
  const tone = KIND_TONES[node.node_kind] ?? "var(--main)"
  return (
    <div className={cn(depth > 0 && "ml-5 border-l border-chrome-border/40 pl-3")}>
      <button
        type="button"
        onClick={() => onSelectNode(node.node_key)}
        className={cn(
          "group w-full rounded-md border p-2.5 text-left transition",
          selectedNodeKey === node.node_key
            ? "border-cyan-300/45 bg-cyan-400/7 shadow-[inset_2px_0_0_oklch(76%_0.14_190)]"
            : "border-chrome-border/50 bg-secondary-background/20 hover:border-chrome-border hover:bg-foreground/[0.035]",
        )}
      >
        <div className="flex items-start gap-2">
          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-sm" style={{ background: tone, boxShadow: `0 0 12px ${tone}` }} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-medium">{node.name}</span>
              <span className="rounded-full border px-1.5 py-px font-mono text-[7px] uppercase tracking-wider" style={{ borderColor: `color-mix(in oklch, ${tone} 45%, transparent)`, color: tone }}>
                {node.node_kind}
              </span>
              <span className="ml-auto font-mono text-[8px] tabular-nums text-chrome-text/45">{Math.round(node.confidence * 100)}%</span>
            </div>
            {node.description ? <p className="mt-1 line-clamp-2 text-[9px] leading-relaxed text-chrome-text/55">{node.description}</p> : null}
            <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[8px] text-chrome-text/38">
              <span>{bindings.length} bindings</span>
              <span>{sources.size} sources</span>
              {descendants.length > 0 ? <span>{descendants.length} children</span> : null}
            </div>
          </div>
        </div>
      </button>
      {descendants.length > 0 ? (
        <div className="mt-2 space-y-2">
          {descendants.map((child) => (
            <TopologyBranch
              key={child.node_key}
              node={child}
              depth={depth + 1}
              childMap={childMap}
              bindingsByNode={bindingsByNode}
              populationMap={populationMap}
              selectedNodeKey={selectedNodeKey}
              onSelectNode={onSelectNode}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function EdgeRow({ edge, nodes }: { edge: TopologyEdge; nodes: TopologyNode[] }) {
  const names = new Map(nodes.map((node) => [node.node_key, node.name]))
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_36px] items-center gap-2 px-2.5 py-1.5 text-[9px]">
      <span className="truncate text-right text-foreground/75">{names.get(edge.subject_node_key) ?? edge.subject_node_key}</span>
      <span className="rounded-full border border-chrome-border/45 bg-doc-bg/35 px-1.5 py-px font-mono text-[7px] text-chrome-text/55">{edge.predicate}</span>
      <span className="truncate text-foreground/75">{names.get(edge.object_node_key) ?? edge.object_node_key}</span>
      <span className="font-mono text-[8px] tabular-nums text-chrome-text/40">{Math.round(edge.confidence * 100)}%</span>
    </div>
  )
}

function Inspector({
  connectionId,
  correctionsAvailable,
  bundle,
  node,
  tab,
  onTab,
  onSaved,
}: {
  connectionId: string
  correctionsAvailable: boolean
  bundle: TopologyBundle
  node: TopologyNode | null
  tab: InspectorTab
  onTab: (tab: InspectorTab) => void
  onSaved: () => Promise<void>
}) {
  return (
    <aside className="flex min-h-0 flex-col bg-secondary-background/10 max-[900px]:hidden">
      <div className="flex h-9 shrink-0 items-center border-b border-chrome-border/45 px-2">
        {(["object", "evidence", "review", "receipt"] as InspectorTab[]).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => onTab(value)}
            className={cn(
              "h-9 border-b-2 px-2 font-mono text-[8px] uppercase tracking-wider",
              tab === value ? "border-b-cyan-300 text-cyan-200" : "border-b-transparent text-chrome-text/45 hover:text-foreground",
            )}
          >
            {value}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "object" ? <ObjectInspector bundle={bundle} node={node} /> : null}
        {tab === "evidence" ? <EvidenceInspector bundle={bundle} /> : null}
        {tab === "review" ? (
          <ReviewInspector
            key={bundle.bundleId}
            connectionId={connectionId}
            correctionsAvailable={correctionsAvailable}
            bundle={bundle}
            selectedNode={node}
            onSaved={onSaved}
          />
        ) : null}
        {tab === "receipt" ? <ReceiptInspector bundle={bundle} /> : null}
      </div>
    </aside>
  )
}

function ObjectInspector({ bundle, node }: { bundle: TopologyBundle; node: TopologyNode | null }) {
  if (!node) return <div className="text-[10px] text-chrome-text/45">This bundle has no proposed object to inspect.</div>
  const populations = new Map((bundle.context.populations ?? []).map((item) => [item.population_id, item]))
  const bindings = bundle.result.bindings.filter((binding) => binding.node_key === node.node_key)
  const edges = bundle.result.edges.filter((edge) => edge.subject_node_key === node.node_key || edge.object_node_key === node.node_key)
  return (
    <div className="space-y-4">
      <section>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: KIND_TONES[node.node_kind] }} />
          <h3 className="text-sm font-medium">{node.name}</h3>
          <span className="ml-auto font-mono text-[9px] text-chrome-text/50">{Math.round(node.confidence * 100)}%</span>
        </div>
        <div className="mt-1 font-mono text-[8px] text-chrome-text/35">{node.node_key}</div>
        {node.description ? <p className="mt-2 text-[10px] leading-relaxed text-chrome-text/65">{node.description}</p> : null}
      </section>

      <InspectorSection title={`bindings · ${bindings.length}`} icon={Layers}>
        {bindings.length === 0 ? <Muted>No bindings.</Muted> : (
          <div className="space-y-1.5">
            {bindings.map((binding, index) => (
              <BindingCard key={`${binding.population_id}:${binding.binding_role}:${index}`} binding={binding} population={populations.get(binding.population_id)} />
            ))}
          </div>
        )}
      </InspectorSection>

      <InspectorSection title={`relationships · ${edges.length}`} icon={GitBranch}>
        {edges.length === 0 ? <Muted>No cross-cutting relationships.</Muted> : (
          <div className="space-y-1">
            {edges.map((edge, index) => (
              <div key={`${edge.subject_node_key}:${edge.predicate}:${index}`} className="rounded-sm border border-chrome-border/45 bg-doc-bg/25 p-2 font-mono text-[8px] leading-relaxed text-chrome-text/65">
                {edge.subject_node_key} <span className="text-cyan-200/75">{edge.predicate}</span> {edge.object_node_key}
              </div>
            ))}
          </div>
        )}
      </InspectorSection>

      <InspectorSection title="evidence receipts" icon={Hash}>
        <div className="space-y-1">
          {node.evidence_work_ids.map((workId) => <HashLine key={workId} value={workId} />)}
        </div>
      </InspectorSection>
    </div>
  )
}

function BindingCard({ binding, population }: { binding: TopologyBinding; population?: TopologyPopulationContext }) {
  return (
    <div className="rounded-sm border border-chrome-border/45 bg-doc-bg/25 p-2">
      <div className="truncate font-mono text-[9px] text-foreground/75" title={population?.display_name ?? binding.population_id}>
        {population?.display_name ?? binding.population_id}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <SmallChip>{binding.binding_role}</SmallChip>
        <SmallChip>{binding.authority_hint}</SmallChip>
        <span className="ml-auto font-mono text-[8px] text-chrome-text/45">{Math.round(binding.confidence * 100)}%</span>
      </div>
    </div>
  )
}

function ReviewInspector({
  connectionId,
  correctionsAvailable,
  bundle,
  selectedNode,
  onSaved,
}: {
  connectionId: string
  correctionsAvailable: boolean
  bundle: TopologyBundle
  selectedNode: TopologyNode | null
  onSaved: () => Promise<void>
}) {
  const [draft, setDraft] = useState<TopologyBundleCorrection>(() => correctionDraft(bundle))
  const [reviewNodeKey, setReviewNodeKey] = useState(
    selectedNode?.node_key ?? bundle.receiptResult.nodes[0]?.node_key ?? "",
  )
  const [bindingIndex, setBindingIndex] = useState(0)
  const [mergeTarget, setMergeTarget] = useState("")
  const [expectedRevision, setExpectedRevision] = useState(bundle.correctionRevision)
  const [saving, setSaving] = useState<"draft" | "complete" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)

  const rawNodes = bundle.receiptResult.nodes
  const nodePatchMap = useMemo(
    () => new Map(draft.node_patches.map((patch) => [patch.node_key, patch])),
    [draft.node_patches],
  )
  const reviewNode = rawNodes.find((item) => item.node_key === reviewNodeKey) ?? rawNodes[0] ?? null
  const reviewPatch = reviewNode ? nodePatchMap.get(reviewNode.node_key) : undefined
  const effectiveNodeName = reviewNode ? reviewPatch?.name ?? reviewNode.name : ""
  const effectiveNodeDescription = reviewNode
    ? (Object.hasOwn(reviewPatch ?? {}, "description") ? reviewPatch?.description : reviewNode.description) ?? ""
    : ""
  const effectiveNodeKind = reviewNode ? reviewPatch?.node_kind ?? reviewNode.node_kind : "object"
  const effectiveParent = reviewNode
    ? Object.hasOwn(reviewPatch ?? {}, "parent_node_key")
      ? reviewPatch?.parent_node_key ?? ""
      : reviewNode.parent_node_key ?? ""
    : ""

  const descendants = useMemo(() => {
    if (!reviewNode) return new Set<string>()
    const childMap = new Map<string, string[]>()
    for (const item of rawNodes) {
      const patch = nodePatchMap.get(item.node_key)
      const parent = Object.hasOwn(patch ?? {}, "parent_node_key")
        ? patch?.parent_node_key ?? null
        : item.parent_node_key ?? null
      if (!parent) continue
      childMap.set(parent, [...(childMap.get(parent) ?? []), item.node_key])
    }
    const found = new Set<string>()
    const pending = [...(childMap.get(reviewNode.node_key) ?? [])]
    while (pending.length > 0) {
      const next = pending.shift()
      if (!next || found.has(next)) continue
      found.add(next)
      pending.push(...(childMap.get(next) ?? []))
    }
    return found
  }, [nodePatchMap, rawNodes, reviewNode])

  const nodeBindings = useMemo(
    () => bundle.receiptResult.bindings.filter((binding) => binding.node_key === reviewNode?.node_key),
    [bundle.receiptResult.bindings, reviewNode?.node_key],
  )
  const reviewBinding = nodeBindings[bindingIndex] ?? nodeBindings[0] ?? null
  const reviewBindingPatch = reviewBinding
    ? draft.binding_patches.find(
        (patch) =>
          patch.node_key === reviewBinding.node_key
          && patch.population_id === reviewBinding.population_id
          && patch.original_binding_role === reviewBinding.binding_role,
      )
    : undefined
  const populationMap = useMemo(
    () => new Map((bundle.context.populations ?? []).map((population) => [population.population_id, population])),
    [bundle.context.populations],
  )

  function selectReviewNode(nodeKey: string) {
    setReviewNodeKey(nodeKey)
    setBindingIndex(0)
    setMergeTarget("")
  }

  function updateNodePatch(values: Partial<TopologyNodePatch>) {
    if (!reviewNode) return
    setDraft((current) => {
      const index = current.node_patches.findIndex((patch) => patch.node_key === reviewNode.node_key)
      const nextPatch: TopologyNodePatch = {
        ...(index >= 0 ? current.node_patches[index] : { node_key: reviewNode.node_key }),
        ...values,
      }
      const nodePatches = [...current.node_patches]
      if (index >= 0) nodePatches[index] = nextPatch
      else nodePatches.push(nextPatch)
      return { ...current, node_patches: nodePatches }
    })
    setSavedMessage(null)
  }

  function suppressBranch(suppressed: boolean) {
    if (!reviewNode) return
    const affected = new Set([reviewNode.node_key, ...descendants])
    setDraft((current) => {
      const patches = new Map(current.node_patches.map((patch) => [patch.node_key, patch]))
      for (const nodeKey of affected) {
        patches.set(nodeKey, { ...(patches.get(nodeKey) ?? { node_key: nodeKey }), suppressed })
      }
      return { ...current, node_patches: [...patches.values()] }
    })
    setSavedMessage(null)
  }

  function updateBindingPatch(values: Partial<TopologyBindingPatch>) {
    if (!reviewBinding) return
    setDraft((current) => {
      const identity = {
        node_key: reviewBinding.node_key,
        population_id: reviewBinding.population_id,
        original_binding_role: reviewBinding.binding_role,
      }
      const index = current.binding_patches.findIndex(
        (patch) =>
          patch.node_key === identity.node_key
          && patch.population_id === identity.population_id
          && patch.original_binding_role === identity.original_binding_role,
      )
      const nextPatch: TopologyBindingPatch = {
        ...(index >= 0 ? current.binding_patches[index] : identity),
        ...values,
      }
      const bindingPatches = [...current.binding_patches]
      if (index >= 0) bindingPatches[index] = nextPatch
      else bindingPatches.push(nextPatch)
      return { ...current, binding_patches: bindingPatches }
    })
    setSavedMessage(null)
  }

  function addSuggestion(kind: "split_node" | "merge_nodes", nodeKeys: string[]) {
    if (nodeKeys.length === 0) return
    setDraft((current) => {
      const duplicate = current.relationship_suggestions.some(
        (suggestion) => suggestion.kind === kind && suggestion.node_keys.join("\u0000") === nodeKeys.join("\u0000"),
      )
      return duplicate
        ? current
        : {
            ...current,
            relationship_suggestions: [
              ...current.relationship_suggestions,
              { kind, node_keys: nodeKeys },
            ],
          }
    })
    setSavedMessage(null)
  }

  async function saveCorrection(correctionState: "draft" | "complete") {
    if (!draft.canonical_name?.trim()) {
      setError("Give this tree a canonical business name before saving.")
      return
    }
    setSaving(correctionState)
    setError(null)
    setSavedMessage(null)
    const payload: TopologyBundleCorrection = {
      ...draft,
      canonical_name: draft.canonical_name.trim(),
      review_note: draft.review_note?.trim() || undefined,
    }
    try {
      const result = await saveBusinessTopologyCorrection(
        connectionId,
        bundle.bundleId,
        payload,
        correctionState,
        expectedRevision,
      )
      if (!result.ok) {
        setError(result.error)
        return
      }
      setExpectedRevision(result.revision)
      await onSaved()
      setSavedMessage(
        correctionState === "complete"
          ? `Review pass saved as revision ${result.revision}. It is still shadow state.`
          : `Draft revision ${result.revision} saved.`,
      )
    } catch (caught) {
      setError(String(caught))
    } finally {
      setSaving(null)
    }
  }

  if (!correctionsAvailable) {
    return (
      <div className="rounded-md border border-amber-400/25 bg-amber-400/5 p-3">
        <div className="flex items-center gap-2 text-[10px] text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5" /> Correction overlays are not installed here
        </div>
        <p className="mt-2 text-[9px] leading-relaxed text-chrome-text/55">
          Apply migration 0284 to edit this immutable proposal through versioned review overlays.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-cyan-400/25 bg-cyan-400/5 p-2.5">
        <div className="flex items-center gap-2">
          <Pencil className="h-3.5 w-3.5 text-cyan-200" />
          <span className="text-[10px] font-medium">Correction overlay</span>
          <span className="ml-auto font-mono text-[8px] text-chrome-text/50">
            revision {expectedRevision}
          </span>
        </div>
        <p className="mt-1.5 text-[9px] leading-relaxed text-chrome-text/55">
          The model receipt stays immutable. These edits change only the debugger projection and never promote governed topology.
        </p>
        {bundle.correctionState ? (
          <div className="mt-1.5 font-mono text-[8px] text-chrome-text/45">
            latest {bundle.correctionState} · {bundle.correctedBy ?? "unknown reviewer"} · {formatTime(bundle.correctedAt ?? "")}
          </div>
        ) : null}
      </div>

      <InspectorSection title="tree identity" icon={TreeStructure}>
        <ReviewLabel label="canonical business name">
          <input
            value={draft.canonical_name ?? ""}
            maxLength={120}
            onChange={(event) => {
              setDraft((current) => ({ ...current, canonical_name: event.target.value }))
              setSavedMessage(null)
            }}
            className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg/45 px-2 text-[10px] outline-none focus:border-cyan-400/45"
          />
        </ReviewLabel>
      </InspectorSection>

      {reviewNode ? (
        <InspectorSection title="object correction" icon={Layers}>
          <div className="space-y-2 rounded-md border border-chrome-border/45 bg-doc-bg/20 p-2.5">
            <ReviewLabel label="receipt object">
              <select
                value={reviewNode.node_key}
                onChange={(event) => selectReviewNode(event.target.value)}
                className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 text-[9px] outline-none focus:border-cyan-400/45"
              >
                {rawNodes.map((item) => {
                  const patch = nodePatchMap.get(item.node_key)
                  return (
                    <option key={item.node_key} value={item.node_key}>
                      {patch?.suppressed ? "[hidden] " : ""}{patch?.name ?? item.name}
                    </option>
                  )
                })}
              </select>
            </ReviewLabel>
            <ReviewLabel label="display name">
              <input
                value={effectiveNodeName}
                maxLength={180}
                onChange={(event) => updateNodePatch({ name: event.target.value })}
                className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg/45 px-2 text-[10px] outline-none focus:border-cyan-400/45"
              />
            </ReviewLabel>
            <div className="grid grid-cols-2 gap-2">
              <ReviewLabel label="kind">
                <select
                  value={effectiveNodeKind}
                  onChange={(event) => updateNodePatch({ node_kind: event.target.value as TopologyNodeKind })}
                  className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 font-mono text-[8px] outline-none focus:border-cyan-400/45"
                >
                  {NODE_KINDS.map((kind) => <option key={kind}>{kind}</option>)}
                </select>
              </ReviewLabel>
              <ReviewLabel label="parent">
                <select
                  value={effectiveParent}
                  onChange={(event) => updateNodePatch({ parent_node_key: event.target.value || null })}
                  className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 text-[8px] outline-none focus:border-cyan-400/45"
                >
                  <option value="">top level</option>
                  {rawNodes
                    .filter((item) =>
                      item.node_key !== reviewNode.node_key
                      && !descendants.has(item.node_key)
                      && !nodePatchMap.get(item.node_key)?.suppressed,
                    )
                    .map((item) => (
                      <option key={item.node_key} value={item.node_key}>
                        {nodePatchMap.get(item.node_key)?.name ?? item.name}
                      </option>
                    ))}
                </select>
              </ReviewLabel>
            </div>
            <ReviewLabel label="description">
              <textarea
                value={effectiveNodeDescription}
                maxLength={4000}
                rows={3}
                onChange={(event) => updateNodePatch({ description: event.target.value || null })}
                className="w-full resize-y rounded-sm border border-chrome-border/55 bg-doc-bg/45 px-2 py-1.5 text-[9px] leading-relaxed outline-none focus:border-cyan-400/45"
              />
            </ReviewLabel>
            <button
              type="button"
              onClick={() => suppressBranch(!reviewPatch?.suppressed)}
              className={cn(
                "flex h-7 w-full items-center justify-center gap-1.5 rounded-sm border font-mono text-[8px] uppercase tracking-wide transition",
                reviewPatch?.suppressed
                  ? "border-cyan-400/30 bg-cyan-400/8 text-cyan-200 hover:bg-cyan-400/12"
                  : "border-danger/25 text-danger/70 hover:bg-danger/8",
              )}
            >
              {reviewPatch?.suppressed ? <Eye className="h-3 w-3" /> : <X className="h-3 w-3" />}
              {reviewPatch?.suppressed ? "restore branch" : `suppress branch${descendants.size ? ` · ${descendants.size + 1} objects` : ""}`}
            </button>
          </div>
        </InspectorSection>
      ) : null}

      {reviewNode && nodeBindings.length > 0 ? (
        <InspectorSection title={`binding correction · ${nodeBindings.length}`} icon={Hash}>
          <div className="space-y-2 rounded-md border border-chrome-border/45 bg-doc-bg/20 p-2.5">
            <ReviewLabel label="receipt binding">
              <select
                value={Math.min(bindingIndex, nodeBindings.length - 1)}
                onChange={(event) => setBindingIndex(Number(event.target.value))}
                className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 text-[8px] outline-none focus:border-cyan-400/45"
              >
                {nodeBindings.map((binding, index) => (
                  <option key={`${bindingPatchKey(binding)}:${index}`} value={index}>
                    {populationMap.get(binding.population_id)?.display_name ?? binding.population_id}
                  </option>
                ))}
              </select>
            </ReviewLabel>
            {reviewBinding ? (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <ReviewLabel label="role">
                    <select
                      value={reviewBindingPatch?.binding_role ?? reviewBinding.binding_role}
                      onChange={(event) => updateBindingPatch({ binding_role: event.target.value })}
                      className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 font-mono text-[8px] outline-none focus:border-cyan-400/45"
                    >
                      {BINDING_ROLES.map((role) => <option key={role}>{role}</option>)}
                    </select>
                  </ReviewLabel>
                  <ReviewLabel label="authority">
                    <select
                      value={reviewBindingPatch?.authority_hint ?? reviewBinding.authority_hint}
                      onChange={(event) => updateBindingPatch({ authority_hint: event.target.value })}
                      className="h-8 w-full rounded-sm border border-chrome-border/55 bg-doc-bg px-2 font-mono text-[8px] outline-none focus:border-cyan-400/45"
                    >
                      {AUTHORITY_HINTS.map((authority) => <option key={authority}>{authority}</option>)}
                    </select>
                  </ReviewLabel>
                </div>
                <button
                  type="button"
                  onClick={() => updateBindingPatch({ suppressed: !reviewBindingPatch?.suppressed })}
                  className={cn(
                    "h-7 w-full rounded-sm border font-mono text-[8px] uppercase tracking-wide transition",
                    reviewBindingPatch?.suppressed
                      ? "border-cyan-400/30 text-cyan-200 hover:bg-cyan-400/8"
                      : "border-chrome-border/55 text-chrome-text/55 hover:bg-foreground/[0.04] hover:text-foreground",
                  )}
                >
                  {reviewBindingPatch?.suppressed ? "restore binding" : "suppress binding"}
                </button>
              </>
            ) : null}
          </div>
        </InspectorSection>
      ) : null}

      {reviewNode ? (
        <InspectorSection title="structural suggestions" icon={GitBranch}>
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => addSuggestion("split_node", [reviewNode.node_key])}
              className="h-7 w-full rounded-sm border border-chrome-border/55 font-mono text-[8px] uppercase tracking-wide text-chrome-text/60 transition hover:bg-foreground/[0.04] hover:text-foreground"
            >
              suggest splitting this object
            </button>
            {rawNodes.length > 1 ? (
              <div className="flex gap-1.5">
                <select
                  value={mergeTarget}
                  onChange={(event) => setMergeTarget(event.target.value)}
                  className="h-7 min-w-0 flex-1 rounded-sm border border-chrome-border/55 bg-doc-bg px-2 text-[8px] outline-none focus:border-cyan-400/45"
                >
                  <option value="">merge with…</option>
                  {rawNodes.filter((item) => item.node_key !== reviewNode.node_key).map((item) => (
                    <option key={item.node_key} value={item.node_key}>{nodePatchMap.get(item.node_key)?.name ?? item.name}</option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={!mergeTarget}
                  onClick={() => {
                    if (!mergeTarget) return
                    addSuggestion("merge_nodes", [reviewNode.node_key, mergeTarget])
                    setMergeTarget("")
                  }}
                  className="h-7 rounded-sm border border-chrome-border/55 px-2 font-mono text-[8px] uppercase text-chrome-text/60 hover:bg-foreground/[0.04] hover:text-foreground disabled:opacity-35"
                >
                  add
                </button>
              </div>
            ) : null}
            {draft.relationship_suggestions.map((suggestion, index) => (
              <div key={`${suggestion.kind}:${suggestion.node_keys.join(":")}:${index}`} className="flex items-start gap-2 rounded-sm border border-amber-400/20 bg-amber-400/5 p-2">
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[8px] uppercase text-amber-200/80">{suggestion.kind.replace("_", " ")}</div>
                  <div className="mt-0.5 line-clamp-2 text-[8px] text-chrome-text/50">
                    {suggestion.node_keys.map((key) => nodePatchMap.get(key)?.name ?? rawNodes.find((item) => item.node_key === key)?.name ?? key).join(" + ")}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setDraft((current) => ({
                    ...current,
                    relationship_suggestions: current.relationship_suggestions.filter((_, itemIndex) => itemIndex !== index),
                  }))}
                  className="grid h-5 w-5 shrink-0 place-items-center rounded-sm text-chrome-text/40 hover:bg-foreground/[0.05] hover:text-foreground"
                  title="Remove suggestion"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </InspectorSection>
      ) : null}

      <InspectorSection title="review note" icon={Pencil}>
        <textarea
          value={draft.review_note ?? ""}
          maxLength={4000}
          rows={4}
          placeholder="What did the model misunderstand? This becomes training and review signal."
          onChange={(event) => {
            setDraft((current) => ({ ...current, review_note: event.target.value }))
            setSavedMessage(null)
          }}
          className="w-full resize-y rounded-sm border border-chrome-border/55 bg-doc-bg/45 px-2 py-1.5 text-[9px] leading-relaxed outline-none placeholder:text-chrome-text/28 focus:border-cyan-400/45"
        />
      </InspectorSection>

      {error ? <div className="rounded-sm border border-danger/25 bg-danger/5 p-2 text-[9px] leading-relaxed text-danger/80">{error}</div> : null}
      {savedMessage ? <div className="rounded-sm border border-emerald-400/20 bg-emerald-400/5 p-2 text-[9px] leading-relaxed text-emerald-200/75">{savedMessage}</div> : null}

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          disabled={saving !== null}
          onClick={() => void saveCorrection("draft")}
          className="flex h-8 items-center justify-center gap-1.5 rounded-sm border border-chrome-border/60 font-mono text-[8px] uppercase tracking-wide text-chrome-text/65 transition hover:bg-foreground/[0.05] hover:text-foreground disabled:opacity-45"
        >
          {saving === "draft" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
          save draft
        </button>
        <button
          type="button"
          disabled={saving !== null}
          onClick={() => void saveCorrection("complete")}
          className="flex h-8 items-center justify-center gap-1.5 rounded-sm border border-cyan-400/35 bg-cyan-400/8 font-mono text-[8px] uppercase tracking-wide text-cyan-200 transition hover:bg-cyan-400/12 disabled:opacity-45"
        >
          {saving === "complete" ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
          finish review
        </button>
      </div>
      <div className="text-center font-mono text-[7px] uppercase tracking-wide text-chrome-text/30">
        finish review means correction complete · not accepted · not governed
      </div>
    </div>
  )
}

function ReviewLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block font-mono text-[7px] uppercase tracking-wide text-chrome-text/38">{label}</span>
      {children}
    </label>
  )
}

function EvidenceInspector({ bundle }: { bundle: TopologyBundle }) {
  const validation = bundle.validation
  return (
    <div className="space-y-4">
      <InspectorSection title="validation boundary" icon={CheckCircle2}>
        <div className="grid grid-cols-2 gap-1.5">
          <Datum label="valid" value={String(validation.valid ?? true)} />
          <Datum label="status" value={bundle.status.replace("_", " ")} />
          <Datum label="nodes" value={bundle.nodeCount} />
          <Datum label="bindings" value={bundle.bindingCount} />
          <Datum label="edges" value={bundle.edgeCount} />
          <Datum label="unbound" value={bundle.unboundPopulationCount} warn={bundle.unboundPopulationCount > 0} />
        </div>
      </InspectorSection>

      <InspectorSection title={`sources · ${bundle.sources.length}`} icon={Layers}>
        <div className="space-y-1.5">
          {bundle.sources.map((source) => (
            <div key={source.source_key} className="rounded-sm border border-chrome-border/45 bg-doc-bg/25 p-2">
              <div className="font-mono text-[9px] text-foreground/75">{sourceLabel(source)}</div>
              <div className="mt-0.5 truncate font-mono text-[7px] text-chrome-text/35">{source.source_key}</div>
            </div>
          ))}
        </div>
      </InspectorSection>

      <InspectorSection title="bundle rationale" icon={Sparkles}>
        <p className="text-[10px] leading-relaxed text-chrome-text/65">{bundle.result.rationale || "No additional rationale was supplied."}</p>
      </InspectorSection>

      <InspectorSection title="privacy contract" icon={Eye}>
        <div className="space-y-1 font-mono text-[8px] text-chrome-text/55">
          <div>raw values <span className="float-right text-emerald-300">absent</span></div>
          <div>value hashes <span className="float-right text-emerald-300">absent</span></div>
          <div>profile packets <span className="float-right text-emerald-300">absent</span></div>
          <div>materialized topology <span className="float-right text-amber-300">false</span></div>
        </div>
      </InspectorSection>
    </div>
  )
}

function ReceiptInspector({ bundle }: { bundle: TopologyBundle }) {
  return (
    <div className="space-y-4">
      <InspectorSection title="execution" icon={Sparkles}>
        <div className="grid grid-cols-2 gap-1.5">
          <Datum label="prompt" value={bundle.promptTokens.toLocaleString()} />
          <Datum label="completion" value={bundle.completionTokens.toLocaleString()} />
          <Datum label="total tokens" value={bundle.totalTokens.toLocaleString()} />
          <Datum label="cost" value={formatMoney(bundle.cost)} />
        </div>
        <div className="mt-2 space-y-1">
          {bundle.modelVersions.map((model) => <HashLine key={model} value={model} />)}
        </div>
      </InspectorSection>

      <InspectorSection title="pinned contracts" icon={Hash}>
        <div className="space-y-2">
          <LabeledHash label="plan" value={bundle.planSha256} />
          <LabeledHash label="input" value={bundle.inputPacketSha256} />
          <LabeledHash label="worker" value={bundle.workerVersion} />
          <LabeledHash label="prompt contract" value={bundle.promptContractVersion} />
          <LabeledHash label="work" value={bundle.workId} />
        </div>
      </InspectorSection>

      <InspectorSection title={`model attempts · ${bundle.executionReceipts.length}`} icon={GitBranch}>
        <div className="space-y-2">
          {bundle.executionReceipts.map((receipt, index) => (
            <div key={`${receipt.provider_request_id ?? "attempt"}:${index}`} className="rounded-sm border border-chrome-border/45 bg-doc-bg/25 p-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[8px] uppercase text-cyan-200/75">attempt {index + 1}</span>
                <span className="ml-auto font-mono text-[8px] text-chrome-text/45">{Math.round(Number(receipt.latency_ms ?? 0))} ms</span>
              </div>
              <div className="mt-1 truncate font-mono text-[8px] text-chrome-text/55">{receipt.model_version ?? bundle.modelVersions[0] ?? "unknown model"}</div>
              {receipt.provider_request_id ? <div className="mt-1 truncate font-mono text-[7px] text-chrome-text/35">{receipt.provider_request_id}</div> : null}
              {(receipt.transport_attempts ?? 1) > 1 ? <div className="mt-1 text-[8px] text-amber-300/75">{receipt.transport_attempts} transport attempts</div> : null}
            </div>
          ))}
        </div>
      </InspectorSection>

      <div className="font-mono text-[8px] text-chrome-text/35">completed {formatTime(bundle.completedAt)}</div>
    </div>
  )
}

function InspectorSection({ title, icon: Icon, children }: { title: string; icon: typeof Layers; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-wider text-chrome-text/45">
        <Icon className="h-3 w-3" /> {title}
      </div>
      {children}
    </section>
  )
}

function Datum({ label, value, warn = false }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className="rounded-sm border border-chrome-border/40 bg-doc-bg/25 px-2 py-1.5">
      <div className="font-mono text-[7px] uppercase tracking-wide text-chrome-text/35">{label}</div>
      <div className={cn("mt-0.5 font-mono text-[10px]", warn ? "text-amber-300" : "text-foreground/75")}>{value}</div>
    </div>
  )
}

function HashLine({ value }: { value: string }) {
  return <div className="truncate rounded-sm border border-chrome-border/35 bg-doc-bg/25 px-1.5 py-1 font-mono text-[7px] text-chrome-text/45" title={value}>{value}</div>
}

function LabeledHash({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-[7px] uppercase tracking-wide text-chrome-text/30">{label}</div>
      <HashLine value={value || "not recorded"} />
    </div>
  )
}

function SmallChip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full border border-chrome-border/45 bg-secondary-background/35 px-1.5 py-px font-mono text-[7px] uppercase text-chrome-text/55">{children}</span>
}

function Muted({ children }: { children: React.ReactNode }) {
  return <div className="text-[9px] text-chrome-text/40">{children}</div>
}
