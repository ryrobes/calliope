-- 0449 — Meaning enrichment: semantic dimensions that feed the next sweep.
--
-- A usable subject gets an `enrich` pass. A high-judgement model reads the
-- dossier, the candidate text/code columns with sampled values, and the Jev
-- capability catalog, and proposes classifications as Jev questions or
-- decision trees over named columns. Jev answers them over a sample through
-- decision_tree_batch; labels are stored keyed by row, exposed as a view under
-- `meaning_labels`, and registered as a `label:<name>` column card with role
-- `category` so the tree, derived metrics, packets and cards treat them as
-- dimensions with no special casing. Shared label sets then flow back into the
-- map and dossier packets, which is the point: the NEXT sweep is the receipt.
--
-- The model never composes SQL and never sees row-level output. It names a
-- relation, its columns and a question; this layer builds every query.
--
-- See docs/MEANING_ENRICH_PLAN.md.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rvbbit.meaning_enrichments (
    id              bigserial PRIMARY KEY,
    subject_key     text NOT NULL,
    relation        text NOT NULL,
    name            text NOT NULL,                     -- slug, unique per relation
    label_name      text NOT NULL,
    columns         text[] NOT NULL DEFAULT '{}',      -- state columns
    key_columns     text[] NOT NULL DEFAULT '{}',      -- entity keys, or the state columns themselves
    question        jsonb NOT NULL DEFAULT '{}'::jsonb,
    tree            jsonb,                             -- what decision_tree_batch actually runs
    expression      text,                              -- the SQL a person would write
    why             text,
    expects         text,
    status          text NOT NULL DEFAULT 'proposed',  -- proposed|sampled|published|promoted|retired|error
    sample_n        int,
    distribution    jsonb,
    judge           jsonb,
    view_name       text,
    materialized_n  bigint NOT NULL DEFAULT 0,
    watermark       jsonb,
    model           text,
    jev_model       text,
    cost_usd        numeric(12,8) NOT NULL DEFAULT 0,
    attempts        int NOT NULL DEFAULT 0,
    error           text,
    dossier_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (relation, name)
);
CREATE INDEX IF NOT EXISTS meaning_enrichments_subject_idx ON rvbbit.meaning_enrichments (subject_key, status);
COMMENT ON TABLE rvbbit.meaning_enrichments IS
    'Proposed and published Jev classifications over a relation''s text/code columns. One row per label dimension; sentinel rows (relation = ''-'') record a subject pass that proposed nothing.';

CREATE TABLE IF NOT EXISTS rvbbit.meaning_enrich_values (
    enrichment_id   bigint NOT NULL REFERENCES rvbbit.meaning_enrichments(id) ON DELETE CASCADE,
    row_key         jsonb NOT NULL,
    -- A relation with no entity key is keyed by the classified text itself, and
    -- a product review runs to kilobytes: a jsonb key blows the btree's 2704-byte
    -- limit. The key travels whole for the view; the index rides on its digest.
    row_hash        text GENERATED ALWAYS AS (md5(row_key::text)) STORED,
    label           text,                              -- NULL when Jev failed for the row; never a false fallback
    score           numeric(5,4),
    steps           jsonb,
    jev_model       text,
    decided_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (enrichment_id, row_hash)
);
CREATE INDEX IF NOT EXISTS meaning_enrich_values_label_idx ON rvbbit.meaning_enrich_values (enrichment_id, label);

-- Label views live in their own schema, never excavated as relations of their own.
CREATE SCHEMA IF NOT EXISTS meaning_labels;
INSERT INTO rvbbit.business_sources (schema_name, source_system, role, decided_by, note)
VALUES ('meaning_labels', 'meaning', 'excluded', 'meaning',
        'Jev label dimensions published by Meaning enrichment; join them to the base relation, never excavate them.')
