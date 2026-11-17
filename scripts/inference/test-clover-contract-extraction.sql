\set ON_ERROR_STOP on

DO $test$
DECLARE
    contract jsonb := $json${
      "type": "object",
      "additionalProperties": false,
      "required": ["topic", "action_requested", "action_text", "amounts"],
      "properties": {
        "topic": {"type": ["string", "null"], "enum": ["operations", "billing", null]},
        "action_requested": {"type": "boolean"},
        "action_text": {"type": ["string", "null"]},
        "amounts": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["value", "currency", "unit"],
            "properties": {
              "value": {"type": "number"},
              "currency": {"type": ["string", "null"]},
              "unit": {"type": ["string", "null"]}
            }
          }
        }
      },
      "x-invariants": [{
        "if": {"path": "action_requested", "op": "eq", "value": true},
        "then": {"path": "action_text", "op": "present"},
        "message": "action_text is required when an action is requested"
      }]
    }$json$::jsonb;
    validation jsonb;
    job_id uuid;
BEGIN
    IF jsonb_array_length(rvbbit._clover_extract_contract_errors(contract)) <> 0 THEN
        RAISE EXCEPTION 'valid contract was rejected: %',
            rvbbit._clover_extract_contract_errors(contract);
    END IF;

    validation := rvbbit.clover_extract_validate(
        '{"topic":"operations","action_requested":false,"action_text":null,"amounts":[{"value":42.5,"currency":null,"unit":"MW"}]}'::jsonb,
        contract
    );
    IF NOT (validation->>'valid')::boolean THEN
        RAISE EXCEPTION 'valid data was rejected: %', validation;
    END IF;

    validation := rvbbit.clover_extract_validate(
        '{"topic":"information","action_requested":true,"action_text":null,"amounts":[{"value":"42.5","currency":"MW","unit":null}],"extra":1}'::jsonb,
        contract
    );
    IF (validation->>'valid')::boolean
       OR NOT (validation->'errors' @> '[{"code":"enum"}]'::jsonb)
       OR NOT (validation->'errors' @> '[{"code":"type"}]'::jsonb)
       OR NOT (validation->'errors' @> '[{"code":"additional_property"}]'::jsonb)
       OR NOT (validation->'errors' @> '[{"code":"invariant"}]'::jsonb) THEN
        RAISE EXCEPTION 'invalid data did not produce the expected errors: %', validation;
    END IF;

    IF rvbbit.clover_extract_json('no inference', '{"type":"array","items":{"type":"string"}}'::jsonb)->>'status'
       <> 'invalid_contract' THEN
        RAISE EXCEPTION 'invalid contract should fail before inference';
    END IF;
    IF rvbbit.clover_extract_json('no inference', contract, '{"cache":"yes"}'::jsonb)->>'status'
       <> 'invalid_policy' THEN
        RAISE EXCEPTION 'invalid policy should fail before inference';
    END IF;
    IF rvbbit.clover_extract_json(
        'no inference',
        '{"type":"object","properties":{"x":{"type":"string","minLength":"one"}}}'::jsonb
    )->>'status' <> 'invalid_contract' THEN
        RAISE EXCEPTION 'malformed constraints should fail before inference';
    END IF;
    IF rvbbit.clover_extract_json(
        'no inference',
        '{"type":"object","properties":{},"unevaluatedProperties":false}'::jsonb
    )->>'status' <> 'invalid_contract' THEN
        RAISE EXCEPTION 'unsupported contract keywords should fail before inference';
    END IF;
    IF rvbbit.clover_extract_json(
        'no inference', contract, '{"semantic_rules":[""]}'::jsonb
    )->>'status' <> 'invalid_policy' THEN
        RAISE EXCEPTION 'malformed semantic rules should fail before inference';
    END IF;
    IF rvbbit.clover_extract_json(
        'no inference', contract,
        '{"semantic_rules":["use source evidence"],"semantic_review_max_attempts":0}'::jsonb
    )->>'status' <> 'invalid_policy' THEN
        RAISE EXCEPTION 'semantic review retry bounds should fail before inference';
    END IF;
    validation := rvbbit._clover_extract_error_metadata(
        'HTTP 502: upstream returned no answer after retry');
    IF validation->>'error_class' <> 'upstream_http'
       OR validation->>'upstream_status' <> '502'
       OR NOT coalesce((validation->>'retryable')::boolean, false) THEN
        RAISE EXCEPTION 'retryable transport error was not normalized: %', validation;
    END IF;
    validation := rvbbit._clover_extract_error_metadata(
        'step ''review'': HTTP error: error sending request for url (http://127.0.0.1:9/v1/chat/completions)');
    IF validation->>'error_class' <> 'transport'
       OR validation->>'upstream_status' IS NOT NULL
       OR NOT coalesce((validation->>'retryable')::boolean, false) THEN
        RAISE EXCEPTION 'send-request transport error was not normalized: %', validation;
    END IF;
    validation := rvbbit._clover_extract_error_metadata(
        'operator returned invalid JSON: expected value');
    IF validation->>'error_class' <> 'invalid_output'
       OR coalesce((validation->>'retryable')::boolean, true) THEN
        RAISE EXCEPTION 'invalid output should not be transport-retryable: %', validation;
    END IF;

    job_id := rvbbit.clover_extract_enqueue(
        '[{"id":"a","text":"alpha"},{"id":"b","text":"beta"}]'::jsonb,
        contract,
        '{"max_attempts":2}'::jsonb,
        'contract-test-job'
    );
    IF rvbbit.clover_extract_enqueue(
        '[{"id":"a","text":"alpha"},{"id":"b","text":"beta"}]'::jsonb,
        contract,
        '{"max_attempts":2}'::jsonb,
        'contract-test-job'
    ) IS DISTINCT FROM job_id THEN
        RAISE EXCEPTION 'job-key enqueue was not idempotent';
    END IF;
    IF rvbbit.clover_extract_job(job_id)->'counts'->>'queued' <> '2' THEN
        RAISE EXCEPTION 'queued job summary is incorrect: %', rvbbit.clover_extract_job(job_id);
    END IF;
