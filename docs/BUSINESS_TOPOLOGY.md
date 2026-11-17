# Business Topology

Business Topology is RVBBIT's living, evidence-backed map of the business
concepts represented across customer data. It is intentionally complementary
to both existing knowledge graphs:

| Layer | Answers | Example |
|---|---|---|
| Catalog KG | What physical data exists? | `crm.people.email` is a text field. |
| Business Topology | What business concepts and facets appear where? | Customer identity appears in CRM, billing, and support data. |
| Business Value Graph | What outcome does this data explain, what composes it, and what changed? | Revenue rose because two product categories grew while one contracted. |
| Fact / semantic KG | What concrete claims have been observed? | Alice is enrolled in BIO-101. |

The promoted topology should remain compact enough to render as a hierarchy or
"company skeleton." Cross-links are retained, but the default product surface
must not become an undifferentiated network diagram.

## Status

Migration `0279_business_topology_foundation` ships the first core slice:

- Generic source and semantic-population registry.
- Bounded PostgreSQL relation profiling.
- Immutable profile snapshots and exact model-packet receipts.
- Installation-salted local value fingerprints.
- Inverted-index overlap candidate generation.
- Versioned inference queue and lease/complete worker contract.
- Proposal/evidence/review lifecycle.
- Human-promoted topology nodes, population bindings, and cross-links.
- A readable `rvbbit.business_topology_skeleton` view.

Migration `0282_business_topology_proposal_bundles` adds whole-skeleton shadow
receipts for the native DataRabbit debugger. Migration
`0284_business_topology_bundle_corrections` adds append-only human correction
overlays. Migration `0285_business_topology_workflows` adds the durable SQL
control plane for a complete, resumable excavation and the standing
Warehouse MCP executor that performs it. It does **not** yet ship a trained
topology specialist, a Calliope
company-skeleton projection, transactional bundle promotion, or a profiler for
accepted composite/slice/document populations. Those remain later layers.

Migration `0296_business_value_graph` adds a domain-level shadow pass above the
object proposals. It produces a few executable outcome/component/driver
definitions, contribution decompositions, period-over-period change graphs,
and concrete metric/cube recommendations. Migration
`0341_business_value_system_families` separates provisional semantic use from
canonical materialization: a deterministically reconciled first pass is usable
immediately and visibly labeled provisional, but it is not silently promoted
into governed metrics or cubes.

Migration `0350_business_semantic_ledger` makes those repeated observations
inspectable at assertion granularity. Each reconciled graph revision is
normalized into typed domain, concept, process, membership, relationship,
metric, question, and gap assertions. A bounded dossier exposes their evidence,
confidence, predecessor diff, and deterministic validation-watch coverage.
Source-presence and existing metric reconciliation evidence are represented
immediately; exact joins receive an explicit pending coverage/fanout watch.
Comparisons never receive join watches. The first slice defines and exposes the
watch plan.

Migration `0351_business_semantic_watch_execution` executes that plan without
another model call. It retains append-only measurements for bounded source
presence, exact-join coverage/fanout, and metric/dimension reconciliation;
updates a separate effective-confidence and review-state projection; and leaves
the inferred assertion untouched. The Dossier surface exposes the actual check
evidence and a **Run checks** action. Hosted installs also run at most 20 due
watches hourly, while individual source/metric watches default to six-hour
cadence and exact joins to twelve hours.

Migration `0352_business_semantic_dimension_census` prevents a bounded profile
or a two-period chart from masquerading as a complete category dictionary.
Every executed metric watch now records an append-only census for its declared
dimensions. A projection with at most the configured row bound is labeled
`exact`; larger projections remain explicitly `bounded_sample`. Complete
low-cardinality dictionaries retain every member with all-time count and
first/last observation, so a rare historical value remains visible even when
it contributes zero to the current comparison period. Consecutive complete
censuses also record added and removed categories and move the metric assertion
to `warning / needs_revision` without silently rewriting it. DataRabbit keeps
period-local movement separate from all-history contribution and displays the
coverage mode. Subsequent topology runs receive this distilled evidence;
sampled member values are withheld from inference, while exact dictionaries
can be reused and taxonomy drift bypasses the semantic cache for review.

### Operational TODO: resumable provider throttling

The profiler, source embeddings, work plan, and per-item execution receipts are
already durable, but a full excavation can still fail after several minutes
when the configured generative provider stays rate-limited. The 2026-08-25
hosted failure completed profiling and embeddings, planned 631 items / 115
model calls, then surfaced an OpenRouter Nemotron HTTP 429 through Hutch as a
502 after only three short transport retries. Smaller excavations failed by the
same path; this is not the earlier missing-embedding repair.

