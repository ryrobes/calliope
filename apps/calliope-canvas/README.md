# Calliope Canvas

A Linux desktop experiment built with Rust, GPUI 0.2.2 and gpui-component
0.5.1. The home screen is a persistent, GPU-rendered canvas of useful objects
from different Lab conversations. Home contains topic bubbles and kept objects.
Each topic opens its own curated canvas, with conversation and voice scoped to
that topic. Full conversation history stays available in Lab.

This is a working local prototype, not a packaged cross-platform release.
There is no browser engine embedded in the app.

## Run locally

Keep the existing Lab tunnel running: the default API is
`http://localhost:18080`; browser links use `https://localhost:18443`.
The normal Lab credentials work in the native Connection dialog.

From this directory:

```sh
cargo build --release --locked
./run.sh
./run.sh --meaning     # native map of published business meaning
./run.sh --check       # authenticated, read-only connection smoke check
./install-local.sh     # add Calliope Canvas to the Linux application menu
cargo test --locked
CALLIOPE_CANVAS_PROFILE=debug ./run.sh  # explicit developer build
```

The launcher and local Studio use the optimized release binary in this checkout.
`debug = 0` in Cargo's development profile removes symbols, not optimization costs.
Rebuild with `cargo build --release --locked` after source changes;
keep the checkout in place. `run.sh` prevents two instances from writing the
same Home. The initial build requires Rust, a C toolchain and GPUI's Linux
graphics/text dependencies. This checkout was built and exercised on the
local Arch/Omarchy Wayland system with Rust 1.98.1, Fontconfig, XKB and Vulkan.
Voice I/O also requires `pw-cat` from PipeWire (already installed here).

For agent testing, use the [isolated native workflow](business/NATIVE_TESTING.md)
instead of opening windows on the user's Hyprland workspaces. Published apps can
be captured at fixed dimensions with `python3 business/capture_published.py SLUG
/tmp/calliope-preview.png`. This runs the real release renderer against Calliope
in a disposable virtual display/profile; it does not start a demo server.

Published company apps use a responsive tile launcher shared with Android. Tiles
use the configured palette and typography, plus registry names, descriptions and
app kinds. Charts in published apps use Calliope's authenticated
`/api/calliope/ui-charts/render` endpoint for PNGs and picking geometry; the client
no longer needs a Python chart helper for these apps. Local fixture previews
still use the shared converter through `business/render_chart.py`. Native chart
errors can be retried with a click or the app's Refresh action.

Worker results, business actions and bound edits wake the host immediately. A
120 ms maintenance timer remains for voice/debounce/deadline checks; it does not
gate clicks or completed requests. Unchanged models skip draft serialization and
reactive payload copying. The opt-in test probe also sleeps until a request arrives.

Canvas layouts and business drafts are saved by ordered background writers, with
one replaceable pending snapshot per file: 250 ms after the last change, or a
checkpoint after one second of continuous changes. Normal window close drains the
final save. Disk failures appear in the UI and stderr. A forced kill can lose the
latest unsaved interval. Offline report submission retains its existing explicit
durable-save-before-send behavior.

For repeatable renderer measurements, use a disposable native Studio session:

```sh
python3 business/studio.py --session /tmp/canvas-perf start --native
python3 business/studio.py --session /tmp/canvas-perf frames --count 120
# Read once the sample finishes (remaining: 0).
python3 business/studio.py --session /tmp/canvas-perf frames
python3 business/studio.py --session /tmp/canvas-perf stop
```

These are forced-redraw frame intervals including presentation pacing, not GPU
execution times. Studio's Xvfb uses software Vulkan; compare identical definitions,
window sizes and backends, and verify hardware performance separately on Wayland.
The frame probe is available only through the opt-in private testing socket.

## Explore

- **Meaning:** explore published business areas as a native graph of sources,
  metrics, analysis ideas and authorized apps. Select connections for evidence,
  pin useful objects to Home, or prepare a contextual question for Callie.
  Values retain their sample/observation timestamps. See the
  [Meaning map guide](business/MEANING.md) for provenance, bounds and isolated tests.

- **Home:** click a topic bubble (or Enter) to open its workspace; drag a
  bubble to arrange it. Home also holds objects pinned from inside topics.
  The existing Workbench is retained for older placements.
