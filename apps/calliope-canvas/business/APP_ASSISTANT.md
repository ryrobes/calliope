# Callie in company apps

The **Ask Callie** avatar opens a conversation beside a desktop app or in an
Android sheet. The latest exchange is shown first; earlier conversation is
available on demand. Chats are ordinary Calliope sessions and turns in Postgres,
discoverable from either client. There is no new app-specific agent or database.

## Context and review

Each message captures `calliope.app-context.v1` as its durable `origin_context`:

- App slug and immutable version, loaded navigation/record identity, observation
  timestamp, and whether the view is offline.
- Bounded content from the rendered component tree, including current form
  values, edited flags, and last-read saved values from the form's SQL source.
  Refreshing the app does not turn an unsaved draft into a saved fact. Missing or
  mismatched source records have an unknown comparison. Hidden branches and
  password/secret fields are omitted.
- Editable fields identified by component ID and their declared bindings/types;
  named actions distinguish navigation from business commands.

The server rechecks artifact access and resolves the canonical app/version.
Visible content and unsaved values remain **client observations**, not verified
SQL facts or execution authority. Normal Calliope tool permissions still apply.

Callie can offer one JSON proposal, fenced as `calliope-ui` or as a trailing JSON
object when the response formatter removes fences:

```json
{"context_id":"captured-turn-uuid","changes":[{"field":"page.notes","value":"Inspected the unit. Filter is clean."}]}
```

The client renders an explicit **Use these draft changes** review. It checks
the original app/version/record, unchanged affected fields, current visibility,
binding, type and offered choices before applying the entire patch. It never
accepts model-supplied paths, SQL, attachment IDs or commands. Normal form
validation and the existing Submit button remain in charge of submission.
New turns also retain declared record links. Their record chip can reopen the
captured record; it never applies a draft or business command.

Navigation proposals use `{"context_id":"…","navigate":"component-id"}` and
must match an offered navigation action, never a business command.

Proposals stay attached to their original messages when the user changes record.
Retries reattach using `client_turn_id`; an interrupted durable message restored
from server history is never submitted as a new message. A Stop reply control
uses Calliope's existing turn cancellation endpoint.

Context construction is cached by model and rendered-tree revision. Streaming a
reply does not rebuild the full application context on every frame.

## Voice and phone layout

Dictation uses `/api/calliope/transcriptions` and puts text in the composer for
review. It does not send automatically. Read aloud uses the existing voice render
and timed PCM stream. Provider selection, credentials and speech preparation stay
on the server. Proposal JSON is excluded from spoken text.

Android uses runtime microphone permission, AudioRecord and AudioTrack. Recording
and playback stop when the app backgrounds; generation and audio session IDs
reject late events from cancelled operations. Changing the selected record while
dictating cancels that recording. The desktop retains its PipeWire adapter.

`CSection` supports `collapse: "phone"` and a data-bound `summary`. It becomes a
keyboard/touch disclosure on narrow screens and stays an open section on desktop.
The demo suite uses this for navigation, filters and work lists. These are shared
catalog primitives in GPUI and web, not per-demo renderer branches.

A cached app retains its loaded version until the user chooses **Use vN**.
Unfinished record drafts and pending submissions block that change and retain
their original version; network failure can still restore the cached view.

## Verification

Use the private Xvfb or ADB probe described in `NATIVE_TESTING.md`.
`assistant_open`, `assistant_close`, `assistant_inspect`, `assistant_send`,
`assistant_apply`, `assistant_dictate`, `assistant_read` and
`assistant_stop_audio` operate through the real conversation and renderer paths.
`assistant_send` accepts `message` or `retry: true`; apply/read take a turn `index`.
`check_assistant.py --live-agent --output /tmp/calliope-assistant` runs the
record-switch and intervening-edit checks in an isolated desktop display.
These probes can invoke the live agent and incur usage; applying suggestions only
edits the local draft. Existing `press` on Submit executes the business action.

Client tests cover wrong record/version, intervening edits, unknown fields,
command-as-navigation rejection, formatter compatibility, stream routing and
restart recovery. Backend tests cover app authorization, canonical identity,
bounded context, private conversation lookup and speech protocol removal.

Current limits: context is a bounded page snapshot, not continuous agent vision;
arbitrary HTML and hidden datasets are not included. The first simultaneous
message from two devices may create two normal chats; subsequent discovery picks
the most recently updated owned chat. Local unsent chat composer text is not a
cross-device draft. Field drafts and attachments retain their existing durable
device journal.
