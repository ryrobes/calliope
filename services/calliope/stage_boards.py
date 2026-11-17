"""Boards: one Stage card that composes existing surfaces into a cohesive layout.

A board is not a new dashboard DSL. Its cells point at surfaces that already
exist in the chat — live charts, query results, metric tiles, maps, answers,
images, Found tiles — plus two small primitives of its own: a stat (one number
read from a query result) and a text block (eyebrow / heading / body). The
Stage renders every cell with the renderer that surface kind already has, so
theme, receipts, revisions and provenance carry over untouched; the board only
adds a 12-column layout. Standalone cards that a visible board assembles are
folded into it on the Stage.

Layout: cells are placed in order on a 12-column grid. `layout: "auto"` picks
spans from cell kinds (header text full width, stats in rows of up to four,
paired charts side by side, tables full width); explicit `span` (1–12) and
`rows` (1–3 height units) on a cell always win, and `layout: {"rows": [...]}`
lays cells out row by row.
"""
import hashlib
import json
import re
from uuid import UUID

MAX_CELLS = 16
MAX_ROWS = 24
SIZES = {"wide", "full"}
CELL_KINDS = {"surface", "stat", "text"}
MEMBER_KINDS = {"chart", "query", "metric", "map", "answer", "image", "find", "document", "cube", "value_system"}
STAT_FORMATS = {"auto", "number", "compact", "percent", "currency", "text"}
VIEWS = {"table", "chart", "sql"}


def _text(value, name, limit, required=False):
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be plain text")
    value = " ".join(value.split())
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    return value


def _surface_id(value, name="ref"):
    try:
        return str(UUID(str(value).removeprefix("surface:")))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{name} must be a Stage surface UUID from this chat (bare UUID or surface:UUID)") from None