- **Browse topics / Browse Lab:** find a Lab conversation, then **Open topic**.
  **New topic** creates a real Lab conversation and opens its canvas.
- **Topic canvas:** starts with up to eight useful objects, including any
  previously extracted query views. Newly discovered work adds at most four
  objects per sync. **All topic items** lets you keep more or restore hidden
  items. Older artifact versions and the full receipts remain in the web UI.
- **Move and zoom:** drag headings to move objects; drag empty canvas to pan;
  scroll to zoom around the pointer, or use +/−. Fit includes topic bubbles as well as objects.
  Every canvas remembers its own camera and placements.
- **Resize blocks:** drag the bottom-right corner, right edge or bottom edge.
  Sizes persist per placement and resizing accounts for canvas zoom. The selected
  block comes forward over its neighbors. **Reset block size** restores the default.
- **Tables:** every returned column and row is available, with readable column
  widths, fixed headings, and horizontal/vertical scrollbars. Shift+wheel scrolls
  sideways; scrolling a grid does not zoom the canvas. Click a cell to read or copy
  its full value, then **← Table** to return. Footers distinguish loaded rows from
  a larger reported result; these are still bounded Lab results.
- **Explore a card:** read its native content; **Wide view** uses the window width
  and **Side view** returns to the panel. **Pin to Home**,
  or **Hide from this canvas**. Hiding never deletes Lab work. Hiding a saved
  artifact also prevents later versions of that lineage from automatically
  reappearing in the topic. You can explicitly keep one again from the browser.
- **Boards:** **Open board** opens a native, scrollable workpack in Wide view.
  The published 12-column spans arrange text, formatted stats and referenced
  surfaces; narrow blocks and Side view stack cells. Tables retain both scrollbars
  and full-cell inspection. New board blocks start at 960 × 640; existing sizes
  are preserved, and **Reset block size** applies the larger board default.
- **Board pieces:** each cell in an open board has **Keep here +** and **Pin Home ↗**.
  A piece keeps its heading, format and exact board/source references, with its
  own position and size. Pinning again does not duplicate it; hiding a piece
  does not hide the parent board.
- **Board data:** boards initially show saved results. **Refresh data** reruns
  their saved query definitions; **Auto: on** refreshes them at most once a minute
  while their board or a pinned cell is visible in the focused app. **Use saved
  data** returns to the original receipts and stops future reads. These controls
  also appear when exploring an individual board piece. Cells that reference the
  same query share one result and setting across boards, Home and topics.
  At most two previews run concurrently across boards and extracted app queries.
  Written notes, query filters, explicit data-time scope and source versions stay
  fixed. A new read time is not proof that the underlying data changed.
  Failures retain the previous result with a visible error; manual failures wait
  for **Refresh data**. Returning to saved data discards late refresh responses.
  Refresh preferences survive restarts; refreshed data itself remains in memory
  and is read again from Lab when next viewed.
- **Shared objects:** Home and topic placements use the same exact source/query
  identity and share one in-memory result and refresh setting. Their positions,
  sizes and visibility are independent. Click the origin label to return to
  the object's topic. Putting a topic away hides its bubble, preserving its
  contents, layout, and Home pins; reopening it from Browse restores the bubble.
- **Keep a live view:** Explore an app, choose **Pull out a native piece**, then
  **Keep live view** on a supported query definition. This creates a refreshable
  object beside the saved receipts. Existing query pieces migrate with manual
  refresh; Explore lets you enable **Auto refresh** or **Refresh now**.
- **Refresh policy:** opted-in query views refresh at most once every 60 seconds
  while on-screen in the focused app, with at most two concurrent preview reads.
  The server rechecks access and validates the bounded query. All placements
  share the result. Labels show the read time, fixed historical scope when
  supplied by Lab, and failures; an unsuccessful refresh retains the previous
  result visibly marked as such. A read time is not a source-data freshness claim.
  Query definitions/versions and their parameters remain fixed; refresh never
  silently switches to a newer artifact or removes an as-of constraint.