ON CONFLICT (schema_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Small helpers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_slug(p_text text)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT left(regexp_replace(regexp_replace(lower(coalesce(p_text, '')), '[^a-z0-9]+', '_', 'g'), '^_+|_+$', '', 'g'), 40)
$fn$;

-- A choice or truth question is a one-node tree, so one execution path serves all three kinds.
CREATE OR REPLACE FUNCTION rvbbit._meaning_enrich_tree(p_question jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE
    v_kind   text := lower(coalesce(p_question->>'kind', ''));
    v_instr  text := btrim(coalesce(p_question->>'instructions', ''));
    v_crit   jsonb := CASE WHEN jsonb_typeof(p_question->'criteria') = 'object' THEN p_question->'criteria' ELSE '{}'::jsonb END;
    v_routes jsonb := '{}'::jsonb;
    v_thresh numeric := coalesce(nullif(p_question->>'threshold', '')::numeric, 0.5);
    v_pair   record;
BEGIN
    IF v_kind = 'tree' THEN
        RETURN p_question->'tree';
    ELSIF v_kind = 'truth' THEN
        IF v_instr = '' THEN RAISE EXCEPTION 'truth question needs instructions'; END IF;
        RETURN rvbbit.branch(v_instr, rvbbit.leaf('yes'), rvbbit.leaf('no'), v_thresh::double precision);
    ELSIF v_kind = 'choice' THEN
        IF v_instr = '' THEN RAISE EXCEPTION 'choice question needs instructions'; END IF;
        FOR v_pair IN SELECT key FROM jsonb_each_text(v_crit) LOOP
            v_routes := v_routes || jsonb_build_object(v_pair.key, rvbbit.leaf(v_pair.key));
        END LOOP;
        RETURN rvbbit.choice_branch(v_instr, v_crit, v_routes);
    END IF;
    RAISE EXCEPTION 'unknown question kind %', v_kind;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit._meaning_enrich_log(p_action text, p_subject text, p_detail jsonb)
RETURNS void LANGUAGE sql VOLATILE AS $fn$
    INSERT INTO rvbbit.meaning_desire_path_log (action, subject_key, detail, actor)
    VALUES (p_action, p_subject, coalesce(p_detail, '{}'::jsonb), 'meaning-enrich')
$fn$;

-- A subject pass that proposed nothing still records that it ran, so the queue
-- does not re-ask every tick until the dossier changes.
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_sentinel(p_subject_key text, p_reason text DEFAULT 'none_proposed')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE v_at timestamptz;
BEGIN
    SELECT dossier_at INTO v_at FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    INSERT INTO rvbbit.meaning_enrichments AS e
        (subject_key, relation, name, label_name, status, error, dossier_at, updated_at)
    VALUES (p_subject_key, '-', 'pass:' || p_subject_key, 'pass', 'retired', left(p_reason, 400), v_at, now())
    ON CONFLICT (relation, name) DO UPDATE SET
        subject_key = EXCLUDED.subject_key, status = 'retired', error = EXCLUDED.error,
        dossier_at = EXCLUDED.dossier_at, attempts = e.attempts + 1, updated_at = now();
    RETURN jsonb_build_object('status', 'sentinel', 'subject', p_subject_key, 'reason', p_reason);
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_spend(p_window interval DEFAULT interval '24 hours')
RETURNS numeric LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(sum(cost_usd), 0)::numeric FROM rvbbit.meaning_enrichments WHERE updated_at > now() - p_window
$fn$;

-- ---------------------------------------------------------------------------
-- Candidate columns and the proposal packet
-- ---------------------------------------------------------------------------
-- Text and code columns worth classifying: free text first, then wide
-- categories (codes worth grouping). Personal data never enters a packet.
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_candidates(p_relation text, p_max int DEFAULT 6)
RETURNS TABLE(col_name text, role text, what text, ndv bigint, null_frac numeric, top_values jsonb)
LANGUAGE sql STABLE AS $fn$
    SELECT c.col_name, c.role, c.what, c.ndv, c.null_frac, c.top_values
      FROM rvbbit.meaning_columns c
     WHERE c.relation = lower(p_relation)
       AND coalesce(c.pii, 0) < 0.6
       AND coalesce(c.null_frac, 0) < 0.9
       AND c.role IS DISTINCT FROM 'structured_blob'
       AND (c.role = 'free_text'
            OR (c.role = 'category' AND coalesce(c.ndv, 0) > 12)
            OR (c.role = 'identifier' AND coalesce(c.data_type, '') ~* '(text|char)' AND coalesce(c.ndv, 0) > 60
                AND c.col_name !~* '(^|_)(id|uuid|guid|key|sha|hash)(_c)?$'))
     ORDER BY CASE c.role WHEN 'free_text' THEN 0 WHEN 'category' THEN 1 ELSE 2 END,
              coalesce(c.ndv, 0) DESC, c.col_name
     LIMIT greatest(p_max, 1)
$fn$;

-- The packet Sol reads. Everything but the live value sample comes from cards.
-- VOLATILE: it samples the base tables under a per-table statement timeout.
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_packet(
    p_subject_key text, p_max_tables int DEFAULT 10, p_max_columns int DEFAULT 5, p_values int DEFAULT 10)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    d         rvbbit.meaning_domains%ROWTYPE;
    v_tables  jsonb := '[]'::jsonb;
    v_t       record;
    v_cols    jsonb;
    v_samples jsonb;
    v_sql     text;
    v_parts   text[];
    v_c       record;
    v_err     text;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    IF NOT FOUND THEN RETURN NULL; END IF;

    FOR v_t IN
        SELECT m.relation, m.rel_name, m.business_name, m.what, m.narrative, m.kind, m.n_rows,
               m.entity_keys, m.subject_role
          FROM rvbbit.meaning_relations m
         WHERE m.relation = ANY(d.members)
           AND coalesce(m.subject_role, 'support') IN ('core', 'support')
           AND coalesce(m.plumbing, 0) < 0.7
         ORDER BY (coalesce(m.subject_role, '') = 'core') DESC,
                  coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
         LIMIT greatest(p_max_tables, 1)
    LOOP
        v_cols := '[]'::jsonb;
        v_parts := '{}';
        FOR v_c IN SELECT * FROM rvbbit.meaning_enrich_candidates(v_t.relation, p_max_columns) LOOP
            v_cols := v_cols || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
                'column', v_c.col_name, 'role', v_c.role, 'what', v_c.what,
                'distinct', v_c.ndv, 'null_pct', round(coalesce(v_c.null_frac, 0) * 100),
                'top', v_c.top_values)));
            v_parts := array_append(v_parts, format(
                '%L, (SELECT jsonb_agg(v) FROM (SELECT DISTINCT left(%I::text, 160) AS v FROM (SELECT %I FROM %I.%I WHERE %I IS NOT NULL LIMIT 400) a LIMIT %s) b)',
                v_c.col_name, v_c.col_name, v_c.col_name,
                split_part(v_t.relation, '.', 1), split_part(v_t.relation, '.', 2), v_c.col_name, greatest(p_values, 1)));
        END LOOP;
        CONTINUE WHEN jsonb_array_length(v_cols) = 0;

        v_samples := NULL; v_err := NULL;
        BEGIN
            SET LOCAL statement_timeout = '6s';
            v_sql := 'SELECT jsonb_strip_nulls(jsonb_build_object(' || array_to_string(v_parts, ', ') || '))';
            EXECUTE v_sql INTO v_samples;
        EXCEPTION WHEN OTHERS THEN
            v_err := left(SQLERRM, 120);
        END;
        RESET statement_timeout;

        v_tables := v_tables || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
            'table', v_t.relation,
            'name', coalesce(v_t.business_name, rvbbit.meaning_display_name(v_t.rel_name)),
            'what', left(coalesce(v_t.narrative, v_t.what), 220),
            'kind', v_t.kind, 'role', v_t.subject_role, 'rows', v_t.n_rows,
            'keys', CASE WHEN cardinality(v_t.entity_keys) > 0 THEN to_jsonb(v_t.entity_keys[1:2]) END,
            'columns', v_cols,
            'sampled_values', v_samples,
            'sample_error', v_err)));
    END LOOP;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name,
        'about', d.about,
        'areas', d.areas,
        'questions', d.questions,
        'gaps', d.gaps,
        'tables', v_tables,
        'existing', (SELECT jsonb_agg(jsonb_build_object(
                            'table', e.relation, 'name', e.name, 'label_name', e.label_name,
                            'labels', (SELECT jsonb_agg(x->>'label') FROM jsonb_array_elements(rvbbit._jsonb_arr(e.distribution)) x)))
                       FROM rvbbit.meaning_enrichments e
                      WHERE e.subject_key = p_subject_key AND e.status IN ('published', 'promoted')),
        'shared_labels', nullif(rvbbit.meaning_shared_labels(NULL), '[]'::jsonb),
        'capabilities', (SELECT jsonb_agg(jsonb_build_object(
                                'name', cs.name, 'signature', cs.signature,
                                'description', left(cs.description, 600), 'example', cs.example))
                           FROM rvbbit.capability_seed cs
                          WHERE cs.name IN ('choose', 'truth', 'truth_score', 'clover_decide', 'truth_question',
                                            'choice_question', 'leaf', 'branch', 'choice_branch',
                                            'decision_tree', 'decision_tree_batch', 'clover_decisions'))));
END $fn$;

