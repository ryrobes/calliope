-- Reviewed correspondence. Contacts do not grant login or impersonation rights.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_contacts (
 id uuid PRIMARY KEY, owner_email text NOT NULL, name text NOT NULL,
 organization text NOT NULL DEFAULT '', role text NOT NULL DEFAULT '',
 principal_email text, facts jsonb NOT NULL DEFAULT '{}',
 summary text NOT NULL DEFAULT '', guidance text NOT NULL DEFAULT '',
 version integer NOT NULL DEFAULT 1, summarized_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_contact_channels (
 owner_email text NOT NULL, channel text NOT NULL CHECK(channel IN ('email','sms','external_id')),
 address text NOT NULL, contact_id uuid NOT NULL REFERENCES rvbbit.calliope_contacts(id),
 confirmed_by text NOT NULL, confirmed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_email,channel,address)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_contact_notes (
 id uuid PRIMARY KEY, contact_id uuid NOT NULL REFERENCES rvbbit.calliope_contacts(id),
 body text NOT NULL, source jsonb NOT NULL DEFAULT '{}', created_by text NOT NULL,
 superseded_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calliope_contact_notes_search_idx ON rvbbit.calliope_contact_notes
 USING gin(to_tsvector('simple',body));
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mailboxes (
 owner_email text PRIMARY KEY, address text NOT NULL UNIQUE, settings jsonb NOT NULL,
 credential_ref text NOT NULL, cursor jsonb, enabled boolean NOT NULL DEFAULT true,
 checked_at timestamptz, error text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mail_batches (
 id uuid PRIMARY KEY, owner_email text NOT NULL,
 session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE SET NULL,
 turn_id uuid REFERENCES rvbbit.calliope_turns(id) ON DELETE SET NULL,
 surface_id uuid REFERENCES rvbbit.calliope_surfaces(id) ON DELETE SET NULL, title text NOT NULL,
 request_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(session_id,request_key)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mail_drafts (
 id uuid PRIMARY KEY, batch_id uuid NOT NULL REFERENCES rvbbit.calliope_mail_batches(id),
 contact_id uuid NOT NULL REFERENCES rvbbit.calliope_contacts(id), ordinal integer NOT NULL,
 conversation_id uuid NOT NULL, reply_message_id text,
 recipient text NOT NULL, subject text NOT NULL DEFAULT '', body text NOT NULL DEFAULT '',
 attachments jsonb NOT NULL DEFAULT '[]', evidence_refs jsonb NOT NULL DEFAULT '[]',
 context jsonb NOT NULL DEFAULT '{}', context_token uuid NOT NULL,
 revision integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'preparing' CHECK(status IN ('preparing','draft','held','queued','sending','sent','failed','uncertain')),
 last_error text, updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(batch_id,ordinal)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mail_revisions (
 draft_id uuid NOT NULL REFERENCES rvbbit.calliope_mail_drafts(id), revision integer NOT NULL,
 snapshot jsonb NOT NULL, actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(draft_id,revision)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mail_outbox (
 id uuid PRIMARY KEY, draft_id uuid NOT NULL REFERENCES rvbbit.calliope_mail_drafts(id),
 revision integer NOT NULL, owner_email text NOT NULL, message_id text NOT NULL UNIQUE,
 snapshot jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
 result jsonb, started_at timestamptz, completed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_mail_outbox_active_idx ON rvbbit.calliope_mail_outbox(draft_id)
 WHERE state IN ('queued','sending','sent','uncertain');
CREATE TABLE IF NOT EXISTS rvbbit.calliope_mail_messages (
 id uuid PRIMARY KEY, owner_email text NOT NULL, contact_id uuid REFERENCES rvbbit.calliope_contacts(id),
 batch_id uuid REFERENCES rvbbit.calliope_mail_batches(id), conversation_id uuid NOT NULL,
 direction text NOT NULL CHECK(direction IN ('incoming','outgoing')),
 message_id text NOT NULL, reply_to text, sender text NOT NULL, recipient text NOT NULL,
 subject text NOT NULL, body text NOT NULL, attachments jsonb NOT NULL DEFAULT '[]',
 metadata jsonb NOT NULL DEFAULT '{}', received_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_email,message_id)
);
CREATE INDEX IF NOT EXISTS calliope_mail_messages_contact_idx ON rvbbit.calliope_mail_messages(contact_id,received_at DESC);
CREATE INDEX IF NOT EXISTS calliope_mail_messages_search_idx ON rvbbit.calliope_mail_messages
 USING gin(to_tsvector('simple',subject || ' ' || body));
CREATE TABLE IF NOT EXISTS rvbbit.calliope_contact_context_versions (
 contact_id uuid NOT NULL REFERENCES rvbbit.calliope_contacts(id), version integer NOT NULL,
 summary text NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(contact_id,version)
);
