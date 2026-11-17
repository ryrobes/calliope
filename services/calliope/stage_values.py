"""Values and grids: what a team's dashboards already show, as building blocks.

Nobody declares these. The artifact semantic compiler describes each callout
of a published dashboard as a value and each table, chart or repeated run as a
grid (what it measures, over which slice, the SQL that recreates it), and this
module lists them across every artifact a person may open, so an arrangement
can place one as a live block: a number, or a query grid.

Pure helpers only; the routes that read the database and run a value for the
viewer live in server.py beside the artifact inspection route.
"""
from __future__ import annotations

import json
import re

MAX_PARAMS = 16
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$", re.I)
_OBJECT = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")
_PARAM = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


def _clip(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def slug(value, name="artifact"):
    text = str(value or "").strip()
    if not _SLUG.fullmatch(text):
        raise ValueError(f"{name} must be an artifact slug")
    return text


def object_id(value, name="object"):
    text = str(value or "").strip()
    if not _OBJECT.fullmatch(text):
        raise ValueError(f"{name} must be a value id")
    return text


def params(value, name="params"):
    """Slice overrides a block carries: a few named scalars or short lists."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict) or len(value) > MAX_PARAMS:
        raise ValueError(f"{name} must be an object of at most {MAX_PARAMS} slices")
    out = {}
    for key, item in value.items():
        if not _PARAM.fullmatch(str(key)):
            raise ValueError(f"{name}: {key!r} is not a slice name")
        if isinstance(item, list):
            if len(item) > 50 or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in item):
                raise ValueError(f"{name}: {key} must be a short list of text or numbers")
            out[str(key)] = [x[:200] if isinstance(x, str) else x for x in item]
        elif isinstance(item, (bool, int, float)) or item is None:
            out[str(key)] = item
        elif isinstance(item, str):
            out[str(key)] = item[:400]
        else:
            raise ValueError(f"{name}: {key} must be text, a number or yes/no")
    return out


SHAPES = {"value", "grid"}


def shape(value, name="shape"):
    text = str(value or "value").strip().lower()
    if text not in SHAPES:
        raise ValueError(f"{name} must be value or grid")
    return text


def objects_of(authored, generated):
    """An artifact version's values: what its builder wrote, then what the
    compiler found that the builder did not already cover."""
    out, seen = [], set()
    for source in (authored, generated):
        for obj in (source or {}).get("objects") or []:
            if isinstance(obj, dict) and obj.get("id") and obj["id"] not in seen and (obj.get("evaluator") or {}).get("sql"):
                seen.add(obj["id"])
                out.append(obj)
    return out


def grids_of(semantic_map):
    """An artifact version's grids: each table, chart or repeated run as one query."""
    out, seen = [], set()
    for grid in (semantic_map or {}).get("grids") or []:
        if isinstance(grid, dict) and grid.get("id") and grid["id"] not in seen and (grid.get("evaluator") or {}).get("sql"):
            seen.add(grid["id"])
            out.append(grid)
    return out


def slices_of(obj, overrides=None):
    """The filters a value is taken over, with the value each one has now."""
    overrides = overrides or {}
    out = []
    for name, spec in list((obj.get("parameters") or {}).items())[:MAX_PARAMS]:
        if not isinstance(spec, dict):
            continue
        out.append({k: v for k, v in {
            "name": name,
            "label": spec.get("label") or name.replace("_", " ").title(),
            "type": spec.get("type") or "text",
            "default": spec.get("default"),
            "value": overrides.get(name, spec.get("default")),
            "changed": True if name in overrides and overrides[name] != spec.get("default") else None,
            "dimension": spec.get("dimension"),
            "fixed": True if spec.get("fixed") else None,
        }.items() if v is not None or k in ("default", "value")})
    return out


def item(row, obj):
    """One library entry. `row` is an artifact; no SQL leaves the server here."""
    meaning = obj.get("meaning") or {}
    label = _clip(meaning.get("label") or obj["id"].replace("_", " "), 160)
    is_grid = obj.get("kind") == "grid"
    columns = [{k: c.get(k) for k in ("name", "label", "role", "measure", "unit") if c.get(k)}
               for c in (obj.get("columns") or []) if isinstance(c, dict)][:40] if is_grid else None
    return {k: v for k, v in {
        "key": f"{row['slug']}:{'grid:' if is_grid else ''}{obj['id']}",
        "shape": "grid" if is_grid else "value",
        "measures": (meaning.get("measures") or None) if is_grid else None,
        "grain": meaning.get("grain") if is_grid else None,
        "columns": columns or None,
        "artifact": row["slug"],
        "artifact_name": row.get("name") or row["slug"],
        "owner": str(row.get("owner_email") or "").split("@")[0] or None,
        "version": int(row.get("version") or row.get("latest_version") or 1),
        "object": obj["id"],
        "label": label,
        "measure": meaning.get("measure") or None,
        "unit": meaning.get("unit") or None,
        "description": _clip(meaning.get("description") or meaning.get("formula"), 260) or None,
        "asked_as": meaning.get("asked_as") or None,
        "slices": slices_of(obj) or None,
        "updated_at": row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else row.get("updated_at"),
        "href": row.get("href"),
    }.items() if v is not None}


def flatten(rows, named_only=True):
    """Every value of every artifact row ({slug, name, …, authored, generated}).

    By default only values that name what they measure are listed. Older
    analyses reproduced a number without saying what it counts or over which
    slice; they stay readable where already placed, and join the library when
    their dashboard is next published and recompiled."""
    out = []
    for row in rows:
        for obj in objects_of(row.get("authored"), row.get("generated")):
            if named_only and not (obj.get("meaning") or {}).get("measure"):
                continue
            # One cell of a table or mark of a chart is not a building block;
            # the grid it belongs to is.
            if named_only and obj.get("kind") == "cell":
                continue
            out.append(item(row, obj))
        for grid in grids_of(row.get("generated")):
            out.append(item(row, grid))
    return out


def _haystack(entry):
    return " ".join(str(part or "") for part in (
        entry.get("label"), entry.get("measure"), entry.get("artifact_name"), entry.get("description"),
        entry.get("asked_as"), entry.get("unit"), entry.get("owner"), entry.get("grain"),
        "grid table chart" if entry.get("shape") == "grid" else "value",
        " ".join(entry.get("measures") or []),
        " ".join(str(s.get("value") if s.get("value") is not None else "") for s in entry.get("slices") or []),
    )).lower()


def search(items, query="", limit=40):
    """Entries whose text holds every word of the query, best first: a word in
    the label or measure counts for more than one in the description; newest
    artifacts break ties. Without a query, the newest artifacts' values."""
    words = [w for w in re.split(r"\s+", str(query or "").lower().strip()) if w]
    scored = []
    for entry in items:
        if not words:
            scored.append((0, entry))
            continue
        text = _haystack(entry)
        if not all(word in text for word in words):
            continue
        label = str(entry.get("label") or "").lower()
        measure = " ".join([str(entry.get("measure") or "")] + list(entry.get("measures") or [])).lower()
        score = sum(3 * (word in label) + 3 * (word in measure) + 1 for word in words)
        scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], _neg(pair[1].get("updated_at")), str(pair[1].get("label") or "").lower()))
    limit = max(1, min(int(limit or 40), 600))
    return [entry for _, entry in scored[:limit]], len(scored)


def _neg(stamp):
    # Newest first inside one score, without parsing: ISO stamps sort as text.
    return "".join(chr(0x10FFFF - ord(ch)) for ch in str(stamp or ""))


def match_object(objects, wanted_id, source_label=None, measure=None):
    """Find a placed value again after its artifact was recompiled: by id, then
    by the measure and label it had when placed, then by that label alone when
    only one value carries it. Returns (object, how) or (None, None)."""
    for obj in objects:
        if obj.get("id") == wanted_id:
            return obj, "id"
    label = str(source_label or "").strip().lower()
    if not label:
        return None, None
    same_label = [o for o in objects if str((o.get("meaning") or {}).get("label") or "").strip().lower() == label]
    if measure:
        both = [o for o in same_label if (o.get("meaning") or {}).get("measure") == measure]
        if len(both) == 1:
            return both[0], "measure"
    if len(same_label) == 1:
        return same_label[0], "label"
    return None, None


def cache_key(viewer, artifact, version, obj, overrides):
    return json.dumps([viewer, artifact, version, obj.get("definition_hash") or obj.get("id"), overrides], sort_keys=True, default=str)
