"use client"

/**
 * DataRabbit projection of receipt-backed Business Topology proposal bundles.
 *
 * Object bundles remain shadow state. Reconciled Value Graphs can be reviewed
 * and atomically promoted into native RVBBIT cubes + governed metrics.
 */

export type TopologyBundleStatus = "proposed" | "needs_revision" | "rejected" | "superseded"
export type BusinessValueGraphStatus = TopologyBundleStatus | "approved" | "promoted"
export type TopologyNodeKind = "object" | "facet" | "lifecycle" | "event" | "measure" | "category"
export type TopologyCorrectionState = "draft" | "complete"

export interface TopologySource {
  source_key: string
  name: string
  motif_work_id: string
  source: {
    kind?: string
    database?: string
    schema?: string
    relation?: string
  }
}

export interface TopologyPopulationContext {
  population_id: string
  source_key: string
  source_label: string
  field_name: string
  display_name: string
}

export interface TopologyNode {
  node_key: string
  node_kind: TopologyNodeKind
  name: string
  description?: string | null
  parent_node_key?: string | null
  confidence: number
  evidence_work_ids: string[]
  properties?: Record<string, unknown>
}

export interface TopologyBinding {
  node_key: string
  population_id: string
  binding_role: string
  authority_hint: string
  confidence: number
  evidence_work_ids: string[]
}

export interface TopologyEdge {
  subject_node_key: string
  predicate: string
  object_node_key: string
  confidence: number
  evidence_work_ids: string[]
}

export interface TopologySkeletonResult {
  schema_version: string
  work_id: string
  status: "proposed" | "abstained"
  canonical_name?: string
  rationale?: string
  nodes: TopologyNode[]
  bindings: TopologyBinding[]
  edges: TopologyEdge[]
  unbound_population_ids: string[]
}

export interface TopologyExecutionReceipt {
  backend?: string
  endpoint_host?: string
  model_version?: string
  service_model?: string
  provider_request_id?: string
  latency_ms?: number
  prompt_sha256?: string
  response_sha256?: string
  transport_attempts?: number
  transport_errors?: string[]
  usage?: {
    prompt_tokens?: number
    completion_tokens?: number
    total_tokens?: number
    cost?: number
  }
}

export interface TopologyNodePatch {
  node_key: string
  name?: string
  description?: string | null
  node_kind?: TopologyNodeKind
  parent_node_key?: string | null
  suppressed?: boolean
}

export interface TopologyBindingPatch {
  node_key: string
  population_id: string
  original_binding_role: string
  binding_role?: string
  authority_hint?: string
  suppressed?: boolean
}

export interface TopologyRelationshipSuggestion {
  kind: "split_node" | "merge_nodes" | "reconsider_relationship" | "other"
  node_keys: string[]
  note?: string
}

export interface TopologyBundleCorrection {
  schema_version: "rvbbit.business-topology.bundle-correction.v1"
  canonical_name?: string
  node_patches: TopologyNodePatch[]
  binding_patches: TopologyBindingPatch[]
  relationship_suggestions: TopologyRelationshipSuggestion[]
  review_note?: string
}

export interface TopologyBundle {
  bundleId: string
  bundleKey: string
  canonicalName: string
  status: TopologyBundleStatus
  workKind: "neighborhood_synthesis" | "bridge_synthesis"
  scopeKind: "excavation_unit" | "boundary_link"
  scopeId: string
  planSha256: string
  workId: string
  sourceCount: number
  sourceKeys: string[]
  sources: TopologySource[]
  nodeCount: number
  bindingCount: number
  edgeCount: number
  unboundPopulationCount: number
  averageConfidence: number | null
  minimumConfidence: number | null
  promptTokens: number
  completionTokens: number
  totalTokens: number
  cost: number
  modelVersions: string[]
  workerVersion: string
  promptContractVersion: string
  inputPacketSha256: string
  completedAt: string
  proposedAt: string
  updatedAt: string
  validation: Record<string, unknown>
  receiptResult: TopologySkeletonResult
  result: TopologySkeletonResult
  correctionRevision: number
  correctionState: TopologyCorrectionState | null
  correction: TopologyBundleCorrection | null
  correctedBy: string | null
  correctedAt: string | null
  context: {
    schema_version?: string
    scope?: Record<string, unknown>
    sources?: TopologySource[]
    populations?: TopologyPopulationContext[]
    dependency_work_ids?: string[]
    privacy?: Record<string, unknown>
  }
  executionReceipts: TopologyExecutionReceipt[]
}

export interface BusinessTopologyState {
  available: boolean
  correctionsAvailable: boolean
  bundles: TopologyBundle[]
  error?: string
}

export interface BusinessValueDimension {
  key: string
  name: string
}

export interface BusinessValueMetric {
  metric_key: string
  name: string
  description: string
  metric_kind: "outcome" | "component" | "driver"
  unit: "currency" | "count" | "quantity" | "ratio" | "duration" | "score" | "other"
  grain: string
  time_semantics: string
  formula: string
  event_sql: string
  dimensions: BusinessValueDimension[]
  primary_dimension_key?: string | null
  confidence: number
}

export interface BusinessValueEdge {
  parent_metric_key: string
  child_metric_key: string
  relationship: "adds" | "subtracts" | "multiplies" | "divides" | "drives" | "contributes_to"
  confidence: number
  rationale?: string
}

export interface BusinessDomainSummary {
  domain_key: string
  name: string
  summary: string
}

export interface BusinessConcept {
  concept_key: string
  name: string
  kind: "object" | "event" | "process" | "classification" | "measure" | "place" | "actor"
  description: string
}

export interface BusinessProcess {
  process_key: string
  name: string
  description: string
  concept_keys: string[]
}

export interface BusinessSourceMembership {
  relation: string
  role: "event_stream" | "transaction" | "reference" | "dimension" | "snapshot" | "context"
  concept_keys: string[]
  rationale: string
  confidence: number
}

export interface BusinessSemanticRelationship {
  relationship: "joins_to" | "parallel_to" | "comparable_to" | "describes" | "classifies" | "located_at" | "performed_by" | "part_of" | "informs"
  left_relation?: string | null
  right_relation?: string | null
  subject_concept_key?: string | null
  object_concept_key?: string | null
  left_columns: string[]
  right_columns: string[]
  rationale: string
  confidence: number
}

export interface BusinessValuePeriod {
  period_start: string
  value: number
}

export interface BusinessValueChangeItem {
  dimension_value: string
  prior_value: number
  current_value: number
  change: number
  share_of_change: number
}

