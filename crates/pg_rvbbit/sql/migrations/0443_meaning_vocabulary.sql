-- 0443_meaning_vocabulary — institutional knowledge that sticks, on the objects (docs/MEANING_PLAN.md).
--
-- Two new statements a person (or Calliope, on a person's word) can make about a
-- relation, kept in meaning_overrides with actor, reason and retirement like every
-- other override:
--   succession: this object replaced others as of a date ("Person replaced Lead on
--               2026-08-10; history before that is backfill; comparisons across the
--               cutover need a warning"). Both sides of the succession carry it.
--   caveat:     a dated note every reader of the card should see.
-- And one federation: the Resolution Ledger's term bindings and the governed metric
-- registry surface on cards, nouns and find hits, including the negative ("the business
-- term 'leads' means salesforce_submitted_leads, not rows of this table").

-- ── meaning_state accepts the new fields ────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_state(p_relation text, p_field text, p_value jsonb, p_reason text DEFAULT NULL, p_col text DEFAULT NULL, p_actor text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint; v_sup text;
BEGIN
    IF p_field NOT IN ('kind', 'business_name', 'what', 'domain_key', 'ignore', 'role', 'ref_target', 'pii', 'succession', 'caveat') THEN
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
            'note', nullif(left(p_value->>'note', 600), '')));
    ELSIF p_field = 'caveat' THEN
        IF jsonb_typeof(p_value) = 'string' THEN p_value := jsonb_build_object('text', p_value #>> '{}'); END IF;
        IF jsonb_typeof(p_value) <> 'object' OR nullif(trim(p_value->>'text'), '') IS NULL THEN
            RAISE EXCEPTION 'meaning_state: caveat needs {text, as_of?: YYYY-MM-DD}';
        END IF;
        p_value := jsonb_strip_nulls(jsonb_build_object('text', left(trim(p_value->>'text'), 600),
                                                        'as_of', CASE WHEN p_value->>'as_of' IS NOT NULL THEN (p_value->>'as_of')::date END));
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
            updated_at = now()
         WHERE relation = lower(p_relation);
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

-- Retire one caveat by its override id (caveats accumulate, so they retire one at a time).
CREATE OR REPLACE FUNCTION rvbbit.meaning_retire_statement(p_override_id bigint, p_reason text DEFAULT NULL)
RETURNS boolean LANGUAGE sql AS $fn$
    UPDATE rvbbit.meaning_overrides SET retired_at = now(), reason = coalesce(reason, '') || CASE WHEN p_reason IS NULL THEN '' ELSE ' | retired: ' || p_reason END
     WHERE override_id = p_override_id AND retired_at IS NULL RETURNING true
$fn$;

-- ── succession, both directions ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_succession(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH live AS (
        SELECT o.relation, o.value, o.reason, o.actor, o.created_at
          FROM rvbbit.meaning_overrides o WHERE o.field = 'succession' AND o.retired_at IS NULL
    ),
    successor AS (
        SELECT jsonb_strip_nulls(jsonb_build_object(
                 'supersedes', l.value->'supersedes', 'effective_from', l.value->>'effective_from',
                 'federation', l.value->>'federation', 'note', l.value->>'note',
                 'stated_by', l.actor, 'reason', l.reason, 'stated_at', l.created_at)) AS j
          FROM live l WHERE l.relation = lower(p_relation) LIMIT 1
    ),
    superseded AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                 'by', l.relation, 'effective_from', l.value->>'effective_from',
                 'federation', l.value->>'federation', 'note', l.value->>'note',
                 'stated_by', l.actor, 'stated_at', l.created_at)) ORDER BY l.created_at DESC) AS j
          FROM live l WHERE l.value->'supersedes' ? lower(p_relation)
    )
    SELECT jsonb_strip_nulls(jsonb_build_object('succeeds', (SELECT j FROM successor), 'superseded_by', (SELECT j FROM superseded)))
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_caveats(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'id', o.override_id, 'text', o.value->>'text', 'as_of', o.value->>'as_of',
               'stated_by', o.actor, 'reason', o.reason, 'stated_at', o.created_at)) ORDER BY o.created_at DESC), '[]'::jsonb)
      FROM rvbbit.meaning_overrides o
     WHERE o.relation = lower(p_relation) AND o.field = 'caveat' AND o.retired_at IS NULL
