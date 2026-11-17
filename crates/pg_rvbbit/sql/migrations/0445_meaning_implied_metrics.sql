-- 0445_meaning_implied_metrics — the dossier's candidate metrics become callable
-- implied metrics; use promotes them, silence retires them (docs/MEANING_PLAN.md §2).
--
-- On the pilot the governed registry was the working answer surface (286
-- metric() calls a week over 43 of 109 metrics) while the 33 candidate metrics
-- the dossiers wrote reached nobody: the tree is read by people, and find's
-- governed hits read metric_defs. Nobody who answers a question needs to know
-- the word "governed"; they need the number. So:
--
--   implied   a dossier candidate registered once per subject as a metric_defs row
--             (labels.status = implied, origin = meaning), SQL synthesized from the
--             formula on the canonical source and proven by running it. metric() runs
--             it, find surfaces it, the Floor can ghost it. Callie says it is
--             unconfirmed once, when it matters.
--   proposed  used in enough distinct sessions → a pending proposal in the same
--             inbox every governed metric came through (86 accepted there so far).
--             Accepting defines the next version without the implied status.
--   retired   registered long enough with no use → hidden from the catalog, kept
--             in metric_defs (a person can revive it by teaching it).
--
-- Derived metrics (Total spend over each table) are never registered: they are
-- grammar, free to compute, and the wrong unit to govern.

CREATE TABLE IF NOT EXISTS rvbbit.meaning_desire_path_log (
    id           bigserial PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    action       text NOT NULL,            -- registered | proposed | retired | tick
    metric_name  text,
    subject_key  text,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor        text NOT NULL DEFAULT 'meaning'
);
CREATE INDEX IF NOT EXISTS meaning_desire_path_log_ts_idx ON rvbbit.meaning_desire_path_log (ts DESC);

