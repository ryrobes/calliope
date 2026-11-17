# rvbbit live-dashboard boilerplate

A drop-in starter for building live dashboards on top of the rvbbit MCP server. Ship `dashboard-boilerplate.html` as an example in the MCP (e.g. as a skill asset or a `publish_dashboard` template) so any generated dashboard inherits the same data plumbing and look.

## How the live data works

The artifact never calls the network directly — the in-app sandbox blocks it. Instead it calls a host-provided bridge:

```
artifact  ->  window.cowork.callMcpTool('mcp__<server-id>__run_sql', { sql })
          ->  Cowork host  ->  your MCP server  ->  warehouse
```

Auth is the connector authorization the user grants once when adding the MCP server. No login inside the artifact, nothing re-prompts on refresh, and DB credentials never reach the browser.

## The three rules baked into the template

1. **Wait for the bridge.** `window.cowork.callMcpTool` is injected a moment after the inline script runs; calling it immediately hangs. `waitForBridge()` polls for it first.
2. **One round trip, many flat queries.** Each `callMcpTool` adds ~1.5s of fixed host overhead, and several fired in parallel can hit a concurrency/timeout cap — so `composePayload()` batches every part into **one** `run_sql_multi` call. But each part stays its own FLAT query on the wire: routable by the accelerated engines, visible to the catalog/source map, and individually promotable later. Never hand-write a `json_build_object` payload query.
3. **Declare the tool.** The fully-qualified `mcp__<server-id>__run_sql` must be listed in the artifact's `mcp_tools`, and the id must match your server.

On hosted pages, `rvbbitQuery` takes precedence even when the host also supplies a
Cowork compatibility shim. Independent parts run concurrently. Inside Cowork,
the template keeps the single batch call; individual-call fallback is permitted
only when `run_sql_multi` is explicitly unavailable. A SQL timeout, permission
error, or malformed response never triggers a replay of the successful queries.

For independently loading regions, use the optional `onPart` callback. The
renderer functions in this example belong to the artifact and accept the rows
or scalar shape declared in `PARTS`:

```js
const generation = ++loadGeneration;
try {
  await composePayload(PARTS, {
    onPart(name, {data, error}) {
      if (generation !== loadGeneration) return; // ignore an older refresh
      if (error) showRegionError(name, error.message);
      else renderers[name](data);
    }
  });
} catch (error) {
  if (generation !== loadGeneration) return;
  // QUERY_ERROR parts have already been delivered above. Keep their good data.
  if (error.code !== 'QUERY_ERROR') showLoadError(error.message);
}
```

Initialize `let loadGeneration = 0` once. Without `onPart`, the existing return
shape is unchanged. A partial failure throws `QUERY_ERROR` after all parts
settle, with `error.results` containing successful parts and `error.errors`
identifying failed parts. Callbacks arrive progressively on the hosted path;
Cowork delivers them when its batch response arrives.

`capture_live_app` accepts `data_timeout_ms` for a bounded HTML data wait
(500–60,000 ms; normally up to 60,000, returning early when ready). This is separate from the post-load `wait_ms`
paint delay and does not extend SQL execution timeouts. Captures run batch
queries concurrently, like the hosted bridge. `verification.status` distinguishes
`ready`, `no_queries`, `data_pending`, `query_failed`, and `render_failed`.
Partial and failed captures carry a visible diagnostic banner; automatic feedback
allows two diagnostic turns separately from the two visual reviews. A pending
capture is not automatically re-rendered merely to generate feedback.
A saved screenshot or `no_queries` result alone does not prove that a live
dashboard rendered its intended data; inspect the final version's image.

## Engine gotchas (rvbbit_native read-only guard)

**Stay engine-portable.** A query routes to the accelerated engine only when
every function it uses is supported there; otherwise it falls back to native
Postgres, which is 10–50× slower on wide tables (measured: an unsupported
`to_date` mask in a KPI query = 11 s native vs 0.2 s accelerated). Fine:
`make_date`, `substr`, `date_trunc`, `extract`, `split_part`, `cast(x as t)`,
and `to_date` / `to_char` with common masks (`YYYY-MM-DD`, `MM/DD/YYYY`,
`YYYYMMDD`, `YYYY-MM`, `Mon`, `HH24:MI` …). Native-only by design:
`to_timestamp(text, mask)`, `format()`, `age()`, regex operators, unusual masks.
Text dates such as `'08/11/2019'`: `to_date(d, 'MM/DD/YYYY')` works;
`make_date(cast(substr(d,7,4) as int), cast(substr(d,1,2) as int), cast(substr(d,4,2) as int))`
is fastest.


