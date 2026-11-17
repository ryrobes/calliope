-- 0424: one row per harvest, and what changed in the crops.
--
-- A nightly sweep already has a unified id (business_topology_sweeps →
-- sweep_batches → workflow_runs via parameters->>'sweep_id' → graphs via
-- workflow_run_id), but the spend lived only in per-call receipt files on
-- disk. business_topology_jobs now rolls each run's receipts into
-- result.usage (progress.usage for failed runs); these views sum them per
-- sweep and classify the graphs a sweep staged against what existed before.
--
--   SELECT * FROM rvbbit.business_topology_sweep_summary ORDER BY requested_at DESC;
--   SELECT * FROM rvbbit.business_topology_crop_diff();          -- latest sweep
--   SELECT * FROM rvbbit.business_topology_crop_diff('<sweep>');
--
-- Reading the diff: a later sweep only digs relations whose fingerprint
-- changed or that are new (0421 relation marks), so graphs from earlier
-- sweeps are carried, not re-staged — "gone" is not a state here.

CREATE OR REPLACE VIEW rvbbit.business_topology_sweep_runs AS
SELECT (r.parameters->>'sweep_id')::uuid AS sweep_id,
       r.run_id, r.run_name, r.status, r.attempts, r.started_at, r.finished_at,
       coalesce(r.result->'usage', r.progress->'usage') AS usage,
       r.progress->'value_domain_plan_verdict'->>'status' AS verdict,
       coalesce((r.result->>'llm_attempts')::integer, (r.progress->>'llm_attempts')::integer, 0) AS llm_attempts,
       coalesce((r.result->>'semantic_cache_hits')::integer, (r.progress->>'semantic_cache_hits')::integer, 0) AS semantic_cache_hits,
       coalesce(r.result->'model_routes'->'judgment'->>'model', r.progress->'model_routes'->'judgment'->>'model') AS judgment_model,
       r.error
  FROM rvbbit.business_topology_workflow_runs r
 WHERE r.parameters ? 'sweep_id';

COMMENT ON VIEW rvbbit.business_topology_sweep_runs IS
'Workflow runs that belong to a nightly sweep, with their usage rollup, verdict and judge.';

CREATE OR REPLACE VIEW rvbbit.business_topology_sweep_summary AS
WITH graphs AS (
    SELECT sr.sweep_id,
           count(*) AS graphs_staged,
           count(*) FILTER (WHERE g.single_table) AS graphs_single_table,
           count(*) FILTER (WHERE g.status = 'superseded') AS graphs_superseded
      FROM rvbbit.business_value_graph_generations g
      JOIN rvbbit.business_topology_sweep_runs sr ON sr.run_id = g.workflow_run_id
     GROUP BY sr.sweep_id
)
SELECT s.sweep_id, s.status, s.batch_size, s.requested_by, s.requested_at, s.finished_at,
       round(extract(epoch FROM coalesce(s.finished_at, now()) - s.requested_at) / 3600.0, 2) AS hours,
       (SELECT count(*) FROM rvbbit.business_topology_sweep_batches b WHERE b.sweep_id = s.sweep_id) AS batches,
       count(r.run_id) FILTER (WHERE r.status = 'completed') AS runs_completed,
       count(r.run_id) FILTER (WHERE r.status = 'failed') AS runs_failed,
       count(r.run_id) FILTER (WHERE r.status IN ('queued', 'running', 'deferred')) AS runs_open,
       count(r.run_id) FILTER (WHERE r.verdict = 'coherent') AS coherent,
       count(r.run_id) FILTER (WHERE r.verdict = 'partial') AS partial,
       count(r.run_id) FILTER (WHERE r.verdict = 'no_coherent_system') AS declined,
       coalesce(g.graphs_staged, 0) AS graphs_staged,
       coalesce(g.graphs_single_table, 0) AS graphs_single_table,
       coalesce(g.graphs_superseded, 0) AS graphs_superseded,
       coalesce(sum(r.llm_attempts), 0) AS llm_calls,
       coalesce(sum(r.semantic_cache_hits), 0) AS semantic_cache_hits,
       coalesce(sum((r.usage->>'prompt_tokens')::bigint), 0) AS prompt_tokens,
       coalesce(sum((r.usage->>'completion_tokens')::bigint), 0) AS completion_tokens,
       round(coalesce(sum((r.usage->>'cost_usd')::numeric), 0), 2) AS cost_usd,
       count(r.run_id) FILTER (WHERE r.usage IS NULL AND r.status IN ('completed', 'failed')) AS runs_without_usage,
       (SELECT string_agg(m || ' ×' || n, ', ' ORDER BY n DESC)
          FROM (SELECT coalesce(judgment_model, '?') AS m, count(*) AS n
                  FROM rvbbit.business_topology_sweep_runs x
                 WHERE x.sweep_id = s.sweep_id AND x.status = 'completed'
                 GROUP BY 1) j) AS judges
  FROM rvbbit.business_topology_sweeps s
  LEFT JOIN rvbbit.business_topology_sweep_runs r ON r.sweep_id = s.sweep_id
  LEFT JOIN graphs g ON g.sweep_id = s.sweep_id
 GROUP BY s.sweep_id, s.status, s.batch_size, s.requested_by, s.requested_at, s.finished_at,
          g.graphs_staged, g.graphs_single_table, g.graphs_superseded;