Before treating unattended Business Topology runs as reliable, add a bounded
long-running transient policy that:

- honors `Retry-After` and uses exponential backoff with jitter for 429 and
  retryable 5xx responses;
- resumes from completed per-item receipts instead of replaying the run;
- exposes retry state, provider route, next attempt, and terminal cause in the
  workflow receipt; and
- optionally advances to an explicitly configured compatible fallback route,
  without silently changing the topology model contract.

Exercise the policy with deterministic 429/502 tests and one full hosted run.
Until then, a provider throttle is a visible failed run, not a silently partial
topology promotion.

### Review and promotion

A reconciled, non-fallback Value Graph can be promoted from the DataRabbit
inspector or directly in SQL. Promotion is one atomic operation over the native
RVBBIT primitives:

```sql
SELECT rvbbit.business_value_promote_graph(
  '<graph-id>'::uuid,
  'commerce_value',
  'month',
  'Reviewed with Finance; revenue and dimensional splits reconcile.'
);
```

This creates `cubes.commerce_value` through `rvbbit.define_cube`, so the compact
period × metric × full-dimension aggregate is a normal `USING rvbbit` table. It
also creates one versioned `rvbbit.define_metric` definition per validated graph
metric. The governed definitions read the compact cube rather than rescanning
the source events. Existing Value Graph series, contribution, and change
functions switch to the cube automatically after promotion.

`rvbbit.business_value_graph_promotions` retains the original graph, the full
evaluation/model receipt, generated cube SQL, metric versions, source and
aggregate row counts, and an exact per-metric reconciliation. If any metric
total or event count differs from the evaluated proposal, the transaction is
rolled back. Refresh the materialization after mirrors update with:

```sql
SELECT rvbbit.business_value_refresh_promotion('<graph-id>'::uuid);
```

## Business Context retrieval

Migration `0298_business_context_retrieval` turns reconciled Value Graphs into
a compact, rebuildable semantic projection for Calliope. It does not copy raw
rows or replace the proposal and promotion receipts. It indexes three kinds:

- `business_value_system`: the domain outcome, metric set, relationships,
  source relations, governance state, and promoted cube;
- `business_metric`: meaning, formula, unit, grain, time semantics, validated
  dimensions, and governed metric handle when promoted;
- `business_object`: a validated contributor/dimension through which a metric
  can be explained.

Proposal and promotion changes mark the projection stale. The first
`search_business_context` or blended `search_data` call rebuilds it under an
advisory lock, including Clover embeddings when available. This keeps schema
migrations and workflow completion fast while making retrieval self-healing.

Two lanes touch the projection and they must not fight. The five-minute
`business_context_maintenance_tick` pre-warms a stale projection with
`business_context_crawl(false)` — lexical documents only, no remote embedder
call — so cron never blocks on Clover. Migration
`0322_business_context_embedding_repair` makes that pre-warm non-destructive
and convergent: the projection upsert keeps an existing vector whenever the
document text is unchanged (only changed text invalidates it), and the tick
then calls `business_context_embed_missing(200)` to embed a bounded slice of
whatever still lacks a vector. `business_context_embed_pending()` is the
independent signal for that state; `business_context_search_stale()` remains
purely structural (dirty/crawled/empty). Warehouse (`_ensure_business_context_fresh`)
and the notebook packet run the same repair on read. Before 0322 a no-embed
pre-warm overwrote every vector with NULL and nothing embedded them again, so
`business_context_search` returned nothing while `business_context_state`
reported `crawled_at` fresh — check `embedded_count` against `node_count`
whenever semantic business retrieval looks empty.

Retrieval scores are rank-normalized: `business_context_search` (via
`data_search`) reports the best hit of *any* query as 1.0, and the dense floor
is permissive, so the projection always answers with its nearest neighbours.
The blended `search_data` therefore does not promote business hits by kind
alone. It reads the absolute signal once more (`dense_knn_tiered` cosine on
the cached query vector plus `catalog_lexical_knn`) and only keeps a business
hit in the top tier when the cosine clears
`CALLIOPE_BUSINESS_CONTEXT_MATCH_FLOOR` (default 0.30; measured on the hosted
appliance, related nodes score 0.33–0.42 and unrelated 0.15–0.26) or the
lexical ranker matched; weak hits sort after tables. Both tools attach a
`match` block (`cosine`, `lexical`, `strength`) to business results, and
`search_business_context` adds a note when nothing matched strongly, so an
agent asking about an unmodelled subject is pointed at raw tables instead of
being handed a confident-looking Value System.

