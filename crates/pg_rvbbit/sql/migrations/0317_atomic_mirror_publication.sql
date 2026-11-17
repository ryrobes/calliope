-- 0317: Publish dlt snapshots atomically and avoid no-op target mutations.
--
-- dlt's truncate-and-insert strategy commits TRUNCATE before its load jobs.
-- That exposes an empty/partial reporting table and makes RVBBIT correctly
-- invalidate acceleration before the replacement is complete.  Hosted mirrors
-- now load CSV files through COPY into dlt's private staging dataset, then call
-- this publisher once after every staging job succeeds.

ALTER TABLE rvbbit.mirror_jobs
    DROP CONSTRAINT IF EXISTS mirror_jobs_loader_check;
ALTER TABLE rvbbit.mirror_jobs
    ADD CONSTRAINT mirror_jobs_loader_check
    CHECK (loader_file_format IN ('csv', 'insert_values'));
ALTER TABLE rvbbit.mirror_jobs
    ALTER COLUMN loader_file_format SET DEFAULT 'csv';

-- Existing hosted jobs should receive the safe/bulk default automatically.
UPDATE rvbbit.mirror_jobs
   SET loader_file_format = 'csv',
       updated_at = clock_timestamp()
 WHERE loader_file_format = 'insert_values';

CREATE TABLE IF NOT EXISTS rvbbit.mirror_publications (
    publication_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_oid oid NOT NULL,
    target_relation text NOT NULL,
    staging_relation text NOT NULL,
    action text NOT NULL,
    source_rows bigint NOT NULL DEFAULT 0,
    rows_inserted bigint NOT NULL DEFAULT 0,
    rows_updated bigint NOT NULL DEFAULT 0,
    rows_deleted bigint NOT NULL DEFAULT 0,
    key_columns text[] NOT NULL DEFAULT ARRAY[]::text[],
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    CONSTRAINT mirror_publications_action_check
        CHECK (action IN ('noop', 'merge', 'replace')),
    CONSTRAINT mirror_publications_counts_check CHECK (
        source_rows >= 0 AND rows_inserted >= 0
        AND rows_updated >= 0 AND rows_deleted >= 0
    )
);

CREATE INDEX IF NOT EXISTS mirror_publications_target_time_idx
    ON rvbbit.mirror_publications (target_oid, published_at DESC);

REVOKE ALL ON rvbbit.mirror_publications FROM PUBLIC;

