-- 0437_find_primitive — one search door into the data, with the receipts attached.
--
-- See docs/FIND_PLAN.md. What ships here, in dependency order:
--
--   1. Lexical seam gets an index: generated `lex` tsvector + `ident` columns on
--      rvbbit.catalog_docs with GIN (tsvector) and GIN trigram indexes; the
--      catalog_lexical_knn seam is rewritten onto them (same signature). Before:
--      to_tsvector over every doc per query (0.7–1.4 s on 45k docs). After: ms.
--   2. Dense seam fails LOUD: catalog_dense_knn raises a NOTICE when it refuses
--      (brute-force cap), and rvbbit.dense_seam_status() reports which tier is
--      live so a caller can tell "nothing matches" from "half the search is off"
--      (the pilot ran lexical-only for weeks without anyone noticing).
--   3. pgvector tier refresh becomes build-then-rename: readers block for the
--      rename instead of the whole HNSW build; pgvector_refresh_catalog_if_stale
--      only rebuilds when docs changed. The catalog crawl calls it at its tail.
--   4. Snapshots that make the envelope cheap: rvbbit.relation_refs (which
--      metrics / cubes / dashboards / value-system domains read a relation),
--      rvbbit.usage_snapshot (mcp_activity touches; the live view was a 47 s
--      cold aggregate on the pilot and sat inside search_data_weighted),
--      rvbbit.relation_lineage (role + source system + connection + last load,
--      from the rvbbit mirror registry, Fivetran metadata, or dlt load tables —
--      whichever the install has). rvbbit.find_refresh() rebuilds all of them.
--   5. Values seam: rvbbit.value_index, ONE crawler-style index of distinct text
--      values per (relation, column) with trigram + tsvector indexes; deltas by
--      fingerprint drift, prunes by last_seen, PII-shaped column names skipped.
--      rvbbit.value_index_sweep() is the budgeted background pass.
--   6. rvbbit.find_json / find / find_flat: RRF over the three seams, optional
--      bge rerank (call_specialist_batch, one HTTP call) and Jev judge
--      (clover_decide, ≤8 questions, one call), and per-hit envelope:
--      object, match, lineage, fresh, usage, meaning, ask.
--
-- Everything is idempotent (CREATE OR REPLACE / IF NOT EXISTS) and every gate
-- degrades to "absent", never to an error.

DO $$ BEGIN
    BEGIN CREATE EXTENSION IF NOT EXISTS pg_trgm;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'rvbbit: pg_trgm unavailable (%); substring seams run unindexed', SQLERRM;
    END;
END $$;

-- ═══════════════════════════════════════════════════════════════════════
-- 1. Lexical seam: stored tsvector + identifier columns and their indexes
-- ═══════════════════════════════════════════════════════════════════════

