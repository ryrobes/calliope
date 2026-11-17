-- 0483: SECURITY DEFINER functions are safe to call.
--
-- These functions run as the extension's owner, a superuser. Without a fixed
-- search_path, a caller who can create objects on its own search_path could
-- substitute a table or function that the body names without a schema, and
-- have it run with the owner's rights. Pin every one of them (pg_temp last, so
-- temporary objects can never shadow anything).
DO $harden$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        WHERE p.pronamespace = 'rvbbit'::regnamespace
          AND p.prosecdef
          AND NOT EXISTS (
              SELECT 1 FROM unnest(coalesce(p.proconfig, '{}'::text[])) c
              WHERE c LIKE 'search_path=%'
          )
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, rvbbit, public, pg_temp', f);
    END LOOP;
END
$harden$;

-- Enrolling a login role (and optionally making it an rvbbit_admin) and
-- opening a schema to the guest are DBA decisions. Both ran for any role with
-- USAGE on rvbbit; burrow_enroll also grants the enrolled role to the caller,
-- so it let such a role take on any non-superuser login role. Owner only now;
-- a DBA can still GRANT EXECUTE to a role they trust with it.
REVOKE EXECUTE ON FUNCTION rvbbit.burrow_enroll(text, boolean) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION rvbbit.burrow_grant_guest(text) FROM PUBLIC;
