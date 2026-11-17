-- 0486: the autopilot accelerates what people read, and parks what nobody reads.
--
-- Until now the autopilot's evidence was the SQL Calliope's agents run
-- (rvbbit.mcp_activity), mapped to tables with EXPLAIN, and promotion went
-- through a streak, three volume thresholds and a payoff formula. Apps,
-- DataRabbit and psql were invisible, and the knobs disagreed with each other.
--
-- Now there is one kind of evidence: reads. pg_rvbbit counts every SELECT whose
-- rows go to a client (any role, any tool, Calliope's agents included; never
-- rvbbit's own server-side work; sessions with rvbbit.count_reads = off opt
-- out). rvbbit.accel_capture_read_evidence() copies the counter into
-- rvbbit.accel_read_evidence (last read per table) and rvbbit.accel_read_days
-- (one row per table per UTC day it was read).
--
-- The rules, all in rvbbit.accel_autopilot_evaluate():
--   ready     a plain permanent table between min_table_bytes and
--             max_table_bytes, with no policy set by hand, read on at least
--             act_promote_min_read_days of the last 7 days;
--   promote   (act mode) up to act_max_promotions_per_run ready tables an hour
--             get a scheduled policy owned by the autopilot. The freshness
--             worker builds them; nothing is built inside the act run;
--   park      (act mode) an autopilot-owned table nobody has read for
--             act_park_idle_days is set inactive (files kept, reads fall back to
--             the heap), at most act_max_parks_per_run an hour, and only once
--             reads have been counted for that long and something in the
--             database was read in the last day;
--   reactivate a parked table read again comes back, with the promotion cap.
-- Policies set by hand (managed_by IS NULL) are never touched, and neither are
-- tables someone disabled, rvbbit's own tables, pg_cron's and Hindsight's
-- schemas, or tables an extension owns.
--
-- Removed: the MCP observer and its shape/EXPLAIN machinery, the streak, the
-- payoff rule (accel_payoff, accel_payoff_ok, accel_payoffs), the write-ratio
-- gate, the park guards built on MCP traffic, accel_auto_reset (it took over
-- hand-set policies), the per-run observation history and the counters table,
-- and 16 config columns. Kept: mode, the size bounds, excluded_schemas,
-- retention_days, the per-run caps, act_park_idle_days, act_small_table_bytes
-- and the tick_* cadence settings.

-- ── dependents of what goes away ────────────────────────────────────────────
DROP VIEW IF EXISTS rvbbit.accel_payoffs;
DROP FUNCTION IF EXISTS rvbbit.accel_payoff_ok(oid);
DROP FUNCTION IF EXISTS rvbbit.accel_payoff(oid);
DROP FUNCTION IF EXISTS rvbbit.accel_auto_reset(boolean);
DROP VIEW IF EXISTS rvbbit.accel_autopilot_managed;

-- ── config ──────────────────────────────────────────────────────────────────
ALTER TABLE rvbbit.accel_autopilot_config
    ADD COLUMN IF NOT EXISTS act_promote_min_read_days integer NOT NULL DEFAULT 3
        CHECK (act_promote_min_read_days BETWEEN 1 AND 7);

ALTER TABLE rvbbit.accel_autopilot_config
    DROP COLUMN IF EXISTS lookback_hours,
    DROP COLUMN IF EXISTS max_query_shapes,
    DROP COLUMN IF EXISTS min_calls,
    DROP COLUMN IF EXISTS min_active_hours,
    DROP COLUMN IF EXISTS min_attributed_ms,
    DROP COLUMN IF EXISTS min_seq_scans_delta,
    DROP COLUMN IF EXISTS min_seq_rows_delta,
    DROP COLUMN IF EXISTS max_write_ratio,
    DROP COLUMN IF EXISTS act_promote_min_streak,
    DROP COLUMN IF EXISTS act_park_min_age_days,
    DROP COLUMN IF EXISTS act_park_min_daily_cost_ms,
    DROP COLUMN IF EXISTS act_park_min_world_calls,
    DROP COLUMN IF EXISTS act_max_parks_per_day,
    DROP COLUMN IF EXISTS evidence_exclude_callers,
    DROP COLUMN IF EXISTS act_payoff_multiplier,
    DROP COLUMN IF EXISTS act_payoff_fold_bytes_per_ms;