def _span(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12:
        raise ValueError(f"{name} must be an integer 1–12 (grid columns)")
    return value


def validate(title, cells, layout="auto", subtitle="", meta="", size="full"):
    """Shape-check the board; surface references are resolved later against the chat."""
    title = _text(title, "title", 160, required=True)
    subtitle = _text(subtitle, "subtitle", 320)
    meta = _text(meta, "meta", 160)
    if size not in SIZES:
        raise ValueError("size must be wide or full")
    if not isinstance(cells, list) or not 1 <= len(cells) <= MAX_CELLS:
        raise ValueError(f"cells must be a list of 1–{MAX_CELLS} cells")
    out = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValueError(f"cell {index} must be an object")
        kind = str(cell.get("kind") or ("text" if cell.get("heading") or cell.get("body") else "surface"))
        if kind not in CELL_KINDS:
            raise ValueError(f"cell {index}: kind must be one of surface, stat, text")
        clean = {
            "kind": kind,
            "title": _text(cell.get("title"), f"cell {index} title", 120),
            "span": _span(cell.get("span"), f"cell {index} span"),
            "rows": None,
        }
        rows = cell.get("rows")
        if rows is not None:
            if isinstance(rows, bool) or not isinstance(rows, int) or not 1 <= rows <= 3:
                raise ValueError(f"cell {index} rows must be 1–3 height units")
            clean["rows"] = rows
        if kind == "text":
            clean.update({
                "eyebrow": _text(cell.get("eyebrow"), f"cell {index} eyebrow", 80),
                "heading": _text(cell.get("heading"), f"cell {index} heading", 160),
                "body": _text(cell.get("body"), f"cell {index} body", 1200),
                "aside": _text(cell.get("aside"), f"cell {index} aside", 160),
            })
            if not (clean["eyebrow"] or clean["heading"] or clean["body"]):
                raise ValueError(f"cell {index}: a text cell needs an eyebrow, heading or body")
        else:
            clean["ref"] = _surface_id(cell.get("ref") or cell.get("surface_id"), f"cell {index} ref")
            view = cell.get("view")
            if view is not None:
                if view not in VIEWS:
                    raise ValueError(f"cell {index} view must be table, chart or sql")
                clean["view"] = view
        if kind == "stat":
            column = _text(cell.get("column"), f"cell {index} column", 120, required=True)
            fmt = str(cell.get("format") or "auto")
            if fmt not in STAT_FORMATS:
                raise ValueError(f"cell {index} format must be one of {', '.join(sorted(STAT_FORMATS))}")
            row = cell.get("row", 0)
            if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row <= 999:
                raise ValueError(f"cell {index} row must be a result row index (0-based)")
            clean.update({
                "column": column, "format": fmt, "row": row,
                "label": _text(cell.get("label"), f"cell {index} label", 80),
                "caption": _text(cell.get("caption"), f"cell {index} caption", 160),
                "delta_column": _text(cell.get("delta_column"), f"cell {index} delta_column", 120),
            })
        out.append(clean)
    if layout != "auto":
        if not isinstance(layout, dict) or not isinstance(layout.get("rows"), list) or not layout["rows"]:
            raise ValueError('layout must be "auto" or {"rows": [[cell_index, ...], ...]}')
        if len(layout["rows"]) > MAX_ROWS:
            raise ValueError(f"layout may have at most {MAX_ROWS} rows")
        seen = set()
        for r, row in enumerate(layout["rows"]):
            if not isinstance(row, list) or not row:
                raise ValueError(f"layout row {r} must be a non-empty list of cell indexes")
            for item in row:
                if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < len(out):
                    raise ValueError(f"layout row {r}: cell indexes must be 0–{len(out) - 1}")
                if item in seen:
                    raise ValueError(f"layout row {r}: cell {item} placed twice")
                seen.add(item)
        if seen != set(range(len(out))):
            raise ValueError("layout must place every cell exactly once")
        layout = {"rows": [list(row) for row in layout["rows"]]}
    return {"title": title, "subtitle": subtitle, "meta": meta, "size": size, "layout": layout, "cells": out}


def _member_kind(cell, kinds):
    return kinds.get(cell.get("ref")) if cell["kind"] == "surface" else cell["kind"]


def autolayout(cells, kinds):
    """Assign spans in order on a 12-column grid. Explicit spans win.

    kinds maps surface ids to their Stage kinds. Rules, in the language of the
    result: a header text block spans the row; stats sit in rows of up to
    four; charts and maps pair up side by side (a lone chart takes the row);
    tables, answers and Found tiles take the row; metric tiles are quarter
    width; images are half width.
    """
    spans = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        if cell.get("span"):
            spans.append(cell["span"])
            index += 1
            continue
        member = _member_kind(cell, kinds)
        if member == "stat" or member == "metric":
            group = 0
            while index + group < len(cells) and not cells[index + group].get("span") \
                    and _member_kind(cells[index + group], kinds) in {"stat", "metric"}:
                group += 1
            per_row = min(4, group)
            width = 12 // per_row
            spans.extend([width] * group)
            index += group
            continue
        if member in {"chart", "map"}:
            group = 0
            while index + group < len(cells) and not cells[index + group].get("span") \
                    and _member_kind(cells[index + group], kinds) in {"chart", "map"}:
                group += 1
            if group == 1:
                spans.append(12)
            else:
                for offset in range(group):
                    remaining = group - offset
                    spans.append(12 if remaining == 1 and offset % 2 == 0 else 6)
            index += group
            continue
        if member == "image":
            spans.append(6)
        elif member == "text":
            spans.append(12 if (cell.get("heading") or not cell.get("body")) else 6)
        else:
            spans.append(12)
        index += 1
    return spans


def resolve(conn, session_id, payload):
    """Attach member surface ids/kinds/titles and final spans. Raises on bad refs."""
    kinds = {}
    for cell in payload["cells"]:
        if cell["kind"] == "text":
            continue
        row = conn.execute(
            "SELECT id,kind,title,lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s::uuid AND session_id=%s::uuid",
            (cell["ref"], session_id),
        ).fetchone()
        if not row:
            raise ValueError(f"cell ref {cell['ref']} is not a Stage surface in this chat")
        if row["kind"] == "board":
            raise ValueError("boards cannot nest other boards")
        if cell["kind"] == "stat" and row["kind"] != "query":
            raise ValueError("stat cells must reference a query result surface (run_sql)")
        if row["kind"] not in MEMBER_KINDS:
            raise ValueError(f"cell ref {cell['ref']} is a {row['kind']} surface, which boards cannot show")
        cell["member_kind"] = row["kind"]
        cell["member_title"] = row["title"]
        cell["lineage_key"] = row["lineage_key"]
        kinds[cell["ref"]] = row["kind"]
    if payload["layout"] == "auto":
        spans = autolayout(payload["cells"], kinds)
        for cell, span in zip(payload["cells"], spans):
            cell["span"] = span
    else:
        # explicit rows: divide each row evenly unless the cells carry spans
        for row in payload["layout"]["rows"]:
            free = [i for i in row if not payload["cells"][i].get("span")]
            used = sum(payload["cells"][i]["span"] for i in row if payload["cells"][i].get("span"))
            if free:
                width = max(1, (12 - used) // len(free))
                for i in free:
                    payload["cells"][i]["span"] = width
        payload["cells"] = [payload["cells"][i] for row in payload["layout"]["rows"] for i in row]
    for cell in payload["cells"]:
        cell.setdefault("rows", 1)
    payload["members"] = sorted({c["ref"] for c in payload["cells"] if c.get("ref")})
    return payload


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def publish(conn_factory, destination, title, cells, layout="auto", subtitle="", meta="", size="full",
            replaces_surface_id=None):
    import calliope

    payload = validate(title, cells, layout, subtitle, meta, size)
    parent_id = _surface_id(replaces_surface_id, "replaces_surface_id") if replaces_surface_id else None
    session_id, turn_id = destination["session_id"], destination["turn_id"]
    slug = re.sub(r"[^a-z0-9]+", "-", payload["title"].lower()).strip("-")[:48] or "board"
    lineage = f"board:{turn_id}:{slug}"
    with conn_factory() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"stage-board:{session_id}",))
        if parent_id:
            parent = conn.execute(
                "SELECT lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s::uuid "
                "AND session_id=%s::uuid AND kind='board'", (parent_id, session_id),
            ).fetchone()
            if not parent:
                raise ValueError("Replacement board must belong to this chat")
            lineage = parent["lineage_key"]
        resolve(conn, session_id, payload)
        latest = conn.execute(
            "SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid "
            "AND lineage_key=%s AND kind='board' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 1",
            (session_id, lineage),
        ).fetchone()
        digest = fingerprint(payload)
        identical = latest and (latest.get("payload") or {}).get("content_sha256") == digest
        if parent_id and latest and str(latest["id"]) != parent_id:
            if not (identical and str(latest.get("parent_surface_id")) == parent_id):
                raise ValueError(f"Board was revised; use latest surface {latest['id']}")
        if identical:
            return {"surface_id": str(latest["id"]), "kind": "board", "reused": True,
                    "members": payload["members"]}
        payload["content_sha256"] = digest
        parent_id = str(latest["id"]) if latest else None
        rows = calliope._insert_surfaces(conn_factory, session_id, turn_id, [{
            "kind": "board", "title": payload["title"], "lineage_key": lineage,
            "tool_name": "publish_board", "tool_call_id": f"board:{turn_id}:{parent_id or 'root'}:{digest}",
            "parent_surface_id": parent_id, "payload": payload,
            "source": {"origin": "publish_board", "members": payload["members"]},
            "presentation": {"purpose": "board", "phase": "published", "size": size},
        }])
        if not rows:
            raise ValueError("Board publication returned no surface")
        return {"surface_id": rows[0]["id"], "kind": "board", "reused": False,
                "members": payload["members"],
                "cells": [{"kind": c["kind"], "ref": c.get("ref"), "member_kind": c.get("member_kind"),
                           "span": c["span"], "rows": c["rows"]} for c in payload["cells"]],
                "message": "Board published to the Stage; its member cards fold into it. "
                           "Use this surface_id with update_board to revise it."}


def read(conn_factory, surface_id, viewer):
    sid = None
    try:
        sid = _surface_id(surface_id, "surface_id")
    except ValueError:
        pass
    if not sid:
        return {"error": {"code": "NOT_FOUND", "message": "Board not found"}}
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT id,title,payload,parent_surface_id,session_id,lineage_key FROM rvbbit.calliope_surfaces "
            "WHERE id=%s::uuid AND kind='board' AND rvbbit.calliope_session_can_view(session_id,%s,false)",
            (sid, viewer),
        ).fetchone()
        if not row:
            return {"error": {"code": "NOT_FOUND", "message": "Board not found"}}
        revisions = conn.execute(
            "SELECT id,title,created_at FROM rvbbit.calliope_surfaces WHERE session_id=%s AND lineage_key=%s "
            "AND kind='board' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 30",
            (row["session_id"], row["lineage_key"]),
        ).fetchall()
    payload = row["payload"] or {}
    return {"surface_id": str(row["id"]), "title": row["title"], "latest_surface_id": str(revisions[0]["id"]),
            "revisions": [{"surface_id": str(r["id"]), "title": r["title"]} for r in revisions],
            **{key: payload.get(key) for key in ("subtitle", "meta", "size", "layout", "cells", "members", "visual_review")}}
