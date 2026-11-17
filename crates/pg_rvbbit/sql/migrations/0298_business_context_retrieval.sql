-- 0298: project reviewed Business Value Graphs into an agent-facing semantic
-- context graph.  The proposal tables remain the receipt-bearing source of
-- truth; this graph is a compact, rebuildable retrieval projection.

CREATE TABLE IF NOT EXISTS rvbbit.business_context_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    dirty_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    crawled_at timestamptz,
    node_count integer NOT NULL DEFAULT 0,
    embedded_count integer NOT NULL DEFAULT 0,
    last_error text
);

INSERT INTO rvbbit.business_context_state(singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION rvbbit._business_context_mark_stale()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    INSERT INTO rvbbit.business_context_state(singleton,dirty_at)
    VALUES (true,clock_timestamp())
    ON CONFLICT (singleton) DO UPDATE
       SET dirty_at=EXCLUDED.dirty_at;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_graph_stale
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_context_graph_stale
AFTER INSERT OR UPDATE OR DELETE ON rvbbit.business_value_graph_proposals
FOR EACH STATEMENT EXECUTE FUNCTION rvbbit._business_context_mark_stale();

DROP TRIGGER IF EXISTS business_context_promotion_stale
    ON rvbbit.business_value_graph_promotions;
CREATE TRIGGER business_context_promotion_stale
AFTER INSERT OR UPDATE OR DELETE ON rvbbit.business_value_graph_promotions
FOR EACH STATEMENT EXECUTE FUNCTION rvbbit._business_context_mark_stale();

CREATE OR REPLACE FUNCTION rvbbit.business_context_search_stale()
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT state.crawled_at IS NULL
        OR state.dirty_at > state.crawled_at
        OR NOT EXISTS (
            SELECT 1 FROM rvbbit.catalog_docs
             WHERE graph_id='business_context'
        )
      FROM rvbbit.business_context_state state
     WHERE state.singleton
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_context_index_node(
    p_kind text,
    p_label text,
    p_handle text,
    p_properties jsonb,
    p_doc text,
    p_do_embed boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_node_id bigint;
    v_embedding real[];
BEGIN
    v_node_id := rvbbit.kg_assert_node(
        p_kind,p_label,coalesce(p_properties,'{}'::jsonb),1.0,'',0.0,
        'business_context'
    );
    IF p_do_embed THEN
        BEGIN
            v_embedding := rvbbit.embed(p_doc,'','document');
        EXCEPTION WHEN OTHERS THEN
            v_embedding := NULL;
        END;
    END IF;
    INSERT INTO rvbbit.catalog_docs(
        node_id,graph_id,kind,schema_name,rel_name,col_name,doc,
        embedding,embedded_at,updated_at
    ) VALUES (
        v_node_id,'business_context',p_kind,NULL,p_handle,NULL,p_doc,
        v_embedding,CASE WHEN v_embedding IS NOT NULL THEN clock_timestamp() END,
        clock_timestamp()
    )
    ON CONFLICT (graph_id,node_id) DO UPDATE SET
        kind=EXCLUDED.kind,rel_name=EXCLUDED.rel_name,doc=EXCLUDED.doc,
        embedding=EXCLUDED.embedding,embedded_at=EXCLUDED.embedded_at,
        updated_at=EXCLUDED.updated_at;
    RETURN jsonb_build_object(
        'node_id',v_node_id,
        'embedded',v_embedding IS NOT NULL
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_crawl(
    p_do_embed boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph constant text := 'business_context';
    v_system record;
    v_metric record;
    v_edge jsonb;
    v_dimension jsonb;
    v_source text;
    v_system_label text;
    v_system_handle text;
    v_metric_label text;
    v_metric_handle text;
    v_object_label text;
    v_object_handle text;
    v_source_label text;
    v_governed_metric text;
    v_metric_names text;
    v_dimension_names text;
    v_relationships text;
    v_doc text;
    v_result jsonb;
    v_nodes integer := 0;
    v_embedded integer := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business_context_crawl',0
    ));

    DELETE FROM rvbbit.catalog_docs WHERE graph_id=v_graph;
    DELETE FROM rvbbit.kg_nodes WHERE graph_id=v_graph;

    FOR v_system IN
        SELECT summary.*
          FROM rvbbit.business_value_graph_summary summary
         WHERE summary.status IN ('proposed','approved','promoted','needs_revision')
           AND summary.validated_metric_count>0
           AND NOT summary.fallback
         ORDER BY CASE summary.status WHEN 'promoted' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                  summary.updated_at DESC
    LOOP
        v_system_label := v_system.graph_id::text;
        v_system_handle := 'value_system:'||v_system.graph_id::text;
        SELECT string_agg(metric->>'name',', ' ORDER BY metric->>'name')
          INTO v_metric_names
          FROM jsonb_array_elements(v_system.graph->'metrics') metric;
        SELECT string_agg(
                   coalesce(edge->>'parent_metric_key','?')||' '
                   ||coalesce(edge->>'relationship','relates_to')||' '
                   ||coalesce(edge->>'child_metric_key','?'),'; '
                   ORDER BY edge->>'parent_metric_key',edge->>'child_metric_key'
               )
          INTO v_relationships
          FROM jsonb_array_elements(v_system.graph->'edges') edge;
        v_doc := concat_ws(E'\n',
            'Business value system: '||v_system.name||'.',
            'Domain: '||v_system.domain_key||'. Status: '||v_system.status||'.',
            nullif(btrim(v_system.summary),''),
            'Metrics: '||coalesce(v_metric_names,'none')||'.',
            CASE WHEN v_relationships IS NOT NULL
                 THEN 'Value relationships: '||v_relationships||'.' END,
            'Source relations: '||array_to_string(v_system.source_relations,', ')||'.',
            CASE WHEN v_system.status='promoted'
                 THEN 'Governed cube: cubes.'||v_system.cube_name||'. Use the governed metrics instead of rebuilding their arithmetic.'
                 ELSE 'This is a reconciled review proposal, not yet governed company logic.' END
        );
        v_result := rvbbit._business_context_index_node(
            'business_value_system',v_system_label,v_system_handle,
            jsonb_strip_nulls(jsonb_build_object(
                'graph_id',v_system.graph_id,
                'domain_key',v_system.domain_key,
                'name',v_system.name,
                'summary',v_system.summary,
                'status',v_system.status,
                'source_relations',to_jsonb(v_system.source_relations),
                'primary_metric_key',v_system.primary_metric_key,
                'reconciled',v_system.reconciled,
                'cube_name',v_system.cube_name,
                'cube_version',v_system.cube_version,
                'metric_versions',v_system.metric_versions,
                'updated_at',v_system.updated_at
            )),v_doc,p_do_embed
        );
        v_nodes := v_nodes+1;
        v_embedded := v_embedded+CASE
            WHEN (v_result->>'embedded')::boolean THEN 1 ELSE 0 END;

        FOR v_source IN SELECT unnest(v_system.source_relations)
        LOOP
            v_source_label := v_system.graph_id::text||':'||v_source;
            PERFORM rvbbit.kg_assert_node(
                'business_source_relation',v_source_label,
                jsonb_build_object(
                    'graph_id',v_system.graph_id,
                    'relation',v_source
                ),1.0,'',0.0,v_graph
            );
            PERFORM rvbbit.kg_assert_edge(
                'business_value_system',v_system_label,'derived_from',
                'business_source_relation',v_source_label,1.0,'{}'::jsonb,
                jsonb_build_object('relation',v_source),'',0.0,v_graph
            );
        END LOOP;

        FOR v_metric IN
            SELECT catalog.*,
                   governed.name AS governed_metric_name,
                   governed.version AS governed_metric_version
              FROM rvbbit.business_value_metric_catalog catalog
              LEFT JOIN LATERAL (
                  SELECT metric.name,metric.version
                    FROM rvbbit.metric_catalog metric
                   WHERE metric.labels->>'business_value_graph_id'=catalog.graph_id::text
                     AND metric.labels->>'business_value_metric_key'=catalog.metric_key
                   ORDER BY metric.version DESC
                   LIMIT 1
              ) governed ON true
             WHERE catalog.graph_id=v_system.graph_id
             ORDER BY CASE WHEN catalog.is_primary THEN 0 ELSE 1 END,catalog.metric_key
        LOOP
            v_metric_label := v_system.graph_id::text||':'||v_metric.metric_key;
            v_metric_handle := 'business_metric:'||v_metric_label;
            v_governed_metric := v_metric.governed_metric_name;
            SELECT string_agg(
                       coalesce(dimension->>'name',dimension->>'key'),', '
                       ORDER BY coalesce(dimension->>'name',dimension->>'key')
                   )
              INTO v_dimension_names
              FROM jsonb_array_elements(v_metric.dimensions) dimension;
            v_doc := concat_ws(E'\n',
                'Business metric: '||v_metric.metric_name||'.',
                'Value system: '||v_system.name||'. Domain: '||v_system.domain_key||'.',
                'Meaning: '||coalesce(v_metric.description,'')||'.',
                'Formula: '||coalesce(v_metric.formula,'unspecified')||'.',
                'Role: '||v_metric.metric_kind||'. Unit: '||v_metric.unit||'. Grain: '||coalesce(v_metric.grain,'unspecified')||'.',
                'Time semantics: '||coalesce(v_metric.time_semantics,'unspecified')||'.',
                CASE WHEN v_dimension_names IS NOT NULL
                     THEN 'Validated contributors and dimensions: '||v_dimension_names||'.' END,
                CASE WHEN v_governed_metric IS NOT NULL
                     THEN 'Governed metric: '||v_governed_metric||'. Evaluate it with metric(), and use Business Context change analysis for attribution.'
                     ELSE 'Status: '||v_system.status||'; this definition remains a review proposal.' END
            );
            v_result := rvbbit._business_context_index_node(
                'business_metric',v_metric_label,v_metric_handle,
                jsonb_strip_nulls(jsonb_build_object(
                    'graph_id',v_system.graph_id,
                    'domain_key',v_system.domain_key,
                    'value_system_name',v_system.name,
                    'status',v_system.status,
                    'metric_key',v_metric.metric_key,
                    'metric_name',v_metric.metric_name,
                    'metric_kind',v_metric.metric_kind,
                    'unit',v_metric.unit,
                    'formula',v_metric.formula,
                    'grain',v_metric.grain,
                    'time_semantics',v_metric.time_semantics,
                    'dimensions',v_metric.dimensions,
                    'confidence',v_metric.confidence,
                    'is_primary',v_metric.is_primary,
                    'governed_metric_name',v_governed_metric,
                    'governed_metric_version',v_metric.governed_metric_version,
                    'cube_name',v_system.cube_name
                )),v_doc,p_do_embed
            );
            v_nodes := v_nodes+1;
            v_embedded := v_embedded+CASE
                WHEN (v_result->>'embedded')::boolean THEN 1 ELSE 0 END;
            PERFORM rvbbit.kg_assert_edge(
                'business_value_system',v_system_label,'has_metric',
                'business_metric',v_metric_label,1.0,'{}'::jsonb,
                jsonb_build_object(
                    'metric_key',v_metric.metric_key,
                    'is_primary',v_metric.is_primary
                ),'',0.0,v_graph
            );

            FOR v_dimension IN SELECT value FROM jsonb_array_elements(v_metric.dimensions)
            LOOP
                v_object_label := v_system.graph_id::text||':'||(v_dimension->>'key');
                PERFORM rvbbit.kg_assert_node(
                    'business_object',v_object_label,
                    jsonb_build_object(
                        'graph_id',v_system.graph_id,
                        'domain_key',v_system.domain_key,
                        'value_system_name',v_system.name,
                        'status',v_system.status,
                        'key',v_dimension->>'key',
                        'name',coalesce(v_dimension->>'name',v_dimension->>'key')
                    ),1.0,'',0.0,v_graph
                );
                PERFORM rvbbit.kg_assert_edge(
                    'business_metric',v_metric_label,'sliced_by',
                    'business_object',v_object_label,1.0,'{}'::jsonb,
                    jsonb_build_object('dimension_key',v_dimension->>'key'),'',0.0,v_graph
                );
            END LOOP;
        END LOOP;

        -- Index each shared contributor once with the complete set of metrics
        -- it can explain. This avoids whichever metric happened to be visited
        -- last becoming the object's apparent meaning.
        FOR v_metric IN
            SELECT dimension->>'key' AS dimension_key,
                   coalesce(dimension->>'name',dimension->>'key') AS dimension_name,
                   string_agg(DISTINCT catalog.metric_name,', ' ORDER BY catalog.metric_name) AS metric_names,
                   jsonb_agg(DISTINCT catalog.metric_key) AS metric_keys
              FROM rvbbit.business_value_metric_catalog catalog
             CROSS JOIN LATERAL jsonb_array_elements(catalog.dimensions) dimension
             WHERE catalog.graph_id=v_system.graph_id
             GROUP BY dimension->>'key',coalesce(dimension->>'name',dimension->>'key')
             ORDER BY coalesce(dimension->>'name',dimension->>'key')
        LOOP
            v_object_label := v_system.graph_id::text||':'||v_metric.dimension_key;
            v_object_handle := 'business_object:'||v_system.domain_key||':'||v_metric.dimension_key;
            v_doc := concat_ws(E'\n',
                'Business object or contributor: '||v_metric.dimension_name||'.',
                'Value system: '||v_system.name||'. Domain: '||v_system.domain_key||'.',
                'Validated dimension key: '||v_metric.dimension_key||'.',
                'This contributor can explain movement in: '||v_metric.metric_names||'.'
            );
            v_result := rvbbit._business_context_index_node(
                'business_object',v_object_label,v_object_handle,
                jsonb_build_object(
                    'graph_id',v_system.graph_id,
                    'domain_key',v_system.domain_key,
                    'value_system_name',v_system.name,
                    'status',v_system.status,
                    'key',v_metric.dimension_key,
                    'name',v_metric.dimension_name,
                    'metric_keys',v_metric.metric_keys
                ),v_doc,p_do_embed
            );
        END LOOP;

        FOR v_edge IN SELECT value FROM jsonb_array_elements(v_system.graph->'edges')
        LOOP
            PERFORM rvbbit.kg_assert_edge(
                'business_metric',v_system.graph_id::text||':'||(v_edge->>'parent_metric_key'),
                v_edge->>'relationship',
                'business_metric',v_system.graph_id::text||':'||(v_edge->>'child_metric_key'),
                coalesce((v_edge->>'confidence')::double precision,1.0),
                jsonb_build_object('text',coalesce(v_edge->>'rationale','')),
                jsonb_strip_nulls(jsonb_build_object(
                    'rationale',v_edge->>'rationale',
                    'graph_id',v_system.graph_id
                )),'',0.0,v_graph
            );
        END LOOP;
    END LOOP;

    -- Count from the projection because a dimension shared by several metrics
    -- is intentionally one node even though it is encountered repeatedly.
    SELECT count(*),count(*) FILTER (WHERE embedding IS NOT NULL)
      INTO v_nodes,v_embedded
      FROM rvbbit.catalog_docs
     WHERE graph_id=v_graph;
    UPDATE rvbbit.business_context_state
       SET crawled_at=clock_timestamp(),node_count=v_nodes,
           embedded_count=v_embedded,last_error=NULL
     WHERE singleton;
    RETURN jsonb_build_object(
        'graph_id',v_graph,
        'nodes',v_nodes,
        'embedded',v_embedded,
        'crawled_at',clock_timestamp()
    );
EXCEPTION WHEN OTHERS THEN
    UPDATE rvbbit.business_context_state
       SET last_error=SQLERRM
     WHERE singleton;
    RAISE;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_search(
    p_query text,
    p_k integer DEFAULT 8,
    p_statuses text[] DEFAULT ARRAY['promoted','approved','proposed']::text[]
) RETURNS TABLE(
    node_id bigint,
    kind text,
    handle text,
    score double precision,
    doc text,
    properties jsonb
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
          ARRAY['business_value_system','business_metric','business_object'],
          'business_context'
      ) search
      JOIN rvbbit.kg_nodes node
        ON node.node_id=search.node_id AND node.graph_id='business_context'
     WHERE p_statuses IS NULL
        OR node.properties->>'status'=ANY(p_statuses)
     ORDER BY
         CASE node.properties->>'status'
             WHEN 'promoted' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
         score DESC
     LIMIT greatest(least(coalesce(p_k,8),25),1)
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_search(text,integer,text[]) IS
    'Semantic retrieval over compact Value System, business metric, and contributor projections. The receipt-bearing Value Graph remains the source of truth.';

COMMENT ON FUNCTION rvbbit.business_context_crawl(boolean) IS
    'Rebuild the agent-facing Business Context KG from reconciled Value Graph proposals and promotions.';

-- Installing the projection makes any existing graphs discoverable on the
-- next search without performing remote embedding calls during migration.
UPDATE rvbbit.business_context_state
   SET dirty_at=clock_timestamp()
 WHERE singleton;

-- capability_search() is Calliope's system-wide library, but its Warehouse
-- MCP corpus is deliberately generated/static rather than discovered over
-- its own authenticated endpoint. Publish this slice there as well so an
-- agent asking "how can I understand this business?" discovers the semantic
-- route instead of falling back to information_schema.
INSERT INTO rvbbit.capability_mcp_tools (name,summary) VALUES
(
    'search_business_context',
    'Search the company''s interpreted business meaning: reconciled Value Systems, governed business metrics, and validated contributors or dimensions. Use this before raw table exploration when the request is about what the business measures, what drives a number, or how the data fits together.'
),
(
    'get_business_value_system',
    'Open one receipt-backed Business Value System by search handle, UUID, domain, cube, or governed metric. Returns its meaning, formulas, validated dimensions, value relationships, source lineage, reconciliation evidence, and promotion state.'
),
(
    'business_metric_change',
    'Explain the movement of a promoted governed metric between two periods through one of its validated business dimensions. Uses the promoted compact cube and returns signed contributor changes and shares without reconstructing business logic ad hoc.'
)
ON CONFLICT (name) DO UPDATE SET summary=EXCLUDED.summary;

-- Project only the new rows now, without making a migration depend on a
-- remote embedder. A normal capability crawl will add embeddings later; the
-- lexical documents are immediately searchable in the meantime.
DO $projection$
DECLARE
    v_rec record;
    v_doc text;
    v_node bigint;
BEGIN
    FOR v_rec IN
        SELECT name,summary
          FROM rvbbit.capability_mcp_tools
         WHERE name IN (
             'search_business_context',
             'get_business_value_system',
             'business_metric_change'
         )
    LOOP
        v_doc := rvbbit.capability_doc(
            'cap_mcp_tool',v_rec.name,
            'Warehouse MCP connector tool (external agents)',
            v_rec.summary ||
                ' NOT a local SQL function - exposed to agents through the Warehouse MCP connector.',
            '','varies',ARRAY['mcp','business-context','metrics','value-system']
        );
        v_node := rvbbit.kg_assert_node(
            'cap_mcp_tool',v_rec.name,
            jsonb_build_object('search_doc',v_doc),
            1.0,'',0.0,'rvbbit_capabilities'
        );
        INSERT INTO rvbbit.catalog_docs(
            node_id,graph_id,kind,schema_name,rel_name,col_name,
            doc,embedding,embedded_at,updated_at
        ) VALUES (
            v_node,'rvbbit_capabilities','cap_mcp_tool',NULL,v_rec.name,NULL,
            v_doc,NULL,NULL,clock_timestamp()
        )
        ON CONFLICT (graph_id,node_id) DO UPDATE SET
            kind=EXCLUDED.kind,
            rel_name=EXCLUDED.rel_name,
            doc=EXCLUDED.doc,
            embedding=NULL,
            embedded_at=NULL,
            updated_at=clock_timestamp();
    END LOOP;
END
$projection$;
