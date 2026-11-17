-- 0363: Make accelerator maintenance self-bounding under sustained churn.
--
-- A failed staged fold is not useful work.  Cool it down, stop the worker pass
-- after the noop, give automatic tables useful consolidation defaults, and
-- leave dirty-table freshness to one lane instead of letting maintain_storage
-- form a second blocking lock queue.

CREATE OR REPLACE VIEW rvbbit.accel_policy_effective AS
SELECT
    t.table_oid,
    c.oid::regclass::text                          AS table_name,
    coalesce(p.strategy, 'manual')                 AS strategy,
    p.freshness_target_secs,
    coalesce(p.min_interval_secs, 60)              AS min_interval_secs,
    p.daily_refresh_budget,
    coalesce(p.full_rebuild_drift_ratio, 0.5)      AS full_rebuild_drift_ratio,
    coalesce(p.lance_separate, true)               AS lance_separate,
    coalesce(p.active, true)                       AS active,
    coalesce(p.denied_engines, '{}')               AS denied_engines,
    coalesce(p.denied_layouts, '{}')               AS denied_layouts,
    (p.table_oid IS NOT NULL)                      AS explicit,
    p.note,
    p.updated_at,
    coalesce(p.max_row_groups_before_rebuild, 32)  AS max_row_groups_before_rebuild,
    coalesce(
        p.max_tombstones_before_rebuild,
        greatest(100000::bigint, ceil(greatest(c.reltuples, 0) * 2.0)::bigint)
    )                                              AS max_tombstones_before_rebuild,
    p.budget_epoch_at
FROM rvbbit.tables t
JOIN pg_class c ON c.oid = t.table_oid
LEFT JOIN rvbbit.accel_policy p ON p.table_oid = t.table_oid;

COMMENT ON VIEW rvbbit.accel_policy_effective IS
    'Effective automatic policy. NULL consolidation knobs inherit bounded defaults: 32 row groups and max(100k, 2x estimated live rows) tombstones.';

CREATE OR REPLACE FUNCTION rvbbit.accel_final_lock_retry_after(rel oid)
RETURNS timestamptz
LANGUAGE sql STABLE PARALLEL SAFE AS $fn$
    SELECT CASE
        WHEN latest.status = 'noop' AND latest.error = 'final lock busy'
        THEN latest.finished_at + interval '15 minutes'
    END
    FROM (
        SELECT o.status, o.error, o.finished_at
          FROM rvbbit.acceleration_operations o
         WHERE o.table_oid = rel
           AND o.operation = 'rebuild_acceleration'
         ORDER BY o.started_at DESC, o.id DESC
         LIMIT 1
    ) latest
$fn$;

COMMENT ON FUNCTION rvbbit.accel_final_lock_retry_after(oid) IS
    'Fixed engine-owned cooldown after a polite staged rebuild cannot acquire its final publication lock.';

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );

    IF position('final_lock_retry_after timestamptz' IN definition) = 0 THEN
        needle := '    delta_error text;';
        replacement := E'    delta_error text;\n    final_lock_retry_after timestamptz;';
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0363 could not add freshness cooldown state';
        END IF;
        definition := replace(definition, needle, replacement);

        needle := $needle$        act_reason := prop_reason;

        IF prop_action = 'skip' THEN$needle$;
        replacement := $replacement$        act_reason := prop_reason;
        SELECT rvbbit.accel_final_lock_retry_after(cand.f_oid)
          INTO final_lock_retry_after;

        IF prop_action = 'skip' THEN$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0363 could not read freshness cooldown';
        END IF;
        definition := replace(definition, needle, replacement);

        needle := $needle$        IF prop_action = 'skip' THEN
            act_reason := prop_reason;
        ELSIF cand.seconds_since_refresh IS NOT NULL$needle$;
        replacement := $replacement$        IF prop_action = 'skip' THEN
            act_reason := prop_reason;
        ELSIF final_lock_retry_after > clock_timestamp() THEN
            act_reason := format(
                'final lock retry cooling down until %s',
                to_char(final_lock_retry_after, 'YYYY-MM-DD HH24:MI:SSOF')
            );
        ELSIF cand.seconds_since_refresh IS NOT NULL$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0363 could not gate freshness cooldown';
        END IF;
        definition := replace(definition, needle, replacement);

        EXECUTE definition;
    END IF;
END
$migration$;

