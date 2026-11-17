---
name: calliope-live-dashboards
description: Build, revise, publish, and verify live Calliope/RVBBIT dashboards and HTML apps backed by governed warehouse queries. Use for dashboard requests, Stage presentation surfaces, drilldowns, live artifact publishing, adaptive design profiles, query-bridge limits, or dashboard capture and repair — including maps (rvbbitMaps / Mapbox GL, geojson, choropleths, point layers), charts (rvbbitCharts / Vega-Lite), and the hosted runtime theme (rvbbitTheme).
---

# Calliope live dashboards

Build live warehouse artifacts rather than static HTML snapshots. Use the hosted runtime APIs; do not invent a data bridge or bake query results into the document. `artifact_runtime_help` gives focused API references; `dashboard_template` provides an optional starter.

When delegating work with known findings, pass `context.assignment` containing
four arrays of strings: `settled_decisions`, `supplied_evidence`,
`implementation_questions`, and `deliverables`. Use existing findings and references;
do not perform extra preflight to populate the contract. State which step the specialist
owns, and preserve the story and analytical conclusions already settled. Calliope owns
coordinator Playbooks; specialists receive only their bounded implementation or review.

## Preferred ownership

When this skill is selected in the main Calliope profile, strongly prefer handing the work to `Iris` (Dashboard Studio) with `delegate_calliope_specialist(specialist_key="dashboard-studio", ...)` whenever the user's primary requested outcome is to create, rebuild, substantially revise, repair, validate, or publish a dashboard or live app. The managed specialist key is already known: do not list specialists first and do not ask the user for permission to delegate. Routing is step zero: once Iris owns the dashboard work, delegate before following the Workflow below. Do not run Library, catalog, SQL, template, or artifact preflight solely to prepare the handoff. The delegation tool automatically carries the exact current-turn design profile, selected surface, typed object references, evidence, and image attachments; use its `context` argument only for extra task-specific constraints. Never paraphrase or substitute for those pinned objects. Give Iris the concrete outcome and identifiers she still needs; do not rediscover evidence first or paste the whole conversation.

This is a preferred division of work, not a prohibition. Main Calliope may execute the workflow directly for a small edit, explanation, simple query or tool call; when the user explicitly asks Calliope to do it herself; or when the specialist is unavailable, has failed, or would clearly add more work than it removes. If this skill is running inside Iris's Dashboard Studio profile, execute the workflow directly and never delegate it again. After a specialist handoff, main Calliope remains responsible for relating the result to the user's broader conversation.

**An explicit user process wins over this default.** When the user prescribes who does what or an ordered procedure ("build it, then send it to Iris for a design review", "ask me before building", "publish, then wait for my feedback"), follow that division and that order exactly; the delegate-to-Iris default applies only to requests that leave the division of work unspecified. A requested *review* must come from someone who did not build the artifact: if Iris built it, Calliope (or the Analytical Skeptic, `analytical-skeptic`) reviews; if Calliope built it, Iris reviews. Never present a specialist's review of her own build as an independent review, and never fold a "publish, then wait for the user" step into the same turn as the next step.

**Hand-offs can be asynchronous.** `delegate_calliope_specialist` and `continue_calliope_specialist` take `mode="async"`: the call returns at once with the thread_id, the specialist keeps working after this turn ends, and her reply arrives in the conversation later as an agent message (a turn beginning with `[AGENT MESSAGE]`). Use async when you have independent work meanwhile — write the narrative, run the visual self-check, prove another query set, ask a second specialist — or when the user wants results as they land; use sync only when the very next step depends on the reply. After an async hand-off finish the turn normally and tell the user what is in flight; when the agent message arrives, integrate it (apply the review with edits, publish, re-capture) and tell the user only what changed. A specialist can send a precise question back with `ask_calliope`; answer it on the same thread.

**Scope decisions are questions.** A dashboard rooted in a question is easier to build well than a broad sweep. When the request names no question, audience, or scope, and the data forces a choice the user did not make — one borough of many, a rolling 30-day window because the source carries no more, one entity out of a set — say what you found and ask before building unless the user asked you not to stop. If you must proceed, state the scope you chose in the first line of the reply and on the page itself.

Applying or changing a pinned Design Profile on an existing dashboard is a substantive visual revision for ownership purposes, even when the resulting CSS change appears small. Delegate that request to Iris and let her verify the exact published profile version and rendered runtime.

## Hosted authoring mode

