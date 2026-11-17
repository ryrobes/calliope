-- 0491: the final-lock cooldown keeps its familiar wording.
--
-- 0489 folded the 0363 final-lock cooldown into rvbbit.accel_retry_cooldown and
-- named its cause "final lock busy", which changed the skip reason operators and
-- tests know ("final lock retry cooling down until ..."). The cause is
-- "final lock retry cooling down" again; the reason now reads
-- "final lock retry cooling down; next try after ...".

CREATE OR REPLACE FUNCTION rvbbit.accel_retry_cooldown(rel oid)
RETURNS TABLE (retry_after timestamptz, cause text)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, rvbbit, pg_temp
AS $fn$
    WITH since AS (
        SELECT greatest(
                   (SELECT max(r.ran_at) FROM rvbbit.accel_tick_runs r
                     WHERE r.table_oid = rel AND r.executed AND r.status IN ('ok', 'noop')),
                   (SELECT p.updated_at FROM rvbbit.accel_policy p WHERE p.table_oid = rel),
                   now() - interval '7 days'
               ) AS t
    ), failures AS (
        SELECT count(*) AS n,
               max(r.ran_at) AS last_at,
               (array_agg(coalesce(r.error, r.reason) ORDER BY r.ran_at DESC))[1] AS last_error
          FROM rvbbit.accel_tick_runs r, since
         WHERE r.table_oid = rel
           AND r.status = 'failed'
           AND r.ran_at > since.t
    ), candidates AS (
        SELECT f.last_at + least(interval '6 hours',
                                 interval '15 minutes' * power(2, least(f.n, 6) - 1)) AS retry_after,
               format('%s failed attempt%s (last: %s)', f.n, CASE WHEN f.n = 1 THEN '' ELSE 's' END,
                      left(f.last_error, 160)) AS cause
          FROM failures f
         WHERE f.n > 0
        UNION ALL
        SELECT rvbbit.accel_final_lock_retry_after(rel), 'final lock retry cooling down'
    )
    SELECT c.retry_after, c.cause
      FROM candidates c
     WHERE c.retry_after > clock_timestamp()
     ORDER BY c.retry_after DESC
     LIMIT 1
$fn$;

