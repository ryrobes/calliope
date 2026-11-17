-- 0454 · cubes are lenses: a statement about a slice, derived from its definition
--
-- A cube is a product of other tables: someone wrote SQL that says "this slice,
-- at this grain, with these measures, is worth keeping warm". That is a
-- statement, not an observation, so Meaning has nothing to discover about it
-- and the crawler stays out (the `cubes` schema remains plumbing to the sweep:
-- no Jev, no links, no narrative). What a cube gets instead is a DERIVED card
-- built from cube_defs, its columns, the refs snapshot and usage:
--
--   * a row in meaning_relations with status 'derived', kind 'aggregate_or_cube',
--     subject_role 'lens', placed_by 'definition'. Every reader keyed on that
--     table (describe_table, find, the packet, teach_meaning) works unchanged;
--     the status keeps it out of nouns, recognition, dossiers and enrichment.
--   * meaning_lens(relation): grain, what it derives from (each source linked
--     to its noun), which subjects it spans, dimensions/measures/time from its
--     columns, the metrics that read it, refresh state, usage. Served through
--     meaning_card_extras as `lens`, so the card and the noun both carry it.
--   * measured_through on every source relation: the lenses over it and the
--     metrics on each — the path source → lens → metric that was invisible
--     when measured_by only named the cube.
--   * a `lenses` section per subject in meaning_tree: a lens hangs off the
--     nouns it derives from and never competes with them as a noun; one that
--     spans subjects shows in each and says so.
--
-- Refresh: with relation_refs_refresh (server boot, the daily find refresh),
-- after any change to cube_defs (statement trigger), and once here. A stated
-- business_name / what / domain_key on a lens survives every refresh.

-- ── the lens itself ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_lens(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH c AS (
        SELECT d.name, d.version, d.sql, d.grain, d.refresh_cron, d.description, d.category, d.created_at,
               rvbbit.sql_relation_refs(d.sql) AS sources
          FROM rvbbit.cube_defs d
         WHERE lower('cubes.' || d.name) = lower(p_relation)
         ORDER BY d.version DESC LIMIT 1),
    m AS (SELECT * FROM rvbbit.meaning_relations WHERE relation = lower(p_relation)),
    st AS (SELECT s.category, s.refreshed_at, s.last_rows, s.dirty, s.recommended_action
             FROM c, rvbbit.cube_refresh_status s WHERE s.name = c.name),
    cols AS (SELECT ic.column_name, ic.data_type, ic.ordinal_position,
                    ic.data_type IN ('numeric', 'integer', 'bigint', 'smallint', 'double precision', 'real', 'money')
                        AND ic.column_name !~* '(^|_)(year|month|day|week|quarter|hour|id|key|code|number)$' AS is_measure,
                    ic.data_type LIKE 'timestamp%' OR ic.data_type = 'date' AS is_time
               FROM c, information_schema.columns ic WHERE ic.table_schema = 'cubes' AND ic.table_name = c.name)
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'relation', lower(p_relation),
        'name', coalesce(m.business_name, rvbbit.meaning_display_name(c.name)),
        'kind', 'lens',
        'what', m.what,
        'grain', c.grain,
        'category', coalesce((SELECT category FROM st), c.category),
        'version', c.version,
        'rows', m.n_rows,
        'defined_at', c.created_at,
        'refresh', nullif(jsonb_strip_nulls(jsonb_build_object(
                       'cron', c.refresh_cron, 'at', (SELECT refreshed_at FROM st), 'rows', (SELECT last_rows FROM st),
                       'dirty', (SELECT CASE WHEN dirty THEN true END FROM st),
                       'recommended', (SELECT nullif(recommended_action, 'ok') FROM st))), '{}'::jsonb),
        'derives_from', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                 'relation', s.rel,
                                 'name', coalesce(x.business_name, rvbbit.meaning_display_name(split_part(s.rel, '.', 2))),
                                 'subject', coalesce(x.domain_key, (SELECT d.domain_key FROM rvbbit.meaning_domains d
                                                                      WHERE d.status <> 'folded' AND s.rel = ANY(d.members) ORDER BY d.usable DESC LIMIT 1)),
                                 'carded', CASE WHEN x.relation IS NULL THEN false END)) ORDER BY s.rel)
                           FROM unnest(c.sources) AS s(rel) LEFT JOIN rvbbit.meaning_relations x ON x.relation = s.rel),
        'spans', (SELECT jsonb_agg(jsonb_build_object('key', d.domain_key, 'name', d.name) ORDER BY d.name)
                    FROM rvbbit.meaning_domains d WHERE d.status <> 'folded' AND d.members && c.sources),
        'dimensions', (SELECT jsonb_agg(column_name ORDER BY ordinal_position) FROM cols WHERE NOT is_measure AND NOT is_time),
        'measures', (SELECT jsonb_agg(column_name ORDER BY ordinal_position) FROM cols WHERE is_measure),
        'time', (SELECT column_name FROM cols WHERE is_time ORDER BY ordinal_position LIMIT 1),
        'metrics', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', r.ref_name, 'grain', r.ref_meta->>'grain')) ORDER BY r.ref_name)
                      FROM rvbbit.relation_refs r WHERE r.relation = lower(p_relation) AND r.ref_kind = 'metric'),
        'usage', nullif(m.usage, '{}'::jsonb)))
      FROM c LEFT JOIN m ON true
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_lens(text) IS 'A cube read as a lens over its sources: grain, derives_from (each source linked to its noun and subject), spans (subjects touched), dimensions/measures/time from its columns, the metrics that read it, refresh state, usage. Derived from cube_defs; nothing here was judged.';

