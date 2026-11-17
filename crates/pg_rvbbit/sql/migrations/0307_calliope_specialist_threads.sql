-- 0307: durable, multi-round conversations with Hermes specialist agents.
--
-- A specialist thread owns one persistent Hermes session and may contain many
-- recoverable runs.  Runs are individual speaking turns; the thread is the
-- collaboration that Calliope can continue, challenge, or revisit later.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_specialist_threads (
    id uuid PRIMARY KEY,
    specialist_id uuid NOT NULL REFERENCES rvbbit.calliope_specialists(id),
    owner_email text NOT NULL,
    source_session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
    root_turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL,
    hermes_session_id text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'open',
    objective text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    round_count integer NOT NULL DEFAULT 0,
    last_run_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CONSTRAINT calliope_specialist_threads_status_check
        CHECK (status IN ('open','closed','failed')),
    CONSTRAINT calliope_specialist_threads_context_check
        CHECK (jsonb_typeof(context)='object'),
    CONSTRAINT calliope_specialist_threads_round_count_check
        CHECK (round_count >= 0)
);

CREATE INDEX IF NOT EXISTS calliope_specialist_threads_session_idx
    ON rvbbit.calliope_specialist_threads(source_session_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS calliope_specialist_threads_owner_idx
    ON rvbbit.calliope_specialist_threads(owner_email,status,updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_threads_root_identity_idx
    ON rvbbit.calliope_specialist_threads(root_turn_id,specialist_id)
    WHERE root_turn_id IS NOT NULL;

ALTER TABLE rvbbit.calliope_specialist_runs
    ADD COLUMN IF NOT EXISTS thread_id uuid
        REFERENCES rvbbit.calliope_specialist_threads(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS round_no integer NOT NULL DEFAULT 1;

ALTER TABLE rvbbit.calliope_specialist_runs
    DROP CONSTRAINT IF EXISTS calliope_specialist_runs_hermes_session_id_key;

DROP INDEX IF EXISTS rvbbit.calliope_specialist_runs_parent_identity_idx;

CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_runs_thread_round_idx
    ON rvbbit.calliope_specialist_runs(thread_id,round_no)
    WHERE thread_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_runs_thread_active_idx
    ON rvbbit.calliope_specialist_runs(thread_id)
    WHERE thread_id IS NOT NULL AND status IN ('queued','running');
CREATE INDEX IF NOT EXISTS calliope_specialist_runs_thread_created_idx
    ON rvbbit.calliope_specialist_runs(thread_id,created_at);

-- Every pre-thread handoff becomes a one-round conversation.  Keeping its
-- existing Hermes session makes the migration lossless and lets Calliope
-- continue old specialist work immediately after upgrade.
INSERT INTO rvbbit.calliope_specialist_threads (
    id,specialist_id,owner_email,source_session_id,root_turn_id,
    hermes_session_id,status,objective,context,round_count,last_run_id,
    created_at,updated_at,closed_at
)
SELECT
    gen_random_uuid(),r.specialist_id,r.owner_email,r.source_session_id,
    CASE WHEN r.root_rank=1 THEN r.source_turn_id ELSE NULL END,r.hermes_session_id,
    CASE WHEN r.status='cancelled' THEN 'closed'
         WHEN r.status='failed' THEN 'failed' ELSE 'open' END,
    r.task,r.context,1,r.id,r.created_at,
    coalesce(r.completed_at,r.created_at),
    CASE WHEN r.status='cancelled' THEN r.completed_at ELSE NULL END
FROM (
    SELECT existing.*,
           row_number() OVER (
               PARTITION BY existing.source_turn_id,existing.specialist_id
               ORDER BY existing.created_at,existing.id
           ) AS root_rank
    FROM rvbbit.calliope_specialist_runs existing
    WHERE existing.thread_id IS NULL
) r
ON CONFLICT (hermes_session_id) DO NOTHING;

UPDATE rvbbit.calliope_specialist_runs r
SET thread_id=t.id,round_no=1
FROM rvbbit.calliope_specialist_threads t
WHERE r.thread_id IS NULL AND t.hermes_session_id=r.hermes_session_id;

ALTER TABLE rvbbit.calliope_specialist_threads
    DROP CONSTRAINT IF EXISTS calliope_specialist_threads_last_run_id_fkey;
ALTER TABLE rvbbit.calliope_specialist_threads
    ADD CONSTRAINT calliope_specialist_threads_last_run_id_fkey
    FOREIGN KEY (last_run_id) REFERENCES rvbbit.calliope_specialist_runs(id)
    ON DELETE SET NULL;

COMMENT ON TABLE rvbbit.calliope_specialist_threads IS
    'Persistent multi-round Calliope-to-specialist agent conversations backed by one Hermes session.';
COMMENT ON COLUMN rvbbit.calliope_specialist_runs.round_no IS
    'One recoverable speaking turn within its persistent specialist thread.';
