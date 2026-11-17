-- 0329: acceleration observatory views.
--
-- One set of definitions for "how healthy is acceleration", shared by the
-- Calliope 'Acceleration health' dashboard, DataRabbit's Observatory tab, the
-- Hermes operations skill, and the autopilot itself.  Distilled from the
-- Calliope-built dashboard (docs/examples/acceleration-observatory) so its
-- vocabulary survives verbatim:
--
--   eligible   registered, active, and past its first Parquet baseline
--              (row groups exist or a refresh has happened)
--   current    parquet authoritative, heap not dirty, no replacement pending
--   fresh      (a child layout) parent current AND child ready, row-complete,
--              refreshed after the newest visible parent row group
--   synced     child-to-published-Parquet gate only (ignores parent state)
--
-- Additions over the dashboard: scheduled policies without a freshness target
-- use min_interval as their implicit target; per-table delta capability (PK
-- overlay ready) and fold backlog (row groups / tombstones); autopilot
-- management state; expensive-refresh budget spend; orphaned files.

CREATE OR REPLACE VIEW rvbbit.accel_observatory_tables AS
SELECT f.table_oid,
       f.table_name,
       f.strategy,
       f.active,
       e.explicit                                            AS policy_explicit,
       e.note                                                AS policy_note,
       p.managed_by,
       p.parked_at IS NOT NULL AND NOT coalesce(p.active, true) AS autopilot_parked,
       (coalesce(f.row_groups, 0) > 0 OR f.last_refresh_at IS NOT NULL) AS eligible,
       (f.parquet_authoritative AND NOT f.shadow_heap_dirty AND NOT f.current_replacement_pending)
                                                             AS is_current,
       f.parquet_authoritative,
       f.shadow_heap_dirty,
       f.current_replacement_pending,
       f.fold_due,
       f.blocked_reason,
       f.last_refresh_at,
       f.seconds_since_refresh,
       fr.seconds_dirty,
       fr.dirty_since,
       e.freshness_target_secs,
       e.min_interval_secs,
       coalesce(e.freshness_target_secs,
                CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END)
                                                             AS effective_target_secs,
       -- Distance to target is measured on how long the table has been dirty:
       -- a clean table cannot be late, whatever its last refresh time.
       CASE WHEN f.shadow_heap_dirty
             AND coalesce(e.freshness_target_secs,
                          CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END) > 0
            THEN coalesce(fr.seconds_dirty, 0)::double precision
                 / coalesce(e.freshness_target_secs,
                            CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END)
       END                                                   AS target_distance,
       CASE
           WHEN NOT f.shadow_heap_dirty                                      THEN 'current'
           WHEN coalesce(e.freshness_target_secs,
                         CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END) IS NULL
                                                                             THEN 'no_target'
           WHEN coalesce(fr.seconds_dirty, 0) <= coalesce(e.freshness_target_secs,
                         CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END) THEN 'within'
           WHEN coalesce(fr.seconds_dirty, 0) <= 2 * coalesce(e.freshness_target_secs,
                         CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END) THEN '1_2x'
           WHEN coalesce(fr.seconds_dirty, 0) <= 5 * coalesce(e.freshness_target_secs,
                         CASE WHEN f.strategy = 'scheduled' THEN e.min_interval_secs END) THEN '2_5x'
           ELSE '5x_plus'
       END                                                   AS target_bucket,
       e.daily_refresh_budget,
       CASE WHEN e.daily_refresh_budget IS NOT NULL
            THEN rvbbit.accel_budget_spent(f.table_oid, e.budget_epoch_at) END AS budget_spent,
       coalesce(f.row_groups, 0)                             AS row_groups,
       f.max_row_groups_before_rebuild,
       coalesce(f.tombstones, 0)                             AS tombstones,
       f.max_tombstones_before_rebuild,
       fr.drift_ratio,
       fr.parquet_rows,
       fr.parquet_bytes,
       fr.heap_live_tuples,
       rvbbit.accel_overlay_ready(f.table_oid)               AS delta_capable,
       cardinality(rvbbit.accel_identity_columns(f.table_oid)) > 0 AS has_primary_key,
       f.last_rebuild_ms,
       f.last_rebuild_rows,
       fr.op_running,
       (SELECT count(*) FROM rvbbit.orphaned_files o WHERE o.table_oid = f.table_oid) AS orphaned_files
  FROM rvbbit.accel_fold_candidates f
  LEFT JOIN rvbbit.accel_policy_effective e ON e.table_oid = f.table_oid
  LEFT JOIN rvbbit.accel_policy p ON p.table_oid = f.table_oid
  LEFT JOIN rvbbit.accel_freshness fr ON fr.table_oid = f.table_oid;

