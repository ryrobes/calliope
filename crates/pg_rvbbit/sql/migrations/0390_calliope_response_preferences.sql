-- 0390: authenticated response presentation and speech preferences.
--
-- These settings follow the human rather than a browser origin. Browser
-- storage remains an owner-keyed cache after authentication; it is not the
-- source of truth and must never expose one person's personality text to the
-- next person who signs in on the same browser.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_response_preferences (
    owner_email text PRIMARY KEY,
    conversational_answers boolean NOT NULL DEFAULT false,
    read_aloud_mode text NOT NULL DEFAULT 'off',
    response_personality text NOT NULL DEFAULT '',
    speak_working_updates boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_response_preferences_owner_check
        CHECK (owner_email=lower(btrim(owner_email)) AND owner_email LIKE '%@%'),
    CONSTRAINT calliope_response_preferences_mode_check
        CHECK (read_aloud_mode IN ('off','fast','expressive')),
    CONSTRAINT calliope_response_preferences_personality_check
        CHECK (
            length(response_personality) <= 600
            AND response_personality !~ '[[:cntrl:]]'
        )
);

COMMENT ON TABLE rvbbit.calliope_response_preferences IS
    'Private per-human conversational presentation and optional speech preferences shared across browsers and devices.';
COMMENT ON COLUMN rvbbit.calliope_response_preferences.response_personality IS
    'Explicit user-authored presentation direction; never inferred memory or base-model context.';
