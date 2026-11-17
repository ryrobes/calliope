-- 0296: Business Value Graph proposals above the mechanical topology layer.
--
-- A value graph answers three different questions without conflating them:
--   * definition: what arithmetic/business rule defines a metric?
--   * contribution: which dimensions and objects contribute to its value?
--   * change: which contributors explain movement between two periods?
--
-- The canonical metric definition is ordinary read-only PostgreSQL returning
-- (occurred_at timestamptz, value numeric, dimensions jsonb).  Everything else
-- is a checked projection over that relation, so there is no parallel metric
-- expression language to keep in sync with SQL.

CREATE TABLE IF NOT EXISTS rvbbit.business_value_graph_proposals (
    graph_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_run_id uuid NOT NULL
        REFERENCES rvbbit.business_topology_workflow_runs(run_id) ON DELETE CASCADE,
    domain_key text NOT NULL,
    name text NOT NULL,
    summary text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'proposed',
    source_relations text[] NOT NULL,
    primary_metric_key text,
    graph jsonb NOT NULL,
    receipt jsonb NOT NULL,
    proposed_by text NOT NULL DEFAULT current_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reviewed_by text,
    reviewed_at timestamptz,
    review_reason text,
    CONSTRAINT business_value_graph_domain_check CHECK (
        domain_key ~ '^[A-Za-z_][A-Za-z0-9_$]{0,127}$'
    ),
    CONSTRAINT business_value_graph_name_check CHECK (
        length(btrim(name)) BETWEEN 1 AND 180
    ),
    CONSTRAINT business_value_graph_status_check CHECK (
        status IN ('proposed','needs_revision','rejected','superseded')
    ),
    CONSTRAINT business_value_graph_sources_check CHECK (
        cardinality(source_relations)>0
    ),
    CONSTRAINT business_value_graph_json_check CHECK (
        jsonb_typeof(graph)='object' AND jsonb_typeof(receipt)='object'
        AND graph->>'schema_version'='rvbbit.business-value.graph.v1'
        AND receipt->>'schema_version'='rvbbit.business-value.receipt.v1'
        AND jsonb_typeof(graph->'metrics')='array'
        AND jsonb_typeof(graph->'edges')='array'
        AND jsonb_typeof(receipt #> '{evaluation,metrics}')='array'
    ),
    UNIQUE (workflow_run_id,domain_key)
);

CREATE INDEX IF NOT EXISTS business_value_graph_proposals_inbox_idx
    ON rvbbit.business_value_graph_proposals(status,updated_at DESC);

COMMENT ON TABLE rvbbit.business_value_graph_proposals IS
    'Executable, reconciled shadow models of business outcomes, contributions, and change. They are metric/cube recommendations, not governed definitions.';

CREATE OR REPLACE FUNCTION rvbbit.business_value_stage_graph(
    p_workflow_run_id uuid,
    p_graph jsonb,
    p_receipt jsonb,
    p_proposed_by text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph_id uuid;
    v_domain_key text;
    v_primary_metric_key text;
    v_metric jsonb;
    v_metric_keys text[] := ARRAY[]::text[];
    v_metric_key text;
    v_sql_error text;
    v_edge jsonb;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.business_topology_workflow_runs
         WHERE run_id=p_workflow_run_id
    ) THEN
        RAISE EXCEPTION 'value graph workflow % was not found',p_workflow_run_id;
    END IF;
    IF p_graph IS NULL OR jsonb_typeof(p_graph)<>'object'
       OR p_graph->>'schema_version'<>'rvbbit.business-value.graph.v1'
       OR jsonb_typeof(p_graph->'metrics')<>'array'
       OR jsonb_typeof(p_graph->'edges')<>'array' THEN
        RAISE EXCEPTION 'value graph does not match the v1 graph contract';
    END IF;
    IF p_receipt IS NULL OR jsonb_typeof(p_receipt)<>'object'
       OR p_receipt->>'schema_version'<>'rvbbit.business-value.receipt.v1'
       OR jsonb_typeof(p_receipt #> '{evaluation,metrics}')<>'array' THEN
        RAISE EXCEPTION 'value graph receipt does not match the v1 receipt contract';
    END IF;
    v_domain_key := nullif(btrim(p_graph->>'domain_key'),'');
    IF v_domain_key IS NULL OR v_domain_key !~ '^[A-Za-z_][A-Za-z0-9_$]{0,127}$' THEN
        RAISE EXCEPTION 'value graph domain_key is invalid';
    END IF;
    IF coalesce(jsonb_array_length(p_graph->'metrics'),0)>4
       OR coalesce(jsonb_array_length(p_graph->'edges'),0)>12 THEN
        RAISE EXCEPTION 'value graph exceeds its metric or edge budget';
    END IF;

    FOR v_metric IN SELECT value FROM jsonb_array_elements(p_graph->'metrics')
    LOOP
        v_metric_key := nullif(btrim(v_metric->>'metric_key'),'');
        IF v_metric_key IS NULL OR v_metric_key !~ '^[a-z][a-z0-9_]{0,79}$'
           OR v_metric_key=ANY(v_metric_keys) THEN
            RAISE EXCEPTION 'value graph contains an invalid or duplicate metric_key';
        END IF;
        IF v_metric->>'metric_kind' NOT IN ('outcome','component','driver')
           OR v_metric->>'unit' NOT IN (
               'currency','count','quantity'
           ) OR jsonb_typeof(v_metric->'dimensions')<>'array'
           OR nullif(btrim(v_metric->>'formula'),'') IS NULL
           OR nullif(btrim(v_metric->>'event_sql'),'') IS NULL THEN
            RAISE EXCEPTION 'value graph metric % has an invalid contract',v_metric_key;
        END IF;
        v_sql_error := rvbbit._synth_validate(v_metric->>'event_sql');
        IF v_sql_error IS NOT NULL THEN
            RAISE EXCEPTION 'value graph metric % is not a read-only SELECT: %',
                v_metric_key,v_sql_error;
        END IF;
        v_metric_keys := array_append(v_metric_keys,v_metric_key);
    END LOOP;

    v_primary_metric_key := nullif(btrim(p_graph->>'primary_metric_key'),'');
    IF v_primary_metric_key IS NOT NULL
       AND NOT v_primary_metric_key=ANY(v_metric_keys) THEN
        RAISE EXCEPTION 'value graph primary_metric_key is not declared';
    END IF;
    IF jsonb_array_length(p_graph->'metrics')>0 AND (
        v_primary_metric_key IS NULL
        OR (SELECT count(*) FROM jsonb_array_elements(p_graph->'metrics') metric
             WHERE metric->>'metric_kind'='outcome')<>1
        OR NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_graph->'metrics') metric
             WHERE metric->>'metric_key'=v_primary_metric_key
               AND metric->>'metric_kind'='outcome'
        )
    ) THEN
        RAISE EXCEPTION 'value graph must have exactly one primary outcome metric';
    END IF;
    FOR v_edge IN SELECT value FROM jsonb_array_elements(p_graph->'edges')
    LOOP
        IF NOT (v_edge->>'parent_metric_key'=ANY(v_metric_keys))
           OR NOT (v_edge->>'child_metric_key'=ANY(v_metric_keys))
           OR v_edge->>'parent_metric_key'=v_edge->>'child_metric_key'
           OR v_edge->>'relationship' NOT IN (
               'adds','subtracts','multiplies','divides','drives','contributes_to'
           ) THEN
            RAISE EXCEPTION 'value graph contains an invalid metric edge';
        END IF;
    END LOOP;

    INSERT INTO rvbbit.business_value_graph_proposals (
        workflow_run_id,domain_key,name,summary,status,source_relations,
        primary_metric_key,graph,receipt,proposed_by
    ) VALUES (
        p_workflow_run_id,v_domain_key,btrim(p_graph->>'name'),
        coalesce(p_graph->>'summary',''),'proposed',
        ARRAY(SELECT jsonb_array_elements_text(p_graph->'source_relations')),
        v_primary_metric_key,p_graph,p_receipt,
        coalesce(nullif(btrim(p_proposed_by),''),current_user)
    )
    ON CONFLICT (workflow_run_id,domain_key) DO UPDATE SET
        name=EXCLUDED.name,
        summary=EXCLUDED.summary,
        status='proposed',
        source_relations=EXCLUDED.source_relations,
        primary_metric_key=EXCLUDED.primary_metric_key,
        graph=EXCLUDED.graph,
        receipt=EXCLUDED.receipt,
        proposed_by=EXCLUDED.proposed_by,
        updated_at=clock_timestamp(),
        reviewed_by=NULL,reviewed_at=NULL,review_reason=NULL
    RETURNING graph_id INTO v_graph_id;
    RETURN v_graph_id;
