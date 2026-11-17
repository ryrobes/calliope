# Calliope shipping bundle

One Compose project contains Calliope, DataRabbit, RVBBIT/Postgres, Warren,
Hermes, Hindsight, self-hosted Clover and its CPU/API workers, and UI Studio.
It uses your OpenRouter key; a Clover account and a GPU are not required.

**Current status: 1.2.0.** The complete product still needs its
empty-machine acceptance test. Fresh Postgres installs a verified schema baseline
baked into the image; existing databases apply only missing migrations. Image
build and schema verification details are recorded in the release artifacts.

## First start

Requirements: Linux x86-64 and Docker Engine with Compose 2.30 or newer. Budget
16 GB RAM and 50 GB disk as an initial test-machine allocation; these are
planning estimates, not measured minimums. The optional desktop/model workers
need additional RAM, disk and initial downloads. Other architectures are not
yet release-tested.

The installer checks the machine, asks for your OpenRouter key, administrator
login and address, then downloads this bundle, configures it and starts it:

```sh
curl -fsSL https://usecalliope.com/install.sh | bash
```

It installs into `./calliope` (set `CALLIOPE_DIR` to change that): each release
unpacks under `versions/`, `current` points at the running one, and the install's
own data (`.env`, `state/`, `settings.env`, `connections.json`) lives beside
them. Run it again to upgrade; it backs up the database first. `install.sh` is
also in this bundle: `CALLIOPE_BUNDLE=/path/to/this.tar.gz ./install.sh` works
without downloading anything.

To do it by hand instead, in an unpacked bundle:

```sh
cp settings.env.example settings.env
chmod 600 settings.env
# Edit settings.env: OpenRouter key, administrator email/password, public URL.
python3 configure.py        # or, without Python 3.9+ on the host:
# docker run --rm --entrypoint python3 --user "$(id -u):$(id -g)" -e HOME=/tmp \
#   -v "$PWD:$PWD" -w "$PWD" "$(grep -m1 -oE '[^ ]+/calliope-server[:@][^ ]+' compose.yaml)" configure.py
docker compose pull
docker compose up -d --wait --wait-timeout 900
```

Open the URL you entered and sign in with the administrator email/password.
Set `CALLIOPE_PUBLIC_URL=https://YOUR_SERVER_NAME` for normal deployment.
Calliope is served at that origin; remote MCP clients use the same origin's
`/mcp` endpoint, including its OAuth discovery and authorization routes.
Use an address resolvable from your phones, desktops and MCP clients.
Plain HTTP is supported only on localhost for development. Remote addresses,
including LAN addresses, require HTTPS for the shared MCP OAuth issuer.
Published ports follow the port in that URL (80/443 when omitted).
Voice/camera and secure browser APIs require HTTPS. A publicly
resolvable hostname enables Caddy's automatic HTTPS on ports 80/443; LAN-only
HTTPS requires a trusted local certificate or your own TLS reverse proxy.
Port mappings listen on the host's network interfaces, not just localhost.
`connections.json` records the configured web, DataRabbit, MCP and SQL endpoints without
including passwords. Public DNS, routing and ports 80/443 must reach this host
for automatic public certificates; the installer does not configure your router.

## Behind your own HTTPS proxy

For a LAN name such as `https://calliope.local`, or when Caddy, nginx or
Traefik already terminates TLS on the network, let that proxy serve HTTPS and
forward to plain listeners. Set these before initialization:

```sh
CALLIOPE_PUBLIC_URL=https://calliope.local
CALLIOPE_TLS=proxy
CALLIOPE_TRUSTED_PROXIES=192.168.1.10   # the address the proxy connects from
CALLIOPE_HTTP_BIND=127.0.0.1:8080       # https://calliope.local      -> here
CALLIOPE_DATA_BIND=127.0.0.1:8081       # https://calliope.local:3001 -> here
```

