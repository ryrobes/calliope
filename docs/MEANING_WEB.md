# Meaning on the web

Open `/meaning` on the Calliope server. The web workspace uses custom HTML/CSS,
shared Calliope theme tokens, Data Explorer worksheet/grid components, and the same authorized Meaning object references
as desktop. The current web design leads with data; it does not translate GPUI
layouts or generate business policies from inferred descriptions.

## Data workspace

Choose an area, then a measure, cube, source, or published app in its independently
scrolling object list. Search and type selectors cover the complete authorized
area. Suggested metrics and analyses remain in a separate Suggestions collection.
They have no fabricated result and are never automatically promoted or executed.

Measures show their latest available value, its provenance (saved observation or
registration sample), fixed default scope, definition version, and dated saved
history. Observation receipts retain both observed-at and data-as-of. Definition
& specs sits beside the history on wide screens and below it when the content
area is narrower than 920px, showing the SQL, version, scope, grain, window, and
recorded description. Metric pages do not mount a pivot or query live source
rows. Open a connected source or cube to explore its records. A stored definition
is not a certification of its business interpretation.

Sources and cubes load bounded live row previews and the Data Explorer’s shared
`CubeWorkbench` / `RollupShelf` components. Drag field pills onto Rows, Columns,
Values, or Filters; clicking a field offers the same destinations on touch or
keyboard. Rows supports multiple dimensions, Values supports multiple aggregates,
and Columns creates a crosstab. Date grains, sorting, Top N, WHERE and HAVING
filters use the same controls and compiler as the chat data panel. Shelf edits
query automatically. No numeric field is implicitly summed; the initial default
is an explicit record count. Cube measures follow published rollup rules, including
component-based ratios and weighted averages. Selecting a group filters the source rows; opening a row shows its
captured values. All Meaning modes share the same full-width responsive frame:
overview, data, definitions, metrics, records, and pinned objects. In object views,
the sidebar, title, and tabs keep the same position and size; the content beneath
them scrolls independently. The overview and mobile view use the same outer gutters.
The field palette, Values, and Filters stay in the left worksheet rail; compact
Columns/Rows shelves sit directly above the shared virtualized `ResultGrid`.
The object list and grid scroll independently. Source rows occupy a second grid
tab, not a second HTML table below the worksheet. Right-click a grouped result
and choose Explore source rows; raw rows support Open row and copy actions.
Search, resize, sort, and transpose are the normal Data Explorer grid controls.
Definitions and metric history use the available content width too; history charts
adapt their horizontal scale while keeping a steady display height.
Shelf edits update the mounted worksheet in place: the last successful result
stays visible with an Updating indicator, preserving the surrounding detail,
grid state, and scroll position. Failed queries retain that result with an error;
record drilldown waits for the current query to succeed. Superseded responses
cannot replace the latest query.

**Metric lineage is not a contributor contract.** Opening a connected source
queries that relation, not a reconstruction of the metric's saved value. It does
not inherit the metric SQL's joins, filters, or historical snapshot. Source
exploration stays on the source's own page and leaves stored metric history
unchanged. Exact metric contributor drill-down needs an explicit saved
contributor query/snapshot contract.

Connections show actual saved dependencies and their recorded status/evidence.
Shared-source associations and inferred links are not promoted to declared facts.
Published app links open their existing renderer. Ask Callie and Pin to Home use
the established reference-based services.

Browser Back/Forward and the in-page controls restore the selected object, source,
query shelves, result/source tab, compound row selection, paging offset, detail view, and reading position.
URLs share the data selection; an individual row preview stays in session history.
Repeated navigation reuses bounded per-page caches; Refresh rereads the data.

## Bounded data API

`POST /api/calliope/meaning/data` accepts a `reference` to a published source/cube,
a structured `spec` using the existing DataRabbit `RollupSpec`, optional row
selection `filter` (a `{field,value}` object or list with optional date `grain`),
and `offset`. `probe: {field,search}` reads filter choices without loading rows.
Legacy `dimension`, `measure`, and `aggregate` links remain supported. It accepts
no browser SQL. The server resolves published membership and actor
access first, reads as the actor's PostgreSQL role in a read-only transaction,
validates fields against actual schema, quotes identifiers, and uses the shared
validated literal compiler for shelf filters plus bound parameters for row selections.
Statements have a five-second timeout and a one-second lock timeout.

