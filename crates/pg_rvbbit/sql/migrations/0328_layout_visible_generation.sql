-- 0328: layout tick reasons about the visible generation, and reconciles stale
-- Vortex layout status.
--
-- Companion to the compact.rs change that scoped layout builders/validators
-- to the visible canonical generation. Two SQL-side leftovers:
--   * rvbbit.layout_tick_candidates and _build_automatic_vortex_target summed
--     every retained generation for target rows/bytes, so their telemetry
--     disagreed with the layouts they scheduled;
--   * a Vortex layout whose status row was written before a snapshot
--     rebuild keeps that stale row (and its variants for retired row groups)
--     until something happens to rebuild it — the observatory then reports
--     "Variant behind" on tables that are, in fact, idle and current.
-- rvbbit.reconcile_stale_vortex_layouts() enqueues an automatic Vortex pass
-- for such tables (bounded per call); worker slot 1 runs it at the start of
-- every layout_tick_worker_pass, so the tick self-heals within a minute.

CREATE OR REPLACE FUNCTION rvbbit.reconcile_stale_vortex_layouts(
    max_tables integer DEFAULT 20
) RETURNS integer
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    stale record;
    enqueued integer := 0;
BEGIN
    FOR stale IN
        SELECT s.table_oid,
               s.expected_rows,
               v.visible_rows,
               v.visible_row_groups
          FROM rvbbit.layout_variant_status s
          JOIN LATERAL (
              SELECT coalesce(sum(rg.n_rows), 0)::bigint AS visible_rows,
                     count(*)::bigint AS visible_row_groups
                FROM rvbbit.row_groups_visible rg
               WHERE rg.table_oid = s.table_oid
          ) v ON true
         WHERE s.layout = 'vortex_scan'
           AND v.visible_row_groups > 0
           AND EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = s.table_oid)
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.variant_build_queue q WHERE q.table_oid = s.table_oid
           )
           AND (
               s.expected_rows IS DISTINCT FROM v.visible_rows
               OR EXISTS (
                   SELECT 1
                     FROM rvbbit.row_group_variants rv
                    WHERE rv.table_oid = s.table_oid
                      AND rv.layout = 'vortex_scan'
                      AND NOT EXISTS (
                          SELECT 1 FROM rvbbit.row_groups_visible rg
                           WHERE rg.table_oid = rv.table_oid AND rg.rg_id = rv.rg_id
                      )
               )
           )
         ORDER BY s.refreshed_at NULLS FIRST
         LIMIT greatest(coalesce(max_tables, 20), 0)
    LOOP
        PERFORM rvbbit.enqueue_variant_build(
            stale.table_oid::regclass,
            'layout_status_stale',
            5
        );
        enqueued := enqueued + 1;
    END LOOP;
    RETURN enqueued;
END;
$$;

COMMENT ON FUNCTION rvbbit.reconcile_stale_vortex_layouts(integer) IS
    'Enqueue an automatic Vortex pass for tables whose vortex_scan status no longer matches the visible generation (row-count drift or variants for retired row groups). Bounded per call; idempotent while the queue entry exists.';

