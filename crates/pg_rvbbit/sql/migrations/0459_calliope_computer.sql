-- One persistent computer per installation; tasks retain separate receipts.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_desktops (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE DEFAULT 'default',
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO rvbbit.calliope_desktops (name) VALUES ('default') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS rvbbit.calliope_desktop_runs (
    id uuid PRIMARY KEY,
    desktop_id uuid NOT NULL REFERENCES rvbbit.calliope_desktops(id),
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id),
    requester text NOT NULL,
    goal text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    outcome text,
    runtime_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    CONSTRAINT calliope_desktop_runs_status_check CHECK
        (status IN ('queued','active','finished','interrupted','failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_desktop_runs_session_live
    ON rvbbit.calliope_desktop_runs(session_id) WHERE status IN ('queued','active');
CREATE INDEX IF NOT EXISTS calliope_desktop_runs_desktop
    ON rvbbit.calliope_desktop_runs(desktop_id,created_at DESC);

-- Keep historical workbench evidence immutable and readable. New receipts use
-- the same append-only stream, with exactly one parent of either generation.
ALTER TABLE rvbbit.calliope_workbench_events ALTER COLUMN workbench_id DROP NOT NULL;
ALTER TABLE rvbbit.calliope_workbench_events
    ADD COLUMN IF NOT EXISTS desktop_run_id uuid REFERENCES rvbbit.calliope_desktop_runs(id);
ALTER TABLE rvbbit.calliope_workbench_events ADD CONSTRAINT calliope_computer_event_parent
    CHECK (num_nonnulls(workbench_id,desktop_run_id)=1);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_computer_event_sequence
    ON rvbbit.calliope_workbench_events(desktop_run_id,sequence) WHERE desktop_run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_computer_event_idempotency
    ON rvbbit.calliope_workbench_events(desktop_run_id,idempotency_key) WHERE desktop_run_id IS NOT NULL;

ALTER TABLE rvbbit.calliope_workbench_recordings ALTER COLUMN workbench_id DROP NOT NULL;
ALTER TABLE rvbbit.calliope_workbench_recordings
    ADD COLUMN IF NOT EXISTS desktop_run_id uuid REFERENCES rvbbit.calliope_desktop_runs(id);
ALTER TABLE rvbbit.calliope_workbench_recordings ADD CONSTRAINT calliope_computer_recording_parent
    CHECK (num_nonnulls(workbench_id,desktop_run_id)=1);
CREATE INDEX IF NOT EXISTS calliope_computer_recordings_run
    ON rvbbit.calliope_workbench_recordings(desktop_run_id,created_at);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_desktop_lessons (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES rvbbit.calliope_desktop_runs(id),
    actor_email text NOT NULL,
    feedback text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','retained','failed')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    retention_result jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
