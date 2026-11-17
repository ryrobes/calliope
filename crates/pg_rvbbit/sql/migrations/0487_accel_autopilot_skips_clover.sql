-- 0487: the autopilot leaves Clover's own tables alone, and explains only what matters.
--
-- 0486 never made rvbbit's, pg_cron's or Hindsight's bookkeeping a candidate,
-- but the bundled Clover suite keeps its ledger (clover_telemetry) and research
-- runs (clover_research) in the same database and reads them all day; on the hub
-- they were the first tables with read evidence. They join the list.
-- A managed or parked table's reasons no longer list size or shape limits that
-- only explain why a table isn't accelerated.

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
           f.registered AND NOT f.acceleration_enabled AS disabled_by_hand,
           f.autopilot_owned OR (f.policy_by_hand AND f.acceleration_enabled AND f.row_groups > 0) AS accelerated
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
           CASE WHEN j.accelerated THEN NULL
                WHEN j.partition THEN 'a partition (accelerate the parent by hand)'
                WHEN j.row_security THEN 'row-level security'
                WHEN NOT j.plain THEN 'not a plain permanent table' END,
           CASE WHEN j.schema_excluded AND NOT j.accelerated THEN 'schema excluded' END,
           CASE WHEN j.plain AND NOT j.accelerated AND j.table_bytes < j.min_table_bytes
                THEN format('smaller than %s', pg_size_pretty(j.min_table_bytes)) END,
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
    'Classify every table that was read or that the autopilot owns: ready (read on act_promote_min_read_days of the last 7 days, fits the size bounds, no policy set by hand), observing, held, managed or parked, with reasons. Read-only.';
