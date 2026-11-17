# Floor browsing at scale

The Floor keeps complete counts separate from downloaded cards. The authenticated
`GET /api/calliope/floor/pieces` endpoint supplies up to 30 cards per lane by
default, full lane totals, matched totals, filter facets, and opaque continuation
cursors. Each lane has an independent budget. Done, Noticed and Archived sort by
most recent activity; active lanes retain attention priority. Item ID breaks
sort ties. No work is deleted to keep the board small.

Global search, lane searches, person, kind and freshness filters run against the
complete authorized projection before the page limit. Matching covers stored
piece titles, summaries, state reasons, action prompts, kinds and participants;
it is not a full-transcript search. Recently generated display titles/statuses
shown to the viewer are also matched through a bounded, viewer-scoped, one-hour
presentation cache. The cache is optional presentation state; stored work remains
searchable without it. Conversation discovery no longer preselects only the
latest 600 conversations before matching.

The existing Floor metadata endpoint returns the first batches with complete
counts. The browser expands the existing “more” affordance into continuous lane
scrolling, fetching additional batches near the end. Variable-height windows
mount only nearby cards. List view uses the same bounded rendering and transport.
Search is debounced and cancels outstanding requests; revision checks reject late
responses even when cancellation arrives too late. Failed batch requests keep
already loaded work and expose a retry action. Periodic live refresh is deferred
while an expanded lane is being browsed, so it does not reset a long scroll;
explicit Refresh remains available.

`as_of` selects a historical frame. Counts, filters and pages are read in the
same transaction under `rvbbit.as_of_timestamp`; current schedules and generated
presentation labels are excluded from that projection. Cursors bind to the
viewer, timestamp and filter scope. The existing `/floor/frame` route delegates
to the same query path. Live cursor walks are not frozen database snapshots:
new/reordered items appear on refresh, and the client suppresses duplicate IDs.
Historical walks remain tied to the selected AS-OF timestamp.

Authorization precedes search, counts and facets. Existing private in-motion
silhouettes retain their separate bounded, hashed, title-free projection and are
never searchable as conversation content. They are excluded from history.

This is a service/API/asset change and requires no database migration or new
index. `floor_data.py` is included in the server image; `floor-stream.js` is a
versioned authenticated Floor asset. It has not been deployed by this repo change.

Validation uses an isolated PostgreSQL fixture with 2,600 pieces, including an
old unique match and another viewer's private work. It checks full counts,
complete cursor traversal, filters, permission boundaries and temporal scope.
Chromium runs the real page assets against that fixture, exercising continuous
scrolling, bounded DOM size, focus/scroll retention, search, history and List view.
A Node test separately exercises delayed responses, cancellation and retry.

```sh
FLOOR_TEST_DSN=postgresql://... PYTHONPATH=services/calliope \
  python -m pytest services/calliope/test_floor_data.py \
    services/calliope/test_floor_browser.py \
    services/calliope/test_calliope.py -k floor
node services/calliope/test_floor_stream.mjs
```

The PostgreSQL fixture test measured approximately 20 ms for its initial complete
count-and-page query. This is fixture evidence, not a production work-graph
benchmark. Browser tests require Playwright and `/usr/bin/chromium`.
