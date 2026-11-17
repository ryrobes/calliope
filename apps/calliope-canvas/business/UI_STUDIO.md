# UI Studio

UI Studio is Callie's headless app development tool. One `calliope.app.v1`
definition feeds the web and GPUI renderers; previews can use supplied data or
governed reads on the Calliope server. Preview actions are simulated. Publication
uses the existing live-app registry and tools. The isolated worker also retains
older disposable SQL fixtures for regression tests; these are not app backends.

## What Callie can use

The main Callie profile and Dashboard Studio receive the `calliope-native-apps`
skill and the `calliope-ui-studio` terminal command. The command discovers its
private connection in `~/.config/calliope-ui-studio.json`; no token in prompts or
arguments. Use `--config PATH` or `CALLIOPE_UI_STUDIO_CONFIG` for an operator client.

- `catalog OUTPUT.json`: current custom vocabulary, supported builtin props,
  `pagePattern` guidance and a runnable `starter` definition. See the
  [workspace style](WORKSPACE_STYLE.md) for live filters and section hierarchy.
- `example VIEW OUTPUT.json`: export any of the ten compositions with fixture data.
- `--session ID fixture_event case_delivery`: post the fixed case delivery through
  reviewed fixture commands; test how an active draft responds to another worker.
  Scenarios also support `assert_data` with `path` and `equals` for model state.
- `check FILE.json`: strict graph, property, binding, resource and chart checks.
- `--session ID start [--native]`: fresh disposable SQL application.
- `--session ID load FILE.json`: switch to an inert generated definition.
- `--session ID inspect [--id PREFIX]`: compact semantic state; `--full` before the
  command includes all properties and data.
- `--session ID fill ID JSON`, `press ID`, `scenario FILE.json`: semantic interactions
  and assertions. Generated previews record resolved events without executing them.
- `--session ID screenshot OUTPUT.png --width 1500 --height 1100`: actual GPUI
  pixels returned to the agent's own filesystem. Capture waits up to 20 seconds
  for measured charts and returns `ready`, `pending_charts` and `chart_errors`.
  A failed render still produces an image for diagnosis; require `ready: true`
  before treating it as a finished visual check. Also check 950 px, or 390 px
  for a desktop-renderer stress test; that is not a native mobile-device test.
- `--session ID offline`, `online`, `restart-native`, `attach ID FILE`: existing
  fixture lifecycle/evidence checks. Restart returns to fixture mode; reload custom
  definitions explicitly afterward.
- `--session ID stop`: reclaim fixture processes and data. Save evidence first.

The authoring document is the published `calliope.app.v1` definition, including
SQL bindings, fragments, repeats, switches, events and forms. `check` and `load`
use the server's validator and materializer. Supplied model values drive these
previews; SQL is not executed by the isolated worker. State-only events apply,
and command events expose their resolved inputs with `simulated: true`; they
never commit changes or clear a supposedly saved draft.

The older `{title?, components, data, reactive?}` format remains supported for
existing fixture tests.
See [reactive resources](REACTIVE.md) for watched evidence, policy JSON, outcomes,
and the `decision RESOURCE LABEL` command for forced structural/native previews. Stable component
IDs and the `root` graph use the same Calliope A2UI catalog already rendered on
web and GPUI. The runner normalizes this into versioned v0.9 messages. It accepts
full fixture snapshots too. Every node must be reachable once; unsupported
components, unresolved bindings, remote chart sources and expression capabilities
are rejected before rendering. Definitions are limited to 500 nodes and 1.5 MB.

Structural previews exercise bindings and action payloads. Native mode sends
semantic actions into the actual renderer and captures its isolated X window.
Both differ from physical keyboard/touch, accessibility, camera, suspend/resume
and device GPU testing. Screenshots still need visual review. Published commands use named SQL actions in the Calliope server; see
[published company apps](SERVER_APPS.md). Preview commands remain inert.

## Complete app previews

Callie's `inspect_ui_app` tool validates a full definition and returns resolved
nodes, binding paths, query diagnostics, and definition/data hashes. Choose
`mode="supplied"` for deterministic layout checks or `mode="server-read"` for
governed reads as the current caller. Actions are simulated in both modes.