COMMENT ON TABLE rvbbit.accel_autopilot_config IS
    'The acceleration autopilot (one row). off = nothing; observe = classify tables by reads (rvbbit.accel_autopilot_candidates); act = also promote, park and reactivate (rvbbit.accel_autopilot_act). The tick_* columns pace the freshness tick in every mode.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_promote_min_read_days IS
    'A table is ready once it was read on this many of the last 7 days (UTC).';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.min_table_bytes IS
    'Smaller tables are not worth accelerating (pg_total_relation_size).';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.max_table_bytes IS
    'Larger tables are left to an operator.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_max_promotions_per_run IS
    'Promotions per act run, and separately reactivations per act run.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_park_idle_days IS
    'An autopilot-owned table nobody read for this many days is parked.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_max_parks_per_run IS
    'Parks per act run.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_small_table_bytes IS
    'Promoted tables below this size refresh at most every 15 minutes, larger ones hourly.';

-- ── evidence ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rvbbit.accel_read_days (
    table_oid oid  NOT NULL,
    day       date NOT NULL,
    PRIMARY KEY (table_oid, day)
);
COMMENT ON TABLE rvbbit.accel_read_days IS
    'One row per table per UTC day a client SELECT read it (from rvbbit.table_reads()). The autopilot promotes on it.';
COMMENT ON TABLE rvbbit.accel_read_evidence IS
    'Last time a client SELECT read each table (persisted from rvbbit.table_reads()). Sets the refresh cadence tier and the autopilot''s park/reactivate decisions.';

CREATE OR REPLACE FUNCTION rvbbit.accel_capture_read_evidence()
RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
DECLARE
    n integer;
    started timestamptz := clock_timestamp();
BEGIN
    INSERT INTO rvbbit.accel_read_evidence AS e (table_oid, last_read_at, recorded_at)
    SELECT r.table_oid, r.last_read_at, clock_timestamp()
      FROM rvbbit.table_reads() r
      JOIN pg_catalog.pg_class c ON c.oid = r.table_oid
     ORDER BY r.table_oid
    ON CONFLICT (table_oid) DO UPDATE
       SET last_read_at = EXCLUDED.last_read_at,
           recorded_at = EXCLUDED.recorded_at
     WHERE EXCLUDED.last_read_at > e.last_read_at;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO rvbbit.accel_read_days (table_oid, day)
    SELECT e.table_oid, (e.last_read_at AT TIME ZONE 'UTC')::date
      FROM rvbbit.accel_read_evidence e
     WHERE e.recorded_at >= started
     ORDER BY e.table_oid
    ON CONFLICT DO NOTHING;
    DELETE FROM rvbbit.accel_read_evidence e
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = e.table_oid);
    DELETE FROM rvbbit.accel_read_days d
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = d.table_oid);
    RETURN n;
END
$fn$;

-- ── what the old observer kept and nothing reads any more ───────────────────
DROP TABLE IF EXISTS rvbbit.accel_observer_observations;
DROP TABLE IF EXISTS rvbbit.accel_observer_counters;

ALTER TABLE rvbbit.accel_observer_runs
    DROP COLUMN IF EXISTS lookback_hours,
    DROP COLUMN IF EXISTS query_shapes_seen,
    DROP COLUMN IF EXISTS query_shapes_resolved;

ALTER TABLE rvbbit.accel_autopilot_candidates
    ADD COLUMN IF NOT EXISTS read_days integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_read_at timestamptz;

-- Steward learning items about a heap table are kept only while people read it.
CREATE OR REPLACE VIEW rvbbit.system_learning_select_items AS
SELECT uri, title, content_hash, occurred_at, body, props
  FROM rvbbit.system_learning_items i
 WHERE coalesce(props ->> 'object_type', '') <> 'heap_acceleration_candidate'
    OR EXISTS (SELECT 1 FROM rvbbit.accel_autopilot_candidates c
                WHERE c.table_name = (i.props ->> 'table') AND c.read_days > 0);

