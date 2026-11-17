-- Living Pages: durable editorial intent and normal Hermes turn provenance.
ALTER TABLE rvbbit.calliope_pages
 ADD COLUMN IF NOT EXISTS standing_brief text NOT NULL DEFAULT '',
 ADD COLUMN IF NOT EXISTS definition_version integer NOT NULL DEFAULT 1,
 ADD COLUMN IF NOT EXISTS session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_pages_session_idx
 ON rvbbit.calliope_pages(session_id) WHERE session_id IS NOT NULL;
ALTER TABLE rvbbit.calliope_page_runs
 ADD COLUMN IF NOT EXISTS client_turn_id uuid,
 ADD COLUMN IF NOT EXISTS session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
 ADD COLUMN IF NOT EXISTS definition_version integer,
 ADD COLUMN IF NOT EXISTS base_revision_id uuid,
 ADD COLUMN IF NOT EXISTS context jsonb NOT NULL DEFAULT '{}'::jsonb,
 ADD COLUMN IF NOT EXISTS outcome_summary text;
CREATE UNIQUE INDEX IF NOT EXISTS calliope_page_runs_client_idx
 ON rvbbit.calliope_page_runs(client_turn_id) WHERE client_turn_id IS NOT NULL;

-- Preserve operator credit choices. An updated Page defaults to the existing
-- synthesized-document estimate; verification is counted without invented savings.
DO $pages$ DECLARE current_view text;
BEGIN
 IF to_regclass('rvbbit.work_ledger_credits') IS NOT NULL THEN
  INSERT INTO rvbbit.work_ledger_credits(class,label,minutes_each,sort) VALUES
   ('page_revision','Living Pages updated',coalesce((SELECT minutes_each FROM rvbbit.work_ledger_credits WHERE class='document'),15),42),
   ('page_verified','Living Pages verified current',0,43)
  ON CONFLICT(class) DO NOTHING;
 END IF;
 IF to_regclass('rvbbit.work_ledger_v') IS NOT NULL THEN
  SELECT pg_get_viewdef('rvbbit.work_ledger_v'::regclass,true) INTO current_view;
  IF position('calliope_page_runs' in current_view)=0 THEN
   EXECUTE 'CREATE OR REPLACE VIEW rvbbit.work_ledger_v AS ' || rtrim(current_view, E';\n ') ||
    ' UNION ALL SELECT CASE WHEN r.status=''complete'' THEN ''page_revision'' ELSE ''page_verified'' END,
       r.requested_by,r.completed_at,r.id::text,r.session_id::text
      FROM rvbbit.calliope_page_runs r
      WHERE r.client_turn_id IS NOT NULL AND r.status IN (''complete'',''unchanged'')';
  END IF;
 END IF;
END $pages$;
