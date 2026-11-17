"use client"

import { useCallback, useMemo, useState, type ReactNode } from "react"
import { AlertTriangle, BookOpen, RefreshCw } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { useWorkspaceActive } from "./workspace-active-context"
import {
  fetchOverlays,
  fetchRevisions,
  fetchWiki,
  setOverlayState,
  setPageState,
  type AgentOverlay,
  type WikiPage,
  type WikiRevision,
} from "@/lib/rvbbit/wiki"

interface Props { activeConnectionId: string | null; hasRvbbit: boolean }

const stateTones: Record<string, string> = {
  affirmed: "var(--success)",
  auto_provisional: "var(--rvbbit-accent)",
  proposed: "var(--warning)",
  demoted: "var(--danger)",
  rejected: "var(--danger)",
  retired: "var(--chrome-text)",
}

function when(value: string | null | undefined) {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function StateBadge({ value }: { value: string }) {
  return <span className="inline-flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.12em]" style={{ color: stateTones[value] || "var(--chrome-text)" }}><i className="h-1.5 w-1.5 rounded-full bg-current" />{value.replace("auto_provisional", "provisional")}</span>
}

function PaneTitle({ label, note }: { label: string; note?: string }) {
  return <div className="sticky top-0 z-10 flex items-baseline justify-between border-b border-chrome-border bg-chrome px-4 py-2"><span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">{label}</span>{note ? <span className="font-mono text-[8px] text-chrome-text/35">{note}</span> : null}</div>
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-8 text-center text-[10px] text-chrome-text/50">{children}</div>
}

type Filter = "all" | "flagged" | "auto_provisional" | "affirmed" | "demoted" | "retired"

export function WikiWindow({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [pages, setPages] = useState<WikiPage[]>([])
  const [ready, setReady] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>("all")
  const [revisions, setRevisions] = useState<WikiRevision[]>([])
  const [mode, setMode] = useState<"pages" | "overlays">("pages")
  const [overlays, setOverlays] = useState<AgentOverlay[]>([])
  const [selectedOverlay, setSelectedOverlay] = useState<string | null>(null)

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    setRefreshing(true)
    const [result, overlayResult] = await Promise.all([
      fetchWiki(activeConnectionId),
      fetchOverlays(activeConnectionId),
    ])
    setRefreshing(false)
    setReady(result.ready)
    setError(result.error || overlayResult.error)
    setPages(result.pages)
    setOverlays(overlayResult.overlays)
  }, [activeConnectionId, hasRvbbit])

  usePolling(reload, 15_000, { enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive), resetKey: activeConnectionId })

  const visible = useMemo(() => pages.filter((p) =>
    filter === "all" ? true : filter === "flagged" ? p.flagged_at != null : p.adoption_state === filter
  ), [pages, filter])
  const page = visible.find((p) => p.slug === selected) || visible[0] || null
  const flaggedCount = pages.filter((p) => p.flagged_at != null).length

  const loadRevisions = useCallback(async (pageId: string) => {
    if (!activeConnectionId) return
    setRevisions(await fetchRevisions(activeConnectionId, pageId))
  }, [activeConnectionId])

  const changeState = async (slug: string, state: "auto_provisional" | "affirmed" | "demoted" | "retired") => {
    if (!activeConnectionId) return
    const reason = state === "demoted" || state === "retired" ? "operator action via DataRabbit" : null
    const result = await setPageState(activeConnectionId, slug, state, reason)
    if (!result.ok) setError(result.error || "state change failed")
    void reload()
  }

  const changeOverlayState = async (overlayId: string, state: "auto_provisional" | "demoted" | "retired" | "rejected") => {
    if (!activeConnectionId) return
    const reason = state === "rejected" || state === "demoted"
      ? window.prompt("Reason (recorded in the audit ledger — the same idea cannot be re-proposed unchanged):") || "operator action via DataRabbit"
      : "operator adoption via DataRabbit"
    const result = await setOverlayState(activeConnectionId, overlayId, state, reason)
    if (!result.ok) setError(result.error || "overlay state change failed")
    void reload()
  }

  const overlay = overlays.find((o) => o.overlay_id === selectedOverlay) || overlays[0] || null
  const proposedCount = overlays.filter((o) => o.adoption_state === "proposed").length

  if (!hasRvbbit || !activeConnectionId) return <Empty>The Wiki needs an active RVBBIT connection.</Empty>
  if (ready === false && !error) return <Empty>The wiki tables are not installed in this database (migration 0369).</Empty>

  return <div className="flex h-full min-h-0 flex-col text-chrome-text">
    <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
      <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10"><BookOpen className="h-4 w-4 text-rvbbit-accent" /></div>
      <div><h1 className="text-[13px] font-semibold text-foreground">Working-Knowledge Wiki</h1><p className="text-[10px] text-chrome-text/55">Narrative pages distilled nightly · adopt-first · Evidence-Lab-flagged · discoverable as cap_wiki</p></div>
      <div className="ml-auto flex items-center gap-2">
        <div className="flex border border-chrome-border">
          <button type="button" onClick={() => setMode("pages")} className={cn("px-2.5 py-1.5 font-mono text-[8px] uppercase tracking-[0.1em]", mode === "pages" ? "bg-rvbbit-accent/15 text-rvbbit-accent" : "text-chrome-text/50 hover:text-foreground")}>Pages</button>
          <button type="button" onClick={() => setMode("overlays")} className={cn("px-2.5 py-1.5 font-mono text-[8px] uppercase tracking-[0.1em]", mode === "overlays" ? "bg-rvbbit-accent/15 text-rvbbit-accent" : "text-chrome-text/50 hover:text-foreground")}>Overlays{proposedCount ? ` (${proposedCount})` : ""}</button>
        </div>
        {mode === "pages" ? (["all", "flagged", "auto_provisional", "affirmed", "demoted", "retired"] as Filter[]).map((f) =>
          <button key={f} type="button" onClick={() => setFilter(f)} className={cn("border px-2 py-1 font-mono text-[8px] uppercase tracking-[0.1em]", filter === f ? "border-rvbbit-accent/60 text-rvbbit-accent" : "border-chrome-border text-chrome-text/50 hover:text-foreground")}>
            {f === "auto_provisional" ? "provisional" : f}{f === "flagged" && flaggedCount ? ` (${flaggedCount})` : ""}
          </button>) : null}
        <button type="button" onClick={() => void reload()} disabled={refreshing} className="inline-flex items-center gap-1.5 border border-chrome-border px-2 py-1.5 text-[9px] text-foreground hover:border-rvbbit-accent/50"><RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />Refresh</button>
      </div>
    </header>
    {error ? <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger"><AlertTriangle className="h-3.5 w-3.5" />{error}</div> : null}
    {mode === "overlays" ? <div className="grid min-h-0 flex-1 grid-cols-[minmax(300px,.8fr)_minmax(420px,1.6fr)] overflow-hidden">
      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Learned overlays" note={`${overlays.length} · proposed behavior waits for you`} />
        {overlays.map((o) => <button key={o.overlay_id} type="button" onClick={() => setSelectedOverlay(o.overlay_id)} className={cn("w-full border-b border-chrome-border/60 px-4 py-3 text-left hover:bg-foreground/[0.025]", overlay?.overlay_id === o.overlay_id && "bg-rvbbit-accent/[0.04]")}>
          <div className="flex items-center justify-between gap-2"><StateBadge value={o.adoption_state} /><span className={cn("font-mono text-[8px] uppercase", o.kind === "behavior" ? "text-warning" : "text-chrome-text/40")}>{o.kind}</span></div>
          <strong className="mt-1.5 block text-[11px] text-foreground">{o.section}</strong>
          <div className="mt-1 font-mono text-[8px] text-chrome-text/40">{o.agent_key} · {o.target_kind}{o.target_key ? `:${o.target_key}` : ""} · {o.proposed_by}</div>
        </button>)}
        {!overlays.length ? <Empty>No overlays yet — the gardener proposes them when lessons recur.</Empty> : null}
      </section>
      <section className="min-h-0 overflow-auto">
        {overlay ? <>
          <PaneTitle label={`${overlay.agent_key} · ${overlay.target_kind}${overlay.target_key ? `:${overlay.target_key}` : ""}`} note={when(overlay.created_at)} />
          <div className="px-5 py-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-[15px] font-semibold text-foreground">{overlay.section}</h2>
              <div className="flex gap-1.5">
                {overlay.adoption_state === "proposed" ? <>
                  <button type="button" onClick={() => void changeOverlayState(overlay.overlay_id, "auto_provisional")} className="border border-success/50 px-2 py-1 font-mono text-[8px] uppercase text-success hover:bg-success/10">adopt</button>
                  <button type="button" onClick={() => void changeOverlayState(overlay.overlay_id, "rejected")} className="border border-danger/40 px-2 py-1 font-mono text-[8px] uppercase text-danger hover:bg-danger/10">reject</button>
                </> : overlay.adoption_state === "auto_provisional" || overlay.adoption_state === "affirmed" ? <>
                  <button type="button" onClick={() => void changeOverlayState(overlay.overlay_id, "demoted")} className="border border-danger/40 px-2 py-1 font-mono text-[8px] uppercase text-danger hover:bg-danger/10">demote</button>
                  <button type="button" onClick={() => void changeOverlayState(overlay.overlay_id, "retired")} className="border border-chrome-border px-2 py-1 font-mono text-[8px] uppercase text-chrome-text/60 hover:text-foreground">retire</button>
                </> : <button type="button" onClick={() => void changeOverlayState(overlay.overlay_id, "auto_provisional")} className="border border-rvbbit-accent/40 px-2 py-1 font-mono text-[8px] uppercase text-rvbbit-accent hover:bg-rvbbit-accent/10">revive</button>}
              </div>
            </div>
            {overlay.kind === "behavior" && overlay.adoption_state === "proposed" ? <div className="mt-3 border-l-2 border-warning/60 bg-warning/[0.06] px-3 py-2 text-[10px] text-chrome-text/70"><b>Behavior change awaiting adoption.</b> This edits how the agent acts, not just what it knows — the one gate the doctrine keeps. Adopting composes it beneath the baseline at the next Hermes reconcile.</div> : null}
            {overlay.rationale ? <p className="mt-3 text-[10.5px] leading-relaxed text-chrome-text/65"><b className="text-chrome-text/80">Rationale:</b> {overlay.rationale}</p> : null}
            {overlay.last_event_reason && (overlay.adoption_state === "rejected" || overlay.adoption_state === "demoted") ? <p className="mt-2 text-[10px] text-danger/80"><b>{overlay.last_event_kind}:</b> {overlay.last_event_reason}</p> : null}
            <pre className="mt-4 whitespace-pre-wrap border border-chrome-border/60 bg-foreground/[0.015] px-4 py-3 font-sans text-[11px] leading-relaxed text-foreground">{overlay.body}</pre>
            <p className="mt-3 font-mono text-[8px] text-chrome-text/40">Adopted overlays compose beneath the repo baseline SOUL/SKILL at the next Hermes reconcile (deploy or container restart). Rejections are permanent for this exact body — the audit ledger blocks unchanged re-proposals.</p>
          </div>
        </> : <Empty>Select an overlay.</Empty>}
      </section>
    </div> :
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(300px,.8fr)_minmax(420px,1.6fr)] overflow-hidden">
      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Pages" note={`${visible.length} of ${pages.length}`} />
        {visible.map((p) => <button key={p.slug} type="button" onClick={() => { setSelected(p.slug); setRevisions([]); void loadRevisions(p.page_id) }} className={cn("w-full border-b border-chrome-border/60 px-4 py-3 text-left hover:bg-foreground/[0.025]", page?.slug === p.slug && "bg-rvbbit-accent/[0.04]")}>
          <div className="flex items-center justify-between gap-2"><StateBadge value={p.adoption_state} />{p.flagged_at ? <span className="font-mono text-[8px] uppercase text-warning">flagged</span> : null}</div>
          <strong className="mt-1.5 block text-[11px] text-foreground">{p.title}</strong>
          <div className="mt-1 flex justify-between font-mono text-[8px] text-chrome-text/40">
            <span>{p.author_kind}:{p.author_key}</span><span>rev {p.revision}</span>
          </div>
          {p.linked_claims > 0 ? <div className="mt-1 font-mono text-[8px] text-chrome-text/45">{p.supported}/{p.linked_claims} claims supported{p.contradicted ? <span className="text-danger"> · {p.contradicted} contradicted</span> : null}</div> : null}
        </button>)}
        {!visible.length ? <Empty>No pages{filter !== "all" ? ` in ${filter}` : " yet — the gardener writes nightly"}.</Empty> : null}
      </section>
      <section className="min-h-0 overflow-auto">
        {page ? <>
          <PaneTitle label={page.slug} note={`updated ${when(page.updated_at)}`} />
          <div className="px-5 py-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-[15px] font-semibold text-foreground">{page.title}</h2>
              <div className="flex gap-1.5">
                {page.adoption_state !== "demoted" && page.adoption_state !== "retired"
                  ? <button type="button" onClick={() => void changeState(page.slug, "demoted")} className="border border-danger/40 px-2 py-1 font-mono text-[8px] uppercase text-danger hover:bg-danger/10">demote</button>
                  : <button type="button" onClick={() => void changeState(page.slug, "auto_provisional")} className="border border-rvbbit-accent/40 px-2 py-1 font-mono text-[8px] uppercase text-rvbbit-accent hover:bg-rvbbit-accent/10">revive</button>}
                {page.adoption_state !== "retired"
                  ? <button type="button" onClick={() => void changeState(page.slug, "retired")} className="border border-chrome-border px-2 py-1 font-mono text-[8px] uppercase text-chrome-text/60 hover:text-foreground">retire</button>
                  : null}
              </div>
            </div>
            {page.flag_reason ? <div className="mt-3 border-l-2 border-warning/60 bg-warning/[0.06] px-3 py-2 text-[10px] text-chrome-text/70"><b>Flagged:</b> {page.flag_reason} — the gardener resolves this by rewriting or retiring; unresolved flags auto-retire at the policy backstop.</div> : null}
            {page.tags.length ? <div className="mt-2 flex flex-wrap gap-1.5">{page.tags.map((t) => <span key={t} className="border border-chrome-border px-1.5 py-0.5 font-mono text-[8px] text-chrome-text/55">{t}</span>)}</div> : null}
            <pre className="mt-4 whitespace-pre-wrap border border-chrome-border/60 bg-foreground/[0.015] px-4 py-3 font-sans text-[11px] leading-relaxed text-foreground">{page.body}</pre>
            {revisions.length ? <div className="mt-4">
              <span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">Prior revisions</span>
              {revisions.map((r) => <div key={r.revision} className="mt-1.5 flex justify-between border-b border-chrome-border/40 pb-1.5 font-mono text-[9px] text-chrome-text/55">
                <span>rev {r.revision} · {r.title}</span><span>{r.author_kind}:{r.author_key} · {when(r.created_at)}</span>
              </div>)}
            </div> : null}
          </div>
        </> : <Empty>Select a page.</Empty>}
      </section>
    </div>}
  </div>
}