-- ---------------------------------------------------------------------------
-- Register a proposal (validate hard; the model's only free text that reaches
-- SQL is the criteria inside a Jev question and the card sentence).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_register(p_subject_key text, p_proposal jsonb, p_model text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    d          rvbbit.meaning_domains%ROWTYPE;
    m          rvbbit.meaning_relations%ROWTYPE;
    v_rel      text := lower(btrim(coalesce(p_proposal->>'relation', '')));
    v_name     text := rvbbit.meaning_enrich_slug(p_proposal->>'name');
    v_label    text := btrim(coalesce(nullif(p_proposal->>'label_name', ''), initcap(replace(v_name, '_', ' '))));
    v_cols     text[] := '{}';
    v_keys     text[] := '{}';
    v_q        jsonb := CASE WHEN jsonb_typeof(p_proposal->'question') = 'object' THEN p_proposal->'question' ELSE '{}'::jsonb END;
    v_kind     text := lower(coalesce(v_q->>'kind', ''));
    v_tree     jsonb;
    v_expr     text;
    v_labels   int;
    v_nodes    int;
    v_id       bigint;
    v_item     text;
    v_state    text[] := '{}';
    v_reason   text;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    IF NOT FOUND THEN RETURN jsonb_build_object('rejected', 'unknown_subject'); END IF;
    IF v_name = '' OR length(v_name) < 2 THEN RETURN jsonb_build_object('rejected', 'bad_name'); END IF;

    SELECT * INTO m FROM rvbbit.meaning_relations WHERE relation = v_rel;
    IF NOT FOUND OR NOT (v_rel = ANY(d.members)) THEN
        RETURN jsonb_build_object('rejected', 'relation_not_in_subject', 'relation', v_rel);
    END IF;

    -- columns must be real, non-personal, and at most four
    FOR v_item IN SELECT lower(btrim(x)) FROM jsonb_array_elements_text(rvbbit._jsonb_arr(p_proposal->'columns')) x LOOP
        IF EXISTS (SELECT 1 FROM rvbbit.meaning_columns c
                    WHERE c.relation = v_rel AND lower(c.col_name) = v_item
                      AND coalesce(c.pii, 0) < 0.6 AND c.role IS DISTINCT FROM 'structured_blob')
        THEN
            SELECT c.col_name INTO v_item FROM rvbbit.meaning_columns c WHERE c.relation = v_rel AND lower(c.col_name) = v_item;
            IF NOT (v_item = ANY(v_cols)) THEN v_cols := array_append(v_cols, v_item); END IF;
        END IF;
    END LOOP;
    IF cardinality(v_cols) = 0 THEN RETURN jsonb_build_object('rejected', 'no_usable_columns', 'relation', v_rel); END IF;
    IF cardinality(v_cols) > 4 THEN v_cols := v_cols[1:4]; END IF;

    -- keys: the relation's entity keys when it has them, else the state columns
    -- themselves (the label then attaches to the value, which is right for a
    -- name or description column). Never ctid.
    SELECT array_agg(k) INTO v_keys
      FROM unnest(coalesce(m.entity_keys, '{}')) k
     WHERE EXISTS (SELECT 1 FROM rvbbit.meaning_columns c WHERE c.relation = v_rel AND c.col_name = k);
    IF v_keys IS NULL OR cardinality(v_keys) = 0 THEN v_keys := v_cols; END IF;
    IF cardinality(v_keys) > 3 THEN v_keys := v_keys[1:3]; END IF;

    -- question shape
    IF v_kind = 'choice' THEN
        v_labels := (SELECT count(*) FROM jsonb_each_text(CASE WHEN jsonb_typeof(v_q->'criteria') = 'object' THEN v_q->'criteria' ELSE '{}'::jsonb END));
        IF v_labels < 2 OR v_labels > 16 THEN RETURN jsonb_build_object('rejected', 'choice_needs_2_to_16_labels', 'labels', v_labels); END IF;
        IF EXISTS (SELECT 1 FROM jsonb_each_text(v_q->'criteria') e WHERE rvbbit.meaning_enrich_slug(e.key) = '' OR btrim(e.value) = '') THEN
            RETURN jsonb_build_object('rejected', 'empty_label_or_criterion');
        END IF;
    ELSIF v_kind = 'truth' THEN
        v_labels := 2;
    ELSIF v_kind = 'tree' THEN
        IF jsonb_typeof(v_q->'tree') <> 'object' THEN RETURN jsonb_build_object('rejected', 'tree_missing'); END IF;
        v_nodes := (length((v_q->'tree')::text) - length(replace((v_q->'tree')::text, '"kind"', ''))) / 6;
        IF v_nodes > 12 THEN RETURN jsonb_build_object('rejected', 'tree_too_large', 'nodes', v_nodes); END IF;
        v_labels := greatest((length((v_q->'tree')::text) - length(replace((v_q->'tree')::text, '"leaf"', ''))) / 6, 2);
    ELSE
        RETURN jsonb_build_object('rejected', 'unknown_question_kind', 'kind', v_kind);
    END IF;

    BEGIN
        v_tree := rvbbit._meaning_enrich_tree(v_q);
        PERFORM rvbbit._decision_tree_prepare(v_tree, 16);
    EXCEPTION WHEN OTHERS THEN
        v_reason := left(SQLERRM, 160);
        PERFORM rvbbit._meaning_enrich_log('enrich_rejected', p_subject_key,
                    jsonb_build_object('relation', v_rel, 'name', v_name, 'reason', v_reason));
        RETURN jsonb_build_object('rejected', 'invalid_tree', 'detail', v_reason);
    END;

    -- the SQL a person would write for this classification
    SELECT 'SELECT ' || array_to_string(ARRAY(SELECT format('%I', k) FROM unnest(v_keys) k), ', ')
           || ', rvbbit.decision_tree(jsonb_build_object('
           || array_to_string(ARRAY(SELECT format('%L, %I', c, c) FROM unnest(v_cols) c), ', ')
           || '), ' || format('%L', v_tree::text) || '::jsonb)->>''label'' AS ' || format('%I', v_name)
           || ' FROM ' || format('%I.%I', split_part(v_rel, '.', 1), split_part(v_rel, '.', 2))
      INTO v_expr;

    INSERT INTO rvbbit.meaning_enrichments AS e
        (subject_key, relation, name, label_name, columns, key_columns, question, tree, expression,
         why, expects, status, model, dossier_at, updated_at)
    VALUES (p_subject_key, v_rel, v_name, v_label, v_cols, v_keys, v_q, v_tree, v_expr,
            left(btrim(coalesce(p_proposal->>'why', '')), 400), left(btrim(coalesce(p_proposal->>'expects', '')), 400),
            'proposed', p_model, d.dossier_at, now())
    ON CONFLICT (relation, name) DO UPDATE SET
        subject_key = EXCLUDED.subject_key, label_name = EXCLUDED.label_name, columns = EXCLUDED.columns,
        key_columns = EXCLUDED.key_columns, question = EXCLUDED.question, tree = EXCLUDED.tree,
        expression = EXCLUDED.expression, why = EXCLUDED.why, expects = EXCLUDED.expects,
        status = 'proposed', sample_n = NULL, distribution = NULL, judge = NULL,
        model = EXCLUDED.model, dossier_at = EXCLUDED.dossier_at, error = NULL,
        attempts = e.attempts + 1, updated_at = now()
    RETURNING e.id INTO v_id;

    PERFORM rvbbit._meaning_enrich_log('enrich_registered', p_subject_key,
                jsonb_build_object('id', v_id, 'relation', v_rel, 'name', v_name, 'kind', v_kind, 'labels', v_labels));
    RETURN jsonb_build_object('id', v_id, 'status', 'proposed', 'relation', v_rel, 'name', v_name,
                              'columns', to_jsonb(v_cols), 'keys', to_jsonb(v_keys), 'labels', v_labels);
END $fn$;

-- ---------------------------------------------------------------------------
-- Run Jev over a sample or a materialization chunk.
-- ---------------------------------------------------------------------------
-- Every query is composed here. `p_mode` is 'sample' or 'materialize'.
CREATE OR REPLACE FUNCTION rvbbit._meaning_enrich_state_sql(p_id bigint, p_mode text, p_n int)
RETURNS text LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    e         rvbbit.meaning_enrichments%ROWTYPE;
    v_rows    bigint;
    v_kind    char;
    v_keysel  text;
    v_statesel text;
    v_where   text;
    v_order   text;
    v_from    text;
    v_pct     numeric;
    v_ident   text;
    v_distinct text := '';
BEGIN
    SELECT * INTO e FROM rvbbit.meaning_enrichments WHERE id = p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'enrichment % not found', p_id; END IF;

    v_keysel := 'jsonb_build_object(' || array_to_string(ARRAY(SELECT format('%L, %I', k, k) FROM unnest(e.key_columns) k), ', ') || ')';
    v_statesel := 'jsonb_build_object(' || array_to_string(ARRAY(SELECT format('%L, left(%I::text, 400)', c, c) FROM unnest(e.columns) c), ', ') || ')';
    -- an all-null state would 422 the whole batch call
    v_where := '(' || array_to_string(ARRAY(SELECT format('%I IS NOT NULL', c) FROM unnest(e.columns) c), ' OR ') || ')';
    v_order := array_to_string(ARRAY(SELECT format('%I', k) FROM unnest(e.key_columns) k), ', ');

    SELECT n_rows INTO v_rows FROM rvbbit.meaning_relations WHERE relation = e.relation;
    SELECT relkind INTO v_kind FROM pg_class WHERE oid = to_regclass(e.relation);
    v_ident := format('%I.%I', split_part(e.relation, '.', 1), split_part(e.relation, '.', 2));
    v_from := v_ident;

    IF p_mode = 'sample' THEN
        -- spread the sample over a big table; a view or a small table takes a plain limit
        IF coalesce(v_rows, 0) > 50000 AND coalesce(v_kind, 'r') IN ('r', 'p', 'm') THEN
            v_pct := least(100, greatest(0.5, round((p_n * 40.0 * 100.0) / v_rows, 3)));
            v_from := v_ident || format(' TABLESAMPLE SYSTEM (%s)', v_pct);
        END IF;
        IF e.key_columns = e.columns THEN v_distinct := 'DISTINCT '; END IF;
        RETURN format('SELECT %s%s AS rk, %s AS st FROM %s WHERE %s LIMIT %s',
                      v_distinct, v_keysel, v_statesel, v_from, v_where, greatest(p_n, 1));
    END IF;

    -- materialize: walk the key order from the watermark
    IF e.watermark IS NOT NULL AND jsonb_typeof(e.watermark) = 'object' THEN
        v_where := v_where || ' AND (' || v_order || ') > ('
                   || array_to_string(ARRAY(SELECT format('%L', e.watermark->>k) FROM unnest(e.key_columns) k), ', ') || ')';
    END IF;
    IF e.key_columns = e.columns THEN v_distinct := 'DISTINCT '; END IF;
    RETURN format('SELECT %s%s AS rk, %s AS st FROM %s WHERE %s ORDER BY %s LIMIT %s',
                  v_distinct, v_keysel, v_statesel, v_ident, v_where, v_order, greatest(p_n, 1));
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit._meaning_enrich_run(p_id bigint, p_mode text, p_n int)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    e         rvbbit.meaning_enrichments%ROWTYPE;
    v_sql     text;
    v_rows    jsonb := '[]'::jsonb;
    v_total   int;
    v_i       int;
    v_j       int;
    v_states  jsonb;
    v_out     jsonb;
    v_r       jsonb;
    v_label   text;
    v_score   numeric;
    v_model   text;
    v_done    int := 0;
    v_failed  int := 0;
    v_bytes   bigint := 0;
    v_last    jsonb;
    v_step    jsonb;
BEGIN
    SELECT * INTO e FROM rvbbit.meaning_enrichments WHERE id = p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'enrichment % not found', p_id; END IF;

    v_sql := rvbbit._meaning_enrich_state_sql(p_id, p_mode, p_n);
    BEGIN
        SET LOCAL statement_timeout = '30s';
        EXECUTE format('SELECT coalesce(jsonb_agg(jsonb_build_object(''k'', q.rk, ''s'', q.st)), ''[]''::jsonb) FROM (%s) q', v_sql)
           INTO v_rows;
    EXCEPTION WHEN OTHERS THEN
        RESET statement_timeout;
        RAISE EXCEPTION 'state query failed: %', left(SQLERRM, 200);
    END;
    RESET statement_timeout;

    v_total := jsonb_array_length(v_rows);
    IF v_total = 0 THEN
        RETURN jsonb_build_object('rows', 0, 'labeled', 0, 'failed', 0, 'cost_usd', 0);
    END IF;

    v_i := 0;
    WHILE v_i < v_total LOOP
        SELECT coalesce(jsonb_agg(x->'s'), '[]'::jsonb) INTO v_states
          FROM jsonb_array_elements(v_rows) WITH ORDINALITY t(x, n)
         WHERE t.n > v_i AND t.n <= v_i + 256;

        v_out := rvbbit.decision_tree_batch(v_states, e.tree, 16);

        FOR v_j IN 0 .. jsonb_array_length(v_out) - 1 LOOP
            v_r := v_out->v_j;
            v_label := NULL; v_score := NULL;
            v_step := CASE WHEN jsonb_typeof(v_r->'path') = 'array' AND jsonb_array_length(v_r->'path') > 0
                           THEN v_r->'path'->(jsonb_array_length(v_r->'path') - 1) END;
            IF v_r->>'status' = 'complete' THEN
                v_label := v_r->>'label';
                v_score := nullif(v_step->>'score', '')::numeric;
                v_model := coalesce(v_model, v_step#>>'{model,0}', v_step->>'model');
                v_done := v_done + 1;
            ELSE
                v_failed := v_failed + 1;
            END IF;
            v_last := v_rows->(v_i + v_j)->'k';
            v_bytes := v_bytes + octet_length((v_rows->(v_i + v_j)->'s')::text);
            INSERT INTO rvbbit.meaning_enrich_values AS v (enrichment_id, row_key, label, score, steps, jev_model, decided_at)
            VALUES (p_id, v_last, v_label, v_score,
                    CASE WHEN v_r->>'status' <> 'complete' THEN jsonb_build_object('status', v_r->>'status', 'error', v_r->>'error')
                         WHEN jsonb_array_length(rvbbit._jsonb_arr(v_r->'path')) > 1 THEN v_r->'path' END,
                    coalesce(v_step#>>'{model,0}', v_step->>'model'), now())
            ON CONFLICT (enrichment_id, row_hash) DO UPDATE SET
                label = EXCLUDED.label, score = EXCLUDED.score, steps = EXCLUDED.steps,
                jev_model = EXCLUDED.jev_model, decided_at = now();
        END LOOP;
        v_i := v_i + 256;
    END LOOP;

    RETURN jsonb_build_object(
        'rows', v_total, 'labeled', v_done, 'failed', v_failed,
        'jev_model', v_model, 'watermark', v_last,
        -- Jev bills input only, at $0.042 per million tokens; ~4 bytes per token
        'cost_usd', round(((v_bytes / 4.0) / 1000000.0 * 0.042)::numeric, 8));
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_sample(p_id bigint, p_n int DEFAULT 500)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_run   jsonb;
    v_dist  jsonb;
    v_total bigint;
BEGIN
    DELETE FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id;
    BEGIN
        v_run := rvbbit._meaning_enrich_run(p_id, 'sample', p_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE rvbbit.meaning_enrichments
           SET status = 'error', error = left(SQLERRM, 400), attempts = attempts + 1, updated_at = now()
         WHERE id = p_id;
        RETURN jsonb_build_object('status', 'error', 'error', left(SQLERRM, 400));
    END;

    SELECT count(*) FILTER (WHERE label IS NOT NULL) INTO v_total
      FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('label', label, 'n', n,
                    'share', CASE WHEN v_total > 0 THEN round(n::numeric / v_total, 4) ELSE 0 END) ORDER BY n DESC), '[]'::jsonb)
      INTO v_dist
      FROM (SELECT label, count(*) AS n FROM rvbbit.meaning_enrich_values
             WHERE enrichment_id = p_id AND label IS NOT NULL GROUP BY label) x;

    UPDATE rvbbit.meaning_enrichments
       SET status = CASE WHEN (v_run->>'labeled')::int > 0 THEN 'sampled' ELSE 'error' END,
           error = CASE WHEN (v_run->>'labeled')::int > 0 THEN NULL ELSE 'no rows labeled' END,
           sample_n = (v_run->>'rows')::int,
           distribution = v_dist,
           jev_model = coalesce(v_run->>'jev_model', jev_model),
           cost_usd = cost_usd + coalesce((v_run->>'cost_usd')::numeric, 0),
           updated_at = now()
     WHERE id = p_id;

    RETURN v_run || jsonb_build_object('distribution', v_dist);
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_materialize(
    p_id bigint, p_max_rows int DEFAULT 20000, p_budget_usd numeric DEFAULT 0.50)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_run    jsonb;
    v_spent  numeric := 0;
    v_rows   bigint := 0;
    v_chunk  int := 2000;
    v_ask    int;
    v_loops  int := 0;
BEGIN
    LOOP
        v_loops := v_loops + 1;
        EXIT WHEN v_loops > 100 OR v_rows >= p_max_rows OR v_spent >= p_budget_usd;
        v_ask := greatest(least(v_chunk, p_max_rows - v_rows::int), 1);
        v_run := rvbbit._meaning_enrich_run(p_id, 'materialize', v_ask);
        EXIT WHEN coalesce((v_run->>'rows')::int, 0) = 0;
        v_rows := v_rows + (v_run->>'rows')::int;
        v_spent := v_spent + coalesce((v_run->>'cost_usd')::numeric, 0);
        UPDATE rvbbit.meaning_enrichments
           SET watermark = coalesce(v_run->'watermark', watermark),
               materialized_n = (SELECT count(*) FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id),
               cost_usd = cost_usd + coalesce((v_run->>'cost_usd')::numeric, 0),
               jev_model = coalesce(v_run->>'jev_model', jev_model),
               updated_at = now()
         WHERE id = p_id;
        EXIT WHEN (v_run->>'rows')::int < v_ask;
    END LOOP;
    RETURN jsonb_build_object('rows', v_rows, 'cost_usd', round(v_spent, 8), 'chunks', v_loops - 1);
END $fn$;

-- ---------------------------------------------------------------------------
-- Judge the distribution (one Jev truth question over the shape, not the rows)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_judge(p_id bigint)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    e       rvbbit.meaning_enrichments%ROWTYPE;
    v_state jsonb;
    v_score numeric;
    v_top   numeric;
    v_small int;
    v_n     int;
    v_keep  boolean;
    v_why   text;
BEGIN
    SELECT * INTO e FROM rvbbit.meaning_enrichments WHERE id = p_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'missing'); END IF;

    v_n := jsonb_array_length(rvbbit._jsonb_arr(e.distribution));
    IF v_n = 0 THEN
        UPDATE rvbbit.meaning_enrichments SET judge = jsonb_build_object('reason', 'no_labels'), updated_at = now() WHERE id = p_id;
        RETURN jsonb_build_object('keep', false, 'reason', 'no_labels');
    END IF;
    SELECT max((x->>'share')::numeric), count(*) FILTER (WHERE (x->>'share')::numeric >= 0.02)
      INTO v_top, v_small FROM jsonb_array_elements(e.distribution) x;

    -- shape gates first: a single label swallowing everything is useless whatever a model says
    v_keep := v_top <= 0.90 AND (v_small >= 2 OR (lower(coalesce(e.question->>'kind', '')) = 'truth' AND v_top <= 0.98));
    IF NOT v_keep THEN
        v_why := CASE WHEN v_top > 0.90 THEN 'one label holds ' || round(v_top * 100) || '% of the sample'
                      ELSE 'fewer than two labels hold 2% of the sample' END;
        UPDATE rvbbit.meaning_enrichments
           SET judge = jsonb_build_object('keep', false, 'reason', v_why, 'top_share', v_top, 'at', now()), updated_at = now()
         WHERE id = p_id;
        RETURN jsonb_build_object('keep', false, 'reason', v_why);
    END IF;

    v_state := jsonb_build_object(
        'label_dimension', e.label_name, 'proposed_for', e.relation,
        'columns_classified', to_jsonb(e.columns), 'why', e.why,
        'sample_rows', e.sample_n, 'labels', e.distribution);
    BEGIN
        v_score := rvbbit.truth_score(v_state::text,
            'these labels split the sampled values into groups a business analyst would find useful, '
            || 'distinct from one another, and worth grouping or filtering by');
    EXCEPTION WHEN OTHERS THEN
        v_score := NULL;
    END;

    -- a judge outage never silently drops a proposal that passed the shape gates
    v_keep := coalesce(v_score, 0.5) >= 0.5;
    UPDATE rvbbit.meaning_enrichments
       SET judge = jsonb_build_object('keep', v_keep, 'score', v_score, 'top_share', v_top,
                                      'labels', v_n, 'judged', v_score IS NOT NULL, 'at', now()),
           updated_at = now()
     WHERE id = p_id;
    RETURN jsonb_build_object('keep', v_keep, 'score', v_score, 'top_share', v_top, 'labels', v_n);
END $fn$;

-- ---------------------------------------------------------------------------
-- Publish: the view an agent groups by, and the column card that makes it a dimension
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_publish(p_id bigint)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    e        rvbbit.meaning_enrichments%ROWTYPE;
    v_view   text;
    v_cols   text[] := '{}';
    v_type   text;
    v_k      text;
    v_sql    text;
    v_labels int;
    v_nulls  numeric;
    v_total  bigint;
BEGIN
    SELECT * INTO e FROM rvbbit.meaning_enrichments WHERE id = p_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'missing'); END IF;

    v_view := left(replace(split_part(e.relation, '.', 2), '.', '_') || '__' || e.name, 63);

    FOREACH v_k IN ARRAY e.key_columns LOOP
        SELECT format_type(a.atttypid, a.atttypmod) INTO v_type
          FROM pg_attribute a WHERE a.attrelid = to_regclass(e.relation) AND a.attname = v_k AND a.attnum > 0;
        v_cols := array_append(v_cols, format('(row_key->>%L)::%s AS %I', v_k, coalesce(v_type, 'text'), v_k));
    END LOOP;

    -- CREATE OR REPLACE VIEW cannot change a column list; drop first
    EXECUTE format('DROP VIEW IF EXISTS meaning_labels.%I', v_view);
    v_sql := format('CREATE VIEW meaning_labels.%I AS SELECT %s, label AS %I, score FROM rvbbit.meaning_enrich_values WHERE enrichment_id = %s AND label IS NOT NULL',
                    v_view, array_to_string(v_cols, ', '), e.name, e.id);
    EXECUTE v_sql;
    EXECUTE format('COMMENT ON VIEW meaning_labels.%I IS %L', v_view,
                   e.label_name || ' — ' || coalesce(e.why, 'Jev classification') ||
                   ' Join on ' || array_to_string(e.key_columns, ', ') || ' to ' || e.relation || '.');

    SELECT count(*) INTO v_total FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id;
    SELECT count(*) FILTER (WHERE label IS NULL)::numeric / greatest(count(*), 1) INTO v_nulls
      FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id;
    v_labels := jsonb_array_length(rvbbit._jsonb_arr(e.distribution));

    -- the card that makes it a dimension everywhere (meaning_noun picks
    -- role='category' with ndv between 2 and 60; derived metrics group by it)
    INSERT INTO rvbbit.meaning_columns AS c
        (relation, col_name, fingerprint, profile, data_type, role, role_p, pii, ndv, null_frac,
         top_values, what, basis, judged_at, judge_model, updated_at)
    VALUES (e.relation, 'label:' || e.name, md5(e.tree::text),
            jsonb_build_object('enrichment_id', e.id, 'view', 'meaning_labels.' || v_view,
                               'expression', e.expression, 'question_kind', e.question->>'kind',
                               'columns', to_jsonb(e.columns), 'keys', to_jsonb(e.key_columns),
                               'sample_n', e.sample_n, 'label_name', e.label_name,
                               'expects', e.expects, 'jev_model', e.jev_model),
            'text', 'category', 1.0, 0, v_labels, v_nulls,
            (SELECT coalesce(jsonb_agg(jsonb_build_object('v', x->>'label', 'n', (x->>'n')::bigint)), '[]'::jsonb)
               FROM jsonb_array_elements(rvbbit._jsonb_arr(e.distribution)) x),
            e.label_name || ' — ' || coalesce(e.why, 'a Jev classification of ' || array_to_string(e.columns, ', ')),
            'enrichment', now(), e.jev_model, now())
    ON CONFLICT (relation, col_name) DO UPDATE SET
        fingerprint = EXCLUDED.fingerprint, profile = EXCLUDED.profile, role = 'category',
        role_p = 1.0, ndv = EXCLUDED.ndv, null_frac = EXCLUDED.null_frac,
        top_values = EXCLUDED.top_values, what = EXCLUDED.what, basis = 'enrichment',
        judged_at = now(), judge_model = EXCLUDED.judge_model, updated_at = now();

    UPDATE rvbbit.meaning_enrichments
       SET status = CASE WHEN status = 'promoted' THEN 'promoted' ELSE 'published' END,
           view_name = 'meaning_labels.' || v_view,
           materialized_n = v_total, error = NULL, updated_at = now()
     WHERE id = p_id;

    PERFORM rvbbit._meaning_enrich_log('enrich_published', e.subject_key,
                jsonb_build_object('id', e.id, 'relation', e.relation, 'name', e.name,
                                   'view', 'meaning_labels.' || v_view, 'labels', v_labels, 'rows', v_total));
    RETURN jsonb_build_object('status', 'published', 'view', 'meaning_labels.' || v_view,
                              'labels', v_labels, 'rows', v_total);
END $fn$;

-- Retire: drop the view, free the values, keep the proposal and the card as
-- memory (role 'unused' takes it out of every dimension reader).
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_retire(p_id bigint, p_reason text DEFAULT 'unused')
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE e rvbbit.meaning_enrichments%ROWTYPE;
BEGIN
    SELECT * INTO e FROM rvbbit.meaning_enrichments WHERE id = p_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'missing'); END IF;
    IF e.view_name IS NOT NULL THEN
        EXECUTE format('DROP VIEW IF EXISTS meaning_labels.%I', split_part(e.view_name, '.', 2));
    END IF;
    DELETE FROM rvbbit.meaning_enrich_values WHERE enrichment_id = p_id;
    UPDATE rvbbit.meaning_columns
       SET role = 'unused', profile = coalesce(profile, '{}'::jsonb) || jsonb_build_object('retired_at', now(), 'retired_reason', p_reason),
           updated_at = now()
     WHERE relation = e.relation AND col_name = 'label:' || e.name;
    UPDATE rvbbit.meaning_enrichments
       SET status = 'retired', error = p_reason, view_name = NULL, materialized_n = 0, watermark = NULL, updated_at = now()
     WHERE id = p_id;
    PERFORM rvbbit._meaning_enrich_log('enrich_retired', e.subject_key,
                jsonb_build_object('id', e.id, 'relation', e.relation, 'name', e.name, 'reason', p_reason));
    RETURN jsonb_build_object('status', 'retired', 'reason', p_reason);
