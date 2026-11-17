-- 0321: durable, secret-free provider control for hosted Calliope.
--
-- API-key ciphertext remains in rvbbit.credentials and OAuth tokens remain in
-- Hermes' native credential store.  These tables contain only references,
-- redacted state, alias mappings, usage aggregates, and optional provider
-- balance observations.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_provider_state (
    provider text PRIMARY KEY,
    display_name text NOT NULL,
    auth_kind text NOT NULL,
    auth_state text NOT NULL DEFAULT 'missing',
    credential_ref text,
    environment_managed boolean NOT NULL DEFAULT false,
    credential_label text,
    account_hint text,
    status text NOT NULL DEFAULT 'unknown',
    last_error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    tested_at timestamptz,
    models_synced_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by text NOT NULL DEFAULT session_user,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_by text NOT NULL DEFAULT session_user,
    CONSTRAINT calliope_provider_state_provider_check CHECK (
        provider ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    ),
    CONSTRAINT calliope_provider_state_auth_kind_check CHECK (
        auth_kind IN ('environment','api_key','oauth')
    ),
    CONSTRAINT calliope_provider_state_auth_state_check CHECK (
        auth_state IN ('missing','pending','configured','expired','error')
    ),
    CONSTRAINT calliope_provider_state_status_check CHECK (
        status IN ('unknown','ready','degraded','error')
    ),
    CONSTRAINT calliope_provider_state_metadata_check CHECK (
        jsonb_typeof(metadata) = 'object'
        AND NOT metadata ?| ARRAY[
            'value','secret','token','access_token','refresh_token','password',
            'private_key','privateKey','credentials','api_key'
        ]
    ),
    CONSTRAINT calliope_provider_state_credential_check CHECK (
        (auth_kind = 'api_key' AND auth_state = 'configured' AND environment_managed)
        OR (auth_kind = 'api_key' AND auth_state = 'configured' AND credential_ref IS NOT NULL)
        OR auth_kind <> 'api_key'
        OR auth_state <> 'configured'
    )
);

CREATE INDEX IF NOT EXISTS calliope_provider_state_ready_idx
    ON rvbbit.calliope_provider_state (auth_state, provider);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_model_routes (
    alias text PRIMARY KEY,
    provider text NOT NULL,
    model text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    description text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by text NOT NULL DEFAULT session_user,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_by text NOT NULL DEFAULT session_user,
    CONSTRAINT calliope_model_routes_alias_check CHECK (
        alias ~ '^calliope[a-z0-9_.-]{0,55}$'
    ),
    CONSTRAINT calliope_model_routes_provider_check CHECK (
        provider ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    ),
    CONSTRAINT calliope_model_routes_model_check CHECK (
        char_length(btrim(model)) BETWEEN 1 AND 256
        AND model !~ E'[\\r\\n]'
    ),
    CONSTRAINT calliope_model_routes_metadata_check CHECK (
        jsonb_typeof(metadata) = 'object'
        AND NOT metadata ?| ARRAY[
            'value','secret','token','access_token','refresh_token','password',
            'private_key','privateKey','credentials','api_key','base_url'
        ]
    )
);

CREATE INDEX IF NOT EXISTS calliope_model_routes_provider_idx
    ON rvbbit.calliope_model_routes (provider, enabled, alias);

-- The canonical hosted route remains explicit in the control plane even
-- though Hermes' global model block owns it when no override is needed.
INSERT INTO rvbbit.calliope_provider_state (
    provider, display_name, auth_kind, auth_state, environment_managed,
    status, metadata, created_by, updated_by
) VALUES (
    'clover', 'Clover / Hutch', 'environment', 'missing', true,
    'unknown', '{"source":"host_environment"}'::jsonb,
    'calliope-migration', 'calliope-migration'
)
ON CONFLICT (provider) DO NOTHING;

INSERT INTO rvbbit.calliope_model_routes (
    alias, provider, model, description, metadata, created_by, updated_by
) VALUES (
    'calliope', 'clover', 'calliope',
    'Primary hosted Calliope route.',
    '{"canonical":true,"global_model":true}'::jsonb,
    'calliope-migration', 'calliope-migration'
)
ON CONFLICT (alias) DO NOTHING;

