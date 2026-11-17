-- 0496_new_data_shows_up — data a person pushes is visible to Callie's tools
-- without anyone registering it, and the appliance's own schemas never are.
--
-- Rooms test stack, 2026-10-20: a new shop schema stayed invisible to find,
-- search_data and the Meaning sweep until an operator inserted a
-- business_sources row, then waited for the nightly catalog crawl. Meanwhile
-- Calliope's own clover_research/clover_telemetry schemas had been defaulted
-- to business, so Callie described the warehouse as "only system telemetry
-- and memory tables". The registry stays (operators can still exclude a
-- schema), but it now works as a denylist:
--   1. rvbbit.is_system_schema() is the one list of the appliance's own
--      schemas. populate, crawl requests, the catalog crawl exclusion
--      (_catalog_excluded_schemas, used by every crawl and sweep path) and
--      business_source_role() all read it.
--   2. business_source_role() answers business for a schema with no row
--      unless it is a system schema; populate re-classifies clover_* rows it
--      had defaulted (an operator's decision always stands).
--   3. A DDL event trigger registers a new schema and queues a schema-scoped
--      catalog crawl whenever a table, view or materialized view is created or
--      altered outside the system schemas. The 2-minute crawl tick then makes it
--      findable; the 10-minute value index and Meaning sweeps follow.
--   4. The crawl tick reads the live-tuple counter as well as reltuples, so a
--      small never-analyzed upload no longer waits 30 minutes for rows.
--   5. The same trigger flags Calliope's routes for a re-check when a table
--      they name changes, and every dead-end route when new data appears.

CREATE OR REPLACE FUNCTION rvbbit.is_system_schema(p_schema text)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT p_schema IS NULL OR btrim(p_schema) = ''
        OR p_schema IN ('information_schema', 'rvbbit', 'rvbbit_meta', 'hindsight', 'seeds', 'parity',
                        'meaning_labels', 'datarabbit', 'dagster_operational', 'fivetran_metadata', 'n8n',
                        'cron', 'pgsodium', 'vault')
        OR p_schema LIKE 'pg\_%'          -- pg_catalog, pg_toast, pg_temp_*
        OR p_schema LIKE '\_dlt\_%'       -- dlt's load bookkeeping
        OR p_schema LIKE 'clover\_%'      -- Clover's research, telemetry and routing tables
$fn$;

COMMENT ON FUNCTION rvbbit.is_system_schema(text) IS
    'The appliance''s own schemas: never business data, never crawled, hidden from Callie''s tools. Everything else a person or mirror creates is business until an operator says otherwise. cubes is not one: its tables are user-facing, crawled and previewed, though populate keeps it out of Meaning.';

-- The crawl exclusion every crawl and sweep path reads: the operator setting
-- (default dagster_operational) plus every system schema that exists.
CREATE OR REPLACE FUNCTION rvbbit._catalog_excluded_schemas()
RETURNS text[] LANGUAGE sql STABLE AS $fn$
    SELECT array(
        SELECT DISTINCT x FROM (
            SELECT unnest(CASE
                WHEN to_regclass('rvbbit.settings') IS NULL THEN ARRAY['dagster_operational']::text[]
                WHEN EXISTS (SELECT 1 FROM rvbbit.settings WHERE key = 'catalog_crawl_exclude_schemas')
                    THEN coalesce((SELECT array_agg(v.x)
                                     FROM rvbbit.settings s,
                                          LATERAL jsonb_array_elements_text(s.value) AS v(x)
                                    WHERE s.key = 'catalog_crawl_exclude_schemas'), ARRAY[]::text[])
                ELSE ARRAY['dagster_operational']::text[]
            END)
            UNION ALL
            -- pg_* (catalog, toast, every temp schema) is skipped by each crawl path already.
            SELECT n.nspname::text FROM pg_namespace n
             WHERE rvbbit.is_system_schema(n.nspname) AND n.nspname NOT LIKE 'pg\_%'
        ) excluded(x)
    )
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_source_role(p_schema text)
RETURNS text LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((SELECT role FROM rvbbit.business_sources WHERE schema_name = btrim(p_schema)),
                    CASE WHEN rvbbit.is_system_schema(btrim(p_schema)) OR btrim(p_schema) = 'cubes'
                         THEN 'plumbing' ELSE 'business' END);
$fn$;

-- Same as 0448 except the internal list is rvbbit.is_system_schema() (plus cubes,
-- whose tables are user-facing but are cube outputs, not business sources).
CREATE OR REPLACE FUNCTION rvbbit.business_sources_populate()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_schema text;
    v_role text;
    v_system text;
    v_adapter text;
    v_by text;
    v_note text;
    v_has_connectors boolean := to_regclass('fivetran_metadata.connector') IS NOT NULL;
    v_connector_sql text;
    v_connector record;
    v_touched integer := 0;
BEGIN
    IF v_has_connectors THEN
        v_connector_sql := 'SELECT connector_name::text AS connector_name, connector_type_id::text AS connector_type_id FROM fivetran_metadata.connector';
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema='fivetran_metadata' AND table_name='connector' AND column_name='_fivetran_deleted') THEN
            v_connector_sql := v_connector_sql || ' WHERE NOT coalesce(_fivetran_deleted,false)';
        END IF;
    END IF;

    FOR v_schema IN
        SELECT n.nspname FROM pg_namespace n
         WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
           AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid AND c.relkind IN ('r','p','m'))
         ORDER BY 1
    LOOP
        v_role := NULL; v_system := NULL; v_adapter := NULL; v_by := 'populate'; v_note := NULL;

        -- The appliance's own schemas and internal tooling: plumbing by definition.
        IF rvbbit.is_system_schema(v_schema) OR v_schema = 'cubes' THEN
            v_role := 'plumbing';
            v_system := CASE WHEN v_schema LIKE '\_dlt\_%' THEN 'dlt' ELSE v_schema END;
            v_by := 'internal_list';
        END IF;

        -- The appliance's own mirror jobs: a destination schema is a business
        -- mirror of the named connection.
        IF v_role IS NULL AND to_regclass('rvbbit.mirror_jobs') IS NOT NULL THEN
            SELECT j.connection_name, 'mirror:'||coalesce(c.dialect, 'unknown')
              INTO v_system, v_adapter
              FROM rvbbit.mirror_jobs j
              LEFT JOIN rvbbit.mirror_connections c USING (connection_name)
             WHERE j.destination_schema = v_schema
             ORDER BY j.enabled DESC, j.job_name
             LIMIT 1;
            IF v_system IS NOT NULL THEN
                v_role := 'business'; v_by := 'mirror_jobs';
            END IF;
        END IF;

        -- The Fivetran connector map: the mirror's own record of what each schema is.
        IF v_role IS NULL AND v_has_connectors THEN
            FOR v_connector IN EXECUTE v_connector_sql LOOP
                IF v_schema = v_connector.connector_name
                   OR v_schema LIKE v_connector.connector_name || '\_%' THEN
                    IF v_connector.connector_type_id = 'fivetran_log' THEN
                        v_role := 'plumbing';
                    ELSIF v_schema <> v_connector.connector_name
                          AND (v_schema LIKE '%\_source' OR v_schema LIKE '%\_staging'
                               OR v_schema LIKE '%\_stg\_%' OR v_schema LIKE 'stg\_%') THEN
                        v_role := 'derived';
                    ELSE
                        v_role := 'business';
                    END IF;
                    v_system := v_connector.connector_type_id;
                    v_adapter := 'fivetran:' || v_connector.connector_type_id;
                    v_by := 'connector_map';
                    EXIT;
                END IF;
            END LOOP;
        END IF;

        -- Transformation layers (dbt-style) are derived: excavated, attributed upstream.
        IF v_role IS NULL AND (v_schema IN ('marts','reporting','intermediate','staging')
                               OR v_schema LIKE '%\_staging' OR v_schema LIKE 'stg\_%'
                               OR v_schema LIKE '%\_source' OR v_schema LIKE '%\_stg\_%'
                               OR v_schema LIKE '%\_marts' OR v_schema LIKE '%\_intermediate') THEN
            v_role := 'derived'; v_system := 'transform'; v_by := 'heuristic';
        END IF;

        IF v_role IS NULL THEN
            -- Tables a person put here are business data until someone says
            -- otherwise. 'unknown' stalled every appliance whose uploads
            -- landed in public or a fresh schema (test-hosted1, 2026-09-21:
            -- nothing analyzed for a day). The default is written as its
            -- own decision so an operator can still set plumbing, and it
            -- never overwrites a row that already exists (see the upsert).
            v_role := 'business'; v_by := 'default';
            v_note := 'defaulted to business: set role to plumbing (decided_by operator) to keep it out of Meaning';
            -- A hint, never a decision: a connector whose leading token matches.
            IF v_has_connectors THEN
                FOR v_connector IN EXECUTE v_connector_sql LOOP
                    IF split_part(v_schema,'_',1) <> '' AND split_part(v_schema,'_',1) = split_part(v_connector.connector_name,'_',1) THEN
                        v_note := 'possibly connector '||v_connector.connector_name||' ('||v_connector.connector_type_id||') — set role and decided_by=''operator'' to confirm';
                        EXIT;
                    END IF;
                END LOOP;
            END IF;
        END IF;

        INSERT INTO rvbbit.business_sources(schema_name, source_system, role, adapter, decided_by, note)
        VALUES (v_schema, v_system, v_role, v_adapter, v_by, v_note)
        ON CONFLICT (schema_name) DO UPDATE
           SET source_system=coalesce(excluded.source_system, rvbbit.business_sources.source_system),
               adapter=coalesce(excluded.adapter, rvbbit.business_sources.adapter),
               role=excluded.role, decided_by=excluded.decided_by,
               note=coalesce(excluded.note, rvbbit.business_sources.note),
               updated_at=now()
         WHERE rvbbit.business_sources.decided_by <> 'operator'
           -- a default never revises a row that exists: the deterministic
           -- classes (internal list, mirror jobs, connectors) still refresh
           AND excluded.decided_by <> 'default';
        IF FOUND THEN v_touched := v_touched + 1; END IF;
    END LOOP;
    RETURN jsonb_build_object(
        'status','complete','touched',v_touched,
        'roles',(SELECT coalesce(jsonb_object_agg(role, n),'{}'::jsonb)
                   FROM (SELECT role, count(*) AS n FROM rvbbit.business_sources GROUP BY role) s));
