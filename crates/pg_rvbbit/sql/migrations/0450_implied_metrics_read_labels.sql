-- 0450 — An implied metric can be broken down by a label dimension, and a
-- column whose name also happens to name a table still counts as a column.
--
-- Two things kept dossier candidates out of the registry on test-hosted1
-- (2026-09-21, 26 of 57 candidates "no_source"):
--
--   1. `meaning_implied_source` treated every formula token that matched some
--      schema or relation name as a table reference rather than a column. The
--      box has an `sh.sales` table, so `SUM(public.superstore.sales)` lost
--      `sales`, ended with zero required columns, and was dropped by the
--      `count(*) > 0` guard. A token is now read as a column whenever it IS a
--      column on one of the subject's own tables, whatever else it names.
--
--   2. A candidate that groups by a published label dimension names a view in
--      the `meaning_labels` schema, which is deliberately never excavated, so
--      the view name looked like a missing column. Label references are now
--      resolved to the enrichment that published them: they leave the required
--      columns, they pin the source to the table the labels were computed over,
--      and they arrive as `label:<name>` in the metric's grain, which is the
--      same breakdown vocabulary every other dimension uses.
--
-- A metric stays one scalar value. "COUNT(note_id) GROUP BY <label>" registers
-- as COUNT(note_id) whose grain names the label, not as a many-row query.

-- The label dimensions a formula or grain refers to, by view name
-- (meaning_labels.x__y), by card column (label:y), or by the view's basename.
CREATE OR REPLACE FUNCTION rvbbit.meaning_formula_labels(p_formula text, p_grain text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH t AS (SELECT lower(coalesce(p_formula, '') || ' ' || coalesce(p_grain, '')) AS txt)
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'enrichment_id', e.id, 'relation', e.relation, 'name', e.name,
               'label_name', e.label_name, 'view', e.view_name,
               'column', 'label:' || e.name, 'keys', to_jsonb(e.key_columns))
           ORDER BY e.id), '[]'::jsonb)
      FROM rvbbit.meaning_enrichments e, t
     WHERE e.status IN ('published', 'promoted') AND e.view_name IS NOT NULL
       AND (t.txt LIKE '%' || lower(e.view_name) || '%'
            OR t.txt LIKE '%' || lower(split_part(e.view_name, '.', 2)) || '%'
            OR t.txt LIKE '%label:' || lower(e.name) || '%')
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_formula_labels(text, text) IS
    'Published label dimensions a candidate metric''s formula or grain refers to, with the table they were computed over and the keys they join on.';

-- The scalar half of a candidate formula: a metric returns one value, so a
-- trailing "grouped by <dimension>" is a grain statement, not part of the
-- aggregate.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_scalar(p_formula text)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT btrim(
        -- " in co.shipments," / " over sales.orders" names the table, which the
        -- source resolver finds from the columns anyway; it is prose here.
        regexp_replace(
            regexp_replace(
                coalesce(p_formula, ''),
                ',?\s*\y(group\s+by|grouped\s+by|broken\s+down\s+by|split\s+by|per\s+published\s+label)\y.*$',
                '', 'i'),
            '\s+\y(in|over|across|from)\s+[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\s*,?', ' ', 'gi'))
$fn$;

