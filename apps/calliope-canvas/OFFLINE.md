# Native drafts and camera evidence

Android and desktop PublishedApps share a small recovery journal. It is not a
local SQL replica: app definitions, queries, decisions and committed records
remain on the Calliope server. Web behavior is unchanged.

## Working offline

Open an app while connected once. Its last rendered graph, query results,
editable state and attachment labels are cached under that server/account.
Edits are saved on a serial worker using private files, fsync and atomic rename.
The status line distinguishes saving, saved on device, and a disk error. An
acknowledged local save survives process death. Uninstalling or clearing app
storage deletes these drafts.

After restarting offline, Android uses the saved session identity and cached
appearance/fonts to open previously visited apps. Authentication denials do not
trigger the offline fallback. A new device needs an initial online sign-in.
The native app's data directory contains `app-drafts/<account>/apps/<slug>.json`;
there is no SQLite database, Python service, or synchronization of SQL tables.
Android content uses the app sandbox/device storage protection; only the login
session is additionally encrypted with Android Keystore. Desktop draft files
and directories use user-only permissions.

Editable forms now retain separate drafts by **app version, form path, and record
key**. Switching A → B → A restores A's latest edits and staged attachments.
**Drafts** lists unfinished records by their label; opening one returns to its cached view.
Cached apps without edits are not labeled as drafts. **Discard** first asks for
confirmation, then clears only that record/form's edits. Back or Keep editing
cancels the confirmation. An older Back entry cannot
resurrect discarded or successfully submitted edits. A pending submission still
freezes editing/navigation within that app until its outcome is resolved: there
is **one pending submission per app**, not a queue of independent reports.

Back restores the prior page/record, filters and scroll positions before leaving
the app. Both the toolbar and Android Back use this history, which survives a
restart. Open renderer modals and the Drafts list close first. Previously visited
records can also be selected offline when their declarative navigation event
matches a cached view. New views, fresh SQL and decisions still need a connection.
The cache retains 24 recent clean views plus views needed by unfinished drafts;
Back retains 24 entries. Native charts still require server rasterization on a
fresh open; chart images are not persisted as part of the offline view cache.

Read recovery retries after 5, 10, 20, 40, then at most every 60 seconds. The
visible timestamp records the last server response, not a local autosave.
Backgrounding Android pauses SQL polling; resuming schedules a read refresh.
Leaving the desktop Apps surface or opening Android Account also pauses polling.
Authentication/permission and contract errors stop automatic retries rather than
being treated as offline. Reconnecting with a pending submission checks server
access but never sends the pending command automatically.

## Submitting and recovering

Press the app's normal Save/Submit control. Native clients validate rendered,
editable typed fields and focus the first invalid field before creating a pending
submission. Inline errors clear as values are corrected. Server validation remains
authoritative; client validation does not authorize a write. Before network access, the journal
records the complete versioned state, event, context and a UUID request key.
Attachments use separate persistent upload keys. Upload results are journaled
before the business request, and local handles are replaced with authorized
server file IDs. The server's existing idempotent action contract remains the
authority for writes.

If the connection drops, **Send pending** retries the same operation explicitly.
It never creates a new request key. Editing that pending operation is paused;
leaving the app or restarting does not discard it. Reconnecting alone does not
submit business changes. A successful response is written to the journal before
the UI reports success. Server errors that explicitly guarantee rollback unlock
the draft for correction; unknown outcomes retain the request for recovery.
An external action with an unknown outcome still needs operator inspection.

Snapshots expose `commands` (events that invoke named server actions),
`forms` (form/source paths and record identity keys), action-scoped `resets`, and
navigation identity paths. Native reads send bounded `drafts` hints for the loaded
version. The server selects the matching record draft during form hydration,
before dependent SQL/decisions run. Hints never persist business records.
Omitting hints keeps the existing web behavior. Explicit resets win over hints. Read-only navigation is not queued
as a business submission. Refresh removes reserved `local:<UUID>` attachment
handles from SQL inputs and restores them only to the same record's draft.
This also keeps local evidence intact when changing pages without changing the
selected record. SQL sees only uploaded evidence.

## Camera and local files

Android's Take photo button uses the installed camera and a narrow FileProvider
grant. Confirmed images are normalized to upright JPEG, at most 2048 pixels on
the longest edge, without EXIF/GPS metadata. Cancel keeps the prior draft.
A persisted capture target and completion marker recover a confirmed shot even
if Android kills Calliope while the camera is open. Preview/unfinished captures
are never silently submitted. Camera normalization finishes before the photo is
acknowledged as attached.

The existing system picker also stages files durably: PNG, JPEG, PDF or UTF-8
text, up to 5 MB each and five per attachment component. Local evidence can be
opened through the device viewer without a connection. Upload happens when an
explicit server action needs that evidence. Successfully acknowledged actions
reclaim local blobs no longer referenced by the draft. Abandoned or removed
files may remain until a later successful action or app-data cleanup; no automatic
retention policy for abandoned drafts/server uploads is imposed in this experiment.

## Verification

`cargo test --locked --bin calliope-canvas` includes restart/frozen-request,
account isolation, corrupt journal, camera completion, record-safe refresh, and
lost-response replay cases. The lost-response test uses an actual loopback HTTP
server and verifies one file upload and identical business requests on retry.
Physical-device checks should disable only the test ADB reverse mapping, restart
the client to close existing HTTP connections, edit a cached draft, restart,
submit offline, restart again, reconnect, then explicitly Send pending. Restore
the reverse mapping in a `finally` block. Never use airplane mode or alter the
user's global networking for this test.

## Published app updates

Cached workspaces now stay on their saved version even when clean. The shell
shows the loaded version and a **Use vN** action for a newer release. Unsaved
record drafts, local evidence/camera work, storage errors and pending requests
block switching. Finish or explicitly discard them first. Updating fetches fresh
state; it does not migrate a version-one draft into version two. Unsupported
catalogs show an explanation and browser action, retaining an existing workspace.

The web host also retains version-scoped form drafts and pending action bodies
in its existing IndexedDB store. It requires an authorized server read before
restoring after reload; it is not an offline browser application. Pending
submissions lock further actions until an explicit retry is resolved. A second
tab cannot silently overwrite a newer journal. Native offline startup and local
camera/file recovery continue to use the existing private device journal.
