-- 0448_meaning_just_works — Meaning on a fresh appliance without an operator.
--
-- test-hosted1, 2026-09-21: a person uploaded tables into public and a new
-- store schema and waited a day. Nothing was analyzed, because
-- business_sources_populate() had run once, at migration time, defaulting
-- every unclassified schema to 'unknown', and the sweep only registers
-- relations from schemas whose role is business or derived. store had no row
-- at all. Two changes, both deterministic:
--   1. populate defaults an unclassified schema to business (decided_by
--      'default'). A default is written once and never revises an existing
--      row, so an operator's plumbing decision, and the pilot's deliberate
--      unknowns, stand.
--   2. the sweep runs populate first, so a schema that appeared since the
--      last pass is a candidate in that same pass.
-- (The narrative half of the same outage — no OpenRouter token on the box —
-- is the worker's fallback to the Clover backend, in services/calliope.)

CREATE OR REPLACE FUNCTION rvbbit.business_sources_populate()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_schema text;
    v_role text;
    v_system text;
    v_adapter text;
    v_by text;
    v_note text;
    v_has_connectors boolean := to_regclass('fivetran_metadata.connector') IS NOT NULL;
    v_connector_sql text;
    v_connector record;
    v_touched integer := 0;
BEGIN
    IF v_has_connectors THEN
        v_connector_sql := 'SELECT connector_name::text AS connector_name, connector_type_id::text AS connector_type_id FROM fivetran_metadata.connector';
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema='fivetran_metadata' AND table_name='connector' AND column_name='_fivetran_deleted') THEN
            v_connector_sql := v_connector_sql || ' WHERE NOT coalesce(_fivetran_deleted,false)';
        END IF;
    END IF;

    FOR v_schema IN
        SELECT n.nspname FROM pg_namespace n
         WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
           AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid AND c.relkind IN ('r','p','m'))
         ORDER BY 1
    LOOP
        v_role := NULL; v_system := NULL; v_adapter := NULL; v_by := 'populate'; v_note := NULL;

        -- The appliance's own schemas and internal tooling: plumbing by definition.
        IF v_schema IN ('rvbbit','hindsight','cubes','seeds','parity','dagster_operational',
                        'fivetran_metadata','n8n','cron','pgsodium','vault')
           OR v_schema LIKE '\_dlt\_%' THEN
            v_role := 'plumbing';
            v_system := CASE WHEN v_schema LIKE '\_dlt\_%' THEN 'dlt' ELSE v_schema END;
            v_by := 'internal_list';
        END IF;

        -- The appliance's own mirror jobs: a destination schema is a business
        -- mirror of the named connection.
        IF v_role IS NULL AND to_regclass('rvbbit.mirror_jobs') IS NOT NULL THEN
            SELECT j.connection_name, 'mirror:'||coalesce(c.dialect, 'unknown')
              INTO v_system, v_adapter
              FROM rvbbit.mirror_jobs j
              LEFT JOIN rvbbit.mirror_connections c USING (connection_name)
             WHERE j.destination_schema = v_schema
             ORDER BY j.enabled DESC, j.job_name
             LIMIT 1;
            IF v_system IS NOT NULL THEN
                v_role := 'business'; v_by := 'mirror_jobs';
            END IF;
        END IF;

        -- The Fivetran connector map: the mirror's own record of what each schema is.
        IF v_role IS NULL AND v_has_connectors THEN
            FOR v_connector IN EXECUTE v_connector_sql LOOP
                IF v_schema = v_connector.connector_name
                   OR v_schema LIKE v_connector.connector_name || '\_%' THEN
                    IF v_connector.connector_type_id = 'fivetran_log' THEN
                        v_role := 'plumbing';
                    ELSIF v_schema <> v_connector.connector_name
                          AND (v_schema LIKE '%\_source' OR v_schema LIKE '%\_staging'
                               OR v_schema LIKE '%\_stg\_%' OR v_schema LIKE 'stg\_%') THEN
                        v_role := 'derived';
                    ELSE
                        v_role := 'business';
                    END IF;
                    v_system := v_connector.connector_type_id;
                    v_adapter := 'fivetran:' || v_connector.connector_type_id;
                    v_by := 'connector_map';
                    EXIT;
                END IF;
            END LOOP;
        END IF;

        -- Transformation layers (dbt-style) are derived: excavated, attributed upstream.
        IF v_role IS NULL AND (v_schema IN ('marts','reporting','intermediate','staging')
                               OR v_schema LIKE '%\_staging' OR v_schema LIKE 'stg\_%'
                               OR v_schema LIKE '%\_source' OR v_schema LIKE '%\_stg\_%'
                               OR v_schema LIKE '%\_marts' OR v_schema LIKE '%\_intermediate') THEN
            v_role := 'derived'; v_system := 'transform'; v_by := 'heuristic';
        END IF;

        IF v_role IS NULL THEN
            -- Tables a person put here are business data until someone says
            -- otherwise. 'unknown' stalled every appliance whose uploads
            -- landed in public or a fresh schema (test-hosted1, 2026-09-21:
            -- nothing analyzed for a day). The default is written as its
            -- own decision so an operator can still set plumbing, and it
            -- never overwrites a row that already exists (see the upsert).
            v_role := 'business'; v_by := 'default';
            v_note := 'defaulted to business: set role to plumbing (decided_by operator) to keep it out of Meaning';
            -- A hint, never a decision: a connector whose leading token matches.
            IF v_has_connectors THEN
                FOR v_connector IN EXECUTE v_connector_sql LOOP
                    IF split_part(v_schema,'_',1) <> '' AND split_part(v_schema,'_',1) = split_part(v_connector.connector_name,'_',1) THEN
                        v_note := 'possibly connector '||v_connector.connector_name||' ('||v_connector.connector_type_id||') — set role and decided_by=''operator'' to confirm';
                        EXIT;
                    END IF;
                END LOOP;
            END IF;
        END IF;

        INSERT INTO rvbbit.business_sources(schema_name, source_system, role, adapter, decided_by, note)
        VALUES (v_schema, v_system, v_role, v_adapter, v_by, v_note)
        ON CONFLICT (schema_name) DO UPDATE
           SET source_system=coalesce(excluded.source_system, rvbbit.business_sources.source_system),
               adapter=coalesce(excluded.adapter, rvbbit.business_sources.adapter),
               role=excluded.role, decided_by=excluded.decided_by,
               note=coalesce(excluded.note, rvbbit.business_sources.note),
               updated_at=now()
         WHERE rvbbit.business_sources.decided_by <> 'operator'
           -- a default never revises a row that exists: the deterministic
           -- classes (internal list, mirror jobs, connectors) still refresh
           AND excluded.decided_by <> 'default';
        IF FOUND THEN v_touched := v_touched + 1; END IF;
    END LOOP;
    RETURN jsonb_build_object(
        'status','complete','touched',v_touched,
        'roles',(SELECT coalesce(jsonb_object_agg(role, n),'{}'::jsonb)
                   FROM (SELECT role, count(*) AS n FROM rvbbit.business_sources GROUP BY role) s));
