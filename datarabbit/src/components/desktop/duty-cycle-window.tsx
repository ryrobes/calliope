"use client"

import { useCallback, useState, type ReactNode } from "react"
import { AlertTriangle, Moon, RefreshCw } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { useWorkspaceActive } from "./workspace-active-context"
import {
  fetchDutyCycles,
  fetchExecutions,
  type DutyCycleAlertItem,
  type DutyCycleExecution,
  type DutyCycleJob,
} from "@/lib/rvbbit/duty-cycle"

interface Props { activeConnectionId: string | null; hasRvbbit: boolean }

const statusTones: Record<string, string> = {
  completed: "var(--success)",
  running: "var(--rvbbit-accent)",
  failed: "var(--danger)",
  unknown: "var(--warning)",
}

function when(value: string | null | undefined) {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function StatusBadge({ value }: { value: string | null }) {
  const label = value || "never ran"
  return <span className="inline-flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.12em]" style={{ color: statusTones[label] || "var(--chrome-text)" }}><i className="h-1.5 w-1.5 rounded-full bg-current" />{label}</span>
}

function PaneTitle({ label, note }: { label: string; note?: string }) {
  return <div className="sticky top-0 z-10 flex items-baseline justify-between border-b border-chrome-border bg-chrome px-4 py-2"><span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">{label}</span>{note ? <span className="font-mono text-[8px] text-chrome-text/35">{note}</span> : null}</div>
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-8 text-center text-[10px] text-chrome-text/50">{children}</div>
}

export function DutyCycleWindow({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [jobs, setJobs] = useState<DutyCycleJob[]>([])
  const [alerts, setAlerts] = useState<DutyCycleAlertItem[]>([])
  const [ready, setReady] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [executions, setExecutions] = useState<DutyCycleExecution[]>([])

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    setRefreshing(true)
    const result = await fetchDutyCycles(activeConnectionId)
    setRefreshing(false)
    setReady(result.ready)
    setError(result.error)
    setJobs(result.jobs)
    setAlerts(result.alerts)
  }, [activeConnectionId, hasRvbbit])

  usePolling(reload, 20_000, { enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive), resetKey: activeConnectionId })

  const jobKey = (j: DutyCycleJob) => `${j.profile_key}:${j.job_id}`
  const job = jobs.find((j) => jobKey(j) === selected) || jobs[0] || null

  const loadExecutions = useCallback(async (j: DutyCycleJob) => {
    if (!activeConnectionId) return
    setExecutions(await fetchExecutions(activeConnectionId, j.profile_key, j.job_id))
  }, [activeConnectionId])

  const broken = jobs.filter((j) => j.failure_streak >= 2).length

  if (!hasRvbbit || !activeConnectionId) return <Empty>The Night Shift needs an active RVBBIT connection.</Empty>
  if (ready === false && !error) return <Empty>The duty-cycle mirror is not installed in this database (migration 0379).</Empty>

  return <div className="flex h-full min-h-0 flex-col text-chrome-text">
    <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
      <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10"><Moon className="h-4 w-4 text-rvbbit-accent" /></div>
      <div><h1 className="text-[13px] font-semibold text-foreground">Night Shift</h1><p className="text-[10px] text-chrome-text/55">Every scheduled duty cycle, its receipts, and who it told when it broke</p></div>
      <div className="ml-auto flex items-center gap-3">
        {broken > 0
          ? <span className="inline-flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.1em]" style={{ color: "var(--danger)" }}><AlertTriangle className="h-3 w-3" />{broken} failing</span>
          : <span className="font-mono text-[8px] uppercase tracking-[0.1em]" style={{ color: "var(--success)" }}>all cycles healthy</span>}
        <button type="button" onClick={() => void reload()} className="grid h-7 w-7 place-items-center border border-chrome-border text-chrome-text/60 hover:text-foreground" title="Refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
        </button>
      </div>
    </header>

    {error ? <div className="border-b border-chrome-border bg-danger/10 px-4 py-2 text-[10px]" style={{ color: "var(--danger)" }}>{error}</div> : null}

    {alerts.length > 0 ? <div className="border-b border-chrome-border px-4 py-2">
      <div className="font-mono text-[8px] uppercase tracking-[0.14em] text-chrome-text/45">Open inbox items ({alerts.length})</div>
      {alerts.map((a) => <div key={a.id} className="mt-1 flex items-baseline gap-2 text-[10px]">
        <span className="font-mono text-[8px] uppercase" style={{ color: a.urgency === "high" || a.urgency === "critical" ? "var(--danger)" : "var(--warning)" }}>{a.urgency}</span>
        <span className="text-foreground/90">{a.title}</span>
        <span className="ml-auto shrink-0 font-mono text-[8px] text-chrome-text/40">{when(a.created_at)}</span>
      </div>)}
    </div> : null}

    <div className="flex min-h-0 flex-1">
      <div className="w-[46%] min-w-[380px] overflow-y-auto border-r border-chrome-border">
        <PaneTitle label="Duty cycles" note={`${jobs.length} scheduled`} />
        {jobs.length === 0 ? <Empty>No mirrored cron jobs yet — the mirror sweeps every couple of minutes.</Empty> : jobs.map((j) => {
          const key = jobKey(j)
          return <button key={key} type="button"
            onClick={() => { setSelected(key); void loadExecutions(j) }}
            className={cn("block w-full border-b border-chrome-border/60 px-4 py-2.5 text-left hover:bg-chrome-hover/40", job && jobKey(job) === key && "bg-chrome-hover/60")}>
            <div className="flex items-baseline gap-2">
              <span className="truncate text-[11px] font-medium text-foreground">{j.name || j.job_id}</span>
              <span className="ml-auto shrink-0"><StatusBadge value={j.last_status} /></span>
            </div>
            <div className="mt-1 flex items-center gap-2 font-mono text-[8px] text-chrome-text/50">
              <span>{j.profile_key}</span>
              <span>· {j.schedule || "?"}</span>
              {j.pinned ? <span style={{ color: "var(--success)" }}>· pinned {j.provider}/{j.model}</span> : <span style={{ color: "var(--warning)" }}>· UNPINNED — drift-skippable</span>}
              {j.failure_streak > 0 ? <span style={{ color: "var(--danger)" }}>· streak {j.failure_streak}</span> : null}
              {j.drift_skipped ? <span style={{ color: "var(--danger)" }}>· drift</span> : null}
            </div>
          </button>
        })}
      </div>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <PaneTitle label={job ? (job.name || job.job_id) : "Executions"} note={job ? `last: ${when(job.last_run_at)}` : undefined} />
        {!job ? <Empty>Select a duty cycle.</Empty> : <>
          {job.last_error ? <div className="border-b border-chrome-border/60 px-4 py-2 text-[9px] leading-relaxed text-chrome-text/70"><span className="font-mono text-[8px] uppercase tracking-[0.1em] text-chrome-text/45">last error · </span>{job.last_error}</div> : null}
          {executions.length === 0 ? <Empty>Click the cycle again to load its execution history.</Empty> : executions.map((e) => <div key={e.execution_id} className="border-b border-chrome-border/50 px-4 py-2">
            <div className="flex items-baseline gap-2 text-[10px]">
              <StatusBadge value={e.status} />
              <span className="font-mono text-[8px] text-chrome-text/50">{when(e.started_at)}</span>
              {e.delivery_outcome ? <span className="ml-auto font-mono text-[8px] text-chrome-text/40">{e.delivery_outcome}</span> : null}
            </div>
            {e.error ? <div className="mt-1 text-[9px] leading-relaxed text-chrome-text/60">{e.error}</div> : null}
          </div>)}
        </>}
      </div>
    </div>
  </div>
}
