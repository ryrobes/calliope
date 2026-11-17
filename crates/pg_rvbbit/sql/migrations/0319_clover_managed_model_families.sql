-- 0319: reserve the Clover and Calliope service-name families for managed
-- Hutch routes. This permits future route ids such as calliope-low or
-- clover-bulk-v2 without allowing arbitrary provider/model ids through the
-- capability-owned clover_llm backend.

UPDATE rvbbit.backends
SET transport_opts = coalesce(transport_opts, '{}'::jsonb) || jsonb_build_object(
        'model_prefix_allowlist', jsonb_build_array('clover*', 'calliope*')
    )
WHERE name = 'clover_llm'
  AND (
      install_manifest->>'capability' = 'managed/clover'
      OR endpoint_url LIKE '%clover.rvbb.it%/v1/chat/completions'
  );

SELECT rvbbit.reload_backends();