- **Conversation:** talk within the open topic; select an object to include its
  parent surface as context. For extracted queries, context still identifies
  the parent app, not the particular query. Unsent drafts survive topic switches
  in memory. History is available on demand. A running reply stays associated
  with its topic when you navigate elsewhere; one turn runs at a time.
  Switching canvases cancels microphone/playback so speech cannot land in the
  wrong draft. Read-only/mirrored chats stay read-only.
- **Open in browser:** full apps, charts, maps and unsupported interactions
  open in the OS browser, with its normal Lab login.

Native renderers cover query rows, single-row values, metrics, image previews,
text/Markdown and the published board layout contract. Boards support text,
number/currency/percent/compact stats, table and SQL query views, Markdown and
images. Chart-style queries show their saved data; chart/map surfaces and other
unsupported members have browser source links. Full app execution and arbitrary
generated HTML remain in the browser. Boards reuse Lab's existing declarative
format; this does not introduce a separate UI DSL or execute generated code.

## Voice

- **Dictate** starts the system's default microphone. **Stop recording** uploads
  the bounded recording to Lab, then inserts the transcript into the editable
  composer. It never sends a chat message. **Discard** stops capture and ignores
  late results. A small level meter shows whether audio is reaching the app.
- **Voice on/off** controls automatic reading of new replies on this device.
  Initially it inherits the web client's saved `read_aloud_mode`; the toggle
  persists a local override without changing web preferences.
- **Listen** reads/replays the latest completed reply in the selected
  conversation. **Stop audio** immediately stops local output, including during
  preparation. Starting dictation or sending a new message also stops playback.

`src/voice.rs` is the reusable native audio layer. It captures mono 16 kHz PCM
through PipeWire, keeps it in memory, wraps it as WAV, and calls the web client's
`POST /api/calliope/transcriptions` endpoint. It respects Lab's byte and duration
limits. Microphone capture starts only from the Dictate control and stops when
the app closes. The server's current Lab STT provider uses recorded audio;
the native client does not implement the optional WebRTC live-preview path.

TTS calls `POST /api/calliope/voice/renders` with the real session/turn IDs and
your saved voice mode/personality, then streams the returned audio URL using
`timed-pcm-ndjson-v1`. The output sample rate comes from the response headers.
The provider, credentials, rewrite, voice choice and audio cache remain on Lab.
Automatic playback uses the server's `auto=1` lease; refresh/history reads do
not replay old replies. Only completed replies are read automatically; spoken
working updates and karaoke highlighting are not implemented yet.

The desktop stores no recordings or PCM playback files. Stop/Discard invalidates
late client events; already submitted transcription/synthesis work may finish
on the server. PipeWire uses the system's selected input/output and processing;
there is no extra browser-style echo cancellation or noise suppression here.
Optional `CALLIOPE_CANVAS_AUDIO_INPUT` and `CALLIOPE_CANVAS_AUDIO_OUTPUT` environment
variables select PipeWire targets. `CALLIOPE_CANVAS_AUDIO_MONITOR=1` explicitly
captures the selected sink monitor, used for isolated synthetic-audio testing.

## Appearance primitives

`src/theme.rs` loads `/api/calliope/appearance`, including the resolved personal
or workspace appearance. It maps the shared image palette and dark/light mode
to semantic native colors and all gpui-component color roles. Inputs, Markdown,
panels and canvas objects share one typography scale, palette, chart colors,
corners and surface treatments. Components should use these roles instead of
adding independent colors or font stacks.

The same Superior, Superior Mono and Superior Serif font files are loaded from
Lab's `/theme/fonts/` routes into GPUI's text system. Uploaded wallpapers use
the authenticated appearance image route; library wallpapers stay on the Lab
origin. Images are cached privately and rendered as GPU textures, with a subtle
opacity, directional shade, translucent cards and soft shadows.

**Appearance** offers Quiet/Soft/Vivid wallpaper strength for this desktop and
**Sync from Lab** to reload the shared appearance and voice settings. Initial
strength is Soft (22%). Change the underlying palette, wallpaper or color mode
in the existing web theme system, then sync here. Desktop adjustments do not
rewrite the web/workspace appearance. Color conversion is sRGB with gamut
clipping, so extreme colors can differ slightly from wide-gamut browser output.

## Connection and persistence

Linux defaults (respects the standard XDG locations):

