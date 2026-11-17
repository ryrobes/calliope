"""Application-layer sharing and lifecycle for published artifacts.

An artifact's owner manages it: sharing, archive, and handing it to someone
else. Members of the Admins Team can do all of that for any artifact, private
ones included, and change any artifact's content; every admin action is
recorded with ``acting_as: admin`` in its access receipts. Lists still show an
admin only what is shared with them. View grants may target one observed human
or one flat application Team. The protected Everyone Team
is resolved dynamically by ``rvbbit.artifact_can_view`` against Warehouse's
already-verified human subject; it is never a public/anonymous grant.

Every artifact made in a chat remembers that chat (``home_session_id``). The
chat's members can see it, and can change it from that chat, so a room revises
one artifact instead of forking it. Sharing and archiving stay with the owner.

Archive is deliberately orthogonal to sharing. It suppresses every ordinary
viewer path while retaining grants, immutable versions, lineage, pins, and
receipts for a reversible restore.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

import application_teams


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$", re.I)


class ArtifactAccessError(Exception):
    """Typed error shared by Gallery routes and MCP tools."""

    def __init__(
        self, code: str, message: str, status: int = 400, detail: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail or {}


# 0254 installs the same shape for extension upgrades. This additive copy lets
# a Warehouse image self-heal safely when it starts ahead of pg_rvbbit. The
# singleton marker makes the existing-artifact Everyone grant genuinely
# one-time: artifacts published after installation remain private.
DDL = """
ALTER TABLE rvbbit.dashboards
    ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS archived_at timestamptz,
    ADD COLUMN IF NOT EXISTS archived_by text,
    ADD COLUMN IF NOT EXISTS access_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS home_session_id uuid,
    ADD COLUMN IF NOT EXISTS home_set_at timestamptz;
CREATE INDEX IF NOT EXISTS dashboards_home_session_idx
    ON rvbbit.dashboards (home_session_id) WHERE home_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS dashboards_active_updated_idx
    ON rvbbit.dashboards (updated_at DESC) WHERE NOT archived;
