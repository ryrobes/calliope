-- 0301: Close the automatic Business Context maintenance loop.
--
-- Mirror completion already requests a schema-scoped catalog crawl, and a
-- successful catalog crawl already observes/queues structural topology drift.
-- This migration adds the durable repair lanes around that happy path:
--
--   * remember that mirrored *data* changed, independently of schema drift;
--   * repair a missed background catalog crawl after a quiet grace period;
--   * pre-warm a stale Business Context search projection;
--   * queue at most one debounced data-semantic refresh per domain/day; and
--   * keep the structural safety net from incorrectly clearing pending_catalog.

ALTER TABLE rvbbit.business_context_domains
    ADD COLUMN IF NOT EXISTS data_dirty_since timestamptz,
    ADD COLUMN IF NOT EXISTS last_mirror_data_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_data_modeled_at timestamptz;

UPDATE rvbbit.business_context_domains
   SET last_data_modeled_at=coalesce(last_data_modeled_at,last_modeled_at)
 WHERE last_data_modeled_at IS NULL
   AND last_modeled_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS business_context_domains_data_refresh_idx
    ON rvbbit.business_context_domains(
        data_dirty_since,last_data_modeled_at,last_mirror_data_at
    )
    WHERE data_dirty_since IS NOT NULL;

COMMENT ON COLUMN rvbbit.business_context_domains.data_dirty_since IS
    'Oldest successful mirrored-data change not yet covered by a completed semantic Value System refresh.';
COMMENT ON COLUMN rvbbit.business_context_domains.last_mirror_data_at IS
    'Most recent successful or partial mirror completion for this destination schema.';
COMMENT ON COLUMN rvbbit.business_context_domains.last_data_modeled_at IS
    'Most recent completed topology workflow that covered pending mirrored-data change.';

CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain text;
    v_finished_at timestamptz;
BEGIN
    IF NEW.status IN ('succeeded','partial')
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT destination_schema INTO v_domain
          FROM rvbbit.mirror_jobs WHERE job_name=NEW.job_name;
        IF v_domain IS NOT NULL THEN
            v_finished_at := coalesce(NEW.finished_at,clock_timestamp());
            PERFORM rvbbit.business_context_mark_domain_dirty(
                v_domain,'mirror',NEW.run_id::text,
                jsonb_build_object(
                    'job_name',NEW.job_name,'status',NEW.status,
                    'tables_succeeded',NEW.tables_succeeded,
                    'tables_failed',NEW.tables_failed,'rows_loaded',NEW.rows_loaded
                )
            );
            -- A succeeded table may legitimately become empty, so
            -- tables_succeeded (not rows_loaded > 0) is the data-change signal.
            IF NEW.tables_succeeded>0 THEN
                UPDATE rvbbit.business_context_domains
                   SET data_dirty_since=coalesce(data_dirty_since,v_finished_at),
                       last_mirror_data_at=v_finished_at,
                       updated_at=clock_timestamp()
                 WHERE domain_key=v_domain;
            END IF;
        END IF;
    END IF;
    RETURN NULL;
END
$fn$;

-- The 0299 completion trigger finalizes the graph first (Postgres orders
-- same-event triggers by name); this sibling only acknowledges data freshness
-- after that durable workflow really completed.
CREATE OR REPLACE FUNCTION rvbbit._business_context_workflow_data_fresh()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.status='completed' AND OLD.status IS DISTINCT FROM NEW.status THEN
        UPDATE rvbbit.business_context_domains
           SET data_dirty_since=NULL,
               last_data_modeled_at=clock_timestamp(),
               updated_at=clock_timestamp()
         WHERE last_completed_workflow_run_id=NEW.run_id;
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_workflow_data_fresh
    ON rvbbit.business_topology_workflow_runs;
CREATE TRIGGER business_context_workflow_data_fresh
AFTER UPDATE OF status ON rvbbit.business_topology_workflow_runs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_context_workflow_data_fresh();

