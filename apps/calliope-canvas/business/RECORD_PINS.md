# Portable records and live Home pins

Published apps declare a read-only record summary and how to reopen its record.
**Pin to Home** stores a personal reference in Calliope's Postgres. Desktop shows
it on the movable/resizable canvas; Android shows the same pins above app tiles.
**Open record** selects that record; **Ask Callie** adds the contextual conversation.
The Field notebook demo supplies status, appointment, parts and next step from
its existing `job` SQL binding. There are no HVAC handlers or another database.

## Declaration

Add a `records` map to `calliope.app.v1`:

```json
{"records":{"visit":{
  "id":{"path":"/job/id"},
  "title":{"path":"/job/customer"},
  "when":{"/state/page":"field"},
  "open":{"/state/page":"field","/state/selectedId":{"recordId":true}},
  "summary":{"queries":["job"],"components":[
    {"id":"root","component":"CText","text":{"path":"/job/status"}}
  ]}
}}}
```

`open` and `when` address declared `navigation.paths`, outside editable forms.
`id` binds a server query result. Summaries use an ordinary flat tree rooted at
`root`, with up to 40 read-only presentation nodes: `CStack`, `CSurface`, `CText`,
`CBadge`, `CMeter`, and `CSection`, subject to the catalog. Only the declared SQL
queries and their dependency closure execute. Responses contain resolved summary
components, metadata and query receipts, without hidden datasets or draft values.

## Identity and storage

References are `{slug,record,id}`. The authenticated connection supplies the
instance/account. No SQL, credentials, URLs, drafts or copied UI go in a reference.
String and integer IDs stay distinct. Keep record names and key types stable
across versions; use a new record name if its meaning changes.

- `GET /api/calliope/app-pins` lists the caller's references.
- `POST /api/calliope/app-pins` accepts `{operation:"pin",reference}` or
  `{operation:"remove",id}`. Pinning is deduplicated across devices.
- `POST /api/apps/{slug}/record-summary` takes a reference and resolves the latest
  published summary under normal artifact/SQL permissions.
- `POST /api/apps/{slug}/ui` takes `{record:reference}` to reopen it through the
  published contract. This cannot also supply state or execute an event.
- **Copy record link** creates `/apps/<slug>#record=<encoded reference>` for web.
  New contextual chat turns retain a clickable captured-record chip.

`rvbbit.ui_record_pins` stores at most 32 personal pins per actor. Mutations never
replace the whole collection. Devices cannot overwrite each other's pin lists.
Refresh polls every 15 seconds with at most four concurrent summaries, using
existing SQL timeouts/cache policies. Local action receipts request a refresh.

Summaries follow the latest published version. Clean record opens do too;
unfinished drafts and pending submissions retain their original version.
Navigation preserves per-record drafts and cannot bypass a pending submission.
Old chat suggestions retain their original version/review checks.

Desktop placement geometry is device-local. A private, account-scoped JSON cache
holds last-read summaries for offline display and is labelled cached. It is
optional/disposable, not a SQL replica. Authoritative missing/deleted/denied
responses remove the cached summary; the pin remains removable. Network failures
retain the last successful summary. Previously visited records can reopen offline;
new records cannot. Pin mutations need server acknowledgement and are not queued.

This slice exposes one declared record's Pin button per page. Arbitrary subtree
extraction, editable forms on Home, and OS app-link handlers are outside this
slice. Shared links open the web fallback; in-app chips/pins navigate natively.
Existing stage/board pins keep their existing storage.

## Verification

Native probe operations: `pins`, `pin_record`, `remove_pin` (id), `refresh_pins`,
and `open_record` (reference, optional `ask`). They use the real host pathways.
`check_record_pins.py OUTPUT` uses an isolated display/profile, verifies A→B→A
draft preservation and Callie context, and captures the app collection/canvas.
It creates a personal Harbor Dental pin and removes it afterward unless it was
already present or `--keep-pin` was supplied. It submits no business form.

Backend tests cover projection/dependencies, bad identities, missing records,
unavailable SQL, removed contracts, and authorization before querying. The live
Postgres check used a temporary fictional visit and the existing typed status
action, verified updated summaries/deduplication/owner isolation/deletion, and
removed its temporary record/pin. Device and private-display evidence are kept
outside the repository. Harbor Dental is retained for trying the feature.
