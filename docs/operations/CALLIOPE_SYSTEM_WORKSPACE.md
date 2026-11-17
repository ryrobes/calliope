# Calliope System workspace

The **System** entry opens `/calliope?system=1`, resolves the administrator's
existing System conversation (or creates it once), and keeps normal chat beside
a persistent live stage. It is not a second chat application or a modal.

## Available panels

- **Overview:** Calliope, Hermes, RVBBIT/Postgres, Clover, Hindsight, and desktop status;
  capability readiness; installed add-ons; telemetry writer health; request volume,
  errors, latency, provider charges and Data links. Usage periods are 1h, 24h, 7d, and 30d.
  Registered MCP servers are listed inline with transport, tool count and recent activity.
- **Capabilities:** searchable operations with configured model/version, worker
  package, health evidence, and usage for the selected period.
- **Checks:** installed SQL tests, background runs, history, expected/actual output,
  dependency availability, and results in the existing Data panel. Opening Checks
  selects an operator from the active or latest recorded run. Selecting an operator
  opens its own latest run, including older runs outside the five visible history
  columns. History cells explicitly pin a run/operator pair. Queued, running,
  interrupted, skipped, and absent results are distinguished; **Latest results**
  returns to automatic selection. No new test runs start on opening the view.
- **Settings:** shared Instance AI connection plus optional chat connections and Models & voice controls, reusing
  the existing credential, OAuth, model catalog, agent route, fallback, loadout,
  image-generation, and dictation controls and their authenticated admin APIs.
  The Models & providers menu now opens this panel. Model drafts wait for the
  existing Apply all action; moving between System tabs preserves them.
- **Work & receipts:** the ordinary conversation stage remains available.
  Chat receipt links reveal it automatically; the System navigation stays fixed.
- **Ask Callie:** fills the normal composer and attaches the selected observation,
  capability, invocation, or saved settings to the next turn. It does not send
  automatically. Focusing a provider or agent identifies that setting in context.

The dimmed, theme-colored glass surface begins at the tabs and encloses the
scrolling content, with rounded top and bottom corners. Chat remains alongside it.
A compact header shows the observation timestamp and period selector. Refresh
controls use icons with accessible names and tooltips; loading does not change their size.

The OpenRouter card manages the instance's actual shared AI key. Additional chat
connections include OpenRouter using that same shared key, plus optional
subscriptions or accounts. Clover appears in agent model selection as Instance AI.
The shared OpenRouter card shows its discovered models and explains where to
change the key; it has no separate key input or disconnect action. Clover's underlying
specialist routing and worker installation remain server-managed. Onboarding
and controls for those Clover settings are follow-up work.
Opening the workspace does not install packages or change inference routes.

System components run the application (server, runtime, database, inference gateway).
Connections give it access to external tools/accounts. The Overview shows registered
MCP server names, transports, tool counts, last calls and 24-hour failure counts;
an optional Data link opens the same safe inventory projection for deeper inspection. Further connection management can use the existing
connection workflow in the same conversation.

## Identity and access

`rvbbit.calliope_sessions.workspace_kind = 'system'` identifies this conversation.
A partial unique index allows one unarchived System conversation per owner;
renaming it does not break the entry point. Archiving it permits a fresh System
conversation on the next open. Each administrator has a private conversation
while inspecting the same instance state.

The navigation, resolver, snapshot API, context hydration, and session access
predicate check Calliope admin membership. Revoked admins cannot keep accessing
the thread. Sharing is disabled; an ordinary view grant does not bypass the admin
requirement. This reuses the existing teams and session authorization systems.

## Data and context

- `POST /api/calliope/system/session` resolves the persistent conversation.
- `GET /api/calliope/system/snapshot?session=UUID&window=24h&probe=true`
  reads authenticated Clover `/v1/capabilities`, the Postgres
  `clover_telemetry.calls`/`writers` views, and Warren's capability inventory.
- Health checks run on initial load, on entering Overview or Capabilities, and on
  periodic/manual refresh. `probe=true` is the API default; `probe=false` remains
  available for configuration-only observations. These calls do not run inference.
- Capability availability counts are **Enabled**, **Disabled**, and **Needs setup**.
  **Health check passed / Health check failed / No health check / Health not checked** is a separate observation.
  A capability without a health endpoint is not considered unhealthy. These counts
  describe capabilities, not distinct servers; several can share a service.
  Warren packages have a separate inventory and capability count.