- `::type` casts are rejected (`unsupported token: ::json`). Use `json_agg` / `row_to_json` bare, or `cast(x as t)` only if unavoidable.
- Reserved words can't be bare column aliases — `month` fails, use `ym`. Sanity-check aliases you template.
- Numeric columns may arrive stringified (`"4649793.4000"`) depending on the path — `fmt.*` helpers coerce with `+x`, or `parseFloat` where you do math.

## Allowed libraries

The sandbox CDN allowlist is integrity-pinned. Only these load; anything else is silently blocked:

- Chart.js 4.5.0 — charts
- Grid.js 5.0.2 (+ its theme CSS) — sortable/searchable tables
- Mermaid 11.10.0 — diagrams (commented out by default)

> **Hosted `/d/<slug>` pages are not sandboxed** and get pinned, self-hosted copies of the
> runtime libraries at `/vendor/<lib>@<version>/<file>` (Vega, Vega-Lite, vega-embed, Mapbox GL,
> Grid.js + theme; see `window.RVBBIT_VENDOR` for the exact URLs). Prefer those over CDNs on
> hosted pages: same-origin, immutable, work air-gapped, and captures see them. `rvbbitCharts`
> and `rvbbitMaps` already use them.

> The starter provides minimal structural CSS and bridge mechanics, without a palette or typeface. Follow the user's visual brief and author the presentation; technical scaffolding is not a design recommendation.

### Charts on hosted pages (Vega-Lite via `rvbbitCharts`)

The hosted page shim injects `window.rvbbitCharts` (Vega-Lite, loaded on demand
from a pinned build) and `window.rvbbitTheme`. `rvbbitCharts.embed(el, spec)`
renders SVG (Artifact Lens can read it), sizes to the container, and re-embeds
when the resolved light/dark mode changes. Authored `spec` and `opts.config`
control colors, fonts and scales. `opts.config` deeply merges over runtime
settings and survives both data updates and theme changes. With no profile or
authored tokens, typography/text follow the page and the chart library supplies
its normal category defaults; Calliope injects no branded palette. Explicitly
selected profile tokens can supply defaults. Chart.js remains available inside
the Cowork sandbox.

```js
await rvbbitCharts.embed('#trend', {
  height: 240, data: {values: data.by_month},
  mark: {type: 'line', point: true},
  encoding: {x: {field: 'month', type: 'temporal', title: null},
             y: {field: 'stops', type: 'quantitative'},
             color: {field: 'violation_type', type: 'nominal', title: null}},
  params: [{name: 'pick', select: {type: 'point', fields: ['violation_type']}}]
}, {onSelect: (value) => applyFilter(value)});
```

### Maps (hosted appliances)

> **Rules of the road:** every map on a hosted page goes through `rvbbitMaps` — never Leaflet /
> OpenLayers / raster tile CDNs (they ignore the light/dark design profile, add third‑party runtime
> dependencies, and `publish_dashboard` reports them as `MAP_NOT_RVBBITMAPS`). `rvbbitQuery` returns
> 1,000 rows by default: point layers must pass `{limit: N}` (up to the appliance cap, usually
> 10,000) or aggregate in SQL — otherwise the map silently plots a subset. Treat sentinel
> timestamps (epoch 0 / 1970 values some feeds emit for "never") as unknown, not as "56 years ago".
> Revise published pages with `get_live_app(slug, part='html')` + `update_live_app(slug,
> edits=[{find, replace}])` — exact-match, server-side, loud on a miss — not by retyping the
> document; full-html updates are for restructuring and report `SECTIONS_REMOVED` when they drop
> headings or ids.

Hosted appliances carry a per-client, URL-restricted Mapbox public token. The
hosted page shim (and the capture harness) inject `window.rvbbitMaps`; the
`dashboard_template` tool's `maps` block says whether it is available here and
returns the API and a recipe. It is **not** available inside the Cowork sandbox
(CSP) — build maps for `/d/<slug>` pages.

