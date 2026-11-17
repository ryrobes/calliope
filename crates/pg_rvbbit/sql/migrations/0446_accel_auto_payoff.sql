-- 0446_accel_auto_payoff — acceleration chooses itself from real query evidence.
--
-- Three months of pilot use said the freshness machinery (targets, budgets, strategies,
-- worker slots) optimized a variable nobody watched, while the variable that matters —
-- is anyone reading this table through the accelerator? — was only advisory. Of 133
-- automatically refreshed tables, 4 had any query evidence; the other 129 cost 28 hours
-- of full folds a day because their loaders truncate-and-reload hourly and a truncated
-- baseline can only be rebuilt whole. See docs/ACCEL_AUTO.md for the receipts.
--
-- What changes, all in place (no new strategy value, no new tables):
--   * the workload observer ignores internal callers (the appliance's own identities,
--     automation keys) — evidence_exclude_callers, matched on the caller's local part;
--   * the freshness tick orders candidates by real query evidence first and derives the
--     cadence from it: read in the last day → the policy's own interval; not read for a
--     week → tick_warm_interval_secs (6 h); longer → tick_cold_interval_secs (24 h).
--     'continuous' policies and cubes.* opt out. Dirty tables route to the heap in the
--     meantime, so correctness never waits on a fold;
--   * the autopilot promotes only when the read time pays for the folds:
--     attributed read ms/day >= act_payoff_multiplier × (fold ms × folds/day), where the
--     fold cost is the measured last rebuild (or a size estimate) and folds/day comes from
--     the ledger (or the write churn); the same bar gates reactivation. Rows enrolled by
--     rvbbit.accel_auto_reset (managed, inactive, never parked) are promoted the same way;
--   * rvbbit.accel_auto_reset(dry_run) hands every automatic non-cube policy to the
--     autopilot and parks it (files kept) so the estate re-earns acceleration from use.

ALTER TABLE rvbbit.accel_autopilot_config
    ADD COLUMN IF NOT EXISTS evidence_exclude_callers text[] NOT NULL
        DEFAULT ARRAY['calliope', 'rvbbit', 'codex', 'static-key'],
    ADD COLUMN IF NOT EXISTS tick_hot_window_hours integer NOT NULL DEFAULT 24
        CHECK (tick_hot_window_hours BETWEEN 1 AND 720),
    ADD COLUMN IF NOT EXISTS tick_warm_window_days integer NOT NULL DEFAULT 7
        CHECK (tick_warm_window_days BETWEEN 1 AND 365),
    ADD COLUMN IF NOT EXISTS tick_warm_interval_secs integer NOT NULL DEFAULT 21600
        CHECK (tick_warm_interval_secs >= 0),
    ADD COLUMN IF NOT EXISTS tick_cold_interval_secs integer NOT NULL DEFAULT 86400
        CHECK (tick_cold_interval_secs >= 0),
    ADD COLUMN IF NOT EXISTS act_payoff_multiplier double precision NOT NULL DEFAULT 2.0
        CHECK (act_payoff_multiplier >= 0),
    ADD COLUMN IF NOT EXISTS act_payoff_fold_bytes_per_ms double precision NOT NULL DEFAULT 4000.0
        CHECK (act_payoff_fold_bytes_per_ms > 0);

COMMENT ON COLUMN rvbbit.accel_autopilot_config.evidence_exclude_callers IS
    'Callers (local part of the identity, e.g. calliope, rvbbit, static-key) whose queries are not evidence: the appliance talking to itself.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_payoff_multiplier IS
    'Promote only when attributed read ms/day >= multiplier × projected fold ms/day.';

-- ── payoff: does reading this table through the accelerator pay for keeping it fresh? ──
CREATE OR REPLACE FUNCTION rvbbit.accel_payoff(p_table_oid oid)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    WITH cfg AS (
        SELECT greatest(lookback_hours, 1) AS lookback_hours, act_payoff_multiplier AS k, act_payoff_fold_bytes_per_ms AS bytes_per_ms
          FROM rvbbit.accel_autopilot_config WHERE singleton
    ),
    ev AS (
        SELECT coalesce(c.attributed_ms, 0)::double precision AS attributed_ms,
               coalesce(c.writes_delta, 0)::double precision AS writes_delta,
               greatest(coalesce(c.row_estimate, 0), 1)::double precision AS row_estimate,
               coalesce(c.table_bytes, 0)::double precision AS table_bytes,
               c.last_observed_at
          FROM rvbbit.accel_autopilot_candidates c WHERE c.table_oid = p_table_oid
    ),
    fresh AS (
        SELECT nullif(f.last_rebuild_ms, 0)::double precision AS last_rebuild_ms FROM rvbbit.accel_freshness f WHERE f.table_oid = p_table_oid
    ),
    ledger AS (
        SELECT count(*)::double precision / 7.0 AS folds_per_day
          FROM rvbbit.acceleration_operations o
         WHERE o.table_oid = p_table_oid AND o.operation = 'rebuild_acceleration' AND o.status = 'ok'
           AND o.started_at > now() - interval '7 days'
    ),
    calc AS (
        SELECT (SELECT attributed_ms FROM ev) * 24.0 / cfg.lookback_hours AS read_ms_per_day,
               coalesce((SELECT last_rebuild_ms FROM fresh),
                        greatest(60000.0, (SELECT table_bytes FROM ev) / cfg.bytes_per_ms)) AS fold_ms,
               -- folds/day: the ledger when the table has history; else the write churn seen by the
               -- (hourly) observer scaled to a day, floored at one fold a day for any dirty table
               greatest(1.0, coalesce(nullif((SELECT folds_per_day FROM ledger), 0),
                                      (SELECT writes_delta / row_estimate FROM ev) * 24.0)) AS folds_per_day,
               cfg.k AS k
          FROM cfg
    )
    SELECT jsonb_build_object(
               'read_ms_per_day', round(coalesce(read_ms_per_day, 0)),
               'fold_ms', round(fold_ms), 'folds_per_day', round(folds_per_day::numeric, 2),
               'fold_ms_per_day', round(fold_ms * folds_per_day), 'multiplier', k,
               'ok', coalesce(read_ms_per_day, 0) >= k * fold_ms * folds_per_day,
               'summary', format('%s ms read/day vs %s ms fold/day (x%s)',
                                 round(coalesce(read_ms_per_day, 0)), round(fold_ms * folds_per_day), k))
      FROM calc
$fn$;
COMMENT ON FUNCTION rvbbit.accel_payoff(oid) IS 'Read time attributed to a table per day against the projected cost of keeping its accelerator fresh; ok when reads pay for folds by act_payoff_multiplier.';

CREATE OR REPLACE FUNCTION rvbbit.accel_payoff_ok(p_table_oid oid)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((rvbbit.accel_payoff(p_table_oid)->>'ok')::boolean, false)
$fn$;

-- ── reset: hand the automatic estate to the autopilot and let use re-earn it ──
CREATE OR REPLACE FUNCTION rvbbit.accel_auto_reset(p_dry_run boolean DEFAULT true)
RETURNS TABLE(table_name text, strategy text, fold_min_per_day numeric, evidence_calls bigint, action text)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    r record;
    v_note text := format('auto: reset %s — parked until real query evidence pays for the fold (files kept)', to_char(now(), 'YYYY-MM-DD'));
BEGIN
    PERFORM set_config('rvbbit.accel_autopilot_actor', 'on', true);
    FOR r IN
        SELECT p.table_oid, p.table_oid::regclass::text AS tname, p.strategy,
               round(coalesce(o.mins, 0) / 2, 1) AS fold_min_per_day, coalesce(c.query_calls, 0) AS evidence_calls
          FROM rvbbit.accel_policy p
          LEFT JOIN rvbbit.accel_autopilot_candidates c ON c.table_oid = p.table_oid
          LEFT JOIN LATERAL (SELECT sum(extract(epoch FROM (coalesce(finished_at, now()) - started_at)))/60 AS mins
                               FROM rvbbit.acceleration_operations o WHERE o.table_oid = p.table_oid AND o.started_at > now() - interval '48 hours') o ON true
         WHERE p.active AND p.strategy <> 'manual'
           AND p.table_oid::regclass::text NOT LIKE 'cubes.%'
         ORDER BY coalesce(o.mins, 0) DESC
    LOOP
        table_name := r.tname; strategy := r.strategy; fold_min_per_day := r.fold_min_per_day; evidence_calls := r.evidence_calls;
        IF p_dry_run THEN
            action := 'would_park';
        ELSE
            UPDATE rvbbit.accel_policy
               SET managed_by = 'autopilot', managed_since = clock_timestamp(), active = false,
                   parked_at = clock_timestamp(), note = v_note, updated_at = clock_timestamp()
             WHERE table_oid = r.table_oid;
            PERFORM rvbbit._accel_log_event(nextval('rvbbit.accel_sweep_id_seq'), 'autopilot', 'table_finished',
                r.table_oid, r.tname, r.strategy, 'park', v_note, 'ok',
                p_details => jsonb_build_object('reset', true, 'fold_min_per_day', r.fold_min_per_day, 'evidence_calls', r.evidence_calls));
            action := 'parked';
        END IF;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('rvbbit.accel_autopilot_actor', 'off', true);
