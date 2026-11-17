-- 0421: Sweep hygiene — three things the first unattended day needs.
--
--   1. Stale-pin guard. A pinned side-car worker (0420) that dies hands the
--      queue back to the old in-process worker two minutes later. The sweep
--      feed now pauses instead: business_topology_sweep_tick enqueues nothing
--      while a live pin names a worker that has stopped heartbeating. Stall
--      is the safe failure; pollution is not.
--   2. Structural fingerprints. The nightly sweep re-dug every admitted
--      relation every night (~7.5M tokens a pass on the pilot); the only
--      dedupe was at the family level, after the inference was spent. Each
--      relation now carries a mark — a fingerprint of its columns and types
--      plus the outcome of its last sweep — and an unchanged relation is not
--      re-excavated. Data drift on promoted domains stays the steward's job;
--      structure is what the excavator reacts to. Three consecutive failures
--      quarantine a relation until an operator forces it.
--   3. A morning check: one SELECT that answers "did the night go well".

-- ── 1. Stale pin ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit._business_topology_pin_stale()
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.settings s
         WHERE s.key='business_topology_worker_pin'
           AND (s.value->>'until')::timestamptz > clock_timestamp()
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_topology_workflow_workers w
                WHERE w.worker_id = s.value->>'worker_id'
                  AND w.last_seen_at > clock_timestamp() - interval '120 seconds')
    );
$fn$;

-- ── 2. Relation marks ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rvbbit.business_topology_relation_marks (
    relation text PRIMARY KEY,
    schema_name text NOT NULL,
    fingerprint text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('completed','failed')),
    run_id uuid,
    sweep_id uuid,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_error text,
    marked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE rvbbit.business_topology_relation_marks IS
    'Per-relation memory of the last sweep: structural fingerprint (columns + types) and outcome. An unchanged, completed relation is skipped by the next nightly sweep; three consecutive failures quarantine it until forced.';

CREATE OR REPLACE FUNCTION rvbbit.business_topology_relation_fingerprint(p_relation text)
RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT md5(c.relkind::text || ':' || coalesce(string_agg(
               a.attname || ':' || format_type(a.atttypid, a.atttypmod),
               ',' ORDER BY a.attnum), ''))
      FROM pg_class c
      LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
     WHERE c.oid = rvbbit._safe_regclass(p_relation)
     GROUP BY c.relkind;
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_topology_mark_run_relations()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_relation text;
    v_fp text;
BEGIN
    IF NEW.status NOT IN ('completed','failed')
       OR OLD.status IS NOT DISTINCT FROM NEW.status
       OR NOT (NEW.parameters ? 'sweep_id') THEN
        RETURN NULL;
    END IF;
    BEGIN
        FOR v_relation IN
            SELECT value FROM jsonb_array_elements_text(coalesce(NEW.parameters->'relations','[]'::jsonb))
        LOOP
            v_fp := rvbbit.business_topology_relation_fingerprint(v_relation);
            IF v_fp IS NULL THEN CONTINUE; END IF;
            INSERT INTO rvbbit.business_topology_relation_marks
                (relation, schema_name, fingerprint, outcome, run_id, sweep_id,
                 consecutive_failures, last_error, marked_at)
            VALUES (v_relation, split_part(v_relation,'.',1), v_fp, NEW.status, NEW.run_id,
                    (NEW.parameters->>'sweep_id')::uuid,
                    CASE WHEN NEW.status='failed' THEN 1 ELSE 0 END,
                    CASE WHEN NEW.status='failed' THEN left(NEW.error, 500) END,
                    clock_timestamp())
            ON CONFLICT (relation) DO UPDATE
               SET schema_name=EXCLUDED.schema_name,
                   fingerprint=EXCLUDED.fingerprint,
                   outcome=EXCLUDED.outcome,
                   run_id=EXCLUDED.run_id,
                   sweep_id=EXCLUDED.sweep_id,
                   consecutive_failures=CASE WHEN EXCLUDED.outcome='failed'
                        THEN rvbbit.business_topology_relation_marks.consecutive_failures+1 ELSE 0 END,
                   last_error=EXCLUDED.last_error,
                   marked_at=EXCLUDED.marked_at;
        END LOOP;
    EXCEPTION WHEN OTHERS THEN
        -- Marks are memory, never a reason to fail a run's completion.
        RAISE NOTICE 'business_topology_relation_marks: % (run %)', SQLERRM, NEW.run_id;
    END;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_topology_mark_run_relations ON rvbbit.business_topology_workflow_runs;
