-- 0327: honest current_work_started_at in rvbbit.accel_live_activity.
--
-- Freshness workers advertise their work in application_name as
-- 'rvbbit/fresh s=<sweep> w=<slot>/<n> o=<oid> a=<action> t=<epoch> <name>',
-- truncated to PostgreSQL's 63-character application_name limit.  Once sweep
-- ids reach six digits and the action is long ('build_vortex'), the truncation
-- lands inside the epoch: 't=1786953416.933' becomes 't=178695', which
-- to_timestamp() reads as January 1970, and the observatory reported a
-- 29,522,834-minute (56-year) running build on the pilot appliance.
--
-- Two fixes: the emitters put t=, o=, a= (time, table, action) right after the
-- lane so the diagnostic sweep/worker fields are what gets cut, never the
-- essentials (the parsers are order-independent), and the view ignores any
-- t= that cannot be a real timestamp (before 2001) and falls back to
-- query_start, so a truncated stamp degrades to a coarser start, not 1970.

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

    needle := $needle$
                    'rvbbit/fresh s=%s w=%s/%s o=%s a=%s t=%s %s',
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                    cand.f_oid,
                    prop_action,
                    round(extract(epoch FROM table_started)::numeric, 3),
                    cand.f_name
$needle$;
    replacement := $replacement$
                    'rvbbit/fresh t=%s o=%s a=%s s=%s w=%s/%s %s',
                    round(extract(epoch FROM table_started)::numeric, 3),
                    cand.f_oid,
                    prop_action,
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                    cand.f_name
$replacement$;
    IF position(needle IN definition) > 0 THEN
        definition := replace(definition, needle, replacement);
        changed := true;
    END IF;

    needle := $needle$
                                    'rvbbit/fresh s=%s w=%s/%s o=%s a=full t=%s %s',
                                    sweep_id,
                                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                                    cand.f_oid,
                                    round(extract(epoch FROM table_started)::numeric, 3),
                                    cand.f_name
$needle$;
    replacement := $replacement$
                                    'rvbbit/fresh t=%s o=%s a=full s=%s w=%s/%s %s',
                                    round(extract(epoch FROM table_started)::numeric, 3),
                                    cand.f_oid,
                                    sweep_id,
                                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                                    cand.f_name
$replacement$;
    IF position(needle IN definition) > 0 THEN
        definition := replace(definition, needle, replacement);
        changed := true;
    END IF;

    IF NOT changed AND position('rvbbit/fresh t=%s' IN definition) = 0 THEN
        RAISE EXCEPTION
            '0327 could not find the freshness application_name emitters in rvbbit._accel_tick_batch';
    END IF;
    IF changed THEN
        EXECUTE definition;
    END IF;
END;
$migration$;

