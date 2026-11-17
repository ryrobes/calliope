-- 0318: the managed Clover endpoint exposes two intentional service routes.
-- `clover` remains the inexpensive canonical/default route; `calliope` is the
-- stronger hosted-agent route. Keep arbitrary upstream model selection closed,
-- but allow these explicit service ids to pass through the pg_rvbbit transport.

UPDATE rvbbit.backends
SET transport_opts = coalesce(transport_opts, '{}'::jsonb) || jsonb_build_object(
        'model', 'clover',
        'model_policy', 'managed',
        'model_aliases', jsonb_build_array('gemma4'),
        'model_allowlist', jsonb_build_array('clover', 'calliope'),
        'model_prefix_allowlist', jsonb_build_array('clover*', 'calliope*')
    )
WHERE name = 'clover_llm'
  AND (
      install_manifest->>'capability' = 'managed/clover'
      OR endpoint_url LIKE '%clover.rvbb.it%/v1/chat/completions'
  );

-- Expose each explicitly allowed managed route to model pickers. Legacy
-- aliases such as gemma4 intentionally remain hidden because they normalize to
-- the canonical service rather than naming a separately selectable route.
CREATE OR REPLACE FUNCTION rvbbit.sync_managed_provider_models() RETURNS integer
LANGUAGE sql
AS $fn$
WITH managed_backends AS (
    SELECT b.name AS provider,
           coalesce(nullif(b.transport_opts->>'model', ''), b.source_model) AS canonical_model,
           b.source_model,
           b.transport_opts
    FROM rvbbit.backends b
    WHERE b.install_manifest IS NOT NULL
      AND b.transport IN ('openai_chat', 'anthropic', 'gemini')
      AND coalesce(nullif(b.transport_opts->>'model', ''), b.source_model) IS NOT NULL
), managed AS (
    SELECT provider, canonical_model AS model, source_model
    FROM managed_backends
    UNION
    SELECT b.provider, allowed.model, b.source_model
    FROM managed_backends b
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN jsonb_typeof(b.transport_opts->'model_allowlist') = 'array'
            THEN b.transport_opts->'model_allowlist'
            ELSE '[]'::jsonb
        END
    ) AS allowed(model)
    WHERE btrim(allowed.model) <> ''
), up AS (
    INSERT INTO rvbbit.provider_models (provider, model, display_name, family, source, available)
    SELECT m.provider, m.model,
           CASE WHEN m.model = b.canonical_model THEN coalesce(b.source_model, m.model) ELSE m.model END,
           split_part(m.model, '/', 1),
           'capability', true
    FROM managed m
    JOIN managed_backends b USING (provider)
    ON CONFLICT (provider, model) DO UPDATE SET
        available = true,
        source = 'capability',
        display_name = EXCLUDED.display_name,
        updated_at = clock_timestamp()
    RETURNING 1
), pruned AS (
    DELETE FROM rvbbit.provider_models pm
    WHERE pm.source = 'capability'
      AND NOT EXISTS (
          SELECT 1 FROM managed m
          WHERE m.provider = pm.provider AND m.model = pm.model
      )
    RETURNING 1
)
SELECT coalesce((SELECT count(*) FROM up), 0)::int
     + coalesce((SELECT count(*) FROM pruned), 0)::int;
$fn$;

COMMENT ON FUNCTION rvbbit.sync_managed_provider_models() IS
    'Mirror canonical and explicitly allowlisted capability-managed LLM routes into rvbbit.provider_models; compatibility aliases remain hidden.';

SELECT rvbbit.sync_managed_provider_models();

-- The artifact compiler needs the stronger hosted-agent route. Rewrite only
-- its named agent node, preserving the prompt, tools, budgets, and any future
-- sibling steps already present on an upgraded installation.
WITH rewritten AS (
    SELECT o.name,
           jsonb_agg(
               CASE
                   WHEN node->>'name' = 'enricher'
                    AND node->>'kind' = 'agent'
                   THEN jsonb_set(
                       jsonb_set(node, '{provider}', to_jsonb('clover_llm'::text), true),
                       '{model}', to_jsonb('calliope'::text), true
                   )
                   ELSE node
               END
               ORDER BY ord
           ) AS steps
    FROM rvbbit.operators o
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(o.steps) = 'array' THEN o.steps ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS n(node, ord)
    WHERE o.name = 'artifact_semantic_enrich'
    GROUP BY o.name
)
UPDATE rvbbit.operators o
SET model = 'calliope',
    steps = rewritten.steps,
    updated_at = clock_timestamp()
FROM rewritten
WHERE o.name = rewritten.name
  AND (o.model IS DISTINCT FROM 'calliope' OR o.steps IS DISTINCT FROM rewritten.steps);

SELECT rvbbit.reload_backends();
