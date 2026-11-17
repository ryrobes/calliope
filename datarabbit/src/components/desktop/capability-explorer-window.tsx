"use client"

/**
 * Capability Explorer — browses everything rvbbit.capability_search()
 * knows about (operators, metrics, cubes, packs, document brains, alerts,
 * MCP connector tools, Colony peer backends, and curated doctrine
 * patterns), grouped into folders by kind, with a "Try it" click-through
 * that drops a real or correctly-templated SQL call — the same
 * onOpenSqlData plumbing Colony's own roster uses. Plain browsing reads
 * rvbbit.kg_nodes directly (no embedder call); a "Semantic search" toggle
 * runs the real rvbbit.capability_search() KNN for fuzzy/conceptual
 * queries across the whole graph.
 */

import { useCallback, useMemo, useState } from "react"
import {
  AlertTriangle,
  BarChart3,
  Bell,
  BookOpen,
  Boxes,
  Brain,
  Compass,
  Loader2,
  Package,
  Plug,
  RefreshCw,
  Search,
  Sparkles,
  Users,
  Wand2,
} from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { Panel, Metric, fmtAgo } from "./instruments"
import {
  fetchCapabilityNodes,
  searchCapabilities,
  resolveTryIt,
  deriveStatus,
  CAP_KINDS,
  KIND_LABELS,
  type CapabilityNode,
  type CapKind,
  type CapabilitySearchHit,
  type CapabilityStatus,
} from "@/lib/rvbbit/capability-explorer"

interface CapabilityExplorerWindowProps {
  activeConnectionId: string | null
  onOpenSqlData?: (sql: string, title: string) => void
}

const KIND_ICONS: Record<CapKind, React.ComponentType<{ className?: string }>> = {
  cap_syntax: BookOpen,
  cap_operator: Wand2,
  cap_metric: BarChart3,
  cap_cube: Boxes,
  cap_pack: Package,
  cap_brain: Brain,
  cap_alert: Bell,
  cap_mcp_tool: Plug,
  cap_peer_backend: Users,
}

