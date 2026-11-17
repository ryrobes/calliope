-- 0383: backend-neutral, contract-validated structured extraction.
--
-- The public contract is JSON Schema-shaped and intentionally does not name a
-- model. A constrained extraction specialist gets the first attempt; the
-- configured Clover generalist repairs rejected output or covers an unavailable
-- specialist. Only validated successes enter the result cache. Raw attempts
-- remain visible through ordinary semantic receipts.

CREATE TABLE IF NOT EXISTS rvbbit.clover_extract_success_cache (
    cache_key       text PRIMARY KEY,
    owner_name      text NOT NULL,
    input_hash      text NOT NULL,
    contract_hash   text NOT NULL,
    result          jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_hit_at     timestamptz,
    hit_count       bigint NOT NULL DEFAULT 0,
    CONSTRAINT clover_extract_success_cache_result_ok
        CHECK (result->>'status' = 'ok' AND jsonb_typeof(result->'data') = 'object')
);

CREATE INDEX IF NOT EXISTS clover_extract_success_cache_owner_created_idx
    ON rvbbit.clover_extract_success_cache(owner_name, created_at DESC);

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_error(
    p_path text,
    p_code text,
    p_message text
) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
    SELECT jsonb_build_object('path', p_path, 'code', p_code, 'message', p_message)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_schema_types(p_schema jsonb)
RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
BEGIN
    IF jsonb_typeof(p_schema->'type') = 'string' THEN
        RETURN ARRAY[p_schema->>'type'];
    ELSIF jsonb_typeof(p_schema->'type') = 'array' THEN
        RETURN ARRAY(SELECT jsonb_array_elements_text(p_schema->'type'));
    END IF;
    RETURN ARRAY[]::text[];
END
$fn$;

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
            'route', 'instructions', 'cache', 'include_rejected',
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

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_type_matches(
    p_value jsonb,
    p_schema jsonb
) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_types text[] := rvbbit._clover_extract_schema_types(p_schema);
    v_type text := jsonb_typeof(p_value);
