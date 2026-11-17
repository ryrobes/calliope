-- Restore functions that managed Clover installs reverted to older copies.
-- The Clover pack's install SQL re-created these after the schema, so installs
-- that applied it lost 0471 (decision-tree `inputs` binding), 0472 (opt-in
-- decision usage) and 0455's capability-index staleness check. The pack is now
-- synced and guarded by tests/test_capability_pack_contract.py; this re-applies
-- the latest definitions for databases it already touched. Idempotent.

-- _decision_tree_validate(jsonb, integer) from 0471_decision_tree_inputs
CREATE OR REPLACE FUNCTION rvbbit._decision_tree_validate(node jsonb, depth integer DEFAULT 0)
RETURNS integer LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE child jsonb; total integer := 1; allowed text[];
BEGIN
    IF node IS NULL OR jsonb_typeof(node) <> 'object' OR depth > 16 THEN
        RAISE EXCEPTION 'decision tree: expected a node object, maximum depth is 16';
    END IF;
    CASE node->>'kind'
    WHEN 'leaf' THEN
        allowed := ARRAY['kind','label'];
        IF jsonb_typeof(node->'label') IS DISTINCT FROM 'string'
           OR btrim(node->>'label') = '' OR length(node->>'label') > 200 THEN
            RAISE EXCEPTION 'decision tree: leaf label must contain 1..200 characters';
        END IF;
    WHEN 'branch' THEN
        allowed := ARRAY['kind','question','yes','no','threshold','inputs'];
        IF jsonb_typeof(node->'threshold') IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION 'decision tree: threshold must be numeric';
        END IF;
        IF NOT (node->>'threshold')::numeric BETWEEN 0 AND 1 THEN
            RAISE EXCEPTION 'decision tree: threshold must be between 0 and 1';
        END IF;
        total := total + rvbbit._decision_tree_validate(node->'yes', depth+1)
                       + rvbbit._decision_tree_validate(node->'no', depth+1);
    WHEN 'choice' THEN
        allowed := ARRAY['kind','question','choices','routes','inputs'];
        PERFORM rvbbit.choice_question(node->'choices');
        IF node->'choices' IS NULL OR jsonb_typeof(node->'routes') IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'decision tree: choice needs definitions and routes';
        END IF;
        IF (SELECT array_agg(key ORDER BY key) FROM jsonb_each(node->'choices')) IS DISTINCT FROM
           (SELECT array_agg(key ORDER BY key) FROM jsonb_each(node->'routes')) THEN
            RAISE EXCEPTION 'decision tree: every choice must have exactly one matching route';
        END IF;
        FOR child IN SELECT value FROM jsonb_each(node->'routes') LOOP
            total := total + rvbbit._decision_tree_validate(child, depth+1);
            IF total > 255 THEN RAISE EXCEPTION 'decision tree: maximum 255 nodes'; END IF;
        END LOOP;
    ELSE RAISE EXCEPTION 'decision tree: unknown node kind';
    END CASE;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(node) key WHERE NOT key = ANY(allowed)) THEN
        RAISE EXCEPTION 'decision tree: unsupported node field';
    END IF;
    IF node->>'kind' <> 'leaf' AND (jsonb_typeof(node->'question') IS DISTINCT FROM 'string'
       OR btrim(node->>'question') = '' OR length(node->>'question') > 1800) THEN
        RAISE EXCEPTION 'decision tree: question must contain 1..1800 characters';
    END IF;
    IF node ? 'inputs' THEN
        IF jsonb_typeof(node->'inputs') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'decision tree: inputs must be an array of field names';
        END IF;
        IF jsonb_array_length(node->'inputs') NOT BETWEEN 1 AND 16
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(node->'inputs') v
                      WHERE jsonb_typeof(v) <> 'string' OR btrim(v #>> '{}') = '' OR length(v #>> '{}') > 80)
           OR (SELECT count(DISTINCT v) FROM jsonb_array_elements(node->'inputs') v) <> jsonb_array_length(node->'inputs') THEN
            RAISE EXCEPTION 'decision tree: inputs must contain 1..16 distinct field names of 1..80 characters';
        END IF;
    END IF;
    IF total > 255 THEN RAISE EXCEPTION 'decision tree: maximum 255 nodes'; END IF;
    RETURN total;
END
$fn$;

-- _decision_tree_request(jsonb, jsonb) from 0471_decision_tree_inputs
CREATE OR REPLACE FUNCTION rvbbit._decision_tree_request(evidence jsonb, cursor_state jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
DECLARE selected_evidence jsonb := evidence; missing jsonb;
BEGIN
    IF cursor_state->'node' ? 'inputs' THEN
        SELECT coalesce(jsonb_agg(key ORDER BY ord), '[]'::jsonb) INTO missing
        FROM jsonb_array_elements_text(cursor_state #> '{node,inputs}') WITH ORDINALITY AS fields(key,ord)
        WHERE jsonb_typeof(evidence) <> 'object' OR NOT evidence ? key;
        IF missing <> '[]'::jsonb THEN
            RETURN jsonb_build_object('status','missing_input','error','required_inputs_missing','missing_inputs',missing);
        END IF;
        SELECT jsonb_object_agg(key,evidence->key) INTO selected_evidence
        FROM jsonb_array_elements_text(cursor_state #> '{node,inputs}') AS fields(key);
    END IF;
    RETURN jsonb_build_object(
        'state', jsonb_build_object('context_version','clover-tree-v1','evidence',selected_evidence,
            'prior_judgments',cursor_state->'judgments'),
        'questions',jsonb_build_object('result',
            jsonb_build_object('type',CASE WHEN cursor_state #>> '{node,kind}' = 'choice' THEN 'choice' ELSE 'noul' END,
                'instructions','Evaluate the question using evidence. Prior judgments are fallible interpretations, not new evidence. Question: ' || (cursor_state #>> '{node,question}'))
            || CASE WHEN cursor_state #>> '{node,kind}' = 'choice'
                THEN jsonb_build_object('criteria',cursor_state #> '{node,choices}') ELSE '{}'::jsonb END));
END
$fn$;

-- _decision_tree_advance(jsonb, jsonb) from 0471_decision_tree_inputs
CREATE OR REPLACE FUNCTION rvbbit._decision_tree_advance(cursor_state jsonb, decision jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE node jsonb := cursor_state->'node'; a jsonb := decision #> '{answers,result}';
    score numeric; answer jsonb; route text; next_node jsonb; judgment jsonb; trace jsonb;
BEGIN
    IF decision IS NULL THEN
        RETURN cursor_state || jsonb_build_object('status','unavailable','error','decision_unavailable');
    END IF;
    IF node->>'kind' = 'branch' THEN
        IF a->>'type' IS DISTINCT FROM 'noul' OR jsonb_typeof(a->'noul') IS DISTINCT FROM 'number' THEN
            RETURN cursor_state || jsonb_build_object('status','unavailable','error','invalid_decision');
        END IF;
        score := (a->>'noul')::numeric;
        answer := to_jsonb(score > (node->>'threshold')::numeric);
        route := CASE WHEN answer = 'true'::jsonb THEN 'yes' ELSE 'no' END;
        next_node := node->route;
    ELSE
        route := a->>'choice';
        IF a->>'type' IS DISTINCT FROM 'choice' OR NOT coalesce((node->'routes') ? route,false)
           OR jsonb_typeof(a->'probabilities'->route) IS DISTINCT FROM 'number' THEN
            RETURN cursor_state || jsonb_build_object('status','unavailable','error','invalid_decision');
        END IF;
        score := (a->'probabilities'->>route)::numeric;
        answer := to_jsonb(route);
        next_node := node->'routes'->route;
    END IF;
    IF NOT score BETWEEN 0 AND 1 THEN
        RETURN cursor_state || jsonb_build_object('status','unavailable','error','invalid_decision');
    END IF;
    -- Only interpretation-relevant history enters the next cache key. Neither
    -- timestamps, provider accounting, tree placement nor unvisited branches do.
    judgment := jsonb_build_object('question',node->>'question','answer',answer,'score',score)
        || CASE WHEN node->>'kind'='branch' THEN jsonb_build_object('threshold',node->'threshold')
                ELSE jsonb_build_object('choices',node->'choices') END;
    trace := judgment || jsonb_build_object('node',cursor_state->>'location','route',route,
        'model',decision->'model','contract',decision->'contract')
        || CASE WHEN node ? 'inputs' THEN jsonb_build_object('inputs',node->'inputs') ELSE '{}'::jsonb END;
    RETURN cursor_state || jsonb_build_object('node',next_node,
        'location',(cursor_state->>'location') || '/' || replace(replace(route,'~','~0'),'/','~1'),
        'path',(cursor_state->'path') || jsonb_build_array(trace),
        'judgments',(cursor_state->'judgments') || jsonb_build_array(judgment));
END
$fn$;

-- _decision_tree_result(jsonb, jsonb) from 0471_decision_tree_inputs
CREATE OR REPLACE FUNCTION rvbbit._decision_tree_result(cursor_state jsonb, tree jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
    SELECT jsonb_build_object('version','clover-tree-v1','tree_id',md5(tree::text),
        'status',cursor_state->>'status','label',cursor_state->'label',
        'steps',jsonb_array_length(cursor_state->'path'),'path',cursor_state->'path')
        || CASE WHEN cursor_state ? 'error' THEN jsonb_build_object('error',cursor_state->>'error',
            'stopped_at',cursor_state->>'location') ELSE '{}'::jsonb END
        || CASE WHEN cursor_state ? 'missing_inputs' THEN jsonb_build_object('missing_inputs',cursor_state->'missing_inputs') ELSE '{}'::jsonb END
$fn$;

-- decision_tree_batch(jsonb, jsonb, integer) from 0471_decision_tree_inputs
CREATE OR REPLACE FUNCTION rvbbit.decision_tree_batch(states jsonb, tree jsonb, max_steps integer DEFAULT 16)
RETURNS jsonb LANGUAGE plpgsql STRICT AS $fn$
DECLARE initial jsonb := rvbbit._decision_tree_prepare(tree,max_steps);
    cursors jsonb[] := ARRAY[]::jsonb[]; requests jsonb[]; wave jsonb; request jsonb;
    outputs jsonb := '[]'::jsonb; i integer; n integer; errors bigint;
BEGIN
    IF jsonb_typeof(states) <> 'array' THEN RAISE EXCEPTION 'decision tree batch: states must be a JSON array'; END IF;
    n := jsonb_array_length(states);
    IF n > 256 OR octet_length(states::text) > 8388608 THEN
        RAISE EXCEPTION 'decision tree batch: maximum 256 states / 8 MiB';
    END IF;
    IF coalesce(current_setting('rvbbit.cache_bypass',true),'off') = 'on'
       OR EXISTS (SELECT 1 FROM rvbbit.operators WHERE name='clover_decide' AND cache_policy='never') THEN
        RAISE EXCEPTION 'decision tree batch requires the clover_decide cache; use scalar decision_tree for cache bypass';
    END IF;
    FOR i IN 1..n LOOP
        cursors := array_append(cursors,CASE WHEN states->(i-1)='null'::jsonb THEN NULL ELSE initial END);
    END LOOP;
    LOOP
        wave := '[]'::jsonb; requests := array_fill(NULL::jsonb,ARRAY[n]);
        FOR i IN 1..n LOOP
            IF cursors[i] IS NULL THEN CONTINUE; END IF;
            cursors[i] := rvbbit._decision_tree_ready(cursors[i],max_steps);
            IF cursors[i]->>'status' <> 'running' THEN CONTINUE; END IF;
            request := rvbbit._decision_tree_request(states->(i-1),cursors[i]);
            IF request->>'status' = 'missing_input' THEN
                cursors[i] := cursors[i] || request;
                CONTINUE;
            END IF;
            IF octet_length((request->'state')::text) > 32768 THEN
                cursors[i] := cursors[i] || '{"status":"context_limit","error":"state_and_history_exceed_32kib"}'::jsonb;
                CONTINUE;
            END IF;
            requests[i] := request;
            wave := wave || jsonb_build_array(request);
        END LOOP;
        EXIT WHEN wave = '[]'::jsonb;
        SELECT n_errors INTO errors FROM rvbbit.prewarm_operator('clover_decide',
            format('SELECT state,questions FROM jsonb_to_recordset(%L::jsonb) AS r(state jsonb,questions jsonb)',wave::text));
        FOR i IN 1..n LOOP
            IF requests[i] IS NULL THEN CONTINUE; END IF;
            -- Do not silently retry an unsuccessful prewarm via the scalar
            -- executor. A failed wave is explicit; successful work stays cached
            -- for a caller-requested retry. Already completed rows are retained.
            IF errors > 0 THEN
                cursors[i] := cursors[i] || '{"status":"unavailable","error":"batch_round_failed"}'::jsonb;
            ELSE
                cursors[i] := rvbbit._decision_tree_advance(cursors[i],
                    rvbbit.clover_decide(requests[i]->'state',requests[i]->'questions'));
            END IF;
        END LOOP;
    END LOOP;
    FOR i IN 1..n LOOP
        outputs := outputs || jsonb_build_array(rvbbit._decision_tree_result(cursors[i],tree));
    END LOOP;
    RETURN outputs;
END
$fn$;

-- decision_tree(jsonb, jsonb, integer) from 0472_decision_tree_usage
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

-- capability_search_stale() from 0455_capability_index_follows_tools_and_keys
CREATE OR REPLACE FUNCTION rvbbit.capability_search_stale()
RETURNS boolean LANGUAGE sql STABLE AS $function$
    WITH g AS (
        SELECT count(*) AS n, max(updated_at) AS ts
        FROM rvbbit.catalog_docs WHERE graph_id = 'rvbbit_capabilities'
    )
    SELECT (SELECT n FROM g) = 0
        OR EXISTS (
            SELECT 1 FROM rvbbit.capability_seed s
            WHERE NOT EXISTS (
                SELECT 1 FROM rvbbit.catalog_docs d
                WHERE d.graph_id='rvbbit_capabilities' AND d.kind=s.kind AND d.rel_name=s.name
                  AND d.doc=rvbbit.capability_doc(s.kind,s.name,s.signature,s.description,s.example,s.cost_class,s.tags)
            )
        )
        -- the tool table changed (a sync at boot), or a tool has no doc, or a doc has no tool
        OR (SELECT count(*) FROM rvbbit.capability_mcp_tools)
           <> (SELECT count(*) FROM rvbbit.catalog_docs WHERE graph_id = 'rvbbit_capabilities' AND kind = 'cap_mcp_tool')
        OR greatest(
               coalesce((SELECT max(updated_at) FROM rvbbit.operators), 'epoch'::timestamptz),
               coalesce((SELECT max(updated_at) FROM rvbbit.capability_catalog), 'epoch'::timestamptz),
               coalesce((SELECT max(updated_at) FROM rvbbit.capability_mcp_tools), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.metric_defs), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.cube_defs), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.brain_sources), 'epoch'::timestamptz)
           ) > coalesce((SELECT ts FROM g), 'epoch'::timestamptz);
$function$;
