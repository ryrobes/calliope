-- Generated definitions are not routable until their Hermes profile responds.
ALTER TABLE rvbbit.calliope_specialists DROP CONSTRAINT calliope_specialists_status_check;
ALTER TABLE rvbbit.calliope_specialists ADD CONSTRAINT calliope_specialists_status_check
    CHECK (status IN ('active','paused','disabled','provisioning'));
ALTER TABLE rvbbit.generated_specialists
    ADD COLUMN IF NOT EXISTS profile_ready boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS profile_checked_at timestamptz,
    ADD COLUMN IF NOT EXISTS profile_error text;
CREATE OR REPLACE FUNCTION rvbbit.guard_generated_specialist_readiness()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='active' AND EXISTS (
        SELECT 1 FROM rvbbit.generated_specialists g
        WHERE g.specialist_key=NEW.specialist_key AND NOT g.profile_ready
    ) THEN
        NEW.status := 'provisioning';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS generated_specialist_readiness ON rvbbit.calliope_specialists;
CREATE TRIGGER generated_specialist_readiness BEFORE INSERT OR UPDATE ON rvbbit.calliope_specialists
    FOR EACH ROW EXECUTE FUNCTION rvbbit.guard_generated_specialist_readiness();
-- Preserve explicit pauses/disables. Existing active agents await one readiness probe.
UPDATE rvbbit.calliope_specialists s SET status='provisioning',updated_at=now()
FROM rvbbit.generated_specialists g WHERE g.specialist_key=s.specialist_key
    AND s.status='active' AND NOT g.profile_ready;

-- An existing definition awaiting Hermes should be provisioned, not minted again.
CREATE OR REPLACE VIEW rvbbit.domain_agent_candidates AS
SELECT summary.family_id, summary.domain_key, summary.name,
       summary.family_revision, summary.graph_id,
       (SELECT count(*) FROM rvbbit.business_semantic_assertions a
         WHERE a.family_id=summary.family_id) AS assertions
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.status='promoted'
   AND summary.validated_metric_count > 0
   AND replace(summary.domain_key,'_','-') NOT IN (
       SELECT specialist_key FROM rvbbit.calliope_specialists
        WHERE status='active')
   AND NOT EXISTS (
       SELECT 1 FROM rvbbit.generated_specialists g
        WHERE g.domain_key=summary.domain_key);
