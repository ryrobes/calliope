"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { CaretRight, Database, RefreshCw, Search, TreeStructure } from "@/lib/icons"
import { fmtAgo, fmtCount } from "./instruments"
import {
  crawlCatalog,
  fetchCatalogStatus,
  fetchFindStatus,
  findData,
  findHitLabel,
  hitLabel,
  probeEmbedder,
  probeFind,
  runFindRefresh,
  runValueSweep,
  searchData,
  shortDoc,
  type CatalogKind,
  type CatalogStatus,
  type DataSearchHit,
  type EmbedderHealth,
  type FindHit,
  type FindResult,
} from "@/lib/rvbbit/data-search"
import type { DataSearchPayload } from "@/lib/desktop/types"
import { VIZ_CHIP_FG } from "@/lib/desktop/viz-colors"
import { cn } from "@/lib/utils"

interface DataSearchWindowProps {
  payload: DataSearchPayload
  activeConnectionId: string | null
  hasRvbbit: boolean
  /** Open the underlying table in a Data window. */
  onOpenTable: (schema: string, name: string) => void
  /** Open a single field as a focused query (distribution / numeric summary). */
  onOpenField: (schema: string, rel: string, col: string) => void
  /** Open the db_catalog graph (optionally seeded at a node) in the KG Explorer. */
  onOpenCatalogGraph: (seedKind?: string | null, seedLabel?: string | null) => void
}

type KindFilter = "all" | "db_table" | "db_column"

const KIND_COLOR: Record<string, string> = {
  db_table: "var(--viz-kind-table)",
  db_column: "var(--viz-kind-column)",
  cube: "var(--viz-kind-cube, var(--viz-kind-table))",
}

const KIND_LABEL: Record<string, string> = {
  db_table: "table",
  db_column: "col",
  cube: "cube",
}

