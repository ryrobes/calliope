-- 0509: optional demo guest role.
--
-- Inert until an operator sets CALLIOPE_DEMO_MODE and calls
-- rvbbit.configure_demo_guest. The role is NOLOGIN: the service user
-- SET ROLE into it. It is not a member of any privileged role, and this
-- migration grants it nothing in rvbbit.
--
-- PostgreSQL has no DENY. Privileges granted to PUBLIC still apply, and
-- taking them away from PUBLIC would change every non-demo install, so
-- this file does not do that. rvbbit is a private schema (no USAGE for
-- PUBLIC). The guest therefore cannot see rvbbit tables or call rvbbit
-- functions unless an operator later grants that schema to PUBLIC. The
-- demo query bridge rejects those calls even if that grant exists.
-- docs/DEMO_MODE.md

DO $demo_guest$
BEGIN
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_guest') THEN
            CREATE ROLE calliope_demo_guest NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        END IF;
        COMMENT ON ROLE calliope_demo_guest IS
            'Calliope demo mode. NOLOGIN. Dashboard queries SET ROLE here. No rvbbit grants. docs/DEMO_MODE.md';
        EXECUTE format('GRANT calliope_demo_guest TO %I', session_user);
        EXECUTE 'REVOKE ALL ON SCHEMA rvbbit FROM calliope_demo_guest';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA rvbbit FROM calliope_demo_guest';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA rvbbit FROM calliope_demo_guest';
        EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA rvbbit FROM calliope_demo_guest';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'demo: skipping calliope_demo_guest (needs CREATEROLE)';
    END;
END
$demo_guest$;

ALTER TABLE rvbbit.dashboards
    ADD COLUMN IF NOT EXISTS demo_public boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS rvbbit.demo_query_allowlist (
    dashboard_id bigint NOT NULL,
    version integer NOT NULL,
    sql_hash text NOT NULL,
    canonical_sql text NOT NULL,
    PRIMARY KEY (dashboard_id, version, sql_hash),
    FOREIGN KEY (dashboard_id, version)
        REFERENCES rvbbit.dashboard_versions (dashboard_id, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rvbbit.demo_build_provenance (
    dashboard_id bigint NOT NULL,
    version integer NOT NULL,
    thread_id text,
    models jsonb NOT NULL DEFAULT '[]'::jsonb,
    token_cost numeric,
    token_count bigint,
    wall_ms bigint,
    steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dashboard_id, version),
    FOREIGN KEY (dashboard_id, version)
        REFERENCES rvbbit.dashboard_versions (dashboard_id, version) ON DELETE CASCADE
);

REVOKE ALL ON rvbbit.demo_query_allowlist FROM PUBLIC;
REVOKE ALL ON rvbbit.demo_build_provenance FROM PUBLIC;

CREATE OR REPLACE FUNCTION rvbbit.configure_demo_guest(schemas text[])
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $configure$
DECLARE
    schema_name text;
    database_name text := current_database();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calliope_demo_guest') THEN
        RAISE EXCEPTION 'calliope_demo_guest does not exist';
    END IF;
    IF schemas IS NULL OR array_length(schemas, 1) IS NULL THEN
        RAISE EXCEPTION 'at least one demo schema is required';
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO calliope_demo_guest', database_name);
    EXECUTE 'REVOKE ALL ON SCHEMA rvbbit FROM calliope_demo_guest';
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA rvbbit FROM calliope_demo_guest';
    EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA rvbbit FROM calliope_demo_guest';
    EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA rvbbit FROM calliope_demo_guest';
    FOREACH schema_name IN ARRAY schemas LOOP
        IF schema_name !~ '^[a-z_][a-z0-9_]{0,62}$'
           OR schema_name IN ('rvbbit', 'rvbbit_meta', 'hindsight', 'information_schema', 'pg_catalog', 'public')
           OR schema_name LIKE 'pg\_%' THEN
            RAISE EXCEPTION 'schema % is not a demo schema', schema_name;
        END IF;
        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', schema_name);
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO calliope_demo_guest', schema_name);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO calliope_demo_guest', schema_name);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO calliope_demo_guest',
            schema_name
        );
    END LOOP;
    RETURN 'calliope_demo_guest can read ' || array_to_string(schemas, ',');
END
$configure$;

REVOKE ALL ON FUNCTION rvbbit.configure_demo_guest(text[]) FROM PUBLIC;

COMMENT ON FUNCTION rvbbit.configure_demo_guest(text[]) IS
    'Grant the demo guest CONNECT plus USAGE and SELECT on the named schemas only. Refuses rvbbit and other system schemas. docs/DEMO_MODE.md';
