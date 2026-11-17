# Business UI examples and published apps

For server-owned apps backed by Calliope Postgres, see [Published company apps](SERVER_APPS.md).
The SQLite/Python setup below is the opt-in fictional example harness (`--business`).

# Transaction desk: A2UI in GPUI and the browser

Local, fictional real estate and HVAC workspaces for evaluating the Lotus Notes style
application direction. The included applications are hand-authored A2UI fixtures. The optional
[UI Studio](UI_STUDIO.md) lets Callie create and test new definitions; production
app publication and business-command registration remain separate.

## Try it

From the repository root:

```bash
cargo build --locked --manifest-path apps/calliope-canvas/Cargo.toml
apps/calliope-canvas/run.sh --business
```

Or select **Business desk** in the canvas header. **← Canvas** retains the open
desk and its draft. **Open web view** opens http://127.0.0.1:18455/ in the OS
browser. The desktop starts the local service automatically; it remains running
when the desktop closes so the web view keeps working. To run only the web app:

```bash
bash apps/calliope-canvas/business/run-web.sh
```

For a fresh checkout, build the web bundle once with `npm ci --ignore-scripts`
and `npm run build` in `business/web` (the web launch script does this if missing).
After editing web source, rebuild and reload the browser. After editing server
source, stop/restart this local service. No Lab service restart is needed.

Try selecting a transaction and editing it while the other client is open. Live
updates arrive about every four seconds, including while a draft is open. Stable
controls retain their input state; independent remote edits merge into the draft.
When both clients edit the same field, the desk shows both values and requires
**Use server values** or **Keep my edits** before saving. A concurrent receipt
requires reviewing the whole delivery against the new remaining quantities.

Navigation and related-record inspection retain a separate draft per record.
Draft tabs reopen them after navigation or restart. GPUI stores private local
JSON (`business-drafts-v2.json`); web uses this origin's localStorage. These are
device-local edits. Field reports have a separate evidence outbox (below). Saving/discarding clears the
corresponding draft. Workflow transitions still require saved edits.

Complete the open commitments, then advance the transaction. The server enforces
that prerequisite. Filter the work list by owner. The work log identifies which
view made each change. These actions affect fictional data only.

## What is shared

`server.py` owns the view model, SQL queries, validation and actions;
`presentation.py` composes the layouts. Both clients receive the exact same
v0.9 messages. SQLite persists the
records, related commitments and activity under
`$XDG_DATA_HOME/calliope-canvas/business-demo.sqlite` (normally
`~/.local/share/calliope-canvas/`). A `--db` argument can select a separate demo.
Saved appearance tokens are exported by the desktop to `business-theme.json`;
the web reads them on page load and serves cached LT Superior fonts locally.
Web-only startup uses a fallback palette until the desktop exports its theme.

- GPUI: community `liangdi/a2ui` renderer and core pinned at commit
  `97391988e41052240c36c27442912420903f13a8`, with a small vendored host adapter.
- Web: official `@a2ui/react` and `@a2ui/web_core` 0.12.0, explicit v0.9 imports.
- Shared catalog: standard Button, TextField and ChoicePicker; thirteen Calliope
  presentation/data components; JSON Pointer bindings and resolved event actions.
- Host code: transport, busy/error state, polling, draft protection and chrome.
- Mutations: record-version checks plus atomic request-ID replay protection.

## Styling experiment

The first three buttons switch compositions without changing the underlying records:

- **Dossier:** property illustration, large serif title, key facts, working file,
  commitments and work log.
- **Pipeline:** four stage lanes, compact records, colored progress, portfolio
  value and links into a dossier. This is a view, not drag-and-drop stage editing.
- **Briefing:** editorial heading, date, due-date agenda, next closing and pulse.

`catalog.json` is a finite token contract, not the complete A2UI schema. The
web adapter derives strict Zod schemas from it, marks child references for the
official resolver, and registers a custom A2UI catalog. The native renderer
implements the same vocabulary in `vendor/a2ui-gpui/src/workspace.rs`:

| Component | Presentation choices |
| --- | --- |
| CStack | Direction, spacing, relative weight, wrapping, phone/tablet collapse |
| CSurface | Semantic tone, plain/outlined/soft/gradient treatment, padding |
| CText | Six scales, semantic ink, body/display/mono font |
| CBadge | Status label with semantic tone |
| CMeter | Labeled fraction and progress bar |
| CArtwork | Known asset and banner/portrait framing |
| CVegaChart | SQL-bound Vega-Lite spec, measured chart size |
| CDataGrid | Stable rows, column resizing, keyboard entry, rectangular paste, narrow-layout cards |
| CRecordPicker | Bound business IDs, readable labels; native search for longer option lists |
| CField | Typed money/quantity/date/time/text, units, conditional/required/read-only fields, inline errors |
| CRecordPane | Related-record inspector, independent desktop scrolling, narrow overlay sheet |
| CAttachments | Receipt uploads or local field evidence, camera affordance, bounded files |
| CResourceDay | Resource lanes with minute positions, ordinary child cards, narrow grouped layout |

