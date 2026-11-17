-- 0352: make categorical evidence explicit.  Metric dimensions receive an
-- append-only census after reconciliation, with honest exact/bounded coverage
-- and drift against the preceding complete dictionary.  The ordinary topology
-- profile remains cheap and sampled; it is never promoted to a complete value
-- dictionary by this migration.

UPDATE rvbbit.business_semantic_watch_specs
   SET specification=specification || jsonb_build_object(
           'timeout_seconds',greatest(240,coalesce((specification->>'timeout_seconds')::integer,240)),
           'census_row_limit',greatest(1000,coalesce((specification->>'census_row_limit')::integer,250000)),
           'census_member_limit',greatest(8,coalesce((specification->>'census_member_limit')::integer,128))
       )
 WHERE watch_kind='metric_reconciliation';

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_dimension_census_snapshots (
    census_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Deliberately not a foreign key: immutable evidence must survive graph or
    -- watch retirement, just like business_semantic_watch_observations.
    watch_id uuid NOT NULL,
    family_id uuid NOT NULL,
    graph_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    metric_key text NOT NULL,
    dimension_key text NOT NULL,
    coverage_mode text NOT NULL,
    coverage_complete boolean NOT NULL,
    member_list_complete boolean NOT NULL,
    row_limit integer NOT NULL,
    rows_observed bigint NOT NULL,
    member_count integer NOT NULL,
    members jsonb NOT NULL DEFAULT '[]'::jsonb,
    tail_member_count integer NOT NULL DEFAULT 0,
    tail_rows bigint NOT NULL DEFAULT 0,
    total_metric_value numeric,
    event_sql_fingerprint text NOT NULL,
    baseline_census_id bigint,
    added_values jsonb NOT NULL DEFAULT '[]'::jsonb,
    removed_values jsonb NOT NULL DEFAULT '[]'::jsonb,
    taxonomy_changed boolean NOT NULL DEFAULT false,
    trigger_kind text NOT NULL DEFAULT 'manual',
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_dimension_census_coverage_check CHECK (
        coverage_mode IN ('exact','bounded_sample')
    ),
    CONSTRAINT business_semantic_dimension_census_count_check CHECK (
        row_limit>0 AND rows_observed>=0 AND member_count>=0
        AND tail_member_count>=0 AND tail_rows>=0
    ),
    CONSTRAINT business_semantic_dimension_census_json_check CHECK (
        jsonb_typeof(members)='array'
        AND jsonb_typeof(added_values)='array'
        AND jsonb_typeof(removed_values)='array'
    )
);

CREATE INDEX IF NOT EXISTS business_semantic_dimension_census_watch_idx
    ON rvbbit.business_semantic_dimension_census_snapshots(
        watch_id,dimension_key,observed_at DESC,census_id DESC
    );
CREATE INDEX IF NOT EXISTS business_semantic_dimension_census_graph_idx
    ON rvbbit.business_semantic_dimension_census_snapshots(
        graph_id,metric_key,dimension_key,observed_at DESC
    );

