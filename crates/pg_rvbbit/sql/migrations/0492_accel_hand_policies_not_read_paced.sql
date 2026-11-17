-- 0492: a policy set by hand refreshes on its own interval; only the autopilot's are paced by reads.
--
-- Since 0446 the freshness tick stretched a dirty table's interval by how
-- recently clients had read it: read today -> the policy's interval, this week
-- -> tick_warm_interval_secs, otherwise tick_cold_interval_secs. That applied
-- to every target/scheduled policy, including ones an operator set by hand.
-- Tables read only inside SQL functions (an ETL that runs as pg_cron jobs: SPI
-- reads never count) looked unread, so on the hub a "target 5 minutes" policy
-- refreshed hourly. Read-based pacing now applies only to policies the
-- autopilot owns (managed_by = 'autopilot'), which it chose on read evidence
-- in the first place; policies set by hand do what they say, as continuous
-- policies and cubes already did.

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );
    IF position('AS autopilot_owned' IN definition) > 0 THEN
        RETURN;
    END IF;

    needle := $needle$                   AS evidence_warm
          FROM rvbbit.accel_freshness f$needle$;
    replacement := $replacement$                   AS evidence_warm,
               coalesce((SELECT p.managed_by = 'autopilot'
                           FROM rvbbit.accel_policy p
                          WHERE p.table_oid = f.table_oid), false)
                   AS autopilot_owned
          FROM rvbbit.accel_freshness f$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0492 could not find the tick candidate columns';
    END IF;
    definition := replace(definition, needle, replacement);

    needle := $needle$        IF cand.strategy = 'continuous' OR cand.f_name LIKE 'cubes.%' OR cand.evidence_hot THEN$needle$;
    replacement := $replacement$        -- 0492: only autopilot-owned policies are paced by reads.
        IF cand.strategy = 'continuous' OR cand.f_name LIKE 'cubes.%' OR cand.evidence_hot
           OR NOT cand.autopilot_owned THEN$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0492 could not find the evidence tier choice';
    END IF;
    definition := replace(definition, needle, replacement);

    EXECUTE definition;
END
$migration$;
