"use client"

/**
 * Meaning data layer (docs/MEANING_PLAN.md).
 *
 * One card per admitted relation and column: what it is, how it relates,
 * whether it deserves metrics, whether it drifts. SQL-first, mirrors
 * catalog-drift.ts.
 *
 * Surface:
 *   - rvbbit.meaning_status()
 *   - rvbbit.meaning_overview            (view: one row per judged relation)
 *   - rvbbit.meaning_card(schema, rel)
 *   - rvbbit.meaning_column_card(schema, rel, col) / rvbbit.meaning_columns
 *   - rvbbit.meaning_state(relation, field, value, reason, col, actor)
 *   - CALL rvbbit.meaning_sweep(budget, max_relations, max_columns, source)
 */

export interface MeaningStatus {
  relations: number
  relationsError: number
  admitted: number
  pending: number
  columns: number
  columnsByBasis: Record<string, number>
  links: Record<string, number>
  overrides: number
  costUsd: number
  kinds: Record<string, number>
  lastRun: Record<string, unknown> | null
}

export interface MeaningRow {
  relation: string
  schema: string
  rel: string
  sourceSystem: string | null
  sourceRole: string | null
  name: string
  kind: string | null
  kindP: number | null
  what: string | null
  rows: number | null
  columns: number | null
  customFields: number
  timeColumn: string | null
  timeGrain: string | null
  keys: string[]
  metricWorthy: number | null
  plumbing: number | null
  pii: number | null
  customized: number | null
  confidence: number | null
  domainKey: string | null
  driftSeverity: number | null
  driftFlags: string[]
  touches30d: number | null
  columnsJudged: number
  verifiedJoins: number
  measuredBy: number
  overridden: boolean
  status: string
  judgedAt: number | null
  subjectRole: string | null
  placedBy: string | null
}

export interface MeaningColumn {
  col: string
  type: string | null
  role: string | null
  roleP: number | null
  refTarget: string | null
  pii: number | null
  ndv: number | null
  nullFrac: number | null
  topValues: Array<{ v: string; n: number }>
  basis: string | null
}

export type MeaningCard = Record<string, unknown>

export interface MeaningNarrativeStatus {
  sourcesRecognized: number
  sourcesError: number
  relationsNamed: number
  domains: number
  domainsUsable: number
  domainsReviewed: number
  domainsError: number
  queue: Record<string, number>
  spend24hUsd: number
  spendTotalUsd: number
  lastActivity: number | null
}

export interface MeaningDomain {
  key: string
  schema: string
  sourceSystem: string | null
  name: string | null
  about: string | null
  questions: string[]
  candidateMetrics: Array<{ name: string; formula?: string; grain?: string; why?: string }>
  members: string[]
  usable: boolean
  review: { makes_sense?: number; kind?: string } | null
  status: string
  error: string | null
  costUsd: number
  updatedAt: number | null
}

export interface MeaningEnrichment {
  id: number
  subjectKey: string
  relation: string
  name: string
  labelName: string
  columns: string[]
  questionKind: string
  why: string | null
  expects: string | null
  status: string
  sampleN: number | null
  distribution: Array<{ label: string; n: number; share: number }>
  judgeScore: number | null
  judgeReason: string | null
  viewName: string | null
  materializedN: number
  expression: string | null
  jevModel: string | null
  costUsd: number
  error: string | null
  updatedAt: number | null
}

// ── plumbing ─────────────────────────────────────────────────────────

interface QueryOk {
  ok: true
  columns: { name: string }[]
  rows: Array<Record<string, unknown>>
}
interface QueryErr {
  ok: false
  error: string
}

async function runQuery(connectionId: string, sql: string, rowLimit = 5000): Promise<QueryOk | QueryErr> {
  try {
    const res = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit }),
    })
    return (await res.json()) as QueryOk | QueryErr
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

