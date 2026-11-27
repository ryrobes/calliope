# Flow Runs, Cache-Hit Breadcrumbs, and the Inbox

Migration `0511_flow_runs_receipt_gaps_inbox` adds four things that let one
piece of work be followed end to end in SQL: where it came in, how it was
routed, every model or MCP call it caused (including the ones served from
cache), and what it cost. Everything is additive; existing receipts, cost
views and call counts are unchanged.

## 1. Flow runs: `run_id` / `flow_id`

`rvbbit.receipts`, `rvbbit.cost_events`, `rvbbit.mcp_invocations` and
`rvbbit.receipt_hits` have `run_id uuid` and `flow_id text` columns. They
default from two settings, so every call made while a run is active is
grouped without any writer passing ids around. Nested operator calls and
their `sub_calls` inherit the same run.

```sql
SELECT rvbbit.begin_run('ap_triage');            -- returns the run_id
SELECT rvbbit.decision_tree(i.payload, t.tree) FROM ...;
SELECT rvbbit.mcp_call('linear', 'create_issue', '{...}');
SELECT rvbbit.end_run();                         -- status 'done' by default
```

| function | meaning |
|---|---|
| `rvbbit.begin_run(flow_id text = NULL, run_id uuid = NULL, txn_local bool = false, inbox_id bigint = NULL, meta jsonb = NULL) → uuid` | Sets `rvbbit.run_id` / `rvbbit.flow_id` for the session (or only this transaction with `txn_local => true`, the safe choice on pooled connections) and upserts a `rvbbit.flow_runs` row. |
| `rvbbit.end_run(status text = 'done', run_id uuid = NULL) → uuid` | Closes the run (`done`, `failed`, `needs_human`, `abandoned`) and clears the settings. |
| `rvbbit.current_run_id()`, `rvbbit.current_flow_id()` | The active values. A malformed `rvbbit.run_id` reads as NULL, so it can never fail a receipt write. |

`set_config('rvbbit.run_id', '<uuid>', true)` works too; `flow_runs` is
optional bookkeeping.

Cost events always take the run of the receipt they price (trigger
`cost_events_run_context`), so settlement events appended later from another
session (`settle_decision_costs`, OpenRouter reconcile, the delayed-receipt
flush) stay in the right run. Receipts drained from the delayed queue keep the
run captured when the call ran.

Views:

- `rvbbit.run_trail`: one row per real call (`kind = 'call'`, from receipts,
  including direct MCP calls) and per cache hit (`kind = 'cache_hit'`, cost 0,
  `source_receipt_id` = the receipt that did the work), ordered by `at`.
- `rvbbit.run_summary`: per run: flow, status, inbox item, calls, errors,
  cache hits, ledger cost (`cost_latest`), timing.

## 2. Cache-hit breadcrumbs: `rvbbit.receipt_hits`

An L1 (in-memory) or L2 (receipts) semantic-cache hit, or an MCP result-cache
hit, can write a small zero-cost row: operator, `cache_layer` (`l1`, `l2`,
`mcp`), `inputs_hash`, `source_receipt_id` (known for L2; `run_trail` resolves
L1/MCP through `inputs_hash`), `query_id`, `run_id`, `flow_id`, caller.

Hits are deliberately **not** written to `rvbbit.receipts`: receipts are the
L2 cache and are counted by `decision_costs`, `receipt_cost_audit` and the
cost UIs, and existing tests rely on "a cache hit adds no receipt".

Controlled by `rvbbit.cache_hit_receipts`:

| value | behaviour |
|---|---|
| `run` (default) | log hits only while a run is active |
| `on` | log every hit (one small INSERT per hit; avoid for multi-million-row warm scans) |
| `off` | never |

Hits are skipped where Postgres forbids writes: parallel workers, the leader
of a parallel scan, read-only transactions, and flow-pool threads.

## 3. Direct MCP calls write receipts

A direct `rvbbit.mcp_call(...)` or `rvbbit.mcp_rows(...)` now also writes a
receipt shaped like an operator receipt:

- `operator` = `mcp_call` / `mcp_rows`, `model` = `<server>.<tool>`
- `inputs` = `{server, tool, args}`, `output` = the envelope (NULL on a tool error, with `error` set)
- one `sub_calls` entry of `kind = 'mcp'`, `latency_ms`
- its cost event (priced by `cost_policies` for the `mcp_tool`, as before)
  carries the `receipt_id`, so `receipt_cost_audit` covers MCP calls