A preview returns up to 40 rows and 64 fields, with cells capped at 4,000 characters.
Paging is bounded to offset 10,000. Values are serialized as text to preserve decimal
and bigint precision. Shelf results show up to 200 groups, with explicit overflow;
the shared compiler bounds shelves to eight dimensions and eight values, and
pivots to 24 categories / 96 output columns. Field-value probes return up to 200
choices. Cube metadata comes from the published definition, never browser hints.
Legacy breakdown requests return the top 20 groups by value and explicitly
report overflow; they cover the source before the selected group filter. These are
live queries and separate page requests can see source changes. No stored metrics,
Meaning definitions, or operational rows are changed by exploration.

## Discuss an exact object

**Ask Callie**, **Promote…**, and **Disagree…** open a new chat with an editable,
unsent prompt. The server re-resolves the selected reference and authorization,
then stores a timestamped snapshot as an ordinary Stage evidence surface. The
snapshot includes the current definition version, inspect details and optionally
the relationship being discussed. The web chat selects and pins that surface;
its structured metadata is included as untrusted evidence in the next selected
surface context. The Stage object links back to its live Meaning reference.

Promotion and disagreement are discussion intents. Opening the chat does not
approve a definition, update a metric, run SQL or send a model turn. Callie can
use the existing Meaning/metric tools during the ensuing authorized discussion.
The saved chat evidence remains a receipt even if the live definition changes.

`POST /api/calliope/meaning/discuss` accepts:

```json
{
  "reference": {"kind": "meaning", "area": "subject:service", "node": "metric:cost"},
  "intent": "ask",
  "request_key": "a caller-generated UUID",
  "edge": null
}
```

The request must be same-origin JSON and authenticated. Intent is `ask`, `promote`
or `disagree`; an optional edge must belong to the authorized neighborhood and
connect to the object. Browser-supplied SQL, definitions or replacement metadata
are rejected. Repeating the same request key for the same actor reuses the saved
chat; reusing it for a different reference or intent returns 409. Session creation
uses the normal Calliope/Hermes session helpers and creates only a completed
evidence bootstrap turn. A partial save failure cleans up that newly created
session. Runtime errors leave the browser on the map with a retry message.

## Implementation and checks

- `meaning_web.py`: authenticated page/assets, exploration route, chat handoff.
- `datarabbit/src/calliope-data/meaning-pivot.tsx`: shared React shelves mounted in
  a Shadow DOM with inherited Calliope theme tokens and the shared `ResultGrid`;
  grid menus and image previews portal inside the same style boundary. No iframe
  or separate service. The shelf arrangement matches the chat Data Explorer.
- `cube_shelf.py`: shared validated query compiler, with explicit row count support.
- `meaning_data.py`: bounded source and cube exploration under actor permissions.
- `calliope/meaning-desk.js` and `.css`: web data workspace and navigation.
- `calliope/meaning-data.mjs`: pure route, source selection, and presentation helpers.
- `calliope/meaning-present.mjs`: shared exact-value, timestamp, SQL and history views.
- `meaning_graph.py`: published membership, dependency projection, metric receipts.

Run from the repository with Calliope Python dependencies available:

```sh
node datarabbit/scripts/build-meaning-pivot.mjs
node datarabbit/scripts/build-calliope-data.mjs
python -m pytest -q services/calliope/test_cube_shelf.py services/calliope/test_meaning_data.py services/calliope/test_meaning_web.py services/calliope/test_meaning_graph.py
node --test services/calliope/test_meaning_data.mjs services/calliope/test_meaning_present.mjs
```

The older graph/explorer assets remain available for existing integrations; the
web page now loads the data workspace. Native desktop rendering is unchanged.