COMMENT ON VIEW rvbbit.accel_observatory_tables IS
    'One row per registered table with acceleration state, policy, target distance (seconds dirty over the target; min_interval is the implicit target for scheduled policies; clean tables are ''current''), delta capability, fold backlog, budget spend, and autopilot management. eligible = past its first baseline; is_current = authoritative, clean, no replacement pending.';

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
       (SELECT mode FROM rvbbit.accel_autopilot_config WHERE singleton)   AS autopilot_mode
  FROM rvbbit.accel_observatory_tables t;

COMMENT ON VIEW rvbbit.accel_observatory_coverage IS
    'Headline acceleration coverage: eligible vs current tables, beyond-target count, delta-capable count, autopilot state, running workers, orphaned files.';

CREATE OR REPLACE VIEW rvbbit.accel_observatory_strategy AS
SELECT t.strategy,
       count(*)                                    AS tables,
       count(*) FILTER (WHERE t.is_current)        AS current_tables,
       count(*) FILTER (WHERE NOT t.is_current)    AS other_tables,
       count(*) FILTER (WHERE t.delta_capable)     AS delta_capable_tables
  FROM rvbbit.accel_observatory_tables t
 WHERE t.active AND t.eligible
 GROUP BY t.strategy;

CREATE OR REPLACE VIEW rvbbit.accel_observatory_target_distance AS
SELECT t.target_bucket,
       CASE t.target_bucket
           WHEN 'current' THEN 0 WHEN 'within' THEN 1 WHEN '1_2x' THEN 2 WHEN '2_5x' THEN 3
           WHEN '5x_plus' THEN 4 ELSE 5
       END                                          AS bucket_order,
       count(*)                                     AS tables
  FROM rvbbit.accel_observatory_tables t
 WHERE t.active AND t.eligible
 GROUP BY t.target_bucket;

CREATE OR REPLACE VIEW rvbbit.accel_observatory_layouts AS
WITH cur AS (
    SELECT rg.table_oid,
           sum(rg.n_rows)::bigint AS current_rows,
           max(rg.created_at)     AS newest_row_group
      FROM rvbbit.row_groups_visible rg
     GROUP BY rg.table_oid
)
SELECT l.table_oid,
       l.table_oid::regclass::text                              AS table_name,
       l.layout,
       CASE WHEN l.layout = 'vortex_scan' THEN 'Vortex'
            WHEN l.layout LIKE 'hive:%' THEN 'Hive'
            WHEN l.layout LIKE 'cluster:%' THEN 'Cluster'
            ELSE 'Other' END                                    AS family,
       l.status,
       l.expected_rows,
       l.actual_rows,
       c.current_rows,
       l.file_count,
       l.status_message,
       l.refreshed_at,
       c.newest_row_group,
       t.parquet_authoritative,
       t.shadow_heap_dirty,
       t.current_replacement_pending,
       coalesce(t.is_current, false)                            AS parent_current,
       (l.status = 'ready' AND l.actual_rows = c.current_rows
        AND l.refreshed_at >= c.newest_row_group)                AS layout_synced,
       (coalesce(t.is_current, false) AND l.status = 'ready'
        AND l.actual_rows = c.current_rows
        AND l.refreshed_at >= c.newest_row_group)                AS is_fresh,
       CASE
           WHEN NOT coalesce(t.is_current, false)                            THEN 'parent_stale'
           WHEN l.status <> 'ready'                                          THEN 'variant_' || l.status
           WHEN l.actual_rows IS DISTINCT FROM c.current_rows
             OR l.refreshed_at < c.newest_row_group                          THEN 'variant_behind'
           ELSE 'fresh'
       END                                                      AS gate
  FROM rvbbit.layout_variant_status l
  JOIN rvbbit.accel_observatory_tables t ON t.table_oid = l.table_oid AND t.active
  LEFT JOIN cur c ON c.table_oid = l.table_oid;

