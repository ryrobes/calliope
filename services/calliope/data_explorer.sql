-- Personal explorations are independent of conversations. Proposals and runs
-- are immutable; publication references one exact execution.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_data_drafts (
  id uuid PRIMARY KEY, owner_email text NOT NULL, question text NOT NULL,
  sources jsonb NOT NULL, proposal jsonb NOT NULL DEFAULT '{}'::jsonb,
  context jsonb NOT NULL DEFAULT '{}'::jsonb, receipts jsonb NOT NULL DEFAULT '[]'::jsonb,
  previous_id uuid REFERENCES rvbbit.calliope_data_drafts(id),
  status text NOT NULL CHECK(status IN ('building','ready','clarification','error')),
  error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calliope_data_drafts_owner_created
  ON rvbbit.calliope_data_drafts(owner_email,created_at DESC);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_data_runs (
  id uuid PRIMARY KEY, draft_id uuid NOT NULL REFERENCES rvbbit.calliope_data_drafts(id),
  owner_email text NOT NULL, result jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL CHECK(status IN ('running','complete','error')), error text,
  created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS calliope_data_runs_draft ON rvbbit.calliope_data_runs(draft_id,created_at DESC);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_data_publications (
  run_id uuid NOT NULL REFERENCES rvbbit.calliope_data_runs(id),
  session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
  surface_id uuid NOT NULL REFERENCES rvbbit.calliope_surfaces(id) ON DELETE CASCADE,
  actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(run_id,session_id)
);