END $fn$;

-- ---------------------------------------------------------------------------
-- Feeding the loop: label sets shared across relations
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_shared_labels(p_subject_key text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH live AS MATERIALIZED (
        SELECT e.id, e.subject_key, e.relation, e.name, e.label_name, e.question,
               (SELECT array_agg(x->>'label' ORDER BY x->>'label') FROM jsonb_array_elements(rvbbit._jsonb_arr(e.distribution)) x) AS labels
          FROM rvbbit.meaning_enrichments e
         WHERE e.status IN ('published', 'promoted')
    ), grouped AS (
        -- Group on the slug, not the human name: the worker keeps a revised
        -- proposal in its original slot while the model is free to rename the
        -- dimension, so label_name drifts and the slug is the stable identity.
        SELECT l.name AS key, min(l.label_name) AS label_name,
               array_agg(DISTINCT l.relation) AS relations,
               -- array_agg over text[] builds a 2-D array and refuses ragged rows,
               -- so carry the widest label set as jsonb instead
               (jsonb_agg(to_jsonb(l.labels) ORDER BY cardinality(l.labels) DESC))->0 AS labels,
               array_agg(DISTINCT l.subject_key) AS subjects
          FROM live l GROUP BY l.name
        HAVING count(DISTINCT l.relation) > 1
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'label_dimension', g.label_name, 'relations', to_jsonb(g.relations),
               'labels', g.labels, 'subjects', to_jsonb(g.subjects))
           ORDER BY cardinality(g.relations) DESC), '[]'::jsonb)
      FROM grouped g
     WHERE p_subject_key IS NULL OR p_subject_key = ANY(g.subjects)
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_shared_labels(text) IS
    'Label dimensions published on more than one relation. Soft evidence that two tables are about the same thing even when no column joins them.';

