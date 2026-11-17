# Feature parity matrix

This matrix is the working contract between the running `rvbbit-lens` reference
on port 3000 and Data Rabbit for macOS. “Source parity” means the exact product
implementation is present in this repository. “Runtime verified” is only
checked after the packaged app has exercised the corresponding behavior.

Runtime labels are deliberately conservative:

- **Verified** — the packaged `.app` has an automated assertion for the family.
- **Partial** — a representative path is green, but important paths remain.
- **Pending** — source is present, but packaged runtime coverage has not landed.

| Feature family | Representative surfaces | Source parity | Runtime verified |
| --- | --- | --- | --- |
| Desktop shell | menu bar, icons, windows, focus, drag, resize, minimize, tile mode | Yes | Verified |
| Workspaces and scenes | workspaces 1–5, scene slot, scene tray, snapshots, thumbnails | Yes | Verified — switching, native snapshot, save, relaunch |
| Postgres connections | connection registry, default connection, password redaction, SSL | Yes | Verified |
| SSH tunneling | key file, inline key, password/passphrase, tunnel lifecycle | Yes | Verified |
| Finder and schema | schemas, tables, columns, vitals, stats, object DDL | Yes | Verified |
| SQL editing | CodeMirror, completion, formatting, block references, scrubbers | Yes | Verified |
| Query execution | transactions, cancellation, progress, detached runs, history | Yes | Verified |
| Results and inspection | virtualized grids, row inspector, single-cell callout | Yes | Verified |
| Visualization | chart inference, shelves, Vega, ECharts, Chart.js, Flint | Yes | Verified |
| Imports | CSV inspection, dialect inference, DDL, streaming import | Yes | Verified |
| Saved views and apps | view builder, saved apps, custom icons, app execution | Yes | Verified |
| Dashboards and plates | dashboards, plates, walls, actions, layout stamping | Yes | Verified |
| Postgres administration | monitor, locks, MVCC, maintenance, system health | Yes | Verified |
| Notifications and schedules | LISTEN, alerts, scheduled tasks, cron display | Yes | Partial — live LISTEN/NOTIFY, alert detail, eight-job pg_cron home, and recent-run history verified; schedule mutation pending |
| Desktop persistence | local storage, Lens SQLite mirror, homes, layouts | Yes | Verified — isolated quit/relaunch persistence |
| Assistant | chat, execution commands, identity, settings, voice input/output | Yes | Partial — identity rename, settings, summon/dismiss, and dock verified; model execution and voice pending |
| MCP | server catalog, install surface, incoming work, gateway secret | Yes | Partial — live incoming flow/activity and missing-gateway safety verified; install and gateway mutation pending |
| rvbbit discovery | extension/status detection and feature gating | Yes | Verified |
| Semantic operators | operators, receipts, semantic tests, specialists | Yes | Partial — live registry, flow inspector, receipts, test evidence, and specialist detail verified; billable execution intentionally pending |
| Routing and GQE | profiles, training, overlays, freshness, query engine views | Yes | Partial — live overlay pins, zero-profile training state, receipt trace, and non-routable GQE gate verified; training/GQE execution pending |
| Knowledge | KG explorer/browser, entity detail, extraction, merge review | Yes | Partial — live graph search/entity/evidence plus extraction and merge empty queues verified; extraction/merge writes pending |
| Models and capability fleet | model studio/settings, capabilities, Warren, fleet | Yes | Partial — live model resolution, 48-pack catalog/detail/generated SQL, zero-model training form, Warren, and Fleet verified; install/train/deploy pending |
| Hindsight and brain | Hindsight memory, document brain, source explorer | Yes | Partial — live Brain source/corpus plus memory evidence and graph interactions verified; recall mutation pending |
| Metrics, cubes, and costs | catalogs, creators, inspectors, proposals, KPI board, viz blocks, costs | Yes | Partial — live metric execution/resolved creator preview, viz-block version/preview, KPI catalog fallback, costs, cube inspector/creator/pack forms, and proposal inbox verified; writes and model drafts pending |
| Data movement | movers, sync mirror, Duck integrations | Yes | Partial — Fletch launcher gate plus Mirror and Duck empty states verified; live runtime/transfer data unavailable |
| Native lifecycle | `.app`, single instance, activate/reopen, clean server shutdown | Yes | Verified |
| Native menus | app/file/edit/database/desktop/view/window/help actions | Yes | Verified — 16 entries, 10 surfaces |
| Native storage | Application Support config, Lens data, logs, workspaces | Yes | Verified |
| Native security | sandbox, isolated preload, navigation/link policy, permissions | Yes | Verified |
| Packaging | standalone server, app bundle, ad-hoc local seal, DMG, ZIP | Yes | Verified — strict deep signature and both archives |

