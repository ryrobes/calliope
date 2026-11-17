# Reactive semantic resources

Open **09 Adaptive** in the business desk (or `/?view=adaptive`). The example reads
six fictional HVAC visits from SQLite. Selecting a visit or editing its working
notes changes the evidence watched by a resource. After 450 ms without another
change, the host calls Lab's `rvbbit.decision_tree`. The result changes a heading,
panel tone, and child composition; working notes and handoff remain local drafts.
This is a workflow suggestion, never a technical diagnosis or stock authorization.

The runtime lives in `src/business_reactive.rs` and `web/reactive.js`, above both
renderers. The A2UI catalog and GPU paint loop perform no inference. Unrelated
model updates do not trigger calls. There is at most one call in flight per
resource; newer evidence invalidates an older response immediately, then runs
after that call finishes. The last composition stays visible during refresh or
failure. A delayed activity glyph appears after 250 ms. No automatic error retry
loop. **Reconsider** explicitly retries; **Hold / resume** pins the current result.
Stable component IDs and data paths retain input state. Removed input values remain
in the model, but removing the focused control can still move focus: keep primary
editors outside changing regions, reuse IDs for equivalent controls, or hold the
workspace while editing. This experiment does not claim mobile lifecycle support.

## Definition

Add `reactive` to a Studio `{components,data}` document. Example:

```json
{
  "id": "next",
  "watch": {"job": "/job", "notes": "/draft/notes"},
  "debounceMs": 450,
  "tree": {"kind":"branch","question":"Does this describe a return visit?",
    "yes":{"kind":"leaf","label":"comeback"},
    "no":{"kind":"leaf","label":"clarify"}},
  "initial": "clarify",
  "target": "next-slot",
  "variants": {
    "clarify": {"value":{"heading":"A little more context."}},
    "comeback": {
      "value":{"heading":"Pick up the whole story."},
      "patches":{"next-panel":{"tone":"warm"}},
      "root":"next.body",
      "components":[{"id":"next.body","component":"CText","text":"Review the prior visit."}]
    }
  }
}
```

Bind labels to `/resources/next/value/heading`; bind a status label to
`/resources/next/message`. `target` owns an initially empty `CStack`. Fragment IDs
must start with `next.`. `patches` change presentation props; identity, input
bindings, actions, and child links use their explicit contracts instead. A
value-only resource needs no target. Output values can bind any supported data
property, including labels, descriptions, booleans, and picker collections where
the catalog supports bindings. Literal design tokens use property patches.

All variants, their graphs, and bindings are checked before rendering. A document
has at most four independent resources; watches cannot reference `/resources`
(no implicit feedback loops). Each policy has at most four sequential decisions,
63 nodes, eight choices per choice node, 32 KB of policy and 24 KB of evidence.
Every leaf must have a variant. Authors can use normal A2UI components, not only
prebuilt workflow templates. Policy JSON is inline in this version; versioned
policy registry references and composed resource dependencies are future work.

`/resources/<id>` exposes `label`, `value`, `status`, `message`, and the last
`receipt` (model, policy hash, decision path, elapsed time). During pending/error
states the receipt describes the previous evaluation, not current evidence.
Scores in the SQL receipt are not calibrated probabilities. An explicit `clarify`
leaf is useful for insufficient evidence; unavailable/budget-exhausted responses
retain the prior view and show an error rather than choosing a success branch.

## Studio

`calliope-ui-studio example adaptive /tmp/adaptive.json` exports the full policy
and all variants. `check` validates each outcome. `load` runs it in native mode;
`fill adaptive-notes '"…"'` exercises the real watcher. Structural mode checks
bindings and forced compositions, and does not run asynchronous inference.

`decision next comeback` forces and holds a declared variant. Its receipt says
`studio-forced`, so a forced branch cannot masquerade as a Jev result. This command
is available only through the opt-in Studio probe, not the ordinary client or
HTTP decision endpoint. A scenario can include:

```json
[
  {"op":"fill","id":"next.handoff","value":"Retain this handoff"},
  {"op":"decision","id":"next","value":"parts"},
  {"op":"assert","id":"adaptive-result","equals":"Bring the right part."},
  {"op":"assert","id":"next.handoff","property":"value","equals":"Retain this handoff"}
]
```

Native `inspect` includes reactive status and receipts. Screenshot readiness waits
for decisions as well as charts. Review error/held status separately: a completed
fallback screenshot is not evidence that live inference succeeded.

## Transport and Lab installation

The local Python host proxies only `{state,tree,max_steps}` to the fixed authenticated
`POST /api/calliope/ui/decision` route. By default it reuses the desktop's existing
`~/.config/calliope-canvas/connection.json` session, never exposes it to a definition
or browser, and refuses redirects. An operator may set `CALLIOPE_DECISION_CONFIG`
to a private `{url,token}` file, or install the same object at
`~/.config/calliope-canvas/decision.json` (used before session-cookie fallback). UI Studio uses `CALLIOPE_DECISION_URL` plus its
existing service token. The server checks that token's SHA-256 from
`/app/ui-decision-token.sha256` (or configured file/env). The bearer grants only
this bounded decision endpoint; it is not a SQL credential. Browser Origins are
rejected there; browsers use their same-origin local host.

The route executes one parameterized SQL function call with an eight-second
statement timeout and at most four concurrent calls per server process. It cannot
accept SQL, external URLs, or execute generated actions. Repeated explicit retries
can still spend inference budget; the existing Clover cache applies. Production
multi-user quotas and per-policy authorization are beyond this Lab experiment.

`scripts/appliance/install-ui-decisions.py --release DIR --revision SHA` overlays
only this adapter and its registration onto the current running Lab server image,
records the prior runtime and image, preserves its exact environment, and recreates
only that service. DIR contains `ui_decisions.py`. It writes a private Compose
override under `/opt/rvbbit/ui-decisions`; retain that override in future server
releases. Rollback by restoring the prior image/Compose override recorded in its
receipt, then recreating only the server. UI Studio uses its existing separate
installer and image; it still has no database credentials or production mounts.