CREATE TABLE IF NOT EXISTS rvbbit.calliope_provider_credit_snapshots (
    snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL,
    balance_kind text NOT NULL DEFAULT 'credit',
    currency text,
    credit_limit numeric(24,9),
    credit_used numeric(24,9),
    credit_remaining numeric(24,9),
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT calliope_provider_credit_provider_check CHECK (
        provider ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    ),
    CONSTRAINT calliope_provider_credit_kind_check CHECK (
        balance_kind IN ('credit','quota','subscription','unknown')
    ),
    CONSTRAINT calliope_provider_credit_raw_check CHECK (
        jsonb_typeof(raw) = 'object'
        AND NOT raw ?| ARRAY[
            'value','secret','token','access_token','refresh_token','password',
            'private_key','privateKey','credentials','api_key'
        ]
    )
);

CREATE INDEX IF NOT EXISTS calliope_provider_credit_latest_idx
    ON rvbbit.calliope_provider_credit_snapshots (provider, observed_at DESC);

CREATE OR REPLACE VIEW rvbbit.calliope_provider_credit_latest AS
SELECT DISTINCT ON (provider)
    provider, balance_kind, currency, credit_limit, credit_used,
    credit_remaining, raw, observed_at
FROM rvbbit.calliope_provider_credit_snapshots
ORDER BY provider, observed_at DESC, snapshot_id DESC;

CREATE OR REPLACE VIEW rvbbit.calliope_provider_usage_daily AS
SELECT
    date_trunc('day', created_at)::date AS usage_day,
    CASE
        WHEN lower(coalesce(backend, '')) = 'custom' THEN 'clover'
        ELSE coalesce(nullif(backend, ''), 'hermes')
    END AS provider,
    coalesce(nullif(model, ''), 'unknown') AS model,
    count(*)::bigint AS calls,
    coalesce(sum(tokens_in), 0)::bigint AS tokens_in,
    coalesce(sum(tokens_out), 0)::bigint AS tokens_out,
    count(*) FILTER (WHERE status IN ('settled','free'))::bigint AS reported_calls,
    count(*) FILTER (WHERE status = 'estimated')::bigint AS estimated_calls,
    count(*) FILTER (WHERE status = 'uncosted')::bigint AS uncosted_calls,
    coalesce(sum(cost_usd) FILTER (WHERE status <> 'error'), 0)::numeric(24,9)
        AS cost_usd,
    min(created_at) AS first_call_at,
    max(created_at) AS last_call_at
FROM rvbbit.cost_latest
WHERE source = 'hermes'
GROUP BY 1, 2, 3;

COMMENT ON TABLE rvbbit.calliope_provider_state IS
    'Secret-free provider auth and health state for the hosted Calliope model control plane.';
COMMENT ON TABLE rvbbit.calliope_model_routes IS
    'Durable calliope* aliases mapped to a configured Hermes provider and model.';
COMMENT ON VIEW rvbbit.calliope_provider_usage_daily IS
    'Daily Hermes/Calliope provider usage derived from the append-only cost ledger.';

REVOKE ALL ON rvbbit.calliope_provider_state FROM PUBLIC;
REVOKE ALL ON rvbbit.calliope_model_routes FROM PUBLIC;
REVOKE ALL ON rvbbit.calliope_provider_credit_snapshots FROM PUBLIC;
REVOKE ALL ON rvbbit.calliope_provider_credit_latest FROM PUBLIC;
REVOKE ALL ON rvbbit.calliope_provider_usage_daily FROM PUBLIC;

DO $revoke_calliope_provider_sequence$
BEGIN
    IF to_regclass('rvbbit.calliope_provider_credit_snapshots_snapshot_id_seq') IS NOT NULL THEN
        EXECUTE 'REVOKE ALL ON SEQUENCE rvbbit.calliope_provider_credit_snapshots_snapshot_id_seq FROM PUBLIC';
    END IF;
END
$revoke_calliope_provider_sequence$;