function num(v: unknown): number {
  return v == null ? 0 : Number(v)
}
function numOrNull(v: unknown): number | null {
  return v == null || v === "" ? null : Number(v)
}
function strOrNull(v: unknown): string | null {
  return v == null ? null : String(v)
}
function epoch(v: unknown): number | null {
  return v ? new Date(String(v)).getTime() : null
}
function sqlStr(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}
function json<T = unknown>(v: unknown): T | null {
  if (v == null) return null
  if (typeof v === "string") {
    try {
      return JSON.parse(v) as T
    } catch {
      return null
    }
  }
  return v as T
}
function asStrArray(v: unknown): string[] {
  const j = json<unknown>(v)
  return Array.isArray(j) ? j.map((x) => String(x)) : []
}
function asRecord(v: unknown): Record<string, number> {
  const j = json<Record<string, unknown>>(v)
  if (!j || typeof j !== "object") return {}
  return Object.fromEntries(Object.entries(j).map(([k, x]) => [k, num(x)]))
}

// ── fetchers ─────────────────────────────────────────────────────────

export async function fetchMeaningStatus(connectionId: string): Promise<{ status?: MeaningStatus; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_status() AS j`, 1)
  if (!res.ok) return { error: res.error }
  const j = json<Record<string, unknown>>(res.rows[0]?.j) ?? {}
  return {
    status: {
      relations: num(j.relations),
      relationsError: num(j.relations_error),
      admitted: num(j.admitted),
      pending: num(j.pending),
      columns: num(j.columns),
      columnsByBasis: asRecord(j.columns_by_basis),
      links: asRecord(j.links),
      overrides: num(j.overrides),
      costUsd: num(j.cost_usd),
      kinds: asRecord(j.kinds),
      lastRun: json<Record<string, unknown>>(j.last_run),
    },
  }
}

export async function fetchMeaningOverview(connectionId: string): Promise<{ rows?: MeaningRow[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    `SELECT relation, schema_name, rel_name, source_system, source_role, name, kind, kind_p, what, n_rows, n_columns,
            custom_fields, time_column, time_grain, entity_keys, metric_worthy, plumbing, has_pii, customized,
            confidence, domain_key, drift_severity, drift_flags, touches_30d, columns_judged, verified_joins,
            measured_by, overridden, status, judged_at, subject_role, placed_by
       FROM rvbbit.meaning_overview
      ORDER BY source_system, coalesce(touches_30d, 0) DESC, n_rows DESC NULLS LAST`,
    5000,
  )
  if (!res.ok) return { error: res.error }
  return {
    rows: res.rows.map((r) => ({
      relation: String(r.relation),
      schema: String(r.schema_name),
      rel: String(r.rel_name),
      sourceSystem: strOrNull(r.source_system),
      sourceRole: strOrNull(r.source_role),
      name: String(r.name ?? r.rel_name),
      kind: strOrNull(r.kind),
      kindP: numOrNull(r.kind_p),
      what: strOrNull(r.what),
      rows: numOrNull(r.n_rows),
      columns: numOrNull(r.n_columns),
      customFields: num(r.custom_fields),
      timeColumn: strOrNull(r.time_column),
      timeGrain: strOrNull(r.time_grain),
      keys: Array.isArray(r.entity_keys) ? (r.entity_keys as unknown[]).map(String) : asStrArray(r.entity_keys),
      metricWorthy: numOrNull(r.metric_worthy),
      plumbing: numOrNull(r.plumbing),
      pii: numOrNull(r.has_pii),
      customized: numOrNull(r.customized),
      confidence: numOrNull(r.confidence),
      domainKey: strOrNull(r.domain_key),
      driftSeverity: numOrNull(r.drift_severity),
      driftFlags: asStrArray(r.drift_flags),
      touches30d: numOrNull(r.touches_30d),
      columnsJudged: num(r.columns_judged),
      verifiedJoins: num(r.verified_joins),
      measuredBy: num(r.measured_by),
      overridden: Boolean(r.overridden),
      status: String(r.status ?? "pending"),
      judgedAt: epoch(r.judged_at),
      subjectRole: strOrNull(r.subject_role),
      placedBy: strOrNull(r.placed_by),
    })),
  }
}

export async function fetchMeaningCard(
  connectionId: string,
  schema: string,
  rel: string,
): Promise<{ card?: MeaningCard; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_card(${sqlStr(schema)}, ${sqlStr(rel)}) AS j`, 1)
  if (!res.ok) return { error: res.error }
  const card = json<MeaningCard>(res.rows[0]?.j)
  return { card: card ?? undefined }
}

