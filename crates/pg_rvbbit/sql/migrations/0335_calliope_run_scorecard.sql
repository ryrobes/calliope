-- 0335: per-run scorecard over the Hermes session mirror (0334).
--
-- One row per Calliope chat: which models the main agent and the specialists
-- ran on, calls, tokens, cache, peak context, latency, wall clock, output
-- cuts (finish_reason=length), compactions, truncated tool results,
-- specialist rounds and their tokens, and Hermes' own cost estimate — plus a
-- notes table for the human labels (dataset, model under test, quality) that
-- the mirror cannot know. Built for the 2026-08-18 model comparisons; every
-- number is derived, so a run needs only a label to appear in the sheet.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_run_notes (
    session_id      uuid PRIMARY KEY,
    label           text,
    dataset         text,
    model_under_test text,
    quality_score   numeric(4,1),
    notes           text,
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_by      text NOT NULL DEFAULT session_user,
    CONSTRAINT calliope_run_notes_quality_check CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 10))
);

-- Column set changes between revisions of this view; a plain CREATE OR REPLACE
-- cannot reorder or drop view columns, and the Calliope service re-applies the
-- scorecard files at boot, so recreate.
DROP VIEW IF EXISTS rvbbit.calliope_run_scorecard;
CREATE VIEW rvbbit.calliope_run_scorecard AS
WITH main_calls AS (
    SELECT c.session_id, c.store,
           count(*)::int                                     AS calls,
           sum(c.input_tokens)::bigint                       AS input_tokens,
           sum(c.output_tokens)::bigint                      AS output_tokens,
           sum(coalesce(c.cache_read_tokens,0))::bigint      AS cache_read_tokens,
           max(c.input_tokens)::int                          AS peak_input_tokens,
           round(avg(c.latency_ms))::int                     AS avg_latency_ms,
           min(c.ts)                                         AS first_call_at,
           max(c.ts)                                         AS last_call_at,
           string_agg(DISTINCT coalesce(c.route_provider, c.provider) || '/' || coalesce(c.route_model, c.model), ', ') AS models
    FROM rvbbit.hermes_api_calls c
    GROUP BY c.session_id, c.store
),
main_events AS (
    SELECT e.session_id, e.store,
           count(*) FILTER (WHERE e.kind = 'turn_started')::int          AS turns,
           count(*) FILTER (WHERE e.kind = 'compression_started')::int   AS compactions,
           count(*) FILTER (WHERE e.kind = 'tool_result_truncated')::int AS truncated_results,
           count(*) FILTER (WHERE e.kind = 'tool_completed')::int        AS tool_calls
    FROM rvbbit.hermes_context_events e
    GROUP BY e.session_id, e.store
),
main_cuts AS (
    SELECT m.session_id, m.store,
           count(*)::int AS output_cuts
    FROM rvbbit.hermes_messages m
    WHERE m.role = 'assistant' AND m.finish_reason = 'length' AND m.active
    GROUP BY m.session_id, m.store
),
main_cost AS (
    SELECT u.session_id, u.store,
           sum(coalesce(u.actual_cost_usd, u.estimated_cost_usd, 0))::numeric(24,6) AS cost_usd
    FROM rvbbit.hermes_model_usage u
    GROUP BY u.session_id, u.store
),
spec_runs AS (
    SELECT r.source_session_id AS session_id,
           count(*)::int                                     AS specialist_rounds,
           count(DISTINCT r.thread_id)::int                  AS specialist_threads,
           string_agg(DISTINCT r.hermes_session_id, ',')     AS hermes_ids
    FROM rvbbit.calliope_specialist_runs r
    GROUP BY r.source_session_id
),
spec_calls AS (
    SELECT r.source_session_id AS session_id,
           count(c.*)::int                                   AS specialist_calls,
           sum(c.input_tokens)::bigint                       AS specialist_input_tokens,
           sum(c.output_tokens)::bigint                      AS specialist_output_tokens,
           max(c.input_tokens)::int                          AS specialist_peak_input_tokens,
           min(c.ts)                                         AS specialist_first_call_at,
           max(c.ts)                                         AS specialist_last_call_at,
           string_agg(DISTINCT coalesce(c.route_provider, c.provider) || '/' || coalesce(c.route_model, c.model), ', ') AS specialist_models
    FROM rvbbit.calliope_specialist_runs r
    JOIN rvbbit.hermes_api_calls c ON c.session_id = r.hermes_session_id AND c.store <> 'default'
    GROUP BY r.source_session_id
),
spec_cost AS (
    SELECT r.source_session_id AS session_id,
           sum(coalesce(u.actual_cost_usd, u.estimated_cost_usd, 0))::numeric(24,6) AS specialist_cost_usd
    FROM rvbbit.calliope_specialist_runs r
    JOIN rvbbit.hermes_model_usage u ON u.session_id = r.hermes_session_id AND u.store <> 'default'
    GROUP BY r.source_session_id
),
artifacts AS (
    SELECT s.session_id,
           count(*) FILTER (WHERE s.kind = 'artifact')::int  AS artifact_surfaces,
           max(s.artifact_version)                            AS last_artifact_version,
           string_agg(DISTINCT s.artifact_slug, ', ') FILTER (WHERE s.artifact_slug IS NOT NULL) AS artifact_slugs
    FROM rvbbit.calliope_surfaces s
    GROUP BY s.session_id
)
SELECT
    cs.id                                   AS session_id,
    cs.hermes_session_id,
    cs.created_at                           AS started_at,
    n.label,
    n.dataset,
    n.model_under_test,
    n.quality_score,
    n.notes,
    left(cs.title, 80)                      AS title,
    mc.models                               AS main_models,
    sc.specialist_models,
    coalesce(me.turns, 0)                   AS turns,
    coalesce(mc.calls, 0)                   AS main_calls,
    coalesce(mc.input_tokens, 0)            AS main_input_tokens,
    coalesce(mc.output_tokens, 0)           AS main_output_tokens,
    coalesce(mc.cache_read_tokens, 0)       AS main_cache_read_tokens,
    CASE WHEN coalesce(mc.input_tokens,0) > 0
         THEN round(100.0 * mc.cache_read_tokens / mc.input_tokens, 1) END AS main_cache_pct,
    mc.peak_input_tokens                    AS main_peak_input_tokens,
    mc.avg_latency_ms                       AS main_avg_latency_ms,
    coalesce(cuts.output_cuts, 0)           AS output_cuts,
    coalesce(me.compactions, 0)             AS compactions,
    coalesce(me.truncated_results, 0)       AS truncated_results,
    coalesce(me.tool_calls, 0)              AS main_tool_calls,
    coalesce(sr.specialist_rounds, 0)       AS specialist_rounds,
    coalesce(sr.specialist_threads, 0)      AS specialist_threads,
    coalesce(sc.specialist_calls, 0)        AS specialist_calls,
    coalesce(sc.specialist_input_tokens, 0) AS specialist_input_tokens,
    coalesce(sc.specialist_output_tokens, 0) AS specialist_output_tokens,
    sc.specialist_peak_input_tokens,
    coalesce(mc.input_tokens,0) + coalesce(sc.specialist_input_tokens,0)   AS total_input_tokens,
    coalesce(mc.output_tokens,0) + coalesce(sc.specialist_output_tokens,0) AS total_output_tokens,
    coalesce(mco.cost_usd,0) + coalesce(spc.specialist_cost_usd,0)         AS hermes_est_cost_usd,
    least(mc.first_call_at, sc.specialist_first_call_at)                   AS first_call_at,
    greatest(mc.last_call_at, sc.specialist_last_call_at)                  AS last_call_at,
    round(extract(epoch FROM greatest(mc.last_call_at, sc.specialist_last_call_at)
                             - least(mc.first_call_at, sc.specialist_first_call_at)))::int AS wall_seconds,
    coalesce(a.artifact_surfaces, 0)        AS artifact_surfaces,
    a.last_artifact_version,
    a.artifact_slugs
