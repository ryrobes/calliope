-- 0344: reconcile physical steward scopes with semantic Value Systems.
--
-- A steward workflow is queued for a physical schema, but 0343 deliberately
-- emits one or more semantic domain keys.  The old completion trigger expected
-- those identities to be equal, left the physical scope unmodeled, and then
-- synchronously launched the next apparently-dirty workflow.  Periodic mirrors
-- also marked every semantic result structurally dirty every fifteen minutes.
--
-- Physical schemas remain catalog observation and workflow execution scopes.
-- Semantic Value Systems remain the durable modeled identities.  Coverage is
-- the explicit source_relations edge between them.

CREATE OR REPLACE FUNCTION rvbbit._business_context_workflow_changed()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_pending_domains text[];
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NULL; END IF;
    IF NEW.status='running' THEN
        UPDATE rvbbit.business_context_domains
           SET status='running',updated_at=clock_timestamp()
         WHERE pending_workflow_run_id=NEW.run_id;
    ELSIF NEW.status='completed' THEN
        -- Capture the physical execution scopes before the semantic finalizer
        -- clears or errors rows whose key is not itself a graph domain key.
        SELECT array_agg(domain.domain_key ORDER BY domain.domain_key)
          INTO v_pending_domains
          FROM rvbbit.business_context_domains domain
         WHERE domain.pending_workflow_run_id=NEW.run_id;

        PERFORM rvbbit.business_context_finalize_workflow(NEW.run_id);

        UPDATE rvbbit.business_context_domains domain
           SET status='clean',
               modeled_fingerprint=coalesce(
                   domain.observed_fingerprint,domain.modeled_fingerprint
               ),
               pending_workflow_run_id=NULL,
               last_completed_workflow_run_id=NEW.run_id,
               dirty_since=NULL,dirty_reasons='[]'::jsonb,
               last_modeled_at=clock_timestamp(),last_error=NULL,
               updated_at=clock_timestamp()
         WHERE domain.domain_key=ANY(coalesce(v_pending_domains,ARRAY[]::text[]))
           AND EXISTS (
               SELECT 1
                 FROM rvbbit.business_value_graph_proposals proposal
                WHERE proposal.workflow_run_id=NEW.run_id
                  AND (
                      proposal.domain_key=domain.domain_key
                      OR EXISTS (
                          SELECT 1
                            FROM unnest(proposal.source_relations) source_relation
                           WHERE split_part(source_relation,'.',1)=domain.domain_key
                      )
                  )
           );
        -- The five-minute steward cron owns scheduling the next unit.  Do not
        -- hide this terminal state by starting another workflow in this trigger.
    ELSIF NEW.status IN ('failed','cancelled') THEN
        UPDATE rvbbit.business_context_domains
           SET status='error',pending_workflow_run_id=NULL,
               last_error=left(coalesce(NEW.error,'Topology workflow was cancelled.'),2000),
               updated_at=clock_timestamp()
         WHERE pending_workflow_run_id=NEW.run_id;
    END IF;
    RETURN NULL;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain text;
    v_finished_at timestamptz;
    v_reason jsonb;
BEGIN
    IF NEW.status IN ('succeeded','partial')
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT destination_schema INTO v_domain
          FROM rvbbit.mirror_jobs WHERE job_name=NEW.job_name;
        IF v_domain IS NULL OR NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname=v_domain AND c.relkind IN ('r','p','m')
               AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
        ) THEN
            RETURN NULL;
        END IF;
        v_finished_at := coalesce(NEW.finished_at,clock_timestamp());
        v_reason := jsonb_build_object(
            'job_name',NEW.job_name,'status',NEW.status,
            'tables_succeeded',NEW.tables_succeeded,
            'tables_failed',NEW.tables_failed,'rows_loaded',NEW.rows_loaded
        );

        -- The physical destination needs a cheap catalog observation so a real
        -- structural change can be fingerprinted.  Semantic graph identities
        -- do not: their source structure is covered by that physical scope.
        PERFORM rvbbit.business_context_mark_domain_dirty(
            v_domain,'mirror',NEW.run_id::text,v_reason
        );

        IF NEW.tables_succeeded>0 THEN
            -- Data-only change is tracked for both the physical source and each
            -- Value System that consumes it.  The existing quiet/min-interval
            -- data refresh tick, rather than every mirror, decides when to model.
            UPDATE rvbbit.business_context_domains domain
               SET data_dirty_since=coalesce(domain.data_dirty_since,v_finished_at),
                   last_mirror_data_at=v_finished_at,updated_at=clock_timestamp()
             WHERE domain.domain_key=v_domain OR EXISTS (
                 SELECT 1
                   FROM rvbbit.business_value_graph_proposals proposal
                  WHERE proposal.domain_key=domain.domain_key
                    AND EXISTS (
                        SELECT 1
                          FROM unnest(proposal.source_relations) source_relation
                         WHERE split_part(source_relation,'.',1)=v_domain
                    )
             );
        END IF;
    END IF;
    RETURN NULL;
END
$fn$;

-- Repair physical source scopes already covered by a successful semantic run.
-- Do not disturb a currently queued/running scope; its new completion trigger
-- will reconcile status, while this backfill gives it the missing baseline.
UPDATE rvbbit.business_context_domains domain
   SET modeled_fingerprint=domain.observed_fingerprint,
       status=CASE WHEN domain.pending_workflow_run_id IS NULL
                   THEN 'clean' ELSE domain.status END,
       dirty_since=CASE WHEN domain.pending_workflow_run_id IS NULL
                        THEN NULL ELSE domain.dirty_since END,
       dirty_reasons=CASE WHEN domain.pending_workflow_run_id IS NULL
                          THEN '[]'::jsonb ELSE domain.dirty_reasons END,
       last_modeled_at=coalesce(domain.last_modeled_at,workflow.finished_at),
       last_error=CASE WHEN domain.pending_workflow_run_id IS NULL
                       THEN NULL ELSE domain.last_error END,
       updated_at=clock_timestamp()
  FROM rvbbit.business_topology_workflow_runs workflow
 WHERE domain.modeled_fingerprint IS NULL
   AND domain.observed_fingerprint IS NOT NULL
   AND domain.last_completed_workflow_run_id=workflow.run_id
   AND workflow.status='completed'
   AND EXISTS (
       SELECT 1
         FROM rvbbit.business_value_graph_proposals proposal
        WHERE proposal.workflow_run_id=workflow.run_id
          AND (
              proposal.domain_key=domain.domain_key
              OR EXISTS (
                  SELECT 1
                    FROM unnest(proposal.source_relations) source_relation
                   WHERE split_part(source_relation,'.',1)=domain.domain_key
              )
          )
   );

COMMENT ON FUNCTION rvbbit._business_context_workflow_changed() IS
    'Finalize semantic Value Systems, acknowledge their physical source scopes, and leave subsequent steward scheduling to cron.';
COMMENT ON FUNCTION rvbbit._business_context_mirror_finished() IS
    'Observe physical mirror structure immediately while debouncing data-only refresh for every semantic Value System that consumes it.';