-- Literal-substring helper: LIKE treats % _ \ as pattern characters, and an
-- identifier query like patient_id must NOT let its underscore match any char.
CREATE OR REPLACE FUNCTION rvbbit.like_escape(p text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
    SELECT replace(replace(replace(p, '\', '\\'), '%', '\%'), '_', '\_')
$fn$;

ALTER TABLE rvbbit.catalog_docs
    ADD COLUMN IF NOT EXISTS ident text
        GENERATED ALWAYS AS (
            lower(coalesce(schema_name, '') || '.' || coalesce(rel_name, '') || '.' || coalesce(col_name, ''))
        ) STORED;

ALTER TABLE rvbbit.catalog_docs
    ADD COLUMN IF NOT EXISTS lex tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english'::regconfig,
                left(doc, 32768) || ' ' ||
                replace(replace(
                    coalesce(schema_name, '') || ' ' || coalesce(rel_name, '') || ' ' || coalesce(col_name, ''),
                    '_', ' '), '.', ' '))
        ) STORED;

CREATE INDEX IF NOT EXISTS catalog_docs_lex_gin
    ON rvbbit.catalog_docs USING gin (lex);
CREATE INDEX IF NOT EXISTS catalog_docs_path_idx
    ON rvbbit.catalog_docs (graph_id, schema_name, rel_name, col_name);

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS catalog_docs_ident_trgm
            ON rvbbit.catalog_docs USING gin (ident gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS catalog_docs_doc_trgm
            ON rvbbit.catalog_docs USING gin (left(doc, 32768) gin_trgm_ops);
    END IF;
END $$;

-- Same signature as the original seam (catalog_kg.sql); drop-in for data_search.
CREATE OR REPLACE FUNCTION rvbbit.catalog_lexical_knn(
    query text,
    graph text,
    kinds text[],
    k     int)
RETURNS TABLE (node_id bigint, score float8)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_q   text := btrim(coalesce(query, ''));
    v_pat text;
    v_tsq tsquery;
BEGIN
    IF v_q = '' THEN RETURN; END IF;
    v_pat := '%' || rvbbit.like_escape(v_q) || '%';
    BEGIN
        v_tsq := websearch_to_tsquery('english', v_q);
        IF numnode(v_tsq) = 0 THEN v_tsq := NULL; END IF;
    EXCEPTION WHEN OTHERS THEN v_tsq := NULL; END;

    -- name hit = the object's OWN name (a column's column name, a table's table
    -- name), or the dotted path when the query is dotted. Matching the whole
    -- schema.rel.col path let every column of weekly_growth_performance_cube
    -- claim "growth". Likewise a column doc's trailing "In table X." clause is
    -- table context, not the column's content, so it is stripped before the
    -- literal doc test.
    RETURN QUERY
        SELECT x.node_id, x.sc
          FROM (
            SELECT d.node_id,
                   -- an exact own-name / dotted-path match must always make the
                   -- pool (salesforce.lead has 346 columns whose paths also match)
                   (3.0 * (rtrim(d.ident, '.') = lower(v_q)
                           OR lower(coalesce(d.col_name, '')) = lower(v_q)
                           OR (d.kind <> 'db_column' AND lower(coalesce(d.rel_name, '')) = lower(v_q)))::int
                    + 2.0 * (CASE
                               WHEN position('.' IN v_q) > 0 THEN rtrim(d.ident, '.') LIKE lower(v_pat)
                               WHEN d.kind = 'db_column' THEN lower(coalesce(d.col_name, '')) LIKE lower(v_pat)
                               ELSE lower(coalesce(d.rel_name, '')) LIKE lower(v_pat)
                           END)::int
                    + 1.0 * (regexp_replace(left(d.doc, 32768), '\s+In table [^.]+\.\s*$', '') ILIKE v_pat)::int
                    + CASE WHEN v_tsq IS NOT NULL AND d.lex @@ v_tsq
                           THEN ts_rank_cd(d.lex, v_tsq) ELSE 0 END)::float8 AS sc
              FROM rvbbit.catalog_docs d
             WHERE d.graph_id = graph
               AND (kinds IS NULL OR d.kind = ANY (kinds))
               AND ((v_tsq IS NOT NULL AND d.lex @@ v_tsq)
                    OR d.ident LIKE lower(v_pat)
                    OR left(d.doc, 32768) ILIKE v_pat)
          ) x
         WHERE x.sc > 0
         ORDER BY x.sc DESC, x.node_id
         LIMIT k;
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 2. Dense seam: loud refusal + a status probe
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION rvbbit.catalog_dense_knn(
    q_vec     real[],
    graph     text,
    kinds     text[],
    k         int,
    min_score float8 DEFAULT 0.10)
RETURNS TABLE (node_id bigint, score float8)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_mu  real[];
    v_qc  real[];
    v_cnt bigint;
    v_max constant int := 10000;
BEGIN
    IF q_vec IS NULL OR array_length(q_vec, 1) IS NULL THEN RETURN; END IF;

    SELECT count(*) INTO v_cnt
      FROM rvbbit.catalog_docs d
     WHERE d.graph_id = graph
       AND (kinds IS NULL OR d.kind = ANY (kinds))
       AND d.embedding IS NOT NULL
       AND array_length(d.embedding, 1) = array_length(q_vec, 1);
    IF v_cnt = 0 THEN RETURN; END IF;
    IF v_cnt > v_max THEN
        -- LOUD: the pgvector tier is the only way to search a catalog this big.
        RAISE NOTICE 'rvbbit.catalog_dense_knn: % embedded docs exceed the brute-force cap of % — dense seam OFF for graph %; run SELECT rvbbit.pgvector_refresh_catalog(''%'')',
            v_cnt, v_max, graph, graph;
        RETURN;
    END IF;

    SELECT array_agg(m ORDER BY i) INTO v_mu
      FROM (SELECT t.i, avg(t.e)::real AS m
              FROM rvbbit.catalog_docs d
                   CROSS JOIN LATERAL unnest(d.embedding) WITH ORDINALITY AS t(e, i)
             WHERE d.graph_id = graph
               AND (kinds IS NULL OR d.kind = ANY (kinds))
               AND d.embedding IS NOT NULL
               AND array_length(d.embedding, 1) = array_length(q_vec, 1)
             GROUP BY t.i) z;
    IF v_mu IS NULL THEN RETURN; END IF;

    SELECT array_agg(u.qe - u.me ORDER BY u.i) INTO v_qc
      FROM unnest(q_vec, v_mu) WITH ORDINALITY AS u(qe, me, i);

    RETURN QUERY
        WITH scored AS (
            SELECT d.node_id,
                   (SELECT sum((x.de - x.me) * x.qc)
                           / NULLIF(sqrt(sum((x.de - x.me) * (x.de - x.me)))
                                    * sqrt(sum(x.qc * x.qc)), 0)
                      FROM unnest(d.embedding, v_mu, v_qc) AS x(de, me, qc)) AS cos
              FROM rvbbit.catalog_docs d
             WHERE d.graph_id = graph
               AND (kinds IS NULL OR d.kind = ANY (kinds))
               AND d.embedding IS NOT NULL
               AND array_length(d.embedding, 1) = array_length(q_vec, 1)
        )
        SELECT s.node_id, s.cos
          FROM scored s
         WHERE s.cos IS NOT NULL AND s.cos > min_score
         ORDER BY s.cos DESC
         LIMIT k;
END $fn$;

-- Which dense tier would answer right now, and whether it can. Cheap.
--   {"tier":"pgvector","ready":true,"dim":1024,"n_docs":45367,"refreshed_at":…}
--   {"tier":"brute","ready":false,"n_docs":45367,"cap":10000,"reason":"…"}
CREATE OR REPLACE FUNCTION rvbbit.dense_seam_status(graph text DEFAULT 'db_catalog')
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_graph text := coalesce(nullif(btrim(graph), ''), 'db_catalog');
    v_dim   int;
    v_n     bigint;
    v_istatus text; v_idim int; v_indocs bigint; v_irefreshed timestamptz; v_imsg text;
BEGIN
    SELECT d, c INTO v_dim, v_n
      FROM (SELECT array_length(embedding, 1) AS d, count(*) AS c
              FROM rvbbit.catalog_docs
             WHERE graph_id = v_graph AND embedding IS NOT NULL
             GROUP BY 1 ORDER BY 2 DESC LIMIT 1) q;
    IF v_dim IS NULL THEN
        RETURN jsonb_build_object('tier', 'none', 'ready', false, 'n_docs', 0,
                                  'reason', 'no embedded docs');
    END IF;
    IF to_regclass('rvbbit.pgvector_indexes') IS NOT NULL THEN
        SELECT status, dim, n_docs, refreshed_at, message
          INTO v_istatus, v_idim, v_indocs, v_irefreshed, v_imsg
          FROM rvbbit.pgvector_indexes WHERE graph_id = v_graph;
        IF v_istatus = 'ready' AND v_idim = v_dim
           AND to_regclass('rvbbit.catalog_vec') IS NOT NULL THEN
            RETURN jsonb_build_object('tier', 'pgvector', 'ready', true, 'dim', v_dim,
                                      'n_docs', v_n, 'indexed_docs', v_indocs,
                                      'refreshed_at', v_irefreshed,
                                      'stale_docs', greatest(v_n - coalesce(v_indocs, 0), 0));
        END IF;
    END IF;
    IF v_n > 10000 THEN
        RETURN jsonb_build_object('tier', 'brute', 'ready', false, 'dim', v_dim, 'n_docs', v_n,
                                  'cap', 10000,
                                  'reason', 'catalog exceeds the brute-force cap and the pgvector tier is not ready; run rvbbit.pgvector_refresh_catalog()',
                                  'index_status', coalesce(v_istatus, 'absent'),
                                  'index_message', v_imsg);
    END IF;
    RETURN jsonb_build_object('tier', 'brute', 'ready', true, 'dim', v_dim, 'n_docs', v_n, 'cap', 10000);
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 3. pgvector tier: build-then-rename refresh, and refresh-if-stale
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION rvbbit.pgvector_refresh_catalog(graph text DEFAULT 'db_catalog')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_graph text := COALESCE(NULLIF(btrim(graph), ''), 'db_catalog');
    v_dim   int;
    v_n     bigint;
    v_mu    real[];
    v_t0    timestamptz := clock_timestamp();
BEGIN
    IF to_regtype('vector') IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'pgvector not installed');
    END IF;

    SELECT d, c INTO v_dim, v_n
      FROM (SELECT array_length(embedding, 1) AS d, count(*) AS c
              FROM rvbbit.catalog_docs
             WHERE graph_id = v_graph AND embedding IS NOT NULL
             GROUP BY array_length(embedding, 1)
             ORDER BY count(*) DESC
             LIMIT 1) q;
    IF v_dim IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'no embedded docs');
    END IF;

    INSERT INTO rvbbit.pgvector_indexes (graph_id, dim, status, refreshed_at)
         VALUES (v_graph, v_dim, 'building', now())
    ON CONFLICT (graph_id) DO UPDATE
       SET dim = EXCLUDED.dim, status = 'building', message = NULL, refreshed_at = now();

    BEGIN
        SELECT array_agg(m ORDER BY i) INTO v_mu
          FROM (SELECT t.i, avg(t.e)::real AS m
                  FROM rvbbit.catalog_docs d
                       CROSS JOIN LATERAL unnest(d.embedding) WITH ORDINALITY AS t(e, i)
                 WHERE d.graph_id = v_graph AND d.embedding IS NOT NULL
                   AND array_length(d.embedding, 1) = v_dim
                 GROUP BY t.i) z;

        -- Build the replacement beside the live table. The live catalog_vec keeps
        -- serving the whole time; only the rename at the end takes the exclusive
        -- lock (milliseconds), not the HNSW build (tens of seconds).
        EXECUTE 'DROP TABLE IF EXISTS rvbbit.catalog_vec_new';
        EXECUTE format(
            'CREATE TABLE rvbbit.catalog_vec_new ('
            || 'graph_id text NOT NULL, node_id bigint NOT NULL, '
            || 'vec vector(%s) NOT NULL, PRIMARY KEY (graph_id, node_id))', v_dim);

        EXECUTE $q$
            INSERT INTO rvbbit.catalog_vec_new (graph_id, node_id, vec)
            SELECT d.graph_id, d.node_id,
                   (SELECT array_agg(u.de - u.me ORDER BY u.i)
                      FROM unnest(d.embedding, $1) WITH ORDINALITY AS u(de, me, i))::vector
              FROM rvbbit.catalog_docs d
             WHERE d.graph_id = $2 AND d.embedding IS NOT NULL
               AND array_length(d.embedding, 1) = $3
        $q$ USING v_mu, v_graph, v_dim;

        EXECUTE 'CREATE INDEX catalog_vec_new_hnsw ON rvbbit.catalog_vec_new '
             || 'USING hnsw (vec vector_cosine_ops)';
        -- Without stats the planner guessed 227 rows for graph_id = … and
        -- nested-looped every vector through an exact distance instead of the
        -- HNSW scan (410 ms vs ~5 ms on the pilot).
        EXECUTE 'ANALYZE rvbbit.catalog_vec_new';

        -- swap (the pkey index is renamed too: a leftover catalog_vec_new_pkey
        -- would collide with the NEXT build's inline PRIMARY KEY)
        EXECUTE 'DROP TABLE IF EXISTS rvbbit.catalog_vec';
        EXECUTE 'ALTER TABLE rvbbit.catalog_vec_new RENAME TO catalog_vec';
        EXECUTE 'ALTER INDEX rvbbit.catalog_vec_new_hnsw RENAME TO catalog_vec_hnsw';
        EXECUTE 'ALTER INDEX rvbbit.catalog_vec_new_pkey RENAME TO catalog_vec_pkey';

        UPDATE rvbbit.pgvector_indexes
           SET mean = v_mu, n_docs = v_n, status = 'ready', message = NULL, refreshed_at = now()
         WHERE graph_id = v_graph;

        RETURN jsonb_build_object('ok', true, 'dim', v_dim, 'n_docs', v_n,
                                  'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
    EXCEPTION WHEN OTHERS THEN
        -- Leave the previous ready tier alone: only the status row records the
        -- failure. If catalog_vec survived, readers keep using it.
        UPDATE rvbbit.pgvector_indexes
           SET status = CASE WHEN to_regclass('rvbbit.catalog_vec') IS NOT NULL THEN 'ready' ELSE 'failed' END,
               message = SQLERRM, refreshed_at = now()
         WHERE graph_id = v_graph;
        RETURN jsonb_build_object('ok', false, 'reason', SQLERRM);
    END;
END $fn$;

-- A tier built by an earlier swap kept its build-time pkey name; free the name
-- so the next build's inline PRIMARY KEY cannot collide. Also give a never-
-- analyzed live tier its stats (see the ANALYZE note above).
DO $$ BEGIN
    IF to_regclass('rvbbit.catalog_vec_new_pkey') IS NOT NULL
       AND to_regclass('rvbbit.catalog_vec_new') IS NULL
       AND to_regclass('rvbbit.catalog_vec_pkey') IS NULL THEN
        ALTER INDEX rvbbit.catalog_vec_new_pkey RENAME TO catalog_vec_pkey;
    END IF;
    IF to_regclass('rvbbit.catalog_vec') IS NOT NULL
       AND (SELECT last_analyze IS NULL AND last_autoanalyze IS NULL
              FROM pg_stat_user_tables WHERE relid = to_regclass('rvbbit.catalog_vec')) THEN
        ANALYZE rvbbit.catalog_vec;
    END IF;
END $$;

-- Tier-1 dense ranker, HNSW-first: the nearest-neighbour scan runs alone in
-- its own subquery (nothing for the planner to join or filter inside the
-- ORDER BY … LIMIT), then kinds/floor are applied outside. hnsw.ef_search is
-- raised to the over-fetch size for the statement, since pgvector returns at
-- most ef_search rows from an index scan (default 40 — which silently capped
-- the candidate pool).
CREATE OR REPLACE FUNCTION rvbbit.catalog_dense_knn_pgvector(
    q_vec     real[],
    graph     text,
    kinds     text[],
    k         int,
    min_score float8 DEFAULT 0.10)
RETURNS TABLE (node_id bigint, score float8)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_mu   real[];
    v_dim  int;
    v_qc   real[];
    v_over int := greatest(coalesce(k, 10) * 8, 64);
BEGIN
    IF q_vec IS NULL OR array_length(q_vec, 1) IS NULL THEN RETURN; END IF;
    IF to_regtype('vector') IS NULL OR to_regclass('rvbbit.catalog_vec') IS NULL THEN RETURN; END IF;

    SELECT pi.mean, pi.dim INTO v_mu, v_dim
      FROM rvbbit.pgvector_indexes pi
     WHERE pi.graph_id = graph AND pi.status = 'ready';
    IF v_mu IS NULL OR v_dim IS DISTINCT FROM array_length(q_vec, 1) THEN RETURN; END IF;

    SELECT array_agg(u.qe - u.me ORDER BY u.i) INTO v_qc
      FROM unnest(q_vec, v_mu) WITH ORDINALITY AS u(qe, me, i);

    BEGIN PERFORM set_config('hnsw.ef_search', least(v_over, 1000)::text, true);
    EXCEPTION WHEN OTHERS THEN NULL; END;

    RETURN QUERY EXECUTE $q$
        WITH nn AS (
            SELECT cv.node_id, cv.graph_id, (cv.vec <=> $1::vector) AS dist
              FROM rvbbit.catalog_vec cv
             ORDER BY cv.vec <=> $1::vector
             LIMIT $5
        )
        SELECT nn.node_id, (1.0 - nn.dist)::float8 AS score
          FROM nn
          JOIN rvbbit.catalog_docs d ON d.graph_id = nn.graph_id AND d.node_id = nn.node_id
         WHERE nn.graph_id = $2
           AND ($3::text[] IS NULL OR d.kind = ANY ($3))
           AND (1.0 - nn.dist) > $4
         ORDER BY nn.dist
         LIMIT $6
    $q$ USING v_qc, graph, kinds, min_score, v_over, k;
END $fn$;

-- Rebuild only when the tier is missing/failed or the docs moved since the
-- last build. Safe to call after every crawl and from a cron heartbeat.
CREATE OR REPLACE FUNCTION rvbbit.pgvector_refresh_catalog_if_stale(graph text DEFAULT 'db_catalog')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_graph text := COALESCE(NULLIF(btrim(graph), ''), 'db_catalog');
    v_istatus text; v_indocs bigint; v_irefreshed timestamptz; v_idim int;
    v_stale boolean := false;
    v_why   text;
    v_n     bigint;
BEGIN
    IF to_regtype('vector') IS NULL THEN
        RETURN jsonb_build_object('refreshed', false, 'reason', 'pgvector not installed');
    END IF;
    SELECT status, n_docs, refreshed_at, dim INTO v_istatus, v_indocs, v_irefreshed, v_idim
      FROM rvbbit.pgvector_indexes WHERE graph_id = v_graph;
    -- Count at the tier's own dimension: a few docs embedded at another dim
    -- (model swap stragglers) must not read as "moved" on every call, which
    -- rebuilt a fresh tier for 33 s each refresh on the pilot.
    SELECT count(*) INTO v_n FROM rvbbit.catalog_docs
     WHERE graph_id = v_graph AND embedding IS NOT NULL
       AND (v_idim IS NULL OR array_length(embedding, 1) = v_idim);
    IF v_n = 0 THEN
        RETURN jsonb_build_object('refreshed', false, 'reason', 'no embedded docs');
    END IF;
    IF v_istatus IS DISTINCT FROM 'ready' OR to_regclass('rvbbit.catalog_vec') IS NULL THEN
        v_stale := true; v_why := 'tier not ready';
    ELSIF coalesce(v_indocs, -1) <> v_n THEN
        v_stale := true; v_why := format('doc count moved %s -> %s', v_indocs, v_n);
    ELSIF EXISTS (SELECT 1 FROM rvbbit.catalog_docs d
                   WHERE d.graph_id = v_graph AND d.embedding IS NOT NULL
                     AND array_length(d.embedding, 1) = v_idim
                     AND greatest(coalesce(d.embedded_at, d.updated_at), d.updated_at) > v_irefreshed
                   LIMIT 1) THEN
        v_stale := true; v_why := 'docs embedded since last build';
    END IF;
    IF NOT v_stale THEN
        RETURN jsonb_build_object('refreshed', false, 'reason', 'fresh', 'n_docs', v_n,
                                  'refreshed_at', v_irefreshed);
    END IF;
    RETURN jsonb_build_object('refreshed', true, 'reason', v_why)
        || rvbbit.pgvector_refresh_catalog(v_graph);
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 4a. relation_refs — who reads a relation (metrics, cubes, dashboards, domains)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS rvbbit.relation_refs (
    relation     text NOT NULL,           -- schema.rel (lowercase, unquoted)
    ref_kind     text NOT NULL,           -- metric | cube | dashboard | domain
    ref_name     text NOT NULL,
    ref_meta     jsonb,
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (relation, ref_kind, ref_name)
);
CREATE INDEX IF NOT EXISTS relation_refs_relation_idx ON rvbbit.relation_refs (relation);

-- schema-qualified relations after FROM/JOIN in a SQL/HTML/JSON blob, kept
-- only when they resolve to a real relation (precision over recall).
CREATE OR REPLACE FUNCTION rvbbit.sql_relation_refs(p_text text)
RETURNS text[] LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(array_agg(DISTINCT lower(m[1])), '{}'::text[])
      FROM regexp_matches(coalesce(p_text, ''),
                          '(?:from|join|into|update)\s+([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)', 'gi') m
     WHERE to_regclass(lower(m[1])) IS NOT NULL
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.relation_refs_refresh()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0 timestamptz := clock_timestamp();
    v_metrics int := 0; v_cubes int := 0; v_dash int := 0; v_domains int := 0;
BEGIN
    DELETE FROM rvbbit.relation_refs;

    IF to_regclass('rvbbit.metric_defs') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'metric', m.name,
               jsonb_build_object('version', m.version, 'grain', m.grain, 'category', m.labels->>'category')
          FROM (SELECT DISTINCT ON (name) name, version, sql, grain, labels
                  FROM rvbbit.metric_defs ORDER BY name, version DESC) m
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(m.sql)) AS r(rel)
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_metrics = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.cube_defs') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'cube', c.name,
               jsonb_build_object('version', c.version, 'grain', c.grain, 'refresh_cron', c.refresh_cron)
          FROM (SELECT DISTINCT ON (name) name, version, sql, grain, refresh_cron
                  FROM rvbbit.cube_defs ORDER BY name, version DESC) c
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(c.sql)) AS r(rel)
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_cubes = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.dashboards') IS NOT NULL AND to_regclass('rvbbit.dashboard_versions') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'dashboard', d.slug,
               jsonb_build_object('name', d.name, 'status', d.status, 'version', v.version,
                                  'owner', d.owner_email, 'updated_at', d.updated_at)
          FROM rvbbit.dashboards d
          JOIN rvbbit.dashboard_versions v ON v.dashboard_id = d.id AND v.version = d.latest_version
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(
                   coalesce(v.html, '') || ' ' || coalesce(v.manifest::text, '') || ' ' || coalesce(v.source_files::text, '')
               )) AS r(rel)
         WHERE NOT coalesce(d.archived, false)
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_dash = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT DISTINCT ON (lower(r.rel), s.domain_key)
               lower(r.rel), 'domain', s.domain_key,
               jsonb_build_object('name', s.name, 'status', s.status, 'lifecycle', s.lifecycle,
                                  'adoption_state', s.adoption_state, 'graph_id', s.graph_id,
                                  'cube_name', s.cube_name, 'primary_metric_key', s.primary_metric_key)
          FROM rvbbit.business_value_graph_summary s
               CROSS JOIN LATERAL unnest(coalesce(s.source_relations, '{}'::text[])) AS r(rel)
         WHERE coalesce(s.usable, false)
         ORDER BY lower(r.rel), s.domain_key,
                  (s.status = 'promoted') DESC, s.updated_at DESC NULLS LAST
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_domains = ROW_COUNT;
    END IF;

    RETURN jsonb_build_object('metrics', v_metrics, 'cubes', v_cubes, 'dashboards', v_dash,
                              'domains', v_domains,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 4b. usage_snapshot — mcp_activity touches per object, precomputed
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS rvbbit.usage_snapshot (
    object       text PRIMARY KEY,
    touches      bigint NOT NULL,
    users        bigint NOT NULL,
    last_touch   timestamptz,
    touches_30d  bigint NOT NULL DEFAULT 0,
    refreshed_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION rvbbit.usage_snapshot_refresh(p_window interval DEFAULT interval '30 days')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0 timestamptz := clock_timestamp();
    v_n  int := 0;
BEGIN
    IF to_regclass('rvbbit.mcp_activity') IS NULL THEN
        RETURN jsonb_build_object('objects', 0, 'reason', 'no mcp_activity');
    END IF;
    DELETE FROM rvbbit.usage_snapshot;
    INSERT INTO rvbbit.usage_snapshot (object, touches, users, last_touch, touches_30d, refreshed_at)
    SELECT o.obj,
           count(*),
           count(DISTINCT a.caller),
           max(a.ts),
           count(*) FILTER (WHERE a.ts >= now() - p_window),
           now()
      FROM rvbbit.mcp_activity a
           CROSS JOIN LATERAL unnest(a.objects) AS o(obj)
     WHERE o.obj IS NOT NULL AND o.obj <> ''
     GROUP BY o.obj;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN jsonb_build_object('objects', v_n,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- search_data_weighted (0017) reads the live mcp_popular_objects view — a full
-- aggregate of mcp_activity on EVERY agent search (47 s cold / 160 ms warm on
-- the pilot at 395k rows). Same signature; prefers the snapshot when present.
CREATE OR REPLACE FUNCTION rvbbit.search_data_weighted(
    p_query text,
    p_k int DEFAULT 20,
    p_kinds text[] DEFAULT NULL,
    p_graph text DEFAULT 'db_catalog',
    p_usage_weight float8 DEFAULT 0.5
) RETURNS TABLE (
    node_id bigint, kind text, schema_name text, rel_name text, col_name text,
    score double precision, doc text, usage_touches bigint, boosted_score double precision
) LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_snapshot boolean := false;
BEGIN
    IF to_regclass('rvbbit.usage_snapshot') IS NOT NULL THEN
        SELECT EXISTS (SELECT 1 FROM rvbbit.usage_snapshot) INTO v_snapshot;
    END IF;

    IF NOT v_snapshot AND to_regclass('rvbbit.mcp_popular_objects') IS NULL THEN
        RETURN QUERY
        SELECT h.node_id, h.kind, h.schema_name, h.rel_name, h.col_name, h.score, h.doc,
               0::bigint AS usage_touches,
               h.score AS boosted_score
        FROM rvbbit.data_search(p_query, greatest(p_k, 1), p_kinds, p_graph) h
        ORDER BY h.score DESC
        LIMIT greatest(p_k, 1);
        RETURN;
    END IF;

    IF v_snapshot THEN
        RETURN QUERY
        WITH hits AS (
            SELECT * FROM rvbbit.data_search(p_query, greatest(p_k, 1) * 2, p_kinds, p_graph)
        ),
        pop AS (
            SELECT object, touches,
                   touches::float8 / nullif(max(touches) OVER (), 0) AS usage_norm
            FROM rvbbit.usage_snapshot
        )
        SELECT h.node_id, h.kind, h.schema_name, h.rel_name, h.col_name, h.score, h.doc,
               coalesce(u.touches, 0)::bigint AS usage_touches,
               h.score * (1 + coalesce(p_usage_weight, 0.5) * coalesce(u.usage_norm, 0)) AS boosted_score
        FROM hits h
        LEFT JOIN LATERAL (
            SELECT touches, usage_norm FROM pop
             WHERE object = coalesce(h.schema_name || '.', '') || h.rel_name
                OR object = h.rel_name
             ORDER BY (object = coalesce(h.schema_name || '.', '') || h.rel_name) DESC, touches DESC
             LIMIT 1
        ) u ON true
        ORDER BY boosted_score DESC
        LIMIT greatest(p_k, 1);
        RETURN;
    END IF;

    RETURN QUERY
    WITH hits AS (
        SELECT * FROM rvbbit.data_search(p_query, greatest(p_k, 1) * 2, p_kinds, p_graph)
    ),
    pop AS (
        SELECT object, touches,
               touches::float8 / nullif(max(touches) OVER (), 0) AS usage_norm
        FROM rvbbit.mcp_popular_objects
    )
    SELECT h.node_id, h.kind, h.schema_name, h.rel_name, h.col_name, h.score, h.doc,
           coalesce(u.touches, 0)::bigint AS usage_touches,
           h.score * (1 + coalesce(p_usage_weight, 0.5) * coalesce(u.usage_norm, 0)) AS boosted_score
    FROM hits h
    LEFT JOIN LATERAL (
        SELECT touches, usage_norm FROM pop
         WHERE object = coalesce(h.schema_name || '.', '') || h.rel_name
            OR object = h.rel_name
         ORDER BY (object = coalesce(h.schema_name || '.', '') || h.rel_name) DESC, touches DESC
         LIMIT 1
    ) u ON true
    ORDER BY boosted_score DESC
    LIMIT greatest(p_k, 1);
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════
-- 4c. relation_lineage — where a relation comes from and when it last loaded
-- ═══════════════════════════════════════════════════════════════════════
-- Adapters fill what the install has: the rvbbit mirror registry (mirror_*),
-- Fivetran's metadata connector (fivetran_metadata.incremental_mar/connection),
-- dlt load tables (<schema>._dlt_loads). Absent adapters leave the role/source
-- from rvbbit.business_sources, which is always the floor.

CREATE TABLE IF NOT EXISTS rvbbit.relation_lineage (
    relation           text PRIMARY KEY,   -- schema.rel
    schema_name        text NOT NULL,
    rel_name           text NOT NULL,
    relkind            text,
    role               text,               -- business | derived | plumbing | unknown
    source_system      text,
    adapter            text,               -- rvbbit:<dialect> | fivetran:<connector> | dlt | transform | …
    connection         text,
    load_mode          text,
    primary_key        text[],
    cursor_column      text,
    sync_frequency_min int,
    paused             boolean,
    last_load_at       timestamptz,
    last_load_status   text,
    last_load_rows     bigint,
    detail             jsonb,
    refreshed_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS relation_lineage_schema_idx ON rvbbit.relation_lineage (schema_name, rel_name);

CREATE OR REPLACE FUNCTION rvbbit.relation_lineage_refresh()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0 timestamptz := clock_timestamp();
    v_base int := 0; v_mirror int := 0; v_fivetran int := 0; v_dlt int := 0;
    v_sch record;
BEGIN
    -- floor: every user relation, with the schema-role registry when present
    INSERT INTO rvbbit.relation_lineage
        (relation, schema_name, rel_name, relkind, role, source_system, adapter, refreshed_at)
    SELECT n.nspname || '.' || c.relname, n.nspname, c.relname, c.relkind::text,
           bs.role, bs.source_system,
           CASE WHEN bs.adapter IS NOT NULL AND bs.adapter <> '' THEN bs.adapter
                WHEN bs.source_system = 'transform' THEN 'transform' END,
           now()
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN rvbbit.business_sources bs ON bs.schema_name = n.nspname
     WHERE c.relkind IN ('r', 'p', 'm', 'v', 'f')
       AND NOT c.relispartition
       AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'rvbbit')
       AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
    ON CONFLICT (relation) DO UPDATE
       SET relkind = EXCLUDED.relkind, role = EXCLUDED.role, source_system = EXCLUDED.source_system,
           adapter = coalesce(EXCLUDED.adapter, rvbbit.relation_lineage.adapter),
           connection = NULL, load_mode = NULL, primary_key = NULL, cursor_column = NULL,
           sync_frequency_min = NULL, paused = NULL, last_load_at = NULL, last_load_status = NULL,
           last_load_rows = NULL, detail = NULL, refreshed_at = now();
    GET DIAGNOSTICS v_base = ROW_COUNT;
    -- relations that no longer exist (rows touched above carry refreshed_at = now())
    DELETE FROM rvbbit.relation_lineage WHERE refreshed_at < now();

    -- adapter: rvbbit mirror registry
    IF to_regclass('rvbbit.mirror_tables') IS NOT NULL AND to_regclass('rvbbit.mirror_runs') IS NOT NULL THEN
        BEGIN
            UPDATE rvbbit.relation_lineage l
               SET adapter = 'rvbbit:' || coalesce(c.dialect, 'mirror'),
                   source_system = coalesce(l.source_system, c.label, c.connection_name),
                   connection = coalesce(c.label, c.connection_name),
                   load_mode = t.load_mode, primary_key = t.primary_key, cursor_column = t.cursor_column,
                   sync_frequency_min = (j.schedule_seconds / 60)::int,
                   paused = NOT (coalesce(c.enabled, true) AND coalesce(j.enabled, true) AND coalesce(t.enabled, true)),
                   last_load_at = r.finished_at, last_load_status = r.status, last_load_rows = r.rows_loaded,
                   detail = jsonb_build_object('job', j.job_name, 'source_schema', j.source_schema,
                                               'source_table', t.source_table,
                                               'source_host', c.metadata->>'host',
                                               'source_database', c.metadata->>'database',
                                               'error', r.error_message)
              FROM rvbbit.mirror_tables t
              JOIN rvbbit.mirror_jobs j USING (job_name)
              JOIN rvbbit.mirror_connections c USING (connection_name)
              LEFT JOIN LATERAL (SELECT status, finished_at, rows_loaded, error_message
                                   FROM rvbbit.mirror_runs r WHERE r.job_name = j.job_name
                                  ORDER BY requested_at DESC LIMIT 1) r ON true
             WHERE l.schema_name = j.destination_schema AND l.rel_name = t.destination_table;
            GET DIAGNOSTICS v_mirror = ROW_COUNT;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'relation_lineage_refresh: mirror adapter skipped (%)', SQLERRM;
        END;
    END IF;

    -- adapter: Fivetran metadata connector (incremental_mar is per table per day)
    IF to_regclass('fivetran_metadata.incremental_mar') IS NOT NULL THEN
        BEGIN
            UPDATE rvbbit.relation_lineage l
               SET adapter = coalesce(nullif(l.adapter, ''), 'fivetran:' || coalesce(cn.connector_type_id, m.connection_name)),
                   source_system = coalesce(l.source_system, cn.connector_type_id, m.connection_name),
                   connection = m.connection_name,
                   load_mode = coalesce(nullif(lower(m.sync_type), 'unknown'), 'incremental'),
                   sync_frequency_min = cn.sync_frequency, paused = cn.paused,
                   last_load_at = m.measured_date::timestamptz,
                   last_load_status = 'measured', last_load_rows = m.incremental_rows,
                   detail = jsonb_build_object('mar_measured_date', m.measured_date,
                                               'mar_rows_7d', m.rows_7d)
              FROM (SELECT DISTINCT ON (schema_name, table_name)
                           schema_name, table_name, connection_name, measured_date, sync_type,
                           incremental_rows,
                           sum(incremental_rows) OVER (PARTITION BY schema_name, table_name) AS rows_7d
                      FROM fivetran_metadata.incremental_mar
                     WHERE measured_date >= current_date - 7
                     ORDER BY schema_name, table_name, measured_date DESC) m
              LEFT JOIN LATERAL (SELECT connector_type_id, sync_frequency, paused
                                   FROM fivetran_metadata.connection c
                                  WHERE c.connection_name = m.connection_name LIMIT 1) cn ON true
             WHERE l.schema_name = m.schema_name AND l.rel_name = m.table_name;
            GET DIAGNOSTICS v_fivetran = ROW_COUNT;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'relation_lineage_refresh: fivetran adapter skipped (%)', SQLERRM;
        END;
    END IF;

    -- adapter: dlt (<schema>._dlt_loads; schema-level last successful load)
    FOR v_sch IN
        SELECT n.nspname AS sch
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = '_dlt_loads' AND c.relkind IN ('r', 'p', 'v', 'm')
    LOOP
        BEGIN
            EXECUTE format($q$
                UPDATE rvbbit.relation_lineage l
                   SET adapter = coalesce(nullif(l.adapter, ''), 'dlt'),
                       last_load_at = coalesce(l.last_load_at, d.last_ok),
                       last_load_status = coalesce(l.last_load_status, CASE WHEN d.last_ok IS NOT NULL THEN 'ok' END),
                       detail = coalesce(l.detail, '{}'::jsonb) || jsonb_build_object('dlt_pipelines', d.pipelines)
                  FROM (SELECT max(p.last_ok) AS last_ok,
                               jsonb_agg(jsonb_build_object('pipeline', p.schema_name, 'last_ok', p.last_ok, 'loads', p.loads)
                                         ORDER BY p.last_ok DESC NULLS LAST) AS pipelines
                          FROM (SELECT schema_name, max(inserted_at) FILTER (WHERE status = 0) AS last_ok, count(*) AS loads
                                  FROM %I._dlt_loads GROUP BY schema_name) p) d
                 WHERE l.schema_name = %L AND l.rel_name NOT LIKE '\_dlt\_%%'
            $q$, v_sch.sch, v_sch.sch);
            v_dlt := v_dlt + 1;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'relation_lineage_refresh: dlt adapter skipped for % (%)', v_sch.sch, SQLERRM;
        END;
    END LOOP;

    RETURN jsonb_build_object('relations', v_base, 'mirror', v_mirror, 'fivetran', v_fivetran,
                              'dlt_schemas', v_dlt,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 5. Values seam — one index of distinct text values per (relation, column)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS rvbbit.value_index (
    schema_name text NOT NULL,
    rel_name    text NOT NULL,
    col_name    text NOT NULL,
    value       text NOT NULL,             -- bounded to value_index_relation.p_max_len chars
    n           bigint NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, left(value, 200))) STORED,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_name, rel_name, col_name, value)
);
CREATE INDEX IF NOT EXISTS value_index_tsv_gin ON rvbbit.value_index USING gin (tsv);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS value_index_value_trgm
            ON rvbbit.value_index USING gin (value gin_trgm_ops);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS rvbbit.value_index_state (
    schema_name     text NOT NULL,
    rel_name        text NOT NULL,
    snapshot_key    text,                  -- n_rows/size_bytes at index time (drift key)
    indexed_at      timestamptz,
    status          text,                  -- ok | skipped | error
    error           text,
    columns_indexed int,
    columns_skipped int,
    values_indexed  bigint,
    elapsed_ms      int,
    PRIMARY KEY (schema_name, rel_name)
);

-- Column names that are never value-indexed (PII-shaped and secrets). Match
-- against the lowercased column name. Operators can widen by replacing this.
CREATE OR REPLACE FUNCTION rvbbit.value_index_skip_column_patterns()
RETURNS text[] LANGUAGE sql IMMUTABLE AS $fn$
    SELECT ARRAY[
        '(^|_)(e?_?mail|emails|phone|phones|mobile|fax|telephone)(_|$)',
        '(^|_)(ssn|social_security|tax_id|ein|passport|license_no|drivers_license)(_|$)',
        '(^|_)(password|passwd|pwd|secret|token|api_?key|access_?key|private_?key|credential)(s?)(_|$)',
        '(^|_)(credit_?card|card_?number|cc_?num|iban|account_?number|routing_?number)(_|$)',
        '(^|_)(ip|ip_?address|ipaddr|user_?agent|session_?id|cookie)(_|$)',
        '(^|_)(dob|date_?of_?birth|birth_?date|birthday)(_|$)',
        '^_'                                   -- dlt / fivetran system columns
    ]::text[]
$fn$;

-- Drift key for a relation: the crawler's latest table fingerprint when it has
-- one (rows + bytes), else live planner stats. Cheap.
CREATE OR REPLACE FUNCTION rvbbit.value_index_snapshot_key(p_schema text, p_rel text)
RETURNS text LANGUAGE plpgsql STABLE AS $fn$
DECLARE v_key text; v_oid oid;
BEGIN
    IF to_regclass('rvbbit.catalog_snapshots') IS NOT NULL THEN
        SELECT (s.fingerprint->>'n_rows') || '/' || (s.fingerprint->>'size_bytes') INTO v_key
          FROM rvbbit.catalog_snapshots s
         WHERE s.graph_id = 'db_catalog' AND s.kind = 'db_table' AND s.obj_key = p_schema || '.' || p_rel
         ORDER BY s.run_id DESC LIMIT 1;
        IF v_key IS NOT NULL THEN RETURN v_key; END IF;
    END IF;
    v_oid := to_regclass(format('%I.%I', p_schema, p_rel))::oid;
    IF v_oid IS NULL THEN RETURN NULL; END IF;
    SELECT greatest(reltuples, 0)::bigint::text || '/' || pg_relation_size(v_oid)::text INTO v_key
      FROM pg_class WHERE oid = v_oid;
    RETURN v_key;
END $fn$;

-- Index one relation's text columns. Per column: the p_value_cap most frequent
-- distinct values (bounded to p_max_len chars). Columns are skipped when their
-- name is PII-shaped, when values are long free text (avg > 160 chars), or when
-- they look like identifiers (≥90% ids/uuids/hashes/urls/emails). Values not
-- seen this pass are pruned. Returns a receipt.
CREATE OR REPLACE FUNCTION rvbbit.value_index_relation(
    p_schema     text,
    p_rel        text,
    p_value_cap  int DEFAULT 10000,
    p_max_len    int DEFAULT 200,
    p_max_rows   bigint DEFAULT 3000000)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0       timestamptz := clock_timestamp();
    v_oid      oid := to_regclass(format('%I.%I', p_schema, p_rel))::oid;
    v_rows     bigint;
    v_col      record;
    v_n_idx    int := 0;
    v_n_skip   int := 0;
    v_values   bigint := 0;
    v_cnt      bigint;
    v_avg_len  numeric;
    v_id_frac  numeric;
    v_skip     text[] := rvbbit.value_index_skip_column_patterns();
    v_key      text;
    v_status   text := 'ok';
    v_err      text;
BEGIN
    IF v_oid IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'relation not found');
    END IF;
    SELECT greatest(reltuples, 0)::bigint INTO v_rows FROM pg_class WHERE oid = v_oid;
    v_key := rvbbit.value_index_snapshot_key(p_schema, p_rel);

    IF v_rows > p_max_rows THEN
        INSERT INTO rvbbit.value_index_state AS s
            (schema_name, rel_name, snapshot_key, indexed_at, status, error, columns_indexed, columns_skipped, values_indexed, elapsed_ms)
        VALUES (p_schema, p_rel, v_key, now(), 'skipped', format('%s rows exceed max_rows %s', v_rows, p_max_rows), 0, 0, 0, 0)
        ON CONFLICT (schema_name, rel_name) DO UPDATE
           SET snapshot_key = EXCLUDED.snapshot_key, indexed_at = now(), status = EXCLUDED.status,
               error = EXCLUDED.error, elapsed_ms = 0;
        RETURN jsonb_build_object('ok', true, 'status', 'skipped', 'reason', 'too many rows', 'rows', v_rows);
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS _vi_vals (v text, c bigint) ON COMMIT DROP;

    FOR v_col IN
        SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS typ
          FROM pg_attribute a
          JOIN pg_type t ON t.oid = a.atttypid
         WHERE a.attrelid = v_oid AND a.attnum > 0 AND NOT a.attisdropped
           AND t.typname IN ('text', 'varchar', 'bpchar', 'citext', 'name')
         ORDER BY a.attnum
    LOOP
        IF EXISTS (SELECT 1 FROM unnest(v_skip) p WHERE lower(v_col.attname) ~ p) THEN
            v_n_skip := v_n_skip + 1;
            CONTINUE;
        END IF;
        BEGIN
            TRUNCATE _vi_vals;
            EXECUTE format(
                $q$INSERT INTO _vi_vals (v, c)
                   SELECT left(%I::text, %s), count(*)
                     FROM %I.%I
                    WHERE %I IS NOT NULL AND btrim(%I::text) <> ''
                    GROUP BY 1 ORDER BY 2 DESC LIMIT %s$q$,
                v_col.attname, p_max_len, p_schema, p_rel, v_col.attname, v_col.attname, p_value_cap);
            SELECT count(*), avg(length(v)),
                   avg((v ~ '^[0-9a-fA-F-]{20,}$' OR v ~ '^\d+(\.\d+)?$' OR v ~ '^[A-Za-z0-9]{15,18}$'
                        OR v ~ '^\S+@\S+\.\S+$' OR v ~ '^https?://' OR v ~ '^[0-9+()\- .]{7,}$')::int)
              INTO v_cnt, v_avg_len, v_id_frac
              FROM _vi_vals;
            IF v_cnt = 0 OR v_avg_len > 160 OR (v_id_frac >= 0.9 AND v_cnt > 50) THEN
                v_n_skip := v_n_skip + 1;
                CONTINUE;
            END IF;
            INSERT INTO rvbbit.value_index AS vi (schema_name, rel_name, col_name, value, n, first_seen, last_seen)
            SELECT p_schema, p_rel, v_col.attname, x.v, x.c, now(), now()
              FROM _vi_vals x
            ON CONFLICT (schema_name, rel_name, col_name, value) DO UPDATE
               SET n = EXCLUDED.n, last_seen = now();
            v_values := v_values + v_cnt;
            v_n_idx := v_n_idx + 1;
        EXCEPTION WHEN OTHERS THEN
            v_n_skip := v_n_skip + 1;
            v_err := coalesce(v_err || '; ', '') || v_col.attname || ': ' || SQLERRM;
        END;
    END LOOP;

    -- prune values (and whole columns) not seen this pass: rows touched above
    -- carry last_seen = now() (transaction start), older ones are gone
    DELETE FROM rvbbit.value_index
     WHERE schema_name = p_schema AND rel_name = p_rel AND last_seen < now();

    INSERT INTO rvbbit.value_index_state AS s
        (schema_name, rel_name, snapshot_key, indexed_at, status, error, columns_indexed, columns_skipped, values_indexed, elapsed_ms)
    VALUES (p_schema, p_rel, v_key, now(), v_status, v_err, v_n_idx, v_n_skip, v_values,
            round(extract(epoch FROM clock_timestamp() - v_t0) * 1000))
    ON CONFLICT (schema_name, rel_name) DO UPDATE
       SET snapshot_key = EXCLUDED.snapshot_key, indexed_at = EXCLUDED.indexed_at, status = EXCLUDED.status,
           error = EXCLUDED.error, columns_indexed = EXCLUDED.columns_indexed,
           columns_skipped = EXCLUDED.columns_skipped, values_indexed = EXCLUDED.values_indexed,
           elapsed_ms = EXCLUDED.elapsed_ms;

    RETURN jsonb_build_object('ok', true, 'status', v_status, 'columns_indexed', v_n_idx,
                              'columns_skipped', v_n_skip, 'values', v_values, 'errors', v_err,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- Relations the sweep should (re)index, most urgent first: never indexed,
-- then drifted (fingerprint key changed), then older than p_max_age.
CREATE OR REPLACE FUNCTION rvbbit.value_index_candidates(
    p_limit   int DEFAULT 200,
    p_max_age interval DEFAULT interval '7 days')
RETURNS TABLE (schema_name text, rel_name text, reason text, rows_est bigint)
LANGUAGE sql STABLE AS $fn$
    -- Drift keys come from ONE pass over the table snapshots (latest per
    -- obj_key). A per-relation correlated lookup planned as a seq scan of the
    -- whole snapshot history per row (minutes on 1,750 relations).
    WITH rels AS (
        SELECT n.nspname AS sch, c.relname AS rel, c.oid AS reloid,
               greatest(c.reltuples, 0)::bigint AS rows_est
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN rvbbit.business_sources bs ON bs.schema_name = n.nspname
         WHERE c.relkind IN ('r', 'p', 'm')
           AND NOT c.relispartition
           AND bs.role IN ('business', 'derived')
           AND c.relname NOT LIKE '\_%'
    ),
    snap AS (
        SELECT DISTINCT ON (cs.obj_key) cs.obj_key,
               (cs.fingerprint->>'n_rows') || '/' || (cs.fingerprint->>'size_bytes') AS key
          FROM rvbbit.catalog_snapshots cs
         WHERE cs.graph_id = 'db_catalog' AND cs.kind = 'db_table'
         ORDER BY cs.obj_key, cs.run_id DESC
    ),
    keyed AS (
        SELECT r.sch, r.rel, r.rows_est,
               s.schema_name AS st_schema, s.snapshot_key AS st_key, s.indexed_at,
               CASE WHEN s.schema_name IS NULL THEN NULL
                    ELSE coalesce(sn.key, r.rows_est::text || '/' || pg_relation_size(r.reloid)::text)
               END AS cur_key
          FROM rels r
          LEFT JOIN rvbbit.value_index_state s ON s.schema_name = r.sch AND s.rel_name = r.rel
          LEFT JOIN snap sn ON sn.obj_key = r.sch::text || '.' || r.rel::text
    )
    SELECT k.sch, k.rel,
           CASE WHEN k.st_schema IS NULL THEN 'never'
                WHEN k.st_key IS DISTINCT FROM k.cur_key THEN 'drift'
                ELSE 'age' END,
           k.rows_est
      FROM keyed k
     WHERE k.st_schema IS NULL
        OR k.st_key IS DISTINCT FROM k.cur_key
        OR k.indexed_at < now() - p_max_age
     ORDER BY (k.st_schema IS NULL) DESC, k.indexed_at NULLS FIRST, k.rows_est
     LIMIT p_limit
$fn$;

-- Budgeted background pass (pg_cron: CALL rvbbit.value_index_sweep(120)).
-- Commits after every relation so a long table never holds the others hostage.
CREATE OR REPLACE PROCEDURE rvbbit.value_index_sweep(
    p_budget_seconds int DEFAULT 120,
    p_max_relations  int DEFAULT 200,
    p_value_cap      int DEFAULT 10000,
    p_max_rows       bigint DEFAULT 3000000)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_t0   timestamptz := clock_timestamp();
    v_c    record;
    v_done int := 0;
    v_res  jsonb;
BEGIN
    FOR v_c IN SELECT * FROM rvbbit.value_index_candidates(p_max_relations) LOOP
        EXIT WHEN extract(epoch FROM clock_timestamp() - v_t0) > p_budget_seconds;
        BEGIN
            v_res := rvbbit.value_index_relation(v_c.schema_name, v_c.rel_name, p_value_cap, 200, p_max_rows);
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO rvbbit.value_index_state (schema_name, rel_name, snapshot_key, indexed_at, status, error)
            VALUES (v_c.schema_name, v_c.rel_name, rvbbit.value_index_snapshot_key(v_c.schema_name, v_c.rel_name), now(), 'error', SQLERRM)
            ON CONFLICT (schema_name, rel_name) DO UPDATE
               SET indexed_at = now(), status = 'error', error = EXCLUDED.error;
        END;
        COMMIT;
        v_done := v_done + 1;
    END LOOP;
    -- Bulk upserts leave GIN pending lists that every value_knn probe must
    -- scan until VACUUM folds them in; fold them now so reads stay fast.
    IF v_done > 0 THEN
        BEGIN
            PERFORM gin_clean_pending_list('rvbbit.value_index_tsv_gin'::regclass);
            IF to_regclass('rvbbit.value_index_value_trgm') IS NOT NULL THEN
                PERFORM gin_clean_pending_list('rvbbit.value_index_value_trgm'::regclass);
            END IF;
            ANALYZE rvbbit.value_index;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'value_index_sweep: index maintenance skipped (%)', SQLERRM;
        END;
        COMMIT;
    END IF;
    RAISE NOTICE 'value_index_sweep: % relations in % s', v_done,
        round(extract(epoch FROM clock_timestamp() - v_t0));
END $fn$;

-- The values seam. Returns the COLUMN node for each (relation, column) whose
-- indexed values match, with the best-matching values as evidence. A query
-- shorter than 3 characters returns nothing (trigram floor).
CREATE OR REPLACE FUNCTION rvbbit.value_knn(query text, graph text, k int)
RETURNS TABLE (node_id bigint, score float8, matches jsonb)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_q    text := btrim(coalesce(query, ''));
    v_lq   text;
    v_pat  text;
    v_tsq  tsquery;
    v_trgm boolean := EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm');
BEGIN
    IF length(v_q) < 3 OR to_regclass('rvbbit.value_index') IS NULL THEN RETURN; END IF;
    v_lq := lower(v_q);
    v_pat := '%' || rvbbit.like_escape(v_q) || '%';
    BEGIN
        v_tsq := websearch_to_tsquery('simple', v_q);
        IF numnode(v_tsq) = 0 THEN v_tsq := NULL; END IF;
    EXCEPTION WHEN OTHERS THEN v_tsq := NULL; END;

    RETURN QUERY
        WITH cand AS (
            SELECT vi.schema_name, vi.rel_name, vi.col_name, vi.value, vi.n,
                   (CASE WHEN v_trgm THEN similarity(lower(vi.value), v_lq) ELSE 0.5 END)
                   + 0.5 * (lower(vi.value) = v_lq)::int
                   + 0.1 * (vi.value ILIKE v_pat)::int
                   + least(0.15, 0.05 * log((vi.n + 1)::numeric)) AS sc
              FROM rvbbit.value_index vi
             WHERE vi.value ILIKE v_pat
                OR (v_tsq IS NOT NULL AND vi.tsv @@ v_tsq)
             ORDER BY 6 DESC
             LIMIT 2000
        ),
        grouped AS (
            SELECT c.schema_name, c.rel_name, c.col_name,
                   max(c.sc) AS sc,
                   (SELECT jsonb_agg(jsonb_build_object('value', x.value, 'n', x.n, 'score', round(x.sc::numeric, 3))
                                     ORDER BY x.sc DESC, x.n DESC)
                      FROM (SELECT * FROM cand x2
                             WHERE x2.schema_name = c.schema_name AND x2.rel_name = c.rel_name AND x2.col_name = c.col_name
                             ORDER BY x2.sc DESC, x2.n DESC LIMIT 5) x) AS matches
              FROM cand c
             GROUP BY c.schema_name, c.rel_name, c.col_name
        )
        SELECT d.node_id, g.sc::float8, g.matches
          FROM grouped g
          JOIN rvbbit.catalog_docs d
            ON d.graph_id = graph AND d.kind = 'db_column'
           AND d.schema_name = g.schema_name AND d.rel_name = g.rel_name AND d.col_name = g.col_name
         ORDER BY g.sc DESC
         LIMIT k;
END $fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 6. find — RRF over the seams, gates, and the envelope
-- ═══════════════════════════════════════════════════════════════════════

-- C binding for the batched specialist call (specialists/mod.rs). $libdir
-- literal because migrate() runs via SPI where MODULE_PATHNAME is not
-- substituted (0044/0135/0148 precedent). Guarded: on a box whose .so
-- predates the symbol the binding is skipped and find's rerank gate reports
-- itself unavailable instead of failing the migration.
DO $$ BEGIN
    BEGIN
        CREATE OR REPLACE FUNCTION rvbbit.call_specialist_batch(specialist_name text, inputs jsonb)
        RETURNS jsonb LANGUAGE c VOLATILE
        AS '$libdir/pg_rvbbit', 'call_specialist_batch_wrapper';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'rvbbit: call_specialist_batch binding skipped (%); rerank gate stays off until the extension library is updated', SQLERRM;
    END;
END $$;

-- Per-hit envelope. All lookups are point reads on the snapshots above plus
-- live planner stats; nothing here scans data.
CREATE OR REPLACE FUNCTION rvbbit.find_envelope(
    p_node_id bigint,
    p_kind    text,
    p_schema  text,
    p_rel     text,
    p_col     text,
    p_graph   text DEFAULT 'db_catalog')
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_rel     text := p_schema || '.' || p_rel;
    v_oid     oid;
    v_props   jsonb;
    v_tprops  jsonb;
    v_object  jsonb;
    v_lineage jsonb;
    v_fresh   jsonb;
    v_usage   jsonb;
    v_meaning jsonb;
    v_ask     jsonb;
    v_l       rvbbit.relation_lineage%ROWTYPE;
    v_has_l   boolean := false;
    v_crawled timestamptz;
    v_accel   jsonb;
BEGIN
    v_oid := to_regclass(format('%I.%I', p_schema, p_rel))::oid;

    SELECT properties INTO v_props FROM rvbbit.kg_nodes WHERE node_id = p_node_id;
    IF p_kind = 'db_column' THEN
        SELECT properties INTO v_tprops FROM rvbbit.kg_nodes n
         WHERE n.graph_id = p_graph AND n.kind = 'db_table'
           AND (n.label = v_rel OR n.label_norm = lower(v_rel)) LIMIT 1;
    ELSE
        v_tprops := v_props;
    END IF;

    v_object := jsonb_strip_nulls(jsonb_build_object(
        'kind', p_kind, 'schema', p_schema, 'rel', p_rel, 'col', p_col,
        'relation', v_rel, 'exists', v_oid IS NOT NULL,
        'relkind', v_tprops->>'relkind',
        'n_rows', (v_tprops->>'n_rows')::bigint,
        'n_columns', (v_tprops->>'n_columns')::int,
        'data_type', CASE WHEN p_kind = 'db_column' THEN v_props->>'data_type' END,
        'ndv', CASE WHEN p_kind = 'db_column' THEN (v_props->>'ndv')::bigint END,
        'null_frac', CASE WHEN p_kind = 'db_column' THEN round((v_props->>'null_frac')::numeric, 3) END,
        'is_pk', CASE WHEN p_kind = 'db_column' THEN (v_props->>'is_pk')::boolean END,
        'fk_target', CASE WHEN p_kind = 'db_column' THEN v_props->>'fk_target' END,
        'example_values', CASE WHEN p_kind = 'db_column' THEN
            (SELECT jsonb_agg(e->>'value') FROM (SELECT e FROM jsonb_array_elements(coalesce(v_props->'example_values', '[]'::jsonb)) e LIMIT 6) s) END,
        'comment', v_props->>'comment'));

    -- lineage
    SELECT * INTO v_l FROM rvbbit.relation_lineage WHERE relation = v_rel;
    v_has_l := FOUND;
    IF v_has_l THEN
        v_lineage := jsonb_strip_nulls(jsonb_build_object(
            'role', v_l.role, 'source_system', v_l.source_system, 'adapter', v_l.adapter,
            'connection', v_l.connection, 'load_mode', v_l.load_mode, 'primary_key', to_jsonb(v_l.primary_key),
            'cursor_column', v_l.cursor_column, 'sync_frequency_min', v_l.sync_frequency_min,
            'paused', v_l.paused, 'last_load_at', v_l.last_load_at, 'last_load_status', v_l.last_load_status,
            'last_load_rows', v_l.last_load_rows, 'detail', v_l.detail, 'as_of', v_l.refreshed_at));
    ELSE
        SELECT jsonb_strip_nulls(jsonb_build_object('role', bs.role, 'source_system', bs.source_system,
                                                    'adapter', bs.adapter))
          INTO v_lineage
          FROM rvbbit.business_sources bs WHERE bs.schema_name = p_schema;
    END IF;

    -- fresh: live planner stats + acceleration state + last crawl
    IF to_regclass('rvbbit.catalog_snapshots') IS NOT NULL THEN
        SELECT captured_at INTO v_crawled FROM rvbbit.catalog_snapshots s
         WHERE s.graph_id = p_graph AND s.kind = 'db_table' AND s.obj_key = v_rel
         ORDER BY s.run_id DESC LIMIT 1;
    END IF;
    IF v_oid IS NOT NULL AND to_regclass('rvbbit.accel_freshness') IS NOT NULL THEN
        BEGIN
            SELECT jsonb_strip_nulls(jsonb_build_object(
                       'accelerated', true, 'lance', f.lance_accelerated,
                       'rows', f.parquet_rows, 'dirty_since', f.dirty_since,
                       'seconds_since_refresh', round(f.seconds_since_refresh),
                       'drift_rows', f.drift_rows, 'stale', coalesce(f.shadow_heap_dirty, false) OR coalesce(f.drift_rows, 0) > 0))
              INTO v_accel
              FROM rvbbit.accel_freshness f WHERE f.table_oid = v_oid LIMIT 1;
        EXCEPTION WHEN OTHERS THEN v_accel := NULL; END;
    END IF;
    -- rows: planner live tuples when the AM maintains them, else the crawl's count
    -- (rvbbit-AM relations report 0 live tuples to pg_stat).
    v_fresh := jsonb_strip_nulls(jsonb_build_object(
        'live_rows', CASE WHEN v_oid IS NOT NULL THEN nullif(pg_stat_get_live_tuples(v_oid), 0) END,
        'rows_est', coalesce(CASE WHEN v_oid IS NOT NULL THEN nullif(pg_stat_get_live_tuples(v_oid), 0) END,
                             (v_tprops->>'n_rows')::bigint),
        'last_analyze', CASE WHEN v_oid IS NOT NULL THEN
            greatest(pg_stat_get_last_analyze_time(v_oid), pg_stat_get_last_autoanalyze_time(v_oid)) END,
        'bytes', CASE WHEN v_oid IS NOT NULL AND (v_tprops->>'relkind') IN ('r', 'p', 'm') THEN pg_relation_size(v_oid) END,
        'last_crawled', v_crawled,
        'acceleration', v_accel));

    -- usage
    IF to_regclass('rvbbit.usage_snapshot') IS NOT NULL THEN
        SELECT jsonb_strip_nulls(jsonb_build_object('touches', u.touches, 'users', u.users,
                                                    'last_touch', u.last_touch, 'touches_30d', u.touches_30d,
                                                    'as_of', u.refreshed_at))
          INTO v_usage FROM rvbbit.usage_snapshot u WHERE u.object = v_rel;
    END IF;

    -- meaning
    IF to_regclass('rvbbit.relation_refs') IS NOT NULL THEN
        SELECT jsonb_strip_nulls(jsonb_build_object(
                   'domains', (SELECT jsonb_agg(jsonb_build_object('key', r.ref_name, 'name', r.ref_meta->>'name',
                                                                   'status', r.ref_meta->>'status', 'lifecycle', r.ref_meta->>'lifecycle')
                                                ORDER BY (r.ref_meta->>'status' = 'promoted') DESC, r.ref_name)
                                 FROM (SELECT * FROM rvbbit.relation_refs x WHERE x.relation = v_rel AND x.ref_kind = 'domain' LIMIT 6) r),
                   'metrics', (SELECT jsonb_agg(r.ref_name ORDER BY r.ref_name)
                                 FROM (SELECT * FROM rvbbit.relation_refs x WHERE x.relation = v_rel AND x.ref_kind = 'metric' LIMIT 8) r),
                   'cubes', (SELECT jsonb_agg(r.ref_name ORDER BY r.ref_name)
                               FROM (SELECT * FROM rvbbit.relation_refs x WHERE x.relation = v_rel AND x.ref_kind = 'cube' LIMIT 8) r),
                   'dashboards', (SELECT jsonb_agg(jsonb_build_object('slug', r.ref_name, 'name', r.ref_meta->>'name')
                                                   ORDER BY r.ref_meta->>'updated_at' DESC NULLS LAST)
                                    FROM (SELECT * FROM rvbbit.relation_refs x WHERE x.relation = v_rel AND x.ref_kind = 'dashboard' LIMIT 8) r),
                   'ref_counts', (SELECT jsonb_object_agg(ref_kind, n)
                                    FROM (SELECT ref_kind, count(*) AS n FROM rvbbit.relation_refs x WHERE x.relation = v_rel GROUP BY ref_kind) c)))
          INTO v_meaning;
    END IF;

    -- ask
    v_ask := jsonb_build_object(
        'sample_sql',
        CASE
            WHEN p_kind = 'db_column' THEN
                format('SELECT %I, count(*) AS n FROM %I.%I GROUP BY 1 ORDER BY 2 DESC LIMIT 20', p_col, p_schema, p_rel)
            ELSE format('SELECT * FROM %I.%I LIMIT 20', p_schema, p_rel)
        END,
        'describe', v_rel);

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'object', v_object, 'lineage', v_lineage, 'fresh', v_fresh,
        'usage', v_usage, 'meaning', v_meaning, 'ask', v_ask));