-- Other relations that carry a label dimension by the same name.
CREATE OR REPLACE FUNCTION rvbbit.meaning_relates(p_relation text, p_max int DEFAULT 6)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'relation', o.relation,
               'name', coalesce(r.business_name, rvbbit.meaning_display_name(r.rel_name)),
               'via', o.label_name, 'kind', 'label') ORDER BY o.relation), '[]'::jsonb)
      FROM (SELECT DISTINCT e2.relation, e2.label_name
              FROM rvbbit.meaning_enrichments e1
              JOIN rvbbit.meaning_enrichments e2
                ON e2.name = e1.name AND e2.relation <> e1.relation
             WHERE e1.relation = lower(p_relation)
               AND e1.status IN ('published', 'promoted') AND e2.status IN ('published', 'promoted')
             LIMIT greatest(p_max, 1)) o
      JOIN rvbbit.meaning_relations r ON r.relation = o.relation
$fn$;

-- ---------------------------------------------------------------------------
-- Desire path: use promotes, disuse retires
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_usage(p_days int DEFAULT 30)
RETURNS TABLE(enrichment_id bigint, view_name text, calls bigint, sessions bigint, last_used timestamptz)
LANGUAGE sql STABLE AS $fn$
    WITH hits AS MATERIALIZED (
        SELECT m.session_id, m.ts, coalesce(m.content, '') || coalesce(m.tool_calls::text, '') AS txt
          FROM rvbbit.hermes_messages m
         WHERE m.ts > now() - make_interval(days => greatest(p_days, 1))
           AND (coalesce(m.content, '') || coalesce(m.tool_calls::text, '')) LIKE '%meaning_labels.%'
    ), folded AS (
        SELECT h.ts, h.txt, coalesce(r.source_session_id::text, cs.id::text, h.session_id) AS thread
          FROM hits h
          LEFT JOIN LATERAL (SELECT sr.source_session_id FROM rvbbit.calliope_specialist_runs sr
                              WHERE sr.hermes_session_id = h.session_id LIMIT 1) r ON true
          LEFT JOIN LATERAL (SELECT x.id FROM rvbbit.calliope_sessions x
                              WHERE x.hermes_session_id = h.session_id LIMIT 1) cs ON true
    )
    SELECT e.id, e.view_name, count(f.ts), count(DISTINCT f.thread), max(f.ts)
      FROM rvbbit.meaning_enrichments e
      LEFT JOIN folded f ON f.txt LIKE '%' || split_part(e.view_name, '.', 2) || '%'
     WHERE e.status IN ('published', 'promoted') AND e.view_name IS NOT NULL
     GROUP BY e.id, e.view_name
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_promote_by_use(
    p_min_sessions int DEFAULT 2, p_days int DEFAULT 30, p_apply boolean DEFAULT false,
    p_budget_usd numeric DEFAULT 2.00)
