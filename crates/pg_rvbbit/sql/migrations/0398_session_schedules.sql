-- 0398: session schedules — the thread-with-a-heartbeat triad, bound.
--
-- A user standing piece is playbook (what) + thread (accumulated context)
-- + heartbeat (when). This table is the canon binding: desired state that
-- the Hermes gateway supervisor converges into the default cron store
-- (calliope's /opt/data mount is read-only, and jobs.json belongs to the
-- Hermes process — so canon declares, the supervisor reconciles, and
-- hermes_job_id is the ack). Runs execute in their own sidecar sessions
-- (Hermes crons always start fresh); the mirror delivers each run's final
-- summary back to the thread as an agent_message turn, and the run prompt
-- reads the thread's recent operator messages for steering — so talking on
-- the thread between runs IS the steering channel, no extra machinery.
--
-- One enabled heartbeat per thread: the drag-to-Standing gesture and the
-- "make this nightly" ask both mean *this thread*, singular.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_session_schedules (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    owner_email   text NOT NULL,
    playbook_id   uuid REFERENCES rvbbit.calliope_playbooks(id) ON DELETE SET NULL,
    playbook_version integer,
    mission       text NOT NULL DEFAULT '',
    schedule      text NOT NULL,
    prompt        text NOT NULL,
    job_name      text NOT NULL UNIQUE,
    hermes_job_id text,
    enabled       boolean NOT NULL DEFAULT true,
    last_delivered_at timestamptz,
    created_by    text NOT NULL DEFAULT 'calliope',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_session_schedules_one_live_idx
    ON rvbbit.calliope_session_schedules (session_id) WHERE enabled;
CREATE INDEX IF NOT EXISTS calliope_session_schedules_job_idx
    ON rvbbit.calliope_session_schedules (hermes_job_id) WHERE hermes_job_id IS NOT NULL;
