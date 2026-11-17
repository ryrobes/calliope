-- 0377: generated images — the appliance's image provider, agent-usable.
--
-- The expert-portraits lane (0376) generalizes into a first-class
-- capability: the same configured OpenAI-images-shaped endpoint now backs
-- a generate_image MCP tool every agent can call. Images store as bytea
-- (they replicate) and serve through an owner-gated route.

CREATE TABLE IF NOT EXISTS rvbbit.generated_images (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_email text NOT NULL,
    prompt text NOT NULL,
    model text,
    png bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS generated_images_owner_idx
    ON rvbbit.generated_images (owner_email, created_at DESC);
