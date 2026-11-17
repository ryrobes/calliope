# Data exploration

The Data workspace tab is available before starting a conversation. Searching,
previewing and building queries belong to the signed-in person. They do not
create chat turns or invoke a Hermes specialist.

## Meaning areas

Discovery lists real published Meaning subjects above individual datasets. Each
has a larger dossier tile with subtopic labels, a sample question, and counts of
datasets, questions, open questions, and metric ideas. Tiles reflow with the panel.
Selecting an area opens its narrative, subtopics, member datasets, recorded relationships, sample
questions, open questions, and proposed metrics. Related saved metrics and cubes
are associated by SQL source lineage; proposed metrics stay clearly separate.
Subtopics narrow the table scope without collapsing the workspace into accordions.
Back/Forward restores the area, selected subtopic, and subsequent query results.

`data_areas.py` reads `rvbbit.meaning_published_domains`, preserving the last
successful publication during rescans. It never launches a scan. Because an area's
prose can discuss every member, it is shown only when all members pass the current
relation inventory's authorization checks. Partial-access users retain direct
access to their individual tables. Missing area metadata does not block ordinary
table discovery. Detail, query context, and Chat handoff recheck current access.

Sample questions and the area's request input use the existing query builder and
automatic runner. Area scope supports up to twelve member datasets (ordinary
manual selections remain capped at four); larger areas need a smaller subtopic.
The model receives the published dossier, selected subtopic, and member schemas,
and chooses the tables needed for the question. Area membership is not evidence
of a join. Candidate metrics remain proposals, and unknowns must not silently
become facts. Clarifications refresh and retain area context. Private drafts keep
the dossier snapshot; Stage copies only its key, name, publication time, and
selected subtopic. Reopening and refining retrieves current authorized context.

“Ask Callie” on a sample/open question prepares a visible Chat draft with the
authoritative question, area key, publication time, description, and members. It
preserves existing composer text and does not send automatically or edit Meaning.
Sending that draft starts normal Callie work to investigate, ask for business
context, and enrich metadata with supported evidence. Read-only chats cannot take
the handoff. `GET areas/detail?key=…` and `POST areas/handoff` sit under the existing
authenticated `/api/calliope/data/` API; the latter has the usual same-origin guard.

Metric ideas offer **Try in Data** and **Explore with Callie**. Try in Data previews
the proposed calculation using the area's full table scope (metric ideas belong
to the whole area, even if a subtopic is selected). It includes the proposed grain
and purpose, and uses the existing guarded query builder. Explore with Callie
prepares a Chat draft from the current published idea, including its formula,
grain, and purpose; it asks Callie to check for an existing saved metric and
validate the definition. Neither action registers a saved metric automatically.

## Search and queries

The discovery input accepts dataset searches and result requests. Typing uses
ordinary dataset search; Enter or Go submits one Jev decision through the cached
`rvbbit.clover_decide` operator. Jev chooses search versus query and, for a query,
one clearly sufficient source from up to 14 authorized search matches. Uncertain
choices, multi-source requests, unavailable routing or missing matches leave the
person in discovery. A recognized result request is retained for source selection.
The shortcut requires both chosen scores >= 0.7 and margins >= 0.15; these are
conservative routing signals, not calibrated correctness probabilities.

For a clear query route, the UI immediately invokes the existing query builder
and automatically runs its valid proposal. Jev never writes SQL or grants access;
the live Calliope model, schema/memory discovery and SQL guards are unchanged.
The route uses a 20-second database statement timeout. Its native decision receipt
and costs remain in the central ledger, and `data.route` records the choice and
correlation ID; the generated draft retains that ID in `context.entry_route_id`.
No decision runs on individual keystrokes. A discarded navigation branch cannot
start generation from a late routing response.

Find data starts a fresh search with an empty request. Opening an unrelated
dataset also clears the query prompt. An explicitly submitted ambiguous request
can carry its own question into a dataset preview. Global saved-input text is no
longer restored; existing explorations retain their prompts through navigation
history and Recent, so refinement stays available after running a query.

Show results generates a proposal or asks a clarification. The panel automatically
executes valid proposals, with a 30-second statement timeout and at most 1,000
returned rows. Clarifications and invalid proposals never execute. The explicit
Refresh results action reruns a saved query; browsing history never does. Dataset previews return at most 200 rows. Both use the existing
governed SQL runner and the browser's database execution subject. The grid's
search and sort operate on returned rows, not the full dataset.

Add to Stage publishes an immutable execution snapshot with the query,
sources, assumptions and generation receipts. It records a `data_publish`
event, not an assistant exchange. Retrying publication of the same execution
into the same notebook is idempotent. With no notebook selected, the browser
creates one before publishing. Editing Stage queries is deferred.

