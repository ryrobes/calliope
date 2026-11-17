"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import { Anchor, Loader2, Plus, Trash2, Zap } from "@/lib/icons"
import {
  addNode,
  fetchFleet,
  fetchFleetActivity,
  fetchHare,
  fetchPublishState,
  fetchStoreConfig,
  probeNode,
  removeNode,
  setNodeEnabled,
  storeDoctor,
  type FleetNode,
  type HareInfo,
  type NodeActivity,
  type PublishTableState,
  type StoreConfig,
  type StoreDoctorReport,
} from "@/lib/rvbbit/fleet"
import {
  fetchClusterMembers,
  type ClusterCategory,
  type ClusterMember,
} from "@/lib/rvbbit/topology"
import { usePolling } from "@/lib/desktop/use-polling"
import { fmtAgo } from "./instruments"
import { cn } from "@/lib/utils"

/**
 * Fleet — the whole RVBBIT compute cluster as a direct-manipulation map. The
 * brain (this Postgres) sits center-left; around it orbit every kind of
 * muscle attached to this warehouse: read-fleet workers (probeable, latency =
 * distance), the serverless hare, Colony peers (client machines sharing
 * their local models — warrens of their own), Warren nodes, remote services
 * like Clover's specialist zoo, LLM providers, and MCP servers. The picture
 * IS the control surface: click a worker to probe it; click any member to
 * see the skills it contributes and jump to its dedicated window. Below: the
 * pond (publication water levels), the storage doctor, and the hare ledger.
 * A console strip shows the SQL the window is made of.
 */

interface Props {
  activeConnectionId: string | null
  workspaceActive: boolean
  onOpenColony?: () => void
  onOpenWarren?: () => void
  onOpenSpecialists?: () => void
  onOpenMcpServer?: (serverName: string) => void
}

interface ConsoleLine {
  at: number
  text: string
  ok: boolean
}

const POLL_MS = 6000
const ACTIVITY_HOURS = 6

const CATEGORY_COLOR: Record<ClusterCategory, string> = {
  colony: "oklch(70% 0.15 165)",
  warren: "var(--brand-warren, oklch(72% 0.16 55))",
  service: "var(--brand-capability, oklch(72% 0.15 250))",
  provider: "oklch(75% 0.14 75)",
  mcp: "var(--brand-mcp, oklch(70% 0.17 300))",
}

const CATEGORY_LABEL: Record<ClusterCategory, string> = {
  colony: "colony peer",
  warren: "warren",
  service: "service",
  provider: "LLM provider",
  mcp: "MCP server",
}

/** Category ring: things without a probeable latency sit at a fixed
 * conceptual distance — peers close (they're members of the room), local
 * services mid, external providers and MCP servers at the rim. */
const CATEGORY_REST: Record<ClusterCategory, number> = {
  colony: 245,
  warren: 285,
  service: 335,
  provider: 385,
  mcp: 405,
}

