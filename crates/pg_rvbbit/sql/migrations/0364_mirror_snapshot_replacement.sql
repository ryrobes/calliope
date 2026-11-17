-- 0364: Publish mirrors as the complete snapshots they are.
--
-- Keyed row-by-row UPDATE/DELETE is a good generic merge primitive, but a bad
-- default for a full source snapshot on an accelerated table: every changed
-- row becomes a live tombstone and sufficiently busy publishers can eliminate
-- the quiet lock window needed by a later fold.  Use the existing
-- snapshot_load_current path whenever TRUNCATE is legal.  It publishes one
-- complete generation and retires the previous one without row tombstones.
-- Keep the keyed merge fallback for targets referenced by foreign keys.

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
    can_snapshot_replace boolean := false;
    source_rows bigint := 0;
    target_rows bigint := 0;
    inserted_rows bigint := 0;
    updated_rows bigint := 0;
    deleted_rows bigint := 0;
    loaded_rows bigint := 0;
    offending_count bigint := 0;
    offending_sample text := '';
    action_name text := 'noop';
    publication_mode text := 'noop';
    result jsonb;
BEGIN
    IF requested_target IS NULL OR requested_staging IS NULL
       OR requested_target = requested_staging THEN
        RAISE EXCEPTION 'mirror publication requires distinct target and staging relations';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = requested_target::oid
    ) THEN
        PERFORM rvbbit.enable_table(requested_target);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.tables t WHERE t.table_oid = requested_target::oid
    ) THEN
        RAISE EXCEPTION 'mirror publication target % could not be registered with RVBBIT',
            requested_target;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
         WHERE c.oid = requested_staging AND c.relkind IN ('r', 'p')
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
        SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = requested_target AND a.attnum > 0
           AND NOT a.attisdropped AND a.attname = ANY(all_columns)
    ) <> cardinality(all_columns)
    OR (
        SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = requested_staging AND a.attnum > 0
           AND NOT a.attisdropped AND a.attname = ANY(all_columns)
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
        SELECT column_name FROM unnest(business_columns) AS cols(column_name)
         WHERE NOT column_name = ANY(key_columns)
    );

    SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal)
      INTO all_list
      FROM unnest(all_columns) WITH ORDINALITY AS cols(column_name, ordinal);
    SELECT string_agg(format('s.%I', column_name), ', ' ORDER BY ordinal),
           string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal)
      INTO staging_compare_list, target_compare_list
      FROM unnest(compare_columns) WITH ORDINALITY AS cols(column_name, ordinal);

    EXECUTE format('SELECT count(*) FROM %s', requested_staging) INTO source_rows;
    EXECUTE format('SELECT count(*) FROM %s', requested_target) INTO target_rows;
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM ('
        '(SELECT to_jsonb(ROW(%1$s)) FROM %2$s s EXCEPT ALL '
        ' SELECT to_jsonb(ROW(%3$s)) FROM %4$s t) UNION ALL '
        '(SELECT to_jsonb(ROW(%3$s)) FROM %4$s t EXCEPT ALL '
        ' SELECT to_jsonb(ROW(%1$s)) FROM %2$s s)'
        ') AS mirror_delta LIMIT 1)',
        staging_compare_list, requested_staging,
        target_compare_list, requested_target
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
            requested_staging, key_nulls
        ) INTO null_keys;
        IF null_keys THEN
            EXECUTE format('SELECT count(*) FROM %s s WHERE %s', requested_staging, key_nulls)
               INTO offending_count;
            RAISE EXCEPTION USING
                ERRCODE = 'not_null_violation',
                MESSAGE = format(
                    'mirror staging relation %s has %s row(s) with a NULL value in the declared key (%s)',
                    requested_staging, offending_count, array_to_string(key_columns, ', ')),
                HINT = 'The declared mirror key is not an identity at the source. Fix the source rows or declare a different key for this mirror table.';
        END IF;

        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM %s s GROUP BY %s HAVING count(*) > 1 LIMIT 1)',
            requested_staging, key_list
        ) INTO duplicate_keys;
        IF duplicate_keys THEN
            EXECUTE format(
                'SELECT string_agg(k, '', '' ORDER BY k) FROM ('
                'SELECT concat_ws('' / '', %s) AS k FROM %s s '
                'GROUP BY %s HAVING count(*) > 1 LIMIT 5) d',
                (SELECT string_agg(format('s.%I::text', column_name), ', ' ORDER BY ordinal)
                   FROM unnest(key_columns) WITH ORDINALITY AS c(column_name, ordinal)),
                requested_staging, key_list
            ) INTO offending_sample;
            EXECUTE format(
                'SELECT count(*) FROM (SELECT 1 FROM %s s GROUP BY %s HAVING count(*) > 1) d',
                requested_staging, key_list
            ) INTO offending_count;
            RAISE EXCEPTION USING
                ERRCODE = 'unique_violation',
                MESSAGE = format(
                    'mirror staging relation %s has %s duplicated value(s) of the declared key (%s), e.g. %s',
                    requested_staging, offending_count, array_to_string(key_columns, ', '),
                    coalesce(nullif(offending_sample, ''), '?')),
                HINT = 'The declared mirror key is not unique at the source. Fix the source rows or declare a different key for this mirror table; the target was left untouched.';
        END IF;
    END IF;

    IF changed THEN
        SELECT NOT EXISTS (
            SELECT 1 FROM pg_constraint fk
             WHERE fk.contype = 'f'
               AND fk.confrelid = requested_target
               AND fk.conrelid <> requested_target
        ) INTO can_snapshot_replace;

        IF can_snapshot_replace THEN
            SELECT s.rows_loaded
              INTO loaded_rows
              FROM rvbbit.snapshot_load_current(
                  requested_target,
                  format('SELECT %s FROM %s', all_list, requested_staging)
              ) AS s
             LIMIT 1;
            inserted_rows := coalesce(loaded_rows, source_rows);
            deleted_rows := target_rows;
            action_name := 'replace';
            publication_mode := 'snapshot_current';
        ELSIF cardinality(key_columns) > 0 THEN
            IF cardinality(update_columns) > 0 THEN
                SELECT string_agg(
                           format('%1$I = s.%1$I', column_name), ', ' ORDER BY ordinal
                       ),
                       format(
                           'to_jsonb(ROW(%s)) IS DISTINCT FROM to_jsonb(ROW(%s))',
                           string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal),
                           string_agg(format('s.%I', column_name), ', ' ORDER BY ordinal)
                       )
                  INTO update_set, update_changed
                  FROM unnest(update_columns) WITH ORDINALITY AS cols(column_name, ordinal);
                EXECUTE format(
                    'UPDATE %1$s t SET %2$s FROM %3$s s WHERE %4$s AND %5$s',
                    requested_target, update_set, requested_staging, key_match, update_changed
                );
                GET DIAGNOSTICS updated_rows = ROW_COUNT;
            END IF;

            EXECUTE format(
                'INSERT INTO %1$s (%2$s) SELECT %2$s FROM %3$s s '
                'WHERE NOT EXISTS (SELECT 1 FROM %1$s t WHERE %4$s)',
                requested_target, all_list, requested_staging, key_match
            );
            GET DIAGNOSTICS inserted_rows = ROW_COUNT;

            EXECUTE format(
                'DELETE FROM %1$s t WHERE NOT EXISTS (SELECT 1 FROM %2$s s WHERE %3$s)',
                requested_target, requested_staging, key_match
            );
            GET DIAGNOSTICS deleted_rows = ROW_COUNT;
            action_name := 'merge';
            publication_mode := 'keyed_fk_safe_fallback';
        ELSE
            IF NOT can_snapshot_replace THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'feature_not_supported',
                    MESSAGE = format(
                        'mirror target %s has inbound foreign keys and no declared source key',
                        requested_target),
                    HINT = 'Declare a stable mirror key so RVBBIT can use the foreign-key-safe keyed publication fallback.';
            END IF;
            EXECUTE format('TRUNCATE TABLE %s', requested_target);
            EXECUTE format(
                'INSERT INTO %1$s (%2$s) SELECT %2$s FROM %3$s',
                requested_target, all_list, requested_staging
            );
            GET DIAGNOSTICS inserted_rows = ROW_COUNT;
            deleted_rows := target_rows;
            action_name := 'replace';
            publication_mode := 'heap_replace_no_key';
        END IF;
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
        'publication_mode', publication_mode,
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

REVOKE ALL ON FUNCTION rvbbit.publish_mirror_snapshot(regclass,regclass,text[],text[])
    FROM PUBLIC;

COMMENT ON FUNCTION rvbbit.publish_mirror_snapshot(regclass,regclass,text[],text[]) IS
    'Atomically publish a complete mirror snapshot. Uses snapshot/current replacement without row tombstones; referenced targets retain the keyed merge fallback.';