CREATE TRIGGER business_topology_mark_run_relations
AFTER UPDATE OF status ON rvbbit.business_topology_workflow_runs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_topology_mark_run_relations();

-- Backfill from sweep runs that completed before the trigger existed — but
-- only runs of the current generation: anything dug before the last
-- generation retirement (0418) belongs to a retired excavator and must be
-- dug again under the map, not skipped as "unchanged".
INSERT INTO rvbbit.business_topology_relation_marks
    (relation, schema_name, fingerprint, outcome, run_id, sweep_id, marked_at)
SELECT DISTINCT ON (rel.value)
       rel.value, split_part(rel.value,'.',1),
       rvbbit.business_topology_relation_fingerprint(rel.value),
       'completed', r.run_id, (r.parameters->>'sweep_id')::uuid, r.finished_at
  FROM rvbbit.business_topology_workflow_runs r,
       jsonb_array_elements_text(coalesce(r.parameters->'relations','[]'::jsonb)) rel
 WHERE r.status='completed' AND r.parameters ? 'sweep_id'
   AND r.requested_at > coalesce(
           (SELECT max(ev.created_at) FROM rvbbit.knowledge_loop_events ev WHERE ev.to_state='retired'),
           clock_timestamp() - interval '14 days')
   AND rvbbit.business_topology_relation_fingerprint(rel.value) IS NOT NULL
 ORDER BY rel.value, r.finished_at DESC
ON CONFLICT (relation) DO NOTHING;

ALTER TABLE rvbbit.business_topology_sweeps
    ADD COLUMN IF NOT EXISTS plan jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Signature changes (p_force), so the old overload must go first or the
-- cron's bare call becomes ambiguous.
DROP FUNCTION IF EXISTS rvbbit.business_topology_start_nightly_sweep(text[], integer);
CREATE OR REPLACE FUNCTION rvbbit.business_topology_start_nightly_sweep(
    p_schemas text[] DEFAULT NULL,
    p_batch_size integer DEFAULT 8,
    p_force boolean DEFAULT false
) RETURNS uuid LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_sweep uuid;
    v_eligible integer;
    v_unchanged integer;
    v_quarantined integer;
BEGIN
    IF p_batch_size IS NULL OR p_batch_size NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'batch size must be between 1 and 100';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business-topology-sweep',0));
    -- Close a just-finished sweep before deciding whether tonight needs a new one.
    PERFORM rvbbit.business_topology_sweep_tick();
    SELECT sweep_id INTO v_sweep FROM rvbbit.business_topology_sweeps WHERE status='active';
    IF v_sweep IS NOT NULL THEN RETURN v_sweep; END IF;

    CREATE TEMP TABLE IF NOT EXISTS _sweep_candidates (
        schema_name text, relation text, unchanged boolean, quarantined boolean
    ) ON COMMIT DROP;
    DELETE FROM _sweep_candidates;
    INSERT INTO _sweep_candidates
    SELECT n.nspname,
           format('%I.%I',n.nspname,c.relname),
           (m.outcome='completed' AND m.fingerprint = rvbbit.business_topology_relation_fingerprint(format('%I.%I',n.nspname,c.relname))),
           (coalesce(m.consecutive_failures,0) >= 3)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN rvbbit.business_topology_relation_marks m ON m.relation=format('%I.%I',n.nspname,c.relname)
     WHERE c.relkind IN ('r','p','m') AND NOT c.relispartition
       AND (p_schemas IS NULL OR n.nspname=ANY(p_schemas))
       AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
       AND has_table_privilege(c.oid,'SELECT');
    SELECT count(*) FILTER (WHERE coalesce(unchanged,false) AND NOT p_force),
           count(*) FILTER (WHERE quarantined AND NOT p_force),
           count(*) FILTER (WHERE p_force OR (NOT coalesce(unchanged,false) AND NOT quarantined))
      INTO v_unchanged, v_quarantined, v_eligible
      FROM _sweep_candidates;
    IF v_eligible = 0 THEN
        RAISE NOTICE 'nightly sweep: nothing to dig (% unchanged, % quarantined)', v_unchanged, v_quarantined;
        RETURN NULL;
    END IF;

    INSERT INTO rvbbit.business_topology_sweeps(batch_size, plan)
    VALUES (p_batch_size, jsonb_build_object(
                'eligible', v_eligible, 'skipped_unchanged', v_unchanged,
                'skipped_quarantined', v_quarantined, 'forced', p_force,
                'schemas', p_schemas))
    RETURNING sweep_id INTO v_sweep;
    -- Freeze an exact list; newly registered sources join the next sweep.
    WITH eligible AS (
        SELECT schema_name, relation,
               (row_number() OVER (PARTITION BY schema_name ORDER BY relation)-1)/p_batch_size+1 AS batch
          FROM _sweep_candidates
         WHERE p_force OR (NOT coalesce(unchanged,false) AND NOT quarantined)
    )
    INSERT INTO rvbbit.business_topology_sweep_batches(sweep_id,schema_name,batch_number,relations)
    SELECT v_sweep,schema_name,batch::integer,array_agg(relation ORDER BY relation)
      FROM eligible GROUP BY schema_name,batch;
    PERFORM rvbbit.business_topology_sweep_tick();
    RETURN v_sweep;
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_topology_start_nightly_sweep(text[], integer, boolean) IS
    'Starts (or returns the active) nightly topology sweep over admitted relations whose structure changed since their last completed sweep, in per-schema batches of p_batch_size (default 8). p_force re-digs everything, including quarantined relations. Returns NULL when nothing needs digging.';

