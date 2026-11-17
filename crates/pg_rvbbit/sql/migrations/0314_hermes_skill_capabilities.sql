-- 0314: Hermes skills are discoverable capability stubs, not prompt ballast.
--
-- The canonical bodies remain on Hermes's durable volume. Warehouse scans
-- only frontmatter and reconciles this compact projection when the tree
-- changes. capability_search can then discover a relevant procedure and the
-- agent loads exactly that body with skill_view(name), on demand.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_skill_capabilities (
    load_name text PRIMARY KEY,
    skill_name text NOT NULL,
    category text NOT NULL DEFAULT 'general',
    description text NOT NULL,
    source_path text NOT NULL UNIQUE,
    content_hash text NOT NULL,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hermes_skill_capabilities_load_name_check
        CHECK (length(btrim(load_name)) BETWEEN 1 AND 500),
    CONSTRAINT hermes_skill_capabilities_skill_name_check
        CHECK (length(btrim(skill_name)) BETWEEN 1 AND 128),
    CONSTRAINT hermes_skill_capabilities_category_check
        CHECK (length(btrim(category)) BETWEEN 1 AND 500),
    CONSTRAINT hermes_skill_capabilities_description_check
        CHECK (length(description) BETWEEN 1 AND 4000),
    CONSTRAINT hermes_skill_capabilities_source_path_check
        CHECK (source_path !~ '(^/|(^|/)\.\.(/|$))'),
    CONSTRAINT hermes_skill_capabilities_hash_check
        CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_skill_capability_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    manifest_hash text NOT NULL,
    skill_count int NOT NULL DEFAULT 0 CHECK (skill_count >= 0),
    synced_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hermes_skill_capability_state_hash_check
        CHECK (manifest_hash ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION rvbbit.sync_hermes_skill_capabilities(
    p_manifest_hash text,
    p_items jsonb,
    p_do_embed boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph constant text := 'rvbbit_capabilities';
    v_embed_ok boolean := coalesce(p_do_embed,true);
    v_node bigint;
    v_doc text;
    v_vec real[];
    v_count int := 0;
    v_removed int := 0;
    v_existing text;
    rec record;
BEGIN
    IF coalesce(p_manifest_hash,'') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid Hermes skill manifest hash';
    END IF;
    IF jsonb_typeof(p_items) <> 'array' THEN
        RAISE EXCEPTION 'Hermes skill projection must be a JSON array';
    END IF;

    SELECT manifest_hash INTO v_existing
      FROM rvbbit.hermes_skill_capability_state
     WHERE singleton;
    IF v_existing = p_manifest_hash THEN
        SELECT count(*)::int INTO v_count FROM rvbbit.hermes_skill_capabilities;
        RETURN jsonb_build_object(
            'changed', false,
            'manifest', p_manifest_hash,
            'skills', v_count
        );
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS pg_temp.calliope_skill_projection (
        load_name text PRIMARY KEY,
        skill_name text NOT NULL,
        category text NOT NULL,
        description text NOT NULL,
        source_path text NOT NULL UNIQUE,
        content_hash text NOT NULL
    ) ON COMMIT DROP;
    TRUNCATE pg_temp.calliope_skill_projection;

    INSERT INTO pg_temp.calliope_skill_projection
        (load_name,skill_name,category,description,source_path,content_hash)
    SELECT btrim(x.load_name), btrim(x.name),
           coalesce(nullif(btrim(x.category),''),'general'),
           left(btrim(x.description),4000), btrim(x.source_path),
           lower(btrim(x.content_hash))
      FROM jsonb_to_recordset(p_items) AS x(
          name text,
          load_name text,
          category text,
          description text,
          source_path text,
          content_hash text
      )
     WHERE length(btrim(coalesce(x.load_name,''))) BETWEEN 1 AND 500
       AND length(btrim(coalesce(x.name,''))) BETWEEN 1 AND 128
       AND length(btrim(coalesce(x.description,''))) BETWEEN 1 AND 4000
       AND length(btrim(coalesce(x.source_path,''))) BETWEEN 1 AND 2000
       AND btrim(x.source_path) !~ '(^/|(^|/)\.\.(/|$))'
       AND lower(btrim(coalesce(x.content_hash,''))) ~ '^[0-9a-f]{64}$';

    -- Retire deleted or renamed skills from both the searchable document
    -- projection and the graph. Node deletion cascades aliases and edges.
    WITH obsolete AS (
        SELECT n.node_id
          FROM rvbbit.kg_nodes n
         WHERE n.graph_id=v_graph
           AND n.kind='cap_skill'
           AND NOT EXISTS (
               SELECT 1 FROM pg_temp.calliope_skill_projection p
                WHERE p.load_name=n.label
           )
    ), deleted_docs AS (
        DELETE FROM rvbbit.catalog_docs d
         USING obsolete o
         WHERE d.graph_id=v_graph AND d.node_id=o.node_id
         RETURNING d.node_id
    )
    SELECT count(*)::int INTO v_removed FROM obsolete;

    DELETE FROM rvbbit.kg_nodes n
     WHERE n.graph_id=v_graph
       AND n.kind='cap_skill'
       AND NOT EXISTS (
           SELECT 1 FROM pg_temp.calliope_skill_projection p
            WHERE p.load_name=n.label
       );
    DELETE FROM rvbbit.hermes_skill_capabilities s
     WHERE NOT EXISTS (
         SELECT 1 FROM pg_temp.calliope_skill_projection p
          WHERE p.load_name=s.load_name
     );

    INSERT INTO rvbbit.hermes_skill_capabilities
        (load_name,skill_name,category,description,source_path,content_hash)
    SELECT load_name,skill_name,category,description,source_path,content_hash
      FROM pg_temp.calliope_skill_projection
    ON CONFLICT (load_name) DO UPDATE SET
        skill_name=EXCLUDED.skill_name,
        category=EXCLUDED.category,
        description=EXCLUDED.description,
        source_path=EXCLUDED.source_path,
        content_hash=EXCLUDED.content_hash,
        updated_at=now();

    -- One remote batch warms Clover's embedding cache. Per-row calls below
    -- then become cache reads. Lexical capability search still works if the
    -- embedder is temporarily unavailable.
    IF v_embed_ok THEN
        BEGIN
            PERFORM rvbbit.embed_batch(array_agg(doc), '', 'document')
              FROM (
                  SELECT rvbbit.capability_doc(
                      'cap_skill',s.load_name,
                      'skill_view(name => ' || quote_literal(s.load_name) || ')',
                      'Hermes procedural skill in category ' || s.category || '. '
                          || s.description
                          || ' Load only when materially relevant; system, SOUL, pinned docs, and authoritative tool results take precedence.',
                      '', 'free', ARRAY['skill','procedure',s.category]
                  ) AS doc
                    FROM rvbbit.hermes_skill_capabilities s
              ) docs;
        EXCEPTION WHEN others THEN
            v_embed_ok := false;
        END;
    END IF;

    FOR rec IN
        SELECT * FROM rvbbit.hermes_skill_capabilities ORDER BY load_name
    LOOP
        v_doc := rvbbit.capability_doc(
            'cap_skill',rec.load_name,
            'skill_view(name => ' || quote_literal(rec.load_name) || ')',
            'Hermes procedural skill in category ' || rec.category || '. '
                || rec.description
                || ' Load only when materially relevant; system, SOUL, pinned docs, and authoritative tool results take precedence.',
            '', 'free', ARRAY['skill','procedure',rec.category]
        );
        v_node := rvbbit.kg_assert_node(
            'cap_skill',rec.load_name,
            jsonb_build_object(
                'skill_name',rec.skill_name,
                'category',rec.category,
                'source_path',rec.source_path,
                'content_hash',rec.content_hash,
                'loader','skill_view',
                'load_name',rec.load_name,
                'search_doc',v_doc
            ),
            1.0,'',0.0,v_graph
        );
        v_vec := NULL;
        IF v_embed_ok THEN
            BEGIN
                v_vec := rvbbit.embed(v_doc,'','document');
            EXCEPTION WHEN others THEN
                v_vec := NULL;
                v_embed_ok := false;
            END;
        END IF;
        INSERT INTO rvbbit.catalog_docs
            (node_id,graph_id,kind,schema_name,rel_name,col_name,doc,
             embedding,embedded_at,updated_at)
        VALUES
            (v_node,v_graph,'cap_skill',NULL,rec.load_name,NULL,v_doc,v_vec,
             CASE WHEN v_vec IS NOT NULL THEN now() END,now())
        ON CONFLICT (graph_id,node_id) DO UPDATE SET
            kind=EXCLUDED.kind,
            rel_name=EXCLUDED.rel_name,
            doc=EXCLUDED.doc,
            embedding=coalesce(EXCLUDED.embedding,rvbbit.catalog_docs.embedding),
            embedded_at=CASE WHEN EXCLUDED.embedding IS NOT NULL
                             THEN EXCLUDED.embedded_at
                             ELSE rvbbit.catalog_docs.embedded_at END,
            updated_at=now();
        v_count := v_count + 1;
    END LOOP;

    INSERT INTO rvbbit.hermes_skill_capability_state
        (singleton,manifest_hash,skill_count,synced_at)
    VALUES (true,p_manifest_hash,v_count,now())
    ON CONFLICT (singleton) DO UPDATE SET
        manifest_hash=EXCLUDED.manifest_hash,
        skill_count=EXCLUDED.skill_count,
        synced_at=EXCLUDED.synced_at;

    RETURN jsonb_build_object(
        'changed',true,
        'manifest',p_manifest_hash,
        'skills',v_count,
        'removed',v_removed,
        'embedded',v_embed_ok
    );
END
$fn$;

COMMENT ON TABLE rvbbit.hermes_skill_capabilities IS
    'Compact searchable stubs for canonical SKILL.md files on Hermes durable storage; bodies remain file-backed and are loaded explicitly.';
COMMENT ON FUNCTION rvbbit.sync_hermes_skill_capabilities(text,jsonb,boolean) IS
    'Atomically reconcile Hermes skill metadata into cap_skill nodes in the system-wide capability Library.';
