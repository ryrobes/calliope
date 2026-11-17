"""Rooms: what makes a chat with more than one human feel like one place.

A room is a chat with members (see ``calliope_access``). This module holds the
room-specific state around it:

* knocking — someone who can see a chat exists (a Floor silhouette, an
  artifact's home) asks to join; the owner answers a blocking card in the chat;
* notifications — Inbox items for the person an event is about, not the owner;
* presence and catch-up — who has the room open, and what happened since you
  were last in it;
* the room context Callie gets on each run — who is here, who is speaking, and
  what people said to each other since her last turn.

Every function takes an open connection or a connection factory from the
service; nothing here starts threads.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import calliope_access


KNOCK_REASON_MAX = 280
PRESENCE_WINDOW_SECONDS = 45

DDL = """
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
"""


class RoomError(Exception):
    """A room action the caller may not take, with an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def ensure(conn: Any) -> None:
    conn.execute(DDL)


def _email(value: Any) -> str:
    return str(value or "").strip().lower()[:320]


def _uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def auth_mode() -> str:
    import os
    return os.environ.get("WAREHOUSE_AUTH", "shared").strip().lower()


def describe_warnings(warnings: list[dict[str, Any]], name: str) -> str:
    blocked = [w for w in warnings if w["reason"] == "no_access"]
    rls = [w for w in warnings if w["reason"] == "row_level_security"]
    parts = []
    if blocked:
        parts.append(f"{name} can't read " + ", ".join(
            f"{w['table']} (used in {w['cards']} card{'s' if w['cards'] != 1 else ''})" for w in blocked[:4]))
    if rls:
        parts.append(f"{name} may see different rows of " + ", ".join(w["table"] for w in rls[:4]))
    return ("Heads up: " + "; ".join(parts) + ". Everything already in this chat would become visible to them.") if parts else ""


def _one_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _display(conn: Any, email: str) -> str:
    row = conn.execute(
        "SELECT display_name FROM rvbbit.application_principals WHERE email=%s", (email,)
    ).fetchone()
    return str((row or {}).get("display_name") or email)


# ── members ──────────────────────────────────────────────────────────────────

def is_room(conn: Any, session_id: Any) -> bool:
    """A chat with more than one human: anyone besides the owner has access."""
    sid = _uuid(session_id)
    if not sid:
        return False
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM rvbbit.calliope_session_view_grants g "
        "WHERE g.session_id=%s::uuid) AS room",
        (sid,),
    ).fetchone() or {}
    return bool(row.get("room"))