-- The lenses over one relation and the metrics on each: source → lens → metric.
CREATE OR REPLACE FUNCTION rvbbit.meaning_measured_through(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'lens', 'cubes.' || r.ref_name,
               'name', coalesce(m.business_name, rvbbit.meaning_display_name(r.ref_name)),
               'grain', r.ref_meta->>'grain',
               'metrics', (SELECT jsonb_agg(x.ref_name ORDER BY x.ref_name) FROM rvbbit.relation_refs x
                            WHERE x.relation = 'cubes.' || r.ref_name AND x.ref_kind = 'metric')))
             ORDER BY r.ref_name), '[]'::jsonb)
      FROM rvbbit.relation_refs r
      LEFT JOIN rvbbit.meaning_relations m ON m.relation = 'cubes.' || r.ref_name
     WHERE r.relation = lower(p_relation) AND r.ref_kind = 'cube' AND lower(p_relation) NOT LIKE 'cubes.%'
$fn$;

-- The lenses over one subject: those deriving from any of its members.
CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_lenses(p_subject_key text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(rvbbit.meaning_lens(m.relation)
                              ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.relation), '[]'::jsonb)
      FROM rvbbit.meaning_domains d
      JOIN rvbbit.meaning_relations m ON m.status = 'derived' AND m.subject_role = 'lens'
     WHERE d.domain_key = p_subject_key
       AND EXISTS (SELECT 1 FROM rvbbit.relation_refs r
                    WHERE r.ref_kind = 'cube' AND 'cubes.' || r.ref_name = m.relation AND r.relation = ANY(d.members))
$fn$;

-- ── the derived rows ─────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_lenses_refresh()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_c record; v_rel text; v_sources text[]; v_names text; v_subjects text[]; v_what text;
    v_rows bigint; v_cols int; v_time text; v_usage jsonb; v_metrics int; v_stated jsonb;
    v_n int := 0; v_gone int := 0;
