# Demo mode

Demo mode lets a public Calliope install (the planned host is dataslop.com) show a few published dashboards to anonymous visitors. It is **off** unless `CALLIOPE_DEMO_MODE` is `1`, `true`, `yes`, or `on`. With the flag unset, routes, SQL, cookies, and the front door behave as they do today.

Guest queries connect as `calliope_demo_login`, a non-superuser `LOGIN` role that inherits `calliope_demo_guest`. They do not `SET ROLE` on the service connection: a statement can undo that and become the superuser again. The login can `CONNECT` and `SELECT` only in the schemas named by `CALLIOPE_DEMO_SCHEMAS` (default `public_demo`). It has no `USAGE` on schema `rvbbit`. `rvbbit` functions are still executable by `PUBLIC` (on the order of a thousand of them); the missing schema `USAGE` is what stops the login from calling them. Do not `GRANT USAGE ON SCHEMA rvbbit TO PUBLIC`. The login also has no `TEMP` privilege and no access to `pg_stat_statements`. The dashboard bridge refuses any statement that is not one of the queries recorded for that published version, ignores the client's `timeout_ms`, caps rows with a real `LIMIT`, and rejects `rvbbit` references and operator calls (`get_secret`, `classify`, `means`, `mcp_call`, `synth_sql`, `set_config`, and the rest of the bridge denylist) before they reach the database. Validation (`rvbbit.route_explain`) runs on the service connection, never as the guest, and guest error text does not include internal function names.

Which dashboards a guest can see is an explicit choice. Nothing is public until an administrator publishes it.

## What a guest can open

- `GET /demo` mints an HttpOnly `wh_session` cookie for the guest principal and redirects to `/gallery`.
- `GET /` is answered by Calliope when `origin` has `CALLIOPE_DEMO_MODE` set. No cookie renders the public gallery and sets the guest cookie on that same response. A signed-in person is redirected to `/floor`.
- `GET /gallery` lists only dashboards marked public. A request with no cookie is the guest directly.
- `GET /d/{slug}` and `GET /d/{slug}/versions/{version}` open a public dashboard. A request with no cookie renders that page as the guest and sets the cookie. It is not bounced through `/demo`, so a crawler does not loop.
- `POST /api/d/{slug}/q` runs one allowlisted statement for that version.
- `GET /api/d/{slug}/provenance` returns the scrubbed build summary.
- `GET /api/gallery/metrics` returns metric names and values only. `definition_sql` is not included. Metric detail is denied.
- `GET /thumbs/dashboard/{slug}.png` for a public dashboard, and `GET /thumbs/demo/site.png`, return a card image with no cookie. A captured screenshot is not required.
- Static files under `/theme`, `/charts`, `/vendor`, and `/bg`.

Everything else is denied for that cookie, including `/calliope`, `/data`, `/floor`, `/team`, `/desk`, `/ops`, `/mcp`, app actions, and arbitrary SQL. A normal signed-in session is not a guest and is not subject to this list. Turn demo mode off and a leftover guest cookie is ignored.

The receipts widget on a dashboard has a **Built** tab, next to the data overlay, titled "How this was built". It shows the model names, token cost, wall time, and a short step list (tool or Hindsight calls) recorded when that version was published. Prompts, secrets, and filesystem paths are not included. Full traces are out of scope.

## Turn it on

New install: export the variables before `install.sh`, or put them in `settings.env`. `configure.py` writes them into `.env`. Off is the default (`CALLIOPE_DEMO_MODE=0`, root still `/floor`).

```bash
export CALLIOPE_DEMO_MODE=1
export CALLIOPE_DEMO_SCHEMAS=public_demo
```

`configure.py` leaves `CALLIOPE_ROOT_REDIRECT` at `/floor`. Demo mode does not change that fallback.

Existing install: edit the install's `.env` (compose reads this, not `settings.env` after the first configure):

```bash
CALLIOPE_DEMO_MODE=1
CALLIOPE_DEMO_SCHEMAS=public_demo
```

The installed `config/Caddyfile` was copied at install time. Replace the `@root` handle with the block from `docker/origin/Caddyfile.calliope` on this branch. The old `redir * /floor 302` (or a one-line placeholder) sends every visitor, including a crawler, away from Calliope before the app can answer. Keep this line inside the new `route` block:

```
redir * {$CALLIOPE_ROOT_REDIRECT:/floor} 302
```

If the running `deploy/compose.yaml` does not yet pass the variables through, add this override next to it as `compose.override.yaml` (Compose loads that name automatically):

```yaml
services:
  calliope:
    image: calliope-server:demo
    environment:
      CALLIOPE_DEMO_MODE: "1"
      CALLIOPE_DEMO_SCHEMAS: public_demo
      CALLIOPE_DEMO_GUEST_PASSWORD: "replace-with-at-least-16-characters"
      CALLIOPE_DEMO_QUERY_RATE: "2000"
      CALLIOPE_DEMO_QUERY_BURST: "400"
      CALLIOPE_DEMO_QUERY_CACHE_SECONDS: "120"
  origin:
    environment:
      CALLIOPE_DEMO_MODE: "1"
```

