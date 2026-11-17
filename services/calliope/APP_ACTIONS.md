# Editable HTML apps

HTML artifacts can read using `rvbbitQuery()` and execute named SQL transactions
using `rvbbitAction()`. No model invocation, frozen SQL approval, new server process,
or workflow session is involved in a user's button click.

`manifest.capabilities.sql_actions` describes runtime support: `true` for HTML,
`false` for the Python/FastAPI runtime. It does not grant writes or mean actions
have been configured. Only the named definitions in `manifest.actions` are
callable, so an empty HTML starter supports actions but has none to execute.

1. Use `run_workspace_sql` to create app-specific tables, constraints and indexes
   in `calliope_apps` for application state (`calliope` remains writable for data work). Give each app a distinct table prefix.
2. Fetch `live_app_template(runtime_kind="html")`. Its `app_actions_example`
   includes setup SQL, a manifest and complete task-list HTML.
3. Publish the HTML and `manifest.actions` together with `create_live_app` or
   `update_live_app`. They become one immutable app version.
4. Call named actions from user interactions, then update the UI from the returned
   rows or await a fresh `rvbbitQuery()`.

An example action:

```json
{
  "actions": {
    "set_status": {
      "description": "Change task status",
      "inputs": {
        "id": {"type": "uuid"},
        "status": {"type": "string", "enum": ["open", "done"]},
        "version": {"type": "integer"}
      },
      "steps": [{
        "sql": "UPDATE calliope_apps.app_tasks SET status=$2, version=version+1 WHERE id=$1::uuid AND version=$3 RETURNING *",
        "params": ["id", "status", "version"],
        "expect_rows": 1
      }]
    }
  }
}
```

```js
const result = await rvbbitAction('set_status', {
  id: task.id, status: 'done', version: task.version
});
// result.rows: final step; result.results: all step results.
```

Use `$1` parameters, never string interpolation. Inputs accept `string`, `integer`,
`number`, `boolean`, `uuid`, and `json`, with optional `nullable`, `enum`, and
`max_length`. All declared input keys are required, including nullable ones.

Each action executes up to 12 SELECT/INSERT/UPDATE/DELETE/MERGE statements in one
PostgreSQL transaction, using the existing limited workspace role. Writes must target
`calliope_apps` or `calliope`. Registered RVBBIT/MCP operators are resolved from
the live catalogs through the same adapters as workspace SQL. Multiple scalar
or set-returning calls may appear in each statement; pure SELECT operator actions
are supported too. DDL remains build-time work. Each step returns at most 200 rows; transactions have a
15-second execution budget and 2 MB result cap. SQL constraints enforce business
invariants. Schema-qualified source reads use the existing exposed-schema policy.

`expect_rows` is optional. Use it with a version predicate for editable records:
zero matched rows returns HTTP 409 / `CONFLICT` and rolls back **all** steps.
Reload and let the user reconcile their intended change.

Both `/apps/...` pages and sandboxed stage/viewer frames get the same API. The
parent stage supplies its own artifact identity and version. Old tabs execute
old published definitions; plan schema changes compatibly. Rolling back an HTML
version does not undo database changes.

Identical in-flight browser calls coalesce. Each operation has a UUID request key;
the committed result and business writes are persisted in the same transaction.
If the network response is lost, reuse `error.requestKey`:

```js
await rvbbitAction(name, originalInputs, {requestKey: error.requestKey});
```

A committed key replays its result without re-executing. Reusing it for different
inputs or another version is a conflict. Keep the key for uncertain outcomes;
disable submit while pending. A deliberate later operation gets a new key.
A failed read after a successful save is a **reload failure**, not a save failure.

For apps declaring actions, `rvbbitQuery` defaults to `cache:'no-store'`, bypassing
both previous results and older in-flight queries. Await the write before reading.
Explicit query cache options still work. Captures and historical *data-time* views
disable the action helper; normal stage version views remain interactive.

Access follows existing authenticated artifact sharing: anyone allowed to view
this app can invoke its published actions in this first slice. There is no new
per-user/row policy. Browser callers send values, never executable SQL. The server
selects SQL from the requested stored version and attributes the authenticated actor.

`rvbbit.app_action_runs` is quiet execution history: actor, app version, action,
request key, timing, outcome, and returned result/error. Successful records are
atomic with writes; failure recording is best effort during database outages.
`debug_live_app` exposes the latest 20 run summaries. Nothing is posted to chat or
Floor. Audit schema and grants initialize automatically on first action execution;
no separate setup is needed. Deployment requires only calliope-server.

## External SQL calls and retries

SQL wrappers can call remote systems even when their statement starts with SELECT.
A PostgreSQL rollback cannot undo those remote effects. No new approval gate or
per-function name list is introduced.

The optional action-level `external_effects` field accepts `read_only` or `may_write`
(the default). Use `read_only` only if **every** external call is known to read
without changing its remote system. It permits retries of failed reads. This is an
author declaration, not automatic inference from SELECT or function volatility.

For `may_write`, the server durably records dispatch intent immediately before the
first explicit operator statement. A successful action still commits its result
with the local writes and replays completed request keys. If the operation fails,
loses its connection, or crashes after dispatch intent, the same key cannot dispatch
again: `EXTERNAL_OUTCOME_UNKNOWN` tells the UI to inspect the provider outcome.
This also covers a crash between recording intent and actually making the call;
we deliberately cannot claim which external effects completed. Earlier local-only
failures remain retryable. No rollback or exactly-once guarantee is claimed for
remote operations, including repeated calls inside one SQL statement.

This classification covers explicit registered calls in the action SQL. As with
workspace SQL, installed views, triggers, RLS policies and extension code remain
trusted appliance code; avoid hiding external effects inside them.

Inspect uncertainty through `debug_live_app` and the external system. After that,
a new intentional operation uses a new key; there is no automatic re-dispatch,
frozen SQL approval, or inbox blocker. Providers with their own idempotency keys
should receive those keys in the action's bound inputs.
