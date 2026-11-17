-- 0353: compare compact semantic dossiers across Value System families.
-- A relationship proposal is evidence, not a destructive merge.  Human
-- decisions are separate append-only receipts so both the inference and its
-- disposition remain inspectable after graph or family revisions.

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_family_relationship_proposals (
    relationship_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow_run_id uuid NOT NULL,
    source_graph_id uuid NOT NULL,
    target_graph_id uuid NOT NULL,
    relationship_kind text NOT NULL,
    confidence double precision NOT NULL,
    proposed_business_domain_key text,
    proposed_business_domain_name text,
    proposed_family_key text,
    proposed_family_name text,
    rationale text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
    candidate_fingerprint text NOT NULL UNIQUE,
    adjudication_version text NOT NULL,
    proposed_by text NOT NULL DEFAULT current_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_semantic_family_relationship_distinct_check
        CHECK (source_graph_id<>target_graph_id),
    CONSTRAINT business_semantic_family_relationship_kind_check CHECK (
        relationship_kind IN (
            'same_family','extension','parallel_population',
            'alternative_definition','material_contradiction',
            'distinct_family','abstain'
        )
    ),
    CONSTRAINT business_semantic_family_relationship_confidence_check
        CHECK (confidence>=0 AND confidence<=1),
    CONSTRAINT business_semantic_family_relationship_json_check CHECK (
        jsonb_typeof(evidence)='object' AND jsonb_typeof(model_receipt)='object'
    ),
    CONSTRAINT business_semantic_family_relationship_rationale_check
        CHECK (length(btrim(rationale))>0)
);

CREATE INDEX IF NOT EXISTS business_semantic_family_relationship_source_idx
    ON rvbbit.business_semantic_family_relationship_proposals(
        source_graph_id,proposed_at DESC
    );
CREATE INDEX IF NOT EXISTS business_semantic_family_relationship_target_idx
    ON rvbbit.business_semantic_family_relationship_proposals(
        target_graph_id,proposed_at DESC
    );
CREATE INDEX IF NOT EXISTS business_semantic_family_relationship_run_idx
    ON rvbbit.business_semantic_family_relationship_proposals(
        workflow_run_id,proposed_at DESC
    );

