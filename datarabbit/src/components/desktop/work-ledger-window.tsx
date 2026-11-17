"use client"

import { useCallback, useMemo, useState, type ReactNode } from "react"
import { AlertTriangle, ChevronDown, ChevronRight, Clock, Pencil, RefreshCw, Save, X } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { useWorkspaceActive } from "./workspace-active-context"
import {
  clearMemberRate,
  fetchWorkLedger,
  saveSnapshot,
  setCreditMinutes,
  setMemberRate,
  type LedgerSnapshot,
  type WorkLedgerState,
} from "@/lib/rvbbit/work-ledger"

interface Props { activeConnectionId: string | null; hasRvbbit: boolean }

function monthStart(anchor: Date): string {
  return `${anchor.getFullYear()}-${String(anchor.getMonth() + 1).padStart(2, "0")}-01`
}

function monthEnd(anchor: Date): string {
  const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0)
  return `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, "0")}-${String(last.getDate()).padStart(2, "0")}`
}

function monthLabel(anchor: Date): string {
  return anchor.toLocaleDateString([], { month: "long", year: "numeric" })
}

function when(value: string | null | undefined) {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function usd(value: number): string {
  return `$${Math.round(value).toLocaleString()}`
}

function Label({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">{children}</span>
}

function PaneTitle({ label, note }: { label: string; note?: string }) {
  return <div className="sticky top-0 z-10 flex items-baseline justify-between border-b border-chrome-border bg-chrome px-4 py-2"><Label>{label}</Label>{note ? <span className="font-mono text-[8px] text-chrome-text/35">{note}</span> : null}</div>
}

function Readout({ label, value, note, accent }: { label: string; value: string; note: string; accent?: boolean }) {
  return <div className="border-r border-chrome-border px-4 py-3 last:border-r-0">
    <div className={cn("font-mono text-[19px] font-semibold tabular-nums", accent ? "text-rvbbit-accent" : "text-foreground")}>{value}</div>
    <div className="mt-0.5 font-mono text-[8px] uppercase tracking-[0.12em] text-chrome-text/45">{label}</div>
    <div className="mt-0.5 text-[8px] text-chrome-text/35">{note}</div>
  </div>
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-8 text-center text-[10px] text-chrome-text/50">{children}</div>
}

const EMPTY_STATE: WorkLedgerState = {
  ready: false, error: null, credits: [], rates: [], classes: [], members: [],
  totalHours: 0, totalValueUsd: 0, defaultHourlyUsd: 45, snapshots: [],
}

export function WorkLedgerWindow({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [anchor, setAnchor] = useState(() => new Date())
  const [state, setState] = useState<WorkLedgerState>(EMPTY_STATE)
  const [loaded, setLoaded] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedNote, setSavedNote] = useState<string | null>(null)
  const [editingClass, setEditingClass] = useState<string | null>(null)
  const [editingMember, setEditingMember] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [openSnapshot, setOpenSnapshot] = useState<string | null>(null)

  const start = monthStart(anchor)
  const end = monthEnd(anchor)

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    setRefreshing(true)
    const result = await fetchWorkLedger(activeConnectionId, start, end)
    setRefreshing(false)
    setLoaded(true)
    setState(result)
  }, [activeConnectionId, hasRvbbit, start, end])

  usePolling(reload, 20_000, { enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive), resetKey: `${activeConnectionId}:${start}` })

  const shiftMonth = (delta: number) => {
    setAnchor((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1))
    setLoaded(false)
  }

  const objectCount = useMemo(() => state.classes.reduce((a, c) => a + Number(c.count || 0), 0), [state.classes])
  const fte = state.totalHours / 160

  const commitCredit = async (cls: string) => {
    if (!activeConnectionId) return
    const minutes = Number(draft)
    setEditingClass(null)
    const result = await setCreditMinutes(activeConnectionId, cls, minutes)
    if (!result.ok) setState((s) => ({ ...s, error: result.error || "credit update failed" }))
    void reload()
  }

  const commitRate = async (email: string, excluded: boolean) => {
    if (!activeConnectionId) return
    const rate = Number(draft)
    setEditingMember(null)
    const result = await setMemberRate(activeConnectionId, email, rate, excluded)
    if (!result.ok) setState((s) => ({ ...s, error: result.error || "rate update failed" }))
    void reload()
  }

  const toggleExcluded = async (email: string, excluded: boolean, currentRate: number) => {
    if (!activeConnectionId) return
    const result = await setMemberRate(activeConnectionId, email, currentRate, excluded)
    if (!result.ok) setState((s) => ({ ...s, error: result.error || "exclude toggle failed" }))
    void reload()
  }

  const snapshot = async () => {
    if (!activeConnectionId || saving) return
    setSaving(true)
    setSavedNote(null)
    const result = await saveSnapshot(activeConnectionId, start, end, "datarabbit")
    setSaving(false)
    if (result.ok) setSavedNote(`${monthLabel(anchor)} snapshot stored`)
    else setState((s) => ({ ...s, error: result.error }))
    void reload()
  }

  if (!hasRvbbit || !activeConnectionId) return <Empty>The Work Ledger needs an active RVBBIT connection.</Empty>
  if (loaded && !state.ready && !state.error) return <Empty>The Work Ledger tables are not installed in this database (migration 0367).</Empty>

  return <div className="flex h-full min-h-0 flex-col text-chrome-text">
    <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
      <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10"><Clock className="h-4 w-4 text-rvbbit-accent" /></div>
      <div><h1 className="text-[13px] font-semibold text-foreground">Work Ledger</h1><p className="text-[10px] text-chrome-text/55">Hours returned to the team · flat operator-set credits · when in doubt, it undercounts</p></div>
      <div className="ml-auto flex items-center gap-2">
        <div className="flex items-center border border-chrome-border">
          <button type="button" onClick={() => shiftMonth(-1)} className="px-2 py-1.5 hover:bg-foreground/[0.04]" aria-label="Previous month"><ChevronDown className="h-3 w-3 rotate-90" /></button>
          <span className="min-w-[110px] px-2 text-center font-mono text-[9px] text-foreground">{monthLabel(anchor)}</span>
          <button type="button" onClick={() => shiftMonth(1)} className="px-2 py-1.5 hover:bg-foreground/[0.04]" aria-label="Next month"><ChevronRight className="h-3 w-3" /></button>
        </div>
        <button type="button" onClick={() => void reload()} disabled={refreshing} className="inline-flex items-center gap-1.5 border border-chrome-border px-2 py-1.5 text-[9px] text-foreground hover:border-rvbbit-accent/50"><RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />Refresh</button>
        <button type="button" onClick={() => void snapshot()} disabled={saving || !state.ready} className="inline-flex items-center gap-1.5 border border-rvbbit-accent/50 bg-rvbbit-accent/10 px-2 py-1.5 text-[9px] text-foreground hover:bg-rvbbit-accent/20 disabled:opacity-50"><Save className="h-3 w-3" />{saving ? "Saving…" : "Save snapshot"}</button>
      </div>
    </header>
    {state.error ? <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger"><AlertTriangle className="h-3.5 w-3.5" />{state.error}</div> : null}
    {savedNote ? <div className="border-b border-rvbbit-accent/30 bg-rvbbit-accent/[0.06] px-4 py-1.5 text-[9px] text-rvbbit-accent">{savedNote} — replicates with the rvbbit schema wherever mirrors reach.</div> : null}
    <div className="grid grid-cols-4 border-b border-chrome-border">
      <Readout label="hours returned" value={state.totalHours.toFixed(1)} note={`≈ ${fte.toFixed(1)} FTE of the month`} accent />
      <Readout label="estimated value" value={usd(state.totalValueUsd)} note={`default rate $${state.defaultHourlyUsd}/hr`} />
      <Readout label="team members" value={String(state.members.length)} note="operators excluded entirely" />
      <Readout label="work objects" value={String(objectCount)} note="each backed by a stored receipt" />
    </div>
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(340px,1.1fr)_minmax(320px,1fr)_minmax(280px,.85fr)] overflow-hidden">

      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Work delivered" note="credited each · flat minutes" />
        <table className="w-full text-[10px]">
          <tbody>
            {state.classes.map((c) => <tr key={c.class} className="border-b border-chrome-border/60">
              <td className="px-4 py-2.5">
                <div className="text-foreground">{c.label}</div>
                <div className="font-mono text-[8px] text-chrome-text/40">{c.class}</div>
              </td>
              <td className="px-2 py-2.5 text-right font-mono tabular-nums text-foreground">{c.count}</td>
              <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                {editingClass === c.class
                  ? <span className="inline-flex items-center gap-1">
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void commitCredit(c.class); if (e.key === "Escape") setEditingClass(null) }} className="w-12 border border-rvbbit-accent/50 bg-transparent px-1 py-0.5 text-right text-foreground outline-none" />
                      <button type="button" onClick={() => setEditingClass(null)} aria-label="Cancel"><X className="h-3 w-3 text-chrome-text/50" /></button>
                    </span>
                  : <button type="button" onClick={() => { setEditingClass(c.class); setEditingMember(null); setDraft(String(c.minutes_each)) }} className="inline-flex items-center gap-1 text-chrome-text/70 hover:text-rvbbit-accent">{c.minutes_each} min<Pencil className="h-2.5 w-2.5 opacity-50" /></button>}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tabular-nums font-semibold text-foreground">{Number(c.hours).toFixed(1)}</td>
            </tr>)}
            <tr><td className="px-4 py-2.5 font-semibold text-foreground">Returned to the team</td><td /><td /><td className="px-4 py-2.5 text-right font-mono font-semibold tabular-nums text-rvbbit-accent">{state.totalHours.toFixed(1)}</td></tr>
          </tbody>
        </table>
        <div className="mx-4 my-3 border-l-2 border-warning/60 bg-warning/[0.06] px-3 py-2 text-[9px] leading-relaxed text-chrome-text/60">
          <b className="text-chrome-text/80">Deliberately not counted:</b> raw database queries, design-sketch iterations, failed or partial runs, and every excluded member&apos;s sessions. Credits are flat per-class minutes set here by an operator — none are model-generated.
        </div>
      </section>

      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Per member" note="hours × hourly rate" />
        <table className="w-full text-[10px]">
          <tbody>
            {state.members.map((m) => <tr key={m.owner_email} className="border-b border-chrome-border/60">
              <td className="px-4 py-2.5">
                <div className="max-w-[180px] truncate text-foreground" title={m.owner_email}>{m.owner_email}</div>
                <div className="font-mono text-[8px] text-chrome-text/40">{m.count} objects · {Number(m.hours).toFixed(1)} hrs</div>
              </td>
              <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                {editingMember === m.owner_email
                  ? <span className="inline-flex items-center gap-1">
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void commitRate(m.owner_email, false); if (e.key === "Escape") setEditingMember(null) }} className="w-14 border border-rvbbit-accent/50 bg-transparent px-1 py-0.5 text-right text-foreground outline-none" />
                      <button type="button" onClick={() => setEditingMember(null)} aria-label="Cancel"><X className="h-3 w-3 text-chrome-text/50" /></button>
                    </span>
                  : <button type="button" onClick={() => { setEditingMember(m.owner_email); setEditingClass(null); setDraft(String(m.hourly_usd)) }} className={cn("inline-flex items-center gap-1 hover:text-rvbbit-accent", m.rate_is_default ? "text-chrome-text/45" : "text-chrome-text/80")} title={m.rate_is_default ? "inherits the default rate" : "member-specific rate"}>${m.hourly_usd}/hr<Pencil className="h-2.5 w-2.5 opacity-50" /></button>}
              </td>
              <td className="px-2 py-2.5 text-right font-mono font-semibold tabular-nums text-foreground">{usd(Number(m.value_usd))}</td>
              <td className="px-3 py-2.5 text-right">
                <button type="button" onClick={() => void toggleExcluded(m.owner_email, true, Number(m.hourly_usd))} className="font-mono text-[8px] uppercase tracking-[0.1em] text-chrome-text/35 hover:text-danger" title="Exclude this member (operator/test account) from all reports">exclude</button>
              </td>
            </tr>)}
          </tbody>
        </table>
        {state.rates.filter((r) => r.excluded).length ? <div className="px-4 py-2 text-[8px] text-chrome-text/40">
          Excluded: {state.rates.filter((r) => r.excluded).map((r) => <button key={r.owner_email} type="button" onClick={() => void toggleExcluded(r.owner_email, false, Number(r.hourly_usd) || state.defaultHourlyUsd)} className="mr-2 font-mono underline decoration-dotted hover:text-rvbbit-accent" title="Re-include">{r.owner_email}</button>)}
        </div> : null}
        {loaded && !state.members.length ? <Empty>No credited work in {monthLabel(anchor)}.</Empty> : null}
      </section>

      <section className="min-h-0 overflow-auto">
        <PaneTitle label="Saved snapshots" note="rvbbit.work_ledger_snapshots" />
        {state.snapshots.map((s: LedgerSnapshot) => <div key={s.id} className="border-b border-chrome-border/60">
          <button type="button" onClick={() => setOpenSnapshot((cur) => cur === s.id ? null : s.id)} className="w-full px-4 py-3 text-left hover:bg-foreground/[0.025]">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[9px] text-foreground">{s.period_start} → {s.period_end}</span>
              <span className="font-mono text-[9px] font-semibold tabular-nums text-rvbbit-accent">{Number(s.total_hours).toFixed(1)} hrs</span>
            </div>
            <div className="mt-1 flex justify-between text-[8px] text-chrome-text/45">
              <span>{s.members} members · {usd(Number(s.total_value_usd))}</span>
              <span>{when(s.generated_at)}{s.generated_by ? ` · ${s.generated_by}` : ""}</span>
            </div>
          </button>
          {openSnapshot === s.id ? <pre className="max-h-64 overflow-auto border-t border-chrome-border/60 bg-foreground/[0.02] px-4 py-2 font-mono text-[8px] leading-relaxed text-chrome-text/70">{JSON.stringify(s.report, null, 2)}</pre> : null}
        </div>)}
        {loaded && !state.snapshots.length ? <Empty>No snapshots stored yet. Save one to make this period&apos;s report replicable.</Empty> : null}
      </section>
    </div>
  </div>
}