END $fn$;
COMMENT ON FUNCTION rvbbit.accel_auto_reset(boolean) IS 'Park every automatic non-cube acceleration policy under autopilot management (files kept); the autopilot re-promotes each table when real query evidence pays for its folds. Dry run by default.';

-- ── the three bodies below are the live definitions with the changes marked 0446 ──

CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_observe(p_source text DEFAULT 'manual'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE
    cfg                     rvbbit.accel_autopilot_config%ROWTYPE;
    v_run_id                bigint;
    v_shapes_seen           bigint := 0;
    v_shapes_resolved       bigint := 0;
    v_tables_observed       bigint := 0;
    v_ready                 bigint := 0;
    v_activity_available    boolean := false;
    v_activity_error        text;
    v_error                 text;
BEGIN
    SELECT * INTO cfg
      FROM rvbbit.accel_autopilot_config
     WHERE singleton;

    IF NOT FOUND THEN
        INSERT INTO rvbbit.accel_autopilot_config (singleton) VALUES (true)
        RETURNING * INTO cfg;
    END IF;

    IF NOT pg_try_advisory_xact_lock(1381187156, 9) THEN
        RETURN jsonb_build_object(
            'status', 'busy',
            'mode', cfg.mode,
            'message', 'another workload observer run is active'
        );
    END IF;

    INSERT INTO rvbbit.accel_observer_runs (
        status, source, lookback_hours, config_snapshot
    ) VALUES (
        CASE WHEN cfg.mode = 'off' THEN 'skipped' ELSE 'running' END,
        coalesce(nullif(btrim(p_source), ''), 'manual'),
        cfg.lookback_hours,
        to_jsonb(cfg) - 'singleton'
    ) RETURNING run_id INTO v_run_id;

    IF cfg.mode = 'off' THEN
        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(), message = 'observer mode is off'
         WHERE run_id = v_run_id;
        RETURN jsonb_build_object(
            'status', 'skipped', 'mode', cfg.mode, 'run_id', v_run_id,
            'message', 'observer mode is off'
        );
    END IF;

    BEGIN
        DROP TABLE IF EXISTS pg_temp.rvbbit_accel_observer_shapes;
        DROP TABLE IF EXISTS pg_temp.rvbbit_accel_observer_activity;
        DROP TABLE IF EXISTS pg_temp.rvbbit_accel_observer_current;
        DROP TABLE IF EXISTS pg_temp.rvbbit_accel_observer_evidence;

        CREATE TEMP TABLE rvbbit_accel_observer_shapes (
            sql_text       text NOT NULL,
            calls          bigint NOT NULL,
            actors         text[] NOT NULL,
            active_buckets timestamptz[] NOT NULL,
            inclusive_ms   double precision NOT NULL,
            p95_ms         double precision,
            source_tables  text[] NOT NULL DEFAULT '{}'
        ) ON COMMIT DROP;

        v_activity_available :=
            to_regclass('rvbbit.mcp_activity') IS NOT NULL
            AND to_regprocedure('rvbbit._cube_source_tables(text)') IS NOT NULL;

        IF v_activity_available THEN
            BEGIN
                -- to_jsonb(a)->>'subject' keeps this compatible with old activity
                -- tables which predate the subject/actor identity columns.
                EXECUTE $sql$
                    INSERT INTO pg_temp.rvbbit_accel_observer_shapes (
                        sql_text, calls, actors, active_buckets, inclusive_ms, p95_ms
                    )
                    SELECT a.args->>'sql',
                           count(*)::bigint,
                           array_agg(DISTINCT coalesce(
                               nullif(to_jsonb(a)->>'subject', ''),
                               nullif(to_jsonb(a)->>'actor', ''),
                               nullif(a.caller, ''),
                               '?'
                           )),
                           array_agg(DISTINCT date_trunc('hour', a.ts)),
                           coalesce(sum(greatest(coalesce(a.elapsed_ms, 0), 0)), 0)::double precision,
                           percentile_cont(0.95) WITHIN GROUP (
                               ORDER BY greatest(coalesce(a.elapsed_ms, 0), 0)
                           )::double precision
                      FROM rvbbit.mcp_activity a
                     WHERE a.ok
                       AND a.tool IN ('dashboard_query', 'run_sql')
                       AND NOT (coalesce(split_part(a.caller, '@', 1), '') = ANY($3))
                       AND a.ts >= clock_timestamp() - make_interval(hours => $1)
                       AND nullif(btrim(a.args->>'sql'), '') IS NOT NULL
                       AND (a.args->>'sql') ~* '^[[:space:]]*(select|with|table)\M'
                     GROUP BY a.args->>'sql'
                     ORDER BY sum(greatest(coalesce(a.elapsed_ms, 0), 0)) DESC,
                              count(*) DESC
                     LIMIT $2
                $sql$ USING cfg.lookback_hours, cfg.max_query_shapes, cfg.evidence_exclude_callers;

                SELECT count(*) INTO v_shapes_seen
                  FROM pg_temp.rvbbit_accel_observer_shapes;

                -- EXPLAIN (never ANALYZE) is isolated in the existing best-effort
                -- helper; a malformed/stale query resolves to an empty array.
                UPDATE pg_temp.rvbbit_accel_observer_shapes
                   SET source_tables = rvbbit._cube_source_tables(sql_text);

                SELECT count(*) INTO v_shapes_resolved
                  FROM pg_temp.rvbbit_accel_observer_shapes
                 WHERE cardinality(source_tables) > 0;
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS v_activity_error = MESSAGE_TEXT;
                v_activity_available := false;
                v_shapes_seen := 0;
                v_shapes_resolved := 0;
                TRUNCATE pg_temp.rvbbit_accel_observer_shapes;
            END;
        END IF;

        CREATE TEMP TABLE rvbbit_accel_observer_activity ON COMMIT DROP AS
        WITH expanded AS MATERIALIZED (
            SELECT to_regclass(src.table_name)::oid AS table_oid,
                   s.sql_text,
                   s.calls,
                   s.actors,
                   s.active_buckets,
                   s.inclusive_ms,
                   s.p95_ms,
                   greatest(cardinality(s.source_tables), 1)::double precision AS relation_count
              FROM pg_temp.rvbbit_accel_observer_shapes s
              CROSS JOIN LATERAL unnest(s.source_tables) AS src(table_name)
             WHERE to_regclass(src.table_name) IS NOT NULL
        ), metrics AS (
            SELECT table_oid,
                   count(*)::bigint AS query_shapes,
                   sum(calls)::bigint AS query_calls,
                   sum(inclusive_ms)::double precision AS inclusive_ms,
                   sum(inclusive_ms / relation_count)::double precision AS attributed_ms,
                   max(p95_ms)::double precision AS p95_ms
              FROM expanded
             GROUP BY table_oid
        ), people AS (
            SELECT e.table_oid, count(DISTINCT actor)::bigint AS users
              FROM expanded e
              CROSS JOIN LATERAL unnest(e.actors) AS actor
             GROUP BY e.table_oid
        ), hours AS (
            SELECT e.table_oid, count(DISTINCT bucket)::bigint AS active_hours
              FROM expanded e
              CROSS JOIN LATERAL unnest(e.active_buckets) AS bucket
             GROUP BY e.table_oid
        )
        SELECT m.table_oid, m.query_shapes, m.query_calls,
               coalesce(p.users, 0)::bigint AS users,
               coalesce(h.active_hours, 0)::bigint AS active_hours,
               m.inclusive_ms, m.attributed_ms, m.p95_ms
          FROM metrics m
          LEFT JOIN people p USING (table_oid)
          LEFT JOIN hours h USING (table_oid);

        CREATE TEMP TABLE rvbbit_accel_observer_current ON COMMIT DROP AS
        WITH row_group_totals AS (
            SELECT table_oid, count(*)::bigint AS row_groups
              FROM rvbbit.row_groups
             GROUP BY table_oid
        )
        SELECT c.oid AS table_oid,
               c.oid::regclass::text AS table_name,
               n.nspname AS schema_name,
               c.relkind,
               c.relpersistence,
               coalesce(am.amname, '') AS access_method,
               c.relispartition AS is_partition,
               (c.relrowsecurity OR c.relforcerowsecurity) AS row_security,
               greatest(coalesce(s.n_live_tup, 0), greatest(c.reltuples, 0))::bigint AS row_estimate,
               CASE WHEN c.relkind IN ('r', 'p', 'm')
                    THEN pg_total_relation_size(c.oid)::bigint ELSE 0::bigint END AS table_bytes,
               coalesce(s.seq_scan, 0)::bigint AS seq_scans_total,
               coalesce(s.seq_tup_read, 0)::bigint AS seq_rows_total,
               (coalesce(s.n_tup_ins, 0) + coalesce(s.n_tup_upd, 0) + coalesce(s.n_tup_del, 0))::bigint AS writes_total,
               t.table_oid IS NOT NULL AS registered,
               coalesce(t.acceleration_enabled, false) AS acceleration_enabled,
               coalesce(rg.row_groups, 0)::bigint AS row_groups,
               (
                   coalesce(t.acceleration_enabled, false)
                   AND coalesce(rg.row_groups, 0) = 0
                   AND greatest(coalesce(s.n_live_tup, 0), greatest(c.reltuples, 0)) > 0
               ) AS baseline_missing
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_am am ON am.oid = c.relam
          LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
          LEFT JOIN rvbbit.tables t ON t.table_oid = c.oid
          LEFT JOIN row_group_totals rg ON rg.table_oid = c.oid
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'rvbbit')
           AND n.nspname NOT LIKE 'pg_toast%'
           AND n.nspname NOT LIKE 'pg_temp_%'
           AND n.nspname <> ALL(cfg.excluded_schemas)
           AND (
               c.relkind = 'r'
               OR EXISTS (
                   SELECT 1 FROM pg_temp.rvbbit_accel_observer_activity a
                    WHERE a.table_oid = c.oid
               )
           );

        -- OIDs can be reused after a drop. A changed regclass name is a new
        -- table and must start with a clean counter baseline.
        DELETE FROM rvbbit.accel_autopilot_candidates c
         WHERE to_regclass(c.table_name) IS NULL
            OR to_regclass(c.table_name)::oid IS DISTINCT FROM c.table_oid;
        DELETE FROM rvbbit.accel_observer_counters c
         WHERE to_regclass(c.table_name) IS NULL
            OR to_regclass(c.table_name)::oid IS DISTINCT FROM c.table_oid;

        CREATE TEMP TABLE rvbbit_accel_observer_evidence ON COMMIT DROP AS
        WITH base AS (
            SELECT cur.*,
                   coalesce(a.query_shapes, 0)::bigint AS query_shapes,
                   coalesce(a.query_calls, 0)::bigint AS query_calls,
                   coalesce(a.users, 0)::bigint AS users,
                   coalesce(a.active_hours, 0)::bigint AS active_hours,
                   coalesce(a.inclusive_ms, 0)::double precision AS inclusive_ms,
                   coalesce(a.attributed_ms, 0)::double precision AS attributed_ms,
                   a.p95_ms,
                   CASE
                       WHEN prev.table_oid IS NULL OR prev.table_name <> cur.table_name THEN 0
                       WHEN cur.seq_scans_total >= prev.seq_scans_total
                           THEN cur.seq_scans_total - prev.seq_scans_total
                       ELSE 0
                   END::bigint AS seq_scans_delta,
                   CASE
                       WHEN prev.table_oid IS NULL OR prev.table_name <> cur.table_name THEN 0
                       WHEN cur.seq_rows_total >= prev.seq_rows_total
                           THEN cur.seq_rows_total - prev.seq_rows_total
                       ELSE 0
                   END::bigint AS seq_rows_delta,
                   CASE
                       WHEN prev.table_oid IS NULL OR prev.table_name <> cur.table_name THEN 0
                       WHEN cur.writes_total >= prev.writes_total
                           THEN cur.writes_total - prev.writes_total
                       ELSE 0
                   END::bigint AS writes_delta,
                   old.table_oid IS NOT NULL AS previously_observed
              FROM pg_temp.rvbbit_accel_observer_current cur
              LEFT JOIN pg_temp.rvbbit_accel_observer_activity a USING (table_oid)
              LEFT JOIN rvbbit.accel_observer_counters prev USING (table_oid)
              LEFT JOIN rvbbit.accel_autopilot_candidates old USING (table_oid)
        ), measured AS (
            SELECT b.*,
                   (b.relkind = 'r'
                    AND b.relpersistence = 'p'
                    AND NOT b.is_partition
                    AND NOT b.row_security
                    AND b.access_method IN ('heap', 'rvbbit')) AS structurally_eligible,
                   (b.writes_delta::double precision / greatest(b.row_estimate, 1)) AS write_ratio,
                   (b.query_calls >= cfg.min_calls
                    AND b.active_hours >= cfg.min_active_hours
                    AND b.attributed_ms >= cfg.min_attributed_ms) AS hot
              FROM base b
        ), classified AS (
            SELECT m.*,
                   (m.structurally_eligible
                    AND m.table_bytes BETWEEN cfg.min_table_bytes AND cfg.max_table_bytes
                    AND m.write_ratio <= cfg.max_write_ratio) AS eligible,
                   least(100.0, greatest(0.0,
                       least(32.0, ln(1 + greatest(m.query_calls, 0)) * 6.0)
                       + least(18.0, greatest(m.active_hours, 0) * 3.0)
                       + least(28.0, greatest(m.attributed_ms, 0) / 1000.0)
                       + CASE WHEN m.baseline_missing THEN 8.0 ELSE 0.0 END
                       - least(30.0, m.write_ratio * 100.0)
                   ))::double precision AS score
              FROM measured m
        )
        SELECT c.*,
               CASE
                   -- 0446: a parked policy (files kept, autopilot-owned, inactive) is 'parked', and
                   -- 'ready' when it is hot and eligible again; 'managed' means actively accelerated.
                   WHEN EXISTS (SELECT 1 FROM rvbbit.accel_policy pp
                                 WHERE pp.table_oid = c.table_oid AND pp.managed_by = 'autopilot' AND NOT pp.active)
                        THEN CASE WHEN c.hot AND c.eligible THEN 'ready' ELSE 'parked' END
                   WHEN c.acceleration_enabled AND c.row_groups > 0 THEN 'managed'
                   WHEN NOT c.structurally_eligible THEN 'held'
                   WHEN c.hot AND NOT c.eligible THEN 'held'
                   WHEN c.hot AND c.eligible THEN 'ready'
                   ELSE 'observing'
               END::text AS status,
               array_remove(ARRAY[
                   CASE WHEN c.query_calls > 0 THEN format(
                       '%s calls across %s active hour%s; %s ms attributed',
                       c.query_calls, c.active_hours,
                       CASE WHEN c.active_hours = 1 THEN '' ELSE 's' END,
                       round(c.attributed_ms::numeric)
                   ) END,
                   CASE WHEN c.baseline_missing THEN
                       'registered and enabled, but no accelerator baseline exists' END,
                   CASE WHEN c.acceleration_enabled AND c.row_groups > 0 THEN
                       'already accelerated; retained as post-build evidence' END,
                   CASE WHEN c.relkind <> 'r' THEN
                       'not an ordinary table' END,
                   CASE WHEN c.relpersistence <> 'p' THEN
                       'temporary or unlogged tables are never auto-admission candidates' END,
                   CASE WHEN c.is_partition THEN
                       'partition children require an explicit layout decision' END,
                   CASE WHEN c.row_security THEN
                       'row-level-security tables require explicit review' END,
                   CASE WHEN c.access_method NOT IN ('heap', 'rvbbit') THEN
                       'unsupported table access method: ' || coalesce(nullif(c.access_method, ''), 'none') END,
                   CASE WHEN c.table_bytes < cfg.min_table_bytes THEN format(
                       'below the %s byte minimum; acceleration is unlikely to pay back',
                       cfg.min_table_bytes
                   ) END,
                   CASE WHEN c.table_bytes > cfg.max_table_bytes THEN format(
                       'above the %s byte unattended-build ceiling', cfg.max_table_bytes
                   ) END,
                   CASE WHEN c.write_ratio > cfg.max_write_ratio THEN format(
                       'write ratio %s exceeds the %s safety ceiling',
                       round(c.write_ratio::numeric, 4), cfg.max_write_ratio
                   ) END,
                   CASE WHEN NOT c.hot THEN
                       'collecting more recurring SELECT-query evidence' END
               ]::text[], NULL)::text[] AS reasons
          FROM classified c
         WHERE c.query_calls > 0
            OR c.previously_observed;

        INSERT INTO rvbbit.accel_observer_observations (
            run_id, table_oid, table_name, relkind, relpersistence, access_method,
            is_partition, row_security, structurally_eligible, eligible, hot,
            status, score, reasons, query_shapes, query_calls, users, active_hours,
            inclusive_ms, attributed_ms, p95_ms, seq_scans_total, seq_scans_delta,
            seq_rows_total, seq_rows_delta, writes_total, writes_delta, write_ratio,
            row_estimate, table_bytes, registered, acceleration_enabled, row_groups,
            baseline_missing
        )
        SELECT v_run_id, table_oid, table_name, relkind, relpersistence, access_method,
               is_partition, row_security, structurally_eligible, eligible, hot,
               status, score, reasons, query_shapes, query_calls, users, active_hours,
               inclusive_ms, attributed_ms, p95_ms, seq_scans_total, seq_scans_delta,
               seq_rows_total, seq_rows_delta, writes_total, writes_delta, write_ratio,
               row_estimate, table_bytes, registered, acceleration_enabled, row_groups,
               baseline_missing
          FROM pg_temp.rvbbit_accel_observer_evidence;

        INSERT INTO rvbbit.accel_autopilot_candidates (
            table_oid, table_name, first_observed_at, last_observed_at,
            last_evidence_at, observation_count, hot_observation_count, hot_streak,
            status, structurally_eligible, eligible, hot, score, reasons,
            query_shapes, query_calls, users, active_hours, inclusive_ms,
            attributed_ms, p95_ms, seq_scans_total, seq_scans_delta,
            seq_rows_total, seq_rows_delta, writes_total, writes_delta, write_ratio,
            row_estimate, table_bytes, registered, acceleration_enabled, row_groups,
            baseline_missing, last_run_id
        )
        SELECT table_oid, table_name, clock_timestamp(), clock_timestamp(),
               clock_timestamp(), 1, CASE WHEN hot THEN 1 ELSE 0 END,
               CASE WHEN hot THEN 1 ELSE 0 END,
               status, structurally_eligible, eligible, hot, score, reasons,
               query_shapes, query_calls, users, active_hours, inclusive_ms,
               attributed_ms, p95_ms, seq_scans_total, seq_scans_delta,
               seq_rows_total, seq_rows_delta, writes_total, writes_delta, write_ratio,
               row_estimate, table_bytes, registered, acceleration_enabled, row_groups,
               baseline_missing, v_run_id
          FROM pg_temp.rvbbit_accel_observer_evidence
        ON CONFLICT (table_oid) DO UPDATE SET
            table_name = EXCLUDED.table_name,
            last_observed_at = EXCLUDED.last_observed_at,
            last_evidence_at = CASE
                WHEN EXCLUDED.query_calls > 0
                    THEN EXCLUDED.last_observed_at
                ELSE rvbbit.accel_autopilot_candidates.last_evidence_at
            END,
            observation_count = rvbbit.accel_autopilot_candidates.observation_count + 1,
            hot_observation_count = rvbbit.accel_autopilot_candidates.hot_observation_count
                + CASE WHEN EXCLUDED.hot THEN 1 ELSE 0 END,
            hot_streak = CASE WHEN EXCLUDED.hot
                THEN rvbbit.accel_autopilot_candidates.hot_streak + 1 ELSE 0 END,
            status = EXCLUDED.status,
            structurally_eligible = EXCLUDED.structurally_eligible,
            eligible = EXCLUDED.eligible,
            hot = EXCLUDED.hot,
            score = EXCLUDED.score,
            reasons = EXCLUDED.reasons,
            query_shapes = EXCLUDED.query_shapes,
            query_calls = EXCLUDED.query_calls,
            users = EXCLUDED.users,
            active_hours = EXCLUDED.active_hours,
            inclusive_ms = EXCLUDED.inclusive_ms,
            attributed_ms = EXCLUDED.attributed_ms,
            p95_ms = EXCLUDED.p95_ms,
            seq_scans_total = EXCLUDED.seq_scans_total,
            seq_scans_delta = EXCLUDED.seq_scans_delta,
            seq_rows_total = EXCLUDED.seq_rows_total,
            seq_rows_delta = EXCLUDED.seq_rows_delta,
            writes_total = EXCLUDED.writes_total,
            writes_delta = EXCLUDED.writes_delta,
            write_ratio = EXCLUDED.write_ratio,
            row_estimate = EXCLUDED.row_estimate,
            table_bytes = EXCLUDED.table_bytes,
            registered = EXCLUDED.registered,
            acceleration_enabled = EXCLUDED.acceleration_enabled,
            row_groups = EXCLUDED.row_groups,
            baseline_missing = EXCLUDED.baseline_missing,
            last_run_id = EXCLUDED.last_run_id;

        -- Snapshot every ordinary table so the next run sees interval deltas,
        -- while retaining detailed history only for tables with evidence.
        INSERT INTO rvbbit.accel_observer_counters (
            table_oid, table_name, seq_scans_total, seq_rows_total, writes_total, observed_at
        )
        SELECT table_oid, table_name, seq_scans_total, seq_rows_total,
               writes_total, clock_timestamp()
          FROM pg_temp.rvbbit_accel_observer_current
         WHERE relkind = 'r'
        ON CONFLICT (table_oid) DO UPDATE SET
            table_name = EXCLUDED.table_name,
            seq_scans_total = EXCLUDED.seq_scans_total,
            seq_rows_total = EXCLUDED.seq_rows_total,
            writes_total = EXCLUDED.writes_total,
            observed_at = EXCLUDED.observed_at;

        DELETE FROM rvbbit.accel_observer_observations
         WHERE observed_at < clock_timestamp() - make_interval(days => cfg.retention_days);
        DELETE FROM rvbbit.accel_autopilot_candidates
         WHERE last_evidence_at < clock_timestamp() - make_interval(days => cfg.retention_days);
        DELETE FROM rvbbit.accel_observer_runs r
         WHERE r.started_at < clock_timestamp() - make_interval(days => cfg.retention_days)
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.accel_observer_observations o WHERE o.run_id = r.run_id
           );

        SELECT count(*), count(*) FILTER (WHERE status = 'ready')
          INTO v_tables_observed, v_ready
          FROM pg_temp.rvbbit_accel_observer_evidence;

        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(),
               status = 'ok',
               query_shapes_seen = v_shapes_seen,
               query_shapes_resolved = v_shapes_resolved,
               tables_observed = v_tables_observed,
               ready_count = v_ready,
               message = CASE WHEN v_activity_available
                   THEN 'observe-only; no tables were changed'
                   ELSE 'observe-only; SELECT activity unavailable; table statistics retained as context only'
                        || CASE WHEN v_activity_error IS NOT NULL THEN ': ' || v_activity_error ELSE '' END
               END
         WHERE run_id = v_run_id;

        RETURN jsonb_build_object(
            'status', 'ok',
            'mode', cfg.mode,
            'run_id', v_run_id,
            'query_shapes_seen', v_shapes_seen,
            'query_shapes_resolved', v_shapes_resolved,
            'tables_observed', v_tables_observed,
            'ready', v_ready,
            'activity_source', v_activity_available,
            'activity_error', v_activity_error,
            'mutations', 0,
            'message', 'observe-only; no tables were changed'
        );
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_error = MESSAGE_TEXT;
        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(), status = 'failed', error = v_error
         WHERE run_id = v_run_id;
        RETURN jsonb_build_object(
            'status', 'failed', 'mode', cfg.mode, 'run_id', v_run_id,
            'error', v_error, 'mutations', 0
        );
    END;
