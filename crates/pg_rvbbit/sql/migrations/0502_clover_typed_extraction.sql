-- 0502: typed-field extraction through the Clover gateway.
--
-- OpenRouter's Extractions API (Cloudflare Clairic) reads a text and a set of
-- typed fields (text, number, date, time, datetime, duration, list, estimate,
-- forecast) and answers all of them in one forward pass, each with a
-- confidence, a null probability and the character spans it read. It returns
-- NULL instead of guessing when the text doesn't say. At $0.10 per million
-- input tokens and about half a second it is cheaper and steadier than asking
-- an LLM for JSON, and far lighter than a local extraction model.
--
-- * Backend `extractions` (Hutch route `/b/extractions/predict`, adapter
--   hosted-extractions). Existing installs get it at their `decisions`
--   backend's gateway; the Clover configuration publishes whether the gateway
--   serves it.
-- * clover_extract_fields(state, fields, reference_date): the cached core call
--   (answers with confidence, p_null, spans), and extract_field() to build a
--   field. Typed wrappers return one value or NULL: clover_extract_text,
--   clover_extract_number, clover_extract_date, clover_extract_timestamp,
--   clover_extract_duration, clover_extract_list, clover_estimate.
-- * clover_extract_json gains a `typed` route, tried first on `auto` when the
--   contract's fields are all ones the extractor can type (strings, dates,
--   numbers, integers, lists of those, nested objects of those) and the
--   gateway serves it. Its answers are validated against the contract like any
--   attempt; a rejected one goes to the generalist for repair. Results carry
--   `evidence`: per field, the confidence, null probability and spans.
--   Policy gains reference_date (anchors dates without a year) and
--   null_threshold (the null probability above which a field is NULL).

INSERT INTO rvbbit.backends(
    name, transport, endpoint_url, batch_size, max_concurrent, timeout_ms,
    auth_header_env, transport_opts, description, source_provider,
    source_model, install_manifest
)
SELECT
    'extractions', b.transport,
    regexp_replace(b.endpoint_url, '/b/decisions/predict$', '/b/extractions/predict'),
    16, 4, 60000, b.auth_header_env, '{}'::jsonb,
    'Clover typed-field extraction route (OpenRouter Extractions API).',
    'cloudflare', 'clairic',
    jsonb_build_object('capability', 'managed/clover', 'backend', 'extractions')
FROM rvbbit.backends b
WHERE b.name = 'decisions'
  AND b.endpoint_url ~ '/b/decisions/predict$'
ON CONFLICT (name) DO NOTHING;

SELECT rvbbit.create_operator(
    op_name        => 'clover_extract_fields',
    op_arg_names   => ARRAY['state', 'fields', 'reference_date'],
    op_arg_types   => ARRAY['text', 'jsonb', 'text'],
    op_return_type => 'jsonb',
    op_model       => 'openrouter/cloudflare/clairic',
    op_description => 'Clover typed extraction: every field of a {name: field} object (see extract_field) read from the text in one pass. Returns {answers: {name: {value, confidence, p_null, span|spans, guess, ...}}, truncated, usage}; a value is null when the text does not determine it. reference_date (ISO date, or empty) anchors dates without a year. Cached in semantic receipts; NULL on transport failure.',
    op_steps       => '[{"name":"x","kind":"specialist","specialist":"extractions","inputs":{"state":"{{state}}","fields":"{{fields}}","reference_date":"{{reference_date}}"}}]'::jsonb
);

SELECT rvbbit.create_operator(
    op_name        => '_clover_extract_typed',
    op_arg_names   => ARRAY['state', 'fields', 'reference_date', 'request_id'],
    op_arg_types   => ARRAY['text', 'jsonb', 'text', 'text'],
    op_return_type => 'jsonb',
    op_model       => 'openrouter/cloudflare/clairic',
    op_description => 'Internal typed extraction attempt; use clover_extract_json.',
    op_steps       => '[{"name":"x","kind":"specialist","specialist":"extractions","inputs":{"state":"{{state}}","fields":"{{fields}}","reference_date":"{{reference_date}}"}}]'::jsonb
);