END
$fn$;

CREATE OR REPLACE VIEW rvbbit.business_value_graph_summary AS
SELECT proposal.graph_id,proposal.workflow_run_id,proposal.domain_key,
       proposal.name,proposal.summary,proposal.status,
       proposal.source_relations,proposal.primary_metric_key,
       jsonb_array_length(proposal.graph->'metrics')::integer AS metric_count,
       jsonb_array_length(proposal.graph->'edges')::integer AS edge_count,
       coalesce((proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0)
           AS validated_metric_count,
       coalesce((proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
           AS reconciled,
       coalesce((proposal.receipt->>'fallback')::boolean,false) AS fallback,
       proposal.graph,proposal.receipt,
       proposal.proposed_by,proposal.proposed_at,proposal.updated_at,
       proposal.reviewed_by,proposal.reviewed_at,proposal.review_reason
  FROM rvbbit.business_value_graph_proposals proposal;

CREATE OR REPLACE VIEW rvbbit.business_value_metric_catalog AS
SELECT proposal.graph_id,proposal.workflow_run_id,proposal.domain_key,
       proposal.name AS domain_name,proposal.status,
       metric->>'metric_key' AS metric_key,
       metric->>'name' AS metric_name,
       metric->>'description' AS description,
       metric->>'metric_kind' AS metric_kind,
       metric->>'unit' AS unit,
       metric->>'grain' AS grain,
       metric->>'time_semantics' AS time_semantics,
       metric->>'event_sql' AS event_sql,
       metric->'dimensions' AS dimensions,
       metric->>'primary_dimension_key' AS primary_dimension_key,
       (metric->>'confidence')::double precision AS confidence,
       evaluation.value AS evaluation,
       metric->>'metric_key'=proposal.primary_metric_key AS is_primary,
       metric->>'formula' AS formula
  FROM rvbbit.business_value_graph_proposals proposal
 CROSS JOIN LATERAL jsonb_array_elements(proposal.graph->'metrics') metric
 LEFT JOIN LATERAL (
     SELECT value
       FROM jsonb_array_elements(proposal.receipt #> '{evaluation,metrics}') value
      WHERE value->>'metric_key'=metric->>'metric_key'
      LIMIT 1
 ) evaluation ON true;

COMMENT ON VIEW rvbbit.business_value_metric_catalog IS
    'Agent-facing catalog of executable shadow metrics with grain, time semantics, dimensions, evaluation, and cube recommendations.';

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_series(
    p_graph_id uuid,
    p_metric_key text,
    p_bucket text DEFAULT 'month',
    p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL
) RETURNS TABLE(period_start timestamptz,value numeric)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_sql text;
    v_error text;
BEGIN
    IF p_bucket NOT IN ('day','week','month','quarter','year') THEN
        RAISE EXCEPTION 'unsupported value graph time bucket %',p_bucket;
    END IF;
    SELECT event_sql INTO v_sql
      FROM rvbbit.business_value_metric_catalog
     WHERE graph_id=p_graph_id AND metric_key=p_metric_key;
    IF v_sql IS NULL THEN
        RAISE EXCEPTION 'value graph metric %.% was not found',p_graph_id,p_metric_key;
    END IF;
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN
        RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error;
    END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s) '
        'SELECT date_trunc(%L,occurred_at::timestamptz),sum(value::numeric)::numeric '
        'FROM metric_events WHERE occurred_at IS NOT NULL AND value IS NOT NULL '
        'AND (%L::timestamptz IS NULL OR occurred_at::timestamptz >= %L::timestamptz) '
        'AND (%L::timestamptz IS NULL OR occurred_at::timestamptz < %L::timestamptz) '
        'GROUP BY 1 ORDER BY 1',
        v_sql,p_bucket,p_start,p_start,p_end,p_end
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_contributions(
    p_graph_id uuid,
    p_metric_key text,
    p_dimension text,
    p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL,
    p_limit integer DEFAULT 25
) RETURNS TABLE(dimension_value text,value numeric,share double precision)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_sql text;
    v_error text;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'contribution limit must be between 1 and 500';
    END IF;
    SELECT event_sql INTO v_sql
      FROM rvbbit.business_value_metric_catalog
     WHERE graph_id=p_graph_id AND metric_key=p_metric_key
       AND EXISTS (
           SELECT 1 FROM jsonb_array_elements(dimensions) dimension
            WHERE dimension->>'key'=p_dimension
       );
    IF v_sql IS NULL THEN
        RAISE EXCEPTION 'value graph metric or dimension was not found';
    END IF;
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN
        RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error;
    END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s), grouped AS ('
        ' SELECT coalesce(nullif(dimensions::jsonb->>%L,''''),''Unknown'') AS dimension_value,'
        '        sum(value::numeric)::numeric AS value'
        ' FROM metric_events WHERE value IS NOT NULL '
        ' AND (%L::timestamptz IS NULL OR occurred_at::timestamptz >= %L::timestamptz)'
        ' AND (%L::timestamptz IS NULL OR occurred_at::timestamptz < %L::timestamptz)'
        ' GROUP BY 1), totaled AS (SELECT sum(value) AS total FROM grouped)'
        ' SELECT grouped.dimension_value,grouped.value,'
        '        CASE WHEN totaled.total=0 THEN 0 ELSE grouped.value::double precision/totaled.total::double precision END'
        ' FROM grouped CROSS JOIN totaled ORDER BY abs(grouped.value) DESC,grouped.dimension_value LIMIT %s',
        v_sql,p_dimension,p_start,p_start,p_end,p_end,p_limit
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_change(
    p_graph_id uuid,
    p_metric_key text,
    p_dimension text,
    p_prior_start timestamptz,
    p_prior_end timestamptz,
    p_current_start timestamptz,
    p_current_end timestamptz,
    p_limit integer DEFAULT 25
) RETURNS TABLE(
    dimension_value text,
    prior_value numeric,
    current_value numeric,
    change numeric,
    share_of_change double precision
)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_sql text;
    v_error text;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'change limit must be between 1 and 500';
    END IF;
    IF p_prior_start>=p_prior_end OR p_current_start>=p_current_end THEN
        RAISE EXCEPTION 'value graph change periods must be non-empty';
    END IF;
    SELECT event_sql INTO v_sql
      FROM rvbbit.business_value_metric_catalog
     WHERE graph_id=p_graph_id AND metric_key=p_metric_key
       AND EXISTS (
           SELECT 1 FROM jsonb_array_elements(dimensions) dimension
            WHERE dimension->>'key'=p_dimension
       );
    IF v_sql IS NULL THEN
        RAISE EXCEPTION 'value graph metric or dimension was not found';
    END IF;
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN
        RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error;
    END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s), grouped AS ('
        ' SELECT coalesce(nullif(dimensions::jsonb->>%L,''''),''Unknown'') AS dimension_value,'
        ' coalesce(sum(value::numeric) FILTER (WHERE occurred_at::timestamptz >= %L::timestamptz AND occurred_at::timestamptz < %L::timestamptz),0)::numeric AS prior_value,'
        ' coalesce(sum(value::numeric) FILTER (WHERE occurred_at::timestamptz >= %L::timestamptz AND occurred_at::timestamptz < %L::timestamptz),0)::numeric AS current_value'
        ' FROM metric_events WHERE value IS NOT NULL GROUP BY 1), changed AS ('
        ' SELECT *,current_value-prior_value AS change FROM grouped), totals AS ('
        ' SELECT sum(change) AS total_change FROM changed)'
        ' SELECT changed.dimension_value,changed.prior_value,changed.current_value,changed.change,'
        ' CASE WHEN totals.total_change=0 THEN 0 ELSE changed.change::double precision/totals.total_change::double precision END'
        ' FROM changed CROSS JOIN totals ORDER BY abs(changed.change) DESC,changed.dimension_value LIMIT %s',
        v_sql,p_dimension,p_prior_start,p_prior_end,p_current_start,p_current_end,p_limit
    );
END
$fn$;
