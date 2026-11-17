-- 0297: Promote a reviewed Business Value Graph into native RVBBIT objects.
--
-- Promotion is deliberately narrow and atomic:
--   1. the human-reviewed proposal remains immutable evidence;
--   2. define_cube materializes a compact period x metric x dimensions table
--      using the native RVBBIT table AM;
--   3. exact totals/event counts are reconciled against the proposal receipt;
--   4. define_metric publishes each validated measure over that cube;
--   5. a durable receipt links every governed object back to its source SQL.

ALTER TABLE rvbbit.business_value_graph_proposals
    DROP CONSTRAINT IF EXISTS business_value_graph_status_check;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD CONSTRAINT business_value_graph_status_check CHECK (
        status IN ('proposed','approved','promoted','needs_revision','rejected','superseded')
    );

CREATE TABLE IF NOT EXISTS rvbbit.business_value_graph_promotions (
    promotion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id uuid NOT NULL UNIQUE
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE RESTRICT,
    cube_name text NOT NULL UNIQUE,
    cube_version integer NOT NULL,
    materialization_grain text NOT NULL,
    metric_versions jsonb NOT NULL,
    cube_sql text NOT NULL,
    proposal_graph jsonb NOT NULL,
    proposal_receipt jsonb NOT NULL,
    promotion_receipt jsonb NOT NULL,
    promoted_by text NOT NULL DEFAULT current_user,
    promoted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_value_promotion_cube_name_check CHECK (
        cube_name ~ '^[a-z_][a-z0-9_]{0,62}$'
    ),
    CONSTRAINT business_value_promotion_grain_check CHECK (
        materialization_grain IN ('day','week','month','quarter','year')
    ),
    CONSTRAINT business_value_promotion_metric_versions_check CHECK (
        jsonb_typeof(metric_versions)='object' AND metric_versions<>'{}'::jsonb
    ),
    CONSTRAINT business_value_promotion_receipt_check CHECK (
        promotion_receipt->>'schema_version'='rvbbit.business-value.promotion-receipt.v1'
    )
);

CREATE INDEX IF NOT EXISTS business_value_graph_promotions_recent_idx
    ON rvbbit.business_value_graph_promotions(promoted_at DESC);

COMMENT ON TABLE rvbbit.business_value_graph_promotions IS
    'Immutable bridge from a reviewed Value Graph proposal/receipt to its native RVBBIT cube and governed metric versions.';

CREATE OR REPLACE FUNCTION rvbbit._business_value_preserve_promoted_graph()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF OLD.status='promoted' AND EXISTS (
        SELECT 1 FROM rvbbit.business_value_graph_promotions WHERE graph_id=OLD.graph_id
    ) THEN
        NEW.status := 'promoted';
        NEW.domain_key := OLD.domain_key;
        NEW.name := OLD.name;
        NEW.summary := OLD.summary;
        NEW.source_relations := OLD.source_relations;
        NEW.primary_metric_key := OLD.primary_metric_key;
        NEW.graph := OLD.graph;
        NEW.receipt := OLD.receipt;
        NEW.proposed_by := OLD.proposed_by;
        NEW.proposed_at := OLD.proposed_at;
        NEW.reviewed_by := OLD.reviewed_by;
        NEW.reviewed_at := OLD.reviewed_at;
        NEW.review_reason := OLD.review_reason;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS business_value_preserve_promoted_graph
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_value_preserve_promoted_graph
BEFORE UPDATE ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_value_preserve_promoted_graph();

CREATE OR REPLACE FUNCTION rvbbit._business_value_identifier(
    p_value text,
    p_suffix text DEFAULT NULL,
    p_max_length integer DEFAULT 63
) RETURNS text
LANGUAGE plpgsql IMMUTABLE
AS $fn$
DECLARE
    v_name text;
    v_suffix text := lower(regexp_replace(coalesce(p_suffix,''),'[^a-z0-9]+','_','g'));
    v_limit integer;
