-- 0400: Keep same-transaction heap rows in the current full-XID epoch.
--
-- A statement can inspect a row written by its own transaction while the
-- snapshot xmax still precedes that transaction's assigned xid. Using xmax
-- alone then mistakes the row for an xid from the previous epoch and subtracts
-- 2^32, producing a negative full xid in the first epoch. Include the current
-- assigned xid in the high-water reference when one exists.

CREATE OR REPLACE FUNCTION rvbbit.xid_to_fxid(x xid)
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN floor(cur / 4294967296::numeric) * 4294967296::numeric + xv > cur
        THEN floor(cur / 4294967296::numeric) * 4294967296::numeric + xv
             - 4294967296::numeric
        ELSE floor(cur / 4294967296::numeric) * 4294967296::numeric + xv
    END
    FROM (
        SELECT (x::text)::numeric AS xv,
               greatest(
                   (pg_snapshot_xmax(pg_current_snapshot())::text)::numeric,
                   coalesce(
                       (pg_current_xact_id_if_assigned()::text)::numeric,
                       0
                   )
               ) AS cur
    ) v
$$;

COMMENT ON FUNCTION rvbbit.xid_to_fxid(xid) IS
    'Reconstructs a heap xmin as a full numeric xid using the snapshot and current-transaction high-water marks.';