ALTER TABLE rvbbit.accel_autopilot_candidates
    DROP COLUMN IF EXISTS hot_observation_count,
    DROP COLUMN IF EXISTS hot_streak,
    DROP COLUMN IF EXISTS hot,
    DROP COLUMN IF EXISTS score,
    DROP COLUMN IF EXISTS query_shapes,
    DROP COLUMN IF EXISTS query_calls,
    DROP COLUMN IF EXISTS users,
    DROP COLUMN IF EXISTS active_hours,
    DROP COLUMN IF EXISTS inclusive_ms,
    DROP COLUMN IF EXISTS attributed_ms,
    DROP COLUMN IF EXISTS p95_ms,
    DROP COLUMN IF EXISTS seq_scans_total,
    DROP COLUMN IF EXISTS seq_scans_delta,
    DROP COLUMN IF EXISTS seq_rows_total,
    DROP COLUMN IF EXISTS seq_rows_delta,
    DROP COLUMN IF EXISTS writes_total,
    DROP COLUMN IF EXISTS writes_delta,
    DROP COLUMN IF EXISTS write_ratio,
    DROP COLUMN IF EXISTS last_evidence_at,
    DROP COLUMN IF EXISTS observation_count;

-- The old observer's rows describe evidence that no longer exists.
DELETE FROM rvbbit.accel_autopilot_candidates;

CREATE INDEX IF NOT EXISTS accel_autopilot_candidates_status_reads_idx
    ON rvbbit.accel_autopilot_candidates (status, read_days DESC);

COMMENT ON TABLE rvbbit.accel_autopilot_candidates IS
    'The autopilot''s latest classification of every table that was read or that it owns (written by rvbbit.accel_autopilot_observe). status: ready / observing / held / managed / parked; reasons says why.';

-- ── the rules ───────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_evaluate()
RETURNS TABLE (
    table_oid             oid,
    table_name            text,
    status                text,
    structurally_eligible boolean,
    eligible              boolean,
    reasons               text[],
    read_days             integer,
    last_read_at          timestamptz,
    row_estimate          bigint,
    table_bytes           bigint,
    registered            boolean,
    acceleration_enabled  boolean,
    row_groups            bigint,
    baseline_missing      boolean,
    policy_by_hand        boolean,
    autopilot_owned       boolean,
    policy_active         boolean,
    parked_at             timestamptz
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
WITH cfg AS (
    SELECT * FROM rvbbit.accel_autopilot_config WHERE singleton
), universe AS (
    SELECT ev.table_oid FROM rvbbit.accel_read_evidence ev
    UNION
    SELECT p.table_oid FROM rvbbit.accel_policy p WHERE p.managed_by = 'autopilot'
), facts AS (
    SELECT c.oid AS table_oid,
           c.oid::regclass::text AS table_name,
           (c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
            AND NOT c.relrowsecurity AND coalesce(am.amname, 'heap') IN ('heap', 'rvbbit')) AS plain,
           c.relispartition AS partition,
           c.relrowsecurity AS row_security,
           n.nspname = ANY (cfg.excluded_schemas) AS schema_excluded,
           coalesce((SELECT count(DISTINCT d.day)::int FROM rvbbit.accel_read_days d
                      WHERE d.table_oid = c.oid
                        AND d.day > (now() AT TIME ZONE 'UTC')::date - 7), 0) AS read_days,
           ev.last_read_at,
           greatest(c.reltuples, 0)::bigint AS row_estimate,
           pg_total_relation_size(c.oid) AS table_bytes,
           t.table_oid IS NOT NULL AS registered,
           coalesce(t.acceleration_enabled, false) AS acceleration_enabled,
           (SELECT count(*) FROM rvbbit.row_groups g WHERE g.table_oid = c.oid) AS row_groups,
           p.table_oid IS NOT NULL AND p.managed_by IS NULL AS policy_by_hand,
           coalesce(p.managed_by = 'autopilot', false) AS autopilot_owned,
           coalesce(p.active, false) AS policy_active,
           p.parked_at,
           cfg.min_table_bytes, cfg.max_table_bytes, cfg.act_promote_min_read_days
      FROM universe u
      JOIN pg_catalog.pg_class c ON c.oid = u.table_oid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN pg_catalog.pg_am am ON am.oid = c.relam
      CROSS JOIN cfg
      LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = c.oid
      LEFT JOIN rvbbit.tables t ON t.table_oid = c.oid
      LEFT JOIN rvbbit.accel_policy p ON p.table_oid = c.oid
     -- Never rvbbit's own bookkeeping, the bundled services' (pg_cron, Hindsight
     -- memory) or tables an extension owns: they are read all day by machinery.
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'rvbbit', 'cron', 'hindsight')
       AND n.nspname NOT LIKE 'pg\_toast%'
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend dep
                        WHERE dep.classid = 'pg_catalog.pg_class'::regclass
                          AND dep.objid = c.oid AND dep.deptype = 'e')
), judged AS (
    SELECT f.*,
           f.plain AND NOT f.schema_excluded
             AND f.table_bytes BETWEEN f.min_table_bytes AND f.max_table_bytes AS fits,
           f.registered AND NOT f.acceleration_enabled AS disabled_by_hand
      FROM facts f
)
SELECT j.table_oid,
       j.table_name,
       CASE
           WHEN j.autopilot_owned AND NOT j.policy_active THEN 'parked'
           WHEN j.autopilot_owned THEN 'managed'
           WHEN j.policy_by_hand AND j.acceleration_enabled AND j.row_groups > 0 THEN 'managed'
           WHEN j.policy_by_hand OR j.disabled_by_hand OR NOT j.fits THEN 'held'
           WHEN j.read_days >= j.act_promote_min_read_days THEN 'ready'
           ELSE 'observing'
       END,
       j.plain,
       j.fits AND NOT j.disabled_by_hand AND NOT j.policy_by_hand,
       array_remove(ARRAY[
           format('read on %s of the last 7 days (ready at %s)', j.read_days, j.act_promote_min_read_days),
           CASE WHEN j.autopilot_owned AND NOT j.policy_active THEN 'parked by the autopilot; reading it brings it back'
                WHEN j.autopilot_owned THEN 'promoted by the autopilot' END,
           CASE WHEN j.policy_by_hand THEN 'policy set by hand' END,
           CASE WHEN j.disabled_by_hand THEN 'acceleration disabled by hand' END,
           CASE WHEN j.partition THEN 'a partition (accelerate the parent by hand)'
                WHEN j.row_security THEN 'row-level security'
                WHEN NOT j.plain THEN 'not a plain permanent table' END,
           CASE WHEN j.schema_excluded THEN 'schema excluded' END,
           CASE WHEN j.plain AND j.table_bytes < j.min_table_bytes
                THEN format('smaller than %s', pg_size_pretty(j.min_table_bytes)) END,
           CASE WHEN j.plain AND j.table_bytes > j.max_table_bytes
                THEN format('larger than %s', pg_size_pretty(j.max_table_bytes)) END
       ], NULL),
       j.read_days,
       j.last_read_at,
       j.row_estimate,
       j.table_bytes,
       j.registered,
       j.acceleration_enabled,
       j.row_groups,
       j.acceleration_enabled AND j.row_groups = 0 AND j.row_estimate > 0,
       j.policy_by_hand,
       j.autopilot_owned,
       j.policy_active,
       j.parked_at
  FROM judged j