$fn$;

-- ── vocabulary: the Ledger and the metric registry, seen from one relation ──
-- Term bindings live in rvbbit.calliope_term_bindings (created by the Calliope
-- server, so it may be absent on a bare install): referent {kind: metric|relation|
-- artifact, name | schema+relation | slug}, gloss, status, taught_by.
CREATE OR REPLACE FUNCTION rvbbit.meaning_live_bindings()
RETURNS TABLE (term_norm text, referent jsonb, gloss text, status text, taught_by text, created_at timestamptz)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    IF to_regclass('rvbbit.calliope_term_bindings') IS NULL THEN RETURN; END IF;
    RETURN QUERY EXECUTE
        'SELECT term_norm, referent, gloss, status, taught_by, created_at FROM rvbbit.calliope_term_bindings '
        'WHERE invalidated_at IS NULL AND scope = ''company''';
END $fn$;

-- words a term may collide with: the table name and the business name, singular and plural
CREATE OR REPLACE FUNCTION rvbbit.meaning_name_words(p_relation text)
RETURNS text[] LANGUAGE sql STABLE AS $fn$
    SELECT array_agg(DISTINCT w) FROM (
        SELECT lower(w) AS w FROM rvbbit.meaning_relations m,
               LATERAL unnest(ARRAY[
                   regexp_replace(m.rel_name, '_c$|_hd$', ''),
                   regexp_replace(regexp_replace(m.rel_name, '_c$|_hd$', ''), '_', ' ', 'g'),
                   coalesce(m.business_name, ''),
                   regexp_replace(m.rel_name, '_c$|_hd$', '') || 's',
                   coalesce(m.business_name, '') || 's']) w
         WHERE m.relation = lower(p_relation) AND w <> '' AND w <> 's') x
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_vocabulary(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH words AS (SELECT coalesce(rvbbit.meaning_name_words(p_relation), '{}') AS w),
    b AS (SELECT * FROM rvbbit.meaning_live_bindings()),
    bound_here AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('term', b.term_norm, 'gloss', b.gloss, 'status', b.status, 'taught_by', b.taught_by))) AS j
          FROM b WHERE b.referent->>'kind' = 'relation'
           AND lower(coalesce(b.referent->>'schema', 'public') || '.' || (b.referent->>'relation')) = lower(p_relation)
    ),
    collisions AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'term', b.term_norm, 'means', CASE WHEN b.referent->>'kind' = 'metric' THEN 'metric ' || (b.referent->>'name')
                                                      WHEN b.referent->>'kind' = 'relation' THEN 'table ' || lower(coalesce(b.referent->>'schema', 'public') || '.' || (b.referent->>'relation'))
                                                      ELSE (b.referent->>'kind') || ' ' || coalesce(b.referent->>'slug', '') END,
                   'gloss', b.gloss, 'status', b.status,
                   'note', 'the business term "' || b.term_norm || '" does not mean rows of this table'))) AS j
          FROM b, words
         WHERE lower(b.term_norm) = ANY (words.w)
           AND NOT (b.referent->>'kind' = 'relation'
                    AND lower(coalesce(b.referent->>'schema', 'public') || '.' || (b.referent->>'relation')) = lower(p_relation))
    ),
    governed AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'metric', d.name, 'version', d.version,
                   'canonical_for', d.labels->'canonical_for', 'aliases', d.labels->'aliases', 'not', d.labels->'not',
                   'about', left(d.description, 240))) ORDER BY d.name) AS j
          FROM (SELECT DISTINCT ON (md.name) md.name, md.version, md.labels, md.description
                  FROM rvbbit.relation_refs r JOIN rvbbit.metric_defs md ON md.name = r.ref_name
                 WHERE r.relation = lower(p_relation) AND r.ref_kind = 'metric'
                 ORDER BY md.name, md.version DESC) d
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'bound_terms', (SELECT j FROM bound_here),
        'term_collisions', (SELECT j FROM collisions),
        'governed_metrics', (SELECT j FROM governed)))
$fn$;

