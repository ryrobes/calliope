-- 0308: durable, reversible acceleration stewardship for hosted Calliope.
--
-- Deterministic RVBBIT observers and tick workers remain the control plane.
-- The specialist contributes judgment: it chooses bounded trials, explains
-- evidence, and later retains, adjusts, or reverses them. Every mutation is a
-- receipt and is fed back into the existing System Learning brain.

CREATE TABLE IF NOT EXISTS rvbbit.acceleration_steward_decisions (
    id bigserial PRIMARY KEY,
    table_oid oid,
    table_name text NOT NULL,
    action text NOT NULL,
    status text NOT NULL DEFAULT 'applied',
    rationale text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    state_before jsonb NOT NULL DEFAULT '{}'::jsonb,
    state_after jsonb NOT NULL DEFAULT '{}'::jsonb,
    trial_started_at timestamptz,
    trial_expires_at timestamptz,
    outcome_at timestamptz,
    outcome jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor text NOT NULL DEFAULT session_user,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT acceleration_steward_decisions_action_check CHECK (
        action IN (
            'observe','enroll_trial','retain','retire','adjust_policy',
            'accept_layout','reject_layout','no_change','error'
        )
    ),
    CONSTRAINT acceleration_steward_decisions_status_check CHECK (
        status IN ('recorded','applied','observing','successful','reversed','failed')
    ),
    CONSTRAINT acceleration_steward_decisions_evidence_check
        CHECK (jsonb_typeof(evidence)='object'),
    CONSTRAINT acceleration_steward_decisions_before_check
        CHECK (jsonb_typeof(state_before)='object'),
    CONSTRAINT acceleration_steward_decisions_after_check
        CHECK (jsonb_typeof(state_after)='object'),
    CONSTRAINT acceleration_steward_decisions_outcome_check
        CHECK (jsonb_typeof(outcome)='object')
);

CREATE INDEX IF NOT EXISTS acceleration_steward_decisions_table_time_idx
    ON rvbbit.acceleration_steward_decisions(table_oid,decided_at DESC);
CREATE INDEX IF NOT EXISTS acceleration_steward_decisions_trial_idx
    ON rvbbit.acceleration_steward_decisions(trial_expires_at)
    WHERE action='enroll_trial' AND status='observing';

COMMENT ON TABLE rvbbit.acceleration_steward_decisions IS
    'Receipt ledger for model-judged acceleration trials, policy changes, layout decisions, measured outcomes, and reversals.';

CREATE OR REPLACE VIEW rvbbit.acceleration_steward_open_trials AS
SELECT decision.*,
       clock_timestamp() >= decision.trial_expires_at AS review_due
  FROM rvbbit.acceleration_steward_decisions decision
 WHERE decision.action='enroll_trial'
   AND decision.status='observing'
   AND NOT EXISTS (
       SELECT 1
         FROM rvbbit.acceleration_steward_decisions later
        WHERE later.table_name=decision.table_name
          AND later.id>decision.id
          AND later.action IN ('retain','retire')
   );

-- Keep the specialist roster and its intended cadence in the database as
-- discoverable control-plane state. Hermes owns the actual profile-local cron
-- job so its memory, skills, and execution remain isolated with the profile.
INSERT INTO rvbbit.calliope_specialists (
    id,specialist_key,profile_name,display_name,description,capabilities,
    default_schedule,managed
) VALUES (
    'a63ce11e-8c59-5b43-9388-0bf28d683345'::uuid,
    'acceleration-steward','acceleration-steward','Acceleration Steward',
    'Runs evidence-backed acceleration trials and tends canonical and derived RVBBIT layouts.',
    '["acceleration_observe","acceleration_trial","acceleration_policy","layout_stewardship","trial_reversal"]'::jsonb,
    '17 2 * * *',true
)
ON CONFLICT (specialist_key) DO UPDATE SET
    profile_name=EXCLUDED.profile_name,
    display_name=EXCLUDED.display_name,
    description=EXCLUDED.description,
    capabilities=EXCLUDED.capabilities,
    default_schedule=EXCLUDED.default_schedule,
    managed=true,
    updated_at=clock_timestamp();

-- Add the steward's own decisions to the System Learning corpus. The source
-- evidence remains the pre-existing curated surface; this union contributes
-- only compact receipts and measured outcomes, never raw hidden reasoning.
CREATE OR REPLACE VIEW rvbbit.system_learning_stewarded_items AS
SELECT uri,title,content_hash,occurred_at,body,props
  FROM rvbbit.system_learning_select_items
UNION ALL
SELECT
    'rvbbit:acceleration-steward:' || decision.id::text AS uri,
    'Acceleration steward ' || replace(decision.action,'_',' ') ||
        ' for ' || decision.table_name AS title,
    md5(decision.rationale || decision.evidence::text || decision.outcome::text)
        AS content_hash,
    coalesce(decision.outcome_at,decision.decided_at) AS occurred_at,
    concat_ws(E'\n',
        'Calliope acceleration stewardship receipt.',
        'Table: ' || decision.table_name,
        'Action: ' || decision.action,
        'Status: ' || decision.status,
        'Rationale: ' || decision.rationale,
        CASE WHEN decision.trial_expires_at IS NOT NULL
             THEN 'Trial review due: ' || decision.trial_expires_at::text END,
        CASE WHEN decision.evidence <> '{}'::jsonb
             THEN 'Evidence: ' || left(decision.evidence::text,6000) END,
        CASE WHEN decision.outcome <> '{}'::jsonb
             THEN 'Outcome: ' || left(decision.outcome::text,6000) END
    ) AS body,
    jsonb_build_object(
        'object_type','acceleration_steward_decision',
        'table',decision.table_name,
        'action',decision.action,
        'status',decision.status,
        'trial_expires_at',decision.trial_expires_at,
        'evidence',decision.evidence,
        'outcome',decision.outcome
    ) AS props
FROM rvbbit.acceleration_steward_decisions decision;

CREATE OR REPLACE VIEW rvbbit.system_learning_item_summary AS
SELECT coalesce(props->>'object_type','unknown') AS object_type,
       count(*)::bigint AS items,
       max(occurred_at) AS last_seen_at
  FROM rvbbit.system_learning_stewarded_items
 GROUP BY coalesce(props->>'object_type','unknown');

UPDATE rvbbit.brain_doc_providers
   SET list_sql=$provider$
       SELECT uri,title,content_hash,occurred_at,body,props
         FROM rvbbit.system_learning_stewarded_items
   $provider$,
       description='RVBBIT-learned workload, routing, acceleration, steward decisions, and operator artifacts.',
       updated_at=clock_timestamp()
 WHERE provider='rvbbit-system-learning';
