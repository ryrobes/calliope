-- Shared requests remain individual Calliope turns. This is their review state,
-- plus private, reusable author test fixtures, not a second ticket registry.
CREATE TABLE IF NOT EXISTS rvbbit.app_request_reviews (
  turn_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
  revision integer NOT NULL DEFAULT 0,
  owner_status text NOT NULL DEFAULT 'new',
  requester_status text NOT NULL DEFAULT '',
  release_key text NOT NULL DEFAULT '',
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.app_preview_scenarios (
  id uuid PRIMARY KEY,
  owner_email text NOT NULL,
  slug text NOT NULL,
  name text NOT NULL,
  request_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
  version integer NOT NULL,
  definition_sha256 text NOT NULL,
  data jsonb NOT NULL,
  checks jsonb NOT NULL DEFAULT '[]',
  provenance jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_preview_scenarios_owner ON rvbbit.app_preview_scenarios(lower(owner_email),slug);
CREATE TABLE IF NOT EXISTS rvbbit.app_preview_checks (
  id uuid PRIMARY KEY,
  owner_email text NOT NULL,
  slug text NOT NULL,
  scenario_id uuid REFERENCES rvbbit.app_preview_scenarios(id) ON DELETE CASCADE,
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON rvbbit.app_request_reviews,rvbbit.app_preview_scenarios,rvbbit.app_preview_checks FROM PUBLIC;
