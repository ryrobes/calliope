-- 0323: catalog a newly registered table promptly, and name registered tables.
--
-- Observed on the hosted appliance: a user uploaded public.traffic_violations
-- (1.8M rows) and accelerated it; an hour later search_data still could not
-- find it and describe_table had no statistics, because the catalog KG only
-- learns new relations from the nightly catalog_crawl_run() (or from mirror
-- completion, which requests a schema-scoped crawl). Direct uploads and
-- rvbbit.enable_table() had no such hook — while run_sql deliberately blocks
-- information_schema, so an agent had no governed way to discover the table.
--
-- Registration happens inside DDL, where a crawl must not run (and the rows
-- often arrive after the CREATE). So: a small durable request queue, filled by
-- a trigger on rvbbit.tables, drained by a two-minute cron tick that waits for
-- quiet and for rows to exist, then runs the existing schema-scoped
-- catalog_crawl(). Also: a readable rvbbit.accelerated_tables view, because
-- rvbbit.tables carries only OIDs, and a backfill for tables registered before
-- this migration that the catalog never saw.

CREATE TABLE IF NOT EXISTS rvbbit.catalog_crawl_requests (
    request_id    bigserial PRIMARY KEY,
    schema_name   text NOT NULL,
    table_name    text,
    table_oid     oid,
    reason        text NOT NULL DEFAULT 'manual',
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','done','skipped','error')),
    attempts      integer NOT NULL DEFAULT 0,
    requested_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at    timestamptz,
    finished_at   timestamptz,
    error         text,
    result        jsonb
);

-- One pending request per schema: repeated registrations extend the quiet
-- window instead of queueing duplicate crawls.
CREATE UNIQUE INDEX IF NOT EXISTS catalog_crawl_requests_pending_schema_idx
    ON rvbbit.catalog_crawl_requests (schema_name)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS catalog_crawl_requests_status_idx
    ON rvbbit.catalog_crawl_requests (status, requested_at);

COMMENT ON TABLE rvbbit.catalog_crawl_requests IS
    'Debounced requests for a schema-scoped catalog crawl (table registration, uploads, manual). Drained by catalog_crawl_requests_tick().';

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
    IF v_schema IN ('rvbbit','pg_catalog','information_schema','pg_toast','cron','hindsight') THEN
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

COMMENT ON FUNCTION rvbbit.request_catalog_crawl(text,text,text,oid) IS
    'Queue (or extend) a debounced schema-scoped catalog crawl; returns the request id, NULL for operational schemas.';

CREATE OR REPLACE FUNCTION rvbbit._catalog_crawl_on_table_registration()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_schema text;
    v_table  text;
BEGIN
    SELECT n.nspname, c.relname INTO v_schema, v_table
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.oid = NEW.table_oid;
    IF v_schema IS NOT NULL THEN
        BEGIN
            PERFORM rvbbit.request_catalog_crawl(v_schema, v_table, 'table_registered', NEW.table_oid);
        EXCEPTION WHEN OTHERS THEN
            -- Never let a discovery convenience break registration itself.
            RAISE NOTICE 'catalog crawl request skipped for %.%: %', v_schema, v_table, SQLERRM;
        END;
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS catalog_crawl_on_table_registration ON rvbbit.tables;
CREATE TRIGGER catalog_crawl_on_table_registration
AFTER INSERT ON rvbbit.tables
FOR EACH ROW EXECUTE FUNCTION rvbbit._catalog_crawl_on_table_registration();

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
        SELECT c.reltuples INTO v_rows FROM pg_class c WHERE c.oid = v_req.table_oid;
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

COMMENT ON FUNCTION rvbbit.catalog_crawl_requests_tick(interval,interval) IS
    'Cheap cron lane: drain one debounced catalog crawl request (waits for quiet and for rows), running the schema-scoped catalog_crawl().';

-- rvbbit.tables carries only OIDs; agents cannot see pg_class through run_sql.
CREATE OR REPLACE VIEW rvbbit.accelerated_tables AS
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       n.nspname || '.' || c.relname AS relation,
       t.table_oid,
       t.acceleration_enabled,
       t.history_policy,
       t.generation_semantics,
       t.next_generation,
       t.shadow_heap_retained,
       t.shadow_heap_dirty,
       t.dirty_since,
       t.last_write_at,
       t.created_at AS registered_at,
       greatest(c.reltuples, 0)::bigint AS estimated_rows,
       EXISTS (SELECT 1 FROM rvbbit.catalog_docs d
                WHERE d.graph_id = 'db_catalog' AND d.kind = 'db_table'
                  AND d.schema_name = n.nspname AND d.rel_name = c.relname) AS cataloged
  FROM rvbbit.tables t
  JOIN pg_class c ON c.oid = t.table_oid
  JOIN pg_namespace n ON n.oid = c.relnamespace;

COMMENT ON VIEW rvbbit.accelerated_tables IS
    'Registered (USING rvbbit / enable_table) relations by name with acceleration state and whether the catalog KG has seen them.';

-- Backfill: anything registered before this migration that the catalog never
-- saw gets a request now (one per schema; the tick crawls the schema).
INSERT INTO rvbbit.catalog_crawl_requests (schema_name, table_name, table_oid, reason)
SELECT DISTINCT ON (n.nspname) n.nspname, c.relname, c.oid, 'backfill_uncataloged'
  FROM rvbbit.tables t
  JOIN pg_class c ON c.oid = t.table_oid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('rvbbit','pg_catalog','information_schema','pg_toast','cron','hindsight')
   AND NOT EXISTS (SELECT 1 FROM rvbbit.catalog_docs d
                    WHERE d.graph_id = 'db_catalog' AND d.kind = 'db_table'
                      AND d.schema_name = n.nspname AND d.rel_name = c.relname)
   AND NOT EXISTS (SELECT 1 FROM rvbbit.catalog_crawl_requests r
                    WHERE r.schema_name = n.nspname AND r.status = 'pending')
 ORDER BY n.nspname, t.created_at DESC
ON CONFLICT DO NOTHING;
