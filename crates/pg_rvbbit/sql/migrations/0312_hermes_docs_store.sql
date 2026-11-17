-- 0312: a version-pinned, pre-enriched Hermes reference corpus for Calliope.
--
-- This is deliberately separate from Company Brain and Hindsight.  The
-- Calliope image imports a prebuilt bundle for the exact pinned Hermes
-- revision; customer appliances never crawl upstream docs or embed the corpus.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_doc_bundles (
    bundle_id text PRIMARY KEY,
    hermes_revision text NOT NULL,
    hermes_image_digest text NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL,
    content_hash text NOT NULL,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT false,
    installed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT hermes_doc_bundles_manifest_check
        CHECK (jsonb_typeof(manifest) = 'object'),
    CONSTRAINT hermes_doc_bundles_dimensions_check
        CHECK (embedding_dimensions > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS hermes_doc_bundles_one_active_idx
    ON rvbbit.hermes_doc_bundles (active)
    WHERE active;

CREATE TABLE IF NOT EXISTS rvbbit.hermes_docs (
    bundle_id text NOT NULL
        REFERENCES rvbbit.hermes_doc_bundles(bundle_id) ON DELETE CASCADE,
    doc_id text NOT NULL,
    source_kind text NOT NULL,
    path text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (bundle_id, doc_id),
    CONSTRAINT hermes_docs_source_kind_check
        CHECK (source_kind IN ('upstream', 'calliope_overlay')),
    CONSTRAINT hermes_docs_metadata_check
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS hermes_docs_bundle_source_idx
    ON rvbbit.hermes_docs (bundle_id, source_kind, path);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_doc_chunks (
    bundle_id text NOT NULL,
    chunk_id text NOT NULL,
    doc_id text NOT NULL,
    ordinal integer NOT NULL,
    heading text NOT NULL DEFAULT '',
    text text NOT NULL,
    embedding real[] NOT NULL,
    token_est integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(text, '')), 'B')
    ) STORED,
    PRIMARY KEY (bundle_id, chunk_id),
    FOREIGN KEY (bundle_id, doc_id)
        REFERENCES rvbbit.hermes_docs(bundle_id, doc_id) ON DELETE CASCADE,
    CONSTRAINT hermes_doc_chunks_ordinal_check CHECK (ordinal >= 0),
    CONSTRAINT hermes_doc_chunks_embedding_check
        CHECK (cardinality(embedding) > 0),
    CONSTRAINT hermes_doc_chunks_metadata_check
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS hermes_doc_chunks_doc_idx
    ON rvbbit.hermes_doc_chunks (bundle_id, doc_id, ordinal);
CREATE INDEX IF NOT EXISTS hermes_doc_chunks_fts_idx
    ON rvbbit.hermes_doc_chunks USING gin (search_tsv);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_doc_nodes (
    bundle_id text NOT NULL
        REFERENCES rvbbit.hermes_doc_bundles(bundle_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    kind text NOT NULL,
    label text NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (bundle_id, node_id),
    CONSTRAINT hermes_doc_nodes_properties_check
        CHECK (jsonb_typeof(properties) = 'object')
);

CREATE INDEX IF NOT EXISTS hermes_doc_nodes_kind_label_idx
    ON rvbbit.hermes_doc_nodes (bundle_id, kind, lower(label));

CREATE TABLE IF NOT EXISTS rvbbit.hermes_doc_edges (
    bundle_id text NOT NULL
        REFERENCES rvbbit.hermes_doc_bundles(bundle_id) ON DELETE CASCADE,
    edge_id text NOT NULL,
    source_node_id text NOT NULL,
    target_node_id text NOT NULL,
    relation text NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (bundle_id, edge_id),
    FOREIGN KEY (bundle_id, source_node_id)
        REFERENCES rvbbit.hermes_doc_nodes(bundle_id, node_id) ON DELETE CASCADE,
    FOREIGN KEY (bundle_id, target_node_id)
        REFERENCES rvbbit.hermes_doc_nodes(bundle_id, node_id) ON DELETE CASCADE,
    CONSTRAINT hermes_doc_edges_properties_check
        CHECK (jsonb_typeof(properties) = 'object')
);

CREATE INDEX IF NOT EXISTS hermes_doc_edges_source_idx
    ON rvbbit.hermes_doc_edges (bundle_id, source_node_id, relation);
CREATE INDEX IF NOT EXISTS hermes_doc_edges_target_idx
    ON rvbbit.hermes_doc_edges (bundle_id, target_node_id, relation);

CREATE OR REPLACE FUNCTION rvbbit.hermes_docs_search(
    p_query text,
    p_k integer DEFAULT 8,
    p_source_kinds text[] DEFAULT NULL
) RETURNS TABLE (
    bundle_id text,
    source_kind text,
    path text,
    title text,
    heading text,
    score double precision,
    lexical_score double precision,
    semantic_score double precision,
    chunk_text text,
    metadata jsonb
)
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_query text := btrim(coalesce(p_query, ''));
    v_k integer := greatest(1, least(coalesce(p_k, 8), 40));
    v_embedding real[];
    v_tsquery tsquery;
BEGIN
    IF v_query = '' THEN
        RETURN;
    END IF;

    v_tsquery := plainto_tsquery('english', v_query);
    BEGIN
        v_embedding := rvbbit.embed(v_query, '', 'query');
    EXCEPTION WHEN others THEN
        -- Exact-key and lexical lookup remain useful during an embedder outage.
        v_embedding := NULL;
    END;

    RETURN QUERY
    WITH active_bundle AS (
        SELECT b.bundle_id
        FROM rvbbit.hermes_doc_bundles b
        WHERE b.active
        ORDER BY b.installed_at DESC
        LIMIT 1
    ), ranked AS (
        SELECT c.bundle_id,
               d.source_kind,
               d.path,
               d.title,
               c.heading,
               c.text AS chunk_text,
               c.metadata || jsonb_build_object(
                   'doc_id', d.doc_id,
                   'chunk_id', c.chunk_id,
                   'ordinal', c.ordinal,
                   'doc_metadata', d.metadata
               ) AS metadata,
               ts_rank_cd(c.search_tsv, v_tsquery, 32)::double precision AS lexical_score,
               CASE
                   WHEN v_embedding IS NOT NULL
                    AND cardinality(c.embedding) = cardinality(v_embedding)
                   THEN rvbbit.cosine_vec(c.embedding, v_embedding)
                   ELSE 0::double precision
               END AS semantic_score,
               CASE
                   WHEN lower(d.path) = lower(v_query)
                     OR lower(d.title) = lower(v_query)
                     OR lower(c.heading) = lower(v_query) THEN 0.30
                   WHEN lower(d.path) LIKE '%' || lower(v_query) || '%'
                     OR lower(d.title) LIKE '%' || lower(v_query) || '%'
                     OR lower(c.heading) LIKE '%' || lower(v_query) || '%' THEN 0.12
                   ELSE 0::double precision
               END AS exact_boost,
               CASE WHEN d.source_kind = 'calliope_overlay'
                    THEN 0.035 ELSE 0::double precision END AS overlay_boost
        FROM active_bundle a
        JOIN rvbbit.hermes_doc_chunks c USING (bundle_id)
        JOIN rvbbit.hermes_docs d
          ON d.bundle_id = c.bundle_id AND d.doc_id = c.doc_id
        WHERE p_source_kinds IS NULL OR d.source_kind = ANY(p_source_kinds)
    )
    SELECT r.bundle_id,
           r.source_kind,
           r.path,
           r.title,
           r.heading,
           (0.62 * r.semantic_score + 0.33 * r.lexical_score
               + r.exact_boost + r.overlay_boost)::double precision AS score,
           r.lexical_score,
           r.semantic_score,
           r.chunk_text,
           r.metadata
    FROM ranked r
    WHERE r.lexical_score > 0
       OR r.semantic_score > 0
       OR r.exact_boost > 0
    ORDER BY score DESC, r.path, r.heading
    LIMIT v_k;
END
$fn$;

COMMENT ON FUNCTION rvbbit.hermes_docs_search(text, integer, text[]) IS
    'Hybrid lookup over the prebuilt docs for the exact Hermes revision shipped with Calliope. Calliope overlays are identified separately from upstream text.';

INSERT INTO rvbbit.capability_seed
    (name, kind, signature, description, example, cost_class, tags)
VALUES (
    'hermes_docs_search',
    'cap_syntax',
    'rvbbit.hermes_docs_search(query text, k int DEFAULT 8, source_kinds text[] DEFAULT NULL)',
    'Search the version-pinned Hermes documentation bundled with this Calliope appliance. Results combine exact config-key lookup, full text, and precomputed semantic vectors; each result identifies upstream Hermes text versus Calliope hosted guidance. Use before changing Hermes messaging, gateway, profile, cron, plugin, or configuration settings.',
    'SELECT * FROM rvbbit.hermes_docs_search(''configure Telegram messaging'', 8)',
    'cheap',
    ARRAY['hermes','docs','configuration','messaging','gateway','hosted']
)
ON CONFLICT (name) DO UPDATE SET
    kind = EXCLUDED.kind,
    signature = EXCLUDED.signature,
    description = EXCLUDED.description,
    example = EXCLUDED.example,
    cost_class = EXCLUDED.cost_class,
    tags = EXCLUDED.tags;