All hosted authoring now uses **HTML**. Dashboard mode is paused. Preserve the data bridge and author HTML/CSS/JS; choose presentation and chart libraries to match the agreed design. Existing published Dashboard widgets may be retained when revising those pages. The parent turn's current `authoring_contract` supersedes historical Dashboard-mode guidance.

New chats start without a Design Profile; apply one only when explicitly selected. If the user references a Playbook, read its exact version and follow the requested method. Page composition and validated data meaning remain separate concerns.

## Workflow

Follow the user's visual prompt and reference material. A Design Profile supplies
constraints only when the user actually pinned one; new chats start unpinned.
Without a pin, author the palette, typography, materials and composition for the
requested task. Do not suggest or attach a house style, infer a profile from an
example, or adopt the surrounding Calliope UI's appearance. Shared runtime APIs
and optional starters provide mechanics, not visual direction. With a pinned
profile, apply only its stated constraints alongside the user's brief.
Load chosen font assets and verify the rendered font in the browser; declaring
a CSS family alone can silently produce the same system fallback every time.

1. Use known capabilities directly. If an ability or method is unknown, search the system Library with `capability_search` using the user's analytical intent. This is the system-wide inventory of installed and available MCP capabilities, SQL functions/operators, packs, syntax, providers/models, and approved Playbooks. It is not limited to semantic SQL. Use relevant installed abilities; never install an available pack without the user's request.
2. Discover evidence through RVBBIT's semantic surfaces. Use `search_data` for Business Context plus the catalog KG/data KG. When a hit identifies a Value System—or the request asks what an outcome means, what contributes to it, or why it moved—use `search_business_context` and open the result with `get_business_value_system`. Prefer its promoted governed metrics and `business_metric_change` attribution path over reconstructing business arithmetic. Proposed systems are review evidence, not canonical truth. `search_data` also returns enriched descriptions, samples, column statistics, freshness/drift, and usage weighting. Do not begin by listing schemas/tables or querying `information_schema`/`pg_catalog`.
3. When the question depends on company language or unstructured evidence—tickets, meetings, policies, documents, people, projects, customer narratives—use `brain_facets` and `ask_brain`, then deepen only useful hits. Brain evidence complements warehouse data; it is not a substitute for relational lineage.
4. Only after semantic discovery has shortlisted physical relations, use `describe_table` when its additional detail is actually needed. Raw catalog introspection is a last-resort diagnosis for a demonstrably missing or stale catalog, not a discovery strategy.
5. Shape each concern as its own flat `SELECT`. Prefer governed metrics/cubes and relevant Library capabilities over reimplementing them with ad hoc SQL. Prove the named query set with `run_sql_multi` before authoring HTML. Pre-aggregate fact tables before joins that could multiply rows.
6. Use `artifact_runtime_help` for missing API details, or load `dashboard_template` when a starter is useful. Preserve the hosted bridge and result normalization. Replace starter styles and composition to follow the brief; retain theme hooks only when the selected profile needs them.
7. Keep ordinary query results tabular. Use charts only for deliberately chart-shaped comparisons or time series, or when the user requests them.
8. Apply any pinned design profile without freezing runtime theme variables. Keep decision context, freshness, filters, and the first useful signal visible in the initial viewport.
9. Write and patch the complete HTML in Hermes' normal writable scratch volume, `/opt/data` (for example `/opt/data/sales-dashboard.html`). Write a long document in bounded pieces — the shell first, then section-sized writes or edits — never as one enormous single generation: a model response that streams for minutes is the most likely thing a provider kills mid-write, and a small piece retries cheaply (an xAI stream drop cost four whole turns on 2026-08-19). Edit discipline: when a `patch` misses its `old_string` twice, re-read that exact section with `read_file` and rebuild the patch from what is actually there — never fall back to sed/regex one-liners through `terminal` or `execute_code` (they miss silently as "replaced 0" and burn turns; five patch misses in one 2026-08-21 run were all stale-context misses), and never regenerate the whole file to dodge one edit. Run `calliope-web-check <path>` and fix real parse/lint errors — it is a shell command, run through `terminal` like any other; it is not in the tool catalog, so never `tool_search` for it. Page verification happens with `calliope-web-check`, `browser_exec`, and `capture_live_app` — the sandbox has no browser to install, so do not attempt to download Playwright or similar. Upload that exact path once with `upload_artifact(source_path=<path>)`; never copy it to another handoff directory, read it back into model context, or reassemble it in content chunks. In the hosted appliance Warehouse mounts `/opt/data` read-only at the same path.
10. Publish or update through the dashboard/live-app tools using the returned artifact handle. Keep artifacts private unless the user explicitly shares them.
11. Capture once after publishing with `full_page=true`. Verify that the bridge loaded, all expected named queries ran, no query failed, no page error remains, and interactive drilldowns still follow the selected key.
12. Design red team, one round: delegate to Nemesis (specialist_key `design-red-team`) with the slug and the capture. She returns at most seven ranked findings, each with an exact `find`/`replace` edit. Apply every `must` (and any `should` that costs one edit) through `update_live_app`, recapture once, and tell Calliope what you changed and what you declined and why. Do not loop: one review, one pass of edits, one recapture.

