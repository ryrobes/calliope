"use client"

import { useEffect, useMemo, useState } from "react"
import { fmtCount } from "./instruments"
import {
  fetchMeaningSubjects,
  fetchMeaningTree,
  MEANING_KIND_LABEL,
  type MeaningConcept,
  type MeaningNoun,
  type MeaningSubject,
  type MeaningTree as MeaningTreeData,
  type MeaningTreeMetric,
} from "@/lib/rvbbit/meaning"

/**
 * The meaning tree — the warehouse as business nouns with counts, not tables.
 * Subject → areas → concepts and nouns. A noun is one table: what it is, how
 * many, its dimensions (with value counts), what hangs off it (≈N per parent),
 * what it points at, and the metrics it carries or implies. A concept is the
 * fold over nouns that are the same thing at several grains and layers (the
 * same ad report at ad / campaign / advertiser grain, a raw table and its dbt
 * view over the same rows): one card, the canonical table to read first, the
 * other members listed, the metrics once. A projection over the Meaning cards
 * (migrations 0442, 0444, 0445); nothing here is a second model.
 */

const TONE: Record<string, string> = {
  entity_master: "text-cyan-300 border-cyan-400/40",
  event_log: "text-violet-300 border-violet-400/40",
  transactional_fact: "text-emerald-300 border-emerald-400/40",
  aggregate_or_cube: "text-sky-300 border-sky-400/40",
}

const METRIC_KIND_LABEL: Record<string, string> = {
  count: "count",
  breakdown: "breakdown",
  sum: "total",
  avg: "average",
  per_entity: "per entity",
}

const LAYER_LABEL: Record<string, string> = {
  source: "source",
  package: "dbt package",
  intermediate: "intermediate",
  rollup: "rollup",
  reporting: "reporting",
  mart: "mart",
}

interface MeaningTreeProps {
  connectionId: string
  initialSubject?: string | null
  onPickTable: (relation: string) => void
  onOpenTable: (schema: string, name: string) => void
}

/**
 * One metric line. A dossier candidate that is callable shows its registry
 * status instead of "dossier": `implied` (the meaning layer registered it,
 * nobody has confirmed it) or `governed`; the title carries the metric() name.
 */
function Metric({ m, derived }: { m: MeaningTreeMetric; derived?: boolean }) {
  const callable = !derived && m.metric
  const badge = derived
    ? METRIC_KIND_LABEL[m.kind ?? ""] ?? m.kind ?? "derived"
    : m.status === "implied" ? "implied" : m.status === "governed" ? "governed" : m.status === "retired" ? "retired" : "dossier"
  const tone = derived
    ? "border-chrome-border/50 text-chrome-text/50"
    : m.status === "governed" ? "border-emerald-400/60 text-emerald-200"
    : m.status === "implied" ? "border-amber-400/50 text-amber-200"
    : m.status === "retired" ? "border-chrome-border/40 text-chrome-text/40"
    : "border-emerald-400/40 text-emerald-300"
  const title = [m.formula, m.why, callable ? `metric(${m.metric})${m.status === "implied" ? " — implied by the meaning layer, not yet confirmed by a person" : ""}` : null]
    .filter(Boolean)
    .join(" — ")
  return (
    <div className="flex items-baseline gap-1.5 text-[11px]" title={title}>
      <span className={`shrink-0 rounded-base border px-1 text-[9px] uppercase tracking-wide ${tone}`}>{badge}</span>
      <span className="truncate">{m.name}</span>
      {callable ? <span className="shrink-0 font-mono text-[10px] text-chrome-text/45">{m.metric}</span> : null}
      {m.grain ? <span className="ml-auto shrink-0 text-[10px] text-chrome-text/50">{m.grain}</span> : null}
    </div>
  )
}

function KindChip({ kind }: { kind: string | null }) {
  if (!kind) return null
  return <span className={`rounded-base border px-1 ${TONE[kind] ?? "border-chrome-border text-chrome-text/70"}`}>{MEANING_KIND_LABEL[kind] ?? kind}</span>
}

