# Published company apps

The **Calliope server** hosts published A2UI apps; the **Calliope desktop client**
and browser render them. **UI Studio** is the authoring/test tool. **Lab** refers
only to Ryan's test deployment, never a runtime or product type.

## Storage and execution

Use the existing `create_live_app` and `update_live_app` tools with
`runtime_kind="a2ui"`, `source_files={"app.json": JSON_STRING}`, and a manifest.
`live_app_template(runtime_kind="a2ui")` returns a runnable starter and contract.
Definitions use the existing `rvbbit.dashboards` / `rvbbit.dashboard_versions`
registry; no additional registry or app database is created. Published versions
are immutable. Business tables remain in the Calliope Postgres database. The
examples use `calliope_apps` tables created through `run_workspace_sql`.

A definition contains `schema: "calliope.app.v1"`, `title`, `components`, `data`,
`queries`, and `events`. Components use the same A2UI v0.9 catalog as UI Studio.
`data.state` holds transient client form/filter input. Remaining data fields
provide initial placeholders for query results. Native unsaved edits use the private recovery journal described in
[native drafts](../OFFLINE.md); business data remains server-owned. Web form
state remains transient. Neither host maintains a local SQL replica.

Each named query declares SQL, ordered `$1..$N` parameters bound to model paths,
a JSON output path, shape (`rows`, `first`, or first-column `value`), and optional
row limit (1–1000). SQL literals are bound through PostgreSQL's AST and driver
quoting, then use the server's existing validated read-only SQL path, caller
identity/role, timeouts, and lineage. These bindings are ordinary SELECTs over
existing tables, views, joins and functions, not one Python function per dataset.

For example `/parts/readiness` is a JSON pointer into a returned view model; it
is not a URL, a table, or a custom REST route. A query can populate it under any
app-defined name. No framework source change is needed to add another binding.

Named events optionally update state or execute an action from the same
version's `manifest.actions`. SQL writes reuse the existing typed, parameterized,
transactional app-action executor, conflict checks and idempotency receipts.
Actions use the existing app viewer authorization model. Bind `{"actor":true}`
in query `params` and action `step.params` to use the authenticated caller in
SQL ownership/assignment predicates. This is a server-supplied value, not an
action input or writable model field. For example `WHERE assigned_to=$2` with
`params: ["job_id", {"actor":true}]` constrains a named action even when someone
calls it directly. All write steps must carry the appropriate predicate; this
does not automatically add a policy to arbitrary SQL. Artifact grants restrict
which apps a user can open. Do not use visibility or decision branches as
authorization checks.

Both hosts use `/api/apps/{slug}/ui` with the loaded version, transient state,
and optional event/context/idempotency key. Artifact grants are checked on every
request. This generic endpoint accepts no client-provided SQL or definitions.
Browsers must use authenticated same-origin JSON. The desktop uses its existing
Calliope session directly. Queries refresh after filter debounce and every 15
seconds; this first host uses polling rather than database subscriptions.

## Running and testing

Publish [service work orders](../../../../services/calliope/examples/ui-apps/service-work-orders.json)
with its adjacent manifest; optional `setup.sql` creates fictional records in
Postgres. `database-clock.json` is a second, unrelated app with no setup. Neither
adds a handler or process. Example records are never seeded at server startup.

Open `/apps/<slug>` or `/apps/<slug>/versions/<version>` in the browser. In the
desktop, use **Apps**, or launch `run.sh --app SLUG`. Plain `run.sh` no longer
starts the SQLite fixture service. `run.sh --business` explicitly opens the older
UI examples, which retain their isolated fictional SQLite data and draft/outbox
files for regression testing. The published counterparts are in `services/calliope/examples/ui-apps/demos`;
see that folder's README for the remaining fixture-only features.

For agent visual checks, use `python3 business/capture_published.py SLUG
/tmp/app.png --width 1440 --height 1000` from the desktop client directory.
It uses a temporary Xvfb display and profile with the existing authenticated
Calliope connection, captures GPUI, then stops its processes and removes the
temporary profile. No fixture server or SQLite is involved. See
[native testing](NATIVE_TESTING.md) for interaction checks and limitations.

Build the shared web host with `npm run build -- --config vite.published.config.js`
from `business/web`. The Calliope server Dockerfile builds and includes those
assets; generated bundles are not separate per-app artifacts. Assets must be
retained when serving historical definitions that reference their hashes.

## SQL resources and composition

