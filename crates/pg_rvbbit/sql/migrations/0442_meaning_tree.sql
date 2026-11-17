-- 0442_meaning_tree — the meaning tree: business nouns with counts (docs/MEANING_PLAN.md).
--
-- A projection over the cards, never a second model. Nodes are tables the
-- subjects placed as core or support (entities, events, facts, rollups); each
-- carries its count, dimensions (category columns with their value counts),
-- measures, the events hanging off it (verified joins in, with fan-out), what
-- it points at (verified joins out), the dossier metrics whose formulas name
-- its columns, and derived metrics read straight off the column roles: counts
-- over time, totals of amount-like measures, averages of rates and durations,
-- breakdowns by dimension, events per entity. Deterministic; Jev may score
-- the derived metrics for worth later.
--
-- Read: rvbbit.meaning_tree(subject_key), rvbbit.meaning_tree_summary(subject_key),
--       rvbbit.meaning_noun(relation), rvbbit.meaning_subjects().

-- Fan-out of a verified join, table-wide: children per parent row.
-- per_parent = from.n_rows × non-null share × match rate ÷ to.n_rows, so
-- "≈20 SMS messages per lead" reads off the catalog row counts, not the
-- sample (a 2k-row sample of 4M rows shows nearly every key once).
-- unique_key: the from-column is unique in the sample → one-to-one shape.
CREATE OR REPLACE FUNCTION rvbbit.meaning_link_fanout(p_from_relation text, p_from_column text, p_to_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'per_parent', CASE WHEN coalesce(t.n_rows, 0) > 0 AND coalesce(f.n_rows, 0) > 0
                           THEN round(f.n_rows * (1 - coalesce(c.null_frac, 0)) * coalesce(l.match_rate, 1) / t.n_rows, 1) END,
        'unique_key', CASE WHEN coalesce(c.ndv, 0) > 0 AND coalesce((c.profile->>'sampled')::numeric, 0) > 0
                           THEN c.ndv >= 0.95 * (c.profile->>'sampled')::numeric * (1 - coalesce(c.null_frac, 0)) END,
        'null_pct', round(coalesce(c.null_frac, 0) * 100),
        'relationship', CASE WHEN coalesce(t.n_rows, 0) = 0 OR coalesce(f.n_rows, 0) = 0 THEN NULL
                             WHEN coalesce(c.ndv, 0) > 0 AND c.ndv >= 0.95 * coalesce((c.profile->>'sampled')::numeric, 0) * (1 - coalesce(c.null_frac, 0))
                                  AND f.n_rows * (1 - coalesce(c.null_frac, 0)) * coalesce(l.match_rate, 1) / t.n_rows <= 1.2 THEN 'one_to_one'
                             ELSE 'many_to_one' END))
      FROM rvbbit.meaning_relations f
      JOIN rvbbit.meaning_relations t ON t.relation = p_to_relation
      LEFT JOIN rvbbit.meaning_columns c ON c.relation = p_from_relation AND c.col_name = p_from_column
      LEFT JOIN LATERAL (SELECT max(match_rate) AS match_rate FROM rvbbit.meaning_links l
                          WHERE l.from_relation = p_from_relation AND l.from_column = p_from_column AND l.to_relation = p_to_relation AND l.state = 'verified') l ON true
     WHERE f.relation = p_from_relation
$fn$;

