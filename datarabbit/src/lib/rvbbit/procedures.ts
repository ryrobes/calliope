"use client"

interface QueryOk { ok: true; rows: Record<string, unknown>[] }
interface QueryErr { ok: false; error: string }

async function query(connectionId: string, sql: string, rowLimit = 1000): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true }),
    })
    return await response.json() as QueryOk | QueryErr
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

export interface ProcedureDefinition {
  procedure_id: string
  capability_name: string
  owner_email: string
  latest_version: number
  approved_version: number | null
  archived: boolean
  title: string
  synopsis: string
  readiness: string
  trust_level: string | null
  clean_runs: number
  strategy_id: string | null
  strategy_revision: number | null
  strategy_kind: string | null
  strategy_status: string | null
  source_workflow_id: string | null
  source_workflow_revision: number | null
  validation: Record<string, unknown>
  updated_at: string
  total_runs: number
  last_run_at: string | null
  last_run_state: string | null
}

export interface ProcedureRun {
  run_id: string
  procedure_id: string
  capability_name: string
  title: string
  playbook_version: number
  strategy_revision: number
  strategy_kind: string
  trigger_kind: string
  trust_level: string
  state: string
  workbench_id: string | null
  guided_run_id: string | null
  current_step: number
  requested_by: string
  session_id: string | null
  steps: Array<Record<string, unknown>>
  result: Record<string, unknown>
  verification: Record<string, unknown>
  error: string | null
  created_at: string
  started_at: string | null
  parked_at: string | null
  completed_at: string | null
  event_sequence_start?: number | null
  event_sequence_end?: number | null
}

export interface ProcedureEvent {
  id: string
  workbench_id: string
  sequence: number
  event_type: string
  actor_email: string | null
  payload: Record<string, unknown>
  created_at: string
  workbench_status?: string | null
  workbench_stopped_at?: string | null
}

const DEFINITIONS = `
SELECT procedure_id::text,capability_name,owner_email,latest_version,approved_version,
       archived,title,synopsis,readiness,trust_level,clean_runs,strategy_id::text,
       strategy_revision,strategy_kind,strategy_status,source_workflow_id::text,
       source_workflow_revision,validation,updated_at,total_runs,last_run_at,last_run_state
FROM rvbbit.calliope_procedure_observatory
ORDER BY updated_at DESC
LIMIT 300`

const RUNS = `
SELECT run_id::text,procedure_id::text,capability_name,title,playbook_version,
       strategy_revision,strategy_kind,trigger_kind,trust_level,state,workbench_id::text,
       guided_run_id::text,current_step,requested_by,session_id::text,steps,result,
       verification,error,created_at,started_at,parked_at,completed_at
FROM rvbbit.calliope_procedure_run_observatory
ORDER BY created_at DESC
LIMIT 500`

const EVENTS = `
SELECT e.id::text,e.workbench_id::text,e.sequence,e.event_type,e.actor_email,e.payload,e.created_at,
       w.status AS workbench_status,w.stopped_at AS workbench_stopped_at
FROM rvbbit.calliope_workbench_events e
JOIN rvbbit.calliope_workbenches w ON w.id=e.workbench_id
WHERE e.workbench_id IN (
  SELECT workbench_id FROM rvbbit.calliope_procedure_runs WHERE workbench_id IS NOT NULL
  UNION
  SELECT DISTINCT workbench_id FROM rvbbit.calliope_workbench_events WHERE event_type='guided.run.started'
)
ORDER BY e.created_at DESC,e.sequence DESC
LIMIT 3000`

const WORKBENCH_TRIALS_ID = "__calliope_workbench_trials__"

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback
}

function bool(value: unknown): boolean {
  return value === true
}

