-- 0455 · the capability index follows the live tool list, and a person can state a table's keys
--
-- 1. capability_search could not find teach_meaning. cap_mcp_tool docs are
--    built by capability_crawl from rvbbit.capability_mcp_tools, a table seeded
--    by migrations (scripts/gen_capability_mcp_tools.py) — so a tool added after
--    the last seed has no row, a tool whose docstring changed keeps its old
--    summary, and capability_search_stale() never noticed either (it watched
--    operators, catalog, metrics, cubes and brain sources, not this table). The
--    server now syncs the table from its own registry at boot
--    (capability_mcp_tools_sync), the table carries updated_at, and staleness
--    includes it, so the next search rebuilds the docs.
--
-- 2. "Join on title": the enricher keys a relation's labels by its entity keys,
--    falling back to the classified text column when none are known. A person
--    can now state them — meaning_state(relation, 'keys', '["parent_asin"]') —
--    which sets meaning_relations.entity_keys, the value every later reader and
--    the next enrichment use.

ALTER TABLE rvbbit.capability_mcp_tools ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE OR REPLACE FUNCTION rvbbit.capability_mcp_tools_sync(p_tools jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_ins int := 0; v_upd int := 0; v_del int := 0; v_n int;
BEGIN
    -- [{name, summary}] from the live registry; a short or malformed list is refused
    -- rather than allowed to empty the table
    IF jsonb_typeof(p_tools) <> 'array' THEN RAISE EXCEPTION 'capability_mcp_tools_sync: expected a JSON array'; END IF;
    SELECT count(*) INTO v_n FROM jsonb_array_elements(p_tools) t WHERE nullif(trim(t->>'name'), '') IS NOT NULL;
    IF v_n < 20 THEN RAISE EXCEPTION 'capability_mcp_tools_sync: refusing a list of % tools', v_n; END IF;
    WITH live AS (
        SELECT DISTINCT ON (trim(t->>'name')) trim(t->>'name') AS name, left(coalesce(trim(t->>'summary'), ''), 1200) AS summary
          FROM jsonb_array_elements(p_tools) t WHERE nullif(trim(t->>'name'), '') IS NOT NULL
         ORDER BY trim(t->>'name')),
    up AS (
        INSERT INTO rvbbit.capability_mcp_tools AS c (name, summary, updated_at)
        SELECT name, summary, now() FROM live
        ON CONFLICT (name) DO UPDATE SET summary = EXCLUDED.summary, updated_at = now()
            WHERE c.summary IS DISTINCT FROM EXCLUDED.summary
        RETURNING (xmax = 0) AS inserted)
    SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) INTO v_ins, v_upd FROM up;
    DELETE FROM rvbbit.capability_mcp_tools c
     WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_tools) t WHERE trim(t->>'name') = c.name);
    GET DIAGNOSTICS v_del = ROW_COUNT;
    RETURN jsonb_build_object('inserted', v_ins, 'updated', v_upd, 'removed', v_del, 'changed', (v_ins + v_upd + v_del) > 0);
END $fn$;
COMMENT ON FUNCTION rvbbit.capability_mcp_tools_sync(jsonb) IS 'Upsert the Warehouse MCP tool summaries from the live registry and drop rows for tools that no longer exist; returns counts and `changed`. The server calls it at boot; capability_search_stale() then sees updated_at move.';

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

