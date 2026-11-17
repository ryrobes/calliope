"use client"

import { useCallback, useRef, useState } from "react"
import { AlertTriangle, Pause, Play, RefreshCw } from "@/lib/icons"
import { usePolling } from "@/lib/desktop/use-polling"
import { controlWorkspaceJob, fetchWorkspaceJobRuns, type WorkspaceJob, type WorkspaceJobRun, type WorkspaceStatement } from "@/lib/rvbbit/dlt-operations"
import { SqlEditor } from "./sql-editor"
import { useWorkspaceActive } from "./workspace-active-context"

export function workspaceJobState(job: WorkspaceJob): string {
  if (job.running) return "running"
  if (job.run_requested) return "queued"
  if (job.last_status === "failed" || job.last_status === "interrupted") return job.last_status
  return job.enabled ? "scheduled" : job.interval_seconds ? "paused" : "manual"
}

export function jobInterval(seconds: number): string {
  if (!seconds) return "Manual"
  if (seconds % 86400 === 0) return `Every ${seconds / 86400}d`
  if (seconds % 3600 === 0) return `Every ${seconds / 3600}h`
  return `Every ${seconds / 60}m`
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—"
}

const controlClass = "inline-flex items-center gap-1.5 border border-chrome-border px-3 py-2 text-[11px] text-foreground hover:border-rvbbit-accent disabled:opacity-40 disabled:cursor-not-allowed"

