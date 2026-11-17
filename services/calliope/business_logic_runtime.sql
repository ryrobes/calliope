-- The application prepares this connection's authorized, pinned definitions
-- before starting its physically read-only transaction. No telemetry GUC is
-- an identity and this invoker function cannot read the private rule registry.
CREATE OR REPLACE FUNCTION rvbbit._business_logic_frozen_bindings()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'Authorized business logic bindings are immutable for this query' USING ERRCODE='42501';
END
$fn$;

-- Terminal actions belong to the saved definition, never to inference.
CREATE OR REPLACE FUNCTION rvbbit._business_logic_decision_tree(node jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE clean jsonb := node - 'action'; child record; routes jsonb := '{}'::jsonb;
BEGIN
    IF node->>'kind'='branch' THEN
        clean := clean || jsonb_build_object('yes',rvbbit._business_logic_decision_tree(node->'yes'),
                                            'no',rvbbit._business_logic_decision_tree(node->'no'));
    ELSIF node->>'kind'='choice' THEN
        FOR child IN SELECT key,value FROM jsonb_each(node->'routes') LOOP
            routes := routes || jsonb_build_object(child.key,rvbbit._business_logic_decision_tree(child.value));
        END LOOP;
        clean := clean || jsonb_build_object('routes',routes);
    END IF;
    RETURN clean;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_logic(rule text, context jsonb, version integer DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE AS $fn$
DECLARE bound record; answer jsonb; field record; actual text; errors jsonb := '[]'::jsonb;
BEGIN
    IF to_regclass('pg_temp.calliope_logic_bindings') IS NULL THEN
        RAISE EXCEPTION 'Run named business logic through Calliope SQL or run_business_logic; no authorized rule context on this connection'
            USING ERRCODE='42501';
    END IF;
    SELECT b.* INTO bound FROM pg_temp.calliope_logic_bindings b
    WHERE b.rule_key=rule AND (CASE WHEN version IS NULL THEN b.active ELSE b.revision=version END);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Business logic rule or published revision is unavailable: %',rule USING ERRCODE='42501';
    END IF;
    IF context IS NULL OR jsonb_typeof(context)<>'object' OR octet_length(context::text)>24000 THEN
        RAISE EXCEPTION 'Business logic context must be a JSON object of at most 24 KB';
    END IF;
    FOR field IN SELECT key,value FROM jsonb_each(coalesce(bound.snapshot #> '{definition,inputs}','{}'::jsonb)) LOOP
        IF context ? field.key AND context->field.key <> 'null'::jsonb THEN
            actual := jsonb_typeof(context->field.key);
            IF field.value->>'type' <> 'any' AND actual <> field.value->>'type' THEN
                errors := errors || jsonb_build_array(jsonb_build_object('field',field.key,'expected',field.value->>'type','actual',actual));
            END IF;
        END IF;
    END LOOP;
    IF errors <> '[]'::jsonb THEN
        answer := jsonb_build_object('status','invalid_input','label',NULL,'path','[]'::jsonb,'input_errors',errors);
    ELSE
        answer := rvbbit.decision_tree(context,rvbbit._business_logic_decision_tree(bound.snapshot #> '{definition,tree}'),4);
    END IF;
    answer := answer || jsonb_build_object('rule',bound.snapshot - 'definition');
    -- Notices are collected on this connection even when the surrounding SQL
    -- projects just label or aggregates rows. No per-row INSERT in read-only SQL.
    RAISE NOTICE USING MESSAGE = 'calliope_logic:' || jsonb_build_object(
        'run_id',bound.run_id,'input',context,'decision',answer)::text;
    RETURN answer;
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_logic(text,jsonb,integer) IS
 'Evaluate a published named rule against JSON evidence in an authorized Calliope SQL session. Returns status, label, path and exact rule revision. No actions run.';
