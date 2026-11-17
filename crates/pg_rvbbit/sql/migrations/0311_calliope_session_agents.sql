-- Let a normal Calliope notebook be owned by one Hermes specialist profile.
-- The durable specialist key remains the stable product identity; Hermes
-- profile names can evolve independently behind the registry.
ALTER TABLE rvbbit.calliope_sessions
    ADD COLUMN IF NOT EXISTS specialist_key text;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'calliope_sessions_specialist_key_fkey'
          AND conrelid = 'rvbbit.calliope_sessions'::regclass
    ) THEN
        ALTER TABLE rvbbit.calliope_sessions
            ADD CONSTRAINT calliope_sessions_specialist_key_fkey
            FOREIGN KEY (specialist_key)
            REFERENCES rvbbit.calliope_specialists(specialist_key)
            ON UPDATE CASCADE
            ON DELETE SET NULL;
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS calliope_sessions_specialist_idx
    ON rvbbit.calliope_sessions (specialist_key)
    WHERE specialist_key IS NOT NULL;

COMMENT ON COLUMN rvbbit.calliope_sessions.specialist_key IS
    'Optional persistent Hermes specialist profile that owns this direct chat notebook.';
