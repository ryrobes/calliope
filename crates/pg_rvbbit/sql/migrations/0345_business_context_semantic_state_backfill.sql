-- 0345: clear semantic structural dirtiness left by the pre-0344 mirror trigger.
--
-- These rows already have a completed Value Graph and still resolve to live
-- source relations.  They are semantic identities rather than physical catalog
-- schemas, so pending_catalog/dirty/error was legacy scheduling state, not an
-- unmodeled structural change.

WITH repairable AS (
    SELECT domain.domain_key,
           latest.workflow_run_id,
           latest.finished_at,
           rvbbit.business_context_domain_fingerprint(domain.domain_key) AS fingerprint
      FROM rvbbit.business_context_domains domain
      CROSS JOIN LATERAL (
          SELECT proposal.workflow_run_id,workflow.finished_at
            FROM rvbbit.business_value_graph_proposals proposal
            JOIN rvbbit.business_topology_workflow_runs workflow
              ON workflow.run_id=proposal.workflow_run_id
           WHERE proposal.domain_key=domain.domain_key
             AND workflow.status='completed'
           ORDER BY proposal.proposed_at DESC,proposal.graph_id DESC
           LIMIT 1
      ) latest
     WHERE domain.pending_workflow_run_id IS NULL
       AND domain.status IN ('pending_catalog','dirty','error')
       AND NOT EXISTS (
           SELECT 1
             FROM pg_namespace namespace
             JOIN pg_class relation ON relation.relnamespace=namespace.oid
            WHERE namespace.nspname=domain.domain_key
              AND relation.relkind IN ('r','p','m')
              AND NOT relation.relispartition
              AND rvbbit._business_topology_relation_allowed(
                  namespace.nspname,relation.relname
              )
       )
       AND (rvbbit.business_context_domain_snapshot(domain.domain_key)
            ->>'relation_count')::integer>0
)
UPDATE rvbbit.business_context_domains domain
   SET status='clean',observed_fingerprint=repairable.fingerprint,
       modeled_fingerprint=repairable.fingerprint,
       pending_workflow_run_id=NULL,
       last_completed_workflow_run_id=repairable.workflow_run_id,
       dirty_since=NULL,dirty_reasons='[]'::jsonb,
       last_observed_at=coalesce(domain.last_observed_at,repairable.finished_at),
       last_modeled_at=coalesce(domain.last_modeled_at,repairable.finished_at),
       last_error=NULL,updated_at=clock_timestamp()
  FROM repairable
 WHERE domain.domain_key=repairable.domain_key;

COMMENT ON TABLE rvbbit.business_context_domains IS
    'Freshness state for physical observation scopes and semantic Value Systems. Semantic identities are structurally covered through graph source_relations, not same-named schemas.';
