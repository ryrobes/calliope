-- 0405: MVCC cube replacement. Ordinary readers keep the last committed heap
-- and accelerator throughout the build. No TRUNCATE, table swap, or trigger
-- DDL is allowed in a routine refresh. Definition/repair DDL remains separate.
-- Existing tables are not rebuilt and cron activation is not changed here.

CREATE OR REPLACE FUNCTION rvbbit._shadow_heap_dirty_tracking_matches(reloid regclass)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
WITH mode AS (
    SELECT c.relispartition AS part, rvbbit.accel_identity_mode(reloid) AS identity
    FROM pg_class c WHERE c.oid = reloid
), expected(name, function_name, kind, old_table) AS (
    SELECT 'rvbbit_shadow_heap_dirty_' || op,
           'rvbbit.mark_shadow_heap_dirty()', bits, NULL::name
    FROM (VALUES ('insert',4),('update',16),('delete',8),('truncate',32)) v(op,bits), mode
    WHERE NOT part AND (op IN ('insert','truncate') OR identity IS DISTINCT FROM 'primary_key')
    UNION ALL
    SELECT 'rvbbit_shadow_heap_dirty_' || op, 'rvbbit.mark_shadow_heap_dirty()', bits,
           'rvbbit_old_rows'::name
    FROM (VALUES ('update',16),('delete',8)) v(op,bits), mode
    WHERE NOT part AND identity = 'primary_key'
    UNION ALL
    SELECT 'rvbbit_shadow_heap_ctid_' || op, 'rvbbit.mark_shadow_heap_ctid_tombstone()', bits, NULL::name
    FROM (VALUES ('update',17),('delete',9)) v(op,bits), mode
    WHERE NOT part AND identity IS DISTINCT FROM 'primary_key'
    UNION ALL
    SELECT 'rvbbit_shadow_heap_dirty_row_' || op, 'rvbbit.mark_shadow_heap_dirty_row()', bits, NULL::name
    FROM (VALUES ('insert',5),('update',17),('delete',9)) v(op,bits), mode WHERE part
    UNION ALL
    SELECT 'rvbbit_shadow_heap_dirty_truncate', 'rvbbit.mark_shadow_heap_dirty()', 32, NULL::name
    FROM mode WHERE part
), actual AS (
    SELECT * FROM pg_trigger WHERE tgrelid = reloid AND NOT tgisinternal
      AND starts_with(tgname, 'rvbbit_shadow_heap_')
)
SELECT EXISTS(SELECT 1 FROM mode)
   AND (SELECT count(*) FROM expected) = (SELECT count(*) FROM actual)
   AND NOT EXISTS (
       SELECT 1 FROM expected e LEFT JOIN actual a ON a.tgname = e.name
       WHERE a.oid IS NULL OR a.tgfoid <> e.function_name::regprocedure
          OR a.tgtype <> e.kind OR a.tgenabled <> 'A'
          OR a.tgoldtable IS DISTINCT FROM e.old_table OR a.tgnewtable IS NOT NULL
          OR a.tgconstraint <> 0 OR a.tgdeferrable OR a.tginitdeferred
          OR a.tgnargs <> 0 OR a.tgattr <> ''::int2vector OR a.tgqual IS NOT NULL
   )
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.install_shadow_heap_dirty_triggers(reloid regclass)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    identity_mode text := rvbbit.accel_identity_mode(reloid);
    is_partition boolean := false;
    trg record;
