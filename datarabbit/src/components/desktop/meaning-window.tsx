"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { CaretRight, RefreshCw } from "@/lib/icons"
import { fmtAgo, fmtCount } from "./instruments"
import { MeaningTree } from "./meaning-tree"
import {
  fetchMeaningCard,
  fetchMeaningColumns,
  fetchMeaningDomains,
  fetchMeaningEnrichments,
  fetchMeaningNarrativeStatus,
  fetchMeaningOverview,
  fetchMeaningStatus,
  rejudgeMeaning,
  runMeaningSweep,
  stateMeaning,
  MEANING_KIND_LABEL,
  asCollisions,
  asSuccession,
  retireMeaningStatement,
  MEANING_ROLE_LABEL,
  type MeaningCard,
  type MeaningColumn,
  type MeaningDomain,
  type MeaningEnrichment,
  type MeaningNarrativeStatus,
  type MeaningRow,
  type MeaningStatus,
} from "@/lib/rvbbit/meaning"
import type { MeaningPayload } from "@/lib/desktop/types"

/**
 * Meaning — what each table is, how it relates, whether it deserves metrics,
 * whether it drifts (docs/MEANING_PLAN.md). Left: sources → relations. Right:
 * the card, four questions as sections, evidence, and "this is wrong".
 */

interface MeaningWindowProps {
  payload: MeaningPayload
  activeConnectionId: string | null
  hasRvbbit: boolean
  onOpenTable: (schema: string, name: string) => void
}

const KIND_ORDER = ["entity_master", "event_log", "transactional_fact", "history_or_snapshot", "aggregate_or_cube", "lookup_or_config", "staging_or_raw", "system_plumbing"]

const KIND_TONE: Record<string, string> = {
  entity_master: "text-cyan-300 border-cyan-400/40",
  event_log: "text-violet-300 border-violet-400/40",
  transactional_fact: "text-emerald-300 border-emerald-400/40",
  history_or_snapshot: "text-amber-300 border-amber-400/40",
  aggregate_or_cube: "text-sky-300 border-sky-400/40",
  lookup_or_config: "text-chrome-text/70 border-chrome-border",
  staging_or_raw: "text-orange-300 border-orange-400/40",
  system_plumbing: "text-rose-300 border-rose-400/40",
}

function KindChip({ kind }: { kind: string | null }) {
  if (!kind) return <span className="rounded-base border border-chrome-border/60 px-1.5 text-[10px] text-chrome-text/50">pending</span>
  return (
    <span className={`rounded-base border px-1.5 text-[10px] ${KIND_TONE[kind] ?? "text-chrome-text/70 border-chrome-border"}`}>
      {MEANING_KIND_LABEL[kind] ?? kind}
    </span>
  )
}

function Confidence({ value }: { value: number | null }) {
  if (value == null) return null
  const pct = Math.round(value * 100)
  const tone = pct >= 80 ? "bg-emerald-400/70" : pct >= 55 ? "bg-amber-400/70" : "bg-rose-400/70"
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-chrome-text/70" title={`confidence ${pct}%`}>
      <i className={`inline-block h-1.5 w-1.5 rounded-full ${tone}`} />
      {pct}%
    </span>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-chrome-border/40 pt-2">
      <header className="mb-1 flex items-baseline gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-chrome-text/70">{title}</h3>
        {hint ? <span className="text-[10px] text-chrome-text/50">{hint}</span> : null}
      </header>
      <div className="text-[12px] leading-relaxed">{children}</div>
    </section>
  )
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="grid h-full place-items-center p-8 text-center text-[11px] leading-relaxed text-chrome-text/60">
      <div>
        <div className="mb-1 text-[13px] text-foreground">{title}</div>
        <div>{body}</div>
      </div>
    </div>
  )
}

function get<T = unknown>(card: MeaningCard | null, ...path: string[]): T | undefined {
  let cur: unknown = card
  for (const key of path) {
    if (!cur || typeof cur !== "object") return undefined
    cur = (cur as Record<string, unknown>)[key]
  }
  return cur as T | undefined
}

