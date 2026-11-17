# Native Meaning map

The desktop **Meaning** button opens a map of published business areas. Click an
area to explore its concepts/data sources, metric definitions, analysis ideas and
related artifacts. This is a projection of Calliope's existing Meaning metadata,
not another ontology store or generated fixture.

```sh
cd apps/calliope-canvas
cargo build --release --locked
./run.sh --meaning
```

Use the normal saved Calliope connection. No local service or SQLite database is
needed. Close an older client before relaunching; the launcher prevents concurrent
writers to the same Home.

## Interaction

- Search business areas; click one to open its neighborhood.
- Scroll to zoom around the pointer. Drag the background to pan; drag a card's
  small type heading to move it. **Fit** brings visible nodes back into view.
- Select a card to open its object workspace. **Expand details ↑** gives it more
  room; **Collapse ↓** returns to the smaller panel. Both heights follow the
  window. Detail content and connected objects scroll independently of the map.
  Object names navigate to that object; **Why?** (or a graph connection label)
  opens the recorded relationship evidence without changing the selection.
- Metric details show exact saved values, timestamp-spaced observations, the
  calculation grain/window/defaults, interpretation, source links and registered
  SQL. Scroll down for individual observation receipts with data-as-of and saved
  timestamps. A rolling SQL window and a fixed snapshot description stay visible
  as distinct facts. No SQL is executed to populate this view.
- Source details list up to 80 recorded fields with their types, Meaning roles,
  interpretation and provenance. No row samples or profile values are loaded.
  Areas expose useful questions/gaps, apps expose dependencies in this area and
  launch behavior, and suggested breakdowns expose dimensions/measures/caveats.
- **Show N connected** reveals exactly that many currently hidden neighbors of
  the selected object, up to six per click. It does not fetch or reveal unrelated
  objects. The control disappears when all loaded neighbors are already on the map.
- **Browse objects…** opens a separate searchable list of loaded objects, grouped
  by Metrics, Sources, Apps, Metric ideas and Breakdowns. Counts distinguish what
  is loaded, what is available and what is on the map. **Add to map** adds one
  object; **Locate on map** centers an existing object; **Hide** removes only its
  local placement. Nothing is deleted from Meaning or Home pins.
- **Load up to 12 more per type** fetches a larger catalog without adding cards,
  moving them, or changing zoom. It is offered for a filtered type only when that
  type has more loadable objects. Search covers the loaded catalog. Hard preview
  limits are stated separately from completion.
- **Reset view** restores the small starting selection and default arrangement.
  Deliberate additions, hides, camera state and catalog depth persist per area on
  this device. Refresh and navigation preserve them.
- **Open** sends published A2UI apps to the native renderer; HTML artifacts open
  in the OS browser at the configured Calliope instance.
- **Pin to Home** stores a private reference on the Calliope server. Home resolves
  its latest authorized metadata and allows moving, resizing and removing it.
- **Ask Callie** creates or reuses a native conversation and prepares an editable
  question with timestamped node/relationship context. It never sends the turn
  automatically. When loaded, selected-object details travel with that context.
  Opening the map itself runs no model or metric evaluation.

The graph and Home summaries use the configured fonts, palette and background.
Details reduce at low zoom. The map uses native GPUI cards and GPU paths with
viewport culling; it does not embed a graph webpage or run a force simulation on
every frame. The first neighborhood is deliberately small; the full loaded graph
is available through expansion.

## Data and provenance

The server module is `services/calliope/meaning_graph.py`, registered by `ui_apps`.
It reads:

- Completed `meaning_published_domains` areas (not drafts from an active refresh).
- `meaning_relations` / `meaning_columns` metadata for member sources.
- Latest, non-retired `metric_defs` and up to 24 existing observations per metric,
  at the current definition version and empty parameter scope.
- Authorized published `dashboards` with matching latest-version `dashboard_deps`.
- `meaning_links`, retaining recorded verification status and join evidence.

Source cards lead with their business description. Profiled row counts remain
small metadata, not a headline KPI. The starting view favors registered measures
on an area's core sources and three distinct formulas when available. Unscoped
scalar `COUNT(*)`/`COUNT(1)` expressions sort behind other measures; distinct and
conditional business counts are retained normally. This is presentation ranking,
not a judgment that counts are invalid. A shared formula is only a display-diversity
hint: definitions, scopes and pin identities are never merged, and every loaded
metric remains browsable. Suggested metrics fill a gap when there are fewer
registered measures rather than displacing existing values.

Connection labels are placed in free space along their curves. If there is no
collision-free location, the label is omitted from the map; its relationship and
evidence stay in the inspector. Cards are always drawn above labels.

Solid connections denote published membership or declared dependencies, not proof
that a business interpretation is correct. Implied metrics and suggested analysis
remain explicitly labeled. Unverified/implied/contradicted links are dashed.