BEGIN
    IF v_type = 'null' THEN
        RETURN 'null' = ANY(v_types);
    END IF;
    IF v_type = ANY(v_types) THEN
        RETURN true;
    END IF;
    IF v_type = 'number' AND 'integer' = ANY(v_types) THEN
        RETURN (p_value #>> '{}')::numeric = trunc((p_value #>> '{}')::numeric);
    END IF;
    RETURN false;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_condition_valid(p_condition jsonb)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_op text;
    v_path jsonb;
BEGIN
    IF jsonb_typeof(p_condition) <> 'object' THEN
        RETURN false;
    END IF;
    v_op := p_condition->>'op';
    v_path := p_condition->'path';
    IF v_op NOT IN ('eq', 'neq', 'present', 'absent', 'in') THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(v_path) = 'string' THEN
        IF btrim(v_path #>> '{}') = '' THEN RETURN false; END IF;
    ELSIF jsonb_typeof(v_path) = 'array' THEN
        IF jsonb_array_length(v_path) = 0 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(v_path) e
            WHERE jsonb_typeof(e) <> 'string' OR btrim(e #>> '{}') = ''
        ) THEN RETURN false; END IF;
    ELSE
        RETURN false;
    END IF;
    IF v_op IN ('eq', 'neq') AND NOT (p_condition ? 'value') THEN
        RETURN false;
    END IF;
    IF v_op = 'in' AND jsonb_typeof(p_condition->'values') <> 'array' THEN
        RETURN false;
    END IF;
    RETURN true;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_contract_errors(
    p_schema jsonb,
    p_path text DEFAULT '$',
    p_depth integer DEFAULT 0
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_errors jsonb := '[]'::jsonb;
    v_types text[];
    v_type text;
    v_prop record;
    v_item jsonb;
    v_key text;
    v_number numeric;
    v_type_count integer;
    v_distinct_type_count integer;
    v_required_seen text[] := ARRAY[]::text[];
    v_required_name text;
    v_idx integer := 0;
BEGIN
    IF p_depth > 12 THEN
        RETURN jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'max_depth', 'contract nesting exceeds 12 levels'));
    END IF;
    IF p_schema IS NULL OR jsonb_typeof(p_schema) <> 'object' THEN
        RETURN jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'schema_type', 'each schema node must be an object'));
    END IF;

    IF NOT (p_schema ? 'type') THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'missing_type', 'each schema node requires an explicit type'));
        v_types := ARRAY[]::text[];
    ELSIF jsonb_typeof(p_schema->'type') NOT IN ('string', 'array') THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path || '.type', 'type_shape', 'type must be a string or an array of strings'));
        v_types := ARRAY[]::text[];
    ELSIF jsonb_typeof(p_schema->'type') = 'array' AND (
        jsonb_array_length(p_schema->'type') = 0 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_schema->'type') e
            WHERE jsonb_typeof(e) <> 'string'
        )
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path || '.type', 'type_shape', 'type arrays must contain at least one string'));
        v_types := ARRAY[]::text[];
    ELSE
        v_types := rvbbit._clover_extract_schema_types(p_schema);
        SELECT count(*), count(DISTINCT value #>> '{}')
          INTO v_type_count, v_distinct_type_count
          FROM jsonb_array_elements(
              CASE WHEN jsonb_typeof(p_schema->'type') = 'array'
                   THEN p_schema->'type'
                   ELSE jsonb_build_array(p_schema->'type') END
          );
        IF v_type_count <> v_distinct_type_count THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.type', 'duplicate_type', 'type entries must be unique'));
        END IF;
    END IF;

    FOR v_key IN SELECT key FROM jsonb_each(p_schema) LOOP
        IF v_key NOT IN (
            'type', 'description', 'properties', 'required', 'additionalProperties',
            'items', 'enum', 'minLength', 'maxLength', 'pattern', 'format',
            'minimum', 'maximum', 'x-invariants'
        ) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.' || v_key, 'unsupported_keyword',
                format('unsupported extraction-contract keyword: %s', v_key)));
        END IF;
    END LOOP;

    IF p_schema ? 'description' AND (
        jsonb_typeof(p_schema->'description') <> 'string'
        OR length(p_schema->>'description') > 2048
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path || '.description', 'description',
            'description must be a string of at most 2048 characters'));
    END IF;

    FOREACH v_type IN ARRAY v_types LOOP
        IF v_type NOT IN ('object', 'array', 'string', 'number', 'integer', 'boolean', 'null') THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.type', 'unsupported_type', format('unsupported type: %s', v_type)));
        END IF;
    END LOOP;

    IF p_depth = 0 AND v_types IS DISTINCT FROM ARRAY['object']::text[] THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path || '.type', 'root_type',
            'the extraction contract root type must be exactly object'));
    END IF;

    IF 'object' = ANY(v_types) THEN
        IF jsonb_typeof(p_schema->'properties') <> 'object' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.properties', 'properties', 'object schemas require a properties object'));
        ELSE
            FOR v_prop IN SELECT key, value FROM jsonb_each(p_schema->'properties') LOOP
                IF v_prop.key = '' THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        p_path || '.properties', 'empty_property', 'property names cannot be empty'));
                END IF;
                v_errors := v_errors || rvbbit._clover_extract_contract_errors(
                    v_prop.value, p_path || '.properties.' || v_prop.key, p_depth + 1);
            END LOOP;
        END IF;

        IF p_schema ? 'required' THEN
            IF jsonb_typeof(p_schema->'required') <> 'array' THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.required', 'required_shape', 'required must be an array of property names'));
            ELSE
                FOR v_item IN SELECT value FROM jsonb_array_elements(p_schema->'required') LOOP
                    IF jsonb_typeof(v_item) <> 'string' THEN
                        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                            p_path || '.required', 'required_name', 'required entries must be strings'));
                    ELSE
                        v_required_name := v_item #>> '{}';
                        IF v_required_name = '' THEN
                            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                                p_path || '.required', 'required_name',
                                'required property names cannot be empty'));
                        END IF;
                        IF v_required_name = ANY(v_required_seen) THEN
                            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                                p_path || '.required', 'required_duplicate',
                                format('required property is duplicated: %s', v_required_name)));
                        ELSE
                            v_required_seen := array_append(v_required_seen, v_required_name);
                        END IF;
                        IF jsonb_typeof(p_schema->'properties') = 'object'
                           AND NOT ((p_schema->'properties') ? v_required_name) THEN
                            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                                p_path || '.required', 'required_unknown',
                                format('required property is not declared: %s', v_required_name)));
                        END IF;
                    END IF;
                END LOOP;
            END IF;
        END IF;

        IF p_schema ? 'additionalProperties'
           AND jsonb_typeof(p_schema->'additionalProperties') <> 'boolean' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.additionalProperties', 'additional_properties',
                'additionalProperties must be boolean in extraction contracts'));
        END IF;
    ELSE
        FOREACH v_key IN ARRAY ARRAY['properties', 'required', 'additionalProperties'] LOOP
            IF p_schema ? v_key THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.' || v_key, 'keyword_type',
                    v_key || ' is only valid for object fields'));
            END IF;
        END LOOP;
    END IF;

    IF 'array' = ANY(v_types) THEN
        IF jsonb_typeof(p_schema->'items') <> 'object' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.items', 'items', 'array schemas require one items schema object'));
        ELSE
            v_errors := v_errors || rvbbit._clover_extract_contract_errors(
                p_schema->'items', p_path || '.items', p_depth + 1);
        END IF;
    ELSIF p_schema ? 'items' THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path || '.items', 'keyword_type', 'items is only valid for array fields'));
    END IF;

    IF p_schema ? 'enum' THEN
        IF jsonb_typeof(p_schema->'enum') <> 'array' OR jsonb_array_length(p_schema->'enum') = 0 THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.enum', 'enum_shape', 'enum must be a non-empty array'));
        ELSE
            FOR v_item IN SELECT value FROM jsonb_array_elements(p_schema->'enum') LOOP
                IF NOT rvbbit._clover_extract_type_matches(v_item, p_schema) THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        p_path || '.enum', 'enum_type',
                        format('enum value %s does not match its declared type', v_item::text)));
                END IF;
            END LOOP;
        END IF;
    END IF;

    IF p_schema ? 'pattern' THEN
        IF NOT ('string' = ANY(v_types)) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.pattern', 'keyword_type', 'pattern is only valid for string fields'));
        ELSIF jsonb_typeof(p_schema->'pattern') <> 'string' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.pattern', 'pattern_type', 'pattern must be a string'));
        ELSE
            BEGIN
                PERFORM '' ~ (p_schema->>'pattern');
            EXCEPTION WHEN OTHERS THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.pattern', 'pattern_invalid', SQLERRM));
            END;
        END IF;
    END IF;

    FOREACH v_key IN ARRAY ARRAY['minLength', 'maxLength'] LOOP
        IF p_schema ? v_key THEN
            IF NOT ('string' = ANY(v_types)) THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.' || v_key, 'keyword_type', v_key || ' is only valid for string fields'));
            ELSIF jsonb_typeof(p_schema->v_key) <> 'number' THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.' || v_key, 'type', v_key || ' must be a non-negative integer'));
            ELSE
                v_number := (p_schema->>v_key)::numeric;
                IF v_number <> trunc(v_number) OR v_number < 0 OR v_number > 1048576 THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        p_path || '.' || v_key, 'range',
                        v_key || ' must be an integer between 0 and 1048576'));
                END IF;
            END IF;
        END IF;
    END LOOP;
    IF jsonb_typeof(p_schema->'minLength') = 'number'
       AND jsonb_typeof(p_schema->'maxLength') = 'number'
       AND (p_schema->>'minLength')::numeric > (p_schema->>'maxLength')::numeric THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'length_range', 'minLength cannot exceed maxLength'));
    END IF;

    IF p_schema ? 'format' THEN
        IF NOT ('string' = ANY(v_types)) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.format', 'keyword_type', 'format is only valid for string fields'));
        ELSIF jsonb_typeof(p_schema->'format') <> 'string'
           OR p_schema->>'format' NOT IN ('date', 'date-time') THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.format', 'format', 'format must be date or date-time'));
        END IF;
    END IF;

    FOREACH v_key IN ARRAY ARRAY['minimum', 'maximum'] LOOP
        IF p_schema ? v_key THEN
            IF NOT ('number' = ANY(v_types) OR 'integer' = ANY(v_types)) THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.' || v_key, 'keyword_type', v_key || ' is only valid for numeric fields'));
            ELSIF jsonb_typeof(p_schema->v_key) <> 'number' THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path || '.' || v_key, 'type', v_key || ' must be a number'));
            END IF;
        END IF;
    END LOOP;
    IF jsonb_typeof(p_schema->'minimum') = 'number'
       AND jsonb_typeof(p_schema->'maximum') = 'number'
       AND (p_schema->>'minimum')::numeric > (p_schema->>'maximum')::numeric THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'numeric_range', 'minimum cannot exceed maximum'));
    END IF;

    IF p_schema ? 'x-invariants' THEN
        IF NOT ('object' = ANY(v_types)) THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.x-invariants', 'keyword_type',
                'x-invariants is only valid for object fields'));
        ELSIF jsonb_typeof(p_schema->'x-invariants') <> 'array' THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path || '.x-invariants', 'invariants_shape', 'x-invariants must be an array'));
        ELSE
            v_idx := 0;
            FOR v_item IN SELECT value FROM jsonb_array_elements(p_schema->'x-invariants') LOOP
                IF jsonb_typeof(v_item) <> 'object'
                   OR NOT rvbbit._clover_extract_condition_valid(v_item->'if')
                   OR NOT rvbbit._clover_extract_condition_valid(v_item->'then') THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        format('%s.x-invariants[%s]', p_path, v_idx), 'invariant_shape',
                        'each invariant requires valid if and then conditions'));
                END IF;
                v_idx := v_idx + 1;
            END LOOP;
        END IF;
    END IF;

    RETURN v_errors;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_condition_matches(
    p_data jsonb,
    p_condition jsonb
) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_path text[];
    v_value jsonb;
    v_exists boolean;
    v_op text := p_condition->>'op';