CREATE INDEX IF NOT EXISTS dashboards_owner_archived_idx
    ON rvbbit.dashboards (lower(owner_email),archived,updated_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.artifact_view_grants (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artifact_id bigint NOT NULL REFERENCES rvbbit.dashboards(id) ON DELETE CASCADE,
    team_id uuid REFERENCES rvbbit.teams(id) ON DELETE CASCADE,
    principal_email text REFERENCES rvbbit.application_principals(email) ON DELETE CASCADE,
    granted_by text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifact_view_grants_one_grantee_check CHECK (
        num_nonnulls(team_id,principal_email)=1
    ),
    CONSTRAINT artifact_view_grants_email_normalized_check CHECK (
        principal_email IS NULL OR
        (principal_email=lower(btrim(principal_email)) AND principal_email LIKE '%@%')
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS artifact_view_grants_team_key
    ON rvbbit.artifact_view_grants (artifact_id,team_id) WHERE team_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS artifact_view_grants_person_key
    ON rvbbit.artifact_view_grants (artifact_id,principal_email)
    WHERE principal_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS artifact_view_grants_team_lookup_idx
    ON rvbbit.artifact_view_grants (team_id,artifact_id) WHERE team_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS artifact_view_grants_person_lookup_idx
    ON rvbbit.artifact_view_grants (principal_email,artifact_id)
    WHERE principal_email IS NOT NULL;

CREATE TABLE IF NOT EXISTS rvbbit.artifact_access_events (
    event_id uuid PRIMARY KEY,
    artifact_id bigint,
    artifact_slug text NOT NULL,
    event_type text NOT NULL,
    credential_actor text,
    human_subject text NOT NULL,
    auth_mode text NOT NULL,
    delegated boolean NOT NULL DEFAULT false,
    platform text,
    session_ref text,
    before_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    after_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifact_access_events_payload_check CHECK (
        jsonb_typeof(before_state)='object'
        AND jsonb_typeof(after_state)='object'
        AND jsonb_typeof(detail)='object'
    )
);
-- Audit receipts outlive their source row even if a database administrator
-- performs an out-of-band hard delete. Application surfaces only archive.
ALTER TABLE rvbbit.artifact_access_events
    DROP CONSTRAINT IF EXISTS artifact_access_events_artifact_id_fkey;
CREATE INDEX IF NOT EXISTS artifact_access_events_artifact_created_idx
    ON rvbbit.artifact_access_events (artifact_id,created_at DESC);
CREATE INDEX IF NOT EXISTS artifact_access_events_subject_created_idx
    ON rvbbit.artifact_access_events (human_subject,created_at DESC);

CREATE OR REPLACE FUNCTION rvbbit._artifact_access_events_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'Artifact access events are append-only';
END
$fn$;
DROP TRIGGER IF EXISTS artifact_access_events_append_only ON rvbbit.artifact_access_events;
CREATE TRIGGER artifact_access_events_append_only
BEFORE UPDATE OR DELETE ON rvbbit.artifact_access_events
FOR EACH ROW EXECUTE FUNCTION rvbbit._artifact_access_events_append_only();

CREATE TABLE IF NOT EXISTS rvbbit.artifact_access_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    grandfathered_at timestamptz NOT NULL DEFAULT now(),
    grandfathered_count integer NOT NULL DEFAULT 0
);

DO $do$
DECLARE
    first_install boolean := false;
    granted_count integer := 0;
BEGIN
    INSERT INTO rvbbit.artifact_access_state (singleton)
    VALUES (true)
    ON CONFLICT (singleton) DO NOTHING
    RETURNING true INTO first_install;

    IF coalesce(first_install,false) THEN
        INSERT INTO rvbbit.artifact_view_grants
            (artifact_id,team_id,granted_by)
        SELECT d.id,t.id,'system:0254-grandfather'
        FROM rvbbit.dashboards d
        CROSS JOIN rvbbit.teams t
        WHERE t.system_key='everyone'
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS granted_count = ROW_COUNT;
        UPDATE rvbbit.artifact_access_state
        SET grandfathered_count=granted_count
        WHERE singleton;
    END IF;
END
$do$;

-- plpgsql so the home-chat check can name the Calliope membership function,
-- which a fresh service may install after this DDL runs.
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

CREATE OR REPLACE VIEW rvbbit.live_apps AS
  SELECT d.id, d.slug, d.name, d.description, d.owner_email, d.team, d.status,
         d.runtime_kind, d.app_kind, d.latest_version, d.manifest, d.last_health,
         d.last_debug_at, d.created_at, d.updated_at,
         coalesce(dep.queries, 0)::int AS queries,
         coalesce(dep.tables, 0)::int AS tables,
         coalesce(dep.metrics, 0)::int AS metrics,
         coalesce(dep.semantic_objects, 0)::int AS semantic_objects,
         d.area_id,area.label AS area_label,d.area_source,d.area_confidence,d.area_updated_at,
         d.archived,d.archived_at,d.archived_by,d.access_revision
  FROM rvbbit.dashboards d
  LEFT JOIN rvbbit.artifact_areas area ON area.id=d.area_id
  LEFT JOIN LATERAL (
    SELECT
           count(*) FILTER (WHERE kind = 'query') AS queries,
           count(*) FILTER (WHERE kind = 'table') AS tables,
           count(*) FILTER (WHERE kind = 'metric') AS metrics,
           count(*) FILTER (WHERE kind = 'semantic') AS semantic_objects
    FROM rvbbit.dashboard_deps
    WHERE dashboard_id = d.id AND version = d.latest_version
  ) dep ON true;
"""


def ensure_tables(conn_factory: Callable[..., Any]) -> None:
    with conn_factory() as conn:
        conn.execute(DDL)


def _auth_value(authorization: Any, name: str, default: Any = None) -> Any:
    if isinstance(authorization, dict):
        return authorization.get(name, default)
    return getattr(authorization, name, default)


def _subject(authorization: Any) -> str:
    value = _auth_value(authorization, "subject")
    if not value:
        raise ArtifactAccessError(
            "APPLICATION_SUBJECT_REQUIRED",
            "Artifact access requires a direct signed-in user or trusted Calliope delegation.",
            403,
        )
    try:
        return application_teams.normalize_email(value)
    except application_teams.TeamError as exc:
        raise ArtifactAccessError("APPLICATION_SUBJECT_REQUIRED", str(exc), 403) from exc


def _slug(value: Any) -> str:
    slug = str(value or "").strip()
    if not _SLUG_RE.fullmatch(slug):
        raise ArtifactAccessError("INVALID_ARTIFACT", "Choose a valid published artifact.")
    return slug


def _uuid_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        raise ArtifactAccessError("INVALID_GRANTS", "Team grants must be supplied as a list.")
    result: list[str] = []
    for value in values:
        try:
            normalized = str(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            raise ArtifactAccessError("INVALID_GRANTS", "Choose valid Teams to share with.") from None
        if normalized not in result:
            result.append(normalized)
    return result


def _email_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        raise ArtifactAccessError("INVALID_GRANTS", "Individual grants must be supplied as a list.")
    result: list[str] = []
    for value in values:
        try:
            email = application_teams.normalize_email(value)
        except application_teams.TeamError as exc:
            raise ArtifactAccessError("INVALID_GRANTS", str(exc)) from exc
        if email not in result:
            result.append(email)
    return result


def _event_auth(authorization: Any) -> dict[str, Any]:
    subject = _subject(authorization)
    return {
        "credential_actor": str(_auth_value(authorization, "actor") or subject)[:320],
        "human_subject": subject,
        "auth_mode": str(_auth_value(authorization, "mode") or "application")[:80],
        "delegated": bool(_auth_value(authorization, "delegated", False)),
        "platform": str(_auth_value(authorization, "platform") or "")[:80] or None,
        "session_ref": str(_auth_value(authorization, "session_ref") or "")[:500] or None,
    }


def _record_event(
    conn: Any,
    artifact: dict[str, Any],
    event_type: str,
    authorization: Any,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    acting_as: str | None = None,
) -> None:
    auth = _event_auth(authorization)
    if acting_as == "admin":
        detail = {**(detail or {}), "acting_as": "admin"}
    conn.execute(
        "INSERT INTO rvbbit.artifact_access_events "
        "(event_id,artifact_id,artifact_slug,event_type,credential_actor,human_subject,"
        "auth_mode,delegated,platform,session_ref,before_state,after_state,detail) "
        "VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)",
        (
            str(uuid.uuid4()), artifact["id"], artifact["slug"], event_type,
            auth["credential_actor"], auth["human_subject"], auth["auth_mode"],
            auth["delegated"], auth["platform"], auth["session_ref"],
            json.dumps(before or {}), json.dumps(after or {}), json.dumps(detail or {}),
        ),
    )


def is_admin(conn: Any, subject: Any) -> bool:
    """A member of the protected Admins Team."""
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM rvbbit.team_members m JOIN rvbbit.teams t ON t.id=m.team_id "
        "WHERE t.system_key='admins' AND NOT t.archived AND m.principal_email=lower(%s)) AS allowed",
        (str(subject or ""),),
    ).fetchone() or {}
    return bool(row.get("allowed"))


def can_view(conn: Any, slug: Any, subject: Any, *, include_archived: bool = False) -> bool:
    """Resolve one artifact against a subject already verified by Warehouse.

    An admin may open any artifact by its slug (archived ones only where the
    owner could too); lists keep using ``rvbbit.artifact_can_view`` alone."""
    try:
        normalized_slug = _slug(slug)
        normalized_subject = application_teams.normalize_email(subject)
    except (ArtifactAccessError, application_teams.TeamError):
        return False
    row = conn.execute(
        "SELECT rvbbit.artifact_can_view(id,%s,%s) AS allowed, archived "
        "FROM rvbbit.dashboards WHERE slug=%s",
        (normalized_subject, bool(include_archived), normalized_slug),
    ).fetchone() or {}
    if row.get("allowed"):
        return True
    return bool(row) and (include_archived or not row.get("archived")) and is_admin(conn, normalized_subject)


def require_view(conn: Any, slug: Any, subject: Any) -> dict[str, Any]:
    normalized_slug = _slug(slug)
    normalized_subject = application_teams.normalize_email(subject)
    row = conn.execute(
        "SELECT id,slug,name,owner_email,app_kind,runtime_kind,latest_version,archived,"
        "access_revision,rvbbit.artifact_can_view(id,%s,false) AS allowed "
        "FROM rvbbit.dashboards WHERE slug=%s",
        (normalized_subject, normalized_slug),
    ).fetchone()
    if not row or not (row.get("allowed") or (not row.get("archived") and is_admin(conn, normalized_subject))):
        raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "That artifact is not available.", 404)
    return dict(row)


def require_owner(conn: Any, slug: Any, authorization: Any) -> dict[str, Any]:
    """Require someone who manages the artifact: its owner, or an admin."""
    return _owner_artifact(conn, slug, _subject(authorization))


def require_editor(
    conn: Any,
    slug: Any,
    authorization: Any,
    *,
    session_id: Any = None,
    lock: bool = False,
) -> dict[str, Any]:
    """Who may change an artifact's content: its owner or an admin from
    anywhere, or a member of its home chat working in that chat.

    The returned row carries ``editor_role`` (``owner``, ``admin`` or ``member``) and
    ``home_is_room`` (the home chat has members besides its owner), which
    callers use to require ``expected_version`` so two people's edits can't
    silently overwrite each other. Sharing and archiving stay owner-only.
    """
    subject = _subject(authorization)
    suffix = " FOR UPDATE OF d" if lock else ""
    row = conn.execute(
        "SELECT d.id,d.slug,d.name,d.description,d.owner_email,d.app_kind,d.runtime_kind,"
        "d.latest_version,d.archived,d.archived_at,d.archived_by,d.access_revision,d.updated_at,"
        "d.home_session_id::text AS home_session_id,"
        "(d.home_session_id IS NOT NULL AND EXISTS (SELECT 1 FROM rvbbit.calliope_session_view_grants g "
        " WHERE g.session_id=d.home_session_id)) AS home_is_room "
        "FROM rvbbit.dashboards d WHERE d.slug=%s" + suffix,
        (_slug(slug),),
    ).fetchone()
    if not row:
        raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "That artifact is not available.", 404)
    artifact = dict(row)
    home = artifact.get("home_session_id")
    if str(artifact.get("owner_email") or "").strip().lower() == subject:
        artifact["editor_role"] = "owner"
        return artifact
    member_of_home = bool(home) and bool((conn.execute(
        "SELECT rvbbit.calliope_session_can_participate(%s::uuid,%s) AS ok", (home, subject)
    ).fetchone() or {}).get("ok"))
    if member_of_home and session_id and str(session_id) == home:
        artifact["editor_role"] = "member"
        return artifact
    if is_admin(conn, subject):
        # Admins change any artifact, private ones included, from any chat;
        # like room members they must name the version they edited.
        artifact["editor_role"] = "admin"
        return artifact
    visible = bool((conn.execute(
        "SELECT rvbbit.artifact_can_view(%s,%s,false) AS ok", (artifact["id"], subject)
    ).fetchone() or {}).get("ok"))
    if not visible:
        # Do not disclose whether an inaccessible artifact exists.
        raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "That artifact is not available.", 404)
    if member_of_home:
        raise ArtifactAccessError(
            "ARTIFACT_EDIT_IN_HOME_CHAT",
            "This artifact belongs to another chat. Change it from that chat "
            f"(/calliope?session={home}); don't publish a replacement.",
            403,
            {"home_session_id": home, "owner": artifact.get("owner_email")},
        )
    raise ArtifactAccessError(
        "ARTIFACT_OWNER_ONLY",
        f"Only its owner ({artifact.get('owner_email')}), an admin, or the people in its chat can change "
        "this artifact. Don't publish a replacement; tell the person asking who can change it.",
        403,
        {"owner": artifact.get("owner_email"), "home_session_id": home},
    )


def _owner_artifact(
    conn: Any, slug: Any, subject: str, *, lock: bool = False
) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = conn.execute(
        "SELECT id,slug,name,description,owner_email,app_kind,runtime_kind,latest_version,"
        "archived,archived_at,archived_by,access_revision,updated_at "
        "FROM rvbbit.dashboards WHERE slug=%s" + suffix,
        (_slug(slug),),
    ).fetchone()
    if not row:
        raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "That artifact is not available.", 404)
    artifact = dict(row)
    if str(artifact.get("owner_email") or "").strip().lower() == subject:
        artifact["manager_role"] = "owner"
        return artifact
    if is_admin(conn, subject):
        artifact["manager_role"] = "admin"
        return artifact
    # Do not disclose whether an inaccessible artifact exists.
    raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "That artifact is not available.", 404)


def _grants(conn: Any, artifact_id: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    teams = [dict(row) for row in conn.execute(
        "SELECT t.id::text,t.slug,t.name,t.description,t.system_key,t.archived "
        "FROM rvbbit.artifact_view_grants g JOIN rvbbit.teams t ON t.id=g.team_id "
        "WHERE g.artifact_id=%s ORDER BY CASE t.system_key WHEN 'everyone' THEN 0 "
        "WHEN 'admins' THEN 1 ELSE 2 END,t.name",
        (artifact_id,),
    ).fetchall()]
    people = [dict(row) for row in conn.execute(
        "SELECT g.principal_email AS email,p.display_name,p.last_seen_at "
        "FROM rvbbit.artifact_view_grants g "
        "LEFT JOIN rvbbit.application_principals p ON p.email=g.principal_email "
        "WHERE g.artifact_id=%s AND g.principal_email IS NOT NULL "
        "ORDER BY coalesce(p.display_name,g.principal_email),g.principal_email",
        (artifact_id,),
    ).fetchall()]
    return teams, people


def _team_options(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.id::text,t.slug,t.name,t.description,t.system_key,t.revision,"
        "count(m.principal_email)::int AS member_count "
        "FROM rvbbit.teams t LEFT JOIN rvbbit.team_members m ON m.team_id=t.id "
        "WHERE NOT t.archived GROUP BY t.id ORDER BY CASE t.system_key "
        "WHEN 'everyone' THEN 0 WHEN 'admins' THEN 1 ELSE 2 END,t.name"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        dynamic = item.get("system_key") == "everyone"
        item["dynamic_membership"] = dynamic
        item["membership_rule"] = "authenticated_users" if dynamic else "explicit_members"
        item["member_count"] = None if dynamic else int(item.get("member_count") or 0)
        result.append(item)
    return result


def _snapshot(
    conn: Any, artifact: dict[str, Any], *, include_events: bool = False, subject: str | None = None
) -> dict[str, Any]:
    teams, people = _grants(conn, artifact["id"])
    everyone = any(team.get("system_key") == "everyone" for team in teams)
    if everyone:
        summary = "Everyone signed in"
    elif teams or people:
        pieces = []
        if teams:
            pieces.append(f"{len(teams)} Team{'s' if len(teams) != 1 else ''}")
        if people:
            pieces.append(f"{len(people)} person{'s' if len(people) != 1 else ''}")
        summary = "Shared with " + " and ".join(pieces)
    elif subject and subject != str(artifact.get("owner_email") or "").strip().lower():
        summary = "Only its owner"
    else:
        summary = "Only you"
    result = {
        "artifact": {
            **artifact,
            "id": int(artifact["id"]),
            "archived": bool(artifact.get("archived")),
            "access_revision": int(artifact.get("access_revision") or 1),
        },
        "grants": {"teams": teams, "people": people},
        "summary": summary,
        "private": not teams and not people,
        "everyone": everyone,
        "manager_role": artifact.get("manager_role") or "owner",
    }
    if include_events:
        result["events"] = [dict(row) for row in conn.execute(
            "SELECT event_id::text,event_type,credential_actor,human_subject,auth_mode,"
            "delegated,platform,session_ref,before_state,after_state,detail,created_at "
            "FROM rvbbit.artifact_access_events WHERE artifact_id=%s "
            "ORDER BY created_at DESC,event_id DESC LIMIT 30",
            (artifact["id"],),
        ).fetchall()]
    return result


def get_access(
    conn_factory: Callable[..., Any], authorization: Any, slug: Any
) -> dict[str, Any]:
    subject = _subject(authorization)
    with conn_factory() as conn:
        artifact = _owner_artifact(conn, slug, subject)
        result = _snapshot(conn, artifact, include_events=True, subject=subject)
        result["subject"] = subject
        result["is_admin"] = artifact["manager_role"] == "admin" or is_admin(conn, subject)
        result["teams"] = _team_options(conn)
        return result


def search_people(
    conn_factory: Callable[..., Any], authorization: Any, query: Any = "", limit: Any = 100
) -> dict[str, Any]:
    subject = _subject(authorization)
    normalized = re.sub(r"\s+", " ", str(query or "").strip())[:240]
    terms = [term for term in re.split(r"[\W_]+", normalized.casefold()) if term][:16]
    patterns = [f"%{term}%" for term in terms]
    try:
        bounded = max(1, min(int(limit or 100), 300))
    except (TypeError, ValueError):
        bounded = 100
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT email,display_name,last_seen_at,last_auth_mode,last_channel "
            "FROM rvbbit.application_principals WHERE email<>%s AND ("
            "cardinality(%s::text[])=0 "
            "OR lower(concat_ws(' ',email,display_name)) ILIKE ALL(%s::text[])) "
            "ORDER BY last_seen_at DESC,email LIMIT %s",
            (subject, patterns, patterns, bounded),
        ).fetchall()
    return {"subject": subject, "people": [dict(row) for row in rows], "count": len(rows)}


def replace_access(
    conn_factory: Callable[..., Any],
    authorization: Any,
    slug: Any,
    expected_revision: Any,
    *,
    team_ids: Any = None,
    people: Any = None,
    confirm_everyone: bool = False,
) -> dict[str, Any]:
    subject = _subject(authorization)
    desired_teams = _uuid_list(team_ids)
    requested_people = _email_list(people)
    if isinstance(expected_revision, bool):
        raise ArtifactAccessError("INVALID_REVISION", "Inspect the current sharing revision first.")
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ArtifactAccessError("INVALID_REVISION", "Inspect the current sharing revision first.") from None

    with conn_factory() as conn:
        with conn.transaction():
            artifact = _owner_artifact(conn, slug, subject, lock=True)
            owner = str(artifact.get("owner_email") or "").strip().lower()
            desired_people = [email for email in requested_people if email != owner]
            if int(artifact.get("access_revision") or 1) != revision:
                raise ArtifactAccessError(
                    "ARTIFACT_ACCESS_CONFLICT",
                    "Sharing changed while it was open. Refresh before saving.",
                    409,
                )
            team_rows = conn.execute(
                "SELECT id::text,name,system_key FROM rvbbit.teams "
                "WHERE id=ANY(%s::uuid[]) AND NOT archived",
                (desired_teams,),
            ).fetchall() if desired_teams else []
            found_team_ids = {str(row["id"]) for row in team_rows}
            missing_teams = [team_id for team_id in desired_teams if team_id not in found_team_ids]
            if missing_teams:
                raise ArtifactAccessError("TEAM_NOT_FOUND", "One selected Team is no longer available.", 409)
            if desired_people:
                observed = {
                    str(row["email"]) for row in conn.execute(
                        "SELECT email FROM rvbbit.application_principals WHERE email=ANY(%s::text[])",
                        (desired_people,),
                    ).fetchall()
                }
                missing_people = [email for email in desired_people if email not in observed]
                if missing_people:
                    raise ArtifactAccessError(
                        "PERSON_NOT_OBSERVED",
                        "These people have not signed in through a trusted application surface: "
                        + ", ".join(missing_people[:8]),
                    )
            current_teams, current_people = _grants(conn, artifact["id"])
            current_team_ids = {str(team["id"]) for team in current_teams}
            current_emails = {str(person["email"]) for person in current_people}
            desired_team_set = set(desired_teams)
            desired_email_set = set(desired_people)
            added_team_ids = desired_team_set - current_team_ids
            everyone_added = any(
                str(row["id"]) in added_team_ids and row.get("system_key") == "everyone"
                for row in team_rows
            )
            if everyone_added and confirm_everyone is not True:
                raise ArtifactAccessError(
                    "EVERYONE_CONFIRMATION_REQUIRED",
                    "Confirm explicitly before sharing this artifact with Everyone.",
                    409,
                )
            removed_team_ids = current_team_ids - desired_team_set
            added_people = desired_email_set - current_emails
            removed_people = current_emails - desired_email_set
            if not (added_team_ids or removed_team_ids or added_people or removed_people):
                result = _snapshot(conn, artifact, include_events=True, subject=subject)
                result.update({"subject": subject, "changed": False})
                return result
            before = {
                "revision": revision,
                "team_ids": sorted(current_team_ids),
                "people": sorted(current_emails),
                "archived": bool(artifact.get("archived")),
            }
            if removed_team_ids:
                conn.execute(
                    "DELETE FROM rvbbit.artifact_view_grants "
                    "WHERE artifact_id=%s AND team_id=ANY(%s::uuid[])",
                    (artifact["id"], sorted(removed_team_ids)),
                )
            if removed_people:
                conn.execute(
                    "DELETE FROM rvbbit.artifact_view_grants "
                    "WHERE artifact_id=%s AND principal_email=ANY(%s::text[])",
                    (artifact["id"], sorted(removed_people)),
                )
            for team_id in sorted(added_team_ids):
                conn.execute(
                    "INSERT INTO rvbbit.artifact_view_grants (artifact_id,team_id,granted_by) "
                    "VALUES (%s,%s::uuid,%s)",
                    (artifact["id"], team_id, subject),
                )
            for email in sorted(added_people):
                conn.execute(
                    "INSERT INTO rvbbit.artifact_view_grants "
                    "(artifact_id,principal_email,granted_by) VALUES (%s,%s,%s)",
                    (artifact["id"], email, subject),
                )
            updated = dict(conn.execute(
                "UPDATE rvbbit.dashboards SET access_revision=access_revision+1 "
                "WHERE id=%s RETURNING id,slug,name,description,owner_email,app_kind,runtime_kind,"
                "latest_version,archived,archived_at,archived_by,access_revision,updated_at",
                (artifact["id"],),
            ).fetchone())
            after = {
                "revision": updated["access_revision"],
                "team_ids": sorted(desired_team_set),
                "people": sorted(desired_email_set),
                "archived": bool(updated.get("archived")),
            }
            _record_event(
                conn, updated, "sharing_updated", authorization,
                before=before, after=after,
                detail={
                    "teams_added": sorted(added_team_ids),
                    "teams_removed": sorted(removed_team_ids),
                    "people_added": sorted(added_people),
                    "people_removed": sorted(removed_people),
                },
                acting_as=artifact["manager_role"],
            )
            updated["manager_role"] = artifact["manager_role"]
        result = _snapshot(conn, updated, include_events=True, subject=subject)
        result.update({"subject": subject, "changed": True})
        return result


def set_archived(
    conn_factory: Callable[..., Any],
    authorization: Any,
    slug: Any,
    expected_revision: Any,
    archived: Any,
) -> dict[str, Any]:
    subject = _subject(authorization)
    if not isinstance(archived, bool) or isinstance(expected_revision, bool):
        raise ArtifactAccessError("INVALID_LIFECYCLE_CHANGE", "Archive state and revision are required.")
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ArtifactAccessError("INVALID_LIFECYCLE_CHANGE", "Archive state and revision are required.") from None
    with conn_factory() as conn:
        with conn.transaction():
            artifact = _owner_artifact(conn, slug, subject, lock=True)
            if int(artifact.get("access_revision") or 1) != revision:
                raise ArtifactAccessError(
                    "ARTIFACT_ACCESS_CONFLICT",
                    "This artifact changed while it was open. Refresh before continuing.",
                    409,
                )
            if bool(artifact.get("archived")) == archived:
                result = _snapshot(conn, artifact, include_events=True, subject=subject)
                result.update({"subject": subject, "changed": False})
                return result
            before_snapshot = _snapshot(conn, artifact)
            updated = dict(conn.execute(
                "UPDATE rvbbit.dashboards SET archived=%s,"
                "archived_at=CASE WHEN %s THEN now() ELSE NULL END,"
                "archived_by=CASE WHEN %s THEN %s ELSE NULL END,"
                "access_revision=access_revision+1 WHERE id=%s "
                "RETURNING id,slug,name,description,owner_email,app_kind,runtime_kind,"
                "latest_version,archived,archived_at,archived_by,access_revision,updated_at",
                (archived, archived, archived, subject, artifact["id"]),
            ).fetchone())
            after_snapshot = _snapshot(conn, updated)
            _record_event(
                conn, updated, "artifact_archived" if archived else "artifact_restored",
                authorization,
                before={
                    "revision": artifact["access_revision"],
                    "archived": bool(artifact.get("archived")),
                    "sharing": before_snapshot["summary"],
                },
                after={
                    "revision": updated["access_revision"],
                    "archived": archived,
                    "sharing": after_snapshot["summary"],
                },
                acting_as=artifact["manager_role"],
            )
            updated["manager_role"] = artifact["manager_role"]
        result = _snapshot(conn, updated, include_events=True, subject=subject)
        result.update({"subject": subject, "changed": True})
        return result


_ARTIFACT_COLUMNS = (
    "id,slug,name,description,owner_email,app_kind,runtime_kind,latest_version,"
    "archived,archived_at,archived_by,access_revision,updated_at"
)


def _reason(value: Any, *, required: bool) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()[:500]
    if required and not text:
        raise ArtifactAccessError(
            "REASON_REQUIRED",
            "Say why the owner is changing (for example: Jane left; her work moves to Sam).",
        )
    return text


def _new_owner(conn: Any, value: Any) -> str:
    try:
        email = application_teams.normalize_email(value)
    except application_teams.TeamError as exc:
        raise ArtifactAccessError("INVALID_OWNER", str(exc)) from exc
    if not conn.execute(
        "SELECT 1 FROM rvbbit.application_principals WHERE email=%s", (email,)
    ).fetchone():
        raise ArtifactAccessError(
            "PERSON_NOT_OBSERVED",
            f"{email} has not signed in through a trusted application surface yet, "
            "so they cannot own an artifact.",
        )
    return email


def _hand_over(
    conn: Any,
    artifact: dict[str, Any],
    new_owner: str,
    reason: str,
    authorization: Any,
    acting_as: str,
) -> dict[str, Any]:
    """Move one locked artifact to ``new_owner``; the previous owner keeps view access."""
    previous = str(artifact.get("owner_email") or "").strip().lower()
    updated = dict(conn.execute(
        "UPDATE rvbbit.dashboards SET owner_email=%s,access_revision=access_revision+1 "
        f"WHERE id=%s RETURNING {_ARTIFACT_COLUMNS}",
        (new_owner, artifact["id"]),
    ).fetchone())
    # The new owner sees it as its owner; a personal grant would only be noise.
    conn.execute(
        "DELETE FROM rvbbit.artifact_view_grants WHERE artifact_id=%s AND principal_email=%s",
        (artifact["id"], new_owner),
    )
    # Only someone who has signed in can hold a grant (system owners cannot).
    conn.execute(
        "INSERT INTO rvbbit.artifact_view_grants (artifact_id,principal_email,granted_by) "
        "SELECT %s,p.email,%s FROM rvbbit.application_principals p WHERE p.email=%s "
        "ON CONFLICT DO NOTHING",
        (artifact["id"], _subject(authorization), previous),
    )
    kept = conn.execute(
        "SELECT 1 FROM rvbbit.artifact_view_grants WHERE artifact_id=%s AND principal_email=%s",
        (artifact["id"], previous),
    ).fetchone() is not None
    _record_event(
        conn, updated, "owner_transferred", authorization,
        before={"owner": previous, "revision": int(artifact.get("access_revision") or 1)},
        after={"owner": new_owner, "revision": int(updated["access_revision"])},
        detail={"reason": reason,
                "previous_owner_view": "kept" if kept else "not a signed-in person; no grant"},
        acting_as=acting_as,
    )
    updated["manager_role"] = acting_as
    return updated


def transfer_owner(
    conn_factory: Callable[..., Any],
    authorization: Any,
    slug: Any,
    new_owner: Any,
    expected_revision: Any,
    reason: Any = "",
) -> dict[str, Any]:
    """Give one artifact a new owner. Its owner may hand it on; an admin may move
    any artifact (a reason is then required). The previous owner keeps view access,
    and the artifact's history, versions, grants and home chat are unchanged."""
    subject = _subject(authorization)
    if isinstance(expected_revision, bool):
        raise ArtifactAccessError("INVALID_REVISION", "Inspect the current sharing revision first.")
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ArtifactAccessError("INVALID_REVISION", "Inspect the current sharing revision first.") from None
    with conn_factory() as conn:
        with conn.transaction():
            artifact = _owner_artifact(conn, slug, subject, lock=True)
            role = artifact["manager_role"]
            why = _reason(reason, required=role == "admin")
            if int(artifact.get("access_revision") or 1) != revision:
                raise ArtifactAccessError(
                    "ARTIFACT_ACCESS_CONFLICT",
                    "This artifact changed while it was open. Refresh before continuing.",
                    409,
                )
            target = _new_owner(conn, new_owner)
            if target == str(artifact.get("owner_email") or "").strip().lower():
                result = _snapshot(conn, artifact, include_events=True, subject=subject)
                result.update({"subject": subject, "changed": False})
                return result
            updated = _hand_over(conn, artifact, target, why, authorization, role)
        result = _snapshot(conn, updated, include_events=True, subject=subject)
        result.update({"subject": subject, "changed": True})
        return result


def transfer_all(
    conn_factory: Callable[..., Any],
    authorization: Any,
    from_owner: Any,
    to_owner: Any,
    reason: Any = "",
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Admins: move every artifact one person owns, archived ones included, to
    another person (someone left). ``dry_run`` (the default) only lists them."""
    subject = _subject(authorization)
    try:
        source = application_teams.normalize_email(from_owner)
    except application_teams.TeamError as exc:
        raise ArtifactAccessError("INVALID_OWNER", str(exc)) from exc
    with conn_factory() as conn:
        with conn.transaction():
            if not is_admin(conn, subject):
                raise ArtifactAccessError(
                    "ARTIFACT_ADMIN_REQUIRED",
                    "Only members of the Admins Team can move another person's artifacts.",
                    403,
                )
            target = _new_owner(conn, to_owner)
            if target == source:
                raise ArtifactAccessError("INVALID_OWNER", "Choose a different new owner.")
            why = _reason(reason, required=not dry_run)
            rows = [dict(row) for row in conn.execute(
                f"SELECT {_ARTIFACT_COLUMNS} FROM rvbbit.dashboards "
                "WHERE lower(owner_email)=%s ORDER BY updated_at DESC FOR UPDATE",
                (source,),
            ).fetchall()]
            listed = [{"slug": row["slug"], "name": row["name"], "app_kind": row["app_kind"],
                       "archived": bool(row["archived"]), "updated_at": row["updated_at"]} for row in rows]
            if not dry_run:
                for row in rows:
                    _hand_over(conn, row, target, why, authorization, "admin")
    return {"subject": subject, "from_owner": source, "to_owner": target, "dry_run": bool(dry_run),
            "count": len(listed), "artifacts": listed, "changed": bool(rows) and not dry_run}