export interface BusinessValueMetricEvaluation {
  metric_key: string
  event_rows: number
  valued_rows: number
  minimum_time: string | null
  maximum_time: string | null
  total_value: number
  bucket: string
  series: BusinessValuePeriod[]
  prior_period: BusinessValuePeriod | null
  current_period: BusinessValuePeriod | null
  total_change: number | null
  changes: Record<string, BusinessValueChangeItem[]>
  reconciliations: Record<string, {
    prior_difference: number
    current_difference: number
    passed: boolean
  }>
  cube_recommendation: {
    metric: string
    time_dimension: string
    time_grain: string
    dimensions: string[]
    source: string
  }
}

export interface BusinessValueGraph {
  graphId: string
  workflowRunId: string
  domainKey: string
  name: string
  summary: string
  status: BusinessValueGraphStatus
  sourceRelations: string[]
  primaryMetricKey: string | null
  metrics: BusinessValueMetric[]
  edges: BusinessValueEdge[]
  openQuestions: string[]
  businessDomain: BusinessDomainSummary | null
  valueSystemKey: string
  concepts: BusinessConcept[]
  processes: BusinessProcess[]
  sourceMemberships: BusinessSourceMembership[]
  semanticRelationships: BusinessSemanticRelationship[]
  analyticalQuestions: string[]
  gaps: string[]
  evaluations: BusinessValueMetricEvaluation[]
  reconciled: boolean
  fallback: boolean
  semanticDossier: BusinessSemanticDossier | null
  promotion: BusinessValuePromotion | null
  proposedAt: string
  updatedAt: string
}

export interface BusinessSemanticAssertion {
  assertionId: string
  assertionKey: string
  kind: string
  subjectKey: string
  predicate: string
  value: Record<string, unknown>
  confidence: number
  effectiveConfidence: number
  validationState: "unobserved" | "supported" | "warning" | "contradicted"
  reviewState: "normal" | "needs_revision" | "needs_decision"
  observationCount: number
  lastObservedAt: string | null
  provenance: string
  evidence: Record<string, unknown>
}

export interface BusinessSemanticWatch {
  watchId: string
  watchKey: string
  kind: "source_presence" | "join_coverage" | "metric_reconciliation"
  assertionId: string
  state: "passing" | "failing" | "unknown"
  enabled: boolean
  specification: Record<string, unknown>
  evidence: Record<string, unknown>
  observationCount: number
  lastObservedAt: string | null
  nextDueAt: string | null
  consecutivePasses: number
  consecutiveFailures: number
}

export interface BusinessSemanticDiffItem {
  assertion_key: string
  kind: string
  subject_key: string
  predicate: string
  value?: Record<string, unknown>
  before?: Record<string, unknown>
  after?: Record<string, unknown>
  confidence?: number
  before_confidence?: number
  after_confidence?: number
}

export interface BusinessSemanticDimensionMember {
  value: string
  row_count: number
  metric_value: number
  share_of_rows: number
  first_seen: string | null
  last_seen: string | null
}

export interface BusinessSemanticDimensionCensus {
  censusId: number
  metricKey: string
  dimensionKey: string
  coverageMode: "exact" | "bounded_sample"
  coverageComplete: boolean
  memberListComplete: boolean
  rowLimit: number
  rowsObserved: number
  memberCount: number
  members: BusinessSemanticDimensionMember[]
  tailMemberCount: number
  tailRows: number
  totalMetricValue: number | null
  baselineCensusId: number | null
  addedValues: string[]
  removedValues: string[]
  taxonomyChanged: boolean
  triggerKind: string
  observedAt: string | null
}

export interface BusinessSemanticFamilyRelationship {
  relationshipId: number
  workflowRunId: string
  sourceGraphId: string
  sourceFamilyId: string | null
  sourceValueSystemKey: string
  sourceValueSystemName: string
  sourceBusinessDomainKey: string
  sourceBusinessDomainName: string
  targetGraphId: string
  targetFamilyId: string | null
  targetValueSystemKey: string
  targetValueSystemName: string
  targetBusinessDomainKey: string
  targetBusinessDomainName: string
  relationshipKind: "same_family" | "extension" | "parallel_population" | "alternative_definition" | "material_contradiction" | "distinct_family" | "abstain"
  confidence: number
  proposedBusinessDomainKey: string | null
  proposedBusinessDomainName: string | null
  proposedFamilyKey: string | null
  proposedFamilyName: string | null
  rationale: string
  evidence: Record<string, unknown>
  candidateFingerprint: string
  adjudicationVersion: string
  reviewState: "proposed" | "accepted" | "rejected"
  reviewNote: string | null
  decidedBy: string | null
  decidedAt: string | null
  proposedBy: string
  proposedAt: string
}

export interface BusinessSemanticDossier {
  familyId: string
  graphId: string
  workflowRunId: string
  revision: number
  predecessorGraphId: string | null
  assertionCount: number
  watchableAssertionCount: number
  assertionKinds: Record<string, number>
  automatedWatchCoverage: number
  watchCount: number
  watchStates: Record<string, number>
  needsRevisionCount: number
  needsDecisionCount: number
  assertions: BusinessSemanticAssertion[]
  watches: BusinessSemanticWatch[]
  dimensionCensuses: BusinessSemanticDimensionCensus[]
  familyRelationships: BusinessSemanticFamilyRelationship[]
  diff: {
    change_kind?: "baseline" | "no_change" | "changed" | "unavailable"
    material_change?: boolean
    added?: BusinessSemanticDiffItem[]
    removed?: BusinessSemanticDiffItem[]
    changed?: BusinessSemanticDiffItem[]
    unchanged_count?: number
  }
}

export interface BusinessValuePromotion {
  promotionId: string
  cubeName: string
  cubeVersion: number
  materializationGrain: "day" | "week" | "month" | "quarter" | "year"
  metricVersions: Record<string, number>
  receipt: {
    aggregate_rows?: number
    original_event_rows?: number
    row_reduction_ratio?: number | null
    reconciliation?: Array<{
      metric_key: string
      passed: boolean
      expected_value: number
      actual_value: number
      expected_event_rows: number
      actual_event_rows: number
    }>
    [key: string]: unknown
  }
  promotedBy: string
  promotedAt: string
  lastRefreshedAt: string
}

export interface BusinessValueGraphState {
  available: boolean
  graphs: BusinessValueGraph[]
  error?: string
}

export type TopologyWorkflowStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled"