export function CapabilityExplorerWindow({ activeConnectionId, onOpenSqlData }: CapabilityExplorerWindowProps) {
  const [nodes, setNodes] = useState<CapabilityNode[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [folder, setFolder] = useState<CapKind | null>(null)
  const [filterText, setFilterText] = useState("")
  const [selected, setSelected] = useState<CapabilityNode | null>(null)

  const [semanticMode, setSemanticMode] = useState(false)
  const [semanticBusy, setSemanticBusy] = useState(false)
  const [semanticError, setSemanticError] = useState<string | null>(null)
  const [semanticHits, setSemanticHits] = useState<CapabilitySearchHit[] | null>(null)

  const load = useCallback(async () => {
    if (!activeConnectionId) return
    setLoading(true)
    const r = await fetchCapabilityNodes(activeConnectionId)
    setError(r.error)
    setNodes(r.nodes)
    setLoading(false)
  }, [activeConnectionId])

  usePolling(load, 30_000, { enabled: !!activeConnectionId, resetKey: activeConnectionId })

  const nodeByKey = useMemo(() => {
    const m = new Map<string, CapabilityNode>()
    for (const n of nodes) m.set(`${n.kind}:${n.name}`, n)
    return m
  }, [nodes])

  // Text-filtered but folder-INDEPENDENT — the basis for per-folder counts
  // (a count badge means "if I clicked this folder, how many would show",
  // so it can never be narrowed by whichever folder happens to be active).
  const matchedNodes = useMemo(() => {
    const q = filterText.trim().toLowerCase()
    if (!q) return nodes
    return nodes.filter((n) => {
      const doc = typeof n.properties.search_doc === "string" ? (n.properties.search_doc as string) : ""
      return n.name.toLowerCase().includes(q) || doc.toLowerCase().includes(q)
    })
  }, [nodes, filterText])

  // Once a semantic search has run, counts/list reflect ITS results instead
  // of the plain text filter — until the query changes (onChange clears
  // semanticHits back to null, falling back to matchedNodes as a live preview).
  const activeSemanticHits = semanticMode ? semanticHits : null

  const counts = useMemo(() => {
    const source: { kind: string }[] = activeSemanticHits ?? matchedNodes
    const c = new Map<CapKind, number>()
    for (const n of source) c.set(n.kind as CapKind, (c.get(n.kind as CapKind) ?? 0) + 1)
    return c
  }, [activeSemanticHits, matchedNodes])

  const allCount = activeSemanticHits ? activeSemanticHits.length : matchedNodes.length

  const visible = useMemo(() => {
    return folder ? matchedNodes.filter((n) => n.kind === folder) : matchedNodes
  }, [matchedNodes, folder])

  const visibleSemanticHits = useMemo(() => {
    if (!activeSemanticHits) return null
    return folder ? activeSemanticHits.filter((h) => h.kind === folder) : activeSemanticHits
  }, [activeSemanticHits, folder])

  const runSemanticSearch = async () => {
    if (!activeConnectionId || !filterText.trim()) return
    setSemanticBusy(true)
    setSemanticError(null)
    try {
      const r = await searchCapabilities(activeConnectionId, filterText.trim(), 30)
      if (r.error) throw new Error(r.error)
      setSemanticHits(r.hits)
    } catch (e) {
      setSemanticError(e instanceof Error ? e.message : String(e))
    } finally {
      setSemanticBusy(false)
    }
  }

  const selectByName = (kind: CapKind, name: string) => {
    const found = nodeByKey.get(`${kind}:${name}`)
    if (found) setSelected(found)
  }

  return (
    <div className="flex h-full flex-col bg-chrome-bg/20 text-[12px]">
      <div className="flex items-center gap-2 border-b border-chrome-border bg-chrome-bg/40 px-3 py-1.5">
        <Compass className="h-3.5 w-3.5 text-rvbbit-accent" />
        <span className="font-medium text-foreground">Capability Explorer</span>
        <span className="text-[10px] uppercase tracking-wider text-chrome-text/60">
          everything callable, in one place
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="grid h-6 w-6 place-items-center rounded text-chrome-text/60 hover:bg-foreground/[0.08] hover:text-foreground"
          title="Refresh"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
        </button>
      </div>

      <div className="flex items-center gap-2 border-b border-chrome-border/60 bg-chrome-bg/20 px-3 py-1.5">
        <Search className="h-3.5 w-3.5 text-chrome-text/50" />
        <input
          value={filterText}
          onChange={(e) => {
            setFilterText(e.target.value)
            setSemanticHits(null)
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && semanticMode) void runSemanticSearch()
          }}
          placeholder={semanticMode ? "Ask conceptually — e.g. \"something that classifies sentiment\"…" : "Filter by name or description…"}
          className="h-6 flex-1 rounded border border-chrome-border bg-doc-bg px-2 font-mono text-[11px] text-foreground outline-none focus:border-main/60"
        />
        <button
          type="button"
          onClick={() => {
            setSemanticMode((v) => !v)
            setSemanticHits(null)
            setSemanticError(null)
          }}
          className={cn(
            "inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium",
            semanticMode
              ? "border-rvbbit-accent/50 bg-rvbbit-accent/15 text-rvbbit-accent"
              : "border-chrome-border text-chrome-text/70 hover:bg-foreground/[0.08]",
          )}
          title="Toggle semantic search — runs rvbbit.capability_search() (meaning-based ranking) instead of a plain text filter"
        >
          <Sparkles className="h-3 w-3" />
          Semantic
        </button>
        {semanticMode ? (
          <button
            type="button"
            onClick={() => void runSemanticSearch()}
            disabled={semanticBusy || !filterText.trim()}
            className="inline-flex items-center gap-1 rounded border border-chrome-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-foreground/[0.08] disabled:opacity-40"
          >
            {semanticBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            Search
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="flex items-start gap-1.5 border-b border-danger/30 bg-danger/10 px-3 py-1.5 text-[11px] text-danger">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {error}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {/* folders */}
        <div className="w-52 shrink-0 overflow-y-auto border-r border-chrome-border/60 p-2">
          <FolderRow
            label="All capabilities"
            count={allCount}
            active={folder === null}
            onClick={() => setFolder(null)}
            icon={Compass}
          />
          {CAP_KINDS.map((k) => (
            <FolderRow
              key={k}
              label={KIND_LABELS[k]}
              count={counts.get(k) ?? 0}
              active={folder === k}
              onClick={() => setFolder(k)}
              icon={KIND_ICONS[k]}
            />
          ))}
        </div>

        {/* list */}
        <div className="w-72 shrink-0 overflow-y-auto border-r border-chrome-border/60 p-2">
          {visibleSemanticHits ? (
            <>
              {semanticError ? <p className="p-2 text-[11px] text-danger">{semanticError}</p> : null}
              {visibleSemanticHits.length === 0 ? (
                <p className="p-2 text-[11px] text-chrome-text/55">No matches.</p>
              ) : (
                visibleSemanticHits.map((h) => {
                  const status = nodeByKey.get(`${h.kind}:${h.name}`)
                  const s = status ? deriveStatus(status) : null
                  return (
                    <button
                      key={`${h.kind}:${h.name}`}
                      type="button"
                      onClick={() => selectByName(h.kind as CapKind, h.name)}
                      className={cn(
                        "block w-full rounded px-2 py-1.5 text-left hover:bg-foreground/[0.06]",
                        selected?.kind === h.kind && selected?.name === h.name && "bg-rvbbit-accent/10",
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="truncate font-mono text-[11px] text-foreground">{h.name}</span>
                        {s ? <StatusPill status={s} /> : null}
                        <span className="ml-auto shrink-0 text-[9px] tabular-nums text-chrome-text/45">
                          {h.score.toFixed(2)}
                        </span>
                      </div>
                      <div className="truncate text-[10px] text-chrome-text/55">{KIND_LABELS[h.kind as CapKind] ?? h.kind}</div>
                    </button>
                  )
                })
              )}
            </>
          ) : loading && nodes.length === 0 ? (
            <div className="flex items-center gap-2 p-3 text-chrome-text/60">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
            </div>
          ) : visible.length === 0 ? (
            <p className="p-2 text-[11px] text-chrome-text/55">Nothing here.</p>
          ) : (
            visible.map((n) => {
              const s = deriveStatus(n)
              return (
                <button
                  key={`${n.kind}:${n.name}`}
                  type="button"
                  onClick={() => setSelected(n)}
                  className={cn(
                    "block w-full rounded px-2 py-1.5 text-left hover:bg-foreground/[0.06]",
                    selected?.kind === n.kind && selected?.name === n.name && "bg-rvbbit-accent/10",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-[11px] text-foreground">{n.name}</span>
                    {s ? <StatusPill status={s} /> : null}
                  </div>
                  <div className="truncate text-[10px] text-chrome-text/55">{KIND_LABELS[n.kind] ?? n.kind}</div>
                </button>
              )
            })
          )}
        </div>

        {/* detail */}
        <div className="min-w-0 flex-1 overflow-y-auto p-3">
          {selected ? (
            <DetailPanel node={selected} onOpenSqlData={onOpenSqlData} />
          ) : (
            <div className="flex h-full items-center justify-center text-[11px] text-chrome-text/50">
              Pick something on the left to see what it does and try it.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatusPill({ status, size = "xs" }: { status: CapabilityStatus; size?: "xs" | "sm" }) {
  const toneClasses =
    status.tone === "good"
      ? "bg-success/12 text-success ring-success/30"
      : status.tone === "warn"
        ? "bg-warning/12 text-warning ring-warning/40"
        : "bg-foreground/[0.05] text-chrome-text/65 ring-chrome-border/40"
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full uppercase tracking-wider ring-1",
        size === "xs" ? "px-1.5 py-px text-[9px]" : "px-2 py-0.5 text-[10px]",
        toneClasses,
      )}
    >
      {status.label}
    </span>
  )
}

function FolderRow({
  label,
  count,
  active,
  onClick,
  icon: Icon,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
  icon: React.ComponentType<{ className?: string }>
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px]",
        active ? "bg-rvbbit-accent/15 text-rvbbit-accent" : "text-chrome-text/80 hover:bg-foreground/[0.06]",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{label}</span>
      <span className="ml-auto shrink-0 tabular-nums text-chrome-text/45">{count}</span>
    </button>
  )
}

function DetailPanel({
  node,
  onOpenSqlData,
}: {
  node: CapabilityNode
  onOpenSqlData?: (sql: string, title: string) => void
}) {
  const tryIt = useMemo(() => resolveTryIt(node), [node])
  const status = useMemo(() => deriveStatus(node), [node])
  const doc = typeof node.properties.search_doc === "string" ? (node.properties.search_doc as string) : ""
  const propEntries = Object.entries(node.properties).filter(([k]) => k !== "search_doc")

  return (
    <div className="flex flex-col gap-3">
      <Panel
        icon={KIND_ICONS[node.kind]}
        title={node.name}
        right={
          status || (tryIt && "sql" in tryIt && onOpenSqlData) ? (
            <>
              {status ? <StatusPill status={status} size="sm" /> : null}
              {tryIt && "sql" in tryIt && onOpenSqlData ? (
                <button
                  type="button"
                  onClick={() => onOpenSqlData(tryIt.sql, `Try ${node.name}`)}
                  className="rounded border border-chrome-border px-1.5 py-0.5 text-[10px] font-medium text-foreground hover:bg-foreground/[0.08]"
                >
                  Try it
                </button>
              ) : null}
            </>
          ) : null
        }
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          <Metric label="kind" value={KIND_LABELS[node.kind] ?? node.kind} />
          <Metric label="confidence" value={node.confidence.toFixed(2)} />
          <Metric label="updated" value={node.updatedMs ? fmtAgo(node.updatedMs) : "—"} />
        </div>
        {propEntries.length > 0 ? (
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-3">
            {propEntries.map(([k, v]) => (
              <div key={k} className="min-w-0 text-[11px]">
                <span className="text-chrome-text/50">{k}</span>{" "}
                <span className="text-foreground">
                  {typeof v === "object" ? JSON.stringify(v) : String(v ?? "—")}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </Panel>

      {tryIt && "note" in tryIt ? (
        <div className="rounded border border-chrome-border/60 bg-chrome-bg/30 p-2 text-[11px] text-chrome-text/70">
          {tryIt.note}
        </div>
      ) : null}

      <Panel title="Full description" icon={BookOpen}>
        <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded border border-chrome-border/50 bg-doc-bg p-3 font-mono text-[11px] leading-relaxed text-foreground">
          {doc || "(no description)"}
        </pre>
      </Panel>
    </div>
  )
}