Calliope's intended reasoning path is:

1. `search_data` or `search_business_context` finds the relevant Value System;
2. `get_business_value_system` retrieves formulas, relationships, dimensions,
   lineage, proof, and canonical handles;
3. `metric()` evaluates a promoted canonical total;
4. `business_metric_change()` attributes movement through one validated
   contributor without rebuilding joins or arithmetic from raw tables.

Reconciled systems marked `usable` are immediately available as explicitly
provisional business context for exploration and explanation. They retain
their source and validation receipts, but only promoted systems provide
canonical metric and cube handles.

## Topology Steward and Change Graph

Migration `0299_business_context_steward`, extended by migration `0343`, keeps
that interpretation current without repeatedly paying for blind semantic
excavation. Physical schema scopes snapshot all eligible relations; semantic
Value Systems snapshot only the exact source relations in their latest graph.
Columns, comments, and constraints are fingerprinted. Row counts and changing
data values are deliberately excluded so ordinary mirror growth does not look
like schema drift. Mirrored-data changes are tracked separately and fan out to
the semantic domains sourced from that mirror, with a debounced refresh at most
once per domain per 20 hours.

Successful mirror runs mark their destination domain pending observation. A
successful catalog crawl computes the cheap fingerprint; only a difference
from the version last modeled queues the existing bounded, durable topology
workflow immediately. Hosted installs also seed four idempotent `pg_cron`
maintenance lanes:

- every five minutes, pre-warm a stale Business Context search index and drain
  one structurally dirty domain;
- every ten minutes, repair one mirror domain whose expected schema-scoped
  catalog crawl did not finish after its grace period;
- hourly, queue one quiet mirrored-data refresh that has not been semantically
  modeled in the preceding 20 hours; and
- hourly, check for out-of-band structural drift without clearing a mirror that
  is still awaiting catalog processing.

The normal mirror/catalog triggers remain the fast path; cron is the debounce
and recovery path. Workflow serialization remains unchanged, and completion
automatically drains the next dirty domain. A completed semantic refresh clears
its durable data-dirty marker; failed work remains eligible for a bounded retry.

When a refresh proposes a Value System revision, the steward reconciles it into
the domain's durable family before recording a `business_context_changes`
receipt. Metric aliases, display names, and day/month cube projections do not
create separate Value Systems. An exact semantic repeat becomes an observation
receipt; a newer provisional revision supersedes the older provisional head.
The receipt covers:

- added and removed source relations;
- added and removed metrics;
- formula, unit, grain, time-semantics, event-logic, and dimension changes;
- added and removed value relationships;
- a deterministic headline, change kind, severity, and review state.

Source event SQL is compared by hash and never included in the Change Graph.
Only a material conflict with an existing canonical graph remains
`pending_review`, and each family may have at most one such current candidate.
Routine first-pass and compatible revisions never become a human approval
inbox. A pending candidate may be used as explicitly provisional context, but
it cannot silently replace the promoted cube or governed metrics.

Migration `0353_business_semantic_family_consolidation` also compares changed
Value Systems with a deterministic shortlist of other current families. Hermes
receives compact semantic dossiers rather than a schema-wide table dump and
classifies each candidate as the same family, an extension, a parallel
population, an alternative definition, a material contradiction, a distinct
family, or insufficient evidence. The immutable proposal records its rationale,
blocking evidence, confidence, and model receipt. A separate append-only
accept/reject receipt lets operators teach later topology runs without silently
rewriting family identity or source data. Derived, staging, `_dlt`, and cube
relations may support lineage but cannot create a business identity.

Calliope uses `business_context_status`, `business_context_changes`, and
`refresh_business_context`. The SQL maintenance entry points are:

```sql
SELECT rvbbit.business_context_maintenance_tick();
SELECT rvbbit.business_context_catalog_repair_tick();
SELECT rvbbit.business_context_data_refresh_tick();
SELECT rvbbit.business_context_steward_safety_tick();
```

## Ordinary-chat feedback loop

Migration `0300_business_context_feedback_loop` closes the first consumption
loop. Before every substantive non-setup Calliope turn, Warehouse retrieves a
bounded packet of promoted Value Systems. A semantic or lexical match produces
a question-ranked packet; otherwise Calliope receives a tiny ambient index so
useful business meaning can surface even when the human does not know its name.
Ambient context is navigation only and is ignored when unrelated. Packets never
contain retained event SQL or promote a pending candidate.

