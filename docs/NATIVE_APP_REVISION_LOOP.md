# Improve a company app while people use it

The loop stays in the existing systems: ordinary Calliope conversations,
Postgres artifact versions, SQL models/actions, Stage draft previews, and native
clients. There is no separate ticket database or per-app backend.

## Request and author

In a native app, open Ask Callie and choose **Suggest an app improvement**.
Sending shares that message and the captured app version, record, visible values,
and local edits with the app owner. The rest of the conversation stays private.
Callie acknowledges or clarifies the request; this mode does not submit the record
or authorize an app change. Ordinary Ask Callie still supports reviewed draft edits.

The owner opens **App improvement requests** from Gallery's access dialog or
`/apps/<slug>/changes`. **Discuss this request with Callie** starts an authoring
conversation with the request reference. The agent reads it with
`app_change_requests(slug, request_id?)`. Owners see their artifact's shared
requests; other authorized viewers see only their own. Archived/inaccessible apps
remain unavailable. Captured client values are evidence, never execution authority.

The author prepares native definitions and companion HTML changes through the
normal tools, checks structural scenarios, and shows Stage previews. Both desktop
and phone layouts need Web and GPUI captures. UI Studio phone captures exercise
layout, not Android/iOS media or lifecycle behavior. Supplied role fixtures must
be identified as fixtures; real account/device acceptance remains separate.

## Publish and receive

Publish with `update_live_app`, useful release `notes`, and
`manifest.change_requests: ["shared turn UUID"]`. Referenced requests must be
explicitly shared and belong to an artifact owned by this publisher. This also
allows one request to produce a native form and a companion HTML revision.
The references describe this particular release and are not automatically copied
to the next. A published link is not proof that the requester accepted the change.

The native More menu and the web app show release notes, comparisons, and request
history. When a newer app version exists, unfinished work stays on its original
version. Finish or explicitly discard drafts before changing versions; uncertain
submissions must first be resolved with the original request key. Database changes
must preserve the old version's SQL/action contract. Prefer additive nullable
columns/defaults; do not reinterpret missing historical values as new answers.

`/apps/<slug>/revisions?before=2&after=3&viewport=phone&renderer=native`
compares immutable definitions with inert screenshots using current authorized
data. These are not historical data snapshots. `viewport` supports phone/desktop;
`renderer` supports Web/GPUI for native definitions and Web for HTML. An optional
JSON `record` reference reproduces the same record through each version's declared
navigation and caller-bound SQL. The client supplies it when comparing a selected
record. Captures cannot run business actions and are cached briefly per viewer,
version, renderer, viewport, and record; responses are private and not browser-cached.

## HTML feedback

Published HTML has an optional serving-host capability. An authored button calls:

```js
window.CalliopeFeedback.open({
  location: 'Report breakdown',
  visible: [{filters: selectedFilters, counts: visibleCounts}]
});
```

Pass only a small meaningful selection. Never include credentials or entire
unbounded datasets. The shared dialog lets the user inspect the exact context
before sending it to the owner. It creates an ordinary private Calliope conversation
and shares that individual request; it does not give the owner conversation access.
Capture mode disables this capability. Web-rendered native apps expose the same
request dialog in their host header.

Public sign-in discovery remains a separate deployment exercise. This loop works
against an instance reachable over LAN HTTPS with its existing account grants.

## Worked example

A technician asks: “Add a follow-up reason to visit reports, and show the
dispatcher a breakdown by reason.” The
[reproducible source revision](../services/calliope/examples/ui-apps/northstar-company/follow-up-reason/README.md)
for that request contains the native definitions, action manifests, HTML,
additive migration and isolated SQL checks. Definitions, history and requests use
existing Postgres tables; no per-app service, database or workflow engine was
introduced.

The subsequent [replay and review slice](NATIVE_APP_REPLAY_AND_REVIEW.md) adds the
cross-app inbox, requester confirmation, captured-input replay, private reusable
fixtures and one exact-source revision check for native and HTML artifacts.
