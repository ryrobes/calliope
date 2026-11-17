"""The Briarpatch: a living Excalidraw whiteboard of the data universe.

A deterministic projector (docs/KNOWLEDGE_LOOP_PLAN.md §4f) renders what the
knowledge loop currently believes — source systems, domains with their
adoption-ladder state, the domain experts, wiki notes, and the wilds of
not-yet-governed data — into one durable system Sketch, served read-only to
viewers at /calliope/sketches/<id>.

Layout stability is the contract: every machine element carries a stable
``bp-``-prefixed id and a sticky home position, and each tick applies only
the minimal diff (add / set_text / style / delete). Elements without the
``bp-`` prefix belong to humans and are never touched — the v2 seam for
whiteboard-as-correction-channel.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid as uuid_module
from typing import Any, Callable

TICK_SECONDS = int(os.environ.get("BRIARPATCH_TICK_SECONDS", "120"))
TITLE = "The Briarpatch"
MAX_OPS_PER_APPLY = 70  # under the sketch layer's 80-op cap

# Radial "star schema" layout: Calliope at the hub, source systems as
# spokes on a ring, domains fanned outward within their schema's sector,
# experts on the rim, the wilds as their own untamed sector. Angles are
# hash-derived per schema key (stable forever for a given schema) and then
# nudged apart to a minimum separation, so the map grows by adding slices
# without reshuffling what a viewer already knows.
HUB_X, HUB_Y = 1500, 1050
SCHEMA_RADIUS = 430
DOMAIN_RADIUS_BASE, DOMAIN_RADIUS_STEP = 760, 210
CARD_W, CARD_H = 330, 104
WILDS_ANGLE = 90.0  # due south (screen-down) is reserved for the wilds

STATE_STYLE = {
    "affirmed": {"stroke_color": "#2f9e44", "stroke_style": "solid"},
    "manual": {"stroke_color": "#2f9e44", "stroke_style": "solid"},
    "auto_provisional": {"stroke_color": "#1971c2", "stroke_style": "dashed"},
    "proposed": {"stroke_color": "#868e96", "stroke_style": "dotted"},
    "demoted": {"stroke_color": "#e03131", "stroke_style": "dashed"},
}

# The sketch layer refuses scenes over 500 elements. A labeled shape is two
# elements (shape + bound label), an arrow one, a text one, an image one. The
# map budgets itself under that cap instead of failing every tick once the
# harvest outgrows it (the pilot hit 160 usable proposals across 21 schemas).
ELEMENT_BUDGET = 440
ELEMENT_COST = {"add_shape": 2, "connect": 1, "add_text": 1, "add_image": 1}
DOMAINS_PER_SCHEMA_MAX = 8

AVATAR_SIZE = 80        # rendered raster chip, px
AVATAR_RING = 92        # status ring diameter around the chip
AVATAR_THUMB_LIMIT = 64_000   # bytes per thumbnail data payload
FILES_BYTE_BUDGET = 900_000   # total briarpatch-managed file store budget


def _pil_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _avatar_thumbnail(png: bytes) -> tuple[str, str] | None:
    """Circular-masked 96px thumbnail as (data_url, mime), or None when
    Pillow is unavailable / the source is unreadable / the result is still
    too large for the sketch file budget."""
    try:
        import base64
        import io

        from PIL import Image, ImageDraw, ImageOps

        image = Image.open(io.BytesIO(png)).convert("RGBA")
        image = ImageOps.fit(image, (192, 192), Image.LANCZOS)
        mask = Image.new("L", (192, 192), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 191, 191), fill=255)
        image.putalpha(mask)
        image = image.resize((96, 96), Image.LANCZOS)
        buffer = io.BytesIO()
        try:
            image.save(buffer, "WEBP", quality=82, method=4)
            mime = "image/webp"
        except Exception:  # noqa: BLE001 — Pillow built without webp
            buffer = io.BytesIO()
            image.save(buffer, "PNG", optimize=True)
            mime = "image/png"
        raw = buffer.getvalue()
        if len(raw) > AVATAR_THUMB_LIMIT:
            return None
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}", mime
    except Exception:  # noqa: BLE001
        return None


SCHEMA_MIN_GAP_DEG = 52.0   # preferred clearance between schema slices
SCHEMA_NUDGE_DEG = 7.0      # deterministic search step around the ring


def _ring_distance(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _place_angle(angle: float, placed: list[tuple[float, str]]) -> float:
    """Nudge `angle` around the ring until it clears every placed slice and the
    wilds sector by SCHEMA_MIN_GAP_DEG. Always terminates: one full revolution
    is searched in SCHEMA_NUDGE_DEG steps, and when nothing on the ring clears
    the preferred gap (more schemas than the gap allows) the candidate with the
    widest clearance wins. Deterministic for a given input order."""
    best_angle, best_clearance = angle, -1.0
    candidate = angle
    for _ in range(int(360.0 / SCHEMA_NUDGE_DEG) + 1):
        clearance = min(
            [_ring_distance(candidate, other) for other, _ in placed]
            + [_ring_distance(candidate, WILDS_ANGLE)]
        )
        if clearance >= SCHEMA_MIN_GAP_DEG:
            return candidate
        if clearance > best_clearance:
            best_angle, best_clearance = candidate, clearance
        candidate = (candidate + SCHEMA_NUDGE_DEG) % 360.0
    return best_angle


def estimate_elements(desired: dict[str, dict[str, Any]]) -> int:
    """Elements the sketch layer will materialize for a desired scene."""
    total = 0
    for spec in desired.values():
        op = str(spec.get("op") or "")
        cost = ELEMENT_COST.get(op, 1)
        if op == "add_shape" and not spec.get("label"):
            cost = 1
        total += cost
    return total


def _domain_priority(domain: dict[str, Any]) -> tuple[int, int, int, str]:
    """Promoted first, then families waiting on a decision, then the rest by
    how much validated substance they carry. Ties break on the key so the
    kept set is deterministic."""
    status = str(domain.get("status") or "")
    review = str(domain.get("family_review_state") or "")
    tier = 0 if status == "promoted" else 1 if review == "needs_decision" else 2
    return (
        tier,
        -int(domain.get("validated_metric_count") or 0),
        -int(domain.get("assertions") or 0),
        str(domain.get("domain_key") or ""),
    )


def fit_domains(
    domains: list[dict[str, Any]],
    *,
    agent_domains: set[str],
    wiki_pages: int,
    budget: int = ELEMENT_BUDGET,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Choose which domains the map draws so the scene fits the budget.

    Every schema keeps its slice; within a schema the top-k domains by
    priority are drawn and the rest collapse into one "+N more" label. k is
    the largest per-schema cap (up to DOMAINS_PER_SCHEMA_MAX) whose estimated
    scene fits. Returns (kept domains in their original order, overflow count
    per schema)."""
    by_schema: dict[str, list[dict[str, Any]]] = {}
    for domain in domains:
        by_schema.setdefault(str(domain["schema"]), []).append(domain)
    fixed = 2 + 3 + 3 + 3 * len(by_schema)   # hub, wilds+edge, header texts, slices
    for cap in range(DOMAINS_PER_SCHEMA_MAX, 0, -1):
        kept_keys: set[str] = set()
        overflow: dict[str, int] = {}
        for schema, members in by_schema.items():
            ranked = sorted(members, key=_domain_priority)
            kept_keys.update(str(d["domain_key"]) for d in ranked[:cap])
            if len(ranked) > cap:
                overflow[schema] = len(ranked) - cap
        agents_drawn = sum(1 for key in kept_keys if key in agent_domains)
        notes_drawn = min(wiki_pages, 2 * len(kept_keys))
        estimate = (
            fixed + 3 * len(kept_keys) + 3 * agents_drawn + 2 * notes_drawn + len(overflow)
        )
        if estimate <= budget or cap == 1:
            kept = [d for d in domains if str(d["domain_key"]) in kept_keys]
            return kept, overflow
    return domains, {}


