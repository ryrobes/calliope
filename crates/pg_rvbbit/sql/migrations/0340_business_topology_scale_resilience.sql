-- 0340: make no-scope Business Topology runs domain-coherent and bounded.
--
-- The original fresh-excavation path sorted every readable relation globally
-- and took the first 100. On real mirrors that mixed unrelated application,
-- warehouse, advertising, and metadata schemas into one oversized corpus.
-- Automatic runs now rotate through one schema at a time, take at most 24
-- least-recently selected relations, and record the scope decision. Explicit
-- schema/relation requests retain their existing limits and behavior.

CREATE OR REPLACE FUNCTION rvbbit._business_topology_relation_allowed(
    p_schema text,
    p_relation text
) RETURNS boolean
LANGUAGE sql STABLE
AS $allowed$
    SELECT nullif(btrim(p_schema),'') IS NOT NULL
       AND nullif(btrim(p_relation),'') IS NOT NULL
       AND lower(btrim(p_schema)) NOT IN (
           'pg_catalog','information_schema','rvbbit','hindsight'
       )
       AND lower(btrim(p_schema)) NOT LIKE 'pg_toast%'
       AND lower(btrim(p_schema)) NOT LIKE 'pg_temp_%'
       AND btrim(p_schema) !~* '^_dlt_'
       AND btrim(p_schema)<>ALL(rvbbit._catalog_excluded_schemas())
       AND btrim(p_relation) !~* '^_dlt_'
$allowed$;

COMMENT ON FUNCTION rvbbit._business_topology_relation_allowed(text,text) IS
    'True only for relations eligible for Business Topology excavation. Excludes appliance internals, DLT control/staging schemas and tables, and catalog-private schemas.';

