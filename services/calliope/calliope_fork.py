"""Fork a chat at one of its messages into a new chat the caller owns.

The fork holds the original chat up to and including that message: its turns,
Stage pieces and sketch, plus the agent's history up to that moment (copied by
Hermes, see docker/calliope/hermes-session-fork.py). Artifacts the chat made
are republished for the fork at the version they had then, under new slugs,
owned by the caller and private; every copied text names the new slugs, so
nothing in the fork points at (or overwrites) the originals. Artifacts the chat
only changed are listed, not copied. The fork is private: room members are not
carried over, though each message keeps its author.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

import calliope_access

_UNFINISHED = ("running", "queued")


class ForkError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# -- text rewriting -----------------------------------------------------------

def _slug_pattern(slugs: dict[str, str]):
    if not slugs:
        return None
    alternatives = "|".join(re.escape(old) for old in sorted(slugs, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_-])(?:{alternatives})(?![A-Za-z0-9_-])")


class Rewriter:
    """Old artifact slugs become the fork's slugs (whole tokens only); copied row ids
    become the fork's ids (exact UUID strings)."""

    def __init__(self, slugs: dict[str, str], ids: dict[str, str]):
        self.slugs, self.ids = dict(slugs), dict(ids)
        self._slug = _slug_pattern(self.slugs)
        self._id = re.compile("|".join(re.escape(old) for old in self.ids)) if self.ids else None

    def text(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if self._id is not None:
            value = self._id.sub(lambda m: self.ids[m.group(0)], value)
        if self._slug is not None:
            value = self._slug.sub(lambda m: self.slugs[m.group(0)], value)
        return value

    def json(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key) if isinstance(key, str) else key: self.json(item) for key, item in value.items()}
        return value


def _jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


# -- planning -----------------------------------------------------------------

def fork_slug(conn: Any, slug: str, taken: set[str]) -> str:
    base = re.sub(r"-f\d+$", "", str(slug))[:118]
    n = 1
    while True:
        candidate = f"{base}-f{n}"
        if candidate not in taken and not conn.execute(
            "SELECT 1 FROM rvbbit.dashboards WHERE slug=%s", (candidate,)
        ).fetchone():
            taken.add(candidate)
            return candidate
        n += 1


def plan(conn: Any, session_id: Any, turn_id: Any, subject: str) -> dict[str, Any]:
    """What a fork at ``turn_id`` copies. Raises ForkError or CalliopeAccessError."""
    session = calliope_access.require_view(conn, session_id, subject)
    try:
        turn_key = str(uuid.UUID(str(turn_id)))
    except (TypeError, ValueError):
        raise ForkError("TURN_NOT_FOUND", "Choose a message in this chat to fork from.", 404) from None
    chosen = conn.execute(
        "SELECT id,ordinal FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid",
        (turn_key, str(session["id"])),
    ).fetchone()
    if not chosen:
        raise ForkError("TURN_NOT_FOUND", "That message is not in this chat.", 404)
    turns = [dict(row) for row in conn.execute(
        "SELECT * FROM rvbbit.calliope_turns WHERE session_id=%s::uuid AND ordinal<=%s ORDER BY ordinal",
        (str(session["id"]), int(chosen["ordinal"])),
    ).fetchall()]
    if any(turn.get("status") in _UNFINISHED for turn in turns):
        raise ForkError("TURN_NOT_FINISHED",
                        "A message up to this point is still being answered. Fork once it finishes.", 409)
    cut_at = max(turn.get("completed_at") or turn["created_at"] for turn in turns)
    hermes_turns = [turn["completed_at"] for turn in turns if turn.get("hermes_run_id") and turn.get("completed_at")]
    # Hermes runs one turn of a chat at a time, so its history up to the last
    # agent turn shown above this message ends when that turn completed.
    hermes_until = max(hermes_turns).timestamp() if hermes_turns else 0.0
    artifacts = [dict(row) for row in conn.execute(
        "SELECT d.*, v.fork_version FROM rvbbit.dashboards d "
        "JOIN LATERAL (SELECT max(version) AS fork_version FROM rvbbit.dashboard_versions "
        "              WHERE dashboard_id=d.id AND created_at<=%s) v ON v.fork_version IS NOT NULL "
        "WHERE d.home_session_id=%s::uuid AND NOT d.archived ORDER BY d.created_at",
        (cut_at, str(session["id"])),
    ).fetchall()]
    made = {row["slug"] for row in artifacts}
    edited = [row["artifact_slug"] for row in conn.execute(
        "SELECT DISTINCT artifact_slug FROM rvbbit.calliope_surfaces "
        "WHERE session_id=%s::uuid AND turn_id=ANY(%s::uuid[]) AND artifact_slug IS NOT NULL ORDER BY 1",
        (str(session["id"]), [str(turn["id"]) for turn in turns]),
    ).fetchall() if row["artifact_slug"] not in made]
    taken: set[str] = set()
    slugs = {row["slug"]: fork_slug(conn, row["slug"], taken) for row in artifacts}
    return {"session": dict(session), "turns": turns, "turn_ordinal": int(chosen["ordinal"]),
            "turn_id": turn_key, "cut_at": cut_at, "hermes_until": hermes_until,
            "artifacts": artifacts, "edited_only": edited, "slugs": slugs}