def _desired_model(
    conn: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """(id -> desired element spec, file_id -> specialist_key wanted in the
    sketch file store). Deterministic: same state, same scene."""
    all_domains = [dict(r) for r in conn.execute(
        """
        SELECT summary.domain_key, summary.name, summary.status,
               coalesce(summary.adoption_state,'manual') AS adoption_state,
               summary.family_review_state,
               summary.validated_metric_count,
               split_part(summary.source_relations[1],'.',1) AS schema,
               (SELECT count(*) FROM rvbbit.business_semantic_assertions a
                 WHERE a.family_id=summary.family_id) AS assertions
          FROM rvbbit.business_value_graph_summary summary
         WHERE summary.status IN ('promoted')
            OR (summary.status='proposed' AND summary.usable)
         ORDER BY summary.domain_key
        """
    ).fetchall()]
    agents = [dict(r) for r in conn.execute(
        """
        SELECT s.specialist_key, s.display_name,
               EXISTS (SELECT 1 FROM rvbbit.calliope_specialist_runs r
                        WHERE r.specialist_id=s.id AND r.status='running') AS busy
          FROM rvbbit.calliope_specialists s
         WHERE s.status='active' AND s.capabilities @> '["domain_expert"]'::jsonb
        """
    ).fetchall()]
    wiki = [dict(r) for r in conn.execute(
        """
        SELECT slug, title, flagged_at IS NOT NULL AS flagged, subject_key, tags
          FROM rvbbit.wiki_pages WHERE adoption_state IN ('auto_provisional','affirmed')
         ORDER BY updated_at DESC LIMIT 24
        """
    ).fetchall()] if conn.execute(
        "SELECT to_regclass('rvbbit.wiki_pages') IS NOT NULL AS r"
    ).fetchone()["r"] else []
    wilds = [dict(r) for r in conn.execute(
        """
        SELECT domain_key, status FROM rvbbit.business_value_graph_summary
         WHERE status IN ('needs_revision','demoted')
         ORDER BY domain_key LIMIT 14
        """
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        """
        SELECT kind, count(*) AS n FROM rvbbit.knowledge_loop_events
         WHERE created_at > now() - interval '24 hours'
         GROUP BY kind ORDER BY kind
        """
    ).fetchall()]

    agents_by_domain = {
        str(a["specialist_key"]).replace("-", "_"): a for a in agents
    }
    # Budget the scene before laying it out: every schema keeps its slice,
    # crowded schemas draw their top domains and a "+N more" label.
    domains, overflow_by_schema = fit_domains(
        all_domains, agent_domains=set(agents_by_domain), wiki_pages=len(wiki)
    )

    # Portrait digests drive avatar chips: the content hash IS the file id,
    # so a regenerated portrait swaps the chip on the next tick and an
    # unchanged one costs nothing. Without Pillow (or the avatars table)
    # the map falls back to the labeled status ellipses.
    avatar_digests: dict[str, str] = {}
    if _pil_available() and conn.execute(
        "SELECT to_regclass('rvbbit.generated_specialist_avatars') IS NOT NULL AS r"
    ).fetchone()["r"]:
        avatar_digests = {
            str(row["specialist_key"]): str(row["digest"])
            for row in conn.execute(
                "SELECT specialist_key, md5(png) AS digest "
                "FROM rvbbit.generated_specialist_avatars"
            ).fetchall()
        }
    files_wanted: dict[str, str] = {}
    agent_parts: dict[str, tuple[str, str, float]] = {}

    desired: dict[str, dict[str, Any]] = {}
    desired["bp-hub"] = {
        "op": "add_shape", "shape": "ellipse",
        "x": HUB_X - 110, "y": HUB_Y - 54, "width": 220, "height": 108,
        "label": "Calliope",
        "style": {"stroke_color": "#c2255c", "background_color": "transparent",
                  "stroke_style": "solid", "font_size": 20, "roughness": 2},
    }

    import math
    import hashlib as _hashlib

    def _stable_angle(key: str) -> float:
        digest = int(_hashlib.md5(key.encode()).hexdigest()[:8], 16)
        return (digest % 3600) / 10.0

    schemas = sorted({str(d["schema"]) for d in all_domains})
    raw_angles = sorted(
        ((_stable_angle(schema), schema) for schema in schemas),
        key=lambda item: item[0],
    )
    # Nudge angles apart (and away from the wilds sector) so slices never
    # collide; deterministic for a given schema set. The gap shrinks with the
    # schema count so the ring can always hold them, and every nudge search is
    # bounded: the old unbounded `while` spun forever once the pilot grew past
    # six schemas (a 52° gap plus the wilds sector only fits six on 360°), and
    # that single thread held the GIL at 100% for the life of the process.
    placed: list[tuple[float, str]] = []
    for angle, schema in raw_angles:
        placed.append((_place_angle(angle, placed), schema))

    def _at(angle_deg: float, radius: float) -> tuple[float, float]:
        rad = math.radians(angle_deg)
        return HUB_X + radius * math.cos(rad), HUB_Y + radius * math.sin(rad)

    for angle, schema in placed:
        sx, sy = _at(angle, SCHEMA_RADIUS)
        desired[f"bp-src-{schema}"] = {
            "op": "add_shape", "shape": "rectangle",
            "x": sx - 130, "y": sy - 34, "width": 260, "height": 68,
            "label": schema,
            "style": {"stroke_color": "#adb5bd", "background_color": "transparent",
                      "stroke_style": "solid", "font_size": 15, "roughness": 2},
        }
        desired[f"bp-edge-hub-{schema}"] = {
            "op": "connect", "from": "bp-hub", "to": f"bp-src-{schema}",
            "style": {"stroke_color": "#495057", "stroke_style": "solid"},
        }
        members = [d for d in domains if str(d["schema"]) == schema]
        # Total sector spread stays under the 52° inter-sector gap so
        # crowded schemas never fan into their neighbours; crowded sectors
        # take a third ring instead of a wider arc.
        fan = min(22.0, 44.0 / max(len(members) - 1, 1))
        rings = 2 if len(members) <= 4 else 3
        for row, domain in enumerate(members):
            state = (
                str(domain["adoption_state"]) if domain["status"] == "promoted"
                else "proposed"
            )
            style = STATE_STYLE.get(state, STATE_STYLE["proposed"])
            dk = str(domain["domain_key"])
            spread = (row - (len(members) - 1) / 2.0) * fan
            radius = DOMAIN_RADIUS_BASE + (row % rings) * DOMAIN_RADIUS_STEP
            dx, dy = _at(angle + spread, radius)
            desired[f"bp-dom-{dk}"] = {
                "op": "add_shape", "shape": "rectangle",
                "x": dx - CARD_W / 2, "y": dy - CARD_H / 2,
                "width": CARD_W, "height": CARD_H,
                "label": (
                    f"{domain['name']}\n"
                    f"{domain['validated_metric_count']} metrics · "
                    f"{domain['assertions']} assertions · {state.replace('_', ' ')}"
                ),
                "style": {**style, "background_color": "transparent", "font_size": 13},
            }
            desired[f"bp-edge-{schema}-{dk}"] = {
                "op": "connect", "from": f"bp-src-{schema}", "to": f"bp-dom-{dk}",
                "style": {"stroke_color": "#343a40", "stroke_style": "dashed"},
            }
            agent = agents_by_domain.get(dk)
            if agent:
                busy = bool(agent.get("busy"))
                ax, ay = _at(angle + spread, radius + 128)
                key = str(agent["specialist_key"])
                accent = "#e8590c" if busy else "#5f3dc4"
                display = f"{'⚡ ' if busy else ''}{agent['display_name']}"
                digest = avatar_digests.get(key)
                if digest:
                    import calliope as _cal

                    file_id = f"bpav-{digest[:20]}"
                    files_wanted[file_id] = key
                    ring_key = f"bp-agent-{key}-ring"
                    name_width = _cal._sketch_measure_text(display, 12.0)
                    desired[ring_key] = {
                        "op": "add_shape", "shape": "ellipse",
                        "x": ax - AVATAR_RING / 2, "y": ay - AVATAR_RING / 2,
                        "width": AVATAR_RING, "height": AVATAR_RING,
                        "style": {"stroke_color": accent,
                                  "background_color": "transparent",
                                  "stroke_style": "solid", "stroke_width": 2,
                                  "roughness": 0},
                    }
                    desired[f"bp-agent-{key}"] = {
                        "op": "add_image", "file_id": file_id,
                        "x": ax - AVATAR_SIZE / 2, "y": ay - AVATAR_SIZE / 2,
                        "width": AVATAR_SIZE, "height": AVATAR_SIZE,
                    }
                    desired[f"bp-agent-{key}-name"] = {
                        "op": "add_text",
                        "x": round(ax - name_width / 2, 1),
                        "y": ay + AVATAR_RING / 2 + 8,
                        "text": display,
                        "style": {"font_size": 12, "stroke_color": accent},
                    }
                    agent_parts[ring_key] = (
                        f"bp-agent-{key}", f"bp-agent-{key}-name", name_width
                    )
                else:
                    desired[f"bp-agent-{key}"] = {
                        "op": "add_shape", "shape": "ellipse",
                        "x": ax - 62, "y": ay - 24, "width": 124, "height": 48,
                        "label": display,
                        "style": {
                            "stroke_color": accent,
                            "background_color": "transparent",
                            "stroke_style": "solid",
                            "font_size": 12,
                        },
                    }
        hidden = overflow_by_schema.get(schema, 0)
        if hidden:
            # One label past the fan's last card: the map stays honest about
            # what it isn't drawing without spending three elements per domain.
            row = len(members)
            spread = (row - max(len(members) - 1, 0) / 2.0) * fan
            radius = DOMAIN_RADIUS_BASE + (row % rings) * DOMAIN_RADIUS_STEP
            mx, my = _at(angle + spread, radius)
            desired[f"bp-more-{schema}"] = {
                "op": "add_text", "x": round(mx - 90, 1), "y": round(my - 8, 1),
                "text": f"+{hidden} more proposed in {schema}",
                "style": {"font_size": 12, "stroke_color": "#868e96"},
            }

    known_domains = {str(d["domain_key"]) for d in domains}
    notes_by_domain: dict[str, int] = {}
    wiki_homes: dict[str, tuple[str, int]] = {}
    for page in wiki:
        home = None
        for dk in known_domains:
            token = dk.replace("_", "-")
            if token in str(page["slug"]) or dk in (page.get("tags") or []):
                home = dk
                break
        slot = notes_by_domain.get(home or "_", 0)
        if home and slot < 2:
            notes_by_domain[home] = slot + 1
            anchor = desired.get(f"bp-dom-{home}")
            if anchor:
                wiki_homes[f"bp-wiki-{page['slug']}"] = (f"bp-dom-{home}", slot)
                desired[f"bp-wiki-{page['slug']}"] = {
                    "op": "add_shape", "shape": "rectangle",
                    "x": anchor["x"] + 24,
                    "y": anchor["y"] + CARD_H + 18 + slot * 54,
                    "width": 210, "height": 36,
                    "label": f"{'⚑ ' if page['flagged'] else ''}{str(page['title'])[:38]}",
                    "style": {"stroke_color": "#b08d00",
                              "background_color": "transparent", "font_size": 12},
                }

    wx, wy = _at(WILDS_ANGLE, DOMAIN_RADIUS_BASE + 60)
    wilds_lines = [f"{w['domain_key']} · {w['status']}" for w in wilds]
    desired["bp-wilds"] = {
        "op": "add_shape", "shape": "rectangle",
        "x": wx - 200, "y": wy - 40, "width": 400,
        "height": max(150, 96 + 24 * len(wilds_lines)),
        "label": "the wilds — not yet governed\n" + ("\n".join(wilds_lines) or "(nothing! the map is tame)"),
        "style": {"stroke_color": "#846358", "stroke_style": "dashed",
                  "background_color": "transparent", "roughness": 2, "font_size": 15},
    }
    desired["bp-edge-hub-wilds"] = {
        "op": "connect", "from": "bp-hub", "to": "bp-wilds",
        "style": {"stroke_color": "#5c4a42", "stroke_style": "dotted"},
    }

    _relax_collisions(desired, wiki_homes, agent_parts)

    # The header floats above whatever bounding box the constellation
    # settled into — a fixed y collides with upward-fanning sectors.
    top_y = min(
        (spec["y"] for spec in desired.values() if spec.get("op") == "add_shape"),
        default=HUB_Y - 1020,
    )
    header_y = round(top_y - 150, 1)
    desired["bp-title"] = {
        "op": "add_text", "x": HUB_X - 620, "y": header_y,
        "text": "The Briarpatch — Calliope's working map of your data universe",
        "style": {"font_size": 28},
    }
    desired["bp-legend"] = {
        "op": "add_text", "x": HUB_X - 620, "y": header_y + 46,
        "text": ("solid green = affirmed · dashed blue = adopted, provisional · "
                 "dotted grey = proposed · ⚑ = flagged by the Evidence Lab"),
        "style": {"font_size": 14, "stroke_color": "#868e96"},
    }
    if events:
        summary = " · ".join(f"{e['kind']} ×{e['n']}" for e in events)
        desired["bp-pulse"] = {
            "op": "add_text", "x": HUB_X - 620, "y": header_y + 78,
            "text": f"last 24h: {summary}",
            "style": {"font_size": 13, "stroke_color": "#868e96"},
        }
    # Managed map labels follow the UI typography, including existing maps on
    # their next reconciliation. Chat sketches retain explicitly saved fonts.
    for spec in desired.values():
        if spec.get("op") == "add_text" or spec.get("label"):
            spec.setdefault("style", {}).setdefault("font_family", 12)
    return desired, files_wanted


def _relax_pass(
    desired: dict[str, dict[str, Any]],
    keys: list[str],
    pinned: set[str],
    pad: float,
) -> None:
    for _ in range(60):
        moved = False
        for i, a_key in enumerate(keys):
            a = desired[a_key]
            for b_key in keys[i + 1:]:
                b = desired[b_key]
                if a_key in pinned and b_key in pinned:
                    continue
                overlap_x = (min(a["x"] + a["width"], b["x"] + b["width"])
                             - max(a["x"], b["x"]) + pad)
                overlap_y = (min(a["y"] + a["height"], b["y"] + b["height"])
                             - max(a["y"], b["y"]) + pad)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                axis, shift = (
                    ("x", overlap_x) if overlap_x < overlap_y else ("y", overlap_y)
                )
                size = "width" if axis == "x" else "height"
                sign = 1.0 if (a[axis] + a[size] / 2) <= (b[axis] + b[size] / 2) else -1.0
                if a_key in pinned:
                    b[axis] += sign * shift
                elif b_key in pinned:
                    a[axis] -= sign * shift
                else:
                    a[axis] -= sign * shift / 2.0
                    b[axis] += sign * shift / 2.0
                moved = True
        if not moved:
            break


def _relax_collisions(
    desired: dict[str, dict[str, Any]],
    wiki_homes: dict[str, tuple[str, int]],
    agent_parts: dict[str, tuple[str, str, float]] | None = None,
    pad: float = 16.0,
) -> None:
    """Deterministic pairwise separation for the outer constellation (domain
    cards, expert bubbles, wiki notes, the wilds). The radial math places
    cards by angle alone, so crowded sectors overlap; this nudges each
    overlapping pair apart along the axis of least penetration until every
    card has `pad` of breathing room. Hub and source-ring nodes stay pinned
    so the spoke geometry a viewer knows never shifts, and wiki notes are
    re-anchored under their (possibly nudged) domain card before a second
    pass settles them. Same inputs, same output — sticky homes survive."""
    cards = sorted(
        key for key, spec in desired.items()
        if spec.get("op") == "add_shape"
        and key.startswith(("bp-dom-", "bp-agent-", "bp-wilds"))
    )
    _relax_pass(desired, cards, pinned=set(), pad=pad)
    notes = sorted(
        key for key, spec in desired.items()
        if spec.get("op") == "add_shape" and key.startswith("bp-wiki-")
    )
    for note_key in notes:
        home_key, slot = wiki_homes.get(note_key, (None, 0))
        anchor = desired.get(home_key or "")
        if anchor:
            desired[note_key]["x"] = anchor["x"] + 24
            desired[note_key]["y"] = anchor["y"] + CARD_H + 18 + slot * 54
    _relax_pass(desired, cards + notes, pinned=set(cards), pad=pad)
    # Avatar chips and name plates follow their (possibly nudged) status
    # ring — they never relax independently, or the trio would tear apart.
    for ring_key, (image_key, name_key, name_width) in (agent_parts or {}).items():
        ring = desired.get(ring_key)
        if not ring:
            continue
        center_x = ring["x"] + ring["width"] / 2.0
        center_y = ring["y"] + ring["height"] / 2.0
        image = desired.get(image_key)
        if image:
            image["x"] = round(center_x - image["width"] / 2.0, 1)
            image["y"] = round(center_y - image["height"] / 2.0, 1)
        name = desired.get(name_key)
        if name:
            name["x"] = round(center_x - name_width / 2.0, 1)
            name["y"] = round(ring["y"] + ring["height"] + 8.0, 1)
    for key in cards + notes:
        desired[key]["x"] = round(desired[key]["x"], 1)
        desired[key]["y"] = round(desired[key]["y"], 1)


def _element_index(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in elements
        if not item.get("isDeleted")
    }


def _current_label(element: dict[str, Any], elements: list[dict[str, Any]]) -> str:
    if element.get("type") == "text":
        return str(element.get("text") or "")
    bound = next(
        (item for item in elements
         if item.get("containerId") == element.get("id") and not item.get("isDeleted")),
        None,
    )
    if bound:
        return str(bound.get("text") or "")
    label = element.get("label")
    return str(label.get("text") or "") if isinstance(label, dict) else ""


def _diff_operations(
    current: list[dict[str, Any]], desired: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    index = _element_index(current)
    operations: list[dict[str, Any]] = []
    for element_id, spec in desired.items():
        existing = index.get(element_id)
        if spec["op"] == "connect":
            has_bindings = all(
                isinstance(existing.get(key), dict)
                and existing[key].get("elementId")
                for key in ("startBinding", "endBinding")
            ) if existing is not None else False
            if existing is None or not has_bindings:
                if existing is not None:
                    # Orphaned connector (a runtime save-back dropped the
                    # pre-binding skeleton endpoints): rebuild it bound so
                    # it reroutes when its endpoints move.
                    operations.append({"op": "delete", "id": element_id})
                operations.append({
                    "op": "connect", "id": element_id,
                    "from_id": spec["from"], "to_id": spec["to"],
                    "style": spec.get("style") or {},
                })
            continue
        if existing is not None:
            # A piece that changed species (labeled ellipse -> avatar image,
            # or a swapped portrait fileId) is rebuilt, not mutated.
            want_type = {
                "add_shape": str(spec.get("shape") or "rectangle"),
                "add_text": "text",
                "add_image": "image",
            }.get(spec["op"])
            mismatch = (
                want_type is not None
                and str(existing.get("type")) != want_type
            )
            if spec["op"] == "add_image" \
                    and existing.get("fileId") != spec.get("file_id"):
                mismatch = True
            if mismatch:
                operations.append({"op": "delete", "id": element_id})
                existing = None
        if existing is None:
            op = {"id": element_id, **{k: v for k, v in spec.items() if k != "label"}}
            if spec.get("label"):
                op["label"] = spec["label"]
            if spec["op"] == "add_text":
                op["text"] = spec["text"]
                op.pop("label", None)
            operations.append(op)
            continue
        want_x, want_y = spec.get("x"), spec.get("y")
        if want_x is not None and want_y is not None:
            have_x = float(existing.get("x") or 0)
            have_y = float(existing.get("y") or 0)
            if abs(have_x - float(want_x)) > 2 or abs(have_y - float(want_y)) > 2:
                operations.append({
                    "op": "move", "id": element_id,
                    "x": float(want_x), "y": float(want_y),
                })
        want_w, want_h = spec.get("width"), spec.get("height")
        if want_w is not None and want_h is not None:
            have_w = float(existing.get("width") or 0)
            have_h = float(existing.get("height") or 0)
            # Grow-only tolerance: the label materializer may have grown a
            # shape beyond spec to fit its text — never shrink that back.
            if float(want_w) - have_w > 2 or float(want_h) - have_h > 2 \
                    or have_w - float(want_w) > 80 or have_h - float(want_h) > 80:
                operations.append({
                    "op": "resize", "id": element_id,
                    "width": float(want_w), "height": float(want_h),
                })
        want_text = spec.get("text") or spec.get("label") or ""
        if want_text and _current_label(existing, current).strip() != str(want_text).strip():
            operations.append({"op": "set_text", "id": element_id, "text": want_text})
        want_style = spec.get("style") or {}
        stroke = want_style.get("stroke_color")
        stroke_style = want_style.get("stroke_style")
        want_font = want_style.get("font_size")
        want_family = want_style.get("font_family")
        label = existing if existing.get("type") == "text" else next(
            (index[ref["id"]] for ref in (existing.get("boundElements") or [])
             if ref.get("type") == "text" and ref.get("id") in index),
            existing.get("label") or {},
        )
        family_off = want_family is not None and label.get("fontFamily") != want_family
        # Free text is human-scalable in the editor; managed text re-asserts
        # its doctrine size so a stray drag can't leave the title at 66px.
        font_off = (
            want_font is not None
            and existing.get("type") == "text"
            and abs(float(existing.get("fontSize") or 0) - float(want_font)) > 1
        )
        if (stroke and existing.get("strokeColor") != stroke) or (
            stroke_style and existing.get("strokeStyle") != stroke_style
        ) or font_off or family_off:
            keep = {"stroke_color", "stroke_style", "background_color"}
            if font_off:
                keep.add("font_size")
            if family_off:
                keep.add("font_family")
            operations.append({
                "op": "style", "id": element_id,
                "style": {k: v for k, v in want_style.items() if k in keep},
            })
    for element_id in index:
        if element_id.startswith("bp-") and element_id not in desired \
                and not element_id.endswith("--label"):
            operations.append({"op": "delete", "id": element_id})
    return operations


def _ensure_sketch(conn: Any) -> dict[str, Any] | None:
    owner = os.environ.get("BRIARPATCH_OWNER_EMAIL", "").strip().lower()
    if not owner:
        row = conn.execute(
            "SELECT lower(owner_email) AS owner, count(*) FROM rvbbit.calliope_sessions "
            "WHERE owner_email NOT LIKE 'calliope@%' "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
        ).fetchone()
        owner = row["owner"] if row else ""
    if not owner:
        return None
    session = conn.execute(
        "SELECT id FROM rvbbit.calliope_sessions "
        "WHERE lower(owner_email)=%s AND title=%s LIMIT 1",
        (owner, TITLE),
    ).fetchone()
    if not session:
        session_id = str(uuid_module.uuid4())
        conn.execute(
            "INSERT INTO rvbbit.calliope_sessions "
            "(id, owner_email, hermes_session_id, title, title_source) "
            "VALUES (%s::uuid, %s, %s, %s, 'manual')",
            (
                session_id, owner,
                f"briarpatch_{int(time.time())}_{uuid_module.uuid4().hex[:10]}",
                TITLE,
            ),
        )
    else:
        session_id = str(session["id"])
    sketch = conn.execute(
        "SELECT * FROM rvbbit.calliope_sketches WHERE session_id=%s::uuid",
        (session_id,),
    ).fetchone()
    if not sketch:
        sketch = conn.execute(
            "INSERT INTO rvbbit.calliope_sketches "
            "(id,session_id,owner_email,title,revision,elements,app_state,last_actor,"
            "last_actor_email,last_operation_count,last_change_summary) VALUES "
            "(%s::uuid,%s::uuid,%s,%s,1,'[]'::jsonb,'{}'::jsonb,'calliope',%s,0,'{}'::jsonb) "
            "RETURNING *",
            (str(uuid_module.uuid4()), session_id, owner, TITLE, owner),
        ).fetchone()
    return dict(sketch)


def tick(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    import calliope

    with conn_factory() as conn:
        ready = conn.execute(
            "SELECT to_regclass('rvbbit.calliope_sketches') IS NOT NULL "
            "AND to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL AS r"
        ).fetchone()["r"]
        if not ready:
            return {"status": "not_ready"}
        sketch = _ensure_sketch(conn)
        if not sketch:
            return {"status": "no_owner"}
        desired, files_wanted = _desired_model(conn)
        files = _reconcile_files(conn, sketch, desired, files_wanted)
        files_changed = files is not None
        operations = _diff_operations(sketch.get("elements") or [], desired)
        if not operations and not files_changed:
            return {"status": "unchanged", "sketch_id": str(sketch["id"])}
        elements = sketch.get("elements") or []
        revision = int(sketch.get("revision") or 1)
        applied = 0
        for start in range(0, len(operations), MAX_OPS_PER_APPLY):
            chunk = operations[start:start + MAX_OPS_PER_APPLY]
            revision += 1
            elements, summary = calliope._apply_sketch_operations(
                elements, chunk, revision
            )
            applied += len(chunk)
        if not operations:
            revision += 1
        conn.execute(
            "UPDATE rvbbit.calliope_sketches SET revision=%s,elements=%s::jsonb,"
            + ("files=%s::jsonb," if files_changed else "")
            + "preview_path=NULL,preview_revision=NULL,last_actor='calliope',"
            "last_actor_email='briarpatch@system',last_operation_count=%s,"
            "last_change_summary=%s::jsonb,updated_at=now() WHERE id=%s::uuid",
            (
                revision, json.dumps(elements, ensure_ascii=False),
                *([json.dumps(files, ensure_ascii=False)] if files_changed else []),
                applied,
                json.dumps({"briarpatch": True, "operations": applied}),
                str(sketch["id"]),
            ),
        )
    return {
        "status": "updated", "sketch_id": str(sketch["id"]),
        "operations": applied, "revision": revision,
        "files_changed": files_changed,
    }


def _reconcile_files(
    conn: Any,
    sketch: dict[str, Any],
    desired: dict[str, dict[str, Any]],
    files_wanted: dict[str, str],
) -> dict[str, Any] | None:
    """Bring the sketch's server-managed file store in line with the wanted
    avatar thumbnails. Returns the new files map when it differs from the
    stored one, else None. Avatar element specs whose thumbnail could not be
    produced are dropped from `desired` so the scene never references a
    missing file."""
    stored = sketch.get("files") if isinstance(sketch.get("files"), dict) else {}
    files: dict[str, Any] = {
        key: value for key, value in stored.items()
        if not str(key).startswith("bpav-")
    }
    for file_id in files_wanted:
        if file_id in stored:
            files[file_id] = stored[file_id]
    for file_id, specialist_key in files_wanted.items():
        if file_id in files:
            continue
        row = conn.execute(
            "SELECT png FROM rvbbit.generated_specialist_avatars "
            "WHERE specialist_key=%s",
            (specialist_key,),
        ).fetchone()
        thumb = _avatar_thumbnail(bytes(row["png"])) if row else None
        if not thumb:
            continue
        data_url, mime = thumb
        files[file_id] = {
            "id": file_id,
            "mimeType": mime,
            "dataURL": data_url,
            "created": int(time.time() * 1_000),
        }
    total = sum(len(str(item.get("dataURL") or "")) for item in files.values())
    if total > FILES_BYTE_BUDGET:
        for file_id in sorted(files_wanted):
            if total <= FILES_BYTE_BUDGET:
                break
            item = files.pop(file_id, None)
            if item:
                total -= len(str(item.get("dataURL") or ""))
    for file_id, specialist_key in files_wanted.items():
        if file_id not in files:
            # No thumbnail (unreadable/oversized portrait): retire the
            # avatar trio rather than render a broken image — the agent
            # rejoins the map when its portrait regenerates.
            for key in (
                f"bp-agent-{specialist_key}",
                f"bp-agent-{specialist_key}-ring",
                f"bp-agent-{specialist_key}-name",
            ):
                desired.pop(key, None)
    if json.dumps(files, sort_keys=True) == json.dumps(stored, sort_keys=True):
        return None
    return files


_WORKER_STARTED = False


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return False

    def _loop() -> None:
        time.sleep(90)
        announced = False
        while True:
            try:
                result = tick(conn_factory)
                if not announced and result.get("sketch_id"):
                    print(
                        "briarpatch live at /calliope/sketches/"
                        f"{result['sketch_id']}",
                        file=sys.stderr,
                    )
                    announced = True
                if result.get("status") == "updated":
                    print(f"briarpatch: {json.dumps(result)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"briarpatch tick failed: {exc}", file=sys.stderr)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=_loop, name="briarpatch", daemon=True).start()
    _WORKER_STARTED = True
    return True