END
$function$;

CREATE OR REPLACE FUNCTION rvbbit._accel_tick_batch(budget integer DEFAULT NULL::integer, dry_run boolean DEFAULT false, lance_budget integer DEFAULT 1)
 RETURNS TABLE(table_oid oid, table_name text, strategy text, action text, reason text, drift_rows bigint, drift_ratio double precision, seconds_dirty double precision, heap_seq_scans bigint, executed boolean, status text, rows_written bigint, error text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    cand record;
    acted integer := 0;
    lance_acted integer := 0;
    do_execute boolean;
    should_act boolean;
    prop_action text;
    prop_reason text;
    act_reason text;
    baseline_missing boolean;
    res jsonb;
    last_scans bigint;
    used_today integer;
    is_lance boolean;
    sweep_id bigint;
    sweep_started timestamptz;
    table_started timestamptz;
    original_application_name text;
    operation_id bigint;
    written_row_groups bigint;
    candidate_count integer := 0;
    deferred_count integer := 0;
    skipped_count integer := 0;
    failed_count integer := 0;
    total_rows_written bigint := 0;
    live_candidate record;
    worker_slot_setting text := current_setting('rvbbit.accel_tick_worker_slot', true);
    worker_count_setting text := current_setting('rvbbit.accel_tick_worker_count', true);
    worker_slot integer := CASE
        WHEN worker_slot_setting ~ '^[0-9]+$' THEN worker_slot_setting::integer
        ELSE 0
    END;
    worker_count integer := CASE
        WHEN worker_count_setting ~ '^[0-9]+$' THEN worker_count_setting::integer
        ELSE 1
    END;
    parallel_worker boolean := false;
    fallback_deferred boolean := false;
    delta_error text;
    final_lock_retry_after timestamptz;
    claimed_heavy_slot integer := 0;
    heavy_capacity_deferred boolean := false;
    deferred_rows_reported integer := 0;
    -- 0446: real query evidence sets the cadence. Tables read in the last day keep
    -- their policy interval; tables not read for a week wait tier_warm_interval,
    -- longer than that tier_cold_interval. Cubes and 'continuous' policies opt out.
    tier_hot_hours integer := 24;
    tier_warm_days integer := 7;
    tier_warm_interval integer := 21600;
    tier_cold_interval integer := 86400;
    tier_name text;
    effective_min_interval integer;
BEGIN
    SELECT coalesce(c.tick_hot_window_hours, 24), coalesce(c.tick_warm_window_days, 7),
           coalesce(c.tick_warm_interval_secs, 21600), coalesce(c.tick_cold_interval_secs, 86400)
      INTO tier_hot_hours, tier_warm_days, tier_warm_interval, tier_cold_interval
      FROM rvbbit.accel_autopilot_config c WHERE c.singleton;
    parallel_worker := worker_count BETWEEN 1 AND 8
        AND worker_slot BETWEEN 1 AND worker_count;
    IF NOT dry_run THEN
        IF parallel_worker THEN
            -- Shared gate lets the two freshness slots coexist while the
            -- existing exclusive fold/legacy gate still keeps lanes apart.
            IF NOT pg_try_advisory_xact_lock_shared(1381187156, 7) THEN
                RETURN;
            END IF;
            IF NOT pg_try_advisory_xact_lock(
                1381187156,
                70 + worker_slot
            ) THEN
                RETURN;
            END IF;
        ELSIF NOT pg_try_advisory_xact_lock(1381187156, 7) THEN
            RETURN;
        END IF;
    END IF;

    IF NOT dry_run THEN
        original_application_name := current_setting('application_name', true);
        sweep_id := nextval('rvbbit.accel_sweep_id_seq');
        sweep_started := clock_timestamp();
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh s=%s w=%s/%s scan',
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END
                ),
                63
            ),
            true
        );
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'sweep_started',
            p_status => 'running',
            p_details => jsonb_build_object(
                'budget', budget,
                'lance_budget', lance_budget,
                'mode', CASE
                    WHEN parallel_worker THEN 'parallel_freshness'
                    ELSE 'freshness_only'
                END,
                'worker_slot', CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                'worker_count', CASE WHEN parallel_worker THEN worker_count ELSE 1 END
            )
        );
    END IF;

    -- Preserve the warm-prior heartbeat added by migration 0124.
    IF NOT dry_run
       AND (NOT parallel_worker OR worker_slot = 1)
       AND lower(coalesce(nullif(current_setting('rvbbit.route_gpu_gqe_prior', true), ''), 'off'))
           NOT IN ('off','false','0','no') THEN
        BEGIN
            PERFORM rvbbit.warm_gpu_gqe();
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;

    FOR cand IN
        SELECT f.table_oid AS f_oid,
               f.table_name AS f_name,
               f.shadow_heap_dirty,
               f.seconds_dirty,
               f.seconds_since_refresh,
               f.row_groups,
               f.heap_live_tuples,
               f.tombstones,
               f.drift_rows,
               f.drift_ratio,
               f.heap_seq_scans,
               f.lance_accelerated,
               rvbbit.current_replacement_pending(f.table_oid)
                   AS current_replacement_pending,
               e.strategy,
               e.freshness_target_secs,
               e.min_interval_secs,
               e.daily_refresh_budget,
               e.budget_epoch_at,
               e.full_rebuild_drift_ratio,
               e.lance_separate,
               coalesce(ac.last_evidence_at >= clock_timestamp() - make_interval(hours => tier_hot_hours), false)
                   AS evidence_hot,
               coalesce(ac.last_evidence_at >= clock_timestamp() - make_interval(days => tier_warm_days), false)
                   AS evidence_warm
          FROM rvbbit.accel_freshness f
          JOIN rvbbit.accel_policy_effective e ON e.table_oid = f.table_oid
          LEFT JOIN rvbbit.accel_autopilot_candidates ac ON ac.table_oid = f.table_oid
         WHERE e.active
           AND e.strategy <> 'manual'
           -- capacity-eligible candidates only: clean accelerators do not
           -- belong in every worker's execution scan.
           AND (
               coalesce(
                   rvbbit.current_replacement_pending(f.table_oid),
                   false
               )
               OR coalesce(f.shadow_heap_dirty, false)
               OR (
                   coalesce(f.row_groups, 0) = 0
                   AND coalesce(f.heap_live_tuples, 0) > 0
               )
           )
         ORDER BY evidence_hot DESC,
                  evidence_warm DESC,
                  rvbbit.current_replacement_pending(f.table_oid) DESC,
                  CASE
                      WHEN parallel_worker THEN
                          (mod(f.table_oid::bigint, worker_count::bigint)
                              + 1 = worker_slot)::integer
                      ELSE 1
                  END DESC,
                  (f.drift_rows * (1 + f.heap_seq_scans)) DESC NULLS LAST,
                  f.seconds_dirty DESC NULLS LAST,
                  f.table_oid
    LOOP
        candidate_count := candidate_count + 1;
        baseline_missing := cand.row_groups = 0 AND cand.heap_live_tuples > 0;

        IF cand.current_replacement_pending THEN
            -- A current-only TRUNCATE is an invalidated baseline, not a delta.
            prop_action := 'full';
            prop_reason := 'current replacement pending';
        ELSIF cand.shadow_heap_dirty THEN
            IF cand.drift_ratio IS NULL THEN
                prop_action := 'full';
                prop_reason := 'no accelerator baseline';
            ELSIF cand.drift_ratio >= cand.full_rebuild_drift_ratio THEN
                prop_action := 'full';
                prop_reason := format(
                    'drift_ratio %s >= %s',
                    round(cand.drift_ratio::numeric, 6),
                    round(cand.full_rebuild_drift_ratio::numeric, 6)
                );
            ELSE
                prop_action := 'delta';
                prop_reason := 'dirty';
            END IF;
        ELSIF baseline_missing THEN
            prop_action := 'delta';
            prop_reason := 'no accelerator baseline';
        ELSE
            prop_action := 'skip';
            prop_reason := 'clean';
        END IF;

        is_lance := false; -- Lance runtime removed; compatibility metadata is inert.
        should_act := false;
        act_reason := prop_reason;
        IF cand.strategy = 'continuous' OR cand.f_name LIKE 'cubes.%' OR cand.evidence_hot THEN
            tier_name := CASE WHEN cand.evidence_hot THEN 'hot' ELSE 'exempt' END;
            effective_min_interval := cand.min_interval_secs;
        ELSIF cand.evidence_warm THEN
            tier_name := 'warm';
            effective_min_interval := greatest(cand.min_interval_secs, tier_warm_interval);
        ELSE
            tier_name := 'cold';
            effective_min_interval := greatest(cand.min_interval_secs, tier_cold_interval);
        END IF;
        SELECT rvbbit.accel_final_lock_retry_after(cand.f_oid)
          INTO final_lock_retry_after;

        IF prop_action = 'skip' THEN
            act_reason := prop_reason;
        ELSIF final_lock_retry_after > clock_timestamp() THEN
            act_reason := format(
                'final lock retry cooling down until %s',
                to_char(final_lock_retry_after, 'YYYY-MM-DD HH24:MI:SSOF')
            );
        ELSIF cand.seconds_since_refresh IS NOT NULL
              AND cand.seconds_since_refresh < effective_min_interval THEN
            act_reason := format('min_interval %ss not elapsed (%s evidence tier)', effective_min_interval, tier_name);
        ELSE
            IF cand.strategy IN ('scheduled', 'continuous') THEN
                should_act := true;
                act_reason := prop_reason;
            ELSIF cand.strategy = 'target' THEN
                IF baseline_missing THEN
                    should_act := true;
                    act_reason := prop_reason || '; target requires a baseline';
                ELSIF cand.freshness_target_secs IS NULL
                   OR coalesce(cand.seconds_dirty, 0) >= cand.freshness_target_secs THEN
                    should_act := true;
                    act_reason := format(
                        '%s; stale %ss >= target %ss',
                        prop_reason,
                        round(coalesce(cand.seconds_dirty, 0))::int,
                        coalesce(cand.freshness_target_secs, 0)
                    );
                ELSE
                    act_reason := format(
                        'within target (%ss < %ss)',
                        round(coalesce(cand.seconds_dirty, 0))::int,
                        cand.freshness_target_secs
                    );
                END IF;
            ELSIF cand.strategy = 'demand' THEN
                SELECT r.heap_seq_scans
                  INTO last_scans
                  FROM rvbbit.accel_tick_runs r
                 WHERE r.table_oid = cand.f_oid
                 ORDER BY r.ran_at DESC
                 LIMIT 1;

                IF baseline_missing
                   AND cand.heap_seq_scans > coalesce(last_scans, 0) THEN
                    should_act := true;
                    act_reason := prop_reason || '; observed slow-path demand';
                ELSIF last_scans IS NOT NULL
                      AND cand.heap_seq_scans > last_scans THEN
                    should_act := true;
                    act_reason := prop_reason || '; demand grew on slow path';
                ELSE
                    act_reason := CASE
                        WHEN last_scans IS NULL THEN 'demand baseline'
                        ELSE 'no new slow-path demand'
                    END;
                END IF;
            END IF;

            -- Policy lifecycle budget epoch: keep historical receipts without
            -- charging a newly enrolled automation policy for old work.
            IF should_act AND cand.daily_refresh_budget IS NOT NULL THEN
                used_today := rvbbit.accel_budget_spent(cand.f_oid, cand.budget_epoch_at);
                IF used_today >= cand.daily_refresh_budget THEN
                    -- An exhausted budget slows a table down; it never lets the
                    -- accelerator rot. Past the overdue horizon, or with no
                    -- baseline at all, the refresh proceeds and says why.
                    IF baseline_missing
                       OR coalesce(cand.seconds_dirty, 0)
                          >= rvbbit.accel_budget_overdue_secs(
                                 cand.freshness_target_secs, cand.min_interval_secs
                             ) THEN
                        act_reason := format(
                            '%s; daily budget %s spent but overdue (%ss >= %ss), refreshing anyway',
                            act_reason,
                            cand.daily_refresh_budget,
                            round(coalesce(cand.seconds_dirty, 0))::int,
                            rvbbit.accel_budget_overdue_secs(
                                cand.freshness_target_secs, cand.min_interval_secs
                            )
                        );
                    ELSE
                        should_act := false;
                        act_reason := format(
                            'daily budget %s spent; deferring until dirty >= %ss (now %ss)',
                            cand.daily_refresh_budget,
                            rvbbit.accel_budget_overdue_secs(
                                cand.freshness_target_secs, cand.min_interval_secs
                            ),
                            round(coalesce(cand.seconds_dirty, 0))::int
                        );
                    END IF;
                END IF;
            END IF;
        END IF;

        do_execute := should_act;
        IF do_execute AND budget IS NOT NULL AND acted >= budget THEN
            do_execute := false;
            act_reason := 'tick budget reached';
        ELSIF do_execute AND is_lance AND lance_acted >= lance_budget THEN
            do_execute := false;
            act_reason := 'lance budget reached';
        END IF;

        _accel_tick_batch.table_oid := cand.f_oid;
        _accel_tick_batch.table_name := cand.f_name;
        _accel_tick_batch.strategy := cand.strategy;
        _accel_tick_batch.drift_rows := cand.drift_rows;
        _accel_tick_batch.drift_ratio := cand.drift_ratio;
        _accel_tick_batch.seconds_dirty := cand.seconds_dirty;
        _accel_tick_batch.heap_seq_scans := cand.heap_seq_scans;
        _accel_tick_batch.rows_written := NULL;
        _accel_tick_batch.error := NULL;

        -- Claim shared heavy capacity before the table claim.  A worker that
        -- loses capacity must not retain advisory locks on every heavy table
        -- while it searches for independent delta work.
        IF do_execute
           AND NOT dry_run
           AND parallel_worker
           AND (prop_action = 'full' OR is_lance) THEN
            IF claimed_heavy_slot = 0 AND NOT heavy_capacity_deferred THEN
                claimed_heavy_slot :=
                    rvbbit._try_claim_accel_heavy_slot();
                heavy_capacity_deferred := claimed_heavy_slot = 0;
            END IF;
            IF heavy_capacity_deferred THEN
                do_execute := false;
                act_reason := prop_reason
                    || '; heavy maintenance slots busy';
            END IF;
        END IF;

        IF do_execute AND NOT dry_run AND parallel_worker THEN
            -- Claim before entering refresh/rebuild. The lower-level lock is
            -- re-entrant for rebuild/compact and harmless for delta refresh.
            IF NOT pg_try_advisory_xact_lock(
                (1380336724::bigint << 32) | cand.f_oid::bigint
            ) THEN
                do_execute := false;
                act_reason := 'table maintenance claimed by another worker';
            ELSE
                -- A manual action or another worker may have committed after
                -- this loop's candidate snapshot. Re-read cheap eligibility
                -- facts after the claim and never act on an obsolete policy.
                SELECT f.shadow_heap_dirty,
                       f.seconds_since_refresh,
                       f.row_groups,
                       f.heap_live_tuples,
                       rvbbit.current_replacement_pending(f.table_oid)
                           AS current_replacement_pending,
                       e.active,
                       e.strategy,
                       e.min_interval_secs,
                       e.daily_refresh_budget,
                       e.budget_epoch_at
                  INTO live_candidate
                  FROM rvbbit.accel_freshness f
                  JOIN rvbbit.accel_policy_effective e
                    ON e.table_oid = f.table_oid
                 WHERE f.table_oid = cand.f_oid;

                IF NOT FOUND
                   OR NOT coalesce(live_candidate.active, false)
                   OR live_candidate.strategy = 'manual' THEN
                    should_act := false;
                    do_execute := false;
                    act_reason := 'table or policy changed before worker claim';
                ELSIF live_candidate.strategy IS DISTINCT FROM cand.strategy THEN
                    do_execute := false;
                    act_reason := 'refresh strategy changed before worker claim';
                ELSIF NOT (
                    coalesce(live_candidate.current_replacement_pending, false)
                    OR coalesce(live_candidate.shadow_heap_dirty, false)
                    OR (
                        coalesce(live_candidate.row_groups, 0) = 0
                        AND coalesce(live_candidate.heap_live_tuples, 0) > 0
                    )
                ) THEN
                    should_act := false;
                    do_execute := false;
                    act_reason := 'candidate became clean before worker claim';
                ELSIF live_candidate.seconds_since_refresh IS NOT NULL
                      AND live_candidate.seconds_since_refresh
                          < greatest(live_candidate.min_interval_secs, effective_min_interval) THEN
                    do_execute := false;
                    act_reason := format(
                        'min_interval %ss not elapsed after worker claim (%s evidence tier)',
                        greatest(live_candidate.min_interval_secs, effective_min_interval), tier_name
                    );
                ELSE
                    -- Recheck the only mutable accounting gate after claiming.
                    IF live_candidate.daily_refresh_budget IS NOT NULL THEN
                        used_today := rvbbit.accel_budget_spent(cand.f_oid, live_candidate.budget_epoch_at);
                        IF used_today >= live_candidate.daily_refresh_budget
                           AND NOT baseline_missing
                           AND coalesce(cand.seconds_dirty, 0)
                               < rvbbit.accel_budget_overdue_secs(
                                     cand.freshness_target_secs, live_candidate.min_interval_secs
                                 ) THEN
                            do_execute := false;
                            act_reason := format(
                                'daily budget %s spent after worker claim; deferring until dirty >= %ss',
                                live_candidate.daily_refresh_budget,
                                rvbbit.accel_budget_overdue_secs(
                                    cand.freshness_target_secs, live_candidate.min_interval_secs
                                )
                            );
                        END IF;
                    END IF;
                END IF;
            END IF;
        END IF;

        IF NOT do_execute THEN
            _accel_tick_batch.action := CASE
                WHEN should_act THEN prop_action
                ELSE 'skip'
            END;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := false;
            _accel_tick_batch.status := CASE
                WHEN should_act THEN 'deferred'
                ELSE 'skip'
            END;
            IF should_act THEN
                deferred_count := deferred_count + 1;
            ELSE
                skipped_count := skipped_count + 1;
            END IF;
            IF NOT dry_run THEN
                -- Sweep totals retain the complete counts, while the row API
                -- and append-only run log receive only a small diagnostic
                -- sample.  This preserves useful direct-call evidence without
                -- multiplying one blocked pass into thousands of rows.
                IF NOT should_act THEN
                    CONTINUE;
                END IF;
                IF deferred_rows_reported >= 8 THEN
                    CONTINUE;
                END IF;
                deferred_rows_reported := deferred_rows_reported + 1;

                INSERT INTO rvbbit.accel_tick_runs (
                    table_oid, table_name, strategy, action, reason,
                    drift_rows, heap_seq_scans, executed, status
                ) VALUES (
                    cand.f_oid, cand.f_name, cand.strategy,
                    _accel_tick_batch.action, act_reason, cand.drift_rows,
                    cand.heap_seq_scans, false, _accel_tick_batch.status
                );
            END IF;
            RETURN NEXT;
            CONTINUE;
        END IF;

        acted := acted + 1;
        IF is_lance THEN
            lance_acted := lance_acted + 1;
        END IF;

        IF dry_run THEN
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := false;
            _accel_tick_batch.status := 'planned';
            RETURN NEXT;
            CONTINUE;
        END IF;

        table_started := clock_timestamp();
        res := NULL;
        operation_id := NULL;
        written_row_groups := NULL;
        fallback_deferred := false;
        delta_error := NULL;
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh t=%s o=%s a=%s s=%s w=%s/%s %s',
                    round(extract(epoch FROM table_started)::numeric, 3),
                    cand.f_oid,
                    prop_action,
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                    cand.f_name
                ),
                63
            ),
            true
        );
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'table_started',
            p_table_oid => cand.f_oid,
            p_table_name => cand.f_name,
            p_strategy => cand.strategy,
            p_action => prop_action,
            p_reason => act_reason,
            p_status => 'running',
            p_details => jsonb_strip_nulls(jsonb_build_object(
                'drift_rows', cand.drift_rows,
                'drift_ratio', cand.drift_ratio,
                'seconds_dirty', cand.seconds_dirty,
                'heap_seq_scans', cand.heap_seq_scans,
                'row_groups', cand.row_groups,
                'tombstones', cand.tombstones,
                'heavy_slot', nullif(
                    current_setting(
                        'rvbbit.accel_maintenance_heavy_slot', true
                    ),
                    ''
                )::integer,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            ))
        );

        BEGIN
            IF prop_action = 'full' THEN
                res := rvbbit.rebuild_acceleration(cand.f_oid::regclass, true);
            ELSE
                BEGIN
                    res := rvbbit.refresh_acceleration(cand.f_oid::regclass, true);
                EXCEPTION WHEN OTHERS THEN
                    delta_error := SQLERRM;
                    prop_action := 'full';
                    act_reason := act_reason || ' (delta->full: ' || delta_error || ')';
                    IF parallel_worker
                       AND rvbbit._try_claim_accel_heavy_slot() = 0 THEN
                        -- Do not wait behind a long full rebuild. A later tick
                        -- can retry the delta or acquire the heavy lane.
                        fallback_deferred := true;
                        act_reason := act_reason || '; heavy maintenance slots busy';
                        res := jsonb_build_object(
                            'status', 'deferred',
                            'reason', act_reason
                        );
                    ELSE
                        PERFORM set_config(
                            'application_name',
                            left(
                                format(
                                    'rvbbit/fresh t=%s o=%s a=full s=%s w=%s/%s %s',
                                    round(extract(epoch FROM table_started)::numeric, 3),
                                    cand.f_oid,
                                    sweep_id,
                                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                                    cand.f_name
                                ),
                                63
                            ),
                            true
                        );
                        res := rvbbit.rebuild_acceleration(cand.f_oid::regclass, true);
                    END IF;
                END;
            END IF;
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            IF fallback_deferred THEN
                _accel_tick_batch.executed := false;
                _accel_tick_batch.status := 'deferred';
                _accel_tick_batch.rows_written := 0;
                acted := greatest(acted - 1, 0);
                IF is_lance THEN
                    lance_acted := greatest(lance_acted - 1, 0);
                END IF;
                deferred_count := deferred_count + 1;
            ELSE
                _accel_tick_batch.executed := true;
                _accel_tick_batch.status := coalesce(res->>'status', 'ok');
                _accel_tick_batch.rows_written := coalesce((res->>'rows_written')::bigint, 0);
                operation_id := nullif(res->>'operation_id', '')::bigint;
                written_row_groups := nullif(res->>'row_groups_written', '')::bigint;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := true;
            _accel_tick_batch.status := 'failed';
            _accel_tick_batch.error := SQLERRM;
        END;

        IF _accel_tick_batch.status = 'failed' THEN
            failed_count := failed_count + 1;
        END IF;
        total_rows_written := total_rows_written + coalesce(_accel_tick_batch.rows_written, 0);

        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'table_finished',
            p_table_oid => cand.f_oid,
            p_table_name => cand.f_name,
            p_strategy => cand.strategy,
            p_action => _accel_tick_batch.action,
            p_reason => _accel_tick_batch.reason,
            p_status => _accel_tick_batch.status,
            p_operation_id => operation_id,
            p_elapsed_ms => greatest(
                0,
                extract(epoch FROM (clock_timestamp() - table_started)) * 1000.0
            ),
            p_rows_written => _accel_tick_batch.rows_written,
            p_row_groups_written => written_row_groups,
            p_details => jsonb_strip_nulls(jsonb_build_object(
                'error', _accel_tick_batch.error,
                'result', res,
                'heavy_slot', nullif(
                    current_setting(
                        'rvbbit.accel_maintenance_heavy_slot', true
                    ),
                    ''
                )::integer,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            ))
        );
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh s=%s w=%s/%s scan',
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END
                ),
                63
            ),
            true
        );

        INSERT INTO rvbbit.accel_tick_runs (
            table_oid, table_name, strategy, action, reason, drift_rows,
            heap_seq_scans, executed, status, rows_written, error
        ) VALUES (
            cand.f_oid, cand.f_name, cand.strategy, _accel_tick_batch.action,
            _accel_tick_batch.reason, cand.drift_rows, cand.heap_seq_scans,
            _accel_tick_batch.executed,
            _accel_tick_batch.status, _accel_tick_batch.rows_written, _accel_tick_batch.error
        );
        RETURN NEXT;
        -- One worker invocation owns at most one table action.  The outer
        -- worker-pass procedure commits before requesting the next table.
        EXIT;
    END LOOP;

    IF NOT dry_run THEN
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'sweep_finished',
            p_status => CASE WHEN failed_count > 0 THEN 'partial' ELSE 'ok' END,
            p_elapsed_ms => greatest(
                0,
                extract(epoch FROM (clock_timestamp() - sweep_started)) * 1000.0
            ),
            p_rows_written => total_rows_written,
            p_details => jsonb_build_object(
                'candidates', candidate_count,
                'tables_executed', acted,
                'tables_failed', failed_count,
                'tables_deferred', deferred_count,
                'tables_skipped', skipped_count,
                'lance_tables_executed', lance_acted,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            )
        );
        PERFORM set_config(
            'application_name',
            coalesce(original_application_name, ''),
            true
        );
    END IF;

    RETURN;
