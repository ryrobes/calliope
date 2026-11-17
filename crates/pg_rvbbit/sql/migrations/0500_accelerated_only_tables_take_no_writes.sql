-- 0500: an accelerated-only table takes no writes.
--
-- The legacy rvbbit.compact(rel, keep_heap => false) moves a table's rows into
-- its accelerated copy and empties the heap, leaving nothing to record writes.
-- Every reader treats a heap that has rows and was not retained as the whole
-- table, so one INSERT afterwards made every read return only the new rows
-- (count(*) went from 50,000 to 1), and an UPDATE or DELETE of a moved row did
-- nothing. Such a table now refuses INSERT, UPDATE, DELETE, TRUNCATE, MERGE and
-- COPY with an error that says why; reads are unchanged. To write to it, copy
-- it into a new table: a plain SELECT of it reads the accelerated copy.
--
-- Its empty heap cannot become its source again either. A refresh (or
-- compact(rel, keep_heap => true)) marked it retained, after which a rebuild
-- from the heap deleted every row and lookups through the heap's indexes found
-- nothing; disable_table left only the empty heap to read. rvbbit.tables
-- refuses both changes for an accelerated-only table. rebuild_acceleration and
-- retire_acceleration already refused a heap that was not retained.
--
-- Tables compacted this way before 0500 get the guard if their heap is still
-- empty. One that was written to since reads only those writes; running
-- compact(rel, keep_heap => false) again moves them into the accelerated copy
-- (all of them, so this is right when they were inserts) and guards the table.

CREATE OR REPLACE FUNCTION rvbbit.is_accelerated_only(rel regclass)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM pg_trigger
         WHERE tgrelid = rel
           AND tgname = 'rvbbit_accelerated_only'
           AND NOT tgisinternal
    )
$$;
COMMENT ON FUNCTION rvbbit.is_accelerated_only(regclass) IS
'True when rvbbit.compact(rel, keep_heap => false) moved the table''s rows out of its heap: its accelerated copy is its only source, and it takes no writes.';

CREATE OR REPLACE FUNCTION rvbbit.refuse_accelerated_only_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% on % refused: the table is accelerated-only', TG_OP, TG_RELID::regclass
        USING ERRCODE = '55000',
              DETAIL = 'rvbbit.compact(..., keep_heap => false) moved its rows out of the heap '
                       'into its accelerated copy, so a write could not be read back with them.',
              HINT = format(
                  'Copy it into a new table to write to it, e.g. CREATE TABLE ... AS SELECT * FROM %s.',
                  TG_RELID::regclass
              );
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit._install_accelerated_only_guard(rel regclass)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF rvbbit.is_accelerated_only(rel) THEN
        RETURN;
    END IF;
    EXECUTE format(
        'CREATE TRIGGER rvbbit_accelerated_only
             BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON %s
             FOR EACH STATEMENT
             EXECUTE FUNCTION rvbbit.refuse_accelerated_only_write()',
        rel
    );
    -- Also under session_replication_role = 'replica' (logical-replication
    -- apply, pg_restore --disable-triggers), like the dirty-tracking triggers.
    EXECUTE format('ALTER TABLE %s ENABLE ALWAYS TRIGGER rvbbit_accelerated_only', rel);
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit._keep_accelerated_only_source()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF rvbbit.is_accelerated_only(NEW.table_oid::regclass) THEN
        RAISE EXCEPTION '% is accelerated-only: its heap holds none of its rows, so it cannot be %',
            NEW.table_oid::regclass,
            CASE WHEN NEW.shadow_heap_retained AND NOT OLD.shadow_heap_retained
                 THEN 'refreshed or rebuilt from its heap'
                 ELSE 'disabled' END
            USING ERRCODE = '55000',
                  HINT = format(
                      'Copy it into a new table to change how it is stored, e.g. CREATE TABLE ... AS SELECT * FROM %s.',
                      NEW.table_oid::regclass
                  );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS rvbbit_keep_accelerated_only_source ON rvbbit.tables;
CREATE TRIGGER rvbbit_keep_accelerated_only_source
    BEFORE UPDATE OF shadow_heap_retained, acceleration_enabled ON rvbbit.tables
    FOR EACH ROW
    WHEN ((NEW.shadow_heap_retained AND NOT OLD.shadow_heap_retained)
          OR (coalesce(OLD.acceleration_enabled, true)
              AND NOT coalesce(NEW.acceleration_enabled, true)))
    EXECUTE FUNCTION rvbbit._keep_accelerated_only_source();

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
    IF keep_heap AND rvbbit.is_accelerated_only(rel) THEN
        RAISE EXCEPTION 'rvbbit.compact: % is accelerated-only; its heap holds none of its rows, so it cannot be kept as its source', rel
            USING ERRCODE = '55000';
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
        -- Drop the heap data. Source is now empty; parquet is authoritative,
        -- and nothing records a write, so the table takes none from here on.
        IF rvbbit.is_accelerated_only(rel) THEN
            EXECUTE format('DROP TRIGGER rvbbit_accelerated_only ON %s', rel);
        END IF;
        EXECUTE format('TRUNCATE TABLE %s', rel);
        PERFORM rvbbit._install_accelerated_only_guard(rel);
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

DO $migration$
DECLARE
    rel regclass;
    written text[] := ARRAY[]::text[];
BEGIN
    FOR rel IN
        SELECT t.table_oid::regclass
          FROM rvbbit.tables t
          JOIN pg_class c ON c.oid = t.table_oid
         WHERE NOT t.shadow_heap_retained
           AND coalesce(t.acceleration_enabled, true)
           AND c.relkind = 'r'
           AND EXISTS (SELECT 1 FROM rvbbit.row_groups g WHERE g.table_oid = t.table_oid)
    LOOP
        IF pg_relation_size(rel) = 0 THEN
            PERFORM rvbbit._install_accelerated_only_guard(rel);
        ELSE
            written := written || rel::text;
        END IF;
    END LOOP;
    IF cardinality(written) > 0 THEN
        RAISE NOTICE 'rvbbit 0500: % accelerated-only table(s) were written to after their rows left the heap and read only those writes: %. rvbbit.compact(<table>, keep_heap => false) moves the writes into the accelerated copy and guards the table.',
            cardinality(written), array_to_string(written, ', ');
    END IF;
END
$migration$;
