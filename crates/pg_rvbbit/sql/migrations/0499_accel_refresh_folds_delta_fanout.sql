-- 0499: a dirty table whose deltas have piled up gets a full refresh instead of another delta.
--
-- Every delta refresh appends at least one row group, and only
-- rvbbit.accel_fold_tick() merged them back, for clean tables, from a weekly
-- job the bundle never installed (0363 had already taken compaction out of
-- maintain_storage). On the hub a table refreshed every 15 minutes reached
-- 724 row groups for 24k rows; every scan opened all of them. The policy's
-- max_row_groups_before_rebuild (effective default 32) and
-- max_tombstones_before_rebuild were never consulted by the freshness lane.
--
-- Fragmentation only grows through deltas, and a delta only runs on a dirty
-- table, so the freshness tick is the place to stop it: when a dirty table
-- has max_row_groups_before_rebuild more row groups than a full build of its
-- rows would write, or has reached max_tombstones_before_rebuild, it gets a
-- full refresh. Row groups a full build writes anyway (one per
-- rvbbit.compact_scan_chunk_rows rows) do not count, so a large table is not
-- rebuilt on every refresh.

CREATE OR REPLACE FUNCTION rvbbit.accel_delta_row_groups(row_groups bigint, parquet_rows bigint)
RETURNS bigint
LANGUAGE sql
STABLE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT greatest(
        coalesce(row_groups, 0)
          - ceil(greatest(coalesce(parquet_rows, 0), 0)::numeric
                 / coalesce(nullif(current_setting('rvbbit.compact_scan_chunk_rows', true), '')::bigint,
                            1048576))::bigint,
        0)
$fn$;

COMMENT ON FUNCTION rvbbit.accel_delta_row_groups(bigint, bigint) IS
    'Row groups beyond the ones a full build of parquet_rows would write (one per rvbbit.compact_scan_chunk_rows, default 1,048,576): what delta refreshes added.';

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );
    IF position('AS delta_row_groups' IN definition) > 0 THEN
        RETURN;
    END IF;

    needle := $needle$               e.full_rebuild_drift_ratio,
               e.lance_separate,$needle$;
    replacement := $replacement$               e.full_rebuild_drift_ratio,
               e.max_row_groups_before_rebuild,
               e.max_tombstones_before_rebuild,
               rvbbit.accel_delta_row_groups(f.row_groups, f.parquet_rows) AS delta_row_groups,
               e.lance_separate,$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0499 could not find the tick candidate columns';
    END IF;
    definition := replace(definition, needle, replacement);

    needle := $needle$            ELSE
                prop_action := 'delta';
                prop_reason := 'dirty';$needle$;
    replacement := $replacement$            -- 0499: fold the deltas back into a full build once they pile up.
            ELSIF cand.max_row_groups_before_rebuild IS NOT NULL
                  AND cand.delta_row_groups >= cand.max_row_groups_before_rebuild THEN
                prop_action := 'full';
                prop_reason := format(
                    '%s row groups from deltas >= %s',
                    cand.delta_row_groups,
                    cand.max_row_groups_before_rebuild
                );
            ELSIF cand.max_tombstones_before_rebuild IS NOT NULL
                  AND cand.tombstones >= cand.max_tombstones_before_rebuild THEN
                prop_action := 'full';
                prop_reason := format(
                    'tombstones %s >= %s',
                    cand.tombstones,
                    cand.max_tombstones_before_rebuild
                );
            ELSE
                prop_action := 'delta';
                prop_reason := 'dirty';$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0499 could not find the delta choice';
    END IF;
    definition := replace(definition, needle, replacement);

    EXECUTE definition;
END
$migration$;
