"""Expert portraits: a face for every factory-minted specialist.

Faces are how humans cache identities — the pilot showed personified
agents make people comfortable faster, and the Greek names never collide
with real employees. This module generates headshots in the house style
(the hand-made team's Mucha-esque Art Nouveau) through any
OpenAI-images-shaped endpoint and stores them in the database so they
replicate with everything else.

Config: rvbbit.settings key 'expert_portraits' —
  {"model": "...", "base_url": null|"https://...", "api_key_env": null|"ENV_NAME"}
base_url defaults to the Clover proxy (RVBBIT_CLOVER_OPENAI_BASE_URL) and
the key to RVBBIT_CLOVER_KEY; secrets always live in environment
variables, settings hold only references. With no model configured the
lane is silently off: agents mint portrait-less and backfill the moment
an operator configures it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from typing import Any, Callable

GENERATION_TIMEOUT_SECONDS = int(os.environ.get("EXPERT_PORTRAIT_TIMEOUT", "180"))
# The Hermes lane carries a whole agent turn around the image call (tool
# dispatch, file copy) on top of a 10-20s quality model — 180s times out.
HERMES_LANE_TIMEOUT_SECONDS = int(os.environ.get("EXPERT_PORTRAIT_HERMES_TIMEOUT", "480"))

# The house style, described from the hand-made avatars (iris.png et al).
_STYLE = (
    "Art Nouveau portrait in the style of an ornate Alphonse Mucha panel, "
    "with a modern American comics/anime inflection in the figure itself: "
    "elegant figure in profile or three-quarter view with clean, confident "
    "inked linework and expressive stylized features, framed by a circular "
    "stained-glass halo with geometric segments, winged jewel ornaments, "
    "hanging crystal pendants, and a decorative border. Luminous "
    "jewel-toned palette, serene expression, square composition. "
    "No text, no watermark."
)

_PALETTES = [
    "deep violet and gold", "emerald and copper", "sapphire and silver",
    "crimson and bronze", "amber and teal", "rose and pewter",
    "indigo and brass", "sea-green and gold",
]


def _config(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT value FROM rvbbit.settings WHERE key='expert_portraits'"
    ).fetchone()
    value = (row or {}).get("value") if row else None
    if not isinstance(value, dict):
        value = {"model": "calliope-image"} if os.environ.get("RVBBIT_CLOVER_KEY") else {}
    if not value.get("model"):
        return None
    model = str(value["model"])
    base_raw = str(value.get("base_url") or "").strip().rstrip("/")
    # The Hermes lane: endpoint 'hermes' (or a grok-imagine-* model with no
    # endpoint) generates through the Hermes agent's own image tool, riding
    # whatever credential Hermes holds (e.g. the SuperGrok OAuth) — no API
    # key ever touches Calliope.
    if base_raw.lower() == "hermes" or (
        not base_raw and model.lower().startswith("grok-imagine")
    ):
        url = (os.environ.get("WAREHOUSE_HERMES_URL") or "").rstrip("/")
        key = os.environ.get("WAREHOUSE_HERMES_API_KEY") or ""
        if not url or not key:
            return None
        return {"lane": "hermes", "model": model, "base_url": url, "api_key": key}
    base_url = base_raw or os.environ.get(
        "RVBBIT_CLOVER_OPENAI_BASE_URL", ""
    ).rstrip("/")
    api_key_env = str(value.get("api_key_env") or "") or "RVBBIT_CLOVER_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not base_url or not api_key:
        return None
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _portrait_prompt(display_name: str, role_label: str, description: str) -> str:
    palette = _PALETTES[
        int(hashlib.md5(display_name.encode()).hexdigest()[:8], 16) % len(_PALETTES)
    ]
    return (
        f"{_STYLE} Palette: {palette}. The figure is {display_name}, "
        f"a mythological patron of {role_label.lower()} — subtly evoke "
        f"their domain ({description.rstrip('.')}) through the halo's "
        "iconography and ornaments, never through literal charts or text."
    )


def _generate_png(config: dict[str, Any], prompt: str,
                  size: str = "1024x1024") -> bytes | None:
    """One image from the configured OpenAI-images-shaped endpoint."""
    if config.get("lane") == "hermes":
        return _generate_png_hermes(config, prompt)
    if "openrouter.ai" in config["base_url"]:
        return _generate_png_openrouter(config, prompt)
    payload: dict[str, Any] = {
        "model": config["model"], "prompt": prompt, "n": 1, "size": size,
    }
    # gpt-image-* always returns b64 and rejects the parameter outright.
    if not str(config["model"]).lower().startswith("gpt-image"):
        payload["response_format"] = "b64_json"
    request = urllib.request.Request(
        config["base_url"] + "/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
    )
    import media_ledger
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=GENERATION_TIMEOUT_SECONDS) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Image response exceeds limit")
            payload = json.loads(raw)
            media_ledger.record(operation="image.generate", model=config["model"], base_url=config["base_url"],
                headers=dict(response.headers), usage=payload.get("usage"), input_bytes=len(request.data), output_bytes=len(raw),
                latency_ms=(time.monotonic()-started)*1000, session_id=config.get("session_id"))
    except Exception as exc:
        media_ledger.record(operation="image.generate", model=config["model"], base_url=config["base_url"],
            latency_ms=(time.monotonic()-started)*1000, session_id=config.get("session_id"), status="error", error_type=type(exc).__name__)
        raise
    items = payload.get("data") or []
    encoded = (items[0] or {}).get("b64_json") if items else None
    if encoded:
        return base64.b64decode(encoded)
    url = (items[0] or {}).get("url") if items else None
    if not url:
        return None
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _hermes_api(config: dict[str, Any], method: str, path: str,
                body: dict[str, Any] | None = None,
                timeout: int | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        config["base_url"] + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(
        request, timeout=timeout or GENERATION_TIMEOUT_SECONDS
    ) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _generate_png_hermes(config: dict[str, Any], prompt: str) -> bytes | None:
    """Generate through an ephemeral Hermes session's image tool. HERMES_HOME
    is the rvbbit_hermes volume, mounted read-only into this container at the
    same path — the agent saves the file, we read the bytes."""
    import time
    import uuid

    token = uuid.uuid4().hex
    target = f"/opt/data/calliope-generated/{token}.png"
    session_id = f"calliope_img_{token[:12]}"
    started_at = time.time()
    _hermes_api(config, "POST", "/api/sessions",
                {"id": session_id, "source": "api_server"}, timeout=30)
    try:
        result = _hermes_api(
            config, "POST", f"/api/sessions/{session_id}/chat",
            {
                "message": (
                    "Call the image_generate tool ONCE with exactly this "
                    f"prompt (verbatim, no additions): {prompt}\n\n"
                    f"Pass model {config['model']} if the tool accepts a "
                    "model argument. Then copy the file it produced to "
                    f"exactly {target} (create the directory if needed) and "
                    "reply with only that path.\n\n"
                    "If image_generate is not in your tools list, reply "
                    "exactly FAILED:NO_IMAGE_TOOL. Never substitute an "
                    "existing image, and never improvise generation through "
                    "the browser, terminal, or code execution — a wrong "
                    "image is worse than no image."
                ),
            },
            timeout=HERMES_LANE_TIMEOUT_SECONDS,
        )
    finally:
        try:
            _hermes_api(config, "DELETE", f"/api/sessions/{session_id}", timeout=10)
        except Exception:  # noqa: BLE001
            pass

    content = (result.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if "FAILED:NO_IMAGE_TOOL" in str(content or ""):
        raise RuntimeError(
            "Hermes reports no image_generate tool — select an image backend "
            "(config image_gen.provider) on the appliance"
        )
    candidates = [target] + re.findall(
        r"(/opt/data/\S+\.(?:png|jpg|jpeg|webp))", str(content or "")
    )
    for _ in range(5):   # the volume is shared but give the copy a beat
        for path in candidates:
            try:
                # A file older than this request is some other session's
                # image (2026-09-02: an agent once satisfied the copy step
                # with a Bigfoot corkboard from the operator's test thread).
                if os.path.getmtime(path) < started_at - 60:
                    continue
                with open(path, "rb") as handle:
                    data = handle.read(9_000_000)
                if data:
                    return data
            except OSError:
                continue
        time.sleep(1)
    return None


def _generate_png_openrouter(config: dict[str, Any], prompt: str) -> bytes | None:
    """OpenRouter serves no /images/generations — its image models answer
    chat completions with modalities=['image','text'] and return the image
    as a data URI on the assistant message. Same key, different shape."""
    request = urllib.request.Request(
        config["base_url"] + "/chat/completions",
        data=json.dumps({
            "model": config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
    )
    import media_ledger
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=GENERATION_TIMEOUT_SECONDS) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Image response exceeds limit")
            payload = json.loads(raw)
            media_ledger.record(operation="image.generate", model=config["model"], base_url=config["base_url"],
                headers=dict(response.headers), usage=payload.get("usage"), input_bytes=len(request.data), output_bytes=len(raw),
                latency_ms=(time.monotonic()-started)*1000, session_id=config.get("session_id"))
    except Exception as exc:
        media_ledger.record(operation="image.generate", model=config["model"], base_url=config["base_url"],
            latency_ms=(time.monotonic()-started)*1000, session_id=config.get("session_id"), status="error", error_type=type(exc).__name__)
        raise
    choices = payload.get("choices") or []
    images = (((choices[0] or {}).get("message") or {}).get("images") or []) if choices else []
    url = ((images[0] or {}).get("image_url") or {}).get("url") if images else None
    if not url:
        return None
    if url.startswith("data:"):
        encoded = url.split(",", 1)[1] if "," in url else ""
        return base64.b64decode(encoded) if encoded else None
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


_OPENROUTER_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_openrouter_image_cache: dict[str, Any] = {"at": 0.0, "models": []}


def openrouter_image_models() -> dict[str, Any]:
    """Image-output models from OpenRouter's public catalog (no key needed).

    The model picker's synced inventory is Hermes's curated, chat-centric
    subset — only a handful of image models ride along, so operators had to
    paste ids by hand. The live catalog, filtered to models whose
    architecture lists image output, is the browsable list. Cached six hours
    in-process; on fetch failure the stale cache (possibly empty) is served
    with the error noted."""
    import time

    now = time.time()
    if _openrouter_image_cache["models"] and now - _openrouter_image_cache["at"] < 21_600:
        return {"models": _openrouter_image_cache["models"], "cached": True}
    request = urllib.request.Request(
        _OPENROUTER_CATALOG_URL, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a browse aid, never a blocker
        return {
            "models": _openrouter_image_cache["models"],
            "error": str(exc)[:200],
        }
    models = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        outputs = arch.get("output_modalities") or []
        if not isinstance(outputs, list) or "image" not in outputs:
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        models.append({
            "model": model_id,
            "display_name": str(item.get("name") or model_id),
            "family": "image",
        })
    models.sort(key=lambda entry: entry["model"])
    _openrouter_image_cache.update({"at": now, "models": models})
    return {"models": models}


def image_media_type(data: bytes) -> str:
    """Chat-shaped providers may hand back JPEG or WebP; sniff before serving
    (the routes set nosniff, so a wrong declared type would refuse to render)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def generate_image(conn_factory: Callable[..., Any], owner_email: str,
                   prompt: str, size: str = "1024x1024", *,
                   stage_destination: dict[str, str] | None = None,
                   title: str = "", purpose: str = "", creator_session: str = "") -> dict[str, Any]:
    """Agent-facing generation: any prompt, stored under the caller, served
    from /api/calliope/generated-images/<id>."""
    prompt = str(prompt or "").strip()[:4000]
    if not prompt:
        return {"error": {"code": "EMPTY_PROMPT", "message": "prompt is required"}}
    if size not in {"1024x1024", "512x512", "1792x1024", "1024x1792"}:
        size = "1024x1024"
    with conn_factory() as conn:
        config = _config(conn)
    if not config:
        return {"error": {
            "code": "IMAGE_PROVIDER_UNCONFIGURED",
            "message": "No image model is configured. An operator sets one "
                       "under Models & agents → Image generation.",
        }}
    config["session_id"] = creator_session or (stage_destination or {}).get("session_id")
    png = _generate_png(config, prompt, size)
    if not png:
        return {"error": {"code": "NO_IMAGE", "message": "the provider returned no image"}}
    if len(png) > 8_000_000:
        return {"error": {"code": "IMAGE_TOO_LARGE", "message": "over the 8MB cap"}}
    import hashlib
    import image_metadata
    metadata = image_metadata.generated(prompt, title=title, purpose=purpose,
                                        model=config["model"], creator_session=creator_session)
    digest = hashlib.sha256(png).hexdigest()
    with conn_factory() as conn:
        row = conn.execute(
            "INSERT INTO rvbbit.generated_images (owner_email, prompt, model, png) "
            "VALUES (%s, %s, %s, %s) RETURNING id::text",
            (owner_email, prompt, config["model"], png),
        ).fetchone()
        if stage_destination:
            _publish_generated_image(conn, stage_destination, row["id"], config["model"],
                                     metadata=metadata, content_hash=digest)
    image_id = row["id"]
    return {
        "image_id": image_id,
        "url": f"/api/calliope/generated-images/{image_id}",
        "model": config["model"],
        "bytes": len(png),
        "title": metadata["title"],
        "image_metadata": metadata,
        "content_sha256": digest,
        "note": "Reference the url in dashboards, documents, or artifacts; "
                "it serves the stored PNG to signed-in viewers.",
    }


