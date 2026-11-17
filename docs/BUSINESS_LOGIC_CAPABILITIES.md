# Business logic as a callable capability

A saved business-logic definition can have a stable key, a short when-to-use
description, field types/descriptions and outcome meanings. The existing tree,
field bindings, examples and source SQL remain its definition. No automatic
business-policy discovery or promotion is involved.

In `/meaning/logic`, **Use with Callie** edits this metadata. **Save metadata**
creates a draft revision. **Available to Callie** publishes the saved revision;
subsequent saves leave that revision active until **Publish this version**.
Removing availability prevents new named executions, including older revisions.
Previously published revisions can otherwise be pinned explicitly.

**Use in SQL** opens with the short named call for a published flow:

```sql
SELECT rvbbit.business_logic('hvac.service_triage', to_jsonb(r)) AS decision
FROM service_requests AS r;
```

Omit the third argument to follow the current published version. The server
resolves and freezes that version for each query; its receipt records the exact
revision used. Pass a third argument, such as `3`, to pin a published revision.
The workbench offers **Named call · latest published**, **Pin published vN**,
and **Full preview SQL** separately. Editing a draft keeps the named call visible;
it continues to use the published definition until the draft is published.
An unpublished flow offers **Set up SQL name** to complete that setup.

The row control defaults to 100 and uses the existing Calliope SQL row limit
(`server.ROW_CAP`), with matching browser validation. It has no separate 24-row
ceiling. Manually requested batches allow up to ten minutes, using the SQL
runner's existing maximum timeout. A `LIMIT` written inside Source SQL can still
return fewer rows; the outer workbench limit does not remove it. Larger runs
retain the normal receipt detail limits described below.

Publication projects a `cap_business_logic` entry into the existing semantic
capability catalog. `capability_search` returns the input contract, outcomes,
examples, a pinned tool invocation and executable SQL syntax. Entries have an
explicit private capability policy; the existing capability grant system can
share them. Search results recheck access and the current publication pointer.

```sql
WITH evaluated AS MATERIALIZED (
  SELECT r.id,
    rvbbit.business_logic('hvac.service_triage', to_jsonb(r), 3) AS decision
  FROM service_requests r
)
SELECT id, decision->>'status' AS status, decision->>'label' AS outcome
FROM evaluated;
```

Equivalent tool call:

```json
{
  "tool": "run_business_logic",
  "arguments": {
    "rule": "hvac.service_triage",
    "version": 3,
    "context": {
      "customer_message": "The upstairs AC stopped cooling again.",
      "previous_visit_notes": "Replaced the capacitor yesterday.",
      "parts_status": "Replacement available."
    }
  }
}
```

`read_business_logic(rule=...,version=...)` reads the exact callable definition.
`read_business_logic_receipt(run_id=...)` reads a recorded run without inference.
`test_business_logic` still tests unsaved trees without publishing or producing a
durable production-run receipt. Source SQL remains a test query, never an
implicit lookup performed by the standalone tool.

## Classifier example: Bigfoot reports

**Classifier example** in the workbench loads `examples.bigfoot_report_classifier`.
If that rule is already saved, the button opens your saved copy instead of
resetting to an unpublished template.
It binds just `observed`, the report narrative. A four-way choice classifies the
reported encounter as visual, physical signs, sounds, or unclear. On the visual
route, a yes/no decision asks whether a road crossing is explicitly described.
This classifies what an account says; it does not establish that the account is
true. Mixed accounts have an explicit precedence: visual, physical, then sounds.

The built-in source contains eight clearly marked fictional test cases covering
every outcome, mixed evidence, an empty narrative, and negation. To use actual
reports, replace Source SQL with:

```sql
SELECT bfroid::text AS request_id, title AS case_name, state, county, observed
FROM public.bigfoot_sightings_locations
WHERE length(btrim(observed)) BETWEEN 1 AND 16000
ORDER BY bfroid
LIMIT 100
```

That relation is present in the local test instance. Other installations can use
any authorized source returning `observed`. The length bound keeps complete
narratives within the input budget rather than truncating their meaning.
Metadata columns remain in the SQL rows/receipt; only `observed` reaches each
question. No source labels or expected answers enter the classifier.

[`calliope_classifier.sql`](../examples/bigfoot/calliope_classifier.sql) includes
both direct `rvbbit.choose` over the root's exact category descriptions and the
published `rvbbit.business_logic` call over the same source. Choose returns a
category; the graph composes that judgment with an optional second question and
returns its outcome and path. Both use `clover_decide` choice judgments; the graph
adds instructions, field bindings and earlier judgments, so their requests/cache
keys are not identical. Binary nodes similarly use the judgment behind `truth`.

After saving and publishing, the standalone tool is:

```json
{
  "tool": "run_business_logic",
  "arguments": {
    "rule": "examples.bigfoot_report_classifier",
    "context": {"observed": "I heard three wood knocks beyond the tents. I saw nothing and found no tracks."}
  }
}
```