-- Visible-generation telemetry in the tick candidates view (same shape as 0272).
CREATE OR REPLACE VIEW rvbbit.layout_tick_candidates AS
WITH canonical AS (
    SELECT t.table_oid,
           t.table_oid::regclass::text AS table_name,
           coalesce(max(rg.generation), 0)::bigint AS target_generation,
           count(rg.rg_id)::bigint AS target_row_groups,
           coalesce(sum(rg.n_rows), 0)::bigint AS target_rows,
           coalesce(sum(rg.n_bytes), 0)::bigint AS target_bytes,
           max(rg.created_at) AS newest_row_group
      FROM rvbbit.tables t
      JOIN pg_catalog.pg_class c
        ON c.oid = t.table_oid
      LEFT JOIN rvbbit.row_groups_visible rg
        ON rg.table_oid = t.table_oid
     WHERE c.relkind IN ('r', 'p')
       AND coalesce(t.acceleration_enabled, true)
     GROUP BY t.table_oid
), workload AS (
    SELECT w.table_oid,
           w.accepted_layouts,
           w.pending_layouts,
           w.accepted_layout_names,
           w.pending_layout_names,
           w.priority,
           w.recommendation_updated_at
      FROM rvbbit.workload_layout_tick_candidates w
)
SELECT canonical.table_oid,
       canonical.table_name,
       canonical.target_generation,
       canonical.target_row_groups,
       canonical.target_rows,
       canonical.target_bytes,
       canonical.newest_row_group,
       (queue.table_oid IS NOT NULL) AS vortex_pending,
       queue.reason AS vortex_reason,
       queue.priority AS vortex_priority,
       queue.requested_at AS vortex_requested_at,
       queue.available_at AS vortex_available_at,
       queue.attempts AS vortex_attempts,
       (workload.table_oid IS NOT NULL) AS workload_pending,
       coalesce(workload.accepted_layouts, 0)::integer AS accepted_layouts,
       coalesce(workload.pending_layouts, 0)::integer AS pending_workload_layouts,
       coalesce(workload.accepted_layout_names, ARRAY[]::text[])
           AS accepted_layout_names,
       coalesce(workload.pending_layout_names, ARRAY[]::text[])
           AS pending_workload_layout_names,
       (
           CASE WHEN queue.table_oid IS NOT NULL
                THEN ARRAY['vortex_scan']::text[]
                ELSE ARRAY[]::text[] END
           || coalesce(workload.pending_layout_names, ARRAY[]::text[])
       ) AS target_layouts,
       (
           CASE WHEN queue.table_oid IS NOT NULL THEN 1 ELSE 0 END
           + coalesce(workload.pending_layouts, 0)
       )::integer AS pending_layouts,
       greatest(
           coalesce(queue.priority, 0)::double precision,
           coalesce(workload.priority, 0)::double precision
       ) AS priority,
       least(
           queue.requested_at,
           CASE WHEN workload.table_oid IS NOT NULL
                THEN greatest(
                    coalesce(canonical.newest_row_group, '-infinity'::timestamptz),
                    workload.recommendation_updated_at
                )
                ELSE NULL END
       ) AS pending_since
  FROM canonical
  LEFT JOIN rvbbit.variant_build_queue queue
    ON queue.table_oid = canonical.table_oid
  LEFT JOIN workload
    ON workload.table_oid = canonical.table_oid
 WHERE queue.table_oid IS NOT NULL
    OR workload.table_oid IS NOT NULL;

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    -- _build_automatic_vortex_target: current_* over the visible generation.
    definition := pg_get_functiondef('rvbbit._build_automatic_vortex_target(oid)'::regprocedure);
    needle := $needle$      FROM pg_catalog.pg_class class
      LEFT JOIN rvbbit.row_groups groups
        ON groups.table_oid = class.oid$needle$;
    replacement := $replacement$      FROM pg_catalog.pg_class class
      LEFT JOIN rvbbit.row_groups_visible groups
        ON groups.table_oid = class.oid$replacement$;
    IF position(needle IN definition) > 0 THEN
        EXECUTE replace(definition, needle, replacement);
    ELSIF position('rvbbit.row_groups_visible groups' IN definition) = 0 THEN
        RAISE EXCEPTION '0328 could not find the canonical snapshot in rvbbit._build_automatic_vortex_target';
    END IF;

    -- _build_automatic_vortex_target: the post-build proof that every canonical
    -- row group carries a Vortex variant must range over the visible generation,
    -- or a correct visible-only rebuild is rejected (and rolled back) whenever
    -- retired generations linger.
    definition := pg_get_functiondef('rvbbit._build_automatic_vortex_target(oid)'::regprocedure);
    needle := $needle$                       SELECT 1
                         FROM rvbbit.row_groups groups
                        WHERE groups.table_oid = rel$needle$;
    replacement := $replacement$                       SELECT 1
                         FROM rvbbit.row_groups_visible groups
                        WHERE groups.table_oid = rel$replacement$;
    IF position(needle IN definition) > 0 THEN
        EXECUTE replace(definition, needle, replacement);
    ELSIF position('rvbbit.row_groups_visible groups
                        WHERE groups.table_oid = rel' IN definition) = 0 THEN
        RAISE EXCEPTION '0328 could not find the Vortex coverage proof in rvbbit._build_automatic_vortex_target';
    END IF;

    -- variant_tick (pre-unified lane, still installed): same two assumptions.
    IF to_regprocedure('rvbbit.variant_tick(integer,boolean)') IS NOT NULL THEN
        definition := pg_get_functiondef('rvbbit.variant_tick(integer,boolean)'::regprocedure);
        definition := replace(definition,
            $old$          FROM rvbbit.row_groups rg_current
         WHERE rg_current.table_oid = cand.table_oid;$old$,
            $new$          FROM rvbbit.row_groups_visible rg_current
         WHERE rg_current.table_oid = cand.table_oid;$new$);
        definition := replace(definition,
            $old$                             FROM rvbbit.row_groups rg
                            WHERE rg.table_oid = cand.table_oid$old$,
            $new$                             FROM rvbbit.row_groups_visible rg
                            WHERE rg.table_oid = cand.table_oid$new$);
        EXECUTE definition;
    END IF;

    -- layout_tick_worker_pass: slot 1 reconciles stale Vortex status first.
    definition := pg_get_functiondef('rvbbit.layout_tick_worker_pass(integer,integer,integer)'::regprocedure);
    needle := $needle$    FOR pass_no IN 1..tables_per_pass LOOP
        SELECT count(*) FILTER (WHERE tick.executed)::integer$needle$;
    replacement := $replacement$    IF worker_slot = 1 THEN
        BEGIN
            PERFORM rvbbit.reconcile_stale_vortex_layouts(20);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'rvbbit.layout_tick_worker_pass: stale layout reconcile skipped: %', SQLERRM;
        END;
        COMMIT;
    END IF;

    FOR pass_no IN 1..tables_per_pass LOOP
        SELECT count(*) FILTER (WHERE tick.executed)::integer$replacement$;
    IF position(needle IN definition) > 0 THEN
        EXECUTE replace(definition, needle, replacement);
    ELSIF position('reconcile_stale_vortex_layouts' IN definition) = 0 THEN
        RAISE EXCEPTION '0328 could not find the pass loop in rvbbit.layout_tick_worker_pass';
    END IF;
END;
$migration$;
