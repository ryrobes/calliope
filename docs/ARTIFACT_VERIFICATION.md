# Artifact verification and tool outcomes

HTML publication receipts include `browser_check` with a runnable helper call
and the published `expected_version`. `browser_page` reuses the persistent
authenticated tab, reloads once if its document is stale, and fails explicitly
if it still has the wrong version. A matching document retains its filters.
This checks the served document rather than inferring its version from its URL.

`rvbbitMaps.diagnostics()` records sanitized Mapbox errors and missing layers
added through the shared map helper. Map style validation details survive;
resource failures disclose only their category. Capture receipts expose these
as `bridge.render_issues` and mark verification `render_failed`, even when all
SQL calls succeeded. Settling or reaching map idle does not clear an error.
Recreating the map starts fresh evidence. Deliberately removed layers do not
count as missing. Raw maps constructed outside `rvbbitMaps.map` are not covered
by this layer tracking. Existing page and console diagnostics still apply.

The Hermes ledger and final chat receipt share `tool_outcomes.py`. They inspect
protocol envelopes, including nested MCP results and desktop job polls, while
leaving arbitrary SQL rows opaque. A successful poll transporting a failed,
cancelled, timed-out or nonzero-exit job is an application failure. The ledger
keeps transport status separately. Existing exported ledgers are not rewritten.

## Release and verification

Deploy `docker/desktop/computer_browser.py` to the computer VM helper before
releasing server receipts that pass `expected_version`. Rebuild the server and
Hermes images; both Dockerfiles install the shared outcome parser. No database
reset or baseline recapture is needed.

Focused tests cover a real Chromium navigation with stale and matching document
versions, the real injected map runtime with deterministic Mapbox error events,
capture health, nested desktop failures, and relative pricing with cache and
context tiers. They do not substitute for a live Mapbox/network smoke check.

```sh
PYTHONPATH=services/calliope:docker/desktop python -m pytest -q \
  services/calliope/test_map_verification.py \
  services/calliope/test_run_ledger.py \
  services/calliope/test_cost_accounting.py \
  docker/desktop/test_computer_browser.py \
  tests/test_benchmark_stream_ledger.py
```

Set `CALLIOPE_COST_TEST_DSN` to a disposable database for accounting SQL
integration tests; never point those tests at a populated lab or production DB.