-- ── Sweep tick: identical to 0404 plus the stale-pin pause ────────────────
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

    -- A pinned worker that stopped heartbeating must not hand the queue to
    -- whatever worker is left; the feed waits instead.
    IF rvbbit._business_topology_pin_stale() THEN
        RETURN jsonb_build_object('status','paused','reason','pinned worker is stale','sweep_id',v_sweep);
    END IF;

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

-- ── 3. Morning check ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.business_topology_morning_check()
RETURNS TABLE (check_name text, value text)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_sweep uuid;
    v_since timestamptz;
    v_status text;
    v_text text;
    v_n bigint;
BEGIN
    SELECT s.sweep_id, s.requested_at, s.status INTO v_sweep, v_since, v_status
      FROM rvbbit.business_topology_sweeps s ORDER BY s.requested_at DESC LIMIT 1;
    v_since := coalesce(v_since, clock_timestamp() - interval '1 day');

    check_name := 'sweep'; value := coalesce(left(v_sweep::text,8),'none')||' '||coalesce(v_status,'')
        ||' since '||to_char(v_since,'YYYY-MM-DD HH24:MI')
        ||coalesce(' plan='||(SELECT s.plan::text FROM rvbbit.business_topology_sweeps s WHERE s.sweep_id=v_sweep),'');
    RETURN NEXT;

    check_name := 'batches';
    SELECT 'pending='||st.pending_batches||' queued='||st.queued_batches||' running='||st.running_batches
           ||' completed='||st.completed_batches||' failed='||st.failed_batches||' of '||st.batches
      INTO value FROM rvbbit.business_topology_sweep_status st WHERE st.sweep_id=v_sweep;
    value := coalesce(value,'-'); RETURN NEXT;

    check_name := 'runs by outcome';
    SELECT string_agg(x.status||'='||x.n, ', ' ORDER BY x.status) INTO value
      FROM (SELECT r.status, count(*) n FROM rvbbit.business_topology_workflow_runs r
             WHERE r.requested_at >= v_since AND r.status<>'queued' GROUP BY r.status) x;
    value := coalesce(value,'-'); RETURN NEXT;

    check_name := 'failure classes';
    SELECT string_agg(x.k||'='||x.n, ', ' ORDER BY x.n DESC) INTO value
      FROM (SELECT coalesce(r.progress->>'error_class', left(r.error,40), '?') k, count(*) n
              FROM rvbbit.business_topology_workflow_runs r
             WHERE r.requested_at >= v_since AND r.status='failed' GROUP BY 1) x;
    value := coalesce(value,'none'); RETURN NEXT;

    check_name := 'deferred (transient upstream)';
    SELECT count(*) INTO v_n FROM rvbbit.business_topology_workflow_runs r
     WHERE r.requested_at >= v_since AND coalesce((r.progress->>'deferrals')::int,0) > 0;
    value := v_n::text; RETURN NEXT;

    check_name := 'judgment transport';
    SELECT string_agg(x.k||'='||x.n, ', ') INTO value
      FROM (SELECT coalesce(r.result->'model_routes'->'judgment'->>'transport',
                            r.result->'model_routes'->'judgment'->>'backend', '?')
                   ||'/'||coalesce(r.result->'model_routes'->'judgment'->>'model','?') k, count(*) n
              FROM rvbbit.business_topology_workflow_runs r
             WHERE r.requested_at >= v_since AND r.status='completed' GROUP BY 1) x;
    value := coalesce(value,'-'); RETURN NEXT;

    check_name := 'verdicts';
    SELECT string_agg(x.k||'='||x.n, ', ' ORDER BY x.n DESC) INTO value
      FROM (SELECT coalesce(r.progress->'value_domain_plan_verdict'->>'status','(none)') k, count(*) n
              FROM rvbbit.business_topology_workflow_runs r
             WHERE r.requested_at >= v_since AND r.status='completed' GROUP BY 1) x;
    value := coalesce(value,'-'); RETURN NEXT;

    check_name := 'needs recorded';
    SELECT count(*) INTO v_n FROM rvbbit.business_topology_workflow_runs r,
         jsonb_array_elements_text(coalesce(r.progress->'value_domain_plan_needs','[]'::jsonb))
     WHERE r.requested_at >= v_since;
    value := v_n::text||' (SELECT jsonb_array_elements_text(progress->''value_domain_plan_needs'') FROM rvbbit.business_topology_workflow_runs …)';
    RETURN NEXT;

    check_name := 'graphs staged this sweep';
    SELECT 'total='||count(*)||' single_table='||count(*) FILTER (WHERE g.single_table)
           ||' avg_relations='||coalesce(round(avg(g.relation_count)::numeric,1)::text,'-')
           ||' provisional='||count(*) FILTER (WHERE g.lifecycle='provisional')
           ||' superseded='||count(*) FILTER (WHERE g.status='superseded')
      INTO value FROM rvbbit.business_value_graph_generations g WHERE g.proposed_at >= v_since;
    RETURN NEXT;

    check_name := 'graphs by schema';
    SELECT string_agg(x.s||'='||x.n, ', ' ORDER BY x.n DESC) INTO value
      FROM (SELECT g.schema_name s, count(*) n FROM rvbbit.business_value_graph_generations g
             WHERE g.proposed_at >= v_since GROUP BY 1) x;
    value := coalesce(value,'-'); RETURN NEXT;

    check_name := 'hindsight topology docs since sweep start';
    IF to_regclass('hindsight.documents') IS NOT NULL THEN
        EXECUTE $q$SELECT count(*) FROM hindsight.documents d
                    WHERE d.tags::text ILIKE '%session:business_topology_%' AND d.created_at >= $1$q$
           INTO v_n USING v_since;
        value := v_n::text||' (expect 0)';
    ELSE
        value := 'n/a';
    END IF;
    RETURN NEXT;

    check_name := 'worker pin';
    SELECT coalesce(s.value->>'worker_id','none')||' until '||coalesce(left(s.value->>'until',16),'-')
           ||CASE WHEN rvbbit._business_topology_pin_stale() THEN ' STALE (feed paused)' ELSE ' alive' END
      INTO value FROM rvbbit.settings s WHERE s.key='business_topology_worker_pin';
    value := coalesce(value,'none'); RETURN NEXT;

    check_name := 'workers seen <2m';
    SELECT string_agg(w.worker_id||'('||w.worker_status||')', ', ') INTO value
      FROM rvbbit.business_topology_workflow_workers w
     WHERE w.last_seen_at > clock_timestamp() - interval '120 seconds';
    value := coalesce(value,'none'); RETURN NEXT;

    check_name := 'relation marks';
    SELECT 'completed='||count(*) FILTER (WHERE m.outcome='completed')
           ||' failed='||count(*) FILTER (WHERE m.outcome='failed')
           ||' quarantined='||count(*) FILTER (WHERE m.consecutive_failures>=3)
      INTO value FROM rvbbit.business_topology_relation_marks m;
    RETURN NEXT;

    check_name := 'next sweep would dig';
    SELECT count(*) INTO v_n
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN rvbbit.business_topology_relation_marks m ON m.relation=format('%I.%I',n.nspname,c.relname)
     WHERE c.relkind IN ('r','p','m') AND NOT c.relispartition
       AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
       AND has_table_privilege(c.oid,'SELECT')
       -- unmarked relations have NULL here; coalesce keeps them in the count
       AND NOT coalesce(m.outcome='completed'
                        AND m.fingerprint = rvbbit.business_topology_relation_fingerprint(format('%I.%I',n.nspname,c.relname)),
                        false)
       AND coalesce(m.consecutive_failures,0) < 3;
    value := v_n::text||' relations'; RETURN NEXT;
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_topology_morning_check() IS
    'One SELECT for "did the night go well": sweep progress, run outcomes and failure classes, judgment transport, verdicts, needs, graphs staged, Hindsight hygiene, worker pin, relation marks, and what the next sweep would dig.';