-- ── small helpers ───────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit._meaning_jsonb(p_text text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $fn$
BEGIN
    RETURN p_text::jsonb;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END $fn$;

-- "Click-Through Rate (CTR)" → click_through_rate
CREATE OR REPLACE FUNCTION rvbbit.meaning_metric_slug(p_name text)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT left(regexp_replace(regexp_replace(lower(regexp_replace(coalesce(p_name, ''), '\([^)]*\)', '', 'g')), '[^a-z0-9]+', '_', 'g'), '^_+|_+$', '', 'g'), 60)
$fn$;

-- "Click-Through Rate (CTR)" → ctr
CREATE OR REPLACE FUNCTION rvbbit.meaning_metric_acronym(p_name text)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT nullif(lower(btrim((regexp_match(coalesce(p_name, ''), '\(([A-Za-z0-9 /-]{2,12})\)'))[1])), '')
$fn$;

-- The column names a formula reads (identifiers minus SQL words and string literals).
CREATE OR REPLACE FUNCTION rvbbit.meaning_formula_columns(p_formula text)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $fn$
    SELECT coalesce(array_agg(DISTINCT w), '{}')
      FROM (SELECT (regexp_matches(lower(regexp_replace(coalesce(p_formula, ''), '''[^'']*''', ' ', 'g')), '([a-z_][a-z0-9_]*)', 'g'))[1] AS w) x
     WHERE w NOT IN ('sum', 'count', 'avg', 'min', 'max', 'nullif', 'coalesce', 'float', 'float8', 'numeric', 'int', 'integer', 'bigint', 'decimal',
                     'distinct', 'case', 'when', 'then', 'else', 'end', 'and', 'or', 'not', 'null', 'as', 'cast', 'date_trunc', 'extract', 'filter',
                     'where', 'over', 'partition', 'by', 'order', 'round', 'abs', 'true', 'false', 'interval', 'day', 'days', 'week', 'month', 'year',
                     'date', 'text', 'boolean', 'is', 'in', 'between', 'like', 'ilike', 'greatest', 'least', 'percentile_cont', 'within', 'group',
                     'stddev', 'variance', 'median', 'from', 'select', 'join', 'on', 'using', 'having', 'limit', 'current_date', 'now', 'to_date',
                     'to_char', 'length', 'lower', 'upper', 'trim', 'left', 'right', 'exists', 'all', 'any', 'some', 'desc', 'asc', 'nulls',
                     'first', 'last', 'with', 'union', 'timestamp', 'timestamptz', 'epoch', 'age', 'value')
$fn$;

-- ── the canonical source of a formula inside a subject ──────────────────────
-- The member relation that carries every column the formula reads: core first,
-- then the highest layer, then facts and rollups, then use, then size. Words in
-- the formula that are schema or relation names are not required columns.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_source(p_subject_key text, p_formula text, p_grain text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH req AS (
        SELECT col FROM unnest(rvbbit.meaning_formula_columns(p_formula)) AS col
         WHERE NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations m WHERE lower(m.schema_name) = col OR lower(m.rel_name) = col)
    ),
    cand AS (
        SELECT m.relation, m.time_column, coalesce(m.subject_role, 'support') AS role, m.kind, m.n_rows,
               coalesce((m.usage->>'touches_30d')::int, 0) AS touches,
               (rvbbit.meaning_layer(m.relation)->>'rank')::int AS layer_rank
          FROM rvbbit.meaning_domains d
          JOIN rvbbit.meaning_relations m ON m.relation = ANY(d.members)
         WHERE d.domain_key = p_subject_key AND m.status = 'judged'
           AND coalesce(m.subject_role, 'support') IN ('core', 'support')
           AND (SELECT count(*) FROM req) > 0
           AND NOT EXISTS (SELECT 1 FROM req r
                            WHERE NOT EXISTS (SELECT 1 FROM rvbbit.meaning_columns c WHERE c.relation = m.relation AND c.col_name = r.col))
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'relation', c.relation, 'time_column', c.time_column, 'role', c.role, 'kind', c.kind, 'rows', c.n_rows,
               'layer', rvbbit.meaning_layer(c.relation)->>'layer',
               'columns', (SELECT jsonb_agg(col) FROM req),
               'grain_columns', (SELECT jsonb_agg(g) FROM (SELECT DISTINCT btrim(g) AS g FROM unnest(string_to_array(coalesce(p_grain, ''), ',')) g) x
                                  WHERE g <> '' AND EXISTS (SELECT 1 FROM rvbbit.meaning_columns k WHERE k.relation = c.relation AND k.col_name = x.g)),
               'candidates', (SELECT count(*) FROM cand)))
      FROM cand c
     ORDER BY (c.role = 'core') DESC, c.layer_rank DESC, (c.kind IN ('transactional_fact', 'aggregate_or_cube')) DESC, c.touches DESC, c.n_rows DESC NULLS LAST
     LIMIT 1
$fn$;

-- The SQL a registered implied metric runs: the formula as `value` over the
-- source, the last {days} days when the source has a time column.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_sql(p_relation text, p_formula text, p_time_column text DEFAULT NULL)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT 'SELECT ' || btrim(p_formula) || ' AS value FROM '
           || quote_ident(split_part(p_relation, '.', 1)) || '.' || quote_ident(split_part(p_relation, '.', 2))
           || CASE WHEN nullif(p_time_column, '') IS NOT NULL
                   THEN ' WHERE ' || quote_ident(p_time_column) || ' >= now() - ({days}::int * interval ''1 day'')'
                   ELSE '' END
$fn$;

-- Prove a synthesized definition by running it (bounded), before it is registered.
CREATE OR REPLACE FUNCTION rvbbit.meaning_implied_probe(p_sql text, p_days int DEFAULT 30, p_timeout_ms int DEFAULT 5000)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_sql   text := replace(p_sql, '{days}', quote_literal(p_days::text));
    v_row   jsonb;
    v_n     int;
    v_t0    timestamptz := clock_timestamp();
    v_saved text := current_setting('statement_timeout', true);
