# SMS conversations with Calliope login users

Twilio is the transport. An administrator explicitly links each allowed E.164
phone number to an **existing Calliope login**. The connection owner does not
become the execution identity for other people's messages. Contact associations
alone grant no access, and unknown numbers do not create users or conversations.

## Demo phone (no Twilio or public tunnel required)

Enable `WAREHOUSE_CALLIOPE_SMS_SIMULATOR_ENABLED=1` on the Calliope server, then
open `/calliope/sms-simulator` using your normal Calliope login. The feature is
off by default. Compose a text, attach/paste/drop up to four photos, and send.
The first message creates an ordinary SMS conversation on the Floor. **Open
Callie’s thread** opens its Stage beside the phone. The expand icon in the phone
header hides the side controls for recording; the simulated-transport label stays.

The conversation selector persists across reloads. **New conversation** starts
a separate simulated phone/thread without erasing earlier work. Messages queue
while Callie is busy, and completed replies and specialist follow-ups return to
the handset. Photos use the same image limits and attachment processing as MMS.

This substitutes authenticated browser submission, local image upload, and a
local outbox destination for Twilio transport. It reuses the SMS inbox/worker,
mapped execution identity, turn route, memory/tools/Stage, Hermes formatting,
and reply queue. It never invokes Twilio send or requires carrier credentials.
Simulated bindings are explicitly separate from real phone mappings. Each login
can access only its own simulated conversations, and cannot supply a different
execution identity. The feature does not grant additional workflow permissions.

This exercises real Calliope work, **not** carrier delivery, signatures or MMS
downloads. Validate those separately with an actual phone before relying on a
real Twilio deployment.

## Setup in Calliope

1. Ask Callie to connect Twilio text messaging. `sms.connect` opens the normal
   secure Stage form for Account SID, Auth Token, Twilio number and public HTTPS
   callback URL. The operator must belong to the Admins Team. The token goes to
   the encrypted credential store; it is not included in prompts or receipts.
2. Set the Twilio number's **A message comes in** webhook to the exact URL shown
   in the card, with **HTTP POST**, for example
   `https://callie.example.com/webhooks/twilio`.
3. Ask Callie to link a person's phone to their login. `sms.user` opens a native
   confirmation form with phone number, existing login email, and an enabled
   checkbox. This step is always confirmed by the administrator in the UI.
4. Text the Twilio number from that phone. The first text starts a private SMS
   conversation owned by the linked login; subsequent texts resume it. It appears
   on that person's Floor. Archiving it starts a new conversation on the next text.

Setup tests credentials and ownership/SMS capability of the Twilio number. It
also reports whether Twilio already has the expected URL/method. It does not
prove that Twilio can reach the callback, and it sends no test message. Use
`calliope_sms_status` or authenticated `GET /api/calliope/sms` to inspect mappings,
failed incoming deliveries and outgoing submission results. Administrators see
appliance settings; other users see only their own phone mappings and messages.

A localhost or SSH tunnel URL is not reachable by Twilio. On a private or
LAN-only install, configure a public HTTPS ingress that forwards
**only POST /webhooks/twilio** to Calliope. Keep its URL stable and enter the
same URL in both the secure form and Twilio.
Do not expose the Hermes control port or its separate native SMS listener.
Ordinary public Calliope installs already forward the callback through Caddy.
Twilio account/carrier eligibility still needs a real-number delivery test.

## Conversation behavior

- Texts run through the normal Calliope turn, tool, specialist, memory and Stage
  system as the mapped user. A missing execution identity fails closed.
- Completed replies and specialist follow-ups are automatically texted back to
  the same number. There is no agent tool for arbitrary outbound SMS recipients.
  Callie receives SMS-specific guidance to keep these replies brief.
- Ordinary chat and non-secret clarification stay on the phone. Channel context
  accompanies human SMS turns and asynchronous follow-ups, and the model is never
  told that a phone conversation originates in the web notebook. Stage artifacts
  remain available for later inspection; answers include the useful details in SMS.
  “Send me the details” means text back here unless another channel is requested.
  Secure setup or explicitly requested reviewed email can still require a web
  control; explain that specific limitation when needed. SMS does not bypass it.
