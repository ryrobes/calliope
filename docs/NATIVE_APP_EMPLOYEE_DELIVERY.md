# Company apps: employee delivery

This extends the existing company-app registry, accounts and flat Teams; it does
not introduce another identity system, app registry or companion data server.

## Employee experience

1. An author shares the app with an existing person or Team using Gallery's
   access editor, or asks Callie to do so with the existing team/artifact tools.
2. The access editor's **Send to a phone** link opens `/connect?app=<slug>`.
   This page supplies a QR, an OS app link and a browser fallback. The installed
   mobile client is still required; this is not an app-store installer.
3. The phone connects directly to the instance over trusted HTTPS. **Continue in
   browser** uses the instance's normal password/Google login, followed by a
   matching-code device approval. **Reconnect in browser** is also in Account.
4. The existing authorized catalog determines which apps appear. Links can open
   a specific record using `&record=<type>&id=<id>`; the destination app resolves
   its data through its ordinary authorized query.

`WAREHOUSE_NATIVE_PUBLIC_URL` optionally gives connection pages their reachable
HTTPS origin when an administrator views the server through a local tunnel.
This setting does **not** change Google OAuth's callback, which still follows
`WAREHOUSE_PUBLIC_URL`. Google sign-in from a phone needs that callback on a real
HTTPS hostname reachable from the phone and registered exactly with Google; a
`localhost` callback cannot complete there.

The OS URL is `calliope://open?server=<encoded-https-origin>&app=<slug>` with
optional record/id. It carries navigation only. A different instance opens the
account screen for sign-in; it does not silently replace a connection. Web record
fallbacks use the existing `/apps/<slug>#record=...` format. The shared Rust link
parser rejects credentials, non-HTTPS origins, unknown or duplicate fields and
malformed record destinations.

## Authentication and recovery

The server issues a short-lived public request ID and a private device secret.
The browser sees the public ID and matching code. Approval requires the existing
personal session and a session-bound CSRF token. Only the initiating client can
collect the one-use result; it receives the normal revocable Calliope session.
Cookies and private secrets never travel in the OS callback URL.

Pending browser sign-in is kept in the existing Android Keystore/iOS Keychain
connection storage. Restarting the client resumes the same request. Cancellation
preserves the previous connection and account-scoped drafts. Restarting the
server expires pending handoffs; the client asks to start sign-in again.

The browser approval page uses `Referrer-Policy: same-origin`: Chrome otherwise
sends `Origin: null` on its form POST under `no-referrer`. Cross-origin approval
remains rejected. The callback merely wakes the app; identity comes from its
private HTTPS exchange.

## HTML analysis thumbnails

HTML company dashboards can read published-app data during an isolated capture
through a narrow read bridge. It passes the actual viewer into the existing app
runtime, repeats artifact access checks, and rejects events, drafts and request
keys. It gives the HTML page no session cookie or service bearer.

HTML thumbnails now use the same viewer-specific storage and private caching
as native apps. Failed/incomplete captures keep the previous image; changed
publication or lost access prevents replacement. The Northstar Analysis JPEG
was visually checked with populated live counts rather than a blank/error page.

## Boundaries and next step

An instance still uses a shared warehouse. App grants and SQL actor predicates
protect the published app path; they do not establish row-level isolation for
separately available general SQL/authoring tools. Attachment access remains
artifact-level.

Employee feedback tied to app version and record, publication and draft-preserving
updates are now covered by [the revision loop](NATIVE_APP_REVISION_LOOP.md).
[Replay and review](NATIVE_APP_REPLAY_AND_REVIEW.md) adds reusable cases, isolated
validation and requester confirmation. Store releases and OSS installer work
remain separate; physical iPhone testing is deferred until hardware is available.
