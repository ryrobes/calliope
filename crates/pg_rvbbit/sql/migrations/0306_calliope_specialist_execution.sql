-- 0306: bind specialist handoffs to Hermes' durable run lifecycle.

ALTER TABLE rvbbit.calliope_specialist_runs
    ADD COLUMN IF NOT EXISTS hermes_run_id text,
    ADD COLUMN IF NOT EXISTS hermes_message_offset integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS runner_id text,
    ADD COLUMN IF NOT EXISTS runner_attempt integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_runs_hermes_run_idx
    ON rvbbit.calliope_specialist_runs(hermes_run_id)
    WHERE hermes_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS calliope_specialist_runs_lease_idx
    ON rvbbit.calliope_specialist_runs(lease_expires_at)
    WHERE status='running' AND hermes_run_id IS NOT NULL;

-- One parent turn has one durable conversation with a given specialist. A
-- failed/cancelled handoff may be retried; reconnects reuse running/completed
-- work even when the model paraphrases the assignment.
CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_runs_parent_identity_idx
    ON rvbbit.calliope_specialist_runs(source_turn_id,specialist_id)
    WHERE source_turn_id IS NOT NULL AND status IN ('running','complete');