Parameters may bind previous query outputs as well as `/state/...` input. The
runtime derives a dependency graph from those paths, adds explicit `dependsOn`
edges, and rejects cycles. Each query may set `timeoutMs` (100–8000) and `cacheMs`
(0–300000, default uncached). The bounded cache is scoped to actor, app version,
source definition and bound SQL. Writes invalidate that app's cache. Query
receipts expose timing, lineage, dependencies, cache hits and errors.

A decision is an ordinary named SQL binding: `SELECT rvbbit.decision_tree($1::jsonb,
$2::jsonb, 4)` can consume evidence from earlier bindings. Registered MCP functions
and `rvbbit.mcp_call(server, tool, args_jsonb)` use the existing SQL operator path;
the app runtime adds no per-service adapters. **Only repeatable reads belong in
refreshing queries.** PostgreSQL read-only transactions cannot undo external API
side effects. Put external writes in explicit `manifest.actions`; their existing
external-attempt receipts prevent an uncertain retry from issuing a second call.
Do not put credentials in app JSON. Use the connection registry.

Optional declarative extensions:

- `fragments`: named `{root, components}` flat component trees.
- `repeat`: empty container ID → `{path, fragment, key}`. Rows supply `{item:
  "/field"}` bindings; generated IDs are stable across row reordering. Grid rows
  use a stack whose children become cells. At most 100 rows / 500 expanded nodes.
- `switch`: empty container ID → `{path, cases: {value: fragment}, default?}`.
  A query can select a whole supporting region. Stable editors stay outside it.
- `forms`: `[{path:"/state/draft", source:"/record", key:"id"}]` initializes a
  form and changes it when the selected record changes. Polls preserve edits.
  Events can explicitly `reset: ["/state/draft"]` after a save or discard.
- `watch`: state paths that trigger a 450 ms debounced refresh in both hosts.
  Filters still use `CFilterBar`; writes remain explicit.

Everything materializes into the ordinary A2UI component/data messages. This is
not an arbitrary code evaluator or a second data engine. Native hosts retain
per-record drafts and navigation history; web forms still have one active record.

Optional `navigation: {paths: ["/state/page", "/state/selectedId"]}` identifies
locations independently of editable form values. State identity paths cannot be
inside a declared form. Older definitions infer identity from state-bound switch
selectors and SQL parameters driving form sources, excluding watched editor
paths. The server returns read-only state events for matching already-cached
views offline. Clients do not execute SQL or decisions offline.

Optional `records` declarations expose stable record links and SQL-backed summaries
for personal live Home pins. See [portable records](RECORD_PINS.md) for the contract
and desktop/Android behavior.

Snapshots advertise `record_drafts: true`. Native clients can then include a
`drafts` list of `{path, value}` entries for declared forms. Entries must have a
unique form/key pair; at most 200 fit within the existing 160 KB request limit.
They are version-scoped transient inputs, just like `state`. The runtime hydrates
the matching record before dependent queries, retains the active form's latest
input, and honors explicit action resets. Local attachment IDs are omitted from
these read hints. Legacy clients omit `drafts` and retain their current behavior.

## Vega-Lite

Both hosts use Vega-Lite 6.1. Inline and named datasets, layers, facets, repeat,
concatenation, calculated fields, aggregation, windows, lookup, regression,
density, statistical marks, and inline GeoJSON render through the real engine.
The gallery exercises 21 families plus four operational charts. Unit/layer views
fit the viewport; composed views get bounded child sizes and the complete output
scales to fit its container. Up to 1 MB / 5000 input rows, 960 px requested height.
External data URLs, hyperlinks and image marks remain disabled. Preload datasets
through SQL instead. Native tooltips/clicks support rectangles and circle/square
points, not arbitrary paths, brush selections, or interactive parameter widgets.
Native export has an eight-second budget and a bounded picking scene.

Published apps now support the old online business workflows and semantic
variants. Shared attachments and native per-record draft recovery are supported. Multiple
pending reports per app and case canvas pins remain fixture-only. UI Studio accepts the canonical `calliope.app.v1` definition, including fragments,
through the shared preview contract; its action previews stay inert. See
`UI_STUDIO.md` for supplied-data and authorized server-read modes.

## Work lists and reactive regions

Catalog v5 adds `CScrollArea`, `CRecordItem`, and `CReactiveRegion` on both hosts.
Scroll areas bound a child to a maximum height (160–1000 px, capped at 60vh),
retain their position on refresh and contain wheel events at the edges.
Record items compare `recordId` with bound `selectedId`, expose selected state,
and dispatch `onSelect` with `{id: recordId}`. Native probe `press` uses the same
activation path; render inspection reports scroll geometry and offsets.