COMMENT ON VIEW rvbbit.accel_observatory_layouts IS
    'Configured table-layouts (Vortex/Hive/Cluster) with end-to-end freshness: fresh requires the parent Parquet current and the child ready, row-complete, and refreshed after the newest visible parent row group; layout_synced is the child gate alone.';

CREATE OR REPLACE VIEW rvbbit.accel_observatory_layout_summary AS
SELECT family,
       count(*)                                    AS layouts,
       count(*) FILTER (WHERE is_fresh)            AS fresh_layouts,
       count(*) FILTER (WHERE layout_synced)       AS synced_layouts,
       count(*) FILTER (WHERE layout_synced AND NOT is_fresh) AS waiting_on_parent,
       count(DISTINCT table_oid)                   AS tables,
       coalesce(sum(file_count), 0)                AS files,
       min(refreshed_at)                           AS oldest_refresh,
       CASE WHEN count(*) > 0
            THEN round(100.0 * count(*) FILTER (WHERE is_fresh) / count(*), 1) END AS fresh_pct
  FROM rvbbit.accel_observatory_layouts
 GROUP BY family;

CREATE OR REPLACE VIEW rvbbit.accel_observatory_live AS
SELECT l.pid,
       l.lane,
       l.table_oid,
       l.table_name,
       l.action,
       l.current_work_started_at,
       l.current_work_elapsed_ms,
       l.state,
       l.wait_event,
       l.blocking_pids,
       p.p95_elapsed_ms,
       (l.current_work_elapsed_ms > p.p95_elapsed_ms)         AS beyond_p95,
       (l.current_work_elapsed_ms > 86400000.0)                AS implausible
  FROM rvbbit.accel_live_activity l
  LEFT JOIN rvbbit.accel_table_runtime_profile p
    ON p.table_name = l.table_name AND p.lane = l.lane;

COMMENT ON VIEW rvbbit.accel_observatory_live IS
    'In-flight accelerator work with the table''s historical P95 for the lane; implausible flags elapsed > 24 h (a stale or orphaned live row).';

CREATE OR REPLACE FUNCTION rvbbit.accel_observatory_timeline(
    p_hours integer DEFAULT 3,
    p_limit integer DEFAULT 500
) RETURNS TABLE (
    table_oid oid,
    table_name text,
    lane text,
    action text,
    status text,
    started_at timestamptz,
    finished_at timestamptz,
    elapsed_ms double precision,
    rows_written bigint,
    reason text
)
LANGUAGE sql
STABLE
AS $$
    SELECT a.table_oid,
           a.table_name,
           a.lane,
           a.action,
           a.status,
           a.event_at - make_interval(secs => coalesce(a.elapsed_ms, 0) / 1000.0) AS started_at,
           a.event_at AS finished_at,
           a.elapsed_ms,
           a.rows_written,
           a.reason
      FROM rvbbit.accel_activity_log a
     WHERE a.event_type = 'table_finished'
       AND a.elapsed_ms IS NOT NULL
       AND a.event_at > now() - make_interval(hours => greatest(coalesce(p_hours, 3), 1))
     ORDER BY a.event_at DESC
     LIMIT greatest(coalesce(p_limit, 500), 1)