$fn$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_evaluate() IS
    'Classify every table that was read or that the autopilot owns: ready (read on act_promote_min_read_days of the last 7 days, fits the size bounds, no policy set by hand), observing, held, managed or parked, with reasons. Read-only.';

-- ── observe: persist the classification ─────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_observe(p_source text DEFAULT 'manual'::text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
DECLARE
    cfg       rvbbit.accel_autopilot_config%ROWTYPE;
    v_run_id  bigint;
    v_tables  bigint := 0;
    v_ready   bigint := 0;
    v_counter boolean := to_regprocedure('rvbbit.table_reads()') IS NOT NULL;
    v_message text;
    v_error   text;
BEGIN
    SELECT * INTO cfg FROM rvbbit.accel_autopilot_config WHERE singleton;
    IF NOT FOUND THEN
        INSERT INTO rvbbit.accel_autopilot_config (singleton) VALUES (true) RETURNING * INTO cfg;
    END IF;

    IF NOT pg_try_advisory_xact_lock(1381187156, 9) THEN
        RETURN jsonb_build_object('status', 'busy', 'mode', cfg.mode,
                                  'message', 'another autopilot observe run is active');
    END IF;

    INSERT INTO rvbbit.accel_observer_runs (status, source, config_snapshot)
    VALUES (CASE WHEN cfg.mode = 'off' THEN 'skipped' ELSE 'running' END,
            coalesce(nullif(btrim(p_source), ''), 'manual'),
            to_jsonb(cfg) - 'singleton')
    RETURNING run_id INTO v_run_id;

    IF cfg.mode = 'off' THEN
        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(), message = 'autopilot mode is off'
         WHERE run_id = v_run_id;
        RETURN jsonb_build_object('status', 'skipped', 'mode', cfg.mode, 'run_id', v_run_id,
                                  'message', 'autopilot mode is off');
    END IF;

    BEGIN
        IF v_counter THEN
            PERFORM rvbbit.accel_capture_read_evidence();
        END IF;

        INSERT INTO rvbbit.accel_autopilot_candidates AS c (
            table_oid, table_name, last_observed_at, status, structurally_eligible, eligible,
            reasons, read_days, last_read_at, row_estimate, table_bytes, registered,
            acceleration_enabled, row_groups, baseline_missing, last_run_id)
        SELECT e.table_oid, e.table_name, clock_timestamp(), e.status, e.structurally_eligible,
               e.eligible, e.reasons, e.read_days, e.last_read_at, e.row_estimate, e.table_bytes,
               e.registered, e.acceleration_enabled, e.row_groups, e.baseline_missing, v_run_id
          FROM rvbbit.accel_autopilot_evaluate() e
        ON CONFLICT (table_oid) DO UPDATE SET
            table_name = EXCLUDED.table_name,
            last_observed_at = EXCLUDED.last_observed_at,
            status = EXCLUDED.status,
            structurally_eligible = EXCLUDED.structurally_eligible,
            eligible = EXCLUDED.eligible,
            reasons = EXCLUDED.reasons,
            read_days = EXCLUDED.read_days,
            last_read_at = EXCLUDED.last_read_at,
            row_estimate = EXCLUDED.row_estimate,
            table_bytes = EXCLUDED.table_bytes,
            registered = EXCLUDED.registered,
            acceleration_enabled = EXCLUDED.acceleration_enabled,
            row_groups = EXCLUDED.row_groups,
            baseline_missing = EXCLUDED.baseline_missing,
            last_run_id = EXCLUDED.last_run_id;
        GET DIAGNOSTICS v_tables = ROW_COUNT;

        -- Tables that were dropped, aged out of the evidence or released.
        DELETE FROM rvbbit.accel_autopilot_candidates c
         WHERE c.last_run_id IS DISTINCT FROM v_run_id;

        DELETE FROM rvbbit.accel_read_days
         WHERE day < (now() AT TIME ZONE 'UTC')::date - cfg.retention_days;
        DELETE FROM rvbbit.accel_read_evidence
         WHERE last_read_at < now() - make_interval(days => cfg.retention_days);
        DELETE FROM rvbbit.accel_observer_runs
         WHERE started_at < now() - make_interval(days => cfg.retention_days);

        SELECT count(*) INTO v_ready FROM rvbbit.accel_autopilot_candidates WHERE status = 'ready';
        v_message := CASE
            WHEN NOT v_counter THEN 'the pg_rvbbit library predates read counting; no new evidence'
            WHEN cfg.mode = 'act' THEN format('%s tables classified, %s ready; the act run promotes them', v_tables, v_ready)
            ELSE format('%s tables classified, %s ready; observe mode changes nothing', v_tables, v_ready)
        END;

        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(), status = 'ok',
               tables_observed = v_tables, ready_count = v_ready, message = v_message
         WHERE run_id = v_run_id;

        RETURN jsonb_build_object(
            'status', 'ok', 'mode', cfg.mode, 'run_id', v_run_id,
            'tables_observed', v_tables, 'ready', v_ready,
            'read_counter', v_counter, 'message', v_message);
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_error = MESSAGE_TEXT;
        UPDATE rvbbit.accel_observer_runs
           SET finished_at = clock_timestamp(), status = 'failed', error = v_error
         WHERE run_id = v_run_id;
        RETURN jsonb_build_object('status', 'failed', 'mode', cfg.mode, 'run_id', v_run_id,
                                  'error', v_error);
    END;
END
$fn$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_observe(text) IS
    'Capture read evidence and record the autopilot''s classification in rvbbit.accel_autopilot_candidates. Never changes a table or a policy.';

-- ── act: promote, reactivate, park ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_act(p_source text DEFAULT 'manual'::text, p_dry_run boolean DEFAULT false)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
DECLARE
    cfg            rvbbit.accel_autopilot_config%ROWTYPE;
    sweep_id       bigint;
    started        timestamptz := clock_timestamp();
    cand           record;
    promoted       jsonb := '[]'::jsonb;
    parked         jsonb := '[]'::jsonb;
    reactivated    jsonb := '[]'::jsonb;
    failures       jsonb := '[]'::jsonb;
    park_guard     jsonb;
    counting_since date;
    read_today     boolean;
    may_park       boolean;
    t0             timestamptz;
    policy_result  jsonb;
    interval_secs  integer;
    note_text      text;
    err            text;
BEGIN
    SELECT * INTO cfg FROM rvbbit.accel_autopilot_config WHERE singleton;
    IF NOT FOUND OR cfg.mode <> 'act' THEN
        RETURN jsonb_build_object('mode', coalesce(cfg.mode, 'missing'), 'acted', false,
                                  'reason', 'autopilot mode is not ''act''', 'source', p_source);
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtext('rvbbit.accel_autopilot_act')) THEN
        RETURN jsonb_build_object('mode', cfg.mode, 'acted', false,
                                  'reason', 'another autopilot run holds the lock', 'source', p_source);
    END IF;

    IF to_regprocedure('rvbbit.table_reads()') IS NOT NULL THEN
        PERFORM rvbbit.accel_capture_read_evidence();
    END IF;
    CREATE TEMP TABLE IF NOT EXISTS rvbbit_autopilot_eval ON COMMIT DROP AS
        SELECT * FROM rvbbit.accel_autopilot_evaluate() WITH NO DATA;
    TRUNCATE pg_temp.rvbbit_autopilot_eval;
    INSERT INTO pg_temp.rvbbit_autopilot_eval SELECT * FROM rvbbit.accel_autopilot_evaluate();

    PERFORM set_config('rvbbit.accel_autopilot_actor', 'on', true);
    sweep_id := nextval('rvbbit.accel_sweep_id_seq');
    PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'sweep_started',
        p_details => jsonb_build_object('source', p_source, 'dry_run', p_dry_run));

    ------------------------------------------------------------------ promote
    FOR cand IN
        SELECT e.* FROM pg_temp.rvbbit_autopilot_eval e
         WHERE e.status = 'ready'
         ORDER BY e.read_days DESC, e.last_read_at DESC NULLS LAST, e.table_bytes
         LIMIT cfg.act_max_promotions_per_run
    LOOP
        t0 := clock_timestamp();
        interval_secs := CASE WHEN cand.table_bytes < cfg.act_small_table_bytes THEN 900 ELSE 3600 END;
        note_text := format('autopilot: promoted %s (read on %s of the last 7 days)',
                            to_char(now(), 'YYYY-MM-DD'), cand.read_days);
        BEGIN
            IF NOT p_dry_run THEN
                IF NOT cand.registered THEN
                    PERFORM rvbbit.enable_table(cand.table_oid::regclass, true);
                END IF;
                -- No build here: the freshness worker builds a table that has a policy and no files.
                policy_result := rvbbit.set_accel_policy(
                    cand.table_oid::regclass,
                    strategy => 'scheduled',
                    freshness_target_secs => NULL,
                    min_interval_secs => interval_secs,
                    daily_refresh_budget => NULL,
                    full_rebuild_drift_ratio => 0.5,
                    lance_separate => true,
                    active => true,
                    note => note_text);
                UPDATE rvbbit.accel_policy
                   SET managed_by = 'autopilot', managed_since = clock_timestamp(), parked_at = NULL
                 WHERE table_oid = cand.table_oid;
                UPDATE rvbbit.accel_autopilot_candidates
                   SET status = 'managed', reasons = array_append(reasons, 'promoted by the autopilot')
                 WHERE table_oid = cand.table_oid;
            END IF;
            promoted := promoted || jsonb_build_object(
                'table', cand.table_name, 'read_days', cand.read_days,
                'last_read_at', cand.last_read_at, 'min_interval_secs', interval_secs);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                cand.table_oid, cand.table_name, 'scheduled', 'promote', note_text,
                CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                p_details => jsonb_build_object('read_days', cand.read_days, 'table_bytes', cand.table_bytes,
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
    FOR cand IN
        SELECT e.* FROM pg_temp.rvbbit_autopilot_eval e
         WHERE e.status = 'parked'
           AND e.structurally_eligible
           AND e.table_bytes BETWEEN cfg.min_table_bytes AND cfg.max_table_bytes
           AND e.last_read_at > coalesce(e.parked_at, '-infinity'::timestamptz)
         ORDER BY e.last_read_at DESC
         LIMIT cfg.act_max_promotions_per_run
    LOOP
        t0 := clock_timestamp();
        note_text := format('autopilot: reactivated %s (read again at %s)',
                            to_char(now(), 'YYYY-MM-DD'), to_char(cand.last_read_at, 'YYYY-MM-DD HH24:MI'));
        BEGIN
            IF NOT p_dry_run THEN
                UPDATE rvbbit.accel_policy
                   SET active = true, parked_at = NULL, managed_since = clock_timestamp(),
                       note = note_text, updated_at = clock_timestamp()
                 WHERE table_oid = cand.table_oid;
                UPDATE rvbbit.accel_autopilot_candidates
                   SET status = 'managed'
                 WHERE table_oid = cand.table_oid;
            END IF;
            reactivated := reactivated || jsonb_build_object(
                'table', cand.table_name, 'last_read_at', cand.last_read_at);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                cand.table_oid, cand.table_name, 'scheduled', 'reactivate', note_text,
                CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0));
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
            failures := failures || jsonb_build_object('table', cand.table_name, 'action', 'reactivate', 'error', err);
        END;
    END LOOP;

    --------------------------------------------------------------------- park
    -- Only once reads have been counted for the whole idle window, and while
    -- the database is being read at all (a stopped counter or an idle estate
    -- parks nothing).
    SELECT min(day) INTO counting_since FROM rvbbit.accel_read_days;
    SELECT EXISTS (SELECT 1 FROM rvbbit.accel_read_evidence WHERE last_read_at > now() - interval '1 day')
      INTO read_today;
    may_park := read_today
        AND counting_since IS NOT NULL
        AND counting_since <= (now() AT TIME ZONE 'UTC')::date - cfg.act_park_idle_days;
    park_guard := jsonb_build_object('reads_counted_since', counting_since,
                                     'read_in_last_day', read_today, 'may_park', may_park);

    IF may_park THEN
        FOR cand IN
            SELECT e.*, p.managed_since FROM pg_temp.rvbbit_autopilot_eval e
              JOIN rvbbit.accel_policy p ON p.table_oid = e.table_oid
             WHERE e.status = 'managed'
               AND e.autopilot_owned
               AND coalesce(e.last_read_at, '-infinity'::timestamptz) < now() - make_interval(days => cfg.act_park_idle_days)
               AND coalesce(p.managed_since, '-infinity'::timestamptz) < now() - make_interval(days => cfg.act_park_idle_days)
             ORDER BY e.last_read_at NULLS FIRST, e.table_bytes DESC
             LIMIT cfg.act_max_parks_per_run
        LOOP
            t0 := clock_timestamp();
            note_text := format('autopilot: parked %s (not read for %s days; files kept)',
                                to_char(now(), 'YYYY-MM-DD'), cfg.act_park_idle_days);
            BEGIN
                IF NOT p_dry_run THEN
                    UPDATE rvbbit.accel_policy
                       SET active = false, parked_at = clock_timestamp(),
                           note = note_text, updated_at = clock_timestamp()
                     WHERE table_oid = cand.table_oid;
                    UPDATE rvbbit.accel_autopilot_candidates
                       SET status = 'parked'
                     WHERE table_oid = cand.table_oid;
                END IF;
                parked := parked || jsonb_build_object('table', cand.table_name, 'last_read_at', cand.last_read_at);
                PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                    cand.table_oid, cand.table_name, 'scheduled', 'park', note_text,
                    CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                    p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                    p_details => jsonb_build_object('last_read_at', cand.last_read_at) || park_guard);
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
                failures := failures || jsonb_build_object('table', cand.table_name, 'action', 'park', 'error', err);
            END;
        END LOOP;
    END IF;

    -- Release the actor marker before returning so a later statement in the
    -- same transaction (an operator's set_accel_policy) counts as by hand.
    PERFORM set_config('rvbbit.accel_autopilot_actor', 'off', true);
    PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'sweep_finished',
        p_status => CASE WHEN jsonb_array_length(failures) > 0 THEN 'partial' ELSE 'ok' END,
        p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - started) * 1000.0),
        p_details => jsonb_build_object('source', p_source, 'dry_run', p_dry_run,
            'promoted', jsonb_array_length(promoted), 'parked', jsonb_array_length(parked),
            'reactivated', jsonb_array_length(reactivated),
            'failures', jsonb_array_length(failures), 'park_guard', park_guard));

    RETURN jsonb_build_object(
        'mode', cfg.mode, 'acted', true, 'dry_run', p_dry_run, 'source', p_source,
        'sweep_id', sweep_id,
        'promoted', promoted, 'parked', parked, 'reactivated', reactivated,
        'failures', failures, 'park_guard', park_guard,
        'elapsed_ms', extract(epoch FROM clock_timestamp() - started) * 1000.0);