export function MeaningWindow({ payload, activeConnectionId, hasRvbbit, onOpenTable }: MeaningWindowProps) {
  const [status, setStatus] = useState<MeaningStatus | null>(null)
  const [narrative, setNarrative] = useState<MeaningNarrativeStatus | null>(null)
  const [domains, setDomains] = useState<MeaningDomain[]>([])
  const [enrichments, setEnrichments] = useState<MeaningEnrichment[]>([])
  const [domainFilter, setDomainFilter] = useState<string | null>(null)
  const [rows, setRows] = useState<MeaningRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState("")
  const [kindFilter, setKindFilter] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(payload.relation ?? null)
  const [card, setCard] = useState<MeaningCard | null>(null)
  const [columns, setColumns] = useState<MeaningColumn[]>([])
  const [cardBusy, setCardBusy] = useState(false)
  const [sweeping, setSweeping] = useState(false)
  const [fix, setFix] = useState<{ field: string; value: string; reason: string; since?: string; federation?: string; note?: string } | null>(null)
  const [closedSources, setClosedSources] = useState<Set<string>>(new Set())
  const [view, setView] = useState<"tables" | "tree">(payload.relation ? "tables" : "tree")

  const reload = useCallback(async () => {
    if (!activeConnectionId) return
    setLoading(true)
    const [s, o, n, d, e] = await Promise.all([
      fetchMeaningStatus(activeConnectionId), fetchMeaningOverview(activeConnectionId),
      fetchMeaningNarrativeStatus(activeConnectionId), fetchMeaningDomains(activeConnectionId),
      fetchMeaningEnrichments(activeConnectionId),
    ])
    setLoading(false)
    if (s.error) { setError(s.error); return }
    if (o.error) { setError(o.error); return }
    setError(null)
    setStatus(s.status ?? null)
    setRows(o.rows ?? [])
    setNarrative(n.status ?? null)      // absent on installs without 0440; the window still works
    setDomains(d.domains ?? [])
    setEnrichments(e.enrichments ?? [])   // absent on installs without 0449; the window still works
  }, [activeConnectionId])

  useEffect(() => { void reload() }, [reload])

  useEffect(() => {
    if (payload.relation) setSelected(payload.relation)
  }, [payload.relation])

  const loadCard = useCallback(async (relation: string) => {
    if (!activeConnectionId) return
    const [schema, rel] = relation.split(".", 2)
    setCardBusy(true)
    const [c, k] = await Promise.all([fetchMeaningCard(activeConnectionId, schema, rel), fetchMeaningColumns(activeConnectionId, schema, rel)])
    setCardBusy(false)
    setCard(c.card ?? null)
    setColumns(k.columns ?? [])
  }, [activeConnectionId])

  useEffect(() => {
    if (selected) void loadCard(selected)
    else { setCard(null); setColumns([]) }
  }, [selected, loadCard])

  const grouped = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const out = new Map<string, MeaningRow[]>()
    const domainMembers = domainFilter && domainFilter !== "__unplaced" ? new Set(domains.find((d) => d.key === domainFilter)?.members ?? []) : null
    for (const r of rows) {
      if (kindFilter && r.kind !== kindFilter) continue
      if (domainFilter === "__unplaced" && r.domainKey) continue
      if (domainMembers && !domainMembers.has(r.relation)) continue
      if (q && !(r.relation.includes(q) || r.name.toLowerCase().includes(q) || (r.what ?? "").toLowerCase().includes(q))) continue
      const key = r.sourceSystem ?? r.schema
      if (!out.has(key)) out.set(key, [])
      out.get(key)!.push(r)
    }
    return [...out.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [rows, filter, kindFilter, domainFilter, domains])

  const selectedRow = useMemo(() => rows.find((r) => r.relation === selected) ?? null, [rows, selected])

  // A set of collapsed sources: everything starts open, a click collapses just
  // that one. (An "open" set that started empty made the first click collapse
  // every other source and leave the clicked one open.)
  const toggleSource = (key: string) => {
    setClosedSources((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  const sweepNow = async () => {
    if (!activeConnectionId) return
    setSweeping(true)
    const r = await runMeaningSweep(activeConnectionId, 120)
    setSweeping(false)
    if (r.error) setError(r.error)
    await reload()
  }

  const rejudge = async () => {
    if (!activeConnectionId || !selected) return
    const [schema, rel] = selected.split(".", 2)
    setCardBusy(true)
    const r = await rejudgeMeaning(activeConnectionId, schema, rel)
    if (r.error) setError(r.error)
    await loadCard(selected)
    await reload()
  }

  const retireStatement = async (id: number) => {
    if (!activeConnectionId || !selected || !Number.isFinite(id)) return
    const r = await retireMeaningStatement(activeConnectionId, id, "retired in DataRabbit")
    if (r.error) { setError(r.error); return }
    await loadCard(selected)
  }

  const submitFix = async () => {
    if (!activeConnectionId || !selected || !fix) return
    let value: string | boolean | Record<string, unknown>
    if (fix.field === "ignore") value = true
    else if (fix.field === "succession") {
      const supersedes = fix.value.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean)
      if (!supersedes.length || !fix.since) return
      value = { supersedes, effective_from: fix.since, federation: fix.federation || "switch", note: fix.note?.trim() || null }
    } else if (fix.field === "caveat") {
      if (!fix.value.trim()) return
      value = { text: fix.value.trim(), as_of: fix.since || null }
    } else {
      value = fix.value.trim()
      if (!value) return
    }
    const r = await stateMeaning(activeConnectionId, selected, fix.field, value, fix.reason.trim() || "stated in DataRabbit")
    if (r.error) { setError(r.error); return }
    setFix(null)
    await loadCard(selected)
    await reload()
  }

  if (!hasRvbbit) return <EmptyState title="RVBBIT is not installed here" body="Meaning cards need the rvbbit extension (migration 0439)." />
  if (!activeConnectionId) return <EmptyState title="No warehouse connected" body="Choose a connection to read what its tables mean." />

  const kindsPresent = KIND_ORDER.filter((k) => (status?.kinds[k] ?? 0) > 0)
  const joinsTo = (get<Array<Record<string, unknown>>>(card, "relates", "joins_to") ?? [])
  const joinedBy = (get<Array<Record<string, unknown>>>(card, "relates", "joined_by") ?? [])
  const measured = get<Record<string, unknown>>(card, "measured_by") ?? {}
  const drift = get<Record<string, unknown>>(card, "drift") ?? {}
  const driftFlags = Array.isArray(drift.flags) ? (drift.flags as string[]) : []
  const personal = get<string[]>(card, "personal_data") ?? []
  const provenance = get<Record<string, unknown>>(card, "provenance") ?? {}
  const stated = provenance.stated_by_people as Record<string, unknown> | undefined
  const scores = get<Record<string, number>>(card, "scores") ?? {}
  const roleCounts = get<Record<string, number>>(card, "column_roles") ?? {}
  const succession = asSuccession(get(card, "succession"))
  const caveats = (get<Array<Record<string, unknown>>>(card, "caveats") ?? []).filter((c) => c && c.text)
  const collisions = asCollisions(get(card, "vocabulary"))
  const boundTerms = (get<Array<Record<string, unknown>>>(card, "vocabulary", "bound_terms") ?? []).map((t) => ({ term: String(t.term ?? ""), gloss: t.gloss ? String(t.gloss) : null }))
  // Plain derivation, not a hook: two early returns sit above this line, so a
  // useMemo here changes the hook count between renders (React #310).
  const tableEnrichments = enrichments.filter((e) => e.relation === selected && e.status !== "proposed")
  const governedMetrics = (get<Array<Record<string, unknown>>>(card, "vocabulary", "governed_metrics") ?? []).map((g) => ({ metric: String(g.metric ?? ""), version: g.version ? Number(g.version) : null }))

  return (
    <div className="flex h-full flex-col text-foreground">
      <div className="shrink-0 border-b border-chrome-border/60 bg-chrome-bg/40 px-3 py-2">
        <div className="flex flex-wrap items-center gap-3 text-[11px]">
          <span className="text-[12px] font-semibold">Meaning</span>
          {status ? (
            <span className="text-chrome-text/70">
              {fmtCount(status.relations)} of {fmtCount(status.admitted)} tables described · {fmtCount(status.columns)} columns ·{" "}
              {fmtCount(status.links.verified ?? 0)} verified joins · {status.pending > 0 ? `${fmtCount(status.pending)} pending · ` : ""}
              ${status.costUsd.toFixed(2)} spent
              {status.lastRun?.finished_at ? ` · last sweep ${fmtAgo(new Date(String(status.lastRun.finished_at)).getTime())}` : ""}
            </span>
          ) : null}
          {narrative ? (
            <span className="text-chrome-text/70" title="Meaning's own narrative: one recognition call per source, one description per verified join cluster, Jev-reviewed">
              narrative: {narrative.sourcesRecognized} sources · {narrative.relationsNamed} tables named · {narrative.domainsUsable} of {narrative.domains} subjects usable
              {Object.keys(narrative.queue).length ? ` · queue ${Object.entries(narrative.queue).map(([k, n]) => `${n} ${k}`).join(", ")}` : ""}
              {` · $${narrative.spendTotalUsd.toFixed(2)}`}
            </span>
          ) : null}
          <span className="ml-auto flex items-center gap-2">
            <span className="inline-flex overflow-hidden rounded-base border border-chrome-border/60 text-[11px]">
              {(["tree", "tables"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className={`px-2 py-0.5 ${view === v ? "bg-chrome-bg text-foreground" : "text-chrome-text/60 hover:bg-chrome-bg/40"}`}
                  title={v === "tree" ? "the data as business nouns with counts" : "every described table, by source"}
                >
                  {v}
                </button>
              ))}
            </span>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="filter tables"
              className="rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px] text-foreground focus:outline-none"
            />
            <button
              type="button"
              onClick={() => void sweepNow()}
              disabled={sweeping}
              className="inline-flex items-center gap-1 rounded-base border border-chrome-border/60 px-2 py-0.5 text-[11px] hover:bg-chrome-bg disabled:opacity-50"
              title="Describe up to two minutes' worth of pending tables now"
            >
              <RefreshCw className={`h-3 w-3 ${sweeping ? "animate-spin" : ""}`} /> {sweeping ? "sweeping…" : "sweep now"}
            </button>
          </span>
        </div>
        {kindsPresent.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {kindsPresent.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKindFilter((cur) => (cur === k ? null : k))}
                className={`rounded-base border px-1.5 text-[10px] ${kindFilter === k ? "bg-chrome-bg" : ""} ${KIND_TONE[k] ?? ""}`}
              >
                {MEANING_KIND_LABEL[k] ?? k} {status?.kinds[k]}
              </button>
            ))}
          </div>
        ) : null}
        {domains.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1">
            <span className="self-center text-[10px] uppercase tracking-wide text-chrome-text/50">subjects</span>
            <button
              type="button"
              onClick={() => setDomainFilter((cur) => (cur === "__unplaced" ? null : "__unplaced"))}
              title="tables no subject claimed yet (the judge said unclear, or nothing has run)"
              className={`rounded-base border border-chrome-border/60 px-1.5 text-[10px] text-chrome-text/60 ${domainFilter === "__unplaced" ? "bg-chrome-bg" : ""}`}
            >
              unplaced {rows.filter((r) => !r.domainKey).length}
            </button>
            {domains.slice(0, 40).map((d) => (
              <button
                key={d.key}
                type="button"
                onClick={() => setDomainFilter((cur) => (cur === d.key ? null : d.key))}
                title={`${d.about ?? ""}${d.review ? ` · judge: ${Math.round((d.review.makes_sense ?? 0) * 100)}% ${d.review.kind ?? ""}` : ""}`}
                className={`rounded-base border px-1.5 text-[10px] ${domainFilter === d.key ? "bg-chrome-bg" : ""} ${d.usable ? "border-emerald-400/40 text-emerald-200" : d.status === "error" ? "border-rose-400/40 text-rose-300" : "border-chrome-border/60 text-chrome-text/60"}`}
              >
                {d.name ?? d.key} {d.members.length}
              </button>
            ))}
          </div>
        ) : null}
        {error ? <div className="mt-1 text-[11px] text-rose-300">{error}</div> : null}
      </div>

      {view === "tree" ? (
        <MeaningTree
          connectionId={activeConnectionId}
          initialSubject={domainFilter && domainFilter !== "__unplaced" ? domainFilter : null}
          onPickTable={(relation) => {
            setSelected(relation)
            setView("tables")
          }}
          onOpenTable={onOpenTable}
        />
      ) : (
      <div className="flex min-h-0 flex-1">
        <div className="w-[340px] shrink-0 overflow-auto border-r border-chrome-border/40">
          {loading && !rows.length ? (
            <div className="p-3 text-[11px] text-chrome-text/60">Loading…</div>
          ) : !rows.length ? (
            <div className="p-3 text-[11px] leading-relaxed text-chrome-text/60">
              Nothing described yet. The sweep runs every ten minutes for two minutes; press <em>sweep now</em> to start.
            </div>
          ) : (
            grouped.map(([source, list]) => {
              const open = !closedSources.has(source)
              return (
                <div key={source}>
                  <button
                    type="button"
                    onClick={() => toggleSource(source)}
                    className="flex w-full items-center gap-1 border-b border-chrome-border/30 bg-chrome-bg/30 px-2 py-1 text-left text-[11px] font-semibold"
                  >
                    <CaretRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
                    <span className="truncate">{source}</span>
                    <span className="ml-auto text-[10px] font-normal text-chrome-text/60">{list.length}</span>
                  </button>
                  {open
                    ? list.map((r) => (
                        <button
                          key={r.relation}
                          type="button"
                          onClick={() => setSelected(r.relation)}
                          className={`flex w-full flex-col gap-0.5 border-b border-chrome-border/20 px-2 py-1.5 text-left hover:bg-chrome-bg/40 ${selected === r.relation ? "bg-chrome-bg/60" : ""}`}
                        >
                          <span className="flex items-center gap-1.5">
                            <span className="truncate font-mono text-[11px]">{r.rel}</span>
                            {r.overridden ? <span className="text-[10px] text-amber-300" title="stated by a person">✎</span> : null}
                            <span className="ml-auto flex items-center gap-1.5">
                              {r.subjectRole && r.subjectRole !== "core" ? <span className="text-[10px] text-chrome-text/50" title={`role in its subject: ${r.subjectRole}`}>{r.subjectRole === "configuration" ? "cfg" : r.subjectRole === "plumbing" ? "plumb" : "sup"}</span> : null}
                              {r.measuredBy > 0 ? <span className="text-[10px] text-emerald-300" title="metrics or cubes read this table">◆</span> : null}
                              {(r.driftSeverity ?? 0) >= 0.3 ? <span className="text-[10px] text-amber-300" title={r.driftFlags.join(", ")}>drift</span> : null}
                              <KindChip kind={r.kind} />
                            </span>
                          </span>
                          <span className="flex items-center gap-2 text-[10px] text-chrome-text/60">
                            {r.rows != null ? <span>{fmtCount(r.rows)} rows</span> : null}
                            {r.touches30d ? <span>{fmtCount(r.touches30d)} touches</span> : null}
                            <Confidence value={r.confidence} />
                          </span>
                        </button>
                      ))
                    : null}
                </div>
              )
            })
          )}
        </div>

        <div className="min-w-0 flex-1 overflow-auto p-3">
          {!selected ? (
            <EmptyState title="Pick a table" body="Every described table answers four questions: what is this, how does it relate, does it deserve metrics, does it drift." />
          ) : !card ? (
            <div className="text-[11px] text-chrome-text/60">{cardBusy ? "Loading…" : "Not described yet."}</div>
          ) : (
            <div className="flex flex-col gap-3">
              <header className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-[15px] font-semibold">{String(get(card, "name") ?? selectedRow?.rel ?? selected)}</h2>
                    <KindChip kind={String(get(card, "kind") ?? "") || null} />
                    <Confidence value={(get<number>(card, "confidence") ?? null)} />
                  </div>
                  <div className="font-mono text-[11px] text-chrome-text/60">
                    <button type="button" className="hover:underline" onClick={() => onOpenTable(selected.split(".")[0], selected.split(".")[1])}>{selected}</button>
                    {get(card, "source", "system") ? <span> · {String(get(card, "source", "system"))} · {String(get(card, "source", "role") ?? "")}</span> : null}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button type="button" onClick={() => void rejudge()} disabled={cardBusy} className="rounded-base border border-chrome-border/60 px-2 py-0.5 text-[11px] hover:bg-chrome-bg disabled:opacity-50">
                    re-judge
                  </button>
                  <button type="button" onClick={() => setFix(fix ? null : { field: "business_name", value: "", reason: "" })} className="rounded-base border border-amber-400/50 px-2 py-0.5 text-[11px] text-amber-200 hover:bg-chrome-bg">
                    this is wrong
                  </button>
                </div>
              </header>

              {fix ? (
                <div className="flex flex-wrap items-center gap-2 rounded-base border border-amber-400/40 bg-amber-400/5 p-2 text-[11px]">
                  <select value={fix.field} onChange={(e) => setFix({ ...fix, field: e.target.value })} className="rounded-base border border-chrome-border/60 bg-background px-1 py-0.5">
                    <option value="business_name">the name people use</option>
                    <option value="what">what it is</option>
                    <option value="kind">its kind</option>
                    <option value="domain_key">its domain</option>
                    <option value="ignore">ignore this table</option>
                    <option value="succession">it replaced another table (cutover)</option>
                    <option value="caveat">a caveat readers must see</option>
                  </select>
                  {fix.field === "kind" ? (
                    <select value={fix.value} onChange={(e) => setFix({ ...fix, value: e.target.value })} className="rounded-base border border-chrome-border/60 bg-background px-1 py-0.5">
                      <option value="">choose…</option>
                      {KIND_ORDER.map((k) => <option key={k} value={k}>{MEANING_KIND_LABEL[k]}</option>)}
                    </select>
                  ) : fix.field === "succession" ? (
                    <>
                      <input value={fix.value} onChange={(e) => setFix({ ...fix, value: e.target.value })} placeholder="replaced: schema.table, schema.table" className="min-w-[220px] rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 font-mono text-[11px]" />
                      <input type="date" value={fix.since ?? ""} onChange={(e) => setFix({ ...fix, since: e.target.value })} className="rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px]" title="effective from" />
                      <select value={fix.federation ?? "switch"} onChange={(e) => setFix({ ...fix, federation: e.target.value })} className="rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px]" title="how to read across the cutover">
                        <option value="switch">switch: old before, new after</option>
                        <option value="union">union: read both</option>
                        <option value="mapped">mapped: needs a crosswalk</option>
                      </select>
                      <input value={fix.note ?? ""} onChange={(e) => setFix({ ...fix, note: e.target.value })} placeholder="what readers must know (comparability, backfill…)" className="min-w-[260px] flex-1 rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px]" />
                    </>
                  ) : fix.field === "caveat" ? (
                    <>
                      <input value={fix.value} onChange={(e) => setFix({ ...fix, value: e.target.value })} placeholder="the caveat, one or two sentences" className="min-w-[280px] flex-1 rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px]" />
                      <input type="date" value={fix.since ?? ""} onChange={(e) => setFix({ ...fix, since: e.target.value })} className="rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5 text-[11px]" title="as of (optional)" />
                    </>
                  ) : fix.field !== "ignore" ? (
                    <input value={fix.value} onChange={(e) => setFix({ ...fix, value: e.target.value })} placeholder="value" className="min-w-[220px] flex-1 rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5" />
                  ) : null}
                  <input value={fix.reason} onChange={(e) => setFix({ ...fix, reason: e.target.value })} placeholder="why (optional)" className="min-w-[160px] rounded-base border border-chrome-border/60 bg-background px-1.5 py-0.5" />
                  <button type="button" onClick={() => void submitFix()} className="rounded-base border border-amber-400/60 px-2 py-0.5 text-amber-200 hover:bg-chrome-bg">state it</button>
                  <button type="button" onClick={() => setFix(null)} className="px-1 text-chrome-text/60 hover:text-foreground">cancel</button>
                </div>
              ) : null}

              <Section title="What is this?" hint={stated ? "stated by a person" : "described from structure + a small judge"}>
                <p>{String(get(card, "what") ?? "")}</p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-chrome-text/70">
                  {get(card, "keys") ? <span>keys: <span className="font-mono">{(get<string[]>(card, "keys") ?? []).join(", ")}</span></span> : null}
                  {get(card, "time", "column") ? <span>time: <span className="font-mono">{String(get(card, "time", "column"))}</span> ({String(get(card, "time", "grain") ?? "")})</span> : null}
                  {get(card, "custom_fields") ? <span>{String(get(card, "custom_fields"))} custom fields</span> : null}
                  {personal.length ? <span className="text-rose-300">personal data: {personal.join(", ")}</span> : null}
                </div>
                {Object.keys(roleCounts).length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {Object.entries(roleCounts).sort((a, b) => b[1] - a[1]).map(([role, n]) => (
                      <span key={role} className="rounded-base border border-chrome-border/50 px-1.5 text-[10px] text-chrome-text/70">{n} {MEANING_ROLE_LABEL[role] ?? role}</span>
                    ))}
                  </div>
                ) : null}
              </Section>

              {(succession || caveats.length || collisions.length || boundTerms.length || governedMetrics.length) ? (
                <Section title="What people have stated" hint="overrides with a name and a reason; they win over the judge and every reader sees them">
                  {succession?.supersedes.length ? (
                    <div className="text-[11px]">
                      <span className="rounded-base border border-amber-400/40 px-1 text-[10px] text-amber-200">succession</span>{" "}
                      replaces <span className="font-mono">{succession.supersedes.join(", ")}</span>
                      {succession.since ? <> from <b>{succession.since}</b></> : null}
                      {succession.federation ? <span className="text-chrome-text/60"> · {succession.federation}</span> : null}
                      {succession.note ? <div className="mt-0.5 text-chrome-text/75">{succession.note}</div> : null}
                    </div>
                  ) : null}
                  {succession?.supersededBy.map((b) => (
                    <div key={b.by} className="text-[11px]">
                      <span className="rounded-base border border-amber-400/40 px-1 text-[10px] text-amber-200">superseded</span>{" "}
                      by <button type="button" className="font-mono hover:underline" onClick={() => setSelected(b.by)}>{b.by}</button>
                      {b.since ? <> from <b>{b.since}</b></> : null}
                      {b.note ? <div className="mt-0.5 text-chrome-text/75">{b.note}</div> : null}
                    </div>
                  ))}
                  {caveats.map((c) => (
                    <div key={String(c.id)} className="flex items-start gap-2 text-[11px]">
                      <span className="rounded-base border border-rose-400/40 px-1 text-[10px] text-rose-200">caveat</span>
                      <span className="flex-1">{String(c.text)}{c.as_of ? <span className="text-chrome-text/60"> (as of {String(c.as_of)})</span> : null}
                        <span className="text-chrome-text/50"> — {String(c.stated_by ?? "")}</span></span>
                      <button type="button" className="text-[10px] text-chrome-text/50 hover:text-foreground" title="retire this caveat"
                        onClick={() => void retireStatement(Number(c.id))}>retire</button>
                    </div>
                  ))}
                  {collisions.map((t) => (
                    <div key={t.term} className="text-[11px]">
                      <span className="rounded-base border border-sky-400/40 px-1 text-[10px] text-sky-200">term</span>{" "}
                      the business term <b>“{t.term}”</b> means <span className="font-mono">{t.means}</span>, not rows of this table
                      {t.gloss ? <div className="mt-0.5 text-chrome-text/60">{t.gloss}</div> : null}
                    </div>
                  ))}
                  {boundTerms.map((t) => (
                    <div key={t.term} className="text-[11px]">
                      <span className="rounded-base border border-sky-400/40 px-1 text-[10px] text-sky-200">term</span>{" "}
                      <b>“{t.term}”</b> means this table{t.gloss ? <span className="text-chrome-text/60"> — {t.gloss}</span> : null}
                    </div>
                  ))}
                  {governedMetrics.length ? (
                    <div className="text-[11px] text-chrome-text/75">
                      governed metrics reading this table:{" "}
                      {governedMetrics.map((g) => <span key={g.metric} className="mr-2 font-mono">{g.metric}{g.version ? ` v${g.version}` : ""}</span>)}
                    </div>
                  ) : null}
                </Section>
              ) : null}

              {tableEnrichments.length ? (
                <Section title="Label dimensions" hint="Classifications Meaning proposed and Jev answered, row by row">
                  <div className="space-y-2">
                    {tableEnrichments.map((e) => {
                      const total = e.distribution.reduce((sum, d) => sum + d.n, 0) || 1
                      return (
                        <div key={e.id} className="rounded-base border border-chrome-border/40 p-2">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="text-[12px] font-medium">{e.labelName}</span>
                            <span className="font-mono text-[10px] text-chrome-text/60">{e.questionKind}</span>
                            <span className="text-[10px] text-chrome-text/60">from {e.columns.join(", ")}</span>
                            {e.status === "promoted" ? <span className="rounded-base border border-emerald-400/40 px-1 text-[10px] text-emerald-200/90">promoted</span> : null}
                            {e.status === "retired" ? <span className="rounded-base border border-chrome-border/50 px-1 text-[10px] text-chrome-text/50">retired: {e.error}</span> : null}
                            {e.judgeScore != null ? <span className="text-[10px] text-chrome-text/60">judge {e.judgeScore.toFixed(2)}</span> : null}
                            <span className="ml-auto text-[10px] text-chrome-text/50">
                              {e.materializedN.toLocaleString()} rows{e.sampleN && e.sampleN !== e.materializedN ? ` (sampled ${e.sampleN.toLocaleString()})` : ""}
                            </span>
                          </div>
                          {e.why ? <p className="mt-1 text-[11px] text-chrome-text/70">{e.why}</p> : null}
                          {e.distribution.length ? (
                            <div className="mt-1.5 space-y-0.5">
                              {e.distribution.map((d) => (
                                <div key={d.label} className="flex items-center gap-2">
                                  <span className="w-40 shrink-0 truncate font-mono text-[10px]">{d.label}</span>
                                  <span className="h-2 rounded-base bg-emerald-400/40" style={{ width: `${Math.max(2, Math.round((d.n / total) * 160))}px` }} />
                                  <span className="text-[10px] text-chrome-text/60">{d.n.toLocaleString()}</span>
                                </div>
                              ))}
                            </div>
                          ) : null}
                          {e.viewName ? (
                            <div className="mt-1.5 truncate font-mono text-[10px] text-chrome-text/60" title={e.expression ?? undefined}>
                              GROUP BY {e.viewName}
                            </div>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                </Section>
              ) : null}

              <Section title="Narrative" hint="Meaning's own words beside the old excavator's, for the same table">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-base border border-emerald-400/30 p-2">
                    <div className="mb-1 text-[10px] uppercase tracking-wide text-emerald-200/80">Meaning (new)</div>
                    {get(card, "source", "recognized_as") ? <div className="text-[11px] text-chrome-text/70">source recognized as <span className="text-foreground">{String(get(card, "source", "recognized_as"))}</span></div> : null}
                    <div className="mt-1 text-[12px]">
                      <span className="font-medium">{String(get(card, "name") ?? "")}</span>
                      {get(card, "object_role") ? <span className="ml-1 rounded-base border border-chrome-border/50 px-1 text-[10px] text-chrome-text/70">{String(get(card, "object_role"))}</span> : null}
                      {get(card, "package") ? <span className="ml-1 text-[10px] text-chrome-text/60">package: {String(get(card, "package"))}</span> : null}
                    </div>
                    {get(card, "what_from_structure") ? <p className="mt-1 text-[12px]">{String(get(card, "what"))}</p> : <p className="mt-1 text-[11px] text-chrome-text/50">not narrated yet (recognition runs once the source's cards are complete)</p>}
                    {get(card, "domain") ? (
                      <div className="mt-2 border-t border-chrome-border/30 pt-1.5">
                        <div className="flex flex-wrap items-center gap-2 text-[12px]">
                          <span className="font-medium">{String(get(card, "domain", "name") ?? "")}</span>
                          {get(card, "domain", "role") ? <span className="rounded-base border border-chrome-border/50 px-1 text-[10px] text-chrome-text/70">{String(get(card, "domain", "role"))} table</span> : null}
                          {get(card, "domain", "status") === "done" ? (get(card, "domain", "usable") ? <span className="text-[10px] text-emerald-300">usable</span> : <span className="text-[10px] text-amber-300">not usable</span>) : <span className="text-[10px] text-chrome-text/50">dossier pending</span>}
                          {get(card, "domain", "review", "makes_sense") != null ? <span className="text-[10px] text-chrome-text/60">judge {Math.round(Number(get(card, "domain", "review", "makes_sense")) * 100)}%</span> : null}
                          {get(card, "domain", "members") ? <span className="text-[10px] text-chrome-text/50">{String(get(card, "domain", "core") ?? 0)} core of {String(get(card, "domain", "members"))} tables</span> : null}
                        </div>
                        {(get<Array<Record<string, unknown>>>(card, "domain", "areas") ?? []).length ? (
                          <div className="mt-1 text-[11px] text-chrome-text/70">area: {(get<Array<Record<string, unknown>>>(card, "domain", "areas") ?? []).map((a) => String(a.name)).join(" · ")}</div>
                        ) : null}
                        <p className="mt-1 text-[12px] text-chrome-text/85">{String(get(card, "domain", "about") ?? "")}</p>
                        {(get<string[]>(card, "domain", "questions") ?? []).length ? (
                          <ul className="mt-1 list-disc pl-4 text-[11px] text-chrome-text/75">
                            {(get<string[]>(card, "domain", "questions") ?? []).map((q, i) => <li key={i}>{q}</li>)}
                          </ul>
                        ) : null}
                        {(get<Array<Record<string, string>>>(card, "domain", "candidate_metrics") ?? []).length ? (
                          <div className="mt-1 text-[11px] text-chrome-text/75">
                            metrics: {(get<Array<Record<string, string>>>(card, "domain", "candidate_metrics") ?? []).map((m) => m.name).join(" · ")}
                          </div>
                        ) : null}
                        {(get<string[]>(card, "domain", "gaps") ?? []).length ? (
                          <div className="mt-1 text-[11px] text-amber-200/80">gaps: {(get<string[]>(card, "domain", "gaps") ?? []).join(" · ")}</div>
                        ) : null}
                        <button type="button" className="mt-1 text-[10px] text-chrome-text/60 hover:underline" onClick={() => setDomainFilter(String(get(card, "domain", "key")))}>
                          show the {String(get(card, "domain", "members") ?? "")} tables in this subject
                        </button>
                      </div>
                    ) : <div className="mt-2 text-[11px] text-chrome-text/50">no subject yet (the company map places tables once every source is recognized; the judge marks a few as unclear)</div>}
                  </div>
                  <div className="rounded-base border border-chrome-border/40 p-2">
                    <div className="mb-1 text-[10px] uppercase tracking-wide text-chrome-text/60">Excavator (old)</div>
                    {(get<Array<Record<string, unknown>>>(card, "measured_by", "domains") ?? []).length ? (
                      (get<Array<Record<string, unknown>>>(card, "measured_by", "domains") ?? []).map((d, i) => (
                        <div key={i} className="text-[12px]">
                          <span className="font-medium">{String(d.name ?? d.key)}</span>
                          <span className="ml-1 text-[10px] text-chrome-text/60">{String(d.status ?? "")}</span>
                        </div>
                      ))
                    ) : <div className="text-[11px] text-chrome-text/50">no value system claims this table</div>}
                    <div className="mt-1 text-[10px] text-chrome-text/45">nightly excavation, strong model, human-gated promotion; still running beside Meaning</div>
                  </div>
                </div>
              </Section>

              <Section title="How does it relate?" hint="joins verified by sampling real values">
                {joinsTo.length || joinedBy.length ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-chrome-text/50">points at</div>
                      {joinsTo.length ? joinsTo.map((j, i) => (
                        <div key={i} className="flex items-center gap-1 font-mono text-[11px]">
                          <span className="text-chrome-text/70">{String(j.via)}</span>
                          <span className="text-chrome-text/40">→</span>
                          <button type="button" className="hover:underline" onClick={() => setSelected(String(j.to))}>{String(j.to)}</button>
                          <span className={`ml-auto text-[10px] ${j.state === "verified" ? "text-emerald-300" : "text-chrome-text/50"}`}>
                            {j.state === "verified" ? `${Math.round(Number(j.match ?? 0) * 100)}% match` : String(j.state)}
                          </span>
                        </div>
                      )) : <div className="text-[11px] text-chrome-text/50">none found</div>}
                    </div>
                    <div>
                      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-chrome-text/50">pointed at by</div>
                      {joinedBy.length ? joinedBy.map((j, i) => (
                        <div key={i} className="flex items-center gap-1 font-mono text-[11px]">
                          <button type="button" className="hover:underline" onClick={() => setSelected(String(j.from))}>{String(j.from)}</button>
                          <span className="text-chrome-text/70">.{String(j.via)}</span>
                          <span className="ml-auto text-[10px] text-emerald-300">{Math.round(Number(j.match ?? 0) * 100)}%</span>
                        </div>
                      )) : <div className="text-[11px] text-chrome-text/50">nothing verified yet</div>}
                    </div>
                  </div>
                ) : (
                  <div className="text-[11px] text-chrome-text/50">No join candidates yet: joins appear once the tables it points at are described too.</div>
                )}
                {get(card, "domain") ? <div className="mt-1 text-[11px] text-chrome-text/70">domain: {String(get(card, "domain"))}</div> : null}
              </Section>

              <Section title="Does it deserve metrics?" hint={`judge: metric-worthy ${Math.round((scores.metric_worthy ?? 0) * 100)}% · plumbing ${Math.round((scores.plumbing ?? 0) * 100)}%`}>
                <p className="font-medium">{String(get(card, "deserves_metrics") ?? "unknown")}</p>
                {Object.keys(measured).length ? (
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-chrome-text/70">
                    {Array.isArray(measured.metrics) ? <span>metrics: <span className="font-mono">{(measured.metrics as string[]).join(", ")}</span></span> : null}
                    {Array.isArray(measured.cubes) ? <span>cubes: <span className="font-mono">{(measured.cubes as string[]).join(", ")}</span></span> : null}
                    {Array.isArray(measured.dashboards) ? <span>dashboards: {(measured.dashboards as string[]).join(", ")}</span> : null}
                  </div>
                ) : null}
              </Section>

              <Section title="Does it drift?" hint={drift.observed_at ? `last two crawls, latest ${fmtAgo(new Date(String(drift.observed_at)).getTime())}` : "no history yet"}>
                {driftFlags.length ? (
                  <p>
                    <span className="text-amber-300">{driftFlags.join(", ")}</span>
                    {drift.rows_pct != null ? <span className="text-chrome-text/70"> · rows {Number(drift.rows_pct) >= 0 ? "+" : ""}{String(drift.rows_pct)}% ({fmtCount(Number(drift.rows_before ?? 0))} → {fmtCount(Number(drift.rows ?? 0))})</span> : null}
                    {drift.null_spikes ? <span className="text-chrome-text/70"> · {String(drift.null_spikes)} columns went null</span> : null}
                    {drift.ndv_shifts ? <span className="text-chrome-text/70"> · {String(drift.ndv_shifts)} columns changed shape</span> : null}
                  </p>
                ) : (
                  <p className="text-chrome-text/70">
                    Steady{drift.rows != null ? `: ${fmtCount(Number(drift.rows))} rows` : ""}{drift.rows_pct != null ? ` (${Number(drift.rows_pct) >= 0 ? "+" : ""}${String(drift.rows_pct)}% since the previous crawl)` : ""}.
                  </p>
                )}
                {get(card, "usage", "touches_30d") ? (
                  <div className="mt-1 text-[11px] text-chrome-text/70">
                    used {fmtCount(Number(get(card, "usage", "touches_30d")))} times by {String(get(card, "usage", "users") ?? "?")} people in 30 days
                  </div>
                ) : null}
              </Section>

              <Section title="Columns" hint={`${columns.length} judged`}>
                <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 gap-y-0.5 font-mono text-[11px]">
                  {columns.filter((c) => c.role !== "unused").slice(0, 80).map((c) => (
                    <div key={c.col} className="contents">
                      <span className="truncate">{c.col}{c.pii != null && c.pii >= 0.6 ? <span className="ml-1 text-[10px] text-rose-300">pii</span> : null}</span>
                      <span className="text-chrome-text/70">{MEANING_ROLE_LABEL[c.role ?? ""] ?? c.role ?? "?"}</span>
                      <span className="text-chrome-text/50">{c.refTarget ? `→ ${c.refTarget}` : c.role === "category" && c.topValues.length ? c.topValues.slice(0, 3).map((t) => t.v).join(" · ") : ""}</span>
                      <span className="text-right text-[10px] text-chrome-text/40">{c.basis}</span>
                    </div>
                  ))}
                </div>
                {columns.some((c) => c.role === "unused") ? (
                  <div className="mt-1 text-[10px] text-chrome-text/50">{columns.filter((c) => c.role === "unused").length} unused columns (all null or constant) hidden</div>
                ) : null}
              </Section>

              <div className="text-[10px] text-chrome-text/45">
                {String(provenance.note ?? "")}{provenance.judged_by ? ` · ${String(provenance.judged_by)}` : ""}{provenance.judged_at ? ` · ${fmtAgo(new Date(String(provenance.judged_at)).getTime())}` : ""}
              </div>
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  )
}