CREATE OR REPLACE PROCEDURE rvbbit.accel_tick_worker_pass(
    worker_slot integer,
    worker_count integer DEFAULT 2,
    tables_per_pass integer DEFAULT 4,
    lance_budget integer DEFAULT 1
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    pass_no integer;
    completed integer;
BEGIN
    IF worker_count NOT BETWEEN 1 AND 8 THEN
        RAISE EXCEPTION 'worker_count must be between 1 and 8 (got %)', worker_count;
    END IF;
    IF worker_slot NOT BETWEEN 1 AND worker_count THEN
        RAISE EXCEPTION
            'worker_slot must be between 1 and worker_count % (got %)',
            worker_count,
            worker_slot;
    END IF;
    IF tables_per_pass NOT BETWEEN 1 AND 16 THEN
        RAISE EXCEPTION
            'tables_per_pass must be between 1 and 16 (got %)',
            tables_per_pass;
    END IF;
    IF lance_budget < 0 THEN
        RAISE EXCEPTION 'lance_budget must be non-negative (got %)', lance_budget;
    END IF;

    FOR pass_no IN 1..tables_per_pass LOOP
        SELECT count(*) FILTER (
                   WHERE tick.executed AND tick.status = 'ok'
               )::integer
          INTO completed
          FROM rvbbit.accel_tick_worker(
                   worker_slot,
                   worker_count,
                   false,
                   lance_budget
               ) AS tick;

        COMMIT;
        -- A noop/deferred/failed table must not be rebuilt repeatedly inside
        -- the same cron invocation. The next heartbeat can reconsider it.
        EXIT WHEN coalesce(completed, 0) = 0;
    END LOOP;
END
$procedure$;

COMMENT ON PROCEDURE rvbbit.accel_tick_worker_pass(integer,integer,integer,integer) IS
    'Runs separately committed successful freshness actions. Noop, deferred, and failed actions stop the pass instead of causing immediate retry thrash.';

-- Existing installations still have the pre-freshness storage compaction
-- loop in their SQL function. Disable only that candidate query; retain layout,
-- generation, log, tombstone-orphan, and file-reap housekeeping.
DO $migration$
DECLARE
    definition text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit.maintain_storage(bigint,boolean)'::regprocedure
    );
    IF position('Dirty-table freshness belongs exclusively to accel_tick' IN definition) = 0
       AND position('WHERE false -- freshness lane owns dirty tables' IN definition) = 0 THEN
        IF position('WHERE ds.shadow_heap_dirty' IN definition) = 0 THEN
            RAISE EXCEPTION '0363 could not locate maintain_storage dirty loop';
        END IF;
        definition := replace(
            definition,
            'WHERE ds.shadow_heap_dirty',
            'WHERE false -- freshness lane owns dirty tables' || E'\n          AND ds.shadow_heap_dirty'
        );
        EXECUTE definition;
    END IF;
END
$migration$;

CREATE OR REPLACE VIEW rvbbit.accel_maintenance_pressure AS
SELECT f.table_oid,
       f.table_name,
       e.strategy,
       e.active,
       f.shadow_heap_dirty,
       f.heap_live_tuples,
       f.parquet_rows,
       f.row_groups,
       f.tombstones,
       f.drift_ratio,
       e.max_row_groups_before_rebuild,
       e.max_tombstones_before_rebuild,
       rvbbit.accel_final_lock_retry_after(f.table_oid) AS retry_after,
       greatest(
           extract(epoch FROM (
               rvbbit.accel_final_lock_retry_after(f.table_oid) - clock_timestamp()
           )),
           0
       )::bigint AS retry_seconds,
       coalesce(fold.fold_due, false) AS fold_due,
       fold.blocked_reason,
       latest.status AS last_rebuild_status,
       latest.error AS last_rebuild_error,
       latest.started_at AS last_rebuild_started_at,
       coalesce(failures.recent_final_lock_failures, 0) AS recent_final_lock_failures
  FROM rvbbit.accel_freshness f
  JOIN rvbbit.accel_policy_effective e USING (table_oid)
  LEFT JOIN rvbbit.accel_fold_candidates fold USING (table_oid)
  LEFT JOIN LATERAL (
      SELECT o.status, o.error, o.started_at
        FROM rvbbit.acceleration_operations o
       WHERE o.table_oid = f.table_oid
         AND o.operation = 'rebuild_acceleration'
       ORDER BY o.started_at DESC, o.id DESC
       LIMIT 1
  ) latest ON true
  LEFT JOIN LATERAL (
      SELECT count(*)::bigint AS recent_final_lock_failures
        FROM rvbbit.acceleration_operations o
       WHERE o.table_oid = f.table_oid
         AND o.operation = 'rebuild_acceleration'
         AND o.status = 'noop'
         AND o.error = 'final lock busy'
         AND o.started_at > clock_timestamp() - interval '1 hour'
  ) failures ON true;

COMMENT ON VIEW rvbbit.accel_maintenance_pressure IS
    'One operator-facing maintenance state: physical pressure, inherited defaults, final-lock cooldown, and recent blocked folds.';
