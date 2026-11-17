-- 0305: durable execution ownership for backend-run Calliope turns.
--
-- Hermes /v1/runs owns the actual agent lifetime.  These columns bind that
-- run to the notebook turn and let any healthy Calliope server determine
-- whether it should observe, recover, or leave an active executor alone.

ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS hermes_run_id text;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS hermes_message_offset integer;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS runner_id text;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS runner_attempt integer NOT NULL DEFAULT 0;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS run_started_at timestamptz;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;

CREATE INDEX IF NOT EXISTS calliope_turns_active_execution_idx
    ON rvbbit.calliope_turns(lease_expires_at,created_at)
    WHERE status='running';

CREATE UNIQUE INDEX IF NOT EXISTS calliope_turns_hermes_run_idx
    ON rvbbit.calliope_turns(hermes_run_id)
    WHERE hermes_run_id IS NOT NULL;

COMMENT ON COLUMN rvbbit.calliope_turns.hermes_run_id IS
    'Hermes /v1/runs execution id; polling it never repeats the user turn.';
COMMENT ON COLUMN rvbbit.calliope_turns.hermes_message_offset IS
    'Hermes session message count before this run, used for exact transcript recovery.';
COMMENT ON COLUMN rvbbit.calliope_turns.lease_expires_at IS
    'Short observer lease; expiry permits another Calliope process to recover the same Hermes run.';