The rule uses the existing catalog, saved revisions, row preview and receipts.
It has no terminal actions and requires no classifier-specific runtime or tables.

## Execution boundary

Named SQL calls run through Calliope's governed `run_sql` path (including its
application bridges). Before an explicit `business_logic(...)` query starts,
the server uses its verified subject to load only authorized published
definitions into a connection-local binding table. Each definition is pinned
for that query. A trigger freezes the bindings, including temp-table DML that
PostgreSQL would otherwise permit in a read-only transaction; the query itself
runs physically read-only. The invoker SQL function cannot resolve arbitrary
private registry entries and never treats `rvbbit.request_user` as authorization.

A bare database connection has no Calliope application identity and fails closed
for named rules. Direct database users can still call the existing
`rvbbit.decision_tree(context,tree,4)` with a definition they possess. This first
integration prepares named bindings for explicit function calls in submitted
SQL; calls hidden exclusively inside database views/functions require a future
session-level integration.

Both named entry points call `rvbbit.business_logic`, which delegates inference
to the existing decision-tree interpreter. Missing visited fields produce
`missing_input`; supplied values with incompatible declared types produce
`invalid_input`; null is supplied evidence. Neither is a negative outcome.
Callers must check `status='complete'` before treating a label as a result.
These evaluation entry points do not run actions. Model scores are not calibrated confidence; pinning a
definition is not a guarantee of immutable model behavior.

## Receipts

The function emits a connection-local notice for each evaluation. The server
captures it even when the surrounding SQL discards the trace or aggregates
labels, then stores one owner-private batch receipt outside the read-only
transaction. The receipt contains definition snapshots/hashes, source SQL,
original JSON evidence, evaluated paths, statuses and outcome counts. Counts
describe evaluations, which can include rows later filtered out by SQL, not
necessarily the final returned row count.

Detailed evidence is capped at 200 evaluations or 2 MB per run; all observed
outcome/status counts continue, and truncation is explicit. Failed SQL runs
retain any evaluation evidence collected before the error. Neither opening a
receipt nor inspecting a step reruns inference. Calliope Stage receives a compact
decision card with an exact-version link and a link to the frozen receipt.

## Optional outcome actions

An outcome leaf can carry one optional action. The graph edits the action inside
that outcome, alongside its label. Questions and branches are unchanged; SQL
evaluation strips action definitions before passing the tree to Jev.

```json
{
  "kind": "leaf",
  "label": "Create follow-up task",
  "action": {
    "kind": "sql",
    "label": "Record follow-up",
    "sql": "INSERT INTO calliope_apps.tasks(request_id, status) VALUES ($1->'input'->>'request_id', $1->'decision'->>'label') RETURNING *;",
    "external_effects": "read_only"
  }
}
```

The SQL parameter `$1::jsonb` is `{input: original_context, decision: recorded_result}`.
The existing app-action executor runs one parameterized statement, including
registered RVBBIT/MCP/Python SQL operators. Local writes target the existing
`calliope` / `calliope_apps` schemas. `external_effects` describes external-service
writes, so `read_only` is compatible with a local INSERT. It defaults to
`may_write`. Tables and operators must already exist; the action does not install
code or create tables.

A Callie handoff instead uses `{kind:"callie",label,instruction}`. It creates a
conversation and queues the instruction plus the same recorded packet through
the existing agent mailbox. The returned `queued` status means the handoff was
accepted; follow the conversation for the task's progress and result.

**Execute selected row** saves any edits and executes that exact saved revision
for the selected source row. **Run SQL**, live preview, `run_business_logic`, and
the SQL scalar remain evaluation-only. The agent entry point is:

```json
{
  "tool": "execute_business_logic",
  "arguments": {
    "rule": "examples.hvac_outcome_actions",
    "version": 1,
    "context": {"request_id": "123", "customer_message": "..."},
    "request_key": "a-generated-uuid-for-this-operation"
  }
}
```

Reuse the request UUID when recovering the same operation. A fresh UUID starts
another deliberate execution. The existing business-logic receipt stores the
exact definition, input, decision, selected action, and output; SQL output links
the existing app-action receipt. A stopped/invalid decision never dispatches an
action. A receipt read never executes anything. An interrupted SQL execution can
recover its committed app-action result without re-evaluating or writing again.
Failures keep their evidence; this first slice does not automatically retry them.

This is one terminal action per outcome, not a general DAG runner. There are no
new scheduler, event identity, permission, fan-out, or compensation systems. The
execution entry point is currently the tool/workbench; arbitrary SQL scalar
calls and workspace schedules do not implicitly execute the terminal action.

The **Action example** uses fictional HVAC rows and a separate local demo table:

```sql
CREATE TABLE calliope_apps.logic_action_demo (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id text NOT NULL,
  task text NOT NULL,
  evidence jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

Create this explicitly through the existing workspace SQL tools before trying
the example on a new installation. The installed experiment uses this table;
its Callie branch only reviews supplied fictional evidence.
