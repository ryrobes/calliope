-- 0501: a rebuild's watermark stops below the transactions still open at its snapshot.
--
-- rebuild_acceleration recorded watermark_after = pg_snapshot_xmax - 1, and the
-- heap-tail overlay and delta refreshes read only heap rows whose xmin is above
-- the watermark. A transaction open at the rebuild's snapshot that wrote the
-- table after the rebuild committed (its earlier statements had already given
-- it an xid) was below the watermark: its new row versions were in neither the
-- accelerated copy nor the tail, while its UPDATE tombstoned the old version in
-- the copy. On the hub, rwx_etl.run_log lost a row this way when a delta-fanout
-- rebuild ran during a 50-second CALL rwx_etl.status_poll(): count(*) through
-- the accelerated path was 11776, against 11777 rows in the heap.
--
-- The watermark now stops below the oldest transaction the rebuild's snapshot
-- saw in progress (below its xmax when there was none), and, when there was
-- one, acceleration_state.covered_snapshot records that snapshot: the copy also
-- holds the rows of the committed transactions above the watermark that it
-- sees, so the heap tail, delta refreshes (export_to_parquet_xid_range and
-- refresh_layout_variants_xid_range gain a covered_snapshot argument) and the
-- pending-rows probe leave those out instead of reading them twice. A refresh
-- drops covered_snapshot once its watermark passes the snapshot's xmax.
--
-- A table a past rebuild already left short stays short until its next rebuild;
-- compare count(*) with SET rvbbit.force_heap_scan = on to find one.

ALTER TABLE rvbbit.acceleration_state
    ADD COLUMN IF NOT EXISTS covered_snapshot pg_snapshot;
COMMENT ON COLUMN rvbbit.acceleration_state.covered_snapshot IS
'The snapshot of the rebuild that wrote the accelerated copy, while it still matters: the copy holds the rows of every transaction it sees, including committed ones above last_refresh_xid. NULL when last_refresh_xid alone says what the copy holds.';

-- C bindings for existing installations ($libdir literal: migrate() runs via
-- SPI). A library that predates them fails this migration, which is right:
-- the functions below call them.
CREATE OR REPLACE FUNCTION rvbbit.export_to_parquet_xid_range(
    rel oid,
    min_xid text,
    max_xid text,
    covered_snapshot text
)
RETURNS bigint
LANGUAGE c
STRICT
AS '$libdir/pg_rvbbit', 'export_to_parquet_xid_range_uncovered_wrapper';

CREATE OR REPLACE FUNCTION rvbbit.refresh_layout_variants_xid_range(
    rel oid,
    min_xid text,
    max_xid text,
    covered_snapshot text
)
RETURNS bigint
LANGUAGE c
STRICT
AS '$libdir/pg_rvbbit', 'refresh_layout_variants_xid_range_uncovered_wrapper';