Use an IP:port the proxy can reach (`0.0.0.0:8080` when it runs on another
machine). The proxy must pass the original `Host` and set
`X-Forwarded-Proto: https`, stream responses without buffering (MCP and live
updates depend on it), and allow WebSocket upgrades. Calliope accepts those
forwarded headers only from `CALLIOPE_TRUSTED_PROXIES`; other clients cannot
claim HTTPS. Phones and desktops must trust the proxy's certificate, for
example by installing your internal CA. No ACME certificate is requested in
this mode, and ports 80/443 are not published by this bundle.

## DataRabbit

DataRabbit is available by default at **https://YOUR_SERVER_NAME:3001**, or open
`/datarabbit` on the main Calliope address. Allow inbound TCP **3001** in your
host/cloud firewall. It uses the same hostname, TLS certificate, and login cookie
as Calliope, so an administrator already signed in can open it directly. Password
and Google sign-in happen on the main Calliope origin and return to DataRabbit;
no additional OAuth redirect registration or DNS record is needed.

The public proxy requires a Calliope administrator for DataRabbit's pages and
APIs, rejects cross-origin changes, and fails closed if authentication is unavailable.
The underlying Next.js server remains private to the Compose network.

Set `CALLIOPE_DATA_URL=https://YOUR_SERVER_NAME:PORT` before initialization to
choose a different port. `CALLIOPE_DATA_BIND` may restrict the listener to a host
IP, but its port must match that URL. Both web URLs must use the same scheme and
hostname. Local HTTP development keeps DataRabbit bound to loopback.

The initializer checks configuration syntax and generates internal passwords
once. It does not call a model or verify your OpenRouter credit balance.
A valid funded key is required for inference. After startup, use System's
capability checks and send Callie a message to verify the complete path.