FROM rvbbit.calliope_sessions cs
LEFT JOIN rvbbit.calliope_run_notes n   ON n.session_id = cs.id
LEFT JOIN main_calls mc                 ON mc.session_id = cs.hermes_session_id AND mc.store = 'default'
LEFT JOIN main_events me                ON me.session_id = cs.hermes_session_id AND me.store = 'default'
LEFT JOIN main_cuts cuts                ON cuts.session_id = cs.hermes_session_id AND cuts.store = 'default'
LEFT JOIN main_cost mco                 ON mco.session_id = cs.hermes_session_id AND mco.store = 'default'
LEFT JOIN spec_runs sr                  ON sr.session_id = cs.id
LEFT JOIN spec_calls sc                 ON sc.session_id = cs.id
LEFT JOIN spec_cost spc                 ON spc.session_id = cs.id
LEFT JOIN artifacts a                   ON a.session_id = cs.id;

COMMENT ON VIEW rvbbit.calliope_run_scorecard IS
  'One row per Calliope chat: models, calls, tokens, cache, peak context, latency, wall clock, output cuts, compactions, specialist rounds/tokens, Hermes cost estimate, artifacts — derived from the Hermes session mirror (0334); labels come from calliope_run_notes.';
COMMENT ON TABLE rvbbit.calliope_run_notes IS
  'Human labels for calliope_run_scorecard rows: dataset, model under test, quality score, notes.';
