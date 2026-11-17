"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useVirtualizer } from "@tanstack/react-virtual"
import { ClipboardCopy, Eye, Flame, GripVertical, ImageIcon, Search, Table2, TreeStructure } from "@/lib/icons"
import { colorForVizKind } from "@/lib/desktop/viz-colors"
import type { QueryResultColumn } from "@/lib/db/types"
import type { DesktopColumnDragPayload, DesktopColumnRef, DesktopParamValue } from "@/lib/desktop/types"
import type { CrossFilter } from "@/lib/desktop/reactive-sql"
import { cn } from "@/lib/utils"
import { formatCellValue, formatIntervalValue, intervalComparableValue, intervalDisplayParts } from "@/lib/sql/format"
import { setActiveColumnDragSource, writeColumnDragPayload } from "@/lib/desktop/column-drag"
import { attachDragGhost } from "@/lib/desktop/drag-ghost"
import { usePresentMode } from "@/lib/desktop/present-mode"
import { ContextMenu, type ContextMenuState } from "./context-menu"

interface ColumnDragSource {
  parentWindowId: string
  parentBlockName: string
  parentTitle: string
  parentSql: string
  relationKey: string
  columns: DesktopColumnRef[]
}

interface ResultGridProps {
  /** Keep overlays inside an embedded host’s style boundary. Defaults to body. */
  portalContainer?: HTMLElement

  /** Host copy must distinguish searching loaded rows from querying a dataset. */
  filterLabel?: string
  /** Saved Stage results can omit row search while retaining grid controls. */
  showSearch?: boolean
  columns: QueryResultColumn[]
  rows: Record<string, unknown>[]
  className?: string
  /** Rotate the native grid so fields are vertical and result rows become columns. */
  transposed?: boolean
  onTransposedChange?: (next: boolean) => void
  /** When provided, headers become draggable and emit a column-drag payload. */
  columnDragSource?: ColumnDragSource | null
  /** When provided, clicking a cell emits a param. cascade=true (plain/⌘) is a
   *  cascading filter; cascade=false (⌥) is a "pick" — published but not
   *  self-filtering. */
  onEmitCellParam?: (
    field: string,
    value: unknown,
    dataTypeId: number,
    operator?: "eq" | "in",
    cascade?: boolean,
    /** The clicked column's real source table+column (pg provenance), so the
     *  emitted param can broadcast to other blocks that read the same table. */
    source?: { schema?: string; table?: string; column?: string },
  ) => void
  /** Params sourced from THIS block — used to highlight the cells that are the
   *  live filter (cascade) or a published pick value. */
  activeParams?: DesktopParamValue[]
  onOpenRow?: (input: {
    row: Record<string, unknown>
    rowIndex: number
    column: QueryResultColumn
  }) => void
  /** Extra entries appended to the cell context menu (e.g. semantic lineage). */
  extraCellMenuItems?: (input: {
    row: Record<string, unknown>
    rowIndex: number
    column: QueryResultColumn
    value: unknown
  }) => Array<{
    id: string
    label: string
    icon: React.ComponentType<{ className?: string }>
    disabled?: boolean
    onSelect: () => void
  }>
  /** Cross-filter: a plain cell click hands back the whole COLUMN (with its
   *  source-table provenance) + value, so a multi-statement dashboard can filter
   *  sibling tiles by the real table. Takes precedence over onEmitCellParam. */
  onCellFilter?: (column: QueryResultColumn, value: unknown) => void
  /** Columns produced by semantic operators — their cells grow a hover ✦ that
   *  opens the receipt/lineage trace directly (the context-menu path made
   *  discoverable). */
  receiptColumns?: Set<string>
  onOpenReceipt?: (input: {
    row: Record<string, unknown>
    rowIndex: number
    column: QueryResultColumn
    value: unknown
  }) => void
  /** Active cross-filters that ORIGINATED in this tile — highlight the cells whose
   *  value is selected (the tile isn't filtered by its own filter, so this is the
   *  visible "you clicked this" indicator, mirroring single-block param highlighting). */
  highlightFilters?: CrossFilter[]
  /** Opt-in spreadsheet editing (normal mode only; transposed stays read-only):
   *  double-click an editable cell to edit in place — Enter commits, Esc/blur
   *  cancels. The grid stays pure: the write path is whatever the host wires
   *  (a plate action, a table UPDATE, a built-not-run statement). */
  editable?: {
    /** Column names that accept edits. */
    columns: string[]
    /** Commit handler; resolve true to keep the value, false to revert. */
    onEdit: (input: {
      row: Record<string, unknown>
      rowIndex: number
      column: QueryResultColumn
      value: string
      previous: unknown
    }) => Promise<boolean> | boolean
  }
}

const ROW_HEIGHT = 24
const THUMB_ROW_HEIGHT = 44
const MIN_COL_WIDTH = 80
const MAX_AUTO_CONTENT_WIDTH = 360
const JSON_COL_WIDTH = 300
const JSON_INLINE_ENTRY_LIMIT = 3
const JSON_INLINE_STRING_LIMIT = 72
const JSON_PARSE_CHAR_LIMIT = 64_000
const TRANSPOSED_FIELD_KEY = "__rvbbit_transposed_field"
const TRANSPOSED_ROW_WIDTH = 152

// A cell is a renderable image when it's an http(s) URL with an image
// extension (query string allowed), an X/CDN-style ?format=jpg URL, or an
// inline data:image URI. Deliberately conservative — a false positive means a
// broken <img> flash before the text fallback.
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|avif|svg)(\?\S*)?$/i
const IMAGE_QUERY_RE = /[?&]format=(png|jpe?g|webp)(&|$)/i

function isImageUrl(value: unknown): value is string {
  if (typeof value !== "string") return false
  const s = value.trim()
  if (s.startsWith("data:image/")) return true
  if (s.length > 2048 || /\s/.test(s) || !/^https?:\/\//i.test(s)) return false
  return IMAGE_EXT_RE.test(s) || IMAGE_QUERY_RE.test(s)
}

// ── Alias-driven conditional formatting ─────────────────────────────────────
// A column aliased `x__heat` gets per-cell heat; `x__heatrow` also tints its
// whole row. Numeric columns get a magnitude scale (diverging around zero when
// the column spans it), everything else gets stable categorical bands. Colors
// are theme variables composed with color-mix — they follow wallpaper-derived
// themes and light/dark live, with zero resolution code. This is deliberately
// a CLOSED two-suffix vocabulary: a suffix says what a column IS, never how to
// draw it — anything needing parameters belongs in saved-view config, not
// column names. Outside Data Rabbit the alias is just a slightly odd name.
const HEAT_SUFFIX_RE = /__heat(row)?$/i

interface HeatSpec {
  /** Real result column name (params/drag/edit all still key on this). */
  column: string
  /** True for `__heatrow` — also paints the whole row. */
  row: boolean
  kind: "numeric" | "categorical"
  min: number
  max: number
  /** Numeric column spans zero → diverge --danger/--success around it. */
  divergent: boolean
  /** Small categorical sets: value → viz-kind slot, spread evenly across the
   *  10-slot wheel so 3 distinct values land on well-separated colors instead
   *  of whatever adjacent shades the hash happens to pick. Absent (large or
   *  unbounded sets) → stable-hash fallback. */
  slots?: Map<string, number>
}

const HEAT_SPREAD_MAX_DISTINCT = 10

const HEAT_CELL_PCT = 38
const HEAT_BAND_PCT = 26
const HEAT_ROW_PCT = 10

// EXPERIMENTAL table calcs — the Tableau-adjacent end of the alias idea.
// Same doctrine (single argument-free words, computed over the LOADED rows,
// display-only): the raw value still drives params, filters, copy, and sort.
//   x__pct_of_ttl → show each value as % of the visible column total
//   x__delta_prev → show the change vs the previous VISIBLE row (sort-aware,
//                   like Tableau's "table (down)"); first row shows nothing.
const CALC_SUFFIX_RE = /__(pct_of_ttl|delta_prev)$/i

// `photo__box` IS a box on `photo`: {top,bottom,left,right} as fractions of
// the image, the shape rvbbit.locate() returns. It outlines that column's
// thumbnail in the same row; the box column still shows its own value.
const BOX_SUFFIX_RE = /__box$/i

interface ImageBox {
  top: number
  bottom: number
  left: number
  right: number
}

function parseImageBox(value: unknown): ImageBox | null {
  let raw = value
  if (typeof raw === "string") {
    try {
      raw = JSON.parse(raw)
    } catch {
      return null
    }
  }
  if (raw && typeof raw === "object" && "box" in raw) raw = (raw as { box: unknown }).box
  if (!raw || typeof raw !== "object") return null
  const { top, bottom, left, right } = raw as Record<string, unknown>
  const edges = [top, bottom, left, right]
  if (!edges.every((n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1)) return null
  const box = { top, bottom, left, right } as ImageBox
  return box.top <= box.bottom && box.left <= box.right ? box : null
}

function BoxOutline({ box, large = false }: { box: ImageBox; large?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "pointer-events-none absolute border-rvbbit-accent",
        large ? "rounded-[3px] border-[3px]" : "rounded-[2px] border-2",
      )}
      style={{
        left: `${box.left * 100}%`,
        top: `${box.top * 100}%`,
        width: `${(box.right - box.left) * 100}%`,
        height: `${(box.bottom - box.top) * 100}%`,
        boxShadow: "0 0 0 1px color-mix(in oklch, black 45%, transparent)",
      }}
    />
  )
}
const ANY_SUFFIX_RE = /__(heat(row)?|pct_of_ttl|delta_prev)$/i

