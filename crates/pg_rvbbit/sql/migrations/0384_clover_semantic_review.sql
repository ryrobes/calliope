-- 0384: optional semantic review for contract-valid extraction output.
--
-- Structural validation proves that JSON obeys its declared type contract; it
-- cannot prove that a model attributed a statement to the right actor or
-- distinguished an FYI from a request. Callers may supply semantic_rules when
-- those distinctions matter. The extraction specialist still drafts the row,
-- while the configured generalist reviews evidence and repairs a rejected
-- candidate. Only structurally and semantically accepted rows are cached.

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
            'route', 'instructions', 'semantic_rules', 'cache', 'include_rejected',
            'fallback_on_error', 'fallback_on_rejection',
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

    FOREACH v_key IN ARRAY ARRAY['max_input_chars', 'max_attempts'] LOOP
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
                        '$.policy.max_attempts', 'range', 'max_attempts must be between 1 and 10'));
                END IF;
            END IF;
        END IF;
    END LOOP;
    RETURN v_errors;
END
$fn$;

SELECT rvbbit.create_operator(
    op_name        => '_clover_extract_semantic_review',
    op_arg_names   => ARRAY['text', 'contract', 'candidate', 'rules', 'request_id'],
    op_return_type => 'jsonb',
    op_parser      => 'json',
    op_max_tokens  => 1200,
    op_temperature => 0.0,
    op_description => 'Internal evidence review for contract-valid extraction output.',
    op_steps       => jsonb_build_array(jsonb_build_object(
        'name', 'review',
        'kind', 'llm',
        'provider', 'clover_llm',
        'model', 'clover',
        'system', 'You are a strict extraction auditor. Judge whether a candidate JSON object is supported by the source text and obeys every semantic rule. Return compact JSON only: {"valid":boolean,"errors":[{"path":"$.field","code":"unsupported|misattributed|contradiction|rule","message":"short explanation"}]}. valid=true requires errors=[]. Do not rewrite the candidate and do not use Markdown.',
        'user', E'SOURCE TEXT:\n{{ inputs.text }}\n\nCONTRACT:\n{{ inputs.contract }}\n\nSEMANTIC RULES:\n{{ inputs.rules }}\n\nCANDIDATE:\n{{ inputs.candidate }}\n\nAudit only against the source and rules. Return the audit JSON object.',
        'enable_thinking', false,
        'max_tokens', 1200,
        'temperature', 0.0
    ))
);

UPDATE rvbbit.operators
SET cache_policy = 'never'
WHERE name = '_clover_extract_semantic_review';

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_semantic_review(text, text, text, text, text, jsonb) FROM PUBLIC;

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
    v_request_id uuid := gen_random_uuid();
    v_review jsonb;
    v_review_validation jsonb;
    v_receipt jsonb;
    v_error text;
    v_errors jsonb;
    v_status text;
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

    IF v_review IS NULL THEN
        v_status := 'error';
        v_errors := jsonb_build_array(rvbbit._clover_extract_error(
            '$', 'semantic_review_error', coalesce(v_error, 'semantic reviewer returned SQL NULL')));
    ELSE
        v_review_validation := rvbbit.clover_extract_validate(v_review, v_review_contract);
        IF NOT coalesce((v_review_validation->>'valid')::boolean, false) THEN
            v_status := 'error';
            v_errors := v_review_validation->'errors';
        ELSIF (v_review->>'valid')::boolean AND jsonb_array_length(v_review->'errors') > 0 THEN
            v_status := 'error';
            v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                '$.errors', 'semantic_review_inconsistent',
                'semantic reviewer returned valid=true with non-empty errors'));
        ELSIF NOT (v_review->>'valid')::boolean AND jsonb_array_length(v_review->'errors') = 0 THEN
            v_status := 'error';
            v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                '$.errors', 'semantic_review_inconsistent',
                'semantic reviewer returned valid=false without an error'));
        ELSIF (v_review->>'valid')::boolean THEN
            v_status := 'ok';
            v_errors := '[]'::jsonb;
        ELSE
            v_status := 'rejected';
            v_errors := v_review->'errors';
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'status', v_status,
        'valid', v_status = 'ok',
        'errors', v_errors,
        'attempt', jsonb_strip_nulls(jsonb_build_object(
            'route', 'semantic_review',
            'request_id', v_request_id,
            'valid', v_status = 'ok',
            'errors', v_errors,
            'receipt', v_receipt,
            'error', v_error
        ))
    );