export async function fetchMeaningColumns(
  connectionId: string,
  schema: string,
  rel: string,
): Promise<{ columns?: MeaningColumn[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    `SELECT col_name, data_type, role, role_p, ref_target, pii, ndv, null_frac, top_values, basis
       FROM rvbbit.meaning_columns
      WHERE relation = ${sqlStr(`${schema}.${rel}`.toLowerCase())}
      ORDER BY CASE role WHEN 'identifier' THEN 0 WHEN 'time' THEN 1 WHEN 'measure' THEN 2 WHEN 'category' THEN 3
                         WHEN 'reference' THEN 4 WHEN 'flag' THEN 5 WHEN 'free_text' THEN 6 WHEN 'structured_blob' THEN 7
                         WHEN 'unused' THEN 9 ELSE 8 END, null_frac NULLS LAST, col_name`,
    2000,
  )
  if (!res.ok) return { error: res.error }
  return {
    columns: res.rows.map((r) => ({
      col: String(r.col_name),
      type: strOrNull(r.data_type),
      role: strOrNull(r.role),
      roleP: numOrNull(r.role_p),
      refTarget: strOrNull(r.ref_target),
      pii: numOrNull(r.pii),
      ndv: numOrNull(r.ndv),
      nullFrac: numOrNull(r.null_frac),
      topValues: (json<Array<{ v: string; n: number }>>(r.top_values) ?? []).map((t) => ({ v: String(t.v), n: num(t.n) })),
      basis: strOrNull(r.basis),
    })),
  }
}

/** A person states a fact: kind | business_name | what | domain_key | ignore | role | ref_target | pii. */
export async function stateMeaning(
  connectionId: string,
  relation: string,
  field: string,
  value: string | boolean | number | Record<string, unknown>,
  reason: string,
  col: string | null = null,
): Promise<{ ok: boolean; error?: string }> {
  const jsonValue = sqlStr(JSON.stringify(value))
  const res = await runQuery(
    connectionId,
    `SELECT rvbbit.meaning_state(${sqlStr(relation)}, ${sqlStr(field)}, ${jsonValue}::jsonb, ${sqlStr(reason)}, ${col == null ? "NULL" : sqlStr(col)}) AS id`,
    1,
  )
  if (!res.ok) return { ok: false, error: res.error }
  return { ok: true }
}

/** Re-judge one relation now (profile + Jev + columns + joins + drift). */
export async function rejudgeMeaning(
  connectionId: string,
  schema: string,
  rel: string,
): Promise<{ ok: boolean; error?: string }> {
  const s = sqlStr(schema)
  const r = sqlStr(rel)
  const res = await runQuery(
    connectionId,
    `WITH a AS (SELECT rvbbit.meaning_judge_relation(${s}, ${r}) AS j),
          b AS (SELECT rvbbit.meaning_judge_columns(${s}, ${r}, 400) AS j),
          c AS (SELECT rvbbit.meaning_infer_links(${s}, ${r}) AS n),
          d AS (UPDATE rvbbit.meaning_relations SET drift = rvbbit.meaning_relation_drift(${s}, ${r}), updated_at = now()
                 WHERE relation = lower(${s} || '.' || ${r}) RETURNING 1)
     SELECT (SELECT j FROM a) AS relation, (SELECT j FROM b) AS columns, (SELECT n FROM c) AS links`,
    1,
  )
  if (!res.ok) return { ok: false, error: res.error }
  return { ok: true }
}