END;
$fn$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_act(text, boolean) IS
    'In act mode: promote ready tables (a scheduled policy owned by the autopilot; the freshness worker builds them), reactivate parked tables read again, park autopilot-owned tables nobody read for act_park_idle_days. Never touches policies set by hand.';

-- ── status ──────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_status()
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
    SELECT jsonb_build_object(
        'installed', true,
        'mode', cfg.mode,
        'evidence', 'reads',
        'read_counter_available', to_regprocedure('rvbbit.table_reads()') IS NOT NULL,
        'reads_counted_since', (SELECT min(day) FROM rvbbit.accel_read_days),
        'read_window_days', 7,
        'config', to_jsonb(cfg) - 'singleton',
        'counts', (
            SELECT jsonb_build_object(
                'total', count(*),
                'ready', count(*) FILTER (WHERE c.status = 'ready'),
                'observing', count(*) FILTER (WHERE c.status = 'observing'),
                'held', count(*) FILTER (WHERE c.status = 'held'),
                'managed', count(*) FILTER (WHERE c.status = 'managed'),
                'parked', count(*) FILTER (WHERE c.status = 'parked'))
              FROM rvbbit.accel_autopilot_candidates c),
        'latest_run', (
            SELECT to_jsonb(r) FROM rvbbit.accel_observer_runs r
             ORDER BY r.started_at DESC LIMIT 1),
        'latest_act', (
            SELECT jsonb_build_object('at', a.event_at, 'status', a.status) || a.details
              FROM rvbbit.accel_activity_log a
             WHERE a.lane = 'autopilot' AND a.event_type = 'sweep_finished'
             ORDER BY a.event_at DESC LIMIT 1))
      FROM rvbbit.accel_autopilot_config cfg
     WHERE cfg.singleton;
