-- 0350: durable assertion ledger and validation-watch plans for Business
-- Topology. Graphs remain the receipt-backed source; this migration normalizes
-- their meaning so repeated runs can compare and validate individual claims
-- instead of treating each excavation as another prose document.

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_assertions (
    assertion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id uuid NOT NULL
        REFERENCES rvbbit.business_value_system_families(family_id) ON DELETE CASCADE,
    graph_id uuid NOT NULL
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE CASCADE,
    workflow_run_id uuid NOT NULL
        REFERENCES rvbbit.business_topology_workflow_runs(run_id) ON DELETE CASCADE,
    family_revision integer NOT NULL CHECK (family_revision>=1),
    assertion_key text NOT NULL,
    assertion_kind text NOT NULL,
    subject_key text NOT NULL,
    predicate text NOT NULL,
    object_value jsonb NOT NULL,
    fingerprint text NOT NULL,
    confidence double precision NOT NULL DEFAULT 0.5,
    provenance_kind text NOT NULL DEFAULT 'topology_inference',
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_assertion_key_check CHECK (
        nullif(btrim(assertion_key),'') IS NOT NULL
    ),
    CONSTRAINT business_semantic_assertion_kind_check CHECK (
        assertion_kind IN (
            'business_domain','concept','process','source_membership',
            'semantic_relationship','metric','analytical_question','gap'
        )
    ),
    CONSTRAINT business_semantic_assertion_provenance_check CHECK (
        provenance_kind IN (
            'topology_inference','catalog_evidence','metric_validation',
            'user_correction','policy','derived'
        )
    ),
    CONSTRAINT business_semantic_assertion_confidence_check CHECK (
        confidence>=0.0 AND confidence<=1.0
    ),
    CONSTRAINT business_semantic_assertion_json_check CHECK (
        jsonb_typeof(object_value)='object' AND jsonb_typeof(evidence)='object'
    ),
    CONSTRAINT business_semantic_assertion_graph_key UNIQUE (graph_id,assertion_key)
);

CREATE INDEX IF NOT EXISTS business_semantic_assertions_family_revision_idx
    ON rvbbit.business_semantic_assertions(family_id,family_revision,assertion_kind);
CREATE INDEX IF NOT EXISTS business_semantic_assertions_subject_idx
    ON rvbbit.business_semantic_assertions(assertion_kind,subject_key);

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_watch_specs (
    watch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id uuid NOT NULL
        REFERENCES rvbbit.business_value_system_families(family_id) ON DELETE CASCADE,
    graph_id uuid NOT NULL
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE CASCADE,
    assertion_id uuid NOT NULL
        REFERENCES rvbbit.business_semantic_assertions(assertion_id) ON DELETE CASCADE,
    watch_key text NOT NULL,
    watch_kind text NOT NULL,
    specification jsonb NOT NULL,
    latest_state text NOT NULL DEFAULT 'unknown',
    latest_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_watch_key_check CHECK (
        nullif(btrim(watch_key),'') IS NOT NULL
    ),
    CONSTRAINT business_semantic_watch_kind_check CHECK (
        watch_kind IN ('source_presence','join_coverage','metric_reconciliation')
    ),
    CONSTRAINT business_semantic_watch_state_check CHECK (
        latest_state IN ('passing','failing','unknown')
    ),
    CONSTRAINT business_semantic_watch_json_check CHECK (
        jsonb_typeof(specification)='object'
        AND jsonb_typeof(latest_evidence)='object'
    ),
    CONSTRAINT business_semantic_watch_graph_key UNIQUE (graph_id,watch_key)
);

CREATE INDEX IF NOT EXISTS business_semantic_watch_family_idx
    ON rvbbit.business_semantic_watch_specs(family_id,watch_kind,latest_state);

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_revision_diffs (
    graph_id uuid PRIMARY KEY
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE CASCADE,
    family_id uuid NOT NULL
        REFERENCES rvbbit.business_value_system_families(family_id) ON DELETE CASCADE,
    from_graph_id uuid
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE SET NULL,
    family_revision integer NOT NULL CHECK (family_revision>=1),
    added_count integer NOT NULL DEFAULT 0 CHECK (added_count>=0),
    removed_count integer NOT NULL DEFAULT 0 CHECK (removed_count>=0),
    changed_count integer NOT NULL DEFAULT 0 CHECK (changed_count>=0),
    unchanged_count integer NOT NULL DEFAULT 0 CHECK (unchanged_count>=0),
    diff jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_revision_diff_json_check CHECK (
        jsonb_typeof(diff)='object'
        AND diff->>'schema_version'='rvbbit.business-semantic.assertion-diff.v1'
    )
);

