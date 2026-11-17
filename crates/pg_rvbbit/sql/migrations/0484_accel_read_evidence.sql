-- 0484: the freshness tick paces refreshes by when people read a table.
-- (It also counts a never-built table's rows from pg_class.reltuples when
-- pg_stat has none, as after a stats reset, so its first build still happens.)
--
-- 0446 tiered the cadence by the autopilot observer's evidence, which only sees
-- the SQL Calliope's agents run (rvbbit.mcp_activity), minus excluded callers and
-- any query its EXPLAIN can't resolve. Tables that apps, DataRabbit or psql read
-- all day had no evidence, so a dirty one waited 24 h however its policy was set.
-- pg_rvbbit now records, in shared
-- memory, every rvbbit table a client query reads (rvbbit.table_reads(): directly,
-- through a view, from the heap or the accelerated copy; rvbbit's own SPI work and
-- tables only written to don't count). The tick persists that in
-- rvbbit.accel_read_evidence and tiers by it. accel_autopilot_config's windows and
-- intervals keep their meaning: read in the last day → the policy's interval, in
-- the last week → 6 h, otherwise 24 h. The autopilot's own admission logic is
-- unchanged.

-- C binding for existing installations ($libdir literal: migrate() runs via SPI).
-- Guarded: a library that predates the symbol skips it, and the tick keeps the
-- evidence it has.
DO $$ BEGIN
    BEGIN
        CREATE OR REPLACE FUNCTION rvbbit.table_reads()
        RETURNS TABLE (table_oid oid, last_read_at timestamp with time zone, reads bigint)
        LANGUAGE c VOLATILE
        AS '$libdir/pg_rvbbit', 'table_reads_wrapper';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'rvbbit: table_reads binding skipped (%); read evidence stays empty until the extension library is updated', SQLERRM;
    END;
END $$;

CREATE TABLE IF NOT EXISTS rvbbit.accel_read_evidence (
    table_oid    oid PRIMARY KEY,
    last_read_at timestamptz NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE rvbbit.accel_read_evidence IS
    'Last time a client query read each rvbbit table (persisted from rvbbit.table_reads() by the freshness tick). Sets the refresh cadence tier.';

CREATE OR REPLACE FUNCTION rvbbit.accel_capture_read_evidence()
RETURNS integer
LANGUAGE plpgsql
AS $fn$
DECLARE
    n integer;
BEGIN
    INSERT INTO rvbbit.accel_read_evidence AS e (table_oid, last_read_at, recorded_at)
    SELECT r.table_oid, r.last_read_at, clock_timestamp()
      FROM rvbbit.table_reads() r
      JOIN rvbbit.tables t ON t.table_oid = r.table_oid
    ON CONFLICT (table_oid) DO UPDATE
       SET last_read_at = EXCLUDED.last_read_at,
           recorded_at = EXCLUDED.recorded_at
     WHERE EXCLUDED.last_read_at > e.last_read_at;
    GET DIAGNOSTICS n = ROW_COUNT;
    DELETE FROM rvbbit.accel_read_evidence e
     WHERE NOT EXISTS (SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = e.table_oid);
    RETURN n;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._accel_tick_batch(budget integer DEFAULT NULL::integer, dry_run boolean DEFAULT false, lance_budget integer DEFAULT 1)
 RETURNS TABLE(table_oid oid, table_name text, strategy text, action text, reason text, drift_rows bigint, drift_ratio double precision, seconds_dirty double precision, heap_seq_scans bigint, executed boolean, status text, rows_written bigint, error text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    cand record;
    acted integer := 0;
    lance_acted integer := 0;
    do_execute boolean;
    should_act boolean;
    prop_action text;
    prop_reason text;
    act_reason text;
    baseline_missing boolean;
    res jsonb;
    last_scans bigint;
    used_today integer;
    is_lance boolean;
    sweep_id bigint;
    sweep_started timestamptz;
    table_started timestamptz;
    original_application_name text;
    operation_id bigint;
    written_row_groups bigint;
    candidate_count integer := 0;
    deferred_count integer := 0;
    skipped_count integer := 0;
    failed_count integer := 0;
    total_rows_written bigint := 0;
    live_candidate record;
    worker_slot_setting text := current_setting('rvbbit.accel_tick_worker_slot', true);
    worker_count_setting text := current_setting('rvbbit.accel_tick_worker_count', true);
    worker_slot integer := CASE
        WHEN worker_slot_setting ~ '^[0-9]+$' THEN worker_slot_setting::integer
        ELSE 0
    END;
    worker_count integer := CASE
        WHEN worker_count_setting ~ '^[0-9]+$' THEN worker_count_setting::integer
        ELSE 1
    END;
    parallel_worker boolean := false;
    fallback_deferred boolean := false;
    delta_error text;
    final_lock_retry_after timestamptz;
    claimed_heavy_slot integer := 0;
    heavy_capacity_deferred boolean := false;
    deferred_rows_reported integer := 0;
    -- Reads set the cadence (0484; 0446 used the autopilot observer's volume-gated
    -- evidence). Tables read in the last day keep their policy interval; tables not
    -- read for a week wait tier_warm_interval, longer than that tier_cold_interval.
    -- Cubes and 'continuous' policies opt out.
    tier_hot_hours integer := 24;
    tier_warm_days integer := 7;
    tier_warm_interval integer := 21600;
    tier_cold_interval integer := 86400;
    tier_name text;
    effective_min_interval integer;
BEGIN
    SELECT coalesce(c.tick_hot_window_hours, 24), coalesce(c.tick_warm_window_days, 7),
           coalesce(c.tick_warm_interval_secs, 21600), coalesce(c.tick_cold_interval_secs, 86400)
      INTO tier_hot_hours, tier_warm_days, tier_warm_interval, tier_cold_interval
      FROM rvbbit.accel_autopilot_config c WHERE c.singleton;
    IF NOT dry_run THEN
        BEGIN
            PERFORM rvbbit.accel_capture_read_evidence();
        EXCEPTION WHEN OTHERS THEN
            -- Advisory: a library without rvbbit.table_reads() keeps the last evidence.
            NULL;
        END;
    END IF;
    parallel_worker := worker_count BETWEEN 1 AND 8
        AND worker_slot BETWEEN 1 AND worker_count;
    IF NOT dry_run THEN
        IF parallel_worker THEN
            -- Shared gate lets the two freshness slots coexist while the
            -- existing exclusive fold/legacy gate still keeps lanes apart.
            IF NOT pg_try_advisory_xact_lock_shared(1381187156, 7) THEN
                RETURN;
            END IF;
            IF NOT pg_try_advisory_xact_lock(
                1381187156,
                70 + worker_slot
            ) THEN
                RETURN;
            END IF;
        ELSIF NOT pg_try_advisory_xact_lock(1381187156, 7) THEN
            RETURN;
        END IF;
    END IF;

    IF NOT dry_run THEN
        original_application_name := current_setting('application_name', true);
        sweep_id := nextval('rvbbit.accel_sweep_id_seq');
        sweep_started := clock_timestamp();
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh s=%s w=%s/%s scan',
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END
                ),
                63
            ),
            true
        );
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'sweep_started',
            p_status => 'running',
            p_details => jsonb_build_object(
                'budget', budget,
                'lance_budget', lance_budget,
                'mode', CASE
                    WHEN parallel_worker THEN 'parallel_freshness'
                    ELSE 'freshness_only'
                END,
                'worker_slot', CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                'worker_count', CASE WHEN parallel_worker THEN worker_count ELSE 1 END
            )
        );
    END IF;

    -- Preserve the warm-prior heartbeat added by migration 0124.
    IF NOT dry_run
       AND (NOT parallel_worker OR worker_slot = 1)
       AND lower(coalesce(nullif(current_setting('rvbbit.route_gpu_gqe_prior', true), ''), 'off'))
           NOT IN ('off','false','0','no') THEN
        BEGIN
            PERFORM rvbbit.warm_gpu_gqe();
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;

    FOR cand IN
        SELECT f.table_oid AS f_oid,
               f.table_name AS f_name,
               f.shadow_heap_dirty,
               f.seconds_dirty,
               f.seconds_since_refresh,
               f.row_groups,
               -- pg_stat's count is 0 after a stats reset (crash, pg_upgrade);
               -- reltuples survives, so a never-built table still gets built.
               greatest(coalesce(f.heap_live_tuples, 0), (SELECT greatest(c.reltuples, 0) FROM pg_catalog.pg_class c WHERE c.oid = f.table_oid)::bigint) AS heap_live_tuples,
               f.tombstones,
               f.drift_rows,
               f.drift_ratio,
               f.heap_seq_scans,
               f.lance_accelerated,
               rvbbit.current_replacement_pending(f.table_oid)
                   AS current_replacement_pending,
               e.strategy,
               e.freshness_target_secs,
               e.min_interval_secs,
               e.daily_refresh_budget,
               e.budget_epoch_at,
               e.full_rebuild_drift_ratio,
               e.lance_separate,
               coalesce(ev.last_read_at >= clock_timestamp() - make_interval(hours => tier_hot_hours), false)
                   AS evidence_hot,
               coalesce(ev.last_read_at >= clock_timestamp() - make_interval(days => tier_warm_days), false)
                   AS evidence_warm
          FROM rvbbit.accel_freshness f
          JOIN rvbbit.accel_policy_effective e ON e.table_oid = f.table_oid
          LEFT JOIN rvbbit.accel_read_evidence ev ON ev.table_oid = f.table_oid
         WHERE e.active
           AND e.strategy <> 'manual'
           -- capacity-eligible candidates only: clean accelerators do not
           -- belong in every worker's execution scan.
           AND (
               coalesce(
                   rvbbit.current_replacement_pending(f.table_oid),
                   false
               )
               OR coalesce(f.shadow_heap_dirty, false)
               OR (
                   coalesce(f.row_groups, 0) = 0
                   AND greatest(coalesce(f.heap_live_tuples, 0), (SELECT greatest(c.reltuples, 0) FROM pg_catalog.pg_class c WHERE c.oid = f.table_oid)::bigint) > 0
               )
           )
         ORDER BY evidence_hot DESC,
                  evidence_warm DESC,
                  rvbbit.current_replacement_pending(f.table_oid) DESC,
                  CASE
                      WHEN parallel_worker THEN
                          (mod(f.table_oid::bigint, worker_count::bigint)
                              + 1 = worker_slot)::integer
                      ELSE 1
                  END DESC,
                  (f.drift_rows * (1 + f.heap_seq_scans)) DESC NULLS LAST,
                  f.seconds_dirty DESC NULLS LAST,
                  f.table_oid
    LOOP
        candidate_count := candidate_count + 1;
        baseline_missing := cand.row_groups = 0 AND cand.heap_live_tuples > 0;

        IF cand.current_replacement_pending THEN
            -- A current-only TRUNCATE is an invalidated baseline, not a delta.
            prop_action := 'full';
            prop_reason := 'current replacement pending';
        ELSIF cand.shadow_heap_dirty THEN
            IF cand.drift_ratio IS NULL THEN
                prop_action := 'full';
                prop_reason := 'no accelerator baseline';
            ELSIF cand.drift_ratio >= cand.full_rebuild_drift_ratio THEN
                prop_action := 'full';
                prop_reason := format(
                    'drift_ratio %s >= %s',
                    round(cand.drift_ratio::numeric, 6),
                    round(cand.full_rebuild_drift_ratio::numeric, 6)
                );
            ELSE
                prop_action := 'delta';
                prop_reason := 'dirty';
            END IF;
        ELSIF baseline_missing THEN
            prop_action := 'delta';
            prop_reason := 'no accelerator baseline';
        ELSE
            prop_action := 'skip';
            prop_reason := 'clean';
        END IF;

        is_lance := false; -- Lance runtime removed; compatibility metadata is inert.
        should_act := false;
        act_reason := prop_reason;
        IF cand.strategy = 'continuous' OR cand.f_name LIKE 'cubes.%' OR cand.evidence_hot THEN
            tier_name := CASE WHEN cand.evidence_hot THEN 'hot' ELSE 'exempt' END;
            effective_min_interval := cand.min_interval_secs;
        ELSIF cand.evidence_warm THEN
            tier_name := 'warm';
            effective_min_interval := greatest(cand.min_interval_secs, tier_warm_interval);
        ELSE
            tier_name := 'cold';
            effective_min_interval := greatest(cand.min_interval_secs, tier_cold_interval);
        END IF;
        SELECT rvbbit.accel_final_lock_retry_after(cand.f_oid)
          INTO final_lock_retry_after;

        IF prop_action = 'skip' THEN
            act_reason := prop_reason;
        ELSIF final_lock_retry_after > clock_timestamp() THEN
            act_reason := format(
                'final lock retry cooling down until %s',
                to_char(final_lock_retry_after, 'YYYY-MM-DD HH24:MI:SSOF')
            );
        ELSIF cand.seconds_since_refresh IS NOT NULL
              AND cand.seconds_since_refresh < effective_min_interval THEN
            act_reason := format('min_interval %ss not elapsed (%s evidence tier)', effective_min_interval, tier_name);
        ELSE
            IF cand.strategy IN ('scheduled', 'continuous') THEN
                should_act := true;
                act_reason := prop_reason;
            ELSIF cand.strategy = 'target' THEN
                IF baseline_missing THEN
                    should_act := true;
                    act_reason := prop_reason || '; target requires a baseline';
                ELSIF cand.freshness_target_secs IS NULL
                   OR coalesce(cand.seconds_dirty, 0) >= cand.freshness_target_secs THEN
                    should_act := true;
                    act_reason := format(
                        '%s; stale %ss >= target %ss',
                        prop_reason,
                        round(coalesce(cand.seconds_dirty, 0))::int,
                        coalesce(cand.freshness_target_secs, 0)
                    );
                ELSE
                    act_reason := format(
                        'within target (%ss < %ss)',
                        round(coalesce(cand.seconds_dirty, 0))::int,
                        cand.freshness_target_secs
                    );
                END IF;
            ELSIF cand.strategy = 'demand' THEN
                SELECT r.heap_seq_scans
                  INTO last_scans
                  FROM rvbbit.accel_tick_runs r
                 WHERE r.table_oid = cand.f_oid
                 ORDER BY r.ran_at DESC
                 LIMIT 1;

                IF baseline_missing
                   AND cand.heap_seq_scans > coalesce(last_scans, 0) THEN
                    should_act := true;
                    act_reason := prop_reason || '; observed slow-path demand';
                ELSIF last_scans IS NOT NULL
                      AND cand.heap_seq_scans > last_scans THEN
                    should_act := true;
                    act_reason := prop_reason || '; demand grew on slow path';
                ELSE
                    act_reason := CASE
                        WHEN last_scans IS NULL THEN 'demand baseline'
                        ELSE 'no new slow-path demand'
                    END;
                END IF;
            END IF;

            -- Policy lifecycle budget epoch: keep historical receipts without
            -- charging a newly enrolled automation policy for old work.
            IF should_act AND cand.daily_refresh_budget IS NOT NULL THEN
                used_today := rvbbit.accel_budget_spent(cand.f_oid, cand.budget_epoch_at);
                IF used_today >= cand.daily_refresh_budget THEN
                    -- An exhausted budget slows a table down; it never lets the
                    -- accelerator rot. Past the overdue horizon, or with no
                    -- baseline at all, the refresh proceeds and says why.
                    IF baseline_missing
                       OR coalesce(cand.seconds_dirty, 0)
                          >= rvbbit.accel_budget_overdue_secs(
                                 cand.freshness_target_secs, cand.min_interval_secs
                             ) THEN
                        act_reason := format(
                            '%s; daily budget %s spent but overdue (%ss >= %ss), refreshing anyway',
                            act_reason,
                            cand.daily_refresh_budget,
                            round(coalesce(cand.seconds_dirty, 0))::int,
                            rvbbit.accel_budget_overdue_secs(
                                cand.freshness_target_secs, cand.min_interval_secs
                            )
                        );
                    ELSE
                        should_act := false;
                        act_reason := format(
                            'daily budget %s spent; deferring until dirty >= %ss (now %ss)',
                            cand.daily_refresh_budget,
                            rvbbit.accel_budget_overdue_secs(
                                cand.freshness_target_secs, cand.min_interval_secs
                            ),
                            round(coalesce(cand.seconds_dirty, 0))::int
                        );
                    END IF;
                END IF;
            END IF;
        END IF;

        do_execute := should_act;
        IF do_execute AND budget IS NOT NULL AND acted >= budget THEN
            do_execute := false;
            act_reason := 'tick budget reached';
        ELSIF do_execute AND is_lance AND lance_acted >= lance_budget THEN
            do_execute := false;
            act_reason := 'lance budget reached';
        END IF;

        _accel_tick_batch.table_oid := cand.f_oid;
        _accel_tick_batch.table_name := cand.f_name;
        _accel_tick_batch.strategy := cand.strategy;
        _accel_tick_batch.drift_rows := cand.drift_rows;
        _accel_tick_batch.drift_ratio := cand.drift_ratio;
        _accel_tick_batch.seconds_dirty := cand.seconds_dirty;
        _accel_tick_batch.heap_seq_scans := cand.heap_seq_scans;
        _accel_tick_batch.rows_written := NULL;
        _accel_tick_batch.error := NULL;

        -- Claim shared heavy capacity before the table claim.  A worker that
        -- loses capacity must not retain advisory locks on every heavy table
        -- while it searches for independent delta work.
        IF do_execute
           AND NOT dry_run
           AND parallel_worker
           AND (prop_action = 'full' OR is_lance) THEN
            IF claimed_heavy_slot = 0 AND NOT heavy_capacity_deferred THEN
                claimed_heavy_slot :=
                    rvbbit._try_claim_accel_heavy_slot();
                heavy_capacity_deferred := claimed_heavy_slot = 0;
            END IF;
            IF heavy_capacity_deferred THEN
                do_execute := false;
                act_reason := prop_reason
                    || '; heavy maintenance slots busy';
            END IF;
        END IF;

        IF do_execute AND NOT dry_run AND parallel_worker THEN
            -- Claim before entering refresh/rebuild. The lower-level lock is
            -- re-entrant for rebuild/compact and harmless for delta refresh.
            IF NOT pg_try_advisory_xact_lock(
                (1380336724::bigint << 32) | cand.f_oid::bigint
            ) THEN
                do_execute := false;
                act_reason := 'table maintenance claimed by another worker';
            ELSE
                -- A manual action or another worker may have committed after
                -- this loop's candidate snapshot. Re-read cheap eligibility
                -- facts after the claim and never act on an obsolete policy.
                SELECT f.shadow_heap_dirty,
                       f.seconds_since_refresh,
                       f.row_groups,
                       greatest(coalesce(f.heap_live_tuples, 0), (SELECT greatest(c.reltuples, 0) FROM pg_catalog.pg_class c WHERE c.oid = f.table_oid)::bigint) AS heap_live_tuples,
                       rvbbit.current_replacement_pending(f.table_oid)
                           AS current_replacement_pending,
                       e.active,
                       e.strategy,
                       e.min_interval_secs,
                       e.daily_refresh_budget,
                       e.budget_epoch_at
                  INTO live_candidate
                  FROM rvbbit.accel_freshness f
                  JOIN rvbbit.accel_policy_effective e
                    ON e.table_oid = f.table_oid
                 WHERE f.table_oid = cand.f_oid;

                IF NOT FOUND
                   OR NOT coalesce(live_candidate.active, false)
                   OR live_candidate.strategy = 'manual' THEN
                    should_act := false;
                    do_execute := false;
                    act_reason := 'table or policy changed before worker claim';
                ELSIF live_candidate.strategy IS DISTINCT FROM cand.strategy THEN
                    do_execute := false;
                    act_reason := 'refresh strategy changed before worker claim';
                ELSIF NOT (
                    coalesce(live_candidate.current_replacement_pending, false)
                    OR coalesce(live_candidate.shadow_heap_dirty, false)
                    OR (
                        coalesce(live_candidate.row_groups, 0) = 0
                        AND coalesce(live_candidate.heap_live_tuples, 0) > 0
                    )
                ) THEN
                    should_act := false;
                    do_execute := false;
                    act_reason := 'candidate became clean before worker claim';
                ELSIF live_candidate.seconds_since_refresh IS NOT NULL
                      AND live_candidate.seconds_since_refresh
                          < greatest(live_candidate.min_interval_secs, effective_min_interval) THEN
                    do_execute := false;
                    act_reason := format(
                        'min_interval %ss not elapsed after worker claim (%s evidence tier)',
                        greatest(live_candidate.min_interval_secs, effective_min_interval), tier_name
                    );
                ELSE
                    -- Recheck the only mutable accounting gate after claiming.
                    IF live_candidate.daily_refresh_budget IS NOT NULL THEN
                        used_today := rvbbit.accel_budget_spent(cand.f_oid, live_candidate.budget_epoch_at);
                        IF used_today >= live_candidate.daily_refresh_budget
                           AND NOT baseline_missing
                           AND coalesce(cand.seconds_dirty, 0)
                               < rvbbit.accel_budget_overdue_secs(
                                     cand.freshness_target_secs, live_candidate.min_interval_secs
                                 ) THEN
                            do_execute := false;
                            act_reason := format(
                                'daily budget %s spent after worker claim; deferring until dirty >= %ss',
                                live_candidate.daily_refresh_budget,
                                rvbbit.accel_budget_overdue_secs(
                                    cand.freshness_target_secs, live_candidate.min_interval_secs
                                )
                            );
                        END IF;
                    END IF;
                END IF;
            END IF;
        END IF;

        IF NOT do_execute THEN
            _accel_tick_batch.action := CASE
                WHEN should_act THEN prop_action
                ELSE 'skip'
            END;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := false;
            _accel_tick_batch.status := CASE
                WHEN should_act THEN 'deferred'
                ELSE 'skip'
            END;
            IF should_act THEN
                deferred_count := deferred_count + 1;
            ELSE
                skipped_count := skipped_count + 1;
            END IF;
            IF NOT dry_run THEN
                -- Sweep totals retain the complete counts, while the row API
                -- and append-only run log receive only a small diagnostic
                -- sample.  This preserves useful direct-call evidence without
                -- multiplying one blocked pass into thousands of rows.
                IF NOT should_act THEN
                    CONTINUE;
                END IF;
                IF deferred_rows_reported >= 8 THEN
                    CONTINUE;
                END IF;
                deferred_rows_reported := deferred_rows_reported + 1;

                INSERT INTO rvbbit.accel_tick_runs (
                    table_oid, table_name, strategy, action, reason,
                    drift_rows, heap_seq_scans, executed, status
                ) VALUES (
                    cand.f_oid, cand.f_name, cand.strategy,
                    _accel_tick_batch.action, act_reason, cand.drift_rows,
                    cand.heap_seq_scans, false, _accel_tick_batch.status
                );
            END IF;
            RETURN NEXT;
            CONTINUE;
        END IF;

        acted := acted + 1;
        IF is_lance THEN
            lance_acted := lance_acted + 1;
        END IF;

        IF dry_run THEN
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := false;
            _accel_tick_batch.status := 'planned';
            RETURN NEXT;
            CONTINUE;
        END IF;

        table_started := clock_timestamp();
        res := NULL;
        operation_id := NULL;
        written_row_groups := NULL;
        fallback_deferred := false;
        delta_error := NULL;
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh t=%s o=%s a=%s s=%s w=%s/%s %s',
                    round(extract(epoch FROM table_started)::numeric, 3),
                    cand.f_oid,
                    prop_action,
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                    cand.f_name
                ),
                63
            ),
            true
        );
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'table_started',
            p_table_oid => cand.f_oid,
            p_table_name => cand.f_name,
            p_strategy => cand.strategy,
            p_action => prop_action,
            p_reason => act_reason,
            p_status => 'running',
            p_details => jsonb_strip_nulls(jsonb_build_object(
                'drift_rows', cand.drift_rows,
                'drift_ratio', cand.drift_ratio,
                'seconds_dirty', cand.seconds_dirty,
                'heap_seq_scans', cand.heap_seq_scans,
                'row_groups', cand.row_groups,
                'tombstones', cand.tombstones,
                'heavy_slot', nullif(
                    current_setting(
                        'rvbbit.accel_maintenance_heavy_slot', true
                    ),
                    ''
                )::integer,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            ))
        );

        BEGIN
            IF prop_action = 'full' THEN
                res := rvbbit.rebuild_acceleration(cand.f_oid::regclass, true);
            ELSE
                BEGIN
                    res := rvbbit.refresh_acceleration(cand.f_oid::regclass, true);
                EXCEPTION WHEN OTHERS THEN
                    delta_error := SQLERRM;
                    prop_action := 'full';
                    act_reason := act_reason || ' (delta->full: ' || delta_error || ')';
                    IF parallel_worker
                       AND rvbbit._try_claim_accel_heavy_slot() = 0 THEN
                        -- Do not wait behind a long full rebuild. A later tick
                        -- can retry the delta or acquire the heavy lane.
                        fallback_deferred := true;
                        act_reason := act_reason || '; heavy maintenance slots busy';
                        res := jsonb_build_object(
                            'status', 'deferred',
                            'reason', act_reason
                        );
                    ELSE
                        PERFORM set_config(
                            'application_name',
                            left(
                                format(
                                    'rvbbit/fresh t=%s o=%s a=full s=%s w=%s/%s %s',
                                    round(extract(epoch FROM table_started)::numeric, 3),
                                    cand.f_oid,
                                    sweep_id,
                                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END,
                                    cand.f_name
                                ),
                                63
                            ),
                            true
                        );
                        res := rvbbit.rebuild_acceleration(cand.f_oid::regclass, true);
                    END IF;
                END;
            END IF;
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            IF fallback_deferred THEN
                _accel_tick_batch.executed := false;
                _accel_tick_batch.status := 'deferred';
                _accel_tick_batch.rows_written := 0;
                acted := greatest(acted - 1, 0);
                IF is_lance THEN
                    lance_acted := greatest(lance_acted - 1, 0);
                END IF;
                deferred_count := deferred_count + 1;
            ELSE
                _accel_tick_batch.executed := true;
                _accel_tick_batch.status := coalesce(res->>'status', 'ok');
                _accel_tick_batch.rows_written := coalesce((res->>'rows_written')::bigint, 0);
                operation_id := nullif(res->>'operation_id', '')::bigint;
                written_row_groups := nullif(res->>'row_groups_written', '')::bigint;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            _accel_tick_batch.action := prop_action;
            _accel_tick_batch.reason := act_reason;
            _accel_tick_batch.executed := true;
            _accel_tick_batch.status := 'failed';
            _accel_tick_batch.error := SQLERRM;
        END;

        IF _accel_tick_batch.status = 'failed' THEN
            failed_count := failed_count + 1;
        END IF;
        total_rows_written := total_rows_written + coalesce(_accel_tick_batch.rows_written, 0);

        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'table_finished',
            p_table_oid => cand.f_oid,
            p_table_name => cand.f_name,
            p_strategy => cand.strategy,
            p_action => _accel_tick_batch.action,
            p_reason => _accel_tick_batch.reason,
            p_status => _accel_tick_batch.status,
            p_operation_id => operation_id,
            p_elapsed_ms => greatest(
                0,
                extract(epoch FROM (clock_timestamp() - table_started)) * 1000.0
            ),
            p_rows_written => _accel_tick_batch.rows_written,
            p_row_groups_written => written_row_groups,
            p_details => jsonb_strip_nulls(jsonb_build_object(
                'error', _accel_tick_batch.error,
                'result', res,
                'heavy_slot', nullif(
                    current_setting(
                        'rvbbit.accel_maintenance_heavy_slot', true
                    ),
                    ''
                )::integer,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            ))
        );
        PERFORM set_config(
            'application_name',
            left(
                format(
                    'rvbbit/fresh s=%s w=%s/%s scan',
                    sweep_id,
                    CASE WHEN parallel_worker THEN worker_slot ELSE 1 END,
                    CASE WHEN parallel_worker THEN worker_count ELSE 1 END
                ),
                63
            ),
            true
        );

        INSERT INTO rvbbit.accel_tick_runs (
            table_oid, table_name, strategy, action, reason, drift_rows,
            heap_seq_scans, executed, status, rows_written, error
        ) VALUES (
            cand.f_oid, cand.f_name, cand.strategy, _accel_tick_batch.action,
            _accel_tick_batch.reason, cand.drift_rows, cand.heap_seq_scans,
            _accel_tick_batch.executed,
            _accel_tick_batch.status, _accel_tick_batch.rows_written, _accel_tick_batch.error
        );
        RETURN NEXT;
        -- One worker invocation owns at most one table action.  The outer
        -- worker-pass procedure commits before requesting the next table.
        EXIT;
    END LOOP;

    IF NOT dry_run THEN
        PERFORM rvbbit._accel_log_event(
            p_sweep_id => sweep_id,
            p_lane => 'freshness',
            p_event_type => 'sweep_finished',
            p_status => CASE WHEN failed_count > 0 THEN 'partial' ELSE 'ok' END,
            p_elapsed_ms => greatest(
                0,
                extract(epoch FROM (clock_timestamp() - sweep_started)) * 1000.0
            ),
            p_rows_written => total_rows_written,
            p_details => jsonb_build_object(
                'candidates', candidate_count,
                'tables_executed', acted,
                'tables_failed', failed_count,
                'tables_deferred', deferred_count,
                'tables_skipped', skipped_count,
                'lance_tables_executed', lance_acted,
                'heavy_slot_limit',
                    rvbbit.accel_maintenance_heavy_slots()
            )
        );
        PERFORM set_config(
            'application_name',
            coalesce(original_application_name, ''),
            true
        );
    END IF;

    RETURN;
END;
$function$;

