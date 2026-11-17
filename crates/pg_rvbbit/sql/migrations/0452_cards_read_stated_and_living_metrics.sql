-- 0452 · the tree reads what people stated, and cards count only living metrics
--
-- Two things Callie found on the test box after teaching Meaning about the
-- store catalog tables, both in readers rather than in what was written:
--
--   1. She stated `what` for three relations; the card showed it, the tree
--      did not. meaning_noun read coalesce(narrative, what): the machine's
--      one-line dossier sentence outranked the person's statement, and
--      neither the noun nor meaning_tree_summary consulted meaning_overrides
--      at all. They now read stated overrides first, as meaning_card does.
--      The narrative stays: it is the machine's reading and the narrator
--      already sees stated overrides the next time it writes one.
--
--   2. `measured_by.metrics` named metrics that no longer exist. The
--      relation_refs snapshot took every metric_defs name, retired ones
--      included, while metric_catalog hides them. After the 2026-09-21 reset
--      61 refs on 46 relations were retired metrics, and for 11 relations the
--      whole "already measured" verdict rested on a dead one. The refresher
--      now skips defs whose latest version is retired, picking the latest the
--      way metric_catalog does, and refreshes once here so the change lands
--      without waiting for the next tick.

-- ── nouns and summaries read stated overrides first ────────────────────────

CREATE OR REPLACE FUNCTION rvbbit.meaning_noun(p_relation text)
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

COMMENT ON FUNCTION rvbbit.meaning_noun(text) IS 'One noun of the meaning tree: the card seen as a tree node. Name, what and kind read what a person stated (meaning_overrides) before the machine''s narrative or judgement.';

CREATE OR REPLACE FUNCTION rvbbit.meaning_tree_summary(p_subject_key text, p_max int DEFAULT 12)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'tables', cardinality(d.members),
        'nouns', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', coalesce(rvbbit.meaning_overrides_for(m.relation, NULL)->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
                                                      'relation', m.relation, 'count', m.n_rows, 'kind', m.kind))
                                   ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST)
                    FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members) AND m.status = 'judged'
                           AND coalesce(m.subject_role, 'support') IN ('core', 'support')
                           AND m.kind IN ('entity_master', 'event_log', 'transactional_fact')
                          ORDER BY (m.relation = ANY(d.core)) DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST LIMIT p_max) m),
        'concepts', (SELECT jsonb_agg(jsonb_build_object('name', c->>'name', 'canonical', c->>'canonical', 'tables', (c->>'tables')::int))
                       FROM (SELECT c FROM jsonb_array_elements(rvbbit.meaning_concepts(d.domain_key)) c LIMIT 6) x)))
      FROM rvbbit.meaning_domains d WHERE d.domain_key = p_subject_key
$fn$;

-- ── the refs snapshot counts only metrics that are still alive ─────────────

CREATE OR REPLACE FUNCTION rvbbit.relation_refs_refresh()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_t0 timestamptz := clock_timestamp();
    v_metrics int := 0; v_cubes int := 0; v_dash int := 0; v_domains int := 0;
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

    RETURN jsonb_build_object('metrics', v_metrics, 'cubes', v_cubes, 'dashboards', v_dash,
                              'domains', v_domains,
                              'elapsed_ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

COMMENT ON FUNCTION rvbbit.relation_refs_refresh() IS 'Rebuilds relation_refs: which metrics, cubes, dashboards and domains name each relation in their SQL. Metrics count only while their latest version is not retired, matching metric_catalog.';

-- land it now rather than at the next tick, so cards stop naming dead metrics today
DO $$ BEGIN
    IF to_regclass('rvbbit.relation_refs') IS NOT NULL THEN
        PERFORM rvbbit.relation_refs_refresh();
    END IF;
END $$;
