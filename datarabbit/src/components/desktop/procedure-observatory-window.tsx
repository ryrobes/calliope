"use client"

import { useCallback, useMemo, useState, type ReactNode } from "react"
import { Activity, AlertTriangle, RefreshCw, Shield } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { useWorkspaceActive } from "./workspace-active-context"
import {
  fetchProcedureObservatory,
  type ProcedureDefinition,
  type ProcedureEvent,
  type ProcedureRun,
} from "@/lib/rvbbit/procedures"

interface Props { activeConnectionId: string | null; hasRvbbit: boolean }

const tones: Record<string, string> = {
  complete: "var(--success)", validated: "var(--success)", scheduled: "var(--success)",
  running: "var(--rvbbit-accent)", supervised: "var(--rvbbit-accent)",
  rehearsed: "var(--warning)", planned: "var(--warning)", parked: "var(--warning)",
  failed: "var(--danger)", cancelled: "var(--chrome-text)", draft: "var(--chrome-text)",
}

function when(value: string | null | undefined) {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function Badge({ value }: { value: string | null | undefined }) {
  const label = value || "unknown"
  return <span className="inline-flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.12em]" style={{ color: tones[label] || "var(--chrome-text)" }}><i className="h-1.5 w-1.5 rounded-full bg-current" />{label}</span>
}

function Label({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">{children}</span>
}

interface ScreenshotEvidence {
  url: string
  label: string
  detail: string
  width: number | null
  height: number | null
  sequence: number
  phase: number
  priority: number
}

interface RecordingEvidence {
  id: string
  state: "starting" | "recording" | "ready" | "failed"
  durationMs: number | null
  width: number | null
  height: number | null
  error: string | null
  sequence: number
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function screenshot(value: unknown, workbenchId: string | null): Pick<ScreenshotEvidence, "url" | "width" | "height"> | null {
  const item = object(value)
  const url = typeof item?.url === "string" ? item.url : ""
  const prefix = workbenchId ? `/api/calliope/workbenches/${encodeURIComponent(workbenchId)}/screenshots/` : ""
  if (!prefix || !url.startsWith(prefix)) return null
  return { url, width: number(item?.width), height: number(item?.height) }
}

function visualEvidence(events: ProcedureEvent[], workbenchId: string | null) {
  const candidates: ScreenshotEvidence[] = []
  const recordings = new Map<string, RecordingEvidence>()
  const proposalSteps = new Map<string, number>()

  for (const event of events) {
    if (event.event_type !== "guided.action.proposed") continue
    const payload = object(event.payload) || {}
    const proposalId = typeof payload.proposal_id === "string" ? payload.proposal_id : ""
    const stepIndex = number(payload.step_index)
    if (proposalId && stepIndex !== null) proposalSteps.set(proposalId, stepIndex)
  }

  const add = (value: unknown, event: ProcedureEvent, label: string, detail: string, phase: number, priority: number) => {
    const asset = screenshot(value, workbenchId)
    if (asset) candidates.push({ ...asset, label, detail, sequence: event.sequence, phase, priority })
  }

  for (const event of events) {
    const payload = object(event.payload) || {}
    const proposalId = typeof payload.proposal_id === "string" ? payload.proposal_id : ""
    const stepIndex = number(payload.step_index) ?? proposalSteps.get(proposalId) ?? null
    const step = stepIndex === null ? "step" : `step ${stepIndex + 1}`
    if (event.event_type === "guided.observed") {
      add(payload.screenshot, event, `Before ${step}`, "Observed desktop", stepIndex === null ? event.sequence * 3 : stepIndex * 3, 100)
    } else if (event.event_type === "guided.action.executed") {
      const receipt = object(payload.action_receipt)
      const receiptPayload = object(receipt?.payload)
      add(receiptPayload?.screenshot, event, `${step} result`, "Action receipt", stepIndex === null ? event.sequence * 3 + 1 : stepIndex * 3 + 1, 90)
      add(payload.after_screenshot, event, `After ${step}`, "Verification frame", stepIndex === null ? event.sequence * 3 + 2 : stepIndex * 3 + 2, 100)
    } else if (event.event_type === "action.succeeded") {
      const kind = typeof payload.kind === "string" ? payload.kind : "action"
      add(payload.screenshot, event, kind === "screenshot" ? "Desktop capture" : `${kind} result`, `Journal event ${event.sequence}`, event.sequence * 3, 40)
    }

    if (event.event_type.startsWith("recording.")) {
      const id = typeof payload.recording_id === "string" ? payload.recording_id : ""
      if (!id) continue
      const state = event.event_type === "recording.ready" ? "ready"
        : event.event_type === "recording.failed" ? "failed"
          : event.event_type === "recording.started" ? "recording" : "starting"
      recordings.set(id, {
        id,
        state,
        durationMs: number(payload.duration_ms),
        width: number(payload.width),
        height: number(payload.height),
        error: typeof payload.error === "string" ? payload.error : null,
        sequence: event.sequence,
      })
    }
  }

  const screenshots = [...candidates.reduce((items, item) => {
    const current = items.get(item.url)
    if (!current || item.priority > current.priority) items.set(item.url, item)
    return items
  }, new Map<string, ScreenshotEvidence>()).values()].sort((a, b) => a.phase - b.phase || a.sequence - b.sequence)

  return { screenshots, recordings: [...recordings.values()].sort((a, b) => a.sequence - b.sequence) }
}

function duration(value: number | null) {
  if (value === null) return "duration unavailable"
  if (value < 1_000) return `${value} ms`
  const seconds = Math.round(value / 100) / 10
  return `${seconds} sec`
}

function adaptationEvidence(events: ProcedureEvent[]) {
  const recoveries = events.filter((event) => event.event_type === "guided.recovery.noted").map((event) => {
    const payload = object(event.payload) || {}
    return {
      id: event.id,
      outcome: typeof payload.outcome === "string" ? payload.outcome : "noted",
      obstacle: typeof payload.obstacle === "string" ? payload.obstacle : "Observed friction",
      strategy: typeof payload.strategy === "string" ? payload.strategy : "",
      attempt: number(payload.attempt),
      promoted: payload.promoted_to_rule === true,
    }
  })
  const trials = events.filter((event) => event.event_type === "proving.receipt.captured").map((event) => {
    const payload = object(event.payload) || {}
    const audit = Array.isArray(payload.audit) ? payload.audit : []
    return {
      id: event.id,
      scenario: typeof payload.scenario_label === "string" ? payload.scenario_label : String(payload.scenario || "Proving-ground trial"),
      state: typeof payload.state === "string" ? payload.state : "captured",
      uploadAttempts: number(payload.upload_attempts),
      sourceFormat: typeof payload.source_format === "string" ? payload.source_format : null,
      uploadFilename: typeof payload.upload_filename === "string" ? payload.upload_filename : null,
      submissionId: typeof payload.submission_id === "string" ? payload.submission_id : null,
      sha256: typeof payload.upload_sha256 === "string" ? payload.upload_sha256 : null,
      auditEvents: audit.length,
    }
  })
  return { recoveries, trials }
}

export function ProcedureObservatoryWindow({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [ready, setReady] = useState<boolean | null>(null)
  const [definitions, setDefinitions] = useState<ProcedureDefinition[]>([])
  const [runs, setRuns] = useState<ProcedureRun[]>([])
  const [events, setEvents] = useState<ProcedureEvent[]>([])
  const [selectedProcedure, setSelectedProcedure] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    setRefreshing(true)
    const result = await fetchProcedureObservatory(activeConnectionId)
    setRefreshing(false)
    setReady(result.ready)
    setDefinitions(result.definitions)
    setRuns(result.runs)
    setEvents(result.events)
    setError(result.error || null)
    setSelectedProcedure((current) => current && result.definitions.some((item) => item.procedure_id === current) ? current : result.definitions[0]?.procedure_id || null)
    setSelectedRun((current) => current && result.runs.some((item) => item.run_id === current) ? current : null)
  }, [activeConnectionId, hasRvbbit])

  usePolling(reload, 5_000, { enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive), resetKey: activeConnectionId })

  const procedure = definitions.find((item) => item.procedure_id === selectedProcedure) || definitions[0] || null
  const procedureRuns = useMemo(() => runs.filter((run) => run.procedure_id === procedure?.procedure_id), [runs, procedure?.procedure_id])
  const run = procedureRuns.find((item) => item.run_id === selectedRun) || procedureRuns[0] || null
  const runEvents = useMemo(() => events.filter((event) => {
    if (event.workbench_id !== run?.workbench_id) return false
    if (run.event_sequence_start != null && event.sequence < run.event_sequence_start) return false
    if (run.event_sequence_end != null && event.sequence > run.event_sequence_end) return false
    return true
  }).sort((a, b) => a.sequence - b.sequence), [events, run?.workbench_id, run?.event_sequence_start, run?.event_sequence_end])
  const active = runs.filter((item) => ["planned", "running", "parked"].includes(item.state)).length
  const parked = runs.filter((item) => item.state === "parked").length
  const complete = runs.filter((item) => item.state === "complete").length

  if (!hasRvbbit || !activeConnectionId) return <Empty>Procedure Observatory needs an active RVBBIT connection.</Empty>
  if (ready === false) return <Empty>The Procedure control plane is not installed in this database.</Empty>

  return <div className="flex h-full min-h-0 flex-col text-chrome-text">
    <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
      <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10"><Activity className="h-4 w-4 text-rvbbit-accent" /></div>
      <div><h1 className="text-[13px] font-semibold text-foreground">Procedure Observatory</h1><p className="text-[10px] text-chrome-text/55">Advanced control-plane trace · definitions, strategies, runs, parks, and receipts</p></div>
      <div className="ml-auto flex items-center gap-3"><Badge value={active ? "running" : "complete"} /><button type="button" onClick={() => void reload()} disabled={refreshing} className="inline-flex items-center gap-1.5 border border-chrome-border px-2 py-1.5 text-[9px] text-foreground hover:border-rvbbit-accent/50"><RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />Refresh</button></div>
    </header>
    {error ? <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger"><AlertTriangle className="h-3.5 w-3.5" />{error}</div> : null}
    <div className="grid grid-cols-4 border-b border-chrome-border">
      <Readout label="procedures" value={definitions.length} note={`${definitions.filter((item) => item.approved_version).length} approved`} />
      <Readout label="active runs" value={active} note="planned · running · parked" />
      <Readout label="parked" value={parked} note={parked ? "human decision needed" : "no exceptions"} danger={parked > 0} />
      <Readout label="completed" value={complete} note={`${runs.length} total receipts`} />
    </div>
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,.75fr)_minmax(300px,.9fr)_minmax(420px,1.35fr)] overflow-hidden">
      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Procedure catalog" note="semantic contract + strategy" />
        {definitions.map((item) => <button key={item.procedure_id} type="button" onClick={() => { setSelectedProcedure(item.procedure_id); setSelectedRun(null) }} className={cn("w-full border-b border-chrome-border/60 px-4 py-3 text-left hover:bg-foreground/[0.025]", procedure?.procedure_id === item.procedure_id && "bg-rvbbit-accent/[0.04]")}>
          <div className="flex items-center justify-between gap-2"><Badge value={item.approved_version ? item.trust_level : "draft"} /><span className="font-mono text-[8px] text-chrome-text/40">v{item.latest_version}</span></div>
          <strong className="mt-2 block text-[11px] text-foreground">{item.title}</strong>
          <p className="mt-1 line-clamp-2 text-[9px] leading-relaxed text-chrome-text/55">{item.synopsis}</p>
          <div className="mt-2 flex gap-3 font-mono text-[8px] text-chrome-text/40"><span>{item.strategy_kind || "no strategy"}</span><span>{item.total_runs} runs</span><span>{item.clean_runs} clean</span></div>
        </button>)}
        {!definitions.length ? <Empty>No Procedures have been distilled yet.</Empty> : null}
      </section>
      <section className="min-h-0 overflow-auto border-r border-chrome-border">
        <PaneTitle label="Run ledger" note={procedure?.capability_name || "select a Procedure"} />
        {procedureRuns.map((item) => <button key={item.run_id} type="button" onClick={() => setSelectedRun(item.run_id)} className={cn("w-full border-b border-chrome-border/60 px-4 py-3 text-left hover:bg-foreground/[0.025]", run?.run_id === item.run_id && "bg-rvbbit-accent/[0.04]")}>
          <div className="flex items-center justify-between"><Badge value={item.state} /><span className="font-mono text-[8px] text-chrome-text/40">{item.trigger_kind}</span></div>
          <div className="mt-2 font-mono text-[9px] text-foreground">{item.run_id.slice(0, 13)}</div>
          <div className="mt-1 flex justify-between text-[8px] text-chrome-text/45"><span>step {item.current_step}/{item.steps.length}</span><span>{when(item.created_at)}</span></div>
        </button>)}
        {procedure && !procedureRuns.length ? <Empty>No execution receipts for this Procedure.</Empty> : null}
      </section>
      <section className="min-h-0 overflow-auto">
        {run ? <RunDetail run={run} events={runEvents} /> : procedure ? <ProcedureDetail item={procedure} /> : <Empty>Select a Procedure to inspect it.</Empty>}
      </section>
    </div>
  </div>
}

