-- 0493: the autopilot releases a table whose data is rewritten faster than an accelerated copy pays off.
--
-- An accelerated copy pays off while it stays usable between refreshes. When an
-- ETL rewrites a table wholesale every few minutes (DELETE everything and INSERT
-- it again, a queue that fills and empties, a one-row cursor), every refresh is
-- a rebuild from scratch, the copy is stale again minutes later and reads go
-- back to the heap anyway: the work buys nothing. On the hub, 12 such tables
-- cost ~1,200 full rebuilds a day (a queue that is always empty took two hours
-- of rebuild time a day on its own).
--
-- New rule, in act mode: an autopilot-owned table whose copy was rebuilt from
-- scratch at least act_churn_rebuilds_per_day times in the last 24 hours is
-- released:
--   * its files are retired (rvbbit.retire_acceleration: grace-period reaping);
--   * acceleration is turned off (rvbbit.disable_table: the ETL's writes no
--     longer fire dirty triggers, and reads plan against the plain table and its
--     indexes);
--   * its policy stays the autopilot's, inactive, with park_reason 'churn', and
--     the table shows as 'released'.
-- If the files can't be retired (a compacted heap, a Lance dataset, cold
-- files), it only stops refreshing, and the note says why. Reads don't bring a
-- released table back: after act_churn_retry_days, if clients still read it on
-- act_promote_min_read_days of the last 7 days, the autopilot turns it on again
-- and the freshness worker rebuilds it. If the data is still rewritten that
-- fast, it is released again within hours. act_churn_rebuilds_per_day = 0 turns
-- the rule off.
--
-- Policies set by hand are still never touched. The candidates' reasons now say
-- when a hand-accelerated table is rebuilt from scratch that often, or is
-- smaller than min_table_bytes, so an operator can see it.

-- ── config and policy state ─────────────────────────────────────────────────
ALTER TABLE rvbbit.accel_autopilot_config
    ADD COLUMN IF NOT EXISTS act_churn_rebuilds_per_day integer NOT NULL DEFAULT 12
        CHECK (act_churn_rebuilds_per_day >= 0),
    ADD COLUMN IF NOT EXISTS act_churn_retry_days integer NOT NULL DEFAULT 7
        CHECK (act_churn_retry_days >= 1);

COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_churn_rebuilds_per_day IS
    'An autopilot-owned table whose copy was rebuilt from scratch this many times in the last 24 hours is released (files retired, acceleration off). 0 = never.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.act_churn_retry_days IS
    'A released table is turned on again after this many days, if clients still read it often enough to promote.';

ALTER TABLE rvbbit.accel_policy
    ADD COLUMN IF NOT EXISTS park_reason text
        CHECK (park_reason IN ('idle', 'churn'));

COMMENT ON COLUMN rvbbit.accel_policy.park_reason IS
    'Why the autopilot set its policy inactive: idle (nobody read the table; a read brings it back) or churn (rebuilt from scratch too often; released until act_churn_retry_days).';

UPDATE rvbbit.accel_policy
   SET park_reason = 'idle'
 WHERE managed_by = 'autopilot' AND parked_at IS NOT NULL AND NOT active AND park_reason IS NULL;

ALTER TABLE rvbbit.accel_autopilot_candidates
    DROP CONSTRAINT IF EXISTS accel_autopilot_candidates_status_check;
ALTER TABLE rvbbit.accel_autopilot_candidates
    ADD CONSTRAINT accel_autopilot_candidates_status_check
        CHECK (status = ANY (ARRAY['observing', 'ready', 'held', 'managed', 'parked', 'released']));

