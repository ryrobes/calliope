-- Govern the stable observation ledger without turning shared company truth
-- into one employee's inbox state.  Feedback is owner-scoped, lifecycle
-- changes are revisioned, and expensive semantic ranking is cached per
-- audience for fast front-door reads.

ALTER TABLE rvbbit.calliope_observations
    ADD COLUMN IF NOT EXISTS state_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS state_changed_by text,
    ADD COLUMN IF NOT EXISTS state_changed_at timestamptz;

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_feedback (
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    verdict text NOT NULL,
    note text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id,owner_email),
    CONSTRAINT calliope_observation_feedback_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_observation_feedback_verdict_check CHECK (
        verdict IN ('relevant','not_relevant','following')
    ),
    CONSTRAINT calliope_observation_feedback_revision_check CHECK (revision>=1)
);
CREATE INDEX IF NOT EXISTS calliope_observation_feedback_owner_idx
    ON rvbbit.calliope_observation_feedback (owner_email,updated_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_feedback_events (
    event_id bigserial PRIMARY KEY,
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    event_kind text NOT NULL,
    note text NOT NULL DEFAULT '',
    revision integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_observation_feedback_events_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_observation_feedback_events_kind_check CHECK (
        event_kind IN ('relevant','not_relevant','following','cleared')
    ),
    CONSTRAINT calliope_observation_feedback_events_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_observation_feedback_events_revision_key
        UNIQUE (observation_id,owner_email,revision)
);
CREATE INDEX IF NOT EXISTS calliope_observation_feedback_events_owner_idx
    ON rvbbit.calliope_observation_feedback_events
       (owner_email,created_at DESC,event_id DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_rankings (
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE CASCADE,
    audience_key text NOT NULL,
    relevance numeric(5,4) NOT NULL,
    priority numeric(5,4) NOT NULL,
    surface_role text NOT NULL,
    factors jsonb NOT NULL DEFAULT '{}'::jsonb,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    criterion_hash text NOT NULL,
    profile_version integer NOT NULL DEFAULT 0,
    ranker text NOT NULL,
    model text,
    ranked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id,audience_key),
    CONSTRAINT calliope_observation_rankings_audience_check CHECK (
        audience_key='company' OR
        (audience_key=lower(btrim(audience_key)) AND audience_key LIKE '%@%')
    ),
    CONSTRAINT calliope_observation_rankings_scores_check CHECK (
        relevance BETWEEN 0 AND 1 AND priority BETWEEN 0 AND 1
    ),
    CONSTRAINT calliope_observation_rankings_role_check CHECK (
        surface_role IN ('lead','supporting','background')
    ),
    CONSTRAINT calliope_observation_rankings_factors_check CHECK (
        jsonb_typeof(factors)='object' AND jsonb_typeof(reason_codes)='array'
    )
);
CREATE INDEX IF NOT EXISTS calliope_observation_rankings_audience_idx
    ON rvbbit.calliope_observation_rankings
       (audience_key,priority DESC,ranked_at DESC);
