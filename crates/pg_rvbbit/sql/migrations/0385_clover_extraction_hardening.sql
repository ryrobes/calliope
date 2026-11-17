-- 0385: make contract extraction safe in governed read-only SQL and make
-- semantic-review transport failures observable, bounded, and non-amplifying.

-- migrate() runs through SPI, where MODULE_PATHNAME is not substituted. Bind
-- the new pgrx diagnostic accessor explicitly for existing same-version
-- appliance databases; fresh CREATE EXTENSION installs the same symbol.
CREATE OR REPLACE FUNCTION rvbbit._operator_last_diagnostic(
    op_name text,
    request_id text
) RETURNS jsonb
LANGUAGE c VOLATILE PARALLEL SAFE
AS '$libdir/pg_rvbbit', '_operator_last_diagnostic_wrapper';

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_policy_errors(p_policy jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_policy jsonb := coalesce(p_policy, '{}'::jsonb);
    v_errors jsonb := '[]'::jsonb;
    v_key text;
    v_number numeric;
BEGIN
    IF jsonb_typeof(v_policy) <> 'object' THEN
        RETURN jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy', 'type', 'policy must be an object'));
    END IF;

    FOR v_key IN SELECT key FROM jsonb_each(v_policy) LOOP
        IF v_key NOT IN (
            'route', 'instructions', 'semantic_rules', 'semantic_review_max_attempts',
            'cache', 'include_rejected', 'fallback_on_error', 'fallback_on_rejection',
            'max_input_chars', 'max_attempts'
        ) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                '$.policy.' || v_key, 'unsupported_policy',
                format('unsupported extraction policy: %s', v_key)));
        END IF;
    END LOOP;

    FOREACH v_key IN ARRAY ARRAY[
        'cache', 'include_rejected', 'fallback_on_error', 'fallback_on_rejection'
    ] LOOP
        IF v_policy ? v_key AND jsonb_typeof(v_policy->v_key) <> 'boolean' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                '$.policy.' || v_key, 'type', v_key || ' must be boolean'));
        END IF;
    END LOOP;

    IF v_policy ? 'route' AND (
        jsonb_typeof(v_policy->'route') <> 'string'
        OR v_policy->>'route' NOT IN ('auto', 'specialist', 'generalist')
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy.route', 'enum', 'route must be auto, specialist, or generalist'));
    END IF;
    IF v_policy ? 'instructions' AND (
        jsonb_typeof(v_policy->'instructions') <> 'string'
        OR length(v_policy->>'instructions') > 32768
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy.instructions', 'type_or_length',
            'instructions must be a string of at most 32768 characters'));
    END IF;

    IF v_policy ? 'semantic_rules' THEN
        IF jsonb_typeof(v_policy->'semantic_rules') <> 'array' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                '$.policy.semantic_rules', 'type',
                'semantic_rules must be a non-empty array of strings'));
        ELSIF jsonb_array_length(v_policy->'semantic_rules') = 0
           OR jsonb_array_length(v_policy->'semantic_rules') > 32
           OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(v_policy->'semantic_rules') rule
                WHERE jsonb_typeof(rule) <> 'string'
                   OR btrim(rule #>> '{}') = ''
                   OR length(rule #>> '{}') > 2048
           ) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                '$.policy.semantic_rules', 'shape_or_length',
                'semantic_rules must contain 1 to 32 non-empty strings of at most 2048 characters'));
        END IF;
    END IF;

    FOREACH v_key IN ARRAY ARRAY[
        'max_input_chars', 'max_attempts', 'semantic_review_max_attempts'
    ] LOOP
        IF v_policy ? v_key THEN
            IF jsonb_typeof(v_policy->v_key) <> 'number' THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    '$.policy.' || v_key, 'type', v_key || ' must be an integer'));
            ELSE
                v_number := (v_policy->>v_key)::numeric;
                IF v_number <> trunc(v_number) THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        '$.policy.' || v_key, 'integer', v_key || ' must be an integer'));
                ELSIF v_key = 'max_input_chars' AND v_number NOT BETWEEN 1 AND 1048576 THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        '$.policy.max_input_chars', 'range',
                        'max_input_chars must be between 1 and 1048576'));
                ELSIF v_key = 'max_attempts' AND v_number NOT BETWEEN 1 AND 10 THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        '$.policy.max_attempts', 'range',
                        'max_attempts must be between 1 and 10'));
                ELSIF v_key = 'semantic_review_max_attempts' AND v_number NOT BETWEEN 1 AND 3 THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        '$.policy.semantic_review_max_attempts', 'range',
                        'semantic_review_max_attempts must be between 1 and 3'));
                END IF;
            END IF;
        END IF;
    END LOOP;
    RETURN v_errors;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_error_metadata(p_error text)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_error text := nullif(btrim(p_error), '');
    v_lower text;
    v_match text[];
    v_status integer;
    v_class text;
    v_retryable boolean := false;
