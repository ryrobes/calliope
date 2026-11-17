# Native testing without desktop interference

Default agent tests must leave the user's workspaces, focus, pointer, and tiled
window layout alone. Do not use ordinary `run.sh`, global input tools, or change
the user's Hyprland rules for routine automated tests. A spare workspace still
shares input and is not equivalent to an isolated desktop session.

## Published apps: real GPUI, direct Calliope connection

From `apps/calliope-canvas`, after building the release client and signing in:

```sh
python3 business/capture_published.py service-work-orders /tmp/work-orders.png \
  --width 1440 --height 1000
```

The command starts a private Xvfb display, explicitly clears inherited Wayland
selection, uses software Vulkan, and launches the release client with `--app`.
It assigns the window geometry directly, independent of the user's tiling rules.
It copies the existing connection into a temporary private configuration and
uses disposable data/cache directories. Cached fonts are copied for typography;
Home, preferences, and drafts are never shared for writing. The server still
supplies the published definition and SQL data. No local Python service or
SQLite database is started. Audio read-aloud is disabled for this test profile.

The PNG, adjacent JSON readiness receipt, and native log remain at the output
location. All owned processes and the temporary profile are removed on exit.
Connection credentials are not printed or included in the screenshot receipt.
Use `--connection PATH` for a separate test account, or `--binary PATH` for a
different compiled client. The default is the existing saved connection.

For semantic interaction checks, Python callers can use
`capture_published.isolated_app` as a context manager, then pass the session to
`studio.probe(session, {"op": "inspect"})`, `fill` or `press`, and `studio.capture`.
Poll the expected bound data after an action; a non-busy frame alone may precede
a filter's debounce. The commands address component IDs through the private
socket and do not operate the physical keyboard or pointer. This is display and
local-profile isolation, **not a server-data sandbox**: a save action has the
connected user's normal server permissions. Prefer read/filter checks or an
explicitly designated test dataset for writes.

Existing `studio.py start --native` remains useful for generated inert previews
and fictional fixture regressions. It has its own fixture server/database and
does not exercise the published app API.

## What this proves

- Real GPUI layout, fonts, charts, resizing and rendered pixels.
- Component bindings, semantic actions and actual published SQL refreshes.
- It does not prove physical pointer/keyboard handling, Wayland integration,
  accessibility, input methods, device voice, or hardware GPU performance.
- A narrow desktop window is a responsive-layout check, not a mobile build.

Use a separate compositor session or VM for OS input and Wayland integration.
For hardware performance, explicitly select and verify the graphics backend;
software Vulkan timings are not workstation GPU timings.

## CUA / Omarchy findings (2026-10-08)

[Cua documents an experimental Hyprland plugin](https://cua.ai/docs/cua-driver/concepts/platform-support#hyprland-and-omarchy)
with two independent agent seats. Its qualification is currently limited to
specific Calc and Inkscape package versions; GPUI support is not established.
The plugin must match Hyprland's ABI/compiler, native Wayland is opt-in, and
explicit foreground actions still move the user's focus/pointer. Installing
the ordinary driver alone does not establish isolated Hyprland input.

The workstation inspection found Hyprland 0.56.2, a user-local CUA Driver 0.25.0,
no running CUA daemon, and no loaded Hyprland plugins. The driver update check
reported 0.34.0. These are an inspection snapshot, not pinned dependencies.
[A multi-monitor Hyprland fix shipped in 0.33.4](https://github.com/trycua/cua/issues/4161),
but that does not expand the plugin's qualified application set.

[Cua also supplies an Omarchy VM image](https://cua.ai/docs/cua-sdk/guides/omarchy)
with its driver/plugin installed. This is a candidate for broader integration
testing, not yet installed or verified with Calliope. Hyprland's
[headless output support](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Using-hyprctl/#output)
can provide predictable geometry, but an extra output on the user's compositor
does not itself provide independent input seats. No user desktop configuration,
CUA installation, or plugin loading was changed for this investigation.

## Published demo regression

`python3 business/check_published_native.py /tmp/published-check` navigates all
eleven Demo surfaces on an isolated display, captures wide layouts, switches
six composed/transformed charts at 950 px, checks live SQL filtering and confirms
that watched notes survive a real decision-driven region change. It submits no
business writes. The generated `native-verification.json` records readiness and
chart errors. The optional Jev assertion requires the decision provider to be up.

`business/check_published_charts.py` and `web/check-published.mjs` consume the
server snapshots exported by the Postgres regression script. They exercise the
real native compiler and official web processor/Vega renderer at 360 and 1000 px.
The web check also validates populated field values, so SQL numeric formatting
cannot quietly introduce invalid editable quantities. Inspect images as well.

`python3 business/check_published_interactions.py /tmp/interaction-check` verifies
real private-display wheel scrolling including list edges, single-record
selection, a watched parts→comeback decision, pending status, Hold/Resume, draft
continuity, and override isolation between pages. This uses unsaved notes only;
it never posts business writes. Its decision assertions require the configured
provider to be available. Render inspection exposes `scroll_areas` geometry and
`refresh_pending`, so evidence goes beyond a screenshot or idle flag.

`python business/check_published_resize.py /tmp/resize-check` exercises the live
published Adaptive app on a private display: changing height, crossing container
breakpoints, keeping drafts/selection/list position, and page scrolling without
expanding the list. It does not save business data.