END
$fn$;

CREATE OR REPLACE PROCEDURE rvbbit.meaning_sweep(
    p_budget_seconds int DEFAULT 120,
    p_max_relations  int DEFAULT 100,
    p_max_columns    int DEFAULT 400,
    p_source         text DEFAULT 'cron')
LANGUAGE plpgsql AS $fn$
DECLARE
    v_t0     timestamptz := clock_timestamp();
    v_run    bigint;
    v_c      record;
    v_r      jsonb;
    v_cols   jsonb;
    v_links  int;
    v_rels   int := 0;
    v_ncols  int := 0;
    v_nlinks int := 0;
    v_errors int := 0;
    v_cost   numeric := 0;
    v_rel    text;
BEGIN
    -- One sweep at a time: the cron tick and a manual call otherwise judge the
    -- same candidates twice (session lock; it survives the per-relation COMMITs).
    IF NOT pg_try_advisory_lock(hashtext('rvbbit.meaning_sweep')) THEN
        RAISE NOTICE 'meaning_sweep: another sweep is running; skipping';
        RETURN;
    END IF;
    -- A schema that appeared since the last sweep (an upload, a new mirror)
    -- gets its registry row now, so it is a candidate this very pass.
    BEGIN
        PERFORM rvbbit.business_sources_populate();
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'meaning_sweep: business_sources_populate failed (%); sweeping what is registered', SQLERRM;
    END;
    INSERT INTO rvbbit.meaning_runs (source, budget_seconds) VALUES (p_source, p_budget_seconds) RETURNING run_id INTO v_run;
    COMMIT;
    FOR v_c IN SELECT * FROM rvbbit.meaning_candidates(p_max_relations) LOOP
        EXIT WHEN extract(epoch FROM clock_timestamp() - v_t0) > p_budget_seconds;
        v_rel := lower(v_c.schema_name || '.' || v_c.rel_name);
        BEGIN
            v_r := rvbbit.meaning_judge_relation(v_c.schema_name, v_c.rel_name);
            IF v_r->>'status' = 'judged' THEN
                v_rels := v_rels + 1;
                v_cost := v_cost + coalesce((v_r->>'cost_usd')::numeric, 0);
                v_cols := rvbbit.meaning_judge_columns(v_c.schema_name, v_c.rel_name, p_max_columns);
                v_ncols := v_ncols + coalesce((v_cols->>'columns')::int, 0);
                v_cost := v_cost + coalesce((v_cols->>'cost_usd')::numeric, 0);
                v_links := rvbbit.meaning_infer_links(v_c.schema_name, v_c.rel_name);
                v_nlinks := v_nlinks + coalesce(v_links, 0);
                UPDATE rvbbit.meaning_relations SET drift = rvbbit.meaning_relation_drift(v_c.schema_name, v_c.rel_name), updated_at = now()
                 WHERE relation = v_rel;
            ELSE
                v_errors := v_errors + 1;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors + 1;
            INSERT INTO rvbbit.meaning_relations AS m (relation, schema_name, rel_name, status, error, attempts, updated_at)
            VALUES (v_rel, v_c.schema_name, v_c.rel_name, 'error', left(SQLERRM, 400), 1, now())
            ON CONFLICT (relation) DO UPDATE SET status = 'error', error = EXCLUDED.error, attempts = m.attempts + 1, updated_at = now();
        END;
        COMMIT;
    END LOOP;
    UPDATE rvbbit.meaning_runs SET finished_at = now(), relations_done = v_rels, columns_done = v_ncols, links_done = v_nlinks,
           errors = v_errors, cost_usd = v_cost,
           notes = jsonb_build_object('elapsed_s', round(extract(epoch FROM clock_timestamp() - v_t0)))
     WHERE run_id = v_run;
    COMMIT;
    PERFORM pg_advisory_unlock(hashtext('rvbbit.meaning_sweep'));
    RAISE NOTICE 'meaning_sweep: % relations, % columns, % links, % errors, $% in % s',
        v_rels, v_ncols, v_nlinks, v_errors, round(v_cost, 4), round(extract(epoch FROM clock_timestamp() - v_t0));
