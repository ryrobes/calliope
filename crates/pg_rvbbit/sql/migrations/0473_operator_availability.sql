CREATE OR REPLACE VIEW rvbbit.warren_backend_status AS
WITH ranked AS (
    SELECT
        d.*,
        row_number() OVER (
            PARTITION BY d.backend_name
            ORDER BY d.updated_at DESC, d.created_at DESC, d.deployment_id DESC
        ) AS rn
    FROM rvbbit.warren_deployments d
    WHERE d.backend_name IS NOT NULL
)
SELECT
    b.name,
    b.transport,
    b.endpoint_url,
    b.batch_size,
    b.max_concurrent,
    b.timeout_ms,
    b.auth_header_env,
    b.transport_opts,
    b.description,
    b.source_provider,
    b.source_model,
    b.source_revision,
    b.install_manifest,
    b.created_at,
    d.deployment_id,
    d.node_name,
    d.kind AS deployment_kind,
    d.name AS deployment_name,
    d.status AS deployment_status,
    CASE
        WHEN b.auth_header_env='RVBBIT_CLOVER_KEY' AND b.transport_opts #>> '{clover_route,enabled}' = 'false' THEN 'unconfigured'
        WHEN d.deployment_id IS NULL THEN 'external'
        WHEN d.status = 'running' THEN 'running'
        WHEN d.status IN ('starting', 'stopping') THEN d.status
        ELSE 'unavailable'
    END AS serving_status,
    ((d.deployment_id IS NULL OR d.status = 'running')
      AND NOT (b.auth_header_env IS NOT DISTINCT FROM 'RVBBIT_CLOVER_KEY'
        AND coalesce(b.transport_opts #>> '{clover_route,enabled}', 'true') = 'false')) AS callable,
    d.error AS deployment_error,
    d.health AS deployment_health,
    d.updated_at AS deployment_updated_at,
    d.stopped_at
FROM rvbbit.backends b
LEFT JOIN ranked d
  ON d.backend_name = b.name
 AND d.rn = 1;

-- The operator registry owns tests and declared dependencies; UIs only project them.
CREATE OR REPLACE VIEW rvbbit.operator_backend_dependencies AS
SELECT DISTINCT o.name AS operator,
 CASE WHEN step->>'kind'='embed' THEN coalesce(step->>'specialist','embed')
      WHEN step->>'kind'='llm' THEN step->>'provider'
      ELSE step->>'specialist' END AS backend
FROM rvbbit.operators o,
 LATERAL jsonb_path_query(coalesce(o.steps,'[]'::jsonb), '$.** ? (@.kind == "specialist" || @.kind == "llm" || @.kind == "embed")') step
WHERE coalesce(step->>'provider',step->>'specialist',CASE WHEN step->>'kind'='embed' THEN 'embed' END) IS NOT NULL;

CREATE OR REPLACE VIEW rvbbit.operator_availability AS
SELECT o.name AS operator, jsonb_array_length(coalesce(o.tests,'[]'::jsonb)) AS test_count,
 NOT EXISTS(SELECT 1 FROM rvbbit.operator_backend_dependencies d
 LEFT JOIN rvbbit.warren_backend_status b ON b.name=d.backend
 WHERE d.operator=o.name AND NOT coalesce(b.callable,false)) AS callable,
 coalesce((SELECT jsonb_agg(jsonb_build_object('backend',d.backend,
  'callable',coalesce(b.callable,false),'state',coalesce(b.serving_status,'not_registered'),
  'route',b.transport_opts->'clover_route','model',b.source_model) ORDER BY d.backend)
 FROM rvbbit.operator_backend_dependencies d LEFT JOIN rvbbit.warren_backend_status b ON b.name=d.backend
 WHERE d.operator=o.name),'[]'::jsonb) AS dependencies
FROM rvbbit.operators o;