- `~/.config/calliope-canvas/connection.json`: API/browser URLs, email and
  session cookie. No password is saved. Edit URLs here while the app is closed.
  Nonlocal connections require HTTPS; authentication redirects are not followed.
- `~/.local/share/calliope-canvas/home.json`: version 2 layout with topic bubbles,
  exact object/query references, optional board-cell indexes, per-canvas cameras,
  curation and live settings.
  The first upgrade saves the previous layout as `home-before-topics.json` and
  preserves every existing Home/Workbench placement. Layouts are scoped to the
  configured origin/email.
  A different account starts a fresh Home; multiple profiles are not yet supported.
- `~/.local/share/calliope-canvas/previews/`: image previews only.
- `~/.local/share/calliope-canvas/preferences.json`: local wallpaper strength
  and optional read-aloud override.
- `~/.local/share/calliope-canvas/appearance/` and `fonts/`: private wallpaper
  and shared font caches. The appearance is authenticated again on connection.

These files use private directories and mode 0600. Query results and chat
transcripts are held in memory, not saved in Home. On restart, sources are
read through authenticated Lab APIs before content is shown. Pins keep their
exact artifact version; the browsing list folds older versions of a lineage.
Query definitions stay pinned, but query results can change within the saved
query scope; cards and Explore display the result's observation time. Query
results, transcripts, and unsent drafts are not persisted in the layout.

The server's existing Data APIs perform bounded query previews. The desktop
client neither executes arbitrary generated code nor introduces new backend
routes. The contents/preview APIs now also accept saved Stage query sources,
checking conversation and dataset access under the viewer on every read. The
server resolves the exact SQL and data-time scope; the desktop sends source and
definition IDs. Incomplete SQL, sheet snapshots and queries unsupported by the
Data guard stay saved with an explanation. Sending a message and running a query
use the real Lab instance.

## Prototype boundaries

The **Business desk** header action opens a separate A2UI experiment: a fictional
real estate transaction workspace rendered natively and in an OS browser from
the same definition and local SQLite store. See [business/README.md](business/README.md)
for launch instructions, supported components and renderer findings. Run with
`--business` to open it directly. This does not change Lab data.

- Tested on Linux/Wayland here. macOS, Windows, iOS and Android builds,
  packaging, signing and mobile layouts have not been implemented or validated.
- No chart/map renderer, general generated-app platform, multi-object context, nested topics,
  undo, cross-device canvas sync or automatic selection of newer definitions.
  A topic currently maps to one Lab conversation. Canvas state is local to this
  device; drafts are retained across navigation but not application restarts.
- No automatic SSE reattachment or background subscription to other clients'
  work. A disconnected submitted turn may keep running on Lab: refresh the
  conversation before resending. A received assistant reply does not imply all
  delegated background work has finished. New work is available through Refresh
  and Browse Lab. Discovered work can enter its topic canvas; Home promotion
  always requires an explicit pin.
- History currently shows the latest 12 turns. Tables show bounded samples,
  not full database exports. Native accessibility, keyboard-only navigation,
  large-canvas performance and offline behavior need further work.
- Session credentials currently use a private file, not the system keyring.
  The app must remain open for voice playback and streamed replies.

## Dependency compatibility

`vendor/xattr` is upstream xattr 0.2.3 with its original MIT/Apache licenses.
GPUI's published dependency chain reaches this older crate through
`gpui_http_client -> zed-async-tar`. Its Linux code used libc's removed ENOATTR
alias. The small local patch uses the equivalent ENODATA constant on
Linux/Android, preserving the BSD/macOS path. This keeps current libc and
avoids modifying the global Cargo cache. See `vendor/xattr/PATCH.md`.

The app has its own Cargo workspace and lockfile; it does not alter the
database extension's build or Lab deployment.

## Callie authoring and testing

Use the shared [workspace style and live-filter recipe](business/WORKSPACE_STYLE.md)
for new pages. Studio’s catalog includes a runnable starter definition.

The optional [UI Studio service](business/UI_STUDIO.md) gives Lab Callie a
structural checker, isolated SQL fixtures, generated-definition previews, semantic
workflow tests and real GPUI screenshots. The ordinary desktop launch never
enables the private testing socket.
