# Central model usage and Costs

DataRabbit Costs reads `rvbbit.cost_activity` when installed, with the legacy
semantic-receipt UI available on older databases. All panels use the same
category, time, model, operation and status filters. The view combines:

- Existing `cost_latest` semantic, decision, tool, title and agent receipts.
  The importer also runs native `settle_decision_costs`: batched Jev calls
  retain upstream cost and token counts in their receipt output even when the
  batch path leaves the generic counters empty. Settlement appends to the
  existing ledger, preserves receipt identity, and never repeats inference.
  Costs displays the actual Jev model from the receipt.
- Request-level Hermes ledger attempts, including retries and specialists.
  Matching old Calliope turn rollups are hidden from this view, preserved in
  their original ledger. Uncovered historical turns remain visibly uncosted.
- Meaning's latest attempt records. New completions preserve exact served
  model, provider usage and pricing evidence; older estimates remain labelled.
  Budget holds and uncertain failed-call reservations are **not spend**.
- Direct Business Topology completions. Cache hits do not issue completions.
  Historical receipts with provider IDs can be imported without counting
  copied/cached receipt files twice.
- Hermes auxiliary session/model/task totals (compression, title generation,
  vision and other recorded tasks), with their actual call count.
- Hindsight memory request metadata. The appliance enables tracing with a
  payload-stripping database trigger: input, output, error text, LLM info and
  arbitrary metadata are not retained. Token counts, model, operation, status
  and duration remain. The native trace retention is seven days; imported
  accounting rows persist independently.

`model_call_usage` retains request evidence and the price snapshot used. Its
primary key deduplicates imports. The importer commits complete JSONL lines and
its file cursor in the same database transaction under an advisory lock;
partial lines wait for the next pass. Corrupt records stop that file's batch
and produce a visible server log error. Inference does not depend on pricing.

`cost_kind` distinguishes `reported` provider charges, `estimated` usage value,
`included` subscription/managed-inference value, `reserved` budget, and
`unknown`. Provider charges are not a customer invoice.
`CALLIOPE_INCLUDED_INFERENCE_HOSTS` lists gateway hosts whose calls are prepaid,
comma-separated. It is empty by default: a self-hosted Clover gateway bills your
own provider key, so its calls count as real spend. Subscription provider IDs
are also recognized. No price is assigned to a routing alias unless an explicit
rate exists for it.

The public OpenRouter model catalog supplies cache-aware per-token tariffs;
no credentials, prompts or usage leave the appliance for that fetch. Fetch
period is at most daily, with hourly retry after outages. Existing
`rvbbit.model_rates` is the fallback. Every estimate keeps its source and fetch
time. Historical backfill uses the captured tariff, not a claim about the
historical invoice. Missing cache-specific rates use ordinary input price as
an explicit approximation. Existing estimates are stable when prices change;
previously unpriced requests can be enriched once a rate becomes available.

Hermes canonical input tokens exclude cache buckets, while prompt tokens
include them. Reasoning tokens are already part of output and are not added
again. Explicit upstream cost is preserved when Hermes' normalized response
retains it; otherwise the estimate remains clearly labelled.

Native subscription model IDs resolve to exact OpenRouter catalog IDs (for
example, `gpt-6-sol` on Codex resolves to `openai/gpt-6-sol`). Ambiguous names
and unknown versions remain unpriced. Each imported valuation also stores
`pricing.relative_cost_usd`: a token-price equivalent, separate from the
reported charge, including a reported zero. This supports comparing runs
across subscriptions and metered providers without implying a subscription bill.

The saved pricing evidence includes the catalog model, fetch time, original
catalog tariff, effective request tariff, cache read/write rates and context
tiers. Tiers use each request's total prompt including cached tokens, with the
catalog's strict greater-than threshold and override ordering. Auxiliary
aggregates lack per-request sizes, so they use the base tariff and explicitly
record `aggregate_base_rate_only`. Time-window overrides are not applied and
remain flagged in `unsupported_conditions`. Missing cache prices are listed
in `cache_rates_missing` rather than silently claiming a known discount.

Benchmark exports include a separate relative-cost subtotal and coverage count
for each attribution group. Unpriced activity is excluded, never treated as
free. Reserved budgets and prior revaluations are excluded from these totals.

## Operations

Warehouse installs the additive schema from `cost_accounting.sql` at startup.
No extension binary or database reset is required. The worker polls every ten
seconds. Calliope, DataRabbit and Hermes images carry the changes. Enable
Hindsight metadata tracing only after Warehouse has installed its stripping
trigger (`CALLIOPE_HINDSIGHT_METADATA_ONLY=true` in the bundled Compose).

One-time backfill, using the container's existing DSN:

```sh
docker exec calliope-server python /app/cost_accounting.py --backfill-topology
```

Useful read-only inspection:

```sql
SELECT category, cost_kind, sum(calls) AS calls, sum(cost_usd) AS usd
FROM rvbbit.cost_activity GROUP BY 1,2 ORDER BY 1,2;
SELECT * FROM rvbbit.model_usage_import_state;
```

## Coverage limits

Auxiliary rows are session/task aggregates, so their timeline uses the last
observation. Topology receipts without provider IDs cannot be safely deduplicated
for historical import and are skipped; legacy file timestamps are labelled.
Provider-internal retries and requests that fail before returning usage may
have unknown charges. Source-system aggregate tables are not added on top of
their constituent requests. Hindsight history from before tracing was enabled
cannot be reconstructed. Exact hosted Hutch billing reconciliation remains a
separate enrichment step; this change does not query another tenant's meter.

## Verification

`test_cost_accounting.py` covers cache arithmetic, reported zero, unknown rates,
transaction rollback, cursor replay, partial lines, turn-rollup suppression,
Meaning reservations, auxiliary aggregation, metadata stripping, topology copy
deduplication and later enrichment. Set `CALLIOPE_COST_TEST_DSN` to a disposable
appliance database for SQL integration tests. The Hermes image build exercises
real SDK hook payloads and the default-route runtime identity contract.
