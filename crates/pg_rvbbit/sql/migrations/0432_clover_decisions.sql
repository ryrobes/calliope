-- Hosted Jev decisions, using the normal operator L1/L2 receipt cache.
-- Keep model and contract in op_steps: both participate in the cache identity.
-- Updating provider semantics requires a contract bump (or judgment_purge).
DO $install$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM rvbbit.backends WHERE name = 'decisions') THEN
        PERFORM rvbbit.register_backend('decisions',
            'https://clover.rvbb.it/b/decisions/predict', 'rvbbit',
            8, 1, 45000, 'RVBBIT_CLOVER_KEY');
    END IF;
END
$install$;

SELECT rvbbit.create_operator(
    'clover_decide', ARRAY['state', 'questions'], 'jsonb',
    op_arg_types := ARRAY['jsonb', 'jsonb'],
    op_model := 'typesafe/jev-1.13',
    op_parser := 'json',
    op_description := 'Clover Jev decision: bounded state plus named choice/noul questions. Returns validated answers, scores, model, contract and usage. Cached in semantic receipts; NULL on transport failure. Use prewarm_operator for bulk work.',
    op_steps := '[{"name":"decision","kind":"specialist","specialist":"decisions","inputs":{"model":"typesafe/jev-1.13","contract":"clover-decisions-v1","state":"{{state}}","questions":"{{questions}}"}}]'::jsonb
);

-- Typed, deterministic question builders are also usable in prewarm queries.
CREATE OR REPLACE FUNCTION rvbbit.truth_question(predicate text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
BEGIN
    IF btrim(predicate) = '' OR length(predicate) > 2000 THEN
        RAISE EXCEPTION 'predicate must contain 1 to 2000 characters';
    END IF;
    RETURN jsonb_build_object('result', jsonb_build_object(
        'type', 'noul', 'instructions', btrim(predicate)));
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.choice_question(categories jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
BEGIN
    IF jsonb_typeof(categories) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'categories must be an object mapping labels to descriptions';
    END IF;
    IF (SELECT count(*) FROM jsonb_each(categories)) NOT BETWEEN 2 AND 16
       OR EXISTS (SELECT 1 FROM jsonb_each(categories) c WHERE
           btrim(c.key) = '' OR length(c.key) > 80 OR jsonb_typeof(c.value) <> 'string'
           OR btrim(c.value #>> '{}') = '' OR length(c.value #>> '{}') > 2000) THEN
        RAISE EXCEPTION 'provide 2 to 16 named categories, labels 1..80 characters and descriptions 1..2000 characters';
    END IF;
    RETURN jsonb_build_object('result', jsonb_build_object(
        'type', 'choice', 'instructions', 'Choose the category that best describes the supplied state.',
        'criteria', categories));
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.choice_question(categories text[])
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
DECLARE definitions jsonb;
BEGIN
    IF cardinality(categories) NOT BETWEEN 2 AND 16
       OR EXISTS (SELECT 1 FROM unnest(categories) c WHERE c IS NULL OR btrim(c) = '')
       OR (SELECT count(DISTINCT c) FROM unnest(categories) c) <> cardinality(categories) THEN
        RAISE EXCEPTION 'provide 2 to 16 distinct non-empty category labels';
    END IF;
    SELECT jsonb_object_agg(c, c) INTO definitions FROM unnest(categories) c;
    RETURN rvbbit.choice_question(definitions);
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.clover_decide(state text, questions jsonb)
RETURNS jsonb LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.clover_decide(to_jsonb(state), questions)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.truth_score(state jsonb, predicate text)
RETURNS double precision LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT (rvbbit.clover_decide(state, rvbbit.truth_question(predicate)) #>> '{answers,result,noul}')::double precision
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.truth_score(state text, predicate text)
RETURNS double precision LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.truth_score(to_jsonb(state), predicate)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.truth(state jsonb, predicate text, threshold double precision DEFAULT 0.5)
RETURNS boolean LANGUAGE plpgsql STRICT PARALLEL SAFE AS $fn$
BEGIN
    IF NOT threshold BETWEEN 0 AND 1 THEN
        RAISE EXCEPTION 'threshold must be a finite number between 0 and 1';
    END IF;
    RETURN rvbbit.truth_score(state, predicate) > threshold;
END
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.truth(state text, predicate text, threshold double precision DEFAULT 0.5)
RETURNS boolean LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.truth(to_jsonb(state), predicate, threshold)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.choose(state jsonb, categories jsonb)
RETURNS text LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.clover_decide(state, rvbbit.choice_question(categories)) #>> '{answers,result,choice}'
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.choose(state text, categories jsonb)
RETURNS text LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.choose(to_jsonb(state), categories)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.choose(state jsonb, categories text[])
RETURNS text LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.clover_decide(state, rvbbit.choice_question(categories)) #>> '{answers,result,choice}'
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.choose(state text, categories text[])
RETURNS text LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT rvbbit.choose(to_jsonb(state), categories)
$fn$;

COMMENT ON FUNCTION rvbbit.truth(jsonb, text, double precision) IS
'Jev semantic predicate. TRUE when score > threshold; NULL on missing input or failed inference, never a false fallback. Score is model output, not calibrated certainty. Shares clover_decide cache with truth_score.';
COMMENT ON FUNCTION rvbbit.choose(jsonb, jsonb) IS
'Jev classifier: label-to-description map, 2..16 choices. Include other/unclear explicitly if wanted; no hidden abstention. Shares clover_decide cache. NULL on missing input or failed inference.';
COMMENT ON FUNCTION rvbbit.truth_score(jsonb, text) IS
'Jev true score in [0,1], not calibrated certainty. Returns NULL on failure. Use truth_question to prewarm the same cached decision.';
SELECT rvbbit.reload_backends();