## Baseline observed

The rvbbit-enabled reference identified itself as Data Rabbit v4.1.3 with the
`rvbbit-dev · bench` connection active and rvbbit v4.0.11 installed. Its
initial desktop exposed Finder,
Scenes, SQL Scratch, Saved Views, Connections, Data Search, System Learning,
MCP Incoming, Capabilities, Apps, Kit Flow, Alerts, Document Brain, Hindsight,
System, Semantic, Knowledge, Metrics, and Cubes.

Every comparison run should preserve the reference data. Tests that mutate
database state must use disposable objects or explicit user-approved targets.

## Current packaged evidence

`npm run test:mac:smoke` launches the actual app bundle in an isolated
Application Support directory and checks Electron 39, the sandboxed bridge,
native chrome spacing, all 16 menu entries, native Scene capture, the
Connections window, and local Lens initialization. It also rejects a second
instance, closes and reopens the window through macOS activation without
changing the private origin, and runs a separate uninstrumented process probe
that quits with code 0 and leaves no bundled server behind. The same suite
drags and resizes an inner desktop window, minimizes it into the dock, restores
its saved geometry, switches focus between windows, and exercises tile mode.
It also asserts the bridge is frozen and capability-allowlisted, Node globals
remain absent, notifications are denied, camera capture is not granted, a
non-HTTP popup creates no window, and navigation away from the private origin
is blocked.

`npm run test:mac:rvbbit` copies the reference connection registry into another
isolated directory without modifying the original. It compares the packaged app
with port 3000 using only read-only operations:

- the same database, rvbbit version, 28 schemas, 359 tables, 990 functions, and
  eight extensions;
- schema-fingerprint caching, Finder vitals, Postgres stats, system health,
  locks, MVCC, and query-summary capability;
- a read-only `SELECT 1` against `bench`, executed through the native-opened SQL
  editor and asserted in the rendered result;
- a second authored SQL query with categorical/numeric rows, switched to the
  full View tab and asserted through classic Vega-Lite, Flint Vega-Lite, Flint
  ECharts, and Flint Chart.js with no fallback;
- a Finder search for `public.bigfoot_sightings` followed by a real table
  preview query;
- the published Engine Vitals dashboard in a script-only sandbox, including
  three live `rvbbitQuery` broker results;
- the Crime Overview plate and its navigation action to Crime Trends, the
  three-pane CRM wall, and stamping that layout back into desktop windows;
- the live public operator registry, a real operator's build/run graph, and a
  test-bearing operator's embedded Semantic Tests evidence without running a
  mutating battery;
- seven-day MCP incoming activity through the Flow, Activity, People, and Tools
  panels;
- Model Settings with a non-empty resolved model catalog and a real
  operator-to-model row;
- all enabled learned routing-overlay pins, derived from the same reference
  query as the panel;
- Routing Train's live zero-profile state, a receipt-backed Query Lens trace
  selected by a real query id, and the GQE menu's correct absence while the
  configured runtime reports `routes_available=false`;
- Knowledge Graph graph selection, label search, entity-detail navigation, and
  stored evidence for a real `db_catalog` node;
- the Knowledge Graph extraction dashboard and merge-review queue, both in
  their current zero-run/zero-candidate states without seeding or suggesting;
- the RVBBIT System Learning Brain source, its live indexed/document counts,
  switching to the admin corpus, and a real document row;
- Metric Catalog navigation into the bitemporal inspector, resolved SQL, a
  successful live metric result, and the definition/data-time labels;
- Metric Creator loading the healthy saved metric and resolving its SQL without
  saving; a real versioned Viz Block loading its version, contracts, links, and
  resolved-SQL preview; and KPI Board's honest zero-KPI/zero-observation state
  followed by its two-metric catalog fallback;
- the live Costs receipt total, Vega timeline, and bounded receipt table; and
- the honest no-job/no-event states for Temporal Mirror and Duck on the current
  reference, without creating either kind of state;
- Assistant identity/settings with a browser-local rename, the renamed native
  summon control, the CodeMirror chat dock, its welcome state, and dismissal
  without sending a model request;
- the MCP server catalog's live zero-server/zero-gateway state, its runtime
  installation guidance, and disabled server/catalog mutations while no
  gateway is ready;
- a real registered Specialist backend followed into its latency, reliability,
  backend, and live-test instruments without invoking the billable test;
- Hindsight's two-bank/two-memory overview, a real memory's stored evidence and
  links, and its non-empty Sigma graph;
- the live `engine_perf` cube from catalog to inspector, definition SQL, and
  semantic column lineage;