`CALLIOPE_DEMO_MODE=1` on `origin` makes Caddy hand `/` to Calliope. Anonymous visitors get the gallery. A signed-in person is sent to `/floor`. Leave `CALLIOPE_ROOT_REDIRECT` at `/floor` so a Caddy that does not see the flag still opens the Floor.

`CALLIOPE_DEMO_SCHEMAS` is a comma-separated list of schema names. `public`, `rvbbit`, and `pg_*` are refused. Use a schema that holds only data you are willing to show.

Restart `calliope` and `origin` after the env and Caddyfile changes.

## Guest login

Migration `0509_calliope_demo_guest` creates the `NOLOGIN` grant role and the allowlist and provenance tables. Migration `0510_calliope_demo_login` creates `calliope_demo_login`, revokes `TEMP` from `PUBLIC` (then grants it back to every existing login except the demo login), and revokes `PUBLIC` access to `pg_stat_statements` the same way. A login role created after that does not receive `TEMP` until you grant it. Superusers are unaffected.

Set `CALLIOPE_DEMO_GUEST_PASSWORD` (at least 16 characters). This branch's `configure.py` generates one when demo mode is turned on. The service applies it with `rvbbit.configure_demo_login` the first time a guest query runs. `CALLIOPE_DEMO_GUEST_DSN` overrides the derived DSN if the login is not on the same host as `WAREHOUSE_DSN`.

```sql
SELECT rvbbit.configure_demo_guest(ARRAY['public_demo']);
```

That grants `CONNECT` on the current database and `USAGE` plus `SELECT` on the named schemas. Re-run it after you add tables. The seed script does this.

`EXECUTE` on `rvbbit` functions remains granted to `PUBLIC`. That is not a grant to the guest role. Without `USAGE` on schema `rvbbit`, the login cannot call those functions. Granting that schema to `PUBLIC` would open them. Do not do that.

PostgreSQL lets a role change its own password, and event triggers cannot see `ALTER ROLE`, so `calliope_demo_login` cannot be forbidden from doing that. A new password does not make it a superuser or a member of `postgres`. The next guest connection that fails authentication sets the password back from `CALLIOPE_DEMO_GUEST_PASSWORD`.

## Publish a dashboard

Publishing or updating a dashboard records a canonical allowlist for that version from `rvbbitQuery("...")` literals and manifest `sql` fields. Statements the bridge would reject are left off the list, so they cannot run for a guest. Existing dashboards are not public and have no list until an administrator backfills them.

HTTP, as an administrator (not the guest cookie):

```bash
curl -sS -b admin-cookies.txt -H 'content-type: application/json' \
  -d '{"public": true}' \
  https://your-host/api/gallery/artifacts/THE-SLUG/demo
```

`{"public": false}` hides it again. Only `app_kind = dashboard` can be marked public. Toggling public refreshes the saved-query allowlist and leaves each version's build summary in place. It does not copy one version's models or cost onto another.

`/`, `/gallery`, and a public dashboard carry Open Graph and Twitter tags while demo mode is on. The site title and description are `CALLIOPE_DEMO_SITE_NAME` (default `Calliope`) and `CALLIOPE_DEMO_SITE_DESCRIPTION`. Set `CALLIOPE_DEMO_SITE_IMAGE` to a full image URL to replace the generated site card. `WAREHOUSE_PUBLIC_URL` should be the public origin so those image URLs are absolute.

## Sample dashboard, no model key

On a running install the database DSN is already in the `calliope` container. This marks `public-demo-revenue` public:

```bash
docker compose exec -T -e PYTHONPATH=/app -e CALLIOPE_DEMO_MODE=1 calliope \
  python - < scripts/demo/seed_public_dashboard.py
```

The script refuses to run without `WAREHOUSE_DSN`. Inside the container that variable is already set; do not point it at `port=55433 dbname=bench` unless that really is your database. Running it again updates the existing version in place. It does not append a new version.

From a checkout, against the published port in `connections.json`:

```bash
export WAREHOUSE_DSN='host=127.0.0.1 port=5432 dbname=rvbbit user=postgres password=...'
export CALLIOPE_DEMO_MODE=1
PYTHONPATH=services/calliope python3 scripts/demo/seed_public_dashboard.py
```

Or, if both migrations are already applied:

```bash
psql "$WAREHOUSE_DSN" -v ON_ERROR_STOP=1 -f deploy/demo/seed_public_dashboard.sql
```

That loads `public_demo.orders` and publishes `/d/public-demo-revenue`. The build panel says the model was `fixture` and the only step was `publish_dashboard`, because no model was called. Open `https://your-host/` with no cookie. A crawler gets the page directly; it is not bounced through `/demo`.

## Rate limits and the guest cache

`caddy:2-alpine` does not ship the third-party `rate_limit` handler. Adding that directive would stop Caddy from booting, including when demo mode is off. Limits are in the Calliope process. Signed-in people are not in the guest bucket, so an administrator on the same address is not throttled by visitors. Defaults are wide on purpose; tighten them only if the host is being flooded.