-- Same view as 0266, with the timestamp plausibility guard. Recreated in full
-- because CREATE OR REPLACE VIEW cannot change an existing column expression
-- through a partial patch.
CREATE OR REPLACE VIEW rvbbit.accel_live_activity AS
WITH worker_locks AS (
    SELECT a.pid,
           bool_or(
               l.locktype = 'advisory'
               AND l.classid = 1381187156::oid
               AND l.objid = 7::oid
               AND l.objsubid = 2
               AND l.granted
           ) AS has_canonical_sweep_lock,
           bool_or(
               l.locktype = 'advisory'
               AND l.classid = 1381187156::oid
               AND l.objid = 8::oid
               AND l.objsubid = 2
               AND l.granted
           ) AS has_variant_sweep_lock,
           array_agg(l.objid::oid ORDER BY l.objid)
               FILTER (
                   WHERE l.locktype = 'advisory'
                     AND l.classid = 1380336724::oid
                     AND l.objsubid = 1
                     AND l.granted
               ) AS locked_table_oids
      FROM pg_stat_activity a
      LEFT JOIN pg_locks l ON l.pid = a.pid
     GROUP BY a.pid
), parsed AS (
    SELECT a.pid,
           a.usename,
           a.datname,
           a.application_name,
           a.client_addr,
           a.backend_start,
           a.xact_start,
           a.query_start,
           a.state,
           a.wait_event_type,
           a.wait_event,
           pg_blocking_pids(a.pid) AS blocking_pids,
           wl.has_canonical_sweep_lock,
           wl.has_variant_sweep_lock,
           wl.locked_table_oids,
           substring(a.application_name FROM '^rvbbit/([^ ]+)') AS app_lane,
           nullif(substring(a.application_name FROM ' s=([0-9]+)'), '')::bigint
               AS app_sweep_id,
           nullif(substring(a.application_name FROM ' o=([0-9]+)'), '')::oid
               AS app_table_oid,
           nullif(substring(a.application_name FROM ' a=([^ ]+)'), '') AS app_action,
           -- A truncated or malformed stamp must never masquerade as 1970:
           -- only epochs from 2001 onwards are believed.
           CASE
               WHEN nullif(substring(a.application_name FROM ' t=([0-9.]+)'), '')::double precision
                    >= 1000000000
               THEN to_timestamp(
                        nullif(substring(a.application_name FROM ' t=([0-9.]+)'), '')::double precision
                    )
           END AS app_table_started_at,
           CASE
               WHEN coalesce(wl.has_variant_sweep_lock, false) THEN 'build_vortex'
               WHEN a.query ~* 'rvbbit\.rebuild_acceleration' THEN 'full'
               WHEN a.query ~* 'rvbbit\.refresh_acceleration' THEN 'delta'
               WHEN a.query ~* 'rvbbit\.compact_acceleration' THEN 'compact'
           END AS inferred_action,
           a.query ~* 'rvbbit\.(refresh_acceleration|rebuild_acceleration|compact_acceleration)'
               AS query_is_accel_operation
      FROM pg_stat_activity a
      JOIN worker_locks wl ON wl.pid = a.pid
     WHERE a.application_name LIKE 'rvbbit/%'
        OR coalesce(wl.has_canonical_sweep_lock, false)
        OR coalesce(wl.has_variant_sweep_lock, false)
        OR (
            cardinality(coalesce(wl.locked_table_oids, '{}'::oid[])) > 0
            AND a.query ~* 'rvbbit\.(refresh_acceleration|rebuild_acceleration|compact_acceleration)'
        )
), resolved AS (
    SELECT p.*,
           coalesce(
               p.app_table_oid,
               CASE
                   WHEN cardinality(p.locked_table_oids) = 1
                   THEN p.locked_table_oids[1]
               END
           ) AS resolved_table_oid
      FROM parsed p
)
SELECT r.pid,
       CASE r.app_lane
           WHEN 'fresh' THEN 'freshness'
           WHEN 'fold' THEN 'fold'
           WHEN 'variant' THEN 'variant'
           ELSE CASE
               WHEN coalesce(r.has_variant_sweep_lock, false) THEN 'variant'
               WHEN coalesce(r.has_canonical_sweep_lock, false) THEN 'freshness_or_fold'
               WHEN r.query_is_accel_operation THEN 'manual'
               ELSE r.app_lane
           END
       END AS lane,
       r.app_sweep_id AS sweep_id,
       r.resolved_table_oid AS table_oid,
       c.oid::regclass::text AS table_name,
       coalesce(r.app_action, r.inferred_action) AS action,
       coalesce(r.app_table_started_at, r.query_start) AS current_work_started_at,
       greatest(
           0,
           extract(epoch FROM (
               clock_timestamp() - coalesce(r.app_table_started_at, r.query_start)
           )) * 1000.0
       ) AS current_work_elapsed_ms,
       r.state,
       r.wait_event_type,
       r.wait_event,
       r.blocking_pids,
       r.locked_table_oids AS transaction_locked_table_oids,
       r.usename,
       r.datname,
       r.application_name,
       r.client_addr,
       r.backend_start,
       r.xact_start,
       r.query_start
  FROM resolved r
  LEFT JOIN pg_class c ON c.oid = r.resolved_table_oid;

COMMENT ON VIEW rvbbit.accel_live_activity IS
    'In-flight accelerator worker/table from pg_stat_activity plus advisory locks. Unlike activity-log rows, this is visible before the worker transaction commits. Table start comes from the worker''s t= stamp when plausible (>= 2001), else query_start.';
