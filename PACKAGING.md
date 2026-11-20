# Installing pg_rvbbit

**Supported PostgreSQL version:** 18. (PG17 backport is tracked but not
shipped — `EXPLAIN (SEMANTIC ON)` and parts of the GROUP BY rewriter use
PG18-only APIs.)

The supported way to run the engine is a Calliope install. Standalone
`rvbbit-postgres` / `rvbbit-lens` images and `rvbbit-sql` release tarballs
are not published and are not an install path. `https://rvbbit.ai/install.sh`
only points at the Calliope installer. Engine and SQL docs live at
[rvbbit.ai](https://rvbbit.ai).

## 1. Calliope (supported)

```bash
curl -fsSL https://usecalliope.com/install.sh | bash
```

The engine image is `ghcr.io/ryrobes/calliope-postgres:<version>`, tagged
with the Calliope release ([`VERSION`](VERSION)). It is built from
[`crates/pg_rvbbit`](crates/pg_rvbbit) by
[`docker/Dockerfile.rvbbit`](docker/Dockerfile.rvbbit): Postgres 18,
`pg_rvbbit`, `rvbbit-duck`, and the first-boot catalog. The shipping
Compose project is [`deploy/compose.yaml`](deploy/compose.yaml).

From the install directory (`./calliope/current`):

```bash
set -a && . ./.env && set +a
export PGPASSWORD="$POSTGRES_PASSWORD"
psql "host=${CALLIOPE_SQL_HOST} port=${CALLIOPE_SQL_BIND##*:} dbname=rvbbit user=postgres sslmode=verify-full sslrootcert=state/postgres-tls/ca.crt" \
    -c 'SELECT rvbbit.rvbbit_version();'
```

Published SQL listens on **5432** unless `CALLIOPE_SQL_BIND` says
otherwise. The database name is `rvbbit`. The superuser password is
`POSTGRES_PASSWORD` in the private `.env` (generated per install, not
the web login). Clients outside the Compose network must use TLS; copy
only `state/postgres-tls/ca.crt`. Details:
[`deploy/README.md`](deploy/README.md).

Files under `docker/docker-compose*.yml` and
`scripts/release/build-and-push.sh` are development tooling. They are
not how you install Calliope. Product images are published by
[`.github/workflows/calliope-images.yml`](.github/workflows/calliope-images.yml);
see [`release/SHIPPING.md`](release/SHIPPING.md).

## 2. Build from source (development)

The extension crate is [`crates/pg_rvbbit`](crates/pg_rvbbit), a member
of the workspace `Cargo.toml`. These commands match the image build in
`docker/Dockerfile.rvbbit`. They install into whatever PostgreSQL 18
`pg_config` points at. This is not the supported product install.

Requirements:

- Rust stable 1.95+ (`rustup`)
- `cargo-pgrx = 0.18.0` (`cargo install --locked cargo-pgrx --version 0.18.0`)
- PostgreSQL 18 development headers (`postgresql-server-dev-18` on Debian)
- `libzstd-dev`, `libclang-dev`, `pkg-config`, `build-essential`, `protobuf-compiler`

From the repository root:

```bash
cargo pgrx init --pg18 "$(which pg_config)"
cargo pgrx install --release --pg-config "$(which pg_config)"
cargo build --release --locked --manifest-path crates/rvbbit_duck/Cargo.toml --bin rvbbit-duck
sudo cp crates/rvbbit_duck/target/release/rvbbit-duck /usr/local/bin/
```

Then add `pg_rvbbit` to `shared_preload_libraries`, restart Postgres, and
run `CREATE EXTENSION pg_rvbbit;` in the target database.

### Local development tarball

`make package` builds the development `pg-rvbbit` image from
`docker/docker-compose.yml` and extracts a tarball under `dist/`:

```bash
make package
ls dist/
# rvbbit-<workspace version>-pg18-linux-<arch>.tar.gz
```

There is no download for this archive. The name is the workspace
version in `Cargo.toml`, not the Calliope release. The unpacked
`install.sh` is `scripts/install-pg-extension.sh`. It is not
`deploy/install.sh`. It copies:

- `pg_rvbbit.so` → `$(pg_config --pkglibdir)`
- `pg_rvbbit*.control` + `pg_rvbbit--*.sql` → `$(pg_config --sharedir)/extension`
- `rvbbit-duck` → `/usr/local/bin/` (override with `DUCK_BIN_DIR=`)

The archive runs on a Debian/Ubuntu host with PostgreSQL 18 and a
matching glibc and libstdc++. Nothing else is required at runtime.

## Runtime characteristics

- **No system libs beyond glibc + libstdc++**. `libzstd`, `libduckdb`,
  ONNX Runtime, and tiktoken encodings are all statically linked /
  embedded. `ldd` on the .so and on `rvbbit-duck` returns only the
  standard system libraries.
- **`shared_preload_libraries = 'pg_rvbbit'` is mandatory**. Without it,
  `CREATE EXTENSION` succeeds but planner hooks never register and no
  parquet routing happens — silent fallback to heap scans. The
  development tarball's `install.sh` (`scripts/install-pg-extension.sh`)
  warns on missing preload.
- **`CREATE EXTENSION pg_rvbbit` requires superuser**. The control file
  sets `superuser = true`. Not usable on hosted Postgres without
  superuser access (RDS, Cloud SQL, Supabase, Neon).
- **First call to `rvbbit.embed()` downloads ~130MB** of model weights
  (`BAAI/bge-small-en-v1.5`) into `$PGDATA/rvbbit/embed_cache/`. Override
  with `RVBBIT_LOCAL_EMBED_CACHE=/path/to/dir` or per-backend
  `transport_opts.cache_dir`.
- **`rvbbit-duck` is looked up at `/usr/local/bin/rvbbit-duck`** by
  default. Override with `RVBBIT_DUCK_BIN=/path/to/binary`.
- **PGDATA paths are read from the `data_directory` GUC**. Override only
  if the `rvbbit-duck` sidecar runs in a separate mount namespace via
  `RVBBIT_PGDATA_PREFIX` + `RVBBIT_VISIBLE_PGDATA_PREFIX`.

## Upgrading

A Calliope install upgrades by running the installer again. It backs up
the database and applies `rvbbit.migrate()`. Do not replace
`calliope-postgres` with a standalone image.

**2.0.14 is the first generally-available extension release.** In-place
`ALTER EXTENSION ... UPDATE` is supported only *from 2.0.14 onward* —
the versioned `pg_rvbbit--<from>--<to>.sql` migration scripts are
installed alongside the `.control` file and Postgres walks that chain
from your installed version to the new `default_version`, preserving
database state (parquet files, judgment cache, embeddings, KG, route
decisions).

To upgrade the extension on a database you built yourself, drop in the
new development artifacts (a local tarball or a rebuild), then:

```sql
ALTER EXTENSION pg_rvbbit UPDATE;
```

**Pre-2.0.14 builds were dev/preview and have no in-place upgrade
path.** To move one to a supported release, recreate the extension:

```sql
DROP EXTENSION pg_rvbbit CASCADE;   -- drops the rvbbit schema + catalog
CREATE EXTENSION pg_rvbbit;          -- installs at default_version
```

(Parquet files under `$PGDATA/rvbbit/` survive `DROP EXTENSION`; remove
them too for a fully clean slate.)

The release tooling guarantees the chain stays intact going forward:
`scripts/release/bump-version.py` auto-creates a contiguous upgrade
stub on every version bump, and `make migration-check`
(`scripts/release/check-migration-chain.py`, also enforced before
`--push`) fails the release if any supported version cannot reach
`default_version`.

## Uninstalling

```sql
DROP EXTENSION pg_rvbbit CASCADE;
```

The `CASCADE` drops the `rvbbit` schema and all data. The parquet files
under `$PGDATA/rvbbit/` are *not* removed by `DROP EXTENSION` and must
be cleaned manually:

```bash
sudo rm -rf $PGDATA/rvbbit/
```

To also remove the binaries:

```bash
sudo rm /usr/lib/postgresql/18/lib/pg_rvbbit.so
sudo rm /usr/share/postgresql/18/extension/pg_rvbbit*
sudo rm /usr/local/bin/rvbbit-duck
```
