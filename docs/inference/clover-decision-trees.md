# Decision trees in SQL

`branch` and `leaf` build inert JSONB. `decision_tree` interprets it, invoking
Jev only along the selected path. No model creates the tree, and no unvisited
branch performs inference. Trees can be literals, CTE values, or stored JSONB.

```sql
WITH policy AS (
  SELECT rvbbit.branch(
    'Does this customer show meaningful churn risk?',
    yes => rvbbit.branch(
      'Is an unresolved service problem the main concern?',
      yes => rvbbit.leaf('service intervention'),
      no  => rvbbit.leaf('relationship review')
    ),
    no => rvbbit.leaf('routine follow-up')
  ) AS tree
)
SELECT c.customer_id,
       rvbbit.decision_tree(
         jsonb_build_object('recent_messages', c.recent_messages,
                            'account_summary', c.account_summary),
         policy.tree
       ) AS decision
FROM customer_contexts c CROSS JOIN policy;
```

For just the classification, use `decision ->> 'label'`. Keep `status` when
unavailable decisions must be distinguished from an actual classification.
A result looks like:

```json
{
  "version": "clover-tree-v1",
  "tree_id": "<hash of the complete tree>",
  "status": "complete",
  "label": "service intervention",
  "steps": 2,
  "path": [
    {"node":"root", "question":"Does this customer show meaningful churn risk?",
     "answer":true, "score":0.94, "threshold":0.5, "route":"yes",
     "model":"typesafe/jev-1.13-20260917", "contract":"clover-decisions-v1"},
    {"node":"root/yes", "question":"Is an unresolved service problem the main concern?",
     "answer":true, "score":0.89, "threshold":0.5, "route":"yes",
     "model":"typesafe/jev-1.13-20260917", "contract":"clover-decisions-v1"}
  ]
}
```

By default each question receives **original evidence plus earlier judgments**.
With `inputs`, it receives only the named fields from the original evidence. Those
judgments include earlier questions, answers and scores (and thresholds or
category definitions), explicitly described as fallible interpretations rather
than new facts. The original evidence is never replaced by a generated summary.
The trace contains model provenance; timestamps and accounting are excluded
from subsequent model context so they do not spoil cache reuse.

Boolean branches use score **strictly greater than** threshold, default 0.5:
`rvbbit.branch('Question?', yes_node, no_node, threshold => 0.8)`. Scores are model
outputs, not calibrated confidence. An abstention option is not automatic;
use an explicit `unclear` category when the classification should allow one.

## Multiway branches

Use `choice_branch` to avoid ambiguous SQL overloads with binary `branch`:

```sql
SELECT rvbbit.decision_tree(
  'I was charged twice for the same appointment',
  rvbbit.choice_branch(
    'What kind of help does the customer need?',
    choices => '{"billing":"Invoices, payments or charges",
                 "scheduling":"Appointment dates or availability",
                 "unclear":"Insufficient information"}'::jsonb,
    routes => jsonb_build_object(
      'billing', rvbbit.leaf('finance'),
      'scheduling', rvbbit.leaf('scheduling team'),
      'unclear', rvbbit.leaf('needs review')
    )
  )
);
```

Every choice must have exactly one matching route. Routes can contain further
branches. Labels in traces use JSON Pointer escaping for `/` and `~`.

## Explicit evidence fields

Question nodes may include `"inputs": ["customer_message", "previous_visit_notes"]`.
These are 1–16 distinct, nonblank, exact top-level JSON keys (up to 80 characters).
Dots and slashes are literal key characters, not traversal syntax. Each selected
value can itself be an object. Constructors remain compatible; attach bindings
with normal JSONB composition:

```sql
SELECT rvbbit.branch('Are all required parts available?',
                    rvbbit.leaf('book'), rvbbit.leaf('check parts'))
       || '{"inputs":["parts_status"]}'::jsonb;
```

The interpreter projects evidence **before** inference and cache lookup. Unused
fields do not change that node's request. Missing inputs stop the visited path:
`{"status":"missing_input","label":null,"missing_inputs":["parts_status"],
"stopped_at":"root/repair/repeat", ...}`. Already evaluated steps remain in the
trace. Missing fields on unvisited paths have no effect. Null is a present value;
write an explicit question/route for unknown values. Binding does not assert types
or convert semantic model judgments into deterministic predicates.

