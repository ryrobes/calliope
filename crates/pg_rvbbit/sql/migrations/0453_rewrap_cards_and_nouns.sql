-- 0453 · put the statements back on cards and nouns
--
-- 0443 turned meaning_card and meaning_noun into thin wrappers: the builder
-- moved under a _base name and the wrapper appended meaning_card_extras
-- (succession, caveats, vocabulary); 0449 added `relates` to the noun. Two
-- later migrations forgot and wrote CREATE OR REPLACE against the wrapper
-- name with a full builder body: 0448 did it to meaning_card (both boxes have
-- served cards without caveats since 2026-09-21), 0452 did it to
-- meaning_noun on the test box. The rule from here: a change to how a card
-- or noun is BUILT goes in meaning_card_base / meaning_noun_base; the
-- wrapper names are spoken for.
--
-- This migration is safe on any install: where a wrapper is intact it only
-- restates the builder and the wrapper; where a wrapper was overwritten, the
-- overwriting body is the newest builder, so it is moved under _base first.

-- ── meaning_card: rescue a clobbered wrapper, then re-wrap ──────────────────
DO $do$
BEGIN
    IF to_regprocedure('rvbbit.meaning_card(text,text)') IS NOT NULL
       AND pg_get_functiondef('rvbbit.meaning_card(text,text)'::regprocedure) NOT ILIKE '%meaning_card_extras%' THEN
        EXECUTE 'DROP FUNCTION IF EXISTS rvbbit.meaning_card_base(text, text)';
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_card(text, text) RENAME TO meaning_card_base';
    END IF;
END $do$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_card(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL ELSE base || rvbbit.meaning_card_extras(lower(p_schema || '.' || p_rel)) END
      FROM (SELECT rvbbit.meaning_card_base(p_schema, p_rel) AS base) b
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_card(text, text) IS 'The card for one relation: meaning_card_base (how it is built) plus meaning_card_extras (what people stated: succession, caveats, vocabulary). Change the builder in meaning_card_base; this wrapper stays thin.';

-- ── meaning_noun: rescue, restate the builder (stated overrides first), re-wrap ──
DO $do$
BEGIN
    IF to_regprocedure('rvbbit.meaning_noun(text)') IS NOT NULL
       AND pg_get_functiondef('rvbbit.meaning_noun(text)'::regprocedure) NOT ILIKE '%meaning_card_extras%' THEN
        EXECUTE 'DROP FUNCTION IF EXISTS rvbbit.meaning_noun_base(text)';
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_noun(text) RENAME TO meaning_noun_base';
    END IF;
END $do$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_noun_base(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'relation', m.relation,
        'name', coalesce(s.o->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(s.o->>'what', m.narrative, m.what),
        'kind', coalesce(s.o->>'kind', m.kind), 'role', m.subject_role,
        'count', m.n_rows,
        'time', m.time_column,
        'dimensions', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', c.col_name, 'values', c.ndv,
                              'top', (SELECT jsonb_agg(jsonb_build_object('v', t->>'v', 'n', (t->>'n')::bigint)) FROM (SELECT t FROM jsonb_array_elements(rvbbit._jsonb_arr(c.top_values)) t LIMIT 5) x)))
                            ORDER BY CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|program|location|campus|region)' THEN 0 ELSE 1 END, c.ndv)
                         FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = m.relation AND c.role = 'category' AND coalesce(c.ndv, 0) BETWEEN 2 AND 60
                                ORDER BY CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|program|location|campus|region)' THEN 0 ELSE 1 END, c.ndv LIMIT 8) c),
        'measures', (SELECT jsonb_agg(c.col_name ORDER BY c.null_frac NULLS LAST)
                       FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = m.relation AND c.role = 'measure'
                              AND NOT (c.col_name ~* '(^|_)(id|latitude|longitude|lat|lon|lng|zip|postal)(_c)?$') ORDER BY c.null_frac NULLS LAST LIMIT 10) c),
        'has', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', x.oname, 'relation', x.relation, 'count', x.n_rows, 'kind', x.kind, 'via', x.from_column,
                          'per_parent', (rvbbit.meaning_link_fanout(x.relation, x.from_column, m.relation)->>'per_parent')::numeric)) ORDER BY x.n_rows DESC NULLS LAST)
                  FROM (SELECT o.relation, o.n_rows, o.kind, l.from_column, coalesce(o.business_name, rvbbit.meaning_display_name(o.rel_name)) AS oname
                  FROM (SELECT l.*, row_number() OVER (PARTITION BY l.from_relation ORDER BY l.match_rate DESC NULLS LAST) AS rn
                          FROM rvbbit.meaning_links l WHERE l.to_relation = m.relation AND l.state = 'verified') l
                  JOIN rvbbit.meaning_relations o ON o.relation = l.from_relation
                 WHERE l.rn = 1 AND o.relation <> m.relation AND coalesce(o.subject_role, '') NOT IN ('plumbing') AND o.kind <> 'history_or_snapshot'
                 ORDER BY o.n_rows DESC NULLS LAST LIMIT 12) x),
        'points_at', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', coalesce(o.business_name, rvbbit.meaning_display_name(o.rel_name)), 'relation', o.relation,
                              'via', l.from_column, 'relationship', rvbbit.meaning_link_fanout(l.from_relation, l.from_column, l.to_relation)->>'relationship'))
                            ORDER BY l.match_rate DESC NULLS LAST)
                      FROM (SELECT * FROM rvbbit.meaning_links l WHERE l.from_relation = m.relation AND l.state = 'verified' ORDER BY l.match_rate DESC NULLS LAST LIMIT 10) l
                      JOIN rvbbit.meaning_relations o ON o.relation = l.to_relation WHERE o.relation <> m.relation),
        'metrics', nullif(rvbbit.meaning_metrics_on(m.relation), '[]'::jsonb),
        'derived_metrics', nullif(rvbbit.meaning_derived_metrics(m.relation), '[]'::jsonb),
        'measured_by', (SELECT jsonb_agg(ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs r WHERE r.relation = m.relation AND r.ref_kind IN ('metric', 'cube') LIMIT 6) x),
        'drift', CASE WHEN jsonb_array_length(rvbbit._jsonb_arr(m.drift->'flags')) > 0 THEN m.drift->'flags' END,
        'touches_30d', (m.usage->>'touches_30d')::int))
      FROM rvbbit.meaning_relations m,
           LATERAL (SELECT rvbbit.meaning_overrides_for(m.relation, NULL) AS o) s
     WHERE m.relation = p_relation
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_noun_base(text) IS 'How one noun of the meaning tree is built: the card seen as a tree node. Name, what and kind read what a person stated before the machine''s narrative or judgement. meaning_noun wraps this with the statements and label relations.';

CREATE OR REPLACE FUNCTION rvbbit.meaning_noun(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN base IS NULL THEN NULL
                ELSE base || rvbbit.meaning_card_extras(lower(p_relation))
                     || jsonb_strip_nulls(jsonb_build_object(
                            'relates', nullif(rvbbit.meaning_relates(lower(p_relation)), '[]'::jsonb))) END
      FROM (SELECT rvbbit.meaning_noun_base(p_relation) AS base) b
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_noun(text) IS 'One noun of the meaning tree: meaning_noun_base plus what people stated (succession, caveats, vocabulary) and `relates` (tables sharing a label dimension). Change the builder in meaning_noun_base; this wrapper stays thin.';
