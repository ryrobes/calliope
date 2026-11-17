-- 0330: observatory coverage counts within-target dirtiness as healthy.
--
-- With mirrors landing every 15 minutes and min_interval 900 s, some tables
-- are always between "data landed" and "next refresh". They are dirty, but
-- exactly as fresh as their policy promises; reporting them as "not current"
-- and docking coverage for them made a healthy appliance read as "3 tables
-- behind" all day. Coverage therefore now distinguishes:
--
--   current          authoritative, clean, no replacement pending
--   within_target    dirty, but seconds dirty <= target (min_interval for scheduled)
--   healthy          current OR within_target          -> healthy_pct is the headline
--   behind           eligible and neither: beyond target, replacement pending,
--                    or not authoritative
--
-- The per-table view already carried target_bucket; this only changes the
-- roll-up (existing columns keep their meaning) and adds a `healthy` flag on
-- attention rows so queues can de-emphasise the merely-in-flight.

CREATE OR REPLACE VIEW rvbbit.accel_observatory_coverage AS
SELECT count(*) FILTER (WHERE t.active AND t.eligible)                    AS eligible_tables,
       count(*) FILTER (WHERE t.active AND NOT t.eligible)                AS awaiting_baseline,
       count(*) FILTER (WHERE t.active AND t.eligible AND t.is_current)   AS current_tables,
       count(*) FILTER (WHERE t.active AND t.eligible AND NOT t.is_current) AS unaccelerated_tables,
       count(*) FILTER (WHERE t.active AND t.eligible AND t.target_bucket IN ('1_2x', '2_5x', '5x_plus'))
                                                                          AS beyond_target,
       count(*) FILTER (WHERE t.active AND t.eligible AND t.delta_capable) AS delta_capable_tables,
       count(*) FILTER (WHERE t.managed_by = 'autopilot')                 AS autopilot_managed,
       count(*) FILTER (WHERE t.autopilot_parked)                         AS autopilot_parked,
       CASE WHEN count(*) FILTER (WHERE t.active AND t.eligible) > 0
            THEN round(100.0 * count(*) FILTER (WHERE t.active AND t.eligible AND t.is_current)
                       / count(*) FILTER (WHERE t.active AND t.eligible), 1) END AS coverage_pct,
       (SELECT count(*) FROM rvbbit.accel_live_activity)                  AS running_now,
       (SELECT count(*) FROM rvbbit.orphaned_files)                       AS orphaned_files,
       (SELECT mode FROM rvbbit.accel_autopilot_config WHERE singleton)   AS autopilot_mode,
       -- 0330: within-target dirtiness is healthy
       count(*) FILTER (WHERE t.active AND t.eligible AND NOT t.is_current
                          AND t.target_bucket = 'within')                 AS within_target_tables,
       count(*) FILTER (WHERE t.active AND t.eligible
                          AND (t.is_current OR t.target_bucket = 'within')) AS healthy_tables,
       count(*) FILTER (WHERE t.active AND t.eligible
                          AND NOT (t.is_current OR t.target_bucket = 'within')) AS behind_tables,
       CASE WHEN count(*) FILTER (WHERE t.active AND t.eligible) > 0
            THEN round(100.0 * count(*) FILTER (WHERE t.active AND t.eligible
                                                  AND (t.is_current OR t.target_bucket = 'within'))
                       / count(*) FILTER (WHERE t.active AND t.eligible), 1) END AS healthy_pct
  FROM rvbbit.accel_observatory_tables t;

COMMENT ON VIEW rvbbit.accel_observatory_coverage IS
    'Headline acceleration coverage. healthy = current OR dirty within target (healthy_pct is the number to watch); behind = eligible and neither (beyond target, replacement pending, or not authoritative). coverage_pct keeps the strict current-only meaning.';

CREATE OR REPLACE VIEW rvbbit.accel_observatory_attention AS
SELECT t.table_oid,
       t.table_name,
       t.strategy,
       CASE
           WHEN t.fold_due THEN 'fold due'
           WHEN t.current_replacement_pending THEN 'pending'
           WHEN t.shadow_heap_dirty THEN 'dirty'
           WHEN NOT t.parquet_authoritative THEN 'not authoritative'
           ELSE 'attention'
       END                                          AS state,
       t.seconds_since_refresh,
       t.seconds_dirty,
       t.effective_target_secs,
       t.target_distance,
       t.blocked_reason,
       t.delta_capable,
       t.row_groups,
       t.tombstones,
       t.budget_spent,
       t.daily_refresh_budget,
       CASE WHEN t.fold_due THEN 0
            WHEN t.current_replacement_pending THEN 1
            WHEN t.shadow_heap_dirty THEN 2
            ELSE 3 END                              AS priority,
       (t.shadow_heap_dirty AND t.target_bucket = 'within'
        AND NOT t.current_replacement_pending AND NOT t.fold_due) AS healthy
  FROM rvbbit.accel_observatory_tables t
 WHERE t.active AND t.eligible
   AND (t.shadow_heap_dirty OR t.current_replacement_pending OR t.fold_due OR NOT t.parquet_authoritative);

COMMENT ON VIEW rvbbit.accel_observatory_attention IS
    'Eligible tables that are not current right now. healthy=true marks rows that are merely dirty within target (in flight, as promised); the rest need attention.';