- A small inventory of at most eight accessible, approved Playbook versions gives
  short/photo-led messages a discovery starting point. Draft-only, archived,
  blocked and inaccessible methods are excluded. Callie must read the approved
  version before using it; capability search covers methods outside the shortlist.
- Existing contact context is consulted within the mapped user's access. Another
  operator's private contact notes are not imported by matching phone numbers.
- Incoming JPEG, PNG, WebP and GIF MMS images, including photo-only messages, use
  normal Calliope attachments. Up to four images share the configured chat image
  budget (8 MB by default). Unsupported/unavailable media produces an explicit
  attachment-failure note, rather than pretending an image was inspected.
- To transfer a phone, revoke the old mapping first, then link the new login.
  Bindings have distinct immutable IDs. Old pending messages and replies cannot
  leak into the new user's session. A Twilio `OptOutType=STOP` callback revokes
  the binding; START never creates/reinstates a login grant by itself.

## Implementation and recovery

The bridge imports the deployed Hermes `plugins.platforms.sms.adapter.SmsAdapter`
for signature validation, plain-text formatting, chunking and Twilio submission.
Calliope owns the incoming route and durable delivery queues so user permissions
and Floor conversations remain authoritative. We do not run a second independent
Hermes messaging gateway. See [Hermes SMS documentation](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/sms).

- `services/calliope/sms.py` and `sms.sql`: setup actions, versioned mappings,
  signed incoming queue, per-login conversation dispatch and reply outbox.
- `docker/calliope/sms_bridge.py`: authenticated Hermes control bridge and durable
  per-chunk send spool. The inherited Hermes chunk helper defaults to 4096;
  this bridge explicitly requests the adapter's 1600-character SMS limit.
- `calliope.py`: normal turn delivery and SMS-specific context, including replies
  after asynchronous specialist work. Browser requests cannot impersonate
  internal SMS deliveries.
- Migration `0468_sms_login_channels`: additive tables and permission support for
  the SMS owner to use tools and review native cards. Other channel transcripts
  retain their existing access behavior.

The public callback is limited to 64 KiB of form data. Hermes validates the exact
configured URL signature, Account SID and destination. Twilio MessageSid is unique
in the inbox. Downloads and model work happen after acknowledgment. Incoming
media must belong to that message/account; CDN redirects do not receive Twilio
credentials. Queued texts are delivered in order when the user's SMS thread is
idle. Turn submission uses the inbox UUID for idempotency.

Each outgoing chunk has its own durable attempt ID. Transport retries reuse it.
The Hermes spool records an attempt before submission; an ambiguous timeout or
process crash becomes `uncertain`, never an automatic second send. Later chunks
of that reply are cancelled if an earlier chunk is failed/uncertain. `sent` means
**accepted by Twilio**, not confirmed delivered to the handset. Check Twilio's
message logs before manually retrying an uncertain submission. Delivery-status
callbacks, outbound MMS, unsolicited/bulk SMS and non-login customer messaging
are outside this first slice.

## Verification

Run the real-PostgreSQL queue/access tests, mocked transport tests and browser
forms/review tests:

```sh
python -m pytest services/calliope/test_sms.py \
  services/calliope/test_sms_bridge.py services/calliope/test_sms_ui.py
```

Set `ACTIVITY_TEST_ADMIN_DSN` to a disposable PostgreSQL cluster and run
`test_sms_turns.py` for the actual turn-route admission/retry tests.

The Hermes image build also runs `docker/calliope/test-sms-bridge-native.py`
against its **actual pinned adapter**, testing signatures, formatting, the
1600-character split and idempotent submission with a fake HTTP client. No real SMS is sent by these tests. Live acceptance
requires the operator's Twilio account, number, public callback and linked test
phone; do not call a credential-free implementation test a successful live send.
