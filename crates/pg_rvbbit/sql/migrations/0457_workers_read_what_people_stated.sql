-- 0457 · the workers read what people stated, and a subject forgets a table that left it
--
-- Callie curated the Amazon Store sample (subject:product_reviews_voice) on
-- 2026-09-22/23 and left notes for the Meaning worker as a specialist finding
-- (#101, "worker guardrails"). Nothing reads findings, and the review of what
-- the worker actually consumes found four gaps this migration closes:
--
-- 1. THE WRITER. meaning_apply_dossier started from the subject's old roles map,
--    only added or changed roles the dossier mentioned, and derived `core` from
--    that map. A table that had left the subject (co.products, re-homed to
--    sales_catalog_promotions by a stated domain_key) stayed core forever, and
--    the review judged the subject on seven core tables, one of them foreign.
--    Roles are now pruned to members before core is derived, and every subject
--    is repaired once below.
--
-- 2. RE-HOMING. meaning_state(domain_key) moved the relation row but never
--    touched either subject's member list, so a person had to trim members by
--    hand. Stating a subject now removes the table from every other subject's
--    members/core/roles and appends it to the stated one, keeping the role it
--    had. Member fingerprints are deliberately left stale: the mismatch is the
--    signal that queues each touched subject's dossier rewrite.
--
-- 3. THE PROPOSER. The enrich packet listed only published labels, so a label
--    a person retired (product_offering_role, three times) was invisible to the
--    model, and meaning_enrich_register's upsert on (relation, name) would have
--    revived it in place with status=proposed. The packet now carries `retired`
--    (table, name, reason) and `stated` (live caveats and ignored columns per
--    member table); register refuses a retired (relation, name) outright; the
--    dossier packet carries the same `stated` block. A retired label comes back
--    only when a person deletes its row.
--
-- 4. SUGGESTIONS. meaning_derived_metrics pattern-matched measure names into
--    "Average average rating" and "Total price" with no regard for what people
--    stated. A column-level ignore (meaning_state(rel, 'ignore', true, …, col))
--    now drops the column from suggested metrics, breakdown dimensions and
--    classification candidates; a live caveat that names a measure column as a
--    whole word suppresses its auto-aggregate (the person has said something
--    about it; the governed metric, if any, is on the card already).
--
-- No new tables. Bodies are carried forward verbatim from 0441/0444/0449/0455
-- with the edits above; the wrapper trap (0453) does not apply here —
-- meaning_subject_packet is a wrapper over meaning_subject_packet_base and is
-- re-wrapped, not rebuilt.

-- ── helpers ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_column_ignored(p_relation text, p_col text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.meaning_overrides o
         WHERE o.relation = lower(p_relation) AND o.col_name = p_col AND o.field = 'ignore' AND o.retired_at IS NULL
           AND lower(coalesce(o.value #>> '{}', 'true')) NOT IN ('false', 'no', '0', ''))
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_column_ignored(text, text) IS
  'True when a person stated ignore on this column (meaning_state(rel, ''ignore'', true, reason, col)). Ignored columns are left out of suggested metrics, breakdown dimensions and enrichment candidates.';

CREATE OR REPLACE FUNCTION rvbbit.meaning_caveat_names(p_relation text, p_col text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.meaning_overrides o
         WHERE o.relation = lower(p_relation) AND o.field = 'caveat' AND o.retired_at IS NULL
           AND coalesce(o.value->>'text', '') ~* ('\m' || regexp_replace(p_col, '([.^$|()\[\]{}*+?\\])', '\\\1', 'g') || '\M'))
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_caveat_names(text, text) IS
  'True when a live caveat on the relation names the column as a whole word. The auto-suggested aggregate over such a measure is withheld: a person has said something about it.';

CREATE OR REPLACE FUNCTION rvbbit.meaning_packet_stated(p_relations text[])
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'table', r.relation,
               'caveats', nullif((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('text', left(o.value->>'text', 1200), 'as_of', o.value->>'as_of')) ORDER BY o.created_at DESC)
                                    FROM rvbbit.meaning_overrides o
                                   WHERE o.relation = r.relation AND o.field = 'caveat' AND o.col_name IS NULL AND o.retired_at IS NULL), '[]'::jsonb),
               'ignore_columns', nullif((SELECT jsonb_agg(DISTINCT o.col_name)
                                           FROM rvbbit.meaning_overrides o
                                          WHERE o.relation = r.relation AND o.col_name IS NOT NULL AND rvbbit.meaning_column_ignored(r.relation, o.col_name)), '[]'::jsonb)))
             ORDER BY r.relation), '[]'::jsonb)
      FROM (SELECT DISTINCT lower(x) AS relation FROM unnest(coalesce(p_relations, '{}'::text[])) x) r
     WHERE EXISTS (SELECT 1 FROM rvbbit.meaning_overrides o
                    WHERE o.relation = r.relation AND o.retired_at IS NULL
                      AND ((o.field = 'caveat' AND o.col_name IS NULL) OR (o.field = 'ignore' AND o.col_name IS NOT NULL)))
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_packet_stated(text[]) IS
  'What people stated about these relations, shaped for a worker packet: live caveats and ignored columns per table (only tables that have any). Names and `what` already reach packets through the relation row.';