RETURNS TABLE(enrichment_id bigint, view_name text, sessions bigint, action text, detail jsonb)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE u record; v_run jsonb;
BEGIN
    FOR u IN SELECT * FROM rvbbit.meaning_enrich_usage(p_days) WHERE sessions >= p_min_sessions LOOP
        IF NOT EXISTS (SELECT 1 FROM rvbbit.meaning_enrichments WHERE id = u.enrichment_id AND status = 'published') THEN
            CONTINUE;
        END IF;
        IF NOT p_apply THEN
            RETURN QUERY SELECT u.enrichment_id, u.view_name, u.sessions, 'would_promote'::text, '{}'::jsonb;
            CONTINUE;
        END IF;
        UPDATE rvbbit.meaning_enrichments SET status = 'promoted', updated_at = now() WHERE id = u.enrichment_id;
        v_run := rvbbit.meaning_enrich_materialize(u.enrichment_id, 1000000, p_budget_usd);
        PERFORM rvbbit.meaning_enrich_publish(u.enrichment_id);
        PERFORM rvbbit._meaning_enrich_log('enrich_promoted', NULL,
                    jsonb_build_object('id', u.enrichment_id, 'sessions', u.sessions, 'run', v_run));
        RETURN QUERY SELECT u.enrichment_id, u.view_name, u.sessions, 'promoted'::text, v_run;
    END LOOP;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_prune(p_days int DEFAULT 60, p_apply boolean DEFAULT false)