BEGIN
    PERFORM set_config('statement_timeout', p_timeout_ms::text, true);
    BEGIN
        EXECUTE 'SELECT count(*), (array_agg(to_jsonb(t)))[1] FROM (' || v_sql || ') AS t' INTO v_n, v_row;
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('statement_timeout', coalesce(v_saved, '0'), true);
        RETURN jsonb_build_object('ok', false, 'error', left(SQLERRM, 300), 'ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
    END;
    PERFORM set_config('statement_timeout', coalesce(v_saved, '0'), true);
    IF coalesce(v_n, 0) <> 1 THEN
        RETURN jsonb_build_object('ok', false, 'error', format('returned %s rows; a metric returns one', coalesce(v_n, 0)), 'ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
    END IF;
    RETURN jsonb_build_object('ok', true, 'value', v_row->'value', 'ms', round(extract(epoch FROM clock_timestamp() - v_t0) * 1000));
END $fn$;

-- ── register: one implied metric per dossier candidate ──────────────────────
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
                      || ' as ' || btrim(c->>'formula')
                      || CASE WHEN v_src->>'time_column' IS NOT NULL THEN ' over the last 30 days by ' || (v_src->>'time_column') || ' (params.days changes the window).' ELSE '.' END
                      || ' Not yet confirmed by a person; say so once when it matters.';
            v_labels := jsonb_strip_nulls(jsonb_build_object(
                'status', 'implied', 'origin', 'meaning',
                'subject', d.domain_key, 'subject_name', d.name,
                'source_objects', jsonb_build_array(relation), 'source_layer', v_src->>'layer', 'source_role', v_src->>'role',
                'aliases', (SELECT jsonb_agg(DISTINCT a) FROM unnest(ARRAY[lower(regexp_replace(c->>'name', '\s*\([^)]*\)', '', 'g')), v_acr]) a WHERE a IS NOT NULL AND a <> ''),
                'formula', btrim(c->>'formula'), 'grain_columns', v_src->'grain_columns', 'why', c->>'why',
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
COMMENT ON FUNCTION rvbbit.meaning_register_implied_metrics(text, boolean) IS 'Register each dossier candidate metric once as an implied metric (labels.status=implied, origin=meaning) on its canonical source, after proving the SQL runs. Dry run unless p_apply.';

-- ── use: who calls what ─────────────────────────────────────────────────────
-- A specialist's call counts for the thread that delegated it: one question that
-- Callie answers with Athena's help is one session, not two (the first thread
-- test showed cost_per_lead at 2 sessions from one ask).
CREATE OR REPLACE FUNCTION rvbbit.meaning_metric_usage(p_days int DEFAULT 30)
RETURNS TABLE(metric_name text, calls bigint, sessions bigint, users bigint, first_used timestamptz, last_used timestamptz)
LANGUAGE sql STABLE AS $fn$
    WITH calls AS (
        SELECT rvbbit._meaning_jsonb(tc->'function'->>'arguments')->>'name' AS metric_name, m.session_id, m.ts
          FROM rvbbit.hermes_messages m
          CROSS JOIN LATERAL jsonb_array_elements(m.tool_calls) tc
         WHERE m.ts > now() - make_interval(days => greatest(p_days, 1))
           AND m.tool_calls IS NOT NULL AND jsonb_typeof(m.tool_calls) = 'array'
           AND EXISTS (SELECT 1 FROM unnest(coalesce(m.tool_call_names, '{}')) n WHERE n ~ '__metric$')
           AND (tc->'function'->>'name') ~ '__metric$'
    ),
    folded AS (
        -- thread = the Calliope session: a specialist's run names the session that delegated it,
        -- Callie's own Hermes session is recorded on calliope_sessions.hermes_session_id
        SELECT c.metric_name, c.ts,
               coalesce(r.source_session_id::text, cs.id::text, c.session_id) AS thread, s.user_id
          FROM calls c
          LEFT JOIN LATERAL (SELECT sr.source_session_id FROM rvbbit.calliope_specialist_runs sr WHERE sr.hermes_session_id = c.session_id LIMIT 1) r ON true
          LEFT JOIN LATERAL (SELECT x.id FROM rvbbit.calliope_sessions x WHERE x.hermes_session_id = c.session_id LIMIT 1) cs ON true
          LEFT JOIN rvbbit.hermes_sessions s ON s.id::text = c.session_id
    )
    SELECT f.metric_name, count(*), count(DISTINCT f.thread), count(DISTINCT f.user_id), min(f.ts), max(f.ts)
      FROM folded f
     WHERE f.metric_name IS NOT NULL
     GROUP BY f.metric_name
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_metric_usage(int) IS 'metric() calls per metric name from the Hermes session mirror: calls, distinct threads (a specialist counts for the thread that delegated it), distinct users, first and last use.';

CREATE OR REPLACE VIEW rvbbit.meaning_implied_metrics AS
    SELECT m.name, m.version, m.labels->>'status' AS status, m.labels->>'subject' AS subject_key, m.labels->>'subject_name' AS subject_name,
           m.labels->'source_objects'->>0 AS source, m.labels->>'formula' AS formula, m.grain, m.description,
           (m.labels->>'registered_at')::timestamptz AS registered_at,
           (m.labels->>'proposed_at')::timestamptz AS proposed_at,
           (m.labels->>'retired_at')::timestamptz AS retired_at,
           m.created_at
      FROM (SELECT DISTINCT ON (name) * FROM rvbbit.metric_defs ORDER BY name, created_at DESC, version DESC) m
     WHERE m.labels->>'origin' = 'meaning';

-- ── promote by use ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_promote_by_use(p_min_sessions int DEFAULT 3, p_days int DEFAULT 30, p_apply boolean DEFAULT false)
RETURNS TABLE(metric_name text, sessions bigint, calls bigint, users bigint, action text, proposal_id bigint)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    r      record;
    v_def  record;
    v_pid  bigint;
BEGIN
    FOR r IN SELECT i.name, u.sessions, u.calls, u.users
               FROM rvbbit.meaning_implied_metrics i
               JOIN rvbbit.meaning_metric_usage(p_days) u ON u.metric_name = i.name
              WHERE i.status = 'implied' AND u.sessions >= p_min_sessions
              ORDER BY u.sessions DESC, u.calls DESC
    LOOP
        metric_name := r.name; sessions := r.sessions; calls := r.calls; users := r.users; proposal_id := NULL;
        SELECT p.proposal_id INTO v_pid FROM rvbbit.proposals p WHERE p.kind = 'metric' AND p.name = r.name AND p.status = 'pending' LIMIT 1;
        IF FOUND THEN
            action := 'pending'; proposal_id := v_pid; RETURN NEXT; CONTINUE;
        END IF;
        SELECT * INTO v_def FROM rvbbit.metric_defs m WHERE m.name = r.name ORDER BY m.version DESC LIMIT 1;
        IF p_apply THEN
            v_pid := rvbbit.record_proposal('metric', jsonb_build_object(
                'name', r.name, 'subject', v_def.labels->>'subject_name', 'sql', v_def.sql, 'grain', v_def.grain, 'params', v_def.params,
                'description', regexp_replace(v_def.description, '\s*Not yet confirmed by a person; say so once when it matters\.$', '')
                               || format(' Asked for in %s sessions by %s people in the last %s days; promoted by use.', r.sessions, r.users, p_days),
                'source', v_def.labels->'source_objects'->>0, 'source_tables', v_def.labels->'source_objects',
                'confidence', least(0.95, 0.5 + 0.1 * r.sessions), 'category', v_def.labels->>'subject_name'), 'meaning', 'desire_path');
            UPDATE rvbbit.metric_defs m SET labels = m.labels || jsonb_build_object('proposed_at', now(), 'proposal_id', v_pid)
             WHERE m.name = r.name AND m.version = v_def.version;
            INSERT INTO rvbbit.meaning_desire_path_log (action, metric_name, subject_key, detail)
            VALUES ('proposed', r.name, v_def.labels->>'subject', jsonb_build_object('sessions', r.sessions, 'calls', r.calls, 'users', r.users, 'proposal_id', v_pid, 'days', p_days));
            action := 'proposed'; proposal_id := v_pid;
        ELSE
            action := 'would_propose';
        END IF;
        RETURN NEXT;
    END LOOP;
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_promote_by_use(int, int, boolean) IS 'Implied metrics asked for in enough distinct sessions become pending metric proposals (the same inbox every governed metric came through). Dry run unless p_apply.';

-- ── retire by silence ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_prune_implied(p_days int DEFAULT 60, p_apply boolean DEFAULT false)
RETURNS TABLE(metric_name text, registered_at timestamptz, action text)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT i.name, i.version, i.registered_at, i.subject_key
               FROM rvbbit.meaning_implied_metrics i
              WHERE i.status = 'implied'
                AND coalesce(i.registered_at, i.created_at) < now() - make_interval(days => greatest(p_days, 1))
                AND NOT EXISTS (SELECT 1 FROM rvbbit.meaning_metric_usage(p_days) u WHERE u.metric_name = i.name)
                AND NOT EXISTS (SELECT 1 FROM rvbbit.proposals p WHERE p.kind = 'metric' AND p.name = i.name AND p.status = 'pending')
              ORDER BY i.registered_at
    LOOP
        metric_name := r.name; registered_at := r.registered_at;
        IF p_apply THEN
            UPDATE rvbbit.metric_defs m SET labels = m.labels || jsonb_build_object('status', 'retired', 'retired_at', now(), 'retired_reason', format('no use in %s days', p_days))
             WHERE m.name = r.name AND m.version = r.version;
            INSERT INTO rvbbit.meaning_desire_path_log (action, metric_name, subject_key, detail)
            VALUES ('retired', r.name, r.subject_key, jsonb_build_object('registered_at', r.registered_at, 'days', p_days));
            action := 'retired';
        ELSE
            action := 'would_retire';
        END IF;
        RETURN NEXT;
    END LOOP;
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_prune_implied(int, boolean) IS 'Implied metrics registered for p_days with no metric() use are hidden from the catalog (labels.status=retired), never deleted. Dry run unless p_apply.';

-- ── the daily tick ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.meaning_desire_path_tick(p_min_sessions int DEFAULT 3, p_prune_days int DEFAULT 60)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_reg   jsonb;
    v_prom  jsonb;
    v_prune jsonb;
    v_out   jsonb;
BEGIN
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_reg
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_register_implied_metrics(NULL, true) GROUP BY 1) x;
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_prom
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_promote_by_use(p_min_sessions, 30, true) GROUP BY 1) x;
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_prune
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_prune_implied(p_prune_days, true) GROUP BY 1) x;
    v_out := jsonb_build_object('registered', v_reg, 'promoted', v_prom, 'retired', v_prune, 'at', now());
    INSERT INTO rvbbit.meaning_desire_path_log (action, detail) VALUES ('tick', v_out);
    RETURN v_out;
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_desire_path_tick(int, int) IS 'Daily: register new dossier candidates as implied metrics, propose the ones in use, retire the silent ones. Every step logs to meaning_desire_path_log.';

