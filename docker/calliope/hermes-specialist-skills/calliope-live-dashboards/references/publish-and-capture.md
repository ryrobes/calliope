# Live dashboard technical reference

## SQL and grain

- Prefer operational relations that can support the requested filters and drilldowns; a similarly named tiny sample table is not a substitute.
- Prove every named query with `run_sql_multi` before writing HTML.
- Keep each data concern as a flat query. Do not combine unrelated payloads with `json_build_object`; that hides query lineage and accelerator eligibility.
- Pre-aggregate each many-side fact before joining. Joining two fact tables directly through a shared dimension can multiply counts and sums.
- Group and sort the same expression. If a native-engine `GROUP BY` rejects an ordering expression, project the normalized value in a subquery and group/order its alias.
- Prefer `cast(x as type)` over `::type`, and avoid reserved words as bare aliases.

## Adaptive presentation

- Use local CSS aliases with fallbacks such as `--page: var(--artifact-bg, #0b1218)`; do not assign runtime `--artifact-*` variables on `:root`.
- Read runtime chart/text colors from `getComputedStyle(document.documentElement)` and redraw charts on `rvbbit:adaptive-theme`.
- Keep the masthead compact: title, decision question, freshness, and active period. Put the primary signal in the first viewport.
- Prefer bars and lines for comparison and time. Avoid decorative charts that obscure exact values.
- Do not autofocus an input on every render. Restore selection or caret only after user interaction.

## Bridge and row limits

- Use the template's dual bridge: Cowork `callMcpTool` in the Stage and host-injected `rvbbitQuery` when published.
- The default is 1,000 rows. Put complete-population KPIs and trends in separate aggregate queries.
- For an intentional detail result, set a per-part limit:

```javascript
const PARTS = {
  summary: {sql: `select ...`, shape: 'row'},
  detail: {sql: `select ...`, shape: 'list', limit: 2500},
};
```

- The host records `requested_limit`, `applied_limit`, and `truncated`. If the applied limit is lower or the result is truncated, label the grid honestly or narrow it with pagination/filtering.

## Upload integrity

- Keep the working HTML in `/opt/data`, Hermes' existing writable scratch volume. Validate it with `calliope-web-check <path>`, then call `upload_artifact(source_path=<the same /opt/data path>)` once and publish with the returned handle. Warehouse sees the same volume read-only; do not copy the file to a second export directory.
- Warehouse resolves the path inside its read-only allowlist, reads the complete file, and returns its bytes and SHA-256. Do not read the completed file back into context, split it into arbitrary chunks, or manually reconstruct it in MCP arguments.
- Treat the local file as an implementation detail, not the finished artifact. Do not stop or respond with a filesystem path after validation; completion requires the publish/update result and its live URL.
- If publish dependency metadata reports no queries when the source contains live queries, treat that as an incomplete upload and replace it with the verified full artifact.
- Prefer targeted patches over regenerating the entire artifact. `calliope-web-check` parses HTML, embedded JavaScript/CSS, and runs browser-aware JavaScript linting without printing the whole source.
- For a published artifact the patch path is server-side: `get_live_app(slug, part='html')` then `update_live_app(slug, edits=[{"find", "replace"}])`. `find` is matched exactly, must be unique, and a miss is a typed error (`EDIT_NOT_FOUND` / `EDIT_AMBIGUOUS`) that changes nothing. Whole-document `html` / `source_artifact_id` updates are for restructuring; their result carries `advisories` (`SECTIONS_REMOVED`, `DOCUMENT_SHRANK`) when the new version lost headings, ids, or a fifth of its bytes.

## Capture and verification

- Capture the full page after publish (`capture_live_app(width=1200, height=800, full_page=true, return_image=false)`) so below-the-fold panels are checked too; do not return or re-analyze the same image through multiple vision paths.
- Confirm expected query count, zero query failures, no actionable page errors, and a rendered decision surface.
- Exercise at least one interactive filter or drilldown. Verify related detail follows the selected business key and has an honest empty state.
- Assign an existing artifact Area when appropriate. New artifacts remain private until explicitly shared.

## Browser address and identity

The hosted appliance makes its configured loopback public URL reachable from the
local Hermes browser as well. browser_exec does not inherit the human's login.
Use capture_live_app for authenticated inspection of private apps. If browser_exec
lands on a login page or chrome-error://chromewebdata/, navigation did not reach
the app: do not interpret undefined rvbbitMaps/rvbbitQuery as missing runtime APIs.
Confirm the final page URL before probing window globals.

## Interaction acceptance evidence

A screenshot proves appearance, not linked filtering. For each requested filter,
use a selection known to produce a different nonempty subset: observe actual row
IDs/counts for the map and every promised linked panel, select, verify the expected
subset, clear, and verify restoration. Test at least two facets together. Keep
source-specific filters from changing the other source; state and test which
reference totals/denominators intentionally stay fixed. Preserve filter dimensions
in the query grain (never use max(dimension) to label a mixed group).

`calliope-interaction-check /opt/data/workspace/check-adapter.cjs` runs an authored
Node adapter and emits structured JSON. The adapter exports `artifact:{slug,version}`,
`basis:'live-data'|'offline-fixture'`, and `cases`. Each case supplies `id`,
`observation` (which data is measured), `affected` and `unchanged` panel names,
async `clear`, `select`, `settle`, `snapshot` functions, and
`verify(baseline,selected)` returning true only for the expected rows/counts.
`snapshot` returns a JSON object keyed by panel. Await real render/query completion
in `settle`; observing CSS/opacity is not data verification. Save its stdout as
`/opt/data/workspace/interaction-checks.json`. Re-run against the final published
version. Offline fixtures are useful logic tests but do not certify live data or
browser wiring. If live interactions were not tested, say so in the handoff.

For saved media use `read_calliope_image(reference=...)` or give the same managed
reference to `vision_analyze`: generated-image URLs, managed capture paths, and
`/home/kasm-user/Shared/<conversation UUID>/<image>` are resolved by Calliope.
Use `look_at_desktop` for the current screen; do not regenerate a mock or ask for
login because another container cannot read a saved image path.