-- An operator's edit releases a table from autopilot management (0326); it now
-- also forgets why the autopilot had parked it.
CREATE OR REPLACE FUNCTION rvbbit.accel_policy_autopilot_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_setting('rvbbit.accel_autopilot_actor', true) IS DISTINCT FROM 'on'
       AND OLD.managed_by IS NOT NULL
       AND (
           NEW.strategy IS DISTINCT FROM OLD.strategy
           OR NEW.active IS DISTINCT FROM OLD.active
           OR NEW.min_interval_secs IS DISTINCT FROM OLD.min_interval_secs
           OR NEW.freshness_target_secs IS DISTINCT FROM OLD.freshness_target_secs
           OR NEW.daily_refresh_budget IS DISTINCT FROM OLD.daily_refresh_budget
           OR NEW.note IS DISTINCT FROM OLD.note
       ) THEN
        NEW.managed_by := NULL;
        NEW.managed_since := NULL;
        NEW.parked_at := NULL;
        NEW.park_reason := NULL;
    END IF;
    RETURN NEW;
END;
$$;

-- ── evidence ────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.accel_rebuilds_last_day(p_table_oid oid)
RETURNS integer
LANGUAGE sql
STABLE
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
    SELECT count(*)::integer
      FROM rvbbit.acceleration_operations o
     WHERE o.table_oid = p_table_oid
       AND o.operation = 'rebuild_acceleration'
       AND o.status = 'ok'
       AND o.started_at > now() - interval '1 day'
$fn$;

COMMENT ON FUNCTION rvbbit.accel_rebuilds_last_day(oid) IS
    'How many times the table''s accelerated copy was rebuilt from scratch in the last 24 hours: the autopilot''s churn evidence.';

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
           p.park_reason,
           CASE WHEN p.table_oid IS NOT NULL THEN rvbbit.accel_rebuilds_last_day(c.oid) ELSE 0 END
               AS rebuilds_last_day,
           cfg.min_table_bytes, cfg.max_table_bytes, cfg.act_promote_min_read_days,
           cfg.act_churn_rebuilds_per_day, cfg.act_churn_retry_days
      FROM universe u
      JOIN pg_catalog.pg_class c ON c.oid = u.table_oid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN pg_catalog.pg_am am ON am.oid = c.relam
      CROSS JOIN cfg
      LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = c.oid
      LEFT JOIN rvbbit.tables t ON t.table_oid = c.oid
      LEFT JOIN rvbbit.accel_policy p ON p.table_oid = c.oid
     -- Never rvbbit's own bookkeeping, the bundled services' (pg_cron, Hindsight
     -- memory, Clover's ledger and research runs) or tables an extension owns:
     -- they are read all day by machinery.
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'rvbbit', 'cron', 'hindsight',
                             'clover_telemetry', 'clover_research')
       AND n.nspname NOT LIKE 'pg\_toast%'
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend dep
                        WHERE dep.classid = 'pg_catalog.pg_class'::regclass
                          AND dep.objid = c.oid AND dep.deptype = 'e')
), judged AS (
    SELECT f.*,
           f.plain AND NOT f.schema_excluded
             AND f.table_bytes BETWEEN f.min_table_bytes AND f.max_table_bytes AS fits,
           -- A table the autopilot released has acceleration off, but not by hand.
           f.registered AND NOT f.acceleration_enabled AND NOT f.autopilot_owned AS disabled_by_hand,
           f.autopilot_owned OR (f.policy_by_hand AND f.acceleration_enabled AND f.row_groups > 0) AS accelerated,
           f.act_churn_rebuilds_per_day > 0
             AND f.rebuilds_last_day >= f.act_churn_rebuilds_per_day AS churning
      FROM facts f
)
SELECT j.table_oid,
       j.table_name,
       CASE
           WHEN j.autopilot_owned AND NOT j.policy_active AND j.park_reason = 'churn' THEN 'released'
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
           CASE WHEN j.autopilot_owned AND NOT j.policy_active AND j.park_reason = 'churn'
                THEN format('released by the autopilot: rebuilt from scratch too often to pay off; tries again from %s',
                            to_char(j.parked_at + make_interval(days => j.act_churn_retry_days), 'YYYY-MM-DD'))
                WHEN j.autopilot_owned AND NOT j.policy_active THEN 'parked by the autopilot; reading it brings it back'
                WHEN j.autopilot_owned THEN 'promoted by the autopilot' END,
           CASE WHEN j.churning AND j.autopilot_owned AND j.policy_active
                THEN format('rebuilt from scratch %s times in the last day; act mode releases it', j.rebuilds_last_day)
                WHEN j.churning AND j.policy_by_hand AND j.accelerated
                THEN format('rebuilt from scratch %s times in the last day: probably rewritten faster than a copy pays off',
                            j.rebuilds_last_day) END,
           CASE WHEN j.policy_by_hand THEN 'policy set by hand' END,
           CASE WHEN j.disabled_by_hand THEN 'acceleration disabled by hand' END,
           CASE WHEN j.accelerated THEN NULL
                WHEN j.partition THEN 'a partition (accelerate the parent by hand)'
                WHEN j.row_security THEN 'row-level security'
                WHEN NOT j.plain THEN 'not a plain permanent table' END,
           CASE WHEN j.schema_excluded AND NOT j.accelerated THEN 'schema excluded' END,
           CASE WHEN j.plain AND NOT j.accelerated AND j.table_bytes < j.min_table_bytes
                THEN format('smaller than %s', pg_size_pretty(j.min_table_bytes))
                WHEN j.plain AND j.policy_by_hand AND j.accelerated AND j.table_bytes < j.min_table_bytes
                THEN format('smaller than %s, where the plain table is as fast', pg_size_pretty(j.min_table_bytes)) END,
           CASE WHEN j.plain AND NOT j.accelerated AND j.table_bytes > j.max_table_bytes
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
    'Classify every table that was read or that the autopilot owns: ready (read on act_promote_min_read_days of the last 7 days, fits the size bounds, no policy set by hand), observing, held, managed, parked (nobody read it) or released (rebuilt from scratch too often), with reasons. Read-only.';