function PaneTitle({ label, note }: { label: string; note: string }) {
  return <div className="sticky top-0 z-10 border-b border-chrome-border bg-chrome-bg/80 px-4 py-3 backdrop-blur"><Label>{label}</Label><div className="mt-1 truncate font-mono text-[8px] text-chrome-text/38">{note}</div></div>
}

function Readout({ label, value, note, danger }: { label: string; value: number; note: string; danger?: boolean }) {
  return <div className="border-r border-chrome-border px-4 py-3 last:border-r-0"><Label>{label}</Label><div className={cn("mt-1 font-mono text-[21px] tabular-nums text-foreground", danger && "text-danger")}>{value}</div><div className="text-[8px] text-chrome-text/42">{note}</div></div>
}

function ProcedureDetail({ item }: { item: ProcedureDefinition }) {
  const validation = item.validation || {}
  return <div className="p-5">
    <div className="flex items-start gap-3"><Shield className="mt-0.5 h-5 w-5 text-rvbbit-accent" /><div><Label>Selected Procedure</Label><h2 className="mt-1 text-[16px] font-semibold text-foreground">{item.title}</h2><p className="mt-2 max-w-2xl text-[10px] leading-relaxed text-chrome-text/60">{item.synopsis}</p></div></div>
    <div className="mt-5 grid grid-cols-2 gap-px border border-chrome-border bg-chrome-border"><Fact label="Capability" value={item.capability_name} /><Fact label="Owner" value={item.owner_email} /><Fact label="Trust" value={item.trust_level || "drafted"} /><Fact label="Strategy" value={`${item.strategy_kind || "—"} · ${item.strategy_status || "—"} · r${item.strategy_revision || "—"}`} /><Fact label="Workflow source" value={item.source_workflow_id ? `${item.source_workflow_id.slice(0, 13)} · r${item.source_workflow_revision}` : item.procedure_id.startsWith("__") ? "Workbench events · not promoted" : "—"} /><Fact label="Validation" value={`${String(validation.successful_replays || 0)} fresh replays · ${String(validation.status || "unvalidated")}`} /></div>
  </div>
}