function displayColumnName(name: string): string {
  return name.replace(ANY_SUFFIX_RE, "")
}

function isWebUrl(value: unknown): value is string {
  return typeof value === "string" && value.length <= 2048 && /^https?:\/\/\S+$/i.test(value.trim())
}

function fmtHeatNum(n: number): string {
  if (!Number.isFinite(n)) return String(n)
  return Math.abs(n) >= 100 ? n.toFixed(0) : String(Number(n.toPrecision(3)))
}

/** Header tooltip line explaining a heat column's scale. */
function heatHint(spec: HeatSpec): string {
  const rowNote = spec.row ? " · tints the whole row" : ""
  if (spec.kind === "categorical") {
    return spec.slots
      ? `heat: ${spec.slots.size} value bands${rowNote}`
      : `heat: value bands (hashed colors)${rowNote}`
  }
  if (spec.divergent) {
    return `heat: diverging around 0 (${fmtHeatNum(spec.min)} … ${fmtHeatNum(spec.max)})${rowNote}`
  }
  return `heat: ${fmtHeatNum(spec.min)} → ${fmtHeatNum(spec.max)}${rowNote}`
}

function buildHeatSpecs(columns: QueryResultColumn[], rows: Record<string, unknown>[]): Map<string, HeatSpec> {
  const specs = new Map<string, HeatSpec>()
  for (const c of columns) {
    const m = HEAT_SUFFIX_RE.exec(c.name)
    if (!m) continue
    let min = Infinity
    let max = -Infinity
    let seen = 0
    let numeric = true
    const distinct = new Set<string>()
    for (const r of rows) {
      const v = r?.[c.name]
      if (v == null || v === "") continue
      if (numeric) {
        const n = Number(v)
        if (Number.isFinite(n)) {
          seen += 1
          if (n < min) min = n
          if (n > max) max = n
        } else {
          numeric = false
        }
      }
      // Track distinct values (bounded) in the same pass — cheap, and only
      // consulted when the column turns out to be categorical.
      if (distinct.size <= HEAT_SPREAD_MAX_DISTINCT) distinct.add(String(v))
    }
    if (!numeric || seen === 0) {
      // Small sets: sort for run-to-run stability and spread across the wheel
      // (3 values → slots 1/4/7, not neighbors). Bigger sets keep the stable
      // hash so a value's color survives filtering and re-runs.
      let slots: Map<string, number> | undefined
      if (distinct.size > 0 && distinct.size <= HEAT_SPREAD_MAX_DISTINCT) {
        const ordered = [...distinct].sort((a, b) => a.localeCompare(b))
        slots = new Map(ordered.map((v, i) => [v, Math.floor((i * 10) / ordered.length) + 1]))
      }
      specs.set(c.name, { column: c.name, row: m[1] != null, kind: "categorical", min: 0, max: 0, divergent: false, slots })
    } else {
      specs.set(c.name, {
        column: c.name,
        row: m[1] != null,
        kind: "numeric",
        min,
        max,
        divergent: min < 0 && max > 0,
      })
    }
  }
  return specs
}

/** Theme-relative background for a heat cell/row, or null for no tint. */
function heatBackground(spec: HeatSpec, value: unknown, maxPct: number): string | null {
  if (value == null || value === "") return null
  if (spec.kind === "categorical") {
    const s = String(value)
    const slot = spec.slots?.get(s)
    const color = slot != null ? `var(--viz-kind-${slot})` : colorForVizKind(s)
    return `color-mix(in oklch, ${color} ${maxPct}%, transparent)`
  }
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  if (spec.divergent) {
    const mag = Math.max(Math.abs(spec.min), Math.abs(spec.max))
    if (mag <= 0) return null
    const t = Math.min(1, Math.abs(n) / mag)
    if (t < 0.02) return null
    const hue = n < 0 ? "var(--danger)" : "var(--success)"
    return `color-mix(in oklch, ${hue} ${(t * maxPct).toFixed(1)}%, transparent)`
  }
  if (spec.max <= spec.min) return null
  const t = (n - spec.min) / (spec.max - spec.min)
  return `color-mix(in oklch, var(--chart-3) ${(3 + t * (maxPct - 3)).toFixed(1)}%, transparent)`
}

