-- 0372: per-notebook Team mode.
--
-- Off (default): Calliope handles requests herself — direct 1:1, no
-- reflexive delegation. On: requests are treated as projects — plan
-- first, clarify if needed, delegate scoped workstreams to specialists
-- in parallel, and own the synthesis. The flag lives on the session so
-- both modes are measurable side by side in the mirror/forensics SQL.
ALTER TABLE rvbbit.calliope_sessions
    ADD COLUMN IF NOT EXISTS team_mode boolean NOT NULL DEFAULT false;