/** Run a bounded sweep now (seconds of budget). */
export async function runMeaningSweep(
  connectionId: string,
  budgetSeconds = 120,
): Promise<{ ok: boolean; error?: string }> {
  const res = await runQuery(connectionId, `CALL rvbbit.meaning_sweep(${Math.max(10, Math.min(900, Math.floor(budgetSeconds)))}, 100, 400, 'datarabbit')`, 1)
  if (!res.ok) return { ok: false, error: res.error }
  return { ok: true }
}

export const MEANING_KIND_LABEL: Record<string, string> = {
  entity_master: "Entities",
  event_log: "Event log",
  transactional_fact: "Activity",
  history_or_snapshot: "History",
  aggregate_or_cube: "Rollup",
  staging_or_raw: "Raw / staging",
  system_plumbing: "Plumbing",
  lookup_or_config: "Reference",
}

export const MEANING_ROLE_LABEL: Record<string, string> = {
  identifier: "id",
  reference: "ref",
  time: "time",
  measure: "measure",
  category: "category",
  flag: "flag",
  free_text: "text",
  structured_blob: "json",
  unused: "unused",
}

export async function fetchMeaningNarrativeStatus(connectionId: string): Promise<{ status?: MeaningNarrativeStatus; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_narrative_status() AS j`, 1)
  if (!res.ok) return { error: res.error }
  const j = json<Record<string, unknown>>(res.rows[0]?.j) ?? {}
  return {
    status: {
      sourcesRecognized: num(j.sources_recognized),
      sourcesError: num(j.sources_error),
      relationsNamed: num(j.relations_named),
      domains: num(j.domains),
      domainsUsable: num(j.domains_usable),
      domainsReviewed: num(j.domains_reviewed),
      domainsError: num(j.domains_error),
      queue: asRecord(j.queue),
      spend24hUsd: num(j.spend_24h_usd),
      spendTotalUsd: num(j.spend_total_usd),
      lastActivity: epoch(j.last_activity),
    },
  }
}

/** Label dimensions Meaning proposed and Jev answered. Absent on installs without 0449. */
export async function fetchMeaningEnrichments(connectionId: string): Promise<{ enrichments?: MeaningEnrichment[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    `SELECT id, subject_key, relation, name, label_name, columns, question->>'kind' AS question_kind,
            why, expects, status, sample_n, distribution, judge, view_name, materialized_n,
            expression, jev_model, cost_usd, error, updated_at
       FROM rvbbit.meaning_enrichments WHERE relation <> '-' ORDER BY subject_key, relation, name`,
    500,
  )
  if (!res.ok) return { error: res.error }
  return {
    enrichments: res.rows.map((r) => {
      const judge = json<Record<string, unknown>>(r.judge) ?? {}
      return {
        id: num(r.id),
        subjectKey: String(r.subject_key ?? ""),
        relation: String(r.relation ?? ""),
        name: String(r.name ?? ""),
        labelName: String(r.label_name ?? ""),
        columns: (json<string[]>(r.columns) ?? (Array.isArray(r.columns) ? (r.columns as string[]) : [])) ?? [],
        questionKind: String(r.question_kind ?? ""),
        why: (r.why as string) ?? null,
        expects: (r.expects as string) ?? null,
        status: String(r.status ?? ""),
        sampleN: numOrNull(r.sample_n),
        distribution: json<Array<{ label: string; n: number; share: number }>>(r.distribution) ?? [],
        judgeScore: numOrNull(judge.score),
        judgeReason: (judge.reason as string) ?? null,
        viewName: (r.view_name as string) ?? null,
        materializedN: num(r.materialized_n),
        expression: (r.expression as string) ?? null,
        jevModel: (r.jev_model as string) ?? null,
        costUsd: num(r.cost_usd),
        error: (r.error as string) ?? null,
        updatedAt: epoch(r.updated_at),
      }
    }),
  }
}

export async function fetchMeaningDomains(connectionId: string): Promise<{ domains?: MeaningDomain[]; error?: string }> {
  const res = await runQuery(
    connectionId,
    `SELECT domain_key, schema_name, source_system, name, about, questions, candidate_metrics, members, usable, review, status, error, cost_usd, updated_at
       FROM rvbbit.meaning_domains WHERE status <> 'folded' ORDER BY usable DESC, cardinality(members) DESC, name`,
    2000,
  )
  if (!res.ok) return { error: res.error }
  return {
    domains: res.rows.map((r) => ({
      key: String(r.domain_key),
      schema: String(r.schema_name),
      sourceSystem: strOrNull(r.source_system),
      name: strOrNull(r.name),
      about: strOrNull(r.about),
      questions: asStrArray(r.questions),
      candidateMetrics: (json<Array<Record<string, string>>>(r.candidate_metrics) ?? []).map((m) => ({ name: String(m.name ?? ""), formula: m.formula, grain: m.grain, why: m.why })),
      members: Array.isArray(r.members) ? (r.members as unknown[]).map(String) : asStrArray(r.members),
      usable: Boolean(r.usable),
      review: json<{ makes_sense?: number; kind?: string }>(r.review),
      status: String(r.status ?? "pending"),
      error: strOrNull(r.error),
      costUsd: num(r.cost_usd),
      updatedAt: epoch(r.updated_at),
    })),
  }
}

// ── the meaning tree (migration 0442): subjects → areas → nouns with counts ──

export interface MeaningSubject {
  key: string
  name: string
  tables: number
  core: number
  usable: boolean
  about: string | null
  rows: number | null
  topNouns: string[]
}

export interface MeaningTreeDimension {
  name: string
  values: number | null
  top: Array<{ v: string; n: number }>
}

export interface MeaningTreeEdge {
  name: string
  relation: string
  count: number | null
  kind: string | null
  via: string | null
  perParent: number | null
  relationship: string | null
}

export interface MeaningTreeMetric {
  name: string
  kind: string | null
  formula: string | null
  grain: string | null
  why: string | null
  subject: string | null
  /** the registry name when this candidate is callable through metric() (0445) */
  metric: string | null
  /** implied (registered by the meaning layer, unconfirmed) | governed | retired */
  status: string | null
}

/** One member of a concept: a table that is the same thing at another grain or layer. */
export interface MeaningConceptMember {
  relation: string
  name: string
  count: number | null
  layer: string | null
  role: string | null
  kind: string | null
  sameRowsAs: string | null
}

/** Nouns folded into one thing (migration 0444): canonical member first, metrics once. */
export interface MeaningConcept {
  name: string
  family: string | null
  canonical: string
  kind: string | null
  count: number | null
  tables: number
  members: MeaningConceptMember[]
  layers: Record<string, number>
  measures: string[]
  metrics: MeaningTreeMetric[]
  derivedMetrics: MeaningTreeMetric[]
}

/** A derived metric the subject repeats across nouns, with the noun to read first. */
export interface MeaningDerivedGroup {
  name: string
  kind: string | null
  sources: number
  canonical: string
  formula: string | null
  grain: string | null
  relations: string[]
}

export interface MeaningNoun {
  relation: string
  name: string
  what: string | null
  kind: string | null
  role: string | null
  count: number | null
  time: string | null
  dimensions: MeaningTreeDimension[]
  measures: string[]
  has: MeaningTreeEdge[]
  pointsAt: MeaningTreeEdge[]
  metrics: MeaningTreeMetric[]
  derivedMetrics: MeaningTreeMetric[]
  measuredBy: string[]
  /** the lenses (cubes) over this noun and the metrics that read each — source → lens → metric */
  measuredThrough: MeaningMeasuredThrough[]
  /** when the noun itself is a cube: its lens (migration 0454) */
  lens: MeaningLens | null
  drift: string[]
  touches30d: number | null
  succession: MeaningSuccession | null
  collisions: MeaningCollision[]
}

/** A cube read as a lens over its sources (migration 0454): derived from its definition, never judged. */
export interface MeaningLens {
  relation: string
  name: string
  what: string | null
  grain: string | null
  category: string | null
  rows: number | null
  refresh: { cron: string | null; at: string | null; rows: number | null; dirty: boolean; recommended: string | null }
  derivesFrom: { relation: string; name: string; subject: string | null; carded: boolean }[]
  spans: { key: string; name: string }[]
  dimensions: string[]
  measures: string[]
  time: string | null
  metrics: { name: string; grain: string | null }[]
  touches30d: number | null
}

export interface MeaningMeasuredThrough {
  lens: string
  name: string
  grain: string | null
  metrics: string[]
}

export interface MeaningTreeArea {
  name: string
  about: string | null
  nouns: MeaningNoun[]
  otherTables: number
}

export interface MeaningTree {
  subject: string
  key: string
  about: string | null
  tables: number
  nouns: number
  roles: Record<string, number>
  questions: string[]
  metrics: MeaningTreeMetric[]
  gaps: string[]
  usable: boolean
  areas: MeaningTreeArea[]
  concepts: MeaningConcept[]
  /** how many of the drawn nouns sit inside a concept */
  folded: number
  derived: MeaningDerivedGroup[]
  /** cubes over this subject's nouns; a lens hangs off nouns and is never one */
  lenses: MeaningLens[]
}

function asMetric(m: Record<string, unknown>): MeaningTreeMetric {
  return {
    name: String(m.name ?? ""),
    kind: strOrNull(m.kind),
    formula: strOrNull(m.formula),
    grain: strOrNull(m.grain),
    why: strOrNull(m.why),
    subject: strOrNull(m.subject),
    metric: strOrNull(m.metric),
    status: strOrNull(m.status),
  }
}

function asConcept(c: Record<string, unknown>): MeaningConcept {
  const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
  return {
    name: String(c.name ?? c.canonical ?? ""),
    family: strOrNull(c.family),
    canonical: String(c.canonical ?? ""),
    kind: strOrNull(c.kind),
    count: numOrNull(c.count),
    tables: num(c.tables),
    members: arr<Record<string, unknown>>(c.members).map((m) => ({
      relation: String(m.relation ?? ""),
      name: String(m.name ?? m.relation ?? ""),
      count: numOrNull(m.count),
      layer: strOrNull(m.layer),
      role: strOrNull(m.role),
      kind: strOrNull(m.kind),
      sameRowsAs: strOrNull(m.same_rows_as),
    })),
    layers: asRecord(c.layers),
    measures: arr<unknown>(c.measures).map(String),
    metrics: arr<Record<string, unknown>>(c.metrics).map(asMetric),
    derivedMetrics: arr<Record<string, unknown>>(c.derived_metrics).map(asMetric),
  }
}

function asDerivedGroup(d: Record<string, unknown>): MeaningDerivedGroup {
  return {
    name: String(d.name ?? ""),
    kind: strOrNull(d.kind),
    sources: num(d.sources),
    canonical: String(d.canonical ?? ""),
    formula: strOrNull(d.formula),
    grain: strOrNull(d.grain),
    relations: asStrArray(d.relations),
  }
}

function asEdge(e: Record<string, unknown>): MeaningTreeEdge {
  return {
    name: String(e.name ?? e.relation ?? ""),
    relation: String(e.relation ?? ""),
    count: numOrNull(e.count),
    kind: strOrNull(e.kind),
    via: strOrNull(e.via),
    perParent: numOrNull(e.per_parent),
    relationship: strOrNull(e.relationship),
  }
}

export function asLens(l: Record<string, unknown>): MeaningLens {
  const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
  const refresh = l.refresh && typeof l.refresh === "object" ? (l.refresh as Record<string, unknown>) : {}
  const usage = l.usage && typeof l.usage === "object" ? (l.usage as Record<string, unknown>) : {}
  return {
    relation: String(l.relation ?? ""),
    name: String(l.name ?? l.relation ?? ""),
    what: strOrNull(l.what),
    grain: strOrNull(l.grain),
    category: strOrNull(l.category),
    rows: numOrNull(l.rows),
    refresh: {
      cron: strOrNull(refresh.cron),
      at: strOrNull(refresh.at),
      rows: numOrNull(refresh.rows),
      dirty: Boolean(refresh.dirty),
      recommended: strOrNull(refresh.recommended),
    },
    derivesFrom: arr<Record<string, unknown>>(l.derives_from).map((d) => ({
      relation: String(d.relation ?? ""),
      name: String(d.name ?? d.relation ?? ""),
      subject: strOrNull(d.subject),
      carded: d.carded !== false,
    })),
    spans: arr<Record<string, unknown>>(l.spans).map((s) => ({ key: String(s.key ?? ""), name: String(s.name ?? s.key ?? "") })),
    dimensions: arr<unknown>(l.dimensions).map(String),
    measures: arr<unknown>(l.measures).map(String),
    time: strOrNull(l.time),
    metrics: arr<Record<string, unknown>>(l.metrics).map((m) => ({ name: String(m.name ?? ""), grain: strOrNull(m.grain) })),
    touches30d: numOrNull(usage.touches_30d),
  }
}

function asMeasuredThrough(t: Record<string, unknown>): MeaningMeasuredThrough {
  return {
    lens: String(t.lens ?? ""),
    name: String(t.name ?? t.lens ?? ""),
    grain: strOrNull(t.grain),
    metrics: (Array.isArray(t.metrics) ? t.metrics : []).map(String),
  }
}

export function asNoun(n: Record<string, unknown>): MeaningNoun {
  const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
  return {
    relation: String(n.relation ?? ""),
    name: String(n.name ?? n.relation ?? ""),
    what: strOrNull(n.what),
    kind: strOrNull(n.kind),
    role: strOrNull(n.role),
    count: numOrNull(n.count),
    time: strOrNull(n.time),
    dimensions: arr<Record<string, unknown>>(n.dimensions).map((d) => ({
      name: String(d.name ?? ""),
      values: numOrNull(d.values),
      top: arr<Record<string, unknown>>(d.top).map((t) => ({ v: String(t.v ?? ""), n: num(t.n) })),
    })),
    measures: arr<unknown>(n.measures).map(String),
    has: arr<Record<string, unknown>>(n.has).map(asEdge),
    pointsAt: arr<Record<string, unknown>>(n.points_at).map(asEdge),
    metrics: arr<Record<string, unknown>>(n.metrics).map(asMetric),
    derivedMetrics: arr<Record<string, unknown>>(n.derived_metrics).map(asMetric),
    measuredBy: arr<unknown>(n.measured_by).map(String),
    measuredThrough: arr<Record<string, unknown>>(n.measured_through).map(asMeasuredThrough),
    lens: n.lens && typeof n.lens === "object" ? asLens(n.lens as Record<string, unknown>) : null,
    drift: arr<unknown>(n.drift).map(String),
    touches30d: numOrNull(n.touches_30d),
    succession: asSuccession(n.succession),
    collisions: asCollisions(n.vocabulary),
  }
}

export async function fetchMeaningSubjects(connectionId: string): Promise<{ subjects?: MeaningSubject[]; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_subjects() AS subjects`, 1)
  if (!res.ok) return { error: res.error }
  const list = json<Array<Record<string, unknown>>>(res.rows[0]?.subjects) ?? []
  return {
    subjects: list.map((s) => ({
      key: String(s.key ?? ""),
      name: String(s.name ?? s.key ?? ""),
      tables: num(s.tables),
      core: num(s.core),
      usable: Boolean(s.usable),
      about: strOrNull(s.about),
      rows: numOrNull(s.rows),
      topNouns: asStrArray(s.top_nouns),
    })),
  }
}