`capture_ui_app` resolves data once and captures Web and GPUI at Desktop
(1440×1000) and Phone (390×844), using the caller's appearance. Its image order
and per-capture evidence identify viewport, renderer build, data, theme, and
readiness. Linux GPUI captures are explicitly layout checks, not phone OS tests.
Require successful readiness and review the images; a diagnostic screenshot
does not mean the capture passed. Save the evidence alongside `app.json` when
publishing the exact tested definition.

The worker's `screenshot` command also accepts `--viewport desktop|tablet|phone`;
explicit `--width` / `--height` override the preset. The private worker API's
`render` operation captures all requested presets in one disposable session and
cleans it up, including when a capture fails.

Saving `app.json` or `NAME.app.json` during a Callie build now feeds the existing
artifact-work Stage preview. Desktop/Phone and Web/GPUI controls inspect the
same revision; Try app opens an interactive web view with server reads and
simulated commands. Uploads are disabled there. Stale revisions cannot replace
newer captures, and failed updates retain the previous successful frame.
These drafts remain temporary and session/viewer scoped; publish through the
normal app tools to create a durable artifact version.

Use `services/calliope/examples/ui-apps/hvac-visit.app.json` for the first shared
fixture. It has supplied fictional visits and ordinary SQL bindings to the
already-installed demo tables, a decision-tree region, notes, a chart and photo
controls. Its manifest saves only when explicitly invoked in a published app.

## Deployment

Build from this monorepo, with the pinned Cargo/npm locks retained:

```bash
docker build -f apps/calliope-canvas/Dockerfile.studio \
  --build-arg REVISION=GIT_SHA -t calliope-ui-studio:REV .
```

The builder uses Rust 1.98 on Debian trixie so the binary does not depend on the
workstation's newer glibc. Runtime includes software Vulkan, Xvfb, ImageMagick,
fonts, and the checksum-pinned vl-convert 1.9.0 renderer. It runs as UID 10001.
The image uses about 700 MB locally; its first build compiles the native dependency
graph, while repeat builds cache it. Build/runner paths intentionally match because
some host-owned asset paths are embedded at compile time.

Load that image on the appliance, then prepare one release directory containing:

- `docker/docker-compose.ui-studio.yml` as `docker-compose.ui-studio.yml`
- `apps/calliope-canvas/business/studio_client.py` as `studio_client.py`
- `docker/calliope/hermes-skills/calliope-native-apps/SKILL.md` as `SKILL.md`
- `scripts/appliance/install-ui-studio.py`

Run on the appliance (image must already be loaded):

```bash
sudo python3 /path/to/release/install-ui-studio.py \
  --release /path/to/release --image calliope-ui-studio:REV --revision GIT_SHA
```

The installer creates `/opt/rvbbit/ui-studio`, a private persistent token, isolated
fixture storage, a Compose file/env, configuration backups and a receipt. It starts
only `calliope-ui-studio`; existing application containers and their runtime env
are preserved. The client and skill live in the durable Hermes volume, including
the Dashboard Studio profile. Installing does not restart Hermes. Start a fresh
agent turn/task to pick up skill discovery; an in-flight prompt is not rewritten.

The service has **no published host port**. It uses the internal Calliope Docker
network, bearer authentication, a read-only root filesystem and its own writable
fixture volume. It has no Lab data credentials, Docker socket or Hermes data
mount. Screenshots and definitions cross the API as bounded JSON, not arbitrary
filesystem paths. It cannot execute uploaded Python, shell or JavaScript.
Four concurrent sessions maximum, a one-hour idle expiry, and fixed resource
limits bound the runner. Container restart invalidates all sessions. This is a
trusted single-appliance authoring tool, not a public multi-tenant rendering API.

Rollback: use the previous image/configuration saved under the deployment root's
`backups`, or `docker compose ... down` for this separate project. Client/config
and skill backups live in Hermes `/opt/data/backups/ui-studio-REV`. Do not reset
Lab's normal Compose stack or its SQL volumes to roll back the optional runner.