END
$fn$;

-- Same as 0323 except system schemas come from rvbbit.is_system_schema().
CREATE OR REPLACE FUNCTION rvbbit.request_catalog_crawl(
    p_schema text,
    p_table  text DEFAULT NULL,
    p_reason text DEFAULT 'manual',
    p_table_oid oid DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_schema text := btrim(coalesce(p_schema,''));
    v_id bigint;
BEGIN
    IF v_schema = '' THEN
        RAISE EXCEPTION 'request_catalog_crawl: schema is required';
    END IF;
    -- Operational schemas are never catalog subjects.
    IF rvbbit.is_system_schema(v_schema) THEN
        RETURN NULL;
    END IF;
    INSERT INTO rvbbit.catalog_crawl_requests (schema_name, table_name, table_oid, reason)
    VALUES (v_schema, nullif(btrim(coalesce(p_table,'')),''), p_table_oid, coalesce(nullif(btrim(p_reason),''),'manual'))
    ON CONFLICT (schema_name) WHERE status = 'pending' DO UPDATE
       SET requested_at = clock_timestamp(),
           table_name   = coalesce(EXCLUDED.table_name, rvbbit.catalog_crawl_requests.table_name),
           table_oid    = coalesce(EXCLUDED.table_oid, rvbbit.catalog_crawl_requests.table_oid),
           reason       = EXCLUDED.reason
    RETURNING request_id INTO v_id;
    RETURN v_id;
END
$fn$;

-- Same as 0323 except a table's rows are read from the live-tuple counter too.
CREATE OR REPLACE FUNCTION rvbbit.catalog_crawl_requests_tick(
    p_quiet    interval DEFAULT interval '2 minutes',
    p_max_wait interval DEFAULT interval '30 minutes'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_req      rvbbit.catalog_crawl_requests%ROWTYPE;
    v_rows     real;
    v_result   jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.catalog-crawl-requests',0)) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    SELECT * INTO v_req
      FROM rvbbit.catalog_crawl_requests
     WHERE status = 'pending'
       AND requested_at <= clock_timestamp() - p_quiet
       AND NOT EXISTS (
           SELECT 1 FROM rvbbit.catalog_runs run
            WHERE run.status = 'running' AND run.graph_id = 'db_catalog'
              AND (run.schemas IS NULL OR schema_name = ANY(run.schemas))
       )
     ORDER BY requested_at
     LIMIT 1;
    IF v_req.request_id IS NULL THEN
        RETURN jsonb_build_object('status','idle');
    END IF;
    -- A table registered empty is usually still loading: give it up to
    -- p_max_wait to grow before spending a crawl on it.
    IF v_req.table_oid IS NOT NULL THEN
        -- reltuples stays -1 until the first ANALYZE, which autovacuum never runs
        -- on a small upload; the live-tuple counter sees rows as soon as they commit.
        SELECT greatest(c.reltuples, pg_stat_get_live_tuples(c.oid)) INTO v_rows
          FROM pg_class c WHERE c.oid = v_req.table_oid;
        IF v_rows IS NULL THEN
            UPDATE rvbbit.catalog_crawl_requests
               SET status='skipped', finished_at=clock_timestamp(),
                   error='relation no longer exists'
             WHERE request_id = v_req.request_id;
            RETURN jsonb_build_object('status','skipped','request_id',v_req.request_id,
                                      'schema',v_req.schema_name,'table',v_req.table_name);
        END IF;
        IF v_rows <= 0 AND v_req.requested_at > clock_timestamp() - p_max_wait THEN
            RETURN jsonb_build_object('status','waiting_for_rows','request_id',v_req.request_id,
                                      'schema',v_req.schema_name,'table',v_req.table_name);
        END IF;
    END IF;
    UPDATE rvbbit.catalog_crawl_requests
       SET status='running', started_at=clock_timestamp(), attempts=attempts+1
     WHERE request_id = v_req.request_id;
    BEGIN
        v_result := rvbbit.catalog_crawl(ARRAY[v_req.schema_name], 'db_catalog', 50000, 12, true, '');
        UPDATE rvbbit.catalog_crawl_requests
           SET status='done', finished_at=clock_timestamp(), result=v_result, error=NULL
         WHERE request_id = v_req.request_id;
        RETURN jsonb_build_object('status','crawled','request_id',v_req.request_id,
                                  'schema',v_req.schema_name,'table',v_req.table_name,
                                  'result',v_result);
    EXCEPTION WHEN OTHERS THEN
        UPDATE rvbbit.catalog_crawl_requests
           SET status = CASE WHEN attempts >= 3 THEN 'error' ELSE 'pending' END,
               requested_at = CASE WHEN attempts >= 3 THEN requested_at ELSE clock_timestamp() END,
               finished_at = CASE WHEN attempts >= 3 THEN clock_timestamp() ELSE NULL END,
               error = left(SQLERRM, 2000)
         WHERE request_id = v_req.request_id;
        RETURN jsonb_build_object('status','error','request_id',v_req.request_id,
                                  'schema',v_req.schema_name,'error',left(SQLERRM,500));
    END;
END
$fn$;

-- New data announces itself: register the schema and queue a crawl. Runs as
-- the owner so a person creating a table in their own schema still registers
-- it, and it never fails the statement that fired it.
CREATE OR REPLACE FUNCTION rvbbit._discover_new_relations()
RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE
    r record;
    v_schema text;
    v_rel text;
    v_kind "char";
BEGIN
    FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE NOT in_extension LOOP
        BEGIN
            v_schema := CASE WHEN r.object_type = 'schema' THEN r.object_identity ELSE r.schema_name END;
            CONTINUE WHEN rvbbit.is_system_schema(v_schema);
            IF NOT EXISTS (SELECT 1 FROM rvbbit.business_sources WHERE schema_name = v_schema) THEN
                PERFORM rvbbit.business_sources_populate();
            END IF;
            IF r.object_type IN ('table', 'view', 'materialized view', 'foreign table') THEN
                SELECT c.relname, c.relkind INTO v_rel, v_kind FROM pg_class c WHERE c.oid = r.objid;
                CONTINUE WHEN v_rel IS NULL OR v_rel LIKE '\_%';
                -- A table may still be loading: the tick waits for its rows. Views have none to wait for.
                PERFORM rvbbit.request_catalog_crawl(v_schema, v_rel, 'ddl',
                                                     CASE WHEN v_kind IN ('r', 'p') THEN r.objid END);
                -- Calliope's routes (how earlier questions were answered) re-check
                -- when a table they name changes, and every dead end does when new
                -- data appears. The table is Calliope's and may not exist yet.
                IF to_regclass('rvbbit.calliope_routes') IS NOT NULL THEN
                    UPDATE rvbbit.calliope_routes SET checked_at = NULL
                     WHERE status IN ('adopted', 'stale', 'unverified', 'broken')
                       AND (route->'tables' ? (v_schema || '.' || v_rel) OR route->>'outcome' = 'dead_end');
                END IF;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'rvbbit discovery skipped %: %', r.object_identity, SQLERRM;
        END;
    END LOOP;
END
$fn$;

DO $trigger$
BEGIN
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE NOTICE '0496: not a superuser; new data will be registered by the 10-minute Meaning sweep instead';
        RETURN;
    END IF;
    DROP EVENT TRIGGER IF EXISTS rvbbit_discover_new_relations;
    CREATE EVENT TRIGGER rvbbit_discover_new_relations ON ddl_command_end
        WHEN TAG IN ('CREATE SCHEMA', 'CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO', 'CREATE VIEW',
                     'CREATE MATERIALIZED VIEW', 'CREATE FOREIGN TABLE', 'IMPORT FOREIGN SCHEMA', 'ALTER TABLE')
        EXECUTE FUNCTION rvbbit._discover_new_relations();
END
$trigger$;

-- Once: re-classify what populate had defaulted, drop the system schemas'
-- catalog entries, and queue a crawl for business data that has none yet.
SELECT rvbbit.business_sources_populate();
DELETE FROM rvbbit.catalog_docs WHERE graph_id = 'db_catalog' AND rvbbit.is_system_schema(schema_name);
DELETE FROM rvbbit.catalog_snapshots WHERE graph_id = 'db_catalog' AND rvbbit.is_system_schema(schema_name);
SELECT rvbbit.request_catalog_crawl(bs.schema_name, NULL, 'migration')
  FROM rvbbit.business_sources bs
 WHERE bs.role IN ('business', 'derived')
   AND EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = bs.schema_name AND c.relkind IN ('r', 'p', 'm'))
   AND NOT EXISTS (SELECT 1 FROM rvbbit.catalog_docs d
                    WHERE d.graph_id = 'db_catalog' AND d.schema_name = bs.schema_name);
