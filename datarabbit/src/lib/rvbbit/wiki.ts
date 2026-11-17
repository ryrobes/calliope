"use client"

interface QueryOk { ok: true; rows: Record<string, unknown>[] }
interface QueryErr { ok: false; error: string }

async function query(connectionId: string, sql: string, rowLimit = 500, readOnly = true): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly }),
    })
    return await response.json() as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

function lit(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

export interface WikiPage {
  page_id: string
  slug: string
  title: string
  body: string
  author_kind: string
  author_key: string
  tags: string[]
  claim_fingerprints: string[]
  adoption_state: string
  flagged_at: string | null
  flag_reason: string | null
  revision: number
  created_at: string
  updated_at: string
  linked_claims: number
  supported: number
  contradicted: number
}

export interface WikiRevision {
  revision: number
  title: string
  author_kind: string
  author_key: string
  created_at: string
}

export interface WikiState {
  ready: boolean
  error: string | null
  pages: WikiPage[]
}

export async function fetchWiki(connectionId: string): Promise<WikiState> {
  const probe = await query(connectionId, "SELECT to_regclass('rvbbit.wiki_pages') IS NOT NULL AS ready", 1)
  if (!probe.ok) return { ready: false, error: probe.error, pages: [] }
  if (!(probe.rows[0]?.ready === true)) return { ready: false, error: null, pages: [] }
  const result = await query(connectionId, `
    SELECT page.page_id::text, page.slug, page.title, page.body, page.author_kind,
           page.author_key, page.tags, page.claim_fingerprints, page.adoption_state,
           page.flagged_at::text, page.flag_reason, page.revision,
           page.created_at::text, page.updated_at::text,
           coalesce(verdict.linked_claims, 0)::int AS linked_claims,
           coalesce(verdict.supported, 0)::int AS supported,
           coalesce(verdict.contradicted, 0)::int AS contradicted
    FROM rvbbit.wiki_pages page
    LEFT JOIN rvbbit.wiki_page_verdicts verdict USING (page_id)
    ORDER BY page.updated_at DESC LIMIT 300`, 300)
  if (!result.ok) return { ready: true, error: result.error, pages: [] }
  return { ready: true, error: null, pages: result.rows as unknown as WikiPage[] }
}

export async function fetchRevisions(connectionId: string, pageId: string): Promise<WikiRevision[]> {
  const result = await query(connectionId, `
    SELECT revision, title, author_kind, author_key, created_at::text
    FROM rvbbit.wiki_page_revisions WHERE page_id=${lit(pageId)}::uuid
    ORDER BY revision DESC LIMIT 50`, 50)
  return result.ok ? (result.rows as unknown as WikiRevision[]) : []
}

export interface AgentOverlay {
  overlay_id: string
  agent_key: string
  target_kind: string
  target_key: string | null
  section: string
  body: string
  kind: string
  adoption_state: string
  rationale: string | null
  proposed_by: string
  created_at: string
  last_event_kind: string | null
  last_event_reason: string | null
}

export async function fetchOverlays(connectionId: string): Promise<{ ready: boolean; error: string | null; overlays: AgentOverlay[] }> {
  const probe = await query(connectionId, "SELECT to_regclass('rvbbit.agent_overlays') IS NOT NULL AS ready", 1)
  if (!probe.ok) return { ready: false, error: probe.error, overlays: [] }
  if (!(probe.rows[0]?.ready === true)) return { ready: false, error: null, overlays: [] }
  const result = await query(connectionId, `
    SELECT o.overlay_id::text, o.agent_key, o.target_kind, o.target_key, o.section,
           o.body, o.kind, o.adoption_state, o.rationale, o.proposed_by, o.created_at::text,
           ev.kind AS last_event_kind, ev.reason AS last_event_reason
    FROM rvbbit.agent_overlays o
    LEFT JOIN LATERAL (
      SELECT kind, reason FROM rvbbit.agent_overlay_events e
      WHERE e.overlay_id=o.overlay_id ORDER BY e.created_at DESC LIMIT 1
    ) ev ON true
    ORDER BY (o.adoption_state='proposed') DESC, o.created_at DESC LIMIT 200`, 200)
  if (!result.ok) return { ready: true, error: result.error, overlays: [] }
  return { ready: true, error: null, overlays: result.rows as unknown as AgentOverlay[] }
}

export async function setOverlayState(
  connectionId: string,
  overlayId: string,
  state: "auto_provisional" | "affirmed" | "demoted" | "retired" | "rejected",
  reason: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const result = await query(
    connectionId,
    `SELECT rvbbit.agent_overlay_set_state(${lit(overlayId)}::uuid, ${lit(state)}, ${reason ? lit(reason) : "NULL"}, 'datarabbit')`,
    1,
    false,
  )
  return result.ok ? { ok: true } : { ok: false, error: result.error }
}

export async function setPageState(
  connectionId: string,
  slug: string,
  state: "auto_provisional" | "affirmed" | "demoted" | "retired",
  reason: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const result = await query(
    connectionId,
    `SELECT rvbbit.wiki_set_page_state(${lit(slug)}, ${lit(state)}, ${reason ? lit(reason) : "NULL"}, 'datarabbit')`,
    1,
    false,
  )
  return result.ok ? { ok: true } : { ok: false, error: result.error }
}
