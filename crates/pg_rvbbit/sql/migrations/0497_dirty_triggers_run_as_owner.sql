-- 0497: a role that may write a table can still write it once it is accelerated.
--
-- The triggers that record a write to an accelerated table (dirty markers, the
-- registry's dirty flags, tombstones in delete_log) or to a cube's source table
-- ran as the writing role. An application role with INSERT on its own table
-- failed with "permission denied for table table_dirty_markers" as soon as
-- that table was accelerated, and the only way out was granting the role
-- rvbbit's internal tables (the hub's frontdoor app lost a day of inserts this
-- way). They run as the extension owner now, with the search_path pinned as
-- 0483 pins every SECURITY DEFINER function; their bodies need nothing from
-- public, so it is left out.
--
-- Each of them only writes bookkeeping keyed by TG_RELID, and attaching a
-- trigger needs TRIGGER on the table, so a role can reach the bookkeeping of
-- its own tables and nothing else. A SET clause restores only the variables it
-- names when the function returns, so the flags these triggers hand each other
-- within a statement with set_config(..., true) (rvbbit.rows_changed_<oid>,
-- rvbbit.row_tombstone_generation) still carry over.
DO $migration$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT to_regprocedure(name)
          FROM unnest(ARRAY[
              'rvbbit.mark_shadow_heap_dirty()',
              'rvbbit.mark_shadow_heap_dirty_row()',
              'rvbbit.mark_shadow_heap_ctid_tombstone()',
              'rvbbit.mark_cube_source_write()'
          ]) AS name
         WHERE to_regprocedure(name) IS NOT NULL
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s SECURITY DEFINER SET search_path = pg_catalog, rvbbit, pg_temp',
            f
        );
    END LOOP;
END
$migration$;