CREATE OR REPLACE FUNCTION rvbbit.business_context_catalog_repair_tick(
    p_grace interval DEFAULT interval '10 minutes'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text;
    v_catalog jsonb;
    v_status text;
BEGIN
    IF p_grace<interval '1 minute' OR p_grace>interval '24 hours' THEN
        RAISE EXCEPTION 'Business Context catalog repair grace must be between 1 minute and 24 hours';
    END IF;
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.business-context-catalog-repair',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    SELECT domain.domain_key INTO v_domain
      FROM rvbbit.business_context_domains domain
     WHERE domain.status='pending_catalog'
       AND coalesce(domain.dirty_since,domain.updated_at)
           <=clock_timestamp()-p_grace
       AND NOT EXISTS (
           SELECT 1
             FROM rvbbit.mirror_jobs job
             JOIN rvbbit.mirror_runs run USING (job_name)
            WHERE job.destination_schema=domain.domain_key
              AND run.status IN ('queued','running')
       )
       AND NOT EXISTS (
           SELECT 1
             FROM rvbbit.catalog_runs catalog
            WHERE catalog.status='running'
              AND catalog.graph_id='db_catalog'
              AND (
                  catalog.schemas IS NULL
                  OR domain.domain_key=ANY(catalog.schemas)
              )
       )
     ORDER BY coalesce(domain.dirty_since,domain.updated_at),domain.domain_key
     LIMIT 1;
    IF v_domain IS NULL THEN
        RETURN jsonb_build_object('status','idle');
    END IF;
    BEGIN
        -- The normal dlt worker uses durable catalog_crawl_run() immediately.
        -- This bounded single-schema function is only the repair path after the
        -- background worker was interrupted or failed.
        v_catalog := rvbbit.catalog_crawl(
            ARRAY[v_domain],'db_catalog',50000,12,true,''
        );
    EXCEPTION WHEN OTHERS THEN
        UPDATE rvbbit.business_context_domains
           SET status='pending_catalog',last_error=left(SQLERRM,2000),
               updated_at=clock_timestamp()
         WHERE domain_key=v_domain;
        RETURN jsonb_build_object(
            'status','error','domain_key',v_domain,'error',left(SQLERRM,500)
        );
    END;
    SELECT status INTO v_status
      FROM rvbbit.business_context_domains WHERE domain_key=v_domain;
    RETURN jsonb_build_object(
        'status','repaired','domain_key',v_domain,
        'domain_status',v_status,'catalog',v_catalog
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_data_refresh_tick(
    p_quiet interval DEFAULT interval '15 minutes',
    p_min_interval interval DEFAULT interval '20 hours'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text;
    v_observation jsonb;
    v_steward jsonb;
BEGIN
    IF p_quiet<interval '1 minute' OR p_quiet>interval '24 hours' THEN
        RAISE EXCEPTION 'Business Context data refresh quiet period must be between 1 minute and 24 hours';
    END IF;
    IF p_min_interval<interval '1 hour' OR p_min_interval>interval '30 days' THEN
        RAISE EXCEPTION 'Business Context data refresh interval must be between 1 hour and 30 days';
    END IF;
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.business-context-data-refresh',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    SELECT domain.domain_key INTO v_domain
      FROM rvbbit.business_context_domains domain
     WHERE domain.data_dirty_since IS NOT NULL
       AND domain.status IN ('clean','review','error')
       AND domain.pending_workflow_run_id IS NULL
       AND domain.last_mirror_data_at<=clock_timestamp()-p_quiet
       AND (
           domain.last_data_modeled_at IS NULL
           OR domain.last_data_modeled_at<=clock_timestamp()-p_min_interval
       )
       AND (domain.status<>'error' OR domain.updated_at<=clock_timestamp()-interval '1 hour')
     ORDER BY domain.data_dirty_since,domain.domain_key
     LIMIT 1;
    IF v_domain IS NULL THEN
        RETURN jsonb_build_object('status','idle');
    END IF;
    PERFORM rvbbit.business_context_mark_domain_dirty(
        v_domain,'schedule',NULL,
        jsonb_build_object('reason','mirrored_data_changed','debounced',true)
    );
    v_observation := rvbbit.business_context_observe_domain(
        v_domain,'schedule',NULL,
        jsonb_build_object('reason','mirrored_data_changed','debounced',true)
    );
    -- A data-only refresh is intentional even when the structural fingerprint
    -- is unchanged. The 20-hour gate prevents each periodic mirror from
    -- becoming an LLM excavation.
    UPDATE rvbbit.business_context_domains
       SET status='dirty',dirty_since=coalesce(dirty_since,data_dirty_since),
           updated_at=clock_timestamp()
     WHERE domain_key=v_domain;
    v_steward := rvbbit.business_context_steward_tick(v_domain,false);
    RETURN jsonb_build_object(
        'status','processed','domain_key',v_domain,
        'observation',v_observation,'steward',v_steward
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_maintenance_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_search jsonb := jsonb_build_object('status','fresh');
    v_steward jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.business-context-maintenance',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    IF rvbbit.business_context_search_stale() THEN
        BEGIN
            v_search := jsonb_build_object(
                'status','refreshed','result',rvbbit.business_context_crawl(false)
            );
        EXCEPTION WHEN OTHERS THEN
            v_search := jsonb_build_object(
                'status','error','error',left(SQLERRM,500)
            );
        END;
    END IF;
    v_steward := rvbbit.business_context_steward_tick();
    RETURN jsonb_build_object(
        'status','complete','search',v_search,'steward',v_steward
    );
END
$fn$;

-- Hourly structural safety net. pending_catalog is deliberately excluded:
-- only a catalog completion (or its repair tick) may clear that state.
CREATE OR REPLACE FUNCTION rvbbit.business_context_steward_safety_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text;
    v_observed integer := 0;
    v_errors jsonb := '[]'::jsonb;
    v_tick jsonb;
BEGIN
    FOR v_domain IN
        SELECT candidate.domain_key
          FROM (
              SELECT domain.domain_key FROM rvbbit.business_context_domains domain
              UNION SELECT job.destination_schema FROM rvbbit.mirror_jobs job
              UNION SELECT proposal.domain_key FROM rvbbit.business_value_graph_proposals proposal
          ) candidate
          LEFT JOIN rvbbit.business_context_domains domain
            ON domain.domain_key=candidate.domain_key
         WHERE domain.status IS DISTINCT FROM 'pending_catalog'
           AND (
               domain.domain_key IS NULL
               OR domain.status IN ('dirty','error')
               OR domain.last_observed_at IS NULL
               OR domain.last_observed_at<=clock_timestamp()-interval '1 hour'
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM rvbbit.mirror_jobs job
                 JOIN rvbbit.mirror_runs run USING (job_name)
                WHERE job.destination_schema=candidate.domain_key
                  AND run.status IN ('queued','running')
           )
         ORDER BY candidate.domain_key
    LOOP
        BEGIN
            PERFORM rvbbit.business_context_observe_domain(
                v_domain,'schedule',NULL,jsonb_build_object('safety_net',true)
            );
            v_observed := v_observed+1;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors||jsonb_build_array(jsonb_build_object(
                'domain_key',v_domain,'error',left(SQLERRM,500)
            ));
        END;
    END LOOP;
    v_tick := rvbbit.business_context_steward_tick();
    RETURN jsonb_build_object(
        'domains_observed',v_observed,'errors',v_errors,'steward',v_tick
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_catalog_repair_tick(interval) IS
    'Repair one mirror domain whose expected schema-scoped catalog crawl did not complete after the grace period.';
COMMENT ON FUNCTION rvbbit.business_context_data_refresh_tick(interval,interval) IS
    'Queue at most one debounced semantic refresh for mirrored data changes, even when the schema fingerprint is unchanged.';
COMMENT ON FUNCTION rvbbit.business_context_maintenance_tick() IS
    'Frequent cheap lane: pre-warm a stale Business Context search index and dispatch at most one structurally dirty domain.';
COMMENT ON FUNCTION rvbbit.business_context_steward_safety_tick() IS
    'Hourly out-of-band structural drift safety net; pending mirror catalogs are reserved for the catalog repair lane.';
