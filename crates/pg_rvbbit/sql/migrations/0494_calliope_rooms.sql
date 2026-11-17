-- 0494: rooms — a chat with more than one human, and artifacts that remember their chat.
--
-- 1. Every artifact made in a chat remembers that chat (dashboards.home_session_id).
--    The chat's members can see it and change it from that chat, so a room
--    revises one artifact instead of forking a copy per person. Existing
--    artifacts are tagged once: the chat that recorded version 1, otherwise the
--    earliest chat that showed any version, and only a chat the artifact's
--    owner can see (so nobody's Gallery "ask" chat becomes someone else's home).
-- 2. No spectators: every grant on a chat is a membership. People and the
--    members of granted Teams post as themselves; only a channel that can't
--    take messages here, or an archived chat, is read-only. Existing view-only
--    grants are converted once, with an audit event per converted chat. The
--    owner can make a person a manager (grant role 'manager'): managers also
--    rename, archive, share and set up the chat.
-- 3. Turns in a room: who stopped one ("Stopped by Bob"), who a message was
--    for (Callie, or the people in the room) and how that was decided, and the
--    original request of a message waiting its turn (status 'queued').
-- 4. Knocks (calliope_join_requests), who is in a room right now
--    (calliope_room_presence) and how far each member has read
--    (calliope_room_reads).
--
-- The Calliope service carries the same functions and tables in its startup
-- DDL (calliope_access.py, artifact_access.py, calliope_rooms.py, calliope.py);
-- change one, change both.

-- ── artifacts remember their chat ───────────────────────────────────────────
ALTER TABLE rvbbit.dashboards
    ADD COLUMN IF NOT EXISTS home_session_id uuid,
    ADD COLUMN IF NOT EXISTS home_set_at timestamptz;
CREATE INDEX IF NOT EXISTS dashboards_home_session_idx
    ON rvbbit.dashboards (home_session_id) WHERE home_session_id IS NOT NULL;

COMMENT ON COLUMN rvbbit.dashboards.home_session_id IS
    'The chat that made this artifact. Its members can see it and change it from that chat.';

CREATE OR REPLACE FUNCTION rvbbit.artifact_can_view(
    p_artifact_id bigint,
    p_subject text,
    p_include_archived boolean DEFAULT false
) RETURNS boolean
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_subject text := lower(btrim(p_subject));
    v_owner text;
    v_archived boolean;
    v_home uuid;
BEGIN
    IF p_subject IS NULL OR btrim(p_subject) NOT LIKE '%@%' THEN
        RETURN false;
    END IF;
    SELECT lower(d.owner_email), d.archived, d.home_session_id
      INTO v_owner, v_archived, v_home
      FROM rvbbit.dashboards d WHERE d.id=p_artifact_id;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF v_archived AND NOT (p_include_archived AND v_owner=v_subject) THEN
        RETURN false;
    END IF;
    IF v_owner=v_subject THEN
        RETURN true;
    END IF;
    IF EXISTS (
        SELECT 1 FROM rvbbit.artifact_view_grants g
        WHERE g.artifact_id=p_artifact_id AND g.principal_email=v_subject
    ) OR EXISTS (
        SELECT 1
        FROM rvbbit.artifact_view_grants g
        JOIN rvbbit.teams t ON t.id=g.team_id AND NOT t.archived
        WHERE g.artifact_id=p_artifact_id
          AND (
              t.system_key='everyone'
              OR EXISTS (
                  SELECT 1 FROM rvbbit.team_members m
                  WHERE m.team_id=t.id AND m.principal_email=v_subject
              )
          )
    ) THEN
        RETURN true;
    END IF;
    -- Members of the chat that made it see what the room made.
    IF v_home IS NOT NULL
       AND to_regprocedure('rvbbit.calliope_session_can_view(uuid,text,boolean)') IS NOT NULL THEN
        RETURN rvbbit.calliope_session_can_view(v_home, v_subject, false);
    END IF;
    RETURN false;
END
$fn$;

-- ── one kind of member ──────────────────────────────────────────────────────
ALTER TABLE rvbbit.calliope_session_view_grants
    ADD COLUMN IF NOT EXISTS can_participate boolean NOT NULL DEFAULT true;
ALTER TABLE rvbbit.calliope_session_view_grants
    ALTER COLUMN can_participate SET DEFAULT true;
ALTER TABLE rvbbit.calliope_session_view_grants
    ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'member';