def members(conn: Any, session_id: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    """Owner first, then people with access, with who has the room open now.

    Members of a granted Team are members; the Everyone Team is reported as
    one entry rather than the whole company.
    """
    sid = _uuid(session_id)
    if not sid:
        return []
    rows = conn.execute(
        "WITH s AS (SELECT id,lower(owner_email) AS owner FROM rvbbit.calliope_sessions WHERE id=%s::uuid), "
        "people AS ("
        " SELECT s.owner AS email,'owner' AS role FROM s "
        " UNION SELECT g.principal_email,g.role FROM rvbbit.calliope_session_view_grants g, s "
        "  WHERE g.session_id=s.id AND g.principal_email IS NOT NULL "
        " UNION SELECT m.principal_email,'member' FROM rvbbit.calliope_session_view_grants g "
        "  JOIN rvbbit.teams t ON t.id=g.team_id AND NOT t.archived AND t.system_key IS DISTINCT FROM 'everyone' "
        "  JOIN rvbbit.team_members m ON m.team_id=t.id, s WHERE g.session_id=s.id) "
        "SELECT DISTINCT ON (p.email) p.email,p.role,pr.display_name,pr.avatar_key::text AS avatar_key,"
        "(pres.last_seen_at > now() - make_interval(secs => %s)) AS present "
        "FROM people p "
        "LEFT JOIN rvbbit.application_principals pr ON pr.email=p.email "
        "LEFT JOIN rvbbit.calliope_room_presence pres ON pres.session_id=%s::uuid AND pres.email=p.email "
        "ORDER BY p.email, CASE p.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END",
        (sid, PRESENCE_WINDOW_SECONDS, sid),
    ).fetchall()
    result = [
        {
            "email": str(row["email"]),
            "role": str(row["role"]),
            "display_name": row.get("display_name") or str(row["email"]),
            "avatar_url": calliope_access.avatar_url(row.get("avatar_key")),
            "present": bool(row.get("present")),
        }
        for row in rows
    ]
    result.sort(key=lambda m: (m["role"] != "owner", not m["present"], m["display_name"].lower()))
    everyone = conn.execute(
        "SELECT 1 FROM rvbbit.calliope_session_view_grants g JOIN rvbbit.teams t ON t.id=g.team_id "
        "WHERE g.session_id=%s::uuid AND t.system_key='everyone' AND NOT t.archived",
        (sid,),
    ).fetchone()
    if everyone:
        result.append({"email": "", "role": "everyone", "display_name": "Everyone signed in",
                       "avatar_url": None, "present": False})
    return result[:limit]


# ── presence and catch-up ───────────────────────────────────────────────────

def touch_presence(conn: Any, session_id: Any, email: Any) -> None:
    sid, who = _uuid(session_id), _email(email)
    if not sid or not who:
        return
    conn.execute(
        "INSERT INTO rvbbit.calliope_room_presence (session_id,email,last_seen_at) "
        "VALUES (%s::uuid,%s,now()) ON CONFLICT (session_id,email) "
        "DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at",
        (sid, who),
    )


def present_emails(conn: Any, session_id: Any) -> list[str]:
    sid = _uuid(session_id)
    if not sid:
        return []
    return [str(row["email"]) for row in conn.execute(
        "SELECT email FROM rvbbit.calliope_room_presence WHERE session_id=%s::uuid "
        "AND last_seen_at > now() - make_interval(secs => %s) ORDER BY email",
        (sid, PRESENCE_WINDOW_SECONDS),
    ).fetchall()]


def catch_up(conn: Any, session_id: Any, email: Any, *, mark: bool = True) -> dict[str, Any] | None:
    """What happened in a room since this member last opened it.

    Deterministic: who spoke, what Callie changed on the Stage, and what is
    still waiting. Returns None the first time someone opens a room (there
    is nothing to catch up on) or when nothing happened since.
    """
    sid, who = _uuid(session_id), _email(email)
    if not sid or not who:
        return None
    previous = conn.execute(
        "SELECT last_read_ordinal,last_read_at FROM rvbbit.calliope_room_reads "
        "WHERE session_id=%s::uuid AND email=%s",
        (sid, who),
    ).fetchone()
    latest = conn.execute(
        "SELECT coalesce(max(ordinal),0) AS n FROM rvbbit.calliope_turns WHERE session_id=%s::uuid",
        (sid,),
    ).fetchone()["n"]
    summary = None
    if previous and int(previous["last_read_ordinal"]) < int(latest):
        since = int(previous["last_read_ordinal"])
        turns = conn.execute(
            "SELECT t.ordinal,t.author_email,coalesce(p.display_name,t.author_email) AS author,"
            "t.turn_kind,t.status,t.user_message FROM rvbbit.calliope_turns t "
            "LEFT JOIN rvbbit.application_principals p ON p.email=lower(t.author_email) "
            "WHERE t.session_id=%s::uuid AND t.ordinal>%s AND lower(t.author_email)<>%s "
            "AND t.turn_kind IN ('chat','note','agent_message','knock') ORDER BY t.ordinal",
            (sid, since, who),
        ).fetchall()
        changed = conn.execute(
            "SELECT DISTINCT ON (f.lineage_key) f.title,f.kind,f.artifact_version "
            "FROM rvbbit.calliope_surfaces f JOIN rvbbit.calliope_turns t ON t.id=f.turn_id "
            "WHERE f.session_id=%s::uuid AND t.ordinal>%s "
            "AND f.kind IN ('artifact','board','chart','answer','map','query') "
            "ORDER BY f.lineage_key,f.created_at DESC LIMIT 6",
            (sid, since),
        ).fetchall()
        waiting = conn.execute(
            "SELECT count(*)::int AS n FROM rvbbit.calliope_turns WHERE session_id=%s::uuid "
            "AND status='queued'",
            (sid,),
        ).fetchone()["n"]
        if turns or changed:
            authors: list[str] = []
            for row in turns:
                name = str(row["author"] or row["author_email"])
                if name not in authors:
                    authors.append(name)
            summary = {
                "since": previous["last_read_at"].isoformat() if previous.get("last_read_at") else None,
                "messages": len(turns),
                "people": authors[:6],
                "notes": sum(1 for row in turns if row["turn_kind"] == "note"),
                "knocks": sum(1 for row in turns if row["turn_kind"] == "knock"),
                "changed": [
                    {"title": row["title"], "kind": row["kind"],
                     "version": row.get("artifact_version")}
                    for row in changed
                ],
                "queued": int(waiting),
                "recent": [
                    {"author": str(row["author"]), "kind": row["turn_kind"],
                     "text": re.sub(r"\s+", " ", str(row["user_message"] or ""))[:160]}
                    for row in turns[-4:]
                ],
            }
    if mark:
        conn.execute(
            "INSERT INTO rvbbit.calliope_room_reads (session_id,email,last_read_ordinal,last_read_at) "
            "VALUES (%s::uuid,%s,%s,now()) ON CONFLICT (session_id,email) DO UPDATE SET "
            "last_read_ordinal=greatest(rvbbit.calliope_room_reads.last_read_ordinal,EXCLUDED.last_read_ordinal),"
            "last_read_at=EXCLUDED.last_read_at",
            (sid, who, int(latest)),
        )
    return summary


# ── notifications ───────────────────────────────────────────────────────────

def notify(
    conn: Any,
    recipient: Any,
    session_id: Any,
    *,
    title: str,
    summary: str = "",
    kind: str = "result",
    urgency: str = "normal",
    dedupe_key: str | None = None,
    context: dict[str, Any] | None = None,
    expires_in_days: int = 7,
) -> None:
    """An Inbox item for the person an event is about.

    Room events go to whoever they concern (the person added, mentioned, or
    knocked for), never by default to the chat's owner. Re-sending the same
    dedupe key refreshes the item instead of stacking duplicates.
    """
    who, sid = _email(recipient), _uuid(session_id)
    if not who or "@" not in who:
        return
    expires_at = datetime.now(timezone.utc) + timedelta(days=max(1, int(expires_in_days)))
    conn.execute(
        "INSERT INTO rvbbit.calliope_work_items "
        "(id,owner_email,session_id,kind,source,source_ref,dedupe_key,title,summary,urgency,state,"
        "context,action_prompt,expires_at) "
        "VALUES (%s::uuid,%s,%s::uuid,%s,'calliope_room',%s,%s,%s,%s,%s,'unread',%s::jsonb,'',%s) "
        "ON CONFLICT (owner_email,source,dedupe_key) DO UPDATE SET "
        "title=EXCLUDED.title,summary=EXCLUDED.summary,urgency=EXCLUDED.urgency,state='unread',"
        "context=EXCLUDED.context,session_id=EXCLUDED.session_id,updated_at=now(),"
        "seen_at=NULL,resolved_at=NULL,expires_at=EXCLUDED.expires_at",
        (
            str(uuid.uuid4()), who, sid, kind, sid, dedupe_key or f"room:{uuid.uuid4()}",
            str(title)[:240], str(summary)[:4000], urgency,
            json.dumps({"session_id": sid, **(context or {})}, default=str), expires_at,
        ),
    )


def resolve_notifications(conn: Any, dedupe_prefix: str) -> None:
    """Close room items once what they asked for has happened (for everyone)."""
    conn.execute(
        "UPDATE rvbbit.calliope_work_items SET state='done',resolved_at=now(),updated_at=now(),"
        "seen_at=coalesce(seen_at,now()) WHERE source='calliope_room' AND dedupe_key LIKE %s "
        "AND state IN ('unread','seen')",
        (dedupe_prefix.replace("%", r"\%") + "%",),
    )


def settle_knocks(conn: Any, session_id: Any, emails: list[str], by: str) -> None:
    """People brought in by sharing: a knock they left is answered, its card
    closes and its notices clear, as if the owner had let them in."""
    sid, who = _uuid(session_id), [_email(e) for e in emails if _email(e)]
    if not sid or not who:
        return
    decider = _email(by)
    for row in conn.execute(
        "UPDATE rvbbit.calliope_join_requests SET status='approved',decided_by=%s,decided_at=now(),"
        "updated_at=now() WHERE session_id=%s::uuid AND requester_email=ANY(%s) AND status='pending' "
        "RETURNING id::text,input_request_id::text,requester_email",
        (decider, sid, who),
    ).fetchall():
        if row.get("input_request_id"):
            request = conn.execute(
                "UPDATE rvbbit.calliope_input_requests SET status='answered',answers=%s::jsonb,"
                "revision=revision+1,answered_by=%s,answered_at=clock_timestamp(),closed_by=%s,"
                "closed_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE id=%s::uuid AND status='open' RETURNING id,session_id,owner_email,status",
                (json.dumps({"decision": "let_in"}), decider, decider, row["input_request_id"]),
            ).fetchone()
            if request:
                import calliope_inputs
                calliope_inputs.resolve_work_items(conn, dict(request))
        resolve_notifications(conn, f"room-knock:{row['id']}")
        for earlier in conn.execute(
            "SELECT id::text FROM rvbbit.calliope_join_requests WHERE session_id=%s::uuid "
            "AND requester_email=%s AND id<>%s::uuid",
            (sid, row["requester_email"], row["id"]),
        ).fetchall():
            resolve_notifications(conn, f"room-knock-answer:{earlier['id']}")


def notify_added(conn: Any, session: dict[str, Any], emails: list[str], by: str) -> None:
    """Tell people they are now in a room."""
    title = str(session.get("title") or "a chat")
    inviter = _display(conn, _email(by))
    for email in emails:
        notify(
            conn, email, session["id"],
            title=f"{inviter} brought you into “{title}”"[:240],
            summary="You can read everything in this room, talk to everyone in it, and ask Callie to work on its artifacts.",
            dedupe_key=f"room-added:{session['id']}:{_email(email)}",
            context={"event": "room_added", "by": _email(by)},
        )


# ── knocking ────────────────────────────────────────────────────────────────

def pending_knock(conn: Any, session_id: Any, requester: Any) -> dict[str, Any] | None:
    sid, who = _uuid(session_id), _email(requester)
    if not sid or not who:
        return None
    row = conn.execute(
        "SELECT id::text,status,reason,created_at FROM rvbbit.calliope_join_requests "
        "WHERE session_id=%s::uuid AND requester_email=%s ORDER BY created_at DESC LIMIT 1",
        (sid, who),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return result


def knock(
    conn_factory: Callable[..., Any],
    requester: Any,
    session_id: Any,
    *,
    reason: Any = "",
    via: str = "floor",
    artifact_slug: Any = None,
) -> dict[str, Any]:
    """Ask to join a chat you can see exists but aren't in.

    The owner gets a blocking card in the chat (approve / not now, with an
    optional reply) and a "For you" item. Knocking again while a knock is
    pending updates its reason. Nothing about the chat is revealed to the
    requester beyond what a Floor silhouette already shows.
    """
    who = _email(requester)
    sid = _uuid(session_id)
    clean_reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:KNOCK_REASON_MAX]
    clean_via = via if via in {"floor", "gallery", "lens", "stage"} else "floor"
    if not sid or "@" not in who:
        raise RoomError("INVALID_KNOCK", "Choose a chat to knock on.")
    with conn_factory() as conn:
        with conn.transaction():
            session = conn.execute(
                "SELECT s.id::text,s.title,s.owner_email,s.archived,s.workspace_kind,"
                "coalesce(s.channel,'web') AS channel FROM rvbbit.calliope_sessions s "
                "WHERE s.id=%s::uuid FOR UPDATE",
                (sid,),
            ).fetchone()
            if (not session or session.get("archived") or session.get("workspace_kind") == "system"
                    or session.get("channel") != "web"):
                raise RoomError("KNOCK_UNAVAILABLE", "That chat can't take visitors.", 404)
            if conn.execute(
                "SELECT rvbbit.calliope_session_can_view(%s::uuid,%s,false) AS ok", (sid, who)
            ).fetchone()["ok"]:
                raise RoomError("ALREADY_MEMBER", "You're already in this chat.", 409)
            if not conn.execute(
                "SELECT 1 FROM rvbbit.application_principals WHERE email=%s", (who,)
            ).fetchone():
                raise RoomError("KNOCK_UNAVAILABLE", "Sign in once before knocking.", 403)
            if calliope_access._session_kind(conn, sid) != "chat":
                raise RoomError("KNOCK_UNAVAILABLE", "That chat can't take visitors.", 404)
            existing = conn.execute(
                "SELECT * FROM rvbbit.calliope_join_requests WHERE session_id=%s::uuid "
                "AND requester_email=%s AND status='pending' FOR UPDATE",
                (sid, who),
            ).fetchone()
            name = _display(conn, who)
            owner = _email(session["owner_email"])
            if existing:
                conn.execute(
                    "UPDATE rvbbit.calliope_join_requests SET reason=%s,updated_at=now() WHERE id=%s",
                    (clean_reason, existing["id"]),
                )
                if existing.get("turn_id"):
                    conn.execute(
                        "UPDATE rvbbit.calliope_turns SET user_message=%s WHERE id=%s",
                        (_knock_line(name, clean_reason), existing["turn_id"]),
                    )
                request_id = str(existing["id"])
            else:
                request_id = str(uuid.uuid4())
                turn_id = str(uuid.uuid4())
                next_ordinal = conn.execute(
                    "SELECT coalesce(max(ordinal),0)+1 AS n FROM rvbbit.calliope_turns "
                    "WHERE session_id=%s::uuid",
                    (sid,),
                ).fetchone()["n"]
                conn.execute(
                    "INSERT INTO rvbbit.calliope_turns (id,session_id,ordinal,user_message,assistant_message,"
                    "status,completed_at,turn_kind,author_email,author_display_name,origin_context) "
                    "VALUES (%s::uuid,%s::uuid,%s,%s,NULL,'complete',now(),'knock',%s,%s,%s::jsonb)",
                    (turn_id, sid, next_ordinal, _knock_line(name, clean_reason), who, name,
                     json.dumps({"join_request_id": request_id, "via": clean_via})),
                )
                surface_id = str(uuid.uuid4())
                heads_up = describe_warnings(
                    audience_warnings(conn, sid, [who], auth_mode=auth_mode()), name
                )
                payload = {
                    "kind": "join_request",
                    "join_request_id": request_id,
                    "title": f"{name} is knocking",
                    "prompt": ((f"“{clean_reason}”" if clean_reason
                                else f"{name} would like to join this chat.")
                               + (f"\n\n{heads_up}" if heads_up else "")),
                    "requester": {"email": who, "display_name": name},
                    "blocking": True,
                    "submit_label": "Answer",
                    "fields": [
                        {"key": "decision", "label": "Let them in?", "type": "choice", "required": True,
                         "options": [{"value": "let_in", "label": "Let in"},
                                     {"value": "not_now", "label": "Not now"}]},
                        {"key": "reply", "label": "Reply (optional)", "type": "text", "required": False},
                    ],
                }
                conn.execute(
                    "INSERT INTO rvbbit.calliope_surfaces (id,session_id,turn_id,ordinal,kind,title,tool_name,"
                    "tool_call_id,lineage_key,payload,source) VALUES (%s::uuid,%s::uuid,%s::uuid,1,'input',%s,"
                    "'calliope_room_knock',%s,%s,%s::jsonb,%s::jsonb)",
                    (surface_id, sid, turn_id, f"{name} is knocking"[:240], f"knock:{request_id}",
                     f"knock:{request_id}", json.dumps(payload),
                     json.dumps({"origin": "calliope_room_knock", "join_request_id": request_id})),
                )
                conn.execute(
                    "INSERT INTO rvbbit.calliope_join_requests (id,session_id,requester_email,reason,via,"
                    "artifact_slug,turn_id,input_request_id) VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,%s::uuid,%s::uuid)",
                    (request_id, sid, who, clean_reason, clean_via,
                     str(artifact_slug or "")[:240] or None, turn_id, surface_id),
                )
                conn.execute(
                    "UPDATE rvbbit.calliope_sessions SET updated_at=now() WHERE id=%s::uuid", (sid,)
                )
            notify(
                conn, owner, sid,
                title=f"{name} is knocking on “{session.get('title') or 'your chat'}”"[:240],
                summary=clean_reason or f"{name} would like to join.",
                kind="blocked", urgency="high",
                dedupe_key=f"room-knock:{request_id}",
                context={"event": "knock", "join_request_id": request_id, "requester": who},
            )
            # Managers can let people in too.
            for manager in conn.execute(
                "SELECT principal_email FROM rvbbit.calliope_session_view_grants "
                "WHERE session_id=%s::uuid AND role='manager' AND principal_email IS NOT NULL",
                (sid,),
            ).fetchall():
                notify(
                    conn, manager["principal_email"], sid,
                    title=f"{name} is knocking on “{session.get('title') or 'a room you manage'}”"[:240],
                    summary=clean_reason or f"{name} would like to join.",
                    kind="blocked", urgency="high",
                    dedupe_key=f"room-knock:{request_id}:{manager['principal_email']}",
                    context={"event": "knock", "join_request_id": request_id, "requester": who},
                )
    return {"id": request_id, "status": "pending"}


def _knock_line(name: str, reason: str) -> str:
    return f"✋ {name} knocked" + (f": “{reason}”" if reason else "")


def apply_knock_decision(
    conn: Any, request: dict[str, Any], decider: str, answers: dict[str, Any], action: str
) -> None:
    """Carry out the owner's answer to a knock card, inside its transaction.

    "Let in" makes the requester a member (an ordinary person grant with an
    audit event); "Not now" or closing the card declines. Either way the
    requester is told, with the owner's reply if there is one.
    """
    payload = request.get("request") or {}
    join_id = _uuid(payload.get("join_request_id"))
    if not join_id:
        return
    knock_row = conn.execute(
        "SELECT * FROM rvbbit.calliope_join_requests WHERE id=%s::uuid FOR UPDATE", (join_id,)
    ).fetchone()
    if not knock_row or knock_row["status"] != "pending":
        return
    approved = action == "answer" and answers.get("decision") == "let_in"
    reply = re.sub(r"\s+", " ", str(answers.get("reply") or "")).strip()[:600]
    who = _email(knock_row["requester_email"])
    session = conn.execute(
        "SELECT id::text,title,owner_email,access_revision FROM rvbbit.calliope_sessions "
        "WHERE id=%s::uuid FOR UPDATE",
        (str(knock_row["session_id"]),),
    ).fetchone()
    if approved:
        added = conn.execute(
            "INSERT INTO rvbbit.calliope_session_view_grants (session_id,principal_email,granted_by,can_participate) "
            "VALUES (%s::uuid,%s,%s,true) ON CONFLICT DO NOTHING RETURNING id",
            (session["id"], who, _email(decider)),
        ).fetchone()
        if added:
            conn.execute(
                "UPDATE rvbbit.calliope_sessions SET access_revision=access_revision+1,updated_at=now() "
                "WHERE id=%s::uuid",
                (session["id"],),
            )
            conn.execute(
                "INSERT INTO rvbbit.calliope_session_access_events (event_id,session_id,event_type,"
                "credential_actor,human_subject,auth_mode,delegated,before_state,after_state,detail) "
                "VALUES (%s::uuid,%s::uuid,'knock_approved',%s,%s,'application',false,'{}'::jsonb,"
                "%s::jsonb,%s::jsonb)",
                (str(uuid.uuid4()), session["id"], _email(decider), _email(decider),
                 json.dumps({"people_added": [who]}),
                 json.dumps({"join_request_id": join_id, "reason": knock_row.get("reason") or ""})),
            )
    conn.execute(
        "UPDATE rvbbit.calliope_join_requests SET status=%s,decided_by=%s,decided_at=now(),reply=%s,"
        "updated_at=now() WHERE id=%s::uuid",
        ("approved" if approved else "declined", _email(decider), reply, join_id),
    )
    owner_name = _display(conn, _email(decider))
    title = str(session.get("title") or "the chat")
    if approved:
        # An earlier "not now" for this chat is answered now.
        for earlier in conn.execute(
            "SELECT id::text FROM rvbbit.calliope_join_requests WHERE session_id=%s::uuid "
            "AND requester_email=%s AND id<>%s::uuid",
            (session["id"], who, join_id),
        ).fetchall():
            resolve_notifications(conn, f"room-knock-answer:{earlier['id']}")
        notify(
            conn, who, session["id"],
            title=f"You're in: “{title}”"[:240],
            summary=(f"{owner_name}: {reply}" if reply else f"{owner_name} let you in."),
            dedupe_key=f"room-knock-answer:{join_id}",
            context={"event": "knock_approved", "join_request_id": join_id},
        )
    else:
        notify(
            conn, who, None,
            title=f"Not now, from {owner_name}"[:240],
            summary=reply or f"{owner_name} didn't let you into that chat this time.",
            dedupe_key=f"room-knock-answer:{join_id}",
            context={"event": "knock_declined", "join_request_id": join_id},
        )
    resolve_notifications(conn, f"room-knock:{join_id}")


# ── what Callie is told about the room ──────────────────────────────────────

def notes_since_last_run(conn: Any, session_id: Any, before_ordinal: int) -> list[dict[str, Any]]:
    """People's messages to each other since Callie's previous run.

    Notes never start a run; they reach Callie as context at her next one.
    """
    sid = _uuid(session_id)
    if not sid:
        return []
    last_run = conn.execute(
        "SELECT coalesce(max(ordinal),0) AS n FROM rvbbit.calliope_turns WHERE session_id=%s::uuid "
        "AND ordinal<%s AND turn_kind NOT IN ('note','knock') AND status NOT IN ('queued')",
        (sid, int(before_ordinal)),
    ).fetchone()["n"]
    return [dict(row) for row in conn.execute(
        "SELECT t.ordinal,t.author_email,coalesce(t.author_display_name,t.author_email) AS author,"
        "t.user_message FROM rvbbit.calliope_turns t WHERE t.session_id=%s::uuid "
        "AND t.turn_kind='note' AND t.ordinal>%s AND t.ordinal<%s ORDER BY t.ordinal LIMIT 60",
        (sid, int(last_run), int(before_ordinal)),
    ).fetchall()]


def room_instructions(conn: Any, session: dict[str, Any], speaker: str, *, queued_after: int | None = None) -> str:
    """The room block of Callie's per-run instructions (empty for a solo chat)."""
    sid = str(session.get("id") or "")
    if not is_room(conn, sid):
        return ""
    roster = members(conn, sid)
    people = ", ".join(
        f"{m['display_name']} <{m['email']}>" + (f" ({m['role']})" if m["role"] in {"owner", "manager"} else "")
        + (" — here now" if m.get("present") else "")
        for m in roster if m["role"] != "everyone"
    )
    lines = [
        "This chat is a room: several people work in it together, and they all see everything here.",
        f"People in the room: {people or 'unknown'}." + (
            " Everyone signed in at the company can also join." if any(m["role"] == "everyone" for m in roster) else ""
        ),
        "User messages are tagged with their author, e.g. [Alice]. Address the person you are answering "
        "by name when it helps. Messages tagged as between people were not addressed to you: use them as "
        "context, don't answer them unless someone asks you to.",
        "Artifacts made in this room belong to it: anyone here may ask you to change them, and you revise "
        "the same artifact (with expected_version) rather than making a copy.",
    ]
    try:
        with conn.transaction():
            brief = conn.execute(
                "SELECT synopsis FROM rvbbit.calliope_session_synopses WHERE session_id=%s::uuid "
                "AND status='ready' AND nullif(btrim(synopsis),'') IS NOT NULL",
                (sid,),
            ).fetchone()
        if brief:
            lines.append("What this room is about and has settled so far: " + _one_line(brief["synopsis"])[:900])
    except Exception:  # noqa: BLE001 — installs without the brief worker
        pass
    if queued_after is not None:
        waiting = conn.execute(
            "SELECT coalesce(t.author_display_name,t.author_email) AS author,t.user_message "
            "FROM rvbbit.calliope_turns t WHERE t.session_id=%s::uuid AND t.status='queued' "
            "ORDER BY t.ordinal LIMIT 6",
            (sid,),
        ).fetchall()
        if waiting:
            lines.append(
                "Also waiting their turn after this one (don't do these now; they will reach you next): "
                + "; ".join(
                    "{}: “{}”".format(row["author"], _one_line(row["user_message"])[:200])
                    for row in waiting
                )
            )
    return "\n".join(lines)


def tag_message(author_name: str, message: str) -> str:
    """The durable author tag on a room message sent to Callie."""
    return f"[{author_name}] {message}"


def notes_preamble(notes: list[dict[str, Any]]) -> str:
    if not notes:
        return ""
    lines = [f"[{row['author']}, to the room] {str(row['user_message'] or '').strip()[:1500]}" for row in notes]
    return (
        "Messages people sent each other in this room since your last turn (context only, "
        "not addressed to you):\n" + "\n".join(lines) + "\n\n"
    )


# ── what a new member would see ─────────────────────────────────────────────

def _room_tables(conn: Any, session_id: str) -> dict[str, int]:
    """Tables this room's results came from, with how many cards used each.

    Query cards record the planner-resolved tables they read (and keep a
    snapshot of the rows, which is what a new member would see); artifacts
    made here record their table dependencies.
    """
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(f.payload->'warehouse_objects')='array' "
        "THEN f.payload->'warehouse_objects' ELSE '[]'::jsonb END) AS relation "
        "FROM rvbbit.calliope_surfaces f WHERE f.session_id=%s::uuid AND f.kind='query'",
        (session_id,),
    ).fetchall():
        relation = str(row.get("relation") or "").strip()
        if relation:
            counts[relation] = counts.get(relation, 0) + 1
    try:
        with conn.transaction():
            for row in conn.execute(
                "SELECT dd.object_ref AS relation FROM rvbbit.dashboards d JOIN rvbbit.dashboard_deps dd "
                "ON dd.dashboard_id=d.id AND dd.version=d.latest_version AND dd.kind='table' "
                "WHERE d.home_session_id=%s::uuid",
                (session_id,),
            ).fetchall():
                relation = str(row.get("relation") or "").strip()
                if relation:
                    counts[relation] = counts.get(relation, 0) + 1
    except Exception:  # noqa: BLE001 — installs without published-artifact dependencies
        pass
    return counts


