-- Canonical, owner-scoped human decisions. These are projected into a board
-- alongside Work Inbox blockers and mirrored Hermes cards, but remain their
-- own lifecycle so a bounded choice is not flattened into generic inbox prose.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_decision_requests (
    id uuid PRIMARY KEY,
    owner_email text NOT NULL,
    session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
    observation_id uuid NOT NULL
        REFERENCES rvbbit.calliope_observations(id) ON DELETE RESTRICT,
    decision_kind text NOT NULL DEFAULT 'choice',
    question text NOT NULL,
    summary text NOT NULL DEFAULT '',
    options jsonb NOT NULL,
    recommended_option text,
    urgency text NOT NULL DEFAULT 'normal',
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'open',
    selected_option text,
    response_note text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    created_by text NOT NULL,
    answered_by text,
    due_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz,
    closed_at timestamptz,
    CONSTRAINT calliope_decision_requests_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_decision_requests_kind_check CHECK (
        decision_kind IN ('approval','choice','exception','confirmation')
    ),
    CONSTRAINT calliope_decision_requests_urgency_check CHECK (
        urgency IN ('low','normal','high','critical')
    ),
    CONSTRAINT calliope_decision_requests_status_check CHECK (
        status IN ('open','answered','cancelled','expired')
    ),
    CONSTRAINT calliope_decision_requests_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_decision_requests_json_check CHECK (
        jsonb_typeof(options)='array' AND jsonb_array_length(options) BETWEEN 2 AND 6
        AND jsonb_typeof(evidence)='array' AND jsonb_typeof(context)='object'
    ),
    CONSTRAINT calliope_decision_requests_answer_check CHECK (
        (status='answered' AND selected_option IS NOT NULL AND answered_at IS NOT NULL)
        OR (status<>'answered' AND selected_option IS NULL AND answered_at IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS calliope_decision_requests_one_open_observation_idx
    ON rvbbit.calliope_decision_requests (owner_email,observation_id)
    WHERE status='open';

CREATE INDEX IF NOT EXISTS calliope_decision_requests_owner_status_idx
    ON rvbbit.calliope_decision_requests
       (owner_email,status,urgency,updated_at DESC);

CREATE INDEX IF NOT EXISTS calliope_decision_requests_due_idx
    ON rvbbit.calliope_decision_requests (coalesce(expires_at,due_at))
    WHERE status='open';

CREATE TABLE IF NOT EXISTS rvbbit.calliope_decision_events (
    event_id bigserial PRIMARY KEY,
    decision_id uuid NOT NULL
        REFERENCES rvbbit.calliope_decision_requests(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    actor_email text NOT NULL,
    revision integer NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_decision_events_kind_check CHECK (
        event_kind IN ('created','updated','answered','cancelled','expired')
    ),
    CONSTRAINT calliope_decision_events_actor_check CHECK (
        actor_email=lower(btrim(actor_email)) AND actor_email LIKE '%@%'
    ),
    CONSTRAINT calliope_decision_events_revision_check CHECK (revision>=1),
    CONSTRAINT calliope_decision_events_payload_check CHECK (
        jsonb_typeof(payload)='object'
    ),
    CONSTRAINT calliope_decision_events_revision_key UNIQUE (decision_id,revision)
);

CREATE INDEX IF NOT EXISTS calliope_decision_events_decision_idx
    ON rvbbit.calliope_decision_events (decision_id,created_at,event_id);