END;
$function$;

CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_act(p_source text DEFAULT 'manual'::text, p_dry_run boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'rvbbit'
AS $function$
DECLARE
    cfg rvbbit.accel_autopilot_config%ROWTYPE;
    sweep_id bigint;
    started timestamptz := clock_timestamp();
    cand record;
    pol record;
    promoted jsonb := '[]'::jsonb;
    parked jsonb := '[]'::jsonb;
    reactivated jsonb := '[]'::jsonb;
    failures jsonb := '[]'::jsonb;
    park_guard jsonb;
    world_calls bigint;
    observer_runs bigint;
    parks_today integer;
    world_awake boolean;
    observer_watching boolean;
    n_promoted integer := 0;
    n_parked integer := 0;
    n_reactivated integer := 0;
    t0 timestamptz;
    policy_result jsonb;
    interval_secs integer;
    note_text text;
    err text;
BEGIN
    SELECT * INTO cfg FROM rvbbit.accel_autopilot_config WHERE singleton;
    IF NOT FOUND OR cfg.mode <> 'act' THEN
        RETURN jsonb_build_object(
            'mode', coalesce(cfg.mode, 'missing'), 'acted', false,
            'reason', 'autopilot mode is not ''act''', 'source', p_source
        );
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtext('rvbbit.accel_autopilot_act')) THEN
        RETURN jsonb_build_object('mode', cfg.mode, 'acted', false,
                                  'reason', 'another autopilot run holds the lock', 'source', p_source);
    END IF;

    PERFORM set_config('rvbbit.accel_autopilot_actor', 'on', true);
    sweep_id := nextval('rvbbit.accel_sweep_id_seq');
    PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'sweep_started',
        p_details => jsonb_build_object('source', p_source, 'dry_run', p_dry_run));

    ------------------------------------------------------------------ promote
    FOR cand IN
        SELECT c.table_oid, c.table_name, c.score, c.hot_streak, c.query_calls,
               c.active_hours, c.attributed_ms, c.table_bytes, c.registered
          FROM rvbbit.accel_autopilot_candidates c
          LEFT JOIN rvbbit.accel_policy p ON p.table_oid = c.table_oid
         WHERE c.status = 'ready'
           AND c.hot
           AND c.eligible
           AND c.hot_streak >= cfg.act_promote_min_streak
           AND c.last_observed_at > now() - interval '3 hours'
           AND (
               p.table_oid IS NULL                                   -- never had a policy
               OR (p.managed_by = 'autopilot' AND NOT p.active AND p.parked_at IS NULL)  -- enrolled, awaiting evidence
           )
           AND rvbbit.accel_payoff_ok(c.table_oid)                  -- 0446: the read time must pay for the folds
           AND EXISTS (SELECT 1 FROM pg_class k WHERE k.oid = c.table_oid AND k.relkind = 'r')
         ORDER BY c.score DESC, c.table_bytes ASC
         LIMIT cfg.act_max_promotions_per_run
    LOOP
        t0 := clock_timestamp();
        interval_secs := CASE WHEN cand.table_bytes < cfg.act_small_table_bytes THEN 900 ELSE 3600 END;
        note_text := format('autopilot: promoted %s (%s calls / %s active hours / %s ms attributed; streak %s; payoff %s)',
                            to_char(now(), 'YYYY-MM-DD'), cand.query_calls, cand.active_hours,
                            round(cand.attributed_ms)::bigint, cand.hot_streak,
                            coalesce(rvbbit.accel_payoff(cand.table_oid)->>'summary', '?'));
        BEGIN
            IF NOT p_dry_run THEN
                IF NOT cand.registered THEN
                    PERFORM rvbbit.enable_table(cand.table_oid::regclass, true);
                END IF;
                PERFORM rvbbit.rebuild_acceleration(cand.table_oid::regclass, true);
                policy_result := rvbbit.set_accel_policy(
                    cand.table_oid::regclass,
                    strategy => 'scheduled',
                    freshness_target_secs => NULL,
                    min_interval_secs => interval_secs,
                    daily_refresh_budget => NULL,
                    full_rebuild_drift_ratio => 0.5,
                    lance_separate => true,
                    active => true,
                    note => note_text
                );
                UPDATE rvbbit.accel_policy
                   SET managed_by = 'autopilot', managed_since = clock_timestamp(), parked_at = NULL
                 WHERE table_oid = cand.table_oid;
                UPDATE rvbbit.accel_autopilot_candidates
                   SET status = 'managed'
                 WHERE table_oid = cand.table_oid;
            END IF;
            n_promoted := n_promoted + 1;
            promoted := promoted || jsonb_build_object(
                'table', cand.table_name, 'score', cand.score, 'min_interval_secs', interval_secs,
                'elapsed_ms', extract(epoch FROM clock_timestamp() - t0) * 1000.0);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                cand.table_oid, cand.table_name, 'scheduled', 'promote', note_text,
                CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                p_details => jsonb_build_object('score', cand.score, 'table_bytes', cand.table_bytes,
                                                'policy', coalesce(policy_result, '{}'::jsonb)));
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
            failures := failures || jsonb_build_object('table', cand.table_name, 'action', 'promote', 'error', err);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                cand.table_oid, cand.table_name, 'scheduled', 'promote', err, 'failed',
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0));
        END;
    END LOOP;

    --------------------------------------------------------------- reactivate
    FOR pol IN
        SELECT p.table_oid, p.table_oid::regclass::text AS table_name, c.query_calls, c.active_hours, c.hot_streak
          FROM rvbbit.accel_policy p
          JOIN rvbbit.accel_autopilot_candidates c ON c.table_oid = p.table_oid
         WHERE p.managed_by = 'autopilot'
           AND NOT p.active
           AND p.parked_at IS NOT NULL                              -- parked (had files); enrolled rows promote above
           AND c.hot
           AND c.hot_streak >= cfg.act_promote_min_streak
           AND rvbbit.accel_payoff_ok(p.table_oid)                  -- 0446: same bar as promotion
           AND c.last_observed_at > now() - interval '3 hours'
    LOOP
        t0 := clock_timestamp();
        note_text := format('autopilot: reactivated %s (%s calls / %s active hours)',
                            to_char(now(), 'YYYY-MM-DD'), pol.query_calls, pol.active_hours);
        BEGIN
            IF NOT p_dry_run THEN
                UPDATE rvbbit.accel_policy
                   SET active = true, parked_at = NULL, managed_since = clock_timestamp(),
                       note = note_text, updated_at = clock_timestamp()
                 WHERE table_oid = pol.table_oid;
            END IF;
            n_reactivated := n_reactivated + 1;
            reactivated := reactivated || jsonb_build_object('table', pol.table_name);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                pol.table_oid, pol.table_name, 'scheduled', 'reactivate', note_text,
                CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0));
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
            failures := failures || jsonb_build_object('table', pol.table_name, 'action', 'reactivate', 'error', err);
        END;
    END LOOP;

    --------------------------------------------------------------------- park
    SELECT count(*) INTO world_calls
      FROM rvbbit.mcp_activity a
     WHERE a.ts > now() - make_interval(days => cfg.act_park_idle_days)
       AND a.ok
       AND a.tool IN ('dashboard_query', 'run_sql');
    SELECT count(*) INTO observer_runs
      FROM rvbbit.accel_observer_runs r
     WHERE r.status = 'ok'
       AND r.started_at > now() - make_interval(days => cfg.act_park_idle_days);
    SELECT count(*) INTO parks_today
      FROM rvbbit.accel_activity_log a
     WHERE a.lane = 'autopilot' AND a.event_type = 'table_finished'
       AND a.action = 'park' AND a.status = 'ok'
       AND a.event_at > now() - interval '24 hours';
    world_awake := world_calls >= cfg.act_park_min_world_calls;
    observer_watching := observer_runs >= (cfg.act_park_idle_days * 24) / 2;
    park_guard := jsonb_build_object(
        'world_calls', world_calls, 'world_awake', world_awake,
        'observer_runs', observer_runs, 'observer_watching', observer_watching,
        'parks_today', parks_today, 'max_parks_per_day', cfg.act_max_parks_per_day);

    IF world_awake AND observer_watching AND parks_today < cfg.act_max_parks_per_day THEN
        FOR pol IN
            SELECT p.table_oid, p.table_oid::regclass::text AS table_name, p.managed_since,
                   rvbbit.accel_daily_refresh_cost_ms(p.table_oid) AS daily_cost_ms
              FROM rvbbit.accel_policy p
             WHERE p.managed_by = 'autopilot'
               AND p.active
               AND p.managed_since IS NOT NULL
               AND p.managed_since < now() - make_interval(days => cfg.act_park_min_age_days)
               -- the observer looked at this table during the window ...
               AND EXISTS (
                   SELECT 1 FROM rvbbit.accel_observer_observations o
                    WHERE o.table_oid = p.table_oid
                      AND o.observed_at > now() - make_interval(days => cfg.act_park_idle_days))
               -- ... and never saw a SELECT touch it
               AND NOT EXISTS (
                   SELECT 1 FROM rvbbit.accel_observer_observations o
                    WHERE o.table_oid = p.table_oid
                      AND o.observed_at > now() - make_interval(days => cfg.act_park_idle_days)
                      AND (o.query_calls > 0 OR o.hot))
               AND rvbbit.accel_daily_refresh_cost_ms(p.table_oid) >= cfg.act_park_min_daily_cost_ms
             ORDER BY rvbbit.accel_daily_refresh_cost_ms(p.table_oid) DESC
             LIMIT least(cfg.act_max_parks_per_run, cfg.act_max_parks_per_day - parks_today)
        LOOP
            t0 := clock_timestamp();
            note_text := format('autopilot: parked %s (no SELECT evidence for %s days; refresh cost %s ms/day; files kept)',
                                to_char(now(), 'YYYY-MM-DD'), cfg.act_park_idle_days,
                                round(pol.daily_cost_ms)::bigint);
            BEGIN
                IF NOT p_dry_run THEN
                    UPDATE rvbbit.accel_policy
                       SET active = false, parked_at = clock_timestamp(),
                           note = note_text, updated_at = clock_timestamp()
                     WHERE table_oid = pol.table_oid;
                END IF;
                n_parked := n_parked + 1;
                parked := parked || jsonb_build_object('table', pol.table_name, 'daily_cost_ms', pol.daily_cost_ms);
                PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                    pol.table_oid, pol.table_name, 'scheduled', 'park', note_text,
                    CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                    p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                    p_details => jsonb_build_object('daily_cost_ms', pol.daily_cost_ms) || park_guard);
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
                failures := failures || jsonb_build_object('table', pol.table_name, 'action', 'park', 'error', err);
            END;
        END LOOP;
    END IF;

    -- Release the actor marker before returning so a later statement in the
    -- same transaction (an operator's set_accel_policy) is treated as manual.
    PERFORM set_config('rvbbit.accel_autopilot_actor', 'off', true);
    PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'sweep_finished',
        p_status => CASE WHEN jsonb_array_length(failures) > 0 THEN 'partial' ELSE 'ok' END,
        p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - started) * 1000.0),
        p_details => jsonb_build_object('source', p_source, 'dry_run', p_dry_run,
            'promoted', n_promoted, 'parked', n_parked, 'reactivated', n_reactivated,
            'failures', jsonb_array_length(failures), 'park_guard', park_guard));

    RETURN jsonb_build_object(
        'mode', cfg.mode, 'acted', true, 'dry_run', p_dry_run, 'source', p_source,
        'sweep_id', sweep_id,
        'promoted', promoted, 'parked', parked, 'reactivated', reactivated,
        'failures', failures, 'park_guard', park_guard,
        'elapsed_ms', extract(epoch FROM clock_timestamp() - started) * 1000.0
    );
