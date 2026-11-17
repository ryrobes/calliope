-- Carry the canonical compact wrapper onto upgraded databases: legacy wrappers
-- recreated the obsolete all-events trigger alongside modern per-event triggers.
-- Repair setup remains a separately committed NOWAIT transaction, never held
-- through snapshot materialization/export. Unknown SQL errors remain opt-in retries.

CREATE OR REPLACE FUNCTION rvbbit.compact(rel regclass, keep_heap boolean)
RETURNS TABLE (rg_id bigint, n_rows bigint, n_bytes bigint, heap_freed_bytes bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    written_rows  bigint;
    heap_size_pre bigint;
    max_rg_id_pre bigint;
    shred         record;
BEGIN
    IF NOT rvbbit.is_rvbbit_table(rel) THEN
        RAISE EXCEPTION '% is not an rvbbit table', rel;
    END IF;

    heap_size_pre := pg_total_relation_size(rel);
    SELECT COALESCE(max(rg.rg_id), -1)
    INTO max_rg_id_pre
    FROM rvbbit.row_groups rg
    WHERE rg.table_oid = rel;

    -- Preserve planner statistics for the rows that are about to move into
    -- parquet. The heap is truncated below, so analyzing after compact would
    -- teach PostgreSQL that every predicate is operating on an empty table.
    EXECUTE format('ANALYZE %s', rel);

    -- Write a new parquet row group from the heap contents.
    SELECT rvbbit.export_to_parquet(rel) INTO written_rows;

    IF keep_heap THEN
        UPDATE rvbbit.tables
        SET shadow_heap_retained = true,
            shadow_heap_dirty = false,
            dirty_has_insert = false,
            dirty_has_update = false,
            dirty_has_delete = false,
            dirty_has_truncate = false
        WHERE table_oid = rel;
        PERFORM rvbbit.clear_table_dirty_markers(rel::oid);
        PERFORM rvbbit.install_shadow_heap_dirty_triggers(rel);
        RAISE NOTICE 'rvbbit.compact: preserving clean shadow heap for %; parquet remains authoritative until the heap is mutated', rel;
    ELSE
        UPDATE rvbbit.tables
        SET shadow_heap_retained = false,
            shadow_heap_dirty = false,
            dirty_has_insert = false,
            dirty_has_update = false,
            dirty_has_delete = false,
            dirty_has_truncate = false
        WHERE table_oid = rel;
        PERFORM rvbbit.clear_table_dirty_markers(rel::oid);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_dirty ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_dirty_insert ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_dirty_update ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_dirty_delete ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_dirty_truncate ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_ctid_update ON %s', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_shadow_heap_ctid_delete ON %s', rel);
        -- Drop the heap data. Source is now empty; parquet is authoritative.
        EXECUTE format('TRUNCATE TABLE %s', rel);
    END IF;

    -- Add the shred columns to the heap relation as PLAIN columns
    -- (not VIRTUAL GENERATED — those get expanded inline by the planner
    -- and the scan never sees them). These columns:
    --   - exist in pg_attribute so Vars to them are valid (rewriter R3 needs this)
    --   - are NULL for any heap row (heap is empty post-compact anyway)
    --   - are populated from parquet by our custom scan
    -- A comment + COMMENT ON COLUMN warns users not to write to them
    -- directly; future versions can add a CHECK constraint.
    FOR shred IN
        SELECT s.column_name, s.source_expr, s.data_type
        FROM rvbbit.shreds s
        WHERE s.table_oid = rel
    LOOP
        BEGIN
            EXECUTE format(
                'ALTER TABLE %s ADD COLUMN IF NOT EXISTS %I %s',
                rel, shred.column_name, shred.data_type
            );
        EXCEPTION WHEN duplicate_column THEN
            -- already added by a prior compact; fine
        END;
    END LOOP;

    RETURN QUERY
        SELECT rg.rg_id, rg.n_rows, rg.n_bytes, heap_size_pre - pg_total_relation_size(rel)
        FROM rvbbit.row_groups rg
        WHERE rg.table_oid = rel
          AND rg.rg_id > max_rg_id_pre
        ORDER BY rg.rg_id DESC
        ;
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit.prepare_cube_refresh(p_name text)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v_dest regclass;
    v_sql text;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.cube:' || p_name, 0)) THEN
        RAISE EXCEPTION 'cube % is busy', p_name USING ERRCODE = '55P03';
    END IF;
    v_dest := to_regclass('cubes.' || quote_ident(p_name));
    SELECT sql INTO v_sql FROM rvbbit.cube_catalog WHERE name = p_name;
    IF v_dest IS NULL OR v_sql IS NULL THEN
        RAISE EXCEPTION 'cube % does not exist or has no definition', p_name;
    END IF;
    IF NOT pg_try_advisory_xact_lock((1380336724::bigint << 32) | v_dest::oid::bigint) THEN
        RAISE EXCEPTION 'snapshot publisher busy: %', v_dest USING ERRCODE = '55P03';
    END IF;
    PERFORM rvbbit._install_cube_source_dirty_tracking(v_sql);
    PERFORM rvbbit.install_shadow_heap_dirty_triggers(v_dest);
    IF NOT rvbbit._shadow_heap_dirty_tracking_matches(v_dest) THEN
        RAISE EXCEPTION 'snapshot dirty tracking needs repair: %', v_dest USING ERRCODE = '55000';
    END IF;