END
$test$;

-- A governed read-only query must be able to consume an existing validated
-- cache entry without attempting to update hit counters.
DO $seed_read_only_cache$
DECLARE
    v_contract constant jsonb := '{
      "type":"object",
      "additionalProperties":false,
      "required":["value"],
      "properties":{"value":{"type":["string","null"]}}
    }'::jsonb;
    v_text constant text := 'clover read-only cache fixture';
    v_policy constant jsonb := '{}'::jsonb;
    v_key text;
BEGIN
    v_key := md5(session_user || chr(31) || v_text || chr(31) || v_contract::text
        || chr(31) || v_policy::text || chr(31) || 'contract-extract-v1');
    INSERT INTO rvbbit.clover_extract_success_cache(
        cache_key, owner_name, input_hash, contract_hash, result)
    VALUES (
        v_key, session_user, md5(v_text), md5(v_contract::text),
        jsonb_build_object(
            'status', 'ok',
            'data', jsonb_build_object('value', 'cached'),
            'validation', jsonb_build_object('valid', true, 'status', 'ok', 'errors', '[]'::jsonb),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('route', 'specialist', 'cache_hit', false, 'model_calls', 1)
        )
    )
    ON CONFLICT (cache_key) DO UPDATE SET result = EXCLUDED.result;
END
$seed_read_only_cache$;

BEGIN TRANSACTION READ ONLY;
DO $test_read_only_cache$
DECLARE
    v_result jsonb;
BEGIN
    v_result := rvbbit.clover_extract_json(
        'clover read-only cache fixture',
        '{
          "type":"object",
          "additionalProperties":false,
          "required":["value"],
          "properties":{"value":{"type":["string","null"]}}
        }'::jsonb
    );
    IF v_result #>> '{data,value}' <> 'cached'
       OR NOT coalesce((v_result #>> '{provenance,cache_hit}')::boolean, false)
       OR v_result #>> '{provenance,cache_mode}' <> 'read_only_hit'
       OR v_result #>> '{provenance,model_calls}' <> '0' THEN
        RAISE EXCEPTION 'read-only cache hit was not side-effect free: %', v_result;
    END IF;
END
$test_read_only_cache$;
ROLLBACK;

DELETE FROM rvbbit.clover_extract_success_cache
WHERE cache_key = md5(session_user || chr(31) || 'clover read-only cache fixture'
    || chr(31) || '{
      "type":"object",
      "additionalProperties":false,
      "required":["value"],
      "properties":{"value":{"type":["string","null"]}}
    }'::jsonb::text || chr(31) || '{}'::jsonb::text || chr(31) || 'contract-extract-v1');

SELECT 'clover contract extraction validation: ok' AS result;
