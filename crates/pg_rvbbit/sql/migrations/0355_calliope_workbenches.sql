-- 0355: Calliope Computer Use -- durable workbench control and receipts.
--
-- The desktop runtime is deliberately replaceable.  These tables are the
-- product contract: ownership, exclusive control, append-only actions, and
-- recordings survive a browser disconnect or a runtime/provider migration.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_workbenches (
    id uuid PRIMARY KEY,
    owner_email text NOT NULL,
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE RESTRICT,
    backend text NOT NULL DEFAULT 'container_debug',
    runtime_ref text,
    status text NOT NULL DEFAULT 'provisioning',
    control_mode text NOT NULL DEFAULT 'paused',
    revision bigint NOT NULL DEFAULT 1,
    lease_id uuid,
    lease_holder text,
    lease_expires_at timestamptz,
    image_ref text NOT NULL,
    image_revision text,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    stopped_at timestamptz,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '4 hours'),
    CONSTRAINT calliope_workbenches_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_workbenches_backend_check CHECK (
        backend IN ('container_debug','libvirt')
    ),
    CONSTRAINT calliope_workbenches_status_check CHECK (
        status IN ('provisioning','ready','stopping','stopped','expired','error')
    ),
    CONSTRAINT calliope_workbenches_control_mode_check CHECK (
        control_mode IN ('human','debug','paused')
    ),
    CONSTRAINT calliope_workbenches_revision_check CHECK (revision >= 1),
    CONSTRAINT calliope_workbenches_lease_check CHECK (
        (lease_id IS NULL AND lease_holder IS NULL AND lease_expires_at IS NULL)
        OR (lease_id IS NOT NULL AND lease_holder IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT calliope_workbenches_config_check CHECK (jsonb_typeof(config)='object')
);

-- The first provider is one deliberately static disposable desktop service.
-- A later libvirt provider may replace this index with a capacity allocator;
-- none of the browser/API contracts need to change when that happens.
CREATE UNIQUE INDEX IF NOT EXISTS calliope_workbenches_one_live_debug_idx
    ON rvbbit.calliope_workbenches ((backend))
    WHERE backend='container_debug'
      AND status IN ('provisioning','ready','stopping');
CREATE INDEX IF NOT EXISTS calliope_workbenches_owner_session_idx
    ON rvbbit.calliope_workbenches (owner_email,session_id,created_at DESC);
CREATE INDEX IF NOT EXISTS calliope_workbenches_expiry_idx
    ON rvbbit.calliope_workbenches (expires_at)
    WHERE status IN ('provisioning','ready');

CREATE TABLE IF NOT EXISTS rvbbit.calliope_workbench_events (
    id uuid PRIMARY KEY,
    workbench_id uuid NOT NULL REFERENCES rvbbit.calliope_workbenches(id) ON DELETE RESTRICT,
    sequence bigint NOT NULL,
    event_type text NOT NULL,
    actor_email text,
    lease_id uuid,
    request_event_id uuid REFERENCES rvbbit.calliope_workbench_events(id) ON DELETE RESTRICT,
    idempotency_key text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_workbench_events_sequence_check CHECK (sequence >= 1),
    CONSTRAINT calliope_workbench_events_payload_check CHECK (jsonb_typeof(payload)='object'),
    CONSTRAINT calliope_workbench_events_sequence_key UNIQUE (workbench_id,sequence),
    CONSTRAINT calliope_workbench_events_idempotency_key UNIQUE (workbench_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS calliope_workbench_events_workbench_idx
    ON rvbbit.calliope_workbench_events (workbench_id,sequence);

CREATE OR REPLACE FUNCTION rvbbit._calliope_workbench_events_immutable()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'Calliope workbench events are append-only';
END
$fn$;
DROP TRIGGER IF EXISTS calliope_workbench_events_immutable
    ON rvbbit.calliope_workbench_events;
CREATE TRIGGER calliope_workbench_events_immutable
BEFORE UPDATE OR DELETE ON rvbbit.calliope_workbench_events
FOR EACH ROW EXECUTE FUNCTION rvbbit._calliope_workbench_events_immutable();

CREATE TABLE IF NOT EXISTS rvbbit.calliope_workbench_recordings (
    id uuid PRIMARY KEY,
    workbench_id uuid NOT NULL REFERENCES rvbbit.calliope_workbenches(id) ON DELETE RESTRICT,
    state text NOT NULL DEFAULT 'starting',
    storage_backend text NOT NULL DEFAULT 'local_volume',
    object_key text NOT NULL,
    mime_type text NOT NULL DEFAULT 'video/mp4',
    duration_ms bigint,
    bytes bigint,
    sha256 text,
    width integer,
    height integer,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    expires_at timestamptz,
    CONSTRAINT calliope_workbench_recordings_state_check CHECK (
        state IN ('starting','recording','ready','failed')
    ),
    CONSTRAINT calliope_workbench_recordings_backend_check CHECK (
        storage_backend IN ('local_volume','s3','gcs')
    ),
    CONSTRAINT calliope_workbench_recordings_sha_check CHECK (
        sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'
    )
);
CREATE INDEX IF NOT EXISTS calliope_workbench_recordings_workbench_idx
    ON rvbbit.calliope_workbench_recordings (workbench_id,created_at DESC);

COMMENT ON TABLE rvbbit.calliope_workbenches IS
    'Owner-scoped Calliope computer-use sessions. backend is replaceable; revision and lease fields serialize control.';
COMMENT ON TABLE rvbbit.calliope_workbench_events IS
    'Append-only lifecycle, lease, action, and recording receipts for a Calliope workbench.';
COMMENT ON TABLE rvbbit.calliope_workbench_recordings IS
    'Durable recording metadata; object_key is provider-neutral and never grants browser access by itself.';