END;
$fn$;
COMMENT ON FUNCTION rvbbit.prepare_cube_refresh(text) IS
'Repair cube source and destination tracking using NOWAIT locks. Commit this short setup transaction before refresh_cube or maintain_cube; never combine it with the expensive refresh transaction.';

CREATE OR REPLACE PROCEDURE rvbbit.refresh_all_cubes(
    p_category        text    DEFAULT NULL,
    p_subcategory     text    DEFAULT NULL,
    p_sleep_seconds   numeric DEFAULT 0.5,
    p_force           boolean DEFAULT false,
    p_max_cubes       integer DEFAULT NULL,
    p_max_age_seconds integer DEFAULT NULL,
    p_retry_errors    boolean DEFAULT false
) LANGUAGE plpgsql AS $fn$
DECLARE
    rec record;
    v_seen integer := 0;
    v_ready boolean;
BEGIN
    FOR rec IN
        SELECT s.name
          FROM rvbbit.cube_refresh_status s
         WHERE (p_category    IS NULL OR s.category    = p_category)
           AND (p_subcategory IS NULL OR s.subcategory = p_subcategory)
           AND s.refresh_mode <> 'manual'
           AND (
                coalesce(p_force, false)
                OR s.recommended_action = 'refresh_cube'
                -- Old maintenance errors must not strand repairable cubes.
                OR NOT rvbbit._shadow_heap_dirty_tracking_matches(
                    to_regclass('cubes.' || quote_ident(s.name)))
                OR s.last_error = 'deadlock detected'
                OR s.last_error LIKE 'snapshot dirty tracking needs repair:%'
                OR s.last_error LIKE 'rvbbit.refresh_cube: source tracking needs repair%'
                OR s.last_error LIKE 'snapshot publisher busy:%'
                OR (
                    coalesce(p_retry_errors, false)
                    AND s.recommended_action = 'fix_error'
                )
                OR (
                    p_max_age_seconds IS NOT NULL
                    AND coalesce(s.seconds_since_refresh, 999999999) >= p_max_age_seconds
                )
           )
         ORDER BY
           CASE WHEN s.source_dirty THEN 0 ELSE 1 END,
           s.source_seconds_dirty DESC NULLS LAST,
           s.seconds_since_refresh DESC NULLS LAST,
           s.name
    LOOP
        EXIT WHEN p_max_cubes IS NOT NULL AND v_seen >= p_max_cubes;
        v_seen := v_seen + 1;
        -- Commit trigger setup before snapshot_load. An error subtransaction
        -- releases any source locks acquired before a later source was busy.
        v_ready := true;
        BEGIN
            PERFORM rvbbit.prepare_cube_refresh(rec.name);
        EXCEPTION
            WHEN lock_not_available THEN
                v_ready := false;
                RAISE NOTICE 'rvbbit.refresh_all_cubes: skipping busy cube %: %', rec.name, SQLERRM;
            WHEN others THEN
                v_ready := false;
                UPDATE rvbbit.cube_control
                   SET last_error = SQLERRM, updated_at = now()
                 WHERE cube_name = rec.name;
        END;
        COMMIT;
        IF NOT v_ready THEN
            CONTINUE;
        END IF;

        BEGIN
            PERFORM rvbbit.refresh_cube(rec.name);
        EXCEPTION
          WHEN lock_not_available THEN
            -- Keep freshness/error state intact so a later sweep can retry.
            RAISE NOTICE 'rvbbit.refresh_all_cubes: skipping busy cube %: %', rec.name, SQLERRM;
          WHEN others THEN
            UPDATE rvbbit.cube_control
               SET last_error = SQLERRM, updated_at = now()
             WHERE cube_name = rec.name;
        END;
        COMMIT;
        IF coalesce(p_sleep_seconds, 0) > 0 THEN
            PERFORM pg_sleep(p_sleep_seconds);
        END IF;
    END LOOP;
END;
$fn$;
