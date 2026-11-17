-- Browser identity stays an email; credentials are independent of PG roles.
CREATE TABLE IF NOT EXISTS rvbbit.application_accounts (
    email text PRIMARY KEY REFERENCES rvbbit.application_principals(email),
    enabled boolean NOT NULL DEFAULT true,
    password_secret text,
    credential_version integer NOT NULL DEFAULT 0,
    google_subject text UNIQUE,
    password_changed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.application_login_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    allowed_emails text[] NOT NULL DEFAULT '{}',
    google_client_id text NOT NULL DEFAULT '',
    google_client_secret text,
    google_domain text NOT NULL DEFAULT '',
    google_only boolean NOT NULL DEFAULT false,
    revision integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.application_account_links (
    token_hash text PRIMARY KEY,
    email text NOT NULL REFERENCES rvbbit.application_accounts(email),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.application_account_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor text NOT NULL,
    email text,
    action text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON rvbbit.application_accounts,rvbbit.application_login_settings,
    rvbbit.application_account_links,rvbbit.application_account_events FROM PUBLIC;

-- The older Team guard counts memberships. Also protect the last *active*
-- administrator after disabled accounts are retained in their groups.
CREATE OR REPLACE FUNCTION rvbbit._protect_active_account_admin()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.team_id='00000000-0000-4000-8000-000000000001'::uuid THEN
        PERFORM 1 FROM rvbbit.teams WHERE id=OLD.team_id FOR UPDATE;
        IF EXISTS(SELECT 1 FROM rvbbit.application_accounts WHERE email=OLD.principal_email AND enabled)
           AND NOT EXISTS(SELECT 1 FROM rvbbit.team_members m JOIN rvbbit.application_accounts a ON a.email=m.principal_email
               WHERE m.team_id=OLD.team_id AND m.principal_email<>OLD.principal_email AND a.enabled) THEN
            RAISE EXCEPTION 'Keep at least one active administrator';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS protect_active_account_admin ON rvbbit.team_members;
CREATE TRIGGER protect_active_account_admin BEFORE DELETE OR UPDATE OF team_id,principal_email ON rvbbit.team_members
FOR EACH ROW EXECUTE FUNCTION rvbbit._protect_active_account_admin();
