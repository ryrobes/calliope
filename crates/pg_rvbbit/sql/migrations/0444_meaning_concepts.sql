-- 0444_meaning_concepts — concepts over nouns, derived metrics with the right
-- grammar, and one card per derived metric the subject repeats (docs/MEANING_PLAN.md §2).
--
-- A noun in the tree is one relation. A warehouse says the same thing many
-- times: the same ad report at ad / ad-group / campaign / advertiser grain, the
-- raw Fivetran table and its dbt package view over the same rows, three
-- versions of a location-allocation fact. Every one of them carried its own
-- "Total spend", so the paid-media tree read as forty enrichments, not
-- inference (40 nouns, 253 derived rows, "Total spend" ×23 on the pilot).
--
-- A concept is the deterministic fold: nouns of one subject that share most of
-- their measures and come from the same source family (or share a name word)
-- are one thing at several grains and layers; so are two same-family tables
-- with the same row count and a shared name word (a raw table and its package
-- view). The canonical member is the core one, else the highest layer, else the
-- most used, else the largest. Metrics attach to the concept once. Nothing is
-- stored: the fold is a projection, like the tree. Entities with no measures
-- (Person = person_c / lead / contact) are not folded here — that is a Jev call.
--
-- Read: rvbbit.meaning_concepts(subject_key), rvbbit.meaning_subject_derived(subject_key),
--       rvbbit.meaning_layer(relation); meaning_tree() now carries `concepts` and `derived`.

-- ── where a relation sits in the pipeline ───────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_layer(p_relation text)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $fn$
    WITH p AS (SELECT lower(split_part(coalesce(p_relation, ''), '.', 1)) AS s, lower(split_part(coalesce(p_relation, ''), '.', 2)) AS r)
    SELECT CASE
        WHEN s ~ '(^|_)marts?($|_)' OR r ~ '^mart_'                                                THEN jsonb_build_object('layer', 'mart', 'rank', 5)
        WHEN s ~ '^(reporting|report|reports|analytics|bi|dw|warehouse|gold)$' OR r ~ '^(fact|fct|dim)_' THEN jsonb_build_object('layer', 'reporting', 'rank', 4)
        WHEN r ~ '(summary|rollup|agg|metrics|performance|snapshot)'                               THEN jsonb_build_object('layer', 'rollup', 'rank', 3)
        WHEN r ~ '^(int|intermediate)_' OR s ~ '^(intermediate|silver)$'                          THEN jsonb_build_object('layer', 'intermediate', 'rank', 2)
        WHEN s ~ '^(.+)_\1$' OR r ~ '__' OR r ~ '^stg_' OR s ~ '^(staging|stg|bronze)$'           THEN jsonb_build_object('layer', 'package', 'rank', 1)
        ELSE jsonb_build_object('layer', 'source', 'rank', 0) END
      FROM p
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_layer(text) IS 'Where a relation sits in the pipeline, from its names: mart 5 > reporting 4 > rollup 3 > intermediate 2 > package 1 > source 0.';

-- The source family a relation belongs to: its recognized source system, else
-- the schema stem (tiktok_ads_tiktok_ads → tiktok_ads).
CREATE OR REPLACE FUNCTION rvbbit.meaning_source_family(p_relation text)
RETURNS text LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(nullif(lower(m.source_system), ''), regexp_replace(lower(m.schema_name), '^(.+)_\1$', '\1'))
      FROM rvbbit.meaning_relations m WHERE m.relation = p_relation
$fn$;

-- Name words that say what a table is about, with the grain and layer words
-- removed (ad_report_daily → {ad}; tiktok_ads__campaign_report → {campaign}).
CREATE OR REPLACE FUNCTION rvbbit.meaning_name_tokens(p_relation text)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $fn$
    SELECT coalesce(array_agg(DISTINCT t), '{}')
      FROM unnest(string_to_array(
               regexp_replace(regexp_replace(lower(split_part(coalesce(p_relation, ''), '.', 2)), '^[a-z0-9]+__', ''), '_v[0-9]+$', ''),
               '_')) t
     WHERE length(t) >= 3 AND t !~ '^[0-9]+$'
       AND t NOT IN ('report', 'reports', 'daily', 'weekly', 'monthly', 'lifetime', 'stats', 'stat', 'fact', 'fct', 'dim', 'mart', 'marts',
                     'stg', 'int', 'all', 'levels', 'level', 'the', 'data', 'table', 'raw', 'base', 'tmp', 'new', 'old', 'per', 'and',
                     'summary', 'snapshot', 'history', 'current', 'latest', 'view', 'agg', 'rollup')
