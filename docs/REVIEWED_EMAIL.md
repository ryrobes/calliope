# Reviewed email and recipient context

Callie prepares correspondence on the Stage. The operator can page through a
batch, edit an email, ask Callie to revise it, hold a draft, send one, or send all
ready drafts. Sending uses the exact displayed revision and frozen attachments;
it does not invoke another model pass. Held and unfinished drafts are excluded.

## First use

Ask **“Connect my email”** (or supply the mailbox address). Callie uses the
`email.connect` action through `administer_calliope_action` to open a native
secure connection card in the conversation. The mailbox owner can do this
without being an organization administrator. A regular question panel can
clarify the provider/address first; the app password goes only in the secure
card. No draft email, desktop takeover, or Hermes gateway configuration is
needed. Gmail defaults are prefilled; other IMAP/SMTP hostnames and ports are
available under **Mail server details**.

**Test and connect** checks both mail protocols and saves the credential only
on success. The card becomes a receipt and a durable, credential-free event
resumes the same conversation. Failures keep the card available for correction;
duplicate submissions reuse the completed result. `list_calliope_email` includes
the operator's connection status as well as their batches and incoming mail.

Connection failures identify the failed IMAP or SMTP step, with completed checks
recorded in the action receipt. Network/TLS and Inbox metadata failures are
distinct from a rejected login. Server replies and passwords are excluded from
diagnostic logs. Gmail app passwords pasted with their display spaces are
normalized for Gmail connections; passwords for other providers are preserved.

Ask Callie, for example:

> Draft a short email for each branch manager about this week's one-sheet.
> Use their contact context and the current branch data, attach the relevant
> PDF, and put the drafts on the Stage for my review.

Addresses must come from actual supplied or queried records. Demo recipients
should be addresses the operator controls. Drafting works before a sender
mailbox is connected.

You can also click **Connect sender mailbox** on an email card to test IMAP and SMTP and save
the credential through Calliope's canonical encrypted credential store. The
first transport supports Gmail-compatible IMAP/SMTP with an app password
(when the account permits app passwords). OAuth mailbox authorization is not
part of this slice. Defaults are `imap.gmail.com:993` and `smtp.gmail.com:587`.
The form also accepts other server hostnames; the HTTP API supports alternate
ports and implicit SMTP TLS on 465. Passwords never enter the model context.

Connecting starts collection at the mailbox's current UID cursor. It does not
import historical mail. Reconnecting the same account preserves the cursor.
Do not enable Hermes's native automatic email responder on the same mailbox:
the reviewed-mail bridge is deliberately a separate consumer.

## Contact continuity

Contacts have stable IDs, sourced business notes, a compact write-up, and explicit
email, SMS and external-record associations. Linking an existing Calliope login
is optional and grants no login or impersonation rights. Contact context is
shared across that operator's chats and channels; company-wide contact sharing
and SMS transport are later slices.

`prepare_calliope_email` returns each recipient's current context before writing.
`read_calliope_email_batch` refreshes the context for editable drafts.
`save_calliope_email_draft` consumes that context token and the expected draft
revision. The context includes known preferences, business references, notes,
and relevant actual correspondence. Callie still queries the business data for
current amounts and statuses. Existing private user dossiers are never copied
into recipient memory.

Click a recipient's name to inspect and correct their context. Corrections are
stored as sourced notes. A background write-up uses the existing Hermes/Dream
generation path; its model and usage receipt are saved with the summary's source
message and note IDs. Summary generation runs separately from mail delivery.
Unsent drafts do not count as communicated promises. Approved messages retain
their original recipient-context snapshot.

## Replies and delivery

The Hermes supervisor exposes authenticated `/v1/reviewed-mail/probe`, `poll`,
and `send` routes. IMAP reads use UID cursors and `BODY.PEEK`, independently of
whether another mail client marked a message read. Messages are committed before
advancing the cursor and deduplicated by Message-ID. Attachments are downloadable
from the review card through authenticated routes.

Replies match the original Message-ID/References and correspondent, not the
latest chat with that person. They appear in the originating batch and queue an
existing Calliope agent-mailbox event. Callie can investigate and prepare a reply
for review. Unmatched messages remain available through `list_calliope_email`;
they do not arbitrarily wake an unrelated chat. This is not a replacement for
the operator's general email client.

Replies to a tagged recipient such as `alice+contractor@example.com` may come
from `alice@example.com`. When the headers reference the sent message, this
routes back to that message's conversation and contact. This applies to Gmail
addresses and the connected Google Workspace mailbox's own domain
(`imap.gmail.com`). Other domains retain exact address matching. In-Reply-To
takes precedence over older References. Different plus tags remain separate
contacts: no global address normalization, contact merging, or reassignment of
unrelated base-address mail.
Follow-up drafts use the actual reply sender while retaining the original
contact context. Messages without matching reply headers remain unmatched.

Deleting a chat detaches its correspondence rather than deleting the contact or
mail history. A detached conversation remains discoverable through the mail
tools; new replies no longer wake the deleted chat.

The browser approval endpoint is distinct from MCP: there is no model-facing
send or approve tool. Duplicate clicks and HTTP retries reuse durable outbox
state. The Hermes volume keeps a SQLite submission ledger so retrying a lost
HTTP response cannot send the same attempt twice. A timeout after SMTP submission
is labeled **Check send status**, rather than silently retried. Inspect the
original mailbox's Sent folder before preparing another email in that case.
**Sent** means accepted by SMTP, not proof of delivery or reading.

Incoming message contents are external evidence, not operator instructions.
Stage correspondence follows the chat's existing viewing rules; mail editing,
sending, connection management and attachment downloads require its owner.

## Deployment and verification

Both `calliope-server` and `calliope-hermes` need the new images. PostgreSQL schema
0465 is also installed idempotently by the server at startup, so an existing install
does not require a PostgreSQL image rebuild just to test this feature. Preserve
the Hermes data volume, which contains `.calliope-control/mail-spool.sqlite`.

Run from the repository root with the project's Python test environment:

```sh
python -m pytest -q services/calliope/test_correspondence.py services/calliope/test_correspondence_ui.py
```

These tests use an isolated PostgreSQL cluster, simulated mail transport, and
Chromium rendering the real Stage components. They exercise recipient context,
revision conflicts, exact batch approvals, held drafts, reply routing, malformed
mail, frozen attachments, uncertain submission, ownership, responsive layouts,
and edits surviving Stage repacks. No real mailbox is contacted. A connected
Gmail send/reply test remains a separate operator-controlled step.
