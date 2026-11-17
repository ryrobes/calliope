-- 0456 · the capability index forgets what is gone, and removing an MCP server removes all of it
--
-- Callie, on the pilot: capability_search advertised "a corpus of 184 Fireflies
-- meeting documents" weeks after the connection was removed and the Brain data
-- purged. Three additive-only paths conspired:
--   * drop_mcp_server only deleted the mcp_servers row; the operators that
--     generate_mcp_operators registered, their SQL wrappers, the catalog pack
--     row and the capability docs all stayed;
--   * capability_crawl only upserts catalog_docs/kg nodes — a cap_brain doc is
--     built from brain_sources plus a document count at crawl time, so it outlived
--     the purge; eighteen other operator docs outlived their functions;
--   * capability_crawl(false), which the server calls after syncing tool
--     summaries and after defining an object, writes docs without embeddings and
--     nothing backfilled them — 881 of the pilot's 1,274 capability docs had no
--     vector, so the dense seam was blind to them.
--
-- capability_prune_stale() removes docs and nodes whose source row is gone,
-- by the same sources the crawl builds from; capability_embed_missing(limit)
-- embeds what a fast crawl left bare; drop_mcp_server(name) now unpublishes:
-- operators and wrappers, the mcp/<name> catalog row, and the docs — then prunes.

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
        OR (c.kind = 'cap_cube'     AND NOT EXISTS (SELECT 1 FROM rvbbit.cube_defs d WHERE d.name = c.rel_name)));
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
COMMENT ON FUNCTION rvbbit.capability_prune_stale() IS 'Removes capability docs and KG nodes whose source is gone — an operator no longer registered, a Brain source, a pack no longer active, a Warehouse tool, a metric, a cube — by the same sources capability_crawl builds from. The crawl only upserts; this is its forgetting.';

CREATE OR REPLACE FUNCTION rvbbit.capability_embed_missing(p_limit int DEFAULT 1000)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_graph text := 'rvbbit_capabilities';
    r record; v_vec vector; v_done int := 0; v_failed int := 0; v_err text;
BEGIN
    FOR r IN SELECT node_id, doc FROM rvbbit.catalog_docs
              WHERE graph_id = v_graph AND embedding IS NULL ORDER BY updated_at DESC LIMIT greatest(1, p_limit) LOOP
        BEGIN
            v_vec := rvbbit.embed(r.doc, '', 'document');
            UPDATE rvbbit.catalog_docs SET embedding = v_vec, embedded_at = now() WHERE graph_id = v_graph AND node_id = r.node_id;
            v_done := v_done + 1;
        EXCEPTION WHEN OTHERS THEN
            v_failed := v_failed + 1; v_err := left(SQLERRM, 200);
            EXIT WHEN v_failed >= 5;                  -- an embedder that is down stays down; do not hammer it
        END;
    END LOOP;
    RETURN jsonb_strip_nulls(jsonb_build_object('embedded', v_done, 'failed', v_failed, 'error', v_err,
                             'remaining', (SELECT count(*) FROM rvbbit.catalog_docs WHERE graph_id = v_graph AND embedding IS NULL)));
END $fn$;
COMMENT ON FUNCTION rvbbit.capability_embed_missing(int) IS 'Embeds capability docs that a fast crawl (capability_crawl(false)) left without a vector, newest first, stopping after five embedder failures. The dense seam of capability_search is blind to a doc without one.';

-- ── removing a server removes what publishing created ───────────────────────
CREATE OR REPLACE FUNCTION rvbbit.drop_mcp_server(server_name text)
RETURNS void LANGUAGE plpgsql AS $rm$
DECLARE
    t record; r record; v_op text; v_ops text[] := '{}';
BEGIN
    -- the operator names generate_mcp_operators derived from this server's tools
    FOR t IN SELECT name FROM rvbbit.mcp_tools WHERE server = server_name LOOP
        v_op := server_name || '_' ||
            CASE WHEN left(t.name, length(server_name) + 1) = server_name || '_'
                 THEN substr(t.name, length(server_name) + 2) ELSE t.name END;
        v_ops := v_ops || v_op;
    END LOOP;
    -- and any wrapper that still carries the server's prefix from an earlier scan
    FOR r IN SELECT DISTINCT proname FROM pg_proc
              WHERE pronamespace = 'rvbbit'::regnamespace
                AND (proname = ANY(v_ops) OR proname LIKE replace(server_name, '_', '\_') || '\_%' OR proname LIKE '\_op\_' || replace(server_name, '_', '\_') || '\_%') LOOP
        FOR t IN SELECT oid::regprocedure AS sig FROM pg_proc WHERE proname = r.proname AND pronamespace = 'rvbbit'::regnamespace LOOP
            EXECUTE 'DROP FUNCTION IF EXISTS ' || t.sig::text || ' CASCADE';
        END LOOP;
    END LOOP;
    DELETE FROM rvbbit.operators o WHERE o.name = ANY(v_ops) OR o.name LIKE replace(server_name, '_', '\_') || '\_%';
    UPDATE rvbbit.capability_catalog SET active = false WHERE id = 'mcp/' || server_name AND active;
    DELETE FROM rvbbit.mcp_servers WHERE name = server_name;          -- cascades to mcp_tools / mcp_resources
    PERFORM rvbbit.capability_prune_stale();
END $rm$;
COMMENT ON FUNCTION rvbbit.drop_mcp_server(text) IS 'Deregister an MCP server and unpublish what registering it created: its operators and SQL wrappers, the mcp/<name> catalog row, the registry rows (mcp_tools/mcp_resources cascade) and the capability docs that described them. The invocation audit log and cache rows are kept.';
