-- User-authored decisions. No automatic discovery, promotion, or sample seeds.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_business_logic (
 id uuid PRIMARY KEY,
 owner_email text NOT NULL,
 name text NOT NULL,
 topic text NOT NULL DEFAULT '',
 version integer NOT NULL,
 definition jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calliope_business_logic_owner
 ON rvbbit.calliope_business_logic(owner_email, updated_at DESC);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_business_logic_versions (
 logic_id uuid NOT NULL REFERENCES rvbbit.calliope_business_logic(id),
 version integer NOT NULL,
 definition jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(logic_id, version)
);
REVOKE ALL ON rvbbit.calliope_business_logic FROM PUBLIC;
REVOKE ALL ON rvbbit.calliope_business_logic_versions FROM PUBLIC;

ALTER TABLE rvbbit.calliope_business_logic ADD COLUMN IF NOT EXISTS rule_key text;
ALTER TABLE rvbbit.calliope_business_logic ADD COLUMN IF NOT EXISTS published_version integer;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_business_logic_key ON rvbbit.calliope_business_logic(rule_key);
ALTER TABLE rvbbit.calliope_business_logic_versions ADD COLUMN IF NOT EXISTS published_at timestamptz;
CREATE TABLE IF NOT EXISTS rvbbit.calliope_business_logic_runs (
 id uuid PRIMARY KEY,
 owner_email text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 receipt jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS calliope_business_logic_runs_owner ON rvbbit.calliope_business_logic_runs(owner_email,created_at DESC);
REVOKE ALL ON rvbbit.calliope_business_logic_runs FROM PUBLIC;

-- Private references to per-invocation accounting; no copy of source evidence.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_business_logic_usage (
 id uuid PRIMARY KEY,
 owner_email text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 rows jsonb NOT NULL
);
REVOKE ALL ON rvbbit.calliope_business_logic_usage FROM PUBLIC;
