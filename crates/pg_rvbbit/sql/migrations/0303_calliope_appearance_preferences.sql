-- 0303: one authenticated appearance preference shared by Calliope and DataRabbit.
--
-- Browser storage is scoped to an origin, so the hosted appliance's direct
-- DataRabbit and Calliope ports cannot synchronize through localStorage or
-- IndexedDB. Keep the compact theme contract and optional uploaded wallpaper
-- under the signed human identity instead; browser stores remain first-paint
-- caches rather than competing sources of truth.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_appearance_preferences (
    owner_email text PRIMARY KEY,
    color_mode text NOT NULL DEFAULT 'dark',
    appearance jsonb,
    upload_name text,
    upload_mime text,
    upload_bytes bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_appearance_color_mode_check
        CHECK (color_mode IN ('dark','light')),
    CONSTRAINT calliope_appearance_object_check
        CHECK (appearance IS NULL OR jsonb_typeof(appearance)='object'),
    CONSTRAINT calliope_appearance_upload_check
        CHECK (
            (upload_bytes IS NULL AND upload_mime IS NULL)
            OR (upload_bytes IS NOT NULL AND upload_mime IS NOT NULL)
        )
);

COMMENT ON TABLE rvbbit.calliope_appearance_preferences IS
    'Per-human hosted appearance shared across Calliope, DataRabbit, browser origins, and devices.';
