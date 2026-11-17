---
name: calliope-live-dashboards
description: Technical dashboard implementation, repair, publication and verification for a scoped specialist assignment. Covers warehouse queries, HTML runtime, maps, charts, theme, upload and capture.
---

# Specialist dashboard implementation

Calliope owns the overall task and Playbook. Follow the exact delegated assignment,
current-turn evidence and pinned design profile. Do not read or execute coordinator
Playbooks, adopt their workflow, delegate review to another specialist, or expand a
review assignment into a rebuild. Ask Calliope if a missing decision blocks the scope.
artifact_runtime_help supplies focused API documentation; dashboard_template supplies
an optional technical starter. Neither is a coordinator Playbook.

## Implementation

1. Reuse handed-off query evidence and decisions. Discover only the evidence needed
   for your assigned scope; use bounded semantic/Library searches and governed metrics.
   Do not repeat successful discovery after compression. Do not browse raw schemas.
2. Reuse supplied successful query results. Prove only new or changed named flat SELECT
   queries with run_sql_multi, or recheck a specific integration mismatch. Pre-aggregate many-side
   facts before joins. Keep complete-population KPIs separate from limited detail rows.
3. Use artifact_runtime_help for the API topic you need. Load dashboard_template once only
   when a starter is useful. Preserve the bridge and normalization; choose presentation
   from the user's visual prompt and references. Apply only an explicitly pinned profile's
   actual constraints. If unpinned, author the palette, fonts, materials and composition
   for the task; do not suggest or attach a house style. Runtime examples do not prescribe
   an aesthetic. Load chosen font assets and verify actual rendered typography.
4. Write the shell and then section-sized edits to a file in /opt/data. Do not stream
   the whole large document in one tool call or regenerate it to repair one section.
   After two exact patch misses, reread that section and patch the actual current text.
5. Run calliope-web-check <path> via terminal and fix real errors. It is a shell command,
   not an MCP tool. Do not install browser tooling; use browser_exec and capture_live_app.
6. Upload that exact path using upload_artifact(source_path=...). Publish with its returned
   handle, keep the artifact private, and return the published URL to Calliope.
7. Capture once after publication with full_page=true. Check all expected queries,
   query failures, page errors, and an interactive filter or drilldown. Apply any scoped
   corrections and recapture if needed. Calliope arranges independent reviews.

For a review-only assignment, inspect and return findings as requested; do not publish
changes unless assigned. The handoff must state what was produced, validation evidence,
and unresolved issues. Never claim a scratch file or a successful upload is publication.

## Hosted runtime primitives (already on every published page)

The hosted page shim and the capture harness inject these on `window`; the Warehouse
`artifact_runtime_help(topic="maps"|"queries"|"charts"|"theme"|"verification")` returns focused
API contracts without a starter layout or a coordinator Playbook. Use these helpers instead
of importing your own libraries. Once a tool is identified, follow its invocation and included
parameters; request an exact schema only when deferred, rather than repeating capability searches.

- `rvbbitQuery(sql, {limit})` — the governed data bridge (the template's `composePayload` batches
  named queries into one round trip).
- `rvbbitTheme.tokens()` / `rvbbitTheme.color(cssOrToken)` — the live design-profile tokens
  (light/dark palettes, fonts) as sRGB for anything you draw by hand. Never freeze theme values
  into CSS; the page re-themes at runtime.
- `rvbbitCharts.embed(selector, vegaLiteSpec, opts)` — vendored Vega-Lite, themed and
  capture-safe (`opts.onSelect` for cross-filtering), for deliberately chart-shaped comparisons
  and time series.
- `rvbbitMaps` — vendored Mapbox GL, themed to the profile, token supplied by the appliance
  (never embed one): `rvbbitMaps.enabled`, `await rvbbitMaps.map(container, opts)`,
  `rvbbitMaps.geojson(rows, {lon, lat})`, `rvbbitMaps.bounds(fc)`, `rvbbitMaps.color(token)`,
  `rvbbitMaps.style(mode)`, `rvbbitMaps.ready()`. Every map on a hosted page goes through it — no
  Leaflet, raster tile URLs, or hand-drawn SVG "maps"; when `rvbbitMaps.enabled` is false, say so
  on the page and fall back to a table. Vendored library URLs live under
  `/vendor/<lib>@<version>/` (`window.RVBBIT_VENDOR`).

## Revising an existing artifact

File authoring and upload are for new documents and for restructuring. For every other change to a published
artifact — a limit, a query, a colour, a new panel, a fix — do **not** retype the document:

1. Read the current source as plain text: `get_live_app(slug, part='html')` (or
   `get_dashboard(slug, part='html')`). Never edit from memory of an earlier version.
2. Apply exact-match edits server-side: `update_live_app(slug, edits=[{"find": "<exact current
   text>", "replace": "<new text>"}, …], notes=…)`. Copy `find` verbatim from the read, whitespace
   included; make it long enough to be unique (or set `"all": true` deliberately). A stale `find`
   fails with `EDIT_NOT_FOUND` and nothing changes — re-read and retry; never fall back to a
   whole-document rewrite to get past it.
3. Read the result's `advisories`. `SECTIONS_REMOVED` lists headings/elements the new version no
   longer has; unless the user asked for that removal, restore them (edits again) before capturing.
   `structure.headings` is the page's section inventory — compare it with what the user expects.

Retyping a 15k-character document under an output cap is how unrelated sections disappear
silently; edits can only touch what they name.

## Query limits

- The live bridge defaults to 1,000 rows.
- Keep aggregates, KPIs, and trends in their own flat queries so they represent the complete population.
- For an intentional detail grid, set `limit` on that `composePayload` part or pass `{limit: N}` to `runSql`/`rvbbitQuery`/`callMcpTool`.
- Explicit limits remain bounded by the appliance artifact ceiling. Inspect `requested_limit`, `applied_limit`, and `truncated`; never silently present a truncated detail result as complete.


Read [the technical reference](references/publish-and-capture.md) for SQL, bridge, theme, upload and capture details.

## Assignment boundaries

Use the handoff's settled decisions and supplied evidence as build inputs. Answer only
its open implementation questions and deliver the assigned outputs. Do not restart
narrative selection, schema exploration, or independent analytical review when those
results were supplied. A focused integration check is appropriate when a specific fact
blocks implementation. With adequate inputs, write a useful first artifact before more
optional investigation. If a settled decision conflicts with observed evidence, use
ask_calliope with decision_to_reopen and contradicting_evidence, then end the blocked
round. Report the exact file, validation result, and publication receipt in your handoff.
