-- 0343: make Business Topology domains semantic rather than schema-shaped.
--
-- Value modeling now plans coherent business domains from compact per-source
-- findings. A schema or table is evidence and execution scope, never identity.
-- This migration also keeps the Business Context steward able to observe and
-- refresh a semantic domain through the exact source relations in its graph.

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
       AND lower(btrim(p_relation)) NOT IN (
           '_sync_metadata','_sync_state','_snapshot_metadata'
       )
$allowed$;

COMMENT ON FUNCTION rvbbit._business_topology_relation_allowed(text,text) IS
    'True only for business-facing relations eligible for excavation. Excludes appliance internals, DLT staging/control objects, and local sync metadata.';

CREATE OR REPLACE FUNCTION rvbbit.business_context_domain_sources(
    p_domain text
) RETURNS TABLE(schema_name text,relation_name text)
LANGUAGE sql STABLE
AS $fn$
    WITH physical AS (
        SELECT n.nspname AS schema_name,c.relname AS relation_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname=btrim(p_domain)
           AND c.relkind IN ('r','p','m')
           AND NOT c.relispartition
           AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
    ), latest_graph AS (
        SELECT proposal.source_relations
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.domain_key=btrim(p_domain)
         ORDER BY proposal.usable DESC,proposal.updated_at DESC,proposal.graph_id DESC
         LIMIT 1
    ), semantic AS (
        SELECT split_part(source_relation,'.',1) AS schema_name,
               substr(source_relation,strpos(source_relation,'.')+1) AS relation_name
          FROM latest_graph
          CROSS JOIN LATERAL unnest(latest_graph.source_relations) source_relation
         WHERE strpos(source_relation,'.')>1
    )
    SELECT physical.schema_name,physical.relation_name FROM physical
    UNION ALL
    SELECT semantic.schema_name,semantic.relation_name
      FROM semantic
     WHERE NOT EXISTS (SELECT 1 FROM physical)
       AND rvbbit._business_topology_relation_allowed(
           semantic.schema_name,semantic.relation_name
       )
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_domain_sources(text) IS
    'Resolve a physical schema domain to its eligible relations, or a semantic Value System domain to the exact relations in its latest graph.';

CREATE OR REPLACE FUNCTION rvbbit.business_context_domain_snapshot(
    p_domain text
) RETURNS jsonb
LANGUAGE sql STABLE
AS $fn$
    WITH relations AS (
        SELECT c.oid,n.nspname,c.relname,c.relkind,
               obj_description(c.oid,'pg_class') AS description
          FROM rvbbit.business_context_domain_sources(p_domain) source
          JOIN pg_namespace n ON n.nspname=source.schema_name
          JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=source.relation_name
         WHERE c.relkind IN ('r','p','m') AND NOT c.relispartition
    ), packets AS (
        SELECT relation.nspname,relation.relname,
               jsonb_strip_nulls(jsonb_build_object(
                   'schema',relation.nspname,
                   'relation',relation.relname,
                   'kind',relation.relkind,
                   'description',relation.description,
                   'columns',coalesce((
                       SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                           'name',attribute.attname,
                           'type',format_type(attribute.atttypid,attribute.atttypmod),
                           'not_null',attribute.attnotnull,
                           'identity',nullif(attribute.attidentity,''),
                           'generated',nullif(attribute.attgenerated,''),
                           'description',col_description(attribute.attrelid,attribute.attnum)
                       )) ORDER BY attribute.attnum)
                         FROM pg_attribute attribute
                        WHERE attribute.attrelid=relation.oid
                          AND attribute.attnum>0 AND NOT attribute.attisdropped
                   ),'[]'::jsonb),
                   'constraints',coalesce((
                       SELECT jsonb_agg(jsonb_build_object(
                           'type',constraint_row.contype,
                           'definition',pg_get_constraintdef(constraint_row.oid,true)
                       ) ORDER BY constraint_row.contype,constraint_row.conname)
                         FROM pg_constraint constraint_row
                        WHERE constraint_row.conrelid=relation.oid
                   ),'[]'::jsonb)
               )) AS packet
          FROM relations relation
    )
    SELECT jsonb_build_object(
        'schema_version','rvbbit.business-context.domain-snapshot.v2',
        'domain_key',btrim(p_domain),
        'relation_count',(SELECT count(*) FROM packets),
        'column_count',(
            SELECT count(*)
              FROM pg_attribute attribute
              JOIN relations relation ON relation.oid=attribute.attrelid
             WHERE attribute.attnum>0 AND NOT attribute.attisdropped
        ),
        'relations',coalesce((
            SELECT jsonb_agg(packet ORDER BY nspname,relname) FROM packets
        ),'[]'::jsonb)
    )