- `GET /api/calliope/system/settings?session=UUID` reads the existing redacted
  provider summary and speech controls, and saves a compact settings observation.
  The same admin and private-System-session checks apply before reading settings.
- `POST /api/calliope/system/instance-ai` validates and saves, or checks, the core
  OpenRouter key. Admin membership, owned System thread, and the admin request
  header are required. Key values never enter observations.
- `GET /api/calliope/system/detail-query` supplies SQL for failures, all calls, or
  MCP inventory. The parent sends it to the existing Data iframe; normal SQL
  prepare/run, read-only checks, role privileges and receipts apply. A small explicit
  internal-relation allowlist requires live admin membership on every execution.
- Specialist deployment descriptions come from the existing canonical
  `scripts/clover/capability_options.json`, copied into the server image.
- Responses expose an allowlist of inventory fields, not service credentials,
  worker addresses, provider request bodies, or generated model outputs.

Observations are saved in `rvbbit.calliope_system_snapshots`. The browser sends
only the observation ID and selected view/object; the server checks owner and
session, reloads the observation, and hydrates the normal turn's origin context.
Callie sees the same observation and timestamp as the administrator, rather than
client-provided status claims. Context already stored on a turn survives snapshot
cleanup. Refresh deletes that owner's observations older than seven days.

Settings observations share that table with `payload.kind = 'settings'`; they
cannot be mistaken for gateway observations. Only provider authentication state,
saved agent/fallback/background model choices, pending saved revisions, and
speech configuration enter chat context. API keys, credential references, OAuth
codes, catalog metadata, and unsaved form edits are excluded. Existing mutation
handlers reread saved settings after applying changes and update the reference.

The visible workspace refreshes once a minute unless an input in it has focus
or the Settings tab is open.
Manual **Refresh** and **Recheck health** are available; health probing is automatic. The System DOM lives outside ordinary stage
reconciliation, preserving selection while chat and receipts update.

## Interpreting the numbers

Capabilities show **Enabled / Unavailable / Disabled / Needs setup** alongside
specific health evidence. Worker checks validate readiness JSON; hosted checks
validate the key, exact model and output modality. Legacy routes show **Route
found · worker unverified** because the old gateway exposes its registry but no
worker health endpoint. Details explain check scope or failure, and Callie receives
that same server-observed evidence. Inference tests remain in Checks and never
run automatically on opening the page.

Agent discovery filters operators with disabled/uninstalled dependencies, plus
failures from System observations less than two minutes old. This does not delete
SQL definitions, hide their diagnostic tests, or prevent discovery of installable
packages. A fresh successful check restores discovery; old health evidence expires.
Last successful and failed invocation timestamps remain separate for the selected
period. A remote route can work without a local Warren package.

The ledger counts calls received by Clover, excluding SQL cache hits and calls
sent directly to providers. Unknown cost remains unknown; a reported zero remains
zero. Known charges can cover only part of the period's calls, so coverage is
shown alongside the amount. Hardware costs and tariff estimates are not added to
reported provider charges. Later ledger updates appear on refresh. Failed-call
queries include timestamp, HTTP status, recorded error code, model, invocation ID
and upstream attempts. Historical provider error bodies were not stored; the
interface does not pretend the generic error code is a complete root cause.

## Validation

`services/calliope/test_system_console.py` exercises real disposable Postgres
databases and the existing headless browser fixture. Together with
`test_calliope_access.py`, it covers thread reuse, archival uniqueness, admin
revocation, grants, API authorization, observation provenance, ledger arithmetic,
late charges, namespaced chat/media/speech ledger joins, responsive layouts,
retained selection, and opening chat receipts. Settings coverage also exercises
real inline controls, draft preservation, Apply all through the existing admin
API, saved-settings provenance, secret exclusion, and access failures. Together
with provider settings/refresh tests and instance-key checks, the focused run passed 74 tests.
The follow-up System/Data suite passed 47 tests, including live admin revocation
and SQL handoff into the compiled Data iframe; these suites overlap. Worker
and configuration tests cover key rotation, removal and legacy environment support.

Set `ACTIVITY_TEST_ADMIN_DSN` to a disposable PostgreSQL cluster, then run:

```sh
PYTHONPATH=services/calliope python -m pytest -q \
  services/calliope/test_system_console.py \
  services/calliope/test_calliope_access.py \
  services/calliope/test_provider_settings.py \
  services/calliope/test_provider_refresh.py
```

Set `CALLIOPE_PROVIDER_TEST_DSN` to a separate disposable database as well to run
the provider persistence tests instead of skipping them.


## Shared instance credential