RETURNS TABLE(enrichment_id bigint, view_name text, action text)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE u record;
BEGIN
    FOR u IN
        SELECT e.id, e.view_name FROM rvbbit.meaning_enrichments e
          LEFT JOIN rvbbit.meaning_enrich_usage(p_days) x ON x.enrichment_id = e.id
         WHERE e.status = 'published' AND e.updated_at < now() - make_interval(days => greatest(p_days, 1))
           AND coalesce(x.sessions, 0) = 0
    LOOP
        IF p_apply THEN
            PERFORM rvbbit.meaning_enrich_retire(u.id, 'unused for ' || p_days || ' days');
            RETURN QUERY SELECT u.id, u.view_name, 'retired'::text;
        ELSE
            RETURN QUERY SELECT u.id, u.view_name, 'would_retire'::text;
        END IF;
    END LOOP;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_status()
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'by_status', (SELECT coalesce(jsonb_object_agg(status, n), '{}'::jsonb)
                        FROM (SELECT status, count(*) AS n FROM rvbbit.meaning_enrichments GROUP BY status) x),
        'labelled_rows', (SELECT count(*) FROM rvbbit.meaning_enrich_values WHERE label IS NOT NULL),
        'spend_24h_usd', rvbbit.meaning_enrich_spend(),
        'shared_dimensions', jsonb_array_length(rvbbit.meaning_shared_labels(NULL)),
        'last_published', (SELECT max(updated_at) FROM rvbbit.meaning_enrichments WHERE status IN ('published', 'promoted')))
