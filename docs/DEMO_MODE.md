# Demo mode

Demo mode lets a public Calliope install (the planned host is dataslop.com) show a few published dashboards to anonymous visitors. It is **off** unless `CALLIOPE_DEMO_MODE` is `1`, `true`, `yes`, or `on`. With the flag unset, routes, SQL, cookies, and the front door behave as they do today.

Visitors are not given a database login. The service account `SET ROLE`s to `calliope_demo_guest`, a `NOLOGIN` role, including when `WAREHOUSE_AUTH=shared`. That role can `CONNECT` and `SELECT` only in the schemas named by `CALLIOPE_DEMO_SCHEMAS` (default `public_demo`). It is not granted `USAGE` on `rvbbit` and is not granted `EXECUTE` on `rvbbit` functions. The dashboard bridge also refuses any statement that is not one of the queries recorded for that published version, ignores the client's `timeout_ms`, caps rows with a real `LIMIT`, and rejects `rvbbit` references and operator calls (`get_secret`, `classify`, `means`, `mcp_call`, `synth_sql`, and the rest of the bridge denylist) before they reach the database.

Which dashboards a guest can see is an explicit choice. Nothing is public until an administrator publishes it.

## What a guest can open

- `GET /demo` mints an HttpOnly `wh_session` cookie for the guest principal and redirects to `/gallery`.
- `GET /` redirects to `/gallery` (Caddy `CALLIOPE_ROOT_REDIRECT`), not `/floor`.
- `GET /gallery` lists only dashboards marked public.
- `GET /d/{slug}` and `GET /d/{slug}/versions/{version}` open a public dashboard. A deep link with no cookie is sent through `/demo` and back.
- `POST /api/d/{slug}/q` runs one allowlisted statement for that version.
- `GET /api/d/{slug}/provenance` returns the scrubbed build summary.
- `GET /api/gallery/metrics` returns metric names and values only. `definition_sql` is not included. Metric detail is denied.
- Static files under `/theme`, `/charts`, `/vendor`, and `/bg`.

Everything else is denied for that cookie, including `/calliope`, `/data`, `/floor`, `/team`, `/desk`, `/ops`, `/mcp`, app actions, and arbitrary SQL. A normal signed-in session is not a guest and is not subject to this list. Turn demo mode off and a leftover guest cookie is ignored.

The receipts widget on a dashboard has a **Built** tab, next to the data overlay, titled "How this was built". It shows the model names, token cost, wall time, and a short step list (tool or Hindsight calls) recorded when that version was published. Prompts, secrets, and filesystem paths are not included. Full traces are out of scope.

## Turn it on

New install: export the variables before `install.sh`, or put them in `settings.env`. `configure.py` writes them into `.env`. Off is the default (`CALLIOPE_DEMO_MODE=0`, root still `/floor`).

```bash
export CALLIOPE_DEMO_MODE=1
export CALLIOPE_DEMO_SCHEMAS=public_demo
```

When the flag is on, configure sets `CALLIOPE_ROOT_REDIRECT=/gallery`.

Existing install: edit the install's `.env` (compose reads this, not `settings.env` after the first configure):

```bash
CALLIOPE_DEMO_MODE=1
CALLIOPE_DEMO_SCHEMAS=public_demo
CALLIOPE_ROOT_REDIRECT=/gallery
```

The installed `config/Caddyfile` has to contain the placeholder. Releases copied the file at install time, so an older file still says `redir * /floor 302`. Change that one line to:

```
redir * {$CALLIOPE_ROOT_REDIRECT:/floor} 302
```

If the running `deploy/compose.yaml` does not yet pass the variables through, add this override next to it as `compose.override.yaml` (Compose loads that name automatically):

```yaml
services:
  calliope:
    environment:
      CALLIOPE_DEMO_MODE: "1"
      CALLIOPE_DEMO_SCHEMAS: public_demo
      CALLIOPE_DEMO_ROW_LIMIT: "500"
      CALLIOPE_DEMO_QUERY_TIMEOUT_MS: "5000"
  origin:
    environment:
      CALLIOPE_ROOT_REDIRECT: /gallery
```

`CALLIOPE_DEMO_SCHEMAS` is a comma-separated list of schema names. `public`, `rvbbit`, and `pg_*` are refused. Use a schema that holds only data you are willing to show.

Restart `calliope` and `origin` after the env and Caddyfile changes.

## Guest role

Migration `0509_calliope_demo_guest` creates the role and the allowlist and provenance tables. It does not grant the role anything to read. After it has run:

```sql
SELECT rvbbit.configure_demo_guest(ARRAY['public_demo']);
```