def _publish_generated_image(conn: Any, destination: dict[str, str],
                             image_id: str, model: str, *, metadata=None, content_hash=None) -> None:
    """Commit the image and its Stage receipt together, before replying to MCP.

    The sync tool worker can outlive a disconnected caller. Publication must
    not depend on Hermes acknowledging the response or a later transcript scan.
    """
    import contextlib
    import calliope

    session_id, turn_id = destination["session_id"], destination["turn_id"]
    # A user may have deleted the chat during generation. Keep the image, but
    # never attach it to a different/latest turn as a fallback.
    if not conn.execute(
        "SELECT id FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid FOR KEY SHARE",
        (turn_id, session_id),
    ).fetchone():
        return
    calliope._insert_surfaces(lambda: contextlib.nullcontext(conn), session_id, turn_id, [{
        "kind": "image", "title": (metadata or {}).get("title") or "Generated image",
        "tool_name": "generate_image", "tool_call_id": f"generated-image:{image_id}",
        "lineage_key": f"generated-image:{image_id}",
        "payload": {"image_id": image_id, "image_url": f"/api/calliope/generated-images/{image_id}",
                    "mime_type": "image/png", "model": model,
                    "image_metadata": metadata or {}, "content_sha256": content_hash},
        "source": {"origin": "generated_image"},
        "presentation": {"purpose": "generated_image"},
    }])