BEGIN
    v_name := lower(regexp_replace(coalesce(p_value,''),'[^a-z0-9]+','_','g'));
    v_name := regexp_replace(v_name,'^_+|_+$','','g');
    IF v_name='' THEN v_name := 'value'; END IF;
    IF v_name !~ '^[a-z_]' THEN v_name := 'v_'||v_name; END IF;
    v_suffix := regexp_replace(v_suffix,'^_+|_+$','','g');
    v_limit := greatest(8,least(coalesce(p_max_length,63),63));
    IF v_suffix<>'' THEN
        v_name := left(v_name,greatest(1,v_limit-length(v_suffix)-1))||'_'||v_suffix;
    ELSE
        v_name := left(v_name,v_limit);
    END IF;
    RETURN v_name;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_review_graph(
    p_graph_id uuid,
    p_decision text,
    p_reason text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_decision text := lower(btrim(coalesce(p_decision,'')));
    v_proposal rvbbit.business_value_graph_proposals%ROWTYPE;
BEGIN
    IF v_decision NOT IN ('approved','needs_revision','rejected') THEN
        RAISE EXCEPTION 'value graph review decision must be approved, needs_revision, or rejected';
    END IF;
    SELECT * INTO v_proposal
      FROM rvbbit.business_value_graph_proposals
     WHERE graph_id=p_graph_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'value graph % was not found',p_graph_id;
    END IF;
    IF v_proposal.status='promoted' THEN
        RAISE EXCEPTION 'value graph % is already promoted',p_graph_id;
    END IF;
    IF v_decision='approved' AND (
        coalesce((v_proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0)=0
        OR NOT coalesce((v_proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
        OR coalesce((v_proposal.receipt->>'fallback')::boolean,false)
    ) THEN
        RAISE EXCEPTION 'only non-fallback value graphs with reconciled executable metrics can be approved';
    END IF;
    UPDATE rvbbit.business_value_graph_proposals
       SET status=v_decision,
           reviewed_by=session_user,
           reviewed_at=clock_timestamp(),
           review_reason=nullif(btrim(coalesce(p_reason,'')),''),
           updated_at=clock_timestamp()
     WHERE graph_id=p_graph_id;
    RETURN jsonb_build_object(
        'graph_id',p_graph_id,
        'status',v_decision,
        'reviewed_by',session_user,
        'reviewed_at',clock_timestamp()
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_value_cube_sql(
    p_graph_id uuid,
    p_grain text
) RETURNS text
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_metric record;
    v_parts text[] := ARRAY[]::text[];
    v_error text;
BEGIN
    IF p_grain NOT IN ('day','week','month','quarter','year') THEN
        RAISE EXCEPTION 'unsupported value cube grain %',p_grain;
    END IF;
    FOR v_metric IN
        SELECT metric_key,event_sql
          FROM rvbbit.business_value_metric_catalog
         WHERE graph_id=p_graph_id
         ORDER BY CASE WHEN is_primary THEN 0 ELSE 1 END,metric_key
    LOOP
        v_error := rvbbit._synth_validate(v_metric.event_sql);
        IF v_error IS NOT NULL THEN
            RAISE EXCEPTION 'value graph metric % is no longer a safe SELECT: %',
                v_metric.metric_key,v_error;
        END IF;
        v_parts := array_append(v_parts,format(
            'SELECT %L::text AS metric_key,'
            ' date_trunc(%L,occurred_at::timestamptz)::timestamptz AS period_start,'
            ' coalesce(dimensions::jsonb,''{}''::jsonb) AS dimensions,'
            ' sum(value::numeric)::numeric AS value,'
            ' count(*)::bigint AS source_event_rows'
            ' FROM (%s) AS value_events'
            ' WHERE occurred_at IS NOT NULL AND value IS NOT NULL'
            ' GROUP BY 2,3',
            v_metric.metric_key,p_grain,v_metric.event_sql
        ));
    END LOOP;
    IF cardinality(v_parts)=0 THEN
        RAISE EXCEPTION 'value graph % has no executable metrics',p_graph_id;
    END IF;
    RETURN array_to_string(v_parts,E'\nUNION ALL\n');
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_promotion_plan(
    p_graph_id uuid,
    p_cube_name text DEFAULT NULL,
    p_grain text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_proposal rvbbit.business_value_graph_proposals%ROWTYPE;
    v_cube_name text;
    v_grain text;
    v_metrics jsonb;
    v_original_rows bigint;
BEGIN
    SELECT * INTO v_proposal
      FROM rvbbit.business_value_graph_proposals
     WHERE graph_id=p_graph_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'value graph % was not found',p_graph_id; END IF;
    IF jsonb_array_length(v_proposal.graph->'metrics')=0 THEN
        RAISE EXCEPTION 'value graph % has no promotable metrics',p_graph_id;
    END IF;
    v_cube_name := coalesce(
        nullif(btrim(p_cube_name),''),
        rvbbit._business_value_identifier(v_proposal.domain_key,'value',63)
    );
    IF v_cube_name !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
        RAISE EXCEPTION 'value cube name must be a lowercase PostgreSQL identifier';
    END IF;
    v_grain := coalesce(nullif(lower(btrim(p_grain)),''),(
        SELECT CASE evaluation->>'bucket'
                 WHEN 'day' THEN 'day'
                 WHEN 'week' THEN 'week'
                 ELSE 'month'
               END
          FROM jsonb_array_elements(v_proposal.receipt #> '{evaluation,metrics}') evaluation
         WHERE evaluation->>'metric_key'=v_proposal.primary_metric_key
         LIMIT 1
    ),'month');
    IF v_grain NOT IN ('day','week','month','quarter','year') THEN
        RAISE EXCEPTION 'unsupported value cube grain %',v_grain;
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'metric_key',catalog.metric_key,
               'metric_name',rvbbit._business_value_identifier(
                   v_proposal.domain_key,catalog.metric_key,63
               ),
               'name',catalog.metric_name,
               'kind',catalog.metric_kind,
               'unit',catalog.unit,
               'grain',catalog.grain,
               'formula',catalog.formula,
               'dimensions',catalog.dimensions,
               'event_rows',coalesce((catalog.evaluation->>'event_rows')::bigint,0)
           ) ORDER BY CASE WHEN catalog.is_primary THEN 0 ELSE 1 END,catalog.metric_key),'[]'::jsonb),
           coalesce(sum((catalog.evaluation->>'event_rows')::bigint),0)
      INTO v_metrics,v_original_rows
      FROM rvbbit.business_value_metric_catalog catalog
     WHERE catalog.graph_id=p_graph_id;
    RETURN jsonb_build_object(
        'schema_version','rvbbit.business-value.promotion-plan.v1',
        'graph_id',p_graph_id,
        'graph_status',v_proposal.status,
        'cube_name',v_cube_name,
        'cube_relation','cubes.'||v_cube_name,
        'materialization_grain',v_grain,
        'grain','one row per '||v_grain||', metric, and complete business-dimension combination',
        'metrics',v_metrics,
        'original_event_rows',v_original_rows,
        'source_relations',to_jsonb(v_proposal.source_relations),
        'cube_sql',rvbbit._business_value_cube_sql(p_graph_id,v_grain),
        'reconciled',coalesce((v_proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false),
        'fallback',coalesce((v_proposal.receipt->>'fallback')::boolean,false)
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_value_reconcile_cube(
    p_graph_id uuid,
    p_cube_name text
) RETURNS jsonb
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_metric record;
    v_actual_value numeric;
    v_actual_rows bigint;
    v_expected_value numeric;
    v_expected_rows bigint;
    v_tolerance numeric;
    v_passed boolean;
    v_results jsonb := '[]'::jsonb;
BEGIN
    FOR v_metric IN
        SELECT metric_key,evaluation
          FROM rvbbit.business_value_metric_catalog
         WHERE graph_id=p_graph_id
         ORDER BY CASE WHEN is_primary THEN 0 ELSE 1 END,metric_key
    LOOP
        EXECUTE format(
            'SELECT coalesce(sum(value),0)::numeric,coalesce(sum(source_event_rows),0)::bigint'
            ' FROM cubes.%I WHERE metric_key=%L',
            p_cube_name,v_metric.metric_key
        ) INTO v_actual_value,v_actual_rows;
        v_expected_value := coalesce((v_metric.evaluation->>'total_value')::numeric,0);
        v_expected_rows := coalesce((v_metric.evaluation->>'event_rows')::bigint,0);
        v_tolerance := greatest(abs(v_expected_value),1)*0.00000001;
        v_passed := abs(v_actual_value-v_expected_value)<=v_tolerance
                    AND v_actual_rows=v_expected_rows;
        v_results := v_results||jsonb_build_array(jsonb_build_object(
            'metric_key',v_metric.metric_key,
            'expected_value',v_expected_value,
            'actual_value',v_actual_value,
            'value_difference',v_actual_value-v_expected_value,
            'expected_event_rows',v_expected_rows,
            'actual_event_rows',v_actual_rows,
            'passed',v_passed
        ));
    END LOOP;
    RETURN v_results;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_promote_graph(
    p_graph_id uuid,
    p_cube_name text DEFAULT NULL,
    p_grain text DEFAULT NULL,
    p_review_reason text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_proposal rvbbit.business_value_graph_proposals%ROWTYPE;
    v_existing rvbbit.business_value_graph_promotions%ROWTYPE;
    v_plan jsonb;
    v_cube_name text;
    v_grain text;
    v_cube_sql text;
    v_cube_version integer;
    v_metric record;
    v_metric_name text;
    v_metric_sql text;
    v_metric_version integer;
    v_metric_versions jsonb := '{}'::jsonb;
    v_reconciliation jsonb;
    v_aggregate_rows bigint;
    v_original_rows bigint;
    v_promotion_id uuid;
    v_receipt jsonb;
    v_capability_crawl boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business-value:'||p_graph_id::text,0));
    SELECT * INTO v_existing
      FROM rvbbit.business_value_graph_promotions
     WHERE graph_id=p_graph_id;
    IF FOUND THEN RETURN v_existing.promotion_receipt; END IF;

    SELECT * INTO v_proposal
      FROM rvbbit.business_value_graph_proposals
     WHERE graph_id=p_graph_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'value graph % was not found',p_graph_id; END IF;
    IF v_proposal.status NOT IN ('proposed','approved') THEN
        RAISE EXCEPTION 'value graph % cannot be promoted from status %',p_graph_id,v_proposal.status;
    END IF;
    IF coalesce((v_proposal.receipt->>'fallback')::boolean,false)
       OR NOT coalesce((v_proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
       OR coalesce((v_proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0)=0 THEN
        RAISE EXCEPTION 'value graph % is not a reconciled, non-fallback executable proposal',p_graph_id;
    END IF;

    v_plan := rvbbit.business_value_promotion_plan(p_graph_id,p_cube_name,p_grain);
    v_cube_name := v_plan->>'cube_name';
    v_grain := v_plan->>'materialization_grain';
    v_cube_sql := v_plan->>'cube_sql';
    v_original_rows := (v_plan->>'original_event_rows')::bigint;

    IF EXISTS (
        SELECT 1 FROM rvbbit.cube_catalog
         WHERE name=v_cube_name
           AND coalesce(labels->>'business_value_graph_id','')<>p_graph_id::text
    ) OR (to_regclass('cubes.'||quote_ident(v_cube_name)) IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM rvbbit.cube_catalog WHERE name=v_cube_name
    )) THEN
        RAISE EXCEPTION 'cube name % is already owned by another object',v_cube_name;
    END IF;

    PERFORM set_config('statement_timeout','0',true);
    v_cube_version := rvbbit.define_cube(
        v_cube_name,
        v_cube_sql,
        'one row per '||v_grain||', metric, and complete business-dimension combination',
        'Compact value-system aggregate promoted from '||v_proposal.name||'. Source SQL and evaluation receipt are retained in rvbbit.business_value_graph_promotions.',
        session_user,
        NULL,
        'Business Value',
        jsonb_build_object(
            'business_value_graph_id',p_graph_id,
            'business_value_domain_key',v_proposal.domain_key,
            'materialization_grain',v_grain,
            'source_relations',to_jsonb(v_proposal.source_relations),
            'proposal_receipt_retained',true
        )
    );
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS %I ON cubes.%I(metric_key,period_start)',
        rvbbit._business_value_identifier(v_cube_name,'metric_period_idx',63),v_cube_name
    );

    v_reconciliation := rvbbit._business_value_reconcile_cube(p_graph_id,v_cube_name);
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_reconciliation) item WHERE (item->>'passed')::boolean IS NOT TRUE) THEN
        RAISE EXCEPTION 'value cube reconciliation failed: %',v_reconciliation;
    END IF;
    EXECUTE format('SELECT count(*)::bigint FROM cubes.%I',v_cube_name) INTO v_aggregate_rows;

    FOR v_metric IN
        SELECT *
          FROM rvbbit.business_value_metric_catalog
         WHERE graph_id=p_graph_id
         ORDER BY CASE WHEN is_primary THEN 0 ELSE 1 END,metric_key
    LOOP
        v_metric_name := rvbbit._business_value_identifier(
            v_proposal.domain_key,v_metric.metric_key,63
        );
        IF EXISTS (
            SELECT 1 FROM rvbbit.metric_catalog
             WHERE name=v_metric_name
               AND coalesce(labels->>'business_value_graph_id','')<>p_graph_id::text
        ) THEN
            RAISE EXCEPTION 'metric name % is already owned by another definition',v_metric_name;
        END IF;
        v_metric_sql := format(
            'SELECT coalesce(sum(value),0)::numeric AS value FROM cubes.%I'
            ' WHERE metric_key=%L'
            ' AND ({start}::timestamptz IS NULL OR period_start >= {start}::timestamptz)'
            ' AND ({end}::timestamptz IS NULL OR period_start < {end}::timestamptz)',
            v_cube_name,v_metric.metric_key
        );
        v_metric_version := rvbbit.define_metric(
            v_metric_name,
            v_metric_sql,
            '{"start":null,"end":null}'::jsonb,
            v_metric.grain,
            v_metric.description,
            session_user,
            jsonb_build_object(
                'cube_source',v_cube_name,
                'metric_value_column','value',
                'metric_kind','scalar',
                'business_value_graph_id',p_graph_id,
                'business_value_metric_key',v_metric.metric_key,
                'business_value_kind',v_metric.metric_kind,
                'unit',v_metric.unit,
                'formula',v_metric.formula,
                'dimensions',v_metric.dimensions,
                'source_event_sql',v_metric.event_sql,
                'proposal_receipt_retained',true
            ),
            NULL
        );
        v_metric_versions := v_metric_versions||jsonb_build_object(v_metric_name,v_metric_version);
    END LOOP;

    BEGIN
        PERFORM rvbbit.capability_crawl();
        v_capability_crawl := true;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'value promotion capability crawl deferred: %',SQLERRM;
    END;

    v_receipt := jsonb_build_object(
        'schema_version','rvbbit.business-value.promotion-receipt.v1',
        'graph_id',p_graph_id,
        'domain_key',v_proposal.domain_key,
        'cube_name',v_cube_name,
        'cube_relation','cubes.'||v_cube_name,
        'cube_version',v_cube_version,
        'materialization_grain',v_grain,
        'aggregate_rows',v_aggregate_rows,
        'original_event_rows',v_original_rows,
        'row_reduction_ratio',CASE WHEN v_original_rows=0 THEN NULL ELSE v_aggregate_rows::double precision/v_original_rows::double precision END,
        'metric_versions',v_metric_versions,
        'reconciliation',v_reconciliation,
        'proposal_graph_md5',md5(v_proposal.graph::text),
        'proposal_receipt_md5',md5(v_proposal.receipt::text),
        'source_relations',to_jsonb(v_proposal.source_relations),
        'capability_crawl_completed',v_capability_crawl,
        'promoted_by',session_user,
        'promoted_at',clock_timestamp()
    );

    INSERT INTO rvbbit.business_value_graph_promotions (
        graph_id,cube_name,cube_version,materialization_grain,metric_versions,
        cube_sql,proposal_graph,proposal_receipt,promotion_receipt,promoted_by
    ) VALUES (
        p_graph_id,v_cube_name,v_cube_version,v_grain,v_metric_versions,
        v_cube_sql,v_proposal.graph,v_proposal.receipt,v_receipt,session_user
    ) RETURNING promotion_id INTO v_promotion_id;
    UPDATE rvbbit.business_value_graph_proposals
       SET status='promoted',
           reviewed_by=session_user,
           reviewed_at=coalesce(reviewed_at,clock_timestamp()),
           review_reason=coalesce(nullif(btrim(coalesce(p_review_reason,'')),''),review_reason),
           updated_at=clock_timestamp()
     WHERE graph_id=p_graph_id;
    RETURN v_receipt||jsonb_build_object('promotion_id',v_promotion_id);
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_refresh_promotion(
    p_graph_id uuid
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_promotion rvbbit.business_value_graph_promotions%ROWTYPE;
    v_rows bigint;
    v_event_rows bigint;
    v_refresh jsonb;
BEGIN
    SELECT * INTO v_promotion
      FROM rvbbit.business_value_graph_promotions
     WHERE graph_id=p_graph_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'value graph % has not been promoted',p_graph_id; END IF;
    PERFORM set_config('statement_timeout','0',true);
    v_rows := rvbbit.refresh_cube(v_promotion.cube_name);
    EXECUTE format(
        'SELECT coalesce(sum(source_event_rows),0)::bigint FROM cubes.%I',
        v_promotion.cube_name
    ) INTO v_event_rows;
    v_refresh := jsonb_build_object(
        'refreshed_at',clock_timestamp(),
        'aggregate_rows',v_rows,
        'source_event_rows',v_event_rows,
        'refreshed_by',session_user
    );
    UPDATE rvbbit.business_value_graph_promotions
       SET last_refreshed_at=clock_timestamp(),
           promotion_receipt=jsonb_set(
               promotion_receipt,'{latest_refresh}',v_refresh,true
           )
     WHERE promotion_id=v_promotion.promotion_id;
    RETURN v_refresh;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_runtime_sql(
    p_graph_id uuid,
    p_metric_key text
) RETURNS text
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_sql text;
    v_cube_name text;
BEGIN
    SELECT promotion.cube_name INTO v_cube_name
      FROM rvbbit.business_value_graph_promotions promotion
     WHERE promotion.graph_id=p_graph_id;
    IF v_cube_name IS NOT NULL THEN
        RETURN format(
            'SELECT period_start AS occurred_at,value,dimensions FROM cubes.%I WHERE metric_key=%L',
            v_cube_name,p_metric_key
        );
    END IF;
    SELECT event_sql INTO v_sql
      FROM rvbbit.business_value_metric_catalog
     WHERE graph_id=p_graph_id AND metric_key=p_metric_key;
    RETURN v_sql;
END
$fn$;

-- Once promoted, all Value Graph exploration transparently reads the compact
-- RVBBIT cube. The original event SQL remains available in the immutable graph.
CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_series(
    p_graph_id uuid,
    p_metric_key text,
    p_bucket text DEFAULT 'month',
    p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL
) RETURNS TABLE(period_start timestamptz,value numeric)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE v_sql text; v_error text; v_materialization_grain text;
BEGIN
    IF p_bucket NOT IN ('day','week','month','quarter','year') THEN
        RAISE EXCEPTION 'unsupported value graph time bucket %',p_bucket;
    END IF;
    v_sql := rvbbit.business_value_metric_runtime_sql(p_graph_id,p_metric_key);
    IF v_sql IS NULL THEN RAISE EXCEPTION 'value graph metric %.% was not found',p_graph_id,p_metric_key; END IF;
    SELECT materialization_grain INTO v_materialization_grain
      FROM rvbbit.business_value_graph_promotions WHERE graph_id=p_graph_id;
    IF v_materialization_grain IS NOT NULL AND (CASE p_bucket
        WHEN 'day' THEN 1 WHEN 'week' THEN 2 WHEN 'month' THEN 3
        WHEN 'quarter' THEN 4 ELSE 5 END) < (CASE v_materialization_grain
        WHEN 'day' THEN 1 WHEN 'week' THEN 2 WHEN 'month' THEN 3
        WHEN 'quarter' THEN 4 ELSE 5 END) THEN
        RAISE EXCEPTION 'value graph % is materialized at % grain; % would imply unavailable detail',
            p_graph_id,v_materialization_grain,p_bucket;
    END IF;
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error; END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s) '
        'SELECT date_trunc(%L,occurred_at::timestamptz),sum(value::numeric)::numeric '
        'FROM metric_events WHERE occurred_at IS NOT NULL AND value IS NOT NULL '
        'AND (%L::timestamptz IS NULL OR occurred_at::timestamptz >= %L::timestamptz) '
        'AND (%L::timestamptz IS NULL OR occurred_at::timestamptz < %L::timestamptz) '
        'GROUP BY 1 ORDER BY 1',v_sql,p_bucket,p_start,p_start,p_end,p_end
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_contributions(
    p_graph_id uuid,p_metric_key text,p_dimension text,
    p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,p_limit integer DEFAULT 25
) RETURNS TABLE(dimension_value text,value numeric,share double precision)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE v_sql text; v_error text;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'contribution limit must be between 1 and 500'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.business_value_metric_catalog catalog
         WHERE catalog.graph_id=p_graph_id AND catalog.metric_key=p_metric_key
           AND EXISTS (SELECT 1 FROM jsonb_array_elements(catalog.dimensions) d WHERE d->>'key'=p_dimension)
    ) THEN RAISE EXCEPTION 'value graph metric or dimension was not found'; END IF;
    v_sql := rvbbit.business_value_metric_runtime_sql(p_graph_id,p_metric_key);
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error; END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s), grouped AS ('
        ' SELECT coalesce(nullif(dimensions::jsonb->>%L,''''),''Unknown'') AS dimension_value,sum(value::numeric)::numeric AS value'
        ' FROM metric_events WHERE value IS NOT NULL'
        ' AND (%L::timestamptz IS NULL OR occurred_at::timestamptz >= %L::timestamptz)'
        ' AND (%L::timestamptz IS NULL OR occurred_at::timestamptz < %L::timestamptz) GROUP BY 1),'
        ' totaled AS (SELECT sum(value) AS total FROM grouped)'
        ' SELECT grouped.dimension_value,grouped.value,CASE WHEN totaled.total=0 THEN 0 ELSE grouped.value::double precision/totaled.total::double precision END'
        ' FROM grouped CROSS JOIN totaled ORDER BY abs(grouped.value) DESC,grouped.dimension_value LIMIT %s',
        v_sql,p_dimension,p_start,p_start,p_end,p_end,p_limit
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_metric_change(
    p_graph_id uuid,p_metric_key text,p_dimension text,
    p_prior_start timestamptz,p_prior_end timestamptz,
    p_current_start timestamptz,p_current_end timestamptz,p_limit integer DEFAULT 25
) RETURNS TABLE(dimension_value text,prior_value numeric,current_value numeric,change numeric,share_of_change double precision)
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE v_sql text; v_error text;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'change limit must be between 1 and 500'; END IF;
    IF p_prior_start>=p_prior_end OR p_current_start>=p_current_end THEN RAISE EXCEPTION 'value graph change periods must be non-empty'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.business_value_metric_catalog catalog
         WHERE catalog.graph_id=p_graph_id AND catalog.metric_key=p_metric_key
           AND EXISTS (SELECT 1 FROM jsonb_array_elements(catalog.dimensions) d WHERE d->>'key'=p_dimension)
    ) THEN RAISE EXCEPTION 'value graph metric or dimension was not found'; END IF;
    v_sql := rvbbit.business_value_metric_runtime_sql(p_graph_id,p_metric_key);
    v_error := rvbbit._synth_validate(v_sql);
    IF v_error IS NOT NULL THEN RAISE EXCEPTION 'value graph metric is no longer a safe SELECT: %',v_error; END IF;
    PERFORM set_config('statement_timeout','60000',true);
    RETURN QUERY EXECUTE format(
        'WITH metric_events AS (%s), grouped AS ('
        ' SELECT coalesce(nullif(dimensions::jsonb->>%L,''''),''Unknown'') AS dimension_value,'
        ' coalesce(sum(value::numeric) FILTER (WHERE occurred_at::timestamptz >= %L::timestamptz AND occurred_at::timestamptz < %L::timestamptz),0)::numeric AS prior_value,'
        ' coalesce(sum(value::numeric) FILTER (WHERE occurred_at::timestamptz >= %L::timestamptz AND occurred_at::timestamptz < %L::timestamptz),0)::numeric AS current_value'
        ' FROM metric_events WHERE value IS NOT NULL GROUP BY 1), changed AS ('
        ' SELECT *,current_value-prior_value AS change FROM grouped), totals AS (SELECT sum(change) AS total_change FROM changed)'
        ' SELECT changed.dimension_value,changed.prior_value,changed.current_value,changed.change,'
        ' CASE WHEN totals.total_change=0 THEN 0 ELSE changed.change::double precision/totals.total_change::double precision END'
        ' FROM changed CROSS JOIN totals ORDER BY abs(changed.change) DESC,changed.dimension_value LIMIT %s',
        v_sql,p_dimension,p_prior_start,p_prior_end,p_current_start,p_current_end,p_limit
    );
END
$fn$;

DROP VIEW IF EXISTS rvbbit.business_value_promotion_catalog;
DROP VIEW IF EXISTS rvbbit.business_value_graph_summary;
CREATE VIEW rvbbit.business_value_graph_summary AS
SELECT proposal.graph_id,proposal.workflow_run_id,proposal.domain_key,
       proposal.name,proposal.summary,proposal.status,
       proposal.source_relations,proposal.primary_metric_key,
       jsonb_array_length(proposal.graph->'metrics')::integer AS metric_count,
       jsonb_array_length(proposal.graph->'edges')::integer AS edge_count,
       coalesce((proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0) AS validated_metric_count,
       coalesce((proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false) AS reconciled,
       coalesce((proposal.receipt->>'fallback')::boolean,false) AS fallback,
       proposal.graph,proposal.receipt,
       proposal.proposed_by,proposal.proposed_at,proposal.updated_at,
       proposal.reviewed_by,proposal.reviewed_at,proposal.review_reason,
       promotion.promotion_id,promotion.cube_name,promotion.cube_version,
       promotion.materialization_grain,promotion.metric_versions,
       promotion.promotion_receipt,promotion.promoted_by,promotion.promoted_at,
       promotion.last_refreshed_at
  FROM rvbbit.business_value_graph_proposals proposal
  LEFT JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id);

CREATE OR REPLACE VIEW rvbbit.business_value_promotion_catalog AS
SELECT summary.graph_id,summary.workflow_run_id,summary.domain_key,summary.name,
       summary.status,summary.promotion_id,summary.cube_name,
       'cubes.'||summary.cube_name AS cube_relation,
       summary.cube_version,summary.materialization_grain,summary.metric_versions,
       summary.promotion_receipt,summary.promoted_by,summary.promoted_at,
       summary.last_refreshed_at
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.promotion_id IS NOT NULL;

COMMENT ON VIEW rvbbit.business_value_promotion_catalog IS
    'Promoted business value systems with their native RVBBIT cube, governed metric versions, refresh state, and reconciliation receipt.';
