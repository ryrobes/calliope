"use client"

import { useCallback, useMemo, useRef, useState, type ReactNode } from "react"
import { Activity, AlertTriangle, Database, Layers, RefreshCw } from "@/lib/icons"
import { usePolling } from "@/lib/desktop/use-polling"
import { cn } from "@/lib/utils"
import {
  fetchDltOperations,
  type WorkspaceJob,
  type DltLineage,
  type DltRun,
  type DltTableRun,
} from "@/lib/rvbbit/dlt-operations"
import { WorkspaceJobDetail, workspaceJobState, jobInterval } from "./workspace-job-detail"
import { useWorkspaceActive } from "./workspace-active-context"

interface Props {
  activeConnectionId: string | null
  hasRvbbit: boolean
}

const STATUS_COLOR: Record<string, string> = {
  succeeded: "var(--success)",
  complete: "var(--success)",
  scheduled: "var(--success)",
  interrupted: "var(--danger)",
  paused: "var(--chrome-text)",
  manual: "var(--chrome-text)",
  skipped: "var(--viz-5, #65c6a7)",
  running: "var(--rvbbit-accent)",
  queued: "var(--warning)",
  partial: "var(--warning)",
  failed: "var(--danger)",
  cancelled: "var(--chrome-text)",
}

const RUN_GROUP_GAP_MS = 15_000

interface DltRunGroup {
  key: string
  trigger: string
  requestedAt: string
  runs: DltRun[]
}

function n(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function compact(value: unknown): string {
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n(value))
}