$$;

COMMENT ON FUNCTION rvbbit.accel_observatory_timeline(integer, integer) IS
    'Finished accelerator table operations in the last p_hours (default 3), with derived start times, for worker-overlap timelines.';

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
            ELSE 3 END                              AS priority
  FROM rvbbit.accel_observatory_tables t
 WHERE t.active AND t.eligible
   AND (t.shadow_heap_dirty OR t.current_replacement_pending OR t.fold_due OR NOT t.parquet_authoritative);

COMMENT ON VIEW rvbbit.accel_observatory_attention IS
    'Eligible tables that are not current right now (dirty, replacement pending, fold due, or non-authoritative), ordered for an operator queue via priority.';

CREATE OR REPLACE VIEW rvbbit.accel_observatory_rebuild_cost AS
WITH completed AS (
    SELECT a.table_oid, a.table_name, a.event_at, a.elapsed_ms, a.rows_written,
           a.row_groups_written, a.reason
      FROM rvbbit.accel_activity_log a
     WHERE a.event_type = 'table_finished'
       AND a.action = 'full'
       AND a.status = 'ok'
       AND a.elapsed_ms IS NOT NULL
), scope AS (
    SELECT min(event_at) AS window_start, max(event_at) AS window_end, count(*) AS completed_runs
      FROM completed
), ranked AS (
    SELECT c.table_oid,
           c.table_name,
           count(*)                                                       AS samples,
           avg(c.elapsed_ms)                                              AS avg_elapsed_ms,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.elapsed_ms)      AS p50_elapsed_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY c.elapsed_ms)     AS p95_elapsed_ms,
           max(c.elapsed_ms)                                              AS max_elapsed_ms,
           avg(c.rows_written)                                            AS avg_rows_written,
           avg(c.row_groups_written)                                      AS avg_row_groups_written,
           max(c.event_at)                                                AS last_finished_at,
           mode() WITHIN GROUP (ORDER BY c.reason)                        AS common_reason,
           sum(c.elapsed_ms) FILTER (WHERE c.event_at > now() - interval '7 days') / 7.0
                                                                          AS daily_cost_ms
      FROM completed c
     GROUP BY c.table_oid, c.table_name
)
SELECT row_number() OVER (ORDER BY r.avg_elapsed_ms DESC)  AS rank,
       r.table_oid,
       r.table_name,
       coalesce(t.strategy, '—')                            AS strategy,
       r.samples,
       r.avg_elapsed_ms,
       r.p50_elapsed_ms,
       r.p95_elapsed_ms,
       r.max_elapsed_ms,
       r.avg_rows_written,
       r.avg_row_groups_written,
       r.daily_cost_ms,
       r.last_finished_at,
       r.common_reason,
       coalesce(t.row_groups, 0)                            AS current_row_groups,
       t.is_current,
       t.delta_capable,
       t.managed_by,
       s.window_start,
       s.window_end,
       s.completed_runs
  FROM ranked r
  CROSS JOIN scope s
  LEFT JOIN rvbbit.accel_observatory_tables t ON t.table_oid = r.table_oid;

COMMENT ON VIEW rvbbit.accel_observatory_rebuild_cost IS
    'Full-rebuild cost profile per table (successful full refreshes only): mean/P50/P95/max elapsed, rows, typical trigger, 7-day daily cost, and whether the table can now delta-refresh.';

CREATE OR REPLACE VIEW rvbbit.accel_observatory_autopilot_events AS
SELECT a.event_at,
       a.sweep_id,
       a.action,
       a.status,
       a.table_oid,
       a.table_name,
       a.reason,
       a.details
  FROM rvbbit.accel_activity_log a
 WHERE a.lane = 'autopilot'
   AND a.event_type = 'table_finished';

COMMENT ON VIEW rvbbit.accel_observatory_autopilot_events IS
    'Autopilot promote/park/reactivate actions from accel_activity_log.';