CREATE OR REPLACE FUNCTION rvbbit.publish_mirror_snapshot(
    requested_target regclass,
    requested_staging regclass,
    requested_columns text[],
    requested_key_columns text[] DEFAULT ARRAY[]::text[]
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $publish_mirror_snapshot$
DECLARE
    all_columns text[];
    business_columns text[];
    compare_columns text[];
    key_columns text[];
    update_columns text[];
    all_list text;
    staging_compare_list text;
    target_compare_list text;
    key_list text;
    key_match text;
    key_nulls text;
    update_set text;
    update_changed text;
    changed boolean := false;
    duplicate_keys boolean := false;
    null_keys boolean := false;
    source_rows bigint := 0;
    inserted_rows bigint := 0;
    updated_rows bigint := 0;
    deleted_rows bigint := 0;
    action_name text := 'noop';
    result jsonb;
BEGIN
    IF requested_target IS NULL OR requested_staging IS NULL
       OR requested_target = requested_staging THEN
        RAISE EXCEPTION 'mirror publication requires distinct target and staging relations';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = requested_target::oid
    ) THEN
        -- Fresh tables are normally born through CREATE TABLE ... USING
        -- rvbbit.  Keep upgrades from the pre-registration mirror worker safe.
        PERFORM rvbbit.enable_table(requested_target);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = requested_target::oid
    ) THEN
        RAISE EXCEPTION 'mirror publication target % could not be registered with RVBBIT',
            requested_target;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_class c
         WHERE c.oid = requested_staging
           AND c.relkind IN ('r', 'p')
    ) THEN
        RAISE EXCEPTION 'mirror publication staging relation % is not a table',
            requested_staging;
    END IF;

    SELECT array_agg(value ORDER BY ordinal)
      INTO all_columns
      FROM (
          SELECT btrim(value) AS value, min(ordinal) AS ordinal
            FROM unnest(coalesce(requested_columns, ARRAY[]::text[]))
                 WITH ORDINALITY AS requested(value, ordinal)
           WHERE btrim(value) <> ''
           GROUP BY btrim(value)
      ) normalized;
    IF coalesce(cardinality(all_columns), 0) = 0
       OR cardinality(all_columns) <> cardinality(requested_columns) THEN
        RAISE EXCEPTION 'mirror publication columns must be non-empty and unique';
    END IF;

    SELECT array_agg(value ORDER BY ordinal)
      INTO key_columns
      FROM (
          SELECT btrim(value) AS value, min(ordinal) AS ordinal
            FROM unnest(coalesce(requested_key_columns, ARRAY[]::text[]))
                 WITH ORDINALITY AS requested(value, ordinal)
           WHERE btrim(value) <> ''
           GROUP BY btrim(value)
      ) normalized;
    key_columns := coalesce(key_columns, ARRAY[]::text[]);
    IF cardinality(key_columns) <> cardinality(coalesce(requested_key_columns, ARRAY[]::text[]))
       OR NOT key_columns <@ all_columns THEN
        RAISE EXCEPTION 'mirror publication key columns must be unique target columns';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_attribute a
         WHERE a.attrelid = requested_target
           AND a.attnum > 0
           AND NOT a.attisdropped
           AND a.attname = ANY(all_columns)
    ) <> cardinality(all_columns)
    OR (
        SELECT count(*)
          FROM pg_attribute a
         WHERE a.attrelid = requested_staging
           AND a.attnum > 0
           AND NOT a.attisdropped
           AND a.attname = ANY(all_columns)
    ) <> cardinality(all_columns) THEN
        RAISE EXCEPTION 'mirror publication columns do not match target and staging relations';
    END IF;

    SELECT coalesce(array_agg(column_name ORDER BY ordinal), ARRAY[]::text[])
      INTO business_columns
      FROM unnest(all_columns) WITH ORDINALITY AS cols(column_name, ordinal)
     WHERE column_name !~ '^_dlt_';
    compare_columns := CASE
        WHEN cardinality(business_columns) > 0 THEN business_columns
        ELSE all_columns
    END;
    update_columns := ARRAY(
        SELECT column_name
          FROM unnest(business_columns) AS cols(column_name)
         WHERE NOT column_name = ANY(key_columns)
    );

    SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal)
      INTO all_list
      FROM unnest(all_columns) WITH ORDINALITY AS cols(column_name, ordinal);
    SELECT string_agg(format('s.%I', column_name), ', ' ORDER BY ordinal),
           string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal)
      INTO staging_compare_list, target_compare_list
      FROM unnest(compare_columns) WITH ORDINALITY AS cols(column_name, ordinal);

    EXECUTE format('SELECT count(*) FROM %s', requested_staging)
       INTO source_rows;
    EXECUTE format(
        'SELECT EXISTS ('
        'SELECT 1 FROM ('
        '(SELECT to_jsonb(ROW(%1$s)) FROM %2$s s '
        ' EXCEPT ALL '
        ' SELECT to_jsonb(ROW(%3$s)) FROM %4$s t) '
        'UNION ALL '
        '(SELECT to_jsonb(ROW(%3$s)) FROM %4$s t '
        ' EXCEPT ALL '
        ' SELECT to_jsonb(ROW(%1$s)) FROM %2$s s)'
        ') AS mirror_delta LIMIT 1)',
        staging_compare_list,
        requested_staging,
        target_compare_list,
        requested_target
    ) INTO changed;

    IF changed AND cardinality(key_columns) > 0 THEN
        SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal),
               string_agg(
                   format('t.%1$I IS NOT DISTINCT FROM s.%1$I', column_name),
                   ' AND ' ORDER BY ordinal
               ),
               string_agg(format('s.%I IS NULL', column_name), ' OR ' ORDER BY ordinal)
          INTO key_list, key_match, key_nulls
          FROM unnest(key_columns) WITH ORDINALITY AS cols(column_name, ordinal);

        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM %s s WHERE %s LIMIT 1)',
            requested_staging,
            key_nulls
        ) INTO null_keys;
        IF null_keys THEN
            RAISE EXCEPTION 'mirror staging relation % contains a null source key',
                requested_staging;
        END IF;
        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %s s GROUP BY %s HAVING count(*) > 1 LIMIT 1)',
            requested_staging,
            key_list
        ) INTO duplicate_keys;
        IF duplicate_keys THEN
            RAISE EXCEPTION 'mirror staging relation % contains duplicate source keys',
                requested_staging;
        END IF;

        IF cardinality(update_columns) > 0 THEN
            SELECT string_agg(
                       format('%1$I = s.%1$I', column_name),
                       ', ' ORDER BY ordinal
                   ),
                   format(
                       'to_jsonb(ROW(%s)) IS DISTINCT FROM to_jsonb(ROW(%s))',
                       string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal),
                       string_agg(format('s.%I', column_name), ', ' ORDER BY ordinal)
                   )
              INTO update_set, update_changed
              FROM unnest(update_columns)
                   WITH ORDINALITY AS cols(column_name, ordinal);
            EXECUTE format(
                'UPDATE %1$s t SET %2$s FROM %3$s s '
                'WHERE %4$s AND %5$s',
                requested_target,
                update_set,
                requested_staging,
                key_match,
                update_changed
            );
            GET DIAGNOSTICS updated_rows = ROW_COUNT;
        END IF;

        EXECUTE format(
            'INSERT INTO %1$s (%2$s) '
            'SELECT %2$s FROM %3$s s '
            'WHERE NOT EXISTS (SELECT 1 FROM %1$s t WHERE %4$s)',
            requested_target,
            all_list,
            requested_staging,
            key_match
        );
        GET DIAGNOSTICS inserted_rows = ROW_COUNT;

        EXECUTE format(
            'DELETE FROM %1$s t '
            'WHERE NOT EXISTS (SELECT 1 FROM %2$s s WHERE %3$s)',
            requested_target,
            requested_staging,
            key_match
        );
        GET DIAGNOSTICS deleted_rows = ROW_COUNT;
        action_name := 'merge';
    ELSIF changed THEN
        EXECUTE format('TRUNCATE TABLE %s', requested_target);
        EXECUTE format(
            'INSERT INTO %1$s (%2$s) SELECT %2$s FROM %3$s',
            requested_target,
            all_list,
            requested_staging
        );
        GET DIAGNOSTICS inserted_rows = ROW_COUNT;
        action_name := 'replace';
    END IF;

    INSERT INTO rvbbit.mirror_publications (
        target_oid, target_relation, staging_relation, action, source_rows,
        rows_inserted, rows_updated, rows_deleted, key_columns
    ) VALUES (
        requested_target::oid, requested_target::text, requested_staging::text,
        action_name, source_rows, inserted_rows, updated_rows, deleted_rows,
        key_columns
    );

    result := jsonb_build_object(
        'action', action_name,
        'target', requested_target::text,
        'staging', requested_staging::text,
        'source_rows', source_rows,
        'rows_inserted', inserted_rows,
        'rows_updated', updated_rows,
        'rows_deleted', deleted_rows,
        'key_columns', to_jsonb(key_columns)
    );
    RETURN result;