Turn completion separates context availability from evidence-backed use.
`business_context_turn_receipts` records the exact bounded packet, matched
governed metrics, dimensions, source relations, tool summaries, query-surface
lineage, outcome, and possible user correction. It stores only a hash of the
question because the Calliope turn ledger already owns the prose. Normalized
rows in `business_context_turn_evidence` make those observations consumable by
the steward and a future proactive findings loop. Repeated usage influences the
ambient packet's ranking and is included as supporting evidence, but never
changes formula, unit, grain, or relationships by itself.

The chat receipt shows a compact Business section only when a promoted concept
actually contributed to the answer. A terse correction can attach to the
immediately preceding governed receipt; it becomes review evidence rather than
an automatic semantic mutation. Aggregated support is available in:

```sql
SELECT *
FROM rvbbit.business_context_evidence_summary
ORDER BY last_observed_at DESC;
```

Agents can inspect the same automatic preflight through
`business_context_for_question(question)`. This explicit tool is useful for a
mid-turn reframing; the Calliope notebook does not need to call it because the
packet is already supplied at turn start.

## Population, not table

The inference atom is a **population**, not a table:

- `field`: an automatically profiled atomic field.
- `record_context`: source neighborhood supplied to a model; never itself
  asserted as a business object.
- `composite`: a related field bundle such as customer identity.
- `slice`: a declarative subset such as contacts associated with active accounts.
- `event_stream`: recurring business events.
- `mention_set`: mentions of a concept in unstructured sources.
- `query_projection`: a stable derived projection.

One relation can contribute to many concepts. One concept can bind to many
populations across many systems. Accepted slice/composite selectors are
declarative JSON and may not contain executable SQL.

## Privacy and bounded work

`rvbbit.business_topology_profile_packet()` and all queued model packets have:

```json
{
  "privacy": {
    "raw_values": false,
    "value_hashes": false,
    "bounded_sample": true
  }
}
```

The relation profiler:

- Requires the caller to have `SELECT` on the relation.
- Materializes at most the requested sample limit (32–50,000 rows).
- Uses deterministic page sampling for large analyzed heaps and a bounded
  `LIMIT` fallback for unsupported/custom relation types.
- Never runs a source `count(*)` and never sorts a source by `random()`.
- Never persists raw sampled values.
- Classifies value shapes, repetition, cardinality, null behavior, type family,
  and weak role/sensitivity hints.
- Derives bounded field-pair behavior—co-presence, shared missingness, equality,
  and approximate directional dependency—from at most 48 sampled fields, then
  discards the sampled cells.

Top sampled values are normalized and hashed with an installation-local salt.
Those hashes live only in `business_topology_value_fingerprints`. They are used
inside the customer database to compute numeric overlap/containment features;
neither hashes nor raw values enter a Clover packet.

This is a privacy boundary, not a claim that salted hashes are anonymous data.
Access to the private fingerprint table should remain at the same administrative
trust level as the warehouse itself.

## First run

Profile selected sources without model work:

```sql
SELECT rvbbit.business_topology_excavation_run(
    p_relations      => ARRAY[
        'crm.contacts',
        'billing.accounts',
        'support.requesters'
    ],
    p_sample_rows    => 2048,
    p_enqueue        => false
);
```

Or enumerate caller-readable base/materialized relations outside system and
configured catalog-exclusion schemas:

```sql
SELECT rvbbit.business_topology_excavation_run(
    p_schemas       => ARRAY['crm','billing','support'],
    p_sample_rows   => 2048,
    p_max_relations => 200,
    p_enqueue       => true
);
```

`p_enqueue => true` creates:

- One `population_embedding` job per changed field/context packet.
- One `source_motifs` job per changed relation context. This job is explicitly
  allowed to propose several composites/slices within a relation.
- `correspondence` jobs for plausible value-overlap candidates after the sweep.

Unchanged profile and model-input hashes do not create snapshots or jobs.

Inspect the local deterministic baseline:

```sql
SELECT *
FROM rvbbit.business_topology_overlap_candidates(
    p_min_shared => 2,
    p_max_fanout => 20,
    p_limit      => 1000
);
```

An overlap candidate means only "worth scoring." It does not mean joinable,
same concept, or same instance.

## Full appliance-native excavation

Migration `0285_business_topology_workflows` turns the complete shadow pipeline
into one durable SQL job. The shortest way to make a fresh topology proposal
set is:

```sql
SELECT rvbbit.business_topology_start_workflow();
```

