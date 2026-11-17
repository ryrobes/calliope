# Reproduce, check and close an app improvement

The review loop uses shared Calliope turns, immutable artifact versions and
Postgres. Native clients keep their existing improvement links; no mobile binary
update is required for this server-side slice. iOS hardware work is deferred
until a physical device is available.

## Inbox and confirmation

Open `/apps/improvements` from Gallery, or `/apps/<slug>/changes` from a native
app's More menu. Owners see requests explicitly shared with their apps; other
viewers see only their own requests. The remainder of each conversation stays
private. The Gallery link shows the number requiring attention in the recent
inbox. The all-app view shows the latest 200 requests; per-app history shows 50.

Owners can acknowledge a request, mark it in progress or defer it. Publication
still comes from `manifest.change_requests` on artifact versions. Only the
requester can confirm a linked release as fixed or report that it is still
happening. Confirmation references the release set: another linked publication
needs a fresh confirmation. Concurrent review writes use a revision check;
stale edits return a conflict. This is a review state attached to the original
turn, not a separate ticket/conversation system.

## Replay and reusable cases

The report's original definition is combined with captured navigation, visible
bindings, form identity, saved field values and local edits. Replay runs no SQL,
does not impersonate the requester and cannot submit a business action.

Existing reports did not capture complete models. Their previews explicitly say
**Partial replay**: uncaptured values retain definition defaults, while removed
or unrecognized fields are listed as unmapped. An empty queue in such a replay
does not prove the original queue was empty. These are reconstructed observations,
not full historical snapshots or pixel-perfect recordings.

**Save report as test case** creates a private, immutable fixture and assertions
for the captured form values. Saved cases can be reused against later versions;
their previous evidence remains accessible after reloading. Cases and results
belong to their creator and require continued app/request access. Releasing an
app does not expose its author's fixtures or test data to every app viewer.

Callie can also provide a complete, explicitly labelled fixture:

```text
app_improvements()
app_change_requests(slug, request_id)
save_app_scenario(slug, name, request_id=...)
save_app_scenario(slug, name, version=3, data=COMPLETE_MODEL, checks=[...])
app_preview_scenarios(slug)
check_app_revision(slug, version=3, scenario_id=...)
check_app_revision(slug, scenario_id=..., document=CANDIDATE_DEFINITION)
```

Checks accept one of:

```json
{"path":"/state/draft/notes","equals":"Unsent notes"}
{"component":"visit-title","property":"text","equals":"Maple Studio"}
{"event":"save","context":{},"equals":{"notes":"Unsent notes"}}
```

Event checks compare resolved action inputs without submitting the action or
clearing a draft. Additive candidate fields retain candidate defaults; removed
model paths are reported and prevent a passing check. Assertions can catch a
binding/label regression independently of whether the renderer becomes ready.

## One revision check

Native checks use one exact definition and one resolved data snapshot for Web
and GPUI phone/desktop captures. Evidence records source/data hashes, renderer
builds, readiness, assertion results and failures. `captures=false` performs
structural checks only and records that limitation. Callie still needs to inspect
the images: readiness is not a visual-quality judgment or device acceptance.

HTML checks use the existing isolated capture host:

```text
check_app_revision(slug="northstar-analysis", version=2)
check_app_revision(slug="northstar-analysis", html_source=EXACT_CANDIDATE_HTML)
```

This supports the caller-bound company-app read bridge with business actions
disabled. It does not open or repair the user's computer/browser. HTML feedback
remains authored context; native model fixtures are not treated as replayable
HTML state. HTML and native checks without a scenario use **current authorized
reads**, explicitly distinguished from captured request data.

Receipts remain in `rvbbit.app_preview_checks`; fixtures are in
`rvbbit.app_preview_scenarios`. Screenshot files are private cache artifacts kept
for seven days, then regenerated on demand. Published definitions remain in the
existing artifact registry. Source hashes allow the exact checked definition to
be compared with publication; publication is not automatically gated by a check.

## Boundaries

Still separate: solution-level dependency/migration packaging, public mobile
sign-in acceptance, physical iPhone testing and signed store distribution.