BEGIN
    SELECT coalesce(c.relispartition, false)
      INTO is_partition
      FROM pg_class c
     WHERE c.oid = reloid;

    IF rvbbit._shadow_heap_dirty_tracking_matches(reloid) THEN
        RETURN;
    END IF;
    -- Never queue trigger DDL behind a live scan and block subsequent readers.
    EXECUTE format('LOCK TABLE ONLY %s IN ACCESS EXCLUSIVE MODE NOWAIT', reloid);
    PERFORM rvbbit.drop_shadow_heap_dirty_triggers(reloid);

    IF is_partition THEN
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_row_insert
                 AFTER INSERT ON %s
                 FOR EACH ROW
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty_row()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_row_update
                 AFTER UPDATE ON %s
                 FOR EACH ROW
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty_row()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_row_delete
                 AFTER DELETE ON %s
                 FOR EACH ROW
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty_row()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_truncate
                 AFTER TRUNCATE ON %s
                 FOR EACH STATEMENT
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
            reloid
        );

        -- Fire even under session_replication_role = 'replica' (logical-repl
        -- apply, pg_restore --disable-triggers, some ETL). ORIGIN/LOCAL triggers
        -- are skipped in that mode, so heap writes would bypass dirty tracking
        -- and the accelerator would go silently stale. ENABLE ALWAYS forces it.
        FOR trg IN
            SELECT tgname FROM pg_trigger
            WHERE tgrelid = reloid AND NOT tgisinternal
              AND starts_with(tgname, 'rvbbit_shadow_heap_')
        LOOP
            EXECUTE format('ALTER TABLE %s ENABLE ALWAYS TRIGGER %I', reloid, trg.tgname);
        END LOOP;
        RETURN;
    END IF;

    EXECUTE format(
        'CREATE TRIGGER rvbbit_shadow_heap_dirty_insert
             AFTER INSERT ON %s
             FOR EACH STATEMENT
             EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
        reloid
    );
    IF identity_mode = 'primary_key' THEN
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_update
                 AFTER UPDATE ON %s
                 REFERENCING OLD TABLE AS rvbbit_old_rows
                 FOR EACH STATEMENT
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_delete
                 AFTER DELETE ON %s
                 REFERENCING OLD TABLE AS rvbbit_old_rows
                 FOR EACH STATEMENT
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
            reloid
        );
    ELSE
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_update
                 AFTER UPDATE ON %s
                 FOR EACH STATEMENT
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_dirty_delete
                 AFTER DELETE ON %s
                 FOR EACH STATEMENT
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_ctid_update
                 AFTER UPDATE ON %s
                 FOR EACH ROW
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_ctid_tombstone()',
            reloid
        );
        EXECUTE format(
            'CREATE TRIGGER rvbbit_shadow_heap_ctid_delete
                 AFTER DELETE ON %s
                 FOR EACH ROW
                 EXECUTE FUNCTION rvbbit.mark_shadow_heap_ctid_tombstone()',
            reloid
        );
    END IF;
    EXECUTE format(
        'CREATE TRIGGER rvbbit_shadow_heap_dirty_truncate
             AFTER TRUNCATE ON %s
             FOR EACH STATEMENT
             EXECUTE FUNCTION rvbbit.mark_shadow_heap_dirty()',
        reloid
    );

    -- Fire even under session_replication_role = 'replica' (logical-repl apply,
    -- pg_restore --disable-triggers, some ETL). ORIGIN/LOCAL triggers are
    -- skipped in that mode, so heap writes would bypass dirty tracking and the
    -- accelerator would go silently stale. ENABLE ALWAYS forces it.
    FOR trg IN
        SELECT tgname FROM pg_trigger
        WHERE tgrelid = reloid AND NOT tgisinternal
          AND starts_with(tgname, 'rvbbit_shadow_heap_')
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ALWAYS TRIGGER %I', reloid, trg.tgname);
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit.mark_shadow_heap_dirty()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    key_expr text;
    tombstone_gen bigint := 0;
    overlay_ready boolean := false;
    identity_mode text;
    marker_op char(1);
    marker_shard smallint;