-- ── 1. the dossier writer ───────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_apply_dossier(
    p_key text, p_output jsonb, p_packet jsonb, p_model text, p_tokens_in int, p_tokens_out int, p_cost numeric)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    d       rvbbit.meaning_domains%ROWTYPE;
    v_rel   text;
    v_role  text;
    v_roles jsonb := '{}'::jsonb;
    v_core  text[] := '{}';
    v_pair  record;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_key;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'missing'); END IF;
    -- per-table roles from the dossier, for members only; unmentioned members keep the map's role
    -- 0457: a table that left the subject (re-homed by a person, or trimmed by hand) drops out of
    -- roles here, so core — derived from roles below — can never name a table that is not a member.
    SELECT coalesce(jsonb_object_agg(e.k, e.v), '{}'::jsonb) INTO v_roles
      FROM jsonb_each_text(coalesce(d.roles, '{}'::jsonb)) AS e(k, v) WHERE e.k = ANY(d.members);
    FOR v_pair IN SELECT key, value FROM jsonb_each_text(CASE WHEN jsonb_typeof(p_output->'roles') = 'object' THEN p_output->'roles' ELSE '{}'::jsonb END) LOOP
        v_rel := lower(v_pair.key);
        CONTINUE WHEN NOT (v_rel = ANY(d.members));
        v_role := CASE lower(v_pair.value) WHEN 'core' THEN 'core' WHEN 'support' THEN 'support' WHEN 'configuration' THEN 'configuration' WHEN 'plumbing' THEN 'plumbing' ELSE NULL END;
        CONTINUE WHEN v_role IS NULL;
        v_roles := v_roles || jsonb_build_object(v_rel, v_role);
    END LOOP;
    SELECT coalesce(array_agg(k), '{}') INTO v_core FROM jsonb_each_text(v_roles) AS e(k, v) WHERE v = 'core';
    UPDATE rvbbit.meaning_domains SET
        about = coalesce(left(btrim(p_output->>'about'), 1600), about),
        questions = CASE WHEN jsonb_array_length(rvbbit._jsonb_arr(p_output->'questions')) > 0 THEN rvbbit._jsonb_arr(p_output->'questions') ELSE questions END,
        candidate_metrics = rvbbit._jsonb_arr(p_output->'candidate_metrics'),
        areas = rvbbit._jsonb_arr(p_output->'areas'),
        gaps = rvbbit._jsonb_arr(p_output->'gaps'),
        roles = v_roles, core = v_core,
        packet = p_packet, output = p_output, model = p_model, tokens_in = p_tokens_in, tokens_out = p_tokens_out,
        cost_usd = cost_usd + coalesce(p_cost, 0), status = 'done', error = NULL, attempts = attempts + 1,
        dossier_at = now(), review = NULL, usable = false, updated_at = now()
     WHERE domain_key = p_key;
    UPDATE rvbbit.meaning_relations m SET subject_role = v_roles->>m.relation, updated_at = now()
     WHERE m.domain_key = p_key AND v_roles ? m.relation AND m.placed_by IS DISTINCT FROM 'override';
    RETURN jsonb_build_object('core', cardinality(v_core), 'members', cardinality(d.members));
END $fn$;

-- one-time repair: every subject's roles pruned to its members, core re-derived
UPDATE rvbbit.meaning_domains d
   SET roles = pr.roles,
       core = ARRAY(SELECT e.k FROM jsonb_each_text(pr.roles) AS e(k, v) WHERE e.v = 'core' ORDER BY array_position(d.members, e.k)),
       updated_at = now()
  FROM (SELECT x.domain_key,
               coalesce((SELECT jsonb_object_agg(e.k, e.v) FROM jsonb_each_text(coalesce(x.roles, '{}'::jsonb)) AS e(k, v) WHERE e.k = ANY(x.members)), '{}'::jsonb) AS roles
          FROM rvbbit.meaning_domains x) pr
 WHERE pr.domain_key = d.domain_key
   AND (d.roles IS DISTINCT FROM pr.roles
        OR d.core IS DISTINCT FROM ARRAY(SELECT e.k FROM jsonb_each_text(pr.roles) AS e(k, v) WHERE e.v = 'core' ORDER BY array_position(d.members, e.k)));

