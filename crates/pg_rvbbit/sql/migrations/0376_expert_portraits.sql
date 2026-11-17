-- 0376: expert portraits — a face for every factory-minted specialist.
--
-- Generated headshots in the house style (the Mucha-esque Art Nouveau of
-- the hand-made team) via any OpenAI-images-shaped endpoint. Config lives
-- in rvbbit.settings under 'expert_portraits' ({"model": ..., optional
-- "base_url", optional "api_key_env"}); secrets stay in environment
-- variables, settings hold only references. Unconfigured appliances mint
-- agents portrait-less and backfill automatically once configured.

CREATE TABLE IF NOT EXISTS rvbbit.generated_specialist_avatars (
    specialist_key text PRIMARY KEY
        REFERENCES rvbbit.generated_specialists(specialist_key) ON DELETE CASCADE,
    png bytea NOT NULL,
    model text,
    prompt text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO rvbbit.settings (key, value, updated_at)
SELECT 'expert_portraits',
       '{"model": null, "base_url": null, "api_key_env": null}'::jsonb,
       now()
WHERE NOT EXISTS (SELECT 1 FROM rvbbit.settings WHERE key='expert_portraits');