$fn$;

-- ---------------------------------------------------------------------------
-- Wire the loop into the existing surfaces (rename + wrap, as 0443 does)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regprocedure('rvbbit.meaning_narrative_queue_base(integer)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_narrative_queue(integer) RENAME TO meaning_narrative_queue_base';
    END IF;
    IF to_regprocedure('rvbbit.meaning_subject_packet_base(text,integer)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_subject_packet(text,integer) RENAME TO meaning_subject_packet_base';
    END IF;
    IF to_regprocedure('rvbbit.meaning_map_packet_base(integer)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_map_packet(integer) RENAME TO meaning_map_packet_base';
    END IF;
    IF to_regprocedure('rvbbit.meaning_column_card_base(text,text,text)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_column_card(text,text,text) RENAME TO meaning_column_card_base';
    END IF;
END $$;

-- The queue gains a fourth kind, after dossiers. A subject is enriched once
-- its dossier is usable, and again when the dossier changes.
CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_queue(p_max int DEFAULT 20)
RETURNS TABLE (kind text, key text, schema_name text, members text[], member_fingerprint text, reason text, priority numeric)
LANGUAGE sql STABLE AS $fn$
    SELECT q.kind, q.key, q.schema_name, q.members, q.member_fingerprint, q.reason, q.priority
      FROM (
        SELECT * FROM rvbbit.meaning_narrative_queue_base(p_max)
        UNION ALL
        SELECT 'enrich'::text, d.domain_key, d.schema_name, d.members, d.member_fingerprint,
               CASE WHEN e.subject_key IS NULL THEN 'new'
                    WHEN coalesce(e.has_error, false) THEN 'retry' ELSE 'dossier_changed' END,
               (cardinality(d.members) * 5)::numeric
          FROM rvbbit.meaning_domains d
          LEFT JOIN LATERAL (
                SELECT x.subject_key, max(x.dossier_at) AS dossier_at, bool_or(x.status = 'error') AS has_error,
                       max(x.updated_at) AS updated_at, max(x.attempts) AS attempts
                  FROM rvbbit.meaning_enrichments x WHERE x.subject_key = d.domain_key GROUP BY x.subject_key) e ON true
         WHERE d.origin = 'map' AND d.status = 'done' AND d.usable
           AND d.dossier_at IS NOT NULL
           AND (e.subject_key IS NULL
                OR (e.dossier_at IS DISTINCT FROM d.dossier_at AND d.dossier_at < now() - interval '5 minutes')
                OR (coalesce(e.has_error, false) AND coalesce(e.attempts, 0) < 4
                    AND e.updated_at < now() - interval '1 hour'))
      ) q
     ORDER BY CASE q.kind WHEN 'recognition' THEN 0 WHEN 'map' THEN 1 WHEN 'dossier' THEN 2 ELSE 3 END,
              q.priority DESC NULLS LAST
     LIMIT p_max
$fn$;

-- The dossier packet learns about label dimensions and shared label sets.
CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_packet(p_key text, p_max_tables int DEFAULT 90)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || jsonb_strip_nulls(jsonb_build_object(
        'label_dimensions', (SELECT jsonb_agg(jsonb_build_object(
                                    'table', c.relation, 'dimension', c.profile->>'label_name',
                                    'view', c.profile->>'view',
                                    'labels', (SELECT jsonb_agg(t->>'v') FROM jsonb_array_elements(rvbbit._jsonb_arr(c.top_values)) t)))
                               FROM rvbbit.meaning_columns c
                              WHERE c.basis = 'enrichment' AND c.role = 'category'
                                AND c.relation = ANY(coalesce((SELECT d.members FROM rvbbit.meaning_domains d
                                                                 WHERE d.domain_key = p_key), '{}'::text[]))),
        'shared_labels', nullif(rvbbit.meaning_shared_labels(NULL), '[]'::jsonb))) END
      FROM (SELECT rvbbit.meaning_subject_packet_base(p_key, p_max_tables) AS base) b
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_map_packet(p_objects_per_source int DEFAULT 30)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || jsonb_strip_nulls(jsonb_build_object(
        'shared_labels', nullif(rvbbit.meaning_shared_labels(NULL), '[]'::jsonb))) END
      FROM (SELECT rvbbit.meaning_map_packet_base(p_objects_per_source) AS base) b
$fn$;

-- A noun gains the tables it relates to by shared label dimension: soft edges
-- between tables that never join.
CREATE OR REPLACE FUNCTION rvbbit.meaning_noun(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL
                ELSE base || rvbbit.meaning_card_extras(lower(p_relation))
                     || jsonb_strip_nulls(jsonb_build_object(
                            'relates', nullif(rvbbit.meaning_relates(lower(p_relation)), '[]'::jsonb))) END
      FROM (SELECT rvbbit.meaning_noun_base(p_relation) AS base) b
$fn$;

-- The column card tells an agent where to group by.
CREATE OR REPLACE FUNCTION rvbbit.meaning_column_card(p_schema text, p_rel text, p_col text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || coalesce((
        SELECT jsonb_strip_nulls(jsonb_build_object(
            'label_dimension', c.profile->>'label_name',
            'group_by', c.profile->>'view',
            'join_on', c.profile->'keys',
            'classified_from', c.profile->'columns',
            'expression', c.profile->>'expression',
            'classified_by', c.profile->>'jev_model'))
          FROM rvbbit.meaning_columns c
         WHERE c.relation = lower(p_schema || '.' || p_rel) AND c.col_name = p_col AND c.basis = 'enrichment'
    ), '{}'::jsonb) END
      FROM (SELECT rvbbit.meaning_column_card_base(p_schema, p_rel, p_col) AS base) b
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_enrich_packet(text,int,int,int) IS
    'What a high-judgement model reads to propose classifications for one subject: the dossier, candidate text/code columns with sampled values, existing label dimensions, and the Jev capability catalog. Personal-data columns never appear.';
COMMENT ON FUNCTION rvbbit.meaning_enrich_register(text,jsonb,text) IS
    'Validate and store one proposal. The model names a relation, its columns and a question; this layer builds every query it will ever run.';
COMMENT ON FUNCTION rvbbit.meaning_enrich_sample(bigint,int) IS
    'Run a proposal over a sample through decision_tree_batch (256 states per call) and record the label distribution.';
COMMENT ON FUNCTION rvbbit.meaning_enrich_publish(bigint) IS
    'Publish a sampled classification: a meaning_labels view keyed to the base relation and a label:<name> column card with role category, which every dimension reader already understands.';
