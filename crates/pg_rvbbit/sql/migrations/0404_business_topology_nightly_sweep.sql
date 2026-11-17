-- Nightly warehouse coverage is a durable queue, not an unbounded excavation.
-- Scheduling lives in docker/init/03-seed-scheduled-tasks.sh and uber/bootstrap.sh.
CREATE TABLE IF NOT EXISTS rvbbit.business_topology_sweeps (
    sweep_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'active' CHECK (status IN
        ('active','completed','completed_with_errors','cancelled')),
    batch_size integer NOT NULL CHECK (batch_size BETWEEN 1 AND 100),
    requested_by text NOT NULL DEFAULT current_user,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS business_topology_sweeps_one_active_idx
    ON rvbbit.business_topology_sweeps ((true)) WHERE status='active';
CREATE TABLE IF NOT EXISTS rvbbit.business_topology_sweep_batches (
    sweep_id uuid NOT NULL REFERENCES rvbbit.business_topology_sweeps(sweep_id),
    schema_name text NOT NULL,
    batch_number integer NOT NULL,
    relations text[] NOT NULL CHECK (cardinality(relations) BETWEEN 1 AND 100),
    run_id uuid UNIQUE REFERENCES rvbbit.business_topology_workflow_runs(run_id),
    error text,
    PRIMARY KEY (sweep_id,schema_name,batch_number)
);

CREATE OR REPLACE VIEW rvbbit.business_topology_sweep_status AS
SELECT s.sweep_id,s.status,s.requested_at,s.finished_at,s.batch_size,
       count(b.sweep_id) AS batches,
       coalesce(sum(cardinality(b.relations)),0) AS relations,
       count(DISTINCT b.schema_name) AS schemas,
       count(*) FILTER (WHERE b.run_id IS NULL AND b.error IS NULL
                             AND b.sweep_id IS NOT NULL) AS pending_batches,
       count(*) FILTER (WHERE r.status='queued') AS queued_batches,
       count(*) FILTER (WHERE r.status IN ('running','cancel_requested')) AS running_batches,
       count(*) FILTER (WHERE r.status='completed') AS completed_batches,
       count(*) FILTER (WHERE b.error IS NOT NULL OR r.status IN ('failed','cancelled')) AS failed_batches,
       coalesce(sum(cardinality(b.relations)) FILTER (WHERE r.status='completed'),0) AS completed_relations
  FROM rvbbit.business_topology_sweeps s
  LEFT JOIN rvbbit.business_topology_sweep_batches b USING (sweep_id)
  LEFT JOIN rvbbit.business_topology_workflow_runs r USING (run_id)
 GROUP BY s.sweep_id;

CREATE OR REPLACE FUNCTION rvbbit.business_topology_sweep_tick()
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_sweep uuid;
    v_batch record;
    v_run uuid;
    v_free integer;
    v_queued integer := 0;
    v_relation text;
    v_table regclass;
    v_allowed boolean;
    v_status text;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
        'rvbbit.business-topology-sweep',0)) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    SELECT sweep_id INTO v_sweep FROM rvbbit.business_topology_sweeps WHERE status='active';
    IF v_sweep IS NULL THEN RETURN jsonb_build_object('status','idle'); END IF;

    -- Coordinate queue capacity with manual schema requests and legacy starts.
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business_topology_queue_schema',0));
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business_topology_start_workflow',0));
    SELECT greatest(0,8-count(*))::integer INTO v_free
      FROM rvbbit.business_topology_workflow_runs WHERE status='queued';

    FOR v_batch IN
        SELECT b.* FROM rvbbit.business_topology_sweep_batches b
         WHERE b.sweep_id=v_sweep AND b.run_id IS NULL AND b.error IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_topology_workflow_runs r
                WHERE r.status IN ('queued','running','cancel_requested')
                  AND r.parameters->'relations' ?| b.relations
           )
         ORDER BY b.batch_number,b.schema_name
         LIMIT v_free
    LOOP
        BEGIN
            -- A source can disappear or lose access after the nightly snapshot.
            -- Record that batch's failure without losing the rest of the sweep.
            FOREACH v_relation IN ARRAY v_batch.relations LOOP
                v_table := rvbbit._safe_regclass(v_relation);
                IF v_table IS NULL THEN RAISE EXCEPTION 'source no longer exists: %',v_relation; END IF;
                SELECT c.relkind IN ('r','p','m') AND NOT c.relispartition
                       AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
                       AND has_table_privilege(c.oid,'SELECT')
                  INTO v_allowed FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE c.oid=v_table;
                IF NOT coalesce(v_allowed,false) THEN
                    RAISE EXCEPTION 'source no longer eligible: %',v_relation;
                END IF;
            END LOOP;
            v_run := gen_random_uuid();
            INSERT INTO rvbbit.business_topology_workflow_runs
                (run_id,run_name,parameters,progress,requested_by)
            VALUES (
                v_run,'Nightly excavation - '||v_batch.schema_name||' - batch '
                    ||v_batch.batch_number||' - '||left(v_sweep::text,8),
                jsonb_build_object(
                    'schema_version','rvbbit.business-topology.workflow-parameters.v1',
                    'corpus_id','business-topology-'||v_run,
                    'relations',to_jsonb(v_batch.relations),'schemas',jsonb_build_array(v_batch.schema_name),
                    'scope_mode','explicit_relations','scope_schema',v_batch.schema_name,
                    'sweep_id',v_sweep,'sweep_batch',v_batch.batch_number,
                    'sample_rows',2048,'max_relations',cardinality(v_batch.relations),
                    'requested_max_relations',cardinality(v_batch.relations),
                    'max_work_items',500,'max_llm_calls',128,
                    'backend','clover_llm','model','calliope','include_bridges',false,
                    'policy',jsonb_build_object(
                        'maximum_sources_per_unit',12,'maximum_populations_per_unit',48,
                        'max_pairs_per_unit',64,'max_pairs_per_link',8,
                        'max_population_fanout',8,'minimum_pair_priority',0.32,
                        'max_cross_neighborhood_links',24)),
                jsonb_build_object('phase','queued','scope_mode','explicit_relations',
                    'scope_schema',v_batch.schema_name,'sweep_id',v_sweep,
                    'relation_count',cardinality(v_batch.relations),
                    'completed_work_items',0,'llm_attempts',0,'bundles_staged',0,
                    'queued_at',clock_timestamp()),current_user);
            UPDATE rvbbit.business_topology_sweep_batches SET run_id=v_run
             WHERE sweep_id=v_sweep AND schema_name=v_batch.schema_name
               AND batch_number=v_batch.batch_number;
            PERFORM pg_notify('rvbbit_business_topology_jobs',v_run::text);
            v_queued := v_queued+1;
        EXCEPTION WHEN OTHERS THEN
            UPDATE rvbbit.business_topology_sweep_batches SET error=SQLSTATE||': '||left(SQLERRM,1000)
             WHERE sweep_id=v_sweep AND schema_name=v_batch.schema_name
               AND batch_number=v_batch.batch_number;
        END;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.business_topology_sweep_batches b
        LEFT JOIN rvbbit.business_topology_workflow_runs r USING (run_id)
        WHERE b.sweep_id=v_sweep AND b.error IS NULL
          AND (b.run_id IS NULL OR r.status IN ('queued','running','cancel_requested'))
    ) THEN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM rvbbit.business_topology_sweep_batches b
            LEFT JOIN rvbbit.business_topology_workflow_runs r USING (run_id)
            WHERE b.sweep_id=v_sweep AND (b.error IS NOT NULL OR r.status IN ('failed','cancelled'))
        ) THEN 'completed_with_errors' ELSE 'completed' END INTO v_status;
        UPDATE rvbbit.business_topology_sweeps SET status=v_status,finished_at=clock_timestamp()
         WHERE sweep_id=v_sweep;
    ELSE v_status := 'active'; END IF;
    RETURN jsonb_build_object('status',v_status,'sweep_id',v_sweep,'enqueued',v_queued);
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_topology_start_nightly_sweep(
    p_schemas text[] DEFAULT NULL,
    p_batch_size integer DEFAULT 24
) RETURNS uuid LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE v_sweep uuid;
BEGIN
    IF p_batch_size IS NULL OR p_batch_size NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'batch size must be between 1 and 100';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business-topology-sweep',0));
    -- Close a just-finished sweep before deciding whether tonight needs a new one.
    PERFORM rvbbit.business_topology_sweep_tick();
    SELECT sweep_id INTO v_sweep FROM rvbbit.business_topology_sweeps WHERE status='active';
    IF v_sweep IS NOT NULL THEN RETURN v_sweep; END IF;
    INSERT INTO rvbbit.business_topology_sweeps(batch_size) VALUES (p_batch_size)
     RETURNING sweep_id INTO v_sweep;
    -- Freeze an exact list; newly registered sources join the next sweep.
    WITH eligible AS (
        SELECT n.nspname AS schema_name,format('%I.%I',n.nspname,c.relname) AS relation,
               (row_number() OVER (PARTITION BY n.nspname ORDER BY c.relname)-1)/p_batch_size+1 AS batch
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE c.relkind IN ('r','p','m') AND NOT c.relispartition
           AND (p_schemas IS NULL OR n.nspname=ANY(p_schemas))
           AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
           AND has_table_privilege(c.oid,'SELECT')
    )
    INSERT INTO rvbbit.business_topology_sweep_batches(sweep_id,schema_name,batch_number,relations)
    SELECT v_sweep,schema_name,batch::integer,array_agg(relation ORDER BY relation)
      FROM eligible GROUP BY schema_name,batch;
    PERFORM rvbbit.business_topology_sweep_tick();
    RETURN v_sweep;
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_topology_start_nightly_sweep(text[],integer) IS
    'Snapshot all eligible relations into bounded per-schema batches; continue an existing active sweep instead of restarting it.';
COMMENT ON FUNCTION rvbbit.business_topology_sweep_tick() IS
    'Fill up to eight queued excavations; skip overlapping active work, record failures, and finish the durable nightly sweep.';