END
$fn$;

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_run_review(text, jsonb, jsonb, jsonb) FROM PUBLIC;

DO $rename_v1$
BEGIN
    IF to_regprocedure('rvbbit._clover_extract_json_v1(text,jsonb,jsonb)') IS NULL THEN
        ALTER FUNCTION rvbbit.clover_extract_json(text, jsonb, jsonb)
            RENAME TO _clover_extract_json_v1;
    END IF;
END
$rename_v1$;

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_json_v1(text, jsonb, jsonb) FROM PUBLIC;

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
    v_fallback_on_error boolean;
    v_fallback_on_rejection boolean;
    v_max_input_chars integer;
    v_owner text := session_user;
    v_cache_key text;
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
    v_final_route text;
BEGIN
    v_policy_errors := rvbbit._clover_extract_policy_errors(v_policy);
    IF jsonb_array_length(v_policy_errors) > 0 OR NOT (v_policy ? 'semantic_rules') THEN
        RETURN rvbbit._clover_extract_json_v1(p_text, p_contract, v_policy);
    END IF;

    v_rules := v_policy->'semantic_rules';
    v_cache := coalesce((v_policy->>'cache')::boolean, true);
    v_include_rejected := coalesce((v_policy->>'include_rejected')::boolean, false);
    v_fallback_on_error := coalesce((v_policy->>'fallback_on_error')::boolean, true);
    v_fallback_on_rejection := coalesce((v_policy->>'fallback_on_rejection')::boolean, true);
    v_max_input_chars := coalesce((v_policy->>'max_input_chars')::integer, 131072);

    -- Delegate all ordinary zero-token failures to v1 so every route has the
    -- same public envelope and error wording.
    IF p_text IS NULL OR length(p_text) > v_max_input_chars
       OR octet_length(coalesce(p_contract::text, '')) > 65536 THEN
        RETURN rvbbit._clover_extract_json_v1(p_text, p_contract, v_policy);
    END IF;
    v_contract_errors := rvbbit._clover_extract_contract_errors(p_contract);
    IF jsonb_array_length(v_contract_errors) > 0 THEN
        RETURN rvbbit._clover_extract_json_v1(p_text, p_contract, v_policy);
    END IF;

    v_cache_key := md5(v_owner || chr(31) || p_text || chr(31) || p_contract::text
        || chr(31) || (v_policy - 'cache' - 'include_rejected')::text
        || chr(31) || 'contract-extract-semantic-v1');
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

    -- v1 performs the specialist/generalist extraction and exact structural
    -- checks, but cannot cache before semantic acceptance.
    v_base := rvbbit._clover_extract_json_v1(
        p_text,
        p_contract,
        (v_policy - 'semantic_rules' - 'max_attempts')
            || jsonb_build_object('cache', false, 'include_rejected', true)
    );
    IF v_base->>'status' <> 'ok' THEN
        IF NOT v_include_rejected THEN v_base := v_base - 'rejected_data'; END IF;
        RETURN v_base;
    END IF;

    v_candidate := v_base->'data';
    v_attempts := coalesce(v_base->'attempts', '[]'::jsonb);
    v_review := rvbbit._clover_extract_run_review(p_text, p_contract, v_candidate, v_rules);
    v_attempts := v_attempts || jsonb_build_array(v_review->'attempt');

    IF v_review->>'status' = 'ok' THEN
        v_result := jsonb_set(v_base, '{attempts}', v_attempts, true);
        v_result := jsonb_set(v_result, '{validation,semantic_valid}', 'true'::jsonb, true);
        v_result := jsonb_set(v_result, '{validation,semantic_rules}', v_rules, true);
        v_final_route := coalesce(v_base #>> '{provenance,route}', 'unknown') || '_verified';
    ELSE
        v_semantic_errors := v_review->'errors';
        IF (v_review->>'status' = 'error' AND NOT v_fallback_on_error)
           OR (v_review->>'status' = 'rejected' AND NOT v_fallback_on_rejection) THEN
            v_result := jsonb_build_object(
                'status', v_review->>'status',
                'data', NULL,
                'validation', jsonb_build_object(
                    'valid', false, 'status', v_review->>'status',
                    'errors', v_semantic_errors, 'semantic_valid', false,
                    'semantic_rules', v_rules),
                'attempts', v_attempts,
                'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
            );
            IF v_include_rejected THEN
                v_result := v_result || jsonb_build_object('rejected_data', v_candidate);
            END IF;
            RETURN jsonb_set(v_result, '{provenance,model_calls}',
                to_jsonb(jsonb_array_length(v_attempts)), true);
        END IF;

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
        IF v_recovery IS NOT NULL THEN
            v_validation := rvbbit.clover_extract_validate(v_recovery, p_contract);
        ELSE
            v_validation := jsonb_build_object(
                'valid', false, 'status', 'error', 'errors',
                jsonb_build_array(rvbbit._clover_extract_error(
                    '$', 'no_output', coalesce(v_error, 'semantic recovery returned SQL NULL'))));
        END IF;
        v_attempts := v_attempts || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
            'route', 'semantic_recovery',
            'request_id', v_request_id,
            'valid', coalesce((v_validation->>'valid')::boolean, false),
            'errors', v_validation->'errors',
            'receipt', v_receipt,
            'error', v_error
        )));

        IF NOT coalesce((v_validation->>'valid')::boolean, false) THEN
            v_result := jsonb_build_object(
                'status', CASE WHEN v_recovery IS NULL THEN 'error' ELSE 'rejected' END,
                'data', NULL,
                'validation', v_validation || jsonb_build_object(
                    'semantic_valid', false, 'semantic_rules', v_rules),
                'attempts', v_attempts,
                'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
            );
            IF v_include_rejected THEN
                v_result := v_result || jsonb_build_object(
                    'rejected_data', jsonb_build_object(
                        'candidate', v_candidate, 'recovery', v_recovery));
            END IF;
            RETURN jsonb_set(v_result, '{provenance,model_calls}',
                to_jsonb(jsonb_array_length(v_attempts)), true);
        END IF;

        v_review := rvbbit._clover_extract_run_review(p_text, p_contract, v_recovery, v_rules);
        v_attempts := v_attempts || jsonb_build_array(v_review->'attempt');
        IF v_review->>'status' <> 'ok' THEN
            v_result := jsonb_build_object(
                'status', v_review->>'status',
                'data', NULL,
                'validation', v_validation || jsonb_build_object(
                    'valid', false, 'semantic_valid', false,
                    'semantic_rules', v_rules, 'errors', v_review->'errors'),
                'attempts', v_attempts,
                'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
            );
            IF v_include_rejected THEN
                v_result := v_result || jsonb_build_object(
                    'rejected_data', jsonb_build_object(
                        'candidate', v_candidate, 'recovery', v_recovery));
            END IF;
            RETURN jsonb_set(v_result, '{provenance,model_calls}',
                to_jsonb(jsonb_array_length(v_attempts)), true);
        END IF;

        v_result := jsonb_build_object(
            'status', 'ok',
            'data', v_recovery,
            'validation', v_validation || jsonb_build_object(
                'semantic_valid', true, 'semantic_rules', v_rules),
            'attempts', v_attempts,
            'provenance', coalesce(v_base->'provenance', '{}'::jsonb)
        );
        v_final_route := coalesce(v_base #>> '{provenance,route}', 'unknown')
            || '_semantic_recovery_verified';
    END IF;

    v_result := jsonb_set(v_result, '{provenance,route}', to_jsonb(v_final_route), true);
    v_result := jsonb_set(v_result, '{provenance,cache_hit}', 'false'::jsonb, true);
    v_result := jsonb_set(v_result, '{provenance,model_calls}',
        to_jsonb(jsonb_array_length(v_attempts)), true);
    v_result := jsonb_set(v_result, '{provenance,validated_at}',
        to_jsonb(clock_timestamp()), true);

    IF v_cache THEN
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

COMMENT ON FUNCTION rvbbit.clover_extract_json(text, jsonb, jsonb) IS
    'Extract text into a strict JSON contract; semantic_rules optionally invoke evidence review and repair before validated-success caching.';