-- Same contract as 0445, with the two corrections above.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_source(p_subject_key text, p_formula text, p_grain text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH lab AS MATERIALIZED (
        SELECT rvbbit.meaning_formula_labels(p_formula, p_grain) AS labels
    ),
    members AS MATERIALIZED (
        SELECT unnest(d.members) AS relation FROM rvbbit.meaning_domains d WHERE d.domain_key = p_subject_key
    ),
    -- words the label references own: the schema, the view basename, the slug
    label_words AS (
        SELECT lower(w) AS w
          FROM lab, jsonb_array_elements(lab.labels) l,
               unnest(ARRAY['meaning_labels', split_part(l->>'view', '.', 2), l->>'name']) w
         WHERE w IS NOT NULL AND w <> ''
    ),
    req AS (
        SELECT col
          FROM unnest(rvbbit.meaning_formula_columns(rvbbit.meaning_implied_scalar(p_formula))) AS col
         WHERE NOT EXISTS (SELECT 1 FROM label_words lw WHERE col = lw.w OR lw.w LIKE '%' || col || '%')
           AND (
                -- a real column on one of this subject's tables wins over a
                -- table somewhere else that happens to share the name
                EXISTS (SELECT 1 FROM rvbbit.meaning_columns c JOIN members mm ON mm.relation = c.relation
                         WHERE c.col_name = col)
                OR NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations m
                                WHERE lower(m.schema_name) = col OR lower(m.rel_name) = col)
           )
    ),
    cand AS (
        SELECT m.relation, m.time_column, coalesce(m.subject_role, 'support') AS role, m.kind, m.n_rows,
               coalesce((m.usage->>'touches_30d')::int, 0) AS touches,
               (rvbbit.meaning_layer(m.relation)->>'rank')::int AS layer_rank,
               -- a formula that names a label dimension is about the table those
               -- labels were computed over; nothing else can carry them
               EXISTS (SELECT 1 FROM lab, jsonb_array_elements(lab.labels) l WHERE l->>'relation' = m.relation) AS is_label_home
          FROM rvbbit.meaning_domains d
          JOIN rvbbit.meaning_relations m ON m.relation = ANY(d.members)
         WHERE d.domain_key = p_subject_key AND m.status = 'judged'
           AND coalesce(m.subject_role, 'support') IN ('core', 'support')
           AND ((SELECT count(*) FROM req) > 0
                OR (SELECT jsonb_array_length(labels) FROM lab) > 0)
           AND NOT EXISTS (SELECT 1 FROM req r
                            WHERE NOT EXISTS (SELECT 1 FROM rvbbit.meaning_columns c
                                               WHERE c.relation = m.relation AND c.col_name = r.col))
           AND ((SELECT jsonb_array_length(labels) FROM lab) = 0
                OR EXISTS (SELECT 1 FROM lab, jsonb_array_elements(lab.labels) l WHERE l->>'relation' = m.relation))
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'relation', c.relation, 'time_column', c.time_column, 'role', c.role, 'kind', c.kind, 'rows', c.n_rows,
               'layer', rvbbit.meaning_layer(c.relation)->>'layer',
               'columns', (SELECT jsonb_agg(col) FROM req),
               'scalar_formula', rvbbit.meaning_implied_scalar(p_formula),
               -- grain: the named columns that exist, plus every label dimension
               -- this metric's own table carries
               'grain_columns', (
                    SELECT nullif(jsonb_agg(DISTINCT g), '[]'::jsonb) FROM (
                        SELECT btrim(g) AS g FROM unnest(string_to_array(coalesce(p_grain, ''), ',')) g
                         WHERE btrim(g) <> ''
                           AND EXISTS (SELECT 1 FROM rvbbit.meaning_columns k
                                        WHERE k.relation = c.relation AND k.col_name = btrim(g))
                        UNION ALL
                        SELECT k.col_name FROM rvbbit.meaning_columns k
                         WHERE k.relation = c.relation AND k.basis = 'enrichment' AND k.role = 'category'
                    ) x),
               'label_dimensions', (
                    SELECT nullif(jsonb_agg(l), '[]'::jsonb)
                      FROM lab, jsonb_array_elements(lab.labels) l
                     WHERE l->>'relation' = c.relation),
               'candidates', (SELECT count(*) FROM cand)))
      FROM cand c
     ORDER BY c.is_label_home DESC, (c.role = 'core') DESC, c.layer_rank DESC,
              (c.kind IN ('transactional_fact', 'aggregate_or_cube')) DESC, c.touches DESC, c.n_rows DESC NULLS LAST
     LIMIT 1
$fn$;

-- The registered SQL runs the scalar half; the grain says what it breaks down by.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_sql(p_relation text, p_formula text, p_time_column text DEFAULT NULL)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT 'SELECT ' || rvbbit.meaning_implied_scalar(p_formula) || ' AS value FROM '
           || quote_ident(split_part(p_relation, '.', 1)) || '.' || quote_ident(split_part(p_relation, '.', 2))
           || CASE WHEN nullif(p_time_column, '') IS NOT NULL
                   THEN ' WHERE ' || quote_ident(p_time_column) || ' >= now() - ({days}::int * interval ''1 day'')'
                   ELSE '' END
$fn$;