There are no view-specific renderer components, arbitrary CSS fields, or
executable definitions. Python checks the component graph and token values
before returning a snapshot. New design capabilities still require implementing
both renderers and evolving the catalog. This demonstrates a visual range for a
small shared catalog, not the default A2UI catalog's out-of-box appearance or
the quality of agent-generated apps.

Styles inherit the existing theme. Gradients mix semantic colors with surfaces;
fonts use the cached LT Superior family even if the Lab session has expired.
The illustration is explicitly marked concept artwork; see `assets/ASSETS.md`.
Phone/tablet layouts stack at 600/900 pixels, with larger web touch controls.
The native desktop also collapses its content at those widths; the desktop
host chrome is not a native phone shell.

## Findings and limits

The community renderer is a sample gallery, so embedding, action delivery,
weighted rows, multiline sizing and theme inheritance needed adaptation. Its
upstream targets v1.0; this experiment tests only the overlapping v0.9 subset.
See `../vendor/a2ui-gpui/SOURCE.md` for provenance and changes.

The official React 0.12.0 package advertises a missing stylesheet and ships empty
CSS-module lookup objects. `vite.config.js` restores the shipped CSS class names;
`src.jsx` imports the included v0.9 stylesheet directly. The actual components,
bindings, action dispatch and processor remain the upstream implementation.
Review this workaround when upgrading the package.

Selection controls differ (web radio group, native dropdown); this is semantic
parity, not pixel parity. The desktop is currently tested on Linux; web layouts
are checked at desktop and 390-pixel phone width. A responsive web preview does
not validate iOS/Android native rendering. Physical mobile lifecycle, offline stock/scheduling sync,
generated app installation, native accessibility and full catalog conformance
remain untested. There is no embedded browser in the native view.

The local service binds only to loopback and checks Host/Origin. It is a local
prototype, not a multi-user authorization boundary. It exposes no credentials,
uses no Lab tables and performs no external communications. Definitions are
trusted local source; arbitrary agent-produced definitions need stricter
validation before connecting them to the community native renderer.

## Checks

```bash
python3 -m unittest discover -s apps/calliope-canvas/business -v
(cd apps/calliope-canvas/business/web && npm test && npm run build)
cargo test --locked --manifest-path apps/calliope-canvas/Cargo.toml -p a2ui-gpui
```

The Python tests exercise persistence, filtering, prerequisites, stale writes,
validation rollback, concurrent request replay and reference integrity. The web
contract test loads five records across the three real-estate compositions plus HVAC, parts, receiving, dispatch and field reports, and traverses the
official resolved node trees, so an accepted message with unresolved children
fails. Both suites check rejection of unsupported presentation tokens/assets.
Native checks cover action resolution, busy blocking, field validation, atomic
paste, and draft reconciliation. Visual and cross-view interaction checks still
require both running UIs.

## HVAC + SQL-bound Vega-Lite experiment

**04 HVAC** is a fourth workspace with fictional service visits, notes, status
transitions, and three SQL-backed charts. Open `http://127.0.0.1:18455/?view=hvac`
or select HVAC in the desktop desk. Existing real-estate records are retained.

Install the pinned Linux x86_64 renderer once (SHA-256 checked CLI and Python
ABI wheel; files and upstream notices stay in ignored `../local/vega/`):

```bash
python3 apps/calliope-canvas/business/install-vega.py
(cd apps/calliope-canvas/business/web && npm ci && npm run build)
cargo build --release --locked --manifest-path apps/calliope-canvas/Cargo.toml
```

`CVegaChart` binds a Vega-Lite spec from `/charts/<name>`, a height of 180–480,
and a description, with an optional `onSelect` host action name. The server
executes named SQL and supplies inline values. The web host lazy-loads Vega;
GPUI invokes Rust `vl-convert` through its pinned Python ABI asynchronously at
measured dimensions. Native images carry scene-derived bar/cell/point hit
targets: hovering shows Vega-formatted tooltips, and clicking emits an A2UI
action with the datum. HVAC demonstrates SQL-backed visit drilldowns and clearing
the selection. There is no WebView. Brush/zoom and arbitrary Vega signal programs
remain outside this catalog. See [NATIVE_CHART_INTERACTIONS.md](NATIVE_CHART_INTERACTIONS.md).

Additional checks:

```bash
cargo test --locked --manifest-path apps/calliope-canvas/Cargo.toml charts:: -- --include-ignored
```


## Parts desk: quote → stock → order → visit

**05 Parts desk** / `http://127.0.0.1:18455/?view=counter` adds the first
Jobber/ServiceTitan-inspired distributor workflow. `counter.py` owns SQL and
commands, `counter_ui.py` composes standard/catalog components, and `equipment.py`
holds a small fictional equipment register. Both clients use the same stream.

1. Open a quote or choose a customer and create one. Select customer equipment
   and a fulfillment branch. Add catalog parts, edit quantities/unit prices and
   notes, then **Save & check stock**. Add/remove also saves current draft edits.