-- ── 2. stating a subject re-homes the table ─────────────────────────────────
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
        IF p_field = 'domain_key' THEN
            -- 0457: a re-homed table leaves every other subject's members, core and roles and joins the
            -- stated subject with the role it already had. Member fingerprints are left alone on purpose:
            -- the mismatch is what queues each touched subject's dossier rewrite.
            UPDATE rvbbit.meaning_domains
               SET members = array_remove(members, lower(p_relation)), core = array_remove(core, lower(p_relation)),
                   roles = coalesce(roles, '{}'::jsonb) - lower(p_relation), updated_at = now()
             WHERE (lower(p_relation) = ANY(members) OR lower(p_relation) = ANY(core) OR coalesce(roles, '{}'::jsonb) ? lower(p_relation))
               AND domain_key IS DISTINCT FROM (p_value #>> '{}');
            UPDATE rvbbit.meaning_domains d
               SET members = array_append(d.members, lower(p_relation)),
                   roles = coalesce(d.roles, '{}'::jsonb) || jsonb_build_object(lower(p_relation), coalesce(m.subject_role, 'support')),
                   core = CASE WHEN coalesce(m.subject_role, 'support') = 'core' THEN array_append(d.core, lower(p_relation)) ELSE d.core END,
                   updated_at = now()
              FROM rvbbit.meaning_relations m
             WHERE m.relation = lower(p_relation) AND d.domain_key = (p_value #>> '{}') AND NOT (lower(p_relation) = ANY(d.members));
            UPDATE rvbbit.meaning_relations SET placed_by = 'override', updated_at = now()
             WHERE relation = lower(p_relation) AND placed_by IS DISTINCT FROM 'override';
        END IF;
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
  'State a fact about a relation or column; overrides win. Relation fields: kind, business_name, what, domain_key (0457: also moves the table between subjects'' member lists), ignore, keys [column, …] (sets entity_keys; the next enrichment keys its labels by them), succession {supersedes, effective_from, federation?, note?}, caveat {text, as_of?} (caveats accumulate). Column fields (p_col set): role, ref_target, pii, ignore (0457: an ignored column is left out of suggested metrics, breakdown dimensions and enrichment candidates). Caveats and column ignores reach the dossier and enrich packets as `stated`.';

-- ── 3. the packets carry what people stated; the proposer sees what was withdrawn ──
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
        'shared_labels', nullif(rvbbit.meaning_shared_labels(NULL), '[]'::jsonb),
        'stated', nullif(rvbbit.meaning_packet_stated((SELECT d.members FROM rvbbit.meaning_domains d WHERE d.domain_key = p_key)), '[]'::jsonb))) END
      FROM (SELECT rvbbit.meaning_subject_packet_base(p_key, p_max_tables) AS base) b
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_subject_packet(text, integer) IS
  'The dossier packet: meaning_subject_packet_base plus the label dimensions Meaning published over its tables, the labels shared across tables, and (0457) `stated` — live caveats and ignored columns people put on member tables. Change the builder in meaning_subject_packet_base; this wrapper stays thin.';

CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_candidates(p_relation text, p_max int DEFAULT 6)
RETURNS TABLE(col_name text, role text, what text, ndv bigint, null_frac numeric, top_values jsonb)
LANGUAGE sql STABLE AS $fn$
    SELECT c.col_name, c.role, c.what, c.ndv, c.null_frac, c.top_values
      FROM rvbbit.meaning_columns c
     WHERE c.relation = lower(p_relation)
       AND coalesce(c.pii, 0) < 0.6
       AND coalesce(c.null_frac, 0) < 0.9
       AND c.role IS DISTINCT FROM 'structured_blob'
       AND NOT rvbbit.meaning_column_ignored(c.relation, c.col_name)   -- 0457
       AND (c.role = 'free_text'
            OR (c.role = 'category' AND coalesce(c.ndv, 0) > 12)
            OR (c.role = 'identifier' AND coalesce(c.data_type, '') ~* '(text|char)' AND coalesce(c.ndv, 0) > 60
                AND c.col_name !~* '(^|_)(id|uuid|guid|key|sha|hash)(_c)?$'))
     ORDER BY CASE c.role WHEN 'free_text' THEN 0 WHEN 'category' THEN 1 ELSE 2 END,
              coalesce(c.ndv, 0) DESC, c.col_name
     LIMIT greatest(p_max, 1)