END
$publish_mirror_snapshot$;

REVOKE ALL ON FUNCTION rvbbit.publish_mirror_snapshot(regclass, regclass, text[], text[])
    FROM PUBLIC;

CREATE OR REPLACE VIEW rvbbit.mirror_publication_status AS
SELECT
    lineage.connection_name,
    lineage.job_name,
    lineage.source_schema,
    lineage.source_table,
    lineage.destination_schema,
    lineage.destination_table,
    latest.action AS latest_publication_action,
    latest.source_rows,
    latest.rows_inserted,
    latest.rows_updated,
    latest.rows_deleted,
    latest.key_columns,
    latest.published_at
FROM rvbbit.mirror_lineage lineage
LEFT JOIN LATERAL (
    SELECT publication.*
      FROM rvbbit.mirror_publications publication
     WHERE publication.target_oid = to_regclass(lineage.destination_relation)::oid
     ORDER BY publication.publication_id DESC
     LIMIT 1
) latest ON true;

REVOKE ALL ON rvbbit.mirror_publication_status FROM PUBLIC;

-- Preserve the public admin signature while making COPY the default for new
-- jobs.  insert_values remains accepted as an explicit compatibility choice.
CREATE OR REPLACE FUNCTION rvbbit.upsert_mirror_job(
    requested_job text,
    requested_connection text,
    requested_source_schema text,
    requested_destination_schema text,
    requested_schedule_seconds integer DEFAULT NULL,
    requested_enabled boolean DEFAULT true,
    requested_chunk_size integer DEFAULT 50000,
    requested_reflection_level text DEFAULT 'full',
    requested_loader_file_format text DEFAULT 'csv'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $upsert_mirror_job$
DECLARE
    normalized_job text := lower(btrim(requested_job));
    normalized_connection text := lower(btrim(requested_connection));
    normalized_destination_schema text := lower(btrim(requested_destination_schema));
BEGIN
    PERFORM rvbbit.require_capability_catalog_admin();
    INSERT INTO rvbbit.mirror_jobs (
        job_name, connection_name, source_schema, destination_schema,
        enabled, schedule_seconds, next_run_at, chunk_size,
        reflection_level, loader_file_format, created_by, updated_by
    ) VALUES (
        normalized_job, normalized_connection, requested_source_schema,
        normalized_destination_schema, coalesce(requested_enabled, true),
        requested_schedule_seconds,
        CASE
            WHEN coalesce(requested_enabled, true)
             AND requested_schedule_seconds IS NOT NULL THEN clock_timestamp()
            ELSE NULL
        END,
        coalesce(requested_chunk_size, 50000),
        lower(btrim(coalesce(requested_reflection_level, 'full'))),
        lower(btrim(coalesce(requested_loader_file_format, 'csv'))),
        session_user, session_user
    )
    ON CONFLICT (job_name) DO UPDATE SET
        connection_name = EXCLUDED.connection_name,
        source_schema = EXCLUDED.source_schema,
        destination_schema = EXCLUDED.destination_schema,
        enabled = EXCLUDED.enabled,
        schedule_seconds = EXCLUDED.schedule_seconds,
        next_run_at = CASE
            WHEN EXCLUDED.enabled AND EXCLUDED.schedule_seconds IS NOT NULL
                THEN coalesce(rvbbit.mirror_jobs.next_run_at, clock_timestamp())
            ELSE NULL
        END,
        chunk_size = EXCLUDED.chunk_size,
        reflection_level = EXCLUDED.reflection_level,
        loader_file_format = EXCLUDED.loader_file_format,
        updated_at = clock_timestamp(),
        updated_by = session_user;
    RETURN normalized_job;
END
$upsert_mirror_job$;

-- A no-op snapshot completed every requested table successfully; it merely
-- avoided changing the destination.  Keep run summaries intuitive while
-- retaining the per-table `skipped` status and zero mutation count.
CREATE OR REPLACE FUNCTION rvbbit.finish_mirror_run(
    requested_run_id uuid,
    requested_status text,
    requested_error_code text DEFAULT NULL,
    requested_error_message text DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $finish_mirror_run$
DECLARE
    changed integer;
BEGIN
    IF requested_status NOT IN ('succeeded','partial','failed','cancelled') THEN
        RAISE EXCEPTION 'invalid terminal mirror run status';
    END IF;
    UPDATE rvbbit.mirror_runs r
    SET status = requested_status,
        finished_at = clock_timestamp(),
        heartbeat_at = clock_timestamp(),
        tables_succeeded = summary.succeeded,
        tables_failed = summary.failed,
        rows_loaded = summary.rows_loaded,
        load_ids = summary.load_ids,
        error_code = nullif(left(coalesce(requested_error_code, ''), 120), ''),
        error_message = nullif(left(coalesce(requested_error_message, ''), 2000), '')
    FROM (
        SELECT
            count(*) FILTER (WHERE tr.status IN ('succeeded','skipped'))::integer
                AS succeeded,
            count(*) FILTER (WHERE tr.status = 'failed')::integer AS failed,
            coalesce(sum(tr.rows_loaded), 0)::bigint AS rows_loaded,
            coalesce((
                SELECT array_agg(DISTINCT load_id)
                FROM rvbbit.mirror_table_runs tr_ids
                CROSS JOIN LATERAL unnest(tr_ids.load_ids) load_id
                WHERE tr_ids.run_id = requested_run_id
            ), ARRAY[]::text[]) AS load_ids
        FROM rvbbit.mirror_table_runs tr
        WHERE tr.run_id = requested_run_id
    ) summary
    WHERE r.run_id = requested_run_id AND r.status IN ('queued','running');
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed > 0 THEN
        UPDATE rvbbit.mirror_jobs j
        SET last_run_at = clock_timestamp()
        FROM rvbbit.mirror_runs r
        WHERE r.run_id = requested_run_id AND j.job_name = r.job_name;
    END IF;
    RETURN changed > 0;
END
$finish_mirror_run$;

COMMENT ON FUNCTION rvbbit.publish_mirror_snapshot(regclass, regclass, text[], text[]) IS
    'Atomically publishes a fully loaded dlt staging snapshot: exact business-row no-op, keyed INSERT/UPDATE/DELETE reconciliation, or keyless replacement. Technical _dlt columns do not force false updates.';
COMMENT ON TABLE rvbbit.mirror_publications IS
    'One durable receipt per atomic staging publication, including no-op snapshots and exact target mutation counts.';
COMMENT ON VIEW rvbbit.mirror_publication_status IS
    'Latest atomic publication outcome for every configured mirror table.';