def title_for(session: dict[str, Any]) -> str:
    base = re.sub(r"\s*\(fork\)$", "", str(session.get("title") or "New inquiry")).strip()
    return f"{base[:110]} (fork)"


# -- copying ------------------------------------------------------------------

_TURN_COPY = ("ordinal", "user_message", "assistant_message", "attachments", "selected_surface_id", "status",
              "error", "created_at", "completed_at", "design_profile_version_id", "turn_kind", "evidence_refs",
              "object_refs", "response_receipt", "author_email", "author_display_name", "stopped_by",
              "addressed_to", "routing", "origin_context", "authoring_mode")
_SURFACE_COPY = ("ordinal", "kind", "title", "tool_name", "tool_call_id", "lineage_key", "parent_surface_id",
                 "artifact_slug", "artifact_version", "payload", "source", "presentation", "created_at",
                 "design_profile_version_id")
_JSON_COLUMNS = {"attachments", "evidence_refs", "object_refs", "response_receipt", "routing", "origin_context",
                 "payload", "source", "presentation", "manifest", "source_files", "elements", "app_state",
                 "files", "last_change_summary", "metadata", "last_health"}


def _insert(conn: Any, table: str, row: dict[str, Any], returning: str = "") -> Any:
    columns = list(row)
    values = ", ".join(f"%s::jsonb" if column in _JSON_COLUMNS else "%s" for column in columns)
    sql = f"INSERT INTO rvbbit.{table} ({', '.join(columns)}) VALUES ({values})"
    params = [_jsonb(row[c]) if c in _JSON_COLUMNS else row[c] for c in columns]
    if returning:
        return conn.execute(sql + f" RETURNING {returning}", params).fetchone()
    conn.execute(sql, params)
    return None