$fn$;

-- Measure column names of a relation (the noun's measures, unclipped).
CREATE OR REPLACE FUNCTION rvbbit.meaning_measure_names(p_relation text)
RETURNS text[] LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(array_agg(c.col_name ORDER BY c.null_frac NULLS LAST, c.col_name), '{}')
      FROM rvbbit.meaning_columns c
     WHERE c.relation = p_relation AND c.role = 'measure'
       AND NOT (c.col_name ~* '(^|_)(id|latitude|longitude|lat|lon|lng|zip|postal)(_c)?$')
$fn$;

-- The nouns the tree draws for a subject, in tree order (core first, then by
-- use and size). One place for the rule so the fold and the tree agree.
CREATE OR REPLACE FUNCTION rvbbit.meaning_tree_nouns(p_subject_key text, p_max_nouns int DEFAULT 40)
RETURNS text[] LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(array_agg(m.relation ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST), '{}')
      FROM rvbbit.meaning_domains d
      JOIN LATERAL (SELECT * FROM rvbbit.meaning_relations m
                     WHERE m.relation = ANY(d.members) AND m.status = 'judged'
                       AND coalesce(m.subject_role, 'support') IN ('core', 'support')
                       AND m.kind IN ('entity_master', 'event_log', 'transactional_fact', 'aggregate_or_cube')
                     ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
                     LIMIT p_max_nouns) m ON true
     WHERE d.domain_key = p_subject_key
$fn$;

-- ── derived metrics: the right grammar ──────────────────────────────────────
-- A rate is never summed. The old rule matched `cost` before `cost_per_*`, so
-- "Total cost per pageview" was offered as a metric; now the rate patterns are
-- tested first and rates, ratios, costs-per and averages only ever average.
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

-- ── the fold ────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_concepts(p_subject_key text, p_max_nouns int DEFAULT 40, p_min_jaccard numeric DEFAULT 0.6)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH RECURSIVE nouns AS (
        SELECT r AS relation, ord FROM unnest(rvbbit.meaning_tree_nouns(p_subject_key, p_max_nouns)) WITH ORDINALITY AS t(r, ord)
    ),
    n AS (
        SELECT x.relation, x.ord, m.n_rows, m.kind, coalesce(m.subject_role, 'support') AS role,
               coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name)) AS name,
               rvbbit.meaning_source_family(x.relation) AS family,
               rvbbit.meaning_name_tokens(x.relation) AS toks,
               rvbbit.meaning_measure_names(x.relation) AS meas,
               (rvbbit.meaning_layer(x.relation)->>'rank')::int AS layer_rank,
               rvbbit.meaning_layer(x.relation)->>'layer' AS layer,
               coalesce((m.usage->>'touches_30d')::int, 0) AS touches
          FROM nouns x JOIN rvbbit.meaning_relations m ON m.relation = x.relation
    ),
    pairs AS (
        SELECT a.relation AS r1, b.relation AS r2
          FROM n a JOIN n b ON a.ord < b.ord
         WHERE cardinality(a.meas) >= 2 AND cardinality(b.meas) >= 2
           AND (
                -- the same report at several grains or layers: most measures shared, same family or a shared name word
                ( (SELECT count(*) FROM unnest(a.meas) x WHERE x = ANY(b.meas))::numeric
                    / nullif((SELECT count(DISTINCT x) FROM unnest(a.meas || b.meas) x), 0) >= p_min_jaccard
                  AND (a.family = b.family OR a.toks && b.toks) )
                -- the same rows seen through another layer: same family, same row count, a shared name word
             OR ( a.family = b.family AND coalesce(a.n_rows, 0) > 0 AND a.n_rows = b.n_rows AND a.toks && b.toks )
           )
    ),
    edges AS (SELECT r1, r2 FROM pairs UNION SELECT r2, r1 FROM pairs),
    reach AS (
        SELECT relation AS a, relation AS b FROM n
        UNION
        SELECT r.a, e.r2 FROM reach r JOIN edges e ON e.r1 = r.b
    ),
    comp AS (SELECT a AS relation, min(b) AS comp_id FROM reach GROUP BY a),
    members AS (
        SELECT c.comp_id, n.*,
               count(*) OVER (PARTITION BY c.comp_id) AS n_members,
               row_number() OVER (PARTITION BY c.comp_id ORDER BY (n.role = 'core') DESC, n.layer_rank DESC, n.touches DESC, n.n_rows DESC NULLS LAST, n.ord) AS rn
          FROM comp c JOIN n ON n.relation = c.relation
    ),
    concepts AS (
        SELECT m.comp_id, min(m.ord) AS first_ord,
               (array_agg(m.relation ORDER BY m.rn))[1] AS canonical,
               (array_agg(m.name ORDER BY m.rn))[1] AS name,
               (array_agg(m.family ORDER BY m.rn))[1] AS family,
               (array_agg(m.kind ORDER BY m.rn))[1] AS kind,
               (array_agg(m.n_rows ORDER BY m.rn))[1] AS n_rows,
               array_agg(m.relation ORDER BY m.rn) AS rels,
               jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'relation', m.relation, 'name', m.name, 'count', m.n_rows, 'layer', m.layer, 'role', m.role, 'kind', m.kind,
                   'same_rows_as', (SELECT o.relation FROM members o
                                     WHERE o.comp_id = m.comp_id AND o.relation <> m.relation AND o.n_rows = m.n_rows AND o.rn < m.rn
                                     ORDER BY o.rn LIMIT 1))) ORDER BY m.rn) AS members_json
          FROM members m
         WHERE m.n_members >= 2
         GROUP BY m.comp_id
    )
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'name', c.name, 'canonical', c.canonical, 'kind', c.kind, 'count', c.n_rows,
               'family', CASE WHEN c.family IS NOT NULL AND position(replace(c.family, '_', ' ') IN lower(c.name)) = 0
                               AND c.family NOT IN ('transform', 'dbt', 'internal', 'warehouse', 'postgres', 'public', 'analytics', 'reporting', 'marts', 'mart')
                              THEN initcap(replace(c.family, '_', ' ')) END,
               'tables', cardinality(c.rels), 'members', c.members_json,
               'layers', (SELECT jsonb_object_agg(layer, k) FROM (SELECT x->>'layer' AS layer, count(*) AS k FROM jsonb_array_elements(c.members_json) x GROUP BY 1) y),
               'measures', (SELECT jsonb_agg(v) FROM (SELECT v FROM unnest(rvbbit.meaning_measure_names(c.canonical)) WITH ORDINALITY AS t(v, o) ORDER BY o LIMIT 10) z),
               'metrics', nullif((SELECT jsonb_agg(DISTINCT mm) FROM unnest(c.rels) r, jsonb_array_elements(rvbbit.meaning_metrics_on(r)) mm), '[]'::jsonb),
               'derived_metrics', nullif(rvbbit.meaning_derived_metrics(c.canonical), '[]'::jsonb)))
             ORDER BY c.first_ord), '[]'::jsonb)
      FROM concepts c
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_concepts(text, int, numeric) IS 'Nouns of a subject folded into concepts: the same thing at several grains and layers (shared measures + same source family or name word; or same family, same row count, shared name word). Canonical member first; metrics attached once. A projection, never stored.';