BEGIN
    marker_op := CASE TG_OP
        WHEN 'INSERT' THEN 'I'
        WHEN 'UPDATE' THEN 'U'
        WHEN 'DELETE' THEN 'D'
        WHEN 'TRUNCATE' THEN 'T'
    END;
    marker_shard := mod(pg_backend_pid(), 1024)::smallint;

    INSERT INTO rvbbit.table_dirty_markers (table_oid, shard, dirty_op, marked_at)
    SELECT TG_RELID, marker_shard, marker_op, clock_timestamp()
    WHERE marker_op IS NOT NULL
      AND EXISTS (
          SELECT 1
          FROM rvbbit.tables
          WHERE table_oid = TG_RELID
            AND shadow_heap_retained
      )
    ON CONFLICT (table_oid, shard, dirty_op) DO NOTHING;

    -- Best-effort compatibility coalesce for code/operators that still inspect
    -- rvbbit.tables directly. NOWAIT keeps this trigger off the writer hot path;
    -- the marker table above is the authoritative dirty signal.
    BEGIN
        PERFORM 1
        FROM rvbbit.tables
        WHERE table_oid = TG_RELID
          AND shadow_heap_retained
        FOR NO KEY UPDATE NOWAIT;

        IF FOUND THEN
            UPDATE rvbbit.tables
            SET shadow_heap_dirty = true,
                dirty_has_insert = dirty_has_insert OR TG_OP = 'INSERT',
                dirty_has_update = dirty_has_update OR TG_OP = 'UPDATE',
                dirty_has_delete = dirty_has_delete OR TG_OP = 'DELETE',
                dirty_has_truncate = dirty_has_truncate OR TG_OP = 'TRUNCATE',
                -- clock_timestamp() (real wall-clock per statement), not now()
                -- (transaction start), so last_write_at reflects the actual write
                -- time and a long writer txn doesn't backdate it.
                last_write_at = clock_timestamp(),
                -- Stamp the onset only on the clean->dirty edge; keep it stable
                -- across subsequent writes so seconds_dirty measures the whole
                -- stale window, not just the last statement.
                dirty_since = CASE WHEN shadow_heap_dirty THEN dirty_since ELSE clock_timestamp() END
            WHERE table_oid = TG_RELID;
        END IF;
    EXCEPTION WHEN lock_not_available THEN
        NULL;
    END;

    identity_mode := rvbbit.accel_identity_mode(TG_RELID);
    overlay_ready := rvbbit.accel_overlay_ready(TG_RELID);
    IF TG_OP IN ('UPDATE', 'DELETE') AND overlay_ready AND identity_mode = 'primary_key'
       AND nullif(current_setting('rvbbit.snapshot_replace_target', true), '')
           IS DISTINCT FROM TG_RELID::oid::text THEN
        key_expr := rvbbit.accel_identity_expr(TG_RELID, 'rvbbit_old_rows');
        IF key_expr IS NOT NULL THEN
            tombstone_gen := rvbbit.allocate_generation(TG_RELID);
            EXECUTE format(
                'WITH old_keys AS (
                     SELECT DISTINCT %1$s AS key_json
                     FROM rvbbit_old_rows
                 )
                 INSERT INTO rvbbit.delete_log
                     (table_oid, rg_id, ordinal, deleted_xid, deleted_generation)
                 SELECT $1::oid, m.rg_id, m.ordinal, pg_current_xact_id(), $2
                 FROM old_keys k
                 JOIN rvbbit.row_identity_map m
                   ON m.table_oid = $1::oid
                  AND m.key_json = k.key_json
                 ON CONFLICT (table_oid, rg_id, ordinal) DO NOTHING',
                key_expr
            ) USING TG_RELID, tombstone_gen;
        END IF;
    ELSIF TG_OP = 'TRUNCATE'
          AND NOT coalesce((
              SELECT t.history_policy = 'current'
                FROM rvbbit.tables t
               WHERE t.table_oid = TG_RELID
          ), false)
          AND nullif(current_setting('rvbbit.snapshot_load_target', true), '')
              IS DISTINCT FROM TG_RELID::oid::text THEN
        -- Retained history can continue serving an identity overlay, so its
        -- ordinary TRUNCATE needs one tombstone per immutable row. Current-only
        -- tables instead invalidate the entire old baseline and read the heap
        -- until a full rebuild; producing row tombstones there would be pure
        -- delete-log amplification. snapshot_load also suppresses them because
        -- it publishes a complete replacement generation in this transaction.
        tombstone_gen := rvbbit.allocate_generation(TG_RELID);
        INSERT INTO rvbbit.delete_log
            (table_oid, rg_id, ordinal, deleted_xid, deleted_generation)
        SELECT TG_RELID, rg.rg_id, ord::int, pg_current_xact_id(), tombstone_gen
        FROM rvbbit.row_groups rg
        CROSS JOIN LATERAL generate_series(0, rg.n_rows - 1) AS ord
        WHERE rg.table_oid = TG_RELID
        ON CONFLICT (table_oid, rg_id, ordinal) DO NOTHING;
    END IF;

    IF TG_OP = 'TRUNCATE' THEN
        -- TRUNCATE deliberately swaps the heap relfilenode.  The statement
        -- trigger above has now recorded the complete dirty boundary. Retained
        -- tables have tombstoned the prior rows; current-only tables have
        -- invalidated the old baseline and require a full rebuild. Advance the
        -- guard's expected relfilenode transactionally; unrelated CLUSTER,
        -- VACUUM FULL, and ALTER rewrites still differ and remain blocked.
        UPDATE rvbbit.acceleration_state
           SET refresh_relfilenode = pg_relation_filenode(TG_RELID),
               updated_at = clock_timestamp()
         WHERE table_oid = TG_RELID;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit.mark_shadow_heap_ctid_tombstone()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_key_json text;
    gen_setting text;
    tombstone_gen bigint;
