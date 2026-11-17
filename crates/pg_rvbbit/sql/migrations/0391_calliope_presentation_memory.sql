-- 0391: durable delivery ledger for private presentation-layer memory.
--
-- Hindsight remains the memory store. This queue records only ownership and
-- delivery state; the user and rewritten assistant prose stay canonical in
-- calliope_turns and are read just-in-time when a bounded batch is submitted.
-- Nothing from this queue is injected into the base Calliope agent context.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_presentation_memory_queue (
    turn_id uuid PRIMARY KEY
        REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
    session_id uuid NOT NULL
        REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    bank_id text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    batch_key text,
    last_error text,
    last_attempt_at timestamptz,
    submitted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_presentation_memory_owner_check CHECK (
        owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'
    ),
    CONSTRAINT calliope_presentation_memory_bank_check CHECK (
        bank_id ~ '^calliope-presentation-v1-[0-9a-f]{32}$'
    ),
    CONSTRAINT calliope_presentation_memory_status_check CHECK (
        status IN ('pending','submitted')
    ),
    CONSTRAINT calliope_presentation_memory_attempts_check CHECK (attempts >= 0),
    CONSTRAINT calliope_presentation_memory_delivery_check CHECK (
        (status='pending' AND submitted_at IS NULL)
        OR (status='submitted' AND submitted_at IS NOT NULL AND batch_key IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS calliope_presentation_memory_pending_idx
    ON rvbbit.calliope_presentation_memory_queue
        (owner_email, created_at, turn_id)
    WHERE status='pending';

COMMENT ON TABLE rvbbit.calliope_presentation_memory_queue IS
    'Owner-scoped delivery ledger for Calliope presentation exchanges retained into Hindsight; never base-agent context.';