BEGIN
    IF to_regclass('rvbbit.cube_defs') IS NULL OR to_regclass('rvbbit.meaning_relations') IS NULL THEN
        RETURN jsonb_build_object('lenses', 0, 'removed', 0);
    END IF;
    FOR v_c IN SELECT DISTINCT ON (name) name, version, sql, grain, refresh_cron
                 FROM rvbbit.cube_defs ORDER BY name, version DESC LOOP
        v_rel := lower('cubes.' || v_c.name);
        CONTINUE WHEN to_regclass(v_rel) IS NULL;          -- defined but not built yet: nothing to read
        v_sources := rvbbit.sql_relation_refs(v_c.sql);
        SELECT string_agg(coalesce(m.business_name, rvbbit.meaning_display_name(split_part(s.rel, '.', 2))), ', ' ORDER BY s.rel)
          INTO v_names FROM unnest(v_sources) AS s(rel) LEFT JOIN rvbbit.meaning_relations m ON m.relation = s.rel;
        SELECT array_agg(DISTINCT d.domain_key) INTO v_subjects
          FROM rvbbit.meaning_domains d WHERE d.status <> 'folded' AND d.members && v_sources;
        SELECT count(*) INTO v_metrics FROM rvbbit.relation_refs r WHERE r.relation = v_rel AND r.ref_kind = 'metric';
        SELECT n_live_tup INTO v_rows FROM pg_stat_user_tables WHERE schemaname = 'cubes' AND relname = v_c.name;
        SELECT count(*) INTO v_cols FROM information_schema.columns WHERE table_schema = 'cubes' AND table_name = v_c.name;
        SELECT column_name INTO v_time FROM information_schema.columns
         WHERE table_schema = 'cubes' AND table_name = v_c.name AND (data_type LIKE 'timestamp%' OR data_type = 'date')
         ORDER BY ordinal_position LIMIT 1;
        SELECT jsonb_strip_nulls(jsonb_build_object('touches_30d', u.touches_30d, 'users', u.users, 'last_touch', u.last_touch))
          INTO v_usage FROM rvbbit.usage_snapshot u WHERE u.object = v_rel;
        v_stated := rvbbit.meaning_overrides_for(v_rel, NULL);
        v_what := format('Rollup of %s%s; %s metric%s read it%s.',
                         coalesce(v_names, 'its sources'),
                         CASE WHEN v_c.grain IS NOT NULL THEN ' at ' || v_c.grain ELSE '' END,
                         v_metrics, CASE WHEN v_metrics = 1 THEN '' ELSE 's' END,
                         CASE WHEN v_c.refresh_cron IS NOT NULL THEN '; refreshed ' || v_c.refresh_cron ELSE '' END);
        INSERT INTO rvbbit.meaning_relations
               (relation, schema_name, rel_name, source_system, source_role, kind, kind_p, status,
                n_rows, n_columns, time_column, what, business_name, domain_key, confidence, usage,
                subject_role, placed_by, judged_at, updated_at)
        VALUES (v_rel, 'cubes', v_c.name, 'cubes', 'lens', 'aggregate_or_cube', 1, 'derived',
                v_rows, v_cols, v_time,
                coalesce(v_stated->>'what', v_what),
                coalesce(v_stated->>'business_name', rvbbit.meaning_display_name(v_c.name)),
                coalesce(v_stated->>'domain_key', CASE WHEN cardinality(v_subjects) = 1 THEN v_subjects[1] END),
                1, coalesce(v_usage, '{}'::jsonb), 'lens', 'definition', NULL, now())
        ON CONFLICT (relation) DO UPDATE SET
            n_rows = EXCLUDED.n_rows, n_columns = EXCLUDED.n_columns, time_column = EXCLUDED.time_column,
            what = EXCLUDED.what, business_name = EXCLUDED.business_name, domain_key = EXCLUDED.domain_key,
            usage = EXCLUDED.usage, kind = 'aggregate_or_cube', kind_p = 1, status = 'derived',
            subject_role = 'lens', placed_by = 'definition', source_system = 'cubes', source_role = 'lens',
            confidence = 1, updated_at = now()
        WHERE rvbbit.meaning_relations.status = 'derived' OR rvbbit.meaning_relations.schema_name = 'cubes';
        v_n := v_n + 1;
    END LOOP;
    -- a lens whose cube is gone, or whose table was dropped, goes with it
    DELETE FROM rvbbit.meaning_relations m
     WHERE m.status = 'derived' AND m.schema_name = 'cubes'
       AND (to_regclass(m.relation) IS NULL
            OR NOT EXISTS (SELECT 1 FROM rvbbit.cube_defs d WHERE lower('cubes.' || d.name) = m.relation));
    GET DIAGNOSTICS v_gone = ROW_COUNT;
    RETURN jsonb_build_object('lenses', v_n, 'removed', v_gone);
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_lenses_refresh() IS 'Derives one meaning_relations row (status derived, subject_role lens) per built cube from cube_defs, columns, refs and usage; keeps stated business_name/what/domain_key; removes lenses whose cube or table is gone. No model calls.';

