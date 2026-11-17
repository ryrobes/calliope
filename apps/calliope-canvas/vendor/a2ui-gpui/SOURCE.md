# Source and local changes

MIT-licensed `crates/gpui` from https://github.com/liangdi/a2ui,
commit `97391988e41052240c36c27442912420903f13a8` (0.3.1).
The matching `a2ui-base` and `a2ui-image` dependencies are pinned to that commit.

This is a community renderer, not an official Google GPUI implementation.
The upstream project targets v1.0. This experiment uses the overlapping **v0.9**
createSurface/updateComponents/updateDataModel and basic catalog subset.
It does not establish full protocol or catalog conformance.

Local adaptations:

- Embed a surface without the sample gallery and title bar.
- Expose bound state and resolved action events to Calliope's transport.
- Reset input caches when replacing a surface so selection and reload are fresh.
- Honor weighted row children, captions, multiline fields and host theme colors.
- Disable form interaction while a request is pending.
- Render Calliope's six presentation components (responsive stacks, semantic
  surfaces, typography, badges, meters and an allowlisted illustration). The
  same A2UI definitions drive the official React renderer's custom catalog.
- Add `CVegaChart`: host-owned Rust converter, asynchronous measured image views,
  one job per chart, stale-result rejection, and bounded entity retention.
- Resolve custom text labels inside ordinary A2UI buttons.

Ordinary launches load the local transaction service. The optional Studio runner
also accepts generated previews after strict catalog, graph, property, binding and
resource validation in `business/studio_document.py`; their actions are recorded
without execution. Unused upstream image/media/modal paths are outside the
supported subset. The private native socket is not a public definition API.


Parts-desk follow-up: single-choice delegates retain business values separately
from display labels, with a regression for `7 days` → `7` and duplicate labels.
`CRecordPicker` reuses that select (searchable above six options); `CDataGrid`
composes bounded cell children with headers or narrow-window cards. `CStack`
accepts a finite boolean wrapping token.


Receiving/continuity batch:

- Reconcile changed components and data in an existing surface; retain input,
  select and chart entities for stable IDs and prune removed components.
- `CField` adds typed validation, units and conditional/required/read-only state.
- `CDataGrid` uses stable row keys, measured column resizing, bounded atomic TSV
  paste, and GPUI input-action capture for Tab/Enter/up/down navigation.
- `CRecordPane` is independently scrolled beside the primary workspace and uses
  an overlay sheet below 1000 logical pixels.
- `CAttachments` emits bounded host actions; the Calliope host owns OS file
  selection, upload and external-browser viewing.
- Input subscriptions are keyed by component ID. Host-owned per-record drafts
  and three-way merge preserve edits and expose overlapping remote changes.

These are Calliope catalog extensions implemented in both hosts, not features
provided by the default A2UI catalog.


Dispatch and UI Studio batch:

- `CResourceDay` renders shared child components in bounded resource/time lanes,
  with grouped cards below the native compact breakpoint.
- Offline attachment presentation delegates capture and device storage to the
  host. The ordinary receipt-upload path remains separate.
- Opt-in semantic inspection resolves current model bindings, and fill/press use
  bound input entities and the ordinary activation path. Calliope exposes this
  only through a private local socket in a disposable test process. Screenshot
  capture is handled by the isolated Linux test runner, not by a browser.