$fn$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_status() IS
    'The autopilot at a glance: mode, whether reads are being counted and since when, config, candidate counts by status, the latest observe run and the latest act run.';

-- ── tables the autopilot owns ───────────────────────────────────────────────
CREATE VIEW rvbbit.accel_autopilot_managed AS
SELECT p.table_oid,
       p.table_oid::regclass::text                       AS table_name,
       p.active,
       p.parked_at IS NOT NULL AND NOT p.active          AS parked,
       p.managed_since,
       p.parked_at,
       p.min_interval_secs,
       p.note,
       rvbbit.accel_daily_refresh_cost_ms(p.table_oid)   AS daily_refresh_cost_ms,
       c.status                                          AS observer_status,
       c.read_days,
       ev.last_read_at
  FROM rvbbit.accel_policy p
  LEFT JOIN rvbbit.accel_autopilot_candidates c ON c.table_oid = p.table_oid
  LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = p.table_oid
 WHERE p.managed_by = 'autopilot';

COMMENT ON VIEW rvbbit.accel_autopilot_managed IS
    'Tables whose acceleration policy the autopilot owns, with parked state, refresh cost and read evidence.';

-- ── discovery text for the steward's snapshot tool (generated from its docstring in 0438) ──
DO $$ BEGIN
    IF to_regclass('rvbbit.capability_mcp_tools') IS NOT NULL THEN
        UPDATE rvbbit.capability_mcp_tools
           SET summary = 'Return the bounded canonical packet for acceleration judgment. This refreshes the autopilot''s read-based classification (which tables clients read, on how many of the last 7 days), then combines its candidates with mirror load mode/cadence, freshness, policy, runtime cost, recent operations, derived-layout recommendations, open trials, and prior receipts. It never enables or rebuilds a table.'
         WHERE name = 'acceleration_steward_snapshot';
    END IF;
END $$;
