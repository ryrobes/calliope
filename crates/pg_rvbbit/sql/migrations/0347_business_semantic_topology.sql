-- 0347: make Business Topology a semantic graph rather than a table partition.
--
-- The v2 worker emits a small business-domain layer containing concepts and
-- processes, then one or more analytical Value Systems with many-to-many
-- source memberships. Promoted cubes are derived assets attached by lineage;
-- they must never re-enter source discovery as new business domains.

CREATE OR REPLACE FUNCTION rvbbit._business_topology_relation_allowed(
    p_schema text,
    p_relation text
) RETURNS boolean
LANGUAGE sql STABLE
AS $allowed$
    SELECT nullif(btrim(p_schema),'') IS NOT NULL
       AND nullif(btrim(p_relation),'') IS NOT NULL
       AND lower(btrim(p_schema)) NOT IN (
           'pg_catalog','information_schema','rvbbit','hindsight','cubes'
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
    'True only for source relations eligible for excavation. Appliance internals, DLT control objects, sync metadata, and derived cubes are excluded.';

CREATE OR REPLACE FUNCTION rvbbit.business_value_graph_semantic_fingerprint(
    p_graph jsonb
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
    SELECT md5(jsonb_build_object(
        'business_domain',jsonb_strip_nulls(jsonb_build_object(
            'key',lower(coalesce(p_graph #>> '{business_domain,domain_key}',''))
        )),
        'concepts',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'key',lower(concept->>'concept_key'),
                'kind',lower(concept->>'kind')
            ) ORDER BY lower(concept->>'concept_key'))
              FROM jsonb_array_elements(coalesce(p_graph->'concepts','[]'::jsonb)) concept
        ),'[]'::jsonb),
        'processes',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'key',lower(process->>'process_key'),
                'concepts',coalesce(process->'concept_keys','[]'::jsonb)
            ) ORDER BY lower(process->>'process_key'))
              FROM jsonb_array_elements(coalesce(p_graph->'processes','[]'::jsonb)) process
        ),'[]'::jsonb),
        'memberships',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'relation',lower(membership->>'relation'),
                'role',lower(membership->>'role'),
                'concepts',coalesce(membership->'concept_keys','[]'::jsonb)
            ) ORDER BY lower(membership->>'relation'),lower(membership->>'role'))
              FROM jsonb_array_elements(coalesce(p_graph->'source_memberships','[]'::jsonb)) membership
        ),'[]'::jsonb),
        'semantic_relationships',coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'relationship',lower(relationship->>'relationship'),
                'left_relation',lower(relationship->>'left_relation'),
                'right_relation',lower(relationship->>'right_relation'),
                'subject_concept',lower(relationship->>'subject_concept_key'),
                'object_concept',lower(relationship->>'object_concept_key'),
                'left_columns',relationship->'left_columns',
                'right_columns',relationship->'right_columns'
            )) ORDER BY relationship::text)
              FROM jsonb_array_elements(coalesce(p_graph->'semantic_relationships','[]'::jsonb)) relationship
        ),'[]'::jsonb),
        'metrics',coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'kind',lower(metric->>'metric_kind'),
                'unit',lower(metric->>'unit'),
                'event_sql',regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g'),
                'grain',lower(btrim(metric->>'grain')),
                'time',lower(btrim(metric->>'time_semantics')),
                'dimensions',coalesce((
                    SELECT jsonb_agg(lower(coalesce(dimension->>'key',dimension->>'name'))
                                     ORDER BY lower(coalesce(dimension->>'key',dimension->>'name')))
                      FROM jsonb_array_elements(coalesce(metric->'dimensions','[]'::jsonb)) dimension
                ),'[]'::jsonb)
            )) ORDER BY regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g'))
              FROM jsonb_array_elements(coalesce(p_graph->'metrics','[]'::jsonb)) metric
        ),'[]'::jsonb),
        'metric_edges',coalesce(p_graph->'edges','[]'::jsonb)
    )::text)
$fn$;

COMMENT ON FUNCTION rvbbit.business_value_graph_semantic_fingerprint(jsonb) IS
    'Stable Value System identity over business domain, concepts, processes, overlapping source roles, semantic relationships, and executable metric semantics. Materialization names and cube buckets are excluded.';