-- definitions change → lenses follow, in the same transaction
CREATE OR REPLACE FUNCTION rvbbit.meaning_lenses_on_cube_change()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    BEGIN
        PERFORM rvbbit.meaning_lenses_refresh();
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'meaning lenses: refresh after a cube change failed (%)', SQLERRM;
    END;
    RETURN NULL;
END $fn$;
DROP TRIGGER IF EXISTS meaning_lenses_follow_cube_defs ON rvbbit.cube_defs;
CREATE TRIGGER meaning_lenses_follow_cube_defs
    AFTER INSERT OR UPDATE OR DELETE ON rvbbit.cube_defs
    FOR EACH STATEMENT EXECUTE FUNCTION rvbbit.meaning_lenses_on_cube_change();

-- ── cards and nouns carry the lens and the path through it ──────────────────
-- (meaning_card / meaning_noun are wrappers over _base || extras; see 0453)
CREATE OR REPLACE FUNCTION rvbbit.meaning_card_extras(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'succession', nullif(rvbbit.meaning_succession(p_relation), '{}'::jsonb),
        'caveats', nullif(rvbbit.meaning_caveats(p_relation), '[]'::jsonb),
        'vocabulary', nullif(rvbbit.meaning_vocabulary(p_relation), '{}'::jsonb),
        'lens', CASE WHEN EXISTS (SELECT 1 FROM rvbbit.meaning_relations m
                                   WHERE m.relation = lower(p_relation) AND m.status = 'derived' AND m.subject_role = 'lens')
                     THEN rvbbit.meaning_lens(p_relation) END,
        'measured_through', nullif(rvbbit.meaning_measured_through(p_relation), '[]'::jsonb)))
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_card_extras(text) IS 'What is stated about a relation rather than judged: succession, caveats, vocabulary, the lens (when the relation is a cube) and measured_through (the lenses over it and the metrics on each).';

-- ── the tree gets a lenses section per subject ───────────────────────────────
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
    v_lenses jsonb;
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
    v_lenses := rvbbit.meaning_subject_lenses(p_subject_key);
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'key', d.domain_key, 'about', d.about,
        'tables', cardinality(d.members), 'nouns', cardinality(v_nouns),
        'roles', (SELECT jsonb_object_agg(v, n) FROM (SELECT value AS v, count(*) AS n FROM jsonb_each_text(d.roles) GROUP BY 1) x),
        'questions', d.questions,
        'metrics', rvbbit.meaning_candidate_metrics(p_subject_key),
        'concepts', nullif(v_concepts, '[]'::jsonb),
        'folded', (SELECT sum((c->>'tables')::int) FROM jsonb_array_elements(v_concepts) c),
        'derived', nullif(rvbbit.meaning_subject_derived(p_subject_key, p_max_nouns), '[]'::jsonb),
        'lenses', nullif(v_lenses, '[]'::jsonb),
        'gaps', d.gaps,
        'usable', d.usable,
        'areas', v_areas));
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_tree(text, int) IS 'The meaning tree of one subject: areas → nouns with counts, dimensions, measures, what hangs off them, what they point at, dossier and derived metrics; `concepts` (nouns folded into one thing), `derived` (repeated derived metrics), and `lenses` (cubes over the subject''s nouns, with the metrics that read them). A projection over cards.';