-- What the desire path has done and where every implied metric stands.
CREATE OR REPLACE FUNCTION rvbbit.meaning_desire_path_status()
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'implied', (SELECT count(*) FROM rvbbit.meaning_implied_metrics WHERE status = 'implied'),
        'proposed', (SELECT count(*) FROM rvbbit.meaning_implied_metrics WHERE proposed_at IS NOT NULL AND status = 'implied'),
        'promoted', (SELECT count(*) FROM rvbbit.meaning_implied_metrics WHERE status IS NULL OR status NOT IN ('implied', 'retired')),
        'retired', (SELECT count(*) FROM rvbbit.meaning_implied_metrics WHERE status = 'retired'),
        'metrics', (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'name', i.name, 'status', coalesce(i.status, 'governed'), 'subject', i.subject_name, 'source', i.source,
                        'calls_30d', u.calls, 'sessions_30d', u.sessions, 'last_used', u.last_used, 'registered_at', i.registered_at, 'proposed_at', i.proposed_at))
                        ORDER BY coalesce(u.sessions, 0) DESC, i.name), '[]'::jsonb)
                      FROM rvbbit.meaning_implied_metrics i LEFT JOIN rvbbit.meaning_metric_usage(30) u ON u.metric_name = i.name),
        'log', (SELECT coalesce(jsonb_agg(jsonb_build_object('ts', l.ts, 'action', l.action, 'metric', l.metric_name, 'detail', l.detail) ORDER BY l.ts DESC), '[]'::jsonb)
                  FROM (SELECT * FROM rvbbit.meaning_desire_path_log ORDER BY ts DESC LIMIT 40) l))