2. Review branch availability and server-calculated USD totals. Each quantities
   must be whole numbers; tubing accepts hundredths of a foot. Prices and
   quantities use integers in SQL, with explicit half-up rounding and an
   illustrative 6.5% tax. These are fixture prices, not customer pricing rules.
3. **Reserve & create order** refuses a shortage. Either switch branches and
   save, or explicitly **Reserve available + backorder remainder**. One SQLite
   write transaction freezes quantities/prices and reserves stock. Concurrent
   quotes cannot over-reserve. Ordered quotes are read-only.
4. Choose a visit date and technician, then **Create linked service visit**.
   It carries the customer, location, selected equipment and parts/backorder
   context into HVAC. Open the visit and return to its linked order. Arrival time
   is still to confirm; service labor/pricing is not inferred from the parts order.

The grid becomes labeled cards at narrow widths. Tab / Shift-Tab traverse editable
cells; Enter / Shift-Enter and up/down move within a column. Rectangular TSV paste
validates every target before applying any changes, with a 400-cell/16-KB limit.
Drag column dividers to resize; web dividers also support keyboard resizing.
Reset widths restores the responsive layout. The receiving queue demonstrates
server-side search, whitelisted sort and five-row pages; quote lines stay bounded
at 20. This is not an unlimited spreadsheet or formula engine. Demo limits are
100 quotes, with older quotes accessible through a record picker.
Equipment records are inspectable/selectable, not a full asset-management app.

The native dropdown fix preserves each option's business value separately from
its display label. Previously `7 days` was compared against stored `7`, producing
an empty selection. Invalid reporting periods now produce a readable validation
error. A Rust regression covers different labels/values and duplicate labels.

Verified this slice with 21 Python tests, the three official web-processor contract
tests (including ordered and visit-linked definitions), 11 native renderer tests,
and native/web builds. Interactive checks covered native 7/56-day filters,
native branch + quantity edits observed in web, web fractional quantities,
native partial reservation and visit creation, web full reservation and links
both ways, customer-scoped equipment selection, shortage/draft guards, and web
390 CSS-pixel cards without horizontal page overflow, plus GPUI grid/card layouts
at 1517 and 1000 logical pixels. Local fictional data only;
no ERP connection, supplier order, payment, or customer message is generated.


## Receiving: delivery → stock → waiting work

**06 Receiving** / `http://127.0.0.1:18455/?view=receiving` adds purchase-order
queues and partial receiving. Search by order/supplier, filter by branch/status,
sort, or page without losing the open delivery draft.

1. Open a purchase order and enter **usable** quantities from this delivery.
   Zero leaves a line open; units and whole/decimal precision follow the part.
2. Enter receipt date/time and a delivery reference. Damage details become
   required when damage is checked. Attach up to five PNG/JPEG/PDF/text files,
   each at most 5 MB. Desktop uses the OS file picker; file viewing uses the OS
   browser. The prototype stores evidence in the local SQLite database.
3. **Post receipt** atomically creates an immutable receipt, updates branch stock,
   and allocates available stock against the oldest matching backorders using
   order creation time, then order ID. Other branches are unaffected. Optimistic
   PO versions and request replay protection prevent duplicate/stale posting.
4. Inspect the receipt, replenished order and linked visit without leaving the
   delivery workspace. Readiness updates as shortages clear; receiving does not
   dispatch a visit or contact a supplier/customer.

Posted quantities are not editable. Return/correction workflows, supplier ERP
integration, camera capture, barcode scanning and delivery messaging are future
work. Draft persistence does not provide offline stock reservation. This batch
was exercised against fictional local data only.


## Dispatch, field notebook and agent testing

**07 Dispatch** opens a resource/day plan. Select a visit, propose a time or
technician change, give a reason, and review the exact before/after. Confirm
rechecks overlapping appointments and the job version. Release to dispatch also
checks parts readiness. The latest unchanged assignment can be undone before
dispatch; the decision log remains intact.

**08 Field notebook** captures findings, readings and up to three 2 MB evidence
files. Web offers a mobile camera file input; native uses its OS picker. Reports
are durably queued on the device before sending. Pending submissions survive
restarts/offline reload and retry with the same ID. Corrections are new linked
reports. This does not queue stock, dispatch or billing operations.

`studio.py` supplies a fast agent authoring loop: disposable SQL fixtures,
component/binding/action inspection, semantic fill/press operations, declarative
scenario assertions and actual GPUI screenshots. Structural and native modes run
the same scenarios; no browser is required for those checks. Native mode runs on
an isolated Linux display and enables a private test socket only for that process.

See [DISPATCH_BATCH.md](DISPATCH_BATCH.md) for commands, architecture, verification
and remaining device/platform limits. Normal launches do not enable the test API.

Reactive semantic workspace: **09 Adaptive**. See [REACTIVE.md](REACTIVE.md) for watched data, inline decision trees, draft continuity, Studio outcomes, and Lab transport.
