"use client"

import { useCallback, useEffect, useState } from "react"
import type { ReactNode } from "react"
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  Clock,
  Pause,
  Play,
  RefreshCw,
  Save,
  Users,
} from "@/lib/icons"
import { cn } from "@/lib/utils"
import {
  enqueueDreamSweep,
  fetchDreamingSnapshot,
  saveDreamingSettings,
  type DreamingDay,
  type DreamingRange,
  type DreamingSettings,
  type DreamingSnapshot,
} from "@/lib/rvbbit/dreaming"
import {
  detectCronState,
  dreamingInstallSql,
  exec as execCron,
  isDreamingJob,
  listCronJobs,
  setActiveSql,
  type CronJob,
  type CronState,
} from "@/lib/rvbbit/cron"
import { describeCron } from "@/lib/desktop/cron-expr"
import { fmtAgo, fmtCount, fmtMs } from "./instruments"

interface DreamingWindowProps {
  activeConnectionId: string | null
  hasRvbbit: boolean
  workspaceActive: boolean
  onOpenSql: (sql: string, title: string) => void
}

const RANGES: { key: DreamingRange; label: string }[] = [
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
]

export function DreamingWindow({
  activeConnectionId,
  hasRvbbit,
  workspaceActive,
  onOpenSql,
}: DreamingWindowProps) {
  const [range, setRange] = useState<DreamingRange>("30d")
  const [snapshot, setSnapshot] = useState<DreamingSnapshot | null>(null)
  const [cronState, setCronState] = useState<CronState | null>(null)
  const [cronJob, setCronJob] = useState<CronJob | null>(null)
  const [draft, setDraft] = useState<DreamingSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) {
      setSnapshot(null)
      setDraft(null)
      setCronState(null)
      setCronJob(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [next, cron] = await Promise.all([
        fetchDreamingSnapshot(activeConnectionId, range),
        detectCronState(activeConnectionId),
      ])
      setSnapshot(next)
      setDraft(next.settings)
      setError(next.error ?? cron.error)
      setCronState(cron.state)
      if (cron.state?.created) {
        const jobs = await listCronJobs(activeConnectionId, cron.state.cronDb)
        setCronJob(jobs.jobs.find(isDreamingJob) ?? null)
        if (jobs.error) setError(jobs.error)
      } else {
        setCronJob(null)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [activeConnectionId, hasRvbbit, range])

  useEffect(() => {
    if (!workspaceActive) return
    queueMicrotask(() => void refresh())
  }, [refresh, workspaceActive])

  useEffect(() => {
    if (!workspaceActive || !activeConnectionId || !hasRvbbit) return
    const timer = window.setInterval(() => void refresh(), 30_000)
    return () => window.clearInterval(timer)
  }, [activeConnectionId, hasRvbbit, refresh, workspaceActive])

  const runAction = useCallback(async (name: string, action: () => Promise<{ ok: boolean; error?: string | null }>, ok: string) => {
    if (busy) return
    setBusy(name)
    setNotice(null)
    setError(null)
    try {
      const result = await action()
      if (!result.ok) setError(result.error ?? "Operation failed")
      else setNotice(ok)
      await refresh()
    } finally {
      setBusy(null)
    }
  }, [busy, refresh])

  const installSchedule = () => {
    if (!activeConnectionId || !cronState) return
    void runAction(
      "schedule",
      () => execCron(activeConnectionId, dreamingInstallSql(cronState.thisDb), cronState.cronDb ?? undefined),
      "Paused 03:00 Dreaming task added.",
    )
  }

  const toggleSchedule = () => {
    if (!activeConnectionId || !cronState || !cronJob) return
    const active = !cronJob.active
    void runAction(
      "schedule",
      () => execCron(activeConnectionId, setActiveSql(cronJob.jobid, active), cronState.cronDb ?? undefined),
      active ? "Dreaming schedule enabled." : "Dreaming schedule paused.",
    )
  }

  const saveSettings = () => {
    if (!activeConnectionId || !draft) return
    void runAction(
      "settings",
      () => saveDreamingSettings(activeConnectionId, draft),
      "Dreaming options saved.",
    )
  }

  const runNow = () => {
    if (!activeConnectionId) return
    void runAction(
      "run",
      async () => {
        const result = await enqueueDreamSweep(activeConnectionId)
        return { ok: result.ok, error: result.error }
      },
      "Dream sweep queued. Warehouse will process it in the background.",
    )
  }

  const dirty = !!draft
    && !!snapshot?.settings
    && JSON.stringify(draft) !== JSON.stringify(snapshot.settings)

  if (!activeConnectionId || !hasRvbbit) {
    return <CenteredMessage>{!activeConnectionId ? "Choose a connection to inspect Dreaming." : "Dreaming requires RVBBIT."}</CenteredMessage>
  }
  if (!snapshot && loading) return <CenteredMessage>Loading Dreaming operations…</CenteredMessage>
  if (snapshot && !snapshot.detected) {
    return (
      <CenteredMessage>
        Dreaming operations are not installed on this database yet. Apply the latest pg_rvbbit migration.
      </CenteredMessage>
    )
  }

  const overview = snapshot?.overview
  const scheduleLabel = !cronState?.created
    ? "pg_cron unavailable"
    : !cronJob
      ? "not seeded"
      : cronJob.active
        ? "enabled"
        : "paused"
  const scheduleDetail = `${cronJob ? describeCron(cronJob.schedule) : "03:00 seed available"} · ${cronState?.cronTimezone ?? "database time"}`

  return (
    <div className="flex h-full flex-col bg-doc-bg text-foreground group-data-[focused=false]/window:bg-doc-bg/70">
      <header className="flex items-center gap-3 border-b border-chrome-border bg-chrome-bg/55 px-3 py-2">
        <div className="grid h-8 w-8 place-items-center rounded-md border border-rvbbit-accent/30 bg-rvbbit-bg/45">
          <Brain className="h-4 w-4 text-rvbbit-accent" />
        </div>
        <div className="min-w-0">
          <div className="text-[12px] font-medium text-foreground">Dreaming Operations</div>
          <div className="text-[10px] text-chrome-text/55">timing and execution receipts · private content excluded</div>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setRange(item.key)}
              className={cn(
                "rounded px-2 py-1 font-mono text-[10px] transition-colors",
                range === item.key ? "bg-rvbbit-accent/18 text-rvbbit-accent" : "text-chrome-text/55 hover:bg-foreground/[0.06] hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="ml-1 grid h-7 w-7 place-items-center rounded text-chrome-text/55 hover:bg-foreground/[0.07] hover:text-foreground disabled:opacity-40"
            title="Refresh"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
      </header>

      {error ? <Notice tone="error" text={error} /> : null}
      {notice ? <Notice tone="ok" text={notice} /> : null}

      <div className="min-h-0 flex-1 overflow-auto p-3">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            icon={Clock}
            label="Schedule"
            value={scheduleLabel}
            detail={scheduleDetail}
            tone={cronJob?.active ? "ok" : "muted"}
          />
          <StatCard
            icon={Users}
            label="Jobs"
            value={fmtCount(overview?.jobs ?? 0)}
            detail={`${overview?.runningJobs ?? 0} running · ${overview?.queuedJobs ?? 0} queued`}
          />
          <StatCard
            icon={Clock}
            label="Average runtime"
            value={overview?.avgRuntimeMs == null ? "—" : fmtMs(overview.avgRuntimeMs)}
            detail={`p95 ${overview?.p95RuntimeMs == null ? "—" : fmtMs(overview.p95RuntimeMs)}`}
          />
          <StatCard
            icon={overview?.failedJobs ? AlertTriangle : CheckCircle2}
            label="Outcomes"
            value={`${overview?.failedJobs ?? 0} failed`}
            detail={`${overview?.completedJobs ?? 0} complete · ${overview?.skippedJobs ?? 0} unchanged/paused`}
            tone={overview?.failedJobs ? "warn" : "ok"}
          />
        </div>

        <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0 space-y-3">
            <Section title="Runtime over time" subtitle="daily median · dotted p95">
              <RuntimeChart data={snapshot?.daily ?? []} />
            </Section>

            <Section title="Per-user runtime" subtitle={`${snapshot?.users.length ?? 0} active users observed`}>
              <UserTable users={snapshot?.users ?? []} />
            </Section>

            <Section title="Recent execution" subtitle="latest 200 jobs in the selected period">
              <JobTable jobs={snapshot?.jobs ?? []} />
            </Section>
          </div>

          <aside className="space-y-3">
            <Section title="Schedule" subtitle="cadence remains editable in Scheduled Tasks">
              <div className="space-y-2 p-3 text-[11px]">
                <div className="flex items-center justify-between">
                  <span className="text-chrome-text/60">Task</span>
                  <span className={cn("font-mono", cronJob?.active ? "text-success" : "text-chrome-text/55")}>{scheduleLabel}</span>
                </div>
                {cronJob ? (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-chrome-text/60">Cadence</span>
                      <span className="truncate font-mono text-foreground/80">{describeCron(cronJob.schedule)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-chrome-text/60">Time zone</span>
                      <span className="truncate font-mono text-foreground/80">{cronState?.cronTimezone ?? "database time"}</span>
                    </div>
                    <button type="button" disabled={!!busy} onClick={toggleSchedule} className="inline-flex h-7 w-full items-center justify-center gap-1.5 rounded border border-rvbbit-accent/25 bg-rvbbit-accent/10 px-2 text-[10.5px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/20 disabled:opacity-35">
                      {cronJob.active ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                      {cronJob.active ? "Pause schedule" : "Enable schedule"}
                    </button>
                  </>
                ) : (
                  <button type="button" disabled={!!busy || !cronState?.created} onClick={installSchedule} className="inline-flex h-7 w-full items-center justify-center gap-1.5 rounded border border-rvbbit-accent/25 bg-rvbbit-accent/10 px-2 text-[10.5px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/20 disabled:opacity-35">
                    <Clock className="h-3.5 w-3.5" /> Add paused 03:00 task
                  </button>
                )}
                <button type="button" disabled={!!busy || draft?.processingPaused} onClick={runNow} className="inline-flex h-7 w-full items-center justify-center gap-1.5 rounded border border-rvbbit-accent/25 bg-rvbbit-accent/10 px-2 text-[10.5px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/20 disabled:opacity-35">
                  <Play className="h-3.5 w-3.5" /> Queue one sweep now
                </button>
              </div>
            </Section>

            <Section title="Execution options" subtitle="applied when the next sweep is queued">
              {draft ? (
                <div className="space-y-3 p-3">
                  <ToggleRow
                    label="Pause processing"
                    detail="Leave queued work durable"
                    value={draft.processingPaused}
                    onChange={(value) => setDraft({ ...draft, processingPaused: value })}
                  />
                  <ToggleRow
                    label="Company pass"
                    detail="Global, de-identified Dreaming"
                    value={draft.companyEnabled}
                    onChange={(value) => setDraft({ ...draft, companyEnabled: value })}
                  />
                  <ToggleRow
                    label="Personal passes"
                    detail="Owner-private dossiers and Dreams"
                    value={draft.personalEnabled}
                    onChange={(value) => setDraft({ ...draft, personalEnabled: value })}
                  />
                  <NumberRow label="Active window" suffix="days" min={7} max={90} value={draft.activeWindowDays} onChange={(value) => setDraft({ ...draft, activeWindowDays: value })} />
                  <NumberRow label="Chat threshold" suffix="turns" min={1} max={20} value={draft.minChatTurns} onChange={(value) => setDraft({ ...draft, minChatTurns: value })} />
                  <NumberRow label="Tool threshold" suffix="calls" min={1} max={50} value={draft.minToolCalls} onChange={(value) => setDraft({ ...draft, minToolCalls: value })} />
                  <NumberRow label="Users per sweep" suffix="max" min={1} max={500} value={draft.maxPersonalUsers} onChange={(value) => setDraft({ ...draft, maxPersonalUsers: value })} />
                  <button type="button" disabled={!dirty || !!busy} onClick={saveSettings} className="inline-flex h-7 w-full items-center justify-center gap-1.5 rounded border border-rvbbit-accent/25 bg-rvbbit-accent/10 px-2 text-[10.5px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/20 disabled:opacity-35">
                    <Save className="h-3.5 w-3.5" /> Save options
                  </button>
                </div>
              ) : <div className="p-3 text-[11px] text-chrome-text/55">Settings unavailable.</div>}
            </Section>

            <Section title="Recent sweeps" subtitle={`${overview?.sweeps ?? 0} in range`}>
              <div className="max-h-64 divide-y divide-chrome-border/70 overflow-auto">
                {(snapshot?.sweeps ?? []).slice(0, 20).map((sweep) => (
                  <button
                    key={sweep.id}
                    type="button"
                    onClick={() => onOpenSql(`SELECT * FROM rvbbit.calliope_dream_jobs WHERE sweep_id='${sweep.id}'::uuid ORDER BY queued_at;`, "Dream sweep jobs")}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-foreground/[0.035]"
                  >
                    <StatusDot status={sweep.status} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[10.5px]">
                        <span className="font-mono text-foreground/80">{sweep.source}</span>
                        <span className="text-chrome-text/45">{sweep.requestedAt ? fmtAgo(sweep.requestedAt) : "—"}</span>
                      </div>
                      <div className="truncate text-[9.5px] text-chrome-text/50">
                        {sweep.planned} jobs · {sweep.failed} failed · {sweep.runtimeMs == null ? "pending" : fmtMs(sweep.runtimeMs)}
                      </div>
                    </div>
                  </button>
                ))}
                {!snapshot?.sweeps.length ? <div className="p-3 text-[11px] text-chrome-text/45">No sweeps in this period.</div> : null}
              </div>
            </Section>
          </aside>
        </div>
      </div>
    </div>
  )
}

function CenteredMessage({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center bg-doc-bg p-8 text-center text-[12px] text-chrome-text/55">{children}</div>
}

function Notice({ tone, text }: { tone: "error" | "ok"; text: string }) {
  return (
    <div className={cn("border-b px-3 py-1.5 text-[10.5px]", tone === "error" ? "border-danger/35 bg-danger/10 text-danger" : "border-success/30 bg-success/10 text-success")}>
      {text}
    </div>
  )
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-md border border-chrome-border bg-secondary-background/25">
      <div className="flex items-end gap-2 border-b border-chrome-border bg-chrome-bg/35 px-3 py-2">
        <span className="text-[11px] font-medium text-foreground">{title}</span>
        {subtitle ? <span className="truncate text-[9.5px] text-chrome-text/45">{subtitle}</span> : null}
      </div>
      {children}
    </section>
  )
}

function StatCard({ icon: Icon, label, value, detail, tone = "muted" }: { icon: typeof Clock; label: string; value: string; detail: string; tone?: "muted" | "ok" | "warn" }) {
  return (
    <div className="rounded-md border border-chrome-border bg-secondary-background/30 p-3">
      <div className="flex items-center gap-1.5 text-[9.5px] uppercase tracking-[0.12em] text-chrome-text/45"><Icon className="h-3.5 w-3.5" />{label}</div>
      <div className={cn("mt-2 font-mono text-xl tabular-nums", tone === "ok" ? "text-success" : tone === "warn" ? "text-warning" : "text-foreground")}>{value}</div>
      <div className="mt-0.5 truncate text-[9.5px] text-chrome-text/50">{detail}</div>
    </div>
  )
}

function RuntimeChart({ data }: { data: DreamingDay[] }) {
  if (!data.length) return <div className="grid h-44 place-items-center text-[11px] text-chrome-text/40">No completed jobs in this period.</div>
  const width = 760
  const height = 180
  const pad = { left: 18, right: 18, top: 16, bottom: 26 }
  const values = data.flatMap((item) => [item.medianRuntimeMs ?? 0, item.p95RuntimeMs ?? 0])
  const max = Math.max(...values, 1)
  const x = (index: number) => pad.left + (index / Math.max(1, data.length - 1)) * (width - pad.left - pad.right)
  const y = (value: number | null) => pad.top + (1 - (value ?? 0) / max) * (height - pad.top - pad.bottom)
  const path = (key: "medianRuntimeMs" | "p95RuntimeMs") => data.map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(item[key]).toFixed(1)}`).join(" ")
  const area = `${path("medianRuntimeMs")} L${x(data.length - 1)},${height - pad.bottom} L${x(0)},${height - pad.bottom} Z`
  return (
    <div className="p-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full" role="img" aria-label="Dream job runtime by day">
        <defs>
          <linearGradient id="dream-runtime-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--rvbbit-accent)" stopOpacity="0.26" />
            <stop offset="100%" stopColor="var(--rvbbit-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((ratio) => <line key={ratio} x1={pad.left} x2={width-pad.right} y1={pad.top+ratio*(height-pad.top-pad.bottom)} y2={pad.top+ratio*(height-pad.top-pad.bottom)} stroke="var(--chrome-border)" strokeOpacity="0.65" />)}
        <path d={area} fill="url(#dream-runtime-fill)" />
        <path d={path("p95RuntimeMs")} fill="none" stroke="var(--warning)" strokeOpacity="0.75" strokeDasharray="5 5" strokeWidth="1.4" />
        <path d={path("medianRuntimeMs")} fill="none" stroke="var(--rvbbit-accent)" strokeWidth="2" />
        {data.map((item, index) => (
          <g key={item.day}>
            <circle cx={x(index)} cy={y(item.medianRuntimeMs)} r="2.2" fill="var(--rvbbit-accent)"><title>{item.day}: median {fmtMs(item.medianRuntimeMs ?? 0)} · p95 {fmtMs(item.p95RuntimeMs ?? 0)} · {item.jobs} jobs</title></circle>
          </g>
        ))}
        <text x={pad.left} y={height-7} fill="var(--chrome-text)" opacity="0.5" fontSize="9">{data[0]?.day}</text>
        <text x={width-pad.right} y={height-7} textAnchor="end" fill="var(--chrome-text)" opacity="0.5" fontSize="9">{data[data.length-1]?.day}</text>
      </svg>
    </div>
  )
}

function UserTable({ users }: { users: DreamingSnapshot["users"] }) {
  if (!users.length) return <div className="p-4 text-[11px] text-chrome-text/40">No personal jobs in this period.</div>
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full border-collapse text-left text-[10.5px]">
        <thead className="sticky top-0 bg-chrome-bg/95 text-[9px] uppercase tracking-wider text-chrome-text/45">
          <tr><th className="px-3 py-1.5">User</th><th className="px-2 py-1.5 text-right">Runs</th><th className="px-2 py-1.5 text-right">Average</th><th className="px-2 py-1.5 text-right">p95</th><th className="px-3 py-1.5 text-right">Last</th></tr>
        </thead>
        <tbody className="divide-y divide-chrome-border/65">
          {users.map((user) => (
            <tr key={user.email} className="hover:bg-foreground/[0.025]">
              <td className="max-w-72 px-3 py-2"><div className="truncate text-foreground/85">{user.displayName || user.email}</div>{user.displayName ? <div className="truncate font-mono text-[9px] text-chrome-text/40">{user.email}</div> : null}</td>
              <td className="px-2 py-2 text-right font-mono tabular-nums">{user.runs}<div className={cn("text-[8.5px]", user.failed ? "text-danger" : "text-chrome-text/35")}>{user.failed} failed</div></td>
              <td className="px-2 py-2 text-right font-mono tabular-nums">{user.avgRuntimeMs == null ? "—" : fmtMs(user.avgRuntimeMs)}</td>
              <td className="px-2 py-2 text-right font-mono tabular-nums">{user.p95RuntimeMs == null ? "—" : fmtMs(user.p95RuntimeMs)}</td>
              <td className="px-3 py-2 text-right text-chrome-text/50">{user.lastStartedAt ? fmtAgo(user.lastStartedAt) : "—"}<div className="text-[8.5px]">{user.lastOutcome ?? ""}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function JobTable({ jobs }: { jobs: DreamingSnapshot["jobs"] }) {
  if (!jobs.length) return <div className="p-4 text-[11px] text-chrome-text/40">No Dream jobs in this period.</div>
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full border-collapse text-left text-[10px]">
        <thead className="sticky top-0 bg-chrome-bg/95 text-[9px] uppercase tracking-wider text-chrome-text/45">
          <tr><th className="px-3 py-1.5">Scope / user</th><th className="px-2 py-1.5">Outcome</th><th className="px-2 py-1.5 text-right">Runtime</th><th className="px-3 py-1.5 text-right">Started</th></tr>
        </thead>
        <tbody className="divide-y divide-chrome-border/65">
          {jobs.map((job) => (
            <tr key={job.id} className="hover:bg-foreground/[0.025]" title={job.error ?? undefined}>
              <td className="max-w-80 px-3 py-2"><div className="flex items-center gap-1.5"><StatusDot status={job.status} /><span className="truncate text-foreground/80">{job.scope === "company" ? "Company" : job.displayName || job.owner}</span></div>{job.scope === "personal" && job.displayName ? <div className="ml-3 truncate font-mono text-[8.5px] text-chrome-text/35">{job.owner}</div> : null}</td>
              <td className={cn("px-2 py-2 font-mono", job.status === "failed" ? "text-danger" : "text-chrome-text/60")}>{job.outcome || job.status}</td>
              <td className="px-2 py-2 text-right font-mono tabular-nums">{job.runtimeMs == null ? "—" : fmtMs(job.runtimeMs)}</td>
              <td className="px-3 py-2 text-right text-chrome-text/45">{job.startedAt ? fmtAgo(job.startedAt) : "queued"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ToggleRow({ label, detail, value, onChange }: { label: string; detail: string; value: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2">
      <button type="button" role="switch" aria-checked={value} onClick={() => onChange(!value)} className={cn("relative h-4 w-7 rounded-full transition-colors", value ? "bg-rvbbit-accent" : "bg-chrome-border")}><span className={cn("absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform", value ? "translate-x-3.5" : "translate-x-0.5")} /></button>
      <span className="min-w-0"><span className="block text-[10.5px] text-foreground/80">{label}</span><span className="block truncate text-[9px] text-chrome-text/40">{detail}</span></span>
    </label>
  )
}

function NumberRow({ label, suffix, value, min, max, onChange }: { label: string; suffix: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return (
    <label className="flex items-center gap-2 text-[10px]"><span className="flex-1 text-chrome-text/60">{label}</span><input type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))} className="h-6 w-16 rounded border border-chrome-border bg-background/50 px-1.5 text-right font-mono text-foreground outline-none focus:border-rvbbit-accent/60" /><span className="w-9 text-[9px] text-chrome-text/35">{suffix}</span></label>
  )
}

function StatusDot({ status }: { status: string }) {
  return <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", status === "complete" ? "bg-success" : status === "failed" || status === "partial" ? "bg-danger" : status === "running" ? "animate-pulse bg-rvbbit-accent" : "bg-chrome-text/35")} />
}