```js
const rows = data.stops;      // aggregate in SQL first: ≤ ~5,000 features
const fc = rvbbitMaps.geojson(rows, {lon: 'lon', lat: 'lat'});
const map = await rvbbitMaps.map(document.getElementById('map'), {zoom: 9});
map.on('load', () => {
  map.addSource('stops', {type: 'geojson', data: fc});
  map.addLayer({id: 'stops', type: 'circle', source: 'stops',
    paint: {'circle-radius': ['interpolate', ['linear'], ['get', 'n'], 1, 2, 500, 14],
            'circle-color': rvbbitMaps.color('--map-point-color', getComputedStyle(map.getContainer()).color), 'circle-opacity': 0.55}});
  const b = rvbbitMaps.bounds(fc); if (b) map.fitBounds(b, {padding: 32});
});
```

`rvbbitMaps.map()` returns a Mapbox GL map already themed to the design profile
(light/dark) with `preserveDrawingBuffer` on so captures are faithful; pass
`onThemeChange(style, mode, map)` if you want to follow theme flips (re-add
sources/layers on `style.load`). `rvbbitMaps.ready()` hands you `mapboxgl`
directly for full control. Give the container an explicit height, escape any
row value you put in a popup, route every map paint colour through
`rvbbitMaps.color(...)` (profile tokens are `oklch()`, which Mapbox GL rejects),
and remember there is no browser geocoding — coordinates come from the
warehouse.

### Optional TanStack Charts laboratory

The default remains Chart.js. When a user explicitly wants to try the semantic SVG path,
call `tanstack_chart_template` instead. It returns a complete bespoke HTML example and a
matching semantic manifest. The example loads the Warehouse-owned, immutable
`/charts/rvbbit-tanstack-charts-0.16.2.js` asset; no framework, npm install, import map, or
artifact-side build is required.

`window.RVBBIT_CHARTS` exposes the native TanStack grammar, D3 scale factories/curves, and
`mountRvbbitChart()`. The wrapper does not replace TanStack's definitions. It only annotates
the keyed SVG scene with query, mark, row, field, value, and semantic-object metadata, then
emits `rvbbit:chart-select`. That lets ordinary application DOM react to a selected mark and
lets Artifact Lens inspect the exact source datum. Chart.js and arbitrary HTML/JS remain
available in the same artifact.

The runtime is deliberately pinned because TanStack Charts is alpha. Screenshot, PDF,
thumbnail, and semantic-enrichment capture paths inline the same pinned asset when rendering
stored HTML through Playwright. The unified-origin Caddy route forwards `/charts/*` to
Warehouse MCP.

Optional headless cores are also hosted independently:
`/charts/rvbbit-tanstack-table-core-9.2.4.js` exposes `window.RVBBIT_TABLE`, and
`/charts/rvbbit-tanstack-virtual-core-3.17.10.js` exposes `window.RVBBIT_VIRTUAL`.
Both expose native root exports plus `version`, without UI components or React.
Use Table's **v9** API, not v8 examples. See [`charts/README.md`](charts/README.md)
for all imports. Legacy Charts 0.3.1 remains available for existing publications.

## Adapting it

Edit only the two marked blocks:

