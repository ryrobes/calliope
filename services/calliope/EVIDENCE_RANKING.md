# Search suggestion reranking

Floor's debounced search still shows eight company-evidence suggestions plus a
separate conversation lane. The shared evidence resolver now optionally searches
up to ten candidates each from Document Brain, artifacts, warehouse semantics,
and governed metrics before selecting results. Warehouse candidate selection uses
relevance instead of forcing cubes ahead of tables. Existing source permissions,
schema exclusions and lexical retrieval thresholds remain in effect.

`CALLIOPE_EVIDENCE_RANKING_MODE=select` is the default. It requires the existing
server `OPENROUTER_API_KEY`. `off` restores the previous retrieval/selection path;
`shadow` retrieves the wider pool and records judgment counts without applying
model ordering. Shadow/fallback source-balanced results can differ from `off`
because their retrieval pool is wider and warehouse retrieval is relevance-first.

One batched OpenRouter Decisions request (`typesafe/jev-1.13`) classifies each
candidate as direct, supporting, uncertain, or unrelated. The question includes
that candidate's bounded description; the shared state contains the search query.
Do not replace this with array-index references alone: local tests showed poor
grounding later in a 40-item array. No extra document reads, rows, chart series,
pixels, generated questions or agent turns are needed.

Ordering uses relevance category, provider confidence within useful categories,
then original order. Different documents precede repeated chunks within the same
category. Explicit Resolution Ledger bindings remain promoted. When a user opens
a suggestion, its ID is protected using freshly authorized source results even
if the provider subsequently fails. Candidates outside the final display limit
are temporary, not attached to the conversation.

The provider has a 2.8-second total deadline, no retries, and strict response
validation. Failures or a pool with no direct/supporting candidates retain the
deterministic source-balanced results. The existing browser keeps prior cards
visible during lookup; submitting a normal chat does not await this search.

Validated judgments are cached for 120 seconds in a bounded 128-entry process
cache. Keys cover caller, query, model/prompt version and candidate descriptors.
ACL checks and retrieval still happen first on every server request. No raw
candidate content is retained in this cache. Display limit is excluded from the
key so Floor and its expanded notebook share judgments. Provider inputs are
ordered by candidate ID, so tied hits arriving in a different order
reuse the same judgments; labels are mapped back by ID, never by stale position.
Non-content ranking
receipts include mode, status, latency, cache hit and category counts and survive
evidence normalization; application logs never include query/excerpt/provider
error bodies. No database migration is needed.

## Local validation, 2026-09-18

No live deployment or production database was used. The replay script is
`scripts/experiments/jev_evidence_ranking_trial.py`; provide an output path and
optionally an existing frozen retrieval JSON via `--frozen`.

Three intentionally constructed mixed-source cases placed a useful policy,
joinable invoice table, and existing metric below the old candidate cutoff.
The 16-candidate pools omitted all three. With 40 candidates, all three intended
items ranked first. Final candidate-grounded requests took 425–513 ms and cost
approximately $0.00048 each in that run (provider-reported usage). These are
synthetic coverage checks, not proof of improved real-user discovery.

Three previously frozen real document searches were also replayed locally.
One contained supporting project evidence; two contained no relevant candidates
according to Jev. This motivates preserving the baseline when no useful evidence
is found, and demonstrates the limit: reranking cannot fix missing retrieval.
Client excerpts and full provider replies remain in the local Downloads report,
not in the repository. Live end-to-end retrieval latency and click quality still
need evaluation after an authorized deployment.
