-- Upgrade pg_rvbbit 2.0.15 -> 2.0.16
--
-- Cross-cutting org taxonomy: one mutable 2-level category tree shared by metrics
-- and alerts. Purely additive — a new table + functions, and the two catalog
-- views gain (category, subcategory) via a LEFT JOIN. No existing table altered.

CREATE TABLE IF NOT EXISTS rvbbit.entity_categories (
    entity_kind  text NOT NULL,            -- 'metric' | 'alert'
    entity_name  text NOT NULL,
    category     text,
    subcategory  text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_kind, entity_name),
    CONSTRAINT entity_categories_subcat_requires_cat
        CHECK (subcategory IS NULL OR category IS NOT NULL)
);

CREATE OR REPLACE FUNCTION rvbbit.set_category(
    p_kind text, p_name text, p_category text DEFAULT NULL, p_subcategory text DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql AS $fn$
DECLARE
    v_cat text := nullif(btrim(coalesce(p_category, '')), '');
    v_sub text := nullif(btrim(coalesce(p_subcategory, '')), '');
BEGIN
    IF p_kind NOT IN ('metric', 'alert') THEN
        RAISE EXCEPTION 'rvbbit.set_category: kind must be metric or alert (got %)', p_kind;
    END IF;
    IF v_sub IS NOT NULL AND v_cat IS NULL THEN
        RAISE EXCEPTION 'rvbbit.set_category: subcategory requires a category';
    END IF;
    IF v_cat IS NULL THEN
        DELETE FROM rvbbit.entity_categories WHERE entity_kind = p_kind AND entity_name = p_name;
        RETURN;
    END IF;
    INSERT INTO rvbbit.entity_categories (entity_kind, entity_name, category, subcategory, updated_at)
    VALUES (p_kind, p_name, v_cat, v_sub, now())
    ON CONFLICT (entity_kind, entity_name) DO UPDATE
       SET category = EXCLUDED.category, subcategory = EXCLUDED.subcategory, updated_at = now();
END;
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.category_options(p_kind text DEFAULT NULL)
RETURNS TABLE(category text, subcategory text)
LANGUAGE sql STABLE AS $fn$
    SELECT DISTINCT category, subcategory
    FROM rvbbit.entity_categories
    WHERE category IS NOT NULL
      AND (p_kind IS NULL OR entity_kind = p_kind)
    ORDER BY category, subcategory NULLS FIRST;
$fn$;

CREATE OR REPLACE VIEW rvbbit.metric_catalog AS
SELECT DISTINCT ON (m.name)
    m.name, m.version, m.sql, m.params, m.grain, m.description, m.owner, m.labels, m.check_sql, m.created_at,
    ec.category, ec.subcategory
FROM rvbbit.metric_defs m
LEFT JOIN rvbbit.entity_categories ec ON ec.entity_kind = 'metric' AND ec.entity_name = m.name
ORDER BY m.name, m.created_at DESC, m.version DESC;

CREATE OR REPLACE VIEW rvbbit.alert_catalog AS
SELECT DISTINCT ON (r.name)
    r.name, r.version, r.condition_spec, r.fire_policy, r.action_spec,
    r.cardinality, r.fan_out_cap, r.description, r.owner, r.labels, r.created_at,
    coalesce(c.enabled, true)                             AS enabled,
    c.muted_until,
    (c.muted_until IS NOT NULL AND c.muted_until > now()) AS muted,
    coalesce(c.cadence_tier, 'normal')                    AS cadence_tier,
    ec.category, ec.subcategory
FROM rvbbit.alert_rules r
LEFT JOIN rvbbit.alert_control c ON c.name = r.name
LEFT JOIN rvbbit.entity_categories ec ON ec.entity_kind = 'alert' AND ec.entity_name = r.name
ORDER BY r.name, r.created_at DESC, r.version DESC;

CREATE OR REPLACE FUNCTION rvbbit.set_default_embedder(
    backend_name text,
    purge_cache boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    normalized text := nullif(btrim(backend_name), '');
    src rvbbit.backends%ROWTYPE;
    purged bigint := 0;
    reloaded int := NULL;
    source_backend text;
    source_model text;
    source_manifest jsonb;
    default_meta jsonb;
    is_embedding_backend boolean;
BEGIN
    PERFORM rvbbit.require_mcp_gateway_admin();

    IF normalized IS NULL THEN
        RAISE EXCEPTION 'rvbbit.set_default_embedder: backend_name cannot be empty';
    END IF;

    SELECT * INTO src
    FROM rvbbit.backends
    WHERE name = normalized;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'rvbbit.set_default_embedder: backend "%" is not registered', normalized;
    END IF;

    is_embedding_backend :=
        src.transport IN ('local_embed', 'openai', 'stub')
        OR lower(coalesce(src.install_manifest #>> '{runtime,handler}', '')) = 'embedding'
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(coalesce(src.install_manifest, '{}'::jsonb)->'tags') = 'array'
                    THEN coalesce(src.install_manifest, '{}'::jsonb)->'tags'
                    ELSE '[]'::jsonb
                END
            ) AS tag(value)
            WHERE lower(tag.value) IN ('embedding', 'embeddings', 'retrieval')
        );

    IF NOT is_embedding_backend THEN
        RAISE EXCEPTION 'rvbbit.set_default_embedder: backend "%" uses transport "%" and is not marked as an embedding capability',
            normalized, src.transport;
    END IF;

    source_backend := CASE
        WHEN src.name = 'embed' THEN coalesce(nullif(src.install_manifest #>> '{rvbbit_default_embedder,source_backend}', ''), 'embed')
        ELSE src.name
    END;
    source_model := coalesce(src.source_model, src.transport_opts->>'model');
    default_meta := jsonb_build_object(
        'source_backend', source_backend,
        'source_model', source_model,
        'source_transport', src.transport,
        'set_at', clock_timestamp()
    );
    source_manifest := coalesce(src.install_manifest, '{}'::jsonb)
        || jsonb_build_object('rvbbit_default_embedder', default_meta);

    IF src.name <> 'embed' THEN
        INSERT INTO rvbbit.backends
            (name, transport, endpoint_url, batch_size, max_concurrent,
             timeout_ms, auth_header_env, transport_opts, description,
             source_provider, source_model, source_revision, install_manifest)
        VALUES
            ('embed', src.transport, src.endpoint_url, src.batch_size, src.max_concurrent,
             src.timeout_ms, src.auth_header_env, src.transport_opts,
             coalesce(src.description, 'Default embedding backend') || ' (default embedder alias for ' || src.name || ')',
             src.source_provider, src.source_model, src.source_revision, source_manifest)
        ON CONFLICT (name) DO UPDATE SET
            transport        = EXCLUDED.transport,
            endpoint_url     = EXCLUDED.endpoint_url,
            batch_size       = EXCLUDED.batch_size,
            max_concurrent   = EXCLUDED.max_concurrent,
            timeout_ms       = EXCLUDED.timeout_ms,
            auth_header_env  = EXCLUDED.auth_header_env,
            transport_opts   = EXCLUDED.transport_opts,
            description      = EXCLUDED.description,
            source_provider  = EXCLUDED.source_provider,
            source_model     = EXCLUDED.source_model,
            source_revision  = EXCLUDED.source_revision,
            install_manifest = EXCLUDED.install_manifest;
    ELSE
        UPDATE rvbbit.backends
        SET install_manifest = source_manifest
        WHERE name = 'embed';
    END IF;

    INSERT INTO rvbbit.settings (key, value, updated_at)
    VALUES (
        'default_embedder',
        jsonb_build_object(
            'backend', 'embed',
            'source_backend', source_backend,
            'source_model', source_model,
            'source_transport', src.transport
        ),
        clock_timestamp()
    )
    ON CONFLICT (key) DO UPDATE SET
        value = EXCLUDED.value,
        updated_at = clock_timestamp();

    IF purge_cache THEN
        SELECT rvbbit.embedding_purge('embed') INTO purged;
    END IF;

    BEGIN
        SELECT rvbbit.reload_backends() INTO reloaded;
    EXCEPTION WHEN undefined_function THEN
        reloaded := NULL;
    END;

    RETURN jsonb_build_object(
        'default_embedder', 'embed',
        'source_backend', source_backend,
        'source_model', source_model,
        'transport', src.transport,
        'endpoint_url', src.endpoint_url,
        'purged_entries', purged,
        'reloaded_backends', reloaded
    );
END
$$;