function RunDetail({ run, events }: { run: ProcedureRun; events: ProcedureEvent[] }) {
  const evidence = visualEvidence(events, run.workbench_id)
  const adaptations = adaptationEvidence(events)
  return <div>
    <div className="border-b border-chrome-border p-5"><div className="flex items-center justify-between"><div><Label>Execution receipt</Label><h2 className="mt-1 font-mono text-[13px] text-foreground">{run.run_id}</h2></div><Badge value={run.state} /></div><div className="mt-3 grid grid-cols-3 gap-px border border-chrome-border bg-chrome-border"><Fact label="Pinned versions" value={run.procedure_id.startsWith("__") ? "unpromoted · guided trace" : `procedure v${run.playbook_version} · strategy r${run.strategy_revision}`} /><Fact label="Trust / trigger" value={`${run.trust_level} · ${run.trigger_kind}`} /><Fact label="Workbench" value={run.workbench_id?.slice(0, 13) || "not launched"} /></div>{run.error ? <div className="mt-3 border border-danger/30 bg-danger/10 p-3 text-[9px] text-danger">{run.error}</div> : null}</div>
    <section className="border-b border-chrome-border p-5"><Label>Compiled steps</Label><div className="mt-3 space-y-2">{run.steps.map((step, index) => <div key={String(step.id || index)} className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-2 border border-chrome-border/70 bg-foreground/[0.015] p-2.5"><span className="grid h-5 w-5 place-items-center rounded-full border border-chrome-border font-mono text-[8px]">{index + 1}</span><div><strong className="text-[9px] text-foreground">{String(step.label || step.id || `Step ${index + 1}`)}</strong><div className="mt-0.5 font-mono text-[8px] text-chrome-text/40">{String(step.lane || "computer")} · {String(step.risk || "read")}</div></div><Badge value={String(step.status || "pending")} /></div>)}</div></section>
    <Adaptations recoveries={adaptations.recoveries} trials={adaptations.trials} />
    <Evidence run={run} screenshots={evidence.screenshots} recordings={evidence.recordings} />
    <section className="p-5"><div className="flex items-center justify-between"><Label>Workbench journal</Label><span className="font-mono text-[8px] text-chrome-text/40">{events.length} events</span></div><div className="mt-3 border-l border-chrome-border pl-3">{events.map((event) => <div key={event.id} className="relative border-b border-chrome-border/45 py-2.5 pl-2 before:absolute before:-left-[17px] before:top-4 before:h-1.5 before:w-1.5 before:rounded-full before:bg-rvbbit-accent"><div className="flex justify-between gap-3"><span className="font-mono text-[8px] text-foreground">{event.sequence}. {event.event_type}</span><span className="font-mono text-[7px] text-chrome-text/35">{when(event.created_at)}</span></div><p className="mt-1 line-clamp-2 font-mono text-[7px] leading-relaxed text-chrome-text/42">{JSON.stringify(event.payload)}</p></div>)}</div>{!events.length ? <div className="mt-3 text-[9px] text-chrome-text/42">No workbench events yet. A planned run has not launched.</div> : null}</section>
  </div>
}

function Adaptations({ recoveries, trials }: { recoveries: ReturnType<typeof adaptationEvidence>["recoveries"]; trials: ReturnType<typeof adaptationEvidence>["trials"] }) {
  if (!recoveries.length && !trials.length) return null
  return <section className="border-b border-chrome-border p-5">
    <div className="flex items-center justify-between"><Label>Exceptions &amp; adaptations</Label><span className="font-mono text-[8px] text-chrome-text/40">candidate notes, never silent rules</span></div>
    <div className="mt-3 space-y-2">
      {recoveries.map((item) => <article key={item.id} className="border border-warning/25 bg-warning/[0.04] p-3">
        <div className="flex items-center justify-between"><Badge value={item.outcome} /><span className="font-mono text-[7px] text-chrome-text/40">attempt {item.attempt || "—"}/3 · {item.promoted ? "promoted" : "candidate"}</span></div>
        <strong className="mt-2 block text-[9px] text-foreground">{item.obstacle}</strong><p className="mt-1 text-[8px] leading-relaxed text-chrome-text/50">{item.strategy}</p>
      </article>)}
      {trials.map((item) => <article key={item.id} className="border border-rvbbit-accent/25 bg-rvbbit-accent/[0.035] p-3">
        <div className="flex items-center justify-between"><Badge value={item.state} /><span className="font-mono text-[7px] text-chrome-text/40">{item.auditEvents} server events · {item.uploadAttempts ?? 0} upload attempts</span></div>
        <strong className="mt-2 block text-[9px] text-foreground">{item.scenario}</strong><p className="mt-1 break-all font-mono text-[7px] text-chrome-text/45">{item.sourceFormat ? `${item.sourceFormat.toUpperCase()} · ` : ""}{item.uploadFilename || item.submissionId || "No consequential submit"}{item.submissionId && item.uploadFilename ? ` · ${item.submissionId}` : ""}{item.sha256 ? ` · SHA ${item.sha256}` : ""}</p>
      </article>)}
    </div>
  </section>
}

function Evidence({ run, screenshots, recordings }: { run: ProcedureRun; screenshots: ScreenshotEvidence[]; recordings: RecordingEvidence[] }) {
  const readyRecordings = recordings.filter((item) => item.state === "ready")
  return <section className="border-b border-chrome-border p-5">
    <div className="flex items-center justify-between"><Label>Visual evidence</Label><span className="font-mono text-[8px] text-chrome-text/40">{screenshots.length} frames · {readyRecordings.length} videos</span></div>
    {readyRecordings.map((item) => <div key={item.id} className="mt-3 border border-chrome-border bg-black/35 p-2">
      <video controls playsInline preload="metadata" src={`/api/calliope/workbenches/${encodeURIComponent(run.workbench_id || "")}/recordings/${encodeURIComponent(item.id)}/media`} className="aspect-video w-full bg-black object-contain" />
      <div className="mt-2 flex justify-between font-mono text-[7px] text-chrome-text/45"><span>Desktop recording · {duration(item.durationMs)}</span><span>{item.width && item.height ? `${item.width}×${item.height}` : "MP4"}</span></div>
    </div>)}
    {!readyRecordings.length ? <div className="mt-3 border border-chrome-border/70 bg-foreground/[0.015] px-3 py-2 text-[8px] leading-relaxed text-chrome-text/50">No video recording was requested for this replay. Step screenshots remain the durable visual receipt.</div> : null}
    {recordings.filter((item) => item.state !== "ready").map((item) => <div key={item.id} className="mt-2 border border-warning/25 bg-warning/[0.04] px-3 py-2 font-mono text-[8px] text-warning">Recording {item.state}{item.error ? ` · ${item.error}` : ""}</div>)}
    {screenshots.length ? <div className="mt-3 grid grid-cols-2 gap-2">{screenshots.map((item) => <a key={item.url} href={item.url} target="_blank" rel="noreferrer" className="group min-w-0 border border-chrome-border bg-black/25 p-1.5 hover:border-rvbbit-accent/55">
      <img src={item.url} alt={item.label} loading="lazy" className="aspect-[4/3] w-full bg-black/35 object-contain" />
      <div className="mt-1.5 flex items-start justify-between gap-2"><div className="min-w-0"><strong className="block truncate text-[8px] text-foreground">{item.label}</strong><span className="font-mono text-[7px] text-chrome-text/38">{item.detail}</span></div><span className="shrink-0 font-mono text-[7px] text-chrome-text/35">{item.width && item.height ? `${item.width}×${item.height}` : "PNG"}</span></div>
    </a>)}</div> : <div className="mt-3 text-[9px] text-chrome-text/42">No screenshot evidence has been attached to this workbench yet.</div>}
  </section>
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="min-w-0 bg-chrome-bg/75 p-3"><Label>{label}</Label><div className="mt-1 break-all font-mono text-[8px] leading-relaxed text-foreground">{value}</div></div> }
function Empty({ children }: { children: ReactNode }) { return <div className="grid h-full min-h-[180px] place-items-center p-8 text-center text-[10px] text-chrome-text/45"><div><Activity className="mx-auto mb-2 h-5 w-5 opacity-45" />{children}</div></div> }
