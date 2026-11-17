-- Ordinary table owners must not need writes to RVBBIT's internal catalogs
-- just to create/drop their own tables. These no-argument event-trigger
-- functions consume PostgreSQL's event object identities, not user SQL text.
-- Run their housekeeping as the existing trusted schema owner, with no
-- user-writable schemas on search_path. Newer accounting triggers already do so.
DO $workspace_housekeeping$
DECLARE hook record;
BEGIN
    FOR hook IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rvbbit'
          AND p.proname IN ('on_create_table','on_drop_table','reinstall_partition_dirty_triggers_on_alter')
          AND p.pronargs=0 AND p.prorettype='event_trigger'::regtype
          AND p.proowner=n.nspowner
          AND (NOT p.prosecdef OR NOT coalesce(p.proconfig @> ARRAY['search_path=pg_catalog, rvbbit'],false))
    LOOP
        EXECUTE format('ALTER FUNCTION %s SECURITY DEFINER',hook.signature);
        EXECUTE format('ALTER FUNCTION %s SET search_path TO pg_catalog, rvbbit',hook.signature);
    END LOOP;
END
$workspace_housekeeping$;