END $fn$;

-- The primitive. Returns one JSON document:
--   {query, k, graph, elapsed_ms, degraded:[…], seams:{…}, gates:{…}, hits:[…]}
-- opts (all optional):
--   kinds    text[]  default ['db_table','db_column','cube']
--   schemas  text[]  restrict to these schemas
--   seams    text[]  default ['dense','lexical','values']
--   rerank   bool    bge cross-encoder over the candidate pool (one specialist call)
--   judge    bool    Jev "can this answer the ask?" over the top ≤8 (one decision call)
--   pool     int     candidate pool per seam (default max(4k, 40))
CREATE OR REPLACE FUNCTION rvbbit.find_json(
    query text,
    k     int   DEFAULT 10,
    opts  jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0       timestamptz := clock_timestamp();
    v_q        text := btrim(coalesce(query, ''));
    v_k        int  := greatest(coalesce(k, 10), 1);
    v_graph    text := coalesce(nullif(btrim(opts->>'graph'), ''), 'db_catalog');
    v_kinds    text[] := coalesce((SELECT array_agg(x) FROM jsonb_array_elements_text(opts->'kinds') x),
                                  ARRAY['db_table', 'db_column', 'cube']);
    v_schemas  text[] := (SELECT array_agg(x) FROM jsonb_array_elements_text(opts->'schemas') x);
    v_seams    text[] := coalesce((SELECT array_agg(x) FROM jsonb_array_elements_text(opts->'seams') x),
                                  ARRAY['dense', 'lexical', 'values']);
    v_rerank   boolean := coalesce((opts->>'rerank')::boolean, false);
    v_judge    boolean := coalesce((opts->>'judge')::boolean, false);
    v_pool     int := coalesce((opts->>'pool')::int, greatest(v_k * 4, 40));
    v_rrf      float8 := 60;
    v_floor    float8 := 0.10;
    v_prefix   text := coalesce(current_setting('rvbbit.search_query_prefix', true), '');
    v_vec      real[];
    v_dense    jsonb;
    v_degraded text[] := '{}';
    v_cand     jsonb;
    v_hits     jsonb;
    v_gates    jsonb := '{}'::jsonb;
    v_inputs   jsonb;
    v_out      jsonb;
    v_n_cand   int;
    v_q_state  jsonb;
    v_questions jsonb;
    v_answers  jsonb;
    v_vals     jsonb;
    v_t_emb    numeric; v_t_seam numeric; v_t_env numeric; v_t_rerank numeric := 0; v_t_judge numeric := 0;
    -- query shape → ranking priors (docs/FIND_PLAN.md "priors")
    v_lq       text;
    v_qwords   int;
    v_is_ident boolean;
    v_analytic boolean;
    v_judge_min float8 := coalesce((opts->>'judge_min')::float8, 0.15);
    v_suppressed jsonb := '[]'::jsonb;
    v_words    text[];
BEGIN
    IF v_q = '' THEN
        RETURN jsonb_build_object('query', v_q, 'k', v_k, 'hits', '[]'::jsonb, 'degraded', '["empty query"]'::jsonb);
    END IF;
    v_lq := lower(v_q);
    v_qwords := coalesce(array_length(regexp_split_to_array(btrim(v_q), '\s+'), 1), 1);
    -- content words (≥ 3 chars, no function words) for the multi-word name test
    SELECT array_agg(w) INTO v_words
      FROM regexp_split_to_table(v_lq, '[^a-z0-9_]+') w
     WHERE length(w) >= 3
       AND w !~ '^(the|and|for|with|from|that|this|not|are|was|were|has|have|had|all|any|per|our|your|their|into|over|about|what|which|who|how|when|where)$';
    -- an identifier: snake_case and/or dotted, no spaces → exact-name override
    v_is_ident := v_q ~ '^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$' AND v_q ~ '[_.]';
    -- analytic intent → governed cubes/tables outrank stray columns
    v_analytic := v_q ~* '\m(by|per|daily|weekly|monthly|quarterly|yearly|trend|trends|funnel|spend|rate|rates|count|counts|total|totals|average|avg|sum|breakdown|over time|cohort|conversion|pipeline|availability|attribution|workload|volume|kpi|metric)\M';
    BEGIN v_floor := coalesce(nullif(current_setting('rvbbit.search_dense_floor', true), '')::float8, 0.10);
    EXCEPTION WHEN OTHERS THEN v_floor := 0.10; END;

    -- dense: embed + tier status
    IF 'dense' = ANY (v_seams) THEN
        v_dense := rvbbit.dense_seam_status(v_graph);
        IF NOT coalesce((v_dense->>'ready')::boolean, false) THEN
            v_degraded := v_degraded || ('dense: ' || coalesce(v_dense->>'reason', v_dense->>'tier'));
            v_seams := array_remove(v_seams, 'dense');
        ELSE
            BEGIN v_vec := rvbbit.embed(v_prefix || v_q, '', 'query');
            EXCEPTION WHEN OTHERS THEN v_vec := NULL; END;
            IF v_vec IS NULL THEN
                v_degraded := array_append(v_degraded, 'dense: embedder unavailable');
                v_seams := array_remove(v_seams, 'dense');
            END IF;
        END IF;
    END IF;
    v_t_emb := round(extract(epoch FROM clock_timestamp() - v_t0) * 1000);

    -- values seam presence
    IF 'values' = ANY (v_seams) THEN
        SELECT jsonb_build_object('relations', count(*) FILTER (WHERE status = 'ok'), 'values',
                                  coalesce(sum(values_indexed) FILTER (WHERE status = 'ok'), 0))
          INTO v_vals FROM rvbbit.value_index_state;
        IF coalesce((v_vals->>'relations')::int, 0) = 0 THEN
            v_degraded := array_append(v_degraded, 'values: index empty (run CALL rvbbit.value_index_sweep())');
            v_seams := array_remove(v_seams, 'values');
        END IF;
    END IF;

    -- retrieval: RRF over the seams that are on
    WITH d AS (
        SELECT dk.node_id, dk.score, row_number() OVER (ORDER BY dk.score DESC) AS r
          FROM rvbbit.dense_knn_tiered(v_vec, v_graph, v_kinds, v_pool, v_floor) dk
         WHERE 'dense' = ANY (v_seams)
    ),
    l AS (
        SELECT lk.node_id, lk.score, row_number() OVER (ORDER BY lk.score DESC) AS r
          FROM rvbbit.catalog_lexical_knn(CASE WHEN 'lexical' = ANY (v_seams) THEN v_q ELSE '' END,
                                          v_graph, v_kinds, v_pool) lk
    ),
    v AS (
        SELECT vk.node_id, vk.score, vk.matches, row_number() OVER (ORDER BY vk.score DESC) AS r
          FROM rvbbit.value_knn(CASE WHEN 'values' = ANY (v_seams) AND 'db_column' = ANY (v_kinds) THEN v_q ELSE '' END,
                                v_graph, v_pool) vk
    ),
    ids AS (
        SELECT node_id FROM d UNION SELECT node_id FROM l UNION SELECT node_id FROM v
    ),
    fused AS (
        SELECT i.node_id,
               coalesce(1.0 / (v_rrf + d.r), 0) + coalesce(1.0 / (v_rrf + l.r), 0) + coalesce(1.0 / (v_rrf + v.r), 0) AS rrf,
               d.r AS dense_rank, d.score AS dense_score,
               l.r AS lexical_rank, l.score AS lexical_score,
               v.r AS values_rank, v.score AS values_score, v.matches
          FROM ids i
          LEFT JOIN d ON d.node_id = i.node_id
          LEFT JOIN l ON l.node_id = i.node_id
          LEFT JOIN v ON v.node_id = i.node_id
    ),
    ranked AS (
        SELECT f.*, cd.kind, cd.schema_name, cd.rel_name, cd.col_name, cd.doc, cd.ident,
               n.properties->>'data_type' AS data_type,
               (SELECT avg(length(e->>'value'))
                  FROM jsonb_array_elements(CASE WHEN jsonb_typeof(n.properties->'example_values') = 'array'
                                                 THEN n.properties->'example_values' ELSE '[]'::jsonb END) e) AS example_len,
               -- table idents carry a trailing dot (schema.rel.), hence rtrim
               (rtrim(cd.ident, '.') = v_lq OR rtrim(cd.ident, '.') LIKE '%.' || rvbbit.like_escape(v_lq)
                OR (cd.kind <> 'db_column' AND lower(coalesce(cd.rel_name, '')) = v_lq)
                OR (cd.kind = 'db_column' AND lower(coalesce(cd.col_name, '')) = v_lq)) AS exact_ident,
               -- name hit: the whole query inside the object's own name, or (for
               -- multi-word asks) every content word inside the name path —
               -- "opportunity stage" names salesforce.opportunity.stage_name
               ((CASE WHEN position('.' IN v_lq) > 0 THEN rtrim(cd.ident, '.') LIKE '%' || rvbbit.like_escape(v_lq) || '%'
                      WHEN cd.kind = 'db_column' THEN lower(coalesce(cd.col_name, '')) LIKE '%' || rvbbit.like_escape(v_lq) || '%'
                      ELSE lower(coalesce(cd.rel_name, '')) LIKE '%' || rvbbit.like_escape(v_lq) || '%' END)
                OR (coalesce(array_length(v_words, 1), 0) >= 2
                    AND NOT EXISTS (SELECT 1 FROM unnest(v_words) w
                                     WHERE position(w IN CASE WHEN cd.kind = 'db_column' THEN rtrim(cd.ident, '.')
                                                              ELSE lower(coalesce(cd.rel_name, '')) END) = 0))) AS name_hit
          FROM fused f
          JOIN rvbbit.catalog_docs cd ON cd.graph_id = v_graph AND cd.node_id = f.node_id
          LEFT JOIN rvbbit.kg_nodes n ON n.node_id = f.node_id
         WHERE (v_schemas IS NULL OR cd.schema_name = ANY (v_schemas))
    ),
    -- priors: what the seams cannot know from text alone
    --   shape: JSON / long free-text columns hijack both the embedding and the
    --          substring seams ("Spring 2027" → campaign copy); typed, short
    --          columns are what a data question usually wants.
    --   kind:  analytic phrasing ("spend by day", "funnel") → cubes/tables;
    --          one-word asks → tables over their weak child columns.
    --   exact: an identifier query that names a relation/column exactly wins.
    priored AS (
        SELECT r.*,
               CASE WHEN r.kind = 'db_column' AND coalesce(r.data_type, '') ~* '^(json|jsonb|xml|bytea)' THEN 0.55
                    WHEN r.kind = 'db_column' AND coalesce(r.example_len, 0) > 120 THEN 0.7
                    ELSE 1.0 END AS shape,
               CASE WHEN r.kind IN ('cube', 'db_table') AND v_analytic THEN 1.25
                    WHEN r.kind IN ('cube', 'db_table') AND v_qwords <= 2 AND NOT v_is_ident THEN 1.2
                    WHEN r.kind = 'db_column' AND v_qwords = 1 AND NOT v_is_ident AND r.values_rank IS NULL THEN 0.85
                    ELSE 1.0 END AS kind_prior,
               -- identifier queries: an exact object wins outright; a bare word
               -- that IS a column/table name ("company") gets a smaller lift
               CASE WHEN v_is_ident AND r.exact_ident THEN 1.0
                    WHEN r.exact_ident AND v_qwords = 1 THEN 0.5
                    ELSE 0 END AS exact_bonus
          FROM ranked r
    ),
    scored0 AS (
        SELECT p.*, (p.rrf + p.exact_bonus) * p.shape * p.kind_prior AS adj
          FROM priored p
    ),
    -- analytic asks: when two or more columns of one relation surface, the
    -- relation itself is what the asker wants (its doc carries grain and
    -- description) even if no seam ranked it; the columns stay as evidence.
    parents AS (
        SELECT cd2.node_id, cd2.kind, cd2.schema_name, cd2.rel_name, cd2.col_name, cd2.doc, cd2.ident,
               max(s.adj) * 1.15 AS adj, count(*) AS children
          FROM scored0 s
          JOIN rvbbit.catalog_docs cd2
            ON cd2.graph_id = v_graph AND cd2.kind IN ('db_table', 'cube')
           AND cd2.schema_name = s.schema_name AND cd2.rel_name = s.rel_name
         WHERE v_analytic AND s.kind = 'db_column'
           AND NOT EXISTS (SELECT 1 FROM scored0 x WHERE x.node_id = cd2.node_id)
         GROUP BY cd2.node_id, cd2.kind, cd2.schema_name, cd2.rel_name, cd2.col_name, cd2.doc, cd2.ident
        HAVING count(*) >= 2
    ),
    merged AS (
        SELECT s.node_id, s.kind, s.schema_name, s.rel_name, s.col_name, s.doc,
               s.dense_rank, s.dense_score, s.lexical_rank, s.lexical_score, s.values_rank, s.values_score, s.matches,
               s.exact_ident, s.name_hit, s.shape, s.kind_prior, s.exact_bonus, s.adj, 0::bigint AS children
          FROM scored0 s
        UNION ALL
        SELECT p.node_id, p.kind, p.schema_name, p.rel_name, p.col_name, p.doc,
               NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               false, false, 1.0, 1.25, 0, p.adj, p.children   -- analytic kind prior, kept through rerank
          FROM parents p
    ),
    -- one row per relation/column: a cube exists both as the `cube` node and as
    -- the cubes.<name> table node — keep the cube (it carries grain/description)
    scored AS (
        SELECT m.*, max(m.adj) OVER () AS top,
               row_number() OVER (PARTITION BY m.schema_name, m.rel_name, coalesce(m.col_name, '')
                                  ORDER BY (m.kind = 'cube') DESC, m.adj DESC) AS dup_rn
          FROM merged m
    )
    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'node_id', r.node_id, 'kind', r.kind, 'schema', r.schema_name, 'rel', r.rel_name, 'col', r.col_name,
               'score', round((r.adj / nullif(r.top, 0))::numeric, 4),
               'rrf', r.adj,
               'seams', jsonb_strip_nulls(jsonb_build_object(
                   'dense', CASE WHEN r.dense_rank IS NOT NULL THEN jsonb_build_object('rank', r.dense_rank, 'score', round(r.dense_score::numeric, 3)) END,
                   'lexical', CASE WHEN r.lexical_rank IS NOT NULL THEN jsonb_build_object('rank', r.lexical_rank, 'score', round(r.lexical_score::numeric, 3)) END,
                   'values', CASE WHEN r.values_rank IS NOT NULL THEN jsonb_build_object('rank', r.values_rank, 'score', round(r.values_score::numeric, 3)) END)),
               'why', to_jsonb(array_remove(ARRAY[
                   CASE WHEN r.exact_ident THEN 'exact name' END,
                   CASE WHEN r.name_hit AND NOT r.exact_ident THEN 'name' END,
                   CASE WHEN r.matches IS NOT NULL THEN 'value' END,
                   CASE WHEN r.lexical_rank IS NOT NULL AND NOT r.name_hit THEN 'description' END,
                   CASE WHEN r.dense_rank IS NOT NULL THEN 'meaning' END,
                   CASE WHEN r.children > 0 THEN r.children || ' columns matched' END], NULL)),
               -- absolute signal, calibrated on the pilot: nonsense queries still
               -- reach centered cosine 0.40; real asks sit at 0.48–0.57
               'confidence', CASE
                   WHEN (v_is_ident AND r.exact_ident) OR r.name_hit OR coalesce(r.dense_score, 0) >= 0.48 OR coalesce(r.values_score, 0) >= 0.6 THEN 'high'
                   -- any lexical hit is a real text match (the seam only returns rows with signal)
                   WHEN r.children > 0 OR coalesce(r.dense_score, 0) >= 0.42 OR r.lexical_rank IS NOT NULL OR r.values_rank IS NOT NULL THEN 'medium'
                   ELSE 'low' END,
               'priors', jsonb_strip_nulls(jsonb_build_object(
                   'shape', nullif(r.shape, 1.0), 'kind', nullif(r.kind_prior, 1.0), 'exact', nullif(r.exact_bonus, 0),
                   'children', nullif(r.children, 0))),
               'matched_values', r.matches,
               'doc', r.doc)) ORDER BY r.adj DESC, r.node_id)
      INTO v_cand
      FROM (SELECT * FROM scored WHERE dup_rn = 1 ORDER BY adj DESC, node_id LIMIT greatest(v_k * 3, 24)) r;
    v_cand := coalesce(v_cand, '[]'::jsonb);
    v_n_cand := jsonb_array_length(v_cand);
    v_t_seam := round(extract(epoch FROM clock_timestamp() - v_t0) * 1000) - v_t_emb;

    -- gate: rerank (bge cross-encoder, one batched specialist call)
    IF v_rerank AND v_n_cand > 1 THEN
        BEGIN
            -- the passage carries the matched literal values too, else a column
            -- found BY value ("Coast Dental" in lead.company) reads as unrelated
            -- to the cross-encoder, whose text is the fingerprint doc
            SELECT jsonb_agg(jsonb_build_object(
                       'query', v_q,
                       'text', coalesce(c->>'schema', '') || '.' || coalesce(c->>'rel', '')
                               || coalesce('.' || (c->>'col'), '') || ' — ' || left(c->>'doc', 1000)
                               || coalesce(' Matching values: ' || (SELECT string_agg(m->>'value', '; ')
                                                                      FROM jsonb_array_elements(CASE WHEN jsonb_typeof(c->'matched_values') = 'array'
                                                                                                     THEN c->'matched_values' ELSE '[]'::jsonb END) m), '')))
              INTO v_inputs
              FROM jsonb_array_elements(v_cand) c;
            v_out := rvbbit.call_specialist_batch('rerank', v_inputs);
            IF jsonb_typeof(v_out) = 'array' AND jsonb_array_length(v_out) = v_n_cand THEN
                -- the priors survive the rerank: final = rerank × shape × kind + exact
                WITH r AS (
                    SELECT a.c, s.sc,
                           s.sc * coalesce((a.c->'priors'->>'shape')::float8, 1.0)
                                * coalesce((a.c->'priors'->>'kind')::float8, 1.0)
                                + coalesce((a.c->'priors'->>'exact')::float8, 0) AS fin
                      FROM jsonb_array_elements(v_cand) WITH ORDINALITY AS a(c, i)
                      JOIN LATERAL (SELECT CASE
                                        WHEN jsonb_typeof(v_out->(a.i::int - 1)) = 'object' THEN (v_out->(a.i::int - 1)->>'score')::float8
                                        WHEN jsonb_typeof(v_out->(a.i::int - 1)) = 'number' THEN (v_out->>(a.i::int - 1))::float8
                                    END AS sc) s ON true
                ),
                m AS (SELECT max(fin) AS mx FROM r)
                SELECT jsonb_agg(r.c || jsonb_build_object('rerank', r.sc, 'score', round((r.fin / nullif(m.mx, 0))::numeric, 4))
                                 ORDER BY r.fin DESC NULLS LAST, (r.c->>'rrf')::float8 DESC)
                  INTO v_cand
                  FROM r, m;
                v_gates := v_gates || jsonb_build_object('rerank', jsonb_build_object('applied', true, 'candidates', v_n_cand, 'specialist', 'rerank'));
            ELSE
                v_gates := v_gates || jsonb_build_object('rerank', jsonb_build_object('applied', false, 'reason', 'unexpected output shape'));
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_gates := v_gates || jsonb_build_object('rerank', jsonb_build_object('applied', false, 'reason', left(SQLERRM, 300)));
        END;
    ELSIF v_rerank THEN
        v_gates := v_gates || jsonb_build_object('rerank', jsonb_build_object('applied', false, 'reason', 'fewer than 2 candidates'));
    END IF;
    v_t_rerank := round(extract(epoch FROM clock_timestamp() - v_t0) * 1000) - v_t_emb - v_t_seam;

    -- top-k + envelope
    SELECT jsonb_agg((c - 'rrf' - 'doc')
                     || jsonb_build_object('rank', i, 'snippet', left(c->>'doc', 400))
                     || rvbbit.find_envelope((c->>'node_id')::bigint, c->>'kind', c->>'schema', c->>'rel', c->>'col', v_graph)
                     ORDER BY i)
      INTO v_hits
      FROM (SELECT c, row_number() OVER () AS i
              FROM jsonb_array_elements(v_cand) c LIMIT v_k) t;
    v_hits := coalesce(v_hits, '[]'::jsonb);
    v_t_env := round(extract(epoch FROM clock_timestamp() - v_t0) * 1000) - v_t_emb - v_t_seam - v_t_rerank;

    -- gate: judge (Jev, one decision call, ≤ 8 questions)
    IF v_judge AND jsonb_array_length(v_hits) > 0
       AND to_regprocedure('rvbbit.clover_decide(jsonb,jsonb,jsonb)') IS NOT NULL THEN
        BEGIN
            v_q_state := jsonb_build_object('ask', v_q);
            SELECT jsonb_object_agg('h' || (h->>'rank'),
                       jsonb_build_object('type', 'noul',
                           'instructions', 'The state holds a data question ("ask"). Score how likely it is that the '
                               || 'warehouse object described below is a DIRECT place to answer it (its rows or columns '
                               || 'hold the answer, not merely related vocabulary). Object: '
                               || coalesce(h->>'schema', '') || '.' || coalesce(h->>'rel', '') || coalesce('.' || (h->>'col'), '')
                               || ' — ' || left(h->>'snippet', 400)))
              INTO v_questions
              FROM (SELECT h FROM jsonb_array_elements(v_hits) h LIMIT 8) s;
            v_answers := rvbbit.clover_decide(v_q_state, v_questions, '{}'::jsonb);
            SELECT jsonb_agg(h || jsonb_build_object('answers_ask', (v_answers->'answers'->('h' || (h->>'rank'))->>'noul')::float8) ORDER BY (h->>'rank')::int)
              INTO v_hits FROM jsonb_array_elements(v_hits) h;
            -- a judged hit the model scores below judge_min is suppressed (kept
            -- in `suppressed` with its score so the caller can see what was cut)
            IF v_judge_min > 0 THEN
                SELECT coalesce(jsonb_agg(h ORDER BY (h->>'rank')::int) FILTER (WHERE (h->>'answers_ask')::float8 < v_judge_min), '[]'::jsonb),
                       coalesce(jsonb_agg(h ORDER BY (h->>'rank')::int) FILTER (WHERE (h->>'answers_ask') IS NULL OR (h->>'answers_ask')::float8 >= v_judge_min), '[]'::jsonb)
                  INTO v_suppressed, v_hits
                  FROM jsonb_array_elements(v_hits) h;
            END IF;
            v_gates := v_gates || jsonb_build_object('judge', jsonb_build_object('applied', true, 'judged', least(jsonb_array_length(v_hits) + jsonb_array_length(v_suppressed), 8),
                                                                                 'suppressed', jsonb_array_length(v_suppressed), 'judge_min', v_judge_min,
                                                                                 'model', v_answers->>'model'));
        EXCEPTION WHEN OTHERS THEN
            v_gates := v_gates || jsonb_build_object('judge', jsonb_build_object('applied', false, 'reason', left(SQLERRM, 300)));
        END;
        v_t_judge := round(extract(epoch FROM clock_timestamp() - v_t0) * 1000) - v_t_emb - v_t_seam - v_t_rerank - v_t_env;
    END IF;

    RETURN jsonb_build_object(
        'query', v_q, 'k', v_k, 'graph', v_graph,
        'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000),
        'timing_ms', jsonb_strip_nulls(jsonb_build_object('embed', v_t_emb, 'seams', v_t_seam,
                                                          'rerank', nullif(v_t_rerank, 0), 'envelope', v_t_env,
                                                          'judge', nullif(v_t_judge, 0))),
        'degraded', to_jsonb(v_degraded),
        'seams', jsonb_build_object('on', to_jsonb(v_seams), 'dense', v_dense, 'values', v_vals, 'candidates', v_n_cand),
        'gates', v_gates,
        'query_shape', jsonb_build_object('words', v_qwords, 'identifier', v_is_ident, 'analytic', v_analytic),
        -- result-level confidence = the top hit's absolute signal; `weak` means
        -- nothing matched by name, by value, or with a strong embedding — a
        -- nonsense query still gets ranked rows, but they are labelled as such
        'confidence', coalesce((SELECT CASE WHEN bool_or(h->>'confidence' = 'high') THEN 'high'
                                            WHEN bool_or(h->>'confidence' = 'medium') THEN 'medium'
                                            WHEN count(*) > 0 THEN 'low' END
                                  FROM (SELECT h FROM jsonb_array_elements(v_hits) h LIMIT 3) t), 'none'),
        'weak', NOT EXISTS (SELECT 1 FROM (SELECT h FROM jsonb_array_elements(v_hits) h LIMIT 3) t
                             WHERE h->>'confidence' IN ('high', 'medium')),
        'suppressed', v_suppressed,
        'hits', v_hits);
