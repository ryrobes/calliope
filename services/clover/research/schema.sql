CREATE SCHEMA IF NOT EXISTS clover_research;

CREATE TABLE IF NOT EXISTS clover_research.web_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    tenant_scope     TEXT NOT NULL,
    requested_url    TEXT NOT NULL,
    final_url        TEXT NOT NULL,
    title            TEXT NOT NULL,
    format           TEXT,
    published_date   TEXT,
    content_sha256   TEXT NOT NULL,
    etag             TEXT,
    last_modified    TEXT,
    markdown         TEXT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_checked_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_scope, requested_url, content_sha256)
);
CREATE INDEX IF NOT EXISTS web_snapshots_latest_idx
    ON clover_research.web_snapshots (tenant_scope, requested_url, last_checked_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS clover_research.research_runs (
    id            BIGSERIAL PRIMARY KEY,
    tenant_scope  TEXT NOT NULL,
    mode          TEXT NOT NULL,
    question      TEXT NOT NULL,
    watch_id      TEXT,
    result_json   JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS research_runs_scope_idx
    ON clover_research.research_runs (tenant_scope, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS clover_research.research_run_sources (
    run_id            BIGINT NOT NULL REFERENCES clover_research.research_runs(id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL,
    snapshot_id       BIGINT NOT NULL REFERENCES clover_research.web_snapshots(id),
    discovered_by     TEXT NOT NULL,
    cache_status      TEXT NOT NULL,
    selected_chars    INTEGER NOT NULL,
    evidence_excerpt  TEXT NOT NULL,
    evidence_text     TEXT NOT NULL,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS clover_research.research_claims (
    run_id             BIGINT NOT NULL REFERENCES clover_research.research_runs(id) ON DELETE CASCADE,
    ordinal            INTEGER NOT NULL,
    claim              TEXT NOT NULL,
    confidence         DOUBLE PRECISION NOT NULL,
    source_ids_json    JSONB NOT NULL,
    snapshot_ids_json  JSONB NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS clover_research.research_watches (
    tenant_scope     TEXT NOT NULL,
    watch_id         TEXT NOT NULL,
    question         TEXT NOT NULL,
    last_run_id      BIGINT NOT NULL REFERENCES clover_research.research_runs(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_checked_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_scope, watch_id)
);
