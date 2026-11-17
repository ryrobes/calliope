-- 0302: durable Calliope specialist identities and observable delegated runs.
--
-- Hermes profiles own isolated working memory and sessions. RVBBIT remains
-- the canonical control plane: every handoff, specialist message/tool event,
-- parent notebook, and projected Stage result is correlated here so the
-- browser can later expose the conversation without scraping Hermes state.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_specialists (
    id uuid PRIMARY KEY,
    specialist_key text NOT NULL UNIQUE,
    profile_name text NOT NULL UNIQUE,
    display_name text NOT NULL,
    description text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'active',
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    default_schedule text,
    managed boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_specialists_key_check
        CHECK (specialist_key ~ '^[a-z][a-z0-9-]{1,62}$'),
    CONSTRAINT calliope_specialists_profile_check
        CHECK (profile_name ~ '^[a-z][a-z0-9-]{1,62}$'),
    CONSTRAINT calliope_specialists_status_check
        CHECK (status IN ('active','paused','disabled')),
    CONSTRAINT calliope_specialists_capabilities_check
        CHECK (jsonb_typeof(capabilities)='array')
);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_specialist_runs (
    id uuid PRIMARY KEY,
    specialist_id uuid NOT NULL REFERENCES rvbbit.calliope_specialists(id),
    owner_email text NOT NULL,
    source_session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
    source_turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL,
    trigger_kind text NOT NULL DEFAULT 'delegated',
    hermes_session_id text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'queued',
    task text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    result text NOT NULL DEFAULT '',
    error text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    runtime jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_specialist_runs_trigger_check
        CHECK (trigger_kind IN ('delegated','scheduled','manual')),
    CONSTRAINT calliope_specialist_runs_status_check
        CHECK (status IN ('queued','running','complete','failed','cancelled')),
    CONSTRAINT calliope_specialist_runs_context_check
        CHECK (jsonb_typeof(context)='object'),
    CONSTRAINT calliope_specialist_runs_usage_check
        CHECK (jsonb_typeof(usage)='object'),
    CONSTRAINT calliope_specialist_runs_runtime_check
        CHECK (jsonb_typeof(runtime)='object')
);

CREATE INDEX IF NOT EXISTS calliope_specialist_runs_owner_created_idx
    ON rvbbit.calliope_specialist_runs(owner_email,created_at DESC);
CREATE INDEX IF NOT EXISTS calliope_specialist_runs_parent_idx
    ON rvbbit.calliope_specialist_runs(source_session_id,source_turn_id,created_at);
CREATE INDEX IF NOT EXISTS calliope_specialist_runs_active_idx
    ON rvbbit.calliope_specialist_runs(specialist_id,started_at)
    WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS rvbbit.calliope_specialist_messages (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES rvbbit.calliope_specialist_runs(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    role text NOT NULL,
    kind text NOT NULL,
    body text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_specialist_messages_run_ordinal_key UNIQUE(run_id,ordinal),
    CONSTRAINT calliope_specialist_messages_role_check
        CHECK (role IN ('calliope','specialist','tool','system')),
    CONSTRAINT calliope_specialist_messages_kind_check
        CHECK (kind IN ('handoff','progress','assistant','tool_started','tool_completed','tool_failed','system')),
    CONSTRAINT calliope_specialist_messages_payload_check
        CHECK (jsonb_typeof(payload)='object')
);

CREATE INDEX IF NOT EXISTS calliope_specialist_messages_run_idx
    ON rvbbit.calliope_specialist_messages(run_id,ordinal);

INSERT INTO rvbbit.calliope_specialists (
    id,specialist_key,profile_name,display_name,description,capabilities
) VALUES
(
    '4bb62b7f-2387-5a04-9b77-16877dd9e4af'::uuid,
    'dashboard-studio','dashboard-studio','Dashboard Studio',
    'Builds, repairs, validates, and publishes evidence-backed dashboards and live apps.',
    '["dashboard_build","dashboard_repair","visual_analysis","artifact_publish"]'::jsonb
),
(
    'ea54bc94-92df-59d3-a172-8df0099541f4'::uuid,
    'warehouse-steward','warehouse-steward','Warehouse Steward',
    'Diagnoses and tends mirrors, catalog, acceleration, business context, and local RVBBIT health.',
    '["warehouse_health","mirror_diagnosis","catalog_health","acceleration_health","local_repair"]'::jsonb
)
ON CONFLICT (specialist_key) DO UPDATE SET
    profile_name=EXCLUDED.profile_name,
    display_name=EXCLUDED.display_name,
    description=EXCLUDED.description,
    capabilities=EXCLUDED.capabilities,
    managed=true,
    updated_at=clock_timestamp();

COMMENT ON TABLE rvbbit.calliope_specialists IS
    'Managed Hermes specialist profiles exposed to Calliope as bounded persistent collaborators.';
COMMENT ON TABLE rvbbit.calliope_specialist_runs IS
    'One observable specialist handoff correlated to its parent Calliope notebook and Hermes session.';
COMMENT ON TABLE rvbbit.calliope_specialist_messages IS
    'Ordered conversation and tool-status ledger for future inline chat and Stage observability.';
