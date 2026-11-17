-- Request evidence is append-only; valuation may be enriched independently.
CREATE TABLE IF NOT EXISTS rvbbit.model_call_usage (
 call_id text PRIMARY KEY, source text NOT NULL, category text NOT NULL,
 operation text NOT NULL, backend text NOT NULL, model text NOT NULL,
 requested_model text, run_id text, session_id text, caller text,
 occurred_at timestamptz NOT NULL, status text NOT NULL,
 tokens_in bigint, tokens_out bigint, cache_read_tokens bigint, cache_write_tokens bigint,
 latency_ms double precision, cost_usd numeric, cost_kind text NOT NULL,
 pricing jsonb NOT NULL DEFAULT '{}', evidence jsonb NOT NULL DEFAULT '{}',
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS model_call_usage_run_idx ON rvbbit.model_call_usage(run_id);
CREATE INDEX IF NOT EXISTS model_call_usage_session_idx ON rvbbit.model_call_usage(session_id);
CREATE INDEX IF NOT EXISTS model_call_usage_time_idx ON rvbbit.model_call_usage(occurred_at);
CREATE TABLE IF NOT EXISTS rvbbit.model_usage_import_state (
 path text PRIMARY KEY, identity text NOT NULL, byte_offset bigint NOT NULL DEFAULT 0,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.model_price_snapshots (
 model text PRIMARY KEY, rates jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rvbbit.model_alias_snapshots (
 alias text NOT NULL, endpoint_host text NOT NULL, model text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(), evidence jsonb NOT NULL,
 PRIMARY KEY(alias,endpoint_host,observed_at)
);

-- Raw auxiliary receipts are evidence for native aggregate reconciliation, not
-- a second set of billed activities. Keep provider charges, including zero.
CREATE TABLE IF NOT EXISTS rvbbit.model_aux_receipts (
 event_id text PRIMARY KEY, session_id text NOT NULL, model text NOT NULL,
 provider text NOT NULL, endpoint_host text NOT NULL, task text NOT NULL,
 occurred_at timestamptz NOT NULL, usage jsonb NOT NULL,
 reported_cost_usd numeric, evidence jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS model_aux_receipts_group_idx ON rvbbit.model_aux_receipts
 (session_id,model,provider,endpoint_host,task);

CREATE OR REPLACE VIEW rvbbit.cost_activity AS
-- Canonical request-level imports (Hermes, direct topology, enriched Meaning).
SELECT m.call_id AS activity_id, m.source, m.category, m.operation, m.backend,
 m.model,m.requested_model,m.status,m.cost_kind,m.cost_usd,
 m.tokens_in,m.tokens_out,m.cache_read_tokens,m.cache_write_tokens,m.latency_ms,
 m.occurred_at,m.caller,m.run_id,m.session_id,
 m.call_id AS receipt_id,NULL::uuid AS query_id,coalesce((m.evidence->>'calls')::bigint,1::bigint) AS calls,
 m.pricing,m.evidence
FROM rvbbit.model_call_usage m
UNION ALL
-- Existing semantic, decision, title, tool and agent receipts remain authoritative.
SELECT 'semantic:'||c.cost_request_id,'semantic',
 CASE WHEN coalesce(r.operator,'') ILIKE '%meaning%' THEN 'Meaning'
      WHEN coalesce(r.operator,'') ILIKE '%topology%' THEN 'Business topology'
      WHEN c.source='hermes' THEN 'Calliope'
      WHEN coalesce(r.operator,'') ILIKE '%title%' THEN 'Titles & summaries'
      WHEN c.backend='decisions' THEN 'Decisions'
      WHEN c.source='mcp' THEN 'Tools'
      ELSE 'Semantic SQL' END,
 coalesce(r.operator,c.tool,c.source),coalesce(c.backend,'unknown'),
 CASE WHEN c.backend='decisions' AND r.operator='clover_decide' AND c.status='settled'
      THEN coalesce(r.output::jsonb->>'model',c.model,r.model,'unknown')
      ELSE coalesce(c.model,r.model,'unknown') END,
 NULL::text,c.status,
 CASE WHEN c.status IN ('pending','uncosted','error') THEN 'unknown'
      WHEN c.backend LIKE 'clover%' OR c.cost_source='subscription' THEN 'included'
      WHEN c.status='estimated' THEN 'estimated' ELSE 'reported' END,
 CASE WHEN c.status IN ('pending','uncosted','error') THEN NULL ELSE c.cost_usd END,
 c.tokens_in::bigint,c.tokens_out::bigint,c.cached_tokens::bigint,NULL::bigint,
 r.latency_ms::double precision,coalesce(r.invocation_at,c.created_at),c.caller,
 c.raw->>'run_id',c.raw->>'hermes_session_id',c.receipt_id::text,c.query_id,1::bigint,
 jsonb_build_object('source',c.cost_source),
 jsonb_build_object('cost_request_id',c.cost_request_id,'event_id',c.event_id)
FROM rvbbit.cost_latest c LEFT JOIN rvbbit.receipts r USING(receipt_id)
WHERE NOT (c.source='hermes' AND EXISTS (
 SELECT 1 FROM rvbbit.calliope_turns t JOIN rvbbit.model_call_usage m ON m.run_id=t.hermes_run_id
 WHERE t.id::text=c.raw->>'calliope_turn_id' AND m.source='hermes_ledger'
))
UNION ALL
-- Meaning's reservations are budget holds, never reported provider charges.
SELECT 'meaning:'||m.call_id,'meaning','Meaning','meaning.'||m.kind,'unknown',coalesce(m.model,'unknown'),
 m.model,CASE m.event WHEN 'reserved' THEN 'pending' WHEN 'failed' THEN 'error' ELSE 'estimated' END,
 CASE WHEN m.event IN ('reserved','failed') THEN 'reserved' ELSE 'estimated' END,
 m.cost_usd,m.tokens_in,m.tokens_out,NULL::bigint,NULL::bigint,NULL::double precision,
 m.created_at,NULL::text,NULL::text,NULL::text,m.call_id::text,NULL::uuid,1::bigint,
 jsonb_build_object('source',CASE WHEN m.event='legacy' THEN 'legacy_opening_balance' ELSE 'meaning_budget_ledger' END),
 jsonb_build_object('work_key',m.work_key,'event',m.event,'precision','legacy estimate; provider charge not distinguished')
FROM (SELECT DISTINCT ON(call_id) * FROM rvbbit.meaning_model_calls ORDER BY call_id,event_id DESC) m
WHERE NOT EXISTS (SELECT 1 FROM rvbbit.model_call_usage u WHERE u.call_id='meaning:'||m.call_id)
UNION ALL
-- Hermes already records auxiliary calls separately from the main agent loop.
-- Keep their original session/model/task aggregation visible (calls may exceed 1).
SELECT 'hermes_aux:'||md5(concat_ws('|',h.session_id,h.model,h.billing_provider,h.billing_base_url,h.billing_mode,h.task)),
 'hermes_aux','Hermes auxiliary',h.task,coalesce(nullif(h.billing_provider,''),'hermes'),h.model,NULL::text,
 CASE WHEN h.actual_cost_usd>0 THEN 'settled' WHEN h.estimated_cost_usd>0 THEN 'estimated' ELSE 'uncosted' END,
 CASE WHEN h.billing_mode ILIKE '%subscription%' OR h.billing_provider LIKE '%codex%' THEN 'included'
      WHEN h.actual_cost_usd>0 THEN 'reported' WHEN h.estimated_cost_usd>0 THEN 'estimated' ELSE 'unknown' END,
 CASE WHEN h.actual_cost_usd>0 THEN h.actual_cost_usd WHEN h.estimated_cost_usd>0 THEN h.estimated_cost_usd ELSE NULL END,
 h.input_tokens,h.output_tokens,h.cache_read_tokens,h.cache_write_tokens,NULL::double precision,
 h.last_seen,NULL::text,NULL::text,h.session_id,NULL::text,NULL::uuid,h.api_call_count,
 jsonb_build_object('source',h.cost_source),jsonb_build_object('aggregation','session_model_task','billing_mode',h.billing_mode)
FROM rvbbit.hermes_model_usage h WHERE h.task<>'' AND NOT EXISTS (
 SELECT 1 FROM rvbbit.model_call_usage m WHERE m.call_id='hermes_aux:'||md5(concat_ws('|',
 h.session_id,h.model,h.billing_provider,h.billing_base_url,h.billing_mode,h.task)));
COMMENT ON VIEW rvbbit.cost_activity IS 'Central cost activity: semantic receipts, request-level Hermes, Meaning attempts, direct topology and Hermes auxiliary totals. cost_kind separates reported provider cost, estimates, included value, reservations and unknowns. Source evidence retained; no prompts.';