CREATE TRIGGER business_semantic_dimension_census_immutable
BEFORE UPDATE OR DELETE ON rvbbit.business_semantic_dimension_census_snapshots
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_observation_immutable();

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_run_dimension_census(
    p_watch_id uuid,
    p_trigger_kind text DEFAULT 'manual'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_watch rvbbit.business_semantic_watch_specs%ROWTYPE;
    v_graph jsonb;
    v_metric jsonb;
    v_event_sql text;
    v_validation_error text;
    v_dimension_keys text[];
    v_row_limit integer;
    v_member_limit integer;
    v_result record;
    v_previous_census_id bigint;
    v_previous_members jsonb;
    v_members jsonb;
    v_added jsonb;
    v_removed jsonb;
    v_census_id bigint;
    v_censuses jsonb := '[]'::jsonb;
    v_changed_dimensions integer := 0;
BEGIN
    SELECT * INTO v_watch
      FROM rvbbit.business_semantic_watch_specs
     WHERE watch_id=p_watch_id;
    IF NOT FOUND OR NOT v_watch.enabled OR v_watch.watch_kind<>'metric_reconciliation' THEN
        RETURN jsonb_build_object('status','not_applicable','watch_id',p_watch_id);
    END IF;

    SELECT proposal.graph INTO v_graph
      FROM rvbbit.business_value_graph_proposals proposal
     WHERE proposal.graph_id=v_watch.graph_id;
    SELECT value INTO v_metric
      FROM jsonb_array_elements(coalesce(v_graph->'metrics','[]'::jsonb))
     WHERE value->>'metric_key'=v_watch.specification->>'metric_key'
     LIMIT 1;
    v_event_sql := v_metric->>'event_sql';
    v_validation_error := rvbbit._synth_validate(v_event_sql);
    IF v_metric IS NULL OR nullif(btrim(v_event_sql),'') IS NULL
       OR v_validation_error IS NOT NULL THEN
        RETURN jsonb_build_object(
            'status','invalid_metric_projection','watch_id',p_watch_id,
            'event_sql_fingerprint',v_watch.specification->>'event_sql_fingerprint'
        );
    END IF;

    SELECT coalesce(array_agg(value->>'key' ORDER BY ordinality),'{}'::text[])
      INTO v_dimension_keys
      FROM jsonb_array_elements(coalesce(v_metric->'dimensions','[]'::jsonb))
           WITH ORDINALITY;
    IF cardinality(v_dimension_keys)=0 THEN
        RETURN jsonb_build_object('status','completed','watch_id',p_watch_id,'dimensions',0);
    END IF;

    v_row_limit := least(1000000,greatest(1000,
        coalesce((v_watch.specification->>'census_row_limit')::integer,250000)));
    v_member_limit := least(256,greatest(8,
        coalesce((v_watch.specification->>'census_member_limit')::integer,128)));
    PERFORM set_config('statement_timeout',
        (least(600,greatest(30,coalesce(
            (v_watch.specification->>'timeout_seconds')::integer,240)))*1000)::text,true);

    FOR v_result IN EXECUTE format(
        'WITH metric_events AS (%s),'
        ' bounded AS MATERIALIZED (SELECT * FROM metric_events LIMIT %s),'
        ' coverage AS (SELECT count(*)::bigint AS fetched_rows FROM bounded),'
        ' usable AS (SELECT * FROM bounded LIMIT %s),'
        ' expanded AS ('
        '   SELECT dimension_key,'
        '          left(coalesce(nullif(event.dimensions::jsonb->>dimension_key,''''),''Unknown''),256) AS dimension_value,'
        '          event.value::numeric AS metric_value,event.occurred_at::timestamptz AS occurred_at'
        '     FROM usable event CROSS JOIN unnest(%L::text[]) dimension_key'
        '    WHERE event.value IS NOT NULL'
        ' ), grouped AS MATERIALIZED ('
        '   SELECT dimension_key,dimension_value,count(*)::bigint AS row_count,'
        '          sum(metric_value)::numeric AS metric_value,min(occurred_at) AS first_seen,'
        '          max(occurred_at) AS last_seen'
        '     FROM expanded GROUP BY dimension_key,dimension_value'
        ' ), ranked AS ('
        '   SELECT grouped.*,row_number() OVER ('
        '              PARTITION BY dimension_key ORDER BY row_count DESC,dimension_value) AS member_rank,'
        '          count(*) OVER (PARTITION BY dimension_key)::integer AS member_count,'
        '          sum(row_count) OVER (PARTITION BY dimension_key)::bigint AS rows_observed,'
        '          sum(metric_value) OVER (PARTITION BY dimension_key)::numeric AS total_metric_value'
        '     FROM grouped'
        ' )'
        ' SELECT ranked.dimension_key,max(ranked.member_count)::integer AS member_count,'
        '        max(ranked.rows_observed)::bigint AS rows_observed,'
        '        max(ranked.total_metric_value)::numeric AS total_metric_value,'
        '        (SELECT fetched_rows<=%s FROM coverage) AS coverage_complete,'
        '        coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('
        '          ''value'',ranked.dimension_value,''row_count'',ranked.row_count,'
        '          ''metric_value'',ranked.metric_value,'
        '          ''share_of_rows'',CASE WHEN ranked.rows_observed=0 THEN 0'
        '             ELSE ranked.row_count::double precision/ranked.rows_observed::double precision END,'
        '          ''first_seen'',ranked.first_seen,''last_seen'',ranked.last_seen))'
        '          ORDER BY ranked.row_count DESC,ranked.dimension_value)'
        '          FILTER (WHERE ranked.member_rank<=%s),''[]''::jsonb) AS members,'
        '        count(*) FILTER (WHERE ranked.member_rank>%s)::integer AS tail_member_count,'
        '        coalesce(sum(ranked.row_count) FILTER (WHERE ranked.member_rank>%s),0)::bigint AS tail_rows'
        '   FROM ranked GROUP BY ranked.dimension_key ORDER BY ranked.dimension_key',
        v_event_sql,v_row_limit+1,v_row_limit,v_dimension_keys::text,
        v_row_limit,v_member_limit,v_member_limit,v_member_limit
    ) LOOP
        v_members := coalesce(v_result.members,'[]'::jsonb);
        v_added := '[]'::jsonb;
        v_removed := '[]'::jsonb;
        v_previous_census_id := NULL;
        v_previous_members := NULL;

        IF v_result.coverage_complete AND v_result.member_count<=v_member_limit THEN
            SELECT census_id,members INTO v_previous_census_id,v_previous_members
              FROM rvbbit.business_semantic_dimension_census_snapshots
             WHERE watch_id=p_watch_id
               AND dimension_key=v_result.dimension_key
               AND coverage_complete
               AND member_list_complete
             ORDER BY observed_at DESC,census_id DESC
             LIMIT 1;
            IF v_previous_census_id IS NOT NULL THEN
                SELECT coalesce(jsonb_agg(current_member->>'value' ORDER BY current_member->>'value'),'[]'::jsonb)
                  INTO v_added
                  FROM jsonb_array_elements(v_members) current_member
                 WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(v_previous_members) previous_member
                     WHERE previous_member->>'value'=current_member->>'value'
                 );
                SELECT coalesce(jsonb_agg(previous_member->>'value' ORDER BY previous_member->>'value'),'[]'::jsonb)
                  INTO v_removed
                  FROM jsonb_array_elements(v_previous_members) previous_member
                 WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(v_members) current_member
                     WHERE current_member->>'value'=previous_member->>'value'
                 );
            END IF;
        END IF;

        INSERT INTO rvbbit.business_semantic_dimension_census_snapshots(
            watch_id,family_id,graph_id,assertion_id,metric_key,dimension_key,
            coverage_mode,coverage_complete,member_list_complete,row_limit,
            rows_observed,member_count,members,tail_member_count,tail_rows,
            total_metric_value,event_sql_fingerprint,baseline_census_id,
            added_values,removed_values,taxonomy_changed,trigger_kind
        ) VALUES (
            p_watch_id,v_watch.family_id,v_watch.graph_id,v_watch.assertion_id,
            v_metric->>'metric_key',v_result.dimension_key,
            CASE WHEN v_result.coverage_complete THEN 'exact' ELSE 'bounded_sample' END,
            v_result.coverage_complete,
            v_result.coverage_complete AND v_result.member_count<=v_member_limit,
            v_row_limit,v_result.rows_observed,v_result.member_count,v_members,
            v_result.tail_member_count,v_result.tail_rows,v_result.total_metric_value,
            md5(v_event_sql),v_previous_census_id,v_added,v_removed,
            jsonb_array_length(v_added)>0 OR jsonb_array_length(v_removed)>0,
            coalesce(nullif(btrim(p_trigger_kind),''),'manual')
        ) RETURNING census_id INTO v_census_id;

        IF jsonb_array_length(v_added)>0 OR jsonb_array_length(v_removed)>0 THEN
            v_changed_dimensions := v_changed_dimensions+1;
        END IF;
        v_censuses := v_censuses || jsonb_build_array(jsonb_build_object(
            'census_id',v_census_id,'dimension_key',v_result.dimension_key,
            'coverage_mode',CASE WHEN v_result.coverage_complete THEN 'exact' ELSE 'bounded_sample' END,
            'coverage_complete',v_result.coverage_complete,
            'member_list_complete',v_result.coverage_complete AND v_result.member_count<=v_member_limit,
            'rows_observed',v_result.rows_observed,'member_count',v_result.member_count,
            'added_values',v_added,'removed_values',v_removed
        ));
    END LOOP;

    IF v_changed_dimensions>0 THEN
        UPDATE rvbbit.business_semantic_assertion_states
           SET effective_confidence=greatest(0.05,effective_confidence-0.05),
               validation_state='warning',
               review_state=CASE WHEN review_state='needs_decision' THEN review_state ELSE 'needs_revision' END,
               updated_at=clock_timestamp()
         WHERE assertion_id=v_watch.assertion_id;
    END IF;

    RETURN jsonb_build_object(
        'status','completed','watch_id',p_watch_id,
        'dimensions',jsonb_array_length(v_censuses),
        'changed_dimensions',v_changed_dimensions,'censuses',v_censuses
    );
END
$fn$;

-- Preserve the tested reconciliation implementation and wrap it with the
-- census.  Existing dossier/manual/scheduled callers keep the same API.
ALTER FUNCTION rvbbit.business_semantic_run_watch(uuid,text)
    RENAME TO _business_semantic_run_watch_reconciliation;

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_run_watch(
    p_watch_id uuid,
    p_trigger_kind text DEFAULT 'manual'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_result jsonb;
    v_kind text;
    v_census jsonb;
BEGIN
    SELECT watch_kind INTO v_kind
      FROM rvbbit.business_semantic_watch_specs
     WHERE watch_id=p_watch_id;
    v_result := rvbbit._business_semantic_run_watch_reconciliation(
        p_watch_id,p_trigger_kind
    );
    IF v_kind='metric_reconciliation'
       AND coalesce(v_result->>'state','unknown')<>'unknown' THEN
        BEGIN
            v_census := rvbbit.business_semantic_run_dimension_census(
                p_watch_id,p_trigger_kind
            );
        EXCEPTION
            WHEN query_canceled THEN
                v_census := jsonb_build_object(
                    'status','bounded_timeout','error_code',SQLSTATE
                );
            WHEN OTHERS THEN
                v_census := jsonb_build_object(
                    'status','execution_error','error_code',SQLSTATE
                );
        END;
        v_result := v_result || jsonb_build_object('dimension_census',v_census);
    END IF;
    RETURN v_result;
END
$fn$;

CREATE OR REPLACE VIEW rvbbit.business_semantic_dimension_census_current AS
SELECT DISTINCT ON (snapshot.graph_id,snapshot.metric_key,snapshot.dimension_key)
       snapshot.census_id,snapshot.watch_id,snapshot.family_id,snapshot.graph_id,
       snapshot.assertion_id,snapshot.metric_key,snapshot.dimension_key,
       snapshot.coverage_mode,snapshot.coverage_complete,
       snapshot.member_list_complete,snapshot.row_limit,snapshot.rows_observed,
       snapshot.member_count,snapshot.members,snapshot.tail_member_count,
       snapshot.tail_rows,snapshot.total_metric_value,
       snapshot.event_sql_fingerprint,snapshot.baseline_census_id,
       snapshot.added_values,snapshot.removed_values,snapshot.taxonomy_changed,
       snapshot.trigger_kind,snapshot.observed_at,
       jsonb_build_object(
           'census_id',snapshot.census_id,'metric_key',snapshot.metric_key,
           'dimension_key',snapshot.dimension_key,
           'coverage_mode',snapshot.coverage_mode,
           'coverage_complete',snapshot.coverage_complete,
           'member_list_complete',snapshot.member_list_complete,
           'row_limit',snapshot.row_limit,'rows_observed',snapshot.rows_observed,
           'member_count',snapshot.member_count,'members',snapshot.members,
           'tail_member_count',snapshot.tail_member_count,'tail_rows',snapshot.tail_rows,
           'total_metric_value',snapshot.total_metric_value,
           'event_sql_fingerprint',snapshot.event_sql_fingerprint,
           'baseline_census_id',snapshot.baseline_census_id,
           'added_values',snapshot.added_values,'removed_values',snapshot.removed_values,
           'taxonomy_changed',snapshot.taxonomy_changed,
           'trigger_kind',snapshot.trigger_kind,'observed_at',snapshot.observed_at
       ) AS census
  FROM rvbbit.business_semantic_dimension_census_snapshots snapshot
 ORDER BY snapshot.graph_id,snapshot.metric_key,snapshot.dimension_key,
          snapshot.observed_at DESC,snapshot.census_id DESC;

COMMENT ON TABLE rvbbit.business_semantic_dimension_census_snapshots IS
    'Append-only category membership evidence for declared metric dimensions; exactness and member-list completeness are explicit.';
COMMENT ON VIEW rvbbit.business_semantic_dimension_census_current IS
    'Latest exact or bounded census for each metric dimension, including all-time counts, temporal extent, and taxonomy drift.';
