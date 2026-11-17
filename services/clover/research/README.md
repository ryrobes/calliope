# Clover Research

CPU orchestration for public-web research: OpenRouter/Exa source discovery,
independent Clover Web extraction, cited synthesis, bounded deeper searches,
and cheap change watches. Imported from the existing Clover Research v0.2
implementation; v0.3 stores evidence in Calliope Postgres.

Use [the installation guide](../../../docs/CLOVER_SELF_HOSTED.md#web-research-with-postgres-evidence).
No GPU or SQLite runtime. The service listens internally on
8092 and is reached through Hutch's authenticated `/b/web_research/predict`.

Required environment:

- `OPENROUTER_API_KEY`
- `CLOVER_RESEARCH_DSN` (restricted role; apply `schema.sql` via the bootstrap first)
- `CLOVER_RESEARCH_SCRAPE_URL` (Compose sets `http://clover-web:8091/v1/scrape`)

Optional `CLOVER_RESEARCH_MODEL` defaults to `openai/gpt-6-luna`. Other bounds
are documented in `src/config.rs`. `/health` checks Postgres; `/metrics` exposes
worker counters. The gateway forwards an opaque server-generated tenant scope,
which is mandatory. Do not publish the worker directly to untrusted clients.

Example Predict input (maximum two items by default):

```json
{"inputs":[{"question":"What changed in this policy?","seed_urls":["https://example.com/policy"],"mode":"watch"}]}
```

Results include `answer_markdown`, sourced `claims`, `knowledge`, `sources`,
`changes`, `trail`, `usage`, and `memory`. IDs such as `R123` and `P456` identify
a saved run and source snapshot in this installation. Watches are checked on
invocation, not scheduled by this worker; use the existing SQL scheduler.

## Tests

```bash
cargo test --manifest-path services/clover/research/Cargo.toml
cargo clippy --manifest-path services/clover/research/Cargo.toml --all-targets -- -D warnings
```

For the four real Postgres tests, create an expendable database, prepare/apply
`research-bootstrap.sql`, put its restricted role DSN in
`CLOVER_RESEARCH_TEST_DSN`, then run the same tests with `-- --include-ignored`.
Tests use unique tenants and mock model/extractor servers; no paid inference.
They cover exact evidence/claim persistence, failed-write atomicity, concurrent
watch commits, tenant isolation, reconnect reuse, watch invalidation, and deep
mode follow-up. `scripts/clover/test_research.py` additionally verifies role
privileges with `CLOVER_RESEARCH_ADMIN_TEST_DSN` in an expendable database.
