-- 0349: adopt a completed semantic graph as the first context fingerprint.
--
-- A Business Context crawl may first observe a semantic Value System after its
-- topology workflow has completed.  In that ordering, the domain already has a
-- completed graph and last_completed_workflow_run_id, but modeled_fingerprint
-- remains NULL.  Calling it dirty immediately queues a redundant per-system
-- excavation.  The first observation is the baseline for that completed graph;
-- subsequent fingerprint changes still use the normal dirty/refresh path.

CREATE OR REPLACE FUNCTION rvbbit._business_context_adopt_semantic_baseline()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.modeled_fingerprint IS NULL
       AND NEW.observed_fingerprint IS NOT NULL
       AND NEW.last_completed_workflow_run_id IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM rvbbit.business_value_graph_proposals proposal
            WHERE proposal.domain_key=NEW.domain_key
              AND proposal.workflow_run_id=NEW.last_completed_workflow_run_id
       ) THEN
        NEW.modeled_fingerprint := NEW.observed_fingerprint;
        NEW.status := 'clean';
        NEW.dirty_since := NULL;
        NEW.dirty_reasons := '[]'::jsonb;
        NEW.last_modeled_at := coalesce(NEW.last_modeled_at,clock_timestamp());
        NEW.last_error := NULL;
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_adopt_semantic_baseline
    ON rvbbit.business_context_domains;
CREATE TRIGGER business_context_adopt_semantic_baseline
BEFORE INSERT OR UPDATE ON rvbbit.business_context_domains
FOR EACH ROW
EXECUTE FUNCTION rvbbit._business_context_adopt_semantic_baseline();

UPDATE rvbbit.business_context_domains domain
   SET modeled_fingerprint=domain.observed_fingerprint,
       status='clean',dirty_since=NULL,dirty_reasons='[]'::jsonb,
       last_modeled_at=coalesce(domain.last_modeled_at,clock_timestamp()),
       last_error=NULL,updated_at=clock_timestamp()
 WHERE domain.modeled_fingerprint IS NULL
   AND domain.observed_fingerprint IS NOT NULL
   AND domain.last_completed_workflow_run_id IS NOT NULL
   AND EXISTS (
       SELECT 1
         FROM rvbbit.business_value_graph_proposals proposal
        WHERE proposal.domain_key=domain.domain_key
          AND proposal.workflow_run_id=domain.last_completed_workflow_run_id
   );

COMMENT ON FUNCTION rvbbit._business_context_adopt_semantic_baseline() IS
    'Adopt the first observed fingerprint for a semantic Value System that already has a graph from its completed topology workflow.';