`CALLIOPE_OPENROUTER_KEY_FILE` points Calliope at the shared secret. Clover and its
hosted decisions/utilities/research workers use the same file through
`OPENROUTER_API_KEY_FILE`. The shipped Compose overlays give only Calliope a writable
mount. The private host configuration parent is 0700; the mounted credentials
directory is 0755 and its one key is 0644 so different unprivileged container UIDs
can read it. Treat this directory as a secret in backups, like providers.env.

Check & save verifies `/api/v1/key` without paid inference, then atomically replaces
the file. Clover requests reread it without restarting workers. Hermes receives
the same directory read-only, installs the validated key in its native pool, and
restarts its gateway child after a replacement. Its supervisor also registers the
key at first boot. Existing onboarding/provider forms route through this same
instance-key handler. Identical keys avoid duplicate restarts; failed synchronization
is shown as pending and retried by the existing supervisor reconciliation loop.
The control request contains no key value, and no agent model selection changes. An explicitly configured unreadable/missing
file fails closed instead of falling back to a stale environment key. Env-only
standalone deployments remain supported. `configure.py --force` preserves a rotated
file unless `--provider-key-file` explicitly supplies a replacement.

## SQL checks

System → Checks projects the installed operator harness, not a separate test
catalog. Definitions come from `rvbbit.operators.tests`; execution uses
`rvbbit.run_tests(operator)` and results append to `rvbbit.operator_test_runs`,
so DataRabbit sees the same run IDs and outcomes. **Run available tests** includes
configured remote services as well as local workers. Only unavailable dependencies
or definitions changed after queuing are excluded, with an explicit reason. Opening or refreshing Checks does not start a new
run; the run buttons make real SQL/model calls and can incur provider charges.

`operator_test_batches` holds selection and frozen operator/test/route metadata;
`operator_test_observations` adds per-function duration, exclusion reasons, and
PostgreSQL warnings (including backend errors that an operator converts to NULL). These
supplement the existing results table. Each operator commits independently, so
progress survives navigation. A database advisory lock prevents overlapping
batches across server processes. An interrupted worker keeps completed results
and is marked interrupted on the next checks read; it never silently replays SQL.
Definitions or backend configuration changed after queuing are skipped.

The matrix shows the latest five runs in compact columns, while the SQL ledger retains all history.
Test SQL uses read-only CodeMirror with the Data panel syntax colors.
The matrix separates failed assertions, errors, skipped operators, unrun tests,
and missing test coverage. Historical selections show the definition captured
for that run. Older DataRabbit runs remain readable but have only their original
ledger metadata. Results in Data opens the existing SQL explorer; Ask Callie
references the selected run and operator and hydrates its evidence on the server.
All checks API routes require an administrator's own System workspace.

The SQL harness bypasses the operator result cache; downstream cache state is
not asserted. Tests retain their installed expectations, including old thresholds
that may fail against a newly selected provider. No threshold tuning or fixture
replacement happens as a side effect of testing.

Model labels follow the configured execution path: specialist backend models and
LLM step/provider routing. The generic operator model is omitted when unused.
New runs freeze these routing facts; historical runs without them say so and
offer Current routing separately. These are configuration observations, not
per-test provider telemetry; explicit test SQL may override an LLM choice.

Migration `0474_luna_model_defaults` updates the shipped mini/Luna defaults to
`openai/gpt-6-luna` (provider-qualified `openrouter/openai/gpt-6-luna`). Custom
models and historical results remain unchanged.
The SQL helper route (`clover`) defaults Luna 6 reasoning to `none`, keeping short
classifiers' answer budgets available for output; callers may override it. Chat
routes retain their reasoning defaults. Earlier failed runs remain in the ledger.

### Test queries, execution hints, and image inventory

Each embedded test has an **Open in Data** action. It uses the existing SQL
workspace and runs the selected SQL there, with the same model-call costs as
running it normally. Incoming SQL (including Stage queries and restored SQL
workspaces) uses the Data editor's PostgreSQL formatter before display. Unsupported
syntax remains unchanged; formatting never prevents opening the query.

Migration `0475_relevance_test_contract` replaces only the shipped BGE cutoffs:
related text must rank above unrelated text, and both scores must be non-null in
0..1. It does not recalibrate the reranker, alter user-written tests, or rewrite
historical test outcomes. The managed Clover pack and embedded catalog have the
same tests for new installs.

