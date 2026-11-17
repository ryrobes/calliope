-- 0485: a pooled connection notices when a table is accelerated or stops being.
--
-- Each backend caches, per table, whether rvbbit accelerates it. The cache was
-- never dropped, so a long-lived connection (an app's pool, DataRabbit) that had
-- planned a query on a table before rvbbit.enable_table kept scanning the heap
-- and recording no reads of it; one that had seen it before rvbbit.disable_table
-- kept routing to the accelerated copy. The cache is now dropped whenever the
-- cross-backend scan epoch moves. Rebuilds already bump it; this trigger bumps
-- it when a table is registered, enabled, disabled or removed.

-- C binding for existing installations ($libdir literal: migrate() runs via SPI).
-- Guarded: a library that predates the symbol skips the trigger too.
DO $$ BEGIN
    BEGIN
        CREATE OR REPLACE FUNCTION rvbbit.scan_epoch_bump_on_commit()
        RETURNS void
        LANGUAGE c VOLATILE
        AS '$libdir/pg_rvbbit', 'scan_epoch_bump_on_commit_wrapper';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'rvbbit: scan_epoch_bump_on_commit binding skipped (%); pooled connections keep their table cache until the extension library is updated', SQLERRM;
    END;
END $$;

DO $$ BEGIN
    IF to_regprocedure('rvbbit.scan_epoch_bump_on_commit()') IS NULL THEN
        RETURN;
    END IF;
    REVOKE EXECUTE ON FUNCTION rvbbit.scan_epoch_bump_on_commit() FROM PUBLIC;

    CREATE OR REPLACE FUNCTION rvbbit._tables_registry_changed()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, rvbbit, pg_temp
    AS $fn$
    BEGIN
        PERFORM rvbbit.scan_epoch_bump_on_commit();
        RETURN NULL;
    END
    $fn$;

    DROP TRIGGER IF EXISTS tables_registry_rows_changed ON rvbbit.tables;
    CREATE TRIGGER tables_registry_rows_changed
        AFTER INSERT OR DELETE ON rvbbit.tables
        FOR EACH STATEMENT EXECUTE FUNCTION rvbbit._tables_registry_changed();

    DROP TRIGGER IF EXISTS tables_registry_enabled_changed ON rvbbit.tables;
    CREATE TRIGGER tables_registry_enabled_changed
        AFTER UPDATE OF acceleration_enabled ON rvbbit.tables
        FOR EACH ROW
        WHEN (OLD.acceleration_enabled IS DISTINCT FROM NEW.acceleration_enabled)
        EXECUTE FUNCTION rvbbit._tables_registry_changed();
END $$;
