-- 0362: Bounded, self-maintaining catalog history.
--
-- Catalog history is engine metadata, not an operator-managed archive.  Keep
-- it bounded per crawl scope, never index loader/runtime relations whose table
-- name begins with an underscore, and cap the bytes copied from scalar values
-- into every fingerprint generation.

CREATE OR REPLACE FUNCTION rvbbit.catalog_bounded_value(
    value text,
    prefix_chars integer DEFAULT 384
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT CASE
        WHEN value IS NULL THEN NULL
        WHEN length(value) <= greatest(coalesce(prefix_chars, 384), 32) THEN value
        ELSE left(value, greatest(coalesce(prefix_chars, 384), 32))
             || '… [md5:' || md5(value)
             || '; bytes:' || octet_length(value)::text || ']'
    END
$fn$;

-- Patch the installed fingerprint function without replacing the many later
-- performance fixes layered onto it by earlier migrations.
DO $migration$
DECLARE
    definition text;
    old_dist text;
    new_dist text;
    old_minmax text;
    new_minmax text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit.catalog_fingerprint_table(regclass,integer,integer)'::regprocedure
    );

    IF position('catalog_bounded_value(%I::text)' IN definition) = 0 THEN
        old_dist := $old$SELECT jsonb_object_agg(t.v, t.c)
                     FROM (SELECT %I::text AS v, count(*) AS c
                             FROM %s WHERE %I IS NOT NULL
                            GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT %s) t$old$;
        new_dist := $new$SELECT jsonb_object_agg(t.v, t.c)
                     FROM (SELECT rvbbit.catalog_bounded_value(%I::text) AS v,
                                  count(*) AS c
                             FROM %s WHERE %I IS NOT NULL
                            GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT %s) t$new$;
        IF position(old_dist IN definition) = 0 THEN
            RAISE EXCEPTION '0362 could not locate catalog value distribution';
        END IF;
        definition := replace(definition, old_dist, new_dist);
    END IF;

    IF position('v_min := rvbbit.catalog_bounded_value(v_min)' IN definition) = 0 THEN
        old_minmax := $old$                           rc.attname, rc.attname, v_src) INTO v_min, v_max;
        EXCEPTION WHEN others THEN v_min := NULL; v_max := NULL; END;$old$;
        new_minmax := $new$                           rc.attname, rc.attname, v_src) INTO v_min, v_max;
            v_min := rvbbit.catalog_bounded_value(v_min);
            v_max := rvbbit.catalog_bounded_value(v_max);
        EXCEPTION WHEN others THEN v_min := NULL; v_max := NULL; END;$new$;
        IF position(old_minmax IN definition) = 0 THEN
            RAISE EXCEPTION '0362 could not locate catalog min/max fingerprinting';
        END IF;
        definition := replace(definition, old_minmax, new_minmax);
    END IF;

    EXECUTE definition;
END
$migration$;

-- Every catalog enumeration path gets the same userspace boundary.  Direct
-- SQL/debugging remains available; only the durable search catalog excludes
-- leading-underscore relations.
DO $migration$
DECLARE
    signature regprocedure;
    definition text;
BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'rvbbit.catalog_crawl(text[],text,integer,integer,boolean,text)'::regprocedure,
        'rvbbit.catalog_crawl_run(text[],text,integer,integer,boolean,text)'::regprocedure,
        'rvbbit.catalog_crawl_run_parallel(text[],text,integer,integer,boolean,text,integer)'::regprocedure
    ] LOOP
        definition := pg_get_functiondef(signature);
        IF position('c.relname !~ ''^_''' IN definition) = 0 THEN
            IF position('AND NOT c.relispartition' IN definition) = 0 THEN
                RAISE EXCEPTION '0362 could not locate relation enumeration in %', signature;
            END IF;
            definition := replace(
                definition,
                'AND NOT c.relispartition',
                E'AND NOT c.relispartition\n           AND c.relname !~ ''^_'''
            );
            EXECUTE definition;
        END IF;
    END LOOP;
END
$migration$;

