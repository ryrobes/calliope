"""Arrangements: a user-composed grid of a chat's Stage pieces.

An arrangement is a surface of kind `arrangement`: cells placed on a uniform
pixel lattice (UNIT px square), each showing another surface of this chat, a
one-number stat read from a query result, a value one of the team's dashboards
already shows (read live for whoever is looking), or a text block. Blocks are
content only, never overlap, and render exactly as placed (no responsive scaling).

Like boards, every save is a revision: one lineage per arrangement, a content
hash so an identical save is a no-op, a parent pointer, and a superseded check
so two editors cannot silently overwrite each other. Unlike boards, placing a
surface in an arrangement never folds it out of the Stage, any renderable kind
may be placed (dashboards included), and people revise arrangements directly:
a revision attaches to the chat's latest turn, as workspace snapshots do.

A cell follows its lineage: `version: "latest"` shows the newest revision of
the referenced surface's lineage at read time, so a chart Callie revises updates
in place; a surface UUID pins one revision.
"""
from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID, uuid4

import calliope_access
import stage_values

UNIT = 60
MAX_COLUMNS = 64
MAX_ROWS = 400
MAX_CELLS = 200
CELL_KINDS = {"surface", "stat", "text", "value"}
# Kinds a cell may show. Arrangements never nest; sketches are live
# collaborative documents with their own shelf.
MEMBER_KINDS = {
    "artifact", "chart", "query", "board", "metric", "map", "answer", "document", "image",
    "find", "cube", "value_system", "selection", "evidence", "email",
}
# Smallest cell (w, h) per shown kind, in units.
# Blocks can shrink to two cells each way; a stat or a note to one row.
MIN_SIZE = {
    "artifact": (2, 2), "board": (2, 2), "map": (2, 2), "chart": (2, 2), "query": (2, 2),
    "metric": (2, 2), "answer": (2, 2), "document": (2, 2), "image": (2, 2), "find": (2, 2),
    "cube": (2, 2), "value_system": (2, 2), "selection": (2, 2), "evidence": (2, 2), "email": (2, 2),
    "stat": (2, 1), "text": (2, 1), "value": (2, 1),
}
DEFAULT_SIZE = {
    "artifact": (12, 8), "board": (12, 8), "map": (8, 6), "chart": (8, 5), "query": (10, 5),
    "metric": (4, 2), "answer": (6, 4), "document": (6, 5), "image": (6, 5), "find": (8, 4),
    "cube": (10, 6), "value_system": (8, 5), "selection": (6, 3), "evidence": (6, 3), "email": (6, 4),
    "stat": (3, 2), "text": (6, 1), "value": (3, 2),
}
FORMATS = {"auto", "number", "compact", "percent", "currency", "text"}
VIEWS = {"table", "chart", "sql"}


def _text(value, name, limit, required=False):
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    clean = re.sub(r"\s+", " ", value).strip()
    if required and not clean:
        raise ValueError(f"{name} is required")
    if len(clean) > limit:
        raise ValueError(f"{name} is limited to {limit} characters")
    return clean


def _surface_id(value, name="ref"):
    try:
        return str(UUID(str(value).strip().removeprefix("surface:")))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{name} must be a surface UUID") from None


def _int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}–{high}")
    return value