UPDATE rvbbit.operators SET cache_policy = 'never' WHERE name = '_clover_extract_typed';

SELECT rvbbit.reload_backends();

REVOKE EXECUTE ON FUNCTION rvbbit._clover_extract_typed(text, jsonb, text, text, jsonb) FROM PUBLIC;

-- The gateway serves typed extraction. Explicit: an install migrated before
-- its Clover configuration added the route must not send every extraction to
-- a route that answers 404.
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_typed_ready()
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((
        SELECT (b.transport_opts #>> '{clover_route,enabled}') = 'true'
        FROM rvbbit.backends b
        WHERE b.name = 'extractions'
    ), false)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.extract_field(
    kind text,
    instructions text DEFAULT NULL,
    options jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    IF kind IS NULL OR kind NOT IN (
        'text', 'number', 'date', 'time', 'datetime', 'duration', 'list', 'estimate', 'forecast'
    ) THEN
        RAISE EXCEPTION 'field type must be text, number, date, time, datetime, duration, list, estimate or forecast';
    END IF;
    IF options IS NOT NULL AND jsonb_typeof(options) <> 'object' THEN
        RAISE EXCEPTION 'field options must be a JSON object';
    END IF;
    RETURN jsonb_strip_nulls(jsonb_build_object('type', kind, 'instructions', nullif(btrim(instructions), '')))
        || coalesce(options, '{}'::jsonb);
END
$fn$;

-- A field name the extractor accepts that still says what is asked; the model
-- reads names as well as instructions.
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_field_name(what text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT CASE
        WHEN name = '' THEN 'value'
        WHEN name ~ '^[A-Za-z_]' THEN name
        ELSE '_' || name
    END
    FROM (
        SELECT left(btrim(regexp_replace(lower(coalesce(what, '')), '[^a-z0-9]+', '_', 'g'), '_'), 63) AS name
    ) n
$fn$;

-- One field, one answer: the wrappers below share clover_extract_fields' cache.
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_one(
    state text, field jsonb, what text, reference_date text
) RETURNS jsonb
LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT rvbbit.clover_extract_fields(
        state,
        jsonb_build_object(rvbbit._clover_extract_field_name(what), field),
        coalesce(reference_date, '')
    ) #> ARRAY['answers', rvbbit._clover_extract_field_name(what)]
    WHERE btrim(state) <> ''
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_text(state text, what text)
RETURNS text LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit._clover_extract_one(state, rvbbit.extract_field('text', what), what, '') ->> 'value'
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_number(state text, what text, unit text DEFAULT NULL)
RETURNS numeric LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT (rvbbit._clover_extract_one(state,
        rvbbit.extract_field('number', what, jsonb_strip_nulls(jsonb_build_object('unit', unit))),
        what, '') ->> 'value')::numeric
    WHERE state IS NOT NULL AND what IS NOT NULL
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_date(state text, what text, reference_date date DEFAULT current_date)
RETURNS date LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT (rvbbit._clover_extract_one(state, rvbbit.extract_field('date', what),
        what, reference_date::text) ->> 'value')::date
    WHERE state IS NOT NULL AND what IS NOT NULL
$fn$;

-- The extractor reports wall-clock times without a zone, so this is a timestamp.
CREATE OR REPLACE FUNCTION rvbbit.clover_extract_timestamp(state text, what text, reference_date date DEFAULT current_date)
RETURNS timestamp LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT (rvbbit._clover_extract_one(state, rvbbit.extract_field('datetime', what),
        what, reference_date::text) ->> 'value')::timestamp
    WHERE state IS NOT NULL AND what IS NOT NULL
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_duration(state text, what text, unit text DEFAULT 'days')
RETURNS numeric LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT (rvbbit._clover_extract_one(state,
        rvbbit.extract_field('duration', what, jsonb_build_object('unit', coalesce(unit, 'days'))),
        what, '') ->> 'value')::numeric
    WHERE state IS NOT NULL AND what IS NOT NULL
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_extract_list(state text, what text, items text DEFAULT 'text')
RETURNS text[] LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT ARRAY(SELECT jsonb_array_elements_text(answer -> 'value'))
    FROM (
        SELECT rvbbit._clover_extract_one(state,
            rvbbit.extract_field('list', what, jsonb_build_object('items', coalesce(items, 'text'))),
            what, current_date::text) AS answer
    ) a
    WHERE state IS NOT NULL AND what IS NOT NULL AND jsonb_typeof(answer -> 'value') = 'array'
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_estimate(
    state text, what text, min numeric, max numeric, scale text DEFAULT 'linear'
) RETURNS jsonb
LANGUAGE sql PARALLEL SAFE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'value', answer -> 'value', 'median', answer -> 'median', 'mean', answer -> 'mean',
        'p10', answer -> 'p10', 'p90', answer -> 'p90',
        'p_below', answer -> 'p_below', 'p_above', answer -> 'p_above', 'p_null', answer -> 'p_null'))
    FROM (
        SELECT rvbbit._clover_extract_one(state,
            rvbbit.extract_field('estimate', what, jsonb_build_object(
                'min', min, 'max', max, 'scale', coalesce(scale, 'linear'))),
            what, '') AS answer
    ) a
    WHERE state IS NOT NULL AND what IS NOT NULL AND min IS NOT NULL AND max IS NOT NULL
      AND answer IS NOT NULL
$fn$;

COMMENT ON FUNCTION rvbbit.extract_field(text, text, jsonb) IS
'One field for clover_extract_fields: type (text, number, date, time, datetime, duration, list, estimate, forecast), what to read, and options such as unit, integer, decimals, items (for lists), min/max/scale (for estimates and forecasts), horizon/interval (forecasts), null_threshold.';
COMMENT ON FUNCTION rvbbit.clover_extract_text(text, text) IS
'The span of the text that answers what, or NULL when the text does not say. Shares clover_extract_fields'' cache.';
COMMENT ON FUNCTION rvbbit.clover_extract_number(text, text, text) IS
'The number the text gives for what (optionally in unit), or NULL when it does not say. Reading is strong; multi-step arithmetic is not: compute in SQL from extracted operands.';
COMMENT ON FUNCTION rvbbit.clover_extract_date(text, text, date) IS
'The date the text gives for what, years filled in from reference_date, or NULL when it does not say. Relative dates (yesterday, next week) are not reliable.';
COMMENT ON FUNCTION rvbbit.clover_extract_timestamp(text, text, date) IS
'The wall-clock date and time the text gives for what (no time zone), or NULL.';
COMMENT ON FUNCTION rvbbit.clover_extract_duration(text, text, text) IS
'The length of time the text gives for what, in unit (seconds, minutes, hours, days, weeks, months, years), or NULL.';
COMMENT ON FUNCTION rvbbit.clover_extract_list(text, text, text) IS
'Every item the text lists for what (items: text, number or date), as text; NULL when the call fails. Check confidence with clover_extract_fields before trusting a long list.';
COMMENT ON FUNCTION rvbbit.clover_estimate(text, text, numeric, numeric, text) IS
'An estimate of what between min and max from the text: {value, median, mean, p10, p90, p_below, p_above}. For uncertain quantities; the band is the extractor''s, measured rather than guaranteed calibration.';

-- The extractor's fields for a contract, or NULL when it uses a type the
-- extractor cannot fill: booleans, enums (values usually aren't verbatim in
-- the text, and text answers are spans of it), arrays of objects or mixed
-- types. {fields: {name: field}, leaves: {name: {path, required, nullable}}}
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_typed_plan(
    p_schema jsonb,
    p_path text[] DEFAULT ARRAY[]::text[],
    p_required boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE
    v_types text[] := rvbbit._clover_extract_schema_types(p_schema);
    v_kinds text[];
    v_kind text;
    v_prop record;
    v_sub jsonb;
    v_fields jsonb := '{}'::jsonb;
    v_leaves jsonb := '{}'::jsonb;
    v_field jsonb;
    v_items jsonb;
    v_item_kinds text[];
    v_name text;
    v_required text[];
BEGIN
    v_kinds := array_remove(v_types, 'null');
    IF cardinality(v_kinds) <> 1 OR p_schema ? 'enum' THEN
        RETURN NULL;
    END IF;
    v_kind := v_kinds[1];
    IF v_kind = 'object' THEN
        IF jsonb_typeof(p_schema->'properties') <> 'object'
           OR NOT EXISTS (SELECT 1 FROM jsonb_object_keys(p_schema->'properties')) THEN
            RETURN NULL;
        END IF;
        v_required := ARRAY(SELECT jsonb_array_elements_text(coalesce(p_schema->'required', '[]'::jsonb)));
        FOR v_prop IN SELECT key, value FROM jsonb_each(p_schema->'properties') LOOP
            v_sub := rvbbit._clover_extract_typed_plan(
                v_prop.value, p_path || v_prop.key, p_required AND v_prop.key = ANY (v_required));
            IF v_sub IS NULL OR v_fields ?| ARRAY(SELECT jsonb_object_keys(v_sub->'fields')) THEN
                RETURN NULL;
            END IF;
            v_fields := v_fields || (v_sub->'fields');
            v_leaves := v_leaves || (v_sub->'leaves');
        END LOOP;
        RETURN jsonb_build_object('fields', v_fields, 'leaves', v_leaves);
    END IF;
    IF cardinality(p_path) = 0 THEN
        RETURN NULL;
    END IF;

    IF v_kind = 'string' THEN
        v_field := jsonb_build_object('type', CASE p_schema->>'format'
            WHEN 'date' THEN 'date' WHEN 'date-time' THEN 'datetime' ELSE 'text' END);
    ELSIF v_kind = 'number' THEN
        v_field := jsonb_build_object('type', 'number');
    ELSIF v_kind = 'integer' THEN
        v_field := jsonb_build_object('type', 'number', 'integer', true);
    ELSIF v_kind = 'array' THEN
        v_items := p_schema->'items';
        v_item_kinds := array_remove(rvbbit._clover_extract_schema_types(v_items), 'null');
        IF cardinality(v_item_kinds) <> 1 OR v_items ? 'enum' THEN
            RETURN NULL;
        END IF;
        v_field := jsonb_build_object('type', 'list', 'items', CASE
            WHEN v_item_kinds[1] = 'string' AND v_items->>'format' = 'date' THEN 'date'
            WHEN v_item_kinds[1] = 'string' AND v_items ? 'format' THEN NULL
            WHEN v_item_kinds[1] = 'string' THEN 'text'
            WHEN v_item_kinds[1] IN ('number', 'integer') THEN 'number'
        END);
        IF v_field->>'items' IS NULL THEN
            RETURN NULL;
        END IF;
    ELSE
        RETURN NULL;
    END IF;
    IF p_schema ? 'description' THEN
        v_field := v_field || jsonb_build_object('instructions', p_schema->>'description');
    END IF;

    SELECT left(string_agg(regexp_replace(k, '[^A-Za-z0-9_-]+', '_', 'g'), '.' ORDER BY o), 64)
      INTO v_name
      FROM unnest(p_path) WITH ORDINALITY AS p(k, o);
    IF v_name !~ '^[A-Za-z_]' THEN
        v_name := left('_' || v_name, 64);
    END IF;
    RETURN jsonb_build_object(
        'fields', jsonb_build_object(v_name, v_field),
        'leaves', jsonb_build_object(v_name, jsonb_build_object(
            'path', to_jsonb(p_path),
            'required', p_required,
            'nullable', 'null' = ANY (v_types)))
    );
END
$fn$;

-- Put typed answers where the contract wants them. A value the text doesn't
-- determine is left out of an optional field and set to null in a required
-- one (validation then rejects it unless the contract allows null).
CREATE OR REPLACE FUNCTION rvbbit._clover_extract_typed_data(p_answers jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE
    v_data jsonb := '{}'::jsonb;
    v_evidence jsonb := '{}'::jsonb;
    v_leaf record;
    v_answer jsonb;
    v_value jsonb;
    v_path text[];
    v_i integer;
BEGIN
    FOR v_leaf IN SELECT key, value FROM jsonb_each(p_plan->'leaves') ORDER BY key LOOP
        v_answer := p_answers->v_leaf.key;
        v_value := coalesce(v_answer->'value', 'null'::jsonb);
        v_path := ARRAY(SELECT jsonb_array_elements_text(v_leaf.value->'path'));
        v_evidence := v_evidence || jsonb_build_object(array_to_string(v_path, '.'), jsonb_strip_nulls(
            jsonb_build_object(
                'confidence', v_answer->'confidence',
                'p_null', v_answer->'p_null',
                'span', v_answer->'span',
                'spans', v_answer->'spans',
                'guess', CASE WHEN v_value = 'null'::jsonb THEN v_answer->'guess' END)));
        IF v_value = 'null'::jsonb AND NOT (v_leaf.value->>'required')::boolean THEN
            CONTINUE;
        END IF;
        FOR v_i IN 1 .. cardinality(v_path) - 1 LOOP
            IF jsonb_typeof(v_data #> v_path[1:v_i]) IS DISTINCT FROM 'object' THEN
                v_data := jsonb_set(v_data, v_path[1:v_i], '{}'::jsonb, true);
            END IF;
        END LOOP;
        v_data := jsonb_set(v_data, v_path, v_value, true);
    END LOOP;
    RETURN jsonb_build_object('data', v_data, 'evidence', v_evidence);
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_policy_errors(p_policy jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 IMMUTABLE PARALLEL SAFE
AS $function$
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
            'max_input_chars', 'max_attempts', 'reference_date', 'null_threshold'
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
        OR v_policy->>'route' NOT IN ('auto', 'typed', 'specialist', 'generalist')
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy.route', 'enum', 'route must be auto, typed, specialist, or generalist'));
    END IF;
    IF v_policy ? 'reference_date' AND (
        jsonb_typeof(v_policy->'reference_date') <> 'string'
        OR v_policy->>'reference_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy.reference_date', 'format', 'reference_date must be an ISO date (YYYY-MM-DD)'));
    END IF;
    IF v_policy ? 'null_threshold' AND (
        jsonb_typeof(v_policy->'null_threshold') <> 'number'
        OR NOT ((v_policy->>'null_threshold')::numeric > 0 AND (v_policy->>'null_threshold')::numeric <= 1)
    ) THEN
        v_errors := v_errors || jsonb_build_array(rvbbit._clover_extract_error(
            '$.policy.null_threshold', 'range', 'null_threshold must be a number above 0 and at most 1'));
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
$function$;

CREATE OR REPLACE FUNCTION rvbbit._clover_extract_json_v1(p_text text, p_contract jsonb, p_policy jsonb DEFAULT '{}'::jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'rvbbit'
AS $function$
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
    v_plan jsonb;
    v_typed jsonb;
    v_fields jsonb;
    v_evidence jsonb;
    v_first_route text;
    v_reference_date text;
BEGIN
    v_policy_errors := rvbbit._clover_extract_policy_errors(v_policy);
    IF jsonb_array_length(v_policy_errors) > 0 THEN
        RETURN jsonb_build_object('status', 'invalid_policy', 'data', NULL,
            'validation', jsonb_build_object('valid', false, 'errors', v_policy_errors),
            'attempts', '[]'::jsonb,
            'provenance', jsonb_build_object('model_calls', 0));
    END IF;
    v_route := coalesce(nullif(v_policy->>'route', ''), 'auto');
    v_reference_date := coalesce(v_policy->>'reference_date', '');
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

    -- Typed single-pass extraction goes first when the gateway serves it and
    -- every field of the contract is one it can type.
    IF v_route IN ('auto', 'typed') THEN
        v_plan := rvbbit._clover_extract_typed_plan(p_contract);
        IF v_plan IS NULL OR NOT rvbbit._clover_extract_typed_ready() THEN
            IF v_route = 'typed' THEN
                RETURN jsonb_build_object('status', 'error', 'data', NULL,
                    'validation', jsonb_build_object('valid', false, 'errors', jsonb_build_array(
                        rvbbit._clover_extract_error('$', 'typed_unavailable', CASE
                            WHEN v_plan IS NULL THEN 'the contract has fields the typed extractor cannot fill (booleans, enums, arrays of objects or mixed types)'
                            ELSE 'this Clover gateway does not serve typed extraction' END))),
                    'attempts', '[]'::jsonb,
                    'provenance', jsonb_build_object('route', 'typed', 'model_calls', 0));
            END IF;
            v_plan := NULL;
        END IF;
    END IF;
    -- A route the installation marked unconfigured cannot answer; 'auto' goes straight
    -- to the generalist instead of logging a failed specialist attempt on every call.
    IF v_route = 'auto' AND v_plan IS NULL AND NOT rvbbit._clover_extract_specialist_ready() THEN
        v_route := 'generalist';
    END IF;

    IF v_plan IS NOT NULL THEN
        v_first_route := 'typed';
        v_request_id := gen_random_uuid();
        v_candidate := NULL;
        v_typed := NULL;
        v_error := NULL;
        v_validation := NULL;
        v_fields := v_plan->'fields';
        IF v_policy ? 'null_threshold' THEN
            SELECT jsonb_object_agg(key, value || jsonb_build_object('null_threshold', v_policy->'null_threshold'))
              INTO v_fields
              FROM jsonb_each(v_fields);
        END IF;
        BEGIN
            v_typed := rvbbit._clover_extract_typed(
                p_text, v_fields, v_reference_date, v_request_id::text);
        EXCEPTION WHEN OTHERS THEN
            v_error := SQLERRM;
        END;
        v_receipt := rvbbit._clover_extract_receipt('_clover_extract_typed', v_request_id);
        IF coalesce((v_typed->>'truncated')::boolean, false) THEN
            v_error := 'the text is longer than the typed extractor reads';
        ELSIF jsonb_typeof(v_typed->'answers') = 'object' THEN
            v_typed := rvbbit._clover_extract_typed_data(v_typed->'answers', v_plan);
            v_candidate := v_typed->'data';
            v_evidence := v_typed->'evidence';
        END IF;
        IF v_candidate IS NOT NULL THEN
            v_validation := rvbbit.clover_extract_validate(v_candidate, p_contract);
            v_errors := v_validation->'errors';
        ELSE
            v_errors := jsonb_build_array(rvbbit._clover_extract_error(
                '$', 'no_output', coalesce(v_error, 'typed extractor returned SQL NULL')));
        END IF;
        v_attempts := v_attempts || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
            'route', 'typed',
            'request_id', v_request_id,
            'valid', coalesce((v_validation->>'valid')::boolean, false),
            'errors', v_errors,
            'evidence', v_evidence,
            'receipt', v_receipt,
            'error', v_error
        )));

        IF coalesce((v_validation->>'valid')::boolean, false) THEN
            v_result := jsonb_build_object(
                'status', 'ok',
                'data', v_candidate,
                'evidence', v_evidence,
                'validation', v_validation,
                'attempts', v_attempts,
                'provenance', jsonb_build_object(
                    'route', 'typed', 'cache_hit', false,
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
        v_rejected := v_rejected || jsonb_build_object('typed', v_candidate);
        IF v_route = 'typed'
           OR (v_candidate IS NULL AND NOT v_fallback_on_error)
           OR (v_candidate IS NOT NULL AND NOT v_fallback_on_rejection) THEN
            v_result := jsonb_build_object('status', CASE WHEN v_candidate IS NULL THEN 'error' ELSE 'rejected' END,
                'data', NULL,
                'validation', jsonb_build_object('valid', false, 'errors', v_errors),
                'attempts', v_attempts,
                'provenance', jsonb_build_object('route', 'typed', 'cache_hit', false,
                    'model_calls', jsonb_array_length(v_attempts),
                    'input_hash_md5', v_input_hash, 'contract_hash_md5', v_contract_hash));
            IF v_include_rejected THEN
                v_result := v_result || jsonb_build_object('rejected_data', v_rejected);
            END IF;
            RETURN v_result;
        END IF;
    ELSIF v_route <> 'generalist' THEN
        v_first_route := 'specialist';
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
            'route', CASE WHEN v_route = 'generalist' THEN 'generalist'
                          ELSE coalesce(v_first_route, 'specialist') || '_then_generalist' END,
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
$function$;