export async function fetchMeaningTree(connectionId: string, subjectKey: string): Promise<{ tree?: MeaningTree; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_tree(${sqlStr(subjectKey)}) AS tree`, 1)
  if (!res.ok) return { error: res.error }
  const t = json<Record<string, unknown>>(res.rows[0]?.tree)
  if (!t) return { error: "no tree for this subject yet" }
  const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
  return {
    tree: {
      subject: String(t.subject ?? ""),
      key: String(t.key ?? subjectKey),
      about: strOrNull(t.about),
      tables: num(t.tables),
      nouns: num(t.nouns),
      roles: asRecord(t.roles),
      questions: asStrArray(t.questions),
      metrics: arr<Record<string, unknown>>(t.metrics).map(asMetric),
      gaps: asStrArray(t.gaps),
      usable: Boolean(t.usable),
      areas: arr<Record<string, unknown>>(t.areas).map((a) => ({
        name: String(a.name ?? ""),
        about: strOrNull(a.about),
        nouns: arr<Record<string, unknown>>(a.nouns).map(asNoun),
        otherTables: num(a.other_tables),
      })),
      concepts: arr<Record<string, unknown>>(t.concepts).map(asConcept),
      folded: num(t.folded),
      derived: arr<Record<string, unknown>>(t.derived).map(asDerivedGroup),
      lenses: arr<Record<string, unknown>>(t.lenses).map(asLens),
    },
  }
}

export async function fetchMeaningNoun(connectionId: string, relation: string): Promise<{ noun?: MeaningNoun; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_noun(${sqlStr(relation)}) AS noun`, 1)
  if (!res.ok) return { error: res.error }
  const n = json<Record<string, unknown>>(res.rows[0]?.noun)
  if (!n) return { error: "no meaning card for this table yet" }
  return { noun: asNoun(n) }
}