-- Derived metrics for one noun, read off column roles and names. Plain
-- formulas in words, grain stated, no SQL; a person or an agent turns the
-- ones that matter into governed metrics later (the desire path).
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

    -- the count, over time when there is a time column
    IF m.kind IN ('entity_master', 'event_log', 'transactional_fact') THEN
        v_out := v_out || jsonb_build_object(
            'name', initcap(v_short) || ' count', 'kind', 'count',
            'formula', 'count of rows in ' || m.relation || CASE WHEN m.time_column IS NOT NULL THEN ' by ' || m.time_column ELSE '' END,
            'grain', CASE WHEN m.time_column IS NOT NULL THEN 'per period (' || m.time_column || ')' ELSE 'total' END,
            'why', 'the basic volume of ' || v_short);
    END IF;

    -- dimensions worth a breakdown: low-cardinality categories, named like business facets
    SELECT coalesce(array_agg(c.col_name ORDER BY
               CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|reason|segment|tier|priority|level|program|location|campus|region|owner)' THEN 0 ELSE 1 END,
               c.null_frac NULLS LAST), '{}')
      INTO v_dims
      FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = p_relation AND c.role = 'category' AND coalesce(c.ndv, 0) BETWEEN 2 AND 40
            ORDER BY CASE WHEN c.col_name ~* '(status|stage|type|source|channel|category|kind|state|reason|segment|tier|priority|level|program|location|campus|region|owner)' THEN 0 ELSE 1 END, c.null_frac NULLS LAST LIMIT 4) c;
    IF cardinality(v_dims) > 0 AND m.kind IN ('entity_master', 'event_log', 'transactional_fact') THEN
        v_out := v_out || jsonb_build_object(
            'name', initcap(v_short) || ' by ' || replace(v_dims[1], '_', ' '), 'kind', 'breakdown',
            'formula', 'count of ' || m.relation || ' grouped by ' || array_to_string(v_dims[1:2], ', '),
            'grain', 'per ' || v_dims[1], 'why', 'where the ' || v_short || ' volume sits');
    END IF;

    -- measures: totals for amount-like columns, averages for rates and durations
    FOR v_col IN
        SELECT c.col_name,
               CASE WHEN c.col_name ~* '(amount|revenue|cost|spend|price|fee|tuition|balance|paid|payment|budget|value|total|impressions|clicks|conversions|count|quantity|qty|hours|units)' THEN 'sum'
                    WHEN c.col_name ~* '(rate|pct|percent|ratio|score|latency|minutes|seconds|days|duration|age|ctr|cpc|cpl|cpa|roas)' THEN 'avg'
                    ELSE NULL END AS agg
          FROM rvbbit.meaning_columns c
         WHERE c.relation = p_relation AND c.role = 'measure' AND coalesce(c.null_frac, 0) < 0.9
           AND NOT (c.col_name ~* '(^|_)(id|latitude|longitude|lat|lon|lng|zip|postal|year|month|day|ordinal|position|version|number)(_c)?$')
           AND NOT (c.col_name ~* '(hour|weekday|day_of|month_of|timezone|offset)')
         ORDER BY CASE WHEN c.col_name ~* '(amount|revenue|cost|spend|tuition|balance|paid|payment)' THEN 0 ELSE 1 END, c.null_frac NULLS LAST
         LIMIT 8
    LOOP
        CONTINUE WHEN v_col.agg IS NULL;
        v_out := v_out || jsonb_build_object(
            'name', CASE WHEN v_col.agg = 'sum' THEN 'Total ' ELSE 'Average ' END || replace(regexp_replace(v_col.col_name, '_c$', ''), '_', ' '),
            'kind', v_col.agg,
            'formula', v_col.agg || '(' || v_col.col_name || ') over ' || m.relation || CASE WHEN m.time_column IS NOT NULL THEN ' by ' || m.time_column ELSE '' END,
            'grain', CASE WHEN m.time_column IS NOT NULL THEN 'per period' ELSE 'total' END
                     || CASE WHEN cardinality(v_dims) > 0 THEN ', by ' || v_dims[1] ELSE '' END,
            'why', CASE WHEN v_col.col_name ~* '(amount|revenue|tuition|paid|payment|balance)' THEN 'money moving through ' || v_short
                        WHEN v_col.col_name ~* '(cost|spend|budget)' THEN 'what ' || v_short || ' costs'
                        WHEN v_col.agg = 'avg' THEN 'how ' || v_short || ' performs'
                        ELSE 'volume carried by ' || v_short END);
        EXIT WHEN jsonb_array_length(v_out) >= p_max;
    END LOOP;

    -- events per entity: what hangs off this noun, per row of it
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

-- Dossier metrics whose formula names one of this noun's columns.
CREATE OR REPLACE FUNCTION rvbbit.meaning_metrics_on(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object('name', c->>'name', 'formula', c->>'formula', 'grain', c->>'grain', 'subject', d.name)), '[]'::jsonb)
      FROM rvbbit.meaning_relations m
      JOIN rvbbit.meaning_domains d ON d.domain_key = m.domain_key
      CROSS JOIN LATERAL jsonb_array_elements(d.candidate_metrics) c
     WHERE m.relation = p_relation
       AND (c->>'formula' ILIKE '%' || m.relation || '%' OR c->>'formula' ILIKE '%' || m.rel_name || '.%'
            OR EXISTS (SELECT 1 FROM rvbbit.meaning_columns k
                        WHERE k.relation = m.relation AND k.role IN ('measure', 'category', 'time', 'identifier')
                          AND length(k.col_name) >= 6 AND (c->>'formula') ~* ('(^|[^a-z0-9_])' || k.col_name || '([^a-z0-9_]|$)')))
$fn$;