def audience_warnings(conn: Any, session_id: Any, emails: list[str], *, auth_mode: str) -> list[dict[str, Any]]:
    """Tables a would-be member can't read but the room already shows results from.

    Only meaningful where people have different data access: Burrow mode
    (``WAREHOUSE_AUTH=pg``), where each person's queries run as their own
    Postgres role. In the default shared mode everyone reads the same data,
    so there is nothing to warn about. v1 warns; it never redacts.
    """
    sid = _uuid(session_id)
    if auth_mode != "pg" or not sid or not emails:
        return []
    tables = _room_tables(conn, sid)
    if not tables:
        return []
    warnings: list[dict[str, Any]] = []
    for email in sorted({_email(e) for e in emails if _email(e)}):
        mapped = conn.execute(
            "SELECT coalesce((SELECT m.role_name FROM rvbbit.identity_map m WHERE m.identity=%s AND m.enabled),"
            "(SELECT r.rolname FROM pg_roles r WHERE r.rolname=%s AND r.rolcanlogin),'rvbbit_guest') AS role",
            (email, email),
        ).fetchone()
        role = str((mapped or {}).get("role") or "rvbbit_guest")
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            role = None
        for relation, cards in sorted(tables.items()):
            row = conn.execute(
                "SELECT c.oid IS NOT NULL AS exists,c.relrowsecurity AS rls,"
                "CASE WHEN c.oid IS NULL OR %s::text IS NULL THEN false ELSE "
                " has_schema_privilege(%s::text,c.relnamespace,'USAGE') "
                " AND has_any_column_privilege(%s::text,c.oid,'SELECT') END AS readable "
                "FROM (SELECT to_regclass(%s) AS oid) r LEFT JOIN pg_class c ON c.oid=r.oid",
                (role, role, role, relation),
            ).fetchone() or {}
            if not row.get("exists"):
                continue
            if not row.get("readable"):
                warnings.append({"email": email, "table": relation, "cards": cards, "reason": "no_access"})
            elif row.get("rls"):
                warnings.append({"email": email, "table": relation, "cards": cards, "reason": "row_level_security"})
    return warnings