DO $role$
BEGIN
    ALTER TABLE rvbbit.calliope_session_view_grants
        ADD CONSTRAINT calliope_session_view_grants_role_check
        CHECK (role IN ('member','manager') AND (role='member' OR principal_email IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL;
END
$role$;
COMMENT ON COLUMN rvbbit.calliope_session_view_grants.role IS
    'member, or manager: a person the owner lets rename, archive, share and set up the chat.';

CREATE OR REPLACE FUNCTION rvbbit.calliope_session_can_participate(p_session_id uuid, p_subject text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.calliope_sessions s
        WHERE s.id=p_session_id AND NOT s.archived
          AND rvbbit.calliope_session_can_view(s.id,p_subject,false)
          AND (coalesce(s.channel,'web')='web' OR
               (s.channel='sms' AND lower(s.owner_email)=lower(btrim(p_subject))))
    )
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.calliope_session_role(p_session_id uuid, p_subject text)
RETURNS text LANGUAGE sql STABLE AS $fn$
    SELECT CASE WHEN lower(s.owner_email)=lower(btrim(p_subject)) THEN 'owner'
        WHEN rvbbit.calliope_session_can_participate(s.id,p_subject) THEN
            CASE WHEN EXISTS (
                SELECT 1 FROM rvbbit.calliope_session_view_grants g
                WHERE g.session_id=s.id AND g.principal_email=lower(btrim(p_subject)) AND g.role='manager'
            ) THEN 'manager' ELSE 'member' END
        ELSE 'viewer' END
    FROM rvbbit.calliope_sessions s WHERE s.id=p_session_id
$fn$;

-- Convert existing view-only grants once, recording one audit event per chat.
DO $convert$
DECLARE
    converted record;
BEGIN
    FOR converted IN
        WITH changed AS (
            UPDATE rvbbit.calliope_session_view_grants g
               SET can_participate=true
             WHERE NOT g.can_participate
            RETURNING g.session_id, g.principal_email, g.team_id
        )
        SELECT c.session_id,
               array_agg(c.principal_email ORDER BY c.principal_email)
                   FILTER (WHERE c.principal_email IS NOT NULL) AS people,
               array_agg(c.team_id::text ORDER BY c.team_id::text)
                   FILTER (WHERE c.team_id IS NOT NULL) AS team_ids
          FROM changed c
         GROUP BY c.session_id
    LOOP
        INSERT INTO rvbbit.calliope_session_access_events
            (event_id, session_id, event_type, credential_actor, human_subject, auth_mode,
             delegated, before_state, after_state, detail)
        VALUES (
            gen_random_uuid(), converted.session_id, 'rooms_membership_converted',
            'system:0494', 'system:0494', 'migration', false,
            jsonb_build_object('view_only_people', coalesce(to_jsonb(converted.people), '[]'::jsonb),
                               'view_only_team_ids', coalesce(to_jsonb(converted.team_ids), '[]'::jsonb)),
            jsonb_build_object('members', coalesce(to_jsonb(converted.people), '[]'::jsonb)),
            jsonb_build_object('reason', 'Rooms have members, not spectators (0494).')
        );
    END LOOP;
END
$convert$;

-- ── turns in a room ─────────────────────────────────────────────────────────
ALTER TABLE rvbbit.calliope_turns
    ADD COLUMN IF NOT EXISTS stopped_by text,
    ADD COLUMN IF NOT EXISTS addressed_to text,
    ADD COLUMN IF NOT EXISTS routing jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS queued_request jsonb;
CREATE INDEX IF NOT EXISTS calliope_turns_queued_idx
    ON rvbbit.calliope_turns (session_id, ordinal) WHERE status='queued';

-- ── knocks, presence, reads ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rvbbit.calliope_join_requests (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    requester_email text NOT NULL,
    reason text NOT NULL DEFAULT '',
    via text NOT NULL DEFAULT 'floor',
    artifact_slug text,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','declined','withdrawn')),
    turn_id uuid,
    input_request_id uuid,
    decided_by text,
    decided_at timestamptz,
    reply text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_join_requests_pending_key
    ON rvbbit.calliope_join_requests (session_id, requester_email)
    WHERE status='pending';
CREATE INDEX IF NOT EXISTS calliope_join_requests_requester_idx
    ON rvbbit.calliope_join_requests (requester_email, created_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_room_presence (
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    email text NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, email)
);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_room_reads (
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    email text NOT NULL,
    last_read_ordinal integer NOT NULL DEFAULT 0,
    last_read_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, email)
);

-- ── tag existing artifacts with the chat that made them ─────────────────────
WITH candidates AS (
    SELECT DISTINCT ON (d.id)
           d.id AS artifact_id, f.session_id
      FROM rvbbit.dashboards d
      JOIN rvbbit.calliope_surfaces f ON f.artifact_slug = d.slug
      JOIN rvbbit.calliope_sessions s ON s.id = f.session_id
     WHERE d.home_session_id IS NULL
       AND rvbbit.calliope_session_can_view(f.session_id, lower(d.owner_email), true)
     ORDER BY d.id,
              (f.artifact_version = 1) DESC NULLS LAST,
              f.created_at, f.id
)
UPDATE rvbbit.dashboards d
   SET home_session_id = c.session_id,
       home_set_at = now()
  FROM candidates c
 WHERE d.id = c.artifact_id AND d.home_session_id IS NULL;
