# macOS architecture

## Decision

Data Rabbit for macOS uses Electron as a deliberately thin native shell around
the complete Next.js product.

This is a parity decision, not merely a packaging shortcut. The existing
application combines:

- a large React desktop/window manager;
- CodeMirror, AG Grid, Vega, ECharts, Chart.js, Sigma, and drag/drop surfaces;
- more than 50 local server endpoints;
- Postgres pools, transactions, notifications, and cancellation;
- SSH tunneling and streaming multi-gigabyte imports;
- Node filesystem, SQLite, image, audio, and process integrations; and
- optional Docker-backed rvbbit capability installation.

A SwiftUI rewrite would replace all of those mature implementations at once.
Tauri would preserve the renderer, but the server would still require a Node
sidecar or a second backend implementation in Rust. Electron supplies the same
Chromium and Node runtime boundary the product already depends on, while
producing a normal macOS `.app` with native menus, lifecycle, permissions,
windowing, Dock behavior, and future system integrations.

The architecture keeps this choice reversible: product logic stays in the
Next/React codebase, and native APIs are exposed only through a tiny,
allow-listed bridge.

## Runtime

```text
Data Rabbit.app
├── Electron main process
│   ├── macOS application menu and lifecycle
│   ├── single-instance lock
│   ├── external-link isolation
│   ├── microphone permission mediation
│   └── bundled server supervisor
├── private Next server (127.0.0.1:43129)
│   ├── Postgres and SSH integrations
│   ├── Lens SQLite homebase
│   └── rvbbit, Plate, MCP, assistant, and import endpoints
└── sandboxed renderer
    ├── complete Data Rabbit desktop
    └── allow-listed menu and Scene-snapshot bridge
```

The packaged server is the `output: "standalone"` Next build. Build output is
stored as a single resource archive so Electron's dependency filtering cannot
drop the standalone server's nested `node_modules`. On first launch (or after
an app update), the main process verifies the archive size and extracts it into
a versioned runtime under Application Support.

The main process reserves the stable private origin
`http://127.0.0.1:43129`, starts the extracted server with Electron's embedded
Node runtime, waits for an HTTP-ready response, and only then displays the
window. A stable origin is essential because Chromium keys localStorage,
IndexedDB, cookies, and permissions by host and port; choosing a new ephemeral
port on every launch silently loses Scenes, Saved Views, wallpapers, and
desktop state. If the port is occupied, startup fails closed with a diagnostic
instead of loading an unrelated local process. Advanced deployments can choose
a different stable port with `DATARABBIT_SERVER_PORT`. Server output is
appended to `server.log` inside the app's Application Support directory. The
child process is terminated when the app quits.

Closing the last window follows normal macOS behavior: the application and its
private server stay available for a fast Dock reopen. Activation creates a new
window against the same origin and process. A second application launch focuses
the existing instance. Explicit Quit shuts down the server, destroys any
remaining native window, and terminates the application process.

Electron 39 is intentionally pinned as the runtime line. Its embedded Node
22.22 supplies `node:sqlite`, while its native ABI remains compatible with the
current Postgres/SSH dependency ecosystem. `ssh2` treats its NAN-based CPU and
crypto bindings as optional and automatically uses its pure-JavaScript
implementation when they are unavailable, so the packaged server does not
rebuild or rely on those optional accelerators.

Development mode uses the same renderer and bridge against
`http://127.0.0.1:3100`, preserving Next hot reload.

## Persistence

The main process provides explicit runtime paths:

| Environment variable | macOS location |
| --- | --- |
| `RVBBIT_LENS_HOME` | `Application Support/Data Rabbit/config` |
| `LENS_DATA_DIR` | `Application Support/Data Rabbit/lens-data` |
| `RVBBIT_LOCAL_WORK_ROOT` | `Application Support/Data Rabbit/workspaces` |

This keeps mutable state out of the signed application bundle. Connection files
retain the web product's mode-`0600` write behavior.

The server inherits the launch environment so advanced deployments can still
provide rvbbit URLs, MCP tokens, model credentials, and seed DSNs. Its `PATH`
also includes the conventional Homebrew locations so Docker and local helper
CLIs remain discoverable when the app is launched from Finder.

## Security boundary

- `nodeIntegration` is disabled.
- `contextIsolation`, renderer sandboxing, and web security are enabled.
- The preload exposes no filesystem, shell, process, or arbitrary IPC method.
- Scene capture is a no-argument native method limited to the active app window;
  the main process returns a capped JPEG thumbnail rather than exposing screen
  capture primitives.
- Native-to-renderer messages are checked against a fixed menu-action set.
- New windows are denied; HTTP(S) links are handed to the default browser.
- Navigation away from the private server origin is denied.
- `<webview>` attachment is denied.
- Only audio capture can pass the Electron permission handler; macOS retains the
  final system permission prompt.

Database privileges remain exactly those of the Postgres role supplied by the
user.

## Packaging

`electron-builder` uses the dependency-free `electron/package.json` as its app
manifest, so the application archive contains only the native shell. The root
web dependency graph is already present in the standalone-server archive and is
not duplicated in `app.asar`. The packager copies that complete server archive
to application resources. The native icon is generated from the same vector
rabbit mark used by the desktop, and DMG and ZIP targets are generated for the
current CPU architecture.

Local builds receive an ad-hoc outer bundle seal after the final Info.plist
hardening, including a complete `CodeResources` manifest. The packaged smoke
suite requires `codesign --verify --deep --strict` to pass before launch.
Developer ID signing, hardened-runtime entitlements, and Apple notarization are
the final public-distribution layer. They require credentials and therefore
remain outside the repository; when configured, electron-builder replaces the
ad-hoc seal during its normal signing stage.
