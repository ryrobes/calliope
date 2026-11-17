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
ALTER TABLE rvbbit.calliope_sms_bindings ADD COLUMN IF NOT EXISTS transport text NOT NULL DEFAULT 'twilio'
 CHECK (transport IN ('twilio','simulator'));
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
