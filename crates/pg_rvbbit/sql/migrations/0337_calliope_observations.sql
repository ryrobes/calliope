-- Stable Calliope observations outlive any one Dream cycle.  Each producer
-- updates the current row and appends an idempotent source occurrence.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_observations (
    id uuid PRIMARY KEY,
    fingerprint text NOT NULL,
    scope_kind text NOT NULL DEFAULT 'company',
    owner_email text,
    kind text NOT NULL,
    title text NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    entities jsonb NOT NULL DEFAULT '[]'::jsonb,
    signal_count integer NOT NULL DEFAULT 1,
    recurrence_count integer NOT NULL DEFAULT 1,
    confidence numeric(5,4) NOT NULL DEFAULT 0.5,
    urgency numeric(5,4) NOT NULL DEFAULT 0.5,
    state text NOT NULL DEFAULT 'active',
    sensitivity text NOT NULL DEFAULT 'internal',
    first_cycle_id uuid REFERENCES rvbbit.calliope_dream_cycles(id) ON DELETE SET NULL,
    latest_cycle_id uuid REFERENCES rvbbit.calliope_dream_cycles(id) ON DELETE SET NULL,
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observations_scope_check CHECK (
        (scope_kind='company' AND owner_email IS NULL) OR
        (scope_kind='personal' AND owner_email IS NOT NULL
         AND owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%')
    ),
    CONSTRAINT calliope_observations_kind_check CHECK (
        kind IN ('friction','repetition','connection','gap','success','change')
    ),
    CONSTRAINT calliope_observations_state_check CHECK (
        state IN ('active','resolved','stale','suppressed')
    ),
    CONSTRAINT calliope_observations_evidence_check CHECK (jsonb_typeof(evidence)='array'),
    CONSTRAINT calliope_observations_entities_check CHECK (jsonb_typeof(entities)='array'),
    CONSTRAINT calliope_observations_counts_check CHECK (
        signal_count>=1 AND recurrence_count>=1
    ),
    CONSTRAINT calliope_observations_scores_check CHECK (
        confidence BETWEEN 0 AND 1 AND urgency BETWEEN 0 AND 1
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_observations_scope_fingerprint_key
    ON rvbbit.calliope_observations (scope_kind,coalesce(owner_email,''),fingerprint);
CREATE INDEX IF NOT EXISTS calliope_observations_active_idx
    ON rvbbit.calliope_observations
       (scope_kind,owner_email,state,urgency DESC,confidence DESC,last_observed_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_events (
    event_id bigserial PRIMARY KEY,
    observation_id uuid NOT NULL REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    source_kind text NOT NULL,
    source_ref text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observation_events_kind_check CHECK (
        event_kind IN ('observed','resolved','reopened','stale','suppressed')
    ),
    CONSTRAINT calliope_observation_events_payload_check CHECK (jsonb_typeof(payload)='object'),
    CONSTRAINT calliope_observation_events_source_key
        UNIQUE (observation_id,event_kind,source_kind,source_ref)
);
CREATE INDEX IF NOT EXISTS calliope_observation_events_observation_idx
    ON rvbbit.calliope_observation_events (observation_id,created_at DESC,event_id DESC);

ALTER TABLE rvbbit.calliope_dream_observations
    ADD COLUMN IF NOT EXISTS stable_observation_id uuid
        REFERENCES rvbbit.calliope_observations(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS calliope_dream_observations_stable_idx
    ON rvbbit.calliope_dream_observations (stable_observation_id,created_at DESC)
    WHERE stable_observation_id IS NOT NULL;