BEGIN
    IF v_error IS NULL THEN RETURN '{}'::jsonb; END IF;
    v_lower := lower(v_error);
    v_match := regexp_match(v_error,
        '(?i)(?:http(?: status)?|upstream)[^0-9]{0,32}([45][0-9]{2})');
    IF v_match IS NOT NULL THEN v_status := v_match[1]::integer; END IF;

    IF v_status IS NOT NULL THEN
        v_class := 'upstream_http';
        v_retryable := v_status IN (408, 425, 429, 500, 502, 503, 504);
    ELSIF v_lower ~ '(timed out|timeout|deadline exceeded)' THEN
        v_class := 'timeout';
        v_retryable := true;
    ELSIF v_lower ~ '(connection reset|connection refused|connection closed|no answer|temporar|unavailable|dns|socket|broken pipe|error sending request|failed to send request|connect error)' THEN
        v_class := 'transport';
        v_retryable := true;
    ELSIF v_lower ~ '(invalid json|non-json|parse)' THEN
        v_class := 'invalid_output';
    ELSE
        v_class := 'operator';
    END IF;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'error_class', v_class,
        'upstream_status', v_status,
        'retryable', v_retryable
    ));
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_receipt(
    p_operator text,
    p_request_id uuid
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
AS $fn$
DECLARE
    v_receipt jsonb;
    v_live jsonb;
BEGIN
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'receipt_id', r.receipt_id,
        'latency_ms', r.latency_ms,
        'tokens_in', r.n_tokens_in,
        'tokens_out', r.n_tokens_out,
        'error', r.error,
        'model', r.sub_calls #>> '{0,model}',
        'backend', r.sub_calls #>> '{0,backend}',
        'transport', r.sub_calls #>> '{0,transport}',
        'provider_request_id', r.sub_calls #>> '{0,provider_request_id}',
        'upstream_id', r.sub_calls #>> '{0,upstream_id}'
    )) INTO v_receipt
    FROM rvbbit.receipts r
    WHERE r.operator = p_operator
      AND r.inputs->>'request_id' = p_request_id::text
    ORDER BY r.invocation_at DESC
    LIMIT 1;

    -- In governed read-only SQL the durable receipt is queued and flushed by
    -- Calliope after this statement finishes. The backend-local diagnostic is
    -- the same completed call, available synchronously for this envelope.
    v_live := rvbbit._operator_last_diagnostic(p_operator, p_request_id::text);
    IF v_live IS NOT NULL THEN
        v_receipt := coalesce(v_receipt, '{}'::jsonb)
            || jsonb_strip_nulls(v_live - 'operator' - 'request_id');
    END IF;
    IF v_receipt IS NULL OR v_receipt = '{}'::jsonb THEN RETURN NULL; END IF;
    RETURN v_receipt || rvbbit._clover_extract_error_metadata(v_receipt->>'error');
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_enrich_attempts(p_attempts jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_attempt jsonb;
    v_receipt jsonb;
    v_result jsonb := '[]'::jsonb;
BEGIN
    IF jsonb_typeof(coalesce(p_attempts, '[]'::jsonb)) <> 'array' THEN
        RETURN '[]'::jsonb;
    END IF;
    FOR v_attempt IN SELECT value FROM jsonb_array_elements(p_attempts) LOOP
        v_receipt := v_attempt->'receipt';
        IF jsonb_typeof(v_receipt) = 'object' THEN
            v_attempt := v_attempt
                || rvbbit._clover_extract_error_metadata(v_receipt->>'error')
                || jsonb_strip_nulls(jsonb_build_object(
                    'receipt_id', v_receipt->>'receipt_id',
                    'error', coalesce(v_attempt->>'error', v_receipt->>'error')
                ));
        END IF;
        v_result := v_result || jsonb_build_array(v_attempt);
    END LOOP;
    RETURN v_result;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_run_review(
    p_text text,
    p_contract jsonb,
    p_candidate jsonb,
    p_rules jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_request_id uuid;
    v_review jsonb;
    v_review_validation jsonb;
    v_receipt jsonb;
    v_error text;
    v_error_metadata jsonb;
    v_errors jsonb;
    v_status text;
    v_retryable boolean;
    v_attempt jsonb;
    v_attempts jsonb := '[]'::jsonb;
    v_attempt_number integer;
    v_max_attempts integer := greatest(1, least(3, coalesce(
        nullif(current_setting('rvbbit.clover_semantic_review_max_attempts', true), '')::integer,
        2
    )));
    v_review_contract constant jsonb := '{
      "type":"object",
      "additionalProperties":false,
      "required":["valid","errors"],
      "properties":{
        "valid":{"type":"boolean"},
        "errors":{"type":"array","items":{
          "type":"object",
          "additionalProperties":false,
          "required":["path","code","message"],
          "properties":{
            "path":{"type":"string"},
            "code":{"type":"string","enum":["unsupported","misattributed","contradiction","rule"]},
            "message":{"type":"string"}
          }
        }}
      }
    }'::jsonb;
BEGIN
    FOR v_attempt_number IN 1..v_max_attempts LOOP
        v_request_id := gen_random_uuid();
        v_review := NULL;
        v_review_validation := NULL;
        v_receipt := NULL;
        v_error := NULL;
        v_error_metadata := '{}'::jsonb;
        v_errors := '[]'::jsonb;
        v_retryable := false;

        BEGIN
            v_review := rvbbit._clover_extract_semantic_review(
                p_text, p_contract::text, p_candidate::text, p_rules::text,
                v_request_id::text
            );
        EXCEPTION WHEN OTHERS THEN
            v_error := SQLERRM;
        END;
        v_receipt := rvbbit._clover_extract_receipt(
            '_clover_extract_semantic_review', v_request_id);
        v_error := coalesce(v_error, v_receipt->>'error');

        IF v_review IS NULL THEN
            v_status := 'error';
            v_error := coalesce(v_error, 'semantic reviewer returned SQL NULL');
            v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                '$', 'semantic_review_error', v_error));
            v_error_metadata := rvbbit._clover_extract_error_metadata(v_error);
            v_retryable := coalesce((v_error_metadata->>'retryable')::boolean, false);
        ELSE
            v_review_validation := rvbbit.clover_extract_validate(v_review, v_review_contract);
            IF NOT coalesce((v_review_validation->>'valid')::boolean, false) THEN
                v_status := 'error';
                v_error := 'semantic reviewer returned an invalid verdict';
                v_errors := v_review_validation->'errors';
                v_error_metadata := jsonb_build_object(
                    'error_class', 'invalid_output', 'retryable', false);
            ELSIF (v_review->>'valid')::boolean AND jsonb_array_length(v_review->'errors') > 0 THEN
                v_status := 'error';
                v_error := 'semantic reviewer returned valid=true with non-empty errors';
                v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                    '$.errors', 'semantic_review_inconsistent', v_error));
                v_error_metadata := jsonb_build_object(
                    'error_class', 'invalid_output', 'retryable', false);
            ELSIF NOT (v_review->>'valid')::boolean AND jsonb_array_length(v_review->'errors') = 0 THEN
                v_status := 'error';
                v_error := 'semantic reviewer returned valid=false without an error';
                v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                    '$.errors', 'semantic_review_inconsistent', v_error));
                v_error_metadata := jsonb_build_object(
                    'error_class', 'invalid_output', 'retryable', false);
            ELSIF (v_review->>'valid')::boolean THEN
                v_status := 'ok';
                v_errors := '[]'::jsonb;
            ELSE
                v_status := 'rejected';
                v_errors := v_review->'errors';
            END IF;
        END IF;

        v_attempt := jsonb_strip_nulls(jsonb_build_object(
            'route', 'semantic_review',
            'attempt_number', v_attempt_number,
            'request_id', v_request_id,
            'receipt_id', v_receipt->>'receipt_id',
            'status', v_status,
            'valid', v_status = 'ok',
            'errors', v_errors,
            'receipt', v_receipt,
            'error', v_error
        )) || v_error_metadata;
        v_attempts := v_attempts || jsonb_build_array(v_attempt);

        EXIT WHEN v_status <> 'error' OR NOT v_retryable OR v_attempt_number >= v_max_attempts;
    END LOOP;

    RETURN jsonb_build_object(
        'status', v_status,
        'valid', v_status = 'ok',
        'errors', v_errors,
        'attempt', v_attempt,
        'attempts', v_attempts,
        'model_calls', jsonb_array_length(v_attempts)
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_json(
    p_text text,
    p_contract jsonb,
    p_policy jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_policy jsonb := coalesce(p_policy, '{}'::jsonb);
    v_policy_errors jsonb;
    v_contract_errors jsonb;
    v_rules jsonb;
    v_cache boolean;
    v_include_rejected boolean;
    v_fallback_on_rejection boolean;
    v_max_input_chars integer;
    v_review_max_attempts integer;
    v_read_only boolean := current_setting('transaction_read_only', true) = 'on';
    v_owner text := session_user;
    v_cache_key text;
    v_cache_key_version text;
    v_policy_identity jsonb;
    v_policy_hash text;
    v_cached jsonb;
    v_base jsonb;
    v_candidate jsonb;
    v_recovery jsonb;
    v_validation jsonb;
    v_review jsonb;
    v_attempts jsonb;
    v_semantic_errors jsonb;
    v_result jsonb;
    v_request_id uuid;
    v_receipt jsonb;
    v_error text;
    v_error_metadata jsonb;
    v_final_route text;
    v_base_route text;
    v_cache_mode text;
BEGIN
    v_policy_errors := rvbbit._clover_extract_policy_errors(v_policy);
    IF jsonb_array_length(v_policy_errors) > 0 THEN
        RETURN rvbbit._clover_extract_json_v1(p_text, p_contract, v_policy);
    END IF;

    v_cache := coalesce((v_policy->>'cache')::boolean, true);
    v_include_rejected := coalesce((v_policy->>'include_rejected')::boolean, false);
    v_fallback_on_rejection := coalesce((v_policy->>'fallback_on_rejection')::boolean, true);
    v_max_input_chars := coalesce((v_policy->>'max_input_chars')::integer, 131072);
    v_review_max_attempts := coalesce(
        (v_policy->>'semantic_review_max_attempts')::integer, 2);
    v_cache_mode := CASE
        WHEN NOT v_cache THEN 'disabled'
        WHEN v_read_only THEN 'read_only_no_write'
        ELSE 'read_write'
    END;

    IF NOT (v_policy ? 'semantic_rules') THEN
        v_policy_identity := v_policy
            - 'cache' - 'include_rejected' - 'semantic_review_max_attempts';
        v_policy_hash := md5(v_policy_identity::text);
        v_cache_key_version := 'contract-extract-v1';

        -- Preserve useful cache reads in governed SQL, but never mutate the hit
        -- counter or persist a miss from a physically read-only transaction.
        IF v_read_only AND v_cache AND p_text IS NOT NULL
           AND length(p_text) <= v_max_input_chars
           AND octet_length(coalesce(p_contract::text, '')) <= 65536
           AND jsonb_array_length(rvbbit._clover_extract_contract_errors(p_contract)) = 0 THEN
            v_cache_key := md5(v_owner || chr(31) || p_text || chr(31) || p_contract::text
                || chr(31) || v_policy_identity::text || chr(31) || v_cache_key_version);
            SELECT result INTO v_cached
            FROM rvbbit.clover_extract_success_cache
            WHERE cache_key = v_cache_key AND owner_name = v_owner;
            IF v_cached IS NOT NULL THEN
                v_result := jsonb_set(v_cached, '{provenance,cache_hit}', 'true'::jsonb, true);
                v_result := jsonb_set(v_result, '{provenance,model_calls}', '0'::jsonb, true);
                v_result := jsonb_set(v_result, '{provenance,cache_key}', to_jsonb(v_cache_key), true);
                v_cache_mode := 'read_only_hit';
            END IF;
        END IF;
        IF v_result IS NULL THEN
            v_result := rvbbit._clover_extract_json_v1(
                p_text,
                p_contract,
                (v_policy - 'semantic_review_max_attempts')
                    || CASE WHEN v_read_only THEN jsonb_build_object('cache', false) ELSE '{}'::jsonb END
            );
        END IF;
        v_result := jsonb_set(v_result, '{attempts}',
            rvbbit._clover_extract_enrich_attempts(v_result->'attempts'), true);
        v_result := jsonb_set(v_result, '{provenance,policy_hash_md5}',
            to_jsonb(v_policy_hash), true);
        v_result := jsonb_set(v_result, '{provenance,cache_key_version}',
            to_jsonb(v_cache_key_version), true);
        v_result := jsonb_set(v_result, '{provenance,cache_mode}',
            to_jsonb(v_cache_mode), true);
        v_result := jsonb_set(v_result, '{validation,structural_status}',
            to_jsonb(CASE WHEN v_result->>'status' = 'ok' THEN 'ok'
                ELSE coalesce(v_result #>> '{validation,status}', v_result->>'status') END), true);
        v_result := jsonb_set(v_result, '{validation,semantic_status}',
            '"not_requested"'::jsonb, true);
        RETURN v_result;
    END IF;

    v_rules := v_policy->'semantic_rules';
    v_policy_identity := v_policy - 'cache' - 'include_rejected';
    v_policy_hash := md5(v_policy_identity::text);
    v_cache_key_version := 'contract-extract-semantic-v2';
    PERFORM set_config('rvbbit.clover_semantic_review_max_attempts',
        v_review_max_attempts::text, true);

    -- Delegate all ordinary zero-token failures to v1 so every route has the
    -- same public envelope and error wording.
    IF p_text IS NULL OR length(p_text) > v_max_input_chars
       OR octet_length(coalesce(p_contract::text, '')) > 65536 THEN
        v_result := rvbbit._clover_extract_json_v1(
            p_text, p_contract,
            (v_policy - 'semantic_rules' - 'semantic_review_max_attempts')
                || CASE WHEN v_read_only THEN jsonb_build_object('cache', false) ELSE '{}'::jsonb END);
        RETURN jsonb_set(v_result, '{provenance,policy_hash_md5}', to_jsonb(v_policy_hash), true);
    END IF;
    v_contract_errors := rvbbit._clover_extract_contract_errors(p_contract);
    IF jsonb_array_length(v_contract_errors) > 0 THEN
        v_result := rvbbit._clover_extract_json_v1(
            p_text, p_contract,
            (v_policy - 'semantic_rules' - 'semantic_review_max_attempts')
                || CASE WHEN v_read_only THEN jsonb_build_object('cache', false) ELSE '{}'::jsonb END);
        RETURN jsonb_set(v_result, '{provenance,policy_hash_md5}', to_jsonb(v_policy_hash), true);
    END IF;

    v_cache_key := md5(v_owner || chr(31) || p_text || chr(31) || p_contract::text
        || chr(31) || v_policy_identity::text || chr(31) || v_cache_key_version);
    IF v_cache THEN
        SELECT result INTO v_cached
        FROM rvbbit.clover_extract_success_cache
        WHERE cache_key = v_cache_key AND owner_name = v_owner;
        IF v_cached IS NOT NULL THEN
            IF NOT v_read_only THEN
                UPDATE rvbbit.clover_extract_success_cache
                   SET last_hit_at = clock_timestamp(), hit_count = hit_count + 1
                 WHERE cache_key = v_cache_key AND owner_name = v_owner;
            ELSE
                v_cache_mode := 'read_only_hit';
            END IF;
            v_result := jsonb_set(v_cached, '{provenance,cache_hit}', 'true'::jsonb, true);
            v_result := jsonb_set(v_result, '{provenance,model_calls}', '0'::jsonb, true);
            v_result := jsonb_set(v_result, '{provenance,cache_key}', to_jsonb(v_cache_key), true);
            v_result := jsonb_set(v_result, '{provenance,cache_mode}', to_jsonb(v_cache_mode), true);
            RETURN v_result;
        END IF;
    END IF;

    -- v1 performs specialist/generalist extraction and exact structural checks,
    -- but semantic acceptance owns the only success-cache write on this route.
    v_base := rvbbit._clover_extract_json_v1(
        p_text,
        p_contract,
        (v_policy - 'semantic_rules' - 'semantic_review_max_attempts' - 'max_attempts')
            || jsonb_build_object('cache', false, 'include_rejected', true)
    );
    v_base := jsonb_set(v_base, '{attempts}',
        rvbbit._clover_extract_enrich_attempts(v_base->'attempts'), true);
    IF v_base->>'status' <> 'ok' THEN
        IF NOT v_include_rejected THEN v_base := v_base - 'rejected_data'; END IF;
        v_base := jsonb_set(v_base, '{provenance,policy_hash_md5}', to_jsonb(v_policy_hash), true);
        v_base := jsonb_set(v_base, '{provenance,cache_key_version}', to_jsonb(v_cache_key_version), true);
        v_base := jsonb_set(v_base, '{provenance,cache_mode}', to_jsonb(v_cache_mode), true);
        RETURN v_base;
    END IF;

    v_candidate := v_base->'data';
    v_attempts := coalesce(v_base->'attempts', '[]'::jsonb);
    v_base_route := coalesce(v_base #>> '{provenance,route}', 'unknown');
    v_review := rvbbit._clover_extract_run_review(p_text, p_contract, v_candidate, v_rules);
    v_attempts := v_attempts || coalesce(v_review->'attempts', '[]'::jsonb);

    IF v_review->>'status' = 'ok' THEN
        v_result := jsonb_set(v_base, '{attempts}', v_attempts, true);
        v_result := jsonb_set(v_result, '{validation,semantic_valid}', 'true'::jsonb, true);
        v_result := jsonb_set(v_result, '{validation,structural_status}', '"ok"'::jsonb, true);
        v_result := jsonb_set(v_result, '{validation,semantic_status}', '"ok"'::jsonb, true);
        v_result := jsonb_set(v_result, '{validation,semantic_rules}', v_rules, true);
        v_final_route := v_base_route || '_verified';
    ELSIF v_review->>'status' = 'error' THEN
        -- A transport failure is not evidence that the candidate is wrong.
        -- The bounded same-review retries above exhaust before we stop here;
        -- never pay for or trust a recovery based on a missing verdict.
        v_result := jsonb_build_object(
            'status', 'error',
            'data', NULL,
            'validation', jsonb_build_object(
                'valid', false,
                'status', 'semantic_error',
                'structural_status', 'ok',
                'semantic_status', 'error',
                'errors', v_review->'errors',
                'semantic_valid', false,
                'semantic_rules', v_rules),
            'attempts', v_attempts,
            'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
        );
        IF v_include_rejected THEN
            v_result := v_result || jsonb_build_object('rejected_data', v_candidate);
        END IF;
        v_final_route := v_base_route || '_semantic_review_error';
    ELSIF NOT v_fallback_on_rejection THEN
        v_result := jsonb_build_object(
            'status', 'rejected',
            'data', NULL,
            'validation', jsonb_build_object(
                'valid', false,
                'status', 'semantic_rejected',
                'structural_status', 'ok',
                'semantic_status', 'rejected',
                'errors', v_review->'errors',
                'semantic_valid', false,
                'semantic_rules', v_rules),
            'attempts', v_attempts,
            'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
        );
        IF v_include_rejected THEN
            v_result := v_result || jsonb_build_object('rejected_data', v_candidate);
        END IF;
        v_final_route := v_base_route || '_semantic_rejected';
    ELSE
        v_semantic_errors := v_review->'errors';
        v_request_id := gen_random_uuid();
        BEGIN
            v_recovery := rvbbit._clover_extract_recovery(
                p_text,
                p_contract::text,
                coalesce(v_policy->>'instructions',
                    'Extract only facts explicitly supported by the text. Do not copy placeholder values.')
                    || E'\nThe candidate failed these semantic rules. Correct it from source evidence.',
                v_candidate::text,
                v_semantic_errors::text,
                v_request_id::text
            );
        EXCEPTION WHEN OTHERS THEN
            v_error := SQLERRM;
        END;
        v_receipt := rvbbit._clover_extract_receipt('_clover_extract_recovery', v_request_id);
        v_error := coalesce(v_error, v_receipt->>'error');
        IF v_recovery IS NOT NULL THEN
            v_validation := rvbbit.clover_extract_validate(v_recovery, p_contract);
        ELSE
            v_validation := jsonb_build_object(
                'valid', false, 'status', 'error', 'errors',
                jsonb_build_array(rvbbit._clover_extract_error(
                    '$', 'no_output', coalesce(v_error, 'semantic recovery returned SQL NULL'))));
        END IF;
        v_error_metadata := rvbbit._clover_extract_error_metadata(v_error);
        v_attempts := v_attempts || jsonb_build_array(
            jsonb_strip_nulls(jsonb_build_object(
                'route', 'semantic_recovery',
                'request_id', v_request_id,
                'receipt_id', v_receipt->>'receipt_id',
                'valid', coalesce((v_validation->>'valid')::boolean, false),
                'errors', v_validation->'errors',
                'receipt', v_receipt,
                'error', v_error
            )) || v_error_metadata
        );

        IF NOT coalesce((v_validation->>'valid')::boolean, false) THEN
            v_result := jsonb_build_object(
                'status', CASE WHEN v_recovery IS NULL THEN 'error' ELSE 'rejected' END,
                'data', NULL,
                'validation', v_validation || jsonb_build_object(
                    'structural_status', coalesce(v_validation->>'status', 'rejected'),
                    'semantic_status', 'not_run',
                    'semantic_valid', false,
                    'semantic_rules', v_rules),
                'attempts', v_attempts,
                'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
            );
            IF v_include_rejected THEN
                v_result := v_result || jsonb_build_object(
                    'rejected_data', jsonb_build_object(
                        'candidate', v_candidate, 'recovery', v_recovery));
            END IF;
            v_final_route := v_base_route || '_semantic_recovery_invalid';
        ELSE
            v_review := rvbbit._clover_extract_run_review(
                p_text, p_contract, v_recovery, v_rules);
            v_attempts := v_attempts || coalesce(v_review->'attempts', '[]'::jsonb);
            IF v_review->>'status' <> 'ok' THEN
                v_result := jsonb_build_object(
                    'status', v_review->>'status',
                    'data', NULL,
                    'validation', v_validation || jsonb_build_object(
                        'valid', false,
                        'status', CASE WHEN v_review->>'status' = 'error'
                            THEN 'semantic_error' ELSE 'semantic_rejected' END,
                        'structural_status', 'ok',
                        'semantic_status', v_review->>'status',
                        'semantic_valid', false,
                        'semantic_rules', v_rules,
                        'errors', v_review->'errors'),
                    'attempts', v_attempts,
                    'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
                );
                IF v_include_rejected THEN
                    v_result := v_result || jsonb_build_object(
                        'rejected_data', jsonb_build_object(
                            'candidate', v_candidate, 'recovery', v_recovery));
                END IF;
                v_final_route := v_base_route || CASE
                    WHEN v_review->>'status' = 'error'
                        THEN '_semantic_recovery_unverified'
                    ELSE '_semantic_recovery_rejected'
                END;
            ELSE
                v_result := jsonb_build_object(
                    'status', 'ok',
                    'data', v_recovery,
                    'validation', v_validation || jsonb_build_object(
                        'structural_status', 'ok',
                        'semantic_status', 'ok',
                        'semantic_valid', true,
                        'semantic_rules', v_rules),
                    'attempts', v_attempts,
                    'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
                );
                v_final_route := v_base_route || '_semantic_recovery_verified';
            END IF;
        END IF;
    END IF;

    v_result := jsonb_set(v_result, '{provenance,route}', to_jsonb(v_final_route), true);
    v_result := jsonb_set(v_result, '{provenance,cache_hit}', 'false'::jsonb, true);
    v_result := jsonb_set(v_result, '{provenance,model_calls}',
        to_jsonb(jsonb_array_length(v_attempts)), true);
    v_result := jsonb_set(v_result, '{provenance,validated_at}',
        to_jsonb(clock_timestamp()), true);
    v_result := jsonb_set(v_result, '{provenance,policy_hash_md5}',
        to_jsonb(v_policy_hash), true);
    v_result := jsonb_set(v_result, '{provenance,cache_key_version}',
        to_jsonb(v_cache_key_version), true);
    v_result := jsonb_set(v_result, '{provenance,cache_mode}',
        to_jsonb(v_cache_mode), true);

    IF v_result->>'status' = 'ok' AND v_cache AND NOT v_read_only THEN
        INSERT INTO rvbbit.clover_extract_success_cache(
            cache_key, owner_name, input_hash, contract_hash, result)
        VALUES (
            v_cache_key, v_owner, md5(p_text), md5(p_contract::text), v_result)
        ON CONFLICT (cache_key) DO UPDATE SET
            result = EXCLUDED.result,
            created_at = clock_timestamp(),
            last_hit_at = NULL,
            hit_count = 0;
    END IF;
    RETURN v_result;
END
$fn$;

REVOKE EXECUTE ON FUNCTION rvbbit._operator_last_diagnostic(text, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_error_metadata(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_enrich_attempts(jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_run_review(text, jsonb, jsonb, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_receipt(text, uuid) FROM PUBLIC;

COMMENT ON FUNCTION rvbbit.clover_extract_json(text, jsonb, jsonb) IS
    'Extract text into a strict JSON contract with read-only-safe validated caching, bounded transport-aware semantic review, repair receipts, and explicit structural/semantic status.';