-- ── act: promote, reactivate, park, release ─────────────────────────────────
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
    released       jsonb := '[]'::jsonb;
    failures       jsonb := '[]'::jsonb;
    park_guard     jsonb;
    counting_since date;
    read_today     boolean;
    may_park       boolean;
    t0             timestamptz;
    policy_result  jsonb;
    retire_result  jsonb;
    files_kept     text;
    lock_timeout_before text;
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
                   SET managed_by = 'autopilot', managed_since = clock_timestamp(),
                       parked_at = NULL, park_reason = NULL
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
    -- A parked table comes back when someone reads it; a released one only
    -- after act_churn_retry_days, and only if it is still read often enough to
    -- promote. Both share the promotion cap.
    FOR cand IN
        SELECT e.* FROM pg_temp.rvbbit_autopilot_eval e
         WHERE e.structurally_eligible
           AND e.table_bytes BETWEEN cfg.min_table_bytes AND cfg.max_table_bytes
           AND ((e.status = 'parked'
                 AND e.last_read_at > coalesce(e.parked_at, '-infinity'::timestamptz))
             OR (e.status = 'released'
                 AND e.parked_at < now() - make_interval(days => cfg.act_churn_retry_days)
                 AND e.read_days >= cfg.act_promote_min_read_days))
         ORDER BY e.last_read_at DESC NULLS LAST
         LIMIT cfg.act_max_promotions_per_run
    LOOP
        t0 := clock_timestamp();
        note_text := CASE
            WHEN cand.status = 'released'
                THEN format('autopilot: trying again %s (released %s; read on %s of the last 7 days)',
                            to_char(now(), 'YYYY-MM-DD'), to_char(cand.parked_at, 'YYYY-MM-DD'), cand.read_days)
            ELSE format('autopilot: reactivated %s (read again at %s)',
                        to_char(now(), 'YYYY-MM-DD'), to_char(cand.last_read_at, 'YYYY-MM-DD HH24:MI'))
        END;
        BEGIN
            IF NOT p_dry_run THEN
                IF NOT cand.acceleration_enabled THEN
                    -- Released: turn acceleration back on; the freshness worker rebuilds it.
                    PERFORM rvbbit.enable_table(cand.table_oid::regclass, true);
                END IF;
                UPDATE rvbbit.accel_policy
                   SET strategy = 'scheduled', active = true, parked_at = NULL, park_reason = NULL,
                       managed_since = clock_timestamp(), note = note_text, updated_at = clock_timestamp()
                 WHERE table_oid = cand.table_oid;
                UPDATE rvbbit.accel_autopilot_candidates
                   SET status = 'managed'
                 WHERE table_oid = cand.table_oid;
            END IF;
            reactivated := reactivated || jsonb_build_object(
                'table', cand.table_name, 'last_read_at', cand.last_read_at, 'was', cand.status);
            PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                cand.table_oid, cand.table_name, 'scheduled', 'reactivate', note_text,
                CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                p_details => jsonb_build_object('was', cand.status));
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
                       SET active = false, parked_at = clock_timestamp(), park_reason = 'idle',
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

    ------------------------------------------------------------------ release
    -- A copy rebuilt from scratch this often is stale again minutes after each
    -- rebuild: retire its files and turn acceleration off. Releasing only ever
    -- returns a table to the plain heap, so it is not capped per run. Dropping
    -- the dirty triggers needs a brief exclusive lock on the table; a table busy
    -- with other work is skipped and released on a later run.
    IF cfg.act_churn_rebuilds_per_day > 0 THEN
        lock_timeout_before := current_setting('lock_timeout');
        PERFORM set_config('lock_timeout', '5s', true);
        FOR cand IN
            SELECT e.*, rvbbit.accel_rebuilds_last_day(e.table_oid) AS rebuilds
              FROM pg_temp.rvbbit_autopilot_eval e
             WHERE e.status = 'managed'
               AND e.autopilot_owned
               AND e.policy_active
               AND rvbbit.accel_rebuilds_last_day(e.table_oid) >= cfg.act_churn_rebuilds_per_day
             ORDER BY rvbbit.accel_rebuilds_last_day(e.table_oid) DESC, e.table_bytes DESC
        LOOP
            t0 := clock_timestamp();
            retire_result := NULL;
            files_kept := NULL;
            BEGIN
                IF NOT p_dry_run THEN
                    BEGIN
                        retire_result := rvbbit.retire_acceleration(cand.table_oid::regclass);
                        IF retire_result ->> 'status' = 'busy' THEN
                            RAISE EXCEPTION USING ERRCODE = 'lock_not_available',
                                MESSAGE = 'table maintenance is running; released on a later run';
                        END IF;
                        PERFORM rvbbit.disable_table(cand.table_oid::regclass, false);
                    EXCEPTION
                        WHEN lock_not_available THEN
                            RAISE;
                        WHEN OTHERS THEN
                            -- The files can't be retired here (a compacted heap, a
                            -- Lance dataset, cold files): stop refreshing it anyway.
                            GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
                            retire_result := NULL;
                            files_kept := err;
                    END;
                END IF;
                note_text := format('autopilot: released %s (rebuilt from scratch %s times in a day%s; tries again from %s)',
                                    to_char(now(), 'YYYY-MM-DD'), cand.rebuilds,
                                    CASE WHEN files_kept IS NOT NULL THEN '; files kept: ' || files_kept ELSE '' END,
                                    to_char(now() + make_interval(days => cfg.act_churn_retry_days), 'YYYY-MM-DD'));
                IF NOT p_dry_run THEN
                    UPDATE rvbbit.accel_policy
                       SET strategy = 'scheduled', active = false, parked_at = clock_timestamp(),
                           park_reason = 'churn', note = note_text, updated_at = clock_timestamp()
                     WHERE table_oid = cand.table_oid;
                    UPDATE rvbbit.accel_autopilot_candidates
                       SET status = 'released',
                           reasons = array_append(reasons, 'released by the autopilot: rebuilt from scratch too often to pay off')
                     WHERE table_oid = cand.table_oid;
                END IF;
                released := released || jsonb_build_object(
                    'table', cand.table_name, 'rebuilds_last_day', cand.rebuilds,
                    'files_retired', files_kept IS NULL, 'files_kept_because', files_kept);
                PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                    cand.table_oid, cand.table_name, 'scheduled', 'release', note_text,
                    CASE WHEN p_dry_run THEN 'dry_run' ELSE 'ok' END,
                    p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0),
                    p_details => jsonb_build_object('rebuilds_last_day', cand.rebuilds,
                                                    'retire', coalesce(retire_result, '{}'::jsonb),
                                                    'files_kept_because', files_kept));
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
                failures := failures || jsonb_build_object('table', cand.table_name, 'action', 'release', 'error', err);
                PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'table_finished',
                    cand.table_oid, cand.table_name, 'scheduled', 'release', err, 'failed',
                    p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - t0) * 1000.0));
            END;
        END LOOP;
        PERFORM set_config('lock_timeout', lock_timeout_before, true);
    END IF;

    -- Release the actor marker before returning so a later statement in the
    -- same transaction (an operator's set_accel_policy) counts as by hand.
    PERFORM set_config('rvbbit.accel_autopilot_actor', 'off', true);
    PERFORM rvbbit._accel_log_event(sweep_id, 'autopilot', 'sweep_finished',
        p_status => CASE WHEN jsonb_array_length(failures) > 0 THEN 'partial' ELSE 'ok' END,
        p_elapsed_ms => greatest(0, extract(epoch FROM clock_timestamp() - started) * 1000.0),
        p_details => jsonb_build_object('source', p_source, 'dry_run', p_dry_run,
            'promoted', jsonb_array_length(promoted), 'parked', jsonb_array_length(parked),
            'reactivated', jsonb_array_length(reactivated), 'released', jsonb_array_length(released),
            'failures', jsonb_array_length(failures), 'park_guard', park_guard));

    RETURN jsonb_build_object(
        'mode', cfg.mode, 'acted', true, 'dry_run', p_dry_run, 'source', p_source,
        'sweep_id', sweep_id,
        'promoted', promoted, 'parked', parked, 'reactivated', reactivated, 'released', released,
        'failures', failures, 'park_guard', park_guard,
        'elapsed_ms', extract(epoch FROM clock_timestamp() - started) * 1000.0);
END;
$fn$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_act(text, boolean) IS
    'In act mode: promote ready tables (a scheduled policy owned by the autopilot; the freshness worker builds them), reactivate parked tables read again and released tables after act_churn_retry_days, park autopilot-owned tables nobody read for act_park_idle_days, and release autopilot-owned tables rebuilt from scratch act_churn_rebuilds_per_day times in a day (files retired, acceleration off). Never touches policies set by hand.';

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
                'parked', count(*) FILTER (WHERE c.status = 'parked'),
                'released', count(*) FILTER (WHERE c.status = 'released'))
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

-- ── tables the autopilot owns ───────────────────────────────────────────────
CREATE OR REPLACE VIEW rvbbit.accel_autopilot_managed AS
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
       ev.last_read_at,
       p.park_reason,
       rvbbit.accel_rebuilds_last_day(p.table_oid)       AS rebuilds_last_day
  FROM rvbbit.accel_policy p
  LEFT JOIN rvbbit.accel_autopilot_candidates c ON c.table_oid = p.table_oid
  LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = p.table_oid
 WHERE p.managed_by = 'autopilot';

COMMENT ON VIEW rvbbit.accel_autopilot_managed IS
    'Tables whose acceleration policy the autopilot owns, with parked state and why (idle or churn), refresh cost, full rebuilds in the last day and read evidence.';