// ── statements that stick (migration 0443) ─────────────────────────────────

export interface MeaningSuccession {
  supersedes: string[]
  since: string | null
  federation: string | null
  note: string | null
  supersededBy: Array<{ by: string; since: string | null; note: string | null }>
}

export function asSuccession(v: unknown): MeaningSuccession | null {
  const j = json<Record<string, unknown>>(v)
  if (!j || typeof j !== "object") return null
  const succeeds = (j.succeeds && typeof j.succeeds === "object" ? j.succeeds : null) as Record<string, unknown> | null
  const by = Array.isArray(j.superseded_by) ? (j.superseded_by as Array<Record<string, unknown>>) : []
  const out: MeaningSuccession = {
    supersedes: succeeds ? asStrArray(succeeds.supersedes) : [],
    since: succeeds ? strOrNull(succeeds.effective_from) : null,
    federation: succeeds ? strOrNull(succeeds.federation) : null,
    note: succeeds ? strOrNull(succeeds.note) : null,
    supersededBy: by.map((b) => ({ by: String(b.by ?? ""), since: strOrNull(b.effective_from), note: strOrNull(b.note) })),
  }
  return out.supersedes.length || out.supersededBy.length ? out : null
}

export interface MeaningCollision {
  term: string
  means: string | null
  gloss: string | null
}

export function asCollisions(vocabulary: unknown): MeaningCollision[] {
  const v = json<Record<string, unknown>>(vocabulary)
  const items = v && Array.isArray(v.term_collisions) ? (v.term_collisions as Array<Record<string, unknown>>) : []
  return items.map((t) => ({ term: String(t.term ?? ""), means: strOrNull(t.means), gloss: strOrNull(t.gloss) }))
}

export async function retireMeaningStatement(connectionId: string, id: number, reason: string): Promise<{ ok: boolean; error?: string }> {
  const res = await runQuery(connectionId, `SELECT rvbbit.meaning_retire_statement(${Math.floor(id)}, ${sqlStr(reason)}) AS ok`, 1)
  if (!res.ok) return { ok: false, error: res.error }
  return { ok: true }
}