BEGIN
    IF jsonb_typeof(p_condition->'path') = 'array' THEN
        v_path := ARRAY(SELECT jsonb_array_elements_text(p_condition->'path'));
    ELSE
        v_path := string_to_array(p_condition->>'path', '.');
    END IF;
    v_value := p_data #> v_path;
    v_exists := v_value IS NOT NULL;

    CASE v_op
        WHEN 'present' THEN RETURN v_exists AND jsonb_typeof(v_value) <> 'null';
        WHEN 'absent' THEN RETURN NOT v_exists OR jsonb_typeof(v_value) = 'null';
        WHEN 'eq' THEN RETURN v_exists AND v_value = p_condition->'value';
        WHEN 'neq' THEN RETURN NOT v_exists OR v_value <> p_condition->'value';
        WHEN 'in' THEN RETURN v_exists AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_condition->'values') allowed
            WHERE allowed = v_value
        );
        ELSE RETURN false;
    END CASE;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_validation_errors(
    p_value jsonb,
    p_schema jsonb,
    p_path text DEFAULT '$'
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_errors jsonb := '[]'::jsonb;
    v_prop record;
    v_item record;
    v_required jsonb;
    v_text text;
    v_number numeric;
    v_invariant jsonb;
    v_idx integer := 0;
BEGIN
    IF NOT rvbbit._clover_extract_type_matches(p_value, p_schema) THEN
        RETURN jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'type', format('expected %s, got %s',
                coalesce((p_schema->'type')::text, 'unknown'), jsonb_typeof(p_value))));
    END IF;

    IF p_schema ? 'enum' AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_schema->'enum') allowed
        WHERE allowed = p_value
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            p_path, 'enum', format('value is not in enum %s', (p_schema->'enum')::text)));
    END IF;

    IF jsonb_typeof(p_value) = 'object' THEN
        IF jsonb_typeof(p_schema->'required') = 'array' THEN
            FOR v_required IN SELECT value FROM jsonb_array_elements(p_schema->'required') LOOP
                IF NOT (p_value ? (v_required #>> '{}')) THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        p_path || '.' || (v_required #>> '{}'), 'required', 'required property is absent'));
                END IF;
            END LOOP;
        END IF;

        IF p_schema->'additionalProperties' = 'false'::jsonb THEN
            FOR v_prop IN SELECT key FROM jsonb_each(p_value) LOOP
                IF NOT ((p_schema->'properties') ? v_prop.key) THEN
                    v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                        p_path || '.' || v_prop.key, 'additional_property', 'property is not declared'));
                END IF;
            END LOOP;
        END IF;

        IF jsonb_typeof(p_schema->'properties') = 'object' THEN
            FOR v_prop IN SELECT key, value FROM jsonb_each(p_schema->'properties') LOOP
                IF p_value ? v_prop.key THEN
                    v_errors := v_errors || rvbbit._clover_extract_validation_errors(
                        p_value->v_prop.key, v_prop.value, p_path || '.' || v_prop.key);
                END IF;
            END LOOP;
        END IF;
    ELSIF jsonb_typeof(p_value) = 'array' AND jsonb_typeof(p_schema->'items') = 'object' THEN
        FOR v_item IN SELECT value, ordinality - 1 AS idx
                      FROM jsonb_array_elements(p_value) WITH ORDINALITY LOOP
            v_errors := v_errors || rvbbit._clover_extract_validation_errors(
                v_item.value, p_schema->'items', format('%s[%s]', p_path, v_item.idx));
        END LOOP;
    ELSIF jsonb_typeof(p_value) = 'string' THEN
        v_text := p_value #>> '{}';
        IF p_schema ? 'minLength' AND length(v_text) < (p_schema->>'minLength')::integer THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path, 'min_length', format('string is shorter than %s', p_schema->>'minLength')));
        END IF;
        IF p_schema ? 'maxLength' AND length(v_text) > (p_schema->>'maxLength')::integer THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path, 'max_length', format('string is longer than %s', p_schema->>'maxLength')));
        END IF;
        IF p_schema ? 'pattern' AND v_text !~ (p_schema->>'pattern') THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path, 'pattern', 'string does not match the required pattern'));
        END IF;
        IF p_schema->>'format' = 'date' THEN
            BEGIN
                IF v_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RAISE invalid_datetime_format; END IF;
                PERFORM v_text::date;
            EXCEPTION WHEN OTHERS THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path, 'format_date', 'value is not a valid YYYY-MM-DD date'));
            END;
        ELSIF p_schema->>'format' = 'date-time' THEN
            BEGIN
                PERFORM v_text::timestamptz;
            EXCEPTION WHEN OTHERS THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    p_path, 'format_date_time', 'value is not a valid date-time'));
            END;
        END IF;
    ELSIF jsonb_typeof(p_value) = 'number' THEN
        v_number := (p_value #>> '{}')::numeric;
        IF p_schema ? 'minimum' AND v_number < (p_schema->>'minimum')::numeric THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path, 'minimum', format('number is below %s', p_schema->>'minimum')));
        END IF;
        IF p_schema ? 'maximum' AND v_number > (p_schema->>'maximum')::numeric THEN
            v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                p_path, 'maximum', format('number is above %s', p_schema->>'maximum')));
        END IF;
    END IF;

    IF jsonb_typeof(p_schema->'x-invariants') = 'array' THEN
        v_idx := 0;
        FOR v_invariant IN SELECT value FROM jsonb_array_elements(p_schema->'x-invariants') LOOP
            IF rvbbit._clover_extract_condition_matches(p_value, v_invariant->'if')
               AND NOT rvbbit._clover_extract_condition_matches(p_value, v_invariant->'then') THEN
                v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
                    format('%s.x-invariants[%s]', p_path, v_idx), 'invariant',
                    coalesce(v_invariant->>'message', 'cross-field invariant failed')));
            END IF;
            v_idx := v_idx + 1;
        END LOOP;
    END IF;

    RETURN v_errors;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_validate(
    p_data jsonb,
    p_contract jsonb
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_contract_errors jsonb;
    v_errors jsonb;
BEGIN
    v_contract_errors := rvbbit._clover_extract_contract_errors(p_contract);
    IF jsonb_array_length(v_contract_errors) > 0 THEN
        RETURN jsonb_build_object(
            'valid', false,
            'status', 'invalid_contract',
            'errors', v_contract_errors
        );
    END IF;
    v_errors := rvbbit._clover_extract_validation_errors(p_data, p_contract);
    RETURN jsonb_build_object(
        'valid', jsonb_array_length(v_errors) = 0,
        'status', CASE WHEN jsonb_array_length(v_errors) = 0 THEN 'ok' ELSE 'rejected' END,
        'errors', v_errors
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_template(p_schema jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    v_type text;
    v_description text := nullif(btrim(p_schema->>'description'), '');
    v_descriptor text;
    v_template jsonb;
BEGIN
    IF jsonb_typeof(p_schema->'enum') = 'array' THEN
        SELECT to_jsonb(string_agg(value #>> '{}', '|' ORDER BY ordinality))
          INTO v_template
          FROM jsonb_array_elements(p_schema->'enum') WITH ORDINALITY
         WHERE jsonb_typeof(value) <> 'null';
        IF v_template IS NOT NULL THEN RETURN v_template; END IF;
    END IF;

    SELECT t INTO v_type
      FROM unnest(rvbbit._clover_extract_schema_types(p_schema)) t
     WHERE t <> 'null'
     LIMIT 1;

    CASE v_type
        WHEN 'object' THEN
            SELECT coalesce(jsonb_object_agg(key, rvbbit._clover_extract_template(value)), '{}'::jsonb)
              INTO v_template FROM jsonb_each(p_schema->'properties');
            RETURN v_template;
        WHEN 'array' THEN
            RETURN jsonb_build_array(rvbbit._clover_extract_template(p_schema->'items'));
        WHEN 'boolean' THEN RETURN 'true'::jsonb;
        WHEN 'integer' THEN RETURN '0'::jsonb;
        WHEN 'number' THEN RETURN '0.0'::jsonb;
        WHEN 'string' THEN
            v_descriptor := CASE p_schema->>'format'
                WHEN 'date' THEN 'YYYY-MM-DD'
                WHEN 'date-time' THEN 'ISO-8601 date-time'
                ELSE 'verbatim-string'
            END;
            IF v_description IS NOT NULL THEN
                v_descriptor := v_descriptor || '; ' || left(v_description, 240);
            END IF;
            RETURN to_jsonb(v_descriptor);
        ELSE RETURN 'null'::jsonb;
    END CASE;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_receipt(
    p_operator text,
    p_request_id uuid
) RETURNS jsonb
LANGUAGE sql STABLE PARALLEL UNSAFE
AS $fn$
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
    ))
    FROM rvbbit.receipts r
    WHERE r.operator = p_operator
      AND r.inputs->>'request_id' = p_request_id::text
    ORDER BY r.invocation_at DESC
    LIMIT 1
$fn$;

-- Mirror the managed generalist's endpoint/auth into a dedicated extraction
-- route for existing installations. A fresh managed/clover install registers
-- this backend explicitly as well.
INSERT INTO rvbbit.backends(
    name, transport, endpoint_url, batch_size, max_concurrent, timeout_ms,
    auth_header_env, transport_opts, description, source_provider,
    source_model, source_revision, install_manifest
)
SELECT
    'clover_extract_llm', b.transport, b.endpoint_url, 1, 8, 900000,
    b.auth_header_env,
    jsonb_build_object(
        'model', 'clover-extract',
        'model_policy', 'managed',
        'model_aliases', jsonb_build_array('nuextract3'),
        'model_allowlist', jsonb_build_array('clover-extract', 'nuextract3')
    ),
    'Clover contract-extraction specialist route.',
    'numind', 'NuExtract3-W4A16', 'b5028670152c8130a3f362b66981eee16612b7f6',
    jsonb_build_object('capability', 'managed/clover', 'backend', 'clover_extract_llm')
FROM rvbbit.backends b
WHERE b.name = 'clover_llm'
ON CONFLICT (name) DO UPDATE SET
    transport = EXCLUDED.transport,
    endpoint_url = EXCLUDED.endpoint_url,
    batch_size = EXCLUDED.batch_size,
    max_concurrent = EXCLUDED.max_concurrent,
    timeout_ms = EXCLUDED.timeout_ms,
    auth_header_env = EXCLUDED.auth_header_env,
    transport_opts = EXCLUDED.transport_opts,
    description = EXCLUDED.description,
    source_provider = EXCLUDED.source_provider,
    source_model = EXCLUDED.source_model,
    source_revision = EXCLUDED.source_revision,
    install_manifest = EXCLUDED.install_manifest;

SELECT rvbbit.create_operator(
    op_name        => '_clover_extract_primary',
    op_arg_names   => ARRAY['text', 'template', 'instructions', 'request_id'],
    op_return_type => 'jsonb',
    op_parser      => 'json',
    op_max_tokens  => 4096,
    op_temperature => 0.1,
    op_description => 'Internal constrained extraction attempt; use clover_extract_json.',
    op_steps       => jsonb_build_array(jsonb_build_object(
        'name', 'extract',
        'kind', 'llm',
        'provider', 'clover_extract_llm',
        'model', 'clover-extract',
        'user', '{{ inputs.text }}',
        'chat_template', '{{ inputs.template }}',
        'chat_template_instructions', '{{ inputs.instructions }}',
        'enable_thinking', false,
        'max_tokens', 4096,
        'temperature', 0.1
    ))
);

SELECT rvbbit.create_operator(
    op_name        => '_clover_extract_recovery',
    op_arg_names   => ARRAY['text', 'contract', 'instructions', 'rejected', 'errors', 'request_id'],
    op_return_type => 'jsonb',
    op_parser      => 'json',
    op_max_tokens  => 4096,
    op_temperature => 0.1,
    op_description => 'Internal generalist recovery attempt; use clover_extract_json.',
    op_steps       => jsonb_build_array(jsonb_build_object(
        'name', 'recover',
        'kind', 'llm',
        'provider', 'clover_llm',
        'model', 'clover',
        'system', 'You are a strict structured-data extraction and repair engine. Return one compact JSON object only. Never use Markdown. Never invent absent facts.',
        'user', E'Extract TEXT into the supplied JSON SCHEMA. Every required field must be present. Preserve exact JSON types and enum values. Use null only when the schema permits null. Obey the extra instructions. A specialist attempt and deterministic validation errors may be supplied; repair them from TEXT rather than trusting the rejected values.\n\nSCHEMA:\n{{ inputs.contract }}\n\nEXTRA INSTRUCTIONS:\n{{ inputs.instructions }}\n\nREJECTED ATTEMPT:\n{{ inputs.rejected }}\n\nVALIDATION ERRORS:\n{{ inputs.errors }}\n\nTEXT:\n{{ inputs.text }}\n\nReturn one JSON object only.',
        'enable_thinking', false,
        'max_tokens', 4096,
        'temperature', 0.1
    ))
);

UPDATE rvbbit.operators
SET cache_policy = 'never'
WHERE name IN ('_clover_extract_primary', '_clover_extract_recovery');

SELECT rvbbit.reload_backends();

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_primary(text, text, text, text, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_recovery(text, text, text, text, text, text, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_receipt(text, uuid) FROM PUBLIC;

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

COMMENT ON FUNCTION rvbbit.clover_extract_json(text, jsonb, jsonb) IS
    'Extract text into a JSON Schema-shaped contract. Returns explicit status, validated data, attempt receipts, route provenance, and only caches validated successes.';

-- Row-at-a-time durable jobs. Each clover_extract_step call is one transaction:
-- successful rows survive client restarts, while concurrent callers skip rows
-- already locked by another worker.
CREATE TABLE IF NOT EXISTS rvbbit.clover_extract_jobs (
    job_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_name      text NOT NULL,
    job_key         text,
    items_hash      text NOT NULL,
    contract        jsonb NOT NULL,
    policy          jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'completed_with_review')),
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at    timestamptz,
    UNIQUE(owner_name, job_key)
);

CREATE TABLE IF NOT EXISTS rvbbit.clover_extract_items (
    job_id          uuid NOT NULL REFERENCES rvbbit.clover_extract_jobs(job_id) ON DELETE CASCADE,
    item_key        text NOT NULL,
    source_text     text NOT NULL,
    status          text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'review', 'failed')),
    attempts        integer NOT NULL DEFAULT 0,
    worker_name     text,
    result          jsonb,
    last_error      text,
    started_at      timestamptz,
    finished_at     timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(job_id, item_key)
);

CREATE INDEX IF NOT EXISTS clover_extract_items_claim_idx
    ON rvbbit.clover_extract_items(job_id, status, item_key)
    WHERE status IN ('queued', 'running');

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_enqueue(
    p_items jsonb,
    p_contract jsonb,
    p_policy jsonb DEFAULT '{}'::jsonb,
    p_job_key text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_job_id uuid;
    v_owner text := session_user;
    v_contract_errors jsonb;
    v_policy_errors jsonb;
    v_item jsonb;
    v_count integer;
    v_distinct integer;
    v_items_hash text;
BEGIN
    v_contract_errors := rvbbit._clover_extract_contract_errors(p_contract);
    IF jsonb_array_length(v_contract_errors) > 0 THEN
        RAISE EXCEPTION 'invalid extraction contract: %', v_contract_errors::text;
    END IF;
    v_policy_errors := rvbbit._clover_extract_policy_errors(coalesce(p_policy, '{}'::jsonb));
    IF jsonb_array_length(v_policy_errors) > 0 THEN
        RAISE EXCEPTION 'invalid extraction policy: %', v_policy_errors::text;
    END IF;
    IF jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'items must be a non-empty JSON array of {id,text} objects';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR jsonb_typeof(v_item->'id') <> 'string'
           OR btrim(v_item->>'id') = ''
           OR jsonb_typeof(v_item->'text') <> 'string' THEN
            RAISE EXCEPTION 'each extraction item must be an object with non-empty string id and string text';
        END IF;
    END LOOP;
    SELECT count(*), count(DISTINCT value->>'id')
      INTO v_count, v_distinct
      FROM jsonb_array_elements(p_items);
    IF v_count <> v_distinct THEN
        RAISE EXCEPTION 'extraction item ids must be unique within a job';
    END IF;
    SELECT md5(jsonb_agg(value ORDER BY value->>'id')::text)
      INTO v_items_hash
      FROM jsonb_array_elements(p_items);

    IF nullif(btrim(p_job_key), '') IS NOT NULL THEN
        SELECT job_id INTO v_job_id
          FROM rvbbit.clover_extract_jobs
         WHERE owner_name = v_owner AND job_key = btrim(p_job_key);
        IF v_job_id IS NOT NULL THEN
            IF EXISTS (
                SELECT 1 FROM rvbbit.clover_extract_jobs
                WHERE job_id = v_job_id
                  AND (items_hash IS DISTINCT FROM v_items_hash
                       OR contract IS DISTINCT FROM p_contract
                       OR policy IS DISTINCT FROM coalesce(p_policy, '{}'::jsonb))
            ) THEN
                RAISE EXCEPTION 'job_key % already exists with different items, contract, or policy', p_job_key;
            END IF;
            RETURN v_job_id;
        END IF;
    END IF;

    INSERT INTO rvbbit.clover_extract_jobs(owner_name, job_key, items_hash, contract, policy)
    VALUES (v_owner, nullif(btrim(p_job_key), ''), v_items_hash,
            p_contract, coalesce(p_policy, '{}'::jsonb))
    RETURNING job_id INTO v_job_id;

    INSERT INTO rvbbit.clover_extract_items(job_id, item_key, source_text)
    SELECT v_job_id, value->>'id', value->>'text'
    FROM jsonb_array_elements(p_items);

    RETURN v_job_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_refresh_job(p_job_id uuid)
RETURNS text
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_status text;
BEGIN
    SELECT CASE
        WHEN count(*) FILTER (WHERE status IN ('queued', 'running')) > 0 THEN 'running'
        WHEN count(*) FILTER (WHERE status IN ('review', 'failed')) > 0 THEN 'completed_with_review'
        ELSE 'completed'
    END INTO v_status
    FROM rvbbit.clover_extract_items
    WHERE job_id = p_job_id;

    UPDATE rvbbit.clover_extract_jobs
       SET status = v_status,
           updated_at = clock_timestamp(),
           completed_at = CASE WHEN v_status LIKE 'completed%' THEN clock_timestamp() ELSE NULL END
     WHERE job_id = p_job_id;
    RETURN v_status;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_step(
    p_job_id uuid,
    p_worker_name text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_owner text := session_user;
    v_job rvbbit.clover_extract_jobs%ROWTYPE;
    v_item rvbbit.clover_extract_items%ROWTYPE;
    v_result jsonb;
    v_item_status text;
    v_job_status text;
    v_max_attempts integer;
BEGIN
    SELECT * INTO v_job FROM rvbbit.clover_extract_jobs
    WHERE job_id = p_job_id AND owner_name = v_owner;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'extraction job % not found for current user', p_job_id;
    END IF;

    v_max_attempts := coalesce((v_job.policy->>'max_attempts')::integer, 3);

    WITH candidate AS (
        SELECT job_id, item_key
        FROM rvbbit.clover_extract_items
        WHERE job_id = p_job_id
          AND status = 'queued'
        ORDER BY item_key
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE rvbbit.clover_extract_items i
       SET status = 'running',
           attempts = i.attempts + 1,
           worker_name = coalesce(nullif(btrim(p_worker_name), ''), 'pid:' || pg_backend_pid()),
           started_at = coalesce(i.started_at, clock_timestamp()),
           updated_at = clock_timestamp()
      FROM candidate c
     WHERE i.job_id = c.job_id AND i.item_key = c.item_key
    RETURNING i.* INTO v_item;

    IF NOT FOUND THEN
        v_job_status := rvbbit._clover_extract_refresh_job(p_job_id);
        RETURN jsonb_build_object(
            'job_id', p_job_id,
            'status', 'idle',
            'job_status', v_job_status,
            'message', 'no queued item was available'
        );
    END IF;

    BEGIN
        v_result := rvbbit.clover_extract_json(v_item.source_text, v_job.contract, v_job.policy - 'max_attempts');
    EXCEPTION WHEN OTHERS THEN
        v_result := jsonb_build_object(
            'status', 'error', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors',
                jsonb_build_array(rvbbit._clover_extract_error('$', 'runner_exception', SQLERRM))),
            'attempts', '[]'::jsonb
        );
    END;

    v_item_status := CASE v_result->>'status'
        WHEN 'ok' THEN 'succeeded'
        WHEN 'rejected' THEN 'review'
        WHEN 'invalid_contract' THEN 'failed'
        WHEN 'invalid_policy' THEN 'failed'
        WHEN 'invalid_input' THEN 'failed'
        WHEN 'input_too_large' THEN 'failed'
        ELSE CASE WHEN v_item.attempts < v_max_attempts THEN 'queued' ELSE 'failed' END
    END;

    UPDATE rvbbit.clover_extract_items
       SET status = v_item_status,
           result = v_result,
           last_error = CASE WHEN v_result->>'status' = 'ok' THEN NULL
                             ELSE coalesce(v_result #>> '{validation,errors,0,message}', v_result->>'status') END,
           finished_at = CASE WHEN v_item_status IN ('succeeded', 'review', 'failed')
                              THEN clock_timestamp() ELSE NULL END,
           updated_at = clock_timestamp()
     WHERE job_id = p_job_id AND item_key = v_item.item_key;

    v_job_status := rvbbit._clover_extract_refresh_job(p_job_id);
    RETURN jsonb_build_object(
        'job_id', p_job_id,
        'item_key', v_item.item_key,
        'item_status', v_item_status,
        'job_status', v_job_status,
        'attempt', v_item.attempts,
        'result', v_result
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_retry(
    p_job_id uuid,
    p_item_keys text[] DEFAULT NULL
) RETURNS integer
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_count integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.clover_extract_jobs
        WHERE job_id = p_job_id AND owner_name = session_user
    ) THEN
        RAISE EXCEPTION 'extraction job % not found for current user', p_job_id;
    END IF;
    UPDATE rvbbit.clover_extract_items
       SET status = 'queued', attempts = 0, worker_name = NULL,
           result = NULL, last_error = NULL, finished_at = NULL, updated_at = clock_timestamp()
     WHERE job_id = p_job_id
       AND status IN ('review', 'failed')
       AND (p_item_keys IS NULL OR item_key = ANY(p_item_keys));
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count > 0 THEN
        UPDATE rvbbit.clover_extract_jobs
           SET status = 'queued', completed_at = NULL, updated_at = clock_timestamp()
         WHERE job_id = p_job_id;
    END IF;
    RETURN v_count;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_job(
    p_job_id uuid,
    p_include_items boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql STABLE PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_job rvbbit.clover_extract_jobs%ROWTYPE;
    v_counts jsonb;
    v_items jsonb;
BEGIN
    SELECT * INTO v_job FROM rvbbit.clover_extract_jobs
    WHERE job_id = p_job_id AND owner_name = session_user;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'extraction job % not found for current user', p_job_id;
    END IF;

    SELECT coalesce(jsonb_object_agg(status, n), '{}'::jsonb)
      INTO v_counts
      FROM (
          SELECT status, count(*) AS n
          FROM rvbbit.clover_extract_items
          WHERE job_id = p_job_id
          GROUP BY status
      ) counts;

    IF p_include_items THEN
        SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
            'id', item_key,
            'status', status,
            'attempts', attempts,
            'data', result->'data',
            'validation', result->'validation',
            'provenance', result->'provenance',
            'last_error', last_error
        )) ORDER BY item_key), '[]'::jsonb)
        INTO v_items
        FROM rvbbit.clover_extract_items
        WHERE job_id = p_job_id;
    END IF;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'job_id', v_job.job_id,
        'job_key', v_job.job_key,
        'status', v_job.status,
        'counts', v_counts,
        'created_at', v_job.created_at,
        'updated_at', v_job.updated_at,
        'completed_at', v_job.completed_at,
        'items', v_items
    ));
END
$fn$;

COMMENT ON FUNCTION rvbbit.clover_extract_enqueue(jsonb, jsonb, jsonb, text) IS
    'Create or idempotently resume a durable contract-extraction job from [{id,text}, ...].';
COMMENT ON FUNCTION rvbbit.clover_extract_step(uuid, text) IS
    'Claim and process one queued extraction item. Concurrent sessions safely skip locked items; call repeatedly to drain a job.';
COMMENT ON FUNCTION rvbbit.clover_extract_retry(uuid, text[]) IS
    'Reset selected review/failed extraction items to queued for an explicit retry.';
COMMENT ON FUNCTION rvbbit.clover_extract_job(uuid, boolean) IS
    'Return durable extraction job status and optionally validated results without returning source text.';

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_refresh_job(uuid) FROM PUBLIC;