-- Derived metrics the subject repeats, one card per name: how many nouns carry
-- it and which one to read first (core, then highest layer, then tree order).
CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_derived(p_subject_key text, p_max_nouns int DEFAULT 40, p_max int DEFAULT 24)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH nouns AS (
        SELECT r AS relation, ord FROM unnest(rvbbit.meaning_tree_nouns(p_subject_key, p_max_nouns)) WITH ORDINALITY AS t(r, ord)
    ),
    d AS (
        SELECT x.relation, x.ord, m,
               (SELECT (mr.subject_role = 'core') FROM rvbbit.meaning_relations mr WHERE mr.relation = x.relation) AS is_core,
               (rvbbit.meaning_layer(x.relation)->>'rank')::int AS layer_rank
          FROM nouns x, jsonb_array_elements(rvbbit.meaning_derived_metrics(x.relation)) m
         WHERE m->>'kind' IN ('sum', 'avg')
    ),
    ranked AS (
        SELECT lower(m->>'name') AS key, m->>'name' AS name, m->>'kind' AS kind, relation, m->>'formula' AS formula, m->>'grain' AS grain,
               row_number() OVER (PARTITION BY lower(m->>'name') ORDER BY is_core DESC NULLS LAST, layer_rank DESC, ord) AS rn,
               count(*) OVER (PARTITION BY lower(m->>'name')) AS sources
          FROM d
    )
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'name', r.name, 'kind', r.kind, 'sources', r.sources, 'canonical', r.relation, 'formula', r.formula, 'grain', r.grain,
               'relations', (SELECT jsonb_agg(o.relation ORDER BY o.rn) FROM ranked o WHERE o.key = r.key AND o.rn <= 6)))
             ORDER BY r.sources DESC, r.name), '[]'::jsonb)
      FROM (SELECT * FROM ranked WHERE rn = 1 AND sources >= 2 ORDER BY sources DESC, name LIMIT p_max) r
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_subject_derived(text, int, int) IS 'Derived metrics a subject repeats across its nouns, one entry per name with the source count and the noun to read first.';

