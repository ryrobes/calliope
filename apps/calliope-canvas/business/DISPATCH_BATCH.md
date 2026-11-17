# Dispatch, field reports and Callie UI Studio

Local prototype scope: shared GPUI/web resource-day views, reviewed scheduling
commands and guarded undo, append-only field reports/corrections, durable evidence
queues, and an agent-facing structural/native test loop. No Lab deployment or
real technician messages.

## Framework additions

- `CResourceDay` composes ordinary child components in resource lanes with minute
  positions; narrow widths become grouped appointment cards. Selection opens the
  normal typed editor and related-record inspector. Appointment changes use the
  editor and review step, not drag-and-drop or route optimization.
- Dispatch previews store before/after, job version, reason, exact submitted
  inputs and a ten-minute expiry. Confirm rechecks version, overlap and parts
  readiness in one transaction. Request IDs make retries idempotent. Undo applies
  only to the latest unchanged schedule assignment before dispatch; it is an
  audited inverse command, not deletion of the original decision.
- Field reports contain notes, optional temperature/pressure readings and up to
  three files of 2 MB each. Corrections link to an earlier report on the same visit;
  originals remain immutable. Reports do not alter job status, stock or billing.
- Web stores evidence, queued reports and the last field snapshot in IndexedDB,
  with an app-shell service worker for offline reload. Native uses private local
  files and a cached field snapshot. Both retain stable submission IDs until the
  server acknowledges; retries cannot duplicate reports or attachments.
- HTTP validation failures move submissions to Needs review. Network failures
  retain Pending and retry while the app is running. A queue is not background
  delivery while the app is closed. Device storage can still be cleared by the
  user/OS. The queue is capped at 20 pending reports / about 40 MB encoded.

## Agent test interface

Use `studio.py` against a disposable session. It owns a temporary SQL database and
loopback service, leaving the user's live demo and Lab data alone. No browser is
needed for structural or GPUI testing.

```bash
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-ui-1 start
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-ui-1 --compact inspect
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-ui-1 scenario apps/calliope-canvas/business/scenarios/dispatch.json
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-ui-1 stop
```

`inspect` returns stable IDs, component types, resolved text/values, bindings,
actions, validation errors and current state. `fill ID JSON` and `press ID` operate
semantically; scenario assertions fail with nonzero exit status. The same scenario
runs in native mode:

```bash
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-native-1 start --native
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-native-1 scenario apps/calliope-canvas/business/scenarios/dispatch.json
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-native-1 screenshot /tmp/dispatch.png --width 1500 --height 1100
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-native-1 screenshot /tmp/dispatch-narrow.png --width 950 --height 950 --scroll 5
python3 apps/calliope-canvas/business/studio.py --session /tmp/callie-native-1 stop
```

Native mode uses the actual GPUI process on an isolated Linux X display. A private,
opt-in Unix socket exposes semantic inspect/fill/press; button activation uses the
renderer and host's ordinary validation/action path. No test socket exists in an
ordinary launch. Screenshots capture that app's window. Linux native mode requires
`Xvfb`, `xdotool`, ImageMagick and a working graphics driver (the local harness uses
Mesa software Vulkan when available). It exercises GPUI layout but does not prove
physical GPU/Wayland performance, accessibility, touch behavior or OS camera use.

`offline`, `online`, `restart-native`, and `attach ID FILE` support lifecycle and
evidence tests against the disposable fixture. Attach invokes the same local
capture/storage path after file selection; it does not test the OS chooser itself.

Recommended authoring loop: validate/inspect the structure, run workflow scenarios,
then capture wide/narrow native images and review them. Structural success cannot
prove readable layout or visual quality. This studio currently tests locally
implemented definitions; it is not a remote arbitrary-definition execution API,
an MCP deployment, or proof of agent generation quality.

## Verification

Verified on 2026-10-08:

- 36 Python tests, 9 official web processor/continuity tests, 13 native renderer
  tests and 3 native draft tests passed; native and web builds succeeded.
- Backend cases include concurrent confirmation replay, preview expiry, edited
  inputs, stale job versions, overlaps introduced after preview, guarded undo,
  linked-parts dispatch blocking, atomic evidence, retry deduplication and
  cross-visit correction rejection.
- The same 14-step assignment/preview/confirmation/undo scenario passed in
  structural mode (0.040 seconds locally) and the actual GPUI host (5.981 seconds).
  These are fixture timings, not model end-to-end generation benchmarks.
- Native report `FR-EF3DF1C555` and its evidence survived a service outage and full
  native process restart, then delivered once after reconnection. SQL confirmed
  exactly one report/evidence link and no visit status change.
- Web report `FR-F49706FF03` and its evidence survived an offline page reload,
  then delivered after reconnection. A correction `FR-F1746B55F7` remained linked
  to the intact original. A first test exposed missing precached JS/CSS; the
  service worker now precaches initial shell dependencies and the repeat passed.
- Web overlap validation and reviewed confirmation passed interactively. The
  phone-width dispatch and field notebook both had 375 px content/scroll widths
  with a 390 px viewport; its camera input requests the environment camera. Native screenshots
  checked 1500 px lanes/review and 950 px grouped cards. Upcoming unslotted visits
  are accessible through the Visit to plan picker without editing existing jobs.

Physical Android/iOS camera permissions, suspend/resume and OS storage eviction
have not been exercised on a handset. The local browser supplies the mobile web
experiment; this does not establish GPUI mobile readiness. The follow-up [UI Studio](UI_STUDIO.md) adds an authenticated runner, a Callie
command/skill, and strict loading of generated previews. Physical device testing
and production publication remain separate.


The running local service and desktop binary were refreshed after verification.
The web preview is open on the dispatch editor for the existing upcoming Harbor
Dental visit; no schedule command was committed against that user's demo record.
A pre-batch SQLite backup is retained beside the local demo database.

See [UI_STUDIO.md](UI_STUDIO.md) for the subsequent Lab deployment and agent
authoring interface. The verification above records the original local batch.