- Guest and anonymous `POST /api/d/*/q` — 2000 per minute (`CALLIOPE_DEMO_QUERY_RATE`) and a burst of 400 per 10 seconds (`CALLIOPE_DEMO_QUERY_BURST`)
- `POST /login` — 10 per minute (`CALLIOPE_DEMO_LOGIN_RATE`)
- `GET /demo` — 30 per minute (`CALLIOPE_DEMO_ENTER_RATE`)

`X-Forwarded-For` is used only when the immediate peer is a trusted proxy. With the calliope value unset, loopback and private ranges count, which is Compose's Caddy on the bridge. Leave `CALLIOPE_APP_TRUSTED_PROXIES` empty in that setup. Caddy has its own `CALLIOPE_TRUSTED_PROXIES` (default `127.0.0.1/32`). Add the CDN ranges there, or Caddy keeps the CDN address and every visitor shares it. Set `CALLIOPE_APP_TRUSTED_PROXIES` only when a public proxy connects to Calliope directly.

There is already a 90-second cache of identical dashboard queries per viewer (`WAREHOUSE_DASH_QUERY_CACHE_TTL_SECONDS`, single-flight). Guest results use a separate shared cache keyed by dashboard slug, published version, and the canonical saved statement (`CALLIOPE_DEMO_QUERY_CACHE_SECONDS`, default 120, allowed 60–300). Concurrent identical guest requests run in Postgres once. A guest cannot bypass it with `cache=no-store`.

Guest queries also use `CALLIOPE_DEMO_QUERY_TIMEOUT_MS` (default 5000, maximum 15000) and `CALLIOPE_DEMO_ROW_LIMIT` (default 500, maximum 2000). The client's `timeout_ms` is ignored.

## Build the images from this branch

Release images are built only from version tags. Two images change:

| Image | Why |
|---|---|
| `calliope-server` | Guest session, query bridge, gallery, provenance API, receipts widget |
| `calliope-postgres` | Migrations `0509` and `0510` are compiled into `pg_rvbbit` |

Caddy stays `caddy:2-alpine`. The mounted Caddyfile changes, and `origin` needs `CALLIOPE_DEMO_MODE` so `/` is proxied to Calliope instead of redirected. `CALLIOPE_ROOT_REDIRECT` stays `/floor` when demo mode is off.

From the repository root, on the machine that will run Compose:

```bash
docker build -f services/calliope/Dockerfile -t calliope-server:demo .
docker build -f docker/Dockerfile.rvbbit -t calliope-postgres:demo .
```

The postgres build compiles the extension and wants more than 16 GB of RAM. If the build is killed, set `CARGO_BUILD_JOBS=1` or lower Buildx `max-parallelism`. On an **existing** database, skip the image and apply the SQL files instead:

```bash
psql "$WAREHOUSE_DSN" -v ON_ERROR_STOP=1 \
  -f crates/pg_rvbbit/sql/migrations/0509_calliope_demo_guest.sql \
  -f crates/pg_rvbbit/sql/migrations/0510_calliope_demo_login.sql
```

Both files are idempotent, so a later image whose `rvbbit.migrate()` includes `0509` and `0510` can run them again. A brand-new database does not have that SQL until the postgres image contains the migrations, so a fresh install needs the rebuilt `calliope-postgres` image (bootstrap uses that image too).

Point only those two services at the local tags so the rest of the stack keeps its release tag. `CALLIOPE_VERSION` is shared by every product image; do not change it just for this.

```yaml
services:
  calliope:
    image: calliope-server:demo
  postgres:
    image: calliope-postgres:demo
  bootstrap:
    image: calliope-postgres:demo
```

`bootstrap` only needs the new postgres image when you want `migrate()` itself to install `0509` and `0510`. If you applied the SQL files, leave bootstrap on the release image.

Then, from the install directory:

```bash
docker compose up -d --no-deps --pull never calliope origin
```

Add `postgres` to that command when you actually swapped its image. `--pull never` keeps Compose from replacing the local tag with a registry pull.

Sign in as an administrator in a browser that does not have the guest cookie (a private window). The guest cookie is not an admin session; clear it or use another profile to publish dashboards. After a real sign-in, `/`, `/gallery`, and `/demo` as a login destination go to `/floor`. The gallery itself stays available at `/gallery`.

Starting with 1.2.3, `deploy/compose.yaml` passes the demo variables into `calliope`, and passes `CALLIOPE_DEMO_MODE` into `origin`. An older stock `compose.yaml` does not. On that install, keep using `compose.override.yaml` (shown above) instead of editing the released file. Set `CALLIOPE_DEMO_GUEST_PASSWORD` in that override.

## Known limit

A dashboard that builds SQL in the browser from filters or other variables will not match the saved-query allowlist, so those requests fail for guests. A later version can record a parameterized statement plus the allowed bindings instead of one exact string. That is not in this change.