Back and Forward restore dataset selections, search terms, prompts, previews and
saved results within the mounted panel, including scroll position. Selecting a new
path discards the forward branch. Pending responses update their original entry;
a discarded build cannot automatically execute or overwrite the current selection.
Recent retrieves durable private drafts after reload. Transient notifications use
the parent notebook's standard bottom toast, with origin and sender checks.

## Storage and inference

`data_explorer.sql` is applied idempotently by the server. Drafts and runs are
owned by a person; publications belong to the destination notebook audience.
Changing a request creates a new proposal linked to its predecessor. Old
executions retain their results. API reads and execution are owner-scoped;
publication additionally requires participation in the destination notebook.

The generator receives authorized source schemas and bounded samples, Meaning
definitions and caveats, and ACL-filtered system-learning retrieval. It applies
Cassandra's analytical criteria in a dedicated prompt with bounded read-only discovery; it does not
load her conversational profile or private specialist bank. Context is retained
with the private draft. Selectable datasets are rechecked before execution and
publication. Runtime SQL is restricted to one SELECT over selected sources and
a bounded set of ordinary PostgreSQL functions. The expert console is unchanged.

Each revision resolves Calliope's live model selection from the Hermes control
API, including the API platform model override. Rewriting and generation share
that exact selection. Changing Calliope's model takes effect on the next Data
request; the old `WAREHOUSE_DATA_BACKEND` / `WAREHOUSE_DATA_MODEL` overrides are
no longer used. Direct Clover/Hutch and registered OpenRouter routes resolve
credentials from the existing backend registry. Unsupported providers (including
Hermes-only subscription routes), mismatched custom endpoints or unavailable
configuration fail explicitly rather than substituting the cheap alias.

The private revision chain acts as the Data conversation. Both rewriting and
generation receive its earlier requests, clarification questions, original
answers, rewritten requests, assistant summaries and execution outcomes. The
latest query is also supplied in full. Follow-ups such as "same thing, but last
year" inherit the earlier intent, with explicit new choices taking precedence.
Only ancestors of the selected revision are included, never sibling branches,
another person's drafts or stale retrieved documents. Every
ancestor is owner-checked. To bound context, retain the original exchange and
recent exchanges (up to 16, with a 60 KB target); remove whole middle exchanges
and disclose the omitted count to the model. The original and latest exchange
are always retained. The exact context sent is saved with each draft.

Answering a clarification creates a new revision. The selected Calliope model
rewrites the complete request using this conversation plus the current answer.
The visible prompt updates; schema-grounded generation and automatic execution
follow. The original revision, answer and inference receipts remain recorded,
including when the selected model is unavailable. This does not invoke a Hermes
agent or insert a turn in the notebook chat. Failed rewriting never runs an older
query. Restoring a draft from Recent resumes its branch after a reload.

The generator can request up to three lookups before returning the final query:

- `query`: SELECT over currently selected and authorized sources, at most 100
  rows, with a 10-second timeout. Uses the same SQL guard and governed runner as
  final execution. Supports value discovery, frequencies and diagnostic queries.
- `memory`: ACL-filtered shared system-learning search using the existing memory
  service and the signed-in person's identity.
- `result`: a page from an ancestor's saved execution, with owner, branch,
  selected-source and current-access checks. Never reruns that query.

The latest result also supplies a ten-row preview automatically. Evidence retains
whole values and column names under a 32 KB budget; it removes whole rows and
reports truncation/pagination instead of clipping dimension names. Oversized rows
require a narrower query. Past results are identified as saved snapshots. For a
pivot, discover the top values under the applicable filters, then generate quoted
value-named columns. Ask the person about intent, not facts a lookup can retrieve.

Lookups use a structured JSON request/response contract through the existing
completion client, independent of provider-native tool calling. Each attempt,
including errors, arguments, returned evidence and latency, is persisted in the
private draft's `context.discovery`; validation failures are retained separately.
SQL and memory lookups also produce `data.lookup` / `data.memory` activity receipts
with the draft and lookup IDs. Stage publication keeps its existing query ID link;
it does not disclose the private conversation or retrieved memory to a notebook.

There are at most five generation calls (three lookups, one final proposal and
one validation repair), each with a 90-second inference timeout and no transport
retry. A clarification rewrite is one additional call. Central Costs records
`Data exploration / data.generate` and `data.refine`, keyed to the draft ID. `data.run` goes
through the SQL activity receipt path. Stage source metadata retains generation
receipt IDs and the execution ID for exports and later price reconciliation.

## Execution boundary