CREATE TABLE IF NOT EXISTS rvbbit.business_semantic_family_relationship_decisions (
    decision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    relationship_id bigint NOT NULL,
    decision text NOT NULL CHECK (decision IN ('accepted','rejected')),
    note text,
    decided_by text NOT NULL DEFAULT current_user,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS business_semantic_family_relationship_decision_idx
    ON rvbbit.business_semantic_family_relationship_decisions(
        relationship_id,decided_at DESC,decision_id DESC
    );

CREATE TRIGGER business_semantic_family_relationship_proposal_immutable
BEFORE UPDATE OR DELETE ON rvbbit.business_semantic_family_relationship_proposals
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_observation_immutable();

CREATE TRIGGER business_semantic_family_relationship_decision_immutable
BEFORE UPDATE OR DELETE ON rvbbit.business_semantic_family_relationship_decisions
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_semantic_observation_immutable();

CREATE OR REPLACE FUNCTION rvbbit.business_semantic_decide_family_relationship(
    p_relationship_id bigint,
    p_decision text,
    p_note text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_decision_id bigint;
BEGIN
    IF p_decision NOT IN ('accepted','rejected') THEN
        RAISE EXCEPTION 'family relationship decision must be accepted or rejected';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM rvbbit.business_semantic_family_relationship_proposals
         WHERE relationship_id=p_relationship_id
    ) THEN
        RAISE EXCEPTION 'unknown family relationship proposal %',p_relationship_id;
    END IF;
    INSERT INTO rvbbit.business_semantic_family_relationship_decisions(
        relationship_id,decision,note,decided_by
    ) VALUES (
        p_relationship_id,p_decision,nullif(btrim(p_note),''),
        coalesce(nullif(current_setting('rvbbit.request_user',true),''),current_user)
    ) RETURNING decision_id INTO v_decision_id;
    RETURN jsonb_build_object(
        'status','recorded','relationship_id',p_relationship_id,
        'decision_id',v_decision_id,'decision',p_decision
    );
END
$fn$;

CREATE OR REPLACE VIEW rvbbit.business_semantic_family_relationship_current AS
WITH latest_decision AS (
    SELECT DISTINCT ON (decision.relationship_id)
           decision.relationship_id,decision.decision,decision.note,
           decision.decided_by,decision.decided_at
      FROM rvbbit.business_semantic_family_relationship_decisions decision
     ORDER BY decision.relationship_id,decision.decided_at DESC,decision.decision_id DESC
)
SELECT proposal.relationship_id,proposal.workflow_run_id,
       proposal.source_graph_id,source.family_id AS source_family_id,
       source.domain_key AS source_value_system_key,
       source.name AS source_value_system_name,
       coalesce(nullif(source.graph #>> '{business_domain,domain_key}',''),
                nullif(source.graph #>> '{business_domain,key}',''),source.domain_key)
           AS source_business_domain_key,
       coalesce(nullif(source.graph #>> '{business_domain,name}',''),source.name)
           AS source_business_domain_name,
       proposal.target_graph_id,target.family_id AS target_family_id,
       target.domain_key AS target_value_system_key,
       target.name AS target_value_system_name,
       coalesce(nullif(target.graph #>> '{business_domain,domain_key}',''),
                nullif(target.graph #>> '{business_domain,key}',''),target.domain_key)
           AS target_business_domain_key,
       coalesce(nullif(target.graph #>> '{business_domain,name}',''),target.name)
           AS target_business_domain_name,
       proposal.relationship_kind,proposal.confidence,
       proposal.proposed_business_domain_key,proposal.proposed_business_domain_name,
       proposal.proposed_family_key,proposal.proposed_family_name,
       proposal.rationale,proposal.evidence,proposal.model_receipt,
       proposal.candidate_fingerprint,proposal.adjudication_version,
       coalesce(decision.decision,'proposed') AS review_state,
       decision.note AS review_note,decision.decided_by,decision.decided_at,
       proposal.proposed_by,proposal.proposed_at,
       jsonb_strip_nulls(jsonb_build_object(
           'relationship_id',proposal.relationship_id,
           'workflow_run_id',proposal.workflow_run_id,
           'source_graph_id',proposal.source_graph_id,
           'source_family_id',source.family_id,
           'source_value_system_key',source.domain_key,
           'source_value_system_name',source.name,
           'source_business_domain_key',coalesce(
               nullif(source.graph #>> '{business_domain,domain_key}',''),
               nullif(source.graph #>> '{business_domain,key}',''),source.domain_key),
           'source_business_domain_name',coalesce(
               nullif(source.graph #>> '{business_domain,name}',''),source.name),
           'target_graph_id',proposal.target_graph_id,
           'target_family_id',target.family_id,
           'target_value_system_key',target.domain_key,
           'target_value_system_name',target.name,
           'target_business_domain_key',coalesce(
               nullif(target.graph #>> '{business_domain,domain_key}',''),
               nullif(target.graph #>> '{business_domain,key}',''),target.domain_key),
           'target_business_domain_name',coalesce(
               nullif(target.graph #>> '{business_domain,name}',''),target.name),
           'relationship_kind',proposal.relationship_kind,
           'confidence',proposal.confidence,
           'proposed_business_domain_key',proposal.proposed_business_domain_key,
           'proposed_business_domain_name',proposal.proposed_business_domain_name,
           'proposed_family_key',proposal.proposed_family_key,
           'proposed_family_name',proposal.proposed_family_name,
           'rationale',proposal.rationale,'evidence',proposal.evidence,
           'candidate_fingerprint',proposal.candidate_fingerprint,
           'adjudication_version',proposal.adjudication_version,
           'review_state',coalesce(decision.decision,'proposed'),
           'review_note',decision.note,'decided_by',decision.decided_by,
           'decided_at',decision.decided_at,'proposed_by',proposal.proposed_by,
           'proposed_at',proposal.proposed_at
       )) AS relationship
  FROM rvbbit.business_semantic_family_relationship_proposals proposal
  JOIN rvbbit.business_value_graph_proposals source
    ON source.graph_id=proposal.source_graph_id
  JOIN rvbbit.business_value_graph_proposals target
    ON target.graph_id=proposal.target_graph_id
  LEFT JOIN latest_decision decision USING (relationship_id);

COMMENT ON TABLE rvbbit.business_semantic_family_relationship_proposals IS
    'Immutable compact-dossier adjudications between Value System families; proposals never merge or rewrite a family by themselves.';
COMMENT ON VIEW rvbbit.business_semantic_family_relationship_current IS
    'Family relationship proposals with stable family identity, counterpart labels, evidence, and the latest append-only human decision.';
