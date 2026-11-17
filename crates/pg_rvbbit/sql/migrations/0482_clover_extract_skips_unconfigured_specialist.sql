-- clover_extract_json's 'auto' route skips a specialist the installation marked unconfigured.
-- Self-hosted Clover catalogs mark clover_extract_llm `clover_route.enabled = false` when no
-- NuExtract route is configured. Every call still tried it first, logged
-- "operator '_clover_extract_primary' failed … not registered", wrote a failed receipt and
-- only then recovered with the generalist. Backends without a route marking (hosted Clover)
-- keep trying the specialist exactly as before. An explicit `route: specialist` still fails
-- loudly when the route is missing.
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_specialist_ready()
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((
        SELECT (b.transport_opts #>> '{clover_route,enabled}') IS DISTINCT FROM 'false'
        FROM rvbbit.backends b
        WHERE b.name = 'clover_extract_llm'
    ), false)
$fn$;

-- _clover_extract_json_v1 is 0383's clover_extract_json, renamed by 0384; only the route
-- selection above differs.
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_json_v1(
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
    v_validation jsonb;
    v_errors jsonb;
    v_template jsonb;
    v_candidate jsonb;
    v_recovery jsonb;
    v_rejected jsonb := '{}'::jsonb;
    v_attempts jsonb := '[]'::jsonb;
    v_receipt jsonb;
    v_request_id uuid;
    v_error text;
    v_route text := 'auto';
    v_instructions text := 'Extract only facts explicitly supported by the text. Do not copy placeholder values.';
    v_cache boolean := true;
    v_include_rejected boolean := false;
    v_fallback_on_error boolean := true;
    v_fallback_on_rejection boolean := true;
    v_max_input_chars integer := 131072;
    v_input_hash text;
    v_contract_hash text;
    v_cache_key text;
    v_cached jsonb;
    v_result jsonb;
    v_owner text := session_user;
BEGIN
    v_policy_errors := rvbbit._clover_extract_policy_errors(v_policy);
    IF jsonb_array_length(v_policy_errors) > 0 THEN
        RETURN jsonb_build_object('status', 'invalid_policy', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors', v_policy_errors),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;
    v_route := coalesce(nullif(v_policy->>'route', ''), 'auto');
    -- A route the installation marked unconfigured cannot answer; 'auto' goes straight
    -- to the generalist instead of logging a failed specialist attempt on every call.
    IF v_route = 'auto' AND NOT rvbbit._clover_extract_specialist_ready() THEN
        v_route := 'generalist';
    END IF;
    v_instructions := coalesce(v_policy->>'instructions', v_instructions);
    v_cache := coalesce((v_policy->>'cache')::boolean, true);
    v_include_rejected := coalesce((v_policy->>'include_rejected')::boolean, false);
    v_fallback_on_error := coalesce((v_policy->>'fallback_on_error')::boolean, true);
    v_fallback_on_rejection := coalesce((v_policy->>'fallback_on_rejection')::boolean, true);
    v_max_input_chars := coalesce((v_policy->>'max_input_chars')::integer, 131072);
    IF p_text IS NULL THEN
        RETURN jsonb_build_object('status', 'invalid_input', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors',
                jsonb_build_array(rvbbit._clover_extract_error('$.text', 'null', 'text cannot be SQL NULL'))),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;
    IF length(p_text) > v_max_input_chars THEN
        RETURN jsonb_build_object('status', 'input_too_large', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors',
                jsonb_build_array(rvbbit._clover_extract_error('$.text', 'max_length',
                    format('text has %s characters; policy allows %s', length(p_text), v_max_input_chars)))),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;
    IF octet_length(coalesce(p_contract::text, '')) > 65536 THEN
        RETURN jsonb_build_object('status', 'invalid_contract', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors',
                jsonb_build_array(rvbbit._clover_extract_error('$', 'max_size',
                    'contract exceeds 65536 bytes'))),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;

    v_contract_errors := rvbbit._clover_extract_contract_errors(p_contract);
    IF jsonb_array_length(v_contract_errors) > 0 THEN
        RETURN jsonb_build_object('status', 'invalid_contract', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors', v_contract_errors),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;

    v_input_hash := md5(p_text);
    v_contract_hash := md5(p_contract::text);
    v_cache_key := md5(v_owner || chr(31) || p_text || chr(31) || p_contract::text || chr(31)
        || (v_policy - 'cache' - 'include_rejected')::text || chr(31) || 'contract-extract-v1');

    IF v_cache THEN
        SELECT result INTO v_cached
        FROM rvbbit.clover_extract_success_cache
        WHERE cache_key = v_cache_key AND owner_name = v_owner;
        IF v_cached IS NOT NULL THEN
            UPDATE rvbbit.clover_extract_success_cache
               SET last_hit_at = clock_timestamp(), hit_count = hit_count + 1
             WHERE cache_key = v_cache_key AND owner_name = v_owner;
            RETURN jsonb_set(
                jsonb_set(
                    jsonb_set(v_cached, '{provenance,cache_hit}', 'true'::jsonb, true),
                    '{provenance,model_calls}', '0'::jsonb, true
                ),
                '{provenance,cache_key}', to_jsonb(v_cache_key), true
            );
        END IF;
    END IF;

    v_template := rvbbit._clover_extract_template(p_contract);

    IF v_route <> 'generalist' THEN
        v_request_id := gen_random_uuid();
        v_candidate := NULL;
        v_error := NULL;
        BEGIN
            v_candidate := rvbbit._clover_extract_primary(
                p_text, v_template::text, v_instructions, v_request_id::text);
        EXCEPTION WHEN OTHERS THEN
            v_error := SQLERRM;
        END;
        v_receipt := rvbbit._clover_extract_receipt('_clover_extract_primary', v_request_id);
        IF v_candidate IS NOT NULL THEN
            v_validation := rvbbit.clover_extract_validate(v_candidate, p_contract);
            v_errors := v_validation->'errors';
        ELSE
            v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                '$', 'no_output', coalesce(v_error, 'specialist returned SQL NULL')));
        END IF;
        v_attempts := v_attempts || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
            'route', 'specialist',
            'request_id', v_request_id,
            'valid', coalesce((v_validation->>'valid')::boolean, false),
            'errors', v_errors,
            'receipt', v_receipt,
            'error', v_error
        )));

        IF coalesce((v_validation->>'valid')::boolean, false) THEN
            v_result := jsonb_build_object(
                'status', 'ok',
                'data', v_candidate,
                'validation', v_validation,
                'attempts', v_attempts,
                'provenance', jsonb_build_object(
                    'route', 'specialist', 'cache_hit', false,
                    'model_calls', jsonb_array_length(v_attempts),
                    'input_hash_md5', v_input_hash,
                    'contract_hash_md5', v_contract_hash,
                    'source_chars', length(p_text),
                    'validated_at', clock_timestamp()
                )
            );
            IF v_cache THEN
                INSERT INTO rvbbit.clover_extract_success_cache(
                    cache_key, owner_name, input_hash, contract_hash, result)
                VALUES (v_cache_key, v_owner, v_input_hash, v_contract_hash, v_result)
                ON CONFLICT (cache_key) DO UPDATE SET
                    result = EXCLUDED.result,
                    created_at = clock_timestamp(),
                    last_hit_at = NULL,
                    hit_count = 0;
            END IF;
            RETURN v_result;
        END IF;
        v_rejected := v_rejected || jsonb_build_object('specialist', v_candidate);
        IF v_route = 'specialist'
           OR (v_candidate IS NULL AND NOT v_fallback_on_error)
           OR (v_candidate IS NOT NULL AND NOT v_fallback_on_rejection) THEN
            IF v_include_rejected THEN
                RETURN jsonb_build_object('status', CASE WHEN v_candidate IS NULL THEN 'error' ELSE 'rejected' END,
                    'data', NULL, 'rejected_data', v_rejected,
                    'validation', jsonb_build_object('valid', false, 'errors', v_errors),
                    'attempts', v_attempts,
                    'provenance', jsonb_build_object('route', 'specialist', 'cache_hit', false,
                        'model_calls', jsonb_array_length(v_attempts),
                        'input_hash_md5', v_input_hash, 'contract_hash_md5', v_contract_hash));
            END IF;
            RETURN jsonb_build_object('status', CASE WHEN v_candidate IS NULL THEN 'error' ELSE 'rejected' END,
                'data', NULL,
                'validation', jsonb_build_object('valid', false, 'errors', v_errors),
                'attempts', v_attempts,
                'provenance', jsonb_build_object('route', 'specialist', 'cache_hit', false,
                    'model_calls', jsonb_array_length(v_attempts),
                    'input_hash_md5', v_input_hash, 'contract_hash_md5', v_contract_hash));
        END IF;
    END IF;

    v_request_id := gen_random_uuid();
    v_recovery := NULL;
    v_error := NULL;
    v_validation := NULL;
    BEGIN
        v_recovery := rvbbit._clover_extract_recovery(
            p_text,
            p_contract::text,
            v_instructions,
            coalesce(v_candidate::text, 'null'),
            coalesce(v_errors::text, '[]'),
            v_request_id::text
        );
    EXCEPTION WHEN OTHERS THEN
        v_error := SQLERRM;
    END;
    v_receipt := rvbbit._clover_extract_receipt('_clover_extract_recovery', v_request_id);
    IF v_recovery IS NOT NULL THEN
        v_validation := rvbbit.clover_extract_validate(v_recovery, p_contract);
        v_errors := v_validation->'errors';
    ELSE
        v_errors := jsonb_build_array(rvbbit._clover_extract_error(
            '$', 'no_output', coalesce(v_error, 'generalist returned SQL NULL')));
    END IF;
    v_attempts := v_attempts || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
        'route', 'generalist',
        'request_id', v_request_id,
        'valid', coalesce((v_validation->>'valid')::boolean, false),
        'errors', v_errors,
        'receipt', v_receipt,
        'error', v_error
    )));

    IF coalesce((v_validation->>'valid')::boolean, false) THEN
        v_result := jsonb_build_object(
            'status', 'ok',
            'data', v_recovery,
            'validation', v_validation,
            'attempts', v_attempts,
            'provenance', jsonb_build_object(
                'route', 'generalist', 'cache_hit', false,
                'model_calls', jsonb_array_length(v_attempts),
                'input_hash_md5', v_input_hash,
                'contract_hash_md5', v_contract_hash,
                'source_chars', length(p_text),
                'validated_at', clock_timestamp()
            )
        );
        IF v_cache THEN
            INSERT INTO rvbbit.clover_extract_success_cache(
                cache_key, owner_name, input_hash, contract_hash, result)
            VALUES (v_cache_key, v_owner, v_input_hash, v_contract_hash, v_result)
            ON CONFLICT (cache_key) DO UPDATE SET
                result = EXCLUDED.result,
                created_at = clock_timestamp(),
                last_hit_at = NULL,
                hit_count = 0;
        END IF;
        RETURN v_result;
    END IF;

    v_rejected := v_rejected || jsonb_build_object('generalist', v_recovery);
    v_result := jsonb_build_object(
        'status', CASE WHEN v_candidate IS NULL AND v_recovery IS NULL THEN 'error' ELSE 'rejected' END,
        'data', NULL,
        'validation', jsonb_build_object('valid', false, 'errors', v_errors),
        'attempts', v_attempts,
        'provenance', jsonb_build_object(
            'route', CASE WHEN v_route = 'generalist' THEN 'generalist' ELSE 'specialist_then_generalist' END,
            'cache_hit', false,
            'model_calls', jsonb_array_length(v_attempts),
            'input_hash_md5', v_input_hash,
            'contract_hash_md5', v_contract_hash,
            'source_chars', length(p_text),
            'validated_at', clock_timestamp()
        )
    );
    IF v_include_rejected THEN
        v_result := v_result || jsonb_build_object('rejected_data', v_rejected);
    END IF;
    RETURN v_result;
END
$fn$;