export interface TopologyWorkflowRun {
  runId: string
  runName: string
  status: TopologyWorkflowStatus
  phase: string
  parameters: Record<string, unknown>
  progress: Record<string, unknown>
  result: Record<string, unknown> | null
  requestedBy: string
  workerId: string | null
  attempts: number
  error: string | null
  requestedAt: string
  startedAt: string | null
  updatedAt: string
  finishedAt: string | null
  leaseExpiresAt: string | null
  relationCount: number
  populationCount: number
  neighborhoodCount: number
  excavationUnitCount: number
  workItemCount: number
  completedWorkItems: number
  llmAttempts: number
  bundlesStaged: number
  planSha256: string | null
  workerVersion: string | null
  assignedWorkerOnline: boolean
}

export interface TopologyWorkflowState {
  available: boolean
  workerOnline: boolean
  run: TopologyWorkflowRun | null
  displayRunId: string | null
  workers: TopologyWorker[]
  queuedRuns: TopologyQueuedRun[]
  candidateSchemas: string[]
  error?: string
}

export interface TopologyWorker {
  workerId: string
  workerVersion: string
  status: string
  lastSeenAt: string
  online: boolean
  activeRunId: string | null
  activeRunName: string | null
  scopeSchema: string | null
}

export interface TopologyQueuedRun {
  runId: string
  runName: string
  scopeSchema: string | null
  relationCount: number
  requestedAt: string
}

type QueryResult =
  | { ok: true; rows: Record<string, unknown>[] }
  | { ok: false; error: string }

async function runQuery(
  connectionId: string,
  sql: string,
  options: { readOnly?: boolean; rowLimit?: number } = {},
): Promise<QueryResult> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        connectionId,
        sql,
        rowLimit: options.rowLimit ?? 250,
        readOnly: options.readOnly ?? true,
        poolLane: "meta",
      }),
    })
    return (await response.json()) as QueryResult
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

function correctionValue(value: unknown): TopologyBundleCorrection | null {
  const raw = jsonValue<Record<string, unknown> | null>(value, null)
  if (!raw || raw.schema_version !== "rvbbit.business-topology.bundle-correction.v1") {
    return null
  }
  return {
    schema_version: "rvbbit.business-topology.bundle-correction.v1",
    canonical_name:
      typeof raw.canonical_name === "string" && raw.canonical_name.trim()
        ? raw.canonical_name.trim()
        : undefined,
    node_patches: Array.isArray(raw.node_patches)
      ? (raw.node_patches as TopologyNodePatch[])
      : [],
    binding_patches: Array.isArray(raw.binding_patches)
      ? (raw.binding_patches as TopologyBindingPatch[])
      : [],
    relationship_suggestions: Array.isArray(raw.relationship_suggestions)
      ? (raw.relationship_suggestions as TopologyRelationshipSuggestion[])
      : [],
    review_note: typeof raw.review_note === "string" ? raw.review_note : undefined,
  }
}

function applyCorrection(
  result: TopologySkeletonResult,
  correction: TopologyBundleCorrection | null,
): TopologySkeletonResult {
  if (!correction) return result
  const nodePatches = new Map(correction.node_patches.map((patch) => [patch.node_key, patch]))
  const suppressedNodes = new Set(
    correction.node_patches.filter((patch) => patch.suppressed).map((patch) => patch.node_key),
  )
  const nodes = result.nodes
    .filter((node) => !suppressedNodes.has(node.node_key))
    .map((node) => {
      const patch = nodePatches.get(node.node_key)
      if (!patch) return node
      const parent = Object.hasOwn(patch, "parent_node_key")
        ? patch.parent_node_key ?? null
        : node.parent_node_key
      return {
        ...node,
        name: patch.name ?? node.name,
        description: Object.hasOwn(patch, "description")
          ? patch.description
          : node.description,
        node_kind: patch.node_kind ?? node.node_kind,
        parent_node_key: parent && !suppressedNodes.has(parent) ? parent : null,
      }
    })
  const bindingPatches = new Map(
    correction.binding_patches.map((patch) => [
      `${patch.node_key}\u0000${patch.population_id}\u0000${patch.original_binding_role}`,
      patch,
    ]),
  )
  const bindings = result.bindings
    .filter((binding) => !suppressedNodes.has(binding.node_key))
    .flatMap((binding) => {
      const key = `${binding.node_key}\u0000${binding.population_id}\u0000${binding.binding_role}`
      const patch = bindingPatches.get(key)
      if (patch?.suppressed) return []
      return [{
        ...binding,
        binding_role: patch?.binding_role ?? binding.binding_role,
        authority_hint: patch?.authority_hint ?? binding.authority_hint,
      }]
    })
  const boundPopulations = new Set(bindings.map((binding) => binding.population_id))
  const knownPopulations = new Set([
    ...result.unbound_population_ids,
    ...result.bindings.map((binding) => binding.population_id),
  ])
  return {
    ...result,
    canonical_name: correction.canonical_name ?? result.canonical_name,
    nodes,
    bindings,
    edges: result.edges.filter(
      (edge) =>
        !suppressedNodes.has(edge.subject_node_key)
        && !suppressedNodes.has(edge.object_node_key),
    ),
    unbound_population_ids: [...knownPopulations]
      .filter((populationId) => !boundPopulations.has(populationId))
      .sort(),
  }
}

function jsonValue<T>(value: unknown, fallback: T): T {
  if (typeof value === "string") {
    try {
      return JSON.parse(value) as T
    } catch {
      return fallback
    }
  }
  return value == null ? fallback : (value as T)
}

function numberOrNull(value: unknown): number | null {
  if (value == null || value === "") return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}

function workflowRun(row: Record<string, unknown>): TopologyWorkflowRun | null {
  if (!row.run_id) return null
  return {
    runId: String(row.run_id),
    runName: String(row.run_name ?? "Business Topology excavation"),
    status: String(row.status ?? "queued") as TopologyWorkflowStatus,
    phase: String(row.phase ?? "queued"),
    parameters: jsonValue<Record<string, unknown>>(row.parameters, {}),
    progress: jsonValue<Record<string, unknown>>(row.progress, {}),
    result: jsonValue<Record<string, unknown> | null>(row.result, null),
    requestedBy: String(row.requested_by ?? ""),
    workerId: textOrNull(row.worker_id),
    attempts: Number(row.attempts ?? 0),
    error: textOrNull(row.error),
    requestedAt: String(row.requested_at ?? ""),
    startedAt: textOrNull(row.started_at),
    updatedAt: String(row.updated_at ?? ""),
    finishedAt: textOrNull(row.finished_at),
    leaseExpiresAt: textOrNull(row.lease_expires_at),
    relationCount: Number(row.relation_count ?? 0),
    populationCount: Number(row.population_count ?? 0),
    neighborhoodCount: Number(row.neighborhood_count ?? 0),
    excavationUnitCount: Number(row.excavation_unit_count ?? 0),
    workItemCount: Number(row.work_item_count ?? 0),
    completedWorkItems: Number(row.completed_work_items ?? 0),
    llmAttempts: Number(row.llm_attempts ?? 0),
    bundlesStaged: Number(row.bundles_staged ?? 0),
    planSha256: textOrNull(row.plan_sha256),
    workerVersion: textOrNull(row.worker_version),
    assignedWorkerOnline: row.assigned_worker_online === true,
  }
}