def generate(conn_factory: Callable[..., Any], specialist_key: str) -> dict[str, Any]:
    with conn_factory() as conn:
        config = _config(conn)
        if not config:
            return {"status": "unconfigured"}
        spec = conn.execute(
            "SELECT display_name, role_label, description "
            "FROM rvbbit.generated_specialists "
            "WHERE specialist_key=%s AND status='active'",
            (specialist_key,),
        ).fetchone()
        if not spec:
            return {"status": "missing", "specialist_key": specialist_key}

    prompt = _portrait_prompt(
        str(spec["display_name"]), str(spec["role_label"]),
        str(spec["description"]),
    )
    png = _generate_png(config, prompt)
    if not png:
        return {"status": "no_image", "specialist_key": specialist_key}
    if len(png) > 8_000_000:
        return {"status": "too_large", "specialist_key": specialist_key}

    with conn_factory() as conn:
        conn.execute(
            "INSERT INTO rvbbit.generated_specialist_avatars "
            "(specialist_key, png, model, prompt) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (specialist_key) DO UPDATE SET "
            "png=EXCLUDED.png, model=EXCLUDED.model, prompt=EXCLUDED.prompt, "
            "created_at=clock_timestamp()",
            (specialist_key, png, config["model"], prompt),
        )
    return {
        "status": "generated", "specialist_key": specialist_key,
        "bytes": len(png), "model": config["model"],
    }