- `rvbbit.mcp_invocations` keeps its row and links back with `receipt_id`

The previous separate, unlinked MCP cost event is not written for these calls
(so nothing is double counted). MCP calls made inside an operator pipeline
(`kind: "mcp"` nodes) are unchanged: the operator's receipt carries them in
`sub_calls`.

Turn it off with `SET rvbbit.mcp_receipts = off` (back to an invocation row
plus an unlinked cost event). A new extension library running against a
database that has not run `rvbbit.migrate()` yet also keeps the old behaviour.

## 4. The inbox: `rvbbit.inbox`

A SQL-only inbound queue. Anything can write to it (a direct insert, a
pg_cron job polling an MCP source, later a webhook sidecar); a **system
router flow** decides which **user flow** handles each item, so user flows
never deal with intake.

```
inbox_put ─▶ new ──router: inbox_claim('new')──▶ processing ──inbox_route──▶ routed
                                                   └─inbox_needs_human─▶ needs_human
routed ──flow worker: inbox_claim('routed', flow)──▶ processing ─▶ done | failed
                                         inbox_fail(retry) ─▶ back to new/routed after backoff
```

| function | meaning |
|---|---|
| `inbox_put(source, payload jsonb, dedupe_key = NULL, available_at = NULL, max_attempts = 5) → bigint` | Idempotent per `(source, dedupe_key)`: a repeat returns the existing id and changes nothing. A NULL key always inserts. |
| `inbox_claim(stage = 'new', flow = NULL, n = 1, worker = NULL, lease = '5 minutes', source = NULL) → SETOF inbox` | `FOR UPDATE SKIP LOCKED`; claimed rows become `processing`, `attempts + 1`, get a lease and a fresh `run_id` (pass it to `begin_run`). Rows whose lease expired are claimable again at the same stage. |
| `inbox_route(id, flow, detail jsonb = NULL)` | Router verdict; `detail` can hold the decision-tree trace. |
| `inbox_complete(id, result jsonb = NULL)` | `done`. |
| `inbox_fail(id, error, retry = true, backoff = '30 seconds')` | Back to its stage after `backoff * 2^(attempts-1)` until `max_attempts`, then `failed`. |
| `inbox_needs_human(id, reason = NULL, detail = NULL)` | Park for a person. |
| `inbox_requeue(id, flow = NULL)` | Put a parked/failed/done item back (resets attempts). |

`NOTIFY rvbbit_inbox` fires on every insert and when an item becomes `routed`
or `needs_human`; the payload is `{id, source, status, routed_flow}`.
`rvbbit.inbox_stats` counts items by source, flow and status.

### Example router (not built in)

```sql
-- One routing pass. Low-confidence or unknown items go to a person.
WITH policy AS (
    SELECT rvbbit.choice_branch(
        'What kind of inbound item is this?',
        choices => '{"invoice":"A vendor invoice or bill to pay",
                     "support":"A customer asking for help",
                     "other":"Anything else, or unclear"}'::jsonb,
        routes => jsonb_build_object(
            'invoice', rvbbit.leaf('ap_triage'),
            'support', rvbbit.leaf('support_routing'),
            'other',   rvbbit.leaf('needs_human'))) AS tree
), claimed AS (
    SELECT * FROM rvbbit.inbox_claim('new', n => 50, worker => 'router')
), decided AS (
    SELECT c.id, rvbbit.decision_tree(c.payload, p.tree) AS d
    FROM claimed c CROSS JOIN policy p
)
SELECT id,
       CASE WHEN d->>'status' = 'complete' AND d->>'label' <> 'needs_human'
            THEN (rvbbit.inbox_route(id, d->>'label', d)).status
            ELSE (rvbbit.inbox_needs_human(id, 'router: ' || coalesce(d->>'status', 'no verdict'), d)).status
       END
FROM decided;

-- A user flow worker.
SELECT rvbbit.begin_run(i.routed_flow, i.run_id, txn_local => true, inbox_id => i.id)
FROM rvbbit.inbox_claim('routed', flow => 'ap_triage') i;
-- ... decide / extract / queue actions ...
SELECT rvbbit.inbox_complete(:id, '{"action":"queued_for_approval"}');
```

Then `SELECT * FROM rvbbit.run_trail WHERE run_id = :run_id` shows every
decision, extraction and MCP call for that one item, and
`rvbbit.run_summary` its cost.

Out of scope here: an outbox with approvals for side-effecting MCP calls,
replay/backtest, and a webhook writer.
