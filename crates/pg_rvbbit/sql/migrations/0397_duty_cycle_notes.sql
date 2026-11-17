-- 0397: duty-cycle notes — the standing pieces grow an ear.
--
-- "Leave a note for the night shift": operator guidance written from a
-- standing piece's drawer on the Floor, read by the job at the start of its
-- next run (the reconciler appends a read-notes clause to every managed
-- cron prompt), then marked consumed by the run itself. This is rung 2 of
-- the thread-with-a-heartbeat ladder: rung 1 links runs to their mirrored
-- transcripts (hermes_cron_executions.hermes_session_id already exists);
-- rung 3 — runs as turns on a real thread driven by an approved playbook —
-- arrives with the schedule-from-chat flow.
--
-- Keyed by (profile_key, job_name) for the reading side because a job's
-- name is stable across recreation while its id is not; job_id is retained
-- for the Floor's route join.

CREATE TABLE IF NOT EXISTS rvbbit.duty_cycle_notes (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_key  text NOT NULL,
    job_id       text,
    job_name     text NOT NULL,
    note         text NOT NULL,
    author_email text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    consumed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS duty_cycle_notes_open_idx
    ON rvbbit.duty_cycle_notes (profile_key, lower(job_name))
    WHERE consumed_at IS NULL;
