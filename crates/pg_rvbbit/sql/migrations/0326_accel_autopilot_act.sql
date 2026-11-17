-- 0326: acceleration autopilot 'act' mode — deterministic promote / park / reactivate.
--
-- The workload observer (0259/0265) already produces durable, SELECT-backed
-- evidence and marks candidates 'ready'; until now only the nightly LLM
-- Acceleration Steward acted on it.  A 1,200-table mirror estate where ~100
-- tables matter needs a boring, hourly, auditable actor instead:
--
--   promote     ready candidates (hot for >= act_promote_min_streak
--               consecutive observations, no explicit policy) get a baseline
--               and a scheduled auto-refresh policy owned by the autopilot;
--   park        autopilot-owned policies that are provably idle AND expensive
--               are set inactive (files stay; a dirty parquet is simply not
--               authoritative, so reads fall back to heap — correct, not fast);
--   reactivate  parked tables that turn hot again come back at once.
--
-- Parking is deliberately conservative.  A table is parked only when ALL hold:
--   * the policy is autopilot-managed (hand-set and Steward policies are never
--     touched; any manual policy edit releases the table from management);
--   * >= act_park_min_age_days since promotion/reactivation;
--   * zero SELECT evidence for act_park_idle_days -- and the observer was
--     actually watching (>= half of its expected hourly runs in that window)
--     -- and the rest of the estate was awake (>= act_park_min_world_calls
--     SELECT-backed calls elsewhere), so a long weekend, holiday, or a
--     silent MCP outage parks nothing;
--   * refresh cost >= act_park_min_daily_cost_ms averaged over 7 days -- a
--     table that costs 2 s a day to keep fresh is never worth parking;
--   * at most act_max_parks_per_run per run and act_max_parks_per_day per day.
--
-- Everything the actor does lands in rvbbit.accel_activity_log under lane
-- 'autopilot' (one sweep per run), and the policy rows carry managed_by /
-- managed_since / parked_at so the state is inspectable and reversible.
-- Mode defaults to the existing 'observe'; hosted bootstrap opts into 'act'.

ALTER TABLE rvbbit.accel_autopilot_config
    DROP CONSTRAINT IF EXISTS accel_autopilot_config_mode_check;
ALTER TABLE rvbbit.accel_autopilot_config
    ADD CONSTRAINT accel_autopilot_config_mode_check
    CHECK (mode IN ('off', 'observe', 'act'));