function deriveWorkbenchTrials(events: ProcedureEvent[], procedureRuns: ProcedureRun[]): ProcedureRun[] {
  const linkedGuidedRuns = new Set(procedureRuns.map((run) => run.guided_run_id).filter(Boolean))
  const byWorkbench = new Map<string, ProcedureEvent[]>()
  for (const event of events) {
    const current = byWorkbench.get(event.workbench_id) || []
    current.push(event)
    byWorkbench.set(event.workbench_id, current)
  }

  const trials: ProcedureRun[] = []
  for (const [workbenchId, unordered] of byWorkbench) {
    const items = [...unordered].sort((a, b) => a.sequence - b.sequence)
    const starts = items.filter((event) => event.event_type === "guided.run.started")
    starts.forEach((start, index) => {
      const payload = record(start.payload)
      const runId = text(payload.run_id, start.id)
      if (linkedGuidedRuns.has(runId) || text(payload.procedure_run_id)) return

      const previousStart = index > 0 ? starts[index - 1].sequence : -1
      const nextStart = starts[index + 1]?.sequence ?? Number.POSITIVE_INFINITY
      const nextProvingStart = items.find((event) =>
        event.sequence > start.sequence && event.sequence < nextStart && event.event_type === "proving.run.started",
      )
      const nextBoundary = nextProvingStart?.sequence ?? nextStart
      const provingStart = [...items].reverse().find((event) =>
        event.sequence > previousStart && event.sequence <= start.sequence && event.event_type === "proving.run.started",
      )
      const sequenceStart = provingStart?.sequence ?? start.sequence
      const runEvents = items.filter((event) => event.sequence >= sequenceStart && event.sequence < nextBoundary)
      const terminal = [...runEvents].reverse().find((event) => [
        "guided.run.saved", "guided.run.stopped", "guided.replay.completed", "guided.replay.paused",
      ].includes(event.event_type))
      const terminalPayload = record(terminal?.payload)
      const workbenchStatus = text(start.workbench_status)
      const state = terminal?.event_type === "guided.replay.completed"
        ? (bool(terminalPayload.passed) ? "complete" : "failed")
        : terminal?.event_type === "guided.replay.paused"
          ? "parked"
          : terminal?.event_type === "guided.run.saved"
            ? "complete"
            : terminal?.event_type === "guided.run.stopped"
              ? "stopped"
              : workbenchStatus === "failed"
                ? "failed"
                : ["stopped", "expired"].includes(workbenchStatus)
                  ? "stopped"
                  : "running"
      const proposals = runEvents.filter((event) => event.event_type === "guided.action.proposed")
      const verified = runEvents.filter((event) => event.event_type === "guided.step.verified" && bool(record(event.payload).passed))
      const receipt = [...runEvents].reverse().find((event) => event.event_type === "proving.receipt.captured")
      const goal = text(payload.goal, "Guided computer-use run")

      trials.push({
        run_id: runId,
        procedure_id: WORKBENCH_TRIALS_ID,
        capability_name: "computer.use.guided",
        title: goal,
        playbook_version: 0,
        strategy_revision: Number(payload.revision) || 1,
        strategy_kind: text(payload.mode, "teaching"),
        trigger_kind: "guided",
        trust_level: "supervised",
        state,
        workbench_id: workbenchId,
        guided_run_id: runId,
        current_step: verified.length,
        requested_by: start.actor_email || "unknown",
        session_id: null,
        steps: proposals.map((event, stepIndex) => {
          const proposal = record(event.payload)
          const proposalId = text(proposal.proposal_id, event.id)
          const verification = runEvents.find((candidate) =>
            candidate.event_type === "guided.step.verified" && text(record(candidate.payload).proposal_id) === proposalId,
          )
          return {
            id: proposalId,
            label: text(proposal.instruction, `Proposed action ${stepIndex + 1}`),
            lane: "computer",
            risk: text(proposal.risk, "read"),
            status: verification ? (bool(record(verification.payload).passed) ? "verified" : "correction") : "proposed",
          }
        }),
        result: receipt ? record(receipt.payload) : {},
        verification: verified.length ? record(verified[verified.length - 1].payload) : {},
        error: null,
        created_at: start.created_at,
        started_at: start.created_at,
        parked_at: state === "parked" ? terminal?.created_at || null : null,
        completed_at: terminal?.created_at || null,
        event_sequence_start: sequenceStart,
        event_sequence_end: Number.isFinite(nextBoundary) ? nextBoundary - 1 : null,
      })
    })
  }
  return trials.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
}

function workbenchTrialsDefinition(runs: ProcedureRun[]): ProcedureDefinition | null {
  if (!runs.length) return null
  const latest = runs[0]
  return {
    procedure_id: WORKBENCH_TRIALS_ID,
    capability_name: "computer.use.guided",
    owner_email: latest.requested_by,
    latest_version: 0,
    approved_version: null,
    archived: false,
    title: "Guided workbench trials",
    synopsis: "Guided desktop demonstrations, proving-ground receipts, recoveries, and parks outside formal Procedure executions.",
    readiness: "observed",
    trust_level: "supervised",
    clean_runs: runs.filter((run) => run.state === "complete").length,
    strategy_id: null,
    strategy_revision: null,
    strategy_kind: "guided workbench",
    strategy_status: "candidate",
    source_workflow_id: null,
    source_workflow_revision: null,
    validation: { status: "unpromoted", successful_replays: 0 },
    updated_at: latest.created_at,
    total_runs: runs.length,
    last_run_at: latest.created_at,
    last_run_state: latest.state,
  }
}

export async function fetchProcedureObservatory(connectionId: string): Promise<{
  ready: boolean
  definitions: ProcedureDefinition[]
  runs: ProcedureRun[]
  events: ProcedureEvent[]
  error?: string
}> {
  const probe = await query(connectionId,
    "SELECT to_regclass('rvbbit.calliope_procedure_runs') IS NOT NULL AND to_regclass('rvbbit.calliope_procedure_observatory') IS NOT NULL AS ready",
    1,
  )
  if (!probe.ok) return { ready: false, definitions: [], runs: [], events: [], error: probe.error }
  if (!Boolean(probe.rows[0]?.ready)) return { ready: false, definitions: [], runs: [], events: [] }
  const [definitions, runs, events] = await Promise.all([
    query(connectionId, DEFINITIONS, 300),
    query(connectionId, RUNS, 500),
    query(connectionId, EVENTS, 3000),
  ])
  const failure = [definitions, runs, events].find((item) => !item.ok) as QueryErr | undefined
  const procedureDefinitions = definitions.ok ? definitions.rows as unknown as ProcedureDefinition[] : []
  const procedureRuns = runs.ok ? runs.rows as unknown as ProcedureRun[] : []
  const workbenchEvents = events.ok ? (events.rows as unknown as ProcedureEvent[]).map((event) => ({
    ...event,
    sequence: Number(event.sequence) || 0,
  })) : []
  const workbenchTrials = deriveWorkbenchTrials(workbenchEvents, procedureRuns)
  const trialsDefinition = workbenchTrialsDefinition(workbenchTrials)
  return {
    ready: true,
    definitions: trialsDefinition ? [trialsDefinition, ...procedureDefinitions] : procedureDefinitions,
    runs: [...workbenchTrials, ...procedureRuns],
    events: workbenchEvents,
    error: failure?.error,
  }
}