export function ResultGrid({
  portalContainer,
  filterLabel = "filter rows…",
  showSearch = true,
  columns,
  rows,
  className,
  transposed = false,
  onTransposedChange,
  columnDragSource,
  onEmitCellParam,
  activeParams,
  onOpenRow,
  onCellFilter,
  highlightFilters,
  extraCellMenuItems,
  editable,
  receiptColumns,
  onOpenReceipt,
}: ResultGridProps) {
  const parentRef = useRef<HTMLDivElement>(null)
  const [colWidths, setColWidths] = useState<Record<string, number>>({})
  const [selectedHeaders, setSelectedHeaders] = useState<Set<string>>(new Set())
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  // ── Inline editing (spreadsheet feel) ────────────────────────────────
  // Keyed by SOURCE row index + column name, so filter/sort reshuffles
  // can't misdirect a commit. Overrides show the committed value until the
  // host refreshes `rows` (identity change clears them).
  const [editing, setEditing] = useState<{ rowIdx: number; col: string; draft: string } | null>(null)
  const [pendingCells, setPendingCells] = useState<Set<string>>(new Set())
  const [overrides, setOverrides] = useState<Map<string, unknown>>(new Map())
  useEffect(() => {
    setOverrides(new Map())
    setEditing(null)
  }, [rows])
  const editableCols = useMemo(
    () => new Set((editable?.columns ?? []).map((c) => c.toLowerCase())),
    [editable],
  )
  const canEdit = (name: string) => !!editable && !present && editableCols.has(name.toLowerCase())
  const commitEdit = async (row: Record<string, unknown>, rowIdx: number, column: QueryResultColumn, draft: string) => {
    const key = `${rowIdx}:${column.name}`
    const previous = row?.[column.name]
    setEditing(null)
    // No-op edits never hit the write path.
    if (draft === String(previous ?? "")) return
    setPendingCells((s) => new Set(s).add(key))
    let ok = false
    try {
      ok = await editable!.onEdit({ row, rowIndex: rowIdx, column, value: draft, previous })
    } catch {
      ok = false
    }
    setPendingCells((s) => {
      const next = new Set(s)
      next.delete(key)
      return next
    })
    if (ok) setOverrides((m) => new Map(m).set(key, draft))
  }
  // Image thumbnails: columns qualify when any sampled value looks like an
  // image URL. Default ON — the toggle only appears when there's something to
  // toggle, so it never adds chrome to plain-text grids.
  const [thumbs, setThumbs] = useState(true)
  const imageCols = useMemo(() => {
    const found = new Set<string>()
    const sample = rows.slice(0, 50)
    for (const c of columns) {
      if (sample.some((r) => isImageUrl(r?.[c.name]))) found.add(c.name)
    }
    return found
  }, [columns, rows])
  // Image column → its `__box` column, when the result has both.
  const boxCols = useMemo(() => {
    const names = new Set(columns.map((c) => c.name))
    const found = new Map<string, string>()
    for (const c of columns) {
      const target = c.name.replace(BOX_SUFFIX_RE, "")
      if (target !== c.name && names.has(target)) found.set(target, c.name)
    }
    return found
  }, [columns])
  const thumbsActive = thumbs && imageCols.size > 0 && !transposed
  const rowHeight = thumbsActive ? THUMB_ROW_HEIGHT : ROW_HEIGHT

  // JSON/jsonb gets a compact, bounded structural preview rather than a full
  // editor or expandable tree in every cell. Rows are virtualized, but normal
  // grid columns are not; keeping each preview to a handful of top-level nodes
  // prevents wide JSON-heavy results from multiplying thousands of DOM nodes.
  // Full values remain available through Open row / Copy cell.
  const [jsonTree, setJsonTree] = useState(true)
  const jsonCols = useMemo(
    () => new Set(columns.filter(isJsonColumn).map((c) => c.name)),
    [columns],
  )

  // Alias-driven heat (`__heat` / `__heatrow` columns) — default on, with an
  // off-switch that only appears when a heat column exists.
  const [heat, setHeat] = useState(true)
  const heatSpecs = useMemo(() => buildHeatSpecs(columns, rows), [columns, rows])
  const heatActive = heat && heatSpecs.size > 0 && !transposed
  const heatRowSpec = useMemo(() => {
    if (!heatActive) return null
    for (const c of columns) {
      const spec = heatSpecs.get(c.name)
      if (spec?.row) return spec
    }
    return null
  }, [columns, heatActive, heatSpecs])

  // Experimental table calcs (`__pct_of_ttl` / `__delta_prev`) — display-only
  // transforms over the VISIBLE rows, so they respond to filter and sort the
  // way Tableau table calcs do.
  const calcSpecs = useMemo(() => {
    const map = new Map<string, "pct" | "delta">()
    for (const c of columns) {
      const m = CALC_SUFFIX_RE.exec(c.name)
      if (m) map.set(c.name, m[1].toLowerCase() === "pct_of_ttl" ? "pct" : "delta")
    }
    return map
  }, [columns])

  // Client-side sort + filter over the already-fetched rows (no re-query).
  const [filter, setFilter] = useState("")
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" } | null>(null)
  const cycleSort = (name: string) =>
    setSort((s) => (!s || s.col !== name ? { col: name, dir: "asc" } : s.dir === "asc" ? { col: name, dir: "desc" } : null))
  // Present mode: cell-click filtering stays, but column drag-to-group, header
  // multi-select, and width-resize are authoring affordances — turn them off.
  const present = usePresentMode()
  const dragEnabled = !!columnDragSource && !present

  useEffect(() => {
    parentRef.current?.scrollTo({ left: 0, top: 0 })
  }, [transposed])

  // Per-column lookup of values that are an active param for this block, so the
  // matching cells can be highlighted (cascade = the live filter; otherwise a
  // published "pick"). One param per column, so a single entry per field.
  const paramHighlights = useMemo(() => {
    const map = new Map<string, { values: Set<string>; cascade: boolean }>()
    for (const p of activeParams ?? []) {
      const isPick = p.cascade === false
      // An eq cascade already narrows the grid to its single value, so
      // highlighting it is redundant noise — only highlight picks (which don't
      // narrow) and multi-value IN filters.
      if (!isPick && p.operator !== "in") continue
      const vals = (Array.isArray(p.value) ? p.value : [p.value]).map((v) => String(v))
      map.set(p.field, { values: new Set(vals), cascade: !isPick })
    }
    return map
  }, [activeParams])

  // Cross-filter highlights, keyed by grid-column NAME, matched to a filter by pg
  // provenance (the filter carries the REAL source column, the grid column may be
  // aliased). The originating tile isn't filtered by its own filter, so this marks
  // what was clicked — the visible selection that survives the re-run remount.
  const crossHighlights = useMemo(() => {
    const map = new Map<string, Set<string>>()
    if (!highlightFilters?.length) return map
    for (const c of columns) {
      const f = highlightFilters.find(
        (hf) => {
          if (!hf.sourceTable) return hf.column.toLowerCase() === c.name.toLowerCase()
          return (
            !!c.sourceColumn &&
            hf.column.toLowerCase() === c.sourceColumn.toLowerCase() &&
            (!c.sourceTable || hf.sourceTable.toLowerCase() === c.sourceTable.toLowerCase())
          )
        },
      )
      if (!f) continue
      const vals = (Array.isArray(f.value) ? f.value : [f.value]).map((v) => String(v))
      map.set(c.name, new Set(vals))
    }
    return map
  }, [highlightFilters, columns])

  function onHeaderDragStart(e: React.DragEvent<HTMLDivElement>, name: string) {
    if (!columnDragSource || present) return
    const sourceCol = columnDragSource.columns.find((c) => c.name === name)
    if (!sourceCol) return
    const multi = columnDragSource.columns.filter((c) => selectedHeaders.has(c.name))
    const cols = multi.some((c) => c.name === sourceCol.name) && multi.length > 0 ? multi : [sourceCol]
    const payload: DesktopColumnDragPayload = {
      kind: "rvbbit-lens.desktop.column",
      parentWindowId: columnDragSource.parentWindowId,
      parentBlockName: columnDragSource.parentBlockName,
      parentTitle: columnDragSource.parentTitle,
      parentSql: columnDragSource.parentSql,
      relationKey: columnDragSource.relationKey,
      columns: cols,
      // Full source column set so a multi-arg semantic bind can offer sibling
      // columns (e.g. contradicts(a, b) with b bound to another column).
      sourceColumns: columnDragSource.columns,
    }
    writeColumnDragPayload(e.dataTransfer, payload)
    setActiveColumnDragSource({
      parentWindowId: payload.parentWindowId,
      parentBlockName: payload.parentBlockName,
      relationKey: payload.relationKey,
      columns: cols,
    })
    const dimCount = cols.filter((c) => c.role === "dimension").length
    const metricCount = cols.length - dimCount
    const sublabel = cols.length > 1
      ? `${dimCount} dim · ${metricCount} metric`
      : (sourceCol.role === "metric" ? "metric" : "dimension")
    attachDragGhost(e.dataTransfer, {
      variant: "column",
      label: cols.length > 1 ? cols.map((c) => c.name).join(", ") : sourceCol.name,
      sublabel,
      count: cols.length,
    })
  }

  function onHeaderClick(e: React.MouseEvent<HTMLDivElement>, name: string) {
    if (!columnDragSource || present) return
    if (!e.metaKey && !e.ctrlKey && !e.shiftKey) {
      // Single click → toggle role hint? For now we just toggle selection.
      setSelectedHeaders((s) => {
        const next = new Set(s)
        if (next.has(name)) next.delete(name); else next.add(name)
        return next
      })
      return
    }
    setSelectedHeaders((s) => {
      const next = new Set(s)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }

  const columnWidths = useMemo(() => {
    return columns.map((c) => colWidths[c.name] ?? estimateWidth(c, rows))
  }, [colWidths, columns, rows])

  // Rows actually shown: filtered (any cell contains the text) then sorted.
  // In transposed mode, a field-name filter keeps all row columns visible and
  // narrows the vertical field list instead.
  const viewRows = useMemo(() => {
    let out = rows
    const f = filter.trim().toLowerCase()
    if (f) {
      const matchedFieldName = transposed && columns.some((c) => columnMatchesFilter(c, f))
      if (!matchedFieldName) {
        out = out.filter((r) => columns.some((c) => cellMatchesFilter(r[c.name], f, c)))
      }
    }
    if (sort) {
      const { col, dir } = sort
      const mul = dir === "asc" ? 1 : -1
      const sortColumn = columns.find((c) => c.name === col)
      out = [...out].sort((a, b) => {
        const av = a[col]
        const bv = b[col]
        if (av == null && bv == null) return 0
        if (av == null) return 1 // nulls last regardless of dir
        if (bv == null) return -1
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * mul
        if (sortColumn && isIntervalColumn(sortColumn)) {
          const an = intervalComparableValue(av)
          const bn = intervalComparableValue(bv)
          if (an != null && bn != null) return (an - bn) * mul
        }
        const as = sortColumn && isJsonColumn(sortColumn)
          ? boundedJsonText(av, 1_200)
          : sortColumn && isIntervalColumn(sortColumn)
            ? formatIntervalValue(av)
            : String(formatCellValue(av))
        const bs = sortColumn && isJsonColumn(sortColumn)
          ? boundedJsonText(bv, 1_200)
          : sortColumn && isIntervalColumn(sortColumn)
            ? formatIntervalValue(bv)
            : String(formatCellValue(bv))
        const an = Number(as)
        const bn = Number(bs)
        if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * mul
        return as.localeCompare(bs) * mul
      })
    }
    return out
  }, [rows, columns, filter, sort, transposed])

  const transposedColumns = useMemo(() => {
    if (!transposed) return columns
    const f = filter.trim().toLowerCase()
    if (!f) return columns
    return columns.filter((c) => columnMatchesFilter(c, f) || viewRows.some((r) => cellMatchesFilter(r[c.name], f, c)))
  }, [columns, filter, transposed, viewRows])

  // Column sums for __pct_of_ttl, over the rows currently shown.
  const calcTotals = useMemo(() => {
    const totals = new Map<string, number>()
    for (const [col, kind] of calcSpecs) {
      if (kind !== "pct") continue
      let sum = 0
      for (const r of viewRows) {
        const n = Number(r?.[col])
        if (Number.isFinite(n)) sum += n
      }
      totals.set(col, sum)
    }
    return totals
  }, [calcSpecs, viewRows])

  const fieldColumnWidth = colWidths[TRANSPOSED_FIELD_KEY] ?? estimateFieldWidth(columns)

  const virtualizer = useVirtualizer({
    count: transposed ? transposedColumns.length : viewRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 16,
  })

  // Row height flips when thumbnails toggle — remeasure or the virtual rows
  // overlap/gap.
  useEffect(() => {
    virtualizer.measure()
  }, [rowHeight, virtualizer])

  const rowColumnVirtualizer = useVirtualizer({
    horizontal: true,
    count: transposed ? viewRows.length : 0,
    getScrollElement: () => parentRef.current,
    estimateSize: () => TRANSPOSED_ROW_WIDTH,
    overscan: 8,
  })

  function startResize(name: string, e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startW = colWidths[name] ?? (name === TRANSPOSED_FIELD_KEY
      ? estimateFieldWidth(columns)
      : estimateWidth(columns.find((c) => c.name === name)!, rows))
    const minWidth = name === TRANSPOSED_FIELD_KEY
      ? estimateFieldWidth(columns)
      : minimumColumnWidth(columns.find((c) => c.name === name)!)
    function onMove(ev: PointerEvent) {
      const next = Math.max(minWidth, startW + (ev.clientX - startX))
      setColWidths((w) => ({ ...w, [name]: next }))
    }
    function onUp() {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
  }

  if (columns.length === 0) {
    return (
      <div className="grid h-full place-items-center text-xs text-chrome-text/70">
        No columns
      </div>
    )
  }

  const totalWidth = columnWidths.reduce((a, b) => a + b, 0)
  const transposedRowsWidth = rowColumnVirtualizer.getTotalSize()
  const transposedTotalWidth = fieldColumnWidth + transposedRowsWidth
  const rowVirtualItems = rowColumnVirtualizer.getVirtualItems()

  const countLabel = transposed
    ? (filter.trim() || sort
      ? `${transposedColumns.length} fields × ${viewRows.length} rows`
      : `${columns.length} fields × ${rows.length} rows`)
    : (filter.trim() || sort ? `${viewRows.length} of ${rows.length}` : `${rows.length} ${rows.length === 1 ? "row" : "rows"}`)

  function openCellContextMenu(
    e: React.MouseEvent<HTMLDivElement>,
    row: Record<string, unknown>,
    rowIndex: number,
    column: QueryResultColumn,
  ) {
    e.preventDefault()
    e.stopPropagation()
    const value = row?.[column.name]
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          id: "open-row",
          label: "Open row",
          icon: Eye,
          disabled: !onOpenRow,
          onSelect: () => onOpenRow?.({ row, rowIndex, column }),
        },
        {
          id: "copy-cell",
          label: "Copy cell",
          icon: ClipboardCopy,
          onSelect: () => void copyToClipboard(value == null ? "" : cellText(value)),
        },
        {
          id: "copy-row",
          label: "Copy row JSON",
          icon: Table2,
          onSelect: () => void copyToClipboard(stringifyJson(row)),
        },
        ...(extraCellMenuItems?.({ row, rowIndex, column, value }) ?? []),
      ],
    })
  }

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center gap-1.5 border-b border-chrome-border bg-chrome-bg/60 px-2 py-1">
        {showSearch && <><Search className="h-3 w-3 shrink-0 text-chrome-text/40" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label={filterLabel}
          placeholder={transposed ? "filter fields or values…" : filterLabel}
          spellCheck={false}
          className="h-5 min-w-0 flex-1 bg-transparent text-[11px] text-foreground outline-none placeholder:text-chrome-text/35"
        /></>}
        <span className="shrink-0 text-[10px] tabular-nums text-chrome-text/45">
          {countLabel}
        </span>
        {jsonCols.size > 0 && !present ? (
          <button
            type="button"
            aria-pressed={jsonTree}
            data-rvbbit-json-toggle
            onClick={() => setJsonTree((v) => !v)}
            title={jsonTree ? "Structured JSON previews on — click for compact raw JSON" : "Render JSON as compact structured previews"}
            className={cn(
              "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
              jsonTree
                ? "border-main/50 bg-main/15 text-foreground"
                : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground",
            )}
          >
            <TreeStructure className="h-3 w-3" />
          </button>
        ) : null}
        {heatSpecs.size > 0 && !transposed && !present ? (
          <button
            type="button"
            onClick={() => setHeat((v) => !v)}
            title={heat ? "Heat formatting on (__heat / __heatrow columns) — click to disable" : "Enable heat formatting"}
            className={cn(
              "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
              heat
                ? "border-main/50 bg-main/15 text-foreground"
                : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground",
            )}
          >
            <Flame className="h-3 w-3" />
          </button>
        ) : null}
        {imageCols.size > 0 && !transposed && !present ? (
          <button
            type="button"
            onClick={() => setThumbs((v) => !v)}
            title={thumbs ? "Showing image thumbnails — click for text" : "Render image URLs as thumbnails"}
            className={cn(
              "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
              thumbs
                ? "border-main/50 bg-main/15 text-foreground"
                : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground",
            )}
          >
            <ImageIcon className="h-3 w-3" />
          </button>
        ) : null}
        {onTransposedChange && !present ? (
          <button
            type="button"
            role="switch"
            aria-checked={transposed}
            aria-label="Transpose"
            onClick={() => onTransposedChange(!transposed)}
            title="Swap rows and columns; show field names down the left"
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded px-1 py-0.5 text-[10px] transition-colors",
              transposed ? "text-foreground" : "text-chrome-text/55 hover:text-foreground",
            )}
          >
            <span aria-hidden="true" className={cn("relative h-3 w-5 shrink-0 rounded-full border transition-colors",
              transposed ? "border-main/60 bg-main/30" : "border-chrome-border bg-chrome-text/10")}>
              <span className={cn("absolute left-0 top-px h-2 w-2 rounded-full transition-transform",
                transposed ? "translate-x-[calc(0.75rem-3px)] bg-main" : "translate-x-px bg-chrome-text/55")} />
            </span>
            Transpose
          </button>
        ) : null}
      </div>
      <div
        ref={parentRef}
        className="result-grid-scrollport flex flex-1 flex-col overflow-auto bg-doc-bg group-data-[focused=false]/window:bg-doc-bg/70"
      >
      {transposed ? (
        <>
      <div className="sticky top-0 z-20 flex shrink-0 border-b border-chrome-border bg-chrome-bg/95 backdrop-blur" style={{ minWidth: transposedTotalWidth }}>
        <div
          className="sticky left-0 z-30 flex items-center border-r border-chrome-border/70 bg-chrome-bg/95 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text"
          style={{ width: fieldColumnWidth }}
        >
          <span className="truncate text-foreground">Field</span>
          {present ? null : (
            <div
              onPointerDown={(e) => startResize(TRANSPOSED_FIELD_KEY, e)}
              className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-main/50"
            />
          )}
        </div>
        <div className="relative h-full" style={{ width: transposedRowsWidth }}>
          {rowVirtualItems.map((vcol) => {
            const row = viewRows[vcol.index]
            const sourceIndex = rows.indexOf(row)
            return (
              <div
                key={vcol.key}
                className="absolute top-0 flex h-full items-center border-r border-chrome-border/60 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text"
                style={{ transform: `translateX(${vcol.start}px)`, width: vcol.size }}
                title={`Result row ${sourceIndex >= 0 ? sourceIndex + 1 : vcol.index + 1}`}
              >
                <span className="truncate text-foreground">row {sourceIndex >= 0 ? sourceIndex + 1 : vcol.index + 1}</span>
              </div>
            )
          })}
        </div>
      </div>

      <div style={{ height: virtualizer.getTotalSize(), minWidth: transposedTotalWidth }} className="relative shrink-0">
        {virtualizer.getVirtualItems().map((vrow) => {
          const c = transposedColumns[vrow.index]
          if (!c) return null
          const role = columnDragSource?.columns.find((col) => col.name === c.name)?.role
          const isSelected = selectedHeaders.has(c.name)
          const isNumeric = isNumericType(c)
          return (
            <div
              key={vrow.key}
              className={cn(
                "absolute left-0 right-0 flex border-b border-chrome-border/30",
                vrow.index % 2 === 0 ? "bg-transparent" : "bg-foreground/[0.025]",
              )}
              style={{ transform: `translateY(${vrow.start}px)`, height: ROW_HEIGHT, minWidth: transposedTotalWidth }}
            >
              <div
                draggable={dragEnabled}
                onDragStart={(e) => onHeaderDragStart(e, c.name)}
                onDragEnd={() => setActiveColumnDragSource(null)}
                onClick={(e) => onHeaderClick(e, c.name)}
                className={cn(
                  "group sticky left-0 z-10 flex select-none items-center border-r border-chrome-border/70 bg-doc-bg px-2 py-0.5 text-[12px] group-data-[focused=false]/window:bg-doc-bg/90",
                  dragEnabled && "cursor-grab active:cursor-grabbing hover:bg-foreground/[0.05]",
                  isSelected && "bg-main/15 text-foreground",
                )}
                style={{ width: fieldColumnWidth }}
                title={dragEnabled ? "Drag onto canvas to GROUP BY this field. Cmd/Ctrl-click to multi-select." : undefined}
              >
                {dragEnabled ? (
                  <GripVertical className={cn("mr-1 h-3 w-3 shrink-0", role === "metric" ? "text-chart-3" : "text-main/70")} />
                ) : null}
                <span className="min-w-0 flex-1 truncate text-foreground">{displayColumnName(c.name)}</span>
                <span className="ml-2 truncate text-[10px] text-chrome-text/70">
                  {c.dataTypeName ?? `oid:${c.dataTypeId}`}
                </span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    cycleSort(c.name)
                  }}
                  title="Sort row columns by this field"
                  className={cn(
                    "ml-1 shrink-0 px-0.5 text-[10px] leading-none hover:text-foreground",
                    sort?.col === c.name
                      ? "text-main"
                      : "text-chrome-text/40 opacity-0 group-hover:opacity-100",
                  )}
                >
                  {sort?.col === c.name ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                </button>
              </div>
              <div className="relative" style={{ width: transposedRowsWidth }}>
                {rowVirtualItems.map((vcol) => {
                  const row = viewRows[vcol.index]
                  const value = row?.[c.name]
                  const isJson = jsonCols.has(c.name) && value != null
                  const isInterval = isIntervalColumn(c) && value != null
                  const hl = paramHighlights.get(c.name)
                  const picked = hl ? hl.values.has(String(value)) : false
                  const crossPicked = crossHighlights.get(c.name)?.has(String(value)) ?? false
                  return (
                    <div
                      key={`${c.name}:${vcol.key}`}
                      onClick={(e) => {
                        if (onCellFilter) {
                          e.preventDefault()
                          e.stopPropagation()
                          onCellFilter(c, value)
                          return
                        }
                        if (!onEmitCellParam) return
                        e.preventDefault()
                        e.stopPropagation()
                        const cascade = e.metaKey || e.ctrlKey
                        onEmitCellParam(c.name, value, c.dataTypeId, "in", cascade, {
                          schema: c.sourceSchema,
                          table: c.sourceTable,
                          column: c.sourceColumn,
                        })
                      }}
                      onContextMenu={(e) => openCellContextMenu(e, row, rows.indexOf(row), c)}
                      className={cn(
                        "absolute top-0 h-full truncate border-r border-chrome-border/30 px-2 py-0.5 text-[12px]",
                        (isJson || isInterval) && "font-mono",
                        isNumeric ? "text-right tabular-nums" : "text-left",
                        value == null ? "text-chrome-text/40 italic" : "text-foreground",
                        (onEmitCellParam || onCellFilter) && "cursor-pointer hover:bg-main/10",
                        picked &&
                          (hl!.cascade
                            ? "bg-main/25 text-foreground"
                            : "bg-main/15 text-foreground ring-1 ring-inset ring-main/55"),
                        crossPicked && "bg-rvbbit-accent/20 text-foreground ring-1 ring-inset ring-rvbbit-accent/60",
                      )}
                      style={{ transform: `translateX(${vcol.start}px)`, width: vcol.size }}
                      title={
                        onCellFilter
                          ? `${cellTitle(value, c)} — click to filter the dashboard`
                          : onEmitCellParam
                            ? `${cellTitle(value, c)} — click to select · ⌘ filter`
                            : cellTitle(value, c)
                      }
                    >
                      {value == null
                        ? "∅"
                        : isJson
                          ? <JsonCellPreview value={value} structured={jsonTree} compact />
                          : isInterval
                            ? <IntervalCellPreview value={value} compact />
                            : formatCellValue(value)}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
        </>
      ) : (
        <>
      <div className="sticky top-0 z-10 flex shrink-0 border-b border-chrome-border bg-chrome-bg/95 backdrop-blur" style={{ minWidth: totalWidth }}>
        {columns.map((c, i) => {
          const role = columnDragSource?.columns.find((col) => col.name === c.name)?.role
          const isSelected = selectedHeaders.has(c.name)
          return (
            <div
              key={c.name}
              draggable={dragEnabled}
              onDragStart={(e) => onHeaderDragStart(e, c.name)}
              onDragEnd={() => setActiveColumnDragSource(null)}
              onClick={(e) => onHeaderClick(e, c.name)}
              className={cn(
                "group relative flex select-none items-center border-r border-chrome-border/60 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text",
                dragEnabled && "cursor-grab active:cursor-grabbing hover:bg-foreground/[0.05]",
                isSelected && "bg-main/15 text-foreground",
              )}
              style={{ width: columnWidths[i] }}
              title={
                [
                  heatSpecs.has(c.name) ? heatHint(heatSpecs.get(c.name)!) : null,
                  calcSpecs.has(c.name)
                    ? calcSpecs.get(c.name) === "pct"
                      ? "table calc: % of visible column total"
                      : "table calc: change vs previous visible row (sort-aware)"
                    : null,
                  dragEnabled ? "Drag onto canvas to GROUP BY this column. Cmd/Ctrl-click to multi-select." : null,
                ]
                  .filter(Boolean)
                  .join("\n") || undefined
              }
            >
              {dragEnabled ? (
                <GripVertical className={cn("mr-1 h-3 w-3 shrink-0", role === "metric" ? "text-chart-3" : "text-main/70")} />
              ) : null}
              <span className="truncate text-foreground">{displayColumnName(c.name)}</span>
              {heatSpecs.has(c.name) ? (
                <Flame className="ml-1 h-2.5 w-2.5 shrink-0 text-chart-3/80" />
              ) : null}
              <span className="ml-2 truncate text-[10px] text-chrome-text/70">
                {c.dataTypeName ?? `oid:${c.dataTypeId}`}
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  cycleSort(c.name)
                }}
                title="Sort by this column"
                className={cn(
                  "ml-1 shrink-0 px-0.5 text-[10px] leading-none hover:text-foreground",
                  sort?.col === c.name
                    ? "text-main"
                    : "text-chrome-text/40 opacity-0 group-hover:opacity-100",
                )}
              >
                {sort?.col === c.name ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
              </button>
              {present ? null : (
                <div
                  onPointerDown={(e) => startResize(c.name, e)}
                  className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-main/50"
                />
              )}
            </div>
          )
        })}
      </div>

      <div style={{ height: virtualizer.getTotalSize(), minWidth: totalWidth }} className="relative shrink-0">
        {virtualizer.getVirtualItems().map((vrow) => {
          const row = viewRows[vrow.index]
          const rowHeatBg = heatRowSpec ? heatBackground(heatRowSpec, row?.[heatRowSpec.column], HEAT_ROW_PCT) : null
          return (
            <div
              key={vrow.key}
              className={cn(
                "absolute left-0 right-0 flex border-b border-chrome-border/30",
                vrow.index % 2 === 0 ? "bg-transparent" : "bg-foreground/[0.025]",
              )}
              style={{
                transform: `translateY(${vrow.start}px)`,
                height: rowHeight,
                minWidth: totalWidth,
                ...(rowHeatBg ? { background: rowHeatBg } : {}),
              }}
            >
              {columns.map((c, ci) => {
                const sourceIdx = rows.indexOf(row)
                const cellKey = `${sourceIdx}:${c.name}`
                const value = overrides.has(cellKey) ? overrides.get(cellKey) : row?.[c.name]
                const isNumeric = isNumericType(c)
                const isThumb = thumbsActive && imageCols.has(c.name) && isImageUrl(value)
                const isJson = jsonCols.has(c.name) && value != null
                const isInterval = isIntervalColumn(c) && value != null
                const hl = paramHighlights.get(c.name)
                const picked = hl ? hl.values.has(String(value)) : false
                const crossPicked = crossHighlights.get(c.name)?.has(String(value)) ?? false
                const cellEditable = canEdit(c.name)
                const isEditing = editing != null && editing.rowIdx === sourceIdx && editing.col === c.name
                const isPending = pendingCells.has(cellKey)
                // Selection/pick/pending states must stay visible — inline heat
                // backgrounds would paint over their class-based highlights, so
                // heat yields to them.
                const heatSpec = heatActive && !picked && !crossPicked && !isPending ? heatSpecs.get(c.name) : undefined
                const heatBg = heatSpec
                  ? heatBackground(heatSpec, value, heatSpec.kind === "numeric" ? HEAT_CELL_PCT : HEAT_BAND_PCT)
                  : null
                // Experimental table calcs: replace the DISPLAYED value only —
                // the raw value stays behind params, filters, copy, and the
                // hover title.
                let calcNode: React.ReactNode = null
                const calcKind = calcSpecs.get(c.name)
                if (calcKind && value != null) {
                  const n = Number(value)
                  if (Number.isFinite(n)) {
                    if (calcKind === "pct") {
                      const total = calcTotals.get(c.name) ?? 0
                      if (total !== 0) calcNode = `${((n / total) * 100).toFixed(1)}%`
                    } else if (vrow.index > 0) {
                      const p = Number(viewRows[vrow.index - 1]?.[c.name])
                      if (Number.isFinite(p)) {
                        const d = Number((n - p).toPrecision(12))
                        calcNode = (
                          <span style={d === 0 ? undefined : { color: d > 0 ? "var(--success)" : "var(--danger)" }}>
                            {d > 0 ? "+" : ""}
                            {formatCellValue(d)}
                          </span>
                        )
                      }
                    } else {
                      calcNode = <span className="text-chrome-text/40">·</span>
                    }
                  }
                }
                // Hover affixes: ↗ for URL cells, ✦ for semantic-receipt cells.
                const linkHref = typeof value === "string" && isWebUrl(value) ? value.trim() : null
                const showReceipt = !!onOpenReceipt && !!receiptColumns?.has(c.name) && value != null
                if (isEditing) {
                  return (
                    <div
                      key={c.name}
                      className="border-r border-chrome-border/30 bg-main/10 px-0.5 py-0"
                      style={{ width: columnWidths[ci] }}
                    >
                      <input
                        autoFocus
                        value={editing.draft}
                        onChange={(e) => setEditing((ed) => (ed ? { ...ed, draft: e.target.value } : ed))}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault()
                            void commitEdit(row, sourceIdx, c, editing.draft)
                          } else if (e.key === "Escape") {
                            e.preventDefault()
                            setEditing(null)
                          }
                        }}
                        onBlur={() => setEditing(null)}
                        className={cn(
                          "h-full w-full bg-transparent text-[12px] text-foreground outline-none ring-1 ring-inset ring-main/60",
                          isNumeric ? "text-right tabular-nums" : "text-left",
                        )}
                      />
                    </div>
                  )
                }
                return (
                  <div
                    key={c.name}
                    onDoubleClick={(e) => {
                      if (!cellEditable || isPending) return
                      e.preventDefault()
                      e.stopPropagation()
                      setEditing({ rowIdx: sourceIdx, col: c.name, draft: value == null ? "" : String(value) })
                    }}
                    onClick={(e) => {
                      // Cross-filter (multi-statement dashboard): a click filters
                      // sibling tiles by this column's real source table.
                      if (onCellFilter) {
                        e.preventDefault()
                        e.stopPropagation()
                        onCellFilter(c, value)
                        return
                      }
                      // Left-click → "pick": toggle this value into a multi-select
                      // IN set. It highlights + publishes to the shelf but does
                      // NOT filter this grid — drag the chip onto a target to bind.
                      // ⌘/Ctrl-click → cascade filter: narrows this grid (and any
                      // {block} children) — the classic drill-down.
                      if (!onEmitCellParam) return
                      e.preventDefault()
                      e.stopPropagation()
                      const cascade = e.metaKey || e.ctrlKey
                      onEmitCellParam(c.name, value, c.dataTypeId, "in", cascade, {
                        schema: c.sourceSchema,
                        table: c.sourceTable,
                        column: c.sourceColumn,
                      })
                    }}
                    onContextMenu={(e) => openCellContextMenu(e, row, rows.indexOf(row), c)}
                    className={cn(
                      "group/cell relative border-r border-chrome-border/30 px-2 py-0.5 text-[12px]",
                      isThumb ? "flex items-center overflow-hidden" : "truncate",
                      (isJson || isInterval) && "font-mono",
                      isNumeric ? "text-right tabular-nums" : "text-left",
                      value == null ? "text-chrome-text/40 italic" : "text-foreground",
                      (onEmitCellParam || onCellFilter) && "cursor-pointer hover:bg-main/10",
                      cellEditable && "hover:ring-1 hover:ring-inset hover:ring-main/35",
                      isPending && "animate-pulse text-chrome-text/50",
                      picked &&
                        (hl!.cascade
                          ? "bg-main/25 text-foreground"
                          : "bg-main/15 text-foreground ring-1 ring-inset ring-main/55"),
                      crossPicked && "bg-rvbbit-accent/20 text-foreground ring-1 ring-inset ring-rvbbit-accent/60",
                    )}
                    style={{
                      width: columnWidths[ci],
                      // Taller thumb rows: keep single-line text cells vertically
                      // centered without switching them to flex (truncate needs
                      // block layout).
                      ...(thumbsActive && !isThumb ? { lineHeight: `${rowHeight - 6}px` } : {}),
                      ...(heatBg ? { background: heatBg } : {}),
                    }}
                    title={
                      cellEditable
                        ? `${cellTitle(value, c)} — double-click to edit · Enter saves · Esc cancels`
                        : onCellFilter
                          ? `${cellTitle(value, c)} — click to filter the dashboard`
                          : onEmitCellParam
                            ? `${cellTitle(value, c)} — click to select · ⌘ filter`
                            : cellTitle(value, c)
                    }
                  >
                    {value == null
                      ? "∅"
                      : isThumb
                        ? (
                            <CellThumb
                              portalContainer={portalContainer}
                              src={String(value).trim()}
                              box={boxCols.has(c.name) ? parseImageBox(row?.[boxCols.get(c.name)!]) : null}
                            />
                          )
                        : (calcNode ?? (
                            isJson
                              ? <JsonCellPreview value={value} structured={jsonTree} />
                              : isInterval
                                ? <IntervalCellPreview value={value} />
                                : formatCellValue(value)
                          ))}
                    {linkHref || showReceipt ? (
                      <span className="absolute right-0.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover/cell:opacity-100">
                        {showReceipt ? (
                          <button
                            type="button"
                            title="Semantic receipt — trace this value's model call"
                            onClick={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              onOpenReceipt?.({ row, rowIndex: sourceIdx, column: c, value })
                            }}
                            className="rounded border border-chrome-border/60 bg-chrome-bg/90 px-1 text-[10px] leading-4 text-rvbbit-accent hover:text-foreground"
                          >
                            ✦
                          </button>
                        ) : null}
                        {linkHref ? (
                          <a
                            href={linkHref}
                            target="_blank"
                            rel="noreferrer"
                            title={`Open ${linkHref}`}
                            onClick={(e) => e.stopPropagation()}
                            className="rounded border border-chrome-border/60 bg-chrome-bg/90 px-1 text-[10px] leading-4 text-chrome-text hover:text-foreground"
                          >
                            ↗
                          </a>
                        ) : null}
                      </span>
                    ) : null}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
        </>
      )}
      </div>
      <ContextMenu portalContainer={portalContainer} state={contextMenu} onClose={() => setContextMenu(null)} />
    </div>
  )
}

/** Lazy thumbnail with a text fallback — a URL that 404s or isn't actually an
 *  image degrades to the plain cell text instead of a broken-image glyph.
 *  Hovering shows a full-size preview popover (portal, so the virtualized
 *  row's overflow can't clip it). */
function CellThumb({
  src,
  portalContainer,
  box,
}: {
  src: string
  portalContainer?: HTMLElement
  box?: ImageBox | null
}) {
  const [failed, setFailed] = useState(false)
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null)
  useEffect(() => {
    setFailed(false)
    setZoom(null)
  }, [src])
  if (failed) return <span className="min-w-0 truncate">{src}</span>
  return (
    <>
      <span className="relative inline-block shrink-0">
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          draggable={false}
          onError={() => setFailed(true)}
          onMouseEnter={(e) => setZoom({ x: e.clientX, y: e.clientY })}
          onMouseLeave={() => setZoom(null)}
          className="block max-h-[38px] max-w-[220px] rounded-sm object-contain"
        />
        {box ? <BoxOutline box={box} /> : null}
      </span>
      {zoom
        ? createPortal(
            <div
              className="pointer-events-none fixed z-[9999]"
              style={{
                left: Math.min(zoom.x + 18, Math.max(8, window.innerWidth - 448)),
                top: Math.min(zoom.y + 14, Math.max(8, window.innerHeight - 388)),
              }}
            >
              <span className="relative block">
                <img
                  src={src}
                  alt=""
                  referrerPolicy="no-referrer"
                  className="block max-h-[360px] max-w-[440px] rounded-md border border-chrome-border bg-doc-bg object-contain shadow-2xl"
                />
                {box ? <BoxOutline box={box} large /> : null}
              </span>
            </div>,
            portalContainer ?? document.body,
          )
        : null}
    </>
  )
}

function cellMatchesFilter(value: unknown, filter: string, column: QueryResultColumn): boolean {
  if (value == null) return false
  const text = isJsonColumn(column)
    ? boundedJsonText(value, 12_000, 48, 4)
    : isIntervalColumn(column)
      ? formatIntervalValue(value, true)
      : String(formatCellValue(value))
  return text.toLowerCase().includes(filter)
}

function columnMatchesFilter(column: QueryResultColumn, filter: string): boolean {
  return (
    column.name.toLowerCase().includes(filter) ||
    (column.dataTypeName ?? `oid:${column.dataTypeId}`).toLowerCase().includes(filter)
  )
}

function isNumericType(c: QueryResultColumn): boolean {
  const name = (c.dataTypeName ?? "").toLowerCase()
  if (!name) {
    return [20, 21, 23, 700, 701, 1700].includes(c.dataTypeId)
  }
  return (
    name.includes("int") ||
    name === "numeric" ||
    name.includes("float") ||
    name === "real" ||
    name === "double" ||
    name === "money"
  )
}

function isJsonColumn(column: QueryResultColumn): boolean {
  const type = column.dataTypeName?.toLowerCase().trim() ?? ""
  return (
    column.dataTypeId === 114 ||
    column.dataTypeId === 3802 ||
    type === "json" ||
    type === "jsonb" ||
    type.endsWith(" json") ||
    type.endsWith(" jsonb")
  )
}

function isIntervalColumn(column: QueryResultColumn): boolean {
  const type = column.dataTypeName?.toLowerCase().trim() ?? ""
  return column.dataTypeId === 1186 || type === "interval" || type.endsWith(" interval")
}

function minimumColumnWidth(column: QueryResultColumn): number {
  // Header text is uppercase, 11px, and tracked; reserve the field's full width
  // first, then a bounded type label plus padding/sort chrome. PostgreSQL field
  // names are normally <=63 bytes, so letting the header beat the content cap
  // is still a reasonable initial width and avoids mystery columns on first run.
  const fieldChars = Array.from(displayColumnName(column.name)).length
  const typeChars = Array.from(column.dataTypeName ?? `oid:${column.dataTypeId}`).length
  const fieldWidth = fieldChars * 7.4
  const typeWidth = Math.min(110, typeChars * 5.6 + 10)
  return Math.max(MIN_COL_WIDTH, Math.ceil(fieldWidth + typeWidth + 46))
}

function estimateWidth(column: QueryResultColumn, rows: Record<string, unknown>[]): number {
  const headerWidth = minimumColumnWidth(column)
  if (isJsonColumn(column)) return Math.max(headerWidth, JSON_COL_WIDTH)
  const sample = rows.slice(0, 20)
  const sampleMax = sample.reduce((max, r) => {
    const v = r[column.name]
    const len = v == null
      ? 1
      : isIntervalColumn(column)
        ? formatIntervalValue(v).length
        : String(formatCellValue(v)).length
    return Math.max(max, len)
  }, column.name.length)
  const contentWidth = Math.min(MAX_AUTO_CONTENT_WIDTH, sampleMax * 7 + 24)
  return Math.max(headerWidth, contentWidth)
}

function estimateFieldWidth(columns: QueryResultColumn[]): number {
  const max = columns.reduce((n, c) => Math.max(n, c.name.length + (c.dataTypeName?.length ?? 0) + 4), 12)
  return Math.min(640, Math.max(180, max * 7 + 28))
}

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // Best effort only; right-click copy should never disturb the grid.
  }
}

function cellText(value: unknown): string {
  if (typeof value === "string") return value
  if (value != null && typeof value === "object") return stringifyJson(value)
  return formatCellValue(value)
}

function cellTitle(value: unknown, column: QueryResultColumn): string {
  if (value == null) return "null"
  if (isJsonColumn(column)) return boundedJsonText(value, 800, 12, 3)
  if (isIntervalColumn(column)) return formatIntervalValue(value, true)
  return String(formatCellValue(value))
}

function IntervalCellPreview({ value, compact = false }: { value: unknown; compact?: boolean }) {
  const parts = intervalDisplayParts(value)
  if (!parts) return <span className="block max-w-full truncate">{formatCellValue(value)}</span>
  const shown = compact ? parts.slice(0, 3) : parts
  return (
    <span
      data-rvbbit-interval-preview
      className="inline-flex max-w-full items-baseline gap-1.5 overflow-hidden whitespace-nowrap tabular-nums"
    >
      {shown.map((part, index) => (
        <span key={`${index}:${part.shortUnit}`} className="inline-flex shrink-0 items-baseline gap-px">
          <span>{part.value}</span>
          <span className="text-[9px] uppercase tracking-wide text-chrome-text/55">{part.shortUnit}</span>
        </span>
      ))}
      {shown.length < parts.length ? <span className="text-chrome-text/45">…</span> : null}
    </span>
  )
}

function JsonCellPreview({
  value,
  structured,
  compact = false,
}: {
  value: unknown
  structured: boolean
  compact?: boolean
}) {
  if (!structured) {
    return (
      <span data-rvbbit-json-preview="text" className="block max-w-full truncate">
        {boundedJsonText(value, compact ? 240 : 600, 24, 3)}
      </span>
    )
  }

  const normalized = normalizeJsonForPreview(value)
  const entryLimit = compact ? 1 : JSON_INLINE_ENTRY_LIMIT
  if (Array.isArray(normalized)) {
    const shown = normalized.slice(0, entryLimit)
    return (
      <span data-rvbbit-json-preview="tree" className="block max-w-full truncate text-[10px]">
        <JsonPunctuation text="[" />
        {shown.map((item, index) => (
          <span key={index}>
            {index > 0 ? <JsonPunctuation text=", " /> : null}
            <JsonAtom value={item} />
          </span>
        ))}
        {normalized.length > shown.length ? <JsonPunctuation text={shown.length > 0 ? ", …" : "…"} /> : null}
        <JsonPunctuation text="]" />
      </span>
    )
  }

  if (normalized != null && typeof normalized === "object") {
    const { entries, hasMore } = limitedObjectEntries(normalized as Record<string, unknown>, entryLimit)
    return (
      <span data-rvbbit-json-preview="tree" className="block max-w-full truncate text-[10px]">
        <JsonPunctuation text="{" />
        {entries.map(([key, item], index) => (
          <span key={`${index}:${key}`}>
            {index > 0 ? <JsonPunctuation text=", " /> : null}
            <span style={{ color: "var(--syntax-identifier)" }}>{truncateInline(key, 42)}</span>
            <JsonPunctuation text=": " />
            <JsonAtom value={item} />
          </span>
        ))}
        {hasMore ? <JsonPunctuation text={entries.length > 0 ? ", …" : "…"} /> : null}
        <JsonPunctuation text="}" />
      </span>
    )
  }

  return (
    <span data-rvbbit-json-preview="tree" className="block max-w-full truncate text-[10px]">
      <JsonAtom value={normalized} />
    </span>
  )
}

function JsonPunctuation({ text }: { text: string }) {
  return <span style={{ color: "var(--syntax-operator)" }}>{text}</span>
}

function JsonAtom({ value }: { value: unknown }) {
  if (value == null) return <span style={{ color: "var(--syntax-keyword)" }}>null</span>
  if (typeof value === "boolean") {
    return <span style={{ color: "var(--syntax-keyword)" }}>{value ? "true" : "false"}</span>
  }
  if (typeof value === "number" || typeof value === "bigint") {
    return <span style={{ color: "var(--syntax-number)" }}>{String(value)}</span>
  }
  if (typeof value === "string") {
    return (
      <span style={{ color: "var(--syntax-string)" }}>
        {JSON.stringify(truncateInline(value, JSON_INLINE_STRING_LIMIT))}
      </span>
    )
  }
  if (Array.isArray(value)) {
    return (
      <span style={{ color: "var(--syntax-operator)" }}>
        [<span style={{ color: "var(--syntax-number)" }}>{value.length}</span>]
      </span>
    )
  }
  if (typeof value === "object") {
    return <span style={{ color: "var(--syntax-operator)" }}>{"{…}"}</span>
  }
  return <span style={{ color: "var(--syntax-foreground)" }}>{truncateInline(String(value), JSON_INLINE_STRING_LIMIT)}</span>
}

function normalizeJsonForPreview(value: unknown): unknown {
  if (typeof value !== "string") return value
  const text = value.trim()
  if (
    text.length > JSON_PARSE_CHAR_LIMIT ||
    !((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]")))
  ) {
    return value
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    return value
  }
}

function limitedObjectEntries(
  value: Record<string, unknown>,
  limit: number,
): { entries: Array<[string, unknown]>; hasMore: boolean } {
  const entries: Array<[string, unknown]> = []
  let hasMore = false
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue
    if (entries.length >= limit) {
      hasMore = true
      break
    }
    entries.push([key, value[key]])
  }
  return { entries, hasMore }
}

/** A cycle-safe, node-budgeted serializer for cell text, tooltips, filtering,
 *  and JSON sorting. It never walks an entire large document during render. */
function boundedJsonText(
  input: unknown,
  charLimit: number,
  maxNodes = 24,
  maxDepth = 3,
): string {
  const value = normalizeJsonForPreview(input)
  const seen = new WeakSet<object>()
  let nodes = 0

  const visit = (item: unknown, depth: number): string => {
    nodes += 1
    if (nodes > maxNodes) return "…"
    if (item == null) return "null"
    if (typeof item === "string") return JSON.stringify(truncateInline(item, Math.min(charLimit, 320)))
    if (typeof item === "number" || typeof item === "boolean") return String(item)
    if (typeof item === "bigint") return JSON.stringify(item.toString())
    if (item instanceof Date) return JSON.stringify(item.toISOString())
    if (typeof item !== "object") return JSON.stringify(truncateInline(String(item), 160))
    if (seen.has(item)) return '"[Circular]"'
    seen.add(item)

    if (Array.isArray(item)) {
      if (depth >= maxDepth) return item.length > 0 ? "[…]" : "[]"
      const parts: string[] = []
      for (let index = 0; index < item.length && nodes < maxNodes; index += 1) {
        parts.push(visit(item[index], depth + 1))
      }
      if (parts.length < item.length) parts.push("…")
      return `[${parts.join(", ")}]`
    }

    if (depth >= maxDepth) return "{…}"
    const parts: string[] = []
    let hasMore = false
    for (const key in item as Record<string, unknown>) {
      if (!Object.prototype.hasOwnProperty.call(item, key)) continue
      if (nodes >= maxNodes) {
        hasMore = true
        break
      }
      parts.push(`${JSON.stringify(truncateInline(key, 120))}: ${visit((item as Record<string, unknown>)[key], depth + 1)}`)
    }
    if (hasMore) parts.push("…")
    return `{${parts.join(", ")}}`
  }

  return truncateInline(visit(value, 0), charLimit)
}

function truncateInline(value: string, max: number): string {
  if (value.length <= max) return value
  return `${value.slice(0, Math.max(0, max - 1))}…`
}

function stringifyJson(value: unknown): string {
  try {
    return JSON.stringify(value, (_key, v: unknown) => (typeof v === "bigint" ? v.toString() : v), 2)
  } catch {
    return String(value)
  }
}
