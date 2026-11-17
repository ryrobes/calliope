-- 0331: mirror snapshot key rejections are terminal and say which keys.
--
-- Chaos test on the live MariaDB source (duplicate widget_id rows under a
-- declared key): publish_mirror_snapshot correctly refused and left the
-- target untouched, but (a) it raised a generic P0001, which dlt classifies
-- as transient and retried five times for a deterministic failure, and (b)
-- the operator got "contains duplicate source keys" with no idea which keys
-- or how many.
--
-- The two rejections now raise with SQLSTATE 23505 (unique_violation) /
-- 23502 (not_null_violation) — integrity errors dlt treats as terminal — and
-- carry a count plus a sample of offending key values in the message and
-- DETAIL, with a HINT that names the fix (source or declared key).

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit.publish_mirror_snapshot(regclass,regclass,text[],text[])'::regprocedure
    );
    IF position('USING ERRCODE = ''unique_violation''' IN definition) > 0 THEN
        RETURN;  -- already patched
    END IF;

    -- declarations
    needle := $needle$    duplicate_keys boolean := false;
    null_keys boolean := false;$needle$;
    replacement := $replacement$    duplicate_keys boolean := false;
    null_keys boolean := false;
    offending_count bigint := 0;
    offending_sample text := '';$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0331 could not find the key-check declarations in rvbbit.publish_mirror_snapshot';
    END IF;
    definition := replace(definition, needle, replacement);

    -- null keys
    needle := $needle$        IF null_keys THEN
            RAISE EXCEPTION 'mirror staging relation % contains a null source key',
                requested_staging;
        END IF;$needle$;
    replacement := $replacement$        IF null_keys THEN
            EXECUTE format('SELECT count(*) FROM %s s WHERE %s', requested_staging, key_nulls)
               INTO offending_count;
            RAISE EXCEPTION USING
                ERRCODE = 'not_null_violation',
                MESSAGE = format(
                    'mirror staging relation %s has %s row(s) with a NULL value in the declared key (%s)',
                    requested_staging, offending_count, array_to_string(key_columns, ', ')),
                HINT = 'The declared mirror key is not an identity at the source. Fix the source rows or declare a different key for this mirror table.';
        END IF;$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0331 could not find the null-key rejection in rvbbit.publish_mirror_snapshot';
    END IF;
    definition := replace(definition, needle, replacement);

    -- duplicate keys
    needle := $needle$        IF duplicate_keys THEN
            RAISE EXCEPTION 'mirror staging relation % contains duplicate source keys',
                requested_staging;
        END IF;$needle$;
    replacement := $replacement$        IF duplicate_keys THEN
            EXECUTE format(
                'SELECT count(*), string_agg(k, '', '' ORDER BY k) FROM ('
                'SELECT concat_ws('' / '', %s) AS k FROM %s s GROUP BY %s HAVING count(*) > 1 LIMIT 5) d',
                (SELECT string_agg(format('s.%I::text', column_name), ', ' ORDER BY ordinal)
                   FROM unnest(key_columns) WITH ORDINALITY AS c(column_name, ordinal)),
                requested_staging, key_list)
               INTO offending_count, offending_sample;
            EXECUTE format(
                'SELECT count(*) FROM (SELECT 1 FROM %s s GROUP BY %s HAVING count(*) > 1) d',
                requested_staging, key_list)
               INTO offending_count;
            RAISE EXCEPTION USING
                ERRCODE = 'unique_violation',
                MESSAGE = format(
                    'mirror staging relation %s has %s duplicated value(s) of the declared key (%s), e.g. %s',
                    requested_staging, offending_count, array_to_string(key_columns, ', '),
                    coalesce(nullif(offending_sample, ''), '?')),
                HINT = 'The declared mirror key is not unique at the source. Fix the source rows or declare a different key for this mirror table; the target was left untouched.';
        END IF;$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0331 could not find the duplicate-key rejection in rvbbit.publish_mirror_snapshot';
    END IF;
    definition := replace(definition, needle, replacement);

    EXECUTE definition;
END;
$migration$;
