-- 0490: an UPDATE or DELETE that changes no rows leaves an accelerated table clean.
--
-- The dirty markers are statement-level triggers, so they fired for statements
-- that matched nothing: an incremental ETL's "DELETE ... USING todo" with an
-- empty todo, "UPDATE ... WHERE last_seen_at < $1" when nothing qualifies, an
-- upsert that found no conflicts. The table then read from the heap until the
-- next refresh, and a keyless table with a DELETE/UPDATE marker can only be
-- refreshed by a full rebuild: on the hub, a 94k-row table was rebuilt every
-- 10-minute cycle although its rows had not changed.
--
-- The statement trigger now returns early when it can prove the statement
-- changed nothing:
--   * keyed tables: the trigger already receives the old rows as a transition
--     table (rvbbit_old_rows); it is empty. (Until this release's library,
--     pg_rvbbit's query rewriter hid it from a session's first query; when it
--     can't be read the table is marked dirty as before.)
--   * keyless (ctid) tables: the per-row tombstone trigger fires, before the
--     statement trigger, for every updated/deleted row and now notes it in a
--     transaction-local setting; it never fired.
-- Anything it cannot prove keeps the old behaviour: INSERT and TRUNCATE (no
-- row-level evidence), tables with inheritance or partition children, a row
-- trigger that is disabled, conditional or may not fire under the current
-- session_replication_role.

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    ---------------------------------------------------------------- statement
    definition := pg_get_functiondef('rvbbit.mark_shadow_heap_dirty()'::regprocedure);
    IF position('0490: a statement that changed no rows' IN definition) = 0 THEN
        needle := $needle$    marker_shard smallint;
BEGIN$needle$;
        replacement := $replacement$    marker_shard smallint;
    old_rows_name name;
    rows_changed boolean;
    row_flag text;
BEGIN
    -- 0490: a statement that changed no rows leaves the table clean, when that
    -- can be proven (see the migration); otherwise mark it as before.
    IF TG_OP IN ('UPDATE', 'DELETE')
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = TG_RELID) THEN
        SELECT t.tgoldtable INTO old_rows_name
          FROM pg_catalog.pg_trigger t
         WHERE t.tgrelid = TG_RELID AND t.tgname = TG_NAME;
        IF old_rows_name IS NOT NULL THEN
            BEGIN
                EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', old_rows_name) INTO rows_changed;
            EXCEPTION WHEN undefined_table THEN
                -- Can't see it (a pg_rvbbit library older than this release
                -- hides it from a session's first such query): assume changed.
                rows_changed := true;
            END;
            IF NOT rows_changed THEN
                RETURN NULL;
            END IF;
        ELSIF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger t
             WHERE t.tgrelid = TG_RELID
               AND t.tgname = 'rvbbit_shadow_heap_ctid_' || lower(TG_OP)
               AND t.tgfoid = 'rvbbit.mark_shadow_heap_ctid_tombstone()'::regprocedure
               AND t.tgqual IS NULL
               AND (t.tgenabled = 'A'
                    OR (t.tgenabled = 'O' AND current_setting('session_replication_role') <> 'replica')
                    OR (t.tgenabled = 'R' AND current_setting('session_replication_role') = 'replica'))
        ) THEN
            row_flag := 'rvbbit.rows_changed_' || TG_RELID::oid::text;
            IF coalesce(current_setting(row_flag, true), '') <> 'y' THEN
                RETURN NULL;
            END IF;
            PERFORM set_config(row_flag, '', true);
        END IF;
    END IF;
$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0490 could not find the dirty trigger''s declarations';
        END IF;
        definition := replace(definition, needle, replacement);
        EXECUTE definition;
    END IF;

    ---------------------------------------------------------------------- row
    definition := pg_get_functiondef('rvbbit.mark_shadow_heap_ctid_tombstone()'::regprocedure);
    IF position('0490: note that this statement changed a row' IN definition) = 0 THEN
        needle := $needle$BEGIN
    IF nullif(current_setting('rvbbit.snapshot_replace_target', true), '') = TG_RELID::oid::text THEN$needle$;
        replacement := $replacement$BEGIN
    -- 0490: note that this statement changed a row, for the statement-level
    -- dirty trigger that fires after the row triggers.
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM set_config('rvbbit.rows_changed_' || TG_RELID::oid::text, 'y', true);
    END IF;
    IF nullif(current_setting('rvbbit.snapshot_replace_target', true), '') = TG_RELID::oid::text THEN$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0490 could not find the start of the ctid tombstone trigger';
        END IF;
        definition := replace(definition, needle, replacement);
        EXECUTE definition;
    END IF;
END
$migration$;