export function WorkspaceJobDetail({ connectionId, job, onRefresh }: { connectionId: string; job: WorkspaceJob; onRefresh: () => Promise<void> }) {
  const active = useWorkspaceActive()
  const [runs, setRuns] = useState<WorkspaceJobRun[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const inFlight = useRef(false)
  const controlInFlight = useRef(false)
  const reload = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      setRuns(await fetchWorkspaceJobRuns(connectionId, job.id))
      setLoaded(true)
      setError(null)
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally { inFlight.current = false }
  }, [connectionId, job.id])
  usePolling(reload, 10_000, { enabled: active, resetKey: `${job.id}:${job.updated_at}:${job.last_status}` })

  const [controlError, setControlError] = useState<string | null>(null)
  async function control(operation: "pause" | "resume" | "run_now") {
    if (controlInFlight.current) return
    controlInFlight.current = true
    setBusy(operation)
    setControlError(null)
    try {
      await controlWorkspaceJob(job.id, operation)
      await onRefresh()
      await reload()
    } catch (error) {
      setControlError(error instanceof Error ? error.message : String(error))
    } finally { controlInFlight.current = false; setBusy(null) }
  }

  return <div className="min-w-0 p-5" data-testid="workspace-job-detail">
    <div className="border-b border-chrome-border pb-4">
      <div className="font-mono text-[9px] uppercase tracking-widest text-rvbbit-accent">SQL ETL · {workspaceJobState(job)}</div>
      <h2 className="mt-2 break-words text-[20px] font-semibold text-foreground">{job.name}</h2>
      <p className="mt-1 text-[11px] text-chrome-text/60">{job.owner_email} · revision {job.revision}</p>
      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-[11px]">
        <span><strong className="text-foreground">{jobInterval(job.interval_seconds)}</strong></span>
        <span>Next run <strong className="text-foreground">{job.next_run_at ? when(job.next_run_at) : job.enabled ? "Pending" : "Not scheduled"}</strong></span>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" className={controlClass} disabled={Boolean(busy || job.running || job.run_requested)} onClick={() => void control("run_now")}><Play className="h-3 w-3" />{busy === "run_now" ? "Queuing…" : job.run_requested ? "Queued" : "Run now"}</button>
        {job.enabled || job.run_requested || job.running
          ? <button type="button" className={controlClass} disabled={Boolean(busy)} onClick={() => void control("pause")}><Pause className="h-3 w-3" />{busy === "pause" ? "Pausing…" : "Pause"}</button>
          : job.interval_seconds > 0 ? <button type="button" className={controlClass} disabled={Boolean(busy)} onClick={() => void control("resume")}><Play className="h-3 w-3" />{busy === "resume" ? "Resuming…" : "Resume schedule"}</button> : null}
        <button type="button" className={controlClass} disabled={Boolean(busy)} onClick={() => void Promise.all([reload(), onRefresh()])}><RefreshCw className="h-3 w-3" />Refresh</button>
      </div>
      {job.running ? <p className="mt-2 text-[10px] text-chrome-text/60">Pausing cancels queued and future runs; the current batch finishes within its timeout.</p> : null}
    </div>
    {controlError || error ? <div role="alert" className="mt-4 flex gap-2 border-l-2 border-danger bg-danger/10 p-3 text-[11px] text-danger"><AlertTriangle className="h-4 w-4 shrink-0" />{controlError || error}</div> : null}
    {job.last_error ? <div className="mt-4 border-l-2 border-danger bg-danger/10 p-3 text-[11px] text-danger"><strong>{job.enabled ? "Previous run needs attention" : "Job paused after an unsuccessful run"}</strong><p className="mt-1 whitespace-pre-wrap break-words">{job.last_error}</p></div> : null}
    <details className="mt-4 border-b border-chrome-border pb-4" open>
      <summary className="cursor-pointer text-[11px] font-semibold text-foreground">Saved SQL · revision {job.revision}</summary>
      <div className="mt-3"><SqlEditor value={job.sql} onChange={() => {}} readOnly autoFocus={false} height="auto" wrap fontSize={11} /></div>
    </details>
    <h3 className="mt-5 text-[11px] font-semibold text-foreground">Recent runs</h3>
    {!loaded && !error ? <p className="mt-3 text-[11px] text-chrome-text/50">Loading run history…</p> : null}
    {loaded && !runs.length ? <p className="mt-3 text-[11px] text-chrome-text/60">No runs yet. {job.enabled ? `First run: ${when(job.next_run_at)}.` : "Use Run now for a manual execution."}</p> : null}
    {runs.map((run, index) => <details key={run.id} open={index === 0} className="mt-3 border border-chrome-border p-3">
      <summary className="cursor-pointer text-[11px] text-foreground"><strong>{run.status}</strong> · {when(run.started_at)} · {run.trigger} · v{run.revision}</summary>
      <p className="mt-2 text-[10px] text-chrome-text/55">{run.finished_at ? `Finished ${when(run.finished_at)}` : "In progress"}{run.result?.elapsed_ms != null ? ` · ${(run.result.elapsed_ms / 1000).toFixed(1)}s` : ""}</p>
      {run.error ? <p className="mt-3 whitespace-pre-wrap break-words text-[11px] text-danger">{run.error}</p> : null}
      {run.revision !== job.revision ? <details className="mt-3"><summary className="cursor-pointer text-[10px]">SQL executed in revision {run.revision}</summary><SqlEditor value={run.sql} onChange={() => {}} readOnly autoFocus={false} height="auto" wrap fontSize={11} /></details> : null}
      {run.result?.statements?.map((statement) => <StatementResult key={statement.statement} statement={statement} />)}
      {run.action_run_id ? <p className="mt-3 break-all font-mono text-[9px] text-chrome-text/45">Receipt {run.action_run_id}</p> : null}
    </details>)}
  </div>
}

function cell(value: unknown): string {
  return value == null ? "NULL" : typeof value === "object" ? JSON.stringify(value) : String(value)
}

function StatementResult({ statement }: { statement: WorkspaceStatement }) {
  return <details className="mt-3 border-t border-chrome-border pt-2" open={Boolean(statement.rows?.length)}>
    <summary className="cursor-pointer font-mono text-[10px] text-chrome-text/75">{statement.statement}. {statement.status}{statement.rows_affected != null ? ` · ${statement.rows_affected} rows` : ""}</summary>
    {statement.rows?.length ? <div className="mt-2 max-h-80 overflow-auto"><table className="w-full text-left font-mono text-[10px]"><thead className="sticky top-0 bg-chrome-bg"><tr>{statement.columns.map((column, i) => <th key={i} className="border-b border-chrome-border p-2 text-foreground">{column}</th>)}</tr></thead><tbody>{statement.rows.map((row, i) => <tr key={i}>{statement.columns.map((column, j) => <td key={j} className="max-w-96 break-words border-b border-chrome-border/40 p-2 align-top">{cell(row[column])}</td>)}</tr>)}</tbody></table></div> : null}
    {statement.truncated ? <p className="mt-1 text-[10px] text-chrome-text/50">Result preview truncated in the SQL receipt.</p> : null}
  </details>
}