## Hosted runtime primitives (already on every published page)

The hosted page shim and the capture harness inject these on `window`; the Warehouse
`artifact_runtime_help(topic="maps"|"queries"|"charts"|"theme"|"verification")` returns focused
API contracts without a starter layout or a coordinator Playbook. Use these helpers instead
of importing your own libraries. Once a tool is identified, follow its invocation and included
parameters; request an exact schema only when deferred, rather than repeating capability searches.

- `rvbbitQuery(sql, {limit})` — the governed data bridge (the template's `composePayload` batches
  named queries into one round trip).
- `rvbbitTheme.tokens()` / `rvbbitTheme.color(cssOrToken)` — authored or explicitly selected
  profile tokens, with CSS colors converted to sRGB for canvas/Mapbox. When a pinned profile
  requires live appearance, do not freeze its theme values
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

Steps 9–10 are for new documents and for restructuring. For every other change to a published
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

## Discovery discipline

- `capability_search` separates technical tools/SQL from optional methods. Use included invocation contracts. For a known tool without a schema, use exact `tool_describe`/`get_tool_help`; search only when its identity is unknown.
- Check the Library when the right capability is uncertain; use the KG to ground unfamiliar data. Stop once the needed method and evidence are established. Ordinary SQL is often the right method; do not force an exotic operator into every artifact.
- If semantic discovery returns a governed metric, cube, Playbook, operator, or useful existing artifact, explicitly evaluate it before falling back to raw relations.
- Never infer that a capability or company corpus is absent because a physical schema query did not reveal it.

## Query limits

- The live bridge defaults to 1,000 rows.
- Keep aggregates, KPIs, and trends in their own flat queries so they represent the complete population.
- For an intentional detail grid, set `limit` on that `composePayload` part or pass `{limit: N}` to `runSql`/`rvbbitQuery`/`callMcpTool`.
- Explicit limits remain bounded by the appliance artifact ceiling. Inspect `requested_limit`, `applied_limit`, and `truncated`; never silently present a truncated detail result as complete.

## Completion contract

- The work has an evidence trail showing the Library and catalog KG were consulted; Brain was consulted when company-document context was relevant. Any direct physical-table fallback has a stated reason.
- A successful build is not complete until `upload_artifact` returns a handle and `publish_dashboard` or `update_dashboard` returns the live artifact URL. Never send the user to the scratch HTML file as the deliverable.
- Published dependency metadata includes the intended named queries.
- Capture reports zero failed queries and no actionable page errors.
- The first viewport contains a useful decision surface rather than a spinner or oversized masthead.
- Tables, filters, row selection, and drilldowns remain functional after rerendering or theme changes.
- Query receipts preserve local warehouse lineage.

Read [references/publish-and-capture.md](references/publish-and-capture.md) for SQL, adaptive-theme, upload-integrity, escaping, bridge, and capture pitfalls.

## Native Stage charts and boards

For a small diagnostic board, compose saved query surfaces and live charts with
`publish_board`; a full HTML app is not required. SQL responses include
`surface_id`/`stage_refs`; use `list_stage_surfaces` to recover missing references
in the current chat. Never inspect the chat DOM or protected catalogs for IDs.

Chart/board publish and update tools return clean visual-review images. When
composing a board, defer individual chart captures with
`defer_visual_review=true`, then inspect the assembled board's images. Hermes
may expose these as `MEDIA` paths: use `vision_analyze`. Check `visual_review`
status, errors and coverage; do not claim a visual pass from publication alone.
Use `capture_stage_surface` for a necessary additional check. Review images are
private feedback, not new Stage publications. Do not open your own chat on the
Desktop for routine inspection; reserve Desktop for interaction testing.