END;
$function$;

-- ── the write-ratio gate steps aside for the payoff rule ─────────────────────
-- max_write_ratio (writes per observation ÷ rows) predates the payoff rule and did the
-- same job crudely: hold churning tables. An hourly truncate-and-reload sits near 1.0
-- against 0.25, so those tables could never even be candidates — including the
-- Salesforce tables, which look incremental but receive updates and cannot safely be
-- merged without introspection, so they stay snapshots. The payoff rule prices that churn
-- exactly (folds per day × fold cost), so the gate now only stops tables rewritten
-- several times an hour. Installs that never touched the default move with it.
ALTER TABLE rvbbit.accel_autopilot_config
    DROP CONSTRAINT IF EXISTS accel_autopilot_config_max_write_ratio_check;
ALTER TABLE rvbbit.accel_autopilot_config
    ADD CONSTRAINT accel_autopilot_config_max_write_ratio_check
        CHECK (max_write_ratio >= 0 AND max_write_ratio <= 100);
ALTER TABLE rvbbit.accel_autopilot_config
    ALTER COLUMN max_write_ratio SET DEFAULT 5.0;
UPDATE rvbbit.accel_autopilot_config
   SET max_write_ratio = 5.0, updated_at = clock_timestamp()
 WHERE max_write_ratio = 0.25;