BEGIN
    IF nullif(current_setting('rvbbit.snapshot_replace_target', true), '') = TG_RELID::oid::text THEN
        RETURN NULL;
    END IF;
    IF TG_OP NOT IN ('UPDATE', 'DELETE')
       OR rvbbit.accel_identity_mode(TG_RELID) <> 'ctid'
       OR NOT rvbbit.accel_overlay_ready(TG_RELID) THEN
        RETURN NULL;
    END IF;

    gen_setting := nullif(current_setting('rvbbit.row_tombstone_generation', true), '');
    IF split_part(coalesce(gen_setting, ''), ':', 1) = TG_RELID::oid::text THEN
        tombstone_gen := split_part(gen_setting, ':', 2)::bigint;
    ELSE
        tombstone_gen := rvbbit.allocate_generation(TG_RELID);
        PERFORM set_config(
            'rvbbit.row_tombstone_generation',
            TG_RELID::oid::text || ':' || tombstone_gen::text,
            true
        );
    END IF;

    old_key_json := jsonb_build_array(OLD.ctid::text)::text;
    INSERT INTO rvbbit.delete_log
        (table_oid, rg_id, ordinal, deleted_xid, deleted_generation)
    SELECT TG_RELID, m.rg_id, m.ordinal, pg_current_xact_id(), tombstone_gen
    FROM rvbbit.row_identity_map m
    WHERE m.table_oid = TG_RELID
      AND m.key_json = old_key_json
    ON CONFLICT (table_oid, rg_id, ordinal) DO NOTHING;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION rvbbit.snapshot_load_concurrent(dest regclass, source_query text)
RETURNS TABLE (generation bigint, rows_loaded bigint, action text)
LANGUAGE plpgsql AS $fn$
DECLARE
    stage_name text := '_rvbbit_snapshot_' || dest::oid::text;
    n bigint;
    written bigint;
    g bigint;
    prior_target text := current_setting('rvbbit.snapshot_replace_target', true);
    prior_heap text := current_setting('rvbbit.force_heap_scan', true);
