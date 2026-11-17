-- 0332: column-level DDL on an accelerated table invalidates its Parquet.
--
-- Chaos test (schema drift on the live MariaDB source): dlt added two columns
-- to a mirrored destination with ALTER TABLE … ADD COLUMN. The row-level dirty
-- triggers never fire for DDL, so the accelerator stayed "authoritative and
-- clean", reads kept routing to Parquet, and every query touching the table —
-- including the mirror's own publication diff — failed with
-- "column 'extra_1' not found in parquet schema". A Salesforce custom field
-- would do exactly the same to a pilot.
--
-- Column-level DDL (ADD / DROP / ALTER … TYPE / RENAME) on a registered table
-- that has Parquet — detected by comparing the table's columns with the newest
-- row group's column inventory — now marks the table dirty at truncate level: reads fall
-- back to the heap (which holds the new column's values), the freshness tick
-- schedules a full rebuild, and the next Parquet carries the new shape.
-- Reloption changes, constraints, indexes and comments do not trip it.

CREATE OR REPLACE FUNCTION rvbbit._accel_mark_schema_changed(rel oid, ddl_tag text)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog, rvbbit
AS $$
DECLARE
    marked boolean := false;
BEGIN
    IF rel IS NULL OR NOT EXISTS (
        SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = rel AND t.shadow_heap_retained
    ) THEN
        RETURN false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM rvbbit.row_groups rg WHERE rg.table_oid = rel) THEN
        RETURN false;   -- nothing accelerated yet; the first baseline will use the new shape
    END IF;

    INSERT INTO rvbbit.table_dirty_markers (table_oid, shard, dirty_op, marked_at)
    VALUES (rel, mod(pg_backend_pid(), 1024)::smallint, 'T', clock_timestamp())
    ON CONFLICT (table_oid, shard, dirty_op) DO NOTHING;

    BEGIN
        PERFORM 1 FROM rvbbit.tables WHERE table_oid = rel FOR NO KEY UPDATE NOWAIT;
        UPDATE rvbbit.tables
           SET shadow_heap_dirty = true,
               dirty_has_truncate = true,
               last_write_at = clock_timestamp(),
               dirty_since = CASE WHEN shadow_heap_dirty THEN dirty_since ELSE clock_timestamp() END
         WHERE table_oid = rel;
        marked := true;
    EXCEPTION WHEN lock_not_available THEN
        marked := false;   -- a worker holds it; the marker row still forces the rebuild
    END;

    PERFORM rvbbit._accel_log_event(
        nextval('rvbbit.accel_sweep_id_seq'), 'freshness', 'table_started',
        rel, rel::regclass::text, NULL, 'schema_change', ddl_tag, 'dirty',
        p_details => jsonb_build_object('ddl', ddl_tag, 'effect', 'parquet invalidated; full rebuild scheduled'));
    RETURN marked;
END;
$$;

COMMENT ON FUNCTION rvbbit._accel_mark_schema_changed(oid, text) IS
    'Mark an accelerated table dirty at truncate level after column-level DDL so reads use the heap until the freshness tick rebuilds Parquet with the new shape.';

-- Has the table's column set drifted from the newest Parquet row group?
-- pg_event_trigger_ddl_commands() reports ADD COLUMN and ALTER … TYPE only as
-- object_type 'table', so the trigger compares shapes instead of trusting the
-- subcommand list: row_groups.stats carries the Parquet column names.
CREATE OR REPLACE FUNCTION rvbbit._accel_schema_drifted(rel oid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, rvbbit
AS $$
DECLARE
    parquet_cols text[];
    table_cols text[];
    ddl text := coalesce(current_query(), '');
BEGIN
    SELECT array_agg(DISTINCT s ->> 'name')
      INTO parquet_cols
      FROM rvbbit.row_groups rg
     CROSS JOIN LATERAL jsonb_array_elements(
              CASE WHEN jsonb_typeof(rg.stats) = 'array' THEN rg.stats ELSE '[]'::jsonb END) s
     WHERE rg.table_oid = rel
       AND rg.rg_id = (SELECT max(r2.rg_id) FROM rvbbit.row_groups r2 WHERE r2.table_oid = rel);
    SELECT array_agg(a.attname::text)
      INTO table_cols
      FROM pg_attribute a
     WHERE a.attrelid = rel AND a.attnum > 0 AND NOT a.attisdropped;

    IF parquet_cols IS NULL OR cardinality(parquet_cols) = 0 THEN
        -- No column inventory in the metadata: fall back to the statement text.
        RETURN ddl ~* '\m(add|drop|rename)\s+(column\M|\S)'
            OR ddl ~* '\malter\s+(column\s+)?\S+\s+(set\s+data\s+)?type\M';
    END IF;
    RETURN EXISTS (SELECT 1 FROM unnest(table_cols) c WHERE c <> ALL (parquet_cols))
        OR EXISTS (SELECT 1 FROM unnest(parquet_cols) c WHERE c <> ALL (table_cols))
        OR ddl ~* '\malter\s+(column\s+)?\S+\s+(set\s+data\s+)?type\M';
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit._accel_schema_ddl_changed()
RETURNS event_trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $$
DECLARE
    cmd record;
    seen oid[] := '{}';
BEGIN
    FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
        IF cmd.command_tag = 'ALTER TABLE'
           AND cmd.object_type IN ('table', 'table column')
           AND NOT (cmd.objid = ANY (seen)) THEN
            seen := seen || cmd.objid;
            IF rvbbit._accel_schema_drifted(cmd.objid) THEN
                PERFORM rvbbit._accel_mark_schema_changed(cmd.objid, cmd.command_tag);
            END IF;
        END IF;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    -- Never let observability break user DDL.
    RAISE WARNING 'rvbbit: schema-change accounting skipped: %', SQLERRM;
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit._accel_schema_column_dropped()
RETURNS event_trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, rvbbit
AS $$
DECLARE
    obj record;
BEGIN
    FOR obj IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
        IF obj.object_type = 'table column' THEN
            PERFORM rvbbit._accel_mark_schema_changed(obj.objid, 'DROP COLUMN');
        END IF;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'rvbbit: schema-change accounting skipped: %', SQLERRM;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'rvbbit_accel_schema_ddl_changed') THEN
        CREATE EVENT TRIGGER rvbbit_accel_schema_ddl_changed
            ON ddl_command_end
            WHEN TAG IN ('ALTER TABLE')
            EXECUTE FUNCTION rvbbit._accel_schema_ddl_changed();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'rvbbit_accel_schema_column_dropped') THEN
        CREATE EVENT TRIGGER rvbbit_accel_schema_column_dropped
            ON sql_drop
            WHEN TAG IN ('ALTER TABLE')
            EXECUTE FUNCTION rvbbit._accel_schema_column_dropped();
    END IF;
END;
$$;
