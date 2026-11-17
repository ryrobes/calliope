-- 0346: make Business Topology operable as a durable product surface.
--
-- * permit one running excavation plus a bounded targeted queue;
-- * expose a deduplicated current topology independently from run receipts;
-- * keep semantic family identity stable when a model renames the same exact
--   source constellation on a later observation.

DROP INDEX IF EXISTS rvbbit.business_topology_workflow_runs_one_active_idx;
CREATE UNIQUE INDEX IF NOT EXISTS business_topology_workflow_runs_one_running_idx
    ON rvbbit.business_topology_workflow_runs ((true))
    WHERE status IN ('running','cancel_requested');

CREATE OR REPLACE FUNCTION rvbbit.business_topology_claim_workflow(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 300
) RETURNS TABLE (
    run_id uuid,
    run_name text,
    parameters jsonb,
    progress jsonb,
    requested_by text,
    attempts integer
)
LANGUAGE plpgsql VOLATILE
AS $fn$
BEGIN
    IF nullif(btrim(p_worker_id),'') IS NULL THEN
        RAISE EXCEPTION 'worker_id is required';
    END IF;
    IF p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
        RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business-topology-workflow-runner',0
    ));
    IF EXISTS (
        SELECT 1 FROM rvbbit.business_topology_workflow_runs
         WHERE status IN ('running','cancel_requested')
           AND NOT (
               status='running' AND lease_expires_at<clock_timestamp()
           )
    ) THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH picked AS (
        SELECT workflow.run_id
          FROM rvbbit.business_topology_workflow_runs workflow
         WHERE workflow.status='queued'
            OR (
                workflow.status='running'
                AND workflow.lease_expires_at<clock_timestamp()
            )
         ORDER BY CASE WHEN workflow.status='running' THEN 0 ELSE 1 END,
                  workflow.requested_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE rvbbit.business_topology_workflow_runs workflow
           SET status='running',
               phase=CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
               worker_id=btrim(p_worker_id),
               lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
               attempts=workflow.attempts+1,
               started_at=coalesce(workflow.started_at,clock_timestamp()),
               updated_at=clock_timestamp(),error=NULL,
               progress=workflow.progress||jsonb_build_object(
                   'phase',CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
                   'worker_id',btrim(p_worker_id),'claimed_at',clock_timestamp()
               )
          FROM picked
         WHERE workflow.run_id=picked.run_id
         RETURNING workflow.*
    )
    SELECT updated.run_id,updated.run_name,updated.parameters,updated.progress,
           updated.requested_by,updated.attempts
      FROM updated;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_topology_queue_schema(
    p_schema text,
    p_run_name text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_schema text := btrim(coalesce(p_schema,''));
    v_existing uuid;
    v_run_id uuid := gen_random_uuid();
    v_relations text[];
    v_parameters jsonb;
BEGIN
    IF v_schema='' THEN RAISE EXCEPTION 'schema is required'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business_topology_queue_schema',0
    ));
    SELECT array_agg(format('%I.%I',n.nspname,c.relname) ORDER BY c.relname)
      INTO v_relations
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname=v_schema AND c.relkind IN ('r','p','m')
       AND NOT c.relispartition
       AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
       AND has_table_privilege(c.oid,'SELECT');
    IF coalesce(cardinality(v_relations),0)=0 THEN
        RAISE EXCEPTION 'schema % has no caller-readable business relations',v_schema;
    END IF;
    IF cardinality(v_relations)>100 THEN
        RAISE EXCEPTION 'schema % has % relations; choose a bounded relation scope',
            v_schema,cardinality(v_relations);
    END IF;
    SELECT run_id INTO v_existing
      FROM rvbbit.business_topology_workflow_runs
     WHERE status='queued' AND parameters->>'scope_schema'=v_schema
     ORDER BY requested_at LIMIT 1;
    IF v_existing IS NOT NULL THEN RETURN v_existing; END IF;
    IF (
        SELECT count(*) FROM rvbbit.business_topology_workflow_runs
         WHERE status='queued'
    )>=8 THEN
        RAISE EXCEPTION 'Business Topology queue already contains eight runs';
    END IF;

    v_parameters := jsonb_build_object(
        'schema_version','rvbbit.business-topology.workflow-parameters.v1',
        'corpus_id','business-topology-'||v_run_id,
        'relations',to_jsonb(v_relations),'schemas',jsonb_build_array(v_schema),
        'scope_mode','explicit_schemas','scope_schema',v_schema,
        'sample_rows',2048,'max_relations',100,'requested_max_relations',100,
        'max_work_items',500,'max_llm_calls',128,
        'backend','clover_llm','model','calliope','include_bridges',false,
        'policy',jsonb_build_object(
            'maximum_sources_per_unit',12,'maximum_populations_per_unit',48,
            'max_pairs_per_unit',64,'max_pairs_per_link',8,
            'max_population_fanout',8,'minimum_pair_priority',0.32,
            'max_cross_neighborhood_links',24
        )
    );
    INSERT INTO rvbbit.business_topology_workflow_runs(
        run_id,run_name,status,phase,parameters,progress,requested_by
    ) VALUES (
        v_run_id,coalesce(nullif(btrim(p_run_name),''),
            'Targeted excavation - '||v_schema||' - '
            ||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')),
        'queued','queued',v_parameters,
        jsonb_build_object(
            'phase','queued','scope_mode','explicit_schemas',
            'scope_schema',v_schema,'relation_count',cardinality(v_relations),
            'completed_work_items',0,'llm_attempts',0,'bundles_staged',0,
            'queued_at',clock_timestamp()
        ),coalesce(nullif(current_setting('rvbbit.request_user',true),''),current_user)
    );
    PERFORM pg_notify('rvbbit_business_topology_jobs',v_run_id::text);
    RETURN v_run_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_source_fingerprint(
    p_sources text[]
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
    SELECT md5(coalesce(string_agg(lower(btrim(source)),E'\x1f'
                                  ORDER BY lower(btrim(source))),''))
      FROM unnest(coalesce(p_sources,ARRAY[]::text[])) source
$fn$;

ALTER TABLE rvbbit.business_value_system_families
    ADD COLUMN IF NOT EXISTS source_fingerprint text;
UPDATE rvbbit.business_value_system_families family
   SET source_fingerprint=rvbbit.business_value_source_fingerprint(proposal.source_relations)
  FROM rvbbit.business_value_graph_proposals proposal
 WHERE proposal.graph_id=coalesce(
     family.canonical_graph_id,family.active_graph_id,family.pending_graph_id
 ) AND family.source_fingerprint IS NULL;
CREATE INDEX IF NOT EXISTS business_value_system_families_source_idx
    ON rvbbit.business_value_system_families(source_fingerprint)
    WHERE source_fingerprint IS NOT NULL;

CREATE OR REPLACE FUNCTION rvbbit._business_value_canonicalize_source_family()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain_key text;
BEGIN
    SELECT family.domain_key INTO v_domain_key
      FROM rvbbit.business_value_system_families family
     WHERE family.source_fingerprint=
           rvbbit.business_value_source_fingerprint(NEW.source_relations)
     ORDER BY (family.canonical_graph_id IS NOT NULL) DESC,
              family.observation_count DESC,family.created_at
     LIMIT 1;
    IF v_domain_key IS NOT NULL THEN
        NEW.domain_key := v_domain_key;
        NEW.graph := jsonb_set(NEW.graph,'{domain_key}',to_jsonb(v_domain_key),true);
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS business_value_canonicalize_source_family
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_value_canonicalize_source_family
BEFORE INSERT ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_value_canonicalize_source_family();

CREATE OR REPLACE FUNCTION rvbbit._business_value_sync_source_family()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.family_id IS NOT NULL THEN
        UPDATE rvbbit.business_value_system_families
           SET source_fingerprint=coalesce(
               source_fingerprint,
               rvbbit.business_value_source_fingerprint(NEW.source_relations)
           ),updated_at=clock_timestamp()
         WHERE family_id=NEW.family_id;
    END IF;
    RETURN NULL;
END
$fn$;
DROP TRIGGER IF EXISTS business_value_sync_source_family
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_value_sync_source_family
AFTER INSERT OR UPDATE OF family_id ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_value_sync_source_family();

CREATE OR REPLACE VIEW rvbbit.business_value_graph_current AS
SELECT summary.*
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.graph_id IN (
     SELECT DISTINCT ON (rvbbit.business_value_source_fingerprint(candidate.source_relations))
            candidate.graph_id
       FROM rvbbit.business_value_graph_summary candidate
      WHERE candidate.lifecycle IN ('canonical','provisional')
      ORDER BY rvbbit.business_value_source_fingerprint(candidate.source_relations),
               (candidate.lifecycle='canonical') DESC,candidate.usable DESC,
               candidate.updated_at DESC,candidate.graph_id DESC
 );

COMMENT ON VIEW rvbbit.business_value_graph_current IS
    'One current canonical or provisional Value System per exact source constellation; run receipts remain queryable separately.';
COMMENT ON FUNCTION rvbbit.business_topology_queue_schema(text,text) IS
    'Queue a targeted schema excavation behind the one active worker lane; repeated queued requests for that schema are idempotent.';
