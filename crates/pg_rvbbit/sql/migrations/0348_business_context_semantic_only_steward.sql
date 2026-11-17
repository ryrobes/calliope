-- 0348: automatic stewardship refreshes semantic Value Systems, not schemas.
--
-- Physical schemas are discovery and catalog-observation scopes.  Treating a
-- dirty schema as an automatic topology domain recreated one-table domains
-- (notably Austin bikeshare) after every clean topology wipe and prevented the
-- planner from considering cross-schema systems.  A forced manual request may
-- still excavate a physical scope; scheduled work must start from an existing
-- semantic graph and its explicit source_relations.

CREATE OR REPLACE FUNCTION rvbbit.business_context_steward_tick(
    p_domain text DEFAULT NULL,
    p_force boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain rvbbit.business_context_domains%ROWTYPE;
    v_active uuid;
    v_run_id uuid;
    v_schemas text[];
    v_relations text[];
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business-context-steward',0));
    SELECT run_id INTO v_active
      FROM rvbbit.business_topology_workflow_runs
     WHERE status IN ('queued','running','cancel_requested')
     ORDER BY requested_at LIMIT 1;
    IF v_active IS NOT NULL THEN
        RETURN jsonb_build_object('status','busy','active_workflow_run_id',v_active);
    END IF;

    SELECT * INTO v_domain
      FROM rvbbit.business_context_domains domain
     WHERE domain.status='dirty'
       AND domain.pending_workflow_run_id IS NULL
       AND (p_domain IS NULL OR domain.domain_key=btrim(p_domain))
       AND (
           p_force
           OR EXISTS (
               SELECT 1
                 FROM rvbbit.business_value_graph_proposals proposal
                WHERE proposal.domain_key=domain.domain_key
           )
       )
     ORDER BY domain.dirty_since NULLS LAST,domain.updated_at
     LIMIT 1 FOR UPDATE;

    IF NOT FOUND AND p_force
       AND nullif(btrim(coalesce(p_domain,'')),'') IS NOT NULL THEN
        PERFORM rvbbit.business_context_mark_domain_dirty(
            btrim(p_domain),'manual',NULL,jsonb_build_object('force',true)
        );
        PERFORM rvbbit.business_context_observe_domain(
            btrim(p_domain),'manual',NULL,jsonb_build_object('force',true)
        );
        UPDATE rvbbit.business_context_domains
           SET status='dirty',dirty_since=coalesce(dirty_since,clock_timestamp()),
               updated_at=clock_timestamp()
         WHERE domain_key=btrim(p_domain);
        SELECT * INTO v_domain FROM rvbbit.business_context_domains
         WHERE domain_key=btrim(p_domain) FOR UPDATE;
    END IF;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'status','idle',
            'message','No modeled semantic Value System needs excavation.'
        );
    END IF;

    IF p_force AND EXISTS (
        SELECT 1 FROM rvbbit.business_context_domain_sources(v_domain.domain_key) source
         WHERE source.schema_name=v_domain.domain_key
    ) THEN
        v_schemas := ARRAY[v_domain.domain_key];
        v_relations := NULL;
    ELSE
        SELECT array_agg(
                   format('%I.%I',source.schema_name,source.relation_name)
                   ORDER BY source.schema_name,source.relation_name
               )
          INTO v_relations
          FROM rvbbit.business_context_domain_sources(v_domain.domain_key) source;
        IF coalesce(cardinality(v_relations),0)=0 THEN
            UPDATE rvbbit.business_context_domains
               SET status='error',
                   last_error='Semantic Value System has no eligible source relations.',
                   updated_at=clock_timestamp()
             WHERE domain_key=v_domain.domain_key;
            RETURN jsonb_build_object(
                'status','error','domain_key',v_domain.domain_key,
                'message','No eligible source relations.'
            );
        END IF;
        v_schemas := NULL;
    END IF;

    v_run_id := rvbbit.business_topology_start_workflow(
        v_schemas,v_relations,2048,100,500,128,
        'clover_llm','clover',false,
        'Steward refresh - '||v_domain.domain_key||' - '
            ||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')
    );
    UPDATE rvbbit.business_context_domains
       SET status='queued',pending_workflow_run_id=v_run_id,last_error=NULL,
           updated_at=clock_timestamp()
     WHERE domain_key=v_domain.domain_key;
    RETURN jsonb_build_object(
        'status','queued','domain_key',v_domain.domain_key,
        'workflow_run_id',v_run_id,
        'observed_fingerprint',v_domain.observed_fingerprint,
        'modeled_fingerprint',v_domain.modeled_fingerprint
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_steward_tick(text,boolean) IS
    'Automatically refresh an existing semantic Value System from its source relations; physical schema excavation requires an explicit forced request.';
