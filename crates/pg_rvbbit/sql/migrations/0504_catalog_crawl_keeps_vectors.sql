-- 0504: the catalog crawl stops re-embedding docs whose meaning did not change.
--
-- Catalog docs carry live statistics (row counts, distinct counts, null shares)
-- and sampled example values, so a growing table rewrites most of its column
-- docs on every crawl, and each rewrite missed the embedding cache. On a CPU
-- embedder that cost about an hour per crawl, three crawls a day (AWS pilot,
-- 2026-11-11: 6,812 of 34,405 column docs changed between two runs, 4,093 of
-- them only in their numbers). A doc now keeps its vector when only numbers or
-- examples changed and the vector is younger than
-- rvbbit.catalog_vector_reuse_days (default 7; 0 turns reuse off). embedded_at
-- keeps the time the vector was computed, so the age bound holds across runs.
--
-- Also: one refused embed call used to stop embedding for the rest of the table,
-- and the upsert then wrote NULL over each remaining doc's good vector (8,507
-- docs on the pilot). The crawl now gives up only after three failures in a row,
-- and the upsert never replaces a vector with NULL.

-- What a doc says once numbers and sampled examples are set aside.
CREATE OR REPLACE FUNCTION rvbbit._catalog_doc_meaning(p_doc text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT regexp_replace(regexp_replace(p_doc, 'Examples:.*$', ''), '[0-9]+', '#', 'g')
$fn$;

-- The stored vector a crawl may keep for this doc, if any: same object, same
-- meaning, embedded within the reuse window.
CREATE OR REPLACE FUNCTION rvbbit._catalog_reusable_vector(
    p_graph  text,
    p_schema text,
    p_rel    text,
    p_col    text,
    p_doc    text,
    OUT embedding   real[],
    OUT embedded_at timestamptz)
RETURNS SETOF record LANGUAGE sql STABLE AS $fn$
    SELECT o.embedding, o.embedded_at
      FROM rvbbit.catalog_docs o
     WHERE o.graph_id = p_graph
       AND o.schema_name = p_schema
       AND o.rel_name = p_rel
       AND (o.col_name = p_col OR (p_col IS NULL AND o.col_name IS NULL))
       AND o.kind = CASE WHEN p_col IS NULL THEN 'db_table' ELSE 'db_column' END
       AND o.embedding IS NOT NULL
       AND o.embedded_at > now() - make_interval(days => coalesce(
               nullif(current_setting('rvbbit.catalog_vector_reuse_days', true), '')::int, 7))
       AND rvbbit._catalog_doc_meaning(o.doc) = rvbbit._catalog_doc_meaning(p_doc)
     LIMIT 1
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._catalog_crawl_one(
    p_run              bigint,
    p_reloid           oid,
    p_graph            text,
    p_sample_rows      int     DEFAULT 50000,
    p_examples_k       int     DEFAULT 12,
    p_do_embed         boolean DEFAULT true,
    p_embed_specialist text    DEFAULT '',
    p_defer_shared     boolean DEFAULT false)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    fp         jsonb;
    v_col      jsonb;
    v_schema   text;
    v_table    text;
    v_comment  text;
    v_tlabel   text;
    v_clabel   text;
    v_tnode    bigint;
    v_cnode    bigint;
    v_doc      text;
    v_vec      real[];
    v_embed_ok boolean := p_do_embed;
    v_cols     bigint := 0;
    v_edges    bigint := 0;
    v_emb      bigint := 0;
    v_reused   bigint := 0;
    v_fails    int    := 0;
    v_vec_at   timestamptz;
    v_need     text[];
BEGIN
    fp := rvbbit.catalog_fingerprint_table(p_reloid::regclass, p_sample_rows, p_examples_k);

    v_schema  := fp->>'schema';
    v_table   := fp->>'table';
    v_comment := fp->>'comment';
    v_tlabel  := v_schema || '.' || v_table;

    -- Batch-warm the embedding cache for the docs that need a new vector, in ONE
    -- embedder call, so the per-doc rvbbit.embed() calls below resolve from cache.
    -- Docs whose meaning did not change keep their vector (_catalog_reusable_vector)
    -- and are left out. Best-effort: on failure the per-doc path still runs.
    IF v_embed_ok THEN
        BEGIN
            SELECT array_agg(d.doc) INTO v_need
              FROM (SELECT rvbbit.catalog_table_doc(fp) AS doc, NULL::text AS col
                    UNION ALL
                    SELECT rvbbit.catalog_column_doc(v_schema, v_table, v_comment, e), e->>'name'
                      FROM jsonb_array_elements(fp->'columns') AS e) d
             WHERE NOT EXISTS (SELECT 1 FROM rvbbit._catalog_reusable_vector(
                                   p_graph, v_schema, v_table, d.col, d.doc) r);
            IF v_need IS NOT NULL THEN
                PERFORM rvbbit.embed_batch(v_need, p_embed_specialist, 'document');
            END IF;
        EXCEPTION WHEN others THEN NULL;  -- fall back to per-doc embed
        END;
    END IF;
    -- schema node + has_table edge are SHARED across shards (every table touches
    -- the one schema node) → deferred to the parent's serial pass when requested.
    IF NOT p_defer_shared THEN
        PERFORM rvbbit.kg_assert_node('db_schema', v_schema,
                    jsonb_build_object('name', v_schema), 1.0, '', 0.0, p_graph);
    END IF;

    -- table node (full fingerprint summary + search_doc) — disjoint per table
    v_doc := rvbbit.catalog_table_doc(fp);
    v_tnode := rvbbit.kg_assert_node('db_table', v_tlabel,
                jsonb_strip_nulls(jsonb_build_object(
                    'oid',         fp->>'oid',
                    'schema',      v_schema,
                    'table',       v_table,
                    'relkind',     fp->>'relkind',
                    'n_rows',      fp->>'n_rows',
                    'n_sampled',   fp->>'n_sampled',
                    'size_bytes',  fp->>'size_bytes',
                    'comment',     v_comment,
                    'n_columns',   fp->>'n_columns',
                    'profiled_at', fp->>'profiled_at',
                    'search_doc',  v_doc)),
                1.0, '', 0.0, p_graph);

    IF NOT p_defer_shared THEN
        PERFORM rvbbit.kg_assert_edge('db_schema', v_schema, 'has_table',
                    'db_table', v_tlabel, 1.0, '{}'::jsonb, '{}'::jsonb, '', 0.0, p_graph);
        v_edges := v_edges + 1;
    END IF;

    PERFORM rvbbit.kg_link_evidence(
                target_node_id => v_tnode,
                source_table   => p_reloid::regclass,
                source_pk      => fp->>'oid',
                evidence_text  => left(v_doc, 2000),
                confidence     => 1.0,
                graph          => p_graph);

    v_vec := NULL;
    v_vec_at := NULL;
    SELECT r.embedding, r.embedded_at INTO v_vec, v_vec_at
      FROM rvbbit._catalog_reusable_vector(p_graph, v_schema, v_table, NULL, v_doc) r;
    IF v_vec IS NOT NULL THEN
        v_reused := v_reused + 1;
    ELSIF v_embed_ok THEN
        BEGIN
            v_vec := rvbbit.embed(v_doc, p_embed_specialist, 'document');
            v_vec_at := now();
            v_fails := 0;
        EXCEPTION WHEN others THEN
            -- A busy embedder refuses one call; stop only after three in a row.
            -- The doc keeps its previous vector (the upsert below never nulls one).
            v_vec := NULL;
            v_fails := v_fails + 1;
            IF v_fails >= 3 THEN v_embed_ok := false; END IF;
        END;
    END IF;
    INSERT INTO rvbbit.catalog_docs
        (node_id, graph_id, kind, schema_name, rel_name, col_name, doc, embedding, embedded_at, updated_at)
    VALUES (v_tnode, p_graph, 'db_table', v_schema, v_table, NULL, v_doc, v_vec,
            CASE WHEN v_vec IS NOT NULL THEN v_vec_at END, now())
    ON CONFLICT (graph_id, node_id) DO UPDATE SET
        kind = EXCLUDED.kind, schema_name = EXCLUDED.schema_name,
        rel_name = EXCLUDED.rel_name, col_name = EXCLUDED.col_name,
        doc = EXCLUDED.doc,
        embedding = COALESCE(EXCLUDED.embedding, rvbbit.catalog_docs.embedding),
        embedded_at = CASE WHEN EXCLUDED.embedding IS NULL THEN rvbbit.catalog_docs.embedded_at
                           ELSE EXCLUDED.embedded_at END,
        updated_at = now();
    IF v_vec IS NOT NULL THEN v_emb := v_emb + 1; END IF;

    INSERT INTO rvbbit.catalog_snapshots
        (run_id, graph_id, node_id, kind, schema_name, rel_name, col_name, obj_key, fingerprint, embedding)
    VALUES (p_run, p_graph, v_tnode, 'db_table', v_schema, v_table, NULL, v_tlabel,
            fp - 'columns', v_vec);

    FOR v_col IN SELECT e FROM jsonb_array_elements(fp->'columns') AS e
    LOOP
        v_clabel := v_tlabel || '.' || (v_col->>'name');
        v_doc := rvbbit.catalog_column_doc(v_schema, v_table, v_comment, v_col);
        v_cnode := rvbbit.kg_assert_node('db_column', v_clabel,
                    jsonb_strip_nulls((v_col - 'value_dist' - 'quantiles') || jsonb_build_object(
                        'schema',     v_schema,
                        'table',      v_table,
                        'table_oid',  fp->>'oid',
                        'search_doc', v_doc)),
                    1.0, '', 0.0, p_graph);
        v_cols := v_cols + 1;

        -- has_column: subject + object are both THIS table's nodes → disjoint
        PERFORM rvbbit.kg_assert_edge('db_table', v_tlabel, 'has_column',
                    'db_column', v_clabel, 1.0, '{}'::jsonb,
                    jsonb_build_object('ordinal', v_col->>'ordinal'), '', 0.0, p_graph);
        v_edges := v_edges + 1;

        -- references (FK): object is a column owned by ANOTHER table (possibly
        -- another shard) → deferred to the parent's serial pass when requested.
        IF NOT p_defer_shared
           AND (v_col->>'is_fk') = 'true' AND (v_col->>'fk_target') IS NOT NULL THEN
            PERFORM rvbbit.kg_assert_edge('db_column', v_clabel, 'references',
                        'db_column', v_col->>'fk_target', 1.0, '{}'::jsonb, '{}'::jsonb,
                        '', 0.0, p_graph);
            v_edges := v_edges + 1;
        END IF;

        PERFORM rvbbit.kg_link_evidence(
                    target_node_id => v_cnode,
                    source_table   => p_reloid::regclass,
                    source_column  => v_col->>'name',
                    source_pk      => fp->>'oid',
                    evidence_text  => left(v_doc, 2000),
                    confidence     => 1.0,
                    graph          => p_graph);

        v_vec := NULL;
        v_vec_at := NULL;
        SELECT r.embedding, r.embedded_at INTO v_vec, v_vec_at
          FROM rvbbit._catalog_reusable_vector(p_graph, v_schema, v_table, v_col->>'name', v_doc) r;
        IF v_vec IS NOT NULL THEN
            v_reused := v_reused + 1;
        ELSIF v_embed_ok THEN
            BEGIN
                v_vec := rvbbit.embed(v_doc, p_embed_specialist, 'document');
                v_vec_at := now();
                v_fails := 0;
            EXCEPTION WHEN others THEN
                -- A busy embedder refuses one call; stop only after three in a row.
                -- The doc keeps its previous vector (the upsert below never nulls one).
                v_vec := NULL;
                v_fails := v_fails + 1;
                IF v_fails >= 3 THEN v_embed_ok := false; END IF;
            END;
        END IF;
        INSERT INTO rvbbit.catalog_docs
            (node_id, graph_id, kind, schema_name, rel_name, col_name, doc, embedding, embedded_at, updated_at)
        VALUES (v_cnode, p_graph, 'db_column', v_schema, v_table, v_col->>'name', v_doc, v_vec,
                CASE WHEN v_vec IS NOT NULL THEN v_vec_at END, now())
        ON CONFLICT (graph_id, node_id) DO UPDATE SET
            kind = EXCLUDED.kind, schema_name = EXCLUDED.schema_name,
            rel_name = EXCLUDED.rel_name, col_name = EXCLUDED.col_name,
            doc = EXCLUDED.doc,
            embedding = COALESCE(EXCLUDED.embedding, rvbbit.catalog_docs.embedding),
            embedded_at = CASE WHEN EXCLUDED.embedding IS NULL THEN rvbbit.catalog_docs.embedded_at
                               ELSE EXCLUDED.embedded_at END,
            updated_at = now();
        IF v_vec IS NOT NULL THEN v_emb := v_emb + 1; END IF;

        INSERT INTO rvbbit.catalog_snapshots
            (run_id, graph_id, node_id, kind, schema_name, rel_name, col_name, obj_key, fingerprint, embedding)
        VALUES (p_run, p_graph, v_cnode, 'db_column', v_schema, v_table, v_col->>'name', v_clabel,
                v_col, v_vec);
    END LOOP;

    RETURN jsonb_build_object(
        'tables',        1,
        'columns',       v_cols,
        'edges',         v_edges,
        'docs_embedded', v_emb,
        'docs_reused',   v_reused,
        'schema',        v_schema,
        'table',         v_table,
        'n_rows',        (fp->>'n_rows'),
        'n_columns',     (fp->>'n_columns'));
END $fn$;