BEGIN
    IF NOT rvbbit.is_rvbbit_table(dest) THEN
        RAISE EXCEPTION '% is not an rvbbit table', dest;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = dest AND relkind = 'r' AND NOT relispartition) THEN
        RAISE EXCEPTION 'concurrent snapshot requires an ordinary, non-partitioned table: %', dest;
    END IF;
    -- Same generation lock as compact/rebuild/variant workers, always before
    -- relation locks. A busy publisher leaves the committed snapshot intact.
    IF NOT pg_try_advisory_xact_lock((1380336724::bigint << 32) | dest::oid::bigint) THEN
        RAISE EXCEPTION 'snapshot publisher busy: %', dest USING ERRCODE = '55P03';
    END IF;
    EXECUTE format('LOCK TABLE ONLY %s IN ACCESS SHARE MODE NOWAIT', dest);
    IF NOT rvbbit._shadow_heap_dirty_tracking_matches(dest) THEN
        RAISE EXCEPTION 'snapshot dirty tracking needs repair: %', dest
            USING ERRCODE = '55000',
                  HINT = 'Run rvbbit.install_shadow_heap_dirty_triggers and commit before refreshing.';
    END IF;

    -- Materialize the expensive source query before taking the writer guard.
    -- Temp data and all later changes disappear on error/cancellation/rollback.
    EXECUTE format('CREATE TEMP TABLE %I ON COMMIT DROP AS %s', stage_name, source_query);
    GET DIAGNOSTICS n = ROW_COUNT;
    -- EXCLUSIVE still permits ordinary SELECT (ACCESS SHARE). It excludes
    -- writers and SELECT FOR UPDATE, avoiding a late row-lock wait. NOWAIT
    -- prevents maintenance from forming a queue ahead of later requests.
    EXECUTE format('LOCK TABLE ONLY %s IN EXCLUSIVE MODE NOWAIT', dest);
    PERFORM set_config('rvbbit.snapshot_replace_target', dest::oid::text, true);
    PERFORM set_config('rvbbit.force_heap_scan', 'on', true);
    EXECUTE format('DELETE FROM ONLY %s', dest);
    EXECUTE format('INSERT INTO %s SELECT * FROM pg_temp.%I', dest, stage_name);
    GET DIAGNOSTICS written = ROW_COUNT;
    IF written <> n THEN
        RAISE EXCEPTION 'snapshot insert count mismatch for %: staged %, inserted %', dest, n, written;
    END IF;
    PERFORM set_config('rvbbit.snapshot_replace_target', coalesce(prior_target, ''), true);

    -- Export the new, still-private heap versions. This preserves their real
    -- CTIDs/identity map, constraints, indexes, table OID and heap fallback.
    -- compact(keep_heap=true) is deliberately NOT used: it also performs DDL.
    SELECT rvbbit.export_to_parquet(dest::oid) INTO written;
    IF written <> n THEN
        RAISE EXCEPTION 'snapshot export count mismatch for %: staged %, exported %', dest, n, written;
    END IF;
    SELECT next_generation - 1 INTO g FROM rvbbit.tables WHERE table_oid = dest;
    INSERT INTO rvbbit.generations(table_oid, generation, n_rows, n_row_groups)
    VALUES (dest, g, 0, 0) ON CONFLICT ON CONSTRAINT generations_pkey DO NOTHING;
    UPDATE rvbbit.tables
       SET min_visible_generation = g, generation_semantics = 'snapshot',
           shadow_heap_retained = true, shadow_heap_dirty = false,
           dirty_has_insert = false, dirty_has_update = false,
           dirty_has_delete = false, dirty_has_truncate = false
     WHERE table_oid = dest;
    PERFORM rvbbit.clear_table_dirty_markers(dest::oid);
    IF (SELECT history_policy FROM rvbbit.tables WHERE table_oid = dest) = 'current' THEN
        PERFORM rvbbit.prune_snapshot_history(dest);
    END IF;
    -- ANALYZE is reader-compatible; avoid the stale estimates of the old cube.
    EXECUTE format('ANALYZE %s', dest);
    PERFORM set_config('rvbbit.force_heap_scan', coalesce(prior_heap, 'off'), true);
    EXECUTE format('DROP TABLE pg_temp.%I', stage_name);
    RETURN QUERY SELECT g, n, CASE WHEN n = 0 THEN 'empty' ELSE 'snapshot' END;
END;
$fn$;

COMMENT ON FUNCTION rvbbit.snapshot_load_concurrent(regclass,text) IS
'Atomic MVCC snapshot replacement: stages query results, preserves heap and retained generations, builds acceleration without blocking ordinary readers. Serializes destination writers with EXCLUSIVE NOWAIT; no TRUNCATE or routine DDL. Requires preinstalled dirty tracking.';

CREATE OR REPLACE FUNCTION rvbbit.refresh_cube(p_name text)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE
    v_dest regclass;
    v_sql text;
    v_rows bigint;
    v_policy record;
    v_policy_json jsonb;
    v_started timestamptz := clock_timestamp();
    v_seconds numeric;
    v_missing text;
