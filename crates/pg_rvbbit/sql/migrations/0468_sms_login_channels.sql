-- SMS identities are explicit login grants, not inferred contact matches.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_connection (
 id integer PRIMARY KEY CHECK(id=1), settings jsonb NOT NULL,
 credential_ref text NOT NULL, enabled boolean NOT NULL DEFAULT true,
 configured_by text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_bindings (
 id uuid PRIMARY KEY, phone_number text NOT NULL,
 owner_email text NOT NULL REFERENCES rvbbit.application_principals(email),
 enabled boolean NOT NULL DEFAULT true, configured_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_sms_active_number ON rvbbit.calliope_sms_bindings(phone_number) WHERE enabled;
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_sessions (
 session_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
 binding_id uuid NOT NULL REFERENCES rvbbit.calliope_sms_bindings(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_inbox (
 id uuid PRIMARY KEY, message_sid text NOT NULL UNIQUE,
 binding_id uuid NOT NULL REFERENCES rvbbit.calliope_sms_bindings(id),
 body text NOT NULL, media jsonb NOT NULL DEFAULT '[]',
 session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
 turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','delivering','delivered','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0, not_before timestamptz NOT NULL DEFAULT now(),
 last_error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calliope_sms_inbox_pending ON rvbbit.calliope_sms_inbox(state,not_before);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_replies (
 turn_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
 binding_id uuid NOT NULL REFERENCES rvbbit.calliope_sms_bindings(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sms_outbox (
 id uuid PRIMARY KEY, turn_id uuid NOT NULL REFERENCES rvbbit.calliope_sms_replies(turn_id) ON DELETE CASCADE,
 chunk_index integer NOT NULL, body text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','sending','sent','failed','uncertain','cancelled')),
 result jsonb NOT NULL DEFAULT '{}', started_at timestamptz, completed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(turn_id,chunk_index)
);

-- SMS owners can review native cards; the browser chat endpoint remains channel-read-only.

ALTER TABLE rvbbit.calliope_session_view_grants
    ADD COLUMN IF NOT EXISTS can_participate boolean NOT NULL DEFAULT false;
CREATE OR REPLACE FUNCTION rvbbit.calliope_session_can_participate(p_session_id uuid, p_subject text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.calliope_sessions s
        WHERE s.id=p_session_id AND NOT s.archived
          AND (coalesce(s.channel,'web')='web' OR
               (s.channel='sms' AND lower(s.owner_email)=lower(btrim(p_subject))))
          AND (lower(s.owner_email)=lower(btrim(p_subject)) OR EXISTS (
              SELECT 1 FROM rvbbit.calliope_session_view_grants g
              WHERE g.session_id=s.id AND g.can_participate
                AND g.principal_email=lower(btrim(p_subject))
          ))
    )
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.calliope_session_role(p_session_id uuid, p_subject text)
RETURNS text LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN lower(s.owner_email)=lower(btrim(p_subject)) THEN 'owner'
        WHEN rvbbit.calliope_session_can_participate(s.id,p_subject) THEN 'participant'
        ELSE 'viewer' END
    FROM rvbbit.calliope_sessions s WHERE s.id=p_session_id
$fn$;
