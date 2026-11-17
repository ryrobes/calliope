# Persistent chat questions

Migration `0411_calliope_input_requests` gives ordinary `request_calliope_input`
forms their own lifecycle. Formal observation-backed Decision Requests remain
in `calliope_decision_requests` with their existing contract.

New input surfaces create a row in `rvbbit.calliope_input_requests` through a
PostgreSQL trigger, including headless/background turns. The row stores the
original question and fields, owner, originating session/turn/surface, status,
structured answers, revision, answering identity/time, and delivery message ID.
Updates project `request_state` back into the surface and advance the session's
update timestamp so other open tabs reconcile.

- `open`: shown in the chat decision dock and Floor's Awaiting You lane.
- `answered`: structured answers and an outbox message commit together; the
  question moves to Done. The mailbox starts the human's next chat turn when
  the session is idle, using a stable client-turn ID. Retries cannot create a
  second answer delivery. Expired delivery leases are reclaimed after restart.
- `cancelled`: explicitly closed without sending an answer or starting a turn.

An identical answer retry returns the original receipt. Different stale answers
conflict. Owner checks and revision checks run on the server under a row lock.
The browser keeps only drafts; no local-storage marker determines completion.

Floor's Answer button opens the question inside the Floor drawer. It supports
choices, selects, text, multiline text, and booleans. Historical timeline frames
are read-only. Web answers to Google Chat conversations remain unavailable to
preserve the existing home-channel boundary; they do not silently fork a chat.

Existing forms are not backfilled: ambiguous historical questions remain Stage
artifacts and do not enter the new blocker queue. Transcript prose never guesses
that a canonical question was answered. Use its controls to answer or cancel it.

## API

Authenticated, owner-scoped endpoints:

- `GET /api/calliope/input-requests/{surface_id}` returns the definition, lifecycle,
  `can_answer`, and outbox delivery status/turn ID.
- `POST .../{surface_id}/answer` accepts `{ "revision": 1, "answers": { ... } }`.
- `POST .../{surface_id}/cancel` accepts `{ "revision": 1 }`.

A saved answer is distinct from successful model execution. The delivery status
is `queued`, `delivering`, `delivered`, or `failed`; the answer receipt persists
if a model call later fails. The linked chat and delivery turn provide recovery
context without erasing or silently changing the human's choices.

## Deployment and checks

Deploy the calliope-server image and apply migration 0411. The PostgreSQL image
includes it in the additive migration chain. For a server-only rollout, apply
0411 transactionally through the usual operator SQL migration process and
record its name in `rvbbit.schema_migrations`; no PostgreSQL restart is needed.
The server packages the table/trigger DDL for bootstrap, but the full migration
is required to add questions to the Work Graph and future timeline snapshots.

Migration `0466_work_graph_input_requests` restores the graph branch accidentally
omitted when `0422_floor_thread_briefs` replaced the view. Apply 0466 after the
normal migration chain on existing installs and record it in `schema_migrations`.
It preserves the installed view's other branches, columns, dependents and grants;
reapplying it does not duplicate questions. Existing open requests immediately
appear in Awaiting You. This does not reopen answered requests, backfill old
surface-only forms, or rewrite historical timeline frames. No service restart is
needed for this SQL-only repair.

`test_calliope_inputs.py` exercises a real, empty disposable PostgreSQL database
via `CALLIOPE_INPUT_TEST_DSN` (never point this at an installation).
`test_input_questions_ui.py` exercises the real chat and Floor renderers with
local browser fixtures, including error/draft handling and direct Floor answers.
`test_work_graph_inputs.py` applies the registered graph-view migrations in order
against disposable PostgreSQL, then checks the final view and Floor projection.
