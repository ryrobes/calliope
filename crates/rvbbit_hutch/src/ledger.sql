-- Metadata only. Install as database administrator, never on the inference path.
-- The administrator pre-creates this schema and grants scoped runtime access.
REVOKE ALL ON SCHEMA clover_telemetry FROM PUBLIC;
CREATE TABLE IF NOT EXISTS clover_telemetry.invocations (
  event_id text PRIMARY KEY, instance_id text NOT NULL, ts timestamptz NOT NULL,
  invocation_id text NOT NULL, tenant text NOT NULL, backend text NOT NULL,
  stream boolean NOT NULL DEFAULT false, n_inputs bigint NOT NULL, status integer NOT NULL,
  error_code text, duration_ms double precision NOT NULL, upstream_ms double precision,
  model_version text NOT NULL, reference_cost_nanousd bigint NOT NULL DEFAULT 0,
  prompt_tokens bigint, completion_tokens bigint,
  cost_basis text NOT NULL DEFAULT 'reference_tariff'
);
CREATE INDEX IF NOT EXISTS invocations_instance_time ON clover_telemetry.invocations(instance_id, ts);
CREATE INDEX IF NOT EXISTS invocations_correlation ON clover_telemetry.invocations(instance_id, invocation_id);
CREATE TABLE IF NOT EXISTS clover_telemetry.upstream_attempts (
  id bigserial PRIMARY KEY, instance_id text NOT NULL, ts timestamptz NOT NULL,
  invocation_id text NOT NULL, tenant text NOT NULL, canonical_model text NOT NULL,
  provider text NOT NULL, upstream_model text NOT NULL, provider_request_id text,
  attempt_no integer NOT NULL, mode text NOT NULL, status integer NOT NULL,
  prompt_tokens bigint, completion_tokens bigint, provider_cost_nanousd bigint,
  cost_source text NOT NULL, cost_status text NOT NULL,
  reconcile_attempts integer NOT NULL DEFAULT 0, next_reconcile_unix bigint NOT NULL DEFAULT 0,
  reconcile_error text, reconciled_at timestamptz, last_defer_id text,
  UNIQUE(instance_id, invocation_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS attempts_pending ON clover_telemetry.upstream_attempts(instance_id, next_reconcile_unix) WHERE cost_status='pending';
CREATE TABLE IF NOT EXISTS clover_telemetry.provider_price_snapshots (
  event_id text PRIMARY KEY, instance_id text NOT NULL, captured_at timestamptz NOT NULL,
  provider text NOT NULL, upstream_model text NOT NULL,
  prompt_usd_per_token double precision NOT NULL, completion_usd_per_token double precision NOT NULL, source text NOT NULL
);
CREATE INDEX IF NOT EXISTS latest_prices ON clover_telemetry.provider_price_snapshots(instance_id, provider, upstream_model, captured_at DESC);
CREATE TABLE IF NOT EXISTS clover_telemetry.speech_usage (
  instance_id text NOT NULL, invocation_id text NOT NULL, input_characters bigint NOT NULL,
  output_bytes bigint NOT NULL, audio_seconds double precision, first_audio_ms double precision,
  profile_revision text NOT NULL, PRIMARY KEY(instance_id, invocation_id)
);
CREATE TABLE IF NOT EXISTS clover_telemetry.media_usage (
  instance_id text NOT NULL, invocation_id text NOT NULL, capability text NOT NULL,
  input_bytes bigint NOT NULL, output_bytes bigint NOT NULL, usage_json jsonb NOT NULL,
  PRIMARY KEY(instance_id, invocation_id)
);
CREATE TABLE IF NOT EXISTS clover_telemetry.writers (
  writer_id text PRIMARY KEY, instance_id text NOT NULL, seen_at timestamptz NOT NULL DEFAULT now(),
  status jsonb NOT NULL
);
-- One transaction / round trip per bounded batch. Stable IDs make ambiguous
-- commits safe to retry; imports never regress an already reconciled charge.
CREATE OR REPLACE FUNCTION clover_telemetry.ingest(batch jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,clover_telemetry AS $$
DECLARE e jsonb; d jsonb; instance text; eid text; stamp timestamptz;
BEGIN
  FOR e IN SELECT value FROM jsonb_array_elements(batch) LOOP
    d := e->'data'; instance := e->>'instance_id'; eid := e->>'event_id';
    stamp := COALESCE((e->>'ts')::timestamptz, to_timestamp((e->>'unix_ms')::double precision / 1000));
    CASE e->>'kind'
    WHEN 'invocation' THEN
      INSERT INTO invocations SELECT eid,instance,stamp,COALESCE(d->>'invocation_id',eid),d->>'tenant',d->>'backend',
        COALESCE((d->>'stream')::boolean,false),(d->>'n_inputs')::bigint,(d->>'status')::integer,d->>'error_code',
        (d->>'duration_ms')::double precision,(d->>'upstream_ms')::double precision,d->>'model_version',
        COALESCE((d->>'reference_cost_nanousd')::bigint,0),(d->>'prompt_tokens')::bigint,(d->>'completion_tokens')::bigint,
        COALESCE(d->>'cost_basis','reference_tariff') ON CONFLICT DO NOTHING;
    WHEN 'attempt' THEN
      INSERT INTO upstream_attempts(instance_id,ts,invocation_id,tenant,canonical_model,provider,upstream_model,
        provider_request_id,attempt_no,mode,status,prompt_tokens,completion_tokens,provider_cost_nanousd,
        cost_source,cost_status,reconcile_attempts,next_reconcile_unix,reconcile_error,reconciled_at)
      SELECT instance,stamp,d->>'invocation_id',d->>'tenant',d->>'canonical_model',d->>'provider',d->>'upstream_model',
        d->>'provider_request_id',(d->>'attempt_no')::integer,d->>'mode',(d->>'status')::integer,
        (d->>'prompt_tokens')::bigint,(d->>'completion_tokens')::bigint,(d->>'provider_cost_nanousd')::bigint,
        d->>'cost_source',d->>'cost_status',COALESCE((d->>'reconcile_attempts')::integer,0),
        COALESCE((d->>'next_reconcile_unix')::bigint,0),d->>'reconcile_error',(d->>'reconciled_at')::timestamptz
      ON CONFLICT DO NOTHING;
    WHEN 'reconcile' THEN
      UPDATE upstream_attempts SET provider_cost_nanousd=(d->>'provider_cost_nanousd')::bigint,
        prompt_tokens=COALESCE((d->>'prompt_tokens')::bigint,prompt_tokens),
        completion_tokens=COALESCE((d->>'completion_tokens')::bigint,completion_tokens),
        cost_source='reconciled',cost_status='final',reconciled_at=stamp,reconcile_error=NULL
      WHERE instance_id=instance AND id=(d->>'row_id')::bigint AND provider_cost_nanousd IS NULL;
    WHEN 'defer' THEN
      UPDATE upstream_attempts SET reconcile_attempts=reconcile_attempts+1,
        next_reconcile_unix=extract(epoch FROM stamp)::bigint + LEAST(3600,5*(1<<LEAST(reconcile_attempts,9))),
        reconcile_error=left(d->>'detail',300),cost_status=CASE WHEN reconcile_attempts>=9 THEN 'unavailable' ELSE 'pending' END,
        last_defer_id=eid
      WHERE instance_id=instance AND id=(d->>'row_id')::bigint AND provider_cost_nanousd IS NULL
        AND last_defer_id IS DISTINCT FROM eid AND next_reconcile_unix<=extract(epoch FROM stamp)::bigint;
    WHEN 'price' THEN
      INSERT INTO provider_price_snapshots SELECT eid,instance,stamp,d->>'provider',d->>'upstream_model',
        (d->>'prompt_usd_per_token')::double precision,(d->>'completion_usd_per_token')::double precision,d->>'source'
      ON CONFLICT DO NOTHING;
    WHEN 'speech' THEN
      INSERT INTO speech_usage SELECT instance,d->>'invocation_id',(d->>'input_characters')::bigint,
        (d->>'output_bytes')::bigint,(d->>'audio_seconds')::double precision,(d->>'first_audio_ms')::double precision,d->>'profile_revision'
      ON CONFLICT DO NOTHING;
    WHEN 'media' THEN
      INSERT INTO media_usage SELECT instance,d->>'invocation_id',d->>'capability',(d->>'input_bytes')::bigint,
        (d->>'output_bytes')::bigint,d->'usage_json' ON CONFLICT DO NOTHING;
    ELSE RAISE EXCEPTION 'Unknown ledger event kind';
    END CASE;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION clover_telemetry.ingest(jsonb) FROM PUBLIC;

-- Aggregate attempts BEFORE joining calls: a retry must not duplicate a call,
-- its duration or its reference tariff. NULL means unknown, never free.
CREATE OR REPLACE VIEW clover_telemetry.calls AS
SELECT i.*, COALESCE(a.attempt_count,0) AS attempt_count,
  COALESCE(a.known_cost_attempts,0) AS known_cost_attempts,
  COALESCE(a.pending_attempts,0) AS pending_attempts,
  a.known_provider_cost_nanousd,
  CASE WHEN a.attempt_count=a.known_cost_attempts THEN a.known_provider_cost_nanousd END AS provider_cost_nanousd,
  CASE WHEN a.attempt_count IS NULL THEN 'unavailable'
       WHEN a.attempt_count=a.known_cost_attempts THEN 'complete'
       WHEN a.pending_attempts>0 THEN 'pending' ELSE 'partial' END AS provider_cost_status,
  s.input_characters,s.audio_seconds,s.first_audio_ms,
  m.capability AS media_capability,m.usage_json AS media_usage
FROM clover_telemetry.invocations i
LEFT JOIN (
  SELECT instance_id,invocation_id,count(*) AS attempt_count,
    count(provider_cost_nanousd) AS known_cost_attempts,
    count(*) FILTER(WHERE cost_status='pending') AS pending_attempts,
    sum(provider_cost_nanousd) AS known_provider_cost_nanousd
  FROM clover_telemetry.upstream_attempts GROUP BY instance_id,invocation_id
) a USING(instance_id,invocation_id)
LEFT JOIN clover_telemetry.speech_usage s USING(instance_id,invocation_id)
LEFT JOIN clover_telemetry.media_usage m USING(instance_id,invocation_id);
CREATE OR REPLACE VIEW clover_telemetry.daily_usage AS
SELECT instance_id,(ts AT TIME ZONE 'UTC')::date AS day,tenant,backend,model_version,cost_basis,
  count(*) AS calls,count(*) FILTER(WHERE status>=400) AS failed_calls,sum(n_inputs) AS inputs,
  sum(duration_ms) AS duration_ms,sum(prompt_tokens) AS prompt_tokens,sum(completion_tokens) AS completion_tokens,
  sum(reference_cost_nanousd) AS reference_cost_nanousd,
  sum(known_provider_cost_nanousd) AS known_provider_cost_nanousd,
  count(*) FILTER(WHERE provider_cost_status='complete') AS calls_with_complete_cost,
  sum(pending_attempts) AS pending_attempts
FROM clover_telemetry.calls GROUP BY instance_id,(ts AT TIME ZONE 'UTC')::date,tenant,backend,model_version,cost_basis;
