-- Decision (Jev) calls carry their real provider charge in the response's
-- usage block ({"cost": usd, "input_tokens", "output_tokens"}), but the
-- specialist step recorded them as uncosted (tokens 0, no cost) because the
-- sub-call never copied that block. Two repairs, both pure SQL so they work
-- on an installed appliance without an extension rebuild:
--   1. settle_decision_costs(): append a 'settled'/'inline' cost event for
--      every uncosted decisions event whose receipt output carries usage.
--      Append-only, same pattern as the OpenRouter settlement: cost_latest
--      picks the newest event per cost_request_id.
--   2. decision_costs / decision_costs_daily: the easy-to-read ledger.
-- The extension fix (usage → sub-call at call time) makes (1) a no-op later.
-- (Numbered 0436: 0435 was already taken by query_receipt_replay.)

CREATE OR REPLACE FUNCTION rvbbit.settle_decision_costs(p_limit integer DEFAULT 5000)
RETURNS integer
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE v_n integer;
BEGIN
    WITH latest AS (
        SELECT c.* FROM rvbbit.cost_latest c
         WHERE c.backend = 'decisions' AND c.status = 'uncosted'
         ORDER BY c.event_id LIMIT p_limit
    ), settled AS (
        INSERT INTO rvbbit.cost_events
            (cost_request_id, query_id, receipt_id, sub_call_index, source, backend, transport,
             model, tool, status, cost_source, tokens_in, tokens_out, cost_usd, currency, raw, caller)
        SELECT l.cost_request_id, l.query_id, l.receipt_id, l.sub_call_index, l.source, l.backend,
               l.transport, l.model, l.tool, 'settled', 'inline',
               nullif(r.output::jsonb #>> '{usage,input_tokens}', '')::integer,
               nullif(r.output::jsonb #>> '{usage,output_tokens}', '')::integer,
               (r.output::jsonb #>> '{usage,cost}')::numeric,
               'USD',
               jsonb_build_object('settled_by', 'settle_decision_costs',
                                  'usage', r.output::jsonb -> 'usage',
                                  'model', r.output::jsonb ->> 'model',
                                  'supersedes_event_id', l.event_id),
               l.caller
          FROM latest l
          JOIN rvbbit.receipts r ON r.receipt_id = l.receipt_id
         WHERE r.output IS NOT NULL
           AND (r.output::jsonb #>> '{usage,cost}') ~ '^[0-9.eE+-]+$'
        RETURNING 1
    )
    SELECT count(*) INTO v_n FROM settled;
    RETURN v_n;
END
$fn$;
COMMENT ON FUNCTION rvbbit.settle_decision_costs(integer) IS
'Settle uncosted Clover decision cost events from the usage block stored in their receipts (append-only). Safe to run on a schedule; returns the number of events settled.';

-- One row per uncached decision call: what was asked, what it cost.
CREATE OR REPLACE VIEW rvbbit.decision_costs AS
SELECT r.receipt_id,
       r.invocation_at,
       r.caller,
       r.query_id,
       c.status,
       c.cost_source,
       c.tokens_in,
       c.tokens_out,
       c.cost_usd,
       r.latency_ms,
       r.output::jsonb ->> 'model'                                   AS model,
       -- questions is a name→question object; a malformed call (array,
       -- string) is still a receipt, so the view must not choke on it.
       CASE WHEN jsonb_typeof(r.inputs -> 'questions') = 'object'
            THEN (SELECT jsonb_object_agg(q.key, q.value ->> 'type')
                    FROM jsonb_each(r.inputs -> 'questions') q) END  AS question_kinds,
       CASE jsonb_typeof(r.inputs -> 'questions')
            WHEN 'object' THEN (SELECT count(*) FROM jsonb_each(r.inputs -> 'questions'))
            WHEN 'array'  THEN jsonb_array_length(r.inputs -> 'questions')
            ELSE 0 END                                               AS question_count,
       left(r.inputs ->> 'state', 160)                               AS state_head,
       r.output::jsonb -> 'answers'                                  AS answers
  FROM rvbbit.receipts r
  LEFT JOIN rvbbit.cost_latest c ON c.receipt_id = r.receipt_id AND c.backend = 'decisions'
 WHERE r.operator = 'clover_decide';
COMMENT ON VIEW rvbbit.decision_costs IS
'Every uncached Clover decision (Jev) call with its settled cost, tokens, question kinds and answers. Cached decisions do not appear: they cost nothing and produce no receipt.';

CREATE OR REPLACE VIEW rvbbit.decision_costs_daily AS
SELECT date_trunc('day', invocation_at)::date          AS day,
       coalesce(caller, '(unattributed)')               AS caller,
       count(*)                                         AS calls,
       count(*) FILTER (WHERE status = 'settled')       AS settled,
       count(*) FILTER (WHERE status IS DISTINCT FROM 'settled') AS unsettled,
       sum(question_count)                              AS questions,
       sum(tokens_in)                                   AS tokens_in,
       sum(tokens_out)                                  AS tokens_out,
       round(sum(cost_usd), 6)                          AS cost_usd,
       round(avg(latency_ms))                           AS avg_latency_ms
  FROM rvbbit.decision_costs
 GROUP BY 1, 2;
COMMENT ON VIEW rvbbit.decision_costs_daily IS
'Daily Clover decision spend per caller: calls, questions, tokens, USD, latency. Unsettled > 0 means settle_decision_costs() has not run since those calls.';

-- Settle whatever this install already has.
SELECT rvbbit.settle_decision_costs();