CREATE OR REPLACE FUNCTION rvbbit.business_topology_start_workflow(
    p_schemas text[] DEFAULT NULL,
    p_relations text[] DEFAULT NULL,
    p_sample_rows integer DEFAULT 2048,
    p_max_relations integer DEFAULT 100,
    p_max_work_items integer DEFAULT 500,
    p_max_llm_calls integer DEFAULT 128,
    p_backend text DEFAULT 'clover_llm',
    p_model text DEFAULT 'calliope',
    p_include_bridges boolean DEFAULT false,
    p_run_name text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_existing uuid;
    v_run_id uuid := gen_random_uuid();
    v_relations text[] := ARRAY[]::text[];
    v_requested text;
    v_relation regclass;
    v_canonical_relation text;
    v_schema_name text;
    v_relation_name text;
    v_candidate record;
    v_parameters jsonb;
    v_scope_mode text;
    v_scope_schema text;
    v_effective_max_relations integer := p_max_relations;
    v_automatic_batch_limit integer := 24;
BEGIN
    IF p_sample_rows NOT BETWEEN 32 AND 50000 THEN
        RAISE EXCEPTION 'sample_rows must be between 32 and 50000';
    END IF;
    IF p_max_relations NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'max_relations must be between 1 and 1000';
    END IF;
    IF p_max_work_items NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'max_work_items must be between 1 and 5000';
    END IF;
    IF p_max_llm_calls NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'max_llm_calls must be between 1 and 1000';
    END IF;
    IF nullif(btrim(p_backend),'') IS NULL OR nullif(btrim(p_model),'') IS NULL THEN
        RAISE EXCEPTION 'backend and model are required';
    END IF;
    IF p_schemas IS NOT NULL AND EXISTS (
        SELECT 1 FROM unnest(p_schemas) value WHERE nullif(btrim(value),'') IS NULL
    ) THEN
        RAISE EXCEPTION 'schema names must be non-empty';
    END IF;

    -- Make repeated clicks and concurrent DataRabbit windows idempotent.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business_topology_start_workflow',0
    ));
    SELECT run_id INTO v_existing
      FROM rvbbit.business_topology_workflow_runs
     WHERE status IN ('queued','running','cancel_requested')
     ORDER BY requested_at
     LIMIT 1;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;

    IF p_relations IS NOT NULL THEN
        v_scope_mode := 'explicit_relations';
    ELSIF p_schemas IS NOT NULL THEN
        v_scope_mode := 'explicit_schemas';
        IF cardinality(p_schemas)=1 THEN
            v_scope_schema := p_schemas[1];
        END IF;
    ELSE
        v_scope_mode := 'automatic_domain_batch';
        v_effective_max_relations := least(p_max_relations,v_automatic_batch_limit);

        -- Rotate schemas by their last automatic selection. This prevents one
        -- very wide source (for example Salesforce) from starving every other
        -- business domain. Relation rotation below advances within that schema.
        WITH candidate_schemas AS (
            SELECT n.nspname AS schema_name
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE c.relkind IN ('r','p','m')
               AND NOT c.relispartition
               AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
               AND has_table_privilege(c.oid,'SELECT')
             GROUP BY n.nspname
        ), prior_scopes AS (
            SELECT parameters->>'scope_schema' AS schema_name,
                   max(requested_at) AS last_selected_at
              FROM rvbbit.business_topology_workflow_runs
             WHERE parameters->>'scope_mode'='automatic_domain_batch'
               AND nullif(parameters->>'scope_schema','') IS NOT NULL
             GROUP BY parameters->>'scope_schema'
        )
        SELECT candidate.schema_name
          INTO v_scope_schema
          FROM candidate_schemas candidate
          LEFT JOIN prior_scopes prior USING (schema_name)
         ORDER BY prior.last_selected_at NULLS FIRST,candidate.schema_name
         LIMIT 1;
    END IF;

    IF p_relations IS NOT NULL THEN
        FOREACH v_requested IN ARRAY p_relations
        LOOP
            IF nullif(btrim(v_requested),'') IS NULL THEN
                RAISE EXCEPTION 'relation names must be non-empty';
            END IF;
            v_relation := rvbbit._safe_regclass(v_requested);
            IF v_relation IS NULL THEN
                RAISE EXCEPTION 'relation % does not exist or is not visible',v_requested;
            END IF;
            IF NOT has_table_privilege(v_relation,'SELECT') THEN
                RAISE EXCEPTION 'SELECT privilege is required for relation %',v_relation;
            END IF;
            SELECT n.nspname,c.relname,format('%I.%I',n.nspname,c.relname)
              INTO v_schema_name,v_relation_name,v_canonical_relation
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE c.oid=v_relation;
            IF NOT rvbbit._business_topology_relation_allowed(
                v_schema_name,v_relation_name
            ) THEN
                RAISE EXCEPTION 'relation % is appliance-internal and cannot be excavated',
                    v_canonical_relation;
            END IF;
            IF NOT v_canonical_relation=ANY(v_relations) THEN
                v_relations := array_append(v_relations,v_canonical_relation);
            END IF;
            EXIT WHEN cardinality(v_relations)>=p_max_relations;
        END LOOP;
    ELSIF v_scope_mode='automatic_domain_batch' THEN
        FOR v_candidate IN
            SELECT format('%I.%I',n.nspname,c.relname) AS relation_name
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
              LEFT JOIN LATERAL (
                  SELECT max(run.requested_at) AS last_selected_at
                    FROM rvbbit.business_topology_workflow_runs run
                   WHERE run.parameters->'relations'
                         ? format('%I.%I',n.nspname,c.relname)
              ) prior ON true
             WHERE c.relkind IN ('r','p','m')
               AND NOT c.relispartition
               AND n.nspname=v_scope_schema
               AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
               AND has_table_privilege(c.oid,'SELECT')
             ORDER BY prior.last_selected_at NULLS FIRST,c.relname
             LIMIT v_effective_max_relations
        LOOP
            v_relations := array_append(v_relations,v_candidate.relation_name);
        END LOOP;
    ELSE
        FOR v_candidate IN
            SELECT format('%I.%I',n.nspname,c.relname) AS relation_name
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE c.relkind IN ('r','p','m')
               AND NOT c.relispartition
               AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
               AND n.nspname=ANY(p_schemas)
               AND has_table_privilege(c.oid,'SELECT')
             ORDER BY n.nspname,c.relname
             LIMIT p_max_relations
        LOOP
            v_relations := array_append(v_relations,v_candidate.relation_name);
        END LOOP;
    END IF;

    IF cardinality(v_relations)=0 THEN
        RAISE EXCEPTION 'no caller-readable business relations matched the excavation scope';
    END IF;

    v_parameters := jsonb_build_object(
        'schema_version','rvbbit.business-topology.workflow-parameters.v1',
        'corpus_id','business-topology-'||v_run_id,
        'relations',to_jsonb(v_relations),
        'schemas',to_jsonb(p_schemas),
        'scope_mode',v_scope_mode,
        'scope_schema',v_scope_schema,
        'sample_rows',p_sample_rows,
        'max_relations',v_effective_max_relations,
        'requested_max_relations',p_max_relations,
        'max_work_items',p_max_work_items,
        'max_llm_calls',p_max_llm_calls,
        'backend',btrim(p_backend),
        'model',btrim(p_model),
        'include_bridges',coalesce(p_include_bridges,false),
        'policy',jsonb_build_object(
            'maximum_sources_per_unit',12,
            'maximum_populations_per_unit',48,
            'max_pairs_per_unit',64,
            'max_pairs_per_link',8,
            'max_population_fanout',8,
            'minimum_pair_priority',0.32,
            'max_cross_neighborhood_links',24
        )
    );

    INSERT INTO rvbbit.business_topology_workflow_runs (
        run_id,run_name,status,phase,parameters,progress,requested_by
    ) VALUES (
        v_run_id,
        coalesce(
            nullif(btrim(p_run_name),''),
            CASE
                WHEN v_scope_mode='automatic_domain_batch'
                    THEN 'Fresh excavation · '||v_scope_schema||' · '
                         ||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')
                ELSE 'Fresh excavation · '
                         ||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')
            END
        ),
        'queued','queued',v_parameters,
        jsonb_build_object(
            'phase','queued',
            'relation_count',cardinality(v_relations),
            'scope_mode',v_scope_mode,
            'scope_schema',v_scope_schema,
            'completed_work_items',0,
            'llm_attempts',0,
            'bundles_staged',0,
            'queued_at',clock_timestamp()
        ),
        coalesce(nullif(current_setting('rvbbit.request_user',true),''),current_user)
    );

    PERFORM pg_notify('rvbbit_business_topology_jobs',v_run_id::text);
    RETURN v_run_id;
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_topology_start_workflow(
    text[],text[],integer,integer,integer,integer,text,text,boolean,text
) IS
    'Queue one bounded Business Topology excavation. No-scope runs rotate through one domain schema in 24-relation batches; explicit scopes retain caller limits.';
