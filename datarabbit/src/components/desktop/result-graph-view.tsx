"use client"

import { useMemo, useState } from "react"
import type { QueryResultColumn } from "@/lib/db/types"
import type { KgGraph, KgGraphEdge, KgGraphNode } from "@/lib/rvbbit/kg"
import { KnowledgeGraphCanvas } from "./kg-sigma-canvas"

/**
 * Canon entity-graph resultset shapes. A query whose columns match one of
 * these is offered as a graph instead of a grid — the same Sigma/graphology
 * canvas Scry and the KG explorer use, fed straight from result rows:
 *
 *   triples   — subject / predicate / object      (rvbbit.triples, data-KG)
 *   rebel     — head / relation|type / tail        (clover_relations)
 *   edge list — source / target [/ label|predicate|relation]
 *
 * Optional companions, matched by name: <endpoint>_type|<endpoint>_kind for
 * node coloring, and weight|score|confidence for edge weighting.
 */
export interface GraphResultShape {
  from: string
  to: string
  predicate: string | null
  weight: string | null
  fromKind: string | null
  toKind: string | null
}

const WEIGHT_COLS = ["weight", "score", "confidence"]

export function detectGraphResultShape(columns: Array<Pick<QueryResultColumn, "name">>): GraphResultShape | null {
  const byLower = new Map<string, string>()
  for (const c of columns) byLower.set(c.name.toLowerCase(), c.name)
  const pick = (...names: string[]) => {
    for (const n of names) {
      const hit = byLower.get(n)
      if (hit) return hit
    }
    return null
  }

  const endpoints: Array<{ from: string; to: string; predicate: string | null }> = []
  const subject = pick("subject")
  const object = pick("object")
  const sp = pick("predicate")
  if (subject && object && sp) endpoints.push({ from: subject, to: object, predicate: sp })
  const head = pick("head")
  const tail = pick("tail")
  const hp = pick("relation", "predicate", "type")
  if (head && tail && hp) endpoints.push({ from: head, to: tail, predicate: hp })
  const source = pick("source")
  const target = pick("target")
  if (source && target) endpoints.push({ from: source, to: target, predicate: pick("label", "predicate", "relation", "edge") })
  if (endpoints.length === 0) return null

  const e = endpoints[0]
  const kindFor = (endpoint: string) =>
    pick(`${endpoint.toLowerCase()}_type`, `${endpoint.toLowerCase()}_kind`)
  return {
    from: e.from,
    to: e.to,
    predicate: e.predicate,
    weight: pick(...WEIGHT_COLS),
    fromKind: kindFor(e.from),
    toKind: kindFor(e.to),
  }
}

const MAX_NODES = 1200
const MAX_EDGES = 3000

function scalarLabel(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === "string") return value.trim() || null
  if (typeof value === "number" || typeof value === "bigint" || typeof value === "boolean") return String(value)
  return null
}

function buildResultKgGraph(
  rows: Record<string, unknown>[],
  shape: GraphResultShape,
): { graph: KgGraph; truncated: boolean } {
  const nodes: KgGraphNode[] = []
  const idByLabel = new Map<string, number>()
  const degree = new Map<number, number>()
  const edges: KgGraphEdge[] = []
  let truncated = false

  // Edge weights normalized against the column max so arbitrary scales
  // (counts, scores, confidences) all land in the 0..1 range the canvas
  // expects.
  let maxWeight = 0
  if (shape.weight) {
    for (const r of rows) {
      const w = Number(r[shape.weight!])
      if (Number.isFinite(w) && w > maxWeight) maxWeight = w
    }
  }

  const nodeId = (label: string, kind: string): number | null => {
    const existing = idByLabel.get(label)
    if (existing != null) return existing
    if (nodes.length >= MAX_NODES) {
      truncated = true
      return null
    }
    const id = nodes.length + 1
    idByLabel.set(label, id)
    nodes.push({ nodeId: id, kind, label, confidence: null, depth: 1 })
    return id
  }

  for (const r of rows) {
    if (edges.length >= MAX_EDGES) {
      truncated = true
      break
    }
    const fromLabel = scalarLabel(r[shape.from])
    const toLabel = scalarLabel(r[shape.to])
    if (!fromLabel || !toLabel) continue
    const fromKind = scalarLabel(shape.fromKind ? r[shape.fromKind] : null) ?? "entity"
    const toKind = scalarLabel(shape.toKind ? r[shape.toKind] : null) ?? "entity"
    const from = nodeId(fromLabel, fromKind)
    const to = nodeId(toLabel, toKind)
    if (from == null || to == null) continue
    const rawWeight = shape.weight ? Number(r[shape.weight]) : NaN
    const score = maxWeight > 0 && Number.isFinite(rawWeight) ? Math.max(0.05, rawWeight / maxWeight) : 0.6
    edges.push({
      edgeId: edges.length + 1,
      fromNodeId: from,
      toNodeId: to,
      predicate: scalarLabel(shape.predicate ? r[shape.predicate] : null) ?? "related",
      direction: "out",
      confidence: null,
      score,
      depth: 1,
    })
    degree.set(from, (degree.get(from) ?? 0) + 1)
    degree.set(to, (degree.get(to) ?? 0) + 1)
  }

  // Anchor: the highest-degree node plays "seed" so the canvas has a center
  // of gravity for focus + labeling.
  let seed = nodes[0]
  for (const n of nodes) {
    if ((degree.get(n.nodeId) ?? 0) > (degree.get(seed?.nodeId ?? -1) ?? 0)) seed = n
  }
  if (seed) {
    seed.isSeed = true
    seed.depth = 0
  }

  return {
    graph: { seed: { kind: seed?.kind ?? "entity", label: seed?.label ?? "" }, nodes, edges },
    truncated,
  }
}

export function ResultGraphView({
  columns,
  rows,
}: {
  columns: QueryResultColumn[]
  rows: Record<string, unknown>[]
}) {
  const shape = useMemo(() => detectGraphResultShape(columns), [columns])
  const built = useMemo(() => (shape ? buildResultKgGraph(rows, shape) : null), [rows, shape])
  const [hoveredNodeId, setHoveredNodeId] = useState<number | null>(null)
  const [hoveredEdgeId, setHoveredEdgeId] = useState<number | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<number | null>(null)

  if (!built || built.graph.nodes.length === 0) {
    return (
      <div className="grid h-full place-items-center text-xs text-chrome-text/70">
        No graph rows — endpoints were empty or non-scalar.
      </div>
    )
  }

  return (
    <div className="relative h-full w-full overflow-hidden">
      <KnowledgeGraphCanvas
        graph={built.graph}
        mode="force"
        initialViewLayer="detail"
        initialLabelMode={built.graph.nodes.length <= 150 ? "dense" : "smart"}
        hoveredNodeId={hoveredNodeId}
        hoveredEdgeId={hoveredEdgeId}
        selectedEdgeId={selectedEdgeId}
        onHoverNode={setHoveredNodeId}
        onHoverEdge={setHoveredEdgeId}
        onClickNode={() => {}}
        onClickEdge={(e) => setSelectedEdgeId(e.edgeId)}
        onOpenNodeDetail={() => {}}
      />
      {built.truncated ? (
        <div className="pointer-events-none absolute bottom-3 right-3 rounded-md border border-amber-300/25 bg-black/55 px-2 py-1 text-[10px] text-amber-100/90 shadow-lg backdrop-blur">
          showing first {MAX_NODES} nodes / {MAX_EDGES} edges — filter the query to see the rest
        </div>
      ) : null}
    </div>
  )
}