New chat profiles start on **OpenRouter / openai/gpt-6.1-sol**. This is separate
from the models Clover uses for embeddings, decisions, reranking and other
specialist operations. SQL LLM operators and web research run on
**openai/gpt-6-luna** (the generator's `--sql-model` and `--research-model`), so
row-level SQL calls stay fast and cheap regardless of the agent's model. Hermes automatically registers the shared OpenRouter
credential. Connecting OAuth or changing that credential never selects a new
model for an existing profile.

## Remote SQL clients

PostgreSQL is published on **5432** with native PostgreSQL TLS and password
authentication. This is a SQL connection, not an HTTPS URL. SQL clients use
`CALLIOPE_SQL_HOST` (defaults to the hostname/IP in `CALLIOPE_PUBLIC_URL`).
`CALLIOPE_SQL_BIND=0.0.0.0:5432` is the default; set a LAN IP or different port
before initialization when needed, for example `192.168.1.20:15432`.

The first start generates a private instance CA and a server certificate for
that name. Copy **only** `state/postgres-tls/ca.crt` to the client and connect:

```sh
psql 'host=YOUR_SERVER_NAME port=5432 dbname=rvbbit user=YOUR_SQL_USER sslmode=verify-full sslrootcert=/path/to/ca.crt'
```

Use normal Postgres login roles for ingestion, notebooks and database tools.
The generated administrator password is `POSTGRES_PASSWORD` in the private
`.env`; it is separate from the Calliope web password. Non-TLS connections from
outside the database's Compose subnet are rejected. Internal services retain
their existing authenticated private-network connections. If a VPN/proxy
masquerades SQL traffic into that subnet, also enforce TLS in that client/proxy.

For an existing CA/public certificate, set `CALLIOPE_SQL_TLS=provided` before
initialization, then put the certificate chain in `state/postgres-tls/server.crt`
and its key in `server.key` before startup. The TLS initializer validates the
hostname, expiry and matching key and never replaces a supplied certificate.
Clients then use the corresponding CA or their system trust store.

Keep the entire `state/postgres-tls/` directory across upgrades. Generated
certificates are renewed when the TLS initializer runs within 30 days of expiry,
using the same CA. To renew/reload without restarting PostgreSQL:

```sh
docker compose run --rm postgres-tls
docker compose exec -u postgres postgres psql -U postgres -d rvbbit -c 'SELECT pg_reload_conf();'
```

## Optional services

Before initialization, set `COMPOSE_PROFILES=computer` in `settings.env` for
Callie's persistent CPU desktop (browser, files, terminal and computer control).
This is a containerized Linux desktop, not a dependency on an existing host VM.
The separately hosted Qwen CUA GPU worker is not included in this first bundle.

Set `COMPOSE_PROFILES=computer,specialists` to also start GLiNER and TSPulse CPU
workers. Their models download on first use/startup and are cached in volumes.
The initializer only enables their Clover routes when this profile is selected.
All fresh installs otherwise share the same hosted embedding model and dimension.

## State and upgrades

Keep `.env`, `state/`, and the project's Docker volumes together. `settings.env`
contains your bootstrap secrets; delete it after configuration or keep it private.
The runtime OpenRouter key is `state/clover/credentials/openrouter.key`, shared
by Clover and Hermes and replaceable through System settings. Do not rerun the
initializer over an existing install. It refuses to overwrite existing state.

Warren uses the host Docker socket to start the release-pinned Python, MCP and
DLT runtime images. This is a trusted single-instance deployment, not a Docker
sandbox boundary. Internal service APIs stay on the project network;
the PostgreSQL port is published for SQL clients, while
the optional advanced DataRabbit entrance binds to loopback port 3001.

The release manifest lists every product image and dynamic runtime image and
pins their registry digests. `distribution_state: public-images-locked` means
every image passed an anonymous registry check. If it says
`awaiting-public-images`, the publisher must make the packages listed under
`images_needing_public_access` public before anonymous installation will work.
Development candidates use version tags and are labelled as unverified. No
mutable `latest` product tags or local source builds are used by the bundle.

Installer-only revisions use a distinct bundle version, such as
`1.2.0-rc.4+bundle.1`. `release.json` records the bundle's source commit separately
from the unchanged image versions, digests and source commits. These revisions
do not republish image tags or change the database schema target.

Before an upgrade, back up Postgres plus the persistent volumes, `.env` and
`state/`; the installer does this itself (`backups/pre-VERSION-…/`: a
`pg_dumpall` of the database and an archive of `.env` and `state/`). An upgrade
migrates the database in place, and the previous release then refuses to start
against it ("Database contains migrations absent from this image"), so going
back means restoring that dump into a fresh database, not just switching
`current`. The database image verifies baseline equivalence and incremental upgrades during
its build. The full-product upgrade rehearsal is still outstanding; do not treat
this release as a supported production upgrade until that rehearsal is done.

1.2.0 publishes the Python and MCP runtimes as `calliope-python-runtime` and
`calliope-mcp-gateway`. Warren still names those containers
`rvbbit-python-runtime` and `rvbbit-mcp-gateway`, from the capability names
`python_runtime` and `mcp_gateway`, and the registered endpoint URLs keep
using that DNS. Bootstrap does not redeploy a runtime that is already ready,
so an upgrade leaves the running containers on their current images. The
release catalog now records the new repositories for the next deploy. Do not
point that deploy at `rvbbit-python-runtime:1.2.0` or
`rvbbit-mcp-gateway:1.2.0`: those tags already exist and were published by
rvbbit-sql. `data/dlt-mirror` remains `rvbbit-capability-dlt-mirror`.
Recreate the Python or MCP runtime only after `calliope-python-runtime:1.2.0`
and `calliope-mcp-gateway:1.2.0` have been published.
Never use `docker compose down -v` on an instance whose data you need to keep.

## Diagnostics

```sh
docker compose ps -a
docker compose logs --tail 100 bootstrap storage-init
docker compose logs --tail 100 calliope hermes clover
```

Process health is not evidence that every provider model works. System exposes
capability checks and their real errors. Credentials and provider access can be
fixed independently of model selection.
