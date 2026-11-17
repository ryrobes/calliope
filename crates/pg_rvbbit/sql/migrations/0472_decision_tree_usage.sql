-- Opt-in, per-invocation accounting. Usage never enters evidence or cache keys.
-- Register the new entry point for existing installations as well as fresh schemas.
CREATE OR REPLACE FUNCTION rvbbit._exec_op_jsonb_with_usage(op_name text, inputs jsonb, opts jsonb)
RETURNS jsonb LANGUAGE c VOLATILE PARALLEL SAFE STRICT
AS '$libdir/pg_rvbbit', '_exec_op_jsonb_with_usage_wrapper';

CREATE OR REPLACE FUNCTION rvbbit.decision_tree(state jsonb, tree jsonb, max_steps integer DEFAULT 16)
RETURNS jsonb LANGUAGE plpgsql STRICT PARALLEL SAFE AS $fn$
DECLARE cursor_state jsonb := rvbbit._decision_tree_prepare(tree,max_steps); request jsonb; decision jsonb; observed jsonb; usage jsonb := '[]'::jsonb;
    capture boolean := coalesce(current_setting('rvbbit.decision_usage',true),'off')='on';
BEGIN
    IF state = 'null'::jsonb THEN RETURN NULL; END IF;
    LOOP
        cursor_state := rvbbit._decision_tree_ready(cursor_state,max_steps);
        EXIT WHEN cursor_state->>'status' <> 'running';
        request := rvbbit._decision_tree_request(state,cursor_state);
        IF request->>'status' = 'missing_input' THEN
            cursor_state := cursor_state || request;
            EXIT;
        END IF;
        IF octet_length((request->'state')::text) > 32768 THEN
            cursor_state := cursor_state || '{"status":"context_limit","error":"state_and_history_exceed_32kib"}'::jsonb;
            EXIT;
        END IF;
        IF capture THEN
            observed := rvbbit._exec_op_jsonb_with_usage('clover_decide',request,'{}'::jsonb);
            decision := observed->'output';
            IF decision='null'::jsonb THEN decision := NULL; END IF;
            usage := usage || jsonb_build_array((observed->'usage') || jsonb_build_object(
                'node',cursor_state->>'location','question',cursor_state #>> '{node,question}'));
        ELSE
            decision := rvbbit.clover_decide(request->'state',request->'questions');
        END IF;
        cursor_state := rvbbit._decision_tree_advance(cursor_state,decision);
    END LOOP;
    RETURN rvbbit._decision_tree_result(cursor_state,tree)
        || CASE WHEN capture THEN jsonb_build_object('usage',usage) ELSE '{}'::jsonb END;
END
$fn$;