-- Governed vocabulary for a search: Ledger terms and metric names/aliases that a
-- query mentions, for find to put above the table hits.
CREATE OR REPLACE FUNCTION rvbbit.meaning_governed_terms(p_query text, p_max int DEFAULT 4)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH q AS (SELECT ' ' || regexp_replace(lower(coalesce(p_query, '')), '[^a-z0-9 ]+', ' ', 'g') || ' ' AS text),
    from_ledger AS (
        SELECT b.term_norm AS term, b.referent, b.gloss, b.status, b.taught_by, 'ledger' AS via
          FROM rvbbit.meaning_live_bindings() b, q
         WHERE position(' ' || lower(b.term_norm) || ' ' IN q.text) > 0
    ),
    from_metrics AS (
        SELECT DISTINCT ON (d.name) a.alias AS term, jsonb_build_object('kind', 'metric', 'name', d.name) AS referent,
               left(d.description, 300) AS gloss, 'registry' AS status, d.owner AS taught_by, 'metric_alias' AS via
          FROM rvbbit.metric_defs d
          CROSS JOIN LATERAL (SELECT lower(x) AS alias FROM jsonb_array_elements_text(coalesce(d.labels->'aliases', '[]'::jsonb) || coalesce(d.labels->'canonical_for', '[]'::jsonb) || to_jsonb(ARRAY[replace(d.name, '_', ' ')])) x) a, q
         WHERE position(' ' || a.alias || ' ' IN q.text) > 0
           AND NOT EXISTS (SELECT 1 FROM from_ledger l WHERE l.referent->>'kind' = 'metric' AND l.referent->>'name' = d.name)
         ORDER BY d.name, d.version DESC
    ),
    all_hits AS (SELECT * FROM from_ledger UNION ALL SELECT * FROM from_metrics)
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'term', h.term, 'via', h.via, 'status', h.status, 'gloss', h.gloss, 'taught_by', h.taught_by,
               'referent', h.referent,
               'metric', CASE WHEN h.referent->>'kind' = 'metric' THEN (
                   SELECT jsonb_strip_nulls(jsonb_build_object('name', d.name, 'version', d.version, 'grain', d.grain,
                              'canonical_for', d.labels->'canonical_for', 'not', d.labels->'not',
                              'source_objects', d.labels->'source_objects', 'about', left(d.description, 300)))
                     FROM rvbbit.metric_defs d WHERE d.name = h.referent->>'name' ORDER BY d.version DESC LIMIT 1) END))
             ORDER BY (h.via = 'ledger') DESC, h.term), '[]'::jsonb)
      FROM (SELECT * FROM all_hits LIMIT p_max) h
$fn$;

-- ── cards and nouns carry it ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_card_extras(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'succession', nullif(rvbbit.meaning_succession(p_relation), '{}'::jsonb),
        'caveats', nullif(rvbbit.meaning_caveats(p_relation), '[]'::jsonb),
        'vocabulary', nullif(rvbbit.meaning_vocabulary(p_relation), '{}'::jsonb)))
$fn$;

-- meaning_card: wrap the existing builder so every reader (describe_table, find,
-- DataRabbit, the packet) sees the statements without re-deriving them.
DO $do$
BEGIN
    IF to_regprocedure('rvbbit.meaning_card_base(text,text)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_card(text, text) RENAME TO meaning_card_base';
    END IF;
END $do$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_card(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || rvbbit.meaning_card_extras(lower(p_schema || '.' || p_rel)) END
      FROM (SELECT rvbbit.meaning_card_base(p_schema, p_rel) AS base) b
$fn$;

DO $do$
BEGIN
    IF to_regprocedure('rvbbit.meaning_noun_base(text)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_noun(text) RENAME TO meaning_noun_base';
    END IF;
END $do$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_noun(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || rvbbit.meaning_card_extras(lower(p_relation)) END
      FROM (SELECT rvbbit.meaning_noun_base(p_relation) AS base) b
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_state(text, text, jsonb, text, text, text) IS
  'State a fact about a relation or column; overrides win. Relation fields: kind, business_name, what, domain_key, ignore, succession {supersedes[], effective_from, federation, note}, caveat {text, as_of}. Column fields: role, ref_target, pii.';