Values are labeled **Profiled rows**, **Saved observation**, or **Registration
sample**, with their source timestamps. Refreshing the map is not evaluating a
metric or establishing that its source data is current. Sparklines show only real
stored observations; no synthetic trend is created from a single sample. The full
sample timestamp and any cautionary definition prose remain in the inspector.

Map reads refresh at most once a minute while Meaning is active. Pins refresh once
a minute for Home. Transient failures retain in-memory data with a stale/error
label; authoritative unavailability removes the affected summary. Definitions and
business data are never cached as a local database by this feature.

## Server contract and access

All routes use the existing authenticated session and control executor. POSTs
require same-origin JSON and bounded requests.

| Route | Purpose |
| --- | --- |
| `GET /api/calliope/meaning/graph` | Area index, or neighborhood via `area`, optional `focus`, and per-kind `limit` (4–40, default 12) |
| `POST /api/calliope/meaning/object` | Resolve one current authorized reference |
| `POST /api/calliope/meaning/inspect` | Resolve authorization, then load bounded source fields or exact-version metric definition/history for a selected reference |
| `GET /api/calliope/meaning/pins` | List the caller's personal references |
| `POST /api/calliope/meaning/pins` | `{operation:"pin",reference}` or `{operation:"remove",id}` |

A reference is `{kind:"meaning", area:"subject:…", node:"metric:…"}`. Kind-specific
identities also cover areas, relations, ideas, breakdowns and artifacts. Invalid
identities are rejected; an unavailable object returns 404, never another object.

`rvbbit.workspace_object_pins` stores only UUID, authenticated actor, reference JSON
and creation time, with uniqueness on actor/reference. Creation is deduplicated
transactionally and limited to 32 personal pins. Public database access to this
internal table is revoked. Artifact lookup enforces `artifact_can_view` for the
caller. Shared SQL deployments use the existing company Meaning scope. With a
mapped Postgres role, an area is omitted unless every member source is readable
and has no row-level security; aggregate previews from the service account are
suppressed. This conservative policy avoids leaking a broad dossier into a
partially authorized view.

Selected source/metric detail is fetched in the background on selection and after
map refresh. Late responses cannot replace a newer selection; a failure is shown
with an explicit retry. Inspector data stays in memory. Mapped database roles do
not receive observation history or service-account aggregate/profile samples.
The inspector uses the same published-area and artifact checks as graph resolution.

Only local presentation state is persisted: per-account map positions, visible
object identities, catalog depth and cameras in
`meaning-layout.json`, plus Home placement and contextual-topic mappings in the
existing Home file. Server references follow the latest publication, not a frozen
copy of a card. A removed or moved identity can become unavailable; no fuzzy
identity migration is attempted.

## Current bounds

This first version supports the Linux desktop client. It does not add a mobile or
web graph, ontology editing, row-level entity instances, arbitrary Stage receipt
inference, automatic metric execution or a second declarative UI system.

The index is capped at 200 areas. Neighborhood candidates are bounded to 201
metrics/artifacts and 400 source links, then displayed with at most 40 objects per
major kind and six suggested metrics/breakdowns. Pins resolve their exact object
within these candidate bounds. This is neighborhood navigation, not a complete
company-wide graph export. Only explicitly recorded artifact dependencies produce
connections; missing dependency metadata produces no guessed edge.

## Verification without taking over the desktop

```sh
python3 business/check_meaning.py /tmp/calliope-meaning-check
# Search, catalog loading, add/hide, expansion and label-collision regressions:
python3 business/check_meaning_explore.py /tmp/calliope-meaning-explore
# Expanded inspectors, receipts, fields, selection races and compact layouts:
python3 business/check_meaning_details.py /tmp/calliope-meaning-details
# Also prepare an unsent chat, then archive that empty test conversation:
python3 business/check_meaning.py /tmp/calliope-meaning-check --ask
```

The test launches the release binary on private Xvfb/software Vulkan with a
disposable local profile and the existing authenticated connection. It reads real
published data, exercises physical zoom/drag/pan, checks stable placement and pin
deduplication, and opens a real native A2UI artifact. Screenshots and a JSON receipt
are written to the output directory. Temporary pins are removed in `finally`;
preexisting pins are preserved. `--keep-pin` explicitly retains a newly created
pin. `--ask` creates an empty server conversation, verifies no turn was sent, and
archives it afterward. No business record or model call is part of this test.

The opt-in private probe uses `CALLIOPE_UI_PROBE` plus `.meaning`. It supports
inspect, area, select, expand, fit, context, pin, remove_pin, ask, open, catalog,
load_catalog, show, hide, reset_view, detail and evidence. Inspection includes the painted edge-label
rectangles and current catalog matches for structural collision and filtering checks. Ordinary
launches expose no testing socket. Display isolation does not change the user's
server permissions or turn mutating actions into mocks.