/** Deterministic orbit slot for worker i of n: an arc facing the brain. */
function orbitPoint(i: number, n: number, cx: number, cy: number, r: number): { x: number; y: number } {
  const spread = Math.min(100, 28 * Math.max(1, n - 1)) // degrees
  const start = -spread / 2
  const angle = n === 1 ? 0 : start + (spread * i) / (n - 1)
  const rad = (angle * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

/** Probe latency → spring rest length: the topology is a MAP of the fleet's
 * physics — fast workers sit close to the brain, slow ones drift outward.
 * Log-scaled (probes span 10ms warm to multi-second cold) and clamped so
 * everything stays on canvas. Never-probed nodes float mid-orbit. */
function latencyRestLength(probeMs: number | null): number {
  if (probeMs == null || probeMs <= 0) return 300
  return Math.max(170, Math.min(430, 60 + 105 * Math.log10(Math.max(probeMs, 5))))
}

interface SimNode {
  id: string
  restLen: number
  x: number
  y: number
}

/** Tiny deterministic spring relaxation (Scry's force-directed spirit, sized
 * for a couple dozen nodes): springs to the brain at rest length, pairwise
 * repulsion so labels never collide, gentle pull toward the vertical center.
 * Seeded from orbit slots and iterated to rest — no RNG, no jitter, the same
 * cluster always draws the same map. */
function relaxLayout(
  items: { id: string; restLen: number }[],
  bx: number,
  by: number,
  width: number,
  height: number,
): Map<string, { x: number; y: number }> {
  const nodes: SimNode[] = items.map((item, i) => {
    const seed = orbitPoint(i, Math.max(items.length, 1), bx, by, item.restLen)
    return { id: item.id, restLen: item.restLen, x: seed.x, y: seed.y }
  })
  const minX = bx + 120
  const maxX = width - 70
  const minY = 56
  const maxY = height - 74
  for (let step = 0; step < 160; step++) {
    for (const n of nodes) {
      // spring to brain
      const dx = n.x - bx
      const dy = n.y - by
      const dist = Math.max(Math.hypot(dx, dy), 1)
      const stretch = (dist - n.restLen) / dist
      n.x -= dx * stretch * 0.18
      n.y -= dy * stretch * 0.18
      // node-node repulsion (keeps labels apart)
      for (const m of nodes) {
        if (m === n) continue
        const rx = n.x - m.x
        const ry = n.y - m.y
        const rd = Math.max(Math.hypot(rx, ry), 1)
        if (rd < 112) {
          const push = ((112 - rd) / rd) * 0.32
          n.x += rx * push
          n.y += ry * push
        }
      }
      // soft vertical centering + canvas clamp
      n.y += (by - n.y) * 0.004
      n.x = Math.max(minX, Math.min(maxX, n.x))
      n.y = Math.max(minY, Math.min(maxY, n.y))
    }
  }
  return new Map(nodes.map((n) => [n.id, { x: n.x, y: n.y }]))
}

/** Spark-bars: hourly execution counts as a tiny SVG bar strip. */
function SparkBars({ buckets, color }: { buckets: number[]; color: string }) {
  const max = Math.max(...buckets, 1)
  const bw = 7
  const gap = 2
  const width = buckets.length * (bw + gap) - gap
  return (
    <g transform={`translate(${-width / 2}, 0)`}>
      {buckets.map((b, i) => {
        const h = b > 0 ? Math.max(2.5, (b / max) * 13) : 1
        return (
          <rect
            key={i}
            x={i * (bw + gap)}
            y={13 - h}
            width={bw}
            height={h}
            rx={1}
            fill={color}
            opacity={b > 0 ? 0.32 + 0.55 * (b / max) : 0.12}
          />
        )
      })}
    </g>
  )
}

function edgePath(bx: number, by: number, nx: number, ny: number): string {
  const mx = (bx + nx) / 2
  return `M ${bx} ${by} C ${mx} ${by}, ${mx} ${ny}, ${nx} ${ny}`
}

function healthColor(n: FleetNode): string {
  if (!n.enabled) return "var(--chrome-text)"
  if (n.last_probe_ok === true) return "var(--success, #3fb950)"
  if (n.last_probe_ok === false) return "var(--danger, #f85149)"
  return "var(--warning, #d29922)" // never probed
}

function memberHealthColor(m: ClusterMember): string {
  if (m.health === "ok") return "var(--success, #3fb950)"
  if (m.health === "warn") return "var(--warning, #d29922)"
  if (m.health === "down") return "var(--danger, #f85149)"
  return "var(--chrome-text)"
}

/** Lifetime call volume → node radius (log-scaled: 400k calls shouldn't be
 * 400k× the size of 12). */
function memberRadius(calls: number): number {
  return 15 + Math.min(16, calls > 0 ? 3.2 * Math.log10(1 + calls) : 0)
}

function fmtCalls(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${Math.round(n / 1000)}k`
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

/** Recency → edge presence: recently-active wires glow, dormant ones fade. */
function recencyOpacity(lastMs: number | null): number {
  if (lastMs == null) return 0.2
  const ageH = (Date.now() - lastMs) / 3_600_000
  if (ageH < 1) return 0.6
  if (ageH < 24) return 0.42
  return 0.25
}

const SKILL_DOT: Record<string, string> = {
  live: "var(--success, #3fb950)",
  paused: "var(--chrome-text)",
  offline: "var(--warning, #d29922)",
}

export function FleetWindow({
  activeConnectionId,
  workspaceActive,
  onOpenColony,
  onOpenWarren,
  onOpenSpecialists,
  onOpenMcpServer,
}: Props) {
  const [brain, setBrain] = useState<string>("…")
  const [registry, setRegistry] = useState(true)
  const [nodes, setNodes] = useState<FleetNode[]>([])
  const [members, setMembers] = useState<ClusterMember[]>([])
  const [pond, setPond] = useState<PublishTableState[]>([])
  const [store, setStore] = useState<StoreConfig | null>(null)
  const [hare, setHare] = useState<HareInfo | null>(null)
  const [activity, setActivity] = useState<NodeActivity[]>([])
  const [doctor, setDoctor] = useState<StoreDoctorReport | null>(null)
  const [doctorBusy, setDoctorBusy] = useState(false)
  const [probing, setProbing] = useState<string | null>(null)
  const [pulse, setPulse] = useState<{ name: string; nonce: number } | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null)
  const [console_, setConsole] = useState<ConsoleLine[]>([])
  const [adding, setAdding] = useState(false)
  // Two-step remove: first click arms, second confirms. Auto-disarms — a
  // fleet node is one accidental click from vanishing otherwise (it fails
  // open, but still).
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)
  useEffect(() => {
    if (!confirmRemove) return
    const t = setTimeout(() => setConfirmRemove(null), 3500)
    return () => clearTimeout(t)
  }, [confirmRemove])
  const [addName, setAddName] = useState("")
  const [addEndpoint, setAddEndpoint] = useState("")

  const log = useCallback((text: string, ok: boolean) => {
    setConsole((prev) => [{ at: Date.now(), text, ok }, ...prev].slice(0, 6))
  }, [])

  const refresh = useCallback(async () => {
    if (!activeConnectionId) return
    const [fleet, publish, cfg, hareInfo, act, cluster] = await Promise.all([
      fetchFleet(activeConnectionId),
      fetchPublishState(activeConnectionId),
      fetchStoreConfig(activeConnectionId),
      fetchHare(activeConnectionId),
      fetchFleetActivity(activeConnectionId, ACTIVITY_HOURS),
      fetchClusterMembers(activeConnectionId),
    ])
    setBrain(fleet.brain)
    setRegistry(fleet.registry)
    setNodes(fleet.nodes)
    setPond(publish)
    setStore(cfg)
    setHare(hareInfo)
    setActivity(act)
    setMembers(cluster)
  }, [activeConnectionId])

  usePolling(refresh, POLL_MS, {
    enabled: workspaceActive && !!activeConnectionId,
    resetKey: activeConnectionId,
  })

  const runProbe = useCallback(
    async (name: string) => {
      if (!activeConnectionId || probing) return
      setProbing(name)
      setPulse({ name, nonce: Date.now() })
      const r = await probeNode(activeConnectionId, name)
      if ("name" in r) {
        log(
          `rvbbit.fleet_probe('${name}') → ${r.ok ? "ok" : "FAIL"} ${r.probe_ms}ms${r.error ? ` · ${r.error}` : ""}`,
          r.ok,
        )
      } else {
        log(`rvbbit.fleet_probe('${name}') → ${r.error}`, false)
      }
      setProbing(null)
      void refresh()
    },
    [activeConnectionId, probing, log, refresh],
  )

  const runDoctor = useCallback(async () => {
    if (!activeConnectionId || doctorBusy) return
    setDoctorBusy(true)
    const r = await storeDoctor(activeConnectionId)
    if ("error" in r && r.error && !("configured" in r)) {
      log(`rvbbit.publish_store_doctor() → ${r.error}`, false)
    } else {
      const rep = r as StoreDoctorReport
      setDoctor(rep)
      log(
        `rvbbit.publish_store_doctor() → ${rep.ok ? `ok · put ${rep.put_ms}ms · head ${rep.head_ms}ms · del ${rep.delete_ms}ms` : rep.error ?? "not configured"}`,
        !!rep.ok,
      )
    }
    setDoctorBusy(false)
  }, [activeConnectionId, doctorBusy, log])

  const submitAdd = useCallback(async () => {
    if (!activeConnectionId || !addName.trim() || !addEndpoint.trim()) return
    const r = await addNode(activeConnectionId, addName.trim(), addEndpoint.trim())
    log(`rvbbit.fleet_add('${addName.trim()}', '${addEndpoint.trim()}') → ${r.ok ? "ok" : r.error}`, r.ok)
    setAdding(false)
    setAddName("")
    setAddEndpoint("")
    void refresh()
  }, [activeConnectionId, addName, addEndpoint, log, refresh])

  // ── topology geometry: latency/affinity = distance, workload = mass ──
  const W = 920
  const H = 400
  const bx = 170
  const by = H / 2
  const orbitR = 330
  const activityByPlacement = useMemo(() => {
    const m = new Map<string, NodeActivity>()
    for (const a of activity) m.set(a.placement, a)
    return m
  }, [activity])
  const totalExec = useMemo(
    () => Math.max(activity.reduce((s, a) => s + a.executions, 0), 1),
    [activity],
  )
  const hareLastMs = hare?.recent[0]?.total_ms ?? null

  // One shared layout pass over EVERYTHING orbiting the brain — fleet
  // workers (latency-sprung), the hare, and every cluster member (category
  // ring). One physics world, so nothing overlaps across kinds.
  const layout = useMemo(() => {
    const items = [
      ...nodes.map((n) => ({ id: `fleet:${n.name}`, restLen: latencyRestLength(n.last_probe_ms) })),
      ...(hare?.endpoint ? [{ id: "__hare__", restLen: latencyRestLength(hareLastMs) }] : []),
      ...members.map((m) => ({ id: m.id, restLen: CATEGORY_REST[m.category] })),
    ]
    return relaxLayout(items, bx, by, W, H)
  }, [nodes, hare?.endpoint, hareLastMs, members, by])

  const placed = useMemo(() => {
    return nodes.map((n) => {
      const p = layout.get(`fleet:${n.name}`) ?? orbitPoint(0, 1, bx, by, orbitR)
      const act = activityByPlacement.get(n.endpoint)
      const share = act ? act.executions / totalExec : 0
      return { node: n, x: p.x, y: p.y, act, share }
    })
  }, [nodes, layout, activityByPlacement, totalExec, by])
  const harePos = hare?.endpoint ? (layout.get("__hare__") ?? null) : null
  const hareAct = hare?.endpoint ? activityByPlacement.get(`hare:${hare.endpoint}`) : undefined
  const brainAct = activityByPlacement.get("brain")
  const selectedNode = nodes.find((n) => n.name === selected) ?? null
  const selectedMember = members.find((m) => m.id === selectedMemberId) ?? null
  const presentCategories = useMemo(
    () => (["colony", "warren", "service", "provider", "mcp"] as ClusterCategory[]).filter((c) => members.some((m) => m.category === c)),
    [members],
  )

  const memberOpener = useCallback(
    (m: ClusterMember): { label: string; run: () => void } | null => {
      switch (m.category) {
        case "colony":
          return onOpenColony ? { label: "open Colony", run: onOpenColony } : null
        case "warren":
          return onOpenWarren ? { label: "open Warren", run: onOpenWarren } : null
        case "mcp":
          return onOpenMcpServer ? { label: "open MCP server", run: () => onOpenMcpServer(m.label) } : null
        case "service":
        case "provider":
          return onOpenSpecialists ? { label: "open Specialists", run: onOpenSpecialists } : null
        default:
          return null
      }
    },
    [onOpenColony, onOpenWarren, onOpenMcpServer, onOpenSpecialists],
  )

  if (!activeConnectionId) {
    return <div className="grid h-full place-items-center text-[12px] text-chrome-text/55">No connection.</div>
  }

  return (
    <div className="flex h-full flex-col bg-doc-bg text-[12px] text-chrome-text">
      {/* header */}
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-chrome-border px-3">
        <Anchor className="h-4 w-4 text-main" />
        <span className="font-semibold text-foreground">Fleet</span>
        <span className="text-[10px] text-chrome-text/45">
          one brain, many muscles — everything attached to this warehouse, in one map
        </span>
        <div className="flex-1" />
        {nodes.length > 0 ? (
          <button
            type="button"
            onClick={() => nodes.filter((n) => n.enabled).forEach((n) => void runProbe(n.name))}
            className="inline-flex items-center gap-1 rounded border border-main/50 px-2 py-0.5 text-[10px] text-main hover:bg-main/10"
          >
            <Zap className="h-3 w-3" /> probe all
          </button>
        ) : null}
      </div>

      {/* ── topology ── */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" preserveAspectRatio="xMidYMid meet">
          {/* fleet edges — width carries workload share: thick wires are busy wires */}
          {placed.map(({ node, x, y, share }) => {
            const path = edgePath(bx + 62, by, x - 26, y)
            const active = node.enabled && node.last_probe_ok === true
            return (
              <g key={`e-${node.name}`}>
                <path
                  d={path}
                  fill="none"
                  stroke={healthColor(node)}
                  strokeOpacity={active ? 0.45 + Math.min(share, 0.5) * 0.8 : 0.22}
                  strokeWidth={active ? 1.2 + share * 6 : 1}
                  strokeDasharray={node.enabled ? undefined : "4 4"}
                />
                {node.last_probe_ms != null ? (
                  <text
                    x={(bx + x) / 2}
                    y={(by + y) / 2 - 6}
                    textAnchor="middle"
                    className="fill-current"
                    style={{ fill: "var(--chrome-text)", opacity: 0.55, fontSize: 10, fontFamily: "var(--font-mono, monospace)" }}
                  >
                    {Math.round(node.last_probe_ms * 10) / 10}ms
                  </text>
                ) : null}
                {/* probe pulse: a packet travelling the wire */}
                {pulse?.name === node.name ? (
                  <circle key={pulse.nonce} r={4} fill="var(--main)">
                    <animateMotion dur="0.7s" repeatCount="1" path={path} />
                  </circle>
                ) : null}
              </g>
            )
          })}

          {/* member edges — dashed conceptual wires; recency = glow */}
          {members.map((m) => {
            const p = layout.get(m.id)
            if (!p) return null
            const color = CATEGORY_COLOR[m.category]
            return (
              <path
                key={`me-${m.id}`}
                d={edgePath(bx + 62, by, p.x - 22, p.y)}
                fill="none"
                stroke={color}
                strokeOpacity={m.health === "down" ? 0.15 : recencyOpacity(m.lastActivityMs)}
                strokeWidth={1.1 + Math.min(m.calls > 0 ? Math.log10(1 + m.calls) : 0, 6) * 0.45}
                strokeDasharray="3 4"
              />
            )
          })}

          {/* brain */}
          <g>
            <rect x={bx - 62} y={by - 34} width={124} height={68} rx={12} fill="var(--chrome-bg)" stroke="var(--main)" strokeOpacity={0.7} strokeWidth={1.5} />
            <text x={bx} y={by - 6} textAnchor="middle" style={{ fill: "var(--foreground)", fontSize: 13, fontWeight: 600 }}>
              brain
            </text>
            <text x={bx} y={by + 12} textAnchor="middle" style={{ fill: "var(--chrome-text)", opacity: 0.7, fontSize: 10, fontFamily: "var(--font-mono, monospace)" }}>
              {brain}
            </text>
            <circle cx={bx - 48} cy={by - 20} r={4} fill="var(--success, #3fb950)">
              <animate attributeName="opacity" values="1;0.4;1" dur="2.4s" repeatCount="indefinite" />
            </circle>
            {brainAct && brainAct.executions > 0 ? (
              <>
                <text x={bx} y={by + 48} textAnchor="middle" style={{ fill: "var(--main)", opacity: 0.75, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                  {brainAct.executions} q · {Math.round(brainAct.medianMs)}ms · {ACTIVITY_HOURS}h
                </text>
                <g transform={`translate(${bx}, ${by + 53})`}>
                  <SparkBars buckets={brainAct.buckets} color="var(--main)" />
                </g>
              </>
            ) : null}
          </g>

          {/* fleet muscles — radius carries workload share; spark-bars its shape over time */}
          {placed.map(({ node, x, y, act, share }) => {
            const c = healthColor(node)
            const isSel = selected === node.name
            const r = 18 + Math.min(share, 0.75) * 20
            return (
              <g
                key={node.name}
                transform={`translate(${x}, ${y})`}
                style={{ cursor: "pointer" }}
                onClick={() => {
                  setSelected(node.name)
                  setSelectedMemberId(null)
                  void runProbe(node.name)
                }}
              >
                <circle r={r} fill="var(--chrome-bg)" stroke={c} strokeWidth={isSel ? 2.5 : 1.5} strokeOpacity={node.enabled ? 0.9 : 0.4} />
                <circle r={5} fill={c} opacity={node.enabled ? 1 : 0.4}>
                  {node.enabled && node.last_probe_ok ? (
                    <animate attributeName="r" values="5;7;5" dur="2s" repeatCount="indefinite" />
                  ) : null}
                </circle>
                {probing === node.name ? (
                  <circle r={r + 6} fill="none" stroke={c} strokeWidth={1} opacity={0.6}>
                    <animate attributeName="r" values={`${r + 2};${r + 12}`} dur="0.7s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.6;0" dur="0.7s" repeatCount="indefinite" />
                  </circle>
                ) : null}
                <text y={r + 15} textAnchor="middle" style={{ fill: "var(--foreground)", fontSize: 11, fontWeight: 500 }}>
                  {node.name}
                </text>
                <text y={r + 27} textAnchor="middle" style={{ fill: "var(--chrome-text)", opacity: 0.5, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                  {node.endpoint}
                </text>
                {act && act.executions > 0 ? (
                  <>
                    <text y={r + 39} textAnchor="middle" style={{ fill: c, opacity: 0.75, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                      {act.executions} q · {Math.round(act.medianMs)}ms · {ACTIVITY_HOURS}h
                    </text>
                    <g transform={`translate(0, ${r + 44})`}>
                      <SparkBars buckets={act.buckets} color={c} />
                    </g>
                  </>
                ) : null}
              </g>
            )
          })}

          {/* cluster members — colony peers, warrens, services, providers, MCP */}
          {members.map((m) => {
            const p = layout.get(m.id)
            if (!p) return null
            const color = CATEGORY_COLOR[m.category]
            const r = memberRadius(m.calls)
            const isSel = selectedMemberId === m.id
            const dim = m.health === "down"
            return (
              <g
                key={m.id}
                transform={`translate(${p.x}, ${p.y})`}
                style={{ cursor: "pointer" }}
                onClick={() => {
                  setSelectedMemberId(m.id)
                  setSelected(null)
                }}
              >
                <circle
                  r={r}
                  fill="var(--chrome-bg)"
                  stroke={color}
                  strokeWidth={isSel ? 2.5 : 1.5}
                  strokeOpacity={dim ? 0.35 : 0.85}
                  strokeDasharray={m.category === "colony" ? undefined : "5 3"}
                />
                <circle r={4.5} fill={memberHealthColor(m)} opacity={dim ? 0.5 : m.health === "unknown" ? 0.45 : 1}>
                  {m.health === "ok" ? <animate attributeName="r" values="4.5;6;4.5" dur="2.6s" repeatCount="indefinite" /> : null}
                </circle>
                <text y={r + 14} textAnchor="middle" style={{ fill: "var(--foreground)", fontSize: 11, fontWeight: 500 }}>
                  {m.label}
                </text>
                <text y={r + 25} textAnchor="middle" style={{ fill: color, opacity: 0.8, fontSize: 8.5, letterSpacing: 0.4 }}>
                  {CATEGORY_LABEL[m.category].toUpperCase()}
                </text>
                <text y={r + 36} textAnchor="middle" style={{ fill: "var(--chrome-text)", opacity: 0.55, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                  {m.skills.length > 0 ? `${m.skills.length} skill${m.skills.length === 1 ? "" : "s"}` : ""}
                  {m.skills.length > 0 && m.calls > 0 ? " · " : ""}
                  {m.calls > 0 ? `${fmtCalls(m.calls)} calls` : ""}
                </text>
              </g>
            )
          })}

          {/* add-node ghost — parked top-right, out of the physics */}
          <g
            transform={`translate(${W - 64}, 52)`}
            style={{ cursor: "pointer" }}
            onClick={() => setAdding(true)}
          >
            <circle r={18} fill="none" stroke="var(--chrome-text)" strokeOpacity={0.35} strokeDasharray="3 3" />
            <text y={4} textAnchor="middle" style={{ fill: "var(--chrome-text)", opacity: 0.5, fontSize: 16 }}>+</text>
          </g>

          {/* the hare: serverless — no burrow, no registry row, nothing to
              probe. Drawn as a ghost that only half-exists (scale-to-zero
              is the whole point); the edge carries the last invocation's
              wall-clock. Summoned via rvbbit.hare_run(sql). */}
          {hare?.endpoint && harePos ? (
            <>
              <path
                d={edgePath(bx + 62, by, harePos.x - 22, harePos.y)}
                fill="none"
                stroke="var(--main)"
                strokeOpacity={hareAct ? 0.3 + Math.min(hareAct.executions / totalExec, 0.5) * 0.8 : 0.3}
                strokeWidth={hareAct ? 1.2 + (hareAct.executions / totalExec) * 6 : 1.2}
                strokeDasharray="2 5"
              />
              {hareLastMs != null ? (
                <text
                  x={(bx + 62 + harePos.x) / 2}
                  y={(by + harePos.y) / 2 - 8}
                  textAnchor="middle"
                  style={{ fill: "var(--chrome-text)", opacity: 0.55, fontSize: 10, fontFamily: "var(--font-mono, monospace)" }}
                >
                  {Math.round(hareLastMs)}ms
                </text>
              ) : null}
              <g transform={`translate(${harePos.x}, ${harePos.y})`}>
                <circle
                  r={18 + Math.min(hareAct ? hareAct.executions / totalExec : 0, 0.75) * 20}
                  fill="none"
                  stroke="var(--main)"
                  strokeWidth={1.4}
                  strokeDasharray="4 4"
                  strokeOpacity={0.7}
                >
                  <animate attributeName="stroke-opacity" values="0.7;0.25;0.7" dur="3.2s" repeatCount="indefinite" />
                </circle>
                <text y={4} textAnchor="middle" style={{ fill: "var(--main)", fontSize: 12, opacity: 0.85 }}>⌁</text>
                <text y={35} textAnchor="middle" style={{ fill: "var(--foreground)", fontSize: 11, fontWeight: 500 }}>
                  hare
                </text>
                <text y={47} textAnchor="middle" style={{ fill: "var(--chrome-text)", opacity: 0.5, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                  serverless · scale-to-zero
                </text>
                {hareAct && hareAct.executions > 0 ? (
                  <>
                    <text y={59} textAnchor="middle" style={{ fill: "var(--main)", opacity: 0.75, fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}>
                      {hareAct.executions} q · {Math.round(hareAct.medianMs)}ms · {ACTIVITY_HOURS}h
                    </text>
                    <g transform="translate(0, 64)">
                      <SparkBars buckets={hareAct.buckets} color="var(--main)" />
                    </g>
                  </>
                ) : null}
              </g>
            </>
          ) : null}
        </svg>

        {/* legend — one dot per member kind actually present */}
        {presentCategories.length > 0 || nodes.length > 0 ? (
          <div className="pointer-events-none absolute bottom-2 left-3 flex items-center gap-3 text-[9px] text-chrome-text/55">
            {nodes.length > 0 ? (
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: "var(--success, #3fb950)" }} />
                worker
              </span>
            ) : null}
            {presentCategories.map((c) => (
              <span key={c} className="inline-flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: CATEGORY_COLOR[c] }} />
                {CATEGORY_LABEL[c]}
              </span>
            ))}
          </div>
        ) : null}

        {!registry ? (
          <div className="pointer-events-none absolute left-3 top-2 rounded border border-chrome-border/60 bg-chrome-bg/80 px-2 py-1 text-[9px] text-chrome-text/60">
            no read-fleet registry (pre-0136) — workers unavailable; the rest of the cluster still maps
          </div>
        ) : null}

        {/* selected fleet-node card */}
        {selectedNode ? (
          <div className="absolute right-3 top-3 w-64 rounded-md border border-chrome-border bg-chrome-bg/95 p-2.5 shadow-xl backdrop-blur">
            <div className="mb-1 flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ background: healthColor(selectedNode) }} />
              <span className="min-w-0 flex-1 truncate font-medium text-foreground">{selectedNode.name}</span>
              <button
                type="button"
                title={confirmRemove === selectedNode.name ? "Click again to remove from fleet" : "Remove from fleet"}
                onClick={() => {
                  if (confirmRemove !== selectedNode.name) {
                    setConfirmRemove(selectedNode.name)
                    return
                  }
                  setConfirmRemove(null)
                  void removeNode(activeConnectionId, selectedNode.name).then((r) => {
                    log(`rvbbit.fleet_remove('${selectedNode.name}') → ${r.ok ? "ok" : r.error}`, r.ok)
                    setSelected(null)
                    void refresh()
                  })
                }}
                className={cn(
                  "inline-flex items-center gap-1",
                  confirmRemove === selectedNode.name
                    ? "rounded border border-danger/60 bg-danger/10 px-1.5 py-0.5 text-[9px] text-danger"
                    : "text-chrome-text/50 hover:text-danger",
                )}
              >
                <Trash2 className="h-3 w-3" />
                {confirmRemove === selectedNode.name ? "remove?" : null}
              </button>
            </div>
            <div className="space-y-0.5 font-mono text-[10px] text-chrome-text/70">
              <div>{selectedNode.endpoint} · {selectedNode.engine}</div>
              <div>
                probe: {selectedNode.last_probe_ok == null ? "never" : selectedNode.last_probe_ok ? "ok" : "failed"}
                {selectedNode.last_probe_ms != null ? ` · ${Math.round(selectedNode.last_probe_ms * 10) / 10}ms` : ""}
              </div>
              {selectedNode.last_probe_error ? (
                <div className="whitespace-pre-wrap text-danger/80">{selectedNode.last_probe_error.slice(0, 160)}</div>
              ) : null}
            </div>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => void runProbe(selectedNode.name)}
                disabled={probing != null}
                className="inline-flex items-center gap-1 rounded border border-main/50 px-2 py-0.5 text-[10px] text-main hover:bg-main/10 disabled:opacity-50"
              >
                {probing === selectedNode.name ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                probe
              </button>
              <button
                type="button"
                onClick={() => {
                  void setNodeEnabled(activeConnectionId, selectedNode.name, !selectedNode.enabled).then((r) => {
                    log(`rvbbit.fleet_set_enabled('${selectedNode.name}', ${!selectedNode.enabled}) → ${r.ok ? "ok" : r.error}`, r.ok)
                    void refresh()
                  })
                }}
                className="rounded border border-chrome-border px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06]"
              >
                {selectedNode.enabled ? "disable" : "enable"}
              </button>
            </div>
          </div>
        ) : selectedMember ? (
          /* selected cluster-member card: who it is, what skills it brings */
          <div className="absolute right-3 top-3 w-72 rounded-md border border-chrome-border bg-chrome-bg/95 p-2.5 shadow-xl backdrop-blur">
            <div className="mb-1 flex items-center gap-2">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: memberHealthColor(selectedMember) }} />
              <span className="min-w-0 flex-1 truncate font-medium text-foreground">{selectedMember.label}</span>
              <span
                className="shrink-0 rounded-full px-1.5 py-px text-[8.5px] uppercase tracking-wider"
                style={{
                  color: CATEGORY_COLOR[selectedMember.category],
                  background: `color-mix(in oklab, ${CATEGORY_COLOR[selectedMember.category]} 14%, transparent)`,
                }}
              >
                {CATEGORY_LABEL[selectedMember.category]}
              </span>
            </div>
            <div className="space-y-0.5 font-mono text-[10px] text-chrome-text/70">
              <div>{selectedMember.sublabel}</div>
              {selectedMember.lastActivityMs != null ? (
                <div>last activity {fmtAgo(selectedMember.lastActivityMs)}</div>
              ) : null}
              {selectedMember.meta.map(([k, v]) => (
                <div key={k} className="truncate" title={v}>
                  {k}: {v}
                </div>
              ))}
            </div>
            {selectedMember.skills.length > 0 ? (
              <div className="mt-2 max-h-44 space-y-0.5 overflow-auto pr-1">
                {selectedMember.skills.map((s) => (
                  <div key={s.name} className="flex items-center gap-1.5 text-[10px]">
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{
                        background: s.status ? SKILL_DOT[s.status] : CATEGORY_COLOR[selectedMember.category],
                        opacity: s.status ? 1 : 0.45,
                      }}
                      title={s.status ?? undefined}
                    />
                    <span className="min-w-0 flex-1 truncate font-mono text-foreground/90" title={`${s.name} · ${s.info}`}>
                      {s.name}
                    </span>
                    <span className="shrink-0 font-mono text-[9px] text-chrome-text/50">
                      {s.status ?? ""}
                      {s.status && (s.calls > 0 || s.p50Ms != null) ? " · " : ""}
                      {s.calls > 0 ? `${fmtCalls(s.calls)}` : ""}
                      {s.calls > 0 && s.p50Ms != null ? ` · p50 ${s.p50Ms}ms` : ""}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
            {(() => {
              const opener = memberOpener(selectedMember)
              return opener ? (
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={opener.run}
                    className="rounded border border-main/50 px-2 py-0.5 text-[10px] text-main hover:bg-main/10"
                  >
                    {opener.label}
                  </button>
                </div>
              ) : null
            })()}
          </div>
        ) : null}

        {/* add-node form */}
        {adding ? (
          <div className="absolute right-3 bottom-3 w-64 space-y-1.5 rounded-md border border-chrome-border bg-chrome-bg/95 p-2.5 shadow-xl backdrop-blur">
            <div className="text-[11px] font-medium text-foreground">Add fleet worker</div>
            <input
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              placeholder="name (e.g. cpu-1)"
              className="w-full rounded border border-chrome-border bg-background px-1.5 py-1 text-[11px] outline-none focus:border-main/50"
            />
            <input
              value={addEndpoint}
              onChange={(e) => setAddEndpoint(e.target.value)}
              placeholder="host:port (rvbbit-duck --serve-tcp)"
              className="w-full rounded border border-chrome-border bg-background px-1.5 py-1 font-mono text-[11px] outline-none focus:border-main/50"
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void submitAdd()}
                disabled={!addName.trim() || !addEndpoint.trim()}
                className="inline-flex items-center gap-1 rounded border border-main/50 px-2 py-0.5 text-[10px] text-main hover:bg-main/10 disabled:opacity-50"
              >
                <Plus className="h-3 w-3" /> add
              </button>
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="rounded border border-chrome-border px-2 py-0.5 text-[10px] text-chrome-text"
              >
                cancel
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {/* ── pond + storage + hares ── */}
      <div className="grid shrink-0 grid-cols-3 gap-2 border-t border-chrome-border p-2" style={{ minHeight: 132 }}>
        <div className="min-w-0 rounded-md border border-chrome-border/60 bg-chrome-bg/30 p-2">
          <div className="mb-1.5 flex items-baseline gap-2">
            <span className="text-[11px] font-medium text-foreground">Pond</span>
            <span className="text-[9px] uppercase tracking-wider text-chrome-text/40">published artifacts · water level per table</span>
          </div>
          <div className="max-h-24 space-y-1 overflow-auto pr-1">
            {pond.length === 0 ? (
              <div className="text-[10px] text-chrome-text/40">No accelerated tables.</div>
            ) : (
              pond.map((t) => {
                const pubPct = t.row_groups > 0 ? (t.published / t.row_groups) * 100 : 0
                const evPct = t.row_groups > 0 ? (t.evicted / t.row_groups) * 100 : 0
                return (
                  <div key={t.table_name} className="flex items-center gap-2">
                    <span className="w-36 truncate font-mono text-[10px] text-chrome-text/75" title={t.table_name}>
                      {t.table_name}
                    </span>
                    <div className="relative h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-foreground/[0.07]">
                      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pubPct}%`, background: "color-mix(in oklab, var(--main) 55%, transparent)" }} />
                      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${evPct}%`, background: "var(--main)" }} title="evicted (remote-only)" />
                    </div>
                    <span className="shrink-0 font-mono text-[9px] text-chrome-text/50">
                      {t.published}/{t.row_groups}{t.evicted > 0 ? ` · ${t.evicted} evicted` : ""} · gen {t.local_generation}
                    </span>
                  </div>
                )
              })
            )}
          </div>
        </div>

        <div className="min-w-0 rounded-md border border-chrome-border/60 bg-chrome-bg/30 p-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-[11px] font-medium text-foreground">Storage</span>
            {store ? (
              <span className={cn("rounded-full px-1.5 py-px text-[9px]", store.enabled ? "bg-success/15 text-success" : "bg-foreground/[0.08] text-chrome-text/60")}>
                {store.enabled ? "enabled" : "disabled"}
              </span>
            ) : (
              <span className="rounded-full bg-warning/15 px-1.5 py-px text-[9px] text-warning">not configured</span>
            )}
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => void runDoctor()}
              disabled={doctorBusy || !store?.enabled}
              className="inline-flex items-center gap-1 rounded border border-main/50 px-2 py-0.5 text-[10px] text-main hover:bg-main/10 disabled:opacity-50"
            >
              {doctorBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
              doctor
            </button>
          </div>
          {store ? (
            <div className="truncate font-mono text-[10px] text-chrome-text/70" title={store.url_prefix}>{store.url_prefix}</div>
          ) : (
            <div className="font-mono text-[10px] text-chrome-text/50">SELECT rvbbit.set_publish_store(&apos;s3://bucket/prefix&apos;)</div>
          )}
          {doctor?.ok != null ? (
            <div className="mt-1.5 space-y-1">
              {(["put", "head", "delete"] as const).map((op) => {
                const ms = doctor[`${op}_ms` as const] ?? 0
                const w = Math.min(100, (ms / 400) * 100)
                return (
                  <div key={op} className="flex items-center gap-2">
                    <span className="w-10 text-right font-mono text-[9px] text-chrome-text/50">{op}</span>
                    <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-foreground/[0.07]">
                      <div className="h-full rounded-full bg-main" style={{ width: `${Math.max(w, 2)}%` }} />
                    </div>
                    <span className="w-14 shrink-0 font-mono text-[9px] text-chrome-text/55">{ms}ms</span>
                  </div>
                )
              })}
            </div>
          ) : doctor && !doctor.ok ? (
            <div className="mt-1.5 whitespace-pre-wrap text-[10px] text-danger/80">{doctor.error ?? doctor.hint}</div>
          ) : null}
        </div>

        {/* Hares: the invocation ledger. Each bar decomposes a capsule
            round trip into engine (the query itself), fetch (the hare's
            handling: views + artifact GETs), and wire (network/platform/
            cold start) — the "does the query eat the tax?" picture. */}
        <div className="min-w-0 rounded-md border border-chrome-border/60 bg-chrome-bg/30 p-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-[11px] font-medium text-foreground">Hares</span>
            {hare?.endpoint ? (
              <span className="min-w-0 flex-1 truncate font-mono text-[9px] text-chrome-text/50" title={hare.endpoint}>
                {hare.endpoint.replace(/^https?:\/\//, "")}
              </span>
            ) : (
              <span className="rounded-full bg-foreground/[0.08] px-1.5 py-px text-[9px] text-chrome-text/60">
                not configured
              </span>
            )}
          </div>
          {!hare?.available ? (
            <div className="font-mono text-[10px] text-chrome-text/50">
              Needs migration 0140 (rvbbit.hare_run + hare_invocations).
            </div>
          ) : hare.recent.length === 0 ? (
            <div className="font-mono text-[10px] text-chrome-text/50">
              SELECT rvbbit.hare_run(&apos;SELECT …&apos;) — capsules out, answers back, nothing left running.
            </div>
          ) : (
            <div className="max-h-24 space-y-1 overflow-auto pr-1">
              {(() => {
                const maxTotal = Math.max(...hare.recent.map((h) => h.total_ms ?? 0), 1)
                return hare.recent.map((h, i) => {
                  const total = h.total_ms ?? 0
                  const engine = Math.max(h.engine_ms ?? 0, 0)
                  const fetch_ = Math.max((h.server_ms ?? 0) - engine, 0)
                  const wire = Math.max(h.wire_ms ?? 0, 0)
                  const pct = (v: number) => `${(v / maxTotal) * 100}%`
                  return (
                    <div key={`${h.invoked_at}-${i}`} className="flex items-center gap-2" title={h.ok ? h.sql ?? "" : h.error ?? ""}>
                      <div className="relative h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-foreground/[0.07]">
                        {h.ok ? (
                          <div className="absolute inset-y-0 left-0 flex" style={{ width: pct(total) }}>
                            <div style={{ width: `${total ? (engine / total) * 100 : 0}%`, background: "var(--main)" }} />
                            <div style={{ width: `${total ? (fetch_ / total) * 100 : 0}%`, background: "color-mix(in oklab, var(--main) 45%, transparent)" }} />
                            <div style={{ width: `${total ? (wire / total) * 100 : 0}%`, background: "color-mix(in oklab, var(--main) 20%, transparent)" }} />
                          </div>
                        ) : (
                          <div className="absolute inset-y-0 left-0 rounded-full bg-danger/60" style={{ width: pct(Math.max(total, maxTotal * 0.08)) }} />
                        )}
                      </div>
                      <span className={cn("w-24 shrink-0 text-right font-mono text-[9px]", h.ok ? "text-chrome-text/55" : "text-danger/80")}>
                        {h.ok ? `${Math.round(total)}ms · ${h.row_count ?? 0} rows` : "error"}
                      </span>
                    </div>
                  )
                })
              })()}
              <div className="flex items-center gap-2 pt-0.5 font-mono text-[8px] text-chrome-text/40">
                <span className="inline-block h-1.5 w-3 rounded-sm" style={{ background: "var(--main)" }} /> engine
                <span className="inline-block h-1.5 w-3 rounded-sm" style={{ background: "color-mix(in oklab, var(--main) 45%, transparent)" }} /> fetch
                <span className="inline-block h-1.5 w-3 rounded-sm" style={{ background: "color-mix(in oklab, var(--main) 20%, transparent)" }} /> wire
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── console: the SQL this window is made of ── */}
      <div className="shrink-0 border-t border-chrome-border bg-chrome-bg/40 px-2 py-1">
        {console_.length === 0 ? (
          <div className="font-mono text-[9px] text-chrome-text/35">
            rvbbit.fleet + peer_backends + backends + warren_inventory + mcp_servers — polling every {POLL_MS / 1000}s · click anything to inspect it
          </div>
        ) : (
          console_.map((l) => (
            <div key={l.at} className={cn("truncate font-mono text-[9px]", l.ok ? "text-chrome-text/55" : "text-danger/75")}>
              {l.text}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