$fn$;

-- ── 3b. the enrich packet ──────────────────────────────────────────────────
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
        'stated', nullif(rvbbit.meaning_packet_stated(d.members), '[]'::jsonb),
        'retired', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                            'table', e.relation, 'name', e.name, 'label_name', e.label_name,
                            'reason', left(e.error, 240), 'retired_at', e.updated_at)) ORDER BY e.updated_at DESC)
                      FROM rvbbit.meaning_enrichments e
                     WHERE e.status = 'retired' AND e.relation = ANY(d.members)),
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

-- ── 4. register refuses what was withdrawn ─────────────────────────────────
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

    -- 0457: a label a person (or the disuse pruner) withdrew stays withdrawn. The upsert below keys on
    -- (relation, name) and resets status to proposed, so without this a re-proposal revived it in place.
    SELECT left(e.error, 240) INTO v_reason FROM rvbbit.meaning_enrichments e
     WHERE e.relation = v_rel AND e.name = v_name AND e.status = 'retired';
    IF FOUND THEN
        PERFORM rvbbit._meaning_enrich_log('enrich_rejected', p_subject_key,
                    jsonb_build_object('relation', v_rel, 'name', v_name, 'reason', 'retired_label', 'retired_for', v_reason));
        RETURN jsonb_build_object('rejected', 'retired_label', 'relation', v_rel, 'name', v_name, 'retired_for', v_reason);
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

-- ── 5. suggestions honour ignores and caveats ──────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_derived_metrics(p_relation text, p_max int DEFAULT 12)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    m        rvbbit.meaning_relations%ROWTYPE;
    v_name   text;
    v_out    jsonb := '[]'::jsonb;
    v_col    record;
    v_dims   text[];
    v_short  text;