-- ── A closed graph is never usable ────────────────────────────────────────
-- business_value_review_graph('rejected') left usable=true, and the summary
-- view derives lifecycle from usable before status — so a rejected graph
-- stayed "provisional", current, and searchable (pilot, 2026-09-10:
-- connected_crm_org). Closing a graph, by any verb, makes it unusable.
CREATE OR REPLACE FUNCTION rvbbit._business_value_close_makes_unusable()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.status IN ('rejected','superseded','demoted') THEN
        NEW.usable := false;
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS business_value_close_makes_unusable ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_value_close_makes_unusable
BEFORE UPDATE OF status, usable ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_value_close_makes_unusable();
UPDATE rvbbit.business_value_graph_proposals
   SET usable=false, updated_at=clock_timestamp()
 WHERE status IN ('rejected','superseded','demoted') AND usable;

-- ── Reading the results: one row per graph, in words ──────────────────────
-- The generation summary says how many; this says what. Name, one-line
-- summary, the tables it stands on, its metrics, and the run's verdict and
-- needs — enough to judge a night's output without opening the JSON.
CREATE OR REPLACE VIEW rvbbit.business_value_graph_digest AS
SELECT g.proposed_at, g.generation, g.lifecycle, g.status, g.domain_key,
       p.name, p.summary,
       g.schema_name, g.source_role, g.relation_count, g.single_table,
       p.source_relations,
       (SELECT string_agg((m->>'metric_key')||' ('||coalesce(m->>'metric_kind','?')||')', ', '
                          ORDER BY m->>'metric_kind', m->>'metric_key')
          FROM jsonb_array_elements(coalesce(p.graph->'metrics','[]'::jsonb)) m) AS metrics,
       g.metric_count, g.validated_metric_count, g.fallback,
       r.progress->'value_domain_plan_verdict'->>'status' AS verdict,
       r.progress->'value_domain_plan_verdict'->>'reason' AS verdict_reason,
       (SELECT jsonb_agg(value) FROM jsonb_array_elements_text(coalesce(r.progress->'value_domain_plan_needs','[]'::jsonb))) AS needs,
       (SELECT jsonb_agg(value) FROM jsonb_array_elements_text(coalesce(p.graph->'analytical_questions','[]'::jsonb))) AS analytical_questions,
       (SELECT jsonb_agg(value) FROM jsonb_array_elements_text(coalesce(p.graph->'gaps','[]'::jsonb))) AS gaps,
       r.result->'model_routes'->'judgment'->>'transport' AS judgment_transport,
       r.result->'model_routes'->'judgment'->>'model' AS judgment_model,
       (r.progress->>'llm_attempts')::integer AS run_llm_calls,
       g.review_reason, g.graph_id, g.workflow_run_id
  FROM rvbbit.business_value_graph_generations g
  JOIN rvbbit.business_value_graph_proposals p ON p.graph_id=g.graph_id
  LEFT JOIN rvbbit.business_topology_workflow_runs r ON r.run_id=g.workflow_run_id;
COMMENT ON VIEW rvbbit.business_value_graph_digest IS
    'One row per value graph, in words: name, summary, source tables, metrics, the run''s verdict and needs, questions and gaps, judgment route. ORDER BY proposed_at DESC to read last night''s output.';