CREATE OR REPLACE FUNCTION rvbbit.rebuild_acceleration(reloid regclass, refresh_variants boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
<<accel_rebuild>>
DECLARE
    op_id bigint;
    table_name_text text := reloid::text;
    dropped_rgs int := 0;
    rebuilt_rows bigint := 0;
    row_groups_written bigint := 0;
    variants_rows bigint;
    generation_after bigint := 0;
    pre_max_rg_id bigint := -1;
    baseline_max_rg_id bigint := -1;
    staged_max_rg_id bigint := -1;
    staging_rg_base bigint := 0;
    baseline_generation bigint := 0;
    catchup_generation bigint := 0;
    snapshot_floor_before bigint := 0;
    safe_upper_xid numeric;
    scan_snapshot text;
    scan_upper_xid numeric;
    final_upper_xid numeric;
    final_snapshot text;
    phase_id bigint;
    catchup_phase_id bigint;
    phase_bytes_written bigint := 0;
    catchup_rows bigint := 0;
    catchup_row_groups bigint := 0;
    vortex_stage_rows bigint := 0;
    vortex_stage_result bigint;
    vortex_stage_attempted boolean := false;
    vortex_stage_enabled boolean := false;
    vortex_stage_ok boolean := true;
    vortex_stage_error text;
    vortex_expected_rows bigint := 0;
    vortex_actual_rows bigint := 0;
    vortex_file_count integer := 0;
    remapped_tombstones int := 0;
    queued_orphan_files int := 0;
    orphan_paths text[];
    staged_orphan_paths text[];
    final_lock_attempts int := 0;
    final_lock_attempt_timeout_ms int := 100;
    final_lock_retry_sleep_ms int := 50;
    final_lock_max_wait_ms int := 5000;
    final_lock_deadline timestamptz;
    final_lock_acquired boolean := false;
    previous_lock_timeout text;
BEGIN
    IF NOT rvbbit.is_rvbbit_table(reloid) THEN
        RAISE EXCEPTION '% is not an rvbbit table', reloid;
    END IF;

    -- Serialize canonical publication and the independent Vortex retry worker
    -- without taking a heap lock.  The same class/key is used by compact(),
    -- current-history pruning, and variant_tick(); advisory xact locks are
    -- re-entrant when this function is called from accel_tick().
    PERFORM pg_advisory_xact_lock((1380336724::bigint << 32) | reloid::oid::bigint);

    -- Data-loss guard. rebuild regenerates the accelerator FROM the heap. If the
    -- heap is not authoritative (shadow_heap_retained = false — e.g. a legacy
    -- compact(keep_heap := false) truncated it) AND accelerator row groups already
    -- exist, exporting from the empty/partial heap would delete the only surviving
    -- copy of the data (the zero-rows branch below drops every generation and
    -- queues every parquet file for the reaper). Refuse instead. Fresh tables have
    -- no row groups yet (guard skipped); retained-heap tables are unaffected.
    IF EXISTS (SELECT 1 FROM rvbbit.row_groups WHERE table_oid = reloid)
       AND NOT coalesce(
             (SELECT t.shadow_heap_retained FROM rvbbit.tables t
               WHERE t.table_oid = reloid),
             true) THEN
        RAISE EXCEPTION 'rvbbit.rebuild_acceleration: % has a non-authoritative (truncated) heap but existing accelerator row groups; rebuilding from the heap would destroy data. Restore the heap contents or use rvbbit.refresh_acceleration instead.', reloid;
    END IF;

    -- Define the logical fold snapshot without taking a table lock. The long
    -- baseline scan exports only rows visible to this snapshot; after it
    -- finishes, a short final lock appends rows not visible to this snapshot
    -- and remaps concurrent tombstones onto the staged baseline.
    scan_snapshot := pg_current_snapshot()::text;
    scan_upper_xid := greatest(
        0::numeric,
        (pg_snapshot_xmax(scan_snapshot::pg_snapshot)::text)::numeric - 1
    );
    safe_upper_xid := scan_upper_xid;

    INSERT INTO rvbbit.acceleration_operations (
        table_oid, table_name, operation, status,
        watermark_before, watermark_after, settings
    ) VALUES (
        reloid, table_name_text, 'rebuild_acceleration', 'running',
        NULL, safe_upper_xid,
        jsonb_build_object(
            'refresh_variants', refresh_variants,
            'mode', 'lagged_staged_full_heap_fold',
            'heap_guard', 'none_during_baseline_scan',
            'final_guard', 'polite LOCK TABLE IN SHARE MODE polling',
            'final_lock_attempt_timeout_ms', final_lock_attempt_timeout_ms,
            'final_lock_retry_sleep_ms', final_lock_retry_sleep_ms,
            'final_lock_max_wait_ms', final_lock_max_wait_ms,
            'scan_snapshot', scan_snapshot,
            'scan_upper_xid', scan_upper_xid,
            'metadata_swap', 'post_catchup_export',
            'file_reap', 'queued_after_swap',
            'variant_refresh', CASE WHEN refresh_variants THEN 'staged_from_canonical_parquet' ELSE 'skipped' END
        )
    )
    RETURNING id INTO op_id;

    -- Capture the old file set, but keep it live while the expensive full
    -- export runs. Other transactions keep reading the old accelerator until
    -- this transaction commits the metadata swap; physical files are queued for
    -- a later reap instead of being unlinked while old catalog rows are still
    -- MVCC-visible.
    SELECT count(*)::int, coalesce(max(rg_id), -1)::bigint
      INTO dropped_rgs, pre_max_rg_id
      FROM rvbbit.row_groups
     WHERE table_oid = reloid;

    SELECT coalesce(min_visible_generation, 0)
      INTO snapshot_floor_before
      FROM rvbbit.tables
     WHERE table_oid = reloid;

    SELECT greatest(coalesce(max(generation), 0) + 1, (op_id * 2) + 1)
      INTO baseline_generation
      FROM rvbbit.row_groups
     WHERE table_oid = reloid;
    catchup_generation := baseline_generation + 1;
    staging_rg_base := greatest(pre_max_rg_id + 1, op_id * 1000000000);

    SELECT array_agg(path ORDER BY path)
      INTO orphan_paths
      FROM (
          SELECT path FROM rvbbit.row_groups WHERE table_oid = reloid
          UNION ALL
          SELECT path FROM rvbbit.row_group_variants WHERE table_oid = reloid
          UNION ALL
          SELECT path FROM rvbbit.text_dictionaries WHERE table_oid = reloid
      ) old_files;

    INSERT INTO rvbbit.acceleration_operation_phases (
        operation_id, table_oid, table_name, phase, layout, status, details
    ) VALUES (
        op_id, reloid, table_name_text, 'canonical_full_export', 'scan', 'running',
        jsonb_build_object(
            'source', 'heap',
            'mode', 'lagged_staged_full_heap_fold',
            'dropped_row_groups', dropped_rgs,
            'old_max_rg_id', pre_max_rg_id,
            'staging_rg_base', staging_rg_base,
            'baseline_generation', baseline_generation,
            'scan_snapshot', scan_snapshot
        )
    )
    RETURNING id INTO phase_id;

    SELECT rvbbit.export_to_parquet_snapshot_visible_at(
        reloid::oid,
        scan_snapshot,
        staging_rg_base,
        baseline_generation
    )
      INTO rebuilt_rows;

    SELECT count(*)::bigint, coalesce(max(generation), 0)::bigint
      INTO row_groups_written, generation_after
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;
    SELECT coalesce(max(rg_id), pre_max_rg_id)::bigint
      INTO baseline_max_rg_id
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;

    SELECT coalesce(sum(n_bytes), 0)::bigint
      INTO phase_bytes_written
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;

    UPDATE rvbbit.acceleration_operation_phases
       SET status = 'ok',
           finished_at = clock_timestamp(),
           rows_written = rebuilt_rows,
           row_groups_written = accel_rebuild.row_groups_written,
           files_written = accel_rebuild.row_groups_written::integer,
           bytes_written = phase_bytes_written,
           expected_rows = rebuilt_rows,
           actual_rows = rebuilt_rows
     WHERE id = phase_id;

    -- Convert the large baseline from its freshly-written canonical parquet
    -- while OLTP writers are still free to proceed.  This is intentionally a
    -- best-effort derived build: canonical publication must survive a Vortex
    -- encoder failure, which is handed to the coalescing repair queue below.
    IF refresh_variants AND row_groups_written > 0 THEN
        vortex_stage_attempted := true;
        BEGIN
            PERFORM set_config('rvbbit.acceleration_operation_id', op_id::text, true);
            PERFORM set_config('rvbbit.variant_stage_operation_id', op_id::text, true);
            PERFORM set_config('rvbbit.variant_stage_min_rg_id', staging_rg_base::text, true);
            PERFORM set_config('rvbbit.variant_stage_max_rg_id', baseline_max_rg_id::text, true);
            SELECT rvbbit.refresh_layout_variants(reloid::oid)
              INTO vortex_stage_result;
            vortex_stage_enabled := vortex_stage_result >= 0;
            IF vortex_stage_enabled THEN
                vortex_stage_rows := vortex_stage_rows + vortex_stage_result;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            vortex_stage_enabled := true;
            vortex_stage_ok := false;
            vortex_stage_error := SQLERRM;
        END;
    END IF;

    -- Take the short write-blocking handoff lock only after the expensive
    -- baseline scan. Poll with a short lock timeout so a busy table does not
    -- leave a SHARE lock request queued in front of later OLTP writers.
    previous_lock_timeout := current_setting('lock_timeout');
    PERFORM set_config('lock_timeout', final_lock_attempt_timeout_ms::text || 'ms', true);
    final_lock_deadline := clock_timestamp()
        + ((final_lock_max_wait_ms::text || ' milliseconds')::interval);
    WHILE clock_timestamp() < final_lock_deadline LOOP
        final_lock_attempts := final_lock_attempts + 1;
        BEGIN
            EXECUTE format('LOCK TABLE %s IN SHARE MODE', reloid);
            final_lock_acquired := true;
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            PERFORM pg_sleep(final_lock_retry_sleep_ms::double precision / 1000.0);
        END;
    END LOOP;
    PERFORM set_config('lock_timeout', previous_lock_timeout, true);

    IF NOT final_lock_acquired THEN
        SELECT array_agg(path ORDER BY path)
          INTO staged_orphan_paths
          FROM (
              SELECT path FROM rvbbit.row_groups
               WHERE table_oid = reloid AND rg_id >= staging_rg_base
              UNION ALL
              SELECT path FROM rvbbit.row_group_variants
               WHERE table_oid = reloid AND build_operation_id = op_id
              UNION ALL
              SELECT path FROM rvbbit.text_dictionaries
               WHERE table_oid = reloid AND rg_id >= staging_rg_base
          ) staged_files;

        DELETE FROM rvbbit.row_group_variants
         WHERE table_oid = reloid
           AND build_operation_id = op_id;
        DELETE FROM rvbbit.row_groups
         WHERE table_oid = reloid
           AND rg_id >= staging_rg_base;
        DELETE FROM rvbbit.generations
         WHERE table_oid = reloid
           AND NOT EXISTS (
               SELECT 1
               FROM rvbbit.row_groups rg
               WHERE rg.table_oid = rvbbit.generations.table_oid
                 AND rg.generation = rvbbit.generations.generation
           );
        -- Row-group INSERT/DELETE triggers observed the abandoned staged set.
        -- The old canonical + Vortex metadata is still authoritative, so this
        -- failed handoff must not leave a spurious repair request behind.
        DELETE FROM rvbbit.variant_build_queue WHERE table_oid = reloid;

        IF staged_orphan_paths IS NOT NULL THEN
            INSERT INTO rvbbit.orphaned_files (path, table_oid, reason, operation_id)
            SELECT DISTINCT p, reloid, 'rebuild_acceleration_final_lock_busy', op_id
            FROM unnest(staged_orphan_paths) AS p
            WHERE p IS NOT NULL AND btrim(p) <> ''
            ON CONFLICT (path) DO UPDATE
               SET table_oid = EXCLUDED.table_oid,
                   reason = EXCLUDED.reason,
                   operation_id = EXCLUDED.operation_id,
                   queued_at = clock_timestamp(),
                   last_error = NULL;
            GET DIAGNOSTICS queued_orphan_files = ROW_COUNT;
        END IF;

        UPDATE rvbbit.acceleration_operation_phases
           SET details = details || jsonb_build_object(
                   'cleaned_up_after_final_lock_busy', true,
                   'final_lock_attempts', final_lock_attempts
               )
         WHERE id = phase_id;

        UPDATE rvbbit.acceleration_operations
           SET status = 'noop',
               finished_at = clock_timestamp(),
               rows_written = 0,
               row_groups_written = 0,
               variants_rows = NULL,
               generation_after = NULL,
               error = 'final lock busy',
               settings = settings || jsonb_build_object(
                   'final_lock_attempts', final_lock_attempts,
                   'final_lock_acquired', false,
                   'queued_orphan_files', queued_orphan_files,
                   'metadata_swap', 'skipped_final_lock_busy'
               )
         WHERE id = op_id;

        RETURN jsonb_build_object(
            'status', 'noop',
            'operation_id', op_id,
            'table', table_name_text,
            'operation', 'rebuild_acceleration',
            'reason', 'final_lock_busy',
            'final_lock_attempts', final_lock_attempts,
            'queued_orphan_files', queued_orphan_files,
            'baseline_rows', rebuilt_rows,
            'catchup_rows', 0,
            'remapped_tombstones', 0,
            'rows_written', 0,
            'row_groups_written', 0,
            'variants_rows', NULL,
            'generation_after', NULL,
            'watermark_after', scan_upper_xid
        );
    END IF;

    -- Under the lock no transaction has uncommitted writes to the table, so
    -- once the gap is exported the copy holds exactly what this snapshot
    -- sees. A transaction it sees in progress has not written the table yet,
    -- but can once this commits, with an xid below the snapshot's xmax: the
    -- watermark stops below the oldest of them, and covered_snapshot keeps the
    -- committed transactions above it, whose rows the copy already holds.
    -- With none in progress the snapshot sees everything below its xmax.
    final_snapshot := pg_current_snapshot()::text;
    final_upper_xid := greatest(
        0::numeric,
        coalesce(
            (SELECT min((x::text)::numeric)
               FROM pg_snapshot_xip(final_snapshot::pg_snapshot) AS x),
            (pg_snapshot_xmax(final_snapshot::pg_snapshot)::text)::numeric
        ) - 1
    );
    safe_upper_xid := final_upper_xid;

    INSERT INTO rvbbit.acceleration_operation_phases (
        operation_id, table_oid, table_name, phase, layout, status, details
    ) VALUES (
        op_id, reloid, table_name_text, 'canonical_gap_export', 'scan', 'running',
        jsonb_build_object(
            'source', 'heap',
            'mode', 'snapshot_gap',
            'scan_snapshot', scan_snapshot,
            'baseline_max_rg_id', baseline_max_rg_id,
            'catchup_generation', catchup_generation
        )
    )
    RETURNING id INTO catchup_phase_id;

    SELECT rvbbit.export_to_parquet_snapshot_gap_at(
        reloid::oid,
        scan_snapshot,
        greatest(baseline_max_rg_id + 1, staging_rg_base),
        catchup_generation
    )
      INTO catchup_rows;

    SELECT count(*)::bigint
      INTO catchup_row_groups
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base
       AND rg_id > baseline_max_rg_id;

    UPDATE rvbbit.acceleration_operation_phases
       SET status = 'ok',
           finished_at = clock_timestamp(),
           rows_written = catchup_rows,
           row_groups_written = catchup_row_groups,
           files_written = catchup_row_groups::integer,
           expected_rows = catchup_rows,
           actual_rows = catchup_rows
     WHERE id = catchup_phase_id;

    SELECT coalesce(max(rg_id), -1)::bigint
      INTO staged_max_rg_id
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;

    -- The final heap lock is already held here.  Only convert the usually tiny
    -- catch-up range; the large baseline Vortex files were produced before the
    -- lock.  If the baseline was empty, this one call covers the entire staged
    -- canonical set.
    IF refresh_variants
       AND staged_max_rg_id >= staging_rg_base
       AND (NOT vortex_stage_attempted
            OR (vortex_stage_enabled AND vortex_stage_ok
                AND staged_max_rg_id > baseline_max_rg_id)) THEN
        vortex_stage_attempted := true;
        BEGIN
            PERFORM set_config('rvbbit.acceleration_operation_id', op_id::text, true);
            PERFORM set_config('rvbbit.variant_stage_operation_id', op_id::text, true);
            PERFORM set_config(
                'rvbbit.variant_stage_min_rg_id',
                CASE
                    WHEN vortex_stage_enabled THEN (baseline_max_rg_id + 1)::text
                    ELSE staging_rg_base::text
                END,
                true
            );
            PERFORM set_config('rvbbit.variant_stage_max_rg_id', staged_max_rg_id::text, true);
            SELECT rvbbit.refresh_layout_variants(reloid::oid)
              INTO vortex_stage_result;
            IF vortex_stage_result < 0 THEN
                vortex_stage_enabled := false;
            ELSE
                vortex_stage_enabled := true;
                vortex_stage_rows := vortex_stage_rows + vortex_stage_result;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            vortex_stage_enabled := true;
            vortex_stage_ok := false;
            vortex_stage_error := SQLERRM;
        END;
    END IF;

    WITH remapped AS (
        INSERT INTO rvbbit.delete_log
            (table_oid, rg_id, ordinal, deleted_xid, deleted_generation)
        SELECT reloid,
               staged_m.rg_id,
               staged_m.ordinal,
               dl.deleted_xid,
               dl.deleted_generation
        FROM rvbbit.delete_log dl
        JOIN rvbbit.row_identity_map old_m
          ON old_m.table_oid = dl.table_oid
         AND old_m.rg_id = dl.rg_id
         AND old_m.ordinal = dl.ordinal
        JOIN rvbbit.row_identity_map staged_m
         ON staged_m.table_oid = old_m.table_oid
         AND staged_m.key_json = old_m.key_json
         AND staged_m.rg_id >= staging_rg_base
         AND staged_m.rg_id <= baseline_max_rg_id
        WHERE dl.table_oid = reloid
          AND dl.rg_id <= pre_max_rg_id
          AND NOT pg_visible_in_snapshot(dl.deleted_xid, scan_snapshot::pg_snapshot)
        ON CONFLICT (table_oid, rg_id, ordinal) DO UPDATE SET
            deleted_xid = EXCLUDED.deleted_xid,
            deleted_generation = EXCLUDED.deleted_generation
        RETURNING 1
    )
    SELECT count(*)::int INTO remapped_tombstones FROM remapped;

    SELECT count(*)::bigint, coalesce(max(generation), 0)::bigint
      INTO row_groups_written, generation_after
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;

    -- Validate the derived set before the canonical handoff.  A failed Vortex
    -- stage is discarded and queued for retry; it never aborts or delays the
    -- authoritative parquet publication.
    SELECT coalesce(sum(n_rows), 0)::bigint
      INTO vortex_expected_rows
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id >= staging_rg_base;
    SELECT coalesce(sum(n_rows), 0)::bigint, count(*)::integer
      INTO vortex_actual_rows, vortex_file_count
      FROM rvbbit.row_group_variants
     WHERE table_oid = reloid
       AND layout = 'vortex_scan'
       AND build_operation_id = op_id;

    IF vortex_stage_enabled AND vortex_stage_ok
       AND (vortex_expected_rows <= 0
            OR vortex_actual_rows <> vortex_expected_rows
            OR vortex_file_count <= 0) THEN
        vortex_stage_ok := false;
        vortex_stage_error := format(
            'staged Vortex validation failed: expected %s rows, catalog has %s rows in %s files',
            vortex_expected_rows,
            vortex_actual_rows,
            vortex_file_count
        );
    END IF;

    -- Atomic metadata swap inside this transaction: remove old row groups and
    -- their dependent stats/identity rows. Old tombstones are discarded after
    -- any concurrent, post-snapshot tombstones have been remapped onto the
    -- staged baseline row-group ordinals above.  A valid staged Vortex set is
    -- retained by operation id while every prior variant disappears atomically.
    DELETE FROM rvbbit.delete_log
     WHERE table_oid = reloid
       AND rg_id <= pre_max_rg_id;
    DELETE FROM rvbbit.layout_variant_status WHERE table_oid = reloid;
    IF vortex_stage_enabled AND vortex_stage_ok THEN
        DELETE FROM rvbbit.row_group_variants
         WHERE table_oid = reloid
           AND build_operation_id IS DISTINCT FROM op_id;
        INSERT INTO rvbbit.layout_variant_status (
            table_oid, layout, status, expected_rows, actual_rows,
            file_count, status_message, refreshed_at
        ) VALUES (
            reloid, 'vortex_scan', 'ready', vortex_expected_rows,
            vortex_actual_rows, vortex_file_count, NULL, clock_timestamp()
        )
        ON CONFLICT (table_oid, layout) DO UPDATE SET
            status = EXCLUDED.status,
            expected_rows = EXCLUDED.expected_rows,
            actual_rows = EXCLUDED.actual_rows,
            file_count = EXCLUDED.file_count,
            status_message = NULL,
            refreshed_at = EXCLUDED.refreshed_at;
        variants_rows := vortex_actual_rows;
    ELSE
        SELECT array_agg(path ORDER BY path)
          INTO staged_orphan_paths
          FROM rvbbit.row_group_variants
         WHERE table_oid = reloid
           AND build_operation_id = op_id;
        orphan_paths := coalesce(orphan_paths, ARRAY[]::text[])
            || coalesce(staged_orphan_paths, ARRAY[]::text[]);
        DELETE FROM rvbbit.row_group_variants WHERE table_oid = reloid;
        variants_rows := NULL;
    END IF;
    DELETE FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id <= pre_max_rg_id;
    IF row_groups_written > 0 THEN
        DELETE FROM rvbbit.generations
         WHERE table_oid = reloid
           AND NOT EXISTS (
               SELECT 1
               FROM rvbbit.row_groups rg
               WHERE rg.table_oid = rvbbit.generations.table_oid
                 AND rg.generation = rvbbit.generations.generation
           );
    ELSE
        DELETE FROM rvbbit.generations WHERE table_oid = reloid;
        generation_after := 0;
    END IF;

    UPDATE rvbbit.tables
       SET shadow_heap_retained = true,
           shadow_heap_dirty = false,
           dirty_has_insert = false,
           dirty_has_update = false,
           dirty_has_delete = false,
           dirty_has_truncate = false,
           min_visible_generation = CASE
               WHEN snapshot_floor_before > 0 AND generation_after > 0
               THEN generation_after
               ELSE min_visible_generation
           END,
           next_generation = greatest(next_generation, generation_after + 1),
           ctid_identity_relfilenode = CASE
               WHEN rvbbit.accel_identity_mode(reloid) = 'ctid'
               THEN pg_relation_filenode(reloid)
               ELSE ctid_identity_relfilenode
           END
     WHERE table_oid = reloid;
    PERFORM rvbbit.clear_table_dirty_markers(reloid::oid);

    DELETE FROM rvbbit.acceleration_state WHERE table_oid = reloid;

    IF orphan_paths IS NOT NULL THEN
        INSERT INTO rvbbit.orphaned_files (path, table_oid, reason, operation_id)
        SELECT DISTINCT p, reloid, 'rebuild_acceleration_staged_swap', op_id
        FROM unnest(orphan_paths) AS p
        WHERE p IS NOT NULL AND btrim(p) <> ''
        ON CONFLICT (path) DO UPDATE
           SET table_oid = EXCLUDED.table_oid,
               reason = EXCLUDED.reason,
               operation_id = EXCLUDED.operation_id,
               queued_at = clock_timestamp(),
               last_error = NULL;
        GET DIAGNOSTICS queued_orphan_files = ROW_COUNT;
    END IF;

    IF refresh_variants AND row_groups_written > 0 THEN
        IF vortex_stage_enabled AND vortex_stage_ok THEN
            DELETE FROM rvbbit.variant_build_queue WHERE table_oid = reloid;
        ELSIF vortex_stage_enabled OR NOT vortex_stage_attempted THEN
            INSERT INTO rvbbit.variant_build_queue (
                table_oid, target_generation, target_row_groups,
                target_rows, target_bytes, reason, priority,
                requested_at, available_at, attempts, last_error
            )
            SELECT reloid,
                   generation_after,
                   count(*)::bigint,
                   coalesce(sum(n_rows), 0)::bigint,
                   coalesce(sum(n_bytes), 0)::bigint,
                   'canonical_rebuild_stage_failed',
                   100,
                   clock_timestamp(),
                   clock_timestamp(),
                   0,
                   vortex_stage_error
              FROM rvbbit.row_groups
             WHERE table_oid = reloid
            ON CONFLICT (table_oid) DO UPDATE SET
                target_generation = EXCLUDED.target_generation,
                target_row_groups = EXCLUDED.target_row_groups,
                target_rows = EXCLUDED.target_rows,
                target_bytes = EXCLUDED.target_bytes,
                reason = EXCLUDED.reason,
                priority = greatest(rvbbit.variant_build_queue.priority, EXCLUDED.priority),
                requested_at = EXCLUDED.requested_at,
                available_at = EXCLUDED.available_at,
                attempts = 0,
                last_error = EXCLUDED.last_error;
        END IF;
    ELSIF NOT refresh_variants THEN
        DELETE FROM rvbbit.variant_build_queue WHERE table_oid = reloid;
    END IF;

    INSERT INTO rvbbit.acceleration_state (
        table_oid,
        last_refresh_xid,
        covered_snapshot,
        last_refresh_generation,
        last_refresh_rows,
        last_refresh_row_groups,
        refresh_relfilenode,
        last_refresh_at,
        updated_at
    ) VALUES (
        reloid,
        safe_upper_xid,
        CASE
            WHEN EXISTS (SELECT 1 FROM pg_snapshot_xip(final_snapshot::pg_snapshot))
            THEN final_snapshot::pg_snapshot
        END,
        generation_after,
        coalesce(rebuilt_rows, 0) + coalesce(catchup_rows, 0),
        coalesce(row_groups_written, 0),
        pg_relation_filenode(reloid),
        clock_timestamp(),
        clock_timestamp()
    )
    ON CONFLICT (table_oid) DO UPDATE
       SET last_refresh_xid = EXCLUDED.last_refresh_xid,
           covered_snapshot = EXCLUDED.covered_snapshot,
           last_refresh_generation = EXCLUDED.last_refresh_generation,
           last_refresh_rows = EXCLUDED.last_refresh_rows,
           last_refresh_row_groups = EXCLUDED.last_refresh_row_groups,
           refresh_relfilenode = EXCLUDED.refresh_relfilenode,
           last_refresh_at = EXCLUDED.last_refresh_at,
           updated_at = EXCLUDED.updated_at;

    PERFORM rvbbit.install_shadow_heap_dirty_triggers(reloid);

    UPDATE rvbbit.acceleration_operations
       SET status = 'ok',
           finished_at = clock_timestamp(),
           rows_written = rebuilt_rows + coalesce(catchup_rows, 0),
           row_groups_written = accel_rebuild.row_groups_written,
           variants_rows = accel_rebuild.variants_rows,
           generation_after = accel_rebuild.generation_after,
           watermark_after = safe_upper_xid,
           settings = settings || jsonb_build_object(
               'dropped_row_groups', dropped_rgs,
               'old_max_rg_id', pre_max_rg_id,
               'baseline_max_rg_id', baseline_max_rg_id,
               'staging_rg_base', staging_rg_base,
               'baseline_generation', baseline_generation,
               'catchup_generation', catchup_generation,
               'baseline_rows', rebuilt_rows,
               'catchup_rows', catchup_rows,
               'catchup_row_groups', catchup_row_groups,
               'remapped_tombstones', remapped_tombstones,
               'final_lock_attempts', final_lock_attempts,
               'final_lock_acquired', true,
               'queued_orphan_files', queued_orphan_files,
               'metadata_swap', 'lagged_staged',
               'watermark_after', safe_upper_xid,
               'covered_snapshot', final_snapshot
           )
     WHERE id = op_id;

    RETURN jsonb_build_object(
        'status', 'ok',
        'operation_id', op_id,
        'table', table_name_text,
        'operation', 'rebuild_acceleration',
        'dropped_row_groups', dropped_rgs,
        'queued_orphan_files', queued_orphan_files,
        'baseline_rows', rebuilt_rows,
        'catchup_rows', catchup_rows,
        'remapped_tombstones', remapped_tombstones,
        'rows_written', rebuilt_rows + coalesce(catchup_rows, 0),
        'row_groups_written', row_groups_written,
        'variants_rows', variants_rows,
        'generation_after', generation_after,
        'watermark_after', safe_upper_xid
    );
EXCEPTION WHEN OTHERS THEN
    IF op_id IS NOT NULL THEN
        UPDATE rvbbit.acceleration_operation_phases
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = SQLERRM
         WHERE operation_id = op_id
           AND status = 'running';
        UPDATE rvbbit.acceleration_operations
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = SQLERRM
         WHERE id = op_id;
    END IF;
    RAISE;
END $function$;

CREATE OR REPLACE FUNCTION rvbbit.refresh_acceleration(reloid regclass, refresh_variants boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
<<accel_refresh>>
DECLARE
    op_id bigint;
    table_name_text text := reloid::text;
    last_xid numeric;
    current_relfilenode oid;
    stored_relfilenode oid;
    safe_upper_xid numeric;
    frontier_fxid numeric := 0;
    has_pending_above boolean := false;
    covered pg_snapshot;
    rows_written bigint := 0;
    row_groups_written bigint := 0;
    variants_rows bigint;
    max_rg_id_pre bigint;
    existing_rgs bigint;
    generation_after bigint := 0;
    shadow_retained boolean := false;
    shadow_dirty boolean := false;
    dirty_update boolean := false;
    dirty_delete boolean := false;
    dirty_truncate boolean := false;
    overlay_ready boolean := false;
    heap_bytes bigint := 0;
    phase_id bigint;
    phase_bytes_before bigint := 0;
    phase_bytes_after bigint := 0;
BEGIN
    IF NOT rvbbit.is_rvbbit_table(reloid) THEN
        RAISE EXCEPTION '% is not an rvbbit table', reloid;
    END IF;

    -- The refresh watermark is a correctness boundary. Block writers while
    -- we snapshot/export the safe heap range, then install the dirty trigger
    -- before releasing the lock at transaction end.
    EXECUTE format('LOCK TABLE %s IN SHARE MODE', reloid);

    INSERT INTO rvbbit.acceleration_state (table_oid)
    VALUES (reloid)
    ON CONFLICT (table_oid) DO NOTHING;

    SELECT s.last_refresh_xid, s.refresh_relfilenode, s.covered_snapshot
      INTO last_xid, stored_relfilenode, covered
      FROM rvbbit.acceleration_state s
     WHERE s.table_oid = reloid
     FOR UPDATE;

    current_relfilenode := pg_relation_filenode(reloid);

    -- pg_snapshot_xmin is the oldest still-active xid in this snapshot.
    -- XIDs below it are complete, so rows in that range are safe to mark
    -- accelerated without skipping concurrent transactions that commit later.
    safe_upper_xid := greatest(
        0::numeric,
        (pg_snapshot_xmin(pg_current_snapshot())::text)::numeric - 1
    );

    -- The live XID frontier (highest possibly-assigned xid). When an unrelated
    -- long-lived snapshot pins pg_snapshot_xmin far below this, safe_upper_xid is
    -- held back below rows that are already committed to THIS table — see the
    -- pending-above-ceiling probe before the dirty-clear below.
    frontier_fxid := greatest(
        0::numeric,
        (pg_snapshot_xmax(pg_current_snapshot())::text)::numeric - 1
    );

    SELECT count(*)::bigint, coalesce(max(rg_id), -1)::bigint,
           coalesce(max(generation), 0)::bigint
      INTO existing_rgs, max_rg_id_pre, generation_after
      FROM rvbbit.row_groups
     WHERE table_oid = reloid;

    SELECT coalesce(ds.shadow_heap_retained, false),
           coalesce(ds.shadow_heap_dirty, false),
           coalesce(ds.dirty_has_update, false),
           coalesce(ds.dirty_has_delete, false),
           coalesce(ds.dirty_has_truncate, false)
      INTO shadow_retained, shadow_dirty, dirty_update, dirty_delete, dirty_truncate
      FROM rvbbit.table_dirty_state ds
     WHERE ds.table_oid = reloid;

    heap_bytes := pg_relation_size(reloid);
    overlay_ready := rvbbit.accel_overlay_ready(reloid);

    INSERT INTO rvbbit.acceleration_operations (
        table_oid, table_name, operation, status,
        watermark_before, watermark_after, settings
    ) VALUES (
        reloid, table_name_text, 'refresh_acceleration', 'running',
        last_xid, safe_upper_xid,
        jsonb_build_object(
            'refresh_variants', refresh_variants,
            'watermark', 'heap xmin <= pg_snapshot_xmin(pg_current_snapshot()) - 1',
            'heap_guard', 'LOCK TABLE IN SHARE MODE'
        )
    )
    RETURNING id INTO op_id;

    IF existing_rgs > 0
       AND shadow_dirty
       AND (dirty_update OR dirty_delete OR dirty_truncate)
       AND NOT overlay_ready THEN
        UPDATE rvbbit.acceleration_operations
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = 'non-append dirty episode requires rebuild or complete row identity overlay',
               settings = settings || jsonb_build_object(
                   'dirty_has_update', dirty_update,
                   'dirty_has_delete', dirty_delete,
                   'dirty_has_truncate', dirty_truncate,
                   'overlay_ready', overlay_ready,
                   'recommended_action', 'rebuild_acceleration'
               )
         WHERE id = op_id;
        RAISE EXCEPTION
            'rvbbit.refresh_acceleration: % has UPDATE/DELETE/TRUNCATE changes since the last refresh; run rvbbit.rebuild_acceleration(%) or use an overlay-capable path',
            reloid, quote_literal(reloid::text);
    END IF;

    -- Heap-rewrite guard: a rewrite (ALTER TABLE .. TYPE, VACUUM FULL,
    -- CLUSTER) does not fire the dirty triggers above, so it can reach this
    -- point looking "clean" — but every row was just reinserted with a fresh
    -- xmin, which the watermark scan below cannot distinguish from genuinely
    -- new rows. Refuse loudly rather than silently duplicating every row
    -- into a second, never-retired generation of row groups (found live:
    -- ALTER COLUMN TYPE between two refreshes left both generations valid
    -- simultaneously, real duplicate primary keys on plain SELECT).
    IF existing_rgs > 0
       AND stored_relfilenode IS NOT NULL
       AND stored_relfilenode <> current_relfilenode THEN
        UPDATE rvbbit.acceleration_operations
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = 'heap relfilenode changed since the last refresh (a rewrite occurred); incremental refresh cannot safely tell rewritten rows from new ones',
               settings = settings || jsonb_build_object(
                   'stored_relfilenode', stored_relfilenode,
                   'current_relfilenode', current_relfilenode,
                   'recommended_action', 'rebuild_acceleration'
               )
         WHERE id = op_id;
        RAISE EXCEPTION
            'rvbbit.refresh_acceleration: % heap was rewritten (relfilenode changed) since the last refresh; run rvbbit.rebuild_acceleration(%) — incremental refresh cannot be used across a heap rewrite',
            reloid, quote_literal(reloid::text);
    END IF;

    IF last_xid = 0 AND existing_rgs > 0 AND heap_bytes > 0 THEN
        IF shadow_retained AND NOT shadow_dirty THEN
            UPDATE rvbbit.tables
               SET shadow_heap_retained = true,
                   shadow_heap_dirty = false,
                   dirty_has_insert = false,
                   dirty_has_update = false,
                   dirty_has_delete = false,
                   dirty_has_truncate = false
             WHERE table_oid = reloid;
            PERFORM rvbbit.clear_table_dirty_markers(reloid::oid);
            PERFORM rvbbit.install_shadow_heap_dirty_triggers(reloid);
            UPDATE rvbbit.acceleration_state
               SET last_refresh_xid = safe_upper_xid,
                   covered_snapshot = NULL,
                   last_refresh_generation = generation_after,
                   refresh_relfilenode = current_relfilenode,
                   last_refresh_at = clock_timestamp(),
                   updated_at = clock_timestamp()
             WHERE table_oid = reloid;
            UPDATE rvbbit.acceleration_operations
               SET status = 'noop',
                   finished_at = clock_timestamp(),
                   rows_written = 0,
                   row_groups_written = 0,
                   generation_after = accel_refresh.generation_after,
                   settings = settings || jsonb_build_object('bootstrap', 'clean shadow heap already covered by existing row groups')
             WHERE id = op_id;
            RETURN jsonb_build_object(
                'status', 'noop',
                'operation_id', op_id,
                'table', table_name_text,
                'watermark_before', last_xid,
                'watermark_after', safe_upper_xid,
                'rows_written', 0,
                'row_groups_written', 0,
                'bootstrap', true
            );
        ELSIF shadow_dirty THEN
            RAISE EXCEPTION
                'rvbbit.refresh_acceleration: % has existing row groups and a dirty retained heap; run rvbbit.rebuild_acceleration(%) before incremental refresh',
                reloid, quote_literal(reloid::text);
        END IF;
    END IF;

    IF safe_upper_xid <= last_xid THEN
        IF existing_rgs > 0 AND NOT shadow_dirty THEN
            UPDATE rvbbit.tables
               SET shadow_heap_retained = true,
                   shadow_heap_dirty = false,
                   dirty_has_insert = false,
                   dirty_has_update = false,
                   dirty_has_delete = false,
                   dirty_has_truncate = false
             WHERE table_oid = reloid;
            PERFORM rvbbit.clear_table_dirty_markers(reloid::oid);
            PERFORM rvbbit.install_shadow_heap_dirty_triggers(reloid);
        END IF;
        UPDATE rvbbit.acceleration_operations
           SET status = 'noop',
               finished_at = clock_timestamp(),
               rows_written = 0,
               row_groups_written = 0,
               generation_after = accel_refresh.generation_after
         WHERE id = op_id;
        RETURN jsonb_build_object(
            'status', 'noop',
            'operation_id', op_id,
            'table', table_name_text,
            'watermark_before', last_xid,
            'watermark_after', safe_upper_xid,
            'rows_written', 0,
            'row_groups_written', 0
        );
    END IF;

    SELECT coalesce(sum(n_bytes), 0)::bigint
      INTO phase_bytes_before
      FROM rvbbit.row_groups
     WHERE table_oid = reloid;

    INSERT INTO rvbbit.acceleration_operation_phases (
        operation_id, table_oid, table_name, phase, layout, status, details
    ) VALUES (
        op_id, reloid, table_name_text, 'canonical_delta_export', 'scan', 'running',
        jsonb_build_object(
            'source', 'heap',
            'mode', 'watermark_delta',
            'watermark_before', last_xid,
            'watermark_after', safe_upper_xid
        )
    )
    RETURNING id INTO phase_id;

    PERFORM set_config('rvbbit.acceleration_phase_id', phase_id::text, true);
    -- Rows of transactions a rebuild's snapshot saw are in the copy already.
    IF covered IS NULL THEN
        SELECT rvbbit.export_to_parquet_xid_range(
            reloid::oid,
            last_xid::text,
            safe_upper_xid::text
        ) INTO rows_written;
    ELSE
        SELECT rvbbit.export_to_parquet_xid_range(
            reloid::oid,
            last_xid::text,
            safe_upper_xid::text,
            covered::text
        ) INTO rows_written;
    END IF;
    PERFORM set_config('rvbbit.acceleration_phase_id', '', true);

    SELECT count(*)::bigint, coalesce(max(generation), generation_after)::bigint
      INTO row_groups_written, generation_after
      FROM rvbbit.row_groups
     WHERE table_oid = reloid
       AND rg_id > max_rg_id_pre;

    SELECT coalesce(sum(n_bytes), 0)::bigint
      INTO phase_bytes_after
      FROM rvbbit.row_groups
     WHERE table_oid = reloid;

    UPDATE rvbbit.acceleration_operation_phases
       SET status = 'ok',
           finished_at = clock_timestamp(),
           rows_written = accel_refresh.rows_written,
           row_groups_written = accel_refresh.row_groups_written,
           files_written = accel_refresh.row_groups_written::integer,
           bytes_written = greatest(0, phase_bytes_after - phase_bytes_before),
           expected_rows = accel_refresh.rows_written,
           actual_rows = accel_refresh.rows_written
     WHERE id = phase_id;

    IF refresh_variants AND rows_written > 0 THEN
        PERFORM set_config('rvbbit.acceleration_operation_id', op_id::text, true);
        IF covered IS NULL THEN
            SELECT rvbbit.refresh_layout_variants_xid_range(
                reloid::oid,
                last_xid::text,
                safe_upper_xid::text
            ) INTO variants_rows;
        ELSE
            SELECT rvbbit.refresh_layout_variants_xid_range(
                reloid::oid,
                last_xid::text,
                safe_upper_xid::text,
                covered::text
            ) INTO variants_rows;
        END IF;
        PERFORM set_config('rvbbit.acceleration_operation_id', '', true);
    END IF;

    -- Watermark-visibility guard: if an unrelated long-lived snapshot pinned
    -- pg_snapshot_xmin (thus safe_upper_xid) below rows that are already
    -- committed to this table, those rows sit ABOVE the export ceiling and were
    -- not captured. Clearing the dirty flag would drop them from every
    -- accelerated route AND disable the heap-tail overlay, so the accelerated
    -- read silently loses rows until the next unrelated write re-dirties the
    -- table. Detect that case and KEEP the table dirty instead: the heap-tail
    -- overlay serves the pending rows correctly, and the freshness plane retries
    -- once the pinning snapshot ends and safe_upper_xid rises past them. Probe
    -- only when the ceiling was actually held back (frontier check cheap-outs the
    -- common no-pin path so we don't add a scan to every refresh).
    has_pending_above := false;
    IF shadow_dirty AND safe_upper_xid < frontier_fxid THEN
        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM %s WHERE rvbbit.xid_to_fxid(xmin) > %s::numeric '
            'AND ($1 IS NULL OR NOT pg_visible_in_snapshot(rvbbit.xid_to_fxid(xmin)::text::xid8, $1)))',
            reloid::text, safe_upper_xid::text
        ) INTO has_pending_above USING covered;
    END IF;

    IF existing_rgs > 0 OR row_groups_written > 0 THEN
        IF has_pending_above THEN
            -- Mark retained (so the overlay can serve) but KEEP the dirty flag +
            -- markers: this leaves the heap-tail overlay active for correct reads
            -- and signals the freshness plane to retry the delta later.
            UPDATE rvbbit.tables
               SET shadow_heap_retained = true
             WHERE table_oid = reloid;
            PERFORM rvbbit.install_shadow_heap_dirty_triggers(reloid);
        ELSE
            UPDATE rvbbit.tables
               SET shadow_heap_retained = true,
                   shadow_heap_dirty = false,
                   dirty_has_insert = false,
                   dirty_has_update = false,
                   dirty_has_delete = false,
                   dirty_has_truncate = false
             WHERE table_oid = reloid;
            PERFORM rvbbit.clear_table_dirty_markers(reloid::oid);
            PERFORM rvbbit.install_shadow_heap_dirty_triggers(reloid);
        END IF;
    END IF;

    UPDATE rvbbit.acceleration_state
       SET last_refresh_xid = safe_upper_xid,
           -- Once the watermark passes the rebuild snapshot's xmax, every
           -- transaction it saw is below the watermark.
           covered_snapshot = CASE
               WHEN safe_upper_xid < (pg_snapshot_xmax(covered)::text)::numeric - 1 THEN covered
           END,
           last_refresh_generation = generation_after,
           last_refresh_rows = coalesce(last_refresh_rows, 0) + coalesce(rows_written, 0),
           last_refresh_row_groups = coalesce(last_refresh_row_groups, 0) + coalesce(row_groups_written, 0),
           refresh_relfilenode = current_relfilenode,
           last_refresh_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE table_oid = reloid;

    UPDATE rvbbit.acceleration_operations
       SET status = 'ok',
           finished_at = clock_timestamp(),
           rows_written = accel_refresh.rows_written,
           row_groups_written = accel_refresh.row_groups_written,
           variants_rows = accel_refresh.variants_rows,
           generation_after = accel_refresh.generation_after
     WHERE id = op_id;

    RETURN jsonb_build_object(
        'status', 'ok',
        'operation_id', op_id,
        'table', table_name_text,
        'watermark_before', last_xid,
        'watermark_after', safe_upper_xid,
        'rows_written', rows_written,
        'row_groups_written', row_groups_written,
        'variants_rows', variants_rows,
        'generation_after', generation_after
    );
EXCEPTION WHEN OTHERS THEN
    IF op_id IS NOT NULL THEN
        UPDATE rvbbit.acceleration_operation_phases
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = SQLERRM
         WHERE operation_id = op_id
           AND status = 'running';
        UPDATE rvbbit.acceleration_operations
           SET status = 'failed',
               finished_at = clock_timestamp(),
               error = SQLERRM
         WHERE id = op_id;
    END IF;
    RAISE;
END;
$function$;
