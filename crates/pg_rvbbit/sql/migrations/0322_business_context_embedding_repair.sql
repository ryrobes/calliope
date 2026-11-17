-- 0322: Keep Business Context search embeddings alive.
--
-- The frequent maintenance lane pre-warms a stale Business Context projection
-- with business_context_crawl(false) so it never waits on the remote embedder.
-- Two defects made that lane destructive instead of merely cheap:
--
--   * the projection upsert wrote embedding=EXCLUDED.embedding, so a no-embed
--     crawl overwrote every existing vector with NULL; and
--   * nothing ever came back to embed the lexical documents ("a normal
--     capability crawl will add embeddings later" named a crawl that does not
--     exist), while business_context_search_stale() judged the projection
--     fresh because crawled_at was recent and documents existed.
--
-- Net effect on a live appliance: node_count=55, embedded_count=0, stale=false,
-- and business_context_search() returned nothing.
--
-- Repairs, all idempotent:
--   * business_context_crawl snapshots existing vectors before its full
--     rebuild and restores them to documents whose text is unchanged, and
--     _business_context_index_node applies the same rule on upsert, so a
--     no-embed pre-warm costs zero embedder calls for a stable projection;
--   * business_context_embed_pending() reports rows still missing a vector,
--     and business_context_embed_missing(limit) embeds only those rows through
--     the configured embedder (Clover on hosted appliances), bounded per call;
--   * business_context_maintenance_tick() runs that bounded repair after the
--     pre-warm so the cron lane converges on a fully embedded projection.
-- Warehouse's first stale read still performs the full embedded crawl and now
-- also repairs missing vectors on demand.

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
    v_embedded boolean := false;
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
    INSERT INTO rvbbit.catalog_docs AS existing(
        node_id,graph_id,kind,schema_name,rel_name,col_name,doc,
        embedding,embedded_at,updated_at
    ) VALUES (
        v_node_id,'business_context',p_kind,NULL,p_handle,NULL,p_doc,
        v_embedding,CASE WHEN v_embedding IS NOT NULL THEN clock_timestamp() END,
        clock_timestamp()
    )
    ON CONFLICT (graph_id,node_id) DO UPDATE SET
        kind=EXCLUDED.kind,rel_name=EXCLUDED.rel_name,doc=EXCLUDED.doc,
        -- A no-embed pre-warm must not destroy a vector that still describes
        -- the same text; only a changed document invalidates it.
        embedding=CASE
            WHEN EXCLUDED.embedding IS NOT NULL THEN EXCLUDED.embedding
            WHEN existing.doc IS NOT DISTINCT FROM EXCLUDED.doc THEN existing.embedding
            ELSE NULL END,
        embedded_at=CASE
            WHEN EXCLUDED.embedding IS NOT NULL THEN EXCLUDED.embedded_at
            WHEN existing.doc IS NOT DISTINCT FROM EXCLUDED.doc THEN existing.embedded_at
            ELSE NULL END,
        updated_at=EXCLUDED.updated_at
    RETURNING (existing.embedding IS NOT NULL) INTO v_embedded;
    RETURN jsonb_build_object(
        'node_id',v_node_id,
        'embedded',coalesce(v_embedded,false)
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit._business_context_index_node(text,text,text,jsonb,text,boolean) IS
    'Upsert one Business Context projection document; a crawl without embedding preserves an existing vector for unchanged text.';

-- The rebuild deletes and re-creates every projection row (fresh KG node ids),
-- so preserving vectors needs a snapshot/restore around the rebuild rather than
-- an upsert clause alone. This is the 0298 body with those two blocks added.
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

    -- 0322: the rebuild replaces every projection row and KG node, so an
    -- existing vector can never survive by upsert alone. Snapshot the vectors
    -- keyed by document text and restore them after re-indexing so a no-embed
    -- pre-warm (p_do_embed=false) leaves unchanged documents fully embedded.
    IF to_regclass('pg_temp._business_context_embedding_snapshot') IS NOT NULL THEN
        DROP TABLE _business_context_embedding_snapshot;
    END IF;
    CREATE TEMP TABLE _business_context_embedding_snapshot ON COMMIT DROP AS
        SELECT kind,rel_name,doc,embedding,embedded_at
          FROM rvbbit.catalog_docs
         WHERE graph_id=v_graph AND embedding IS NOT NULL;

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

    -- 0322: give unchanged documents their previous vector back.
    UPDATE rvbbit.catalog_docs docs
       SET embedding=snapshot.embedding,
           embedded_at=snapshot.embedded_at
      FROM _business_context_embedding_snapshot snapshot
     WHERE docs.graph_id=v_graph
       AND docs.embedding IS NULL
       AND docs.kind=snapshot.kind
       AND docs.rel_name=snapshot.rel_name
       AND docs.doc=snapshot.doc;
    IF to_regclass('pg_temp._business_context_embedding_snapshot') IS NOT NULL THEN
        DROP TABLE _business_context_embedding_snapshot;
    END IF;

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

COMMENT ON FUNCTION rvbbit.business_context_crawl(boolean) IS
    'Rebuild the agent-facing Business Context KG from reconciled Value Graph proposals and promotions; vectors of unchanged documents survive a no-embed rebuild.';

CREATE OR REPLACE FUNCTION rvbbit.business_context_embed_pending()
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.catalog_docs
         WHERE graph_id='business_context'
           AND embedding IS NULL
    )
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_embed_pending() IS
    'True while any Business Context projection document still lacks an embedding; independent of business_context_search_stale().';

