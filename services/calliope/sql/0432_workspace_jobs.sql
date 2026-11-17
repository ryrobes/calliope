-- Deterministic SQL ETL. Runtime-owned and applied on startup/first use;
-- no extension binary upgrade or pg_cron/Hermes dependency.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_workspace_jobs (
    id uuid PRIMARY KEY,
    owner_email text NOT NULL,
    session_id uuid NOT NULL,
    name text NOT NULL,
    sql text NOT NULL,
    sql_sha256 text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    interval_seconds integer NOT NULL DEFAULT 0 CHECK (interval_seconds = 0 OR interval_seconds >= 60),
    enabled boolean NOT NULL DEFAULT false,
    run_requested boolean NOT NULL DEFAULT false,
    next_run_at timestamptz,
    last_status text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_email, name)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_workspace_job_runs (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES rvbbit.calliope_workspace_jobs(id),
    revision integer NOT NULL,
    session_id uuid NOT NULL,
    sql text NOT NULL,
    sql_sha256 text NOT NULL,
    trigger text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'complete', 'failed', 'interrupted')),
    action_run_id uuid,
    error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_workspace_job_one_running
    ON rvbbit.calliope_workspace_job_runs(job_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS calliope_workspace_job_run_history
    ON rvbbit.calliope_workspace_job_runs(job_id, started_at DESC);

-- Human-readable metadata and direct admin controls, separate from execution SQL.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_job_descriptions (
    kind text NOT NULL CHECK(kind IN ('sql','mirror')),
    job_key text NOT NULL,
    description text NOT NULL CHECK(length(description)<=1000),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(kind,job_key)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_job_controls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor text NOT NULL,
    kind text NOT NULL,
    job_key text NOT NULL,
    operation text NOT NULL,
    previous_enabled boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
