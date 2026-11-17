-- Routine cube refreshes must never hold source-table trigger DDL locks while
-- snapshot_load scans, writes, and compacts the destination. Repair tracking in
-- a separate cron transaction; keep the existing define/redefine API atomic.
-- This does NOT make snapshot_load concurrent: its destination TRUNCATE still
-- holds ACCESS EXCLUSIVE through publication. NOWAIT avoids queued refreshes.
-- No source-table DDL or data rebuild is performed while applying this migration.

CREATE OR REPLACE FUNCTION rvbbit._cube_sources_missing_dirty_tracking(p_sql text)
RETURNS SETOF regclass
LANGUAGE sql VOLATILE AS $fn$
    SELECT DISTINCT t.table_oid::regclass
    FROM unnest(coalesce(rvbbit._cube_source_tables(p_sql), '{}'::text[])) AS src(name)
    JOIN rvbbit.tables t ON t.table_oid = to_regclass(src.name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger tr
        WHERE tr.tgrelid = t.table_oid
          AND tr.tgname = 'rvbbit_cube_source_write'
          AND tr.tgfoid = 'rvbbit.mark_cube_source_write()'::regprocedure
          -- AFTER STATEMENT INSERT | DELETE | UPDATE | TRUNCATE.
          AND tr.tgtype = 60
          AND tr.tgenabled IN ('O', 'A')
          AND NOT tr.tgisinternal
          AND tr.tgconstraint = 0
          AND NOT tr.tgdeferrable AND NOT tr.tginitdeferred
          AND tr.tgnargs = 0 AND tr.tgattr = ''::int2vector
          AND tr.tgqual IS NULL
          AND tr.tgoldtable IS NULL AND tr.tgnewtable IS NULL
    )
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._install_cube_source_dirty_tracking(p_sql text)
RETURNS integer
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_rel regclass;
    v_installed integer := 0;
BEGIN
    FOR v_rel IN
        SELECT s.rel FROM rvbbit._cube_sources_missing_dirty_tracking(p_sql) AS s(rel)
        ORDER BY s.rel::oid
    LOOP
        -- DROP TRIGGER requires ACCESS EXCLUSIVE. Never queue this DDL behind
        -- live readers/writers, where it could in turn hold up later requests.
        EXECUTE format('LOCK TABLE ONLY %s IN ACCESS EXCLUSIVE MODE NOWAIT', v_rel);
        IF EXISTS (
            SELECT 1 FROM rvbbit._cube_sources_missing_dirty_tracking(p_sql) AS s(rel)
            WHERE s.rel = v_rel
        ) THEN
            EXECUTE format('DROP TRIGGER IF EXISTS rvbbit_cube_source_write ON %s', v_rel);
            EXECUTE format(
                'CREATE TRIGGER rvbbit_cube_source_write
                 AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %s
                 FOR EACH STATEMENT EXECUTE FUNCTION rvbbit.mark_cube_source_write()', v_rel);
            v_installed := v_installed + 1;
        END IF;
    END LOOP;
    RETURN v_installed;
END;
$fn$;

COMMENT ON FUNCTION rvbbit._install_cube_source_dirty_tracking(text) IS
'Install or repair missing cube source triggers without waiting for source locks. Returns the number changed. Commit this setup before a routine refresh; valid triggers incur no DDL.';

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
        -- snapshot_load will TRUNCATE the cube. Take that same destination
        -- lock without waiting before starting expensive work.
        EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT', v_dest);
        SELECT rows_loaded INTO v_rows FROM rvbbit.snapshot_load(v_dest, v_sql);
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