-- ── the refs refresh brings the lenses with it ───────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.relation_refs_refresh()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0 timestamptz := clock_timestamp();
    v_metrics int := 0; v_cubes int := 0; v_dash int := 0; v_domains int := 0;
    v_lenses jsonb;
BEGIN
    DELETE FROM rvbbit.relation_refs;

    IF to_regclass('rvbbit.metric_defs') IS NOT NULL THEN
        -- the latest version of each name, the way metric_catalog picks it;
        -- a name whose latest version is retired is not a metric anyone has
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'metric', m.name,
               jsonb_build_object('version', m.version, 'grain', m.grain, 'category', m.labels->>'category')
          FROM (SELECT DISTINCT ON (name) name, version, sql, grain, labels
                  FROM rvbbit.metric_defs ORDER BY name, created_at DESC, version DESC) m
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(m.sql)) AS r(rel)
         WHERE coalesce(m.labels->>'status', '') <> 'retired'
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_metrics = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.cube_defs') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'cube', c.name,
               jsonb_build_object('version', c.version, 'grain', c.grain, 'refresh_cron', c.refresh_cron)
          FROM (SELECT DISTINCT ON (name) name, version, sql, grain, refresh_cron
                  FROM rvbbit.cube_defs ORDER BY name, version DESC) c
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(c.sql)) AS r(rel)
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_cubes = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.dashboards') IS NOT NULL AND to_regclass('rvbbit.dashboard_versions') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT r.rel, 'dashboard', d.slug,
               jsonb_build_object('name', d.name, 'status', d.status, 'version', v.version,
                                  'owner', d.owner_email, 'updated_at', d.updated_at)
          FROM rvbbit.dashboards d
          JOIN rvbbit.dashboard_versions v ON v.dashboard_id = d.id AND v.version = d.latest_version
               CROSS JOIN LATERAL unnest(rvbbit.sql_relation_refs(
                   coalesce(v.html, '') || ' ' || coalesce(v.manifest::text, '') || ' ' || coalesce(v.source_files::text, '')
               )) AS r(rel)
         WHERE NOT coalesce(d.archived, false)
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_dash = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL THEN
        INSERT INTO rvbbit.relation_refs (relation, ref_kind, ref_name, ref_meta)
        SELECT DISTINCT ON (lower(r.rel), s.domain_key)
               lower(r.rel), 'domain', s.domain_key,
               jsonb_build_object('name', s.name, 'status', s.status, 'lifecycle', s.lifecycle,
                                  'adoption_state', s.adoption_state, 'graph_id', s.graph_id,
                                  'cube_name', s.cube_name, 'primary_metric_key', s.primary_metric_key)
          FROM rvbbit.business_value_graph_summary s
               CROSS JOIN LATERAL unnest(coalesce(s.source_relations, '{}'::text[])) AS r(rel)
         WHERE coalesce(s.usable, false)
         ORDER BY lower(r.rel), s.domain_key,
                  (s.status = 'promoted') DESC, s.updated_at DESC NULLS LAST
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_domains = ROW_COUNT;
    END IF;

    -- the lenses ride with the refs: same source of truth (cube_defs), same cadence
    BEGIN
        v_lenses := rvbbit.meaning_lenses_refresh();
    EXCEPTION WHEN OTHERS THEN
        v_lenses := jsonb_build_object('error', left(SQLERRM, 200));
    END;

    RETURN jsonb_build_object('metrics', v_metrics, 'cubes', v_cubes, 'dashboards', v_dash,
                              'domains', v_domains, 'lenses', v_lenses,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- land the lenses now
DO $$ BEGIN PERFORM rvbbit.meaning_lenses_refresh(); END $$;