export async function fetchBusinessTopologyWorkflow(
  connectionId: string,
): Promise<TopologyWorkflowState> {
  const probe = await runQuery(
    connectionId,
    `SELECT to_regclass('rvbbit.business_topology_workflow_runs') IS NOT NULL
              AND to_regclass('rvbbit.business_topology_workflow_status') IS NOT NULL
              AS available`,
    { rowLimit: 1 },
  )
  if (!probe.ok) {
    return { available: false, workerOnline: false, run: null, displayRunId: null, workers: [], queuedRuns: [], candidateSchemas: [], error: probe.error }
  }
  if (probe.rows[0]?.available !== true) {
    return { available: false, workerOnline: false, run: null, displayRunId: null, workers: [], queuedRuns: [], candidateSchemas: [] }
  }

  const result = await runQuery(
    connectionId,
    `WITH latest AS (
       SELECT *
         FROM rvbbit.business_topology_workflow_status
        ORDER BY CASE
                   WHEN status IN ('running','cancel_requested') THEN 0
                   WHEN status='queued' THEN 1 ELSE 2
                 END,requested_at DESC
        LIMIT 1
     ), latest_completed_with_results AS (
       SELECT completed.run_id
         FROM rvbbit.business_topology_workflow_status completed
        WHERE completed.status='completed'
          AND (
              EXISTS (
                  SELECT 1
                    FROM rvbbit.business_topology_proposal_bundles bundle
                   WHERE bundle.workflow_run_id=completed.run_id
              )
              OR EXISTS (
                  SELECT 1
                    FROM rvbbit.business_value_graph_proposals graph
                   WHERE graph.workflow_run_id=completed.run_id
              )
          )
        ORDER BY completed.finished_at DESC NULLS LAST,completed.requested_at DESC
        LIMIT 1
     )
     SELECT latest.*,
            coalesce(
              (SELECT run_id FROM latest_completed_with_results),latest.run_id
            )::text AS display_run_id,
            EXISTS (
              SELECT 1
                FROM rvbbit.business_topology_workflow_workers worker
               WHERE worker.worker_status IN ('ready','busy')
                 AND worker.last_seen_at>clock_timestamp()-interval '90 seconds'
            ) AS worker_online
            ,coalesce((
              SELECT jsonb_agg(jsonb_build_object(
                  'worker_id',worker.worker_id,
                  'worker_version',worker.worker_version,
                  'status',worker.worker_status,
                  'last_seen_at',worker.last_seen_at,
                  'online',worker.last_seen_at>clock_timestamp()-interval '90 seconds',
                  'active_run_id',active.run_id,
                  'active_run_name',active.run_name,
                  'scope_schema',active.parameters->>'scope_schema'
              ) ORDER BY worker.last_seen_at DESC)
                FROM rvbbit.business_topology_workflow_workers worker
                LEFT JOIN rvbbit.business_topology_workflow_runs active
                  ON active.worker_id=worker.worker_id
                 AND active.status IN ('running','cancel_requested')
            ),'[]'::jsonb) AS workers
            ,coalesce((
              SELECT jsonb_agg(jsonb_build_object(
                  'run_id',queued.run_id,
                  'run_name',queued.run_name,
                  'scope_schema',queued.parameters->>'scope_schema',
                  'relation_count',jsonb_array_length(queued.parameters->'relations'),
                  'requested_at',queued.requested_at
              ) ORDER BY queued.requested_at)
                FROM rvbbit.business_topology_workflow_runs queued
               WHERE queued.status='queued'
            ),'[]'::jsonb) AS queued_runs
            ,coalesce((
              SELECT jsonb_agg(schema_name ORDER BY schema_name)
                FROM (
                  SELECT DISTINCT n.nspname AS schema_name
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE c.relkind IN ('r','p','m') AND NOT c.relispartition
                     AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
                     AND has_table_privilege(c.oid,'SELECT')
                ) schemas
            ),'[]'::jsonb) AS candidate_schemas
       FROM (SELECT 1) anchor
       LEFT JOIN latest ON true`,
    { rowLimit: 1 },
  )
  if (!result.ok) {
    return { available: true, workerOnline: false, run: null, displayRunId: null, workers: [], queuedRuns: [], candidateSchemas: [], error: result.error }
  }
  const row = result.rows[0] ?? {}
  return {
    available: true,
    workerOnline: row.worker_online === true,
    run: workflowRun(row),
    displayRunId: textOrNull(row.display_run_id),
    workers: jsonValue<Record<string, unknown>[]>(row.workers, []).map((worker) => ({
      workerId: String(worker.worker_id ?? ""),
      workerVersion: String(worker.worker_version ?? ""),
      status: String(worker.status ?? "unknown"),
      lastSeenAt: String(worker.last_seen_at ?? ""),
      online: worker.online === true,
      activeRunId: textOrNull(worker.active_run_id),
      activeRunName: textOrNull(worker.active_run_name),
      scopeSchema: textOrNull(worker.scope_schema),
    })),
    queuedRuns: jsonValue<Record<string, unknown>[]>(row.queued_runs, []).map((queued) => ({
      runId: String(queued.run_id ?? ""),
      runName: String(queued.run_name ?? ""),
      scopeSchema: textOrNull(queued.scope_schema),
      relationCount: Number(queued.relation_count ?? 0),
      requestedAt: String(queued.requested_at ?? ""),
    })),
    candidateSchemas: jsonValue<string[]>(row.candidate_schemas, []),
  }
}

export async function startBusinessTopologyWorkflow(
  connectionId: string,
  schema: string,
): Promise<{ ok: true; runId: string } | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_topology_queue_schema(${sqlLiteral(schema)})::text AS run_id`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  const runId = String(result.rows[0]?.run_id ?? "")
  return runId ? { ok: true, runId } : { ok: false, error: "The workflow was not queued." }
}

export async function cancelBusinessTopologyWorkflow(
  connectionId: string,
  runId: string,
): Promise<{ ok: true; status: string } | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_topology_cancel_workflow(${sqlLiteral(runId)}::uuid) AS status`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  return { ok: true, status: String(result.rows[0]?.status ?? "cancel_requested") }
}