BEGIN
    -- Share the definition/redefinition lock namespace; another refresh must
    -- not wait and accumulate a queue of requests for this cube.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.cube:' || p_name, 0)) THEN
        RAISE EXCEPTION 'rvbbit.refresh_cube: cube % is busy', p_name
            USING ERRCODE = '55P03';
    END IF;

    v_dest := to_regclass('cubes.' || quote_ident(p_name));
    IF v_dest IS NULL THEN
        RAISE EXCEPTION 'rvbbit.refresh_cube: cube % does not exist (define it first)', p_name;
    END IF;
    SELECT sql INTO v_sql FROM rvbbit.cube_catalog WHERE name = p_name;
    IF v_sql IS NULL THEN
        RAISE EXCEPTION 'rvbbit.refresh_cube: no definition for cube %', p_name;
    END IF;

    SELECT string_agg(s.rel::text, ', ' ORDER BY s.rel::oid) INTO v_missing
    FROM rvbbit._cube_sources_missing_dirty_tracking(v_sql) AS s(rel);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'rvbbit.refresh_cube: source tracking needs repair for %: %', p_name, v_missing
            USING ERRCODE = '55000',
                  HINT = 'Run SELECT rvbbit._install_cube_source_dirty_tracking(sql) FROM rvbbit.cube_catalog WHERE name = <cube name>; commit that setup before refreshing. The scheduled sweep does this automatically.';
    END IF;

    SELECT * INTO v_policy
    FROM rvbbit.cube_refresh_policy_effective(p_name);
    v_policy_json := to_jsonb(v_policy);

    PERFORM set_config('rvbbit.df_inprocess', 'off', true);
    PERFORM set_config('rvbbit.duck_threads', v_policy.query_threads::text, true);
    PERFORM set_config('rvbbit.compact_writer_threads', v_policy.writer_threads::text, true);
    PERFORM set_config('rvbbit.compact_scan_chunk_rows', v_policy.scan_chunk_rows::text, true);
    IF v_policy.metadata_profile IS NOT NULL THEN
        PERFORM set_config('rvbbit.compact_metadata_profile', v_policy.metadata_profile, true);
    END IF;
    PERFORM set_config(
        'rvbbit.compact_variants_sync',
        CASE WHEN v_policy.refresh_variants = 'sync' THEN 'on' ELSE 'off' END,
        true
    );

    BEGIN
        SELECT rows_loaded INTO v_rows FROM rvbbit.snapshot_load_concurrent(v_dest, v_sql);
        v_seconds := extract(epoch FROM clock_timestamp() - v_started);
        UPDATE rvbbit.cube_control
           SET refreshed_at = now(),
               last_refresh_started_at = v_started,
               last_rows = v_rows,
               last_error = NULL,
               last_refresh_policy = v_policy_json,
               last_refresh_seconds = v_seconds,
               updated_at = now()
         WHERE cube_name = p_name;
    EXCEPTION WHEN OTHERS THEN
        UPDATE rvbbit.cube_control
           SET last_error = SQLERRM,
               last_refresh_started_at = v_started,
               last_refresh_policy = v_policy_json,
               last_refresh_seconds = extract(epoch FROM clock_timestamp() - v_started),
               updated_at = now()
         WHERE cube_name = p_name;
        RAISE;
    END;
    RETURN v_rows;
END;
$fn$;

CREATE OR REPLACE PROCEDURE rvbbit.refresh_all_cubes(
    p_category        text    DEFAULT NULL,
    p_subcategory     text    DEFAULT NULL,
    p_sleep_seconds   numeric DEFAULT 0.5,
    p_force           boolean DEFAULT false,
    p_max_cubes       integer DEFAULT NULL,
    p_max_age_seconds integer DEFAULT NULL,
    p_retry_errors    boolean DEFAULT false
) LANGUAGE plpgsql AS $fn$
DECLARE
    rec record;
    v_seen integer := 0;
    v_ready boolean;
BEGIN
    FOR rec IN
        SELECT s.name
          FROM rvbbit.cube_refresh_status s
         WHERE (p_category    IS NULL OR s.category    = p_category)
           AND (p_subcategory IS NULL OR s.subcategory = p_subcategory)
           AND s.refresh_mode <> 'manual'
           AND (
                coalesce(p_force, false)
                OR s.recommended_action = 'refresh_cube'
                OR (
                    coalesce(p_retry_errors, false)
                    AND s.recommended_action = 'fix_error'
                )
                OR (
                    p_max_age_seconds IS NOT NULL
                    AND coalesce(s.seconds_since_refresh, 999999999) >= p_max_age_seconds
                )
           )
         ORDER BY
           CASE WHEN s.source_dirty THEN 0 ELSE 1 END,
           s.source_seconds_dirty DESC NULLS LAST,
           s.seconds_since_refresh DESC NULLS LAST,
           s.name
    LOOP
        EXIT WHEN p_max_cubes IS NOT NULL AND v_seen >= p_max_cubes;
        v_seen := v_seen + 1;
        -- Commit trigger setup before snapshot_load. An error subtransaction
        -- releases any source locks acquired before a later source was busy.
        v_ready := true;
        BEGIN
            IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.cube:' || rec.name, 0)) THEN
                RAISE EXCEPTION 'cube % is busy', rec.name USING ERRCODE = '55P03';
            END IF;
            PERFORM rvbbit._install_cube_source_dirty_tracking(c.sql)
            FROM rvbbit.cube_catalog c WHERE c.name = rec.name;
            PERFORM rvbbit.install_shadow_heap_dirty_triggers(
                to_regclass('cubes.' || quote_ident(rec.name)));
        EXCEPTION
            WHEN lock_not_available THEN
                v_ready := false;
                RAISE NOTICE 'rvbbit.refresh_all_cubes: skipping busy cube %: %', rec.name, SQLERRM;
            WHEN others THEN
                v_ready := false;
                UPDATE rvbbit.cube_control
                   SET last_error = SQLERRM, updated_at = now()
                 WHERE cube_name = rec.name;
        END;
        COMMIT;
        IF NOT v_ready THEN
            CONTINUE;
        END IF;

        BEGIN
            PERFORM rvbbit.refresh_cube(rec.name);
        EXCEPTION
          WHEN lock_not_available THEN
            -- Keep freshness/error state intact so a later sweep can retry.
            RAISE NOTICE 'rvbbit.refresh_all_cubes: skipping busy cube %: %', rec.name, SQLERRM;
          WHEN others THEN
            UPDATE rvbbit.cube_control
               SET last_error = SQLERRM, updated_at = now()
             WHERE cube_name = rec.name;
        END;
        COMMIT;
        IF coalesce(p_sleep_seconds, 0) > 0 THEN
            PERFORM pg_sleep(p_sleep_seconds);
        END IF;
    END LOOP;
