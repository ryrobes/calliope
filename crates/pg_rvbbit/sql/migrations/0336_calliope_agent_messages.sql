-- 0336: agent-to-agent mailbox.
--
-- Until now every specialist exchange was a synchronous tool call: Calliope's
-- turn held an MCP call, which held a Hermes run, which held an HTTP stream,
-- for as long as Iris worked (hence the 2100 s roundtable lane and the
-- "Callie rebuilt what Iris was building" timeouts). This table makes waiting a
-- *state* instead of a *stack*: a specialist round can be delegated with
-- delivery='mailbox', the parent turn ends, and when the round finishes its
-- reply is queued here and delivered to the parent session as an
-- agent-originated turn (turn_kind='agent_message') by the Calliope service.
-- The same shape carries a specialist's mid-work question back to Calliope,
-- and later any ambient specialist's unsolicited note ("while you were away…").
--
-- The message must carry enough of its own context that a compacted parent
-- session can act on it without the transcript; that is why subject/body are
-- text the model reads, and payload holds the structured receipt.

ALTER TABLE rvbbit.calliope_specialist_runs
    ADD COLUMN IF NOT EXISTS delivery text NOT NULL DEFAULT 'sync';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'calliope_specialist_runs_delivery_check'
    ) THEN
        ALTER TABLE rvbbit.calliope_specialist_runs
            ADD CONSTRAINT calliope_specialist_runs_delivery_check
            CHECK (delivery IN ('sync', 'mailbox'));
    END IF;
END $$;
COMMENT ON COLUMN rvbbit.calliope_specialist_runs.delivery IS
  'sync: the delegating tool call blocks until the round ends and returns the result; mailbox: the call returns at once and the result is queued in calliope_agent_messages for delivery to the source session as an agent_message turn.';

CREATE TABLE IF NOT EXISTS rvbbit.calliope_agent_messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_email     text NOT NULL,
    kind            text NOT NULL,
    from_kind       text NOT NULL,
    from_ref        text,
    from_label      text,
    to_session_id   uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    thread_id       uuid REFERENCES rvbbit.calliope_specialist_threads(id) ON DELETE SET NULL,
    run_id          uuid REFERENCES rvbbit.calliope_specialist_runs(id) ON DELETE SET NULL,
    in_reply_to     uuid REFERENCES rvbbit.calliope_agent_messages(id) ON DELETE SET NULL,
    subject         text,
    body            text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'queued',
    not_before      timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts        integer NOT NULL DEFAULT 0,
    last_error      text,
    delivered_turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    delivered_at    timestamptz,
    CONSTRAINT calliope_agent_messages_kind_check
        CHECK (kind IN ('reply', 'question', 'progress', 'note', 'failed', 'cancel')),
    CONSTRAINT calliope_agent_messages_from_kind_check
        CHECK (from_kind IN ('specialist', 'session', 'system')),
    CONSTRAINT calliope_agent_messages_status_check
        CHECK (status IN ('queued', 'delivering', 'delivered', 'failed', 'cancelled')),
    CONSTRAINT calliope_agent_messages_recipient_check
        CHECK (to_session_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS calliope_agent_messages_queue_idx
    ON rvbbit.calliope_agent_messages (not_before, created_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS calliope_agent_messages_session_idx
    ON rvbbit.calliope_agent_messages (to_session_id, created_at);
CREATE INDEX IF NOT EXISTS calliope_agent_messages_thread_idx
    ON rvbbit.calliope_agent_messages (thread_id, created_at)
    WHERE thread_id IS NOT NULL;

COMMENT ON TABLE rvbbit.calliope_agent_messages IS
  'Agent-to-agent mailbox: replies, questions and notes addressed to a Calliope session, delivered by the Calliope service as agent_message turns when the session is idle. Slice 1 (2026-08-18) recipients are sessions only; specialists are addressed through their thread (continue_calliope_specialist).';

-- Scorecard: agent-originated turns and mailbox traffic per run.
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
calliope_turns AS (
    SELECT t.session_id,
           count(*) FILTER (WHERE t.turn_kind = 'agent_message')::int AS agent_turns,
           count(*) FILTER (WHERE t.turn_kind IN ('chat','setup_conductor'))::int AS human_turns
    FROM rvbbit.calliope_turns t
    GROUP BY t.session_id
),
mailbox AS (
    SELECT m.to_session_id AS session_id,
           count(*)::int AS agent_messages,
           count(*) FILTER (WHERE m.kind = 'question')::int AS agent_questions
    FROM rvbbit.calliope_agent_messages m
    GROUP BY m.to_session_id
),
spec_runs AS (
    SELECT r.source_session_id AS session_id,
           count(*)::int                                     AS specialist_rounds,
           count(*) FILTER (WHERE r.delivery = 'mailbox')::int AS async_specialist_rounds,
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
    coalesce(ct.human_turns, 0)             AS human_turns,
    coalesce(ct.agent_turns, 0)             AS agent_turns,
    coalesce(mb.agent_messages, 0)          AS agent_messages,
    coalesce(mb.agent_questions, 0)         AS agent_questions,
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
    coalesce(sr.async_specialist_rounds, 0) AS async_specialist_rounds,
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
LEFT JOIN calliope_turns ct             ON ct.session_id = cs.id
LEFT JOIN mailbox mb                    ON mb.session_id = cs.id
LEFT JOIN spec_runs sr                  ON sr.session_id = cs.id
LEFT JOIN spec_calls sc                 ON sc.session_id = cs.id
LEFT JOIN spec_cost spc                 ON spc.session_id = cs.id
LEFT JOIN artifacts a                   ON a.session_id = cs.id;

COMMENT ON VIEW rvbbit.calliope_run_scorecard IS
  'One row per Calliope chat: models, calls, tokens, cache, peak context, latency, wall clock, output cuts, compactions, human vs agent-originated turns, mailbox traffic, specialist rounds (sync/async) and tokens, Hermes cost estimate, artifacts — derived from the Hermes session mirror (0334); labels come from calliope_run_notes.';
