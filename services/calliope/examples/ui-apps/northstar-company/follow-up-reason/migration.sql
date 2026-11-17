-- Additive only. No backfill and no default. Existing v2 INSERT column lists still work.
ALTER TABLE calliope_apps.northstar_reports ADD COLUMN IF NOT EXISTS follow_up_reason text CHECK (follow_up_reason IN ('No follow-up','Parts needed','Return diagnostic visit','Customer unavailable','Other'));