def validate(payload):
    """Shape and check an arrangement payload. Raises ValueError with a message
    the editor can show. Member kinds are checked in resolve(), which has the
    database; minimums that depend on them are checked there too."""
    if not isinstance(payload, dict):
        raise ValueError("arrangement must be an object")
    title = _text(payload.get("title"), "title", 120, required=True)
    unit = payload.get("unit", UNIT)
    if unit != UNIT:
        raise ValueError(f"unit must be {UNIT}")
    # The payload calls it width: other Stage readers treat a payload's
    # "columns" as a query result's column list.
    width = _int(payload.get("width"), "width", 4, MAX_COLUMNS)
    cells = payload.get("cells")
    if not isinstance(cells, list) or len(cells) > MAX_CELLS:
        raise ValueError(f"cells must be a list of at most {MAX_CELLS} cells")
    out = []
    ids = set()
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValueError(f"cell {index} must be an object")
        kind = str(cell.get("kind") or "surface")
        if kind not in CELL_KINDS:
            raise ValueError(f"cell {index}: kind must be one of surface, stat, text, value")
        cell_id = _text(cell.get("id"), f"cell {index} id", 40, required=True)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cell_id):
            raise ValueError(f"cell {index} id must be letters, digits, _ or -")
        if cell_id in ids:
            raise ValueError(f"cell id {cell_id} is used twice")
        ids.add(cell_id)
        clean = {
            "id": cell_id, "kind": kind,
            "x": _int(cell.get("x"), f"cell {index} x", 0, MAX_COLUMNS - 1),
            "y": _int(cell.get("y"), f"cell {index} y", 0, MAX_ROWS - 1),
            "w": _int(cell.get("w"), f"cell {index} w", 1, MAX_COLUMNS),
            "h": _int(cell.get("h"), f"cell {index} h", 1, MAX_ROWS),
        }
        if clean["x"] + clean["w"] > width:
            raise ValueError(f"cell {index} runs past the right edge")
        if clean["y"] + clean["h"] > MAX_ROWS:
            raise ValueError(f"cell {index} runs past row {MAX_ROWS}")
        title_override = _text(cell.get("title"), f"cell {index} title", 120)
        if title_override:
            clean["title"] = title_override
        if kind == "text":
            clean["heading"] = _text(cell.get("heading"), f"cell {index} heading", 160)
            clean["body"] = _text(cell.get("body"), f"cell {index} body", 2000)
            if not clean["heading"] and not clean["body"]:
                raise ValueError(f"cell {index}: a text cell needs a heading or a body")
        elif kind == "value":
            # A value lives in a published artifact, not in this chat: the block
            # names the artifact and the value, and may move it to another slice.
            if stage_values.shape(cell.get("shape"), f"cell {index} shape") == "grid":
                clean["shape"] = "grid"
            clean["artifact"] = stage_values.slug(cell.get("artifact"), f"cell {index} artifact")
            clean["object"] = stage_values.object_id(cell.get("object"), f"cell {index} object")
            if cell.get("from_version") is not None:
                clean["from_version"] = _int(cell.get("from_version"), f"cell {index} from_version", 1, 1_000_000)
            for key, limit in (("source_label", 240), ("measure", 120), ("label", 80), ("caption", 160)):
                text = _text(cell.get(key), f"cell {index} {key}", limit)
                if text:
                    clean[key] = text
            overrides = stage_values.params(cell.get("params"), f"cell {index} params")
            if overrides:
                clean["params"] = overrides
            fmt = cell.get("format", "auto")
            if fmt not in FORMATS:
                raise ValueError(f"cell {index} format must be one of {', '.join(sorted(FORMATS))}")
            clean["format"] = fmt
            if clean.get("shape") == "grid" and (clean["w"] < 2 or clean["h"] < 2):
                raise ValueError(f"cell {index} is smaller than a grid cell can be (2×2)")
        else:
            clean["ref"] = _surface_id(cell.get("ref"), f"cell {index} ref")
            version = cell.get("version", "latest")
            clean["version"] = "latest" if version in (None, "", "latest") else _surface_id(version, f"cell {index} version")
            view = cell.get("view")
            if view is not None:
                if view not in VIEWS:
                    raise ValueError(f"cell {index} view must be table, chart or sql")
                clean["view"] = view
        if kind == "stat":
            clean["column"] = _text(cell.get("column"), f"cell {index} column", 120, required=True)
            clean["row"] = _int(cell.get("row", 0), f"cell {index} row", 0, 10000)
            clean["label"] = _text(cell.get("label"), f"cell {index} label", 80)
            clean["caption"] = _text(cell.get("caption"), f"cell {index} caption", 160)
            fmt = cell.get("format", "auto")
            if fmt not in FORMATS:
                raise ValueError(f"cell {index} format must be one of {', '.join(sorted(FORMATS))}")
            clean["format"] = fmt
            delta = _text(cell.get("delta_column"), f"cell {index} delta_column", 120)
            if delta:
                clean["delta_column"] = delta
        low = MIN_SIZE[kind] if kind != "surface" else (1, 1)
        if clean["w"] < low[0] or clean["h"] < low[1]:
            raise ValueError(f"cell {index} is smaller than a {kind} cell can be ({low[0]}×{low[1]})")
        out.append(clean)
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]:
                raise ValueError(f"cells {a['id']} and {b['id']} overlap")
    out.sort(key=lambda c: (c["y"], c["x"], c["id"]))
    return {"title": title, "unit": UNIT, "width": width, "cells": out,
            "deleted": bool(payload.get("deleted"))}