COMMENT ON VIEW rvbbit.business_topology_sweep_summary IS
'One row per nightly harvest: batches, outcomes, verdicts, graphs staged, LLM calls, cache hits, tokens and cost (from result.usage rollups), and which judges ran.';

CREATE OR REPLACE FUNCTION rvbbit.business_topology_crop_diff(p_sweep uuid DEFAULT NULL)
RETURNS TABLE (
    change text,
    name text,
    domain_key text,
    schema_name text,
    relation_count integer,
    status text,
    prior_graph_id uuid,
    prior_proposed_at timestamptz,
    graph_id uuid,
    workflow_run_id uuid,
    detail text
)
LANGUAGE sql
STABLE
AS $$
WITH target AS (
    SELECT s.sweep_id, s.requested_at
      FROM rvbbit.business_topology_sweeps s
     WHERE s.sweep_id = coalesce(
               p_sweep,
               (SELECT sweep_id FROM rvbbit.business_topology_sweeps ORDER BY requested_at DESC LIMIT 1))
),
staged AS (
    SELECT d.name, d.domain_key, d.schema_name, d.relation_count, d.status,
           d.source_relations, d.graph_id, d.workflow_run_id, d.proposed_at
      FROM rvbbit.business_value_graph_digest d
      JOIN rvbbit.business_topology_sweep_runs sr ON sr.run_id = d.workflow_run_id
      JOIN target t ON t.sweep_id = sr.sweep_id
),
prior AS (
    SELECT d.domain_key, d.graph_id, d.proposed_at, d.source_relations,
           row_number() OVER (PARTITION BY d.domain_key ORDER BY d.proposed_at DESC) AS rn
      FROM rvbbit.business_value_graph_digest d
      JOIN target t ON d.proposed_at < t.requested_at
)
SELECT CASE
           WHEN p.graph_id IS NULL THEN 'new'
           WHEN coalesce(
                    (SELECT array_agg(x ORDER BY x) FROM unnest(s.source_relations) x),
                    ARRAY[]::text[]) =
                coalesce(
                    (SELECT array_agg(x ORDER BY x) FROM unnest(p.source_relations) x),
                    ARRAY[]::text[]) THEN 'unchanged'
           ELSE 'revised'
       END AS change,
       s.name, s.domain_key, s.schema_name, s.relation_count, s.status,
       p.graph_id AS prior_graph_id, p.proposed_at AS prior_proposed_at,
       s.graph_id, s.workflow_run_id,
       CASE WHEN p.graph_id IS NULL THEN NULL
            ELSE 'prior ' || coalesce(array_length(p.source_relations, 1), 0) || ' relations → now ' || s.relation_count
       END AS detail
  FROM staged s
  LEFT JOIN prior p ON p.domain_key = s.domain_key AND p.rn = 1
UNION ALL
SELECT 'declined', sr.run_name, NULL, b.schema_name,
       coalesce(jsonb_array_length(r.parameters->'relations'), 0), sr.status,
       NULL, NULL, NULL, sr.run_id,
       left(r.progress->'value_domain_plan_verdict'->>'reason', 240)
  FROM rvbbit.business_topology_sweep_runs sr
  JOIN target t ON t.sweep_id = sr.sweep_id
  JOIN rvbbit.business_topology_workflow_runs r ON r.run_id = sr.run_id
  LEFT JOIN rvbbit.business_topology_sweep_batches b ON b.run_id = sr.run_id
 WHERE sr.verdict = 'no_coherent_system'
 ORDER BY 1, 4, 2;
$$;

COMMENT ON FUNCTION rvbbit.business_topology_crop_diff(uuid) IS
'Graphs a sweep staged, classified new / revised / unchanged against the newest earlier graph with the same domain key, plus the batches it declined as "nothing here". Defaults to the latest sweep.';