function skeletonResult(value: unknown): TopologySkeletonResult {
  const raw = jsonValue<Record<string, unknown>>(value, {})
  return {
    schema_version: String(raw.schema_version ?? ""),
    work_id: String(raw.work_id ?? ""),
    status: raw.status === "abstained" ? "abstained" : "proposed",
    canonical_name:
      typeof raw.canonical_name === "string" && raw.canonical_name.trim()
        ? raw.canonical_name.trim()
        : undefined,
    rationale: typeof raw.rationale === "string" ? raw.rationale : undefined,
    nodes: Array.isArray(raw.nodes) ? (raw.nodes as TopologyNode[]) : [],
    bindings: Array.isArray(raw.bindings) ? (raw.bindings as TopologyBinding[]) : [],
    edges: Array.isArray(raw.edges) ? (raw.edges as TopologyEdge[]) : [],
    unbound_population_ids: Array.isArray(raw.unbound_population_ids)
      ? (raw.unbound_population_ids as string[])
      : [],
  }
}

function canonicalTreeName(
  result: TopologySkeletonResult,
  workKind: TopologyBundle["workKind"],
): string {
  if (result.canonical_name) return result.canonical_name

  const nodeKeys = new Set(result.nodes.map((node) => node.node_key))
  const roots = result.nodes.filter(
    (node) => !node.parent_node_key || !nodeKeys.has(node.parent_node_key),
  )
  const durableRoots = roots.filter((node) =>
    ["object", "lifecycle", "event"].includes(node.node_kind),
  )
  const candidates = durableRoots.length > 0
    ? durableRoots
    : roots.length > 0
      ? roots
      : result.nodes
  const names = candidates.reduce<string[]>((values, node) => {
    const name = node.name.trim()
    if (!name || values.some((value) => value.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      return values
    }
    return [...values, name]
  }, [])
  if (names.length > 0) return names.slice(0, 2).join(" & ")
  return workKind === "bridge_synthesis" ? "Cross-domain relationship" : "Unresolved business area"
}

export async function fetchBusinessTopology(
  connectionId: string,
  workflowRunId?: string | null,
): Promise<BusinessTopologyState> {
  const probe = await runQuery(
    connectionId,
    `SELECT to_regclass('rvbbit.business_topology_proposal_bundles') IS NOT NULL AS has_bundles,
            to_regclass('rvbbit.business_topology_proposal_bundle_summary') IS NOT NULL AS has_summary,
            to_regclass('rvbbit.business_value_graph_current') IS NOT NULL AS has_current_graphs,
            to_regclass('rvbbit.business_topology_bundle_corrections') IS NOT NULL
              AND to_regclass('rvbbit.business_topology_proposal_bundle_review') IS NOT NULL
              AS has_corrections`,
  )
  if (!probe.ok) {
    return { available: false, correctionsAvailable: false, bundles: [], error: probe.error }
  }
  if (!probe.rows[0]?.has_bundles || !probe.rows[0]?.has_summary) {
    return { available: false, correctionsAvailable: false, bundles: [] }
  }
  const correctionsAvailable = probe.rows[0]?.has_corrections === true
  const reviewSource = correctionsAvailable
    ? "rvbbit.business_topology_proposal_bundle_review"
    : "rvbbit.business_topology_proposal_bundle_summary"
  const correctionProjection = correctionsAvailable
    ? "correction_revision,correction_state,correction,corrected_by,corrected_at"
    : "0 AS correction_revision,NULL::text AS correction_state,NULL::jsonb AS correction,NULL::text AS corrected_by,NULL::timestamptz AS corrected_at"

  const result = await runQuery(
    connectionId,
    `SELECT bundle_id::text,bundle_key,status,work_kind,scope_kind,scope_id,
            plan_sha256,work_id,source_count,source_keys,sources,
            node_count,binding_count,edge_count,unbound_population_count,
            average_confidence,minimum_confidence,
            prompt_tokens,completion_tokens,total_tokens,cost::float8 AS cost,
            model_versions,worker_version,prompt_contract_version,
            input_packet_sha256,completed_at,proposed_at,updated_at,
            validation,result,context,execution_receipts,
            ${correctionProjection}
       FROM ${reviewSource}
      ${workflowRunId
        ? `WHERE bundle_id IN (
             SELECT bundle_id
               FROM rvbbit.business_topology_proposal_bundles
              WHERE workflow_run_id=${sqlLiteral(workflowRunId)}::uuid
           )`
        : probe.rows[0]?.has_current_graphs === true
          ? `WHERE bundle_id IN (
               SELECT bundle.bundle_id
                 FROM rvbbit.business_topology_proposal_bundles bundle
                WHERE bundle.workflow_run_id IN (
                    SELECT DISTINCT workflow_run_id
                      FROM rvbbit.business_value_graph_current
                )
             )`
          : ""}
      ORDER BY CASE status WHEN 'proposed' THEN 0 WHEN 'needs_revision' THEN 1 ELSE 2 END,
               updated_at DESC,bundle_id
      LIMIT 200`,
  )
  if (!result.ok) {
    return { available: true, correctionsAvailable, bundles: [], error: result.error }
  }

  const bundles = result.rows.map((row): TopologyBundle => {
    const context = jsonValue<TopologyBundle["context"]>(row.context, {})
    const receiptResult = skeletonResult(row.result)
    const correction = correctionValue(row.correction)
    const skeleton = applyCorrection(receiptResult, correction)
    const workKind = String(
      row.work_kind ?? "neighborhood_synthesis",
    ) as TopologyBundle["workKind"]
    return {
      bundleId: String(row.bundle_id ?? ""),
      bundleKey: String(row.bundle_key ?? ""),
      canonicalName: canonicalTreeName(skeleton, workKind),
      status: String(row.status ?? "proposed") as TopologyBundleStatus,
      workKind,
      scopeKind: String(row.scope_kind ?? "excavation_unit") as TopologyBundle["scopeKind"],
      scopeId: String(row.scope_id ?? ""),
      planSha256: String(row.plan_sha256 ?? ""),
      workId: String(row.work_id ?? ""),
      sourceCount: Number(row.source_count ?? 0),
      sourceKeys: jsonValue<string[]>(row.source_keys, []),
      sources: jsonValue<TopologySource[]>(row.sources, context.sources ?? []),
      nodeCount: skeleton.nodes.length,
      bindingCount: skeleton.bindings.length,
      edgeCount: skeleton.edges.length,
      unboundPopulationCount: skeleton.unbound_population_ids.length,
      averageConfidence: numberOrNull(row.average_confidence),
      minimumConfidence: numberOrNull(row.minimum_confidence),
      promptTokens: Number(row.prompt_tokens ?? 0),
      completionTokens: Number(row.completion_tokens ?? 0),
      totalTokens: Number(row.total_tokens ?? 0),
      cost: Number(row.cost ?? 0),
      modelVersions: jsonValue<string[]>(row.model_versions, []),
      workerVersion: String(row.worker_version ?? ""),
      promptContractVersion: String(row.prompt_contract_version ?? ""),
      inputPacketSha256: String(row.input_packet_sha256 ?? ""),
      completedAt: String(row.completed_at ?? ""),
      proposedAt: String(row.proposed_at ?? ""),
      updatedAt: String(row.updated_at ?? ""),
      validation: jsonValue<Record<string, unknown>>(row.validation, {}),
      receiptResult,
      // Bridge receipts deliberately do not have skeleton arrays. Normalize
      // their absent collections so a future include-bridges run cannot crash
      // this WIP debugger while its dedicated bridge view is still evolving.
      result: skeleton,
      correctionRevision: Number(row.correction_revision ?? 0),
      correctionState:
        row.correction_state === "draft" || row.correction_state === "complete"
          ? row.correction_state
          : null,
      correction,
      correctedBy: typeof row.corrected_by === "string" ? row.corrected_by : null,
      correctedAt: typeof row.corrected_at === "string" ? row.corrected_at : null,
      context,
      executionReceipts: jsonValue<TopologyExecutionReceipt[]>(row.execution_receipts, []),
    }
  })
  return { available: true, correctionsAvailable, bundles }
}

export async function fetchBusinessValueGraphs(
  connectionId: string,
  workflowRunId?: string | null,
): Promise<BusinessValueGraphState> {
  const probe = await runQuery(
    connectionId,
    `SELECT to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL AS available,
            to_regclass('rvbbit.business_value_graph_current') IS NOT NULL AS current_available,
            to_regclass('rvbbit.business_value_graph_promotions') IS NOT NULL AS promotions_available,
            to_regclass('rvbbit.business_semantic_dossiers') IS NOT NULL AS dossiers_available,
            to_regclass('rvbbit.business_semantic_dimension_census_current') IS NOT NULL AS dimension_census_available,
            to_regclass('rvbbit.business_semantic_family_relationship_current') IS NOT NULL AS family_relationship_available`,
    { rowLimit: 1 },
  )
  if (!probe.ok) return { available: false, graphs: [], error: probe.error }
  if (probe.rows[0]?.available !== true) return { available: false, graphs: [] }

  const promotionProjection = probe.rows[0]?.promotions_available === true
    ? `promotion_id::text,cube_name,cube_version,materialization_grain,
       metric_versions,promotion_receipt,promoted_by,promoted_at,last_refreshed_at`
    : `NULL::text AS promotion_id,NULL::text AS cube_name,NULL::integer AS cube_version,
       NULL::text AS materialization_grain,NULL::jsonb AS metric_versions,
       NULL::jsonb AS promotion_receipt,NULL::text AS promoted_by,
       NULL::timestamptz AS promoted_at,NULL::timestamptz AS last_refreshed_at`
  const graphSource = !workflowRunId && probe.rows[0]?.current_available === true
    ? "rvbbit.business_value_graph_current"
    : "rvbbit.business_value_graph_summary"
  const censusProjection = probe.rows[0]?.dimension_census_available === true
    ? `coalesce((SELECT jsonb_agg(census.census ORDER BY census.metric_key,census.dimension_key)
                   FROM rvbbit.business_semantic_dimension_census_current census
                  WHERE census.graph_id=source.graph_id),'[]'::jsonb)`
    : `'[]'::jsonb`
  const familyRelationshipProjection = probe.rows[0]?.family_relationship_available === true
    ? `coalesce((SELECT jsonb_agg(relationship.relationship ORDER BY relationship.confidence DESC,relationship.relationship_id)
                   FROM rvbbit.business_semantic_family_relationship_current relationship
                  WHERE relationship.source_family_id=dossier.family_id
                     OR relationship.target_family_id=dossier.family_id),'[]'::jsonb)`
    : `'[]'::jsonb`
  const dossierProjection = probe.rows[0]?.dossiers_available === true
    ? `(SELECT dossier.dossier || jsonb_build_object(
              'dimension_censuses',${censusProjection},
              'family_relationships',${familyRelationshipProjection})
          FROM rvbbit.business_semantic_dossiers dossier
         WHERE dossier.graph_id=source.graph_id) AS semantic_dossier`
    : `NULL::jsonb AS semantic_dossier`
  const result = await runQuery(
    connectionId,
    `SELECT graph_id::text,workflow_run_id::text,domain_key,name,summary,status,
            source_relations,primary_metric_key,reconciled,fallback,graph,receipt,
            proposed_at,updated_at,${promotionProjection},${dossierProjection}
       FROM ${graphSource} source
      ${workflowRunId
        ? `WHERE workflow_run_id=${sqlLiteral(workflowRunId)}::uuid`
        : graphSource.endsWith("_current") ? "" : "WHERE lifecycle IN ('canonical','provisional')"}
      ORDER BY CASE status WHEN 'proposed' THEN 0 WHEN 'needs_revision' THEN 1 ELSE 2 END,
               updated_at DESC,domain_key
      LIMIT 100`,
    { rowLimit: 100 },
  )
  if (!result.ok) return { available: true, graphs: [], error: result.error }

  const graphs = result.rows.map((row): BusinessValueGraph => {
    const graph = jsonValue<Record<string, unknown>>(row.graph, {})
    const receipt = jsonValue<Record<string, unknown>>(row.receipt, {})
    const evaluation = jsonValue<Record<string, unknown>>(receipt.evaluation, {})
    return {
      graphId: String(row.graph_id ?? ""),
      workflowRunId: String(row.workflow_run_id ?? ""),
      domainKey: String(row.domain_key ?? ""),
      name: String(row.name ?? row.domain_key ?? "Value system"),
      summary: String(row.summary ?? ""),
      status: String(row.status ?? "proposed") as BusinessValueGraphStatus,
      sourceRelations: jsonValue<string[]>(row.source_relations, []),
      primaryMetricKey: textOrNull(row.primary_metric_key),
      metrics: Array.isArray(graph.metrics) ? (graph.metrics as BusinessValueMetric[]) : [],
      edges: Array.isArray(graph.edges) ? (graph.edges as BusinessValueEdge[]) : [],
      openQuestions: Array.isArray(graph.open_questions) ? (graph.open_questions as string[]) : [],
      businessDomain:
        graph.business_domain && typeof graph.business_domain === "object"
          ? (graph.business_domain as BusinessDomainSummary)
          : null,
      valueSystemKey: String(graph.value_system_key ?? row.domain_key ?? ""),
      concepts: Array.isArray(graph.concepts) ? (graph.concepts as BusinessConcept[]) : [],
      processes: Array.isArray(graph.processes) ? (graph.processes as BusinessProcess[]) : [],
      sourceMemberships: Array.isArray(graph.source_memberships)
        ? (graph.source_memberships as BusinessSourceMembership[])
        : [],
      semanticRelationships: Array.isArray(graph.semantic_relationships)
        ? (graph.semantic_relationships as BusinessSemanticRelationship[])
        : [],
      analyticalQuestions: Array.isArray(graph.analytical_questions)
        ? (graph.analytical_questions as string[])
        : [],
      gaps: Array.isArray(graph.gaps) ? (graph.gaps as string[]) : [],
      evaluations: Array.isArray(evaluation.metrics)
        ? (evaluation.metrics as BusinessValueMetricEvaluation[])
        : [],
      reconciled: row.reconciled === true,
      fallback: row.fallback === true,
      semanticDossier: semanticDossier(row.semantic_dossier),
      promotion: row.promotion_id ? {
        promotionId: String(row.promotion_id),
        cubeName: String(row.cube_name ?? ""),
        cubeVersion: Number(row.cube_version ?? 0),
        materializationGrain: String(row.materialization_grain ?? "month") as BusinessValuePromotion["materializationGrain"],
        metricVersions: jsonValue<Record<string, number>>(row.metric_versions, {}),
        receipt: jsonValue<BusinessValuePromotion["receipt"]>(row.promotion_receipt, {}),
        promotedBy: String(row.promoted_by ?? ""),
        promotedAt: String(row.promoted_at ?? ""),
        lastRefreshedAt: String(row.last_refreshed_at ?? ""),
      } : null,
      proposedAt: String(row.proposed_at ?? ""),
      updatedAt: String(row.updated_at ?? ""),
    }
  })
  return { available: true, graphs }
}

function semanticDossier(value: unknown): BusinessSemanticDossier | null {
  const raw = jsonValue<Record<string, unknown> | null>(value, null)
  if (!raw || raw.schema_version !== "rvbbit.business-semantic.dossier.v1") return null
  const assertions = Array.isArray(raw.assertions)
    ? (raw.assertions as Record<string, unknown>[])
    : []
  const watches = Array.isArray(raw.watches)
    ? (raw.watches as Record<string, unknown>[])
    : []
  const dimensionCensuses = Array.isArray(raw.dimension_censuses)
    ? (raw.dimension_censuses as Record<string, unknown>[])
    : []
  const familyRelationships = Array.isArray(raw.family_relationships)
    ? (raw.family_relationships as Record<string, unknown>[])
    : []
  return {
    familyId: String(raw.family_id ?? ""),
    graphId: String(raw.graph_id ?? ""),
    workflowRunId: String(raw.workflow_run_id ?? ""),
    revision: Number(raw.revision ?? 0),
    predecessorGraphId: textOrNull(raw.predecessor_graph_id),
    assertionCount: Number(raw.assertion_count ?? assertions.length),
    watchableAssertionCount: Number(raw.watchable_assertion_count ?? watches.length),
    assertionKinds: jsonValue<Record<string, number>>(raw.assertion_kinds, {}),
    automatedWatchCoverage: Number(raw.automated_watch_coverage ?? 0),
    watchCount: Number(raw.watch_count ?? watches.length),
    watchStates: jsonValue<Record<string, number>>(raw.watch_states, {}),
    needsRevisionCount: Number(raw.needs_revision_count ?? 0),
    needsDecisionCount: Number(raw.needs_decision_count ?? 0),
    assertions: assertions.map((assertion) => ({
      assertionId: String(assertion.assertion_id ?? ""),
      assertionKey: String(assertion.assertion_key ?? ""),
      kind: String(assertion.kind ?? ""),
      subjectKey: String(assertion.subject_key ?? ""),
      predicate: String(assertion.predicate ?? ""),
      value: jsonValue<Record<string, unknown>>(assertion.value, {}),
      confidence: Number(assertion.confidence ?? 0),
      effectiveConfidence: Number(assertion.effective_confidence ?? assertion.confidence ?? 0),
      validationState: String(assertion.validation_state ?? "unobserved") as BusinessSemanticAssertion["validationState"],
      reviewState: String(assertion.review_state ?? "normal") as BusinessSemanticAssertion["reviewState"],
      observationCount: Number(assertion.observation_count ?? 0),
      lastObservedAt: textOrNull(assertion.last_observed_at),
      provenance: String(assertion.provenance ?? ""),
      evidence: jsonValue<Record<string, unknown>>(assertion.evidence, {}),
    })),
    watches: watches.map((watch) => ({
      watchId: String(watch.watch_id ?? ""),
      watchKey: String(watch.watch_key ?? ""),
      kind: String(watch.kind ?? "source_presence") as BusinessSemanticWatch["kind"],
      assertionId: String(watch.assertion_id ?? ""),
      state: String(watch.state ?? "unknown") as BusinessSemanticWatch["state"],
      enabled: watch.enabled !== false,
      specification: jsonValue<Record<string, unknown>>(watch.specification, {}),
      evidence: jsonValue<Record<string, unknown>>(watch.evidence, {}),
      observationCount: Number(watch.observation_count ?? 0),
      lastObservedAt: textOrNull(watch.last_observed_at),
      nextDueAt: textOrNull(watch.next_due_at),
      consecutivePasses: Number(watch.consecutive_passes ?? 0),
      consecutiveFailures: Number(watch.consecutive_failures ?? 0),
    })),
    dimensionCensuses: dimensionCensuses.map((census) => ({
      censusId: Number(census.census_id ?? 0),
      metricKey: String(census.metric_key ?? ""),
      dimensionKey: String(census.dimension_key ?? ""),
      coverageMode: String(census.coverage_mode ?? "bounded_sample") as BusinessSemanticDimensionCensus["coverageMode"],
      coverageComplete: census.coverage_complete === true,
      memberListComplete: census.member_list_complete === true,
      rowLimit: Number(census.row_limit ?? 0),
      rowsObserved: Number(census.rows_observed ?? 0),
      memberCount: Number(census.member_count ?? 0),
      members: jsonValue<BusinessSemanticDimensionMember[]>(census.members, []),
      tailMemberCount: Number(census.tail_member_count ?? 0),
      tailRows: Number(census.tail_rows ?? 0),
      totalMetricValue: numberOrNull(census.total_metric_value),
      baselineCensusId: numberOrNull(census.baseline_census_id),
      addedValues: jsonValue<string[]>(census.added_values, []),
      removedValues: jsonValue<string[]>(census.removed_values, []),
      taxonomyChanged: census.taxonomy_changed === true,
      triggerKind: String(census.trigger_kind ?? ""),
      observedAt: textOrNull(census.observed_at),
    })),
    familyRelationships: familyRelationships.map((relationship) => ({
      relationshipId: Number(relationship.relationship_id ?? 0),
      workflowRunId: String(relationship.workflow_run_id ?? ""),
      sourceGraphId: String(relationship.source_graph_id ?? ""),
      sourceFamilyId: textOrNull(relationship.source_family_id),
      sourceValueSystemKey: String(relationship.source_value_system_key ?? ""),
      sourceValueSystemName: String(relationship.source_value_system_name ?? ""),
      sourceBusinessDomainKey: String(relationship.source_business_domain_key ?? ""),
      sourceBusinessDomainName: String(relationship.source_business_domain_name ?? ""),
      targetGraphId: String(relationship.target_graph_id ?? ""),
      targetFamilyId: textOrNull(relationship.target_family_id),
      targetValueSystemKey: String(relationship.target_value_system_key ?? ""),
      targetValueSystemName: String(relationship.target_value_system_name ?? ""),
      targetBusinessDomainKey: String(relationship.target_business_domain_key ?? ""),
      targetBusinessDomainName: String(relationship.target_business_domain_name ?? ""),
      relationshipKind: String(relationship.relationship_kind ?? "abstain") as BusinessSemanticFamilyRelationship["relationshipKind"],
      confidence: Number(relationship.confidence ?? 0),
      proposedBusinessDomainKey: textOrNull(relationship.proposed_business_domain_key),
      proposedBusinessDomainName: textOrNull(relationship.proposed_business_domain_name),
      proposedFamilyKey: textOrNull(relationship.proposed_family_key),
      proposedFamilyName: textOrNull(relationship.proposed_family_name),
      rationale: String(relationship.rationale ?? ""),
      evidence: jsonValue<Record<string, unknown>>(relationship.evidence, {}),
      candidateFingerprint: String(relationship.candidate_fingerprint ?? ""),
      adjudicationVersion: String(relationship.adjudication_version ?? ""),
      reviewState: String(relationship.review_state ?? "proposed") as BusinessSemanticFamilyRelationship["reviewState"],
      reviewNote: textOrNull(relationship.review_note),
      decidedBy: textOrNull(relationship.decided_by),
      decidedAt: textOrNull(relationship.decided_at),
      proposedBy: String(relationship.proposed_by ?? ""),
      proposedAt: String(relationship.proposed_at ?? ""),
    })),
    diff: jsonValue<BusinessSemanticDossier["diff"]>(raw.diff, {}),
  }
}

export async function runBusinessSemanticWatches(
  connectionId: string,
  graphId: string,
): Promise<{
  ok: true
  receipt: { observed?: number; passing?: number; failing?: number; unknown?: number }
} | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_semantic_run_dossier_watches(
       ${sqlLiteral(graphId)}::uuid,'datarabbit',50
     ) AS receipt`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  return {
    ok: true,
    receipt: jsonValue<Record<string, number>>(result.rows[0]?.receipt, {}),
  }
}

export async function decideBusinessSemanticFamilyRelationship(
  connectionId: string,
  relationshipId: number,
  decision: "accepted" | "rejected",
  note?: string,
): Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; error: string }> {
  const normalizedId = Math.max(0, Math.trunc(relationshipId))
  if (!normalizedId) return { ok: false, error: "The family relationship is missing an identifier." }
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_semantic_decide_family_relationship(
       ${normalizedId},${sqlLiteral(decision)},${note?.trim() ? sqlLiteral(note.trim()) : "NULL"}
     ) AS receipt`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  return {
    ok: true,
    receipt: jsonValue<Record<string, unknown>>(result.rows[0]?.receipt, {}),
  }
}