function duration(value: unknown): string {
  const ms = n(value)
  if (!ms) return "—"
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

function when(value: string | null | undefined): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function statusColor(status: string): string {
  return STATUS_COLOR[status] || "var(--chrome-text)"
}

function timestamp(value: string | null | undefined): number {
  const parsed = value ? Date.parse(value) : Number.NaN
  return Number.isFinite(parsed) ? parsed : 0
}

function runGroupStatus(runs: DltRun[]): string {
  const statuses = new Set(runs.map((run) => run.status))
  if (statuses.has("running")) return "running"
  if (statuses.has("queued")) return "queued"
  if (statuses.has("failed") && runs.some((run) => run.status === "succeeded")) return "partial"
  if (statuses.has("failed")) return "failed"
  if (statuses.has("partial")) return "partial"
  if (statuses.has("cancelled")) return "cancelled"
  if (statuses.has("skipped") && statuses.size === 1) return "skipped"
  return "succeeded"
}

function groupRuns(runs: DltRun[]): DltRunGroup[] {
  const sorted = [...runs].sort((a, b) => timestamp(b.requested_at) - timestamp(a.requested_at))
  const groups: DltRunGroup[] = []
  for (const run of sorted) {
    const current = groups.at(-1)
    const joinsCurrent = current
      && current.trigger === run.trigger
      && Math.abs(timestamp(current.requestedAt) - timestamp(run.requested_at)) <= RUN_GROUP_GAP_MS
    if (joinsCurrent) {
      current.runs.push(run)
    } else {
      groups.push({ key: run.run_id, trigger: run.trigger, requestedAt: run.requested_at, runs: [run] })
    }
  }
  return groups
}

export function DltOperationsWindow(props: Props) {
  return <Operations key={props.activeConnectionId ?? "none"} {...props} />
}

function Operations({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [workspaceJobs, setWorkspaceJobs] = useState<WorkspaceJob[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [mode, setMode] = useState<"all" | "sql" | "mirrors">("all")
  const inFlight = useRef(false)
  const [ready, setReady] = useState<boolean | null>(null)
  const [runs, setRuns] = useState<DltRun[]>([])
  const [tableRuns, setTableRuns] = useState<DltTableRun[]>([])
  const [lineage, setLineage] = useState<DltLineage[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    if (inFlight.current) return
    inFlight.current = true
    setRefreshing(true)
    try {
      const result = await fetchDltOperations(activeConnectionId)
      setReady((old) => result.error && old === true ? old : result.ready)
      // Keep usable results on a failed poll; the error remains visible.
      if (!result.error || result.runs.length) setRuns(result.runs)
      if (!result.error || result.tableRuns.length) setTableRuns(result.tableRuns)
      if (!result.error || result.lineage.length) setLineage(result.lineage)
      if (!result.error || result.workspaceJobs.length) setWorkspaceJobs(result.workspaceJobs)
      setError(result.error ?? null)
      if (!result.error) {
        setSelectedJobId((current) => current && result.workspaceJobs.some((job) => job.id === current) ? current : null)
        setSelectedRunId((current) => current && result.runs.some((run) => run.run_id === current) ? current : null)
      }
    } finally { inFlight.current = false; setRefreshing(false) }
  }, [activeConnectionId, hasRvbbit])

  usePolling(reload, 10_000, {
    enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive),
    resetKey: activeConnectionId,
  })

  const selectedJob = mode !== "mirrors" && !selectedRunId
    ? workspaceJobs.find((job) => job.id === selectedJobId) ?? workspaceJobs[0] ?? null : null
  const selected = mode !== "sql" && !selectedJob ? runs.find((run) => run.run_id === selectedRunId) ?? runs[0] ?? null : null
  const selectedTables = useMemo(
    () => tableRuns.filter((row) => row.run_id === selected?.run_id),
    [selected?.run_id, tableRuns],
  )
  const operationCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const row of tableRuns) {
      const operation = row.publication_action || (row.status === "failed" ? "failed" : row.load_mode || row.status)
      counts.set(operation, (counts.get(operation) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [tableRuns])
  const runGroups = useMemo(() => groupRuns(runs), [runs])
  const active = runs.filter((run) => run.status === "running" || run.status === "queued").length + workspaceJobs.filter((job) => job.running || job.run_requested).length
  const failures = runs.filter((run) => run.status === "failed" || run.status === "partial").length + workspaceJobs.filter((job) => ["failed", "interrupted"].includes(job.last_status ?? "")).length
  const medianDuration = useMemo(() => {
    const values = runs.map((run) => n(run.duration_ms)).filter(Boolean).sort((a, b) => a - b)
    return values.length ? values[Math.floor(values.length / 2)] : 0
  }, [runs])

  if (!hasRvbbit || !activeConnectionId) return <Empty copy="DLT Operations needs an active RVBBIT connection." />
  if (ready === false) return <Empty copy={error || "No DLT mirrors or SQL ETL job support is installed in this database."} />

  return (
    <div className="flex h-full min-h-0 flex-col text-chrome-text">
      <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
        <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10">
          <Activity className="h-4 w-4 text-rvbbit-accent" />
        </div>
        <div className="min-w-0">
          <h1 className="text-[13px] font-semibold tracking-tight text-foreground">DLT & SQL Operations</h1>
          <p className="text-[10px] text-chrome-text/55">SQL ETL schedules, mirror publications, and execution receipts</p>
        </div>
        <div className="ml-auto flex items-center gap-3 font-mono text-[9px] uppercase tracking-[0.12em] text-chrome-text/50">
          <span><i className={cn("mr-1 inline-block h-1.5 w-1.5 rounded-full", active ? "animate-pulse bg-rvbbit-accent" : "bg-success")} />{active ? `${active} active` : "quiet"}</span>
          <button type="button" onClick={() => void reload()} className="inline-flex items-center gap-1.5 border border-chrome-border px-2 py-1.5 text-foreground hover:border-rvbbit-accent/50" disabled={refreshing}>
            <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} /> Refresh
          </button>
        </div>
      </header>

      {error ? <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger"><AlertTriangle className="h-3.5 w-3.5" />{error}</div> : null}

      <div className="flex items-center gap-1 border-b border-chrome-border px-4 py-2" aria-label="Job type">
        {([['all', 'All'], ['sql', `SQL ETL · ${workspaceJobs.length}`], ['mirrors', 'DLT mirrors']] as const).map(([value, label]) =>
          <button key={value} type="button" aria-pressed={mode === value} onClick={() => { setMode(value); setSelectedJobId(null); setSelectedRunId(null) }} className={cn("px-3 py-1.5 text-[11px]", mode === value ? "bg-rvbbit-accent/15 text-rvbbit-accent" : "text-chrome-text/60 hover:text-foreground")}>{label}</button>)}
      </div>
      <div className="grid grid-cols-4 border-b border-chrome-border">
        <Readout label="configured jobs" value={compact(workspaceJobs.length + new Set(lineage.map((row) => row.job_name)).size)} note={`${workspaceJobs.length} SQL · ${lineage.length} mirror tables`} />
        <Readout label="rows published" value={compact(runs.reduce((sum, run) => sum + n(run.rows_loaded), 0))} note="mirror runs" />
        <Readout label="median duration" value={duration(medianDuration)} note={`${runs.length} mirror runs`} />
        <Readout label="needs attention" value={compact(failures)} note={failures ? "failed or partial" : "all clear"} tone={failures ? "danger" : "ok"} />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(380px,0.95fr)_minmax(500px,1.45fr)] overflow-hidden">
        <section className="min-h-0 overflow-auto border-r border-chrome-border">
          <div className="sticky top-0 z-10 border-b border-chrome-border bg-chrome-bg/40 px-4 py-3 backdrop-blur">
            <div className="mb-2 flex items-center justify-between"><Label>Jobs & recent runs</Label><span className="font-mono text-[9px] text-chrome-text/45">newest first</span></div>
            {mode !== "sql" && operationCounts.length ? <OperationBand counts={operationCounts} /> : null}
          </div>
          <div>
            {mode !== "mirrors" ? workspaceJobs.map((job) => <button key={job.id} type="button" onClick={() => { setSelectedJobId(job.id); setSelectedRunId(null) }} className={cn("w-full border-b border-chrome-border border-l-2 px-4 py-4 text-left hover:bg-foreground/[0.025]", selectedJob?.id === job.id ? "border-l-rvbbit-accent bg-rvbbit-accent/10" : "border-l-transparent")}>
              <div className="flex items-center gap-2"><span className="font-mono text-[9px] text-rvbbit-accent">SQL ETL</span><Badge status={workspaceJobState(job)} /></div>
              <strong className="mt-2 block break-words text-[12px] text-foreground">{job.name}</strong>
              <span className="mt-1 block text-[10px] text-chrome-text/60">{jobInterval(job.interval_seconds)} · {job.enabled ? `next ${when(job.next_run_at)}` : job.run_requested ? "Queued" : "Not scheduled"}</span>
              <span className="mt-1 block truncate text-[9px] text-chrome-text/45">{job.owner_email} · v{job.revision}</span>
            </button>) : null}
            {mode !== "sql" && runGroups.map((group, index) => (
              <RunGroup key={group.key} group={group} index={index} selectedRunId={selected?.run_id ?? null} onSelect={(id) => { setSelectedRunId(id); setSelectedJobId(null) }} />
            ))}
            {!(mode !== "mirrors" && workspaceJobs.length) && !(mode !== "sql" && runs.length) ? <div className="p-8 text-center text-[11px] text-chrome-text/45">{ready === null ? "Loading operations…" : mode === "sql" ? "No SQL ETL jobs have been saved." : "No jobs or runs to show."}</div> : null}
          </div>
        </section>

        <section className="min-h-0 overflow-auto">
          {selectedJob ? <WorkspaceJobDetail key={selectedJob.id} connectionId={activeConnectionId} job={selectedJob} onRefresh={reload} /> : selected ? <RunDetail run={selected} tables={selectedTables} /> : <Empty copy="Select a SQL job or mirror run to inspect." />}
        </section>
      </div>
    </div>
  )
}

function RunGroup({ group, index, selectedRunId, onSelect }: { group: DltRunGroup; index: number; selectedRunId: string | null; onSelect: (runId: string) => void }) {
  const status = runGroupStatus(group.runs)
  const selected = group.runs.some((run) => run.run_id === selectedRunId)
  const rows = group.runs.reduce((sum, run) => sum + n(run.rows_loaded), 0)
  const starts = group.runs.map((run) => timestamp(run.started_at || run.requested_at)).filter(Boolean)
  const finishes = group.runs.map((run) => timestamp(run.finished_at || run.started_at || run.requested_at)).filter(Boolean)
  const elapsed = starts.length && finishes.length ? Math.max(...finishes) - Math.min(...starts) : 0
  const completed = group.runs.filter((run) => run.status === "succeeded" || run.status === "skipped").length
  return <section className={cn("relative border-b border-chrome-border/70", index % 2 ? "bg-foreground/[0.012]" : "bg-transparent", selected && "bg-rvbbit-accent/[0.025]")}>
    <i className="absolute inset-y-0 left-0 w-[3px]" style={{ background: statusColor(status), opacity: selected ? 0.9 : 0.5 }} />
    <button type="button" onClick={() => onSelect(group.runs[0].run_id)} className="group/cycle grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 border-b border-chrome-border/45 px-5 py-3 text-left hover:bg-foreground/[0.025] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-rvbbit-accent/60">
      <span className="min-w-0">
        <span className="flex items-center gap-2"><Label>Mirror cycle</Label><span className="font-mono text-[8px] text-chrome-text/35">#{String(index + 1).padStart(2, "0")}</span></span>
        <strong className="mt-1.5 block font-mono text-[11px] font-medium tabular-nums text-foreground">{when(group.requestedAt)}</strong>
        <small className="mt-1 block text-[9px] text-chrome-text/48">{group.trigger} dispatch · {group.runs.length} {group.runs.length === 1 ? "job" : "jobs"}</small>
      </span>
      <span className="text-right"><Badge status={status} /><small className="mt-2 block font-mono text-[8px] tabular-nums text-chrome-text/45">{duration(elapsed)} window · {compact(rows)} rows</small></span>
      <span className="col-span-2 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        <span className="flex h-1.5 gap-px overflow-hidden bg-foreground/[0.035]">{group.runs.map((run) => <i key={run.run_id} className="min-w-[5px] flex-1 transition-opacity group-hover/cycle:opacity-100" title={`${run.job_name} · ${run.status} · ${duration(run.duration_ms)}`} style={{ background: statusColor(run.status), opacity: run.run_id === selectedRunId ? 1 : 0.58 }} />)}</span>
        <small className="font-mono text-[8px] tabular-nums text-chrome-text/42">{completed}/{group.runs.length} complete</small>
      </span>
    </button>
    <div className="ml-4 divide-y divide-chrome-border/35 border-l border-chrome-border/55">
      {group.runs.map((run) => <RunRow key={run.run_id} run={run} selected={run.run_id === selectedRunId} onSelect={() => onSelect(run.run_id)} />)}
    </div>
  </section>
}

function Readout({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "danger" | "ok" }) {
  return <div className="border-r border-chrome-border px-4 py-3 last:border-r-0"><Label>{label}</Label><div className={cn("mt-1 font-mono text-[21px] tabular-nums text-foreground", tone === "danger" && "text-danger", tone === "ok" && "text-success")}>{value}</div><div className="mt-0.5 text-[9px] text-chrome-text/45">{note}</div></div>
}

function Label({ children }: { children: ReactNode }) {
  return <div className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/50">{children}</div>
}

function OperationBand({ counts }: { counts: Array<[string, number]> }) {
  const total = counts.reduce((sum, [, value]) => sum + value, 0)
  return <div><div className="flex h-2.5 overflow-hidden bg-foreground/[0.04]">{counts.map(([name, value]) => <span key={name} title={`${name}: ${value}`} style={{ width: `${(value / Math.max(1, total)) * 100}%`, background: operationColor(name) }} />)}</div><div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">{counts.slice(0, 6).map(([name, value]) => <span key={name} className="inline-flex items-center gap-1.5 font-mono text-[8px] text-chrome-text/60"><i className="h-1.5 w-1.5 rounded-full" style={{ background: operationColor(name) }} />{name} {value}</span>)}</div></div>
}

function RunRow({ run, selected, onSelect }: { run: DltRun; selected: boolean; onSelect: () => void }) {
  const total = n(run.tables_succeeded) + n(run.tables_failed)
  return <button type="button" onClick={onSelect} className={cn("grid w-full grid-cols-[5px_minmax(0,1fr)_auto] gap-3 px-4 py-3 text-left hover:bg-foreground/[0.025] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-rvbbit-accent/60", selected && "bg-rvbbit-accent/[0.08]")}>
    <i className="h-full min-h-10 rounded-full" style={{ background: statusColor(run.status), opacity: selected ? 1 : 0.58 }} />
    <span className="min-w-0"><span className="flex items-center gap-2"><strong className="truncate font-mono text-[10px] font-medium text-foreground">{run.job_name}</strong><Badge status={run.status} /></span><small className="mt-1 block truncate text-[9px] text-chrome-text/50">{run.connection_label || run.connection_name} · {run.source_schema} → {run.destination_schema}</small><span className="mt-2 block h-1 overflow-hidden bg-foreground/[0.05]"><i className="block h-full" style={{ width: `${Math.max(3, Math.min(100, total ? (n(run.tables_succeeded) / total) * 100 : run.status === "running" ? 35 : 100))}%`, background: statusColor(run.status) }} /></span></span>
    <span className="text-right"><strong className="block font-mono text-[10px] font-medium tabular-nums text-foreground">{duration(run.duration_ms)}</strong><small className="mt-1 block font-mono text-[8px] text-chrome-text/45">{compact(run.rows_loaded)} rows</small><small className="mt-1 block font-mono text-[8px] text-chrome-text/45">{total} tables</small></span>
  </button>
}

function RunDetail({ run, tables }: { run: DltRun; tables: DltTableRun[] }) {
  const maxDuration = Math.max(1, ...tables.map((row) => n(row.duration_ms)))
  const counts = new Map<string, number>()
  tables.forEach((row) => counts.set(row.publication_action || row.status, (counts.get(row.publication_action || row.status) ?? 0) + 1))
  return <div className="p-5">
    <div className="flex items-start gap-4 border-b border-chrome-border pb-4"><div className="grid h-10 w-10 shrink-0 place-items-center border border-chrome-border bg-secondary-background"><Database className="h-4 w-4 text-rvbbit-accent" /></div><div className="min-w-0"><Label>{run.dialect} · {run.trigger}</Label><h2 className="mt-1 truncate text-[18px] font-semibold tracking-tight text-foreground">{run.job_name}</h2><p className="mt-1 text-[10px] text-chrome-text/55">{run.connection_label || run.connection_name} · {when(run.started_at || run.requested_at)}</p></div><div className="ml-auto text-right"><Badge status={run.status} /><div className="mt-2 font-mono text-[10px] text-chrome-text/60">{duration(run.duration_ms)}</div></div></div>

    <div className="grid grid-cols-4 border-b border-chrome-border py-4"><Mini label="tables" value={`${run.tables_succeeded}/${run.tables_succeeded + run.tables_failed}`} /><Mini label="rows" value={compact(run.rows_loaded)} /><Mini label="attempt" value={String(run.attempt)} /><Mini label="operations" value={String(tables.length)} /></div>

    {run.error_message || run.error_code ? <div className="mt-4 border-l-2 border-danger bg-danger/8 px-3 py-2 text-[10px] leading-relaxed text-danger"><strong className="font-mono">{run.error_code || "DLT_ERROR"}</strong><br />{run.error_message}</div> : null}

    <div className="mt-5 flex items-end justify-between"><div><Label>Table publication trace</Label><p className="mt-1 text-[10px] text-chrome-text/50">Duration bars share a scale within this run. Mutation counts come from the atomic publication receipt.</p></div><div className="flex gap-2">{[...counts].map(([name, value]) => <span key={name} className="font-mono text-[8px] text-chrome-text/55"><i className="mr-1 inline-block h-1.5 w-1.5 rounded-full" style={{ background: operationColor(name) }} />{name} {value}</span>)}</div></div>

    <div className="mt-3 divide-y divide-chrome-border/40 border-y border-chrome-border/70">
      {tables.map((row) => <TableTrace key={`${row.run_id}:${row.source_table}`} row={row} maxDuration={maxDuration} />)}
      {!tables.length ? <div className="p-7 text-center text-[10px] text-chrome-text/45">No table receipts were attached to this run.</div> : null}
    </div>
  </div>
}

function TableTrace({ row, maxDuration }: { row: DltTableRun; maxDuration: number }) {
  const operation = row.publication_action || row.status
  const mutations = n(row.rows_inserted) + n(row.rows_updated) + n(row.rows_deleted)
  return <div className="grid grid-cols-[minmax(170px,0.8fr)_minmax(190px,1fr)_110px] items-center gap-4 py-3">
    <div className="min-w-0"><strong className="block truncate font-mono text-[10px] font-medium text-foreground">{row.destination_relation}</strong><span className="mt-1 block truncate text-[9px] text-chrome-text/45">source · {row.source_table} · {row.load_mode}</span></div>
    <div><div className="relative h-5 bg-foreground/[0.035]"><span className="absolute inset-y-0 left-0 min-w-1" style={{ width: `${Math.max(1.5, (n(row.duration_ms) / maxDuration) * 100)}%`, background: `color-mix(in oklch, ${operationColor(operation)} 68%, transparent)` }} /><span className="absolute inset-0 flex items-center px-2 font-mono text-[8px] text-foreground/75">{duration(row.duration_ms)}</span></div><div className="mt-1 flex gap-2 font-mono text-[7.5px] text-chrome-text/45"><span>+{compact(row.rows_inserted)}</span><span>~{compact(row.rows_updated)}</span><span>−{compact(row.rows_deleted)}</span></div></div>
    <div className="text-right"><span className="inline-flex border px-1.5 py-1 font-mono text-[8px] uppercase tracking-wide" style={{ borderColor: `color-mix(in oklch, ${operationColor(operation)} 45%, var(--chrome-border))`, color: operationColor(operation) }}>{operation}</span><div className="mt-1 font-mono text-[8px] text-chrome-text/45">{compact(row.source_rows ?? row.rows_loaded)} source · {compact(mutations)} changed</div></div>
  </div>
}

function Mini({ label, value }: { label: string; value: string }) { return <div><Label>{label}</Label><div className="mt-1 font-mono text-[14px] text-foreground">{value}</div></div> }
function Badge({ status }: { status: string }) { return <span className="inline-flex items-center gap-1.5 border px-1.5 py-1 font-mono text-[8px] uppercase tracking-wide" style={{ color: statusColor(status), borderColor: `color-mix(in oklch, ${statusColor(status)} 42%, var(--chrome-border))` }}><i className="h-1.5 w-1.5 rounded-full" style={{ background: statusColor(status) }} />{status}</span> }
function operationColor(operation: string): string { return ({ noop: "var(--viz-5, #65c6a7)", merge: "var(--rvbbit-accent)", replace: "var(--viz-3, #b68cff)", incremental_upsert: "var(--viz-2, #6ba7ff)", failed: "var(--danger)", skipped: "var(--viz-5, #65c6a7)" } as Record<string, string>)[operation] || "var(--warning)" }
function Empty({ copy }: { copy: string }) { return <div className="grid h-full place-items-center p-8 text-center"><div><Layers className="mx-auto mb-3 h-8 w-8 text-chrome-text/25" /><p className="text-[11px] text-chrome-text/55">{copy}</p></div></div> }