Capabilities carry subtle CPU, GPU, API, or CPU + API chips based on the serving
route and declared worker metadata. API describes the dependency of this install,
not the provider's hardware. Undeclared hardware remains **Worker**; the tooltip
explains the evidence and distinguishes optional deployment requirements.

**Updates** is an administrator-only, read-only inventory inside the System
thread. It joins the bundled `release/calliope-release.json` with a timestamped
host inventory. Release targets, actual installed image references, OCI version
labels, revisions, image IDs, and available registry digests remain distinct.
Supporting/local images missing from the release manifest are listed separately.
No registry polling, version comparison, image pulling, or upgrade execution is
implemented. The disabled Upgrade all control marks that future surface.

The deployment host exports safe metadata without exposing Docker to Calliope:

```sh
sudo python3 scripts/release/capture-deployment.py \
  --output /opt/calliope/deployment/inventory.json --host-label 'Instance host'
```

Mount the **directory** `/opt/calliope/deployment` read-only at
`/run/calliope/deployment` in the Calliope server. The writer replaces the file
atomically, so a file bind mount would not follow subsequent captures. The path
can be changed with `CALLIOPE_DEPLOYMENT_INVENTORY_PATH`. Capture after deployment
changes; Refresh rereads the saved inventory and shows its capture time, flagging
snapshots older than 24 hours. Missing inventory is shown as unknown, not as a
missing installation. A host capture covers that Docker host, not other VMs.
Callie receives the same server-owned release/inventory observation when Updates
is selected. The current release manifest still needs to absorb the additional
Clover services and worker images before a reproducible release is claimed.

## Provider cost coverage

The charge total is the sum of reported provider charges, including any known
partial charges. It excludes hardware costs and does not estimate missing costs.
Coverage uses evidence recorded with each invocation, never today's routing:

- **Reported**: all attempts have provider costs and the total is nonzero.
- **No additional provider charge**: all costs were explicitly reported as zero.
  CPU workers report zero; composed operations report zero additional charge at
  the parent when their child calls are already recorded separately.
- **Not sent**: no attempts and an explicit gateway rejection (`lanes_saturated`
  or `capability_unavailable`). An arbitrary upstream failure is not treated as free.
- **Pending**, **partial**, **unreported**: actual gaps in recorded cost evidence.
  Imported specialist history without cost records is called out within unreported;
  it is not silently relabeled as free. API calls with missing cost remain visible.

Overview and Usage distinguish these counts. **Inspect in Data / Cost coverage**
opens a SQL query with per-call coverage, known cost, historical cost basis, and
upstream cost sources/statuses. No request content or secrets are included.
GLiNER supplies zero-cost usage at the response envelope so its SQL-facing output
remains the same array of spans. Historical records are preserved.

## Compact service and test status

System components are expandable rows with one vocabulary: Online, Needs attention,
Needs setup, Not configured, or Off. Hindsight health checks its configured memory
service and database connection. Call logging is diagnostic information, not a
separately deployed component. Cost coverage has compact counts and a Data link;
its tooltip distinguishes imported history, pending/partial costs and unsent calls.

Checks show the last five run IDs with columns wide enough for multi-digit IDs.
Cells distinguish Setup, Changed, Excluded, No tests and Not run; selection shows
the recorded reason. Assertions show expected versus actual, SQL exceptions keep
SQLSTATE/detail/hint, and function-run warnings survive refresh and navigation.
Historical runs cannot recover warnings that were not captured at the time.

Migration 0476 adds 11 tests to previously untested Clover wrappers, including all
five web wrappers, decision variants, CPU analytics, GLiNER, TSPulse and draft-spec
generation. Existing custom tests are preserved. Definitions also ship in the
managed/builtin packs and embedded seed. Migration 0477 keeps bad expectation
conversions from aborting later tests and retains SQL exception details.

## System interaction polish

Each tab remembers its own scroll position; capability selections and test runs
have separate detail positions. Component/deployment disclosures survive refresh.
Checks stacks its list and detail at narrow stage widths, offers a function search,
and reveals the selected result with a return-to-functions control. Searching
filters existing rows without remounting the input or SQL editors.

Capability details lead with health evidence and usage. Installed workers do not
show installation instructions. The shared OpenRouter key editor is collapsed
when connected, and open on an unconfigured instance. No credentials or form
drafts enter the conversation observation.

Checks context includes the chosen run/function, function-level diagnostics,
coverage, active filter/search and the five recent runs. The context strip uses
the checks observation timestamp. Work & receipts has its own context instead of
inheriting the last live panel. The Data results query includes saved function
warnings as `function_diagnostics`.
