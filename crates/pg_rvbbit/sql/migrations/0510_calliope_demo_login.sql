-- 0510: the demo guest is a real login, not a SET ROLE on the superuser.
--
-- SET ROLE from the service connection is not a boundary: the statement can
-- set_config('role', 'postgres', false) and become the session user again.
-- Guest queries connect as calliope_demo_login. That role is not a superuser
-- and is not a member of postgres, so it cannot become one.
--
-- calliope_demo_guest stays NOLOGIN and holds the SELECT grants.
-- calliope_demo_login INHERITs those grants. The service sets its password
-- from CALLIOPE_DEMO_GUEST_PASSWORD (rvbbit.configure_demo_login).
--
-- TEMP is revoked from PUBLIC, then granted back to every existing login
-- except the demo login. Superusers never needed the grant. A login role
-- created later does not get TEMP until someone grants it.
--
-- rvbbit functions are executable by PUBLIC. The demo login still cannot
-- call them unless it has USAGE on schema rvbbit, which it does not.
-- docs/DEMO_MODE.md

DO $demo_login$
BEGIN
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_login') THEN
            CREATE ROLE calliope_demo_login
                LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
        END IF;
        ALTER ROLE calliope_demo_login
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
        ALTER ROLE calliope_demo_login SET default_transaction_read_only = on;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_guest') THEN
            GRANT calliope_demo_guest TO calliope_demo_login;
        END IF;
        COMMENT ON ROLE calliope_demo_login IS
            'Calliope demo mode login. Not a superuser. Inherits calliope_demo_guest. docs/DEMO_MODE.md';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'demo: skipping calliope_demo_login (needs CREATEROLE)';
    END;
END
$demo_login$;

DO $demo_temp$
DECLARE
    database_name text := current_database();
    role_name text;
BEGIN
    EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC', database_name);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_login') THEN
        EXECUTE format('REVOKE TEMP ON DATABASE %I FROM calliope_demo_login', database_name);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_guest') THEN
        EXECUTE format('REVOKE TEMP ON DATABASE %I FROM calliope_demo_guest', database_name);
    END IF;
    FOR role_name IN
        SELECT rolname FROM pg_roles
        WHERE rolcanlogin AND rolname <> 'calliope_demo_login'
    LOOP
        EXECUTE format('GRANT TEMP ON DATABASE %I TO %I', database_name, role_name);
    END LOOP;
END
$demo_temp$;

DO $demo_stats$
DECLARE
    stats regclass;
    role_name text;
BEGIN
    stats := to_regclass('public.pg_stat_statements');
    IF stats IS NULL THEN
        stats := to_regclass('pg_stat_statements');
    END IF;
    IF stats IS NOT NULL THEN
    EXECUTE format('REVOKE ALL ON %s FROM PUBLIC', stats);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_login') THEN
        EXECUTE format('REVOKE ALL ON %s FROM calliope_demo_login', stats);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_guest') THEN
        EXECUTE format('REVOKE ALL ON %s FROM calliope_demo_guest', stats);
    END IF;
    FOR role_name IN
        SELECT rolname FROM pg_roles
        WHERE rolcanlogin AND rolname <> 'calliope_demo_login'
    LOOP
        EXECUTE format('GRANT SELECT ON %s TO %I', stats, role_name);
    END LOOP;
    END IF;
END
$demo_stats$;

CREATE TABLE IF NOT EXISTS rvbbit.demo_public_metrics (
    metric_name text PRIMARY KEY,
    published_at timestamptz NOT NULL DEFAULT now(),
    published_by text
);
REVOKE ALL ON rvbbit.demo_public_metrics FROM PUBLIC;

COMMENT ON TABLE rvbbit.demo_public_metrics IS
    'Metric names an administrator has explicitly published to the demo. Guests also see metrics referenced by a public dashboard.';

CREATE OR REPLACE FUNCTION rvbbit.configure_demo_login(p_password text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $configure_login$
BEGIN
    IF p_password IS NULL OR length(p_password) < 16 THEN
        RAISE EXCEPTION 'demo login password must be at least 16 characters';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_login') THEN
        RAISE EXCEPTION 'calliope_demo_login does not exist; apply migration 0510';
    END IF;
    EXECUTE format('ALTER ROLE calliope_demo_login PASSWORD %L', p_password);
    RETURN 'calliope_demo_login password updated';
END
$configure_login$;

REVOKE ALL ON FUNCTION rvbbit.configure_demo_login(text) FROM PUBLIC;

COMMENT ON FUNCTION rvbbit.configure_demo_login(text) IS
    'Set the demo login password. Owner only. The Calliope service calls this from CALLIOPE_DEMO_GUEST_PASSWORD. docs/DEMO_MODE.md';