function NounCard({ noun, onPick, onOpen, onJump }: { noun: MeaningNoun; onPick: () => void; onOpen: () => void; onJump: (relation: string) => void }) {
  const [more, setMore] = useState(false)
  const has = more ? noun.has : noun.has.slice(0, 4)
  const dims = more ? noun.dimensions : noun.dimensions.slice(0, 4)
  const derived = more ? noun.derivedMetrics : noun.derivedMetrics.slice(0, 3)
  const hidden = noun.has.length - has.length + noun.dimensions.length - dims.length + noun.derivedMetrics.length - derived.length
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 rounded-base border bg-chrome-bg/20 p-2 ${noun.role === "core" ? "border-chrome-border" : "border-chrome-border/40"}`}>
      <div className="flex items-baseline gap-2">
        <button type="button" onClick={onPick} className="min-w-0 truncate text-left text-[13px] font-semibold hover:underline" title={`${noun.relation} — open its card`}>
          {noun.name}
        </button>
        <span className="ml-auto shrink-0 font-mono text-[13px] tabular-nums">{noun.count != null ? fmtCount(noun.count) : "?"}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[10px]">
        <KindChip kind={noun.kind} />
        {noun.role === "core" ? <span className="rounded-base border border-amber-400/40 px-1 text-amber-200">core</span> : null}
        {noun.time ? <span className="text-chrome-text/50" title="time column">by {noun.time}</span> : null}
        {noun.drift.length ? <span className="text-amber-300" title={noun.drift.join(", ")}>drift</span> : null}
        <button type="button" onClick={onOpen} className="ml-auto font-mono text-[10px] text-chrome-text/50 hover:text-chrome-text" title="open the table">
          {noun.relation}
        </button>
      </div>
      {noun.what ? <div className="line-clamp-2 text-[11px] leading-snug text-chrome-text/75" title={noun.what}>{noun.what}</div> : null}
      {noun.succession?.supersedes.length ? (
        <div className="text-[10px] text-amber-200" title={noun.succession.note ?? undefined}>
          replaces {noun.succession.supersedes.map((r) => <button key={r} type="button" className="mr-1 font-mono hover:underline" onClick={() => onJump(r)}>{r}</button>)}
          {noun.succession.since ? `from ${noun.succession.since}` : ""}
        </div>
      ) : null}
      {noun.succession?.supersededBy.map((b) => (
        <div key={b.by} className="text-[10px] text-amber-200" title={b.note ?? undefined}>
          superseded by <button type="button" className="font-mono hover:underline" onClick={() => onJump(b.by)}>{b.by}</button>{b.since ? ` from ${b.since}` : ""}
        </div>
      ))}
      {noun.collisions.map((t) => (
        <div key={t.term} className="text-[10px] text-sky-200" title={t.gloss ?? undefined}>“{t.term}” means {t.means}, not this table</div>
      ))}

      {dims.length ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">dimensions</div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {dims.map((d) => (
              <span
                key={d.name}
                className="rounded-base border border-chrome-border/50 px-1 text-[10px]"
                title={d.top.length ? d.top.map((t) => `${t.v} (${fmtCount(t.n)})`).join(" · ") : undefined}
              >
                {d.name.replace(/_c$/, "").replace(/_/g, " ")}
                {d.values != null ? <span className="text-chrome-text/50"> {d.values}</span> : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {has.length ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">has</div>
          <div className="mt-0.5 flex flex-col gap-0.5">
            {has.map((e) => (
              <button
                key={`${e.relation}.${e.via}`}
                type="button"
                onClick={() => onJump(e.relation)}
                className="flex items-baseline gap-1.5 text-left text-[11px] hover:bg-chrome-bg/40"
                title={`${e.relation} via ${e.via ?? "?"}`}
              >
                <span className="truncate">{e.name}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-chrome-text/70">{e.count != null ? fmtCount(e.count) : "?"}</span>
                {e.perParent != null ? <span className="w-14 shrink-0 text-right font-mono text-[10px] tabular-nums text-chrome-text/50">≈{e.perParent} each</span> : <span className="w-14 shrink-0" />}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {noun.pointsAt.length ? (
        <div className="text-[10px] text-chrome-text/60">
          <span className="uppercase tracking-wide text-chrome-text/45">points at </span>
          {noun.pointsAt.slice(0, 5).map((e, i) => (
            <span key={`${e.relation}.${e.via}`}>
              {i ? ", " : ""}
              <button type="button" onClick={() => onJump(e.relation)} className="hover:underline" title={`via ${e.via ?? "?"}${e.relationship ? ` · ${e.relationship.replace(/_/g, "-")}` : ""}`}>
                {e.name}
              </button>
            </span>
          ))}
        </div>
      ) : null}

      {noun.metrics.length || derived.length || noun.measuredBy.length ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">metrics</div>
          <div className="mt-0.5 flex flex-col gap-0.5">
            {noun.metrics.map((m) => <Metric key={`d:${m.name}`} m={m} />)}
            {derived.map((m) => <Metric key={`x:${m.name}`} m={m} derived />)}
            {noun.measuredBy.length ? <div className="text-[10px] text-emerald-300/80">◆ read by {noun.measuredBy.join(", ")}</div> : null}
            {noun.measuredThrough.map((t) => (
              <button
                key={`t:${t.lens}`}
                type="button"
                onClick={() => onJump(t.lens)}
                className="text-left text-[10px] text-sky-300/80 hover:text-sky-200"
                title={`${t.lens}${t.grain ? ` · ${t.grain}` : ""}${t.metrics.length ? ` · ${t.metrics.join(", ")}` : ""}`}
              >
                ◆ read through {t.name} · {t.metrics.length} metric{t.metrics.length === 1 ? "" : "s"}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {noun.lens ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">lens</div>
          <div className="mt-0.5 flex flex-col gap-0.5 text-[10px] text-chrome-text/70">
            {noun.lens.grain ? <div title="grain">{noun.lens.grain}</div> : null}
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-chrome-text/50">from</span>
              {noun.lens.derivesFrom.map((d) => (
                <button key={d.relation} type="button" onClick={() => onJump(d.relation)} className="rounded-base border border-chrome-border/50 px-1 hover:bg-chrome-bg/40" title={d.relation}>
                  {d.name}
                </button>
              ))}
            </div>
            {noun.lens.metrics.length ? <div className="text-emerald-300/80">◆ {noun.lens.metrics.length} metric{noun.lens.metrics.length === 1 ? "" : "s"} read it: {noun.lens.metrics.map((m) => m.name).join(", ")}</div> : null}
            {noun.lens.refresh.at ? (
              <div className="text-chrome-text/50">
                refreshed {new Date(noun.lens.refresh.at).toLocaleString()}{noun.lens.refresh.dirty ? <span className="text-rose-300/80"> · stale</span> : null}{noun.lens.refresh.cron ? ` · ${noun.lens.refresh.cron}` : ""}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {hidden > 0 || more ? (
        <button type="button" onClick={() => setMore((v) => !v)} className="self-start text-[10px] text-chrome-text/50 hover:text-chrome-text">
          {more ? "less" : `${hidden} more`}
        </button>
      ) : null}
    </div>
  )
}

/**
 * A concept: the canonical noun's card, then the other tables that are the
 * same thing (with their layer, size, and "same rows as" when a view repeats a
 * table), then the metrics once. Members are clickable; the fold is the point.
 */
function ConceptCard({
  concept,
  canonical,
  onPick,
  onOpen,
  onJump,
}: {
  concept: MeaningConcept
  canonical: MeaningNoun | null
  onPick: (relation: string) => void
  onOpen: (relation: string) => void
  onJump: (relation: string) => void
}) {
  const [more, setMore] = useState(false)
  const others = concept.members.filter((m) => m.relation !== concept.canonical)
  const derived = more ? concept.derivedMetrics : concept.derivedMetrics.slice(0, 4)
  const dims = canonical ? (more ? canonical.dimensions : canonical.dimensions.slice(0, 4)) : []
  const layers = Object.entries(concept.layers).sort((a, b) => b[1] - a[1])
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-base border border-amber-400/30 bg-chrome-bg/30 p-2">
      <div className="flex items-baseline gap-2">
        <div className="min-w-0">
          {concept.family ? <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">{concept.family}</div> : null}
          <button type="button" onClick={() => onPick(concept.canonical)} className="min-w-0 truncate text-left text-[13px] font-semibold hover:underline" title={`${concept.canonical} — open the canonical table's card`}>
            {concept.name}
          </button>
        </div>
        <span className="ml-auto shrink-0 font-mono text-[13px] tabular-nums" title="rows in the canonical table">{concept.count != null ? fmtCount(concept.count) : "?"}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[10px]">
        <span className="rounded-base border border-amber-400/50 px-1 text-amber-200" title="one thing seen through several tables">{concept.tables} tables · one concept</span>
        <KindChip kind={concept.kind} />
        {layers.map(([layer, n]) => (
          <span key={layer} className="rounded-base border border-chrome-border/50 px-1 text-chrome-text/70">{n} {LAYER_LABEL[layer] ?? layer}</span>
        ))}
        <button type="button" onClick={() => onOpen(concept.canonical)} className="ml-auto font-mono text-[10px] text-chrome-text/50 hover:text-chrome-text" title="open the canonical table">
          {concept.canonical}
        </button>
      </div>
      {canonical?.what ? <div className="line-clamp-2 text-[11px] leading-snug text-chrome-text/75" title={canonical.what}>{canonical.what}</div> : null}

      <div>
        <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">also as</div>
        <div className="mt-0.5 flex flex-col gap-0.5">
          {others.map((m) => (
            <button
              key={m.relation}
              type="button"
              onClick={() => onJump(m.relation)}
              className="flex items-baseline gap-1.5 text-left text-[11px] hover:bg-chrome-bg/40"
              title={`${m.relation}${m.sameRowsAs ? ` — the same rows as ${m.sameRowsAs}` : ""}`}
            >
              <span className="truncate">{m.name}</span>
              <span className="shrink-0 text-[10px] text-chrome-text/45">{LAYER_LABEL[m.layer ?? ""] ?? m.layer}</span>
              {m.sameRowsAs ? <span className="shrink-0 text-[10px] text-amber-200/80" title={`same rows as ${m.sameRowsAs}`}>= same rows</span> : null}
              <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-chrome-text/70">{m.count != null ? fmtCount(m.count) : "?"}</span>
            </button>
          ))}
        </div>
      </div>

      {dims.length ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">dimensions</div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {dims.map((d) => (
              <span key={d.name} className="rounded-base border border-chrome-border/50 px-1 text-[10px]" title={d.top.length ? d.top.map((t) => `${t.v} (${fmtCount(t.n)})`).join(" · ") : undefined}>
                {d.name.replace(/_c$/, "").replace(/_/g, " ")}
                {d.values != null ? <span className="text-chrome-text/50"> {d.values}</span> : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {concept.metrics.length || derived.length ? (
        <div>
          <div className="text-[9px] uppercase tracking-wide text-chrome-text/45">metrics · once for the concept</div>
          <div className="mt-0.5 flex flex-col gap-0.5">
            {concept.metrics.map((m) => <Metric key={`d:${m.name}`} m={m} />)}
            {derived.map((m) => <Metric key={`x:${m.name}`} m={m} derived />)}
          </div>
        </div>
      ) : null}

      {concept.derivedMetrics.length > 4 || (canonical && canonical.dimensions.length > 4) || more ? (
        <button type="button" onClick={() => setMore((v) => !v)} className="self-start text-[10px] text-chrome-text/50 hover:text-chrome-text">
          {more ? "less" : "more"}
        </button>
      ) : null}
    </div>
  )
}

export function MeaningTree({ connectionId, initialSubject, onPickTable, onOpenTable }: MeaningTreeProps) {
  const [subjects, setSubjects] = useState<MeaningSubject[]>([])
  const [subjectKey, setSubjectKey] = useState<string | null>(initialSubject ?? null)
  const [tree, setTree] = useState<MeaningTreeData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    void fetchMeaningSubjects(connectionId).then((r) => {
      if (!live) return
      if (r.error) setError(r.error)
      const list = r.subjects ?? []
      setSubjects(list)
      setSubjectKey((cur) => cur ?? list[0]?.key ?? null)
    })
    return () => {
      live = false
    }
  }, [connectionId])

  useEffect(() => {
    if (!subjectKey) return
    let live = true
    setLoading(true)
    void fetchMeaningTree(connectionId, subjectKey).then((r) => {
      if (!live) return
      setLoading(false)
      if (r.error) setError(r.error)
      else setError(null)
      setTree(r.tree ?? null)
    })
    return () => {
      live = false
    }
  }, [connectionId, subjectKey])

  // the fold: which noun sits inside which concept, and the noun objects by relation
  const { memberOf, nounByRelation } = useMemo(() => {
    const memberOf = new Map<string, MeaningConcept>()
    for (const c of tree?.concepts ?? []) for (const m of c.members) memberOf.set(m.relation, c)
    const nounByRelation = new Map<string, MeaningNoun>()
    for (const a of tree?.areas ?? []) for (const n of a.nouns) nounByRelation.set(n.relation, n)
    return { memberOf, nounByRelation }
  }, [tree])

  // a card is drawn for each concept (anchored on its canonical table) and each noun outside one
  const anchorFor = (relation: string) => memberOf.get(relation)?.canonical ?? relation
  const drawn = useMemo(() => {
    const set = new Set<string>()
    for (const n of nounByRelation.keys()) set.add(anchorFor(n))
    return set
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberOf, nounByRelation])

  const jump = (relation: string) => {
    const anchor = anchorFor(relation)
    if (drawn.has(anchor)) {
      const el = document.getElementById(`meaning-noun-${anchor}`)
      el?.scrollIntoView({ block: "center", behavior: "smooth" })
      setFlash(anchor)
      window.setTimeout(() => setFlash((cur) => (cur === anchor ? null : cur)), 1200)
    } else {
      onPickTable(relation)
    }
  }

  const open = (relation: string) => {
    const [schema, ...rest] = relation.split(".")
    onOpenTable(schema, rest.join("."))
  }

  const conceptCount = tree?.concepts.length ?? 0

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-chrome-border/40 px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {subjects.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSubjectKey(s.key)}
              title={`${s.about ?? ""}${s.rows != null ? ` · ${fmtCount(s.rows)} rows across its nouns` : ""}`}
              className={`rounded-base border px-1.5 py-0.5 text-[11px] ${subjectKey === s.key ? "border-chrome-border bg-chrome-bg" : "border-chrome-border/50 text-chrome-text/70 hover:bg-chrome-bg/40"}`}
            >
              {s.name} <span className="text-chrome-text/50">{s.tables}</span>
            </button>
          ))}
          {!subjects.length && !error ? <span className="text-[11px] text-chrome-text/60">No subjects yet — the company map runs once every source is recognized.</span> : null}
        </div>
        {tree ? (
          <div className="mt-1.5 text-[11px] text-chrome-text/70">
            <span className="font-semibold text-foreground">{tree.subject}</span> · {tree.nouns} nouns drawn from {tree.tables} tables
            {conceptCount ? ` · ${conceptCount} concept${conceptCount === 1 ? "" : "s"} fold ${tree.folded} of them` : ""}
            {tree.lenses.length ? ` · ${tree.lenses.length} lens${tree.lenses.length === 1 ? "" : "es"}` : ""}
            {Object.keys(tree.roles).length ? ` · ${Object.entries(tree.roles).map(([k, n]) => `${n} ${k}`).join(", ")}` : ""}
            {tree.about ? <div className="mt-0.5 line-clamp-2 text-chrome-text/60" title={tree.about}>{tree.about}</div> : null}
          </div>
        ) : null}
        {error ? <div className="mt-1 text-[11px] text-rose-300">{error}</div> : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {loading && !tree ? (
          <div className="text-[11px] text-chrome-text/60">Loading…</div>
        ) : !tree ? null : (
          <div className="flex flex-col gap-4">
            {tree.areas.map((area) => {
              // concepts anchored here (canonical noun in this area) first, then the nouns outside any concept
              const items: Array<{ key: string; concept?: MeaningConcept; noun?: MeaningNoun }> = []
              for (const n of area.nouns) {
                const c = memberOf.get(n.relation)
                if (!c) items.push({ key: n.relation, noun: n })
                else if (c.canonical === n.relation) items.push({ key: n.relation, concept: c })
              }
              // a concept whose canonical table sits in another area but whose members are here is drawn where the canonical is
              const foldedHere = area.nouns.filter((n) => memberOf.has(n.relation) && memberOf.get(n.relation)!.canonical !== n.relation).length
              return (
                <section key={area.name}>
                  <div className="mb-1.5 flex items-baseline gap-2">
                    <h3 className="text-[12px] font-semibold">{area.name}</h3>
                    <span className="text-[10px] text-chrome-text/50">
                      {area.nouns.length} nouns{foldedHere ? ` · ${foldedHere} folded into concepts` : ""}{area.otherTables ? ` · ${area.otherTables} supporting tables not drawn` : ""}
                    </span>
                    {area.about ? <span className="min-w-0 truncate text-[10px] text-chrome-text/50" title={area.about}>{area.about}</span> : null}
                  </div>
                  <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
                    {items.map((it) => (
                      <div key={it.key} id={`meaning-noun-${it.key}`} className={`rounded-base transition-shadow ${flash === it.key ? "ring-2 ring-amber-400/60" : ""}`}>
                        {it.concept ? (
                          <ConceptCard concept={it.concept} canonical={nounByRelation.get(it.concept.canonical) ?? null} onPick={onPickTable} onOpen={open} onJump={jump} />
                        ) : it.noun ? (
                          <NounCard noun={it.noun} onPick={() => onPickTable(it.noun!.relation)} onOpen={() => open(it.noun!.relation)} onJump={jump} />
                        ) : null}
                      </div>
                    ))}
                  </div>
                </section>
              )
            })}
            {tree.questions.length || tree.metrics.length || tree.gaps.length || tree.derived.length || tree.lenses.length ? (
              <section className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
                {tree.questions.length ? (
                  <div>
                    <h3 className="text-[11px] font-semibold">Questions this subject answers</h3>
                    <ul className="mt-1 list-disc pl-4 text-[11px] text-chrome-text/75">{tree.questions.map((q) => <li key={q}>{q}</li>)}</ul>
                  </div>
                ) : null}
                {tree.metrics.length ? (
                  <div>
                    <h3 className="text-[11px] font-semibold">Metrics this subject implies</h3>
                    <div className="mb-1 text-[10px] text-chrome-text/50">
                      From the dossier, once per subject. <span className="text-amber-200">implied</span> ones run through metric() today and are promoted by use;{" "}
                      <span className="text-emerald-200">governed</span> ones were already confirmed.
                    </div>
                    <div className="mt-1 flex flex-col gap-0.5">{tree.metrics.map((m) => <Metric key={m.name} m={m} />)}</div>
                  </div>
                ) : null}
                {tree.derived.length ? (
                  <div>
                    <h3 className="text-[11px] font-semibold">Repeated across the subject</h3>
                    <div className="mb-1 text-[10px] text-chrome-text/50">Derived measures many nouns carry; the table to read first is named. These are grammar, not registered metrics.</div>
                    <div className="mt-1 flex flex-col gap-0.5">
                      {tree.derived.map((d) => (
                        <button
                          key={d.name}
                          type="button"
                          onClick={() => jump(d.canonical)}
                          className="flex items-baseline gap-1.5 text-left text-[11px] hover:bg-chrome-bg/40"
                          title={`${d.formula ?? ""}${d.relations.length ? ` — also on ${d.relations.slice(1).join(", ")}` : ""}`}
                        >
                          <span className="shrink-0 rounded-base border border-chrome-border/50 px-1 text-[9px] uppercase tracking-wide text-chrome-text/50">{METRIC_KIND_LABEL[d.kind ?? ""] ?? d.kind}</span>
                          <span className="truncate">{d.name}</span>
                          <span className="shrink-0 text-[10px] text-chrome-text/50">× {d.sources}</span>
                          <span className="ml-auto shrink-0 truncate font-mono text-[10px] text-chrome-text/45">{d.canonical}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
                {tree.lenses.length ? (
                  <div>
                    <h3 className="text-[11px] font-semibold">Lenses over this subject</h3>
                    <div className="mb-1 text-[10px] text-chrome-text/50">
                      Cubes someone defined over these nouns: a stated slice, not a table the crawler judged. Each names what it derives from and the metrics that read it.
                    </div>
                    <div className="mt-1 flex flex-col gap-1">
                      {tree.lenses.map((l) => (
                        <div key={l.relation} className="rounded-base border border-chrome-border/40 px-1.5 py-1 text-[11px]">
                          <div className="flex items-baseline gap-1.5">
                            <button type="button" onClick={() => jump(l.relation)} className="truncate font-semibold hover:underline" title={l.what ?? l.relation}>
                              {l.name}
                            </button>
                            {l.grain ? <span className="truncate text-[10px] text-chrome-text/55" title={l.grain}>{l.grain}</span> : null}
                            <span className="ml-auto shrink-0 text-[10px] text-chrome-text/50">
                              {l.metrics.length} metric{l.metrics.length === 1 ? "" : "s"}{l.touches30d != null ? ` · ${l.touches30d} touches` : ""}
                            </span>
                          </div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-chrome-text/60">
                            <span>from</span>
                            {l.derivesFrom.map((d) => (
                              <button key={d.relation} type="button" onClick={() => jump(d.relation)} className="rounded-base border border-chrome-border/50 px-1 hover:bg-chrome-bg/40" title={d.relation}>
                                {d.name}
                              </button>
                            ))}
                            {l.spans.length > 1 ? <span className="text-amber-200/80">· spans {l.spans.map((s) => s.name).join(", ")}</span> : null}
                            {l.refresh.dirty ? <span className="text-rose-300/80">· stale</span> : null}
                            {l.refresh.at ? <span className="ml-auto text-chrome-text/45">refreshed {new Date(l.refresh.at).toLocaleString()}</span> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {tree.gaps.length ? (
                  <div>
                    <h3 className="text-[11px] font-semibold">Gaps</h3>
                    <ul className="mt-1 list-disc pl-4 text-[11px] text-chrome-text/75">{tree.gaps.map((g) => <li key={g}>{g}</li>)}</ul>
                  </div>
                ) : null}
              </section>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