def resolve(conn, session_id, payload, actor=None):
    """Attach member kinds, titles and lineage keys; check refs and minimums.
    A value block may only name an artifact its author can open; whoever looks
    at the arrangement later is checked again when the value is read."""
    import artifact_access

    for cell in payload["cells"]:
        if cell["kind"] == "text":
            continue
        if cell["kind"] == "value":
            if actor is not None and not artifact_access.can_view(conn, cell["artifact"], actor):
                raise ValueError(f"cell {cell['id']}: {cell['artifact']} is not an artifact you can open")
            cell["member_kind"] = "grid" if cell.get("shape") == "grid" else "value"
            cell["member_title"] = cell.get("label") or cell.get("source_label") or cell["object"]
            continue
        row = conn.execute(
            "SELECT id,kind,title,lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s::uuid AND session_id=%s::uuid",
            (cell["ref"], session_id),
        ).fetchone()
        if not row:
            raise ValueError(f"cell {cell['id']}: {cell['ref']} is not a Stage surface in this chat")
        if row["kind"] == "arrangement":
            raise ValueError("arrangements cannot show other arrangements")
        if cell["kind"] == "stat" and row["kind"] != "query":
            raise ValueError("stat cells must reference a query result surface")
        if row["kind"] not in MEMBER_KINDS:
            raise ValueError(f"cell {cell['id']}: a {row['kind']} surface cannot be placed in an arrangement")
        if cell["version"] != "latest":
            pinned = conn.execute(
                "SELECT 1 FROM rvbbit.calliope_surfaces WHERE id=%s::uuid AND session_id=%s::uuid AND lineage_key=%s",
                (cell["version"], session_id, row["lineage_key"]),
            ).fetchone()
            if not pinned:
                raise ValueError(f"cell {cell['id']}: pinned version is not a revision of that surface")
        cell["member_kind"] = row["kind"]
        cell["member_title"] = row["title"]
        cell["lineage_key"] = row["lineage_key"]
        low = MIN_SIZE["stat"] if cell["kind"] == "stat" else MIN_SIZE.get(row["kind"], (1, 1))
        if cell["w"] < low[0] or cell["h"] < low[1]:
            raise ValueError(f"cell {cell['id']} is smaller than a {row['kind']} cell can be ({low[0]}×{low[1]})")
    payload["members"] = sorted({c["ref"] for c in payload["cells"] if c.get("ref")})
    return payload


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _lineage(lineage_key):
    return lineage_key if lineage_key.startswith("arrangement:") else None