- **EDIT 1 — CONFIG:** set `SERVER_ID`, the title, and the `PARTS` map. Each part is `{ shape:'row'|'list', sql:'select ...' }` — one FLAT query per concern. `row` → first row as an object, `list` → array of row objects (preserves the query's `ORDER BY`).
- **EDIT 2 — RENDER:** read `p.<name>` (object or array) and lay out KPIs / `chart()` / `table()`.

The `load()` boot logic, bridge helpers, formatters, and chart/table wrappers between the `FRAMEWORK` markers stay as-is.

## Visual direction is explicit

New chats, direct MCP publishers and API sessions start **without a Design Profile**.
Follow the user's visual prompt and references. Do not suggest or attach a house
style; without a pin, author the palette, typography, materials and composition
for the task. Load any chosen font assets and verify the rendered font in the
browser, since a CSS family name alone can silently fall back.

- A pinned Calliope turn supplies its exact profile to publications and specialist
  handoffs. `dashboard_template` / `live_app_template` return that selected contract
  only; they do not add fonts or styling beyond it.
- An unpinned turn removes any agent-supplied or inherited `manifest.design_profile`
  from the new published version. Historical versions are unchanged.
- Direct clients can express an explicit user selection with
  `manifest.design_profile = {"name": …}`, `{"profile_id": …}` or `{"version_id": …}`.
  A frozen selection can carry forward on revisions; `null` removes it. Missing
  selections do not default, and invalid references fail instead of substituting
  Adaptive Calliope. Publication receipts report which choice applied.
- If the selected profile requires live viewer tokens, consume them and repaint
  from already-loaded data when they change. Keep filters intact and never rerun
  queries for a theme change. Unpinned artifacts may use their own variables or
  define `--artifact-*` tokens. A colors-only profile does not dictate typography.

## Describe the visible business objects

Warehouse automatically compiles a semantic map after an HTML artifact is published. It
renders the exact saved version, inventories visible values and live query evidence, asks the
RVBBIT agent operator for candidate definitions, and independently replays each proposed SQL
definition before attaching it. This work is asynchronous and never blocks or rejects a good
dashboard publication.

The template also includes `bindBusinessObject(id, selector, rawValue, context)` for cases
where you want to author a particularly important or unusual definition yourself. Give that
value a stable DOM id, call this helper after rendering it, and pass a matching
`manifest.semantic_map` to the publish or live-app tool. Authored ids and bindings override
generated ones; the compiler fills the remaining gaps. The original manifest and the verified
compiler overlay are both keyed to the same immutable artifact version.

Each object contains:

- `meaning`: a label, plain-language description, optional unit, and human-readable formula;
- `bindings`: stable DOM selectors or element ids;
- `parameters`: typed filter/context inputs with safe publication defaults;
- `evaluator`: independent read-only SQL that recreates the value, plus its result shape and
  value column;
- optional display hints and source query names.

For example, an authored JavaScript `avg(rows.map(r => r.value))` callout should have an evaluator that
computes that average in SQL and returns one row as `value`. This contract uses arbitrary SQL;
it does not require creating a cube, governed metric, or extra database object. At runtime,
pass the exact current filter values in `context`. The Artifact Lens can then explain what the
number means and independently recreate it without guessing from the dashboard's broader
query trace.

For repeated values, reuse one parameterized definition and bind each DOM element separately:

```js
document.querySelectorAll('.regional-rate').forEach((element, index) => {
  bindBusinessObject(
    'regional_drop_rate',
    element,
    rows[index].drop_rate,
    { region: rows[index].region }
  );
});
```

The hosted runtime stores the raw value and context per element, so selecting the APAC label
replays APAC SQL even when EU, LATAM, and US labels share the same semantic definition.

## Two deployment targets

| | In-app Cowork artifact | Your `publish_dashboard` host |
|---|---|---|
| Data call | `callMcpTool('…run_sql')` | injected `rvbbitQuery()` |
| Row shape | objects keyed by column | positional arrays |
| Auth | connector OAuth (once) | session cookie on your domain |
| Sandbox | yes — CDN allowlist only | no — any stack |

This template targets the in-app path. For the hosted path, swap `makeRunSql` for your `rvbbitQuery` and adjust the row parsing.

### Publication checks and concurrent edits

HTML publication compiles inline classic scripts and ES modules before saving a
revision. It does not execute JavaScript, fetch imports, or run SQL. A syntax
failure returns `INVALID_JAVASCRIPT` with the script and available HTML line;
the rejected candidate does not become a version. Fix that candidate before
capturing. External scripts, event-handler attributes, and runtime correctness
still need browser verification.

Read `get_live_app(slug, part="source")` to get the version and HTML together,
then pass that number as `expected_version` to `update_live_app`. The compatible
`get_dashboard` / `update_dashboard` pair supports the same fields. The version
check runs under the publication row lock. `VERSION_CONFLICT` means another
revision landed: read and reconcile the new source, rather than retrying an old
replacement with a newer number. Omitting the field preserves legacy behavior.

A builder's data handoff should contain complete SQL for every region, the exact
returned aliases/result shape, and evidence that those queries executed. A prose
description of a metric is not an executed query. Publication syntax checks do
not certify the meaning of SQL or reconstruct missing joins.
