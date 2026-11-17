-- Workspace defaults remain separate from personal choices. NULL color_mode inherits.

ALTER TABLE rvbbit.calliope_appearance_preferences ALTER COLUMN color_mode DROP NOT NULL;
ALTER TABLE rvbbit.calliope_appearance_preferences ALTER COLUMN color_mode DROP DEFAULT;
CREATE TABLE IF NOT EXISTS rvbbit.calliope_workspace_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    appearance jsonb CHECK (appearance IS NULL OR jsonb_typeof(appearance)='object'),
    color_mode text NOT NULL DEFAULT 'dark' CHECK (color_mode IN ('dark','light')),
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