-- How to break a metric down by one of its label dimensions: the join a person
-- or an agent writes, ready to paste.
CREATE OR REPLACE FUNCTION rvbbit.meaning_metric_label_breakdown(p_metric text, p_days int DEFAULT 30)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH m AS (
        SELECT d.labels, d.sql,
               d.labels->'source_objects'->>0 AS relation,
               coalesce(d.labels->>'scalar_formula', d.labels->>'formula') AS formula,
               -- the metric's own window, kept, but after the join
               -- the stored sql carries {days}; a paste-ready query cannot
               replace(coalesce(substring(d.sql from '(?i)\sWHERE\s.*$'), ''), '{days}', greatest(p_days, 1)::text) AS where_clause
          FROM rvbbit.metric_defs d WHERE d.name = p_metric ORDER BY d.version DESC LIMIT 1
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'dimension', l->>'label_name',
               'column', l->>'column',
               'view', l->>'view',
               'join_on', l->'keys',
               -- The view's key columns are aliased out of the way: a base table
               -- and its label view share key names by construction, so an
               -- unqualified COUNT(note_id) in the formula is otherwise ambiguous.
               'sql', 'SELECT v.' || quote_ident(l->>'name') || ' AS ' || quote_ident(l->>'name')
                      || ', ' || m.formula || ' AS value FROM '
                      || quote_ident(split_part(m.relation, '.', 1)) || '.' || quote_ident(split_part(m.relation, '.', 2))
                      || ' JOIN (SELECT ' || quote_ident(l->>'name')
                      || (SELECT string_agg(', ' || quote_ident(k#>>'{}') || ' AS ' || quote_ident('k' || n), '')
                            FROM jsonb_array_elements(l->'keys') WITH ORDINALITY AS t(k, n))
                      || ' FROM ' || (l->>'view') || ') v ON '
                      || (SELECT string_agg('v.' || quote_ident('k' || n) || ' = '
                                            || quote_ident(split_part(m.relation, '.', 2)) || '.' || quote_ident(k#>>'{}'), ' AND ')
                            FROM jsonb_array_elements(l->'keys') WITH ORDINALITY AS t(k, n))
                      || CASE WHEN m.where_clause <> '' THEN ' ' || m.where_clause ELSE '' END
                      || ' GROUP BY 1 ORDER BY 2 DESC')), '[]'::jsonb)
      FROM m, jsonb_array_elements(coalesce(m.labels->'label_dimensions', '[]'::jsonb)) l
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_metric_label_breakdown(text, int) IS
    'For an implied metric whose grain includes a label dimension: the view, the keys, and the query that groups the metric by the classification.';

-- The registrar itself is unchanged except that a metric now carries the label
-- dimensions it can be broken down by, and describes itself by the scalar half
-- of its formula rather than the prose the dossier wrote.
CREATE OR REPLACE FUNCTION rvbbit.meaning_register_implied_metrics(p_subject_key text DEFAULT NULL, p_apply boolean DEFAULT false)
RETURNS TABLE(subject_key text, candidate text, metric_name text, action text, relation text, value jsonb, note text)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    d        record;
    c        jsonb;
    v_name   text;
    v_acr    text;
    v_src    jsonb;
    v_sql    text;
    v_probe  jsonb;
    v_exist  record;
    v_cover  jsonb;
    v_labels jsonb;
    v_params jsonb;
    v_desc   text;
    v_grain  text;
BEGIN
    FOR d IN SELECT dm.domain_key, dm.name, dm.candidate_metrics FROM rvbbit.meaning_domains dm
              WHERE dm.origin = 'map' AND dm.status = 'done' AND (p_subject_key IS NULL OR dm.domain_key = p_subject_key)
              ORDER BY dm.domain_key
    LOOP
        FOR c IN SELECT x FROM jsonb_array_elements(coalesce(d.candidate_metrics, '[]'::jsonb)) x LOOP
            subject_key := d.domain_key; candidate := c->>'name'; relation := NULL; value := NULL; note := NULL;
            v_name := rvbbit.meaning_metric_slug(c->>'name');
            v_acr  := rvbbit.meaning_metric_acronym(c->>'name');
            metric_name := v_name;
            IF v_name = '' OR nullif(btrim(c->>'formula'), '') IS NULL THEN
                action := 'skipped_bad_candidate'; RETURN NEXT; CONTINUE;
            END IF;

            -- already in the registry?
            SELECT m.labels, m.owner INTO v_exist FROM rvbbit.metric_defs m WHERE m.name = v_name ORDER BY m.version DESC LIMIT 1;
            IF FOUND THEN
                action := CASE WHEN v_exist.labels->>'origin' = 'meaning' THEN
                                    CASE coalesce(v_exist.labels->>'status', 'governed') WHEN 'implied' THEN 'already_implied'
                                                                                          WHEN 'retired' THEN 'retired'
                                                                                          ELSE 'promoted' END
                               ELSE 'exists_governed' END;
                RETURN NEXT; CONTINUE;
            END IF;

            -- covered by a governed metric under another name (an alias or a Ledger term)?
            SELECT g INTO v_cover
              FROM jsonb_array_elements(rvbbit.meaning_governed_terms(lower(regexp_replace(c->>'name', '[()/-]', ' ', 'g')), 3)) g
             WHERE g->'metric'->>'name' IS NOT NULL AND g->'metric'->>'name' <> v_name
               AND coalesce(g->'metric'->>'origin', '') <> 'meaning'
             LIMIT 1;
            IF v_cover IS NOT NULL THEN
                action := 'covered_by_governed'; metric_name := v_cover->'metric'->>'name';
                note := format('"%s" already reaches metric %s', v_cover->>'term', v_cover->'metric'->>'name');
                RETURN NEXT; CONTINUE;
            END IF;

            v_src := rvbbit.meaning_implied_source(d.domain_key, c->>'formula', c->>'grain');
            IF v_src IS NULL OR v_src->>'relation' IS NULL THEN
                action := 'no_source';
                note := 'no member table carries every column of ' || (c->>'formula');
                RETURN NEXT; CONTINUE;
            END IF;
            relation := v_src->>'relation';
            v_sql := rvbbit.meaning_implied_sql(relation, c->>'formula', v_src->>'time_column');
            v_probe := rvbbit.meaning_implied_probe(v_sql);
            IF NOT coalesce((v_probe->>'ok')::boolean, false) THEN
                -- a formula that names another table's columns needs a join: not a one-table implied metric
                action := CASE WHEN v_probe->>'error' ~* 'missing FROM-clause entry' THEN 'needs_join' ELSE 'invalid_sql' END;
                note := v_probe->>'error'; RETURN NEXT; CONTINUE;
            END IF;
            value := v_probe->'value';
            v_params := CASE WHEN v_src->>'time_column' IS NOT NULL THEN jsonb_build_object('days', 30) ELSE '{}'::jsonb END;
            v_grain := coalesce(nullif(btrim(c->>'grain'), ''), 'a single scalar');
            v_desc := coalesce(nullif(btrim(c->>'why'), ''), c->>'name') || ' Implied by the meaning layer from ' || relation
                      || ' as ' || coalesce(v_src->>'scalar_formula', btrim(c->>'formula'))
                      || CASE WHEN v_src->>'time_column' IS NOT NULL THEN ' over the last 30 days by ' || (v_src->>'time_column') || ' (params.days changes the window).' ELSE '.' END
                      || ' Not yet confirmed by a person; say so once when it matters.';
            v_labels := jsonb_strip_nulls(jsonb_build_object(
                'status', 'implied', 'origin', 'meaning',
                'subject', d.domain_key, 'subject_name', d.name,
                'source_objects', jsonb_build_array(relation), 'source_layer', v_src->>'layer', 'source_role', v_src->>'role',
                'aliases', (SELECT jsonb_agg(DISTINCT a) FROM unnest(ARRAY[lower(regexp_replace(c->>'name', '\s*\([^)]*\)', '', 'g')), v_acr]) a WHERE a IS NOT NULL AND a <> ''),
                'formula', btrim(c->>'formula'), 'scalar_formula', v_src->>'scalar_formula',
                'grain_columns', v_src->'grain_columns', 'label_dimensions', v_src->'label_dimensions',
                'why', c->>'why',
                'window', CASE WHEN v_src->>'time_column' IS NOT NULL THEN 'last {days} days' END,
                'registered_at', now(), 'probe', v_probe));
            IF p_apply THEN
                PERFORM rvbbit.define_metric(v_name, v_sql, v_params, v_grain, v_desc, 'meaning', v_labels, NULL);
                BEGIN
                    PERFORM rvbbit.set_category('metric', v_name, d.name, NULL);
                EXCEPTION WHEN OTHERS THEN NULL;
                END;
                INSERT INTO rvbbit.meaning_desire_path_log (action, metric_name, subject_key, detail)
                VALUES ('registered', v_name, d.domain_key, jsonb_build_object('candidate', c->>'name', 'relation', relation, 'value', value, 'sql', v_sql));
                action := 'registered';
            ELSE
                action := 'would_register';
            END IF;
            note := v_sql;
            RETURN NEXT;
        END LOOP;
    END LOOP;
END $fn$;
