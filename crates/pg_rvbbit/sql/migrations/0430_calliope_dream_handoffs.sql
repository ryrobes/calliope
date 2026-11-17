-- Explicit user-started Dream threads, queued through the existing mailbox.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_dream_handoffs (
    dream_id uuid NOT NULL REFERENCES rvbbit.calliope_dreams(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    claim_token uuid NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    surface_id uuid REFERENCES rvbbit.calliope_surfaces(id) ON DELETE SET NULL,
    message_id uuid,
    PRIMARY KEY(dream_id,owner_email)
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_dream_handoff_session_idx
    ON rvbbit.calliope_dream_handoffs(session_id) WHERE session_id IS NOT NULL;

-- Recover provenance for older interactive handoffs. This links existing work;
-- it never starts a turn. When several old threads exist, reuse the newest.
INSERT INTO rvbbit.calliope_dream_handoffs
    (dream_id,owner_email,claim_token,claimed_at,session_id,surface_id)
SELECT DISTINCT ON (d.id,s.owner_email)
    d.id,s.owner_email,gen_random_uuid(),su.created_at,s.id,su.id
FROM rvbbit.calliope_surfaces su
JOIN rvbbit.calliope_sessions s ON s.id=su.session_id
JOIN rvbbit.calliope_dreams d ON d.id::text=su.source->>'dream_id'
WHERE su.kind='dream' AND su.tool_name='calliope_dream_handoff'
    AND s.owner_email IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_dream_handoffs h
        WHERE h.session_id=s.id OR (h.dream_id=d.id AND h.owner_email=s.owner_email))
ORDER BY d.id,s.owner_email,su.created_at DESC,su.id DESC
ON CONFLICT DO NOTHING;
