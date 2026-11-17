-- 0358: Retire the Lance runtime without destroying its compatibility metadata.
--
-- The Rust extension no longer links Lance. Existing catalog rows remain
-- inspectable for one release, but they must not affect refresh scheduling or
-- advertise a ready index. Knowledge-graph resolution now proceeds directly
-- from exact aliases to the existing embedding fallback.

UPDATE rvbbit.lance_text_indexes
   SET status = 'disabled',
       status_message = 'Lance backend removed in pg_rvbbit 4.2.10',
       refreshed_at = clock_timestamp()
 WHERE status <> 'disabled';

UPDATE rvbbit.kg_lance_indexes
   SET status = 'disabled',
       status_message = 'Lance backend removed in pg_rvbbit 4.2.10',
       refreshed_at = clock_timestamp()
 WHERE status <> 'disabled';

DO $migration$
DECLARE
    definition text;
    needle text := $needle$
        is_lance := coalesce(cand.lance_accelerated, false)
            AND coalesce(cand.lance_separate, true);
$needle$;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );
    IF position(needle IN definition) > 0 THEN
        definition := replace(
            definition,
            needle,
            E'\n        is_lance := false; -- Lance runtime removed; compatibility metadata is inert.\n'
        );
        EXECUTE definition;
    ELSIF position('is_lance := false' IN definition) = 0 THEN
        RAISE EXCEPTION '0358 could not neutralize _accel_tick_batch Lance state';
    END IF;
END;
$migration$;

CREATE OR REPLACE FUNCTION rvbbit.kg_resolve_node(
    node_kind text,
    node_label text,
    specialist text DEFAULT '',
    match_threshold double precision DEFAULT 0.92,
    graph text DEFAULT NULL
) RETURNS TABLE (
    node_id bigint,
    kind text,
    label text,
    score double precision,
    match_method text
)
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    norm_kind text;
    norm_label text;
    norm_graph text;
BEGIN
    IF node_kind IS NULL OR btrim(node_kind) = '' THEN
        RAISE EXCEPTION 'rvbbit.kg_resolve_node: node_kind must be non-empty';
    END IF;
    IF node_label IS NULL OR btrim(node_label) = '' THEN
        RAISE EXCEPTION 'rvbbit.kg_resolve_node: node_label must be non-empty';
    END IF;
    IF match_threshold IS NULL THEN
        match_threshold := 0.92;
    END IF;
    IF match_threshold < 0.0 OR match_threshold > 1.0 THEN
        RAISE EXCEPTION 'rvbbit.kg_resolve_node: match_threshold must be between 0 and 1';
    END IF;

    norm_kind := rvbbit.kg_normalize_label(node_kind);
    norm_label := rvbbit.kg_normalize_label(node_label);
    norm_graph := rvbbit.kg_normalize_graph(graph);

    RETURN QUERY
    SELECT n.node_id, n.kind, n.label, 1.0::double precision, 'alias'::text
    FROM rvbbit.kg_aliases a
    JOIN rvbbit.kg_nodes n ON n.node_id = a.node_id
    WHERE a.graph_id = norm_graph
      AND n.graph_id = norm_graph
      AND a.kind = norm_kind
      AND a.alias_norm = norm_label
    ORDER BY a.confidence DESC, n.node_id
    LIMIT 1;

    IF FOUND THEN
        RETURN;
    END IF;

    IF match_threshold > 0.0 THEN
        RETURN QUERY
        SELECT n.node_id, n.kind, n.label, s.score, 'embedding'::text
        FROM rvbbit.kg_nodes n
        CROSS JOIN LATERAL (
            SELECT rvbbit.similarity(node_label, n.label, specialist) AS score
        ) s
        WHERE n.graph_id = norm_graph
          AND n.kind = norm_kind
          AND s.score >= match_threshold
        ORDER BY s.score DESC, n.node_id
        LIMIT 10;
    END IF;
END $$;

COMMENT ON FUNCTION rvbbit.kg_resolve_node(text, text, text, double precision, text) IS
    'Resolve a graph node by exact alias, then embedding similarity. Lance indexes are no longer consulted.';
