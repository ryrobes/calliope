-- 0399: work-item engagement — "have Callie start without me."
--
-- The async half of the Floor's triage contract: engaging an Awaiting You
-- item mints a session, ignites it through the agent mailbox (the note IS
-- the visible first message of the born chat), and stamps the item so it
-- leaves the sacred column immediately — the board never shows Callie's
-- in-flight work as still awaiting a human. Also the backend the
-- awaiting→in_motion drag will call when drags land.

ALTER TABLE rvbbit.calliope_work_items
    ADD COLUMN IF NOT EXISTS engaged_session_id uuid
        REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS engaged_at timestamptz;
CREATE INDEX IF NOT EXISTS calliope_work_items_engaged_idx
    ON rvbbit.calliope_work_items (engaged_session_id)
    WHERE engaged_session_id IS NOT NULL;
