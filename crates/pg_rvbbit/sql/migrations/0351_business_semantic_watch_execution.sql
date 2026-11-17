-- 0351: execute the deterministic part of semantic dossiers. Observations are
-- append-only evidence; they may evolve confidence/review state, but they never
-- rewrite the assertion or its owning Value Graph.

ALTER TABLE rvbbit.business_semantic_watch_specs
    ADD COLUMN IF NOT EXISTS check_interval interval NOT NULL DEFAULT interval '6 hours',
    ADD COLUMN IF NOT EXISTS last_observed_at timestamptz,
    ADD COLUMN IF NOT EXISTS next_due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN IF NOT EXISTS observation_count bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS consecutive_passes integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS consecutive_failures integer NOT NULL DEFAULT 0;

UPDATE rvbbit.business_semantic_watch_specs
   SET check_interval=CASE watch_kind
       WHEN 'join_coverage' THEN interval '12 hours'
       ELSE interval '6 hours'
   END,
       next_due_at=coalesce(last_observed_at,created_at,clock_timestamp());

CREATE OR REPLACE FUNCTION rvbbit._business_semantic_watch_defaults()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.check_interval IS NULL OR NEW.check_interval=interval '6 hours' THEN
        NEW.check_interval := CASE NEW.watch_kind
            WHEN 'join_coverage' THEN interval '12 hours'
            ELSE interval '6 hours'
        END;
    END IF;
    NEW.next_due_at := coalesce(NEW.next_due_at,clock_timestamp());
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS business_semantic_watch_defaults
    ON rvbbit.business_semantic_watch_specs;
CREATE TRIGGER business_semantic_watch_defaults
BEFORE INSERT ON rvbbit.business_semantic_watch_specs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_watch_defaults();

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_watch_observations (
    observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    watch_id uuid NOT NULL,
    family_id uuid NOT NULL,
    graph_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    watch_kind text NOT NULL,
    state text NOT NULL,
    trigger_kind text NOT NULL DEFAULT 'manual',
    measurements jsonb NOT NULL DEFAULT '{}'::jsonb,
    duration_ms integer NOT NULL DEFAULT 0,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_watch_observation_kind_check CHECK (
        watch_kind IN ('source_presence','join_coverage','metric_reconciliation')
    ),
    CONSTRAINT business_semantic_watch_observation_state_check CHECK (
        state IN ('passing','failing','unknown')
    ),
    CONSTRAINT business_semantic_watch_observation_json_check CHECK (
        jsonb_typeof(measurements)='object'
        AND NOT measurements ? 'event_sql'
    )
);

