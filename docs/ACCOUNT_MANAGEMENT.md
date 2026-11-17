# Calliope accounts and sign-in

Open the account menu → **My account** to edit your display name or password.
Administrators also have **People & access**, with People, Groups, Sign-in, and
account-change activity. Groups are the existing application Teams, including
Admins and Everyone; artifact, chat, application, and capability grants continue
to use those memberships. Library permissions remain in the existing Library.

## Personal passwords and new users

On the first server upgrade in shared auth mode, the existing configured admin
and explicitly allowed users receive accounts. The old access password is hashed
once; subsequent password changes are stored in PostgreSQL and are never overwritten
by the bootstrap environment. The old shared-password fallback is no longer used
by Calliope. `WAREHOUSE_LOGIN_PASSWORD` can be removed after initialization;
the Compose configuration allows it to stay unset on later starts. A fresh
instance still requires a bootstrap password or Google credentials before the
account store can initialize.

Administrators create a person by email and share the resulting setup link privately.
The owner picks their own password. Links expire after 24 hours and work once;
creating another invalidates the previous one. A reset link does not change the
existing password until used. Google-only instances do not need password setup links.
Calliope does not send invitations or reset messages automatically.

Passwords require at least 12 characters. A change/reset increments the account's
credential version, invalidating older browser, access, and refresh credentials.
The browser completing a password change receives a new session. Disabling an
account revokes access and overrides both explicit and domain allowlist entries;
work and group membership are retained. At least one active Admin must remain.

The account database stores encrypted, salted scrypt verifiers. Encryption uses a
key derived from the instance's existing independent `WAREHOUSE_JWT_SECRET`, which
must be retained with backups. No plaintext passwords, OAuth secrets, or setup
links are included in account audit events or normal API reads. Changing that
signing secret also changes the account encryption key; treat it as a protected,
durable instance secret, not a disposable startup value.

For host-level recovery, an operator can generate a one-time link for an existing
active admin from the Calliope container without knowing its password:

```python
import os, psycopg
from psycopg.rows import dict_row
from account_management import Accounts

def connection():
    return psycopg.connect(os.environ['WAREHOUSE_DSN'], row_factory=dict_row)

store = Accounts(connection, os.environ['WAREHOUSE_JWT_SECRET'])
email = 'admin@your-company.com'
result = store.setup_link(email, email)
print(os.environ['WAREHOUSE_PUBLIC_URL'].rstrip('/') + '/account/setup#token=' + result['setup_token'])
```

Run this only in a private administrator terminal and share the link privately.
Google-only instances deliberately reject password setup links.

## Google sign-in

Google login already used OpenID Connect. **Sign-in** now configures its web client
and allowlist without editing a server environment file or restarting Calliope.

1. Create a **Web application** OAuth client in Google Cloud's Google Auth Platform.
2. Register the exact redirect URI shown by Calliope's Sign-in screen. On the local
   test instance this is `https://localhost:18443/auth/google/callback`.
3. Enter the client ID and client secret into that screen and save.
4. Use **Test Google sign-in**. Sign in as an allowed address before optionally
   choosing **Use Google sign-in only**.

Use the externally reachable instance origin for a shared deployment; `localhost`
only points to the device on which the browser runs. Configure Google branding and
Audience for the intended users. A project in testing mode may require Google-side
test users as well as Calliope's allowlist.

The login flow requests only `openid email profile`; it does not require Hermes,
Gmail, Drive, Calendar permissions, or Workspace administrative installation.
Existing separate incremental Workspace grants retain their own flows.

Active People accounts are allowed. Additional explicit emails (or `@domain`
entries) can create an account on their first verified Google sign-in. Empty
additional allowlists do **not** mean public registration. The optional Google
Workspace domain checks the signed `hd` claim; leave it blank to accept allowed
consumer Gmail accounts. Tokens are verified for signature, issuer, audience,
expiry, verified email, and nonce, and sign-in state is bound to the initiating
browser. The stable Google subject is associated with the local email account.

References: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
and [web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

## Upgrade and verification

The server additively initializes the account tables; no PostgreSQL extension
rebuild is needed. Ingress must route `/account` and `/account/*` to Calliope.
The canonical Caddy configurations include those paths. Existing standalone
shared-password and `WAREHOUSE_AUTH=pg` installations retain their original auth
behavior when no Calliope account store is bound; the account manager is for the
normal email-based Calliope mode.

Run `pytest services/calliope/test_account_management.py` with the service Python
dependencies and local `initdb`/`pg_ctl` available. These tests use disposable
PostgreSQL and cover personal password migration, one-time links, browser/OAuth
revocation, group reuse, last-admin protection, request origin checks, and Google
callbacks with locally signed RSA tokens. Live Google activation additionally
requires a real client registration and a human Google sign-in.