BEGIN
    SELECT * INTO m FROM rvbbit.meaning_relations WHERE relation = p_relation;
    IF NOT FOUND THEN RETURN v_out; END IF;
    v_name := coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name));
    v_short := lower(regexp_replace(v_name, '\s*\(.*\)$', ''));

    IF m.kind IN ('entity_master', 'event_log', 'transactional_fact') THEN
        v_out := v_out || jsonb_build_object(
            'name', initcap(v_short) || ' count', 'kind', 'count',
            'formula', 'count of rows in ' || m.relation || CASE WHEN m.time_column IS NOT NULL THEN ' by ' || m.time_column ELSE '' END,
            'grain', CASE WHEN m.time_column IS NOT NULL THEN 'per period (' || m.time_column || ')' ELSE 'total' END,
            'why', 'the basic volume of ' || v_short);
    END IF;

    SELECT coalesce(array_agg(c.col_name ORDER BY
               CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|reason|segment|tier|priority|level|program|location|campus|region|owner)' THEN 0 ELSE 1 END,
               c.null_frac NULLS LAST), '{}')
      INTO v_dims
      FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = p_relation AND c.role = 'category' AND coalesce(c.ndv, 0) BETWEEN 2 AND 40
              AND NOT rvbbit.meaning_column_ignored(c.relation, c.col_name)
            ORDER BY CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|reason|segment|tier|priority|level|program|location|campus|region|owner)' THEN 0 ELSE 1 END, c.null_frac NULLS LAST LIMIT 4) c;
    IF cardinality(v_dims) > 0 AND m.kind IN ('entity_master', 'event_log', 'transactional_fact') THEN
        v_out := v_out || jsonb_build_object(
            'name', initcap(v_short) || ' by ' || replace(v_dims[1], '_', ' '), 'kind', 'breakdown',
            'formula', 'count of ' || m.relation || ' grouped by ' || array_to_string(v_dims[1:2], ', '),
            'grain', 'per ' || v_dims[1], 'why', 'where the ' || v_short || ' volume sits');
    END IF;

    -- measures: rates, ratios, costs-per, scores and durations average; amounts and counts total
    FOR v_col IN
        SELECT c.col_name,
               CASE WHEN c.col_name ~* '(rate|pct|percent|ratio|score|latency|minutes|seconds|days|duration|age|ctr|cpc|cpl|cpa|cpm|cpv|roas|roi|frequency|cost_per|value_per|_per_|^per_|^avg_|^average_|^mean_|_avg$|_average$|_mean$)' THEN 'avg'
                    WHEN c.col_name ~* '(amount|revenue|cost|spend|price|fee|tuition|balance|paid|payment|budget|value|total|impressions|clicks|conversions|count|quantity|qty|hours|units|reach|views|visits|likes|shares|comments|engagements)' THEN 'sum'
                    ELSE NULL END AS agg
          FROM rvbbit.meaning_columns c
         WHERE c.relation = p_relation AND c.role = 'measure' AND coalesce(c.null_frac, 0) < 0.9
           AND NOT rvbbit.meaning_column_ignored(c.relation, c.col_name)   -- 0457: a stated column ignore
           AND NOT rvbbit.meaning_caveat_names(c.relation, c.col_name)    -- 0457: a live caveat names the column
           AND NOT (c.col_name ~* '(^|_)(id|latitude|longitude|lat|lon|lng|zip|postal|year|month|day|ordinal|position|version|number)(_c)?$')
           AND NOT (c.col_name ~* '(hour|weekday|day_of|month_of|timezone|offset)')
         ORDER BY CASE WHEN c.col_name ~* '(amount|revenue|cost|spend|tuition|balance|paid|payment)' AND NOT c.col_name ~* '(cost_per|_per_|rate)' THEN 0 ELSE 1 END, c.null_frac NULLS LAST
         LIMIT 8
    LOOP
        CONTINUE WHEN v_col.agg IS NULL;
        v_out := v_out || jsonb_build_object(
            'name', CASE WHEN v_col.agg = 'sum' THEN 'Total ' ELSE 'Average ' END || replace(regexp_replace(v_col.col_name, '_c$', ''), '_', ' '),
            'kind', v_col.agg,
            'formula', v_col.agg || '(' || v_col.col_name || ') over ' || m.relation || CASE WHEN m.time_column IS NOT NULL THEN ' by ' || m.time_column ELSE '' END,
            'grain', CASE WHEN m.time_column IS NOT NULL THEN 'per period' ELSE 'total' END
                     || CASE WHEN cardinality(v_dims) > 0 THEN ', by ' || v_dims[1] ELSE '' END,
            'why', CASE WHEN v_col.agg = 'avg' THEN 'how ' || v_short || ' performs'
                        WHEN v_col.col_name ~* '(amount|revenue|tuition|paid|payment|balance)' THEN 'money moving through ' || v_short
                        WHEN v_col.col_name ~* '(cost|spend|budget)' THEN 'what ' || v_short || ' costs'
                        ELSE 'volume carried by ' || v_short END);
        EXIT WHEN jsonb_array_length(v_out) >= p_max;
    END LOOP;

    IF m.kind = 'entity_master' THEN
        FOR v_col IN
            SELECT l.from_relation, l.from_column, o.n_rows, coalesce(o.business_name, rvbbit.meaning_display_name(o.rel_name)) AS oname,
                   (rvbbit.meaning_link_fanout(l.from_relation, l.from_column, p_relation)->>'per_parent')::numeric AS per_key
              FROM rvbbit.meaning_links l JOIN rvbbit.meaning_relations o ON o.relation = l.from_relation
             WHERE l.to_relation = p_relation AND l.state = 'verified' AND o.kind IN ('event_log', 'transactional_fact')
             ORDER BY o.n_rows DESC NULLS LAST LIMIT 3
        LOOP
            v_out := v_out || jsonb_build_object(
                'name', initcap(lower(v_col.oname)) || ' per ' || v_short, 'kind', 'per_entity',
                'formula', 'count of ' || v_col.from_relation || ' per ' || v_col.from_column || ' (≈' || coalesce(v_col.per_key::text, '?') || ' per ' || v_short || ')',
                'grain', 'per ' || v_short, 'why', 'how much ' || lower(v_col.oname) || ' activity each ' || v_short || ' generates');
            EXIT WHEN jsonb_array_length(v_out) >= p_max;
        END LOOP;
    END IF;
    RETURN v_out;
END $fn$;

COMMENT ON FUNCTION rvbbit.meaning_enrich_register(text, jsonb, text) IS
  'Register one proposal after hard validation. 0457: a (relation, name) that was retired is refused with rejected=retired_label rather than revived; delete the retired row to let it be proposed again.';
COMMENT ON FUNCTION rvbbit.meaning_derived_metrics(text, integer) IS
  'Deterministic metric suggestions for one noun (count, breakdown, avg/sum per measure, per-entity fan-out). 0457: columns a person ignored are skipped; a measure a live caveat names as a whole word gets no auto-aggregate.';