COMMENT ON COLUMN rvbbit.accel_autopilot_config.max_write_ratio IS
    'Candidates whose writes per observation exceed this multiple of their row count are held. 5.0 keeps only tables rewritten several times an hour out; the payoff rule prices ordinary reload churn.';

-- ── the payoff, browsable: one row per observed table ────────────────────────
-- rvbbit.accel_payoff(oid) answers for one table; this view lays the arithmetic out for
-- every table the observer has scored, so the question "why is this table not
-- accelerated?" is a SELECT, not a function call.
CREATE OR REPLACE VIEW rvbbit.accel_payoffs AS
    SELECT c.table_name,
           c.status,
           c.hot,
           c.hot_streak,
           c.query_calls,
           c.users,
           round(c.attributed_ms)::bigint                          AS attributed_ms_lookback,
           (p->>'read_ms_per_day')::bigint                         AS read_ms_per_day,
           (p->>'fold_ms')::bigint                                 AS fold_ms,
           (p->>'folds_per_day')::numeric                          AS folds_per_day,
           (p->>'fold_ms_per_day')::bigint                         AS fold_ms_per_day,
           (p->>'multiplier')::numeric                             AS multiplier,
           (p->>'ok')::boolean                                     AS pays,
           round(c.write_ratio::numeric, 3)                        AS write_ratio,
           c.eligible,
           pol.strategy,
           pol.active                                              AS policy_active,
           pol.managed_by,
           pol.parked_at,
           c.last_evidence_at,
           c.last_observed_at
      FROM rvbbit.accel_autopilot_candidates c
      LEFT JOIN rvbbit.accel_policy pol ON pol.table_oid = c.table_oid
      CROSS JOIN LATERAL (SELECT rvbbit.accel_payoff(c.table_oid) AS p) x;
COMMENT ON VIEW rvbbit.accel_payoffs IS 'Per observed table: real read time per day against the projected cost of keeping its accelerator fresh, with the autopilot state. pays = the autopilot would promote on payoff.';

-- ── the observer may now say 'parked' ────────────────────────────────────────
ALTER TABLE rvbbit.accel_autopilot_candidates
    DROP CONSTRAINT IF EXISTS accel_autopilot_candidates_status_check;
ALTER TABLE rvbbit.accel_autopilot_candidates
    ADD CONSTRAINT accel_autopilot_candidates_status_check
        CHECK (status = ANY (ARRAY['observing', 'ready', 'held', 'managed', 'parked']));
