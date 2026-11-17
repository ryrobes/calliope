-- 0503: the capability index stops re-crawling on every search.
--
-- capability_seed lists some SQL helpers as kind cap_operator (choose,
-- choice_question, branch, decision_tree, decision_tree_batch, box_question,
-- image_part, ...) although they are functions, not rvbbit.operators rows.
-- capability_crawl() writes their docs from the seed; capability_prune_stale()
-- then deleted every cap_operator doc without an operator row, so
-- capability_search_stale() found those seed docs missing and stayed true.
-- Calliope's capability_search runs a full crawl whenever the index is stale,
-- so every tool lookup re-crawled the whole index inside the agent's turn
-- (519 s on a CPU embedder with a cold cache on the AWS pilot). The prune now
-- keeps any doc that a seed row describes.

CREATE OR REPLACE FUNCTION rvbbit.capability_prune_stale()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_graph text := 'rvbbit_capabilities';
    v_docs int := 0; v_nodes int := 0; v_edges int := 0; v_by jsonb;
BEGIN
    DROP TABLE IF EXISTS _cap_stale;
    CREATE TEMP TABLE _cap_stale (node_id bigint, kind text, rel_name text) ON COMMIT DROP;
    INSERT INTO _cap_stale (node_id, kind, rel_name)
    SELECT c.node_id, c.kind, c.rel_name
      FROM rvbbit.catalog_docs c
     WHERE c.graph_id = v_graph AND (
           (c.kind = 'cap_operator' AND NOT EXISTS (SELECT 1 FROM rvbbit.operators o WHERE o.name = c.rel_name AND coalesce(o.visibility, 'public') <> 'kit'))
        OR (c.kind = 'cap_brain'    AND NOT EXISTS (SELECT 1 FROM rvbbit.brain_sources s WHERE s.label = c.rel_name))
        OR (c.kind = 'cap_pack'     AND NOT EXISTS (SELECT 1 FROM rvbbit.capability_catalog k WHERE k.name = c.rel_name AND k.active))
        OR (c.kind = 'cap_mcp_tool' AND to_regclass('rvbbit.capability_mcp_tools') IS NOT NULL
                                    AND NOT EXISTS (SELECT 1 FROM rvbbit.capability_mcp_tools t WHERE t.name = c.rel_name))
        OR (c.kind = 'cap_metric'   AND NOT EXISTS (SELECT 1 FROM rvbbit.metric_defs m WHERE m.name = c.rel_name))
        OR (c.kind = 'cap_cube'     AND NOT EXISTS (SELECT 1 FROM rvbbit.cube_defs d WHERE d.name = c.rel_name)))
       -- A curated seed is a source too: the crawl writes every seed row's doc,
       -- whatever its kind, and capability_search_stale() expects it.
       AND NOT EXISTS (SELECT 1 FROM rvbbit.capability_seed s WHERE s.kind = c.kind AND s.name = c.rel_name);
    SELECT coalesce(jsonb_object_agg(kind, n), '{}'::jsonb) INTO v_by FROM (SELECT kind, count(*) AS n FROM _cap_stale GROUP BY 1) x;
    DELETE FROM rvbbit.catalog_docs c USING _cap_stale s WHERE c.graph_id = v_graph AND c.node_id = s.node_id;
    GET DIAGNOSTICS v_docs = ROW_COUNT;
    DELETE FROM rvbbit.kg_edges e USING _cap_stale s WHERE e.graph_id = v_graph AND (e.subject_node_id = s.node_id OR e.object_node_id = s.node_id);
    GET DIAGNOSTICS v_edges = ROW_COUNT;
    DELETE FROM rvbbit.kg_aliases a USING _cap_stale s WHERE a.graph_id = v_graph AND a.node_id = s.node_id;
    DELETE FROM rvbbit.kg_nodes n USING _cap_stale s WHERE n.graph_id = v_graph AND n.node_id = s.node_id;
    GET DIAGNOSTICS v_nodes = ROW_COUNT;
    RETURN jsonb_build_object('docs', v_docs, 'nodes', v_nodes, 'edges', v_edges, 'by_kind', v_by);
END $fn$;
COMMENT ON FUNCTION rvbbit.capability_prune_stale() IS 'Removes capability docs and KG nodes whose source is gone — an operator no longer registered, a Brain source, a pack no longer active, a Warehouse tool, a metric, a cube — by the same sources capability_crawl builds from, curated seed rows included. The crawl only upserts; this is its forgetting.';