def apply(conn: Any, fork: dict[str, Any], *, owner: str, session_id: str, hermes_session_id: str) -> dict[str, Any]:
    """Write the fork inside the caller's transaction; returns the new session row."""
    source = fork["session"]
    surfaces = [dict(row) for row in conn.execute(
        "SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid AND turn_id=ANY(%s::uuid[]) "
        "ORDER BY created_at, ordinal",
        (str(source["id"]), [str(turn["id"]) for turn in fork["turns"]]),
    ).fetchall()]
    sketches = [dict(row) for row in conn.execute(
        "SELECT * FROM rvbbit.calliope_sketches WHERE session_id=%s::uuid ORDER BY created_at",
        (str(source["id"]),),
    ).fetchall()]
    ids = {str(source["id"]): session_id}
    for row in fork["turns"] + surfaces + sketches:
        ids[str(row["id"])] = str(uuid.uuid4())
    rewrite = Rewriter(fork["slugs"], ids)
    forked_from = {
        "session_id": str(source["id"]), "session_title": source.get("title"),
        "turn_id": fork["turn_id"], "turn_ordinal": fork["turn_ordinal"],
        "cut_at": fork["cut_at"].isoformat() if isinstance(fork["cut_at"], datetime) else str(fork["cut_at"]),
        "forked_by": owner,
        "artifacts": [{"from": row["slug"], "from_version": int(row["fork_version"]), "slug": fork["slugs"][row["slug"]]}
                      for row in fork["artifacts"]],
        "not_copied": list(fork["edited_only"]),
    }
    session = _insert(conn, "calliope_sessions", {
        "id": session_id, "owner_email": owner, "hermes_session_id": hermes_session_id,
        "title": title_for(source), "title_source": "manual", "purpose": "chat",
        "specialist_key": source.get("specialist_key"),
        "design_profile_version_id": source.get("design_profile_version_id"),
        "team_mode": bool(source.get("team_mode")), "authoring_mode": source.get("authoring_mode") or "html",
        "workspace_kind": source.get("workspace_kind"),
    }, returning="*")
    conn.execute("UPDATE rvbbit.calliope_sessions SET forked_from=%s::jsonb WHERE id=%s::uuid",
                 (json.dumps(forked_from), session_id))

    for row in fork["artifacts"]:
        new_slug = fork["slugs"][row["slug"]]
        versions = [dict(v) for v in conn.execute(
            "SELECT * FROM rvbbit.dashboard_versions WHERE dashboard_id=%s AND version<=%s ORDER BY version",
            (row["id"], row["fork_version"]),
        ).fetchall()]
        current = versions[-1]
        created = _insert(conn, "dashboards", {
            "slug": new_slug, "name": f"{re.sub(r' [(]fork[)]$', '', str(row['name']))} (fork)"[:200],
            "description": rewrite.text(row.get("description")), "owner_email": owner, "team": row.get("team"),
            "status": row.get("status"), "latest_version": int(row["fork_version"]),
            "runtime_kind": row.get("runtime_kind"), "app_kind": row.get("app_kind"),
            "manifest": rewrite.json(current.get("manifest") or row.get("manifest") or {}),
            "area_id": row.get("area_id"), "area_source": row.get("area_source"),
            "area_confidence": row.get("area_confidence"),
            "home_session_id": session_id,
        }, returning="id")
        conn.execute("UPDATE rvbbit.dashboards SET home_set_at=now() WHERE id=%s", (created["id"],))
        for version in versions:
            _insert(conn, "dashboard_versions", {
                "dashboard_id": created["id"], "version": version["version"],
                "html": rewrite.text(version.get("html")), "kind": version.get("kind"),
                "created_by": version.get("created_by"), "created_at": version.get("created_at"),
                "notes": rewrite.text(version.get("notes")),
                "manifest": rewrite.json(version.get("manifest")),
                "source_files": rewrite.json(version.get("source_files")),
            })
        conn.execute(
            "INSERT INTO rvbbit.dashboard_deps (dashboard_id,version,kind,object_ref,base_sql,source,confidence,metadata) "
            "SELECT %s,version,kind,object_ref,base_sql,source,confidence,metadata FROM rvbbit.dashboard_deps "
            "WHERE dashboard_id=%s AND version<=%s",
            (created["id"], row["id"], row["fork_version"]),
        )

    for turn in fork["turns"]:
        copy = {column: turn.get(column) for column in _TURN_COPY}
        for column in ("user_message", "assistant_message", "error"):
            copy[column] = rewrite.text(copy[column])
        for column in _JSON_COLUMNS & set(copy):
            copy[column] = rewrite.json(copy[column])
        copy["selected_surface_id"] = ids.get(str(copy["selected_surface_id"])) if copy["selected_surface_id"] else None
        _insert(conn, "calliope_turns", {"id": ids[str(turn["id"])], "session_id": session_id, **copy})
    for surface in surfaces:
        copy = {column: surface.get(column) for column in _SURFACE_COPY}
        copy["parent_surface_id"] = ids.get(str(copy["parent_surface_id"])) if copy["parent_surface_id"] else None
        copy["artifact_slug"] = fork["slugs"].get(copy["artifact_slug"], copy["artifact_slug"])
        copy["lineage_key"] = rewrite.text(copy["lineage_key"])
        copy["title"] = rewrite.text(copy["title"])
        for column in ("payload", "source", "presentation"):
            copy[column] = rewrite.json(copy[column])
        _insert(conn, "calliope_surfaces", {"id": ids[str(surface["id"])], "session_id": session_id,
                                            "turn_id": ids[str(surface["turn_id"])], **copy})
    carry_input_requests(conn, str(source["id"]), ids, fork["cut_at"], rewrite, owner)
    for sketch in sketches:
        _insert(conn, "calliope_sketches", {
            "id": ids[str(sketch["id"])], "session_id": session_id, "owner_email": owner,
            "title": sketch.get("title"), "revision": sketch.get("revision") or 0,
            "elements": rewrite.json(sketch.get("elements")), "app_state": rewrite.json(sketch.get("app_state")),
            "files": rewrite.json(sketch.get("files")), "last_actor": sketch.get("last_actor"),
            "last_actor_email": sketch.get("last_actor_email"),
        })
    result = dict(session)
    result["forked_from"] = forked_from
    return result


