-- A Page can be authored in the requesting chat while retaining its own notebook.
ALTER TABLE rvbbit.calliope_page_runs
 ADD COLUMN IF NOT EXISTS source_turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_page_runs_source_turn_idx
 ON rvbbit.calliope_page_runs(page_id,source_turn_id) WHERE source_turn_id IS NOT NULL;
ALTER TABLE rvbbit.calliope_pages ADD COLUMN IF NOT EXISTS creation_key text;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_pages_creation_key_idx
 ON rvbbit.calliope_pages(owner_email,creation_key) WHERE creation_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS calliope_page_runs_source_session_idx
 ON rvbbit.calliope_page_runs(session_id,source_turn_id) WHERE source_turn_id IS NOT NULL;
