# Calliope

**Ask once. Keep the answer.**

Calliope is an open-source, self-hosted AI teammate for a business. Callie,
its agent, reads across your systems and answers questions about the business.
The answers worth keeping become pages, boards and small apps your team keeps
using. Everything runs on your own hardware, on copies of your data, and every
number carries the query and sources behind it.

Under the hood it is a full data stack that ships as one Compose project:

- **RVBBIT**: a PostgreSQL 18 extension for columnar acceleration, time travel
  and semantic SQL (model judgments as ordinary SQL functions).
- **Mirrors**: scheduled, read-only copies of Postgres, MySQL, SQL Server,
  Oracle and BigQuery sources, so Callie never touches live systems.
- **Meaning**: a map from tables to the business: subjects, metrics,
  definitions and gaps.
- **DataRabbit**: a SQL desktop for the people who want to see everything
  Callie sees.
- **Clover**: a self-hosted model gateway that routes every call through your
  own OpenRouter key and records what it costs.
- **MCP**: your own AI assistant can work against the same data and
  definitions.

## Install

On Linux x86-64 with Docker Engine and Compose 2.30 or newer (roughly 16 GB RAM
and 50 GB disk), and an OpenRouter key. No GPU, no Python, no account with us.

```bash
curl -fsSL https://usecalliope.com/install.sh | bash
```

The installer checks the machine, asks for your OpenRouter key, an administrator
login and how people will reach Calliope, then downloads the release, configures
it and starts it. Run it again to upgrade. To read it first, or to install by
hand from a release bundle, see [`deploy/README.md`](./deploy/README.md).

The current release is **1.2.1** ([`VERSION`](./VERSION)).

## Repository layout

| Path | Purpose |
| --- | --- |
| `services/calliope/` | Calliope web application, MCP server, setup experience and background workers |
| `datarabbit/` | DataRabbit SQL desktop and console |
| `crates/pg_rvbbit/` | RVBBIT Postgres extension and its migration chain |
| `warren/` | Local capability controller |
| `capabilities/` | Shipped runtimes, integrations and installation templates |
| `services/clover/` | Self-hosted Clover gateway and workers |
| `sidecars/` | Document and specialist services |
| `apps/` | Native desktop and mobile apps |
| `docker/` | Image definitions and the development Compose stack |
| `deploy/` | The release bundle: Compose project, configurator and install guide |

## Build from source

```bash
python3 scripts/release/shipping.py check   # versions, migration hashes and Compose agree
scripts/release/build-calliope.sh           # build every product image
python3 scripts/release/shipping.py bundle --candidate --output ./calliope-candidate
```

[`release/SHIPPING.md`](./release/SHIPPING.md) describes a release end
to end, and [`docs/RELEASE_IMAGES.md`](./docs/RELEASE_IMAGES.md) covers the
images. `release/services.json` is the single build inventory.

Calliope's version is in [`VERSION`](./VERSION). The RVBBIT extension keeps its
own forward-only version and migrations, because `pg_rvbbit`, schema `rvbbit`
and `USING rvbbit` are durable SQL contracts. The component manifest is
[`release/calliope-release.json`](./release/calliope-release.json).

## Documentation

- [`docs/RVBBIT_ENGINE.md`](./docs/RVBBIT_ENGINE.md): the Postgres engine
- [`docs/OPERATORS.md`](./docs/OPERATORS.md): semantic SQL operators
- [`docs/MCP.md`](./docs/MCP.md): connecting an assistant over MCP
- [`docs/CLOVER_SELF_HOSTED.md`](./docs/CLOVER_SELF_HOSTED.md): the model gateway
- [`docs/CAPABILITIES.md`](./docs/CAPABILITIES.md): capability packs
- [`docs/METRICS.md`](./docs/METRICS.md): metrics and definitions
- [`docs/TIME_TRAVEL.md`](./docs/TIME_TRAVEL.md): `AS OF` queries
- [`docs/WARREN.md`](./docs/WARREN.md): the capability controller

## License

Calliope is licensed under the [Apache License 2.0](./LICENSE). Third-party
components keep their own licenses; see [`NOTICE`](./NOTICE). The names
Calliope, Callie and RVBBIT and the project's artwork are covered by
[`TRADEMARKS.md`](./TRADEMARKS.md). To contribute, see
[`CONTRIBUTING.md`](./CONTRIBUTING.md).

If you'd rather have Calliope set up for your company, see
[calliopeatwork.com](https://calliopeatwork.com).