Traces record each bound node's `inputs`; prior judgments remain interpretation
context for later questions. Omit `inputs` for existing whole-context behavior.
There are no lookups, templates or executable expressions in a binding. These
semantics apply equally to scalar and batch evaluation; one row's missing fields
do not block other rows. Requires migration **0471_decision_tree_inputs**.

## Bulk evaluation

`decision_tree_batch(states jsonb, tree jsonb, max_steps integer DEFAULT 16)`
accepts an array of up to 256 contexts and returns an array in the same order.
For example, with a saved `customer_policy.tree`:

```sql
SELECT rvbbit.decision_tree_batch(
  '[{"recent_messages":"Please cancel my account"},
    {"recent_messages":"Everything is working well"}]'::jsonb,
  tree
)
FROM customer_policy;
```

Build larger inputs with `jsonb_agg(context ORDER BY customer_id)` over a bounded
slice, retaining the corresponding ordered IDs. Use `jsonb_array_elements(...)
WITH ORDINALITY` to join results back by position. Do not aggregate an unbounded
table; page the workload. An empty input array returns `[]`; a JSON null entry
returns a null result in the same position.

Execution proceeds in waves: collect all active nodes, deduplicate/cache-check,
prewarm through `clover_decide`, then advance their paths. Finished rows no longer
participate. The existing backend controls HTTP batch size and concurrency
(default eight inputs per HTTP request). Scalar and batch evaluations share the
same per-node cache; a later scalar call reuses the warmed results.

If any prewarm request in a wave fails, that wave's still-active rows return
`unavailable` / `batch_round_failed`. Already completed rows remain complete.
There is no immediate scalar retry of the failed prewarm; successful work stays
cached for an explicit rerun. Batch execution requires caching enabled; use the
scalar API when deliberately bypassing the cache.

## Cache identity, bounds and failures

- Node keys include selected evidence (or all evidence for unbound nodes), prior judgments,
  the current question/definitions and the existing Clover model/contract key.
- The full tree is not sent to Jev or included in every node key. Editing an
  unvisited leaf does not invalidate useful decisions. `tree_id` still changes
  so the final result identifies the complete policy used.
- Changing a threshold reuses the score at that node. If a later node runs, its
  prior-judgment context includes the changed threshold and is cached separately.
- The entire tree is validated before inference, including unvisited branches.
  Unknown keys, missing routes, invalid thresholds or malformed nodes raise SQL
  errors. Trees contain no executable SQL, callbacks or graph references.
- Maximum 255 nodes, 16 decisions per path, 256 KiB tree, 1,800 characters per
  question, 200 characters per leaf label. Multiway branches support 2–16
  categories using the normal `choice_question` definitions contract.
- `max_steps` can lower the per-row decision budget (1–16). Reaching a leaf on the
  last allowed step succeeds; needing another question returns `budget_exhausted`.
- Original evidence plus growing history must fit the service's 32 KiB state
  limit. Nothing is silently truncated; an oversized state returns `context_limit`.
- Provider/invalid-response failures return `unavailable`, never a false branch
  or fallback label. Incomplete results have a null label, prior successful path,
  error code and `stopped_at`. Other service request limits still apply.
- SQL NULL or JSON null state yields NULL. A tree consisting of a leaf needs no
  inference. This API evaluates decisions; it does not send alerts or perform
  actions. Feed labels/status into the existing alert reconciliation system.

Migration **0433_clover_decision_trees** and the managed Clover install pack ship
these functions. It builds on migration 0432 and its hosted service; no additional
Clover endpoint or GPU model is needed. The bulk path needs the typed prewarmer
fix shipped with the base decisions implementation.

Run `scripts/inference/test-clover-decision-trees.py` against a disposable
pg_rvbbit database with both migrations applied. The fixture checks lazy paths,
validation before inference, prior-answer context, cache reuse, multiway routing,
scalar/batch equivalence, limits and explicit failure handling.

## Discovery and business rules

Migration `0434_clover_decision_discovery` publishes the helper signatures and
examples as `cap_operator` capabilities. The existing capability search refresh
also detects missing or changed seed documentation, even when the rest of the
graph is populated. Search for `choice_branch` or `decision_tree_batch` to get
the exact callable shape. Batch input is JSONB: use
`to_jsonb(ARRAY['first context', 'second context'])` for a SQL text array.

Use deterministic SQL gates for authoritative facts such as opt-out flags,
verified enrollment, or explicit terminal statuses. Reserve the tree for
ambiguous conflicts after those gates. Its recorded path makes a bad predicate
auditable; it does not turn an inferred label into an authoritative fact.