export async function promoteBusinessValueGraph(
  connectionId: string,
  graphId: string,
  options: { cubeName?: string; grain?: string; reviewReason?: string } = {},
): Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_value_promote_graph(
       ${sqlLiteral(graphId)}::uuid,
       ${options.cubeName?.trim() ? sqlLiteral(options.cubeName.trim()) : "NULL"},
       ${options.grain?.trim() ? sqlLiteral(options.grain.trim()) : "NULL"},
       ${options.reviewReason?.trim() ? sqlLiteral(options.reviewReason.trim()) : "NULL"}
     ) AS receipt`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  return {
    ok: true,
    receipt: jsonValue<Record<string, unknown>>(result.rows[0]?.receipt, {}),
  }
}

export async function refreshBusinessValuePromotion(
  connectionId: string,
  graphId: string,
): Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_value_refresh_promotion(${sqlLiteral(graphId)}::uuid) AS receipt`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  return {
    ok: true,
    receipt: jsonValue<Record<string, unknown>>(result.rows[0]?.receipt, {}),
  }
}

function sqlLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`
}

export async function saveBusinessTopologyCorrection(
  connectionId: string,
  bundleId: string,
  correction: TopologyBundleCorrection,
  correctionState: TopologyCorrectionState,
  expectedRevision: number,
): Promise<{ ok: true; revision: number } | { ok: false; error: string }> {
  const result = await runQuery(
    connectionId,
    `SELECT rvbbit.business_topology_save_bundle_correction(
        ${sqlLiteral(bundleId)}::uuid,
        ${sqlLiteral(JSON.stringify(correction))}::jsonb,
        ${sqlLiteral(correctionState)},
        current_user,
        ${Math.max(0, Math.trunc(expectedRevision))}
      ) AS saved`,
    { readOnly: false, rowLimit: 1 },
  )
  if (!result.ok) return result
  const saved = jsonValue<Record<string, unknown>>(result.rows[0]?.saved, {})
  return { ok: true, revision: Number(saved.revision ?? expectedRevision + 1) }
}