-- Registration of an internal table should not enqueue a schema crawl just to
-- rediscover that the table is intentionally invisible.
DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit.request_catalog_crawl(text,text,text,oid)'::regprocedure
    );
    IF position('left(nullif(btrim(coalesce(p_table' IN definition) = 0 THEN
        needle := $needle$    IF v_schema = '' THEN
        RAISE EXCEPTION 'request_catalog_crawl: schema is required';
    END IF;$needle$;
        replacement := $replacement$    IF v_schema = '' THEN
        RAISE EXCEPTION 'request_catalog_crawl: schema is required';
    END IF;
    IF left(nullif(btrim(coalesce(p_table, '')), ''), 1) = '_' THEN
        RETURN NULL;
    END IF;$replacement$;
        IF position(needle IN definition) = 0 THEN
            RAISE EXCEPTION '0362 could not patch request_catalog_crawl';
        END IF;
        EXECUTE replace(definition, needle, replacement);
    END IF;
END
$migration$;

CREATE OR REPLACE FUNCTION rvbbit.catalog_maintain(
    keep_per_scope integer DEFAULT 15
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    keep_n integer := greatest(coalesce(keep_per_scope, 15), 2);
    expired_run_ids bigint[];
    private_node_ids bigint[];
    snapshots_reaped bigint := 0;
    progress_reaped bigint := 0;
    runs_reaped bigint := 0;
    requests_reaped bigint := 0;
    private_snapshots_reaped bigint := 0;
    private_docs_reaped bigint := 0;
    private_nodes_reaped bigint := 0;
    result jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.catalog-maintain', 0)) THEN
        RETURN jsonb_build_object('status', 'busy');
    END IF;

    SELECT array_agg(run_id ORDER BY run_id)
      INTO expired_run_ids
      FROM (
          SELECT run_id,
                 row_number() OVER (
                     PARTITION BY graph_id, schema_scope
                     ORDER BY finished_at DESC NULLS LAST, run_id DESC
                 ) AS scope_rank
            FROM (
                SELECT r.run_id,
                       r.finished_at,
                       r.graph_id,
                       coalesce(
                           (
                               SELECT array_agg(schema_name ORDER BY schema_name)
                                 FROM unnest(r.schemas) AS schema_name
                           ),
                           ARRAY[]::text[]
                       ) AS schema_scope
                  FROM rvbbit.catalog_runs r
                 WHERE r.status <> 'running' AND r.finished_at IS NOT NULL
            ) scoped
      ) ranked
     WHERE scope_rank > keep_n;

    IF expired_run_ids IS NOT NULL THEN
        DELETE FROM rvbbit.catalog_snapshots
         WHERE run_id = ANY(expired_run_ids);
        GET DIAGNOSTICS snapshots_reaped = ROW_COUNT;

        IF to_regclass('rvbbit.catalog_crawl_progress') IS NOT NULL THEN
            DELETE FROM rvbbit.catalog_crawl_progress
             WHERE run_id = ANY(expired_run_ids);
            GET DIAGNOSTICS progress_reaped = ROW_COUNT;
        END IF;

        DELETE FROM rvbbit.catalog_runs
         WHERE run_id = ANY(expired_run_ids);
        GET DIAGNOSTICS runs_reaped = ROW_COUNT;
    END IF;

    -- Remove already-indexed internal relations as well as excluding them from
    -- future runs. Capture their KG node IDs before deleting the catalog docs;
    -- deleting those nodes then cascades aliases, edges, and evidence.
    SELECT array_agg(DISTINCT node_id)
      INTO private_node_ids
      FROM rvbbit.catalog_docs
     WHERE rel_name LIKE '\_%' ESCAPE '\';

    DELETE FROM rvbbit.catalog_snapshots
     WHERE rel_name LIKE '\_%' ESCAPE '\';
    GET DIAGNOSTICS private_snapshots_reaped = ROW_COUNT;

    DELETE FROM rvbbit.catalog_docs
     WHERE rel_name LIKE '\_%' ESCAPE '\';
    GET DIAGNOSTICS private_docs_reaped = ROW_COUNT;

    IF private_node_ids IS NOT NULL THEN
        DELETE FROM rvbbit.kg_nodes WHERE node_id = ANY(private_node_ids);
        GET DIAGNOSTICS private_nodes_reaped = ROW_COUNT;
    END IF;

    IF to_regclass('rvbbit.catalog_crawl_requests') IS NOT NULL THEN
        DELETE FROM rvbbit.catalog_crawl_requests
         WHERE status IN ('done', 'skipped', 'error')
           AND finished_at < clock_timestamp() - interval '30 days';
        GET DIAGNOSTICS requests_reaped = ROW_COUNT;

        UPDATE rvbbit.catalog_crawl_requests
           SET status = 'skipped',
               finished_at = clock_timestamp(),
               error = 'leading-underscore relation is not catalog userspace'
         WHERE status = 'pending' AND table_name LIKE '\_%' ESCAPE '\';
    END IF;

    result := jsonb_build_object(
        'status', 'ok',
        'keep_per_scope', keep_n,
        'runs_reaped', runs_reaped,
        'snapshots_reaped', snapshots_reaped,
        'progress_rows_reaped', progress_reaped,
        'request_rows_reaped', requests_reaped,
        'private_snapshots_reaped', private_snapshots_reaped,
        'private_docs_reaped', private_docs_reaped,
        'private_nodes_reaped', private_nodes_reaped,
        'finished_at', clock_timestamp()
    );

    INSERT INTO rvbbit.settings(key, value, updated_at)
    VALUES ('catalog_maintenance_status', result, clock_timestamp())
    ON CONFLICT (key) DO UPDATE
       SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;

    RETURN result;
END
$fn$;

COMMENT ON FUNCTION rvbbit.catalog_maintain(integer) IS
    'Automatic bounded catalog hygiene: retain the newest runs per graph/schema scope, purge leading-underscore relations, and trim completed crawl bookkeeping.';

-- Catalog maintenance deletes meaningful volumes of metadata. Put those
-- tables under the same self-tuning policy as the accelerator's other
-- volume/hot catalogs while preserving any explicit operator reloptions.
DO $migration$
DECLARE
    spec record;
    reloid regclass;
    has_toast boolean;
    opts text;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('rvbbit.catalog_runs',            'volume'),
            ('rvbbit.catalog_snapshots',       'volume'),
            ('rvbbit.catalog_docs',            'volume'),
            ('rvbbit.catalog_crawl_progress',  'hot')
        ) AS catalog_table(tbl, tier)
    LOOP
        reloid := to_regclass(spec.tbl);
        IF reloid IS NULL OR EXISTS (
            SELECT 1
              FROM pg_class c,
                   unnest(coalesce(c.reloptions, '{}')) AS option
             WHERE c.oid = reloid
               AND option LIKE 'autovacuum_vacuum_scale_factor=%'
        ) THEN
            CONTINUE;
        END IF;

        SELECT c.reltoastrelid <> 0 INTO has_toast
          FROM pg_class c WHERE c.oid = reloid;
        IF spec.tier = 'volume' THEN
            opts := 'autovacuum_vacuum_scale_factor = 0.005, '
                 || 'autovacuum_vacuum_threshold = 10000, '
                 || 'autovacuum_vacuum_insert_scale_factor = 0.05, '
                 || 'autovacuum_vacuum_insert_threshold = 100000, '
                 || 'autovacuum_analyze_scale_factor = 0.01, '
                 || 'autovacuum_analyze_threshold = 10000';
            IF has_toast THEN
                opts := opts || ', toast.autovacuum_vacuum_scale_factor = 0.01'
                             || ', toast.autovacuum_vacuum_threshold = 10000';
            END IF;
        ELSE
            opts := 'autovacuum_vacuum_scale_factor = 0.02, '
                 || 'autovacuum_vacuum_threshold = 50, '
                 || 'autovacuum_analyze_scale_factor = 0.02, '
                 || 'autovacuum_analyze_threshold = 50';
            IF has_toast THEN
                opts := opts || ', toast.autovacuum_vacuum_scale_factor = 0.02'
                             || ', toast.autovacuum_vacuum_threshold = 100';
            END IF;
        END IF;
        EXECUTE format('ALTER TABLE %s SET (%s)', spec.tbl, opts);
    END LOOP;
END
$migration$;
