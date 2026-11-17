# Optional RVBBIT TanStack runtimes

`rvbbit-tanstack-charts-0.16.2.js` is a pinned, self-contained browser bundle.
It exposes `window.RVBBIT_CHARTS` for artifacts that explicitly opt in. The
default Warehouse dashboard template continues to use Chart.js and does not
load this file.

The bundle contains TanStack Charts, the D3 scales/curves used by the starter,
and a thin `mountRvbbitChart()` adapter. The adapter leaves native TanStack
definitions intact while adding stable DOM metadata and selection events for
Artifact Lens. See `../examples/tanstack-charts-dashboard.html` for a complete
framework-free example.

Additional independent browser bundles expose the native headless cores. Load only
what an artifact needs, using ordinary script tags:

```html
<script src="/charts/rvbbit-tanstack-charts-0.16.2.js"></script>
<script src="/charts/rvbbit-tanstack-table-core-9.2.4.js"></script>
<script src="/charts/rvbbit-tanstack-virtual-core-3.17.10.js"></script>
```

| Package | Browser global | Version |
| --- | --- | --- |
| Charts | `RVBBIT_CHARTS` | 0.16.2 |
| Table Core | `RVBBIT_TABLE` | 9.2.4 |
| Virtual Core | `RVBBIT_VIRTUAL` | 3.17.10 |

These are self-contained bundles with no React dependency or artifact-side build.
Table and Virtual expose their package root exports plus `version`; Table also
exposes the native `storeReactivityBindings()` export for vanilla JS table
construction. They do not supply styled components. Table uses the **v9** API (`constructTable`, feature
registration), not v8's `createTable` API. Charts 0.16 places positional scales
under `defineChart({ scales: { x: ..., y: ... }, ... })`.

The legacy Charts 0.3.1 asset remains available unchanged for existing artifacts.
Capture/PDF rendering inlines each requested bundle at its exact authored version.
`tanstack_chart_template` advertises the optional core paths and browser globals.

Rebuild all three bundles after changing `charts-runtime`:

```sh
cd services/calliope/charts-runtime
npm ci
npm run typecheck
npm run build
```

The filename is versioned intentionally. Existing artifact versions must keep
rendering against the runtime they were authored with.

