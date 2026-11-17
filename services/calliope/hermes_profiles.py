"""Narrow admin bridge for Hermes profiles, skills, YAML, and SOUL documents."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import avatar_colors
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import yaml

import hermes_config


_CONTROL_URL = os.environ.get(
    "WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643"
).rstrip("/")
_CONTROL_KEY = os.environ.get("HERMES_CONTROL_KEY") or os.environ.get(
    "WAREHOUSE_HERMES_API_KEY", ""
)


def _control(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not _CONTROL_KEY:
        raise RuntimeError("WAREHOUSE_HERMES_API_KEY is not configured")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _CONTROL_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {_CONTROL_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
        except Exception:
            result = {"error": f"Hermes control returned HTTP {exc.code}"}
        result.setdefault("http_status", exc.code)
    if not isinstance(result, dict):
        raise RuntimeError("Hermes control returned a non-object response")
    return result


def clean_voice_id(value: Any) -> str:
    """Provider identifiers are opaque; reject paths, whitespace and unbounded input."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) else ""


def clean_voice_preview_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 800:
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()


def clean_speech_persona(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 600:
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()


def agent_voice(profile_key: str | None) -> str:
    identities = {item["key"]: item for item in profiles().get("profiles", []) if isinstance(item, dict) and item.get("key")}
    return clean_voice_id(identities.get(profile_key or "default", {}).get("voice_id")) or clean_voice_id(identities.get("default", {}).get("voice_id"))


@lru_cache(maxsize=256)
def _portrait_fields(profile_key: str, profile_hash: str) -> dict[str, str]:
    query = urllib.parse.urlencode({"profile": profile_key, "document": "profile"})
    result = _control("GET", f"/v1/profile/document?{query}", timeout=10)
    if "content" not in result:
        raise RuntimeError("Profile identity unavailable")
    identity = yaml.safe_load(result["content"]) or {}
    if not isinstance(identity, dict):
        return {}
    voice = clean_voice_id(identity.get("voice_id"))
    return {**avatar_colors.fields(identity), "voice_id": voice or None, "voice_preview_text": clean_voice_preview_text(identity.get("voice_preview_text")) or None, "speech_persona": clean_speech_persona(identity.get("speech_persona")) or None}


def profiles() -> dict[str, Any]:
    result = _control("GET", "/v1/profiles", timeout=10)
    def enrich(item):
        if not isinstance(item, dict) or not item.get("key") or not item.get("profile_hash"):
            return item
        try:
            return {**item, **_portrait_fields(item["key"], item["profile_hash"])}
        except Exception:
            return item  # Missing identity metadata must not hide the roster.
    if result.get("profiles"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = {**result, "profiles": list(pool.map(enrich, result["profiles"]))}
    return result


def profile(profile_key: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"profile": profile_key})
    return _control("GET", f"/v1/profile?{query}", timeout=10)


def skill(profile_key: str, skill_name: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"profile": profile_key, "skill": skill_name})
    return _control("GET", f"/v1/profile/skill?{query}", timeout=10)


def document(
    conn_factory: Callable[..., Any], profile_key: str, document_kind: str
) -> dict[str, Any]:
    if profile_key == "default" and document_kind == "config":
        current = hermes_config.current(conn_factory)
        return {
            "profile": "default",
            "document": "config",
            "content": current["config_yaml"],
            "content_hash": current["config_hash"],
            "revision_id": current["revision_id"],
            "secret_bindings": current.get("secret_bindings") or {},
            "user_managed": True,
        }
    query = urllib.parse.urlencode(
        {"profile": profile_key, "document": document_kind}
    )
    return _control("GET", f"/v1/profile/document?{query}", timeout=10)


def validate_document(
    profile_key: str, document_kind: str, content: str
) -> dict[str, Any]:
    return _control(
        "POST",
        "/v1/profile/validate",
        {"profile": profile_key, "document": document_kind, "content": content},
        timeout=20,
    )


def _reconcile_default_config(config_yaml: str) -> tuple[str, dict[str, Any]]:
    result = _control(
        "POST",
        "/v1/reconcile",
        {"config_yaml": config_yaml},
        timeout=20,
    )
    reconciled = str(result.get("config_yaml") or "")
    if not result.get("valid") or not reconciled:
        return config_yaml, result
    return reconciled, result


def save_document(
    conn_factory: Callable[..., Any],
    profile_key: str,
    document_kind: str,
    content: str,
    expected_hash: str | None,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    if profile_key == "default" and document_kind == "config":
        current = hermes_config.current(conn_factory)
        if expected_hash and expected_hash != current["config_hash"]:
            return {
                "ok": False,
                "phase": "compare_and_swap",
                "error": "document changed after it was opened",
                "active_content_hash": current["config_hash"],
            }
        result = hermes_config.queue_apply(
            conn_factory,
            content,
            reason=reason or "edited Calliope agent YAML in DataRabbit",
            actor=actor,
            secret_bindings=current.get("secret_bindings") or {},
        )
        return {**result, "ok": bool(result.get("queued")), "restart_required": False}
    return _control(
        "POST",
        "/v1/profile/document",
        {
            "profile": profile_key,
            "document": document_kind,
            "content": content,
            "expected_hash": expected_hash,
        },
        timeout=30,
    )


def toggle_skill(
    conn_factory: Callable[..., Any],
    profile_key: str,
    skill_name: str,
    enabled: bool,
    *,
    actor: str,
) -> dict[str, Any]:
    if profile_key != "default":
        # The specialist reconciler can legitimately replace a generated YAML
        # file between the supervisor's read and atomic write.  That is a tiny
        # compare-and-swap window, not a user conflict, so refresh and retry it
        # once instead of leaking an opaque HTTP 409 into the admin surface.
        result: dict[str, Any] = {}
        for _attempt in range(2):
            result = _control(
                "POST",
                "/v1/profile/skill",
                {"profile": profile_key, "skill": skill_name, "enabled": enabled},
                timeout=20,
            )
            if result.get("ok") or result.get("phase") != "compare_and_swap":
                break
        return result

    current = hermes_config.current(conn_factory)
    payload = yaml.safe_load(current["config_yaml"]) or {}
    if not isinstance(payload, dict):
        raise ValueError("active Hermes config is not a YAML object")
    details = profile("default")
    installed = {str(item.get("name")) for item in details.get("skills") or []}
    if skill_name not in installed:
        raise ValueError(f"skill is not installed for default: {skill_name}")
    skills = payload.get("skills") if isinstance(payload.get("skills"), dict) else {}
    disabled = [str(value) for value in (skills.get("disabled") or []) if str(value)]
    disabled = [value for value in disabled if value != skill_name]
    if not enabled:
        disabled.append(skill_name)
    skills["disabled"] = sorted(set(disabled), key=str.casefold)
    payload["skills"] = skills
    candidate = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    candidate, reconciliation = _reconcile_default_config(candidate)
    if not reconciliation.get("valid"):
        return {
            "ok": False,
            "phase": "validation",
            "validation": reconciliation,
        }
    result = hermes_config.queue_apply(
        conn_factory,
        candidate,
        reason=f"{'enabled' if enabled else 'disabled'} skill {skill_name} for Calliope",
        actor=actor,
        secret_bindings=current.get("secret_bindings") or {},
    )
    return {
        **result,
        "ok": bool(result.get("queued")),
        "profile": "default",
        "skill": skill_name,
        "enabled": enabled,
        "restart_required": False,
    }