-- ── a person can state a relation's entity keys ─────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_state(p_relation text, p_field text, p_value jsonb, p_reason text DEFAULT NULL, p_col text DEFAULT NULL, p_actor text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint; v_sup text; v_keys text[]; v_missing text[];
BEGIN
    IF p_field NOT IN ('kind', 'business_name', 'what', 'domain_key', 'ignore', 'role', 'ref_target', 'pii', 'succession', 'caveat', 'keys') THEN
        RAISE EXCEPTION 'meaning_state: unknown field %', p_field;
    END IF;
    IF p_field = 'succession' THEN
        IF p_col IS NOT NULL THEN RAISE EXCEPTION 'meaning_state: succession is a relation-level statement'; END IF;
        IF jsonb_typeof(p_value) <> 'object' OR jsonb_typeof(p_value->'supersedes') <> 'array' OR jsonb_array_length(p_value->'supersedes') = 0 THEN
            RAISE EXCEPTION 'meaning_state: succession needs {supersedes: [schema.table, …], effective_from: YYYY-MM-DD, federation?: switch|union|mapped, note?}';
        END IF;
        IF p_value->>'effective_from' IS NULL OR (p_value->>'effective_from')::date IS NULL THEN
            RAISE EXCEPTION 'meaning_state: succession needs effective_from as a date';
        END IF;
        IF coalesce(p_value->>'federation', 'switch') NOT IN ('switch', 'union', 'mapped') THEN
            RAISE EXCEPTION 'meaning_state: federation must be switch, union or mapped';
        END IF;
        FOR v_sup IN SELECT lower(x) FROM jsonb_array_elements_text(p_value->'supersedes') x LOOP
            IF v_sup = lower(p_relation) THEN RAISE EXCEPTION 'meaning_state: a relation cannot supersede itself'; END IF;
        END LOOP;
        p_value := jsonb_strip_nulls(jsonb_build_object(
            'supersedes', (SELECT jsonb_agg(lower(x)) FROM jsonb_array_elements_text(p_value->'supersedes') x),
            'effective_from', (p_value->>'effective_from')::date,
            'federation', coalesce(p_value->>'federation', 'switch'),
            'note', nullif(left(p_value->>'note', 2000), '')));
    ELSIF p_field = 'caveat' THEN
        IF jsonb_typeof(p_value) = 'string' THEN p_value := jsonb_build_object('text', p_value #>> '{}'); END IF;
        IF jsonb_typeof(p_value) <> 'object' OR nullif(trim(p_value->>'text'), '') IS NULL THEN
            RAISE EXCEPTION 'meaning_state: caveat needs {text, as_of?: YYYY-MM-DD}';
        END IF;
        p_value := jsonb_strip_nulls(jsonb_build_object('text', left(trim(p_value->>'text'), 4000),
                                                        'as_of', CASE WHEN p_value->>'as_of' IS NOT NULL THEN (p_value->>'as_of')::date END));
    ELSIF p_field = 'keys' THEN
        -- the columns that identify one row, in order; each must exist on the relation
        IF p_col IS NOT NULL THEN RAISE EXCEPTION 'meaning_state: keys is a relation-level statement'; END IF;
        IF jsonb_typeof(p_value) = 'string' THEN p_value := jsonb_build_array(p_value #>> '{}'); END IF;
        IF jsonb_typeof(p_value) <> 'array' OR jsonb_array_length(p_value) = 0 THEN
            RAISE EXCEPTION 'meaning_state: keys needs a non-empty array of column names';
        END IF;
        SELECT array_agg(DISTINCT lower(trim(x)) ORDER BY lower(trim(x))) INTO v_keys FROM jsonb_array_elements_text(p_value) x WHERE nullif(trim(x), '') IS NOT NULL;
        SELECT array_agg(k) INTO v_missing FROM unnest(v_keys) k
         WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c
                            WHERE c.table_schema = split_part(lower(p_relation), '.', 1) AND c.table_name = split_part(lower(p_relation), '.', 2) AND lower(c.column_name) = k);
        IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'meaning_state: % has no column(s) %', p_relation, array_to_string(v_missing, ', '); END IF;
        SELECT jsonb_agg(x ORDER BY ord) INTO p_value FROM (SELECT DISTINCT ON (lower(trim(x))) lower(trim(x)) AS x, ord FROM jsonb_array_elements_text(p_value) WITH ORDINALITY t(x, ord) ORDER BY lower(trim(x)), ord) y;
    END IF;
    -- caveats accumulate (each is its own statement); every other field replaces its predecessor
    IF p_field <> 'caveat' THEN
        UPDATE rvbbit.meaning_overrides SET retired_at = now()
         WHERE relation = lower(p_relation) AND col_name IS NOT DISTINCT FROM p_col AND field = p_field AND retired_at IS NULL;
    END IF;
    INSERT INTO rvbbit.meaning_overrides (relation, col_name, field, value, reason, actor)
    VALUES (lower(p_relation), p_col, p_field, p_value, p_reason, coalesce(p_actor, current_user)) RETURNING override_id INTO v_id;
    IF p_col IS NULL THEN
        UPDATE rvbbit.meaning_relations SET
            business_name = CASE WHEN p_field = 'business_name' THEN p_value #>> '{}' ELSE business_name END,
            domain_key = CASE WHEN p_field = 'domain_key' THEN p_value #>> '{}' ELSE domain_key END,
            kind = CASE WHEN p_field = 'kind' THEN p_value #>> '{}' ELSE kind END,
            what = CASE WHEN p_field = 'what' THEN p_value #>> '{}' ELSE what END,
            entity_keys = CASE WHEN p_field = 'keys' THEN ARRAY(SELECT jsonb_array_elements_text(p_value)) ELSE entity_keys END,
            updated_at = now()
         WHERE relation = lower(p_relation);
        IF p_field = 'keys' THEN
            -- the stated key columns are identifiers on the column cards too
            UPDATE rvbbit.meaning_columns SET role = 'identifier', basis = 'override', updated_at = now()
             WHERE relation = lower(p_relation) AND lower(col_name) IN (SELECT jsonb_array_elements_text(p_value));
        END IF;
    ELSE
        UPDATE rvbbit.meaning_columns SET
            role = CASE WHEN p_field = 'role' THEN p_value #>> '{}' ELSE role END,
            ref_target = CASE WHEN p_field = 'ref_target' THEN p_value #>> '{}' ELSE ref_target END,
            pii = CASE WHEN p_field = 'pii' THEN (p_value #>> '{}')::numeric ELSE pii END,
            basis = 'override', updated_at = now()
         WHERE relation = lower(p_relation) AND col_name = p_col;
    END IF;
    RETURN v_id;
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_state(text, text, jsonb, text, text, text) IS
  'State a fact about a relation or column; overrides win. Relation fields: kind, business_name, what, domain_key, ignore, keys [column, …] (sets entity_keys; the next enrichment keys its labels by them), succession {supersedes[], effective_from, federation, note}, caveat {text, as_of}. Column fields: role, ref_target, pii.';
