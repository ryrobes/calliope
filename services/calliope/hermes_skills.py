"""Project durable Hermes skills into RVBBIT's capability Library.

Skill files remain canonical in Hermes's durable volume.  Warehouse reads only
their compact frontmatter and publishes ``cap_skill`` stubs; the agent loads a
full skill explicitly with ``skill_view`` only after capability discovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


SKILLS_ROOT = Path(os.environ.get("WAREHOUSE_HERMES_SKILLS_ROOT", "/opt/data/skills"))
_SCAN_INTERVAL_SECONDS = 5.0
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$")
_SPACE_RE = re.compile(r"\s+")
_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, str, list[dict[str, object]]] | None = None


@dataclass(frozen=True)
class SkillStub:
    name: str
    load_name: str
    category: str
    description: str
    source_path: str
    content_hash: str


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return _SPACE_RE.sub(" ", value).strip()


def _frontmatter(text: str) -> dict[str, str]:
    """Parse the small scalar subset used by Hermes SKILL.md frontmatter."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}

    out: dict[str, str] = {}
    i = 1
    while i < end:
        match = _FRONTMATTER_KEY_RE.match(lines[i])
        if not match:
            i += 1
            continue
        key, raw = match.group(1), (match.group(2) or "")
        if raw.strip() in {"|", "|-", "|+", ">", ">-", ">+"}:
            block: list[str] = []
            i += 1
            while i < end and (not lines[i].strip() or lines[i][:1].isspace()):
                block.append(lines[i].strip())
                i += 1
            out[key] = _clean_scalar(" ".join(block))
            continue
        out[key] = _clean_scalar(raw)
        i += 1
    return out


def _fallback_description(text: str) -> str:
    body = text
    if text.lstrip("\ufeff").startswith("---"):
        parts = text.lstrip("\ufeff").split("---", 2)
        if len(parts) == 3:
            body = parts[2]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                break
            continue
        current.append(stripped)
    if current and not paragraphs:
        paragraphs.append(" ".join(current))
    return _clean_scalar(paragraphs[0] if paragraphs else "Hermes procedural skill.")


def _raw_stubs(root: Path) -> list[SkillStub]:
    if not root.is_dir():
        return []
    candidates: list[tuple[str, str, str, str, str]] = []
    for path in sorted(root.rglob("SKILL.md")):
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            metadata = _frontmatter(text)
            rel_dir = path.parent.relative_to(root)
        except (OSError, ValueError):
            continue
        name = metadata.get("name") or path.parent.name
        if not _NAME_RE.fullmatch(name):
            continue
        category = "/".join(rel_dir.parts[:-1]) or "general"
        description = metadata.get("description") or _fallback_description(text)
        description = _clean_scalar(description)[:2000]
        source_path = str(path.relative_to(root))
        candidates.append(
            (name, category, description, source_path, hashlib.sha256(raw).hexdigest())
        )

    counts: dict[str, int] = {}
    for name, *_ in candidates:
        counts[name] = counts.get(name, 0) + 1

    stubs: list[SkillStub] = []
    for name, category, description, source_path, content_hash in candidates:
        # Bare names are the ergonomic normal path.  A categorized path is the
        # exact, unambiguous form understood by skill_view when names collide.
        load_name = name
        if counts[name] > 1:
            load_name = source_path.removesuffix("/SKILL.md")
        stubs.append(
            SkillStub(
                name=name,
                load_name=load_name,
                category=category,
                description=description,
                source_path=source_path,
                content_hash=content_hash,
            )
        )
    return stubs


def skill_snapshot(root: Path = SKILLS_ROOT) -> tuple[str, list[dict[str, object]]]:
    if not root.is_dir():
        raise FileNotFoundError(f"Hermes skills root is unavailable: {root}")
    stubs = [asdict(stub) for stub in _raw_stubs(root)]
    encoded = json.dumps(stubs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), stubs


def _cached_snapshot(root: Path) -> tuple[str, list[dict[str, object]]]:
    global _CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE is not None and _CACHE[0] > now:
            return _CACHE[1], _CACHE[2]
        manifest, stubs = skill_snapshot(root)
        _CACHE = (now + _SCAN_INTERVAL_SECONDS, manifest, stubs)
        return manifest, stubs


def sync_skill_capabilities(connection, root: Path = SKILLS_ROOT) -> dict[str, object]:
    """Best-effort reconcile into Postgres; callers decide how to handle absence."""
    manifest, stubs = _cached_snapshot(root)
    row = connection.execute(
        "SELECT rvbbit.sync_hermes_skill_capabilities(%s,%s::jsonb,true) AS result",
        (manifest, json.dumps(stubs)),
    ).fetchone()
    result = (row or {}).get("result") if isinstance(row, dict) else None
    return result if isinstance(result, dict) else {
        "manifest": manifest,
        "skills": len(stubs),
    }