def backfill_tick(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """Give one portrait-less active generated specialist a face per tick —
    cost-bounded, and the whole bench backfills within hours of an
    operator configuring the lane."""
    with conn_factory() as conn:
        if not _config(conn):
            return {"status": "unconfigured"}
        ready = conn.execute(
            "SELECT to_regclass('rvbbit.generated_specialist_avatars') IS NOT NULL AS r"
        ).fetchone()["r"]
        if not ready:
            return {"status": "not_ready"}
        row = conn.execute(
            "SELECT g.specialist_key FROM rvbbit.generated_specialists g "
            "LEFT JOIN rvbbit.generated_specialist_avatars a USING (specialist_key) "
            "WHERE g.status='active' AND a.specialist_key IS NULL "
            "ORDER BY g.created_at LIMIT 1"
        ).fetchone()
    if not row:
        _record_status(conn_factory, None)   # backfill done — clear any stale failure
        return {"status": "complete"}
    try:
        result = generate(conn_factory, str(row["specialist_key"]))
    except Exception as exc:  # noqa: BLE001 — a failed portrait never blocks anything
        result = {
            "status": "failed", "specialist_key": str(row["specialist_key"]),
            "error": str(exc)[:300],
        }
    if result.get("status") in {"generated", "failed"}:
        print(f"expert-portraits: {json.dumps(result)[:300]}", file=sys.stderr)
    _record_status(conn_factory, result)
    return result


def _record_status(conn_factory: Callable[..., Any],
                   result: dict[str, Any] | None) -> None:
    """Surface the latest backfill outcome in the config card — a lane that
    404s every tick must not read as quietly 'active'."""
    try:
        with conn_factory() as conn:
            if result is None:
                conn.execute(
                    "DELETE FROM rvbbit.settings WHERE key='expert_portraits_status'"
                )
                return
            conn.execute(
                "INSERT INTO rvbbit.settings (key, value, updated_at) "
                "VALUES ('expert_portraits_status', jsonb_build_object("
                "'status', %s::text, 'specialist_key', %s::text, 'error', %s::text, "
                "'at', to_char(clock_timestamp() AT TIME ZONE 'utc', "
                "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')), now()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                (str(result.get("status") or ""),
                 result.get("specialist_key"), result.get("error")),
            )
    except Exception:  # noqa: BLE001 — status is advisory, never blocks the tick
        pass
