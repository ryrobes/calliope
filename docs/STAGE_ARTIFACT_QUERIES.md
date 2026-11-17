# Stage artifact query drill-down

Published dashboard/app cards offer **Unwrap**, from Stage or a canvas selection.
This opens a bounded, pan/zoom view of definitions belonging to that exact artifact
version. Back restores the previous Stage/canvas context and camera. Evidence shows
the full connected history, including earlier versions, by default. Selecting a
card leaves that history in place; **Follow connections** recenters around it.
Clicking empty canvas clears the selection; dragging still pans the view.

The Stage rail's history scrubber shows saved work up to a recorded timestamp.
Stops group simultaneous records and are spaced by saved changes, rather than
elapsed time. The cutoff applies before latest-version resolution, graph edges,
and text/related-search results, across Stage and all canvas modes. Canvas slots
and cameras survive scrubbing; new arrivals do not advance a historical cutoff.
**Now** restores the live view, and switching chats also returns to Now. On narrow
panels the clock opens the slider; arrow keys step through stops, Home goes to
the first stop, and End returns to Now.

This is saved-work history, not event-sourced replay: surface metadata may have
been updated in place. Historical Stage dashboards use version-bound snapshots;
canvas native previews stay unloaded while viewing history. Opening a saved
artifact or unwrapping its queries can still read current underlying data.

## Definitions and provenance

- **Observed in this version**: execution descriptors retained by the publication
  enrichment capture in `rvbbit.artifact_query_captures`, keyed by dashboard/version.
  Up to 200 calls retain SQL (50,000 characters, with explicit truncation flag),
  columns, row count, engine, timing and `as_of`. Result rows and error bodies are
  not retained or exposed. Observations describe the captured page state, not every
  possible interaction; repeated SQL plus data-time context is collapsed in the UI.
- **Defined in saved source**: valid SQL literals extracted from the immutable HTML
  and source files. This also supports older versions without retained captures.
  Interpolated/unresolved definitions are omitted.
- **Reconstructed calculation**: authored or verified enrichment definitions,
  rendered with their recorded parameter defaults. These are labeled separately
  from queries actually executed by the dashboard. A calculation links to captured
  inputs only when its enrichment capture ID matches the retained capture.

Slug-wide runtime/dependency logs are not used as exact-version evidence. Definition
identity includes the SQL fingerprint; a changed enrichment definition cannot be
silently substituted for a previously selected one.

## Exploration

`GET /api/calliope/data/surfaces/{surface_id}/contents` checks both chat and artifact
view access and returns metadata. Both artifact nodes and their screenshot nodes
resolve an exact version. Missing inventories enqueue a bounded, deduplicated browser
capture (one worker, at most 32 queued versions; each capture starts at most 64
queries within a 30 second discovery window). It executes the saved page's queries
with the artifact owner's identity, retaining only descriptors. It does not run model
enrichment, replace the semantic overlay, or overwrite the original screenshot.
The canvas polls while discovery is in progress. Recorded defaults are usable immediately.

Visible cards fetch `POST .../queries/{entry_id}/preview` two at a time; the server
also limits concurrent preview executions to two. Each preview
rechecks chat, artifact and source access; validates read-only SQL; and runs as the
viewer, with a 10 second SQL timeout, 200 row and 2 MiB response limits. Results are
cached for five minutes, keyed by viewer, execution identity, exact version and query
fingerprint/context. The process cache is capped at 128 entries / 32 MiB. Previews
create no private drafts, Stage surfaces or chat turns. Leaving cancels browser work;
already executing SQL remains bounded by its timeout.

Scalar calculations returning exactly one row render value tiles using recorded
format/unit and parameter scope. Multidimensional results stay grids. Double-click
opens the interactive grid/value; Back or right-click on the canvas restores the previous camera and selection.
Refresh/Retry reruns a preview. Definition version and execution time are separate;
`as_of`, when recorded, is preserved. Otherwise results use current data.

`POST .../queries/{entry_id}/explore` explicitly creates a private Data draft. A valid
server-issued preview token copies that viewer's cached result, after rechecking
access. Expired/missing tokens cause a fresh execution. Client-supplied rows are never
accepted. Failed past captures can be retried; incomplete SQL and unsupported functions
are labeled with a reason and cannot execute. Standard PostgreSQL `EXTRACT` is supported.

Canvas dashboard nodes render exact saved versions when opened or zoomed in; Stage
continues to use historical screenshots. Screenshot nodes have a separate tint/dashed
border and **Open source version** / **Unwrap source** actions.

**Add to Stage** is the existing explicit publication action. Its new query surface
retains parent surface/session, artifact slug/version, definition ID, SQL fingerprint
and parameter/data-time context in `source.derived_from`. Internal query cards are
never synthetic Stage surfaces or additions to the agent transcript.

Existing artifacts use retained source/enrichment metadata immediately. The next
normal enrichment capture also retains executed query descriptors. A fresh capture
of an older version must not be presented as its original publication-time capture.
