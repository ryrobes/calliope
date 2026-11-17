-- 0324: an API-key provider may be configured by a credential Calliope does
-- not hold.
--
-- 0321's calliope_provider_state_credential_check required every configured
-- api_key provider to be either host-environment managed or to reference a
-- Calliope-owned rvbbit.credentials entry. The provider sync mirrors what
-- Hermes reports, and Hermes can legitimately hold a key Calliope never
-- installed: an operator ran `hermes auth add <provider>` in the container, or
-- a Calliope install succeeded in Hermes' credential pool but the state row
-- was rolled back. Mirroring that provider as configured raised the CHECK,
-- which aborted the whole sync transaction, so GET /api/calliope/admin/providers
-- answered 502 for every provider until the pool entry was removed by hand.
--
-- The invariant stays "a configured API-key provider says where its secret
-- lives" — the third answer is Hermes' own pool, recorded as
-- metadata.credential_holder = 'hermes' by the sync.

ALTER TABLE rvbbit.calliope_provider_state
    DROP CONSTRAINT IF EXISTS calliope_provider_state_credential_check;

ALTER TABLE rvbbit.calliope_provider_state
    ADD CONSTRAINT calliope_provider_state_credential_check CHECK (
        auth_kind <> 'api_key'
        OR auth_state <> 'configured'
        OR environment_managed
        OR credential_ref IS NOT NULL
        OR metadata->>'credential_holder' = 'hermes'
    );

COMMENT ON CONSTRAINT calliope_provider_state_credential_check
    ON rvbbit.calliope_provider_state IS
    'A configured api_key provider names its secret holder: the host environment, a Calliope credential_ref, or Hermes'' own credential pool (metadata.credential_holder = ''hermes'').';