def carry_input_requests(conn: Any, source_session_id: str, ids: dict[str, str], cut_at: Any,
                         rewrite: Rewriter, owner: str) -> dict[str, int]:
    """Give each copied question the state it had at the fork point.

    Inserting a question card registers a new open request (the capture trigger
    in 0411), so without this a fork re-asks every question the original chat
    ever answered, each one a blocker. A question answered or cancelled by the
    fork point keeps that answer. One still open then stays open, so a fork
    taken at the message that asked can answer it differently. A knock on the
    original room is never a question for the fork."""
    counts = {"settled": 0, "open": 0, "dropped": 0}
    copied = [key for key in ids if key != source_session_id]
    if not copied:
        return counts
    for row in conn.execute(
        "SELECT id,status,answers,revision,answered_by,answered_at,closed_by,closed_at,created_at,updated_at,"
        "request->>'kind' AS request_kind FROM rvbbit.calliope_input_requests "
        "WHERE session_id=%s::uuid AND id=ANY(%s::uuid[]) ORDER BY created_at",
        (source_session_id, copied),
    ).fetchall():
        row = dict(row)
        target = ids[str(row["id"])]
        closed_at = row.get("closed_at")
        if row.get("status") in ("answered", "cancelled") and closed_at is not None and closed_at <= cut_at:
            conn.execute(
                "UPDATE rvbbit.calliope_input_requests SET status=%s,answers=%s::jsonb,revision=%s,"
                "answered_by=%s,answered_at=%s,closed_by=%s,closed_at=%s,created_at=%s,updated_at=%s "
                "WHERE id=%s::uuid",
                (row["status"], json.dumps(rewrite.json(row.get("answers") or {})), int(row.get("revision") or 1),
                 row.get("answered_by"), row.get("answered_at"), row.get("closed_by") or owner.lower(), closed_at,
                 row.get("created_at"), row.get("updated_at") or closed_at, target),
            )
            counts["settled"] += 1
        elif row.get("request_kind") == "join_request":
            conn.execute(
                "UPDATE rvbbit.calliope_input_requests SET status='cancelled',revision=revision+1,"
                "closed_by=%s,closed_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE id=%s::uuid AND status='open'",
                (owner.lower(), target),
            )
            counts["dropped"] += 1
        else:
            counts["open"] += 1
    return counts


# -- inside the fork ------------------------------------------------------------

def forked_copy(conn: Any, session_id: Any, slug: Any) -> str | None:
    """The fork's own copy of ``slug``, when ``session_id`` is a fork that republished it."""
    if not session_id or not slug:
        return None
    row = conn.execute(
        "SELECT a->>'slug' AS copy FROM rvbbit.calliope_sessions s, "
        "jsonb_array_elements(coalesce(s.forked_from->'artifacts','[]'::jsonb)) a "
        "WHERE s.id=%s::uuid AND a->>'from'=%s LIMIT 1",
        (str(session_id), str(slug)),
    ).fetchone()
    return str(row["copy"]) if row and row.get("copy") else None


def context(forked_from: Any) -> str:
    """The note each turn in a fork carries, so Callie works on the fork's copies."""
    if not isinstance(forked_from, dict) or not forked_from.get("session_id"):
        return ""
    lines = [
        "[CALLIOPE FORK — verified]",
        f"This chat is a fork of \"{forked_from.get('session_title') or 'an earlier chat'}\", copied up to "
        f"its message {forked_from.get('turn_ordinal')}. The history above is that copy; the original chat "
        "continues separately.",
    ]
    copies = forked_from.get("artifacts") or []
    if copies:
        lines.append("Artifacts were republished for this chat. Change only these copies:")
        lines += [f"- {item.get('from')} → {item.get('slug')}" for item in copies]
    if forked_from.get("not_copied"):
        lines.append("Changed in the original chat but not made there, so not copied (they belong to other "
                     "chats; ask before changing them): " + ", ".join(forked_from["not_copied"]))
    lines.append("[/CALLIOPE FORK]")
    return "\n".join(lines)
