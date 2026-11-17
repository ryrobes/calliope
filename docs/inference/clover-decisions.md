# Clover semantic decisions

`rvbbit.truth`, `truth_score`, `choose`, and `clover_decide` use hosted Jev through
Clover's authenticated Hutch backend. No local GPU model is required. Existing
NLI/classifier operators are unchanged. These are model judgments, not SQL proofs;
a score is not a calibrated probability of correctness.

## Small SQL surface

```sql
SELECT id, rvbbit.truth(body, 'The customer explicitly asks to cancel')
FROM support_tickets;

-- Same cached inference; only the numeric projection/threshold differs.
SELECT rvbbit.truth_score('Please cancel my appointment', 'The customer explicitly asks to cancel');
SELECT rvbbit.truth('Please cancel my appointment', 'The customer explicitly asks to cancel', 0.8);

SELECT rvbbit.choose(body, ARRAY['billing', 'technical problem', 'cancellation', 'other'])
FROM support_tickets;

SELECT rvbbit.choose(
  jsonb_build_object('subject', subject, 'body', body),
  '{"blocked":"Cannot proceed without another person or missing information",
    "actionable":"Enough information exists to take the next step",
    "resolved":"The issue is explicitly settled",
    "unclear":"The supplied information does not establish a state"}'::jsonb
) FROM support_tickets;
```

Inputs accept text or JSONB objects/arrays. Classification takes 2–16 unique
labels, or a JSONB object mapping labels to descriptions. Include `other` or
`unclear` if those should be possible answers; the classifier chooses among the
supplied options. Boolean `truth` uses **score > threshold**, default 0.5.
SQL NULL inputs skip inference. Invalid helper arguments raise SQL errors.
Provider failures yield SQL NULL and the normal diagnostic/error receipt; they
never become `false` or a fallback label. A WHERE predicate naturally excludes
NULL, so select `truth_score` separately when you need to distinguish unavailable
judgments from negative ones.

## Full decisions and multiple questions

```sql
SELECT rvbbit.clover_decide(
  '{"subject":"Invoice 204","body":"Please explain this charge"}'::jsonb,
  '{"needs_reply":{"type":"noul","instructions":"Does this message ask for a response?"},
    "topic":{"type":"choice","instructions":"Classify the request",
      "criteria":{"billing":"Invoices, charges, or payments","other":"Anything else"}}}'::jsonb
);
```

The JSON result contains named `answers`, choice probabilities / boolean scores,
requested and actual model, contract version, and provider usage/cost. Up to
8 questions and 32 KiB of state per decision; oversize inputs are rejected, not
silently truncated. Instructions are bounded to 2,000 characters per question.
The fixed prompt boundary treats state as evidence, not instructions.

## Cache and bulk execution

All conveniences call the registered **`clover_decide`** operator, which uses
RVBBIT's existing L1 memory and L2 receipt cache. Keys include canonical input
JSON, questions/category definitions, operator model, and the versioned step
contract. Thresholds are outside the inference, so `truth_score` and `truth`
reuse the same decision. `text[]` categories and the equivalent JSONB definitions
also share results. Failed requests are not successful cache entries.

For a large scan, explicitly prewarm the core operator to collect, deduplicate,
and send batches (up to 8 states each). Ordinary convenience calls are scalar;
do not assume the implicit query rewriter recognizes nested SQL helpers.

```sql
SELECT * FROM rvbbit.prewarm_operator('clover_decide', $q$
  SELECT to_jsonb(body),
         rvbbit.truth_question('The customer explicitly asks to cancel')
  FROM support_tickets
  WHERE created_at >= CURRENT_DATE - 7
$q$);

-- Reads the warmed decisions, including across database connections.
SELECT id, rvbbit.truth(body, 'The customer explicitly asks to cancel')
FROM support_tickets
WHERE created_at >= CURRENT_DATE - 7;
```

For classifiers, prewarm with `rvbbit.choice_question(labels_or_definitions)` as
the second column. Prewarm query columns must have the operator's argument types
and order (here JSONB, JSONB); SQL NULL argument rows are skipped. No inference is
performed by the immutable question builders.

## Deployment / configuration

- Migration **0432_clover_decisions** installs the functions and default
  `decisions` backend; it preserves an existing backend of that name.
- The managed Clover pack contains the same SQL for new installs/reinstalls.
- PostgreSQL calls `<your Clover URL>/b/decisions/predict`, using
  `RVBBIT_CLOVER_KEY` like the other hosted operators.
- The CPU `decisions` service (`services/clover/decisions/`) ships in the
  release bundle as `clover-decisions`, routed by the Clover gateway. It uses
  `OPENROUTER_API_KEY`, receives only gateway traffic, and never loads Qwen.
- Model: `typesafe/jev-1.13`; contract: `clover-decisions-v1`. The actual dated
  provider model is retained in each result. The provider's model alias can
  change behind the same name: when intentionally adopting a new revision or
  changing the service prompt, bump the contract in both service and SQL steps.
  A Hutch-only `model_version` change does **not** invalidate the SQL cache.
  Use `SELECT rvbbit.judgment_purge('clover_decide')` when deliberately clearing
  existing judgments instead.
- Generic Hutch request metering remains in place. Provider token/cost details
  are retained in the decision output; they are not a new subscription price or
  a promise that generic receipt token counters are populated.

The service is bounded to eight concurrent hosted requests across all callers.
It rejects excess capacity with 429 / Retry-After; the existing SQL transport
can retry within its timeout. There are no hidden inference retries in the
service. A failed batch returns an error as a whole (already completed provider
work may have incurred cost); no partial result is cached as a successful batch.

## Validation

`services/clover/decisions/test_hosted_service.py` exercises request/response validation,
batch order, load limits, missing credentials, provider errors and redaction.
`scripts/inference/test-clover-decisions.py` runs against a disposable pg_rvbbit
instance and checks SQL types, shared L1/L2 receipts, changed inputs/model/contract,
NULLs, failed inference, and actual batched prewarm followed by cache-only reads.

## Composed decisions

For reusable nested classifications with prior-answer context, see
[decision trees](clover-decision-trees.md): `branch`, `leaf`, `choice_branch`,
`decision_tree` and `decision_tree_batch`. They compose the same cached operator.
