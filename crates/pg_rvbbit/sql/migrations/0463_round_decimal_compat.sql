-- PostgreSQL has round(float8) and round(numeric, int4), but not
-- round(float8, int4). Generated analytical SQL repeatedly hits that gap.
-- Delegate decimal rounding to the native numeric implementation; do not
-- replace pg_catalog functions or change implicit casts globally.
--
-- Adding only the float8 overload is NOT safe: float8 is a preferred numeric
-- type, so bigint and unknown string literals can start resolving through it
-- and lose precision. Exact integer overloads and a text overload preserve
-- those values. The latter also accepts explicitly typed numeric strings;
-- invalid strings still fail the native numeric cast. A separate real
-- overload avoids widening its binary approximation before conversion.
-- One-argument round and the existing numeric overload remain untouched.
DO $migration$
DECLARE
    input_type text;
    signature text;
    existing oid;
    marker constant text := 'RVBBIT decimal-round compatibility v1';
BEGIN
    FOREACH input_type IN ARRAY ARRAY[
        'double precision', 'real', 'smallint', 'integer', 'bigint', 'text'
    ] LOOP
        signature := format('public.round(%s, integer)', input_type);
        existing := to_regprocedure(signature);
        -- Do not silently replace a client's pre-existing function.
        IF existing IS NOT NULL
           AND obj_description(existing, 'pg_proc') IS DISTINCT FROM marker THEN
            RAISE EXCEPTION 'Cannot install decimal-round compatibility: % already exists', signature;
        END IF;
        EXECUTE format(
            'CREATE OR REPLACE FUNCTION public.round(%s, integer)
             RETURNS numeric LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
             AS %L',
            input_type, 'SELECT pg_catalog.round($1::pg_catalog.numeric, $2)'
        );
        EXECUTE format('COMMENT ON FUNCTION %s IS %L', signature, marker);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO PUBLIC', signature);
    END LOOP;
END
$migration$;
