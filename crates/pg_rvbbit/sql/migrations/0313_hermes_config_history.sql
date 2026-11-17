-- 0313: durable desired-state and recovery ledger for hosted Hermes config.
-- Secret values never belong here: config_yaml contains env/canonical refs.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_config_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    parent_id uuid REFERENCES rvbbit.hermes_config_revisions(id) ON DELETE SET NULL,
    state text NOT NULL DEFAULT 'candidate',
    config_yaml text NOT NULL,
    config_hash text NOT NULL,
    reason text NOT NULL DEFAULT '',
    hermes_revision text NOT NULL,
    hermes_image_digest text NOT NULL,
    validation jsonb NOT NULL DEFAULT '{}'::jsonb,
    verification jsonb NOT NULL DEFAULT '{}'::jsonb,
    secret_bindings jsonb NOT NULL DEFAULT '{}'::jsonb,
    redacted_diff jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by text NOT NULL DEFAULT session_user,
    applied_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT hermes_config_revisions_state_check CHECK (
        state IN (
            'candidate','validating','validated','applying','active',
            'rejected','failed','rolled_back','superseded'
        )
    ),
    CONSTRAINT hermes_config_revisions_hash_check CHECK (
        config_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT hermes_config_revisions_validation_check CHECK (
        jsonb_typeof(validation) = 'object'
    ),
    CONSTRAINT hermes_config_revisions_verification_check CHECK (
        jsonb_typeof(verification) = 'object'
    ),
    CONSTRAINT hermes_config_revisions_secret_bindings_check CHECK (
        jsonb_typeof(secret_bindings) = 'object'
    ),
    CONSTRAINT hermes_config_revisions_diff_check CHECK (
        jsonb_typeof(redacted_diff) = 'array'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS hermes_config_revisions_one_active_idx
    ON rvbbit.hermes_config_revisions (state)
    WHERE state = 'active';
CREATE INDEX IF NOT EXISTS hermes_config_revisions_created_idx
    ON rvbbit.hermes_config_revisions (created_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_config_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    revision_id uuid REFERENCES rvbbit.hermes_config_revisions(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    actor text NOT NULL DEFAULT session_user,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT hermes_config_events_type_check CHECK (
        event_type IN (
            'created','validation_started','validation_passed','validation_failed',
            'apply_started','gateway_stopped','gateway_started','health_passed',
            'verification_passed','apply_failed','rollback_started',
            'rollback_completed','activated','superseded','controller_heartbeat'
        )
    ),
    CONSTRAINT hermes_config_events_details_check CHECK (
        jsonb_typeof(details) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS hermes_config_events_revision_idx
    ON rvbbit.hermes_config_events (revision_id, created_at, event_id);
CREATE INDEX IF NOT EXISTS hermes_config_events_created_idx
    ON rvbbit.hermes_config_events (created_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_config_controller_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_revision_id uuid REFERENCES rvbbit.hermes_config_revisions(id) ON DELETE SET NULL,
    desired_revision_id uuid REFERENCES rvbbit.hermes_config_revisions(id) ON DELETE SET NULL,
    last_good_revision_id uuid REFERENCES rvbbit.hermes_config_revisions(id) ON DELETE SET NULL,
    controller_status text NOT NULL DEFAULT 'unknown',
    gateway_status text NOT NULL DEFAULT 'unknown',
    active_config_hash text,
    controller_heartbeat_at timestamptz,
    gateway_checked_at timestamptz,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT hermes_config_controller_status_check CHECK (
        controller_status IN ('unknown','ready','validating','applying','rolling_back','failed')
    ),
    CONSTRAINT hermes_config_gateway_status_check CHECK (
        gateway_status IN ('unknown','starting','healthy','degraded','stopped','failed')
    ),
    CONSTRAINT hermes_config_controller_hash_check CHECK (
        active_config_hash IS NULL OR active_config_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT hermes_config_controller_details_check CHECK (
        jsonb_typeof(details) = 'object'
    )
);

INSERT INTO rvbbit.hermes_config_controller_state (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

REVOKE ALL ON rvbbit.hermes_config_revisions FROM PUBLIC;
REVOKE ALL ON rvbbit.hermes_config_events FROM PUBLIC;
REVOKE ALL ON rvbbit.hermes_config_controller_state FROM PUBLIC;

CREATE OR REPLACE VIEW rvbbit.hermes_config_history AS
SELECT r.id,
       r.revision,
       r.parent_id,
       r.state,
       r.config_hash,
       r.reason,
       r.hermes_revision,
       r.hermes_image_digest,
       r.validation,
       r.verification,
       r.secret_bindings,
       r.redacted_diff,
       r.error,
       r.created_at,
       r.created_by,
       r.applied_at,
       r.completed_at,
       (s.active_revision_id = r.id) AS is_active,
       (s.last_good_revision_id = r.id) AS is_last_good
FROM rvbbit.hermes_config_revisions r
CROSS JOIN rvbbit.hermes_config_controller_state s
WHERE s.singleton;

COMMENT ON VIEW rvbbit.hermes_config_history IS
    'Secret-free Hermes configuration history for DataRabbit recovery UI; raw YAML remains restricted.';

CREATE OR REPLACE FUNCTION rvbbit.hermes_config_record_candidate(
    p_config_yaml text,
    p_config_hash text,
    p_reason text,
    p_hermes_revision text,
    p_hermes_image_digest text,
    p_validation jsonb,
    p_secret_bindings jsonb DEFAULT '{}'::jsonb,
    p_redacted_diff jsonb DEFAULT '[]'::jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $fn$
DECLARE
    v_id uuid;
    v_parent uuid;
BEGIN
    PERFORM rvbbit.require_capability_catalog_admin();
    IF coalesce(p_config_yaml, '') = '' THEN
        RAISE EXCEPTION 'Hermes candidate YAML is required';
    END IF;
    IF coalesce(p_config_hash, '') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Hermes candidate hash must be lowercase sha256';
    END IF;
    IF jsonb_typeof(coalesce(p_validation, '{}'::jsonb)) <> 'object'
       OR jsonb_typeof(coalesce(p_secret_bindings, '{}'::jsonb)) <> 'object'
       OR jsonb_typeof(coalesce(p_redacted_diff, '[]'::jsonb)) <> 'array' THEN
        RAISE EXCEPTION 'Hermes validation and secret bindings must be objects and diff an array';
    END IF;
    SELECT active_revision_id INTO v_parent
    FROM rvbbit.hermes_config_controller_state
    WHERE singleton;

    INSERT INTO rvbbit.hermes_config_revisions (
        parent_id,state,config_yaml,config_hash,reason,hermes_revision,
        hermes_image_digest,validation,secret_bindings,redacted_diff,created_by
    ) VALUES (
        v_parent,
        CASE WHEN coalesce((p_validation->>'valid')::boolean, false)
             THEN 'validated' ELSE 'rejected' END,
        p_config_yaml,p_config_hash,coalesce(p_reason,''),p_hermes_revision,
        p_hermes_image_digest,coalesce(p_validation,'{}'::jsonb),
        coalesce(p_secret_bindings,'{}'::jsonb),
        coalesce(p_redacted_diff,'[]'::jsonb),session_user
    ) RETURNING id INTO v_id;

    INSERT INTO rvbbit.hermes_config_events (
        revision_id,event_type,actor,details
    ) VALUES (
        v_id,'created',session_user,
        jsonb_build_object('config_hash',p_config_hash,'reason',coalesce(p_reason,''))
    );
    INSERT INTO rvbbit.hermes_config_events (
        revision_id,event_type,actor,details
    ) VALUES (
        v_id,
        CASE WHEN coalesce((p_validation->>'valid')::boolean, false)
             THEN 'validation_passed' ELSE 'validation_failed' END,
        session_user,
        coalesce(p_validation,'{}'::jsonb)
    );
    RETURN v_id;
END
$fn$;

REVOKE ALL ON FUNCTION rvbbit.hermes_config_record_candidate(
    text,text,text,text,text,jsonb,jsonb,jsonb
) FROM PUBLIC;