- Cube Creator's Manual, AI Propose, and From Pack modes, including the live
  `salesforce.opportunities` binding form and existing `engine_perf` editor,
  plus the current zero-proposal review inbox, without drafting, previewing,
  creating, accepting, or rejecting anything;
- the live `slow_frames` alert from the system overview to its full rule detail
  and history action;
- Warren's zero-node/zero-job operational states and Fleet's empty-registry
  topology with a rendered brain node, without dispatching probes;
- the public 48-pack capability catalog, a real Join Detective pack, and its
  generated manifest/registration SQL without installing it;
- Model Studio's live zero-model state and complete new-model training form
  without queueing a run;
- Data Mover's correct launcher suppression while no Fletch runtime is
  registered, plus GQE's equivalent non-routable gate;
- the scheduler's cross-database detection of pg_cron in its `postgres` home
  database, the live eight-job count, and expansion of
  `rvbbit_accel_tick` into its latest `succeeded` recent run, without changing a
  schedule;
- CSV delimiter, header, type-inference, and preview parity through the bounded
  import-inspection endpoint without creating a table;
- database/SSH secret redaction and capability-manifest path confinement; and
- native menu activation for Capabilities, SQL, Finder, Notifications, Scenes,
  Saved Views, command palette, Scry, tiling, and workspace switching.

`npm run test:mac:surfaces` reads the reference
`rvbbit.desktop_panels` help index with a read-only query, then mounts and closes
every visible launcher in the packaged app. The current run covers all 55
reference registry entries plus Hindsight, which is visible in the reference UI
but absent from its stale registry rowset: 56 typed feature windows with no
renderer exception. During this suite `DATARABBIT_READ_ONLY_TEST=1` suppresses
the product's best-effort panel-registry sync, so the test cannot update the
reference database.

`npm run test:mac:persistence` uses one isolated Application Support directory
across two complete packaged-app launches. The first launch captures and saves
a Scene and creates a Saved View with a description, bound connection, custom
database icon, and violet color; the second requires all metadata to survive,
reopens the Saved View, and asserts its saved `SELECT 1` executes to a rendered
single-cell result. This guards the stable-origin contract as well as the local
storage and Lens SQLite paths.

`npm run test:mac:write-workflows` starts an isolated PostgreSQL 18 cluster and
an in-process loopback SSH bastion, then drives the packaged server through its
real APIs. It verifies:

- direct connection creation/test/deletion and secret-safe responses;
- the 4.1.3 Deck publish route, including validation, read-only slide-query
  pinning, persisted runtime/spec HTML, and append-version behavior;
- certificate-verified PostgreSQL TLS, asserted from `pg_stat_ssl` (TLS 1.3 in
  the current run);
- pinned transactions with both rollback invisibility and committed data;
- token-addressed cancellation of a 30-second query in well under a second;
- the native SQL toolbar's manual transaction controls, formatter, query
  history, and Stop button;
- live `pg_stat_activity` elapsed/wait progress rendered in the SQL status bar,
  plus a detached command acknowledged in milliseconds and observed completing
  after its HTTP response;
- schema-aware completion, a drag-to-update numeric literal with debounced
  result refresh, and a resolved cross-window block reference that executes;
- Notification Center channel subscription, a UI-authored `NOTIFY`, persisted
  history rendering, toast delivery, and unsubscribe;
- Finder search for the disposable table and catalog-based reconstruction of
  its columns, constraints, and `CREATE TABLE` script;
- Postgres Admin health/object inspection, generation of reviewed maintenance
  SQL, execution of `ANALYZE`, and confirmation in `pg_stat_user_tables`;
- client filtering and row/field transposition over results, a 5,000-row
  virtualized result, context-menu row inspection, and the single-cell callout;
- CSV inspection, table DDL, streamed COPY progress, typed accepted rows, a
  quarantined invalid row, and the existing-table overwrite guard;
- SSH password auth, a pasted private key, and an encrypted key file with a
  passphrase; and
- cached tunnel reuse plus disposal when each connection is deleted.

All database files, client keys, configuration, and Application Support state
for this suite live under one temporary directory and are removed afterward.
The port-3000 `bench` reference is never placed in write scope.

The exact database counts are observations, not hard-coded expectations: every
run computes them from port 3000 and requires the packaged app to match.

The current reference also contains one stale metric definition:
`demo_sessions` still references a removed `texture` column in
`public.quakeql_episode1_texels`. The parity suite records that as reference
data debt and executes the healthy `frames_rendered` metric instead; it does not
rewrite the live bench catalog.

Mount coverage proves that the native runtime can load each feature family; it
does not replace deeper workflow tests. Rows marked Partial still need the
interactions named in their representative-surface column.
