# Spatial Stage

The small view icon at the upper left of the Stage switches the current chat
between Stage, Topics, Journey, and Evidence. These are projections of the same
authorized surface ledger. Switching views does not create a turn, invoke a
model, or change an artifact. Layout uses saved metadata; native dashboard
previews at close zoom can make their usual data reads.

- **Topics** starts with clusters. Open a cluster to browse its artifacts; use
  All topics to return. Meaning areas and SQL source tables take precedence,
  followed by recorded questions, artifact families, and broad fallback groups.
  These are deterministic organizational labels, not inferred provenance.
- **Journey** lays out turns chronologically and includes older revisions.
  Cards within a turn follow recorded creation time and ordinal. Horizontal
  distance is not elapsed time.
- **Evidence** shows a selected artifact's directly recorded incoming/outgoing
  references. Select a connected card to continue walking. A missing reference
  means no explicit connection was recorded, not that no relationship exists.

The back arrow beside the view toggle retraces cluster, mode, and Evidence
navigation, restoring the previous camera. With no prior view, it returns to
Stage. Clicking a selected card again clears selection without leaving the view.

Pan the empty canvas by dragging or using arrow keys while the canvas has focus.
The scroll wheel or touch pinch zooms around the pointer;
plus/minus controls and keyboard shortcuts also zoom. Fit / Home shows the full
arrangement. Large collections initially open at a readable zoom near recent
work. New arrivals retain existing slots and the current camera.

At 150% zoom, query and artifact cards gain a closer level of detail: the native
saved-result grid or artifact renderer appears in place. At most three visible
cards mount these previews; zooming below 130% or panning away releases them.
The preview stays passive so scrolling still zooms the canvas. Open enables the
full interactive renderer. Zoom extends to 300%. Query previews use saved rows;
current dashboard previews can make their usual data reads. Historical artifacts
retain the existing exact-version snapshot behavior.

Select a card for its connections. Open (or double-click) mounts the normal
Stage component in the canvas inspector. In Stage returns to its original card.
Saved query grids retain Explore in Data; the live Sketch opens in its existing
workspace. Closing the inspector removes its renderer. The canvas overview uses
bounded saved table samples and existing images/captures, not live dashboards or
newly executed queries. An image used as an artifact preview must match its exact
artifact version.

The Versions control reveals older lineage members in Topics; Journey always
includes them, and Evidence includes referenced older members regardless of that
setting. View/camera choices are local to this browser and chat. The default is
Stage. Artifact selection uses the existing selected-surface context.

## Data and boundaries

`stage-canvas.js` constructs only edges whose endpoints are in the currently
loaded authorized chat: parent revision, explicit derived query, answer citation,
board membership, explicit image source, and exact artifact-version capture.
Shared tables affect grouping but never become evidence edges. A citation records
that something was cited, not that it proves a claim. Cross-chat references are
not expanded by this first version.

The surface serializer adds a small `spatial` descriptor using the existing
bounded, cached SQL table/column extractor. No new database tables, migrations,
background jobs, provider requests, or generated classifications are required.
The renderer and camera live in a separate module; full Stage components retain
their existing authorization, actions, hydration, and lifecycle.

Validation covers graph scope and reference kinds, exact-version captures,
stable arrivals, older revisions, responsive camera controls, native grid/Data
integration without re-execution, empty chats, safe title rendering, and the
existing Stage reconciliation and packing behavior.

## Search this Stage

The reserved top rail contains view/back controls, one search field, and an
artifact-type filter. It stays above the scrolling Stage and every canvas;
content cannot overlap it. This search is scoped to the current authorized chat,
separate from company-wide evidence discovery.

Typing filters saved titles, questions, short descriptions, authors, source
names, column names, and query SQL immediately. Press Enter or the search icon
to also find semantically related work through the existing Clover/Jev decision
operator. Type filters combine with either search. The status distinguishes
text-only results, related matches, partial coverage, and provider failure.
Clear or Escape removes both filters. Changing chats clears them; switching
Stage views retains them.

Search includes older revisions and normally folded items. Chronological Stage
shows native matching cards. Topics shows matching individual cards across all
topics; Journey groups them by turn. Evidence shows matching cards with their
recorded links, retaining other matches alongside the selected neighborhood.
Filtering never invents provenance edges. Connections outside the filter become
available again when it is cleared. Unfiltered camera positions are retained.

Semantic search is an explicit, read-only inference request, not a chat turn or
SQL-generation operation. The server rechecks shared-chat access, applies the
same suppression and separate Playbook permissions as normal Stage reads, then
sends bounded metadata to `rvbbit.clover_decide`. Result rows, HTML, full documents,
and arbitrary tool arguments are excluded. The normal operator/provider ledger
accounts for inference under the current viewer. No migration, new index, or
background job is needed. Native artifact renderers keep their usual data reads.

The hosted contract allows eight questions and 32 KiB per decision; requests
are split within both bounds, with three concurrent calls across searches.
At most 512 newest visible items receive semantic judgments, with a 20-second
batch-start deadline and 12-second SQL timeout. All loaded items still participate
in local text filtering; partial semantic coverage is disclosed. Successful
judgments are cached for three minutes against viewer, chat, query, and metadata,
never as an authorization substitute. Late responses cannot cross chats or
repopulate a cleared search. New or changed work is text-filtered immediately;
press Enter again to refresh its semantic judgments.
