-- 0401: SQL-native user provisioning for application accounts.
--
-- Shared-mode installs gate login with WAREHOUSE_ALLOWED_EMAILS — an env
-- edit and a container restart per new person, and no roster the appliance
-- can enumerate or manage. This makes provisioning a SQL statement instead:
-- rvbbit.provision_user() upserts the application principal and marks it
-- login-allowed; auth consults the flag alongside the env allowlist (OR,
-- never instead — existing env- and Google-verified identities are
-- unaffected, and an empty env allowlist still means "any email").
--
-- The roster IS rvbbit.application_principals (0252): provisioning
-- pre-declares a principal the login path would otherwise only observe
-- after the fact. The row carries its own receipt (who provisioned, when).
-- Burrow installs (WAREHOUSE_AUTH=pg) are unaffected: their subjects are PG
-- roles and login is decided by scram + rvbbit_users membership.

ALTER TABLE rvbbit.application_principals
    ADD COLUMN IF NOT EXISTS login_allowed boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS provisioned_by text,
    ADD COLUMN IF NOT EXISTS provisioned_at timestamptz;

COMMENT ON COLUMN rvbbit.application_principals.login_allowed IS
    'SQL-provisioned login permission (rvbbit.provision_user). Checked by shared-mode auth in addition to — never instead of — the WAREHOUSE_ALLOWED_EMAILS env allowlist.';

CREATE OR REPLACE FUNCTION rvbbit.provision_user(
    p_email text,
    p_display_name text DEFAULT NULL,
    p_provisioned_by text DEFAULT NULL
) RETURNS rvbbit.application_principals
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_email text := lower(btrim(p_email));
    v_row rvbbit.application_principals;
BEGIN
    IF v_email IS NULL OR v_email NOT LIKE '%@%' THEN
        RAISE EXCEPTION 'provision_user: % is not an email address', p_email;
    END IF;
    INSERT INTO rvbbit.application_principals AS ap
        (email, display_name, login_allowed, provisioned_by, provisioned_at,
         first_seen_at, last_seen_at, last_auth_mode, observation_count)
    VALUES
        (v_email, nullif(btrim(p_display_name), ''), true,
         nullif(btrim(p_provisioned_by), ''), now(), now(), now(),
         'provisioned', 0)
    ON CONFLICT (email) DO UPDATE SET
        display_name = coalesce(nullif(btrim(p_display_name), ''), ap.display_name),
        login_allowed = true,
        provisioned_by = coalesce(ap.provisioned_by, nullif(btrim(p_provisioned_by), '')),
        provisioned_at = coalesce(ap.provisioned_at, now())
    RETURNING * INTO v_row;
    RETURN v_row;
END
$fn$;

COMMENT ON FUNCTION rvbbit.provision_user(text, text, text) IS
    'Idempotent: declare an application account and allow it to log in (shared-mode installs). Re-provisioning an existing or retired principal reactivates it; observed logins are never overwritten, only marked allowed.';

CREATE OR REPLACE FUNCTION rvbbit.retire_user(p_email text)
RETURNS boolean
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_updated boolean;
BEGIN
    UPDATE rvbbit.application_principals
       SET login_allowed = false
     WHERE email = lower(btrim(p_email))
    RETURNING true INTO v_updated;
    RETURN coalesce(v_updated, false);
END
$fn$;

COMMENT ON FUNCTION rvbbit.retire_user(text) IS
    'Withdraw SQL-provisioned login permission. The principal row and its history remain (receipts are never deleted); an identity still matched by the env allowlist or Google can still log in.';
