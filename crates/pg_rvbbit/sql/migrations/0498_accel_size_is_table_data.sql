-- 0498: the autopilot sizes a table by its own data, not its TOAST or indexes.
--
-- table_bytes was pg_total_relation_size, so out-of-line values counted. On the
-- hub, land_rwx.status_fetch is 546 MB in total but 19 MB of rows (26k of
-- them; the rest is TOASTed JSON bodies) and was promoted as a big table. A
-- scan of the plain table never reads those bodies unless a query asks for
-- them, and there it answered count(*) in 5 ms against 61 ms from Parquet.
-- What a scan of the plain table costs is its main fork, so that is what the
-- size bounds (min_table_bytes, max_table_bytes, act_small_table_bytes) and
-- the evaluate/candidates/decisions table_bytes now measure.

DO $migration$
DECLARE
    definition text;
    needle text;
BEGIN
    definition := pg_get_functiondef('rvbbit.accel_autopilot_evaluate()'::regprocedure);
    IF position('pg_relation_size(c.oid, ''main'') AS table_bytes' IN definition) > 0 THEN
        RETURN;
    END IF;

    needle := 'pg_total_relation_size(c.oid) AS table_bytes';
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0498 could not find table_bytes in accel_autopilot_evaluate';
    END IF;
    definition := replace(definition, needle, 'pg_relation_size(c.oid, ''main'') AS table_bytes');

    needle := $needle$format('smaller than %s', $needle$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0498 could not find the size floor reason';
    END IF;
    definition := replace(definition, needle, $replacement$format('smaller than %s of table data', $replacement$);
    definition := replace(definition,
        $needle$format('smaller than %s, where the plain table is as fast', $needle$,
        $replacement$format('smaller than %s of table data, where the plain table is as fast', $replacement$);
    definition := replace(definition,
        $needle$format('larger than %s', $needle$,
        $replacement$format('larger than %s of table data', $replacement$);

    EXECUTE definition;
END
$migration$;

COMMENT ON COLUMN rvbbit.accel_autopilot_config.min_table_bytes IS
    'Smaller tables are not worth accelerating. Measured as the table''s own data (pg_relation_size, main fork): TOAST and indexes do not count.';
COMMENT ON COLUMN rvbbit.accel_autopilot_config.max_table_bytes IS
    'Larger tables are left to an operator. Measured as the table''s own data (pg_relation_size, main fork).';
