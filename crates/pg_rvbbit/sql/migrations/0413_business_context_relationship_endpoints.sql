-- Concept relationships may carry only one physical source annotation.
-- Rebuild the projection without inventing endpoints or discarding source evidence.
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
    v_relationships_skipped integer := 0;
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
            -- A single source annotation does not make a concept relationship
            -- a table-to-table edge. Preserve the original payload as evidence.
            IF nullif(btrim(v_relationship->>'relationship'),'') IS NULL THEN
                v_relationships_skipped := v_relationships_skipped + 1;
                CONTINUE;
            END IF;
            IF nullif(btrim(v_relationship->>'left_relation'),'') IS NOT NULL
               AND nullif(btrim(v_relationship->>'right_relation'),'') IS NOT NULL THEN
                v_left_label := v_system.graph_id::text||':'||btrim(v_relationship->>'left_relation');
                v_right_label := v_system.graph_id::text||':'||btrim(v_relationship->>'right_relation');
                PERFORM rvbbit.kg_assert_edge(
                    'business_source_relation',v_left_label,btrim(v_relationship->>'relationship'),
                    'business_source_relation',v_right_label,
                    coalesce((v_relationship->>'confidence')::double precision,1.0),
                    jsonb_build_object('text',coalesce(v_relationship->>'rationale','')),
                    v_relationship,'',0.0,v_graph
                );
            ELSIF nullif(btrim(v_relationship->>'subject_concept_key'),'') IS NOT NULL
                  AND nullif(btrim(v_relationship->>'object_concept_key'),'') IS NOT NULL THEN
                v_left_label := v_domain_label||':'||btrim(v_relationship->>'subject_concept_key');
                v_right_label := v_domain_label||':'||btrim(v_relationship->>'object_concept_key');
                PERFORM rvbbit.kg_assert_edge(
                    'business_concept',v_left_label,btrim(v_relationship->>'relationship'),
                    'business_concept',v_right_label,
                    coalesce((v_relationship->>'confidence')::double precision,1.0),
                    jsonb_build_object('text',coalesce(v_relationship->>'rationale','')),
                    v_relationship,'',0.0,v_graph
                );
            ELSE
                -- Incomplete evidence remains in the source topology, but must
                -- not abort the whole rebuildable search projection.
                v_relationships_skipped := v_relationships_skipped + 1;
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
        'nodes',v_nodes,'embedded',v_embedded,'semantic_topology',true,
        'semantic_relationships_skipped',v_relationships_skipped
    );
EXCEPTION WHEN OTHERS THEN
    UPDATE rvbbit.business_context_state SET last_error=SQLERRM WHERE singleton;
    RAISE;
END
$fn$;