-- One noun: the card seen as a tree node.
CREATE OR REPLACE FUNCTION rvbbit.meaning_noun(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'relation', m.relation,
        'name', coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(m.narrative, m.what),
        'kind', m.kind, 'role', m.subject_role,
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
      FROM rvbbit.meaning_relations m WHERE m.relation = p_relation
$fn$;

-- The tree for one subject: areas → nouns. Configuration, plumbing and
-- history tables are counted, not drawn.
CREATE OR REPLACE FUNCTION rvbbit.meaning_tree(p_subject_key text, p_max_nouns int DEFAULT 40)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    d        rvbbit.meaning_domains%ROWTYPE;
    v_nouns  text[];
    v_areas  jsonb := '[]'::jsonb;
    v_area   jsonb;
    v_placed text[] := '{}';
    v_in     text[];
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    IF NOT FOUND THEN RETURN NULL; END IF;
    -- nouns: core first, then support entities/events/facts by usage and rows
    SELECT coalesce(array_agg(m.relation ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST), '{}')
      INTO v_nouns
      FROM (SELECT * FROM rvbbit.meaning_relations m
             WHERE m.relation = ANY(d.members) AND m.status = 'judged'
               AND coalesce(m.subject_role, 'support') IN ('core', 'support')
               AND m.kind IN ('entity_master', 'event_log', 'transactional_fact', 'aggregate_or_cube')
             ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
             LIMIT p_max_nouns) m;
    FOR v_area IN SELECT * FROM jsonb_array_elements(d.areas) LOOP
        SELECT coalesce(array_agg(t), '{}') INTO v_in
          FROM jsonb_array_elements_text(rvbbit._jsonb_arr(v_area->'tables')) t WHERE t = ANY(v_nouns) AND NOT (t = ANY(v_placed));
        CONTINUE WHEN cardinality(v_in) = 0;
        v_placed := v_placed || v_in;
        v_areas := v_areas || jsonb_build_object(
            'name', v_area->>'name', 'about', v_area->>'about',
            'nouns', (SELECT jsonb_agg(rvbbit.meaning_noun(r) ORDER BY array_position(v_nouns, r)) FROM unnest(v_in) r),
            'other_tables', (SELECT count(*) FROM jsonb_array_elements_text(rvbbit._jsonb_arr(v_area->'tables')) t WHERE NOT (t = ANY(v_in))));
    END LOOP;
    IF EXISTS (SELECT 1 FROM unnest(v_nouns) r WHERE NOT (r = ANY(v_placed))) THEN
        v_areas := v_areas || jsonb_build_object(
            'name', 'Other', 'nouns', (SELECT jsonb_agg(rvbbit.meaning_noun(r) ORDER BY array_position(v_nouns, r)) FROM unnest(v_nouns) r WHERE NOT (r = ANY(v_placed))));
    END IF;
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'key', d.domain_key, 'about', d.about,
        'tables', cardinality(d.members), 'nouns', cardinality(v_nouns),
        'roles', (SELECT jsonb_object_agg(v, n) FROM (SELECT value AS v, count(*) AS n FROM jsonb_each_text(d.roles) GROUP BY 1) x),
        'questions', d.questions,
        'metrics', d.candidate_metrics,
        'gaps', d.gaps,
        'usable', d.usable,
        'areas', v_areas));
END $fn$;

-- Compact: subject → nouns with counts (a few hundred bytes for the packet).
CREATE OR REPLACE FUNCTION rvbbit.meaning_tree_summary(p_subject_key text, p_max int DEFAULT 12)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'tables', cardinality(d.members),
        'nouns', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name)), 'relation', m.relation,
                                                      'count', m.n_rows, 'kind', m.kind))
                                   ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST)
                    FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members) AND m.status = 'judged'
                           AND coalesce(m.subject_role, 'support') IN ('core', 'support')
                           AND m.kind IN ('entity_master', 'event_log', 'transactional_fact')
                          ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST LIMIT p_max) m)))
      FROM rvbbit.meaning_domains d WHERE d.domain_key = p_subject_key
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_subjects()
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'key', d.domain_key, 'name', d.name, 'tables', cardinality(d.members), 'core', cardinality(d.core), 'usable', d.usable,
               'about', left(d.about, 240),
               'rows', (SELECT sum(m.n_rows) FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members) AND m.kind IN ('entity_master', 'event_log', 'transactional_fact')),
               'top_nouns', (SELECT jsonb_agg(coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name)) ORDER BY array_position(d.core, m.relation))
                               FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.core) ORDER BY array_position(d.core, m.relation) LIMIT 5) m)))
             ORDER BY cardinality(d.members) DESC), '[]'::jsonb)
      FROM rvbbit.meaning_domains d WHERE d.origin = 'map' AND d.status = 'done'
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_tree(text, int) IS 'The meaning tree of one subject: areas → nouns with counts, dimensions, measures, what hangs off them, what they point at, dossier and derived metrics. A projection over cards.';