$fn$;

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
      FROM rvbbit.business_context_domains
     WHERE status='dirty' AND pending_workflow_run_id IS NULL
       AND (p_domain IS NULL OR domain_key=btrim(p_domain))
     ORDER BY dirty_since NULLS LAST,updated_at
     LIMIT 1 FOR UPDATE;
    IF NOT FOUND AND p_force AND nullif(btrim(coalesce(p_domain,'')),'') IS NOT NULL THEN
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
        RETURN jsonb_build_object('status','idle','message','No structurally dirty Business Context domain needs excavation.');
    END IF;
    IF EXISTS (
        SELECT 1 FROM rvbbit.business_context_domain_sources(v_domain.domain_key) source
         WHERE source.schema_name=v_domain.domain_key
    ) THEN
        v_schemas := ARRAY[v_domain.domain_key];
        v_relations := NULL;
    ELSE
        SELECT array_agg(format('%I.%I',source.schema_name,source.relation_name)
                         ORDER BY source.schema_name,source.relation_name)
          INTO v_relations
          FROM rvbbit.business_context_domain_sources(v_domain.domain_key) source;
        IF coalesce(cardinality(v_relations),0)=0 THEN
            UPDATE rvbbit.business_context_domains
               SET status='error',last_error='Business Context domain has no eligible source relations.',
                   updated_at=clock_timestamp()
             WHERE domain_key=v_domain.domain_key;
            RETURN jsonb_build_object('status','error','domain_key',v_domain.domain_key,
                                      'message','No eligible source relations.');
        END IF;
        v_schemas := NULL;
    END IF;
    v_run_id := rvbbit.business_topology_start_workflow(
        v_schemas,v_relations,2048,100,500,128,
        'clover_llm','clover',false,
        'Steward refresh - '||v_domain.domain_key||' - '||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')
    );
    UPDATE rvbbit.business_context_domains
       SET status='queued',pending_workflow_run_id=v_run_id,last_error=NULL,
           updated_at=clock_timestamp()
     WHERE domain_key=v_domain.domain_key;
    RETURN jsonb_build_object(
        'status','queued','domain_key',v_domain.domain_key,
        'workflow_run_id',v_run_id,'observed_fingerprint',v_domain.observed_fingerprint,
        'modeled_fingerprint',v_domain.modeled_fingerprint
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain text;
    v_semantic_domain text;
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
        PERFORM rvbbit.business_context_mark_domain_dirty(
            v_domain,'mirror',NEW.run_id::text,v_reason
        );
        FOR v_semantic_domain IN
            SELECT DISTINCT proposal.domain_key
              FROM rvbbit.business_value_graph_proposals proposal
             WHERE proposal.domain_key<>v_domain
               AND EXISTS (
                   SELECT 1 FROM unnest(proposal.source_relations) source_relation
                    WHERE split_part(source_relation,'.',1)=v_domain
               )
        LOOP
            PERFORM rvbbit.business_context_mark_domain_dirty(
                v_semantic_domain,'mirror',NEW.run_id::text,v_reason
            );
        END LOOP;
        IF NEW.tables_succeeded>0 THEN
            UPDATE rvbbit.business_context_domains domain
               SET data_dirty_since=coalesce(data_dirty_since,v_finished_at),
                   last_mirror_data_at=v_finished_at,updated_at=clock_timestamp()
             WHERE domain.domain_key=v_domain OR EXISTS (
                 SELECT 1 FROM rvbbit.business_value_graph_proposals proposal
                  WHERE proposal.domain_key=domain.domain_key
                    AND EXISTS (
                        SELECT 1 FROM unnest(proposal.source_relations) source_relation
                         WHERE split_part(source_relation,'.',1)=v_domain
                    )
             );
        END IF;
    END IF;
    RETURN NULL;
END
$fn$;

