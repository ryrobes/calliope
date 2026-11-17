-- 0489: a table whose refresh keeps failing backs off instead of stalling the workers.
--
-- A failed action counts as the sweep's one action and ends the worker pass, and
-- the same table sorts first again the next minute. On the hub, backups of a
-- table with tsvector/halfvec columns (which the parquet export can't encode)
-- were registered on creation, given a policy, and then failed every minute in
-- both worker slots, so nothing else refreshed. After a failure the tick now
-- waits 15 minutes before retrying that table, doubling with each consecutive
-- failure up to 6 hours; a success or any edit to the table's policy starts over.
-- The final-lock cooldown from 0363 is folded into the same check, and the skip
-- reason now says which one applies.

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
        SELECT rvbbit.accel_final_lock_retry_after(rel), 'final lock busy'
    )
    SELECT c.retry_after, c.cause
      FROM candidates c
     WHERE c.retry_after > clock_timestamp()
     ORDER BY c.retry_after DESC
     LIMIT 1
$fn$;

COMMENT ON FUNCTION rvbbit.accel_retry_cooldown(oid) IS
    'When the freshness tick may next try this table: 15 min after a failed action, doubling per consecutive failure up to 6 h (a success or a policy edit resets it), or the 0363 final-lock cooldown. No row when it may try now.';

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );
    IF position('rvbbit.accel_retry_cooldown(cand.f_oid)' IN definition) > 0 THEN
        RETURN;
    END IF;

    needle := '    final_lock_retry_after timestamptz;';
    replacement := E'    final_lock_retry_after timestamptz;\n    retry_cause text;';
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0489 could not find the cooldown state';
    END IF;
    definition := replace(definition, needle, replacement);

    needle := $needle$        SELECT rvbbit.accel_final_lock_retry_after(cand.f_oid)
          INTO final_lock_retry_after;$needle$;
    replacement := $replacement$        final_lock_retry_after := NULL;
        retry_cause := NULL;
        SELECT cd.retry_after, cd.cause
          INTO final_lock_retry_after, retry_cause
          FROM rvbbit.accel_retry_cooldown(cand.f_oid) cd;$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0489 could not find the cooldown read';
    END IF;
    definition := replace(definition, needle, replacement);

    needle := $needle$                'final lock retry cooling down until %s',
                to_char(final_lock_retry_after, 'YYYY-MM-DD HH24:MI:SSOF')$needle$;
    replacement := $replacement$                '%s; next try after %s',
                retry_cause,
                to_char(final_lock_retry_after, 'YYYY-MM-DD HH24:MI:SSOF')$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0489 could not find the cooldown reason';
    END IF;
    definition := replace(definition, needle, replacement);

    EXECUTE definition;
END
$migration$;
