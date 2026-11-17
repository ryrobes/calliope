-- 0495: one view of what the acceleration autopilot sees and did, per table.
--
-- The read-driven autopilot (0486, 0493) decides from read evidence: a table
-- read on enough of the last 7 days is promoted, one nobody reads is parked,
-- and one rebuilt from scratch too often is released. Its state was spread
-- over accel_autopilot_candidates, accel_policy, accel_autopilot_managed and
-- the observatory's event log. This view lays it out in one row per observed
-- table: the classification and its reasons, the read evidence, the policy and
-- who owns it, what keeping the copy fresh costs, and the autopilot's last
-- action. It replaces the payoff view 0486 removed (rvbbit.accel_payoffs),
-- whose read-time arithmetic no longer drives any decision.

CREATE OR REPLACE VIEW rvbbit.accel_autopilot_decisions AS
SELECT c.table_oid,
       c.table_name,
       c.status,
       c.reasons,
       c.eligible,
       c.read_days,
       c.last_read_at,
       c.table_bytes,
       pg_size_pretty(c.table_bytes)                                   AS size,
       pol.strategy,
       pol.active                                                      AS policy_active,
       pol.managed_by,
       pol.managed_since,
       pol.parked_at,
       pol.park_reason,
       CASE WHEN pol.table_oid IS NOT NULL
            THEN rvbbit.accel_rebuilds_last_day(c.table_oid) END        AS rebuilds_last_day,
       CASE WHEN pol.table_oid IS NOT NULL
            THEN rvbbit.accel_daily_refresh_cost_ms(c.table_oid) END    AS daily_refresh_cost_ms,
       ev.action                                                       AS last_action,
       ev.event_at                                                     AS last_action_at,
       ev.reason                                                       AS last_action_reason,
       c.last_observed_at
  FROM rvbbit.accel_autopilot_candidates c
  LEFT JOIN rvbbit.accel_policy pol ON pol.table_oid = c.table_oid
  LEFT JOIN LATERAL (
        SELECT e.action, e.event_at, e.reason
          FROM rvbbit.accel_observatory_autopilot_events e
         WHERE e.table_oid = c.table_oid
         ORDER BY e.event_at DESC
         LIMIT 1) ev ON true;

COMMENT ON VIEW rvbbit.accel_autopilot_decisions IS
    'Per observed table: the autopilot''s classification and reasons, read evidence (days read in the last 7, last read), the acceleration policy and who owns it (managed_by NULL = set by hand, never touched), parked/released reason, full rebuilds in the last day and daily refresh cost, and the autopilot''s last action. Filter managed_by = ''autopilot'' for what it promoted.';