END;
$fn$;


CREATE OR REPLACE FUNCTION rvbbit.define_cube(
    p_name         text,
    p_sql          text,
    p_grain        text  DEFAULT NULL,
    p_description  text  DEFAULT NULL,
    p_owner        text  DEFAULT NULL,
    p_refresh_cron text  DEFAULT NULL,
    p_category     text  DEFAULT NULL,
    p_labels       jsonb DEFAULT '{}'::jsonb
) RETURNS integer LANGUAGE plpgsql AS $fn$
DECLARE
    v_version integer;
    v_qual    text := 'cubes.' || quote_ident(p_name);
    v_created boolean := false;
BEGIN
    IF p_name IS NULL OR btrim(p_name) = '' THEN
        RAISE EXCEPTION 'rvbbit.define_cube: name is required';
    END IF;
    IF p_name !~ '^[a-z_][a-z0-9_]*$' THEN
        RAISE EXCEPTION 'rvbbit.define_cube: name must be a lowercase identifier (got %)', p_name;
    END IF;
    IF p_sql IS NULL OR btrim(p_sql) = '' THEN
        RAISE EXCEPTION 'rvbbit.define_cube: sql is required';
    END IF;
    p_sql := rtrim(btrim(p_sql), E' \t\r\n;');
    CREATE SCHEMA IF NOT EXISTS cubes;
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.cube:' || p_name, 0));
    SELECT coalesce(max(version), 0) + 1
      INTO v_version
      FROM rvbbit.cube_defs
     WHERE name = p_name;
    INSERT INTO rvbbit.cube_defs
        (name, version, sql, grain, description, owner, refresh_cron, category, labels)
    VALUES
        (p_name, v_version, p_sql, p_grain, p_description, p_owner, p_refresh_cron, p_category,
         coalesce(p_labels, '{}'::jsonb));
    INSERT INTO rvbbit.cube_control (cube_name)
    VALUES (p_name)
    ON CONFLICT (cube_name) DO NOTHING;

    IF to_regclass(v_qual) IS NULL THEN
        EXECUTE format('CREATE TABLE %s USING rvbbit AS %s WITH NO DATA', v_qual, p_sql);
        v_created := true;
    END IF;
    IF v_created THEN
        PERFORM rvbbit.set_acceleration_storage_policy(
            v_qual::regclass,
            history_policy => 'retained'
        );
    END IF;

    -- Definition is already a DDL operation and remains atomic. Only new or
    -- changed source tracking takes a lock here; later refreshes do no DDL.
    -- Call the installer and commit beforehand to avoid holding setup locks
    -- through the initial build when defining a cube on a busy source.
    PERFORM rvbbit._install_cube_source_dirty_tracking(p_sql);
    PERFORM rvbbit.install_shadow_heap_dirty_triggers(v_qual::regclass);
    PERFORM rvbbit.refresh_cube(p_name);
    BEGIN
        PERFORM rvbbit.register_cube_node(p_name);
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'rvbbit.define_cube: catalog registration for % failed: %', p_name, SQLERRM;
    END;
    IF nullif(btrim(p_category), '') IS NOT NULL AND p_category <> 'proposed' THEN
        BEGIN
            PERFORM rvbbit.set_category('cube', p_name, p_category, NULL);
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;
    RETURN v_version;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.redefine_cube(
    p_name        text,
    p_sql         text,
    p_grain       text DEFAULT NULL,
    p_description text DEFAULT NULL,
    p_owner       text DEFAULT NULL,
    p_category    text DEFAULT NULL,
    p_subcategory text DEFAULT NULL
) RETURNS integer LANGUAGE plpgsql AS $fn$
DECLARE
    v_qual text := 'cubes.' || quote_ident(p_name);
    cur rvbbit.cube_defs%ROWTYPE;
    v_version integer;
    v_old_cols text;
    v_new_cols text;
    v_recreated boolean := false;