This is a bounded query-building adapter with durable conversation context. Data
owns the private revisions, authorization, frozen executions and Stage publishing;
the existing services own SQL execution and shared-memory retrieval. It has no
delegation, files, browser, shell, background work or general agent lifecycle.

Use a dedicated Hermes profile/session when exploration requires open-ended
analysis, longer tool plans, subscription-provider support, resumable background
work, or the wider Calliope toolset. At that point replace the inference/discovery
adapter and retain Data's storage, ownership and publication contracts. Avoid
building a second general agent runtime here. A Hermes migration must explicitly
scope the profile's tools and memory rather than inheriting Calliope's full
orchestrator/playbook behavior.

## Shared viewer

`datarabbit/src/calliope-data` adapts the same `ResultGrid` DataRabbit uses; there
is no copied grid implementation. A same-origin frame isolates its React and
Tailwind styles. It follows the notebook's current theme. Inline and expanded Stage query
viewers use the same bundle against their already-authorized stored result;
opening a viewer never reruns a query. Stage hides row search while retaining
sorting, column resizing, a Transpose toggle and record inspection. Transpose is
off for new grids; switching it on places field names down the left. Inline grids load
lazily and retain their mounted frame and grid state across Stage reconciliation.
Action receipts with synthetic statement previews retain their compact table.

## Explore a Stage query

“Explore in Data” imports the authoritative saved surface into a new private
exploration. It is the single Stage query opening action, replacing the ordinary
query modal. Setup mode retains its viewer because Data is unavailable there.
The server checks notebook visibility and current source access,
extracts physical relations (excluding CTE names), and applies the existing SQL
guard. Opening never invokes inference or executes the SQL. Saved rows, the
published question, full SQL, assumptions and observation time seed the branch;
the author's private conversation and memory are not copied. Incomplete SQL or
unsupported/unavailable sources still open as a clearly labeled read-only result.

The imported run carries `saved_snapshot: true`. Refresh executes its exact SQL;
Update builds a new revision with the existing query and request as context, then
runs automatically. Only a fresh execution can be added to Stage. Publication
creates a new frozen query with `source.derived_from` provenance and, within the
same notebook, `parent_surface_id`. The original is unchanged. Cross-notebook
publication retains an origin link without violating notebook-scoped parentage.
Visibility of the origin is checked again on publication. Source breadcrumbs
navigate back to the original; Data's Back/Forward controls preserve each step.

`POST /api/calliope/data/surfaces/{surface_id}/explore` uses the same owner and
same-origin mutation checks as the other Data endpoints. No schema change or
Hermes chat turn is needed for importing or refining.

Stage query headers use readable engine names. New cube pivots include the cell
query's selected routing-plan engine and elapsed time; plain PostgreSQL cube
tables are labeled PostgreSQL. This is routing metadata, not confirmation of any
execution fallback. Older cube snapshots without metadata say “Engine not recorded.”

Build and typecheck from `datarabbit`:

```sh
npm ci
npm run build:calliope-data
npm run typecheck
```

The generated `data-runtime.js` and `data-runtime.css` are checked in, as with
the existing notebook runtimes, and included in the server image. No DataRabbit
server deployment is needed for the notebook feature.

Regression coverage: `test_data_explorer.py` (use a disposable PostgreSQL
cluster via `ACTIVITY_TEST_ADMIN_DSN`) and `test_data_panel.py` (browser fixtures).

Jev routes only the discovery entry point. Clarification rewriting and SQL
generation use the selected Calliope model, and deterministic SQL guards remain
the execution gate.

## Metrics and breakdowns

A metric idea is one scalar value for a selected scope and time period. A breakdown
is a separate proposed analysis with a name, purpose, measures, dimensions, row grain
and interpretation caveats. For example, “Regional sales contribution” explains how
revenue is distributed, grouping SUM(sales) by region. A breakdown is not registered
as a scalar metric. It can be explored and published as a query result, or refined
into a cube through Callie.

Published area details show **Breakdowns** separately from **Metric ideas**. Both have
Try in Data and Explore with Callie actions. Breakdown previews carry the narrative
and grouping into the existing guarded query builder. Callie handoffs prepare a draft
with the authoritative area context and require the user's normal Chat send.

Migration `0462_meaning_breakdowns` adds `candidate_breakdowns`, enforces the distinction
at the dossier/domain write boundary, and reclassifies clearly grouped legacy ideas.
It retains raw model output and appends corrected publication snapshots independently
of the working dossier. Scalar ideas with optional slicing remain scalar; existing
saved metric definitions and observations are untouched. The compact Meaning tree
includes breakdowns, so the narrative is available to agents as well as Data.