CREATE OR REPLACE VIEW rvbbit.business_semantic_topology AS
SELECT summary.graph_id,summary.workflow_run_id,
       coalesce(nullif(summary.graph #>> '{business_domain,domain_key}',''),summary.domain_key)
           AS business_domain_key,
       coalesce(nullif(summary.graph #>> '{business_domain,name}',''),summary.name)
           AS business_domain_name,
       summary.domain_key AS value_system_key,summary.name AS value_system_name,
       summary.summary,summary.status,summary.lifecycle,summary.usable,
       summary.source_relations,
       coalesce(summary.graph->'concepts','[]'::jsonb) AS concepts,
       coalesce(summary.graph->'processes','[]'::jsonb) AS processes,
       coalesce(summary.graph->'source_memberships','[]'::jsonb) AS source_memberships,
       coalesce(summary.graph->'semantic_relationships','[]'::jsonb) AS semantic_relationships,
       coalesce(summary.graph->'analytical_questions','[]'::jsonb) AS analytical_questions,
       coalesce(summary.graph->'gaps','[]'::jsonb) AS gaps,
       CASE WHEN summary.cube_name IS NOT NULL THEN 'cubes.'||summary.cube_name END
           AS materialization_relation,
       summary.graph,summary.receipt,summary.updated_at
  FROM rvbbit.business_value_graph_summary summary;

COMMENT ON VIEW rvbbit.business_semantic_topology IS
    'Business domains, concepts, processes, overlapping source memberships, analytical systems, gaps, and lineage-linked materializations from receipt-backed Value Graphs.';

-- A Value System is current by semantic family, not by an exclusive source
-- partition. Two systems are allowed to use the same tables for different
-- questions, and one table may participate in many systems.
CREATE OR REPLACE VIEW rvbbit.business_value_graph_current AS
SELECT summary.*
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.graph_id IN (
     SELECT DISTINCT ON (candidate.family_id)
            candidate.graph_id
       FROM rvbbit.business_value_graph_summary candidate
      WHERE candidate.lifecycle IN ('canonical','provisional')
        AND candidate.family_id IS NOT NULL
      ORDER BY candidate.family_id,
               (candidate.lifecycle='canonical') DESC,candidate.usable DESC,
               candidate.updated_at DESC,candidate.graph_id DESC
 );

COMMENT ON VIEW rvbbit.business_value_graph_current IS
    'One current canonical or provisional graph per semantic Value System family. Source relations may overlap freely across systems.';

-- Preserve the metric-oriented projection and layer semantic domain/concept/
-- process documents around it. The wrapper snapshots semantic vectors before
-- the older rebuild deletes the graph, then restores unchanged documents.
DO $rename$
BEGIN
    IF to_regprocedure('rvbbit._business_context_crawl_metric_v1(boolean)') IS NULL THEN
        ALTER FUNCTION rvbbit.business_context_crawl(boolean)
            RENAME TO _business_context_crawl_metric_v1;
    END IF;
END
$rename$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_crawl(
    p_do_embed boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph constant text := 'business_context';
    v_base jsonb;
    v_system record;
    v_domain jsonb;
    v_concept jsonb;
    v_process jsonb;
    v_membership jsonb;
    v_relationship jsonb;
    v_question_names text;
    v_gap_names text;
    v_domain_label text;
    v_system_label text;
    v_concept_label text;
    v_process_label text;
    v_source_label text;
    v_left_label text;
    v_right_label text;
    v_result jsonb;
    v_nodes integer;
    v_embedded integer;
BEGIN
    CREATE TEMP TABLE _business_semantic_embedding_snapshot ON COMMIT DROP AS
        SELECT kind,rel_name,doc,embedding,embedded_at
          FROM rvbbit.catalog_docs
         WHERE graph_id=v_graph
           AND kind IN (
               'business_domain','business_concept','business_process',
               'business_value_system_topology'
           )
           AND embedding IS NOT NULL;

    v_base := rvbbit._business_context_crawl_metric_v1(p_do_embed);

    FOR v_system IN
        SELECT topology.*
          FROM rvbbit.business_semantic_topology topology
          JOIN rvbbit.business_value_graph_current current_graph USING (graph_id)
         WHERE jsonb_typeof(topology.graph->'business_domain')='object'
         ORDER BY topology.business_domain_key,topology.value_system_key
    LOOP
        v_domain := v_system.graph->'business_domain';
        v_domain_label := v_system.business_domain_key;
        v_system_label := v_system.graph_id::text;

        v_result := rvbbit._business_context_index_node(
            'business_domain',v_domain_label,'business_domain:'||v_domain_label,
            jsonb_build_object(
                'domain_key',v_domain_label,
                'name',v_system.business_domain_name,
                'summary',coalesce(v_domain->>'summary',''),
                'status',v_system.status
            ),
            concat_ws(E'\n',
                'Business domain: '||v_system.business_domain_name||'.',
                nullif(btrim(coalesce(v_domain->>'summary','')),''),
                'Contains Value System: '||v_system.value_system_name||'.'
            ),p_do_embed
        );

        SELECT string_agg(value,'; ' ORDER BY value)
          INTO v_question_names
          FROM jsonb_array_elements_text(v_system.analytical_questions) value;
        SELECT string_agg(value,'; ' ORDER BY value)
          INTO v_gap_names
          FROM jsonb_array_elements_text(v_system.gaps) value;
        v_result := rvbbit._business_context_index_node(
            'business_value_system_topology',v_system_label,
            'value_system_topology:'||v_system.graph_id::text,
            jsonb_strip_nulls(jsonb_build_object(
                'graph_id',v_system.graph_id,
                'domain_key',v_system.business_domain_key,
                'value_system_key',v_system.value_system_key,
                'name',v_system.value_system_name,
                'status',v_system.status,
                'source_relations',to_jsonb(v_system.source_relations),
                'concepts',v_system.concepts,
                'processes',v_system.processes,
                'semantic_relationships',v_system.semantic_relationships,
                'analytical_questions',v_system.analytical_questions,
                'gaps',v_system.gaps,
                'materialization_relation',v_system.materialization_relation
            )),
            concat_ws(E'\n',
                'Business topology for Value System: '||v_system.value_system_name||'.',
                'Business domain: '||v_system.business_domain_name||'.',
                nullif(btrim(v_system.summary),''),
                CASE WHEN v_question_names IS NOT NULL
                     THEN 'Useful analytical questions: '||v_question_names||'.' END,
                CASE WHEN v_gap_names IS NOT NULL
                     THEN 'Known gaps and limitations: '||v_gap_names||'.' END,
                'Source relations may overlap with other Value Systems: '
                    ||array_to_string(v_system.source_relations,', ')||'.',
                CASE WHEN v_system.materialization_relation IS NOT NULL
                     THEN 'Derived materialization: '||v_system.materialization_relation
                          ||'; it remains part of this system and is not a new domain.' END
            ),p_do_embed
        );

        PERFORM rvbbit.kg_assert_node(
            'business_value_system',v_system_label,
            jsonb_build_object(
                'graph_id',v_system.graph_id,'domain_key',v_system.value_system_key,
                'name',v_system.value_system_name,'status',v_system.status
            ),1.0,'',0.0,v_graph
        );
        PERFORM rvbbit.kg_assert_edge(
            'business_domain',v_domain_label,'contains_value_system',
            'business_value_system',v_system_label,1.0,'{}'::jsonb,
            jsonb_build_object('graph_id',v_system.graph_id),'',0.0,v_graph
        );

        FOR v_concept IN SELECT value FROM jsonb_array_elements(v_system.concepts)
        LOOP
            v_concept_label := v_domain_label||':'||(v_concept->>'concept_key');
            v_result := rvbbit._business_context_index_node(
                'business_concept',v_concept_label,
                'business_concept:'||v_concept_label,
                jsonb_build_object(
                    'domain_key',v_domain_label,
                    'concept_key',v_concept->>'concept_key',
                    'name',v_concept->>'name','kind',v_concept->>'kind',
                    'description',coalesce(v_concept->>'description',''),
                    'status',v_system.status
                ),
                concat_ws(E'\n',
                    'Business concept: '||coalesce(v_concept->>'name',v_concept->>'concept_key')||'.',
                    'Kind: '||coalesce(v_concept->>'kind','concept')||'. Domain: '
                        ||v_system.business_domain_name||'.',
                    nullif(btrim(coalesce(v_concept->>'description','')),'')
                ),p_do_embed
            );
            PERFORM rvbbit.kg_assert_edge(
                'business_domain',v_domain_label,'contains_concept',
                'business_concept',v_concept_label,1.0,'{}'::jsonb,
                jsonb_build_object('concept_key',v_concept->>'concept_key'),'',0.0,v_graph
            );
        END LOOP;

        FOR v_process IN SELECT value FROM jsonb_array_elements(v_system.processes)
        LOOP
            v_process_label := v_domain_label||':'||(v_process->>'process_key');
            v_result := rvbbit._business_context_index_node(
                'business_process',v_process_label,
                'business_process:'||v_process_label,
                jsonb_build_object(
                    'domain_key',v_domain_label,
                    'process_key',v_process->>'process_key',
                    'name',v_process->>'name',
                    'description',coalesce(v_process->>'description',''),
                    'concept_keys',coalesce(v_process->'concept_keys','[]'::jsonb),
                    'status',v_system.status
                ),
                concat_ws(E'\n',
                    'Business process: '||coalesce(v_process->>'name',v_process->>'process_key')||'.',
                    'Domain: '||v_system.business_domain_name||'.',
                    nullif(btrim(coalesce(v_process->>'description','')),'')
                ),p_do_embed
            );
            PERFORM rvbbit.kg_assert_edge(
                'business_domain',v_domain_label,'contains_process',
                'business_process',v_process_label,1.0,'{}'::jsonb,
                jsonb_build_object('process_key',v_process->>'process_key'),'',0.0,v_graph
            );
            FOR v_concept IN
                SELECT jsonb_build_object('concept_key',value)
                  FROM jsonb_array_elements_text(coalesce(v_process->'concept_keys','[]'::jsonb)) value
            LOOP
                PERFORM rvbbit.kg_assert_edge(
                    'business_process',v_process_label,'involves',
                    'business_concept',v_domain_label||':'||(v_concept->>'concept_key'),
                    1.0,'{}'::jsonb,'{}'::jsonb,'',0.0,v_graph
                );
            END LOOP;
        END LOOP;

        FOR v_membership IN
            SELECT value FROM jsonb_array_elements(v_system.source_memberships)
        LOOP
            v_source_label := v_system.graph_id::text||':'||(v_membership->>'relation');
            PERFORM rvbbit.kg_assert_node(
                'business_source_relation',v_source_label,
                jsonb_build_object(
                    'graph_id',v_system.graph_id,
                    'relation',v_membership->>'relation',
                    'role',v_membership->>'role'
                ),coalesce((v_membership->>'confidence')::double precision,1.0),
                '',0.0,v_graph
            );
            PERFORM rvbbit.kg_assert_edge(
                'business_value_system',v_system_label,'uses_source',
                'business_source_relation',v_source_label,
                coalesce((v_membership->>'confidence')::double precision,1.0),
                jsonb_build_object('text',coalesce(v_membership->>'rationale','')),
                jsonb_build_object(
                    'relation',v_membership->>'relation','role',v_membership->>'role'
                ),'',0.0,v_graph
            );
            FOR v_concept IN
                SELECT jsonb_build_object('concept_key',value)
                  FROM jsonb_array_elements_text(coalesce(v_membership->'concept_keys','[]'::jsonb)) value
            LOOP
                PERFORM rvbbit.kg_assert_edge(
                    'business_source_relation',v_source_label,'expresses',
                    'business_concept',v_domain_label||':'||(v_concept->>'concept_key'),
                    coalesce((v_membership->>'confidence')::double precision,1.0),
                    '{}','{}','',0.0,v_graph
                );
            END LOOP;
        END LOOP;

        FOR v_relationship IN
            SELECT value FROM jsonb_array_elements(v_system.semantic_relationships)
        LOOP
            IF nullif(v_relationship->>'left_relation','') IS NOT NULL THEN
                v_left_label := v_system.graph_id::text||':'||(v_relationship->>'left_relation');
                v_right_label := v_system.graph_id::text||':'||(v_relationship->>'right_relation');
                PERFORM rvbbit.kg_assert_edge(
                    'business_source_relation',v_left_label,v_relationship->>'relationship',
                    'business_source_relation',v_right_label,
                    coalesce((v_relationship->>'confidence')::double precision,1.0),
                    jsonb_build_object('text',coalesce(v_relationship->>'rationale','')),
                    v_relationship,'',0.0,v_graph
                );
            ELSE
                v_left_label := v_domain_label||':'||(v_relationship->>'subject_concept_key');
                v_right_label := v_domain_label||':'||(v_relationship->>'object_concept_key');
                PERFORM rvbbit.kg_assert_edge(
                    'business_concept',v_left_label,v_relationship->>'relationship',
                    'business_concept',v_right_label,
                    coalesce((v_relationship->>'confidence')::double precision,1.0),
                    jsonb_build_object('text',coalesce(v_relationship->>'rationale','')),
                    v_relationship,'',0.0,v_graph
                );
            END IF;
        END LOOP;
    END LOOP;

    UPDATE rvbbit.catalog_docs docs
       SET embedding=snapshot.embedding,embedded_at=snapshot.embedded_at
      FROM _business_semantic_embedding_snapshot snapshot
     WHERE docs.graph_id=v_graph AND docs.embedding IS NULL
       AND docs.kind=snapshot.kind AND docs.rel_name=snapshot.rel_name
       AND docs.doc=snapshot.doc;
    DROP TABLE _business_semantic_embedding_snapshot;

    SELECT count(*),count(*) FILTER (WHERE embedding IS NOT NULL)
      INTO v_nodes,v_embedded
      FROM rvbbit.catalog_docs WHERE graph_id=v_graph;
    UPDATE rvbbit.business_context_state
       SET crawled_at=clock_timestamp(),node_count=v_nodes,
           embedded_count=v_embedded,last_error=NULL
     WHERE singleton;
    RETURN v_base||jsonb_build_object(
        'nodes',v_nodes,'embedded',v_embedded,'semantic_topology',true
    );
EXCEPTION WHEN OTHERS THEN
    UPDATE rvbbit.business_context_state SET last_error=SQLERRM WHERE singleton;
    RAISE;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_search(
    p_query text,
    p_k integer DEFAULT 8,
    p_statuses text[] DEFAULT ARRAY['promoted','approved','proposed']::text[]
) RETURNS TABLE(
    node_id bigint,kind text,handle text,score double precision,doc text,properties jsonb
)
LANGUAGE sql STABLE
AS $fn$
    SELECT search.node_id,search.kind,search.rel_name AS handle,
           least(1.0,search.score*CASE
               WHEN node.properties->>'status'='promoted' THEN 1.08
               WHEN node.properties->>'status'='approved' THEN 1.03
               ELSE 1.0 END) AS score,
           search.doc,node.properties
      FROM rvbbit.data_search(
          p_query,greatest(least(coalesce(p_k,8)*4,100),8),
          ARRAY[
              'business_domain','business_concept','business_process',
              'business_value_system_topology','business_value_system',
              'business_metric','business_object'
          ],
          'business_context'
      ) search
      JOIN rvbbit.kg_nodes node
        ON node.node_id=search.node_id AND node.graph_id='business_context'
     WHERE p_statuses IS NULL OR node.properties->>'status'=ANY(p_statuses)
     ORDER BY
         CASE node.properties->>'status'
             WHEN 'promoted' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
         score DESC
     LIMIT greatest(least(coalesce(p_k,8),25),1)
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_crawl(boolean) IS
    'Rebuild Business Context with domains, concepts, processes, overlapping source roles, Value Systems, metrics, and materialization lineage while preserving unchanged vectors.';
COMMENT ON FUNCTION rvbbit.business_context_search(text,integer,text[]) IS
    'Semantic retrieval over business domains, concepts, processes, Value Systems, governed metrics, and contributors.';

UPDATE rvbbit.business_context_state
   SET dirty_at=clock_timestamp()
 WHERE singleton;
