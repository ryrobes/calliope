# Published regression workspaces

These are the old business demo's online workflows ported to `calliope.app.v1`.
They use the existing Calliope server, Postgres artifact versions, named SQL
bindings and transactional app actions. No app-specific service or SQLite file
runs on the client or server. Python here authors/installs JSON and test fixtures;
it is not the apps' runtime.

| Definition | Published slug | Exercises |
| --- | --- | --- |
| dossier | demo-transaction-dossier | Records, tasks, notes, optimistic edits |
| pipeline | demo-transaction-pipeline | Pipeline chart, selection, stage changes |
| briefing | demo-morning-briefing | Curated transaction/task summary |
| hvac | demo-service-operations | Live filters, chart selection, job status |
| counter | demo-parts-desk | Editable quote grid, stock reservation, backorders, linked visit |
| receiving | demo-receiving | Reviewed delivery, immutable receipt, oldest-order-first allocation |
| dispatch | demo-dispatch | Resource timeline, reviewed scheduling, conflict rejection |
| field | demo-field-notebook | Append-only reports, corrections, readings, shared file attachments |
| adaptive | demo-adaptive-workspace | Watched notes → SQL decision_tree → supporting region |
| case | demo-shared-case | Shared readiness, notes/handoff, delivery → return visit |
| charts | demo-chart-gallery | 21 Vega-Lite families, SQL datasets, responsive compositions |

Open `/apps/<slug>` or choose the corresponding **Demo** entry under desktop
**Apps**. The navigation also switches among all eleven surfaces within one app.
All entry points share `calliope_apps.demo_*` records. Changes persist and appear
in other sessions after refresh/poll; the case starts waiting for one replacement.
Receive quantity `1` on `PO-CASE-01`, then review a return appointment.

`setup.sql` is an **explicit fixture install**, never a startup migration. Existing
records are retained. `publish.py --actor USER --install-fixtures` runs inside the
Calliope server environment and publishes/version-updates through its existing
artifact tools. Without the flag it only publishes definitions. `workspace.json` and the adjacent `manifest.json` are the shared reviewed source.
The publisher changes only the initial page, title and case selection for each
entry point; each published artifact contains its complete JSON definition.
`build.py` also exports ignored per-page JSON copies for direct MCP publication.

`build.py` generates JSON from shared authoring helpers; `export_fixtures.py` is a
one-time legacy fixture exporter. Neither is needed to open the published apps.

## Checks

`check_postgres.py` runs inside the server container with candidate `ui_apps.py`,
`ui_composition.py`, and `ui_charts.py` adjacent and `CALLIOPE_TEST_ACTOR` set. It
creates uniquely named QA tables, exercises real SQL/actions/decisions, exports
snapshots, then drops only its own QA tables. Action audit receipts remain. It
checks shared readiness, stale writes, replay, collisions, order/visit creation
and report corrections; it does not reset the published demo's records.

Feed the exported snapshots to:

```sh
python3 apps/calliope-canvas/business/check_published_charts.py charts-snapshot.json /tmp/charts
# From apps/calliope-canvas/business/web:
node check-published.mjs /path/to/snapshots
```

Then review actual GPUI screenshots with `business/capture_published.py` and its
semantic probe, which use an isolated Xvfb display and disposable profile.

## Current differences from the legacy fixture

This port covers the online workflow, not every fixture experiment. Multiple pending reports per app, case pins/context export,
persisted panel preferences, and dispatch undo are not exposed yet. Receiving
currently posts usable stock; the old damage/evidence workflow is not exposed.
Native forms retain per-record edits, attachments, and cached navigation; web
forms still have one selected record each. Reconnect never submits pending work
automatically. The example selections and policy
content are fictional app definitions, not hardcoded server routes.

## Composition and desktop density

`design.py` is an authoring pass over the shared fragments. Its output is normal
catalog JSON: a visual dossier, stage lanes, an editorial briefing, and compact
operational desks. No page-specific layout handlers run in either renderer.
Work sheets, context washes, selectable cards, open sections, and quiet activity
rows have different roles. `overview` derives portfolio and service summaries
with SQL over existing bound datasets; service counts share the chart dataset.
The existing action manifest and record semantics remain unchanged.

Published artwork is an allowlisted bundled asset, embedded in the native binary
and emitted with a content hash in the web build. It no longer depends on a local
checkout path. The neighborhood illustration is labelled concept artwork.


Catalog v5 work lists now use shared `CRecordItem` and `CScrollArea` primitives:
selection follows the same bound ID as the editor, whole rows activate by mouse
or keyboard, and list scrolling does not drag the rest of the page. Adaptive and
case suggestions use `CReactiveRegion` with visible pending/held/unavailable
states and semantic-revision transitions. Hold/Resume are ordinary state events;
page or visit navigation restores automatic mode. The dropdown override was
removed, and each adaptive outcome exposes relevant contextual work/actions.
`interactions.py` emits those portable definitions at authoring time.

## App attachments

`CAttachments` now works in published apps, on desktop, Android, and web. An
editable `value` binds an array under `/state/`; `readOnly: true` also accepts a
resolved ID array, including repeated SQL rows. Optional `names` binds a metadata
object outside state; the server fills authorized names and hides inaccessible
file metadata. Native hosts intercept picker/open actions. No per-app handler is
needed. The published host does not support the old fixture's `offline` flag.

`POST /api/apps/{slug}/files` accepts JSON `{name,mime,data,request_key}` (base64
bytes and a UUID retry key). The server validates type/name/size, checks app
access, deduplicates retry keys, and limits uploads to 100 MB per actor per day.
`GET /api/calliope/app-files/{id}/meta` and the corresponding file URL check the
owning artifact's current access, including archive state. Downloads use
attachment disposition, nosniff, and private/no-store. Public URLs are not issued.

The additive `ui_app_files.DDL` creates a protected immutable blob/metadata table
in Calliope Postgres. It is installed lazily by the module (or explicitly through
`ui_app_files.prepare(server)` during deployment). Database backups include files;
there is no parallel SQLite or filesystem synchronization requirement. App SQL
stores UUID references, never blobs. The Field notebook fixture adds an
`attachments` JSONB column to its reports; installing that column precedes
publishing the updated example definitions.

Uploads are artifact-scoped, not record-level ACLs. Generated app authors must
still implement their business record permissions and validate any direct SQL
action inputs; a stored ID cannot bypass download authorization. Unreferenced
uploads are retained, and lifecycle/retention tooling plus object-storage offload
for large files remain future work. The initial 5 MB limit keeps this bounded.