export function DataSearchWindow({
  payload,
  activeConnectionId,
  hasRvbbit,
  onOpenTable,
  onOpenField,
  onOpenCatalogGraph,
}: DataSearchWindowProps) {
  const [q, setQ] = useState(payload.initialQuery ?? "")
  const [kindFilter, setKindFilter] = useState<KindFilter>("all")
  const [hits, setHits] = useState<DataSearchHit[]>([])
  const [findResult, setFindResult] = useState<FindResult | null>(null)
  const [findAvailable, setFindAvailable] = useState<boolean | null>(null)
  const [judge, setJudge] = useState(false)
  const [showIndex, setShowIndex] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<CatalogStatus | null>(null)
  const [embed, setEmbed] = useState<EmbedderHealth | null>(null)
  const [crawling, setCrawling] = useState(false)
  const [crawlMsg, setCrawlMsg] = useState<string | null>(null)
  const seq = useRef(0)

  const refreshStatus = useCallback(async () => {
    if (!activeConnectionId) return
    setStatus(await fetchCatalogStatus(activeConnectionId))
  }, [activeConnectionId])

  useEffect(() => {
    if (!activeConnectionId) return
    let cancelled = false
    ;(async () => {
      const [s, e, f] = await Promise.all([
        fetchCatalogStatus(activeConnectionId),
        probeEmbedder(activeConnectionId),
        probeFind(activeConnectionId),
      ])
      if (cancelled) return
      setStatus(s)
      setEmbed(e)
      setFindAvailable(f)
    })()
    return () => {
      cancelled = true
    }
  }, [activeConnectionId])

  // Debounced search — mirrors the KG explorer's SeedPicker. All state
  // updates happen inside the timeout callback (not the effect body) so the
  // search stays cheap and lint-clean. With rvbbit.find installed the window
  // runs the find primitive (envelope per hit); otherwise plain data_search.
  useEffect(() => {
    if (!activeConnectionId || findAvailable == null) return
    const mine = ++seq.current
    const t = setTimeout(async () => {
      const query = q.trim()
      if (!query) {
        setHits([])
        setFindResult(null)
        setSearching(false)
        setError(null)
        return
      }
      setSearching(true)
      const kinds: CatalogKind[] | null = kindFilter === "all" ? null : [kindFilter]
      if (findAvailable) {
        const { result, error } = await findData(activeConnectionId, query, 30, {
          kinds: kinds ? kinds : ["db_table", "db_column", "cube"],
          rerank: true,
          judge,
        })
        if (mine !== seq.current) return
        setFindResult(result ?? null)
        setHits([])
        setError(error ?? null)
        setSearching(false)
        return
      }
      const { hits, error } = await searchData(activeConnectionId, query, 30, kinds)
      if (mine !== seq.current) return // a newer search superseded this one
      setHits(hits)
      setFindResult(null)
      setError(error ?? null)
      setSearching(false)
      // Empty result with a fully-embedded catalog usually means the
      // embedder failed (data_search swallows it → lexical-only). Re-probe
      // so the empty state can say WHY rather than "no matches".
      if (!error && hits.length === 0) {
        const e = await probeEmbedder(activeConnectionId)
        if (mine === seq.current) setEmbed(e)
      }
    }, 220)
    return () => clearTimeout(t)
  }, [q, kindFilter, activeConnectionId, findAvailable, judge])

  const runCrawl = useCallback(async () => {
    if (!activeConnectionId || crawling) return
    setCrawling(true)
    setCrawlMsg(null)
    const { result, error } = await crawlCatalog(activeConnectionId, {})
    if (error) {
      setCrawlMsg(`Crawl failed: ${error}`)
    } else if (result) {
      setCrawlMsg(
        `Crawled ${fmtCount(result.tables)} tables · ${fmtCount(result.columns)} columns · ${fmtCount(result.docsEmbedded)} embedded`,
      )
    }
    await refreshStatus()
    setCrawling(false)
    // re-run the active search against the refreshed catalog
    if (q.trim() && activeConnectionId) {
      const kinds: CatalogKind[] | null = kindFilter === "all" ? null : [kindFilter]
      if (findAvailable) {
        const r = await findData(activeConnectionId, q.trim(), 30, { kinds, rerank: true, judge })
        setFindResult(r.result ?? null)
      } else {
        const r = await searchData(activeConnectionId, q.trim(), 30, kinds)
        setHits(r.hits)
      }
    }
  }, [activeConnectionId, crawling, refreshStatus, q, kindFilter, findAvailable, judge])

  // ── Gates ──────────────────────────────────────────────────────────
  if (!activeConnectionId) {
    return <Centered>Connect to a database to search its catalog.</Centered>
  }
  if (!hasRvbbit) {
    return (
      <Centered>
        Data Search needs the <span className="font-mono">rvbbit</span> extension on this connection.
      </Centered>
    )
  }

  const installed = status?.installed ?? true
  const empty = installed && (status?.docs ?? 0) === 0
  const findHits = findResult?.hits ?? []
  const degraded = findResult?.degraded ?? []
  const noHits = findAvailable ? findHits.length === 0 : hits.length === 0

  return (
    <div className="flex h-full flex-col text-foreground">
      {/* Header: status + controls */}
      <div className="shrink-0 border-b border-chrome-border/60 bg-chrome-bg/40 px-3 py-2">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[12px] font-medium">
            <Database className="h-4 w-4" style={{ color: "var(--brand-kg)" }} />
            {findAvailable ? "Find" : "Data Search"}
          </div>
          <div className="flex items-center gap-2">
            {findAvailable ? (
              <button
                type="button"
                onClick={() => setShowIndex((v) => !v)}
                className={cn(
                  "flex items-center gap-1 rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] hover:bg-foreground/[0.06] hover:text-foreground",
                  showIndex ? "bg-foreground/[0.10] text-foreground" : "text-chrome-text",
                )}
                title="Search index details: dense tier, lexical index, values index, snapshots, last refresh"
              >
                Index
              </button>
            ) : null}
            {findAvailable ? (
              <button
                type="button"
                onClick={() => setJudge((v) => !v)}
                className={cn(
                  "flex items-center gap-1 rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] hover:bg-foreground/[0.06] hover:text-foreground",
                  judge ? "bg-foreground/[0.10] text-foreground" : "text-chrome-text",
                )}
                title="Ask the decision model whether each top hit can directly answer the query (one call, cached)"
              >
                Judge {judge ? "on" : "off"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => onOpenCatalogGraph()}
              className="flex items-center gap-1 rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06] hover:text-foreground"
              title="Open the db_catalog graph in the Graph Explorer"
            >
              <TreeStructure className="h-3 w-3" /> Browse graph
            </button>
            <button
              type="button"
              onClick={runCrawl}
              disabled={crawling}
              className="flex items-center gap-1 rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-50"
              title="Re-fingerprint the database and rebuild the catalog"
            >
              <RefreshCw className={`h-3 w-3 ${crawling ? "animate-spin" : ""}`} />
              {crawling ? "Crawling…" : status?.docs ? "Refresh" : "Crawl"}
            </button>
          </div>
        </div>

        <div className="relative">
          <div className="flex items-center gap-1.5 rounded-full border border-chrome-border/60 bg-background px-2.5 py-1 text-[12px]">
            <Search className="h-3.5 w-3.5 text-chrome-text/60" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={
                findAvailable
                  ? "describe the data, or type a value… e.g. overdue balance, Coast Dental, Closed Won"
                  : "describe the data… e.g. customer email, order status, signup date"
              }
              className="w-full bg-transparent text-[12px] text-foreground placeholder:text-chrome-text/40 focus:outline-none"
            />
            {searching ? <span className="text-[10px] text-chrome-text/50">…</span> : null}
          </div>
        </div>

        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            {(["all", "db_table", "db_column"] as const).map((kf) => (
              <button
                key={kf}
                type="button"
                onClick={() => setKindFilter(kf)}
                className={`rounded-full px-2 py-0.5 text-[10px] ${
                  kindFilter === kf
                    ? "bg-foreground/[0.10] text-foreground"
                    : "text-chrome-text/60 hover:text-foreground"
                }`}
              >
                {kf === "all" ? "All" : kf === "db_table" ? "Tables" : "Columns"}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-[10px] text-chrome-text/50">
            {embed ? (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5",
                  embed.ok
                    ? "bg-success/10 text-success"
                    : "bg-danger/15 text-danger",
                )}
                title={
                  embed.ok
                    ? `Embedder live (${embed.dim}-dim) — semantic ranking on`
                    : `Embedder not responding${embed.error ? `: ${embed.error}` : ""} — semantic ranking off, keyword matches only`
                }
              >
                {embed.ok ? "semantic" : "semantic off"}
              </span>
            ) : null}
            {findResult ? (
              <span
                className="rounded-full bg-foreground/[0.06] px-1.5 py-0.5 tabular-nums"
                title={`embed ${findResult.timingMs.embed ?? "?"} ms · seams ${findResult.timingMs.seams ?? "?"} ms · envelope ${findResult.timingMs.envelope ?? "?"} ms`}
              >
                {findResult.elapsedMs != null ? `${findResult.elapsedMs} ms` : "—"}
              </span>
            ) : null}
            <span>
              {status?.installed
                ? `${fmtCount(status.tables)} tables · ${fmtCount(status.columns)} columns · ${fmtCount(status.embedded)}/${fmtCount(status.docs)} embedded · ${fmtAgo(status.lastRunAt ?? 0)}`
                : "catalog not installed"}
            </span>
          </div>
        </div>
        {crawlMsg ? <div className="mt-1.5 text-[10px] text-chrome-text/60">{crawlMsg}</div> : null}
        {degraded.length ? (
          <div className="mt-1.5 rounded-base border border-danger/40 bg-danger/10 px-2 py-1 text-[10px] text-danger">
            Partial search — {degraded.join("; ")}
          </div>
        ) : null}
        {findResult?.weak && findHits.length ? (
          <div className="mt-1.5 rounded-base border border-chrome-border/60 bg-foreground/[0.04] px-2 py-1 text-[10px] text-chrome-text/70">
            Weak match — nothing matched by name, by value, or with a strong embedding. These are nearest neighbours, not answers.
          </div>
        ) : null}
        {findResult?.suppressed.length ? (
          <div className="mt-1.5 text-[10px] text-chrome-text/55">
            Judge cut {findResult.suppressed.length}:{" "}
            {findResult.suppressed.map((h) => `${findHitLabel(h)} (${Math.round((h.answersAsk ?? 0) * 100)}%)`).join(", ")}
          </div>
        ) : null}
      </div>

      {showIndex && findAvailable ? <IndexPanel connectionId={activeConnectionId} /> : null}

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-auto">
        {!installed ? (
          <Centered>
            The catalog functions aren&rsquo;t installed on this database. Load{" "}
            <span className="font-mono">crates/pg_rvbbit/sql/catalog_kg.sql</span>, then click{" "}
            <span className="font-medium">Crawl</span>.
          </Centered>
        ) : empty && !q.trim() ? (
          <Centered>
            No catalog yet. Click <span className="font-medium">Crawl</span> to fingerprint this
            database — then search tables and columns by meaning.
          </Centered>
        ) : error ? (
          <Centered>
            <span className="text-danger">{error}</span>
          </Centered>
        ) : !q.trim() ? (
          <Centered>
            {findAvailable ? (
              <>
                Type a phrase or a literal value. Hits carry where the data comes from, when it
                last loaded, who queries it, and what reads it.
              </>
            ) : (
              <>
                Type a phrase to search {fmtCount(status?.docs ?? 0)} fingerprints by meaning. Hits
                rank by semantic similarity, not keywords.
              </>
            )}
          </Centered>
        ) : noHits && !searching ? (
          embed && !embed.ok ? (
            <Centered>
              <div className="space-y-1">
                <div className="text-danger">Semantic search is unavailable.</div>
                <div className="text-chrome-text/60">
                  The embedder (<span className="font-mono">embed</span>) isn&rsquo;t responding
                  {embed.error ? <> — <span className="font-mono">{embed.error}</span></> : null}, so
                  ranking falls back to keywords only. The catalog is fully fingerprinted
                  ({fmtCount(status?.embedded ?? 0)} embedded) — once the embedder recovers, meaning-based
                  search returns.
                </div>
              </div>
            </Centered>
          ) : (
            <Centered>
              No matches for <span className="font-mono">&ldquo;{q.trim()}&rdquo;</span>.
              {degraded.length ? <div className="mt-1 text-danger">{degraded.join("; ")}</div> : null}
            </Centered>
          )
        ) : findAvailable ? (
          <ul className="divide-y divide-chrome-border/30">
            {findHits.map((h) => (
              <FindHitRow
                key={`${h.kind}:${h.nodeId}`}
                hit={h}
                onOpenTable={onOpenTable}
                onOpenField={onOpenField}
                onOpenGraph={onOpenCatalogGraph}
              />
            ))}
          </ul>
        ) : (
          <ul className="divide-y divide-chrome-border/30">
            {hits.map((h) => (
              <HitRow
                key={`${h.kind}:${h.nodeId}`}
                hit={h}
                onOpenTable={onOpenTable}
                onOpenField={onOpenField}
                onOpenGraph={onOpenCatalogGraph}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ── index panel: the seams and snapshots behind find ─────────────────

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

function IndexPanel({ connectionId }: { connectionId: string }) {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<"refresh" | "sweep" | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    const { status, error } = await fetchFindStatus(connectionId)
    setStatus(status ?? null)
    setError(error ?? null)
  }, [connectionId])

  useEffect(() => {
    void load()
  }, [load])

  const refresh = useCallback(async () => {
    setBusy("refresh")
    setNote(null)
    const { result, error } = await runFindRefresh(connectionId)
    setNote(error ? `Refresh failed: ${error}` : `Refreshed in ${rec(result).elapsed_ms ?? "?"} ms`)
    await load()
    setBusy(null)
  }, [connectionId, load])

  const sweep = useCallback(async () => {
    setBusy("sweep")
    setNote(null)
    const { error } = await runValueSweep(connectionId, 60)
    setNote(error ? `Sweep failed: ${error}` : "Values sweep ran for up to 60 s")
    await load()
    setBusy(null)
  }, [connectionId, load])

  const dense = rec(status?.dense)
  const lexical = rec(status?.lexical)
  const values = rec(status?.values)
  const snapshots = rec(status?.snapshots)
  const refs = rec(snapshots.refs)
  const usage = rec(snapshots.usage)
  const lineage = rec(snapshots.lineage)
  const lastRefresh = rec(status?.last_refresh)
  const lastCrawl = rec(status?.last_crawl)
  const degraded = Array.isArray(status?.degraded) ? (status!.degraded as unknown[]).map(String) : []
  const n = (v: unknown) => (v == null ? "—" : fmtCount(Number(v)))
  const when = (v: unknown) => ago(v) ?? "never"

  return (
    <div className="shrink-0 border-b border-chrome-border/60 bg-chrome-bg/20 px-3 py-2 text-[10.5px] leading-snug">
      {error ? (
        <div className="text-danger">{error}</div>
      ) : !status ? (
        <div className="text-chrome-text/50">Loading index details…</div>
      ) : (
        <>
          {degraded.length ? (
            <div className="mb-2 rounded-base border border-danger/40 bg-danger/10 px-2 py-1 text-danger">
              {degraded.join("; ")}
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 md:grid-cols-3">
            <Receipt
              label="Dense seam"
              entries={[
                ["tier", `${String(dense.tier ?? "?")}${dense.ready ? "" : " (not ready)"}`],
                ["docs", `${n(dense.indexed_docs ?? dense.n_docs)} indexed / ${n(dense.n_docs)} embedded`],
                ["dim", String(dense.dim ?? "—")],
                ["built", when(dense.refreshed_at)],
              ]}
            />
            <Receipt
              label="Lexical seam"
              entries={[
                ["index", lexical.indexed ? (lexical.trigram ? "tsvector + trigram" : "tsvector") : "none"],
                ["docs", n(lexical.docs)],
              ]}
            />
            <Receipt
              label="Values seam"
              entries={[
                ["relations", `${n(values.relations_ok)} indexed · ${n(values.relations_skipped)} skipped · ${n(values.relations_error)} errors`],
                ["values", `${n(values.values)} across ${n(values.columns)} columns`],
                ["size", values.bytes != null ? `${Math.round(Number(values.bytes) / 1048576)} MB` : "—"],
                ["pending", n(values.pending)],
                ["last indexed", when(values.last_indexed_at)],
              ]}
            />
            <Receipt
              label="Snapshots"
              entries={[
                ["refs", `${n(refs.rows)} rows · ${n(refs.relations)} relations · ${when(refs.as_of)}`],
                ["usage", `${n(usage.objects)} objects · ${when(usage.as_of)}`],
                ["lineage", `${n(lineage.relations)} relations · ${n(lineage.with_last_load)} with a last load · ${when(lineage.as_of)}`],
              ]}
            />
            <Receipt
              label="Last refresh"
              entries={[
                ["ran", `${when(lastRefresh.ran_at)}${lastRefresh.source ? ` (${String(lastRefresh.source)})` : ""}`],
                ["took", rec(lastRefresh.result).elapsed_ms != null ? `${rec(lastRefresh.result).elapsed_ms} ms` : "—"],
              ]}
            />
            <Receipt
              label="Last crawl"
              entries={[
                ["status", String(lastCrawl.status ?? "—")],
                ["finished", when(lastCrawl.finished_at)],
                ["tables", n(lastCrawl.tables_seen)],
              ]}
            />
          </div>
          {Array.isArray(values.skipped) && (values.skipped as unknown[]).length ? (
            <div className="mt-2 text-chrome-text/60">
              Skipped: {(values.skipped as Array<Record<string, unknown>>).map((s) => `${s.relation} (${s.reason})`).join(" · ")}
            </div>
          ) : null}
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              disabled={busy != null}
              onClick={refresh}
              className="rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-50"
              title="Rebuild the snapshots now: dense tier if stale, relation refs, usage, lineage"
            >
              {busy === "refresh" ? "Refreshing…" : "Refresh snapshots"}
            </button>
            <button
              type="button"
              disabled={busy != null}
              onClick={sweep}
              className="rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-50"
              title="Index literal values for pending relations, up to 60 s"
            >
              {busy === "sweep" ? "Sweeping…" : "Sweep values (60 s)"}
            </button>
            <button
              type="button"
              disabled={busy != null}
              onClick={() => void load()}
              className="rounded-base px-2 py-0.5 text-[10px] text-chrome-text/60 hover:text-foreground disabled:opacity-50"
            >
              Reload
            </button>
            {note ? <span className="text-chrome-text/60">{note}</span> : null}
          </div>
        </>
      )}
    </div>
  )
}

// ── find rows: hit + envelope chips + expandable receipts ────────────

function ago(iso: unknown): string | null {
  if (!iso) return null
  const t = new Date(String(iso)).getTime()
  return Number.isFinite(t) ? fmtAgo(t) : null
}

function Chip({ children, title, tone }: { children: React.ReactNode; title?: string; tone?: "warn" | "good" }) {
  return (
    <span
      title={title}
      className={cn(
        "rounded-full px-1.5 py-[1px] text-[9.5px] tabular-nums",
        tone === "warn"
          ? "bg-danger/10 text-danger"
          : tone === "good"
            ? "bg-success/10 text-success"
            : "bg-foreground/[0.06] text-chrome-text/75",
      )}
    >
      {children}
    </span>
  )
}

function FindHitRow({
  hit,
  onOpenTable,
  onOpenField,
  onOpenGraph,
}: {
  hit: FindHit
  onOpenTable: (schema: string, name: string) => void
  onOpenField: (schema: string, rel: string, col: string) => void
  onOpenGraph: (seedKind?: string | null, seedLabel?: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const isTable = hit.kind !== "db_column"
  const pct = hit.score == null ? null : Math.max(0, Math.min(100, Math.round(hit.score * 100)))
  const lineage = hit.lineage ?? {}
  const fresh = hit.fresh ?? {}
  const usage = hit.usage ?? {}
  const meaning = hit.meaning ?? {}
  const counts = meaning.ref_counts ?? {}
  const seamNames = Object.keys(hit.seams)
  const lastLoad = ago(lineage.last_load_at)
  const rowsEst = fresh.rows_est != null ? fmtCount(Number(fresh.rows_est)) : null
  const touches = usage.touches_30d != null ? Number(usage.touches_30d) : null
  const stale = Boolean((fresh.acceleration as Record<string, unknown> | undefined)?.stale)

  return (
    <li className="group px-3 py-2 hover:bg-foreground/[0.04]">
      <div className="flex items-center gap-2">
        <span
          className="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] uppercase tracking-wider"
          style={{ background: KIND_COLOR[hit.kind] ?? KIND_COLOR.db_table, color: VIZ_CHIP_FG }}
        >
          {KIND_LABEL[hit.kind] ?? hit.kind}
        </span>
        <button
          type="button"
          onClick={() =>
            isTable || !hit.col ? onOpenTable(hit.schema, hit.rel) : onOpenField(hit.schema, hit.rel, hit.col)
          }
          className="min-w-0 flex-1 truncate text-left font-mono text-[12px] text-foreground hover:underline"
          title={isTable || !hit.col ? `Open ${hit.schema}.${hit.rel}` : `Open field ${hit.schema}.${hit.rel}.${hit.col}`}
        >
          <span className="text-chrome-text/55">{hit.schema}.</span>
          {isTable ? (
            <span>{hit.rel}</span>
          ) : (
            <>
              <span className="text-chrome-text/55">{hit.rel}.</span>
              <span>{hit.col}</span>
            </>
          )}
        </button>
        {hit.answersAsk != null ? (
          <Chip
            title="Decision model: likelihood this object directly answers the query"
            tone={hit.answersAsk >= 0.5 ? "good" : undefined}
          >
            answers {Math.round(hit.answersAsk * 100)}%
          </Chip>
        ) : null}
        {pct != null ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-chrome-text/45" title={`seams: ${seamNames.join(" + ") || "—"}`}>
            {pct}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "shrink-0 rounded-base p-1 text-chrome-text/40 transition-opacity hover:bg-foreground/[0.08] hover:text-foreground",
            open ? "opacity-100" : "opacity-0 group-hover:opacity-100",
          )}
          title={open ? "Hide receipts" : "Show receipts (lineage, freshness, usage, what reads it)"}
        >
          <CaretRight className={cn("h-3.5 w-3.5 transition-transform", open ? "rotate-90" : "")} />
        </button>
      </div>

      {/* envelope chips */}
      <div className="mt-1 ml-[34px] flex flex-wrap items-center gap-1">
        {hit.why.length ? (
          <Chip
            title={`matched by ${hit.why.join(", ")} · seams: ${seamNames.map((s) => `${s} #${hit.seams[s].rank}`).join(", ") || "—"}`}
            tone={hit.confidence === "high" ? "good" : hit.confidence === "low" ? "warn" : undefined}
          >
            {hit.why.join(" · ")}
          </Chip>
        ) : (
          seamNames.map((s) => (
            <Chip key={s} title={`ranked #${hit.seams[s].rank} by the ${s} seam`}>
              {s}
            </Chip>
          ))
        )}
        {lineage.adapter || lineage.source_system ? (
          <Chip title={`source: ${lineage.source_system ?? "?"} · connection: ${lineage.connection ?? "?"} · role: ${lineage.role ?? "?"}`}>
            {String(lineage.adapter ?? lineage.source_system)}
          </Chip>
        ) : lineage.role ? (
          <Chip>{String(lineage.role)}</Chip>
        ) : null}
        {lastLoad ? (
          <Chip title={`last load ${String(lineage.last_load_at)} (${String(lineage.last_load_status ?? "")})`}>
            loaded {lastLoad}
          </Chip>
        ) : null}
        {rowsEst ? <Chip title="rows (planner stats or last crawl)">{rowsEst} rows</Chip> : null}
        {stale ? <Chip tone="warn" title="acceleration mirror is dirty or drifted">stale accel</Chip> : null}
        {touches != null && touches > 0 ? (
          <Chip title={`${usage.touches ?? touches} touches all-time · ${usage.users ?? "?"} users · last ${ago(usage.last_touch) ?? "?"}`}>
            {touches} touches/30d
          </Chip>
        ) : null}
        {counts.domain ? <Chip title="value-system domains that source from this relation">{counts.domain} domain{counts.domain === 1 ? "" : "s"}</Chip> : null}
        {counts.metric ? <Chip title="governed metrics whose SQL reads this relation">{counts.metric} metric{counts.metric === 1 ? "" : "s"}</Chip> : null}
        {counts.cube ? <Chip title="cubes built from this relation">{counts.cube} cube{counts.cube === 1 ? "" : "s"}</Chip> : null}
        {counts.dashboard ? <Chip title="dashboards / apps that query this relation">{counts.dashboard} dashboard{counts.dashboard === 1 ? "" : "s"}</Chip> : null}
      </div>

      {hit.matchedValues.length ? (
        <p className="mt-1 ml-[34px] text-[11px] leading-snug text-foreground/85">
          {hit.matchedValues.slice(0, 4).map((m, i) => (
            <span key={`${m.value}:${i}`} className="mr-2">
              <span className="font-mono">{m.value}</span>
              <span className="text-chrome-text/50"> ×{fmtCount(m.n)}</span>
            </span>
          ))}
        </p>
      ) : (
        <p className="mt-1 ml-[34px] line-clamp-2 text-[11px] leading-snug text-chrome-text/65">
          {shortDoc(hit.snippet, 220)}
        </p>
      )}

      {open ? (
        <div className="mt-2 ml-[34px] space-y-2 rounded-base border border-chrome-border/50 bg-chrome-bg/30 p-2 text-[10.5px] leading-snug">
          <Receipt label="Lineage" entries={lineageEntries(lineage)} />
          <Receipt label="Fresh" entries={freshEntries(fresh)} />
          <Receipt label="Usage" entries={usageEntries(usage)} />
          {meaning.domains?.length ? (
            <ReceiptList label="Domains" items={meaning.domains.map((d) => `${d.name ?? d.key}${d.status ? ` (${d.status})` : ""}`)} />
          ) : null}
          {meaning.metrics?.length ? <ReceiptList label="Metrics" items={meaning.metrics} /> : null}
          {meaning.cubes?.length ? <ReceiptList label="Cubes" items={meaning.cubes} /> : null}
          {meaning.dashboards?.length ? (
            <ReceiptList label="Dashboards" items={meaning.dashboards.map((d) => d.name ?? d.slug)} />
          ) : null}
          {hit.ask?.sample_sql ? (
            <div>
              <div className="mb-0.5 uppercase tracking-wider text-chrome-text/50">Ask</div>
              <code className="block whitespace-pre-wrap break-all font-mono text-[10px] text-foreground/85">{hit.ask.sample_sql}</code>
            </div>
          ) : null}
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={() => onOpenGraph(hit.kind, findHitLabel(hit))}
              className="rounded-base border border-chrome-border/60 px-2 py-0.5 text-[10px] text-chrome-text hover:bg-foreground/[0.06] hover:text-foreground"
            >
              Open in catalog graph
            </button>
            {hit.rerank != null ? <Chip title="cross-encoder relevance">rerank {hit.rerank.toFixed(2)}</Chip> : null}
          </div>
        </div>
      ) : null}
    </li>
  )
}

function lineageEntries(l: Record<string, unknown>): Array<[string, string]> {
  const out: Array<[string, string]> = []
  const add = (k: string, v: unknown) => {
    if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) return
    out.push([k, Array.isArray(v) ? v.join(", ") : String(v)])
  }
  add("role", l.role)
  add("source", l.source_system)
  add("adapter", l.adapter)
  add("connection", l.connection)
  add("load", l.load_mode)
  add("primary key", l.primary_key)
  add("cursor", l.cursor_column)
  add("sync every", l.sync_frequency_min != null ? `${l.sync_frequency_min} min` : null)
  add("paused", l.paused === true ? "yes" : null)
  add("last load", l.last_load_at ? `${String(l.last_load_at)}${l.last_load_status ? ` · ${l.last_load_status}` : ""}` : null)
  add("last rows", l.last_load_rows)
  const detail = l.detail as Record<string, unknown> | undefined
  if (detail?.error) add("error", detail.error)
  if (detail?.job) add("job", detail.job)
  return out
}

function freshEntries(f: Record<string, unknown>): Array<[string, string]> {
  const out: Array<[string, string]> = []
  if (f.rows_est != null) out.push(["rows", fmtCount(Number(f.rows_est))])
  if (f.last_analyze) out.push(["analyzed", ago(f.last_analyze) ?? String(f.last_analyze)])
  if (f.last_crawled) out.push(["crawled", ago(f.last_crawled) ?? String(f.last_crawled)])
  if (f.bytes != null) out.push(["bytes", fmtCount(Number(f.bytes))])
  const a = f.acceleration as Record<string, unknown> | undefined
  if (a?.accelerated) {
    out.push([
      "acceleration",
      `${a.lance ? "lance" : "parquet"}${a.rows != null ? ` · ${fmtCount(Number(a.rows))} rows` : ""}${a.seconds_since_refresh != null ? ` · refreshed ${Math.round(Number(a.seconds_since_refresh) / 60)} min ago` : ""}${a.stale ? " · STALE" : ""}`,
    ])
  }
  return out
}

function usageEntries(u: Record<string, unknown>): Array<[string, string]> {
  const out: Array<[string, string]> = []
  if (u.touches != null) out.push(["touches", `${fmtCount(Number(u.touches))} all-time · ${fmtCount(Number(u.touches_30d ?? 0))} in 30d`])
  if (u.users != null) out.push(["people", String(u.users)])
  if (u.last_touch) out.push(["last", ago(u.last_touch) ?? String(u.last_touch)])
  return out
}

function Receipt({ label, entries }: { label: string; entries: Array<[string, string]> }) {
  if (!entries.length) return null
  return (
    <div>
      <div className="mb-0.5 uppercase tracking-wider text-chrome-text/50">{label}</div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-chrome-text/60">{k}</dt>
            <dd className="break-words text-foreground/85">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function ReceiptList({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <div className="mb-0.5 uppercase tracking-wider text-chrome-text/50">{label}</div>
      <div className="text-foreground/85">{items.join(" · ")}</div>
    </div>
  )
}

// ── legacy rows (data_search without the find primitive) ─────────────

function HitRow({
  hit,
  onOpenTable,
  onOpenField,
  onOpenGraph,
}: {
  hit: DataSearchHit
  onOpenTable: (schema: string, name: string) => void
  onOpenField: (schema: string, rel: string, col: string) => void
  onOpenGraph: (seedKind?: string | null, seedLabel?: string | null) => void
}) {
  const isTable = hit.kind === "db_table"
  const pct = hit.score == null ? null : Math.max(0, Math.min(100, Math.round(hit.score * 100)))
  return (
    <li className="group px-3 py-2 hover:bg-foreground/[0.04]">
      <div className="flex items-center gap-2">
        <span
          className="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] uppercase tracking-wider"
          style={{ background: KIND_COLOR[hit.kind] ?? KIND_COLOR.db_table, color: VIZ_CHIP_FG }}
        >
          {isTable ? "table" : "col"}
        </span>
        <button
          type="button"
          onClick={() =>
            isTable || !hit.col ? onOpenTable(hit.schema, hit.rel) : onOpenField(hit.schema, hit.rel, hit.col)
          }
          className="min-w-0 flex-1 truncate text-left font-mono text-[12px] text-foreground hover:underline"
          title={
            isTable || !hit.col
              ? `Open ${hit.schema}.${hit.rel}`
              : `Open field ${hit.schema}.${hit.rel}.${hit.col}`
          }
        >
          <span className="text-chrome-text/55">{hit.schema}.</span>
          {isTable ? (
            <span>{hit.rel}</span>
          ) : (
            <>
              <span className="text-chrome-text/55">{hit.rel}.</span>
              <span>{hit.col}</span>
            </>
          )}
        </button>
        {pct != null ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-chrome-text/45">
            {pct}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => onOpenGraph(hit.kind, hitLabel(hit))}
          className="shrink-0 rounded-base p-1 text-chrome-text/40 opacity-0 transition-opacity hover:bg-foreground/[0.08] hover:text-foreground group-hover:opacity-100"
          title="Open in catalog graph"
        >
          <CaretRight className="h-3.5 w-3.5" />
        </button>
      </div>
      {pct != null ? (
        <div className="mt-1 ml-[34px] h-0.5 w-[min(220px,60%)] overflow-hidden rounded-full bg-foreground/[0.06]">
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: "var(--brand-kg)" }} />
        </div>
      ) : null}
      <p className="mt-1 ml-[34px] line-clamp-2 text-[11px] leading-snug text-chrome-text/65">
        {shortDoc(hit.doc, 220)}
      </p>
    </li>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-8 text-center text-[11px] leading-relaxed text-chrome-text/60">
      <div className="max-w-md space-y-2">{children}</div>
    </div>
  )
}
