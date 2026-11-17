-- 0304: durable, ordered lifecycle events for backend-owned Calliope turns.
--
-- The browser may move between notebooks while Hermes keeps working.  This
-- compact event ledger is the process-independent seam for replay, background
-- observability, and a later worker/lease runner.  Final transcript truth stays
-- in calliope_turns and large Stage payloads stay in calliope_surfaces.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_turn_events (
    turn_id uuid NOT NULL
        REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (turn_id,sequence),
    CONSTRAINT calliope_turn_events_type_check
        CHECK (event_type ~ '^[a-z][a-z0-9_.-]{1,80}$'),
    CONSTRAINT calliope_turn_events_payload_check
        CHECK (jsonb_typeof(payload)='object')
);

CREATE INDEX IF NOT EXISTS calliope_turn_events_created_idx
    ON rvbbit.calliope_turn_events(created_at DESC);

COMMENT ON TABLE rvbbit.calliope_turn_events IS
    'Compact ordered lifecycle ledger for server-owned Calliope turns; excludes token deltas and large surface bodies.';