-- The dossier's candidate metrics for a subject (a seam: 0445 wraps this to say
-- which are registered as implied metrics).
CREATE OR REPLACE FUNCTION rvbbit.meaning_candidate_metrics(p_subject_key text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(d.candidate_metrics, '[]'::jsonb) FROM rvbbit.meaning_domains d WHERE d.domain_key = p_subject_key
$fn$;

-- ── the tree carries the fold ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_tree(p_subject_key text, p_max_nouns int DEFAULT 40)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    d        rvbbit.meaning_domains%ROWTYPE;
    v_nouns  text[];
    v_areas  jsonb := '[]'::jsonb;
    v_area   jsonb;
    v_placed text[] := '{}';
    v_in     text[];
    v_concepts jsonb;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_nouns := rvbbit.meaning_tree_nouns(p_subject_key, p_max_nouns);
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
    v_concepts := rvbbit.meaning_concepts(p_subject_key, p_max_nouns);
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'key', d.domain_key, 'about', d.about,
        'tables', cardinality(d.members), 'nouns', cardinality(v_nouns),
        'roles', (SELECT jsonb_object_agg(v, n) FROM (SELECT value AS v, count(*) AS n FROM jsonb_each_text(d.roles) GROUP BY 1) x),
        'questions', d.questions,
        'metrics', rvbbit.meaning_candidate_metrics(p_subject_key),
        'concepts', nullif(v_concepts, '[]'::jsonb),
        'folded', (SELECT sum((c->>'tables')::int) FROM jsonb_array_elements(v_concepts) c),
        'derived', nullif(rvbbit.meaning_subject_derived(p_subject_key, p_max_nouns), '[]'::jsonb),
        'gaps', d.gaps,
        'usable', d.usable,
        'areas', v_areas));
END $fn$;

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
                          ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST LIMIT p_max) m),
        'concepts', (SELECT jsonb_agg(jsonb_build_object('name', c->>'name', 'canonical', c->>'canonical', 'tables', (c->>'tables')::int))
                       FROM (SELECT c FROM jsonb_array_elements(rvbbit.meaning_concepts(d.domain_key)) c LIMIT 6) x)))
      FROM rvbbit.meaning_domains d WHERE d.domain_key = p_subject_key
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_tree(text, int) IS 'The meaning tree of one subject: areas → nouns with counts, dimensions, measures, what hangs off them, what they point at, dossier and derived metrics; plus `concepts` (nouns folded into one thing at several grains/layers) and `derived` (repeated derived metrics, one card each). A projection over cards.';
