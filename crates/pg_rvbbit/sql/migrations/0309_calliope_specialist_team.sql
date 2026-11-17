-- 0309: expand Calliope's persistent specialist team and give autonomous
-- profiles a compact, durable feedback channel into System Learning.

INSERT INTO rvbbit.calliope_specialists (
    id,specialist_key,profile_name,display_name,description,capabilities,
    default_schedule,managed
) VALUES
(
    '0a514b95-90e8-52cc-a2bc-e5f78523098d'::uuid,
    'semantic-architect','semantic-architect','Semantic Architect',
    'Tends governed metrics, cubes, value systems, and the business-semantic model learned from actual usage.',
    '["metric_architecture","cube_architecture","value_systems","business_topology","semantic_reuse"]'::jsonb,
    '41 3 * * *',true
),
(
    'b51ec9b2-2f8d-5d2e-ae23-c5980a7670e4'::uuid,
    'signal-watch','signal-watch','Signal Watch',
    'Finds and investigates material business changes while suppressing routine statistical noise.',
    '["change_detection","anomaly_investigation","business_signals","metric_monitoring","briefing"]'::jsonb,
    '15 11 * * *',true
),
(
    '2fde8fd2-dbba-5675-9927-9534e78128c2'::uuid,
    'knowledge-gardener','knowledge-gardener','Knowledge Gardener',
    'Tends Company Brain coverage, freshness, identity linkage, duplication, and source quality.',
    '["company_brain","document_quality","entity_linkage","source_coverage","knowledge_freshness"]'::jsonb,
    '53 4 * * *',true
),
(
    'd866e61e-3653-5e67-b65a-6a0378772deb'::uuid,
    'integration-scout','integration-scout','Integration Scout',
    'Discovers, synthesizes, installs, tests, and remembers external service and MCP integrations.',
    '["capability_discovery","mcp_discovery","integration_install","auth_diagnosis","adapter_synthesis"]'::jsonb,
    NULL,true
),
(
    'c7ca1be6-5d65-5336-834b-b9184b3d066d'::uuid,
    'analytical-skeptic','analytical-skeptic','Analytical Skeptic',
    'Adversarially reviews analytical claims, governed definitions, evidence, and visual communication.',
    '["analytical_review","metric_validation","join_audit","evidence_challenge","visual_critique"]'::jsonb,
    NULL,true
)
ON CONFLICT (specialist_key) DO UPDATE SET
    profile_name=EXCLUDED.profile_name,
    display_name=EXCLUDED.display_name,
    description=EXCLUDED.description,
    capabilities=EXCLUDED.capabilities,
    default_schedule=EXCLUDED.default_schedule,
    status='active',managed=true,updated_at=clock_timestamp();

CREATE TABLE IF NOT EXISTS rvbbit.calliope_specialist_findings (
    id bigserial PRIMARY KEY,
    specialist_id uuid NOT NULL REFERENCES rvbbit.calliope_specialists(id),
    source_run_id uuid REFERENCES rvbbit.calliope_specialist_runs(id) ON DELETE SET NULL,
    subject text NOT NULL,
    finding_kind text NOT NULL DEFAULT 'observation',
    status text NOT NULL DEFAULT 'open',
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    recommendation text,
    dedupe_key text,
    next_review_at timestamptz,
    resolved_at timestamptz,
    resolution text,
    actor text NOT NULL DEFAULT session_user,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT calliope_specialist_findings_kind_check CHECK (
        finding_kind IN ('observation','proposal','warning','review','action','no_change')
    ),
    CONSTRAINT calliope_specialist_findings_status_check CHECK (
        status IN ('open','monitoring','applied','resolved','dismissed')
    ),
    CONSTRAINT calliope_specialist_findings_evidence_check
        CHECK (jsonb_typeof(evidence)='object'),
    CONSTRAINT calliope_specialist_findings_subject_check
        CHECK (length(btrim(subject)) BETWEEN 1 AND 500),
    CONSTRAINT calliope_specialist_findings_summary_check
        CHECK (length(btrim(summary)) BETWEEN 1 AND 12000)
);

CREATE INDEX IF NOT EXISTS calliope_specialist_findings_status_idx
    ON rvbbit.calliope_specialist_findings(status,updated_at DESC);
CREATE INDEX IF NOT EXISTS calliope_specialist_findings_specialist_idx
    ON rvbbit.calliope_specialist_findings(specialist_id,updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_specialist_findings_open_dedupe_idx
    ON rvbbit.calliope_specialist_findings(specialist_id,dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('open','monitoring');

COMMENT ON TABLE rvbbit.calliope_specialist_findings IS
    'Compact durable findings emitted by delegated and autonomous Calliope specialists; full private working memory remains in each Hermes Hindsight bank.';

CREATE OR REPLACE VIEW rvbbit.system_learning_collaborative_items AS
SELECT uri,title,content_hash,occurred_at,body,props
  FROM rvbbit.system_learning_stewarded_items
UNION ALL
SELECT
    'rvbbit:calliope-specialist-finding:' || finding.id::text AS uri,
    specialist.display_name || ': ' || finding.subject AS title,
    md5(finding.summary || finding.evidence::text || coalesce(finding.recommendation,'')) AS content_hash,
    finding.updated_at AS occurred_at,
    concat_ws(E'\n',
        'Calliope specialist finding.',
        'Specialist: ' || specialist.display_name,
        'Subject: ' || finding.subject,
        'Kind: ' || finding.finding_kind,
        'Status: ' || finding.status,
        'Summary: ' || finding.summary,
        CASE WHEN finding.recommendation IS NOT NULL
             THEN 'Recommendation: ' || finding.recommendation END,
        CASE WHEN finding.next_review_at IS NOT NULL
             THEN 'Next review: ' || finding.next_review_at::text END,
        CASE WHEN finding.evidence <> '{}'::jsonb
             THEN 'Evidence: ' || left(finding.evidence::text,6000) END,
        CASE WHEN finding.resolution IS NOT NULL
             THEN 'Resolution: ' || finding.resolution END
    ) AS body,
    jsonb_build_object(
        'object_type','calliope_specialist_finding',
        'specialist_key',specialist.specialist_key,
        'finding_kind',finding.finding_kind,
        'status',finding.status,
        'subject',finding.subject,
        'recommendation',finding.recommendation,
        'next_review_at',finding.next_review_at,
        'evidence',finding.evidence
    ) AS props
FROM rvbbit.calliope_specialist_findings finding
JOIN rvbbit.calliope_specialists specialist ON specialist.id=finding.specialist_id;

CREATE OR REPLACE VIEW rvbbit.system_learning_item_summary AS
SELECT coalesce(props->>'object_type','unknown') AS object_type,
       count(*)::bigint AS items,
       max(occurred_at) AS last_seen_at
  FROM rvbbit.system_learning_collaborative_items
 GROUP BY coalesce(props->>'object_type','unknown');

UPDATE rvbbit.brain_doc_providers
   SET list_sql=$provider$
       SELECT uri,title,content_hash,occurred_at,body,props
         FROM rvbbit.system_learning_collaborative_items
   $provider$,
       description='RVBBIT-learned workload, routing, acceleration, specialist findings, and operator artifacts.',
       updated_at=clock_timestamp()
 WHERE provider='rvbbit-system-learning';