CREATE OR REPLACE FUNCTION rvbbit.business_context_embed_missing(
    p_limit integer DEFAULT 200
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph constant text := 'business_context';
    v_row record;
    v_embedding real[];
    v_attempted integer := 0;
    v_embedded integer := 0;
    v_failed integer := 0;
    v_last_error text;
    v_nodes integer := 0;
    v_total_embedded integer := 0;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
        'rvbbit.business_context_embed_missing',0
    )) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    FOR v_row IN
        SELECT graph_id,node_id,doc
          FROM rvbbit.catalog_docs
         WHERE graph_id=v_graph
           AND embedding IS NULL
           AND doc IS NOT NULL
         ORDER BY updated_at,node_id
         LIMIT greatest(1,least(coalesce(p_limit,200),2000))
    LOOP
        v_attempted := v_attempted+1;
        BEGIN
            v_embedding := rvbbit.embed(v_row.doc,'','document');
        EXCEPTION WHEN OTHERS THEN
            v_embedding := NULL;
            v_last_error := left(SQLERRM,500);
        END;
        IF v_embedding IS NULL THEN
            v_failed := v_failed+1;
            CONTINUE;
        END IF;
        UPDATE rvbbit.catalog_docs
           SET embedding=v_embedding,
               embedded_at=clock_timestamp()
         WHERE graph_id=v_row.graph_id
           AND node_id=v_row.node_id
           AND embedding IS NULL;
        IF FOUND THEN
            v_embedded := v_embedded+1;
        END IF;
    END LOOP;
    SELECT count(*),count(*) FILTER (WHERE embedding IS NOT NULL)
      INTO v_nodes,v_total_embedded
      FROM rvbbit.catalog_docs
     WHERE graph_id=v_graph;
    UPDATE rvbbit.business_context_state
       SET node_count=v_nodes,
           embedded_count=v_total_embedded,
           last_error=CASE
               WHEN v_failed>0 AND v_embedded=0 THEN v_last_error
               ELSE last_error END
     WHERE singleton;
    RETURN jsonb_build_object(
        'status',CASE WHEN v_attempted=0 THEN 'complete'
                      WHEN v_failed>0 AND v_embedded=0 THEN 'error'
                      ELSE 'embedded' END,
        'attempted',v_attempted,
        'embedded',v_embedded,
        'failed',v_failed,
        'remaining',v_nodes-v_total_embedded,
        'nodes',v_nodes,
        'embedded_total',v_total_embedded,
        'error',v_last_error
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_embed_missing(integer) IS
    'Embed Business Context projection documents that still lack a vector, bounded per call; safe from cron or a Warehouse request.';

CREATE OR REPLACE FUNCTION rvbbit.business_context_maintenance_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_search jsonb := jsonb_build_object('status','fresh');
    v_embed jsonb := jsonb_build_object('status','complete');
    v_steward jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.business-context-maintenance',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    IF rvbbit.business_context_search_stale() THEN
        BEGIN
            v_search := jsonb_build_object(
                'status','refreshed','result',rvbbit.business_context_crawl(false)
            );
        EXCEPTION WHEN OTHERS THEN
            v_search := jsonb_build_object(
                'status','error','error',left(SQLERRM,500)
            );
        END;
    END IF;
    -- The pre-warm above is lexical only; converge on an embedded projection
    -- a bounded slice at a time so this lane stays cheap but never leaves
    -- semantic retrieval permanently empty.
    IF rvbbit.business_context_embed_pending() THEN
        BEGIN
            v_embed := rvbbit.business_context_embed_missing(200);
        EXCEPTION WHEN OTHERS THEN
            v_embed := jsonb_build_object(
                'status','error','error',left(SQLERRM,500)
            );
        END;
    END IF;
    v_steward := rvbbit.business_context_steward_tick();
    RETURN jsonb_build_object(
        'status','complete','search',v_search,'embed',v_embed,
        'steward',v_steward
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_maintenance_tick() IS
    'Frequent cheap lane: pre-warm a stale Business Context search index, embed any documents still missing vectors, and dispatch at most one structurally dirty domain.';
