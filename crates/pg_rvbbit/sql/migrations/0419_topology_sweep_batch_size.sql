-- 0419: Smaller excavation units for the nightly topology sweep.
--
-- The first map sweep on the pilot (2026-09-10) handed the judgment model a
-- 24-relation, ~60k-token consolidation packet per batch — 176 work items
-- and 17 excavation units for one batch of a custom application schema —
-- and the batch failed its JSON contract after 33 model calls. Eight
-- relations per batch cuts the packet to roughly a third, keeps every unit
-- inside one schema (unchanged), and is the direction Phase B's
-- recognition-scoped units go anyway. Signature and body are otherwise
-- identical to 0404; only the default changes, so the cron's bare call
-- picks it up without being re-scheduled.
CREATE OR REPLACE FUNCTION rvbbit.business_topology_start_nightly_sweep(
    p_schemas text[] DEFAULT NULL,
    p_batch_size integer DEFAULT 8
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
COMMENT ON FUNCTION rvbbit.business_topology_start_nightly_sweep(text[], integer) IS
    'Starts (or returns the active) nightly topology sweep over every admitted relation, frozen into per-schema batches of p_batch_size (default 8 since 0419; 24 before).';