$fn$;

-- ── the catalog hides retired metrics; the vocabulary knows implied ones ─────
CREATE OR REPLACE VIEW rvbbit.metric_catalog AS
    SELECT x.name, x.version, x.sql, x.params, x.grain, x.description, x.owner, x.labels, x.check_sql, x.created_at, x.category, x.subcategory
      FROM (SELECT DISTINCT ON (m.name) m.name, m.version, m.sql, m.params, m.grain, m.description, m.owner, m.labels, m.check_sql, m.created_at,
                   ec.category, ec.subcategory
              FROM rvbbit.metric_defs m
              LEFT JOIN rvbbit.entity_categories ec ON ec.entity_kind = 'metric' AND ec.entity_name = m.name
             ORDER BY m.name, m.created_at DESC, m.version DESC) x
     WHERE coalesce(x.labels->>'status', '') <> 'retired';

CREATE OR REPLACE FUNCTION rvbbit.meaning_governed_terms(p_query text, p_max int DEFAULT 4)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH q AS (SELECT ' ' || regexp_replace(lower(coalesce(p_query, '')), '[^a-z0-9 ]+', ' ', 'g') || ' ' AS text),
    from_ledger AS (
        SELECT b.term_norm AS term, b.referent, b.gloss, b.status, b.taught_by, 'ledger' AS via
          FROM rvbbit.meaning_live_bindings() b, q
         WHERE position(' ' || lower(b.term_norm) || ' ' IN q.text) > 0
    ),
    latest AS (SELECT DISTINCT ON (name) * FROM rvbbit.metric_defs ORDER BY name, created_at DESC, version DESC),
    from_metrics AS (
        SELECT DISTINCT ON (d.name) a.alias AS term, jsonb_build_object('kind', 'metric', 'name', d.name) AS referent,
               left(d.description, 300) AS gloss,
               CASE WHEN d.labels->>'status' = 'implied' THEN 'implied' ELSE 'registry' END AS status,
               d.owner AS taught_by, 'metric_alias' AS via
          FROM latest d
          CROSS JOIN LATERAL (SELECT lower(x) AS alias FROM jsonb_array_elements_text(coalesce(d.labels->'aliases', '[]'::jsonb) || coalesce(d.labels->'canonical_for', '[]'::jsonb) || to_jsonb(ARRAY[replace(d.name, '_', ' ')])) x) a, q
         WHERE coalesce(d.labels->>'status', '') <> 'retired'
           AND position(' ' || a.alias || ' ' IN q.text) > 0
           AND NOT EXISTS (SELECT 1 FROM from_ledger l WHERE l.referent->>'kind' = 'metric' AND l.referent->>'name' = d.name)
         ORDER BY d.name
    ),
    all_hits AS (SELECT * FROM from_ledger UNION ALL SELECT * FROM from_metrics)
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'term', h.term, 'via', h.via, 'status', h.status, 'gloss', h.gloss, 'taught_by', h.taught_by,
               'referent', h.referent,
               'metric', CASE WHEN h.referent->>'kind' = 'metric' THEN (
                   SELECT jsonb_strip_nulls(jsonb_build_object('name', d.name, 'version', d.version, 'grain', d.grain,
                              'canonical_for', d.labels->'canonical_for', 'not', d.labels->'not',
                              'source_objects', d.labels->'source_objects', 'about', left(d.description, 300),
                              'status', CASE WHEN d.labels->>'status' IN ('implied', 'retired') THEN d.labels->>'status' END,
                              'origin', d.labels->>'origin', 'subject', d.labels->>'subject_name'))
                     FROM latest d WHERE d.name = h.referent->>'name') END))
             ORDER BY (h.via = 'ledger') DESC, (h.status = 'implied') ASC, h.term), '[]'::jsonb)
      FROM (SELECT * FROM all_hits ORDER BY (via = 'ledger') DESC, (status = 'implied') ASC, term LIMIT p_max) h
