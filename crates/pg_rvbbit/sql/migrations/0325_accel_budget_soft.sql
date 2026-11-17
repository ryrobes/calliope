-- 0325: daily refresh budgets slow accelerators down; they never let them rot.
--
-- Until now an exhausted daily_refresh_budget made the freshness planner set
-- should_act := false and leave the table dirty until the rolling 24 h window
-- freed a slot.  With mirrors landing every 15 minutes a budget of 12 meant
-- accelerators went stale for the rest of the day, and every refresh spent the
-- same token whether it was a 600 ms delta or a 40 s rebuild; the
-- current-replacement lane (variants) spent from the same counter too.
--
-- Three changes, all in the accounting, none in the refresh machinery:
--   * the budget is spent only by *expensive* freshness refreshes (elapsed
--     >= accel_budget_cheap_refresh_ms, default 2000 ms), counted from
--     rvbbit.accel_activity_log lane 'freshness' -- deltas that cost nothing
--     and replacement/variant work do not draw it down;
--   * an exhausted budget defers, it does not deny: once the table has been
--     dirty for the overdue horizon (max(freshness_target, overdue_factor ×
--     min_interval, 15 min)) or has no baseline at all, the refresh proceeds
--     and the reason says so;
--   * both knobs live in rvbbit.settings (accel_budget_cheap_refresh_ms,
--     accel_budget_overdue_factor) so an operator can tune without a release.
--
-- The tick body is patched surgically, the way 0277 did, so both the planner
-- gate and the post-claim recheck change together and nothing else moves.

CREATE OR REPLACE FUNCTION rvbbit.accel_budget_setting_int(p_key text, p_default integer)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce((
        SELECT CASE
                   WHEN jsonb_typeof(s.value) = 'number' THEN (s.value #>> '{}')::integer
                   WHEN jsonb_typeof(s.value) = 'string'
                        AND (s.value #>> '{}') ~ '^\s*-?\d+\s*$' THEN (s.value #>> '{}')::integer
               END
          FROM rvbbit.settings s
         WHERE s.key = p_key
    ), p_default)
$$;

COMMENT ON FUNCTION rvbbit.accel_budget_setting_int(text, integer) IS
    'Integer operator setting from rvbbit.settings with a default; used by the accel budget accounting.';

CREATE OR REPLACE FUNCTION rvbbit.accel_budget_overdue_secs(
    p_freshness_target_secs integer,
    p_min_interval_secs integer
) RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT greatest(
        coalesce(p_freshness_target_secs, 0),
        coalesce(p_min_interval_secs, 60)
            * greatest(1, rvbbit.accel_budget_setting_int('accel_budget_overdue_factor', 4)),
        900
    )::integer
$$;

COMMENT ON FUNCTION rvbbit.accel_budget_overdue_secs(integer, integer) IS
    'Seconds a table may stay dirty under an exhausted daily budget before the tick refreshes it anyway.';

CREATE OR REPLACE FUNCTION rvbbit.accel_budget_spent(
    p_table_oid oid,
    p_budget_epoch_at timestamptz
) RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT count(*)::integer
      FROM rvbbit.accel_activity_log a
     WHERE a.table_oid = p_table_oid
       AND a.lane = 'freshness'
       AND a.event_type = 'table_finished'
       AND a.status IN ('ok', 'noop')
       AND coalesce(a.elapsed_ms, 0)
           >= rvbbit.accel_budget_setting_int('accel_budget_cheap_refresh_ms', 2000)
       AND a.event_at > greatest(
               now() - interval '24 hours',
               coalesce(p_budget_epoch_at, now() - interval '24 hours')
           )
$$;

COMMENT ON FUNCTION rvbbit.accel_budget_spent(oid, timestamptz) IS
    'Expensive freshness refreshes charged against a table''s daily budget in the current lifecycle window; cheap deltas and replacement/variant work are free.';

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
    changed boolean := false;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );

    IF position('rvbbit.accel_budget_spent(cand.f_oid' IN definition) = 0 THEN
        needle := $needle$
            IF should_act AND cand.daily_refresh_budget IS NOT NULL THEN
                SELECT count(*)
                  INTO used_today
                  FROM rvbbit.accel_tick_runs r
                 WHERE r.table_oid = cand.f_oid
                   AND r.executed
                   AND r.ran_at > greatest(
                       now() - interval '24 hours',
                       cand.budget_epoch_at
                   );
                IF used_today >= cand.daily_refresh_budget THEN
                    should_act := false;
                    act_reason := format(
                        'daily budget %s exhausted',
                        cand.daily_refresh_budget
                    );
                END IF;
            END IF;
$needle$;
        replacement := $replacement$
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
$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION
                '0325 could not find the planner daily-budget gate in rvbbit._accel_tick_batch';
        END IF;
        definition := replace(definition, needle, replacement);
        changed := true;
    END IF;

    IF position('rvbbit.accel_budget_spent(cand.f_oid, live_candidate.budget_epoch_at)' IN definition) = 0 THEN
        needle := $needle$
                    IF live_candidate.daily_refresh_budget IS NOT NULL THEN
                        SELECT count(*)
                          INTO used_today
                          FROM rvbbit.accel_tick_runs r
                         WHERE r.table_oid = cand.f_oid
                           AND r.executed
                           AND r.ran_at > greatest(
                               now() - interval '24 hours',
                               live_candidate.budget_epoch_at
                           );
                        IF used_today >= live_candidate.daily_refresh_budget THEN
                            do_execute := false;
                            act_reason := format(
                                'daily budget %s exhausted after worker claim',
                                live_candidate.daily_refresh_budget
                            );
                        END IF;
                    END IF;
$needle$;
        replacement := $replacement$
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
$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION
                '0325 could not find the post-claim daily-budget gate in rvbbit._accel_tick_batch';
        END IF;
        definition := replace(definition, needle, replacement);
        changed := true;
    END IF;

    IF changed THEN
        EXECUTE definition;
    END IF;
END;
$migration$;

COMMENT ON FUNCTION rvbbit._accel_tick_batch(integer, boolean, integer) IS
    'Internal one-transaction freshness body. Daily refresh budgets are spent only by expensive freshness refreshes and defer rather than deny: an overdue or baseline-less table refreshes regardless.';