That grants `CONNECT` on the current database and `USAGE` plus `SELECT` on the named schemas, and revokes `rvbbit` again. Re-run it after you add tables. The seed script does this.

PostgreSQL cannot deny a privilege that `PUBLIC` already has without revoking it from `PUBLIC`. This migration does not touch `PUBLIC`, because that would change installs that are not in demo mode. `rvbbit` is not granted to `PUBLIC`. If an operator later runs `GRANT USAGE ON SCHEMA rvbbit TO PUBLIC`, a direct `SET ROLE` could call functions that are executable by `PUBLIC`. The bridge still rejects those statements for guest HTTP queries. Do not grant `rvbbit` to `PUBLIC`.

## Publish a dashboard

Publishing or updating a dashboard records a canonical allowlist for that version from `rvbbitQuery("...")` literals and manifest `sql` fields. Statements the bridge would reject are left off the list, so they cannot run for a guest. Existing dashboards are not public and have no list until an administrator backfills them.

HTTP, as an administrator (not the guest cookie):

```bash
curl -sS -b admin-cookies.txt -H 'content-type: application/json' \
  -d '{"public": true}' \
  https://your-host/api/gallery/artifacts/THE-SLUG/demo
```

`{"public": false}` hides it again. Only `app_kind = dashboard` can be marked public.

## Sample dashboard, no model key

From a checkout, against the install's database (the published port is in `connections.json`):

```bash
export WAREHOUSE_DSN='host=127.0.0.1 port=5432 dbname=rvbbit user=postgres password=...'
export CALLIOPE_DEMO_MODE=1
PYTHONPATH=services/calliope python3 scripts/demo/seed_public_dashboard.py
```

Or, if the migration is already applied:

```bash
psql "$WAREHOUSE_DSN" -v ON_ERROR_STOP=1 -f deploy/demo/seed_public_dashboard.sql
```

That loads `public_demo.orders` and publishes `/d/public-demo-revenue`. The build panel says the model was `fixture` and the only step was `publish_dashboard`, because no model was called. Open `https://your-host/demo`.

## Rate limits

`caddy:2-alpine` does not ship the third-party `rate_limit` handler. Adding that directive would stop Caddy from booting, including when demo mode is off. Demo mode therefore limits, per process and per client IP:

- `POST /api/d/*/q` — 30 per minute (`CALLIOPE_DEMO_QUERY_RATE`)
- `POST /login` — 10 per minute (`CALLIOPE_DEMO_LOGIN_RATE`)
- `GET /demo` — 20 per minute (`CALLIOPE_DEMO_ENTER_RATE`)

Guest queries also use `CALLIOPE_DEMO_QUERY_TIMEOUT_MS` (default 5000, maximum 15000) and `CALLIOPE_DEMO_ROW_LIMIT` (default 500, maximum 2000). The client's `timeout_ms` is ignored.

## Build the images from this branch

Release images are built only from version tags. Two images change:

| Image | Why |
|---|---|
| `calliope-server` | Guest session, query bridge, gallery, provenance API, receipts widget |
| `calliope-postgres` | Migration `0509` is compiled into `pg_rvbbit` |

Caddy stays `caddy:2-alpine`. Only the mounted Caddyfile and `CALLIOPE_ROOT_REDIRECT` change.

From the repository root, on the machine that will run Compose:

```bash
docker build -f services/calliope/Dockerfile -t calliope-server:demo .
docker build -f docker/Dockerfile.rvbbit -t calliope-postgres:demo .
```

The postgres build compiles the extension and takes a long time. On an **existing** database you can skip it and apply the migration with the current server instead:

```bash
psql "$WAREHOUSE_DSN" -v ON_ERROR_STOP=1 \
  -f crates/pg_rvbbit/sql/migrations/0509_calliope_demo_guest.sql
```

The file is idempotent, so a later image whose `rvbbit.migrate()` includes `0509` can run it again. A brand-new database does not have that SQL until the postgres image contains the migration, so a fresh install needs the rebuilt `calliope-postgres` image (bootstrap uses that image too).

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

`bootstrap` only needs the new postgres image when you want `migrate()` itself to install `0509`. If you applied the SQL file, leave bootstrap on the release image.

Then, from the install directory:

```bash
docker compose up -d --no-deps --pull never calliope origin
```

Add `postgres` to that command when you actually swapped its image. `--pull never` keeps Compose from replacing the local tag with a registry pull.

Sign in as an administrator in a browser that does not have the guest cookie (a private window). The guest cookie is not an admin session; clear it or use another profile to publish dashboards.