$fn$;

-- The dossier's candidates say which of them are callable now (name + status).
CREATE OR REPLACE FUNCTION rvbbit.meaning_candidate_metrics(p_subject_key text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(c || jsonb_strip_nulls(jsonb_build_object(
               'metric', m.name,
               'status', CASE WHEN m.name IS NULL THEN NULL WHEN m.labels->>'origin' = 'meaning' THEN coalesce(m.labels->>'status', 'governed') ELSE 'governed' END,
               'source', m.labels->'source_objects'->>0)) ORDER BY o), '[]'::jsonb)
      FROM rvbbit.meaning_domains d
      CROSS JOIN LATERAL jsonb_array_elements(coalesce(d.candidate_metrics, '[]'::jsonb)) WITH ORDINALITY AS t(c, o)
      LEFT JOIN LATERAL (SELECT md.name, md.labels FROM rvbbit.metric_defs md WHERE md.name = rvbbit.meaning_metric_slug(c->>'name') ORDER BY md.version DESC LIMIT 1) m ON true
     WHERE d.domain_key = p_subject_key
$fn$;

-- Nouns and concepts carry the same (metric name + status) on the dossier metrics they attach.
CREATE OR REPLACE FUNCTION rvbbit.meaning_metrics_on(p_relation text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_strip_nulls(jsonb_build_object(
               'name', c->>'name', 'formula', c->>'formula', 'grain', c->>'grain', 'subject', d.name,
               'metric', md.name,
               'status', CASE WHEN md.name IS NULL THEN NULL WHEN md.labels->>'origin' = 'meaning' THEN coalesce(md.labels->>'status', 'governed') ELSE 'governed' END))), '[]'::jsonb)
      FROM rvbbit.meaning_relations m
      JOIN rvbbit.meaning_domains d ON d.domain_key = m.domain_key
      CROSS JOIN LATERAL jsonb_array_elements(d.candidate_metrics) c
      LEFT JOIN LATERAL (SELECT x.name, x.labels FROM rvbbit.metric_defs x WHERE x.name = rvbbit.meaning_metric_slug(c->>'name') ORDER BY x.version DESC LIMIT 1) md ON true
     WHERE m.relation = p_relation
       AND (c->>'formula' ILIKE '%' || m.relation || '%' OR c->>'formula' ILIKE '%' || m.rel_name || '.%'
            OR EXISTS (SELECT 1 FROM rvbbit.meaning_columns k
                        WHERE k.relation = m.relation AND k.role IN ('measure', 'category', 'time', 'identifier')
                          AND length(k.col_name) >= 6 AND (c->>'formula') ~* ('(^|[^a-z0-9_])' || k.col_name || '([^a-z0-9_]|$)')))
$fn$;
