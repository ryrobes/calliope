-- Current metric values and KPI checks do not require retained source history.
-- Remove the old AS OF now() workaround for nested snapshot scans: both scan
-- engines now enforce the latest snapshot floor directly. Explicit historical
-- requests still use AS OF and still reject current-only sources.

CREATE OR REPLACE FUNCTION rvbbit.metric(
    p_name       text,
    p_params     jsonb DEFAULT '{}'::jsonb,
    p_def_as_of  timestamptz DEFAULT now(),
    p_data_as_of timestamptz DEFAULT NULL
) RETURNS SETOF jsonb
LANGUAGE plpgsql AS $fn$
DECLARE
    v_sql   text;
    v_saved text;
BEGIN
    v_sql := rvbbit.metric_sql(p_name, p_params, p_def_as_of);
    v_sql := rvbbit._resolve_relative_refs(v_sql, v_sql, p_params, p_def_as_of, p_data_as_of);

    -- NULL data-time means an ordinary current read, not AS OF now().
    -- Scan-level latest predicates already enforce the snapshot floor, including
    -- nested metric queries. An implicit historical read incorrectly rejects
    -- current-only sources. Only an explicit data-time enables time travel;
    -- restore the caller's timestamp after either success or failure.
    v_saved := current_setting('rvbbit.as_of_timestamp', true);
    PERFORM set_config('rvbbit.as_of_timestamp',
                       coalesce(p_data_as_of::text, ''), true);

    BEGIN
        RETURN QUERY EXECUTE 'SELECT to_jsonb(t) FROM (' || v_sql || ') AS t';
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);
        RAISE EXCEPTION 'rvbbit.metric("%"): % | SQL: %', p_name, SQLERRM, v_sql;
    END;

    PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);
    RETURN;
END;
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._run_check(
    p_metric_sql text,
    p_check_sql  text,
    p_data_as_of timestamptz
) RETURNS jsonb
LANGUAGE plpgsql AS $fn$
DECLARE
    v_full    text;
    v_verdict jsonb;
    v_saved   text;
BEGIN
    IF p_check_sql IS NULL OR btrim(p_check_sql) = '' THEN
        RETURN NULL;
    END IF;

    v_full := 'WITH metric AS (' || p_metric_sql || E'\n) ' || p_check_sql;

    -- Match metric(): current reads use the scan's latest-snapshot filter;
    -- only an explicit data-time requests historical source rows.
    v_saved := current_setting('rvbbit.as_of_timestamp', true);
    PERFORM set_config('rvbbit.as_of_timestamp', coalesce(p_data_as_of::text, ''), true);

    BEGIN
        EXECUTE 'SELECT to_jsonb(t) FROM (' || v_full || ') t' INTO STRICT v_verdict;
    EXCEPTION
        WHEN TOO_MANY_ROWS THEN
            PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);
            RAISE EXCEPTION 'rvbbit check returned more than one row; reduce the metric CTE to a single verdict row. | SQL: %', v_full;
        WHEN NO_DATA_FOUND THEN
            PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);
            RAISE EXCEPTION 'rvbbit check returned no rows. | SQL: %', v_full;
        WHEN OTHERS THEN
            PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);
            RAISE EXCEPTION 'rvbbit check failed: % | SQL: %', SQLERRM, v_full;
    END;

    PERFORM set_config('rvbbit.as_of_timestamp', coalesce(v_saved, ''), true);

    IF v_verdict IS NULL OR NOT (v_verdict ? 'ok') THEN
        RAISE EXCEPTION 'rvbbit check must yield an "ok" boolean column (got: %)',
            coalesce(v_verdict::text, 'no row');
    END IF;

    -- A NULL ok is never "pass"; attach a default pass/fail status if absent.
    IF NOT (v_verdict ? 'status') THEN
        v_verdict := v_verdict || jsonb_build_object(
            'status', CASE WHEN (v_verdict->>'ok')::boolean IS TRUE THEN 'pass' ELSE 'fail' END);
    END IF;

    RETURN v_verdict;
END;
$fn$;

-- Keep bulk refreshes on the same current-read contract.
CREATE OR REPLACE FUNCTION rvbbit.materialize_all_metrics(
    p_category    text        DEFAULT NULL,   -- NULL = every category (incl. uncategorized)
    p_subcategory text        DEFAULT NULL,
    p_def_as_of   timestamptz DEFAULT NULL,   -- NULL = now() (definition-time axis)
    p_data_as_of  timestamptz DEFAULT NULL,   -- NULL = ordinary current reads
    p_trigger     text        DEFAULT 'bulk'
) RETURNS TABLE(metric_name text, observation_id bigint, status text, error text)
LANGUAGE plpgsql AS $fn$
DECLARE
    rec   record;
    -- Resolve definition-time once. Preserve NULL data-time so ordinary bulk
    -- refreshes do not turn into historical reads of current-only sources.
    v_def  timestamptz := coalesce(p_def_as_of, now());
    v_data timestamptz := p_data_as_of;
    v_obs  bigint;
BEGIN
    FOR rec IN
        SELECT name
          FROM rvbbit.metric_catalog
         WHERE (p_category    IS NULL OR category    = p_category)
           AND (p_subcategory IS NULL OR subcategory = p_subcategory)
         ORDER BY name
    LOOP
        BEGIN
            v_obs := rvbbit.materialize_metric(rec.name, '{}'::jsonb, v_def, v_data, NULL, p_trigger);
            metric_name := rec.name; observation_id := v_obs; status := 'ok'; error := NULL;
        EXCEPTION WHEN others THEN
            -- isolate a failing metric to its own savepoint; report and continue
            metric_name := rec.name; observation_id := NULL; status := 'error'; error := left(SQLERRM, 500);
        END;
        RETURN NEXT;
    END LOOP;
END $fn$;