COMMENT ON TABLE rvbbit.business_semantic_assertions IS
    'Versioned, typed semantic claims extracted deterministically from each reconciled Business Value Graph; source event SQL is retained only by its owning graph and represented here by a fingerprint.';
COMMENT ON TABLE rvbbit.business_semantic_watch_specs IS
    'Declarative validation plans linked to watchable semantic assertions. Execution history and confidence evolution are a later slice.';
COMMENT ON TABLE rvbbit.business_semantic_revision_diffs IS
    'Assertion-level semantic delta between a graph revision and its predecessor; display and evidence changes are localized rather than presented as a new domain.';

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_capture_graph(
    p_graph_id uuid
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_graph record;
    v_item jsonb;
    v_value jsonb;
    v_evaluation jsonb;
    v_assertion_id uuid;
    v_key text;
    v_subject text;
    v_confidence double precision;
    v_from_graph_id uuid;
    v_added jsonb;
    v_removed jsonb;
    v_changed jsonb;
    v_added_count integer;
    v_removed_count integer;
    v_changed_count integer;
    v_unchanged_count integer;
BEGIN
    SELECT proposal.*,family.domain_key AS family_domain_key
      INTO v_graph
      FROM rvbbit.business_value_graph_proposals proposal
      JOIN rvbbit.business_value_system_families family USING (family_id)
     WHERE proposal.graph_id=p_graph_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'status','skipped','graph_id',p_graph_id,
            'reason','Graph has not been assigned to a semantic family.'
        );
    END IF;

    DELETE FROM rvbbit.business_semantic_assertions WHERE graph_id=p_graph_id;
    DELETE FROM rvbbit.business_semantic_revision_diffs WHERE graph_id=p_graph_id;

    v_value := jsonb_strip_nulls(jsonb_build_object(
        'domain_key',coalesce(nullif(v_graph.graph #>> '{business_domain,domain_key}',''),
                              nullif(v_graph.graph #>> '{business_domain,key}',''),
                              v_graph.family_domain_key),
        'name',coalesce(nullif(v_graph.graph #>> '{business_domain,name}',''),v_graph.name),
        'summary',coalesce(v_graph.graph #>> '{business_domain,summary}','')
    ));
    v_subject := v_value->>'domain_key';
    INSERT INTO rvbbit.business_semantic_assertions(
        family_id,graph_id,workflow_run_id,family_revision,assertion_key,
        assertion_kind,subject_key,predicate,object_value,fingerprint,
        confidence,provenance_kind,evidence
    ) VALUES (
        v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
        'domain:'||lower(v_subject),'business_domain',v_subject,'defines_domain',
        v_value,md5(v_value::text),0.75,'topology_inference',
        jsonb_build_object('graph_id',p_graph_id,'workflow_run_id',v_graph.workflow_run_id)
    );

    FOR v_item IN
        SELECT value FROM jsonb_array_elements(coalesce(v_graph.graph->'concepts','[]'::jsonb))
    LOOP
        v_subject := coalesce(nullif(v_item->>'concept_key',''),'unknown');
        v_value := jsonb_strip_nulls(jsonb_build_object(
            'concept_key',v_subject,'name',v_item->>'name','kind',v_item->>'kind',
            'description',v_item->>'description'
        ));
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            'concept:'||lower(v_subject),'concept',v_subject,'defines_concept',
            v_value,md5(v_value::text),0.7,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO UPDATE SET
            object_value=EXCLUDED.object_value,fingerprint=EXCLUDED.fingerprint,
            confidence=EXCLUDED.confidence,evidence=EXCLUDED.evidence;
    END LOOP;

    FOR v_item IN
        SELECT value FROM jsonb_array_elements(coalesce(v_graph.graph->'processes','[]'::jsonb))
    LOOP
        v_subject := coalesce(nullif(v_item->>'process_key',''),'unknown');
        v_value := jsonb_strip_nulls(jsonb_build_object(
            'process_key',v_subject,'name',v_item->>'name',
            'description',v_item->>'description',
            'concept_keys',coalesce((
                SELECT jsonb_agg(value ORDER BY value)
                  FROM jsonb_array_elements_text(coalesce(v_item->'concept_keys','[]'::jsonb)) value
            ),'[]'::jsonb)
        ));
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            'process:'||lower(v_subject),'process',v_subject,'defines_process',
            v_value,md5(v_value::text),0.7,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO UPDATE SET
            object_value=EXCLUDED.object_value,fingerprint=EXCLUDED.fingerprint,
            confidence=EXCLUDED.confidence,evidence=EXCLUDED.evidence;
    END LOOP;

    FOR v_item IN
        SELECT value FROM jsonb_array_elements(coalesce(v_graph.graph->'source_memberships','[]'::jsonb))
    LOOP
        v_subject := nullif(v_item->>'relation','');
        IF v_subject IS NULL THEN CONTINUE; END IF;
        v_confidence := least(1.0,greatest(0.0,coalesce((v_item->>'confidence')::double precision,0.6)));
        v_value := jsonb_strip_nulls(jsonb_build_object(
            'relation',v_subject,'role',coalesce(nullif(v_item->>'role',''),'context'),
            'concept_keys',coalesce((
                SELECT jsonb_agg(value ORDER BY value)
                  FROM jsonb_array_elements_text(coalesce(v_item->'concept_keys','[]'::jsonb)) value
            ),'[]'::jsonb),
            'rationale',v_item->>'rationale'
        ));
        v_key := 'source:'||lower(v_subject)||':'||lower(v_value->>'role');
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            v_key,'source_membership',v_subject,'participates_as',v_value,
            md5(v_value::text),v_confidence,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO UPDATE SET
            object_value=EXCLUDED.object_value,fingerprint=EXCLUDED.fingerprint,
            confidence=EXCLUDED.confidence,evidence=EXCLUDED.evidence;
    END LOOP;

    -- Older graphs predate semantic memberships. Keep every declared source
    -- accountable in the ledger without pretending that its role was known.
    FOR v_subject IN
        SELECT value FROM jsonb_array_elements_text(coalesce(v_graph.graph->'source_relations','[]'::jsonb))
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM rvbbit.business_semantic_assertions assertion
             WHERE assertion.graph_id=p_graph_id
               AND assertion.assertion_kind='source_membership'
               AND assertion.subject_key=v_subject
        ) THEN
            v_value := jsonb_build_object(
                'relation',v_subject,'role','context','concept_keys','[]'::jsonb,
                'rationale','Declared by the Value Graph; semantic role is not yet classified.'
            );
            INSERT INTO rvbbit.business_semantic_assertions(
                family_id,graph_id,workflow_run_id,family_revision,assertion_key,
                assertion_kind,subject_key,predicate,object_value,fingerprint,
                confidence,provenance_kind,evidence
            ) VALUES (
                v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
                'source:'||lower(v_subject)||':context','source_membership',v_subject,
                'participates_as',v_value,md5(v_value::text),0.4,'derived',
                jsonb_build_object('graph_id',p_graph_id,'legacy_membership',true)
            );
        END IF;
    END LOOP;

    FOR v_item IN
        SELECT value FROM jsonb_array_elements(coalesce(v_graph.graph->'semantic_relationships','[]'::jsonb))
    LOOP
        v_subject := coalesce(nullif(v_item->>'left_relation',''),
                              nullif(v_item->>'subject_concept_key',''),'unknown');
        v_confidence := least(1.0,greatest(0.0,coalesce((v_item->>'confidence')::double precision,0.6)));
        v_value := jsonb_strip_nulls(jsonb_build_object(
            'relationship',v_item->>'relationship',
            'left_relation',v_item->>'left_relation','right_relation',v_item->>'right_relation',
            'subject_concept_key',v_item->>'subject_concept_key',
            'object_concept_key',v_item->>'object_concept_key',
            'left_columns',coalesce(v_item->'left_columns','[]'::jsonb),
            'right_columns',coalesce(v_item->'right_columns','[]'::jsonb),
            'rationale',v_item->>'rationale'
        ));
        v_key := 'relationship:'||lower(coalesce(v_item->>'relationship','related_to'))
                 ||':'||md5(jsonb_build_object(
                     'left_relation',lower(coalesce(v_item->>'left_relation','')),
                     'right_relation',lower(coalesce(v_item->>'right_relation','')),
                     'subject',lower(coalesce(v_item->>'subject_concept_key','')),
                     'object',lower(coalesce(v_item->>'object_concept_key','')),
                     'left_columns',coalesce(v_item->'left_columns','[]'::jsonb),
                     'right_columns',coalesce(v_item->'right_columns','[]'::jsonb)
                 )::text);
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            v_key,'semantic_relationship',v_subject,
            coalesce(nullif(v_item->>'relationship',''),'related_to'),v_value,
            md5(v_value::text),v_confidence,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO UPDATE SET
            object_value=EXCLUDED.object_value,fingerprint=EXCLUDED.fingerprint,
            confidence=EXCLUDED.confidence,evidence=EXCLUDED.evidence;
    END LOOP;

    FOR v_item IN
        SELECT value FROM jsonb_array_elements(coalesce(v_graph.graph->'metrics','[]'::jsonb))
    LOOP
        v_subject := coalesce(nullif(v_item->>'metric_key',''),'unknown');
        SELECT value INTO v_evaluation
          FROM jsonb_array_elements(coalesce(v_graph.receipt #> '{evaluation,metrics}','[]'::jsonb))
         WHERE value->>'metric_key'=v_subject LIMIT 1;
        v_confidence := least(1.0,greatest(0.0,coalesce((v_item->>'confidence')::double precision,0.5)));
        v_value := jsonb_strip_nulls(jsonb_build_object(
            'metric_key',v_subject,'name',v_item->>'name',
            'description',v_item->>'description','metric_kind',v_item->>'metric_kind',
            'unit',v_item->>'unit','formula',v_item->>'formula','grain',v_item->>'grain',
            'time_semantics',v_item->>'time_semantics',
            'event_sql_fingerprint',md5(coalesce(v_item->>'event_sql','')),
            'dimensions',coalesce((
                SELECT jsonb_agg(value ORDER BY lower(coalesce(value->>'key',value->>'name')))
                  FROM jsonb_array_elements(coalesce(v_item->'dimensions','[]'::jsonb)) value
            ),'[]'::jsonb),
            'primary_dimension_key',v_item->>'primary_dimension_key'
        ));
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            'metric:'||lower(v_subject),'metric',v_subject,'defines_metric',v_value,
            md5(v_value::text),v_confidence,
            CASE WHEN v_evaluation IS NULL THEN 'topology_inference' ELSE 'metric_validation' END,
            jsonb_strip_nulls(jsonb_build_object(
                'graph_id',p_graph_id,
                'validated',v_evaluation IS NOT NULL,
                'evaluation',CASE WHEN v_evaluation IS NULL THEN NULL
                                  ELSE v_evaluation-'series'-'changes'-'cube_recommendation' END,
                'revalidation_error',v_graph.receipt #>> '{evaluation,revalidation_error}'
            ))
        ) ON CONFLICT (graph_id,assertion_key) DO UPDATE SET
            object_value=EXCLUDED.object_value,fingerprint=EXCLUDED.fingerprint,
            confidence=EXCLUDED.confidence,provenance_kind=EXCLUDED.provenance_kind,
            evidence=EXCLUDED.evidence;
        v_evaluation := NULL;
    END LOOP;

    FOR v_subject IN
        SELECT value FROM jsonb_array_elements_text(coalesce(v_graph.graph->'analytical_questions','[]'::jsonb))
    LOOP
        v_value := jsonb_build_object('question',btrim(v_subject));
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            'question:'||md5(lower(btrim(v_subject))),'analytical_question',btrim(v_subject),
            'enables_question',v_value,md5(v_value::text),0.6,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO NOTHING;
    END LOOP;

    FOR v_subject IN
        SELECT value FROM jsonb_array_elements_text(coalesce(v_graph.graph->'gaps','[]'::jsonb))
    LOOP
        v_value := jsonb_build_object('gap',btrim(v_subject));
        INSERT INTO rvbbit.business_semantic_assertions(
            family_id,graph_id,workflow_run_id,family_revision,assertion_key,
            assertion_kind,subject_key,predicate,object_value,fingerprint,
            confidence,provenance_kind,evidence
        ) VALUES (
            v_graph.family_id,p_graph_id,v_graph.workflow_run_id,v_graph.family_revision,
            'gap:'||md5(lower(btrim(v_subject))),'gap',btrim(v_subject),
            'records_limitation',v_value,md5(v_value::text),0.75,'topology_inference',
            jsonb_build_object('graph_id',p_graph_id)
        ) ON CONFLICT (graph_id,assertion_key) DO NOTHING;
    END LOOP;

    INSERT INTO rvbbit.business_semantic_watch_specs(
        family_id,graph_id,assertion_id,watch_key,watch_kind,specification,
        latest_state,latest_evidence
    )
    SELECT assertion.family_id,assertion.graph_id,assertion.assertion_id,
           'source-presence:'||lower(assertion.subject_key),'source_presence',
           jsonb_build_object(
               'schema_version','rvbbit.business-semantic.watch-spec.v1',
               'relation',assertion.subject_key,
               'trigger','catalog_observation',
               'checks',jsonb_build_array('relation_exists','relation_allowed')
           ),
           CASE WHEN rvbbit._safe_regclass(assertion.subject_key) IS NOT NULL
                     AND rvbbit._business_topology_relation_allowed(
                         split_part(assertion.subject_key,'.',1),
                         substr(assertion.subject_key,strpos(assertion.subject_key,'.')+1)
                     ) THEN 'passing' ELSE 'failing' END,
           jsonb_build_object('observed_at',clock_timestamp(),'source','capture')
      FROM rvbbit.business_semantic_assertions assertion
     WHERE assertion.graph_id=p_graph_id
       AND assertion.assertion_kind='source_membership';

    INSERT INTO rvbbit.business_semantic_watch_specs(
        family_id,graph_id,assertion_id,watch_key,watch_kind,specification,
        latest_state,latest_evidence
    )
    SELECT assertion.family_id,assertion.graph_id,assertion.assertion_id,
           'join-coverage:'||substr(assertion.assertion_key,14),'join_coverage',
           jsonb_build_object(
               'schema_version','rvbbit.business-semantic.watch-spec.v1',
               'left_relation',assertion.object_value->>'left_relation',
               'right_relation',assertion.object_value->>'right_relation',
               'left_columns',assertion.object_value->'left_columns',
               'right_columns',assertion.object_value->'right_columns',
               'trigger','mirror_or_catalog_observation',
               'sample_limit',50000,
               'checks',jsonb_build_array('left_match_rate','right_key_uniqueness','fanout')
           ),'unknown',
           jsonb_build_object(
               'reason','The exact join is modeled, but bounded coverage and fanout have not been observed yet.'
           )
      FROM rvbbit.business_semantic_assertions assertion
     WHERE assertion.graph_id=p_graph_id
       AND assertion.assertion_kind='semantic_relationship'
       AND assertion.predicate='joins_to'
       AND jsonb_array_length(coalesce(assertion.object_value->'left_columns','[]'::jsonb))>0
       AND jsonb_array_length(coalesce(assertion.object_value->'right_columns','[]'::jsonb))>0;

    INSERT INTO rvbbit.business_semantic_watch_specs(
        family_id,graph_id,assertion_id,watch_key,watch_kind,specification,
        latest_state,latest_evidence
    )
    SELECT assertion.family_id,assertion.graph_id,assertion.assertion_id,
           'metric-reconciliation:'||lower(assertion.subject_key),'metric_reconciliation',
           jsonb_build_object(
               'schema_version','rvbbit.business-semantic.watch-spec.v1',
               'metric_key',assertion.subject_key,
               'event_sql_fingerprint',assertion.object_value->>'event_sql_fingerprint',
               'trigger','mirror_or_topology_refresh','timeout_seconds',60,
               'checks',jsonb_build_array('event_projection_executes','dimensions_reconcile')
           ),
           CASE
             WHEN coalesce((assertion.evidence->>'validated')::boolean,false)
              AND coalesce((v_graph.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
             THEN 'passing'
             WHEN assertion.evidence ? 'revalidation_error' THEN 'failing'
             ELSE 'unknown'
           END,
           coalesce(assertion.evidence->'evaluation','{}'::jsonb)
      FROM rvbbit.business_semantic_assertions assertion
     WHERE assertion.graph_id=p_graph_id
       AND assertion.assertion_kind='metric';

    SELECT coalesce(v_graph.predecessor_graph_id,(
               SELECT prior.graph_id
                 FROM rvbbit.business_value_graph_proposals prior
                WHERE prior.family_id=v_graph.family_id
                  AND prior.graph_id<>p_graph_id
                  AND prior.family_revision<v_graph.family_revision
                ORDER BY prior.family_revision DESC,prior.proposed_at DESC
                LIMIT 1
           )) INTO v_from_graph_id;

    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'assertion_key',current.assertion_key,'kind',current.assertion_kind,
               'subject_key',current.subject_key,'predicate',current.predicate,
               'value',current.object_value,'confidence',current.confidence
           ) ORDER BY current.assertion_kind,current.assertion_key),'[]'::jsonb)
      INTO v_added
      FROM rvbbit.business_semantic_assertions current
     WHERE current.graph_id=p_graph_id
       AND NOT EXISTS (
           SELECT 1 FROM rvbbit.business_semantic_assertions prior
            WHERE prior.graph_id=v_from_graph_id
              AND prior.assertion_key=current.assertion_key
       );
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'assertion_key',prior.assertion_key,'kind',prior.assertion_kind,
               'subject_key',prior.subject_key,'predicate',prior.predicate,
               'value',prior.object_value,'confidence',prior.confidence
           ) ORDER BY prior.assertion_kind,prior.assertion_key),'[]'::jsonb)
      INTO v_removed
      FROM rvbbit.business_semantic_assertions prior
     WHERE prior.graph_id=v_from_graph_id
       AND NOT EXISTS (
           SELECT 1 FROM rvbbit.business_semantic_assertions current
            WHERE current.graph_id=p_graph_id
              AND current.assertion_key=prior.assertion_key
       );
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'assertion_key',current.assertion_key,'kind',current.assertion_kind,
               'subject_key',current.subject_key,'predicate',current.predicate,
               'before',prior.object_value,'after',current.object_value,
               'before_confidence',prior.confidence,
               'after_confidence',current.confidence
           ) ORDER BY current.assertion_kind,current.assertion_key),'[]'::jsonb)
      INTO v_changed
      FROM rvbbit.business_semantic_assertions current
      JOIN rvbbit.business_semantic_assertions prior
        ON prior.graph_id=v_from_graph_id
       AND prior.assertion_key=current.assertion_key
     WHERE current.graph_id=p_graph_id
       AND current.fingerprint<>prior.fingerprint;
    SELECT count(*)::integer INTO v_unchanged_count
      FROM rvbbit.business_semantic_assertions current
      JOIN rvbbit.business_semantic_assertions prior
        ON prior.graph_id=v_from_graph_id
       AND prior.assertion_key=current.assertion_key
     WHERE current.graph_id=p_graph_id
       AND current.fingerprint=prior.fingerprint;
    v_added_count := jsonb_array_length(v_added);
    v_removed_count := jsonb_array_length(v_removed);
    v_changed_count := jsonb_array_length(v_changed);

    INSERT INTO rvbbit.business_semantic_revision_diffs(
        graph_id,family_id,from_graph_id,family_revision,
        added_count,removed_count,changed_count,unchanged_count,diff
    ) VALUES (
        p_graph_id,v_graph.family_id,v_from_graph_id,v_graph.family_revision,
        v_added_count,v_removed_count,v_changed_count,v_unchanged_count,
        jsonb_build_object(
            'schema_version','rvbbit.business-semantic.assertion-diff.v1',
            'change_kind',CASE
                WHEN v_from_graph_id IS NULL THEN 'baseline'
                WHEN v_added_count+v_removed_count+v_changed_count=0 THEN 'no_change'
                ELSE 'changed'
            END,
            'material_change',v_added_count+v_removed_count+v_changed_count>0,
            'from_graph_id',v_from_graph_id,'to_graph_id',p_graph_id,
            'added',v_added,'removed',v_removed,'changed',v_changed,
            'unchanged_count',v_unchanged_count
        )
    );

    RETURN jsonb_build_object(
        'status','captured','graph_id',p_graph_id,'family_id',v_graph.family_id,
        'family_revision',v_graph.family_revision,
        'assertions',(SELECT count(*) FROM rvbbit.business_semantic_assertions WHERE graph_id=p_graph_id),
        'watches',(SELECT count(*) FROM rvbbit.business_semantic_watch_specs WHERE graph_id=p_graph_id),
        'diff',jsonb_build_object(
            'added',v_added_count,'removed',v_removed_count,
            'changed',v_changed_count,'unchanged',v_unchanged_count
        )
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_semantic_capture_graph(uuid) IS
    'Deterministically normalize one reconciled Value Graph into versioned assertions, watch specifications, and an assertion-level predecessor diff.';

CREATE OR REPLACE FUNCTION rvbbit._business_semantic_capture_graph_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.family_id IS NOT NULL AND NEW.family_revision IS NOT NULL THEN
        PERFORM rvbbit.business_semantic_capture_graph(NEW.graph_id);
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_semantic_capture_graph_insert
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_semantic_capture_graph_insert
AFTER INSERT ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_capture_graph_trigger();

DROP TRIGGER IF EXISTS business_semantic_capture_graph_update
    ON rvbbit.business_value_graph_proposals;
CREATE TRIGGER business_semantic_capture_graph_update
AFTER UPDATE OF family_id,family_revision,predecessor_graph_id,graph,receipt
ON rvbbit.business_value_graph_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_capture_graph_trigger();

-- Backfill in family order so predecessor assertions exist before their diff.
DO $backfill$
DECLARE
    v_graph_id uuid;
BEGIN
    FOR v_graph_id IN
        SELECT graph_id
          FROM rvbbit.business_value_graph_proposals
         WHERE family_id IS NOT NULL AND family_revision IS NOT NULL
         ORDER BY family_id,family_revision,proposed_at,graph_id
    LOOP
        PERFORM rvbbit.business_semantic_capture_graph(v_graph_id);
    END LOOP;
END
$backfill$;

DROP VIEW IF EXISTS rvbbit.business_semantic_dossier_current;
DROP VIEW IF EXISTS rvbbit.business_semantic_dossiers;
CREATE OR REPLACE VIEW rvbbit.business_semantic_dossiers AS
WITH assertion_rollup AS (
    SELECT assertion.graph_id,
           count(*)::integer AS assertion_count,
           count(*) FILTER (WHERE
               assertion_kind IN ('source_membership','metric')
               OR (
                   assertion_kind='semantic_relationship'
                   AND predicate='joins_to'
                   AND jsonb_array_length(coalesce(object_value->'left_columns','[]'::jsonb))>0
                   AND jsonb_array_length(coalesce(object_value->'right_columns','[]'::jsonb))>0
               )
           )::integer AS watchable_assertion_count,
           jsonb_object_agg(assertion_kind,kind_count ORDER BY assertion_kind) AS assertion_kinds,
           jsonb_agg(jsonb_build_object(
               'assertion_id',assertion_id,'assertion_key',assertion_key,
               'kind',assertion_kind,'subject_key',subject_key,'predicate',predicate,
               'value',object_value,'confidence',confidence,
               'provenance',provenance_kind,'evidence',evidence
           ) ORDER BY assertion_kind,assertion_key) AS assertions
      FROM (
          SELECT assertion.*,
                 count(*) OVER (PARTITION BY graph_id,assertion_kind)::integer AS kind_count
            FROM rvbbit.business_semantic_assertions assertion
      ) assertion
     GROUP BY assertion.graph_id
), watch_rollup AS (
    SELECT watch.graph_id,count(*)::integer AS watch_count,
           count(DISTINCT assertion_id)::integer AS watched_assertion_count,
           jsonb_object_agg(latest_state,state_count ORDER BY latest_state) AS watch_states,
           jsonb_agg(jsonb_build_object(
               'watch_id',watch_id,'watch_key',watch_key,'kind',watch_kind,
               'assertion_id',assertion_id,'state',latest_state,
               'enabled',enabled,'specification',specification,
               'evidence',latest_evidence
           ) ORDER BY watch_kind,watch_key) AS watches
      FROM (
          SELECT watch.*,
                 count(*) OVER (PARTITION BY graph_id,latest_state)::integer AS state_count
            FROM rvbbit.business_semantic_watch_specs watch
      ) watch
     GROUP BY watch.graph_id
)
SELECT summary.graph_id,summary.workflow_run_id,summary.family_id,
       summary.family_revision,summary.predecessor_graph_id,
       coalesce(nullif(summary.graph #>> '{business_domain,domain_key}',''),
                nullif(summary.graph #>> '{business_domain,key}',''),summary.domain_key)
           AS business_domain_key,
       coalesce(nullif(summary.graph #>> '{business_domain,name}',''),summary.name)
           AS business_domain_name,
       summary.domain_key AS value_system_key,summary.name AS value_system_name,
       summary.summary,summary.lifecycle,summary.family_review_state,
       coalesce(assertions.assertion_count,0) AS assertion_count,
       coalesce(assertions.watchable_assertion_count,0) AS watchable_assertion_count,
       coalesce(assertions.assertion_kinds,'{}'::jsonb) AS assertion_kinds,
       coalesce(watches.watch_count,0) AS watch_count,
       coalesce(watches.watched_assertion_count,0) AS watched_assertion_count,
       coalesce(watches.watch_states,'{}'::jsonb) AS watch_states,
       CASE WHEN coalesce(assertions.watchable_assertion_count,0)=0 THEN 0.0
            ELSE round(coalesce(watches.watched_assertion_count,0)::numeric
                       /assertions.watchable_assertion_count::numeric,4)::double precision END
           AS automated_watch_coverage,
       coalesce(assertions.assertions,'[]'::jsonb) AS assertions,
       coalesce(watches.watches,'[]'::jsonb) AS watches,
       coalesce(revision.diff,jsonb_build_object(
           'schema_version','rvbbit.business-semantic.assertion-diff.v1',
           'change_kind','unavailable','material_change',false,
           'added','[]'::jsonb,'removed','[]'::jsonb,'changed','[]'::jsonb,
           'unchanged_count',0
       )) AS assertion_diff,
       jsonb_build_object(
           'schema_version','rvbbit.business-semantic.dossier.v1',
           'family_id',summary.family_id,'graph_id',summary.graph_id,
           'workflow_run_id',summary.workflow_run_id,
           'revision',summary.family_revision,
           'predecessor_graph_id',summary.predecessor_graph_id,
           'business_domain',jsonb_build_object(
               'key',coalesce(nullif(summary.graph #>> '{business_domain,domain_key}',''),
                              nullif(summary.graph #>> '{business_domain,key}',''),summary.domain_key),
               'name',coalesce(nullif(summary.graph #>> '{business_domain,name}',''),summary.name)
           ),
           'value_system',jsonb_build_object(
               'key',summary.domain_key,'name',summary.name,'summary',summary.summary,
               'lifecycle',summary.lifecycle,'review_state',summary.family_review_state
           ),
           'assertion_count',coalesce(assertions.assertion_count,0),
           'watchable_assertion_count',coalesce(assertions.watchable_assertion_count,0),
           'assertion_kinds',coalesce(assertions.assertion_kinds,'{}'::jsonb),
           'automated_watch_coverage',CASE
               WHEN coalesce(assertions.watchable_assertion_count,0)=0 THEN 0.0
               ELSE round(coalesce(watches.watched_assertion_count,0)::numeric
                          /assertions.watchable_assertion_count::numeric,4)::double precision END,
           'watch_count',coalesce(watches.watch_count,0),
           'watch_states',coalesce(watches.watch_states,'{}'::jsonb),
           'assertions',coalesce(assertions.assertions,'[]'::jsonb),
           'watches',coalesce(watches.watches,'[]'::jsonb),
           'diff',coalesce(revision.diff,'{}'::jsonb)
       ) AS dossier,
       summary.updated_at
  FROM rvbbit.business_value_graph_summary summary
  LEFT JOIN assertion_rollup assertions USING (graph_id)
  LEFT JOIN watch_rollup watches USING (graph_id)
  LEFT JOIN rvbbit.business_semantic_revision_diffs revision USING (graph_id);

CREATE OR REPLACE VIEW rvbbit.business_semantic_dossier_current AS
SELECT dossier.*
  FROM rvbbit.business_semantic_dossiers dossier
  JOIN rvbbit.business_value_graph_current current_graph USING (graph_id);

COMMENT ON VIEW rvbbit.business_semantic_dossiers IS
    'Bounded domain dossier assembled from typed assertions, automated watch coverage, and the assertion-level predecessor diff for every graph revision.';
COMMENT ON VIEW rvbbit.business_semantic_dossier_current IS
    'Current canonical or provisional semantic dossier for each durable Value System family.';