BEGIN
    SELECT * INTO cur
      FROM rvbbit.cube_defs
     WHERE name = p_name
     ORDER BY created_at DESC, version DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rvbbit.redefine_cube: cube % does not exist (use define_cube)', p_name;
    END IF;
    IF nullif(btrim(p_sql), '') IS NULL THEN
        RAISE EXCEPTION 'rvbbit.redefine_cube: sql is required';
    END IF;
    p_sql := rtrim(btrim(p_sql), E' \t\r\n;');

    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.cube:' || p_name, 0));
    SELECT coalesce(max(version), 0) + 1
      INTO v_version
      FROM rvbbit.cube_defs
     WHERE name = p_name;
    INSERT INTO rvbbit.cube_defs
        (name, version, sql, grain, description, owner, refresh_cron, category, labels)
    VALUES (
        p_name,
        v_version,
        p_sql,
        coalesce(nullif(btrim(p_grain), ''), cur.grain),
        coalesce(nullif(btrim(p_description), ''), cur.description),
        coalesce(nullif(btrim(p_owner), ''), cur.owner),
        cur.refresh_cron,
        coalesce(nullif(btrim(p_category), ''), cur.category),
        cur.labels
    );

    IF to_regclass(v_qual) IS NOT NULL THEN
        SELECT string_agg(
                   a.attname || ':' || format_type(a.atttypid, a.atttypmod),
                   ',' ORDER BY a.attnum
               )
          INTO v_old_cols
          FROM pg_attribute a
         WHERE a.attrelid = v_qual::regclass
           AND a.attnum > 0
           AND NOT a.attisdropped;
        BEGIN
            EXECUTE 'DROP TABLE IF EXISTS _redef_shape';
            EXECUTE format(
                'CREATE TEMP TABLE _redef_shape AS SELECT * FROM (%s) _q WITH NO DATA',
                p_sql
            );
            SELECT string_agg(
                       a.attname || ':' || format_type(a.atttypid, a.atttypmod),
                       ',' ORDER BY a.attnum
                   )
              INTO v_new_cols
              FROM pg_attribute a
             WHERE a.attrelid = '_redef_shape'::regclass
               AND a.attnum > 0
               AND NOT a.attisdropped;
            EXECUTE 'DROP TABLE IF EXISTS _redef_shape';
        EXCEPTION WHEN OTHERS THEN
            v_new_cols := NULL;
        END;
    END IF;

    IF to_regclass(v_qual) IS NULL OR v_new_cols IS DISTINCT FROM v_old_cols THEN
        IF to_regclass(v_qual) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %s', v_qual);
        END IF;
        EXECUTE format('CREATE TABLE %s USING rvbbit AS %s WITH NO DATA', v_qual, p_sql);
        v_recreated := true;
    END IF;
    IF v_recreated THEN
        PERFORM rvbbit.set_acceleration_storage_policy(
            v_qual::regclass,
            history_policy => 'retained'
        );
    END IF;

    -- Definition is already a DDL operation and remains atomic. Only new or
    -- changed source tracking takes a lock here; later refreshes do no DDL.
    -- Call the installer and commit beforehand to avoid holding setup locks
    -- through the initial build when defining a cube on a busy source.
    PERFORM rvbbit._install_cube_source_dirty_tracking(p_sql);
    PERFORM rvbbit.install_shadow_heap_dirty_triggers(v_qual::regclass);
    PERFORM rvbbit.refresh_cube(p_name);
    BEGIN
        PERFORM rvbbit.register_cube_node(p_name);
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
    IF nullif(btrim(p_category), '') IS NOT NULL THEN
        BEGIN
            PERFORM rvbbit.set_category(
                'cube', p_name, p_category, nullif(btrim(p_subcategory), '')
            );
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;
    RETURN v_version;
END
$fn$;
