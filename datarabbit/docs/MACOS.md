# Data Rabbit for macOS

The native macOS distribution of the Data Rabbit SQL desktop. It packages the
complete `rvbbit-lens` Next.js application and its local Node server inside a
macOS `.app`, then adds native lifecycle, menus, keyboard shortcuts, window
chrome, permissions, and Application Support storage.

The product remains local-first:

- Postgres connections (including passwords and SSH tunnel settings) are stored
  in the current user's Data Rabbit Application Support directory.
- The local Lens SQLite homebase, scenes, saved views, and assistant history are
  stored beside that configuration.
- The app listens only on its stable private `127.0.0.1:43129` origin. A stable
  origin is required for browser-backed desktop state to survive relaunches.
- No cloud control plane or application account is required.

When the active Postgres database has the `rvbbit` extension, the complete
semantic surface is enabled: capabilities, operators, routing, semantic
testing, knowledge graphs, Warren, Hindsight, plates, metrics, cubes, and the
desktop assistant.

## Requirements

- macOS
- Node.js 22.5 or newer for development (the packaged app supplies its own
  runtime)
- A reachable Postgres database

Docker Desktop is optional and used only by capability installation flows that
launch containers.

## Develop

Install dependencies, then launch the Next development server and native shell:

```bash
npm install
npm run dev
```

The native window connects to a private development server at
`http://127.0.0.1:3100`. To run the source as an ordinary browser application:

```bash
npm run dev:web
```

## Verify

```bash
npm run typecheck
npm run lint:mac
npm run check:sql-viz
npm run build
npm run pack:mac
npm run test:mac:smoke
npm run test:mac:rvbbit
npm run test:mac:surfaces
npm run test:mac:persistence
npm run test:mac:write-workflows
```

`npm run lint` currently reports inherited React-compiler findings from the web
source and is not yet a green release gate. `npm run lint:mac` is the green gate
for the new native shell and test harness.

The rvbbit test is read-only. It compares the packaged app with the running
reference at `http://localhost:3000`, using an isolated copy of the reference
connection registry. In addition to schema/API parity, it follows live records
through dashboards and plates, operator/test evidence, MCP activity, routing
pins, Knowledge Graph entity evidence, Document Brain, a metric execution, and
cost receipts. It also exercises Metric Creator resolved SQL, versioned Viz
Blocks, KPI Board catalog fallback, Cube Creator's propose/pack/edit forms, and
the proposal inbox without saving or drafting. It exercises Assistant identity
and dock behavior, MCP
gateway readiness, Specialist detail, Hindsight evidence and graph rendering,
Cube and Alert detail, Query Lens tracing, KG extraction/merge queues, a
capability pack's generated SQL, Model Studio, scheduler/GQE/Data Mover gates,
Warren empty operations, and Fleet topology without issuing model calls,
installs, deployments, probes, training runs, or transfers. The surface test
additionally mounts every launcher named by the reference feature registry and
suppresses the app's background panel sync so it cannot write to that database.
The persistence test saves a Scene and a Saved View, fully quits the packaged
app, relaunches it with the same isolated Application Support directory, and
reopens both.

The write-workflow test never touches the reference database. When Homebrew
PostgreSQL is installed, it creates a disposable cluster under the system
temporary directory and exercises connection writes, commit/rollback,
cancellation, streaming CSV import, rejected rows, and SSH password/key
tunnels. The cluster, keys, connection registry, and native profile are removed
at the end of the run.

## Package

Create an unpacked app for local testing:

```bash
npm run pack:mac
```

Create DMG and ZIP artifacts for the current architecture:

```bash
npm run dist:mac
```

Artifacts are written under `dist/`. Local builds are ad-hoc sealed and pass
strict deep bundle verification. Distribution outside the developer's own Mac
will also require an Apple Developer ID certificate and notarization
credentials.

For a Developer ID signed and notarized build, configure the Apple credentials
described in [Releasing](docs/RELEASING.md), then run:

```bash
npm run dist:mac:release
```

## Design notes

- [Architecture](docs/MAC_ARCHITECTURE.md)
- [Feature parity matrix](docs/PARITY.md)
- [Release process](docs/RELEASING.md)
- [Original product overview](docs/WEB_PRODUCT.md)