Reactive regions render the normal child graph with bound `status` and semantic
`revision`. The published host exposes pending watched-input evaluation without
blanking the previous child. A changed revision fades in for 240 ms; web reduced
motion disables that animation. Equal outcomes on polling do not retrigger it.
Hold/resume remain declarative app events. The demos reset a hold on page/record
navigation and keep the working-note editor outside the switched subtree.

Responsive work lists use `CScrollArea.height: "available"` (also the default).
Both renderers measure its unscrolled position below headers and filters and
use the remaining app viewport height, leaving a bottom gutter. The optional
`minHeight` (160–600 pixels, default 160) keeps a small usable list when the page
is taller than the window; ordinary page scrolling then remains available.
Scrolling does not change the measured height. A numeric `height` retains the
bounded-list behavior for compact embedded lists. Short lists remain content-sized.

`CStack.collapse` now uses the stack's measured content width: `phone` at 600px,
`tablet` at 900px. A narrow pane can stack its columns even in a wide window.
Resize and reflow are local layout operations, preserve component IDs/drafts,
and do not invoke SQL or decisions. Prefer these shared behaviors over per-app
window listeners or hardcoded header-height subtraction.

## Publishing and client versions

`create_live_app` / `update_live_app` remain the publication path. The server
computes `manifest.ui_runtime` from `app.json`: the preview-compatible
`definition_sha256`, schema, catalog ID/revision, and required components,
including inactive fragments. Compare that hash with the inspected/captured
preview before treating a release as the tested definition. Publication also
validates its named actions; preview actions remain simulated.

Snapshots include `version`, `latest_version`, `release`, and authenticated
`viewer`. The app catalog includes `latest_version` and `ui_runtime`. Native
clients explain unsupported schema/catalog/components and offer the browser.
The supported catalog revision is compiled into each client. Definitions using
that catalog can update without rebuilding desktop or Android binaries.

An open or cached workspace stays on its loaded version. **Use vN** is explicit;
finish/discard drafts and resolve pending submissions first. The switch loads
fresh state from the target definition, without carrying old fields into a new
schema. Failed fetches keep the previous workspace. There is no automatic draft
migration. A normal read refresh does not change app versions.

Browser drafts and frozen pending requests are stored in IndexedDB, scoped by
origin, signed-in viewer, and app. Reload authorizes with the server before
restoring the original version. Pending commands are never replayed by reload
or polling; Retry action reuses their original key, version and payload.
Concurrent-tab writes use a compare-and-write transaction; a stale tab is told
to preserve/copy its edits and reload instead of overwriting newer work.

Unversioned web links use the current shared host for framework fixes. Explicit
`/apps/{slug}/versions/{version}` links keep their immutable definition while
using the current platform-owned native-app host. Hashed
renderer assets are archived on startup/publication in
`WAREHOUSE_CALLIOPE_DIR/ui-app-assets`, alongside existing durable server files.
Keep that directory in normal server volume backups; do not purge it as a cache.
Archive the outgoing image's assets before the first deployment of this change.

## Browser links from native workspaces

Catalog version 8 adds `CLink` for handing off to HTML dashboards or another
browser surface. Use a readable `label` and a literal or bound `href`, for example:

```json
{"id":"analysis","component":"CLink","label":"Open visit analysis","href":"/apps/visit-analysis"}
```

Instance paths resolve against the client's configured browser address. External
links require HTTPS. Native clients persist the current draft before opening the
OS browser; Web opens a separate tab. This is ordinary navigation, with no SQL
command or business action receipt. Inert previews do not open links; UI Studio
records a simulated `open_link` event so scenarios can check the resolved target.
An empty bound URL renders disabled. A definition using this component requires
a version-8-capable native client; publishing later app revisions does not require
another binary build while they stay within its supported catalog.

## Record navigation between apps

Catalog v9 adds `CRecordLink` with `label` and `reference:{path:"/target"}`.
The bound value is `{slug,record,id}`, or null for an unavailable destination.
Use SQL to construct references from actual row keys. The destination's existing
`records` declaration resolves the location and its ordinary authenticated SQL.
Native opens the record inside the client and retains the previous app/record
for Back; its drafts remain in their account-scoped journals. Pending submissions
must be resolved before following a link. Web persists its journal before
following the normal `/apps/<slug>#record=...` URL. A stored draft must not
silently redirect an explicit record link to a previously selected record.

Studio previews emit `open_record` with the resolved reference and
`simulated:true`; they neither open another app nor perform business actions.
General mobile chat recognizes same-instance record URLs and uses native
navigation. External destinations retain the normal browser handoff.