ALTER TABLE rvbbit.accel_autopilot_config
    ADD COLUMN IF NOT EXISTS act_max_promotions_per_run integer NOT NULL DEFAULT 3
        CHECK (act_max_promotions_per_run BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS act_promote_min_streak integer NOT NULL DEFAULT 2
        CHECK (act_promote_min_streak BETWEEN 1 AND 168),
    ADD COLUMN IF NOT EXISTS act_park_idle_days integer NOT NULL DEFAULT 14
        CHECK (act_park_idle_days BETWEEN 3 AND 365),
    ADD COLUMN IF NOT EXISTS act_park_min_age_days integer NOT NULL DEFAULT 14
        CHECK (act_park_min_age_days BETWEEN 1 AND 365),
    ADD COLUMN IF NOT EXISTS act_park_min_daily_cost_ms bigint NOT NULL DEFAULT 60000
        CHECK (act_park_min_daily_cost_ms >= 0),
    ADD COLUMN IF NOT EXISTS act_park_min_world_calls bigint NOT NULL DEFAULT 100
        CHECK (act_park_min_world_calls >= 0),
    ADD COLUMN IF NOT EXISTS act_max_parks_per_run integer NOT NULL DEFAULT 1
        CHECK (act_max_parks_per_run BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS act_max_parks_per_day integer NOT NULL DEFAULT 2
        CHECK (act_max_parks_per_day BETWEEN 0 AND 1000),
    ADD COLUMN IF NOT EXISTS act_small_table_bytes bigint NOT NULL DEFAULT 1073741824
        CHECK (act_small_table_bytes >= 0);

COMMENT ON TABLE rvbbit.accel_autopilot_config IS
    'Singleton thresholds for the acceleration autopilot. off = nothing; observe = durable evidence only; act = observe plus deterministic promote/park/reactivate through rvbbit.accel_autopilot_act().';

ALTER TABLE rvbbit.accel_policy
    ADD COLUMN IF NOT EXISTS managed_by text
        CHECK (managed_by IS NULL OR managed_by IN ('autopilot')),
    ADD COLUMN IF NOT EXISTS managed_since timestamptz,
    ADD COLUMN IF NOT EXISTS parked_at timestamptz;

COMMENT ON COLUMN rvbbit.accel_policy.managed_by IS
    'NULL for operator/Steward policies. ''autopilot'' marks policies the deterministic actor may park/reactivate; any manual edit clears it.';

-- Any policy edit that is not the actor itself releases the table from
-- autopilot management, so an operator's hand-set policy is never parked.
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
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS accel_policy_autopilot_guard ON rvbbit.accel_policy;
CREATE TRIGGER accel_policy_autopilot_guard
BEFORE UPDATE ON rvbbit.accel_policy
FOR EACH ROW EXECUTE FUNCTION rvbbit.accel_policy_autopilot_guard();

-- Cost the actor reasons about: average daily freshness cost over 7 days.
CREATE OR REPLACE FUNCTION rvbbit.accel_daily_refresh_cost_ms(p_table_oid oid)
RETURNS double precision
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce(sum(a.elapsed_ms), 0)::double precision / 7.0
      FROM rvbbit.accel_activity_log a
     WHERE a.table_oid = p_table_oid
       AND a.lane = 'freshness'
       AND a.event_type = 'table_finished'
       AND a.status IN ('ok', 'noop')
       AND a.event_at > now() - interval '7 days'
$$;

CREATE OR REPLACE FUNCTION rvbbit.accel_autopilot_act(
    p_source text DEFAULT 'manual',
    p_dry_run boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog, rvbbit
AS $$
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
           AND p.table_oid IS NULL                        -- no explicit policy of any kind
           AND EXISTS (SELECT 1 FROM pg_class k WHERE k.oid = c.table_oid AND k.relkind = 'r')
         ORDER BY c.score DESC, c.table_bytes ASC
         LIMIT cfg.act_max_promotions_per_run
    LOOP
        t0 := clock_timestamp();
        interval_secs := CASE WHEN cand.table_bytes < cfg.act_small_table_bytes THEN 900 ELSE 3600 END;
        note_text := format('autopilot: promoted %s (%s calls / %s active hours / %s ms attributed; streak %s)',
                            to_char(now(), 'YYYY-MM-DD'), cand.query_calls, cand.active_hours,
                            round(cand.attributed_ms)::bigint, cand.hot_streak);
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
           AND c.hot
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
$$;

COMMENT ON FUNCTION rvbbit.accel_autopilot_act(text, boolean) IS
    'Deterministic acceleration actor for mode=act: promotes ready observer candidates (baseline + scheduled policy, managed_by=autopilot), reactivates parked tables that are hot again, and parks autopilot-managed tables only when idle for act_park_idle_days while the observer was watching and the estate was awake, expensive (>= act_park_min_daily_cost_ms), old enough, and under the per-run/per-day caps. Every action is a lane=autopilot event in accel_activity_log. p_dry_run reports without changing anything.';

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
       c.hot,
       c.hot_streak,
       c.query_calls,
       c.last_observed_at
  FROM rvbbit.accel_policy p
  LEFT JOIN rvbbit.accel_autopilot_candidates c ON c.table_oid = p.table_oid
 WHERE p.managed_by = 'autopilot';

COMMENT ON VIEW rvbbit.accel_autopilot_managed IS
    'Tables whose acceleration policy is owned by the deterministic autopilot, with parked state, refresh cost, and the observer''s latest evidence.';