END $fn$;

-- ---------------------------------------------------------------------------
-- Cards: what the agent and the person read. Overrides win; provenance shown.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_card(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_rel    text := lower(p_schema || '.' || p_rel);
    m        rvbbit.meaning_relations%ROWTYPE;
    v_over   jsonb;
    v_cols   jsonb;
    v_roles  jsonb;
    v_joins  jsonb;
    v_joined jsonb;
    v_refs   jsonb;
    v_pii    jsonb;
    v_deserves text;
BEGIN
    SELECT * INTO m FROM rvbbit.meaning_relations WHERE relation = v_rel;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_over := rvbbit.meaning_overrides_for(v_rel, NULL);

    SELECT jsonb_object_agg(role, n) INTO v_roles
      FROM (SELECT coalesce(role, 'pending') AS role, count(*) AS n FROM rvbbit.meaning_columns WHERE relation = v_rel GROUP BY 1) r;
    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', c.col_name, 'role', c.role, 'ref', c.ref_target,
                        'values', CASE WHEN c.role = 'category' THEN (SELECT jsonb_agg(t->>'v') FROM jsonb_array_elements(rvbbit._jsonb_arr(c.top_values)) WITH ORDINALITY x(t, i) WHERE i <= 4) END))
                     ORDER BY CASE c.role WHEN 'identifier' THEN 0 WHEN 'time' THEN 1 WHEN 'measure' THEN 2 WHEN 'category' THEN 3 WHEN 'reference' THEN 4 ELSE 5 END, c.col_name)
      INTO v_cols
      FROM (SELECT * FROM (
                SELECT c.*, row_number() OVER (PARTITION BY c.role ORDER BY
                           CASE WHEN c.col_name ~* '(^|_)(fivetran|dlt|system_modstamp|last_viewed|last_referenced)' THEN 1 ELSE 0 END,
                           CASE WHEN c.col_name ~* '(created|occurred|status|stage|type|source|amount|name|email)' THEN 0 ELSE 1 END,
                           c.null_frac NULLS LAST, c.col_name) AS rn
                  FROM rvbbit.meaning_columns c
                 WHERE c.relation = v_rel AND c.role IN ('identifier', 'time', 'measure', 'category', 'reference')) r
             WHERE r.rn <= 4) c;
    SELECT jsonb_agg(c.col_name ORDER BY c.pii DESC) INTO v_pii
      FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = v_rel AND c.pii >= 0.6 ORDER BY c.pii DESC LIMIT 8) c;

    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('via', l.from_column, 'to', l.to_relation, 'on', l.to_column, 'state', l.state,
                        'match', l.match_rate, 'basis', l.basis)) ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST)
      INTO v_joins
      FROM (SELECT * FROM rvbbit.meaning_links l WHERE l.from_relation = v_rel AND l.state <> 'contradicted' ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST LIMIT 12) l;
    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('from', l.from_relation, 'via', l.from_column, 'state', l.state, 'match', l.match_rate))
                     ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST)
      INTO v_joined
      FROM (SELECT * FROM rvbbit.meaning_links l WHERE l.to_relation = v_rel AND l.state = 'verified' ORDER BY l.match_rate DESC NULLS LAST LIMIT 12) l;

    IF to_regclass('rvbbit.relation_refs') IS NOT NULL THEN
        SELECT jsonb_strip_nulls(jsonb_build_object(
                 'metrics', (SELECT jsonb_agg(ref_name ORDER BY ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'metric' LIMIT 8) x),
                 'cubes', (SELECT jsonb_agg(ref_name ORDER BY ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'cube' LIMIT 8) x),
                 'dashboards', (SELECT jsonb_agg(ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'dashboard' LIMIT 6) x),
                 'domains', (SELECT jsonb_agg(jsonb_build_object('key', ref_name, 'name', ref_meta->>'name', 'status', ref_meta->>'status'))
                               FROM (SELECT ref_name, ref_meta FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'domain' LIMIT 4) x)))
          INTO v_refs;
    END IF;

    v_deserves := CASE
        WHEN v_refs ? 'metrics' OR v_refs ? 'cubes' THEN 'already measured'
        WHEN coalesce(m.plumbing, 0) >= 0.6 OR m.kind IN ('system_plumbing', 'staging_or_raw') THEN 'no: plumbing or raw'
        WHEN m.kind = 'aggregate_or_cube' THEN 'is itself a rollup'
        WHEN coalesce(m.metric_worthy, 0) >= 0.6 AND m.time_column IS NOT NULL THEN 'yes: activity over time'
        WHEN coalesce(m.metric_worthy, 0) >= 0.6 THEN 'yes: counts and breakdowns'
        WHEN coalesce(m.metric_worthy, 0) >= 0.4 THEN 'maybe'
        ELSE 'unlikely' END;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'relation', v_rel,
        'name', coalesce(v_over->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(v_over->>'what', m.what),
        'kind', coalesce(v_over->>'kind', m.kind),
        'source', jsonb_strip_nulls(jsonb_build_object('system', m.source_system, 'role', m.source_role)),
        'rows', m.n_rows, 'columns', m.n_columns, 'custom_fields', nullif(m.custom_fields, 0),
        'keys', CASE WHEN cardinality(m.entity_keys) > 0 THEN to_jsonb(m.entity_keys) END,
        'time', jsonb_strip_nulls(jsonb_build_object('column', m.time_column, 'grain', m.time_grain)),
        'column_roles', v_roles,
        'notable_columns', v_cols,
        'personal_data', v_pii,
        'relates', jsonb_strip_nulls(jsonb_build_object('joins_to', v_joins, 'joined_by', v_joined)),
        'measured_by', v_refs,
        'deserves_metrics', v_deserves,
        'scores', jsonb_strip_nulls(jsonb_build_object('metric_worthy', m.metric_worthy, 'plumbing', m.plumbing, 'pii', m.has_pii, 'customized', m.customized)),
        'domain', coalesce(v_over->>'domain_key', m.domain_key),
        'drift', m.drift,
        'usage', m.usage,
        'confidence', CASE WHEN v_over <> '{}'::jsonb THEN 1.0 ELSE m.confidence END,
        'provenance', jsonb_strip_nulls(jsonb_build_object(
            'judged_by', m.judge_model, 'judged_at', m.judged_at, 'status', m.status,
            'stated_by_people', CASE WHEN v_over <> '{}'::jsonb THEN v_over END,
            'note', 'provisional: described from structure and a small judge; not affirmed canon'))));
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_column_card(p_schema text, p_rel text, p_col text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'column', c.col_name, 'relation', c.relation, 'type', c.data_type,
        'role', coalesce(o->>'role', c.role), 'role_p', c.role_p,
        'refers_to', coalesce(o->>'ref_target', c.ref_target),
        'joins', (SELECT jsonb_agg(jsonb_build_object('to', l.to_relation, 'on', l.to_column, 'state', l.state, 'match', l.match_rate))
                    FROM rvbbit.meaning_links l WHERE l.from_relation = c.relation AND l.from_column = c.col_name AND l.state <> 'contradicted'),
        'personal_data', coalesce((o->>'pii')::numeric, c.pii),
        'distinct', c.ndv, 'null_pct', round(c.null_frac * 100),
        'top_values', c.top_values,
        'basis', CASE WHEN o <> '{}'::jsonb THEN 'override' ELSE c.basis END,
        'judged_at', c.judged_at))
      FROM rvbbit.meaning_columns c, LATERAL (SELECT rvbbit.meaning_overrides_for(c.relation, c.col_name) AS o) x
     WHERE c.relation = lower(p_schema || '.' || p_rel) AND c.col_name = p_col
$fn$;