def publish(conn_factory, session_id, actor, payload, replaces_surface_id=None, turn_id=None, origin="arrangement"):
    """Save an arrangement revision for `actor`, a participant of the chat.
    Without replaces_surface_id this starts a new arrangement; with it, this
    revises that one and refuses when it is no longer the latest revision.
    A person's revision attaches to the chat's latest turn; Callie's tools
    pass the turn they are serving."""
    import calliope

    payload = validate(payload)
    parent_id = _surface_id(replaces_surface_id, "replaces_surface_id") if replaces_surface_id else None
    with conn_factory() as conn, conn.transaction():
        calliope_access.require_participant(conn, session_id, actor)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"stage-arrangement:{session_id}",))
        if turn_id:
            turn = conn.execute(
                "SELECT id FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid",
                (_surface_id(turn_id, "turn_id"), session_id),
            ).fetchone()
            if not turn:
                raise ValueError("That turn is not part of this chat")
        else:
            turn = conn.execute(
                "SELECT id FROM rvbbit.calliope_turns WHERE session_id=%s::uuid ORDER BY ordinal DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not turn:
            raise ValueError("Start a conversation before arranging its Stage")
        if parent_id:
            parent = conn.execute(
                "SELECT lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s::uuid "
                "AND session_id=%s::uuid AND kind='arrangement'", (parent_id, session_id),
            ).fetchone()
            if not parent:
                raise ValueError("That arrangement does not belong to this chat")
            lineage = parent["lineage_key"]
        else:
            lineage = f"arrangement:{uuid4()}"
        resolve(conn, session_id, payload, actor)
        latest = conn.execute(
            "SELECT id,payload,parent_surface_id FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid "
            "AND lineage_key=%s AND kind='arrangement' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 1",
            (session_id, lineage),
        ).fetchone()
        digest = fingerprint(payload)
        identical = latest and (latest.get("payload") or {}).get("content_sha256") == digest
        if parent_id and latest and str(latest["id"]) != parent_id:
            if not (identical and str(latest.get("parent_surface_id")) == parent_id):
                raise SupersededError(str(latest["id"]))
        if identical:
            return {"surface_id": str(latest["id"]), "lineage_key": lineage, "reused": True,
                    "surface": _surface_row(conn, latest["id"])}
        payload["content_sha256"] = digest
        parent_id = str(latest["id"]) if latest else None
        # Every revision is its own row: restoring an earlier layout repeats
        # its content hash, so the row's identity cannot be the hash.
        rows = calliope._insert_surfaces(conn_factory, session_id, str(turn["id"]), [{
            "kind": "arrangement", "title": payload["title"], "lineage_key": lineage,
            "tool_name": "arrangement", "tool_call_id": f"arrangement:{lineage}:{uuid4()}",
            "parent_surface_id": parent_id, "payload": payload,
            "source": {"origin": origin, "actor": actor, "members": payload["members"]},
            "presentation": {"purpose": "arrangement", "phase": "published"},
        }])
        if not rows:
            raise ValueError("Saving the arrangement returned no surface")
        return {"surface_id": rows[0]["id"], "lineage_key": lineage, "reused": False, "surface": rows[0]}


class SupersededError(ValueError):
    """Someone saved a newer revision; the editor must reload it."""

    def __init__(self, latest_surface_id):
        super().__init__("This arrangement was revised elsewhere; reload it to keep editing.")
        self.latest_surface_id = latest_surface_id


def _surface_row(conn, surface_id):
    import calliope

    row = conn.execute("SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s::uuid", (surface_id,)).fetchone()
    return calliope._surface_json(row) if row else None


def latest(conn_factory, session_id, viewer):
    """The latest revision of every arrangement in the chat, newest first,
    without deleted ones."""
    with conn_factory() as conn:
        calliope_access.require_view(conn, session_id, viewer)
        rows = conn.execute(
            "SELECT DISTINCT ON (lineage_key) id,title,lineage_key,payload,created_at "
            "FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid AND kind='arrangement' "
            "ORDER BY lineage_key, created_at DESC, ordinal DESC, id DESC",
            (session_id,),
        ).fetchall()
    out = []
    for row in rows:
        payload = row["payload"] or {}
        if payload.get("deleted"):
            continue
        out.append({"surface_id": str(row["id"]), "lineage_key": row["lineage_key"], "title": row["title"],
                    "width": payload.get("width"), "cells": len(payload.get("cells") or []),
                    "updated_at": row["created_at"].isoformat() if row.get("created_at") else None})
    out.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    return out


def page_urls(session_id, lineage_key):
    """Where an arrangement lives: its own page, and the chat with it open."""
    uuid_part = str(lineage_key).split(":", 1)[-1]
    return {"page_url": f"/calliope/arrangements/{uuid_part}",
            "chat_url": f"/calliope?session={session_id}&arrangement={uuid_part}"}


def read(conn_factory, ref, viewer):
    """One arrangement by revision id, or by its lineage's uuid (the latest
    revision, which is what the page shows)."""
    try:
        sid = _surface_id(ref, "ref")
    except ValueError:
        return None
    columns = "id,title,payload,parent_surface_id,session_id,lineage_key,created_at"
    with conn_factory() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM rvbbit.calliope_surfaces "
            "WHERE id=%s::uuid AND kind='arrangement' AND rvbbit.calliope_session_can_view(session_id,%s,false)",
            (sid, viewer),
        ).fetchone()
        if not row:
            row = conn.execute(
                f"SELECT {columns} FROM rvbbit.calliope_surfaces WHERE lineage_key=%s AND kind='arrangement' "
                "AND rvbbit.calliope_session_can_view(session_id,%s,false) "
                "ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 1",
                (f"arrangement:{sid}", viewer),
            ).fetchone()
        if not row:
            return None
        revisions = conn.execute(
            "SELECT id,title,created_at,source FROM rvbbit.calliope_surfaces WHERE session_id=%s AND lineage_key=%s "
            "AND kind='arrangement' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 200",
            (row["session_id"], row["lineage_key"]),
        ).fetchall()
    payload = row["payload"] or {}
    return {"surface_id": str(row["id"]), "lineage_key": row["lineage_key"], "title": row["title"],
            "session_id": str(row["session_id"]), "latest_surface_id": str(revisions[0]["id"]),
            **page_urls(row["session_id"], row["lineage_key"]),
            "revisions": [{"surface_id": str(r["id"]), "title": r["title"],
                           "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                           "actor": (r.get("source") or {}).get("actor")} for r in revisions],
            **{key: payload.get(key) for key in ("unit", "width", "cells", "members", "deleted", "content_sha256")}}


def register(mcp, conn_factory, api_owner, json_response, access_error_response):
    import asyncio
    import uuid as _uuid

    def error(message, status=400, **extra):
        return json_response({"error": {"message": message, **extra}}, status)

    def same_origin(request):
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        from urllib.parse import urlsplit
        return not origin or urlsplit(origin).netloc == host

    async def body_of(request, limit=512 * 1024):
        raw = await request.body()
        if len(raw) > limit:
            raise ValueError("The arrangement is too large")
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValueError("Expected an arrangement")
        return body

    @mcp.custom_route("/api/calliope/sessions/{session_id}/arrangements", methods=["GET", "POST"])
    async def arrangements(request):
        owner, err = api_owner(request)
        if err:
            return err
        try:
            session_id = str(_uuid.UUID(request.path_params["session_id"]))
            if request.method == "GET":
                return json_response({"arrangements": await asyncio.to_thread(latest, conn_factory, session_id, owner)})
            if not same_origin(request) or request.headers.get("x-calliope-stage") != "1":
                return error("Arrange from this chat's Stage.", 403)
            body = await body_of(request)
            result = await asyncio.to_thread(publish, conn_factory, session_id, owner, body)
            return json_response(result, 201)
        except calliope_access.CalliopeAccessError as exc:
            return access_error_response(exc)
        except (ValueError, TypeError) as exc:
            return error(str(exc)[:1000])

    @mcp.custom_route("/api/calliope/arrangements/{surface_id}", methods=["GET", "PUT"])
    async def arrangement(request):
        owner, err = api_owner(request)
        if err:
            return err
        surface_id = request.path_params["surface_id"]
        try:
            if request.method == "GET":
                saved = await asyncio.to_thread(read, conn_factory, surface_id, owner)
                return json_response(saved) if saved else error("Arrangement not found", 404, code="NOT_FOUND")
            if not same_origin(request) or request.headers.get("x-calliope-stage") != "1":
                return error("Arrange from this chat's Stage.", 403)
            saved = await asyncio.to_thread(read, conn_factory, surface_id, owner)
            if not saved:
                return error("Arrangement not found", 404, code="NOT_FOUND")
            body = await body_of(request)
            merged = {key: body.get(key, saved.get(key)) for key in ("title", "width", "cells", "unit", "deleted")}
            result = await asyncio.to_thread(publish, conn_factory, saved["session_id"], owner, merged, surface_id)
            return json_response(result)
        except SupersededError as exc:
            return error(str(exc), 409, code="ARRANGEMENT_SUPERSEDED", latest_surface_id=exc.latest_surface_id)
        except calliope_access.CalliopeAccessError as exc:
            return access_error_response(exc)
        except (ValueError, TypeError) as exc:
            return error(str(exc)[:1000])