That call chooses the least-recently visited business schema, snapshots at most
24 least-recently visited relations from that one domain, queues one run, and
returns its UUID. Later no-scope runs rotate across schemas and then advance
within each schema. This avoids making one mixed first-100 alphabetical corpus
from unrelated mirrors. Repeating it while a run is queued, running, or
cancelling returns the same UUID rather than creating duplicate work. To scope
the excavation explicitly (and retain the caller's larger relation limit):

```sql
SELECT rvbbit.business_topology_start_workflow(
    p_schemas        => ARRAY['crm','billing','support'],
    p_sample_rows    => 2048,
    p_max_relations  => 200,
    p_max_work_items => 800,
    p_max_llm_calls  => 160,
    p_run_name       => 'Customer operations excavation'
);
```

`p_relations => ARRAY['crm.contacts','billing.accounts']` may be used instead
of schema discovery. Relation names are resolved and snapshotted when the job
is created, so a later search-path or catalog change cannot silently alter its
scope.

The standing Warehouse MCP worker leases the job and persists progress after
every major phase:

```sql
SELECT run_id,run_name,status,phase,
       relation_count,population_count,excavation_unit_count,
       completed_work_items,work_item_count,llm_attempts,bundles_staged,
       any_worker_online,error,updated_at
FROM rvbbit.business_topology_workflow_status
ORDER BY requested_at DESC
LIMIT 1;
```

During the post-excavation value-modeling phase, the run first asks the
configured Hermes judgment model to reconcile compact per-source findings into
a semantic v2 plan. The plan separates durable business domains from analytical
Value Systems, names concepts and processes first, and records many-to-many
source memberships. One relation may participate in several systems, and a
system may span schemas. Every source is either represented or explicitly
listed under `unassigned_relations` with a reason. Exact `joins_to` relationships
carry proven columns; `parallel_to` and `comparable_to` never masquerade as row
joins. The run's `progress` JSON publishes the business-domain and Value-System
counts, current system, membership/concept/process counts, unassigned sources,
cache hits, repair attempt, exact model-version receipt, and
`value_modeling_llm_attempts`.
The top-level `llm_attempts` is the combined excavation plus value-modeling
count, so a live run cannot appear idle while hidden domain calls continue.

Closing DataRabbit, refreshing the page, or restarting Warehouse MCP does not
cancel the run. Private extraction, embedding, plan, and execution receipts are
resumed from Warehouse's durable data volume; PostgreSQL remains the workflow
control plane. A cancellation request is also SQL-native:

```sql
SELECT rvbbit.business_topology_cancel_workflow('<run uuid>'::uuid);
```

The worker stops after the current bounded operation and records `cancelled`.
On success it stages only deterministically validated proposal bundles in
`business_topology_proposal_bundles`; it never accepts or materializes governed
topology. DataRabbit exposes the same contract under **Knowledge → Business
Topology**: **New excavation** starts the SQL job, the status strip polls its
durable progress, terminal state refreshes the bundle debugger automatically,
and the adjacent cancel control requests a safe stop.

The older `business_topology_excavation_run()` examples above remain useful for
profiling and the lower-level inference queue. They do not run the complete
neighborhood/plan/synthesis/bundle workflow by themselves.

## Business Value Graphs

The object topology is evidence for this layer, not its final presentation. A
schema is an automatic scan boundary and a table is a source boundary; neither
is a business-domain identity. The strong-model planning pass receives
catalog-distilled source cards rather than an undifferentiated schema dump.
Generated work, domain plans, and metric definitions are cached by semantic
source fingerprint in Warehouse's private durable volume. An unchanged rerun
reuses those judgments with zero model calls while re-executing metric SQL
against current rows. Volatile row estimates and profile statistics do not
invalidate the cache.

Promoted `cubes.*` relations are lineage-linked materializations of their
originating Value System. The `cubes` schema is excluded from excavation and a
cube name or time grain is not Value-System identity, so promotion cannot create
another business domain on a later run. The strong-model planning pass receives
distilled per-source object findings, column roles, exact foreign keys, and
shared-key join opportunities. It builds a small semantic graph:

- durable business domains contain concepts and processes;
- analytical Value Systems describe questions and outcomes within a domain;
- joinable facts, lookups, and dimensions carry exact typed relationships;
- parallel regional or application copies remain comparable without fake joins;
- source memberships may overlap across systems; and
- stable system keys reconcile equivalent systems across later runs.

Each resulting Value System gets its own bounded modeling call with at most four
metrics and twelve relationships; the worker no longer sends an entire schema
to one value-modeling prompt. A metric owns one canonical read-only PostgreSQL
event projection:

```text
occurred_at timestamptz, value numeric, dimensions jsonb
```

That one projection drives all three views:

- **Definition** records the business formula, grain, time semantics, source
  SQL, confidence, and unresolved assumptions.
- **Contribution** splits a period total across human-readable dimensions and
  verifies that every full split reconciles to the parent.
- **Change** compares two adjacent periods and ranks positive and negative
  contributors by their share of the net movement.

Opaque source IDs are join evidence, not business dimensions. The worker uses
declared foreign keys, exact shared keys, and the semantic plan's cited join
paths to replace them with bounded, low-cardinality labels such as product
category, channel, region, or promotion category. Joins are wrapped in a
grouped lookup so a dirty dimension relation cannot fan out the metric event
stream. Prices, rates, ratios, and percentages are marked non-additive and may
not be summed into invented amounts.

Domain planning, neighborhood synthesis, bridge synthesis, and value modeling
all use the configured Hermes judgment lane. Clover/Gemma is limited to the
mechanical per-source motif assembly lane. Long Hermes inference uses the
configurable `WAREHOUSE_HERMES_INFERENCE_TIMEOUT_SECONDS` deadline (900 seconds
by default); a timeout retries the same judgment lane and never silently falls
back to the small assembly model. Non-Hermes and assembly requests use the same
900-second default through `WAREHOUSE_BUSINESS_TOPOLOGY_INFERENCE_TIMEOUT_SECONDS`
(bounded to 240-3600 seconds), so no topology inference path retains the old
three- or four-minute cutoff.

Inspect staged proposals and their agent-facing metric catalog:

```sql
SELECT *
FROM rvbbit.business_value_graph_summary
ORDER BY updated_at DESC;

SELECT domain_name,metric_name,metric_kind,formula,grain,
       time_semantics,dimensions,evaluation
FROM rvbbit.business_value_metric_catalog
WHERE status='proposed';
```

The stored SQL remains executable for new periods, rather than freezing the
discovery-time sample. For example:

```sql
SELECT * FROM rvbbit.business_value_metric_change(
    '<graph uuid>'::uuid,
    'sales_revenue',
    'product_category',
    '2021-01-01','2022-01-01',
    '2022-01-01','2023-01-01',
    20
);
```

DataRabbit opens this as the default **Value systems** surface under
**Knowledge → Business Topology**. The older **Object map** remains available
for source-level debugging and correction.

## Clover/Hutch contracts

The intended first deployment adds two checkpoint families, not a new model
zoo:

1. `semantic_population_v1`
   - Consumes `rvbbit.business-topology.population.v1` and
     `rvbbit.business-topology.source-motifs.v1` packets.
   - Produces a contextual embedding, multi-label role probabilities,
     uncertainty, and declarative motif proposals.
2. `semantic_correspondence_v1`
   - Consumes `rvbbit.business-topology.correspondence.v1` packets.
   - Scores distinct verdicts such as `same_concept`, `same_facet`,
     `same_instance_key`, `joinable`, `attribute_of`, `event_about`,
     `measurement_of`, `correlated`, `unrelated`, and `abstain`.

Hutch should expose both as batched, `unlaned: true` specialist backends. They
still retain normal tenant authentication, metering, model-version receipts,
and would-be-cost records. The worker should be stateless: tenant topology state
and profile caches remain in PostgreSQL.

The first executable shadow worker now lives in
`bench/business_topology/worker.py` and is exposed as
`execute-excavation-plan`. It uses the registered Hutch-backed `clover_llm`
only for motif and synthesis work, while the precision-first local
correspondence floor handles the bounded pair volume. Each successful call is
written as a private, resumable receipt with the exact Hutch model-version
header and is validated before storage. It intentionally submits no proposals
and performs no topology DML; this keeps mixed-corpus assumption tests on the
safe side of the review ledger.

Wide sources retain their complete plan scope but do not force every field into
one generative call. When a unit exceeds its binding budget, the worker selects
an evidence-led synthesis frontier from correspondence results, then balances
remaining capacity across source motifs and sources. Prompt-only identifiers
are compact aliases; they are expanded before validation and never persist in
the result. Populations outside the frontier are deterministically included in
the result's unbound set. Each model receipt records the frontier's selected and
total counts plus a stable ID hash, so review can distinguish deliberate
bounded abstention from missing input.

A complete response that exceeds only the binding budget is projected back to
that budget with a deterministic precision-first policy. The projection first
checks all binding references, retains at least one strongest binding per node,
then ranks remaining candidates by confidence and structural role; omitted
populations become explicitly unbound. Its before/after counts are stored in
the execution receipt. Invalid references, malformed evidence, and impossible
node coverage are never repaired by this projection.

No new named Hutch backend is warranted for this shadow phase. A prompt routed
to the general Clover service is not a trained specialist. The first
`semantic_population_v1` or `semantic_correspondence_v1` deployment should be
an unlaned, versioned checkpoint that has cleared the held-out-family and
hard-negative gates below.

Claim work transactionally:

```sql
SELECT *
FROM rvbbit.business_topology_claim_inference_jobs(
    p_worker       => 'clover-topology-1',
    p_task_kinds   => ARRAY['population_embedding','source_motifs'],
    p_limit        => 64,
    p_lease_seconds=> 900
);
```

Complete with an exact model receipt:

```sql
SELECT rvbbit.business_topology_complete_inference_job(
    p_job_id       => '<job uuid>',
    p_succeeded    => true,
    p_model_name   => 'semantic-population',
    p_model_version=> 'semantic-population-v1.0.0',
    p_result       => '{"embedding_ref":"...","roles":{},"uncertainty":0.1}'
);
```

Expired worker leases are reclaimable. A profile change marks unconsumed work
for the prior profile stale, so an old verdict cannot silently become current.

## Governance

Inference results become proposals, not topology truth:

```sql
SELECT rvbbit.business_topology_propose(
    p_proposal_kind => 'node',
    p_payload        => '{
      "node_kind":"object",
      "name":"Customer",
      "description":"A customer known across operating systems"
    }',
    p_confidence     => 0.94,
    p_inference_kind => 'semantic_population_v1',
    p_source_job_id  => '<job uuid>',
    p_model_name     => 'semantic-population',
    p_model_version  => 'semantic-population-v1.0.0'
);
```

Evidence is attached independently with
`business_topology_add_proposal_evidence()`. A reviewer then calls
`business_topology_review_proposal()`. Accepted `population`, `node`, `binding`,
`edge`, `hierarchy`, and `authority` proposals materialize the governed map.
Identity-rule proposals remain ledger-only until the instance-linking contract
lands; acceptance must not silently invent a join.

## Training and evaluation pre-work

The reusable shadow-extraction, review-overlay, candidate-generation, and
baseline evaluation tooling lives in
[`bench/business_topology`](../bench/business_topology/README.md). It is
schema-agnostic after the source-adapter boundary: customer-specific concepts
and review decisions stay in private corpus overlays, while the public suite
contains only contracts and synthetic multi-system regressions.

The baseline has two independent recall paths: dual-channel semantic neighbors
(`focus` plus `context`) and installation-local value overlap. Their numeric
evidence merges before correspondence scoring; neither path asserts an edge.
PostgreSQL embedding and overlap adapters run in transactions that are
unconditionally rolled back, including temporary embedding-cache writes.

The overlap adapter has two bounded modes. Its broad inverted-index pass drops
fingerprints with excessive population fanout so ubiquitous flags and tiny
codes do not create a graph explosion. It is skipped once a corpus exceeds the
configured field-population budget. Independently nominated semantic or usage
pairs receive exact pair-local probes in timeout-isolated batches. A timed-out
global scan or probe batch produces a durable partial/degraded audit rather than
failing the topology workflow; semantic evidence still reaches planning. This
second pass bypasses the global fanout filter only for the strongest capped
nominations; raw values and installation-local fingerprints still never cross
the adapter.

The harness also proposes adaptive source neighborhoods from source-context
embeddings. This is a coarse search boundary, not a table-as-object assumption:
one neighborhood can contain several systems, one source can later yield many
business objects, and weak links are retained as non-merging bridges. Sources
that do not clear both absolute and installation-relative affinity gates remain
singletons. The same implementation accepts relational and non-relational
population packets and never reads private source-family controls during
inference.

Those neighborhoods can now be compiled into a bounded shadow excavation DAG.
Each source first receives independent multi-object motif work; cross-source
population comparisons are limited to its excavation unit; an internal
synthesis step proposes the unit's object skeleton; and only nominated bridge
or shard-boundary evidence reaches the final cross-unit synthesis stage.
Oversized connected components are graph-sharded by both source count and
non-context population count rather than allowed to create an unbounded model
prompt or response. This catches neighborhoods made from only a few very wide
relations. Sources remain intact, individually wide sources are flagged, and
cut affinities remain explicit work instead of disappearing.

The production planner intentionally excludes hard-negative and diversity
review probes. It accepts only semantic-neighbor, installation-local overlap,
or usage-supported correspondence candidates. All work remains proposal-only:
the shadow planner neither enqueues database jobs nor materializes nodes,
bindings, hierarchy, or edges.

Versioned neighborhood and bridge result validators enforce the other side of
that boundary. A neighborhood result must account for every scoped population
as bound or explicitly unbound, ground every node in at least one population,
give the entire proposed tree a short business-readable canonical name, cite
only prerequisite work, preserve an acyclic hierarchy, and contain no SQL, raw
values, fingerprints, or private controls. Bridge results may reference
only their bounded synthesis/node or population endpoints and are forbidden
from requesting an automatic unit merge.

Validated synthesis receipts are persisted whole in
`business_topology_proposal_bundles` for the DataRabbit review surface. A bundle
preserves its local node keys, bindings, hierarchy, edges, exact plan/worker/
prompt/model receipts, source context, and validation as one proposal. It is
explicitly not governed topology: the ledger supports revision, rejection, and
supersession, but has no acceptance/materialization state yet. Transactional
promotion will resolve all local node keys to durable UUIDs together in a later
contract rather than accepting dependent atomic proposals out of order.
The native DataRabbit WIP debugger lives under **Knowledge → Business
Topology** and exposes these bundles, their semantic hierarchy, unbound
populations, evidence, and execution receipts without offering a promotion
action. Canonical business names lead the bundle rail and tree header; source
table names remain visible only as provenance evidence.

Review corrections do not rewrite those receipts. Each save appends one full
overlay snapshot in `business_topology_bundle_corrections`, protected by an
expected-revision check. The overlay can rename and reparent receipt nodes,
change their kind or description, suppress an entire branch or one binding,
adjust binding role/authority, and record split/merge suggestions plus a human
note. DataRabbit projects only the latest revision over the immutable result.
Both `draft` and `complete` remain shadow states: `complete` means the reviewer
finished that correction pass, not that RVBBIT accepted or materialized it.

The PostgreSQL shadow adapter calls only
`business_topology_profile_packet()`, rolls its transaction back, and verifies
that topology ledger counts did not change. Because the bounded profiler uses
transaction-local temporary tables, a hardened shadow role needs `SELECT`,
`TEMP`, and packet-function `EXECUTE`, but no persistent DML privileges.

The first evaluation corpus should come from a non-persisting reference-deployment
shadow extraction, not from assumed schemas. Label a compact set of:

- Population role assignments.
- Same-concept and same-facet pairs.
- Joinable-but-not-same pairs.
- Same-instance-key candidates.
- Hard negatives with deceptively similar formats.
- Multi-object source motifs and accepted field bundles.

Useful weak supervision already exists in RVBBIT:

- Successful joins from actual Calliope SQL and route-shape history.
- Reused dashboards, artifacts, cubes, and promoted metrics.
- Exact/high-confidence local value overlap.
- Document entity links and accepted source mappings.
- Human accept/reject decisions from this proposal ledger.

Train precision-first with calibrated abstention. False semantic edges compound
more dangerously than unknown edges. Frontier LLM work should be limited to
cluster naming, explanations, and a small ambiguous frontier; it should not be
the bulk pair scorer.

One deployment is an assumption detector, not a universal training set. No
checkpoint may be promoted merely because it recalls one organization's known
bridges. Before deployment, repeat candidate-recall and held-out-family tests
against at least one structurally different corpus, including a non-relational
population adapter. Customer nouns, source names, and private labels must remain
outside the reusable feature and checkpoint contracts.

## Next implementation milestones

1. Review source-neighborhood bridges, excavation plans, private reference
   controls, multi-object motifs, and hard negatives.
2. Repeat the generalization gate on additional structurally different corpora.
3. Review and correct receipt-backed proposal bundles in the WIP DataRabbit
   debugger; use the append-only correction history as explicit error signal.
   Train and deploy the population/motif and correspondence specialists through
   Hutch only after their held-out promotion gates pass.
4. Add profile executors for accepted composite/slice/mention populations.
5. Feed successful Calliope joins and promotion decisions back as evidence.
6. Design transactional bundle promotion separately from correction editing,
   then add the Calliope company-skeleton projection.
7. Bind Living Pages, briefs, metrics, and query planning to accepted topology
   nodes while preserving their underlying source receipts.

`rvbbit.data_crawl_run()` remains a separate selective fact-extraction path. It
must not be scheduled as the topology profiler and should not become a source of
raw-row profile evidence.