CREATE INDEX IF NOT EXISTS business_semantic_watch_observations_watch_idx
    ON rvbbit.business_semantic_watch_observations(watch_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS business_semantic_watch_observations_family_idx
    ON rvbbit.business_semantic_watch_observations(family_id,observed_at DESC);

CREATE OR REPLACE FUNCTION rvbbit._business_semantic_observation_immutable()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'business semantic watch observations are append-only';
END
$fn$;

DROP TRIGGER IF EXISTS business_semantic_observation_immutable
    ON rvbbit.business_semantic_watch_observations;
CREATE TRIGGER business_semantic_observation_immutable
BEFORE UPDATE OR DELETE ON rvbbit.business_semantic_watch_observations
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_observation_immutable();

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_assertion_states (
    assertion_id uuid PRIMARY KEY
        REFERENCES rvbbit.business_semantic_assertions(assertion_id) ON DELETE CASCADE,
    base_confidence double precision NOT NULL,
    effective_confidence double precision NOT NULL,
    validation_state text NOT NULL DEFAULT 'unobserved',
    review_state text NOT NULL DEFAULT 'normal',
    observation_count bigint NOT NULL DEFAULT 0,
    consecutive_passes integer NOT NULL DEFAULT 0,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_state text,
    last_observed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_assertion_effective_confidence_check CHECK (
        base_confidence BETWEEN 0.0 AND 1.0
        AND effective_confidence BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT business_semantic_assertion_validation_state_check CHECK (
        validation_state IN ('unobserved','supported','warning','contradicted')
    ),
    CONSTRAINT business_semantic_assertion_review_state_check CHECK (
        review_state IN ('normal','needs_revision','needs_decision')
    ),
    CONSTRAINT business_semantic_assertion_last_state_check CHECK (
        last_state IS NULL OR last_state IN ('passing','failing','unknown')
    )
);

INSERT INTO rvbbit.business_semantic_assertion_states(
    assertion_id,base_confidence,effective_confidence
)
SELECT assertion_id,confidence,confidence
  FROM rvbbit.business_semantic_assertions
ON CONFLICT (assertion_id) DO NOTHING;

CREATE OR REPLACE FUNCTION rvbbit._business_semantic_assertion_state_seed()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO rvbbit.business_semantic_assertion_states(
        assertion_id,base_confidence,effective_confidence
    ) VALUES (NEW.assertion_id,NEW.confidence,NEW.confidence)
    ON CONFLICT (assertion_id) DO NOTHING;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_semantic_assertion_state_seed
    ON rvbbit.business_semantic_assertions;
CREATE TRIGGER business_semantic_assertion_state_seed
AFTER INSERT ON rvbbit.business_semantic_assertions
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_assertion_state_seed();

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_run_watch(
    p_watch_id uuid,
    p_trigger_kind text DEFAULT 'manual'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_watch rvbbit.business_semantic_watch_specs%ROWTYPE;
    v_started timestamptz := clock_timestamp();
    v_state text := 'unknown';
    v_measurements jsonb := '{}'::jsonb;
    v_relation regclass;
    v_left regclass;
    v_right regclass;
    v_schema text;
    v_name text;
    v_left_columns text[];
    v_right_columns text[];
    v_join_predicate text;
    v_right_group text;
    v_right_nonnull text;
    v_sample_limit integer;
    v_min_match_rate double precision;
    v_max_fanout integer;
    v_graph jsonb;
    v_metric jsonb;
    v_event_sql text;
    v_validation_error text;
    v_dimension jsonb;
    v_dimension_result jsonb;
    v_reconciliations jsonb := '[]'::jsonb;
    v_all_reconciled boolean := true;
    v_total numeric;
    v_grouped_total numeric;
    v_duration integer;
    v_lifecycle text;
BEGIN
    SELECT * INTO v_watch
      FROM rvbbit.business_semantic_watch_specs
     WHERE watch_id=p_watch_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','not_found','watch_id',p_watch_id);
    END IF;
    IF NOT v_watch.enabled THEN
        RETURN jsonb_build_object('status','disabled','watch_id',p_watch_id);
    END IF;

    BEGIN
        IF v_watch.watch_kind='source_presence' THEN
            v_relation := rvbbit._safe_regclass(v_watch.specification->>'relation');
            IF v_relation IS NULL THEN
                v_state := 'failing';
                v_measurements := jsonb_build_object(
                    'relation',v_watch.specification->>'relation',
                    'relation_exists',false,'relation_allowed',false
                );
            ELSE
                SELECT namespace.nspname,class.relname
                  INTO v_schema,v_name
                  FROM pg_class class
                  JOIN pg_namespace namespace ON namespace.oid=class.relnamespace
                 WHERE class.oid=v_relation;
                v_state := CASE
                    WHEN rvbbit._business_topology_relation_allowed(v_schema,v_name)
                    THEN 'passing' ELSE 'failing' END;
                v_measurements := jsonb_build_object(
                    'relation',format('%I.%I',v_schema,v_name),
                    'relation_exists',true,
                    'relation_allowed',v_state='passing'
                );
            END IF;

        ELSIF v_watch.watch_kind='join_coverage' THEN
            v_left := rvbbit._safe_regclass(v_watch.specification->>'left_relation');
            v_right := rvbbit._safe_regclass(v_watch.specification->>'right_relation');
            SELECT coalesce(array_agg(value ORDER BY ordinality),'{}'::text[])
              INTO v_left_columns
              FROM jsonb_array_elements_text(coalesce(v_watch.specification->'left_columns','[]'))
                   WITH ORDINALITY;
            SELECT coalesce(array_agg(value ORDER BY ordinality),'{}'::text[])
              INTO v_right_columns
              FROM jsonb_array_elements_text(coalesce(v_watch.specification->'right_columns','[]'))
                   WITH ORDINALITY;
            v_sample_limit := least(50000,greatest(100,
                coalesce((v_watch.specification->>'sample_limit')::integer,50000)));
            v_min_match_rate := greatest(0.0,least(1.0,
                coalesce((v_watch.specification->>'minimum_match_rate')::double precision,0.80)));
            v_max_fanout := greatest(1,
                coalesce((v_watch.specification->>'maximum_fanout')::integer,1));

            IF v_left IS NULL OR v_right IS NULL
               OR cardinality(v_left_columns)=0
               OR cardinality(v_left_columns)<>cardinality(v_right_columns) THEN
                v_state := 'failing';
                v_measurements := jsonb_build_object(
                    'left_relation_exists',v_left IS NOT NULL,
                    'right_relation_exists',v_right IS NOT NULL,
                    'key_arity',cardinality(v_left_columns),
                    'right_key_arity',cardinality(v_right_columns)
                );
            ELSE
                SELECT string_agg(format('l.%I = r.%I',left_col,right_col),' AND ' ORDER BY ord)
                  INTO v_join_predicate
                  FROM unnest(v_left_columns,v_right_columns) WITH ORDINALITY
                       AS keys(left_col,right_col,ord);
                SELECT string_agg(format('%I',right_col),',' ORDER BY ord),
                       string_agg(format('%I IS NOT NULL',right_col),' AND ' ORDER BY ord)
                  INTO v_right_group,v_right_nonnull
                  FROM unnest(v_right_columns) WITH ORDINALITY AS keys(right_col,ord);

                EXECUTE format(
                    'WITH l AS (SELECT row_number() OVER () AS __rvbbit_left_row__,src.* FROM %s src LIMIT %s),'
                    ' r AS (SELECT 1::integer AS __rvbbit_watch_present__,src.* FROM %s src LIMIT %s),'
                    ' joined AS (SELECT l.__rvbbit_left_row__,count(r.__rvbbit_watch_present__)::integer AS fanout'
                    ' FROM l LEFT JOIN r ON %s GROUP BY l.__rvbbit_left_row__),'
                    ' duplicate_keys AS (SELECT count(*)::integer AS groups FROM ('
                    ' SELECT %s FROM r WHERE %s GROUP BY %s HAVING count(*)>1) duplicated)'
                    ' SELECT jsonb_build_object('
                    ' ''sample_limit'',%s,''left_rows'',count(*)::integer,'
                    ' ''matched_left_rows'',count(*) FILTER (WHERE fanout>0)::integer,'
                    ' ''left_match_rate'',CASE WHEN count(*)=0 THEN NULL ELSE round((count(*) FILTER (WHERE fanout>0))::numeric/count(*)::numeric,6) END,'
                    ' ''maximum_fanout'',coalesce(max(fanout),0),'
                    ' ''multi_match_left_rows'',count(*) FILTER (WHERE fanout>1)::integer,'
                    ' ''duplicate_right_key_groups'',(SELECT groups FROM duplicate_keys),'
                    ' ''minimum_match_rate_threshold'',%L::double precision,'
                    ' ''maximum_fanout_threshold'',%s) FROM joined',
                    v_left,v_sample_limit,v_right,v_sample_limit,v_join_predicate,
                    v_right_group,v_right_nonnull,v_right_group,v_sample_limit,
                    v_min_match_rate,v_max_fanout
                ) INTO v_measurements;
                IF coalesce((v_measurements->>'left_rows')::integer,0)=0 THEN
                    v_state := 'unknown';
                ELSIF coalesce((v_measurements->>'left_match_rate')::double precision,0)>=v_min_match_rate
                  AND coalesce((v_measurements->>'maximum_fanout')::integer,0)<=v_max_fanout
                  AND coalesce((v_measurements->>'duplicate_right_key_groups')::integer,0)=0 THEN
                    v_state := 'passing';
                ELSE
                    v_state := 'failing';
                END IF;
            END IF;

        ELSIF v_watch.watch_kind='metric_reconciliation' THEN
            SELECT proposal.graph INTO v_graph
              FROM rvbbit.business_value_graph_proposals proposal
             WHERE proposal.graph_id=v_watch.graph_id;
            SELECT value INTO v_metric
              FROM jsonb_array_elements(coalesce(v_graph->'metrics','[]'))
             WHERE value->>'metric_key'=v_watch.specification->>'metric_key'
             LIMIT 1;
            v_event_sql := v_metric->>'event_sql';
            v_validation_error := rvbbit._synth_validate(v_event_sql);
            IF v_metric IS NULL OR nullif(btrim(v_event_sql),'') IS NULL
               OR v_validation_error IS NOT NULL THEN
                v_state := 'failing';
                v_measurements := jsonb_build_object(
                    'metric_key',v_watch.specification->>'metric_key',
                    'event_sql_fingerprint',v_watch.specification->>'event_sql_fingerprint',
                    'projection_valid',false,
                    'failure_class','invalid_metric_projection'
                );
            ELSE
                v_sample_limit := least(100000,greatest(100,
                    coalesce((v_watch.specification->>'sample_limit')::integer,50000)));
                PERFORM set_config('statement_timeout',
                    (least(300,greatest(10,coalesce(
                        (v_watch.specification->>'timeout_seconds')::integer,120)))*1000)::text,true);
                EXECUTE format(
                    'WITH metric_events AS (%s), bounded AS (SELECT * FROM metric_events LIMIT %s)'
                    ' SELECT jsonb_build_object('
                    ' ''metric_key'',%L,''event_sql_fingerprint'',%L,'
                    ' ''sample_limit'',%s,''event_rows'',count(*)::integer,'
                    ' ''valued_rows'',count(value)::integer,'
                    ' ''total_value'',coalesce(sum(value::numeric),0),'
                    ' ''minimum_time'',min(occurred_at::timestamptz),'
                    ' ''maximum_time'',max(occurred_at::timestamptz),'
                    ' ''projection_valid'',true) FROM bounded',
                    v_event_sql,v_sample_limit,v_metric->>'metric_key',
                    md5(v_event_sql),v_sample_limit
                ) INTO v_measurements;
                v_total := coalesce((v_measurements->>'total_value')::numeric,0);

                FOR v_dimension IN
                    SELECT value FROM jsonb_array_elements(coalesce(v_metric->'dimensions','[]'))
                LOOP
                    EXECUTE format(
                        'WITH metric_events AS (%s), bounded AS (SELECT * FROM metric_events LIMIT %s),'
                        ' grouped AS (SELECT dimensions::jsonb->>%L AS dimension_value,'
                        ' sum(value::numeric)::numeric AS value FROM bounded'
                        ' WHERE value IS NOT NULL GROUP BY 1)'
                        ' SELECT jsonb_build_object('
                        ' ''dimension_key'',%L,''group_count'',count(*)::integer,'
                        ' ''grouped_total'',coalesce(sum(value),0)) FROM grouped',
                        v_event_sql,v_sample_limit,v_dimension->>'key',v_dimension->>'key'
                    ) INTO v_dimension_result;
                    v_grouped_total := coalesce((v_dimension_result->>'grouped_total')::numeric,0);
                    v_dimension_result := v_dimension_result || jsonb_build_object(
                        'difference',v_grouped_total-v_total,
                        'passed',abs(v_grouped_total-v_total)<=greatest(0.000001,abs(v_total)*0.000000001)
                    );
                    v_all_reconciled := v_all_reconciled
                        AND coalesce((v_dimension_result->>'passed')::boolean,false);
                    v_reconciliations := v_reconciliations || jsonb_build_array(v_dimension_result);
                END LOOP;
                v_measurements := v_measurements || jsonb_build_object(
                    'dimensions_reconciled',v_all_reconciled,
                    'reconciliations',v_reconciliations
                );
                v_state := CASE WHEN v_all_reconciled THEN 'passing' ELSE 'failing' END;
            END IF;
        ELSE
            v_state := 'unknown';
            v_measurements := jsonb_build_object('failure_class','unsupported_watch_kind');
        END IF;
    EXCEPTION
        WHEN query_canceled THEN
            v_state := 'unknown';
            v_measurements := jsonb_build_object(
                'failure_class','bounded_timeout','error_code',SQLSTATE
            );
        WHEN OTHERS THEN
            v_state := 'failing';
            v_measurements := jsonb_build_object(
                'failure_class','execution_error','error_code',SQLSTATE
            );
    END;

    v_duration := greatest(0,round(extract(epoch FROM (clock_timestamp()-v_started))*1000)::integer);
    INSERT INTO rvbbit.business_semantic_watch_observations(
        watch_id,family_id,graph_id,assertion_id,watch_kind,state,
        trigger_kind,measurements,duration_ms
    ) VALUES (
        v_watch.watch_id,v_watch.family_id,v_watch.graph_id,v_watch.assertion_id,
        v_watch.watch_kind,v_state,coalesce(nullif(btrim(p_trigger_kind),''),'manual'),
        v_measurements,v_duration
    );

    UPDATE rvbbit.business_semantic_watch_specs
       SET latest_state=v_state,
           latest_evidence=v_measurements,
           last_observed_at=clock_timestamp(),
           next_due_at=clock_timestamp()+check_interval,
           observation_count=observation_count+1,
           consecutive_passes=CASE WHEN v_state='passing' THEN consecutive_passes+1 ELSE 0 END,
           consecutive_failures=CASE WHEN v_state='failing' THEN consecutive_failures+1 ELSE 0 END,
           updated_at=clock_timestamp()
     WHERE watch_id=v_watch.watch_id;

    INSERT INTO rvbbit.business_semantic_assertion_states(
        assertion_id,base_confidence,effective_confidence
    )
    SELECT assertion_id,confidence,confidence
      FROM rvbbit.business_semantic_assertions
     WHERE assertion_id=v_watch.assertion_id
    ON CONFLICT (assertion_id) DO NOTHING;

    SELECT lifecycle INTO v_lifecycle
      FROM rvbbit.business_value_graph_summary
     WHERE graph_id=v_watch.graph_id;
    UPDATE rvbbit.business_semantic_assertion_states
       SET effective_confidence=CASE v_state
               WHEN 'passing' THEN least(0.99,effective_confidence+0.01)
               WHEN 'failing' THEN greatest(0.05,effective_confidence-0.15)
               ELSE effective_confidence END,
           validation_state=CASE v_state
               WHEN 'passing' THEN 'supported'
               WHEN 'failing' THEN 'contradicted'
               ELSE 'warning' END,
           review_state=CASE
               WHEN v_state='failing' AND v_lifecycle='canonical' THEN 'needs_decision'
               WHEN v_state='failing' AND review_state='normal' THEN 'needs_revision'
               ELSE review_state END,
           observation_count=observation_count+1,
           consecutive_passes=CASE WHEN v_state='passing' THEN consecutive_passes+1 ELSE 0 END,
           consecutive_failures=CASE WHEN v_state='failing' THEN consecutive_failures+1 ELSE 0 END,
           last_state=v_state,last_observed_at=clock_timestamp(),updated_at=clock_timestamp()
     WHERE assertion_id=v_watch.assertion_id;

    RETURN jsonb_build_object(
        'status','observed','watch_id',v_watch.watch_id,'watch_kind',v_watch.watch_kind,
        'state',v_state,'measurements',v_measurements,'duration_ms',v_duration
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_semantic_run_watch(uuid,text) IS
    'Execute one bounded deterministic semantic watch and append evidence without rewriting its assertion.';

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_run_dossier_watches(
    p_graph_id uuid,
    p_trigger_kind text DEFAULT 'manual',
    p_limit integer DEFAULT 50
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_watch_id uuid;
    v_result jsonb;
    v_results jsonb := '[]'::jsonb;
    v_count integer := 0;
BEGIN
    FOR v_watch_id IN
        SELECT watch_id
          FROM rvbbit.business_semantic_watch_specs
         WHERE graph_id=p_graph_id AND enabled
         ORDER BY watch_kind,watch_key
         LIMIT least(100,greatest(1,coalesce(p_limit,50)))
    LOOP
        v_result := rvbbit.business_semantic_run_watch(v_watch_id,p_trigger_kind);
        v_results := v_results || jsonb_build_array(v_result);
        v_count := v_count+1;
    END LOOP;
    RETURN jsonb_build_object(
        'status','completed','graph_id',p_graph_id,'observed',v_count,
        'passing',(SELECT count(*) FROM jsonb_array_elements(v_results) item WHERE item->>'state'='passing'),
        'failing',(SELECT count(*) FROM jsonb_array_elements(v_results) item WHERE item->>'state'='failing'),
        'unknown',(SELECT count(*) FROM jsonb_array_elements(v_results) item WHERE item->>'state'='unknown'),
        'results',v_results
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_watch_tick(
    p_limit integer DEFAULT 20
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_watch_id uuid;
    v_result jsonb;
    v_results jsonb := '[]'::jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtext('rvbbit.business_semantic_watch_tick')) THEN
        RETURN jsonb_build_object('status','busy','observed',0);
    END IF;
    FOR v_watch_id IN
        SELECT watch.watch_id
          FROM rvbbit.business_semantic_watch_specs watch
          JOIN rvbbit.business_value_graph_current current_graph USING (graph_id)
         WHERE watch.enabled AND watch.next_due_at<=clock_timestamp()
         ORDER BY watch.next_due_at,watch.watch_kind,watch.watch_id
         LIMIT least(100,greatest(1,coalesce(p_limit,20)))
         FOR UPDATE OF watch SKIP LOCKED
    LOOP
        v_result := rvbbit.business_semantic_run_watch(v_watch_id,'scheduled');
        v_results := v_results || jsonb_build_array(v_result);
    END LOOP;
    RETURN jsonb_build_object(
        'status','completed','observed',jsonb_array_length(v_results),'results',v_results
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.schedule_business_semantic_watch_tick(
    cron_schedule text DEFAULT '17 * * * *',
    budget integer DEFAULT 20
) RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE
    jobid bigint;
    cron_home text := current_setting('cron.database_name',true);
    this_db text := current_database();
    command text := format('SELECT rvbbit.business_semantic_watch_tick(%s)',
                           least(100,greatest(1,coalesce(budget,20))));
BEGIN
    IF cron_home IS NOT NULL AND cron_home<>'' AND cron_home<>this_db THEN
        RAISE EXCEPTION 'pg_cron home database is %, not %; cron.* is not callable here.',cron_home,this_db
            USING HINT=format(
                'Connect to %L and run: SELECT cron.schedule_in_database(%L,%L,%L,%L);',
                cron_home,'rvbbit_business_semantic_watch_tick',cron_schedule,command,this_db);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_cron') THEN
        RAISE EXCEPTION 'pg_cron is not installed; cannot schedule semantic watches.';
    END IF;
    EXECUTE format('SELECT cron.schedule(%L,%L,%L)',
                   'rvbbit_business_semantic_watch_tick',cron_schedule,command)
       INTO jobid;
    RETURN jobid;
END
$fn$;

DROP VIEW IF EXISTS rvbbit.business_semantic_dossier_current;
DROP VIEW IF EXISTS rvbbit.business_semantic_dossiers;
CREATE VIEW rvbbit.business_semantic_dossiers AS
WITH assertion_rollup AS (
    SELECT assertion.graph_id,
           count(*)::integer AS assertion_count,
           count(*) FILTER (WHERE assertion.assertion_kind IN ('source_membership','metric')
             OR (assertion.assertion_kind='semantic_relationship'
                 AND assertion.predicate='joins_to'
                 AND jsonb_array_length(coalesce(assertion.object_value->'left_columns','[]'))>0
                 AND jsonb_array_length(coalesce(assertion.object_value->'right_columns','[]'))>0))::integer
               AS watchable_assertion_count,
           jsonb_object_agg(assertion.assertion_kind,assertion.kind_count ORDER BY assertion.assertion_kind)
               AS assertion_kinds,
           count(*) FILTER (WHERE coalesce(state.review_state,'normal')='needs_revision')::integer
               AS needs_revision_count,
           count(*) FILTER (WHERE coalesce(state.review_state,'normal')='needs_decision')::integer
               AS needs_decision_count,
           jsonb_agg(jsonb_build_object(
               'assertion_id',assertion.assertion_id,'assertion_key',assertion.assertion_key,
               'kind',assertion.assertion_kind,'subject_key',assertion.subject_key,
               'predicate',assertion.predicate,'value',assertion.object_value,
               'confidence',assertion.confidence,
               'effective_confidence',coalesce(state.effective_confidence,assertion.confidence),
               'validation_state',coalesce(state.validation_state,'unobserved'),
               'review_state',coalesce(state.review_state,'normal'),
               'observation_count',coalesce(state.observation_count,0),
               'last_observed_at',state.last_observed_at,
               'provenance',assertion.provenance_kind,'evidence',assertion.evidence
           ) ORDER BY assertion.assertion_kind,assertion.assertion_key) AS assertions
      FROM (SELECT item.*,count(*) OVER (PARTITION BY graph_id,assertion_kind)::integer AS kind_count
              FROM rvbbit.business_semantic_assertions item) assertion
      LEFT JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id)
     GROUP BY assertion.graph_id
), watch_rollup AS (
    SELECT watch.graph_id,count(*)::integer AS watch_count,
           count(DISTINCT watch.assertion_id)::integer AS watched_assertion_count,
           jsonb_object_agg(watch.latest_state,watch.state_count ORDER BY watch.latest_state) AS watch_states,
           jsonb_agg(jsonb_build_object(
               'watch_id',watch.watch_id,'watch_key',watch.watch_key,'kind',watch.watch_kind,
               'assertion_id',watch.assertion_id,'state',watch.latest_state,
               'enabled',watch.enabled,'specification',watch.specification,
               'evidence',watch.latest_evidence,'observation_count',watch.observation_count,
               'last_observed_at',watch.last_observed_at,'next_due_at',watch.next_due_at,
               'consecutive_passes',watch.consecutive_passes,
               'consecutive_failures',watch.consecutive_failures
           ) ORDER BY watch.watch_kind,watch.watch_key) AS watches
      FROM (SELECT item.*,count(*) OVER (PARTITION BY graph_id,latest_state)::integer AS state_count
              FROM rvbbit.business_semantic_watch_specs item) watch
     GROUP BY watch.graph_id
)
SELECT summary.graph_id,summary.workflow_run_id,summary.family_id,
       summary.family_revision,summary.predecessor_graph_id,
       coalesce(nullif(summary.graph #>> '{business_domain,domain_key}',''),
                nullif(summary.graph #>> '{business_domain,key}',''),summary.domain_key) AS business_domain_key,
       coalesce(nullif(summary.graph #>> '{business_domain,name}',''),summary.name) AS business_domain_name,
       summary.domain_key AS value_system_key,summary.name AS value_system_name,
       summary.summary,summary.lifecycle,summary.family_review_state,
       coalesce(assertions.assertion_count,0) AS assertion_count,
       coalesce(assertions.watchable_assertion_count,0) AS watchable_assertion_count,
       coalesce(assertions.assertion_kinds,'{}') AS assertion_kinds,
       coalesce(watches.watch_count,0) AS watch_count,
       coalesce(watches.watched_assertion_count,0) AS watched_assertion_count,
       coalesce(watches.watch_states,'{}') AS watch_states,
       CASE WHEN coalesce(assertions.watchable_assertion_count,0)=0 THEN 0.0
            ELSE round(coalesce(watches.watched_assertion_count,0)::numeric
                       /assertions.watchable_assertion_count::numeric,4)::double precision END
           AS automated_watch_coverage,
       coalesce(assertions.assertions,'[]') AS assertions,
       coalesce(watches.watches,'[]') AS watches,
       coalesce(revision.diff,jsonb_build_object(
           'schema_version','rvbbit.business-semantic.assertion-diff.v1',
           'change_kind','unavailable','material_change',false,'added','[]'::jsonb,
           'removed','[]'::jsonb,'changed','[]'::jsonb,'unchanged_count',0)) AS assertion_diff,
       jsonb_build_object(
           'schema_version','rvbbit.business-semantic.dossier.v1',
           'family_id',summary.family_id,'graph_id',summary.graph_id,
           'workflow_run_id',summary.workflow_run_id,'revision',summary.family_revision,
           'predecessor_graph_id',summary.predecessor_graph_id,
           'business_domain',jsonb_build_object(
               'key',coalesce(nullif(summary.graph #>> '{business_domain,domain_key}',''),
                              nullif(summary.graph #>> '{business_domain,key}',''),summary.domain_key),
               'name',coalesce(nullif(summary.graph #>> '{business_domain,name}',''),summary.name)),
           'value_system',jsonb_build_object(
               'key',summary.domain_key,'name',summary.name,'summary',summary.summary,
               'lifecycle',summary.lifecycle,'review_state',summary.family_review_state),
           'assertion_count',coalesce(assertions.assertion_count,0),
           'watchable_assertion_count',coalesce(assertions.watchable_assertion_count,0),
           'assertion_kinds',coalesce(assertions.assertion_kinds,'{}'),
           'needs_revision_count',coalesce(assertions.needs_revision_count,0),
           'needs_decision_count',coalesce(assertions.needs_decision_count,0),
           'automated_watch_coverage',CASE
               WHEN coalesce(assertions.watchable_assertion_count,0)=0 THEN 0.0
               ELSE round(coalesce(watches.watched_assertion_count,0)::numeric
                          /assertions.watchable_assertion_count::numeric,4)::double precision END,
           'watch_count',coalesce(watches.watch_count,0),
           'watch_states',coalesce(watches.watch_states,'{}'),
           'assertions',coalesce(assertions.assertions,'[]'),
           'watches',coalesce(watches.watches,'[]'),
           'diff',coalesce(revision.diff,'{}')) AS dossier,
       summary.updated_at,
       coalesce(assertions.needs_revision_count,0) AS needs_revision_count,
       coalesce(assertions.needs_decision_count,0) AS needs_decision_count
  FROM rvbbit.business_value_graph_summary summary
  LEFT JOIN assertion_rollup assertions USING (graph_id)
  LEFT JOIN watch_rollup watches USING (graph_id)
  LEFT JOIN rvbbit.business_semantic_revision_diffs revision USING (graph_id);

CREATE VIEW rvbbit.business_semantic_dossier_current AS
SELECT dossier.* FROM rvbbit.business_semantic_dossiers dossier
JOIN rvbbit.business_value_graph_current current_graph USING (graph_id);

COMMENT ON TABLE rvbbit.business_semantic_watch_observations IS
    'Append-only bounded evidence from executing one deterministic semantic watch.';
COMMENT ON TABLE rvbbit.business_semantic_assertion_states IS
    'Evidence-derived confidence and sticky review state; assertions themselves remain immutable hypotheses.';
COMMENT ON VIEW rvbbit.business_semantic_dossiers IS
    'Current evidence, confidence/review evolution, watch observations, and assertion-level semantic revision diff.';