END $fn$;

-- Set-returning form for SQL callers: one row per hit, envelope as `hit`.
CREATE OR REPLACE FUNCTION rvbbit.find(
    query text,
    k     int   DEFAULT 10,
    opts  jsonb DEFAULT '{}'::jsonb)
RETURNS TABLE (rank int, node_id bigint, kind text, schema_name text, rel_name text, col_name text,
               score float8, hit jsonb)
LANGUAGE sql VOLATILE AS $fn$
    SELECT (h->>'rank')::int, (h->>'node_id')::bigint, h->>'kind', h->>'schema', h->>'rel', h->>'col',
           (h->>'score')::float8, h
      FROM jsonb_array_elements(rvbbit.find_json(query, k, opts)->'hits') h
     ORDER BY 1
$fn$;

-- jsonb array or empty array (JSON null / scalars → []), for the flat form.
CREATE OR REPLACE FUNCTION rvbbit._jsonb_arr(x jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE WHEN jsonb_typeof(x) = 'array' THEN x ELSE '[]'::jsonb END
$fn$;

-- Flat form for people (psql, DataRabbit, spreadsheets): the envelope's
-- headline facts as columns. (DROP first: an OUT-column change cannot be
-- applied with CREATE OR REPLACE.)
DROP FUNCTION IF EXISTS rvbbit.find_flat(text, integer, jsonb);
CREATE OR REPLACE FUNCTION rvbbit.find_flat(
    query text,
    k     int   DEFAULT 10,
    opts  jsonb DEFAULT '{}'::jsonb)
RETURNS TABLE (
    rank int, kind text, relation text, "column" text, score float8, seams text,
    role text, source_system text, adapter text, connection text, load_mode text,
    last_load_at timestamptz, last_load_status text, rows_est bigint,
    touches_30d bigint, domains text, metrics text, cubes text, dashboards text,
    matched_values text, sample_sql text, degraded text)
LANGUAGE sql VOLATILE AS $fn$
    WITH r AS (SELECT rvbbit.find_json(query, k, opts) AS j)
    SELECT (h->>'rank')::int, h->>'kind',
           (h->>'schema') || '.' || (h->>'rel'), h->>'col',
           (h->>'score')::float8,
           (SELECT string_agg(s, '+') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(h->'seams') = 'object' THEN h->'seams' ELSE '{}'::jsonb END) s),
           h#>>'{lineage,role}', h#>>'{lineage,source_system}', h#>>'{lineage,adapter}',
           h#>>'{lineage,connection}', h#>>'{lineage,load_mode}',
           (h#>>'{lineage,last_load_at}')::timestamptz, h#>>'{lineage,last_load_status}',
           (h#>>'{fresh,rows_est}')::bigint,
           (h#>>'{usage,touches_30d}')::bigint,
           (SELECT string_agg(d->>'key', ', ') FROM jsonb_array_elements(rvbbit._jsonb_arr(h#>'{meaning,domains}')) d),
           (SELECT string_agg(m, ', ') FROM jsonb_array_elements_text(rvbbit._jsonb_arr(h#>'{meaning,metrics}')) m),
           (SELECT string_agg(m, ', ') FROM jsonb_array_elements_text(rvbbit._jsonb_arr(h#>'{meaning,cubes}')) m),
           (SELECT string_agg(d->>'slug', ', ') FROM jsonb_array_elements(rvbbit._jsonb_arr(h#>'{meaning,dashboards}')) d),
           (SELECT string_agg((v->>'value') || ' ×' || (v->>'n'), ' | ') FROM jsonb_array_elements(rvbbit._jsonb_arr(h->'matched_values')) v),
           h#>>'{ask,sample_sql}',
           (SELECT string_agg(d, '; ') FROM jsonb_array_elements_text(rvbbit._jsonb_arr(r.j->'degraded')) d)
      FROM r, jsonb_array_elements(rvbbit._jsonb_arr(r.j->'hits')) h
     ORDER BY 1
$fn$;

-- ═══════════════════════════════════════════════════════════════════════
-- 7. Refresh orchestration + schedules + crawl tail
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION rvbbit.find_refresh(graph text DEFAULT 'db_catalog')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0  timestamptz := clock_timestamp();
    v_out jsonb := '{}'::jsonb;
BEGIN
    BEGIN v_out := v_out || jsonb_build_object('dense_tier', rvbbit.pgvector_refresh_catalog_if_stale(graph));
    EXCEPTION WHEN OTHERS THEN v_out := v_out || jsonb_build_object('dense_tier', jsonb_build_object('error', SQLERRM)); END;
    BEGIN v_out := v_out || jsonb_build_object('relation_refs', rvbbit.relation_refs_refresh());
    EXCEPTION WHEN OTHERS THEN v_out := v_out || jsonb_build_object('relation_refs', jsonb_build_object('error', SQLERRM)); END;
    BEGIN v_out := v_out || jsonb_build_object('usage', rvbbit.usage_snapshot_refresh());
    EXCEPTION WHEN OTHERS THEN v_out := v_out || jsonb_build_object('usage', jsonb_build_object('error', SQLERRM)); END;
    BEGIN v_out := v_out || jsonb_build_object('lineage', rvbbit.relation_lineage_refresh());
    EXCEPTION WHEN OTHERS THEN v_out := v_out || jsonb_build_object('lineage', jsonb_build_object('error', SQLERRM)); END;
    RETURN v_out || jsonb_build_object('elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- Same contract as schedule_metadata_vacuum: raises with the exact
-- cron.schedule_in_database call when pg_cron's home is another database.
CREATE OR REPLACE FUNCTION rvbbit._schedule_in_cron_home(job_name text, cron_schedule text, command text)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE
    jobid     bigint;
    cron_home text := current_setting('cron.database_name', true);
    this_db   text := current_database();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        RAISE EXCEPTION 'pg_cron is not installed; cannot schedule %.', job_name
            USING HINT = 'Run the command from any external scheduler: ' || command;
    END IF;
    IF cron_home IS NOT NULL AND cron_home <> '' AND cron_home <> this_db THEN
        RAISE EXCEPTION 'pg_cron home database is %, not %; cron.* is not callable here.', cron_home, this_db
            USING HINT = format('Connect to %L and run: SELECT cron.schedule_in_database(%L, %L, %L, %L);',
                                cron_home, job_name, cron_schedule, command, this_db);
    END IF;
    EXECUTE format('SELECT cron.schedule(%L, %L, %L)', job_name, cron_schedule, command) INTO jobid;
    RETURN jobid;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.schedule_find_refresh(cron_schedule text DEFAULT '25 4 * * *')
RETURNS bigint LANGUAGE sql AS $fn$
    SELECT rvbbit._schedule_in_cron_home('rvbbit_find_refresh', cron_schedule, 'SELECT rvbbit.find_refresh();')
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.schedule_value_index_sweep(cron_schedule text DEFAULT '*/10 * * * *')
RETURNS bigint LANGUAGE sql AS $fn$
    SELECT rvbbit._schedule_in_cron_home('rvbbit_value_index_sweep', cron_schedule, 'CALL rvbbit.value_index_sweep(120);')
$fn$;

-- Crawl tail: after a parallel crawl finalizes, refresh the find snapshots so
-- the dense tier and lineage/refs/usage never drift from the catalog. Body is
-- 0033's verbatim plus the guarded tail call.
CREATE OR REPLACE PROCEDURE rvbbit.catalog_crawl_run_parallel(
    schemas          text[]  DEFAULT NULL,
    graph            text    DEFAULT 'db_catalog',
    sample_rows      int     DEFAULT 50000,
    examples_k       int     DEFAULT 12,
    do_embed         boolean DEFAULT true,
    embed_specialist text    DEFAULT '',
    parallelism      int     DEFAULT 4)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_graph   text := COALESCE(NULLIF(btrim(graph), ''), 'db_catalog');
    v_run     bigint;
    v_total   int;
    v_n       int := greatest(1, least(coalesce(parallelism, 1), 16));
    v_conn    text;
    v_have_dblink boolean := false;
    i         int;
BEGIN
    PERFORM rvbbit.catalog_kg_ensure();

    INSERT INTO rvbbit.catalog_runs (graph_id, status, schemas)
    VALUES (v_graph, 'running', schemas)
    RETURNING run_id INTO v_run;
    COMMIT;

    WITH ordered AS (
        SELECT c.oid AS reloid, n.nspname AS sch, c.relname AS rel,
               row_number() OVER (ORDER BY n.nspname, c.relname) AS ord
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'm', 'v')
           AND NOT c.relispartition
           AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'rvbbit')
           AND n.nspname NOT LIKE 'pg_toast%'
           AND n.nspname NOT LIKE 'pg_temp_%'
           AND n.nspname <> ALL (rvbbit._catalog_excluded_schemas())
           AND (schemas IS NULL OR n.nspname = ANY (schemas))
    )
    INSERT INTO rvbbit.catalog_crawl_progress
        (run_id, ordinal, total, reloid, schema_name, rel_name, status, started_at)
    SELECT v_run, ord, (SELECT count(*) FROM ordered), reloid, sch, rel, 'queued', now()
      FROM ordered;
    GET DIAGNOSTICS v_total = ROW_COUNT;
    COMMIT;

    IF v_total = 0 THEN
        UPDATE rvbbit.catalog_runs SET status = 'ok', finished_at = now() WHERE run_id = v_run;
        COMMIT;
        RETURN;
    END IF;

    IF v_n > 1 THEN
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'dblink') THEN
                EXECUTE 'CREATE EXTENSION IF NOT EXISTS dblink';
            END IF;
            v_have_dblink := true;
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'catalog_crawl_run_parallel: dblink unavailable (%), running serially', SQLERRM;
            v_have_dblink := false;
        END;
    END IF;

    IF v_n <= 1 OR NOT v_have_dblink THEN
        CALL rvbbit._catalog_crawl_shard(v_run, 0, 1, v_graph,
                                         sample_rows, examples_k, do_embed, embed_specialist);
    ELSE
        v_conn := rvbbit._crawl_dblink_conninfo();
        FOR i IN 0 .. v_n - 1 LOOP
            PERFORM dblink_connect('rvbbit_crawl_' || i, v_conn);
            PERFORM dblink_send_query('rvbbit_crawl_' || i,
                format('CALL rvbbit._catalog_crawl_shard(%s, %s, %s, %L, %s, %s, %L::boolean, %L)',
                       v_run, i, v_n, v_graph, sample_rows, examples_k, do_embed::text, embed_specialist));
        END LOOP;
        FOR i IN 0 .. v_n - 1 LOOP
            BEGIN
                PERFORM * FROM dblink_get_result('rvbbit_crawl_' || i) AS t(x text);
            EXCEPTION WHEN others THEN
                RAISE NOTICE 'catalog_crawl_run_parallel: worker % failed: %', i, SQLERRM;
            END;
            BEGIN PERFORM dblink_disconnect('rvbbit_crawl_' || i); EXCEPTION WHEN others THEN NULL; END;
        END LOOP;
    END IF;

    CALL rvbbit._catalog_crawl_shared_edges(v_run, v_graph);
    COMMIT;

    UPDATE rvbbit.catalog_runs SET
        status        = 'ok',
        finished_at   = now(),
        tables_seen   = (SELECT count(*) FROM rvbbit.catalog_crawl_progress WHERE run_id = v_run AND status = 'ok'),
        columns_seen  = (SELECT coalesce(sum(n_columns), 0) FROM rvbbit.catalog_crawl_progress WHERE run_id = v_run AND status = 'ok'),
        docs_embedded = (SELECT coalesce(sum(n_embedded), 0) FROM rvbbit.catalog_crawl_progress WHERE run_id = v_run AND status = 'ok')
     WHERE run_id = v_run;
    COMMIT;

    -- find snapshots (dense tier if stale, refs, usage, lineage); never fails the crawl
    BEGIN
        PERFORM rvbbit.find_refresh(v_graph);
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'catalog_crawl_run_parallel: find_refresh skipped (%)', SQLERRM;
    END;
    COMMIT;
END $fn$;
