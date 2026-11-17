#!/usr/bin/env python3
"""Keep Hermes' gateway restartable while Calliope remains recoverable.

The process is the container's durable main program.  It takes the default
profile out of Hermes' built-in s6 supervision, owns that foreground gateway
child itself, and exposes a narrow authenticated control API; it is not a shell
or Docker control surface.  A bad candidate can kill the gateway child without
killing the supervisor that restores the last-known-good YAML.  Specialist
profiles remain under Hermes' normal per-profile supervision.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import runpy
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml


HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
CONFIG_PATH = HERMES_HOME / "config.yaml"
STATE_DIR = HERMES_HOME / ".calliope-control"
ARCHIVE_DIR = STATE_DIR / "revisions"
CONTROL_HOST = os.environ.get("CALLIOPE_HERMES_CONTROL_HOST", "0.0.0.0")
CONTROL_PORT = int(os.environ.get("CALLIOPE_HERMES_CONTROL_PORT", "8643"))
CONTROL_KEY = os.environ.get("HERMES_CONTROL_KEY") or os.environ.get("API_SERVER_KEY", "")
API_KEY = os.environ.get("API_SERVER_KEY", "")
GATEWAY_URL = "http://127.0.0.1:8642"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_SOUL_BYTES = 512 * 1024
MAX_PROFILE_BYTES = 64 * 1024
POLICY_PATH = Path("/opt/hermes/calliope/reconcile-hermes-config.py")
SPECIALIST_MANIFEST = Path("/opt/hermes/calliope/specialists/manifest.json")
SHIPPED_SKILL_ROOTS = (
    Path("/opt/hermes/skills"),
    Path("/opt/hermes/calliope/skills"),
)
S6_SVC = Path("/package/admin/s6/command/s6-svc")
DEFAULT_GATEWAY_SERVICE = Path("/run/service/gateway-default")
_ENV_PLATFORM_PREFIXES = (
    ("WHATSAPP_CLOUD_", "whatsapp_cloud"),
    ("BLUEBUBBLES_", "bluebubbles"),
    ("TELEGRAM_", "telegram"),
    ("DISCORD_", "discord"),
    ("SLACK_", "slack"),
    ("SIGNAL_", "signal"),
    ("MSGRAPH_", "msgraph"),
    ("FEISHU_", "feishu"),
    ("WECOM_", "wecom_callback"),
    ("TWILIO_", "sms"),
    ("LINE_", "line"),
)
_GATEWAY_CORE_READINESS_CHECKS = ("state_db", "config", "model", "gateway")
_MISSING = object()
_PROFILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&+\-]{0,79}$")
_AVATAR_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:png|jpe?g|webp)$", re.I)
_PROVIDER_KEY_ENV = {
    "clover": "RVBBIT_CLOVER_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai-api": "OPENAI_API_KEY",
    "xai": "XAI_API_KEY",
    # Hermes reads GOOGLE_API_KEY or GEMINI_API_KEY; the specific name keeps the
    # key away from every other Google integration that reads GOOGLE_API_KEY.
    "gemini": "GEMINI_API_KEY",
}
# The Claude Pro / Max subscription rides NousResearch's DirectSDK plugin,
# which authenticates only through the official Claude Code CLI (`claude auth
# login`, credentials in the CLI's own store under CLAUDE_CONFIG_DIR). Calliope
# never reads, copies, or prints that store: it drives the CLI and asks it.
_CLI_LOGIN_PROVIDER = "claude-subscription-directsdk-experimental"
_CLI_LOGIN_PROVIDERS = frozenset({_CLI_LOGIN_PROVIDER})
_CLI_LOGIN_COMMAND_ENV = "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"
_CLI_LOGIN_INSTALL_HINT = (
    "Claude Code is not installed in the Hermes image (no `claude` on PATH). "
    "Rebuild with the pinned CLI or set CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND to the binary."
)
# Subscription providers whose sign-in is a child process Calliope drives and
# polls (Hermes' native device-code logins plus the Claude Code CLI login).
_OAUTH_PROVIDERS = frozenset({"nous", "openai-codex", "xai-oauth", _CLI_LOGIN_PROVIDER})
# Providers whose login is a PKCE authorization-code flow: the CLI prints an
# authorization URL, the human signs in with the subscription in a browser,
# and the code shown on Anthropic's callback page is pasted back to the
# waiting CLI's stdin. Device-code providers need no paste.
_CODE_ENTRY_PROVIDERS = frozenset({_CLI_LOGIN_PROVIDER})
_MANAGED_PROVIDER_IDS = frozenset({
    "clover", "nous", "openrouter", "openai-api", "openai-codex", "xai", "xai-oauth", "gemini",
    _CLI_LOGIN_PROVIDER,
})
_PROVIDER_ORDER = [
    "clover", "openai-codex", "nous", _CLI_LOGIN_PROVIDER, "xai-oauth", "openrouter", "openai-api", "gemini", "xai",
]
_CALLIOPE_POOL_SOURCE = "manual:calliope"
_CALLIOPE_POOL_LABEL = "Calliope managed key"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HTTP_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_DEVICE_CODE_RE = re.compile(r"(?:enter|use)(?:\s+this)?\s+code\s*:\s*([A-Za-z0-9._-]+)", re.I)
_OAUTH_JOB_TTL_SECONDS = 20 * 60


def _load_policy():
    spec = importlib.util.spec_from_file_location("calliope_hermes_config_policy", POLICY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Calliope Hermes config policy is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _load_policy()


class FatalGatewayConfiguration(RuntimeError):
    """A configured platform rejected its credentials or permanent setup."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _redact_values(value: Any, secrets: list[str]) -> Any:
    """Remove runtime-only credentials from diagnostics before persistence."""
    secret_values = [item for item in secrets if item]
    if isinstance(value, dict):
        return {key: _redact_values(child, secret_values) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_values(child, secret_values) for child in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _gateway_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("API_SERVER_KEY is required for the Hermes gateway")
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY_URL + path,
        data=raw,
        method=method,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            failure = json.loads(exc.read().decode("utf-8"))
        except Exception:
            failure = {}
        detail = failure.get("detail") if isinstance(failure, dict) else None
        raise ValueError(str(detail or f"Hermes gateway returned HTTP {exc.code}")[:800]) from exc
    if not isinstance(result, dict):
        raise RuntimeError("Hermes gateway returned a non-object response")
    return result


# Live key probes, mirroring Hermes' own `_CREDENTIAL_PROBES` in
# hermes_cli/web_server.py. That validator only exists on the Hermes web
# dashboard (`hermes web`, :9119), which the appliance does not run; the gateway
# API server on :8642 answers 404 for it, which is what a pasted OpenRouter key
# used to surface as "Hermes gateway returned HTTP 404". Probing the provider
# directly keeps the same contract: {ok, reachable, message}.
_PROVIDER_KEY_PROBES: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1/key",
    "openai-api": "https://api.openai.com/v1/models",
    "xai": "https://api.x.ai/v1/models",
    # The OpenAI-compatible listing accepts the Gemini API key as a bearer token.
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/models",
}


def _probe_provider_key(provider: str, token: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Ask the provider whether it accepts `token`. Never logs or echoes it."""
    if provider == "clover":
        base = os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1")
        url = str(base).rstrip("/") + "/models"
    else:
        url = _PROVIDER_KEY_PROBES.get(provider, "")
    if not url:
        return {"ok": True, "reachable": False, "message": ""}
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (TimeoutError, urllib.error.URLError, OSError):
        return {"ok": False, "reachable": False,
                "message": "Could not reach the provider to verify the key."}
    # Google answers a malformed or unknown Gemini key with 400 INVALID_ARGUMENT
    # ("Please pass a valid API key"), not 401.
    if status in (401, 403) or (provider == "gemini" and status == 400):
        return {"ok": False, "reachable": True,
                "message": "That API key was rejected. Double-check it and try again."}
    if status == 429 or 200 <= status < 300:
        # 429 = the key is valid but rate-limited right now.
        return {"ok": True, "reachable": True, "message": ""}
    return {"ok": False, "reachable": True,
            "message": f"Provider returned HTTP {status} for this key."}


def _hutch_model_catalog(token: str) -> dict[str, Any]:
    """Discover the managed chat aliases, independently of Hermes' current model.

    Both application pickers consume this inventory. Only picker metadata is
    returned; provider response bodies and authentication never enter it.
    """
    base = os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1")
    request = urllib.request.Request(
        str(base).rstrip("/") + "/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Hutch model inventory is too large")
    payload = json.loads(raw)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Hutch returned an invalid model inventory")
    models: set[str] = set()
    capabilities: dict[str, dict[str, Any]] = {}
    for row in rows[:5000]:
        if not isinstance(row, dict):
            continue
        name = row.get("id")
        if (not isinstance(name, str) or not name.startswith("calliope")
                or len(name) > 256 or re.search(r"\s|[\x00-\x1f\x7f]", name)):
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        # /models can also contain embedding, image, and speech definitions.
        if meta.get("capability") not in (None, "chat.completions", "responses", "llm"):
            continue
        models.add(name)
        caps: dict[str, Any] = {"chat": True}
        for field in ("context_window", "max_output_tokens"):
            value = meta.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                caps[field] = value
        capabilities.setdefault(name, {}).update(caps)
    if not models:
        raise ValueError("Hutch returned no calliope chat models")
    ordered = sorted(models, key=lambda name: (name != "calliope", name))
    return {"models": ordered, "total_models": len(ordered), "capabilities": capabilities}


def _bounded_model_options(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only picker metadata; never proxy provider config or credentials."""
    providers: list[dict[str, Any]] = []
    total_models = 0
    for raw in payload.get("providers") or []:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug") or "").strip().lower()
        if slug == "custom" and raw.get("is_current"):
            slug = "clover"
        if slug not in _MANAGED_PROVIDER_IDS:
            continue
        models: list[str] = []
        for model in raw.get("models") or []:
            name = str(model or "").strip()
            if not name or len(name) > 256 or re.search(r"[\r\n\x00]", name):
                continue
            models.append(name)
            total_models += 1
            if total_models >= 5_000:
                break
        capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
        providers.append({
            "id": slug,
            "name": "Clover / Hutch" if slug == "clover" else str(raw.get("name") or slug)[:120],
            "auth_kind": (
                "environment" if slug == "clover"
                else "oauth" if slug in _OAUTH_PROVIDERS
                else "api_key"
            ),
            "configured": bool(raw.get("authenticated")),
            "models": models,
            "total_models": max(len(models), int(raw.get("total_models") or 0)),
            "warning": str(raw.get("warning") or "")[:400] or None,
            "capabilities": {
                model: value for model, value in list(capabilities.items())[:500]
                if model in models and isinstance(value, dict)
            },
        })
        if total_models >= 5_000:
            break
    return {
        "provider": str(payload.get("provider") or "")[:64] or None,
        "model": str(payload.get("model") or "")[:256] or None,
        "providers": providers,
    }


def _claude_cli_command() -> list[str] | None:
    """The official Claude Code CLI the DirectSDK plugin authenticates
    through: CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND, else `claude` on PATH
    (the same resolution as the plugin's own directsdk_setup)."""
    head = os.environ.get(_CLI_LOGIN_COMMAND_ENV, "").strip() or "claude"
    if os.path.isabs(head):
        exe = head if os.access(head, os.X_OK) and os.path.isfile(head) else None
    else:
        exe = shutil.which(head)
    return [exe] if exe else None


def _claude_cli_env() -> dict[str, str]:
    """Inherit the supervisor's environment (CLAUDE_CONFIG_DIR points at the
    persistent data volume) and keep the CLI quiet, exactly as the plugin
    does, so a login lands in the store the plugin reads."""
    env = dict(os.environ)
    config_dir = env.pop("CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR", None)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    env.update(
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        DISABLE_TELEMETRY="1",
        DISABLE_ERROR_REPORTING="1",
    )
    # Claude Code's login check reads USER/LOGNAME; the container leaves them
    # unset, and without them a logged-in CLI reports loggedIn: false.
    if not env.get("USER") or not env.get("LOGNAME"):
        try:
            import pwd
            name = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            name = ""
        for key in ("USER", "LOGNAME"):
            if name and not env.get(key):
                env[key] = name
    return env


def _claude_plan_label(raw: Any) -> str:
    plan = str(raw or "").strip()
    if not plan:
        return "Claude subscription"
    if plan.lower().startswith("claude"):
        return plan[:120]
    return ("Claude " + plan.replace("_", " ").title())[:120]


def _claude_cli_status(timeout: float = 20.0) -> dict[str, Any]:
    """`claude auth status` prints one JSON object to stdout and exits 1 when
    logged out; the JSON is authoritative either way. Only the plan name leaves
    this reader — never the credential file, an email, an org, or a token."""
    command = _claude_cli_command()
    if command is None:
        return {"logged_in": False, "available": False, "error": _CLI_LOGIN_INSTALL_HINT}
    try:
        run = subprocess.run(
            command + ["auth", "status"],
            cwd=HERMES_HOME,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_claude_cli_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"logged_in": False, "available": True, "error": f"{type(exc).__name__}: {exc}"}
    text = _ANSI_ESCAPE_RE.sub("", run.stdout or "")
    payload: Any = None
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        return {
            "logged_in": False,
            "available": True,
            "error": f"`claude auth status` returned no status (exit {run.returncode})",
        }
    logged_in = payload.get("loggedIn") is True
    return {
        "logged_in": logged_in,
        "available": True,
        "source": "claude-code-cli",
        "source_label": _claude_plan_label(payload.get("subscriptionType")) if logged_in else "",
        "expires_at": None,
        "has_refresh_token": None,
    }


def _claude_cli_logout(timeout: float = 30.0) -> dict[str, Any]:
    """`claude auth logout` drops the CLI's own credentials; the files stay
    the CLI's to manage — Calliope deletes nothing under CLAUDE_CONFIG_DIR."""
    command = _claude_cli_command()
    if command is None:
        # Nothing can be logged in without the binary; report the gap, do not fail the disconnect.
        return {"cleared": False, "error": _CLI_LOGIN_INSTALL_HINT}
    try:
        run = subprocess.run(
            command + ["auth", "logout"],
            cwd=HERMES_HOME,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_claude_cli_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"could not run `claude auth logout`: {type(exc).__name__}") from exc
    if run.returncode != 0:
        detail = _ANSI_ESCAPE_RE.sub("", (run.stdout or "") + (run.stderr or "")).strip()
        raise ValueError(f"`claude auth logout` exited {run.returncode}: {detail[:200]}")
    return {"cleared": True}


def _native_oauth_status(provider: str) -> dict[str, Any]:
    from hermes_cli.auth import (
        get_codex_auth_status,
        get_nous_auth_status_local,
        get_xai_oauth_auth_status,
    )

    readers = {
        "nous": get_nous_auth_status_local,
        "openai-codex": get_codex_auth_status,
        "xai-oauth": get_xai_oauth_auth_status,
    }
    return readers[provider]() or {}


def _safe_oauth_status(provider: str) -> dict[str, Any]:
    """Read Hermes' native auth store (or ask the Claude Code CLI) while
    returning no credential material."""
    cli_login = provider in _CLI_LOGIN_PROVIDERS
    try:
        raw = _claude_cli_status() if cli_login else _native_oauth_status(provider)
    except Exception as exc:
        raw = {"logged_in": False, "error": f"{type(exc).__name__}: {exc}"}
    configured = bool(raw.get("logged_in"))
    error = str(raw.get("error") or "")[:400] or None
    if not configured and error and re.search(r"\bno\b.+\bcredentials stored\b", error, re.I):
        error = None
    return {
        "configured": configured,
        "auth_kind": "cli_login" if cli_login else "oauth",
        # False only when the CLI-login binary is missing from the image.
        "available": raw.get("available") is not False,
        "flow": "authorization_code" if provider in _CODE_ENTRY_PROVIDERS else "device_code",
        "source": str(raw.get("source") or raw.get("credential_source") or "")[:120] or None,
        "source_label": str(raw.get("source_label") or raw.get("auth_mode") or "")[:240] or None,
        "expires_at": str(
            raw.get("expires_at") or raw.get("access_expires_at") or raw.get("agent_key_expires_at") or ""
        )[:80] or None,
        # The CLI keeps its own refresh handling; there is no token for Calliope to know about.
        "has_refresh_token": None if cli_login else bool(raw.get("has_refresh_token")),
        "error": error,
    }


def _oauth_line_fields(line: str, *, expect_code: bool = False) -> dict[str, Any]:
    """Extract only device-flow display fields from one CLI output line."""
    clean = _ANSI_ESCAPE_RE.sub("", str(line or "")).strip()
    fields: dict[str, Any] = {"expect_code": "enter this code" in clean.lower()}
    url_match = _HTTP_URL_RE.search(clean)
    if url_match:
        fields["verification_url"] = url_match.group(0).rstrip(".,);]")[:2000]
    code_match = _DEVICE_CODE_RE.search(clean)
    if code_match:
        fields["user_code"] = code_match.group(1)[:80]
    elif expect_code and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", clean):
        fields["user_code"] = clean
    lowered = clean.lower()
    safe_markers = (
        "starting hermes login", "portal:", "to continue", "open this url",
        "open:", "enter this code", "enter code", "waiting for", "successful",
        "rate-limit", "rate limit", "timed out", "failed", "error", "cancel",
    )
    if clean and any(marker in lowered for marker in safe_markers):
        fields["message"] = clean[:500]
    return fields


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o660
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _profile_root(profile_key: str) -> Path:
    key = str(profile_key or "default").strip().lower()
    if key == "default":
        return HERMES_HOME
    if not _PROFILE_KEY_RE.fullmatch(key):
        raise ValueError("invalid Hermes profile key")
    root = HERMES_HOME / "profiles" / key
    if not root.is_dir():
        raise ValueError(f"Hermes profile does not exist: {key}")
    return root


def _profile_keys() -> set[str]:
    """Every specialist profile with a config on disk (manifest or operator-added)."""
    root = HERMES_HOME / "profiles"
    if not root.is_dir():
        return set()
    return {
        path.name for path in root.iterdir()
        if path.is_dir() and (path / "config.yaml").is_file()
    }


def _profile_config(profile_key: str) -> tuple[Path, dict[str, Any], bytes]:
    path = _profile_root(profile_key) / "config.yaml"
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Hermes profile config must be a YAML object")
    return path, payload, raw


def _specialist_manifest() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(SPECIALIST_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    profiles = payload.get("profiles") if isinstance(payload, dict) else []
    return {
        str(item.get("key") or "").strip().lower(): item
        for item in profiles or []
        if isinstance(item, dict) and item.get("key")
    }


def _profile_identity(profile_key: str) -> dict[str, Any]:
    path = _profile_root(profile_key) / "profile.yaml"
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


MODEL_OPERATOR_MARKER = "model.operator-managed"


def _profile_model_summary(profile_key: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Secret-free view of one agent's model block for the Models & agents UI."""
    if config is None:
        try:
            _path, config, _raw = _profile_config(profile_key)
        except (OSError, ValueError):
            config = {}
    block = config.get("model") if isinstance(config.get("model"), dict) else {}
    agent_block = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    provider = str(block.get("provider") or "").strip().lower()
    root = _profile_root(profile_key)
    return {
        "provider": provider or None,
        "model": str(block.get("default") or "").strip() or None,
        "reasoning_effort": str(agent_block.get("reasoning_effort") or "").strip().lower() or None,
        "context_length": block.get("context_length") if isinstance(block.get("context_length"), int) else None,
        "max_tokens": block.get("max_tokens") if isinstance(block.get("max_tokens"), int) else None,
        "operator_managed": (
            profile_key != "default"
            and (root / ".calliope-managed" / MODEL_OPERATOR_MARKER).exists()
        ),
        "errors": POLICY.model_block_errors(block, "model") if block else [{"path": "model", "message": "required mapping is missing"}],
    }


def _profile_identity_validation(profile_key: str, raw: bytes) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not raw.strip() or len(raw) > MAX_PROFILE_BYTES:
        errors.append({"path": "", "message": "profile.yaml is empty or too large"})
        payload: Any = {}
    else:
        try:
            payload = yaml.safe_load(raw.decode("utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            payload = {}
            errors.append({"path": "", "message": f"invalid YAML: {exc}"})
    if not isinstance(payload, dict):
        errors.append({"path": "", "message": "profile.yaml must contain a YAML object"})
        payload = {}
    limits = {"name": 80, "role_label": 120, "description": 2000}
    for field, limit in limits.items():
        value = str(payload.get(field) or "").strip()
        if not value:
            errors.append({"path": field, "message": f"{field} is required"})
        elif len(value) > limit:
            errors.append({"path": field, "message": f"{field} exceeds {limit} characters"})
    font = str(payload.get("signature_font") or "").strip()
    if font and not _FONT_FAMILY_RE.fullmatch(font):
        errors.append({"path": "signature_font", "message": "signature_font is not a safe font family name"})
    for field in ("avatar_file", "avatar_day_file", "avatar_night_file"):
        filename = str(payload.get(field) or "").strip()
        if filename and not _AVATAR_FILE_RE.fullmatch(filename):
            errors.append({"path": field, "message": f"{field} must be an image filename, not a path"})
    voice = payload.get("voice_id")
    if voice is not None and (not isinstance(voice, str) or (voice and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", voice))):
        errors.append({"path": "voice_id", "message": "voice_id must be a provider identifier or null"})
    persona = payload.get("speech_persona")
    if persona is not None and (not isinstance(persona, str) or len(persona) > 600):
        errors.append({"path": "speech_persona", "message": "speech_persona must be text of at most 600 characters or null"})
    preview = payload.get("voice_preview_text")
    if preview is not None and (not isinstance(preview, str) or len(preview) > 800):
        errors.append({"path": "voice_preview_text", "message": "voice_preview_text must be text of at most 800 characters or null"})
    known = {
        "speech_persona", "voice_preview_text", "voice_id", "avatar_color", "avatar_day_color", "avatar_night_color", "avatar_color_cache",
        "name", "role_label", "description", "signature_font", "avatar_file",
        "avatar_day_file", "avatar_night_file", "description_auto",
    }
    for field in sorted(set(payload) - known):
        warnings.append({"path": str(field), "message": "preserved custom identity metadata"})
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "content_hash": _sha(raw),
        "profile": profile_key,
    }


def _frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", raw, re.S)
    if not match:
        return {}, raw
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        metadata = {}
    return (metadata if isinstance(metadata, dict) else {}), match.group(2)


@lru_cache(maxsize=1)
def _shipped_skill_hashes() -> dict[str, set[str]]:
    """Index immutable image-owned skills by their declared Hermes name."""
    inventory: dict[str, set[str]] = {}
    for root in SHIPPED_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("SKILL.md"):
            try:
                raw = path.read_text(encoding="utf-8")
                meta, _body = _frontmatter(raw)
                name = str(meta.get("name") or path.parent.name).strip()
                if _SKILL_NAME_RE.fullmatch(name):
                    inventory.setdefault(name, set()).add(_sha(raw.encode("utf-8")))
            except (OSError, UnicodeError, ValueError):
                continue
    return inventory


def _profile_skills(profile_key: str, *, include_content: bool = False) -> list[dict[str, Any]]:
    root = _profile_root(profile_key)
    _path, config, _raw = _profile_config(profile_key)
    skills_config = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    disabled = {
        str(value).strip()
        for value in (skills_config.get("disabled") or [])
        if str(value).strip()
    }
    skills_root = root / "skills"
    shipped = _shipped_skill_hashes()
    items: list[dict[str, Any]] = []
    if not skills_root.is_dir():
        return items
    for path in sorted(skills_root.rglob("SKILL.md")):
        try:
            raw = path.read_text(encoding="utf-8")
            meta, body = _frontmatter(raw)
            directory_name = path.parent.name
            name = str(meta.get("name") or directory_name).strip()
            if not _SKILL_NAME_RE.fullmatch(name):
                continue
            description = str(meta.get("description") or "").strip()
            relative = str(path.relative_to(root))
            content_hash = _sha(raw.encode("utf-8"))
            shipped_hashes = shipped.get(name)
            origin = (
                "builtin"
                if shipped_hashes and content_hash in shipped_hashes
                else "customized"
                if shipped_hashes
                else "custom"
            )
            # A zero-byte SKILL.md is a broken copy, not a customised skill:
            # the agent loads nothing (skill_view returns content ""). Say so.
            empty = not raw.strip()
            if empty:
                origin = "empty"
            item = {
                "name": name,
                "description": description,
                "enabled": name not in disabled and directory_name not in disabled,
                "disabled_key": name if name in disabled else directory_name,
                "path": relative,
                "category": path.parent.parent.name if path.parent.parent != skills_root else "skills",
                "content_hash": content_hash,
                "origin": origin,
                "is_custom": origin not in {"builtin", "empty"},
                "empty": empty,
                "body_preview": " ".join(body.strip().split())[:280],
            }
            if include_content:
                item["content"] = raw
            items.append(item)
        except (OSError, UnicodeError, ValueError):
            continue
    return items


def reconcile_candidate(raw: bytes) -> dict[str, Any]:
    """Upgrade a durable candidate to current hosted invariants before validation."""
    if not raw or len(raw) > MAX_CONFIG_BYTES:
        return validate_candidate(raw)
    try:
        payload = yaml.safe_load(raw.decode("utf-8")) or {}
        changed = POLICY.reconcile_payload(payload)
        reconciled = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8")
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "", "message": f"hosted reconciliation failed: {exc}"}],
            "warnings": [],
            "native_validation": "not_run",
        }
    result = validate_candidate(reconciled)
    result["config_yaml"] = reconciled.decode("utf-8")
    result["reconciled"] = bool(changed)
    return result


def _specialist_config_validation(profile_key: str, raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_CONFIG_BYTES:
        return {"valid": False, "errors": [{"path": "", "message": "candidate YAML is empty or too large"}], "warnings": []}
    try:
        payload = yaml.safe_load(raw.decode("utf-8")) or {}
    except Exception as exc:
        return {"valid": False, "errors": [{"path": "", "message": f"YAML parse failed: {exc}"}], "warnings": []}
    if not isinstance(payload, dict):
        return {"valid": False, "errors": [{"path": "", "message": "candidate YAML must be an object"}], "warnings": []}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    try:
        native_issues = _native_validation(payload)
    except Exception as exc:
        errors.append({"path": "", "message": f"pinned Hermes validator failed: {type(exc).__name__}: {exc}"})
        native_issues = []
    for issue in native_issues:
        target = errors if issue["severity"].lower() == "error" else warnings
        target.append({"path": "", "message": f"Hermes: {issue['message']}", "hint": issue["hint"]})
    # The same model-block rules the default profile is held to: a hosted
    # provider, no plaintext credentials, Hutch bindings only for `custom`.
    errors.extend(POLICY.model_block_errors(payload.get("model"), "model"))

    # These are the handful of multiplex invariants that keep a secondary
    # profile attached to Calliope. Everything else remains user-editable.
    platforms = payload.get("platforms") if isinstance(payload.get("platforms"), dict) else {}
    api_server = platforms.get("api_server") if isinstance(platforms.get("api_server"), dict) else {}
    mcp = payload.get("mcp_servers") if isinstance(payload.get("mcp_servers"), dict) else {}
    warehouse = mcp.get("Specialist") if isinstance(mcp.get("Specialist"), dict) else {}
    toolsets = payload.get("platform_toolsets") if isinstance(payload.get("platform_toolsets"), dict) else {}
    api_tools = toolsets.get("api_server") if isinstance(toolsets.get("api_server"), list) else []
    invariants = (
        (api_server.get("enabled") is False, "platforms.api_server.enabled", "must remain false for a multiplex specialist"),
        (str(warehouse.get("url") or "").startswith("http://calliope:8765/mcp"), "mcp_servers.Specialist.url", "must retain the internal Calliope Warehouse lane"),
        (warehouse.get("forward_session_identity") is True, "mcp_servers.Specialist.forward_session_identity", "must remain true"),
        ("Specialist" in api_tools, "platform_toolsets.api_server", "must include Specialist"),
    )
    for ok, path, message in invariants:
        if not ok:
            errors.append({"path": path, "message": message})
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "native_validation": "failed" if errors else "passed",
        "config_hash": _sha(raw),
        "profile": profile_key,
    }


def _native_validation(payload: dict[str, Any]) -> list[dict[str, str]]:
    from hermes_cli.config import validate_config_structure

    return [
        {"severity": str(issue.severity), "message": issue.message, "hint": issue.hint}
        for issue in validate_config_structure(payload)
    ]


def validate_candidate(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_CONFIG_BYTES:
        return {
            "valid": False,
            "errors": [{"path": "", "message": "candidate YAML is empty or too large"}],
            "warnings": [],
            "native_validation": "not_run",
        }
    try:
        payload = yaml.safe_load(raw.decode("utf-8")) or {}
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "", "message": f"YAML parse failed: {exc}"}],
            "warnings": [],
            "native_validation": "not_run",
        }
    result = POLICY.validate_hosted_payload(payload, native=False)
    try:
        native_issues = _native_validation(payload)
    except Exception as exc:
        result["errors"].append({
            "path": "",
            "message": f"pinned Hermes validator failed: {type(exc).__name__}: {exc}",
        })
        result["native_validation"] = "failed"
    else:
        result["native_validation"] = "passed"
        result["native_issues"] = native_issues
        for issue in native_issues:
            target = result["errors"] if issue["severity"].lower() == "error" else result["warnings"]
            target.append({
                "path": "",
                "message": f"Hermes: {issue['message']}",
                "hint": issue["hint"],
            })
        if any(issue["severity"].lower() == "error" for issue in native_issues):
            result["native_validation"] = "failed"
    result["valid"] = not result["errors"]
    result["config_hash"] = _sha(raw)
    return result


def _gateway_readiness_error(detailed: Any) -> str | None:
    """Return a rollout-blocking readiness error, if any.

    Hermes intentionally marks the aggregate health ``degraded`` for
    operational warnings such as disk utilization. A model/config rollout
    only needs the state database, parsed config, model client, and gateway
    runtime to be ready; treating a disk warning as failed startup caused a
    90-second timeout, rollback, and second restart on every settings change.
    We tolerate only an explicit degraded aggregate whose core checks are all
    present and healthy. Unknown/missing health shapes still fail closed.
    """
    if not isinstance(detailed, dict):
        return "gateway readiness response is invalid"
    status = str(detailed.get("status") or "").lower()
    if status in {"ok", "ready", "healthy"}:
        return None
    if status != "degraded":
        return f"gateway readiness is {detailed.get('status')}"
    readiness = detailed.get("readiness")
    checks = readiness.get("checks") if isinstance(readiness, dict) else None
    if not isinstance(checks, dict):
        return "gateway readiness is degraded (core checks unavailable)"
    failures: list[str] = []
    for name in _GATEWAY_CORE_READINESS_CHECKS:
        item = checks.get(name)
        state = str(item.get("status") or "missing").lower() if isinstance(item, dict) else "missing"
        if state not in {"ok", "ready", "healthy"}:
            failures.append(f"{name}={state}")
    if failures:
        return "gateway core readiness is degraded: " + ", ".join(failures)
    return None


class GatewaySupervisor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.oauth_lock = threading.RLock()
        self.child: subprocess.Popen[bytes] | None = None
        self.oauth_jobs: dict[str, dict[str, Any]] = {}
        self.credit_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.stopping = False
        self.restart_count = 0
        self.last_exit_code: int | None = None
        # Credential values arrive only from Calliope's private controller.
        # They are inherited by the gateway child but never written to YAML,
        # the archive directory, logs, or the supervisor status response.
        self.runtime_env: dict[str, str] = {}
        # Model-provider keys are restored from Hermes' durable native pool.
        # Keep them separate from config secret bindings, which are replaced
        # atomically whenever an administrator applies a YAML revision.
        self.provider_env: dict[str, str] = {}
        self.instance_key_restart_pending = False
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _disable_internal_default_supervisor() -> None:
        """Keep s6 from racing the config controller for the default profile."""
        if not S6_SVC.exists() or not DEFAULT_GATEWAY_SERVICE.exists():
            return
        subprocess.run(
            [str(S6_SVC), "-dwD", str(DEFAULT_GATEWAY_SERVICE)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )

    @staticmethod
    def _forward_child_output(child: subprocess.Popen[bytes], secrets: list[str]) -> None:
        """Forward gateway output to Docker logs without runtime credentials."""
        if child.stdout is None:
            return
        encoded_secrets = [secret.encode("utf-8") for secret in secrets if secret]
        try:
            for line in iter(child.stdout.readline, b""):
                for secret in encoded_secrets:
                    line = line.replace(secret, b"[REDACTED]")
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        finally:
            child.stdout.close()

    @staticmethod
    def _calliope_pool_token(provider: str) -> str:
        """Return only Calliope's managed native-pool token for one provider."""
        try:
            from agent.credential_pool import load_pool
            entries = list(load_pool(provider).entries())
        except Exception:
            return ""
        for entry in reversed(entries):
            if entry.source != _CALLIOPE_POOL_SOURCE and entry.label != _CALLIOPE_POOL_LABEL:
                continue
            token = (
                getattr(entry, "runtime_api_key", None)
                or getattr(entry, "access_token", None)
                or ""
            )
            if token:
                return str(token)
        return ""

    def _restore_provider_environment(self) -> None:
        restored: dict[str, str] = {}
        for provider, env_name in _PROVIDER_KEY_ENV.items():
            shared = provider == "openrouter" and os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE")
            if os.environ.get(env_name) and not shared:
                continue
            token = self._calliope_pool_token(provider)
            if token:
                restored[env_name] = token
        self.provider_env = restored

    def _gateway_child_environment(self) -> dict[str, str]:
        """Build the child environment without overriding its durable model.

        Hermes treats API_SERVER_MODEL_NAME as a request-level model override,
        not as a cosmetic API alias.  The versioned YAML is authoritative so
        provider changes apply equally to browser and headless sessions.
        """
        child_env = {**os.environ, **self.runtime_env, **self.provider_env}
        child_env.pop("API_SERVER_MODEL_NAME", None)
        return child_env

    def start_child(self) -> None:
        if self.child and self.child.poll() is None:
            return
        self._restore_provider_environment()
        self._disable_internal_default_supervisor()
        self.child = subprocess.Popen(
            [
                "/opt/hermes/.venv/bin/hermes",
                "gateway",
                "run",
                "--no-supervise",
                "--external-supervisor",
            ],
            cwd=HERMES_HOME,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=self._gateway_child_environment(),
        )
        threading.Thread(
            target=self._forward_child_output,
            args=(self.child, [*self.runtime_env.values(), *self.provider_env.values()]),
            name=f"calliope-hermes-output-{self.child.pid}",
            daemon=True,
        ).start()
        self.restart_count += 1
        print(f"[calliope-control] gateway started pid={self.child.pid}", flush=True)

    # A SIGKILLed gateway "exited UNCLEANLY" in Hermes' lifecycle ledger, and the next
    # boot then runs abandoned-delegation recovery for every multiplexed profile on
    # the event loop; on 2026-09-23 (0.21.4, 23 profiles) that blocked the loop long
    # enough for the liveness watchdog to exit 75 — itself unclean — and loop. A
    # SIGTERM drain of 23 profiles can take well over 30 s, so wait long enough for
    # it to finish cleanly before escalating.
    def stop_child(self, timeout: float = 150.0) -> None:
        child = self.child
        if not child or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(
                f"[calliope-control] gateway did not drain within {timeout:.0f}s after SIGTERM; "
                "sending SIGKILL (the next boot will run unclean-exit recovery)",
                flush=True,
            )
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)
        self.last_exit_code = child.returncode
        print(f"[calliope-control] gateway stopped rc={child.returncode}", flush=True)

    def _gateway_probe(self, path: str = "/health/detailed", timeout: float = 4.0) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
        request = urllib.request.Request(GATEWAY_URL + path, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def _expected_platforms(self) -> set[str]:
        """Return only platforms enabled by this candidate or its bindings."""
        expected: set[str] = set()
        try:
            payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            payload = {}
        platforms = payload.get("platforms") if isinstance(payload, dict) else {}
        if isinstance(platforms, dict):
            expected.update(
                str(name)
                for name, config in platforms.items()
                if isinstance(config, dict) and config.get("enabled") is True
            )
        for env_name in self.runtime_env:
            for prefix, platform_name in _ENV_PLATFORM_PREFIXES:
                if env_name.startswith(prefix):
                    expected.add(platform_name)
                    break
        return expected

    # 0.21.4 keeps the event loop busy for 2-2.5 minutes after api_server binds while the
    # multiplexed profiles start, so /health/detailed and /v1/commands do not answer until
    # then; 90 s verified a rollback on every settings change (2026-09-23).
    def wait_healthy(
        self, timeout: float = 360.0, *, redaction_values: list[str] | None = None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error = "gateway did not start"
        expected_platforms = self._expected_platforms()
        diagnostic_secrets = list(
            redaction_values or [*self.runtime_env.values(), *self.provider_env.values()]
        )
        while time.monotonic() < deadline:
            child = self.child
            if child and child.poll() is not None:
                raise RuntimeError(f"gateway exited during startup with {child.returncode}")
            try:
                detailed = self._gateway_probe()
                commands = self._gateway_probe("/v1/commands")
                warehouse_request = urllib.request.Request("http://calliope:8765/health")
                with urllib.request.urlopen(warehouse_request, timeout=4) as response:
                    warehouse_raw = response.read()
                    try:
                        warehouse = json.loads(warehouse_raw)
                    except (TypeError, ValueError):
                        warehouse_text = warehouse_raw.decode("utf-8", errors="replace").strip()
                        if response.status != 200 or warehouse_text.lower() not in {
                            "ok",
                            "ready",
                            "healthy",
                        }:
                            raise RuntimeError(
                                f"Warehouse readiness is HTTP {response.status}: {warehouse_text[:120]}"
                            )
                        warehouse = {"status": warehouse_text.lower()}
                readiness_error = _gateway_readiness_error(detailed)
                if readiness_error:
                    raise RuntimeError(readiness_error)
                if child and detailed.get("pid") and int(detailed["pid"]) != child.pid:
                    raise RuntimeError("gateway readiness came from the prior process")
                platform_failures: list[str] = []
                fatal_platform_failures: list[str] = []
                for platform_name, platform_status in (detailed.get("platforms") or {}).items():
                    if platform_name not in expected_platforms or not isinstance(platform_status, dict):
                        continue
                    state = str(platform_status.get("state") or "unknown").lower()
                    if platform_status.get("needs_attention") or state not in {
                        "connected",
                        "ready",
                        "healthy",
                        "running",
                        "disabled",
                    }:
                        code = str(platform_status.get("error_code") or "not_ready")
                        summary = f"{platform_name}={state} ({code})"
                        if state in {"fatal", "failed", "error"}:
                            fatal_platform_failures.append(summary)
                        else:
                            platform_failures.append(summary)
                if fatal_platform_failures:
                    raise FatalGatewayConfiguration(
                        "gateway platforms rejected their configuration: "
                        + ", ".join(fatal_platform_failures)
                    )
                if platform_failures:
                    raise RuntimeError(
                        "gateway platforms are not ready: " + ", ".join(platform_failures)
                    )
                command_items = commands if isinstance(commands, list) else (
                    commands.get("commands")
                    or commands.get("items")
                    or commands.get("data")
                    or []
                )
                return _redact_values(
                    {
                        "gateway": detailed,
                        "commands_available": len(command_items),
                        "warehouse": warehouse,
                    },
                    diagnostic_secrets,
                )
            except FatalGatewayConfiguration:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1)
        raise RuntimeError(f"gateway health timeout: {last_error}")

    def status(self) -> dict[str, Any]:
        # Keep the recovery endpoint observable while an apply owns the
        # lifecycle lock and waits for readiness/rollback.  Atomic config
        # replacement plus Python reference assignment make this snapshot safe.
        child = self.child
        raw = CONFIG_PATH.read_bytes() if CONFIG_PATH.exists() else b""
        return {
            "status": "ready",
            "gateway_running": bool(child and child.poll() is None),
            "gateway_pid": child.pid if child and child.poll() is None else None,
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
            "config_hash": _sha(raw) if raw else None,
            "runtime_env_names": sorted(self.runtime_env),
            "provider_env_names": sorted(self.provider_env),
        }

    def current_config(self) -> dict[str, Any]:
        raw = CONFIG_PATH.read_bytes()
        return {
            "config_yaml": raw.decode("utf-8"),
            "config_hash": _sha(raw),
            "validation": validate_candidate(raw),
        }

    def profiles(self) -> dict[str, Any]:
        manifest = _specialist_manifest()
        result: list[dict[str, Any]] = []
        discovered = {
            path.name
            for path in (HERMES_HOME / "profiles").iterdir()
            if path.is_dir() and (path / "config.yaml").is_file()
        } if (HERMES_HOME / "profiles").is_dir() else set()
        keys = ["default", *sorted(set(manifest) | discovered)]
        for key in keys:
            root = _profile_root(key)
            skills = _profile_skills(key)
            item = manifest.get(key, {})
            profile_metadata = _profile_identity(key)
            profile_raw = (root / "profile.yaml").read_bytes() if (root / "profile.yaml").is_file() else b""
            result.append({
                "key": key,
                "name": str(profile_metadata.get("name") or item.get("name") or ("Calliope" if key == "default" else key.replace("-", " ").title())),
                "description": (
                    str(profile_metadata.get("description") or item.get("description") or (
                        "Primary company intelligence agent and orchestrator."
                        if key == "default" else "Hermes specialist profile"
                    ))
                ),
                "role_label": str(profile_metadata.get("role_label") or ("Warehouse muse" if key == "default" else "Specialist")),
                "signature_font": str(profile_metadata.get("signature_font") or "") or None,
                "voice_id": profile_metadata.get("voice_id") or None,
                "voice_preview_text": profile_metadata.get("voice_preview_text") or None,
                "speech_persona": profile_metadata.get("speech_persona") or None,
                "avatar_file": str(profile_metadata.get("avatar_file") or "") or None,
                "avatar_day_file": str(profile_metadata.get("avatar_day_file") or "") or None,
                "avatar_night_file": str(profile_metadata.get("avatar_night_file") or "") or None,
                "skills_total": len(skills),
                "skills_enabled": sum(1 for skill in skills if skill["enabled"]),
                "config_hash": _sha((root / "config.yaml").read_bytes()),
                "soul_hash": _sha((root / "SOUL.md").read_bytes()) if (root / "SOUL.md").exists() else None,
                "profile_hash": _sha(profile_raw) if profile_raw else None,
                "config_user_managed": (root / ".calliope-managed" / "config.user-managed").exists(),
                "model": _profile_model_summary(key),
            })
        return {"profiles": result, "restart_required": False}

    def profile(self, profile_key: str) -> dict[str, Any]:
        profiles = self.profiles()["profiles"]
        summary = next((item for item in profiles if item["key"] == profile_key), None)
        if summary is None:
            raise ValueError(f"Hermes profile does not exist: {profile_key}")
        return {"profile": summary, "skills": _profile_skills(profile_key)}

    def profile_skill(self, profile_key: str, skill_name: str) -> dict[str, Any]:
        name = str(skill_name or "").strip()
        match = next(
            (item for item in _profile_skills(profile_key, include_content=True) if item["name"] == name),
            None,
        )
        if match is None:
            raise ValueError(f"skill is not installed for {profile_key}: {name}")
        return {"profile": profile_key, "skill": match}

    def profile_document(self, profile_key: str, kind: str) -> dict[str, Any]:
        filenames = {"config": "config.yaml", "profile": "profile.yaml", "soul": "SOUL.md"}
        if kind not in filenames:
            raise ValueError("document must be config, profile, or soul")
        path = _profile_root(profile_key) / filenames[kind]
        raw = path.read_bytes() if path.exists() else b""
        return {
            "profile": profile_key,
            "document": kind,
            "content": raw.decode("utf-8"),
            "content_hash": _sha(raw),
            "user_managed": (_profile_root(profile_key) / ".calliope-managed" / "config.user-managed").exists() if kind == "config" else True,
        }

    def validate_profile_document(self, profile_key: str, kind: str, content: str) -> dict[str, Any]:
        raw = content.encode("utf-8")
        if kind == "profile":
            return _profile_identity_validation(profile_key, raw)
        if kind == "soul":
            valid = bool(raw.strip()) and len(raw) <= MAX_SOUL_BYTES
            return {
                "valid": valid,
                "errors": [] if valid else [{"path": "", "message": "SOUL.md is empty or too large"}],
                "warnings": [],
                "content_hash": _sha(raw),
                "profile": profile_key,
            }
        if kind != "config":
            raise ValueError("document must be config, profile, or soul")
        if profile_key == "default":
            return validate_candidate(raw)
        return _specialist_config_validation(profile_key, raw)

    def save_profile_document(
        self, profile_key: str, kind: str, content: str, expected_hash: str | None
    ) -> dict[str, Any]:
        validation = self.validate_profile_document(profile_key, kind, content)
        if not validation.get("valid"):
            return {"ok": False, "phase": "validation", "validation": validation}
        if profile_key == "default" and kind == "config":
            return {
                "ok": False,
                "phase": "durable_controller_required",
                "error": "The default profile config must be applied through Calliope's revision controller.",
                "validation": validation,
            }
        root = _profile_root(profile_key)
        filenames = {"config": "config.yaml", "profile": "profile.yaml", "soul": "SOUL.md"}
        path = root / filenames[kind]
        with self.lock:
            current = path.read_bytes() if path.exists() else b""
            current_hash = _sha(current)
            if expected_hash and expected_hash != current_hash:
                return {
                    "ok": False,
                    "phase": "compare_and_swap",
                    "error": "document changed after it was opened",
                    "active_content_hash": current_hash,
                }
            archive_dir = STATE_DIR / "profile-revisions" / profile_key / kind
            archive_dir.mkdir(parents=True, exist_ok=True)
            if current:
                _atomic_write(archive_dir / f"{current_hash}.txt", current)
            raw = content.encode("utf-8")
            _atomic_write(path, raw)
            _atomic_write(archive_dir / f"{_sha(raw)}.txt", raw)
            if kind == "config" and profile_key != "default":
                marker = root / ".calliope-managed" / "config.user-managed"
                _atomic_write(marker, b"edited through Calliope Agent Skills\n")
        return {
            "ok": True,
            "profile": profile_key,
            "document": kind,
            "content_hash": _sha(raw),
            "restart_required": kind != "profile",
            "validation": validation,
        }

    def toggle_profile_skill(self, profile_key: str, skill_name: str, enabled: bool) -> dict[str, Any]:
        name = str(skill_name or "").strip()
        if not _SKILL_NAME_RE.fullmatch(name):
            raise ValueError("invalid Hermes skill name")
        # Specialist reconciliation and this UI can both update config.yaml.
        # Rebase a bounded number of times so an ordinary background refresh
        # cannot escape to the user as an opaque HTTP 409.
        for _attempt in range(3):
            known = {str(skill["name"]): skill for skill in _profile_skills(profile_key)}
            if name not in known:
                raise ValueError(f"skill is not installed for {profile_key}: {name}")
            path, payload, current = _profile_config(profile_key)
            skills = payload.get("skills") if isinstance(payload.get("skills"), dict) else {}
            disabled = [str(value) for value in (skills.get("disabled") or []) if str(value).strip()]
            aliases = {name, str(known[name].get("disabled_key") or name)}
            disabled = [value for value in disabled if value not in aliases]
            if not enabled:
                disabled.append(name)
            skills["disabled"] = sorted(set(disabled), key=str.casefold)
            payload["skills"] = skills
            raw = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8")
            validation = validate_candidate(raw) if profile_key == "default" else _specialist_config_validation(profile_key, raw)
            if not validation.get("valid"):
                return {"ok": False, "phase": "validation", "validation": validation}
            with self.lock:
                if path.read_bytes() != current:
                    continue
                _atomic_write(path, raw)
                return {
                    "ok": True,
                    "profile": profile_key,
                    "skill": name,
                    "enabled": bool(enabled),
                    "config_hash": _sha(raw),
                    "restart_required": True,
                }
        return {
            "ok": False,
            "phase": "compare_and_swap",
            "error": "profile config kept changing during the update; refresh and try once more",
        }

    def _restart_verified(self, restore: Any) -> dict[str, Any]:
        """Restart the gateway on the current on-disk config; on a failed
        health check call `restore()` and bring the previous state back."""
        diagnostic_secrets = list({value for value in (*self.runtime_env.values(), *self.provider_env.values()) if value})
        self.stop_child()
        self.start_child()
        try:
            return {"ok": True, "verification": self.wait_healthy(redaction_values=diagnostic_secrets)}
        except Exception as exc:
            self.stop_child()
            restore()
            self.start_child()
            rollback: dict[str, Any] = {"healthy": False}
            try:
                rollback = {"healthy": True, "verification": self.wait_healthy(redaction_values=diagnostic_secrets)}
            except Exception as rollback_exc:
                rollback = {"healthy": False, "error": f"{type(rollback_exc).__name__}: {rollback_exc}"}
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "rolled_back": True, "rollback": rollback}

    def set_profile_model(self, profile_key: str, provider: str, model: str, context_length: Any = None, max_tokens: Any = None, reasoning_effort: Any = None) -> dict[str, Any]:
        """Point one specialist profile at provider/model and restart into it.

        The default profile is applied through Calliope's durable revision
        controller (validated, versioned, verified there); this path is the
        specialists' equivalent: rebase onto the current file, validate with
        the shared policy, atomic write, marker so the reconciler carries the
        choice across image upgrades, then a verified restart that rolls the
        file back if the gateway does not come up healthy."""
        key = str(profile_key or "").strip().lower()
        if key == "default":
            return {
                "ok": False,
                "phase": "durable_controller_required",
                "error": "The default agent's model is applied through Calliope's revision controller.",
            }
        for _attempt in range(3):
            path, payload, current = _profile_config(key)
            try:
                POLICY.apply_model_choice(payload, provider, model, context_length, max_tokens, reasoning_effort)
            except ValueError as exc:
                return {"ok": False, "phase": "validation", "validation": {"valid": False, "errors": [{"path": "model", "message": str(exc)}], "warnings": []}}
            POLICY.reconcile_model_block(payload)
            raw = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8")
            validation = _specialist_config_validation(key, raw)
            if not validation.get("valid"):
                return {"ok": False, "phase": "validation", "validation": validation}
            with self.lock:
                if path.read_bytes() != current:
                    continue
                if raw == current:
                    return {"ok": True, "profile": key, "changed": False, "model": _profile_model_summary(key, payload), "config_hash": _sha(raw), "restart_required": False}
                archive_dir = STATE_DIR / "profile-revisions" / key / "config"
                archive_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write(archive_dir / f"{_sha(current)}.txt", current)
                _atomic_write(path, raw)
                _atomic_write(archive_dir / f"{_sha(raw)}.txt", raw)
                marker = _profile_root(key) / ".calliope-managed" / MODEL_OPERATOR_MARKER
                _atomic_write(marker, b"chosen through Calliope Models & agents\n")

                def restore() -> None:
                    _atomic_write(path, current)

                restart = self._restart_verified(restore)
                if not restart.get("ok"):
                    return {
                        "ok": False,
                        "phase": "verification",
                        "profile": key,
                        "error": restart.get("error"),
                        "rolled_back": True,
                        "rollback": restart.get("rollback"),
                        "model": _profile_model_summary(key),
                    }
                return {
                    "ok": True,
                    "profile": key,
                    "changed": True,
                    "model": _profile_model_summary(key, payload),
                    "config_hash": _sha(raw),
                    "restart_required": False,
                    "verification": restart.get("verification"),
                }
        return {
            "ok": False,
            "phase": "compare_and_swap",
            "error": "profile config kept changing during the update; refresh and try once more",
        }

    def provider_inventory(self, *, refresh: bool = False) -> dict[str, Any]:
        options = _bounded_model_options(_gateway_request(
            "GET",
            "/api/model/options?" + urllib.parse.urlencode({
                "include_unconfigured": "true",
                "refresh": "true" if refresh else "false",
            }),
            timeout=90 if refresh else 30,
        ))
        oauth = {provider: _safe_oauth_status(provider) for provider in _OAUTH_PROVIDERS}
        providers_by_id = {item["id"]: item for item in options["providers"]}
        # The model picker reports Clover as a custom endpoint, but the control
        # plane gives it a stable name. A deployment key remains host-managed;
        # otherwise the same settings surface may provision a durable native-
        # pool key and inject it only into the gateway child environment.
        host_clover = bool(os.environ.get("RVBBIT_CLOVER_KEY"))
        managed_clover = bool(self.provider_env.get("RVBBIT_CLOVER_KEY"))
        clover = providers_by_id.get("clover")
        if clover is None:
            clover = {
                "id": "clover",
                "name": "Clover / Hutch",
                "auth_kind": "environment" if host_clover else "api_key",
                "configured": host_clover or managed_clover,
                "models": ["calliope"],
                "total_models": 1,
                "warning": None,
                "capabilities": {},
            }
            providers_by_id["clover"] = clover
            options["providers"].append(clover)
        clover["configured"] = host_clover or managed_clover
        clover["auth_kind"] = "environment" if host_clover else "api_key"
        clover["environment_managed"] = host_clover
        if clover["configured"]:
            token = os.environ.get("RVBBIT_CLOVER_KEY") or self.provider_env["RVBBIT_CLOVER_KEY"]
            try:
                clover.update(_hutch_model_catalog(token))
                clover["warning"] = None
            except Exception:
                # _sync_inventory rejects this response before making changes,
                # preserving the saved catalog on an outage or invalid payload.
                clover.update(models=[], total_models=0, capabilities={})
                clover["error"] = "Hutch model discovery failed; the saved catalog was kept."
        for provider, status in oauth.items():
            item = providers_by_id.get(provider)
            if item is None:
                item = {
                    "id": provider,
                    "name": provider,
                    "auth_kind": "oauth",
                    "configured": False,
                    "models": [],
                    "total_models": 0,
                    "warning": None,
                    "capabilities": {},
                }
                options["providers"].append(item)
                providers_by_id[provider] = item
            item["configured"] = bool(status["configured"])
            item["oauth"] = status
        openrouter = providers_by_id.get("openrouter")
        if openrouter and os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE"):
            openrouter["environment_managed"] = True
            openrouter["instance_shared"] = True
        if openrouter and openrouter.get("configured"):
            credit = self._provider_credit("openrouter", refresh=refresh)
            if credit:
                openrouter["credit"] = credit
        for item in options["providers"]:
            bench = self._provider_bench(str(item.get("id") or ""))
            if bench:
                item["bench"] = bench
        options["providers"] = sorted(
            options["providers"],
            key=lambda item: _PROVIDER_ORDER.index(item["id"]) if item["id"] in _PROVIDER_ORDER else 99,
        )
        options["ok"] = True
        return options

    def set_profile_models(self, changes: Any, auxiliary: Any = _MISSING) -> dict[str, Any]:
        """Apply specialist model and auxiliary-lane changes in ONE restart.

        The per-profile path restart-verifies each change, so re-pointing the
        whole team meant eight stop/start/health cycles. This validates and
        writes every affected profile first (all-or-nothing: any validation
        failure restores the already-written files and nothing restarts), then
        does a single verified restart whose rollback restores every file.
        The optional auxiliary change touches every specialist in this same
        pass instead of owning a second restart button/path. The default
        profile stays on the durable revision controller.
        """
        if not isinstance(changes, list):
            raise ValueError("changes must be a list")
        auxiliary_present = auxiliary is not _MISSING
        if not changes and not auxiliary_present:
            raise ValueError("changes must be non-empty unless auxiliary is supplied")
        if len(changes) > 256:
            raise ValueError("too many changes in one batch (max 256)")
        cleaned: list[tuple[str, str, str, Any, Any, Any]] = []
        seen: set[str] = set()
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("each change must be an object")
            key = str(change.get("profile") or "").strip().lower()
            if not key:
                raise ValueError("each change needs a profile")
            if key == "default":
                return {
                    "ok": False,
                    "phase": "durable_controller_required",
                    "profile": "default",
                    "error": "The default agent's model is applied through Calliope's revision controller.",
                }
            if key in seen:
                raise ValueError(f"duplicate profile in batch: {key}")
            seen.add(key)
            cleaned.append((
                key,
                str(change.get("provider") or ""),
                str(change.get("model") or ""),
                change.get("context_length"),
                change.get("max_tokens"),
                change.get("reasoning_effort"),
            ))
        auxiliary_provider = None
        auxiliary_model = None
        if auxiliary_present:
            if not isinstance(auxiliary, dict):
                raise ValueError("auxiliary must be an object")
            auxiliary_provider = auxiliary.get("provider")
            auxiliary_model = auxiliary.get("model")
            if bool(auxiliary_provider) != bool(auxiliary_model):
                raise ValueError("auxiliary provider and model must be set together")

        results: list[dict[str, Any]] = []
        written: list[tuple[Path, bytes]] = []
        model_changes = {item[0]: item for item in cleaned}
        target_keys = [item[0] for item in cleaned]
        if auxiliary_present:
            target_keys.extend(key for key in sorted(_profile_keys()) if key not in model_changes)

        def restore_written() -> None:
            for path, current in written:
                _atomic_write(path, current)

        with self.lock:
            # Check every selected profile before writing the first one. A
            # stale editor or a removed agent must not leave half a batch saved.
            expected = {str(item.get("profile") or "").strip().lower(): item.get("expected_hash") for item in changes}
            for key in target_keys:
                path, payload, current = _profile_config(key)
                if expected.get(key) and expected[key] != _sha(current):
                    return {"ok": False, "phase": "validation", "profile": key,
                            "error": "Agent configuration changed; read current settings and review the selection."}
            for key in target_keys:
                path, payload, current = _profile_config(key)
                model_change = model_changes.get(key)
                try:
                    if model_change:
                        _, provider, model, context_length, max_tokens, reasoning_effort = model_change
                        POLICY.apply_model_choice(
                            payload, provider, model, context_length, max_tokens, reasoning_effort,
                        )
                        POLICY.reconcile_model_block(payload)
                    if auxiliary_present:
                        POLICY.apply_auxiliary_settings(payload, auxiliary_provider, auxiliary_model)
                except ValueError as exc:
                    restore_written()
                    area = "model" if model_change else "auxiliary"
                    return {
                        "ok": False, "phase": "validation", "profile": key,
                        "validation": {"valid": False, "errors": [{"path": area, "message": str(exc)}], "warnings": []},
                        "results": results,
                    }
                raw = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8")
                validation = _specialist_config_validation(key, raw)
                if not validation.get("valid"):
                    restore_written()
                    return {"ok": False, "phase": "validation", "profile": key, "validation": validation, "results": results}
                if raw == current:
                    results.append({"profile": key, "changed": False, "model": _profile_model_summary(key, payload), "config_hash": _sha(raw)})
                    continue
                archive_dir = STATE_DIR / "profile-revisions" / key / "config"
                archive_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write(archive_dir / f"{_sha(current)}.txt", current)
                _atomic_write(path, raw)
                _atomic_write(archive_dir / f"{_sha(raw)}.txt", raw)
                marker = _profile_root(key) / ".calliope-managed" / MODEL_OPERATOR_MARKER
                _atomic_write(marker, b"chosen through Calliope Models & agents\n")
                written.append((path, current))
                results.append({"profile": key, "changed": True, "model": _profile_model_summary(key, payload), "config_hash": _sha(raw)})

            if not written:
                return {"ok": True, "changed": False, "restarted": False, "results": results}

            restart = self._restart_verified(restore_written)
            if not restart.get("ok"):
                return {
                    "ok": False,
                    "phase": "verification",
                    "error": restart.get("error"),
                    "rolled_back": True,
                    "rollback": restart.get("rollback"),
                    "results": results,
                }
            return {
                "ok": True,
                "changed": True,
                "restarted": True,
                "results": results,
                "verification": restart.get("verification"),
            }

    def set_profile_fallback(self, provider: Any, model: Any) -> dict[str, Any]:
        """Set (or clear, with provider=model=None) the appliance-wide
        fallback model on EVERY specialist profile with one verified restart.
        The default profile's fallback rides the revision controller."""
        results: list[dict[str, Any]] = []
        written: list[tuple[Path, bytes]] = []

        def restore_written() -> None:
            for path, current in written:
                _atomic_write(path, current)

        with self.lock:
            for key in sorted(_profile_keys()):
                path, payload, current = _profile_config(key)
                try:
                    POLICY.apply_fallback_model(payload, provider, model)
                except ValueError as exc:
                    restore_written()
                    return {"ok": False, "phase": "validation", "profile": key,
                            "validation": {"valid": False, "errors": [{"path": "fallback_model", "message": str(exc)}], "warnings": []}}
                raw = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8")
                validation = _specialist_config_validation(key, raw)
                if not validation.get("valid"):
                    restore_written()
                    return {"ok": False, "phase": "validation", "profile": key, "validation": validation}
                if raw == current:
                    results.append({"profile": key, "changed": False})
                    continue
                _atomic_write(path, raw)
                marker = _profile_root(key) / ".calliope-managed" / MODEL_OPERATOR_MARKER
                _atomic_write(marker, b"chosen through Calliope Models & agents\n")
                written.append((path, current))
                results.append({"profile": key, "changed": True})
            if not written:
                return {"ok": True, "changed": False, "restarted": False, "results": results}
            restart = self._restart_verified(restore_written)
            if not restart.get("ok"):
                return {"ok": False, "phase": "verification", "error": restart.get("error"),
                        "rolled_back": True, "rollback": restart.get("rollback"), "results": results}
            return {"ok": True, "changed": True, "restarted": True, "results": results,
                    "verification": restart.get("verification")}

    def set_profile_auxiliary(self, provider: Any, model: Any) -> dict[str, Any]:
        """Compatibility wrapper; the shared model batch owns the restart."""
        return self.set_profile_models([], {"provider": provider, "model": model})

    def _provider_bench(self, provider: str) -> dict[str, Any] | None:
        """Benched-credential facts for one provider, or None when healthy.

        A 402/429 benches a pool entry for up to an hour (persisted
        last_status/last_status_at in auth.json); after the operator fixes the
        cause externally — adds OpenRouter credits, raises a spend limit —
        nothing on the appliance knows, and every run keeps failing with a
        misleading "no provider" until the TTL lapses (run 11, 2026-08-19).
        Surface the bench so the card can explain it and offer a retry.
        """
        if provider in {"", "clover"}:
            return None
        try:
            from agent.credential_pool import _exhausted_until, load_pool

            pool = load_pool(provider)
            entries = pool.entries() if pool else []
        except Exception:
            return None
        if not entries:
            return None
        benched = []
        for entry in entries:
            status = str(getattr(entry, "last_status", "") or "")
            if status not in {"exhausted", "dead"}:
                continue
            until = None
            try:
                until = _exhausted_until(entry, sole_credential=len(entries) <= 1)
            except Exception:
                until = None
            benched.append({
                "label": str(getattr(entry, "label", "") or "")[:80],
                "status": status,
                "error_code": getattr(entry, "last_error_code", None),
                "message": str(getattr(entry, "last_error_message", "") or "")[:240],
                "benched_until": until,
            })
        if not benched or len(benched) < len(entries):
            # At least one entry still rotates: not blocked, no banner.
            return None
        return {"entries": benched, "all_benched": True}

    def clear_provider_bench(self, provider: str) -> dict[str, Any]:
        """Reset benched pool entries for one provider so the next call retries
        now instead of waiting out the TTL. For after the operator fixed the
        cause externally (credits added, limit raised)."""
        normalized = str(provider or "").strip().lower()
        if normalized not in _MANAGED_PROVIDER_IDS:
            raise ValueError(f"unknown provider: {provider!r}")
        from agent.credential_pool import load_pool

        pool = load_pool(normalized)
        cleared = []
        if pool is not None:
            # Mutate under the pool's own lock and persist through its writer;
            # runs load the pool fresh from auth.json, so a disk-level clear
            # takes effect on the next call without a gateway restart.
            with pool._lock:  # noqa: SLF001 — no public status-reset API upstream
                for entry in pool._entries:  # noqa: SLF001
                    if str(getattr(entry, "last_status", "") or "") not in {"exhausted", "dead"}:
                        continue
                    entry.last_status = "ok"
                    entry.last_status_at = None
                    entry.last_error_code = None
                    entry.last_error_reason = None
                    entry.last_error_message = None
                    entry.last_error_reset_at = None
                    cleared.append(str(getattr(entry, "label", "") or getattr(entry, "id", "")))
                if cleared:
                    pool._persist()  # noqa: SLF001
        if cleared:
            # The gateway runs as the hermes user; if this process is root the
            # rewritten store must not strand a root-owned auth.json (the
            # gateway then reports "No LLM provider configured").
            try:
                auth_path = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "auth.json")
                stat = os.stat(auth_path)
                child_uid = stat.st_uid
                for candidate in (os.environ.get("HERMES_UID"), "10000"):
                    if candidate:
                        child_uid = int(candidate)
                        break
                if stat.st_uid != child_uid and os.geteuid() == 0:
                    os.chown(auth_path, child_uid, child_uid)
            except Exception:
                pass
        self.credit_cache.pop(normalized, None)
        return {"ok": True, "provider": normalized, "cleared": cleared}

    def _provider_credit(self, provider: str, *, refresh: bool = False) -> dict[str, Any] | None:
        """Return bounded billing facts; credentials remain in Hermes' native pool."""
        if provider != "openrouter":
            return None
        now = time.monotonic()
        cached = self.credit_cache.get(provider)
        if cached and not refresh and now - cached[0] < 60:
            return cached[1]
        try:
            from agent.credential_pool import load_pool
            entry = load_pool(provider).select()
            token = (
                getattr(entry, "runtime_api_key", None)
                or getattr(entry, "access_token", None)
                or ""
            ) if entry is not None else ""
            if os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE"):
                token = self._calliope_pool_token("openrouter")
            if not token:
                return None
            request = urllib.request.Request(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.load(response)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return None

            def number(name: str) -> float | None:
                value = data.get(name)
                return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

            result = {
                "usage": number("usage"),
                "limit": number("limit"),
                "remaining": number("limit_remaining"),
                "is_free_tier": bool(data.get("is_free_tier")),
            }
            self.credit_cache[provider] = (now, result)
            return result
        except Exception:
            # Billing telemetry is supplementary and must never make provider
            # selection or inference unavailable.
            return None

    @staticmethod
    def _provider_base_url(provider: str) -> str:
        if provider == "openrouter":
            from hermes_constants import OPENROUTER_BASE_URL
            return str(OPENROUTER_BASE_URL)
        from hermes_cli.auth import PROVIDER_REGISTRY
        config = PROVIDER_REGISTRY.get(provider)
        return str(config.inference_base_url if config else "")

    def sync_instance_key(self, *, restart: bool = True) -> dict[str, Any]:
        """Register the instance key in Hermes' native pool and child environment.

        Startup calls this before launching the child. UI changes call it immediately;
        the existing reconciliation loop retries a missed/failed synchronization.
        """
        path = os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE")
        if not path:
            return {"ok": True, "shared": False, "changed": False, "restarted": False}
        with self.lock:
            try:
                with open(path, encoding="utf-8") as stream:
                    token = stream.read(65537).strip()
            except (OSError, UnicodeError):
                raise ValueError("The shared instance OpenRouter key could not be read.") from None
            if not token or len(token.encode()) > 65536 or any(c.isspace() for c in token):
                raise ValueError("The shared instance OpenRouter key is missing or invalid.")
            changed = self._calliope_pool_token("openrouter") != token
            if changed:
                self.install_provider_key("openrouter", token, instance_shared=True)
                self.instance_key_restart_pending = True
            needs_restart = restart and (self.instance_key_restart_pending
                                         or self.provider_env.get("OPENROUTER_API_KEY") != token)
            if needs_restart:
                previous_secrets = list(self.provider_env.values())
                self.instance_key_restart_pending = True
                self.stop_child()
                self.start_child()
                try:
                    self.wait_healthy(timeout=90, redaction_values=[token, *previous_secrets])
                except Exception:
                    raise RuntimeError("The instance key is registered, but Hermes has not passed its restart health check. Retry synchronization.") from None
                self.instance_key_restart_pending = False
            elif not restart:
                # The caller launches the initial child immediately after this.
                self.instance_key_restart_pending = False
            return {"ok": True, "shared": True, "configured": True,
                    "changed": changed, "restarted": bool(needs_restart)}

    def install_provider_key(self, provider: str, api_key: str, *, instance_shared: bool = False) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        token = str(api_key or "").strip()
        if normalized not in _PROVIDER_KEY_ENV:
            raise ValueError("provider does not accept a managed API key")
        if normalized == "clover" and os.environ.get("RVBBIT_CLOVER_KEY"):
            raise ValueError("Clover is managed by the host environment")
        if normalized == "openrouter" and os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE") and not instance_shared:
            raise ValueError("OpenRouter uses the shared instance key. Change it in System settings.")
        if not token or len(token.encode("utf-8")) > 64 * 1024:
            raise ValueError("API key is empty or too large")

        validation = _probe_provider_key(normalized, token)
        if instance_shared and not validation.get("ok"):
            raise ValueError("OpenRouter could not validate the shared instance key. Hermes credentials have not changed.")
        if validation.get("reachable") and not validation.get("ok"):
            raise ValueError(str(validation.get("message") or "provider rejected the API key")[:800])

        from agent.credential_pool import (
            AUTH_TYPE_API_KEY,
            PooledCredential,
            load_pool,
        )
        pool = load_pool(normalized)
        prior_ids = {
            entry.id for entry in pool.entries()
            if entry.source == _CALLIOPE_POOL_SOURCE or entry.label == _CALLIOPE_POOL_LABEL
        }
        added = pool.add_entry(PooledCredential(
            provider=normalized,
            id=uuid.uuid4().hex[:6],
            label=_CALLIOPE_POOL_LABEL,
            auth_type=AUTH_TYPE_API_KEY,
            priority=0,
            source=_CALLIOPE_POOL_SOURCE,
            access_token=token,
            base_url=self._provider_base_url(normalized),
        ))
        if normalized == "clover":
            previous_provider_env = dict(self.provider_env)
            try:
                with self.lock:
                    self.provider_env[_PROVIDER_KEY_ENV[normalized]] = token
                    self.stop_child()
                    self.start_child()
                self.wait_healthy(
                    timeout=90,
                    redaction_values=[token, *previous_provider_env.values()],
                )
            except Exception:
                index, _match, _error = pool.resolve_target(added.id)
                if index is not None:
                    pool.remove_index(index)
                self.provider_env = previous_provider_env
                with self.lock:
                    self.stop_child()
                    self.start_child()
                raise
        # Persist the replacement before removing the prior key so a failed
        # write never leaves the provider empty.
        for entry in list(pool.entries()):
            if entry.id in prior_ids:
                index, _match, _error = pool.resolve_target(entry.id)
                if index is not None:
                    pool.remove_index(index)
        return {
            "ok": True,
            "provider": normalized,
            "credential_id": added.id,
            "credential_label": added.label,
            "configured": True,
            "validation": {
                "reachable": bool(validation.get("reachable")),
                "verified": bool(validation.get("ok")),
                "message": str(validation.get("message") or "")[:400] or None,
            },
        }

    def remove_provider_key(self, provider: str) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        if normalized not in _PROVIDER_KEY_ENV:
            raise ValueError("provider does not accept a managed API key")
        if normalized == "clover" and os.environ.get("RVBBIT_CLOVER_KEY"):
            raise ValueError("Clover is managed by the host environment")
        if normalized == "openrouter" and os.environ.get("CALLIOPE_OPENROUTER_KEY_FILE"):
            raise ValueError("OpenRouter uses the shared instance key and cannot be disconnected separately.")
        from agent.credential_pool import load_pool
        pool = load_pool(normalized)
        removed = 0
        for entry in list(pool.entries()):
            if entry.source == _CALLIOPE_POOL_SOURCE or entry.label == _CALLIOPE_POOL_LABEL:
                index, _match, _error = pool.resolve_target(entry.id)
                if index is not None and pool.remove_index(index) is not None:
                    removed += 1
        if normalized == "clover":
            self.provider_env.pop(_PROVIDER_KEY_ENV[normalized], None)
            with self.lock:
                self.stop_child()
                self.start_child()
        return {"ok": True, "provider": normalized, "configured": False, "removed": removed}

    def start_provider_oauth(self, provider: str) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        if normalized not in _OAUTH_PROVIDERS:
            raise ValueError("provider does not support hosted OAuth")
        self._expire_oauth_jobs()
        session = uuid.uuid4().hex
        code_entry = normalized in _CODE_ENTRY_PROVIDERS
        if normalized in _CLI_LOGIN_PROVIDERS:
            # The Claude Code CLI, with stdin as a pipe (non-TTY), prints the
            # authorize URL and then blocks on "Paste code here if prompted >".
            # Never `--console`: that is API billing, not the subscription.
            command = _claude_cli_command()
            if command is None:
                raise ValueError(_CLI_LOGIN_INSTALL_HINT)
            argv = command + ["auth", "login"]
            env = _claude_cli_env()
        else:
            argv = [
                "/opt/hermes/.venv/bin/hermes", "auth", "add", normalized,
                "--type", "oauth", "--label", _CALLIOPE_POOL_LABEL,
                "--no-browser",
            ]
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            argv,
            cwd=HERMES_HOME,
            # A PKCE login waits on stdin for the pasted authorization code;
            # device-code logins must never read stdin.
            stdin=subprocess.PIPE if code_entry else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        job: dict[str, Any] = {
            "provider": normalized,
            "process": process,
            "created_at": time.monotonic(),
            "status": "pending",
            "flow": "authorization_code" if code_entry else "device_code",
            "verification_url": None,
            "user_code": None,
            "message": "Starting secure sign-in…",
            "expect_code": False,
            "awaiting_code": False,
        }
        with self.oauth_lock:
            self.oauth_jobs[session] = job
        threading.Thread(
            target=self._read_oauth_job,
            args=(session,),
            daemon=True,
            name=f"oauth-{normalized}-{session[:6]}",
        ).start()

        # Device-code setup is normally immediate. Give the reader a short
        # chance to surface the actionable URL/code in this first response.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            with self.oauth_lock:
                ready = bool(job.get("verification_url")) and (
                    bool(job.get("user_code")) or job.get("flow") == "authorization_code"
                )
                terminal = job.get("status") in {"approved", "error", "cancelled"}
            if ready or terminal:
                break
            time.sleep(0.1)
        return self._oauth_job_response(session, job)

    def _read_oauth_job(self, session: str) -> None:
        with self.oauth_lock:
            job = self.oauth_jobs.get(session)
        if not job:
            return
        process = job["process"]
        stream = process.stdout
        if stream is not None:
            for raw_line in iter(stream.readline, ""):
                with self.oauth_lock:
                    fields = _oauth_line_fields(raw_line, expect_code=bool(job.get("expect_code")))
                    for key in ("verification_url", "user_code", "message"):
                        if fields.get(key):
                            job[key] = fields[key]
                    job["expect_code"] = bool(fields.get("expect_code"))
                    if job.get("flow") == "authorization_code" and job.get("verification_url") and job.get("status") == "pending":
                        # The CLI is now blocked on its code prompt ("Paste code
                        # here if prompted > ") — a line without a newline that
                        # this reader never sees; the URL line is the signal.
                        job["awaiting_code"] = True
                        job["message"] = "Sign in with your Claude subscription, then paste the code Anthropic shows you."
            stream.close()
        return_code = process.wait()
        with self.oauth_lock:
            if job.get("status") == "cancelled":
                return
            status = _safe_oauth_status(job["provider"])
            if return_code == 0 and status.get("configured"):
                job["status"] = "approved"
                job["message"] = "Provider connected."
            else:
                job["status"] = "error"
                job["message"] = job.get("message") or "Hermes could not complete sign-in."

    def _expire_oauth_jobs(self) -> None:
        now = time.monotonic()
        with self.oauth_lock:
            for session, job in list(self.oauth_jobs.items()):
                if now - float(job.get("created_at") or 0) <= _OAUTH_JOB_TTL_SECONDS:
                    continue
                process = job.get("process")
                if process is not None and process.poll() is None:
                    process.terminate()
                self.oauth_jobs.pop(session, None)

    def submit_provider_oauth_code(self, provider: str, session_id: str, code: str) -> dict[str, Any]:
        """Hand the pasted authorization code to the waiting PKCE login."""
        normalized = str(provider or "").strip().lower()
        session = str(session_id or "").strip()
        clean = str(code or "").strip()
        if normalized not in _CODE_ENTRY_PROVIDERS:
            raise ValueError("provider does not take a pasted authorization code")
        if not clean or len(clean) > 512 or not re.fullmatch(r"[A-Za-z0-9._#~-]+", clean):
            raise ValueError("authorization code is empty or malformed")
        with self.oauth_lock:
            job = self.oauth_jobs.get(session)
            if not job or job.get("provider") != normalized:
                raise ValueError("hosted OAuth session was not found or expired")
            process = job.get("process")
            if job.get("status") != "pending" or process is None or process.poll() is not None or process.stdin is None:
                raise ValueError("this sign-in is no longer waiting for a code")
            try:
                process.stdin.write(clean + "\n")
                process.stdin.flush()
                process.stdin.close()
            except (OSError, ValueError) as exc:
                raise ValueError(f"could not hand the code to Hermes: {exc}") from exc
            job["awaiting_code"] = False
            job["status"] = "exchanging"
            job["message"] = "Exchanging the code for a subscription grant…"
            return self._oauth_job_response(session, job)

    @staticmethod
    def _oauth_job_response(session: str, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": job.get("status") != "error",
            "provider": str(job.get("provider") or ""),
            "session_id": session,
            "flow": str(job.get("flow") or "device_code"),
            "awaiting_code": bool(job.get("awaiting_code")),
            "status": str(job.get("status") or "pending"),
            "user_code": str(job.get("user_code") or "")[:80] or None,
            "verification_url": str(job.get("verification_url") or "")[:2000] or None,
            "expires_in": max(0, int(_OAUTH_JOB_TTL_SECONDS - (time.monotonic() - float(job.get("created_at") or 0)))),
            "poll_interval": 3,
            "message": str(job.get("message") or "")[:500] or None,
        }

    def poll_provider_oauth(self, provider: str, session_id: str) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        session = str(session_id or "").strip()
        if normalized not in _OAUTH_PROVIDERS or not session or len(session) > 240:
            raise ValueError("invalid hosted OAuth session")
        self._expire_oauth_jobs()
        with self.oauth_lock:
            job = self.oauth_jobs.get(session)
            if not job or job.get("provider") != normalized:
                raise ValueError("hosted OAuth session was not found or expired")
            return self._oauth_job_response(session, job)

    def cancel_provider_oauth(self, provider: str, session_id: str) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        session = str(session_id or "").strip()
        with self.oauth_lock:
            job = self.oauth_jobs.get(session)
            if normalized not in _OAUTH_PROVIDERS or not job or job.get("provider") != normalized:
                raise ValueError("hosted OAuth session was not found or expired")
            process = job.get("process")
            if process is not None and process.poll() is None:
                process.terminate()
            job["status"] = "cancelled"
            job["message"] = "Sign-in cancelled."
            return self._oauth_job_response(session, job)

    def disconnect_provider_oauth(self, provider: str) -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        if normalized not in _OAUTH_PROVIDERS:
            raise ValueError("provider does not support hosted OAuth")
        if normalized in _CLI_LOGIN_PROVIDERS:
            result = _claude_cli_logout()
            return {
                "ok": True,
                "provider": normalized,
                "configured": False,
                "cleared": bool(result.get("cleared")),
                "error": result.get("error"),
            }
        from hermes_cli.auth import clear_provider_auth
        cleared = bool(clear_provider_auth(normalized))
        return {
            "ok": True,
            "provider": normalized,
            "configured": False,
            "cleared": cleared,
        }

    @staticmethod
    def _runtime_environment(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 64:
            raise ValueError("resolved_env must be an object with at most 64 entries")
        clean: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name or "")
            secret = str(raw_value or "")
            if not name or not name.replace("_", "A").isalnum() or not name[0].isalpha():
                raise ValueError("resolved_env contains an invalid environment variable name")
            if len(name) > 128 or not secret or len(secret.encode("utf-8")) > 64 * 1024:
                raise ValueError("resolved_env contains an empty or oversized value")
            clean[name] = secret
        return clean

    def apply(
        self,
        raw: bytes,
        expected_hash: str | None,
        revision_id: str | None,
        resolved_env: Any = None,
    ) -> dict[str, Any]:
        validation = validate_candidate(raw)
        if not validation["valid"]:
            return {"ok": False, "phase": "validation", "validation": validation}
        with self.lock:
            current = CONFIG_PATH.read_bytes()
            current_hash = _sha(current)
            previous_runtime_env = dict(self.runtime_env)
            if expected_hash and expected_hash != current_hash:
                return {
                    "ok": False,
                    "phase": "compare_and_swap",
                    "error": "active config changed after the candidate was prepared",
                    "active_config_hash": current_hash,
                }
            candidate_hash = _sha(raw)
            archive = ARCHIVE_DIR / f"{current_hash}.yaml"
            if not archive.exists():
                _atomic_write(archive, current)
            _atomic_write(CONFIG_PATH, raw)
            self.runtime_env = self._runtime_environment(resolved_env)
            diagnostic_secrets = list(
                {value for value in (*previous_runtime_env.values(), *self.runtime_env.values()) if value}
            )
            self.stop_child()
            self.start_child()
            try:
                verification = self.wait_healthy(redaction_values=diagnostic_secrets)
            except Exception as exc:
                self.stop_child()
                _atomic_write(CONFIG_PATH, current)
                self.runtime_env = previous_runtime_env
                self.start_child()
                rollback = {"healthy": False}
                try:
                    rollback = {
                        "healthy": True,
                        "verification": self.wait_healthy(
                            redaction_values=diagnostic_secrets
                        ),
                    }
                except Exception as rollback_exc:
                    rollback = {"healthy": False, "error": f"{type(rollback_exc).__name__}: {rollback_exc}"}
                return {
                    "ok": False,
                    "phase": "verification",
                    "error": f"{type(exc).__name__}: {exc}",
                    "rolled_back": True,
                    "active_config_hash": current_hash,
                    "rollback": rollback,
                    "revision_id": revision_id,
                }
            _atomic_write(ARCHIVE_DIR / f"{candidate_hash}.yaml", raw)
            return {
                "ok": True,
                "phase": "complete",
                "active_config_hash": candidate_hash,
                "previous_config_hash": current_hash,
                "revision_id": revision_id,
                "verification": verification,
            }

    def reconcile_generated_profiles(self) -> dict[str, Any]:
        """Install new domain profiles, then reload only at an idle gateway.

        SQL keeps them in provisioning until its independent API probe passes.
        Preserve the controller's resolved credential bindings across reloads.
        """
        with self.lock:
            health = self._gateway_probe()
            if health.get('gateway_busy') is not False or health.get('active_agents') != 0:
                return {'status': 'waiting_for_idle'}
            module = runpy.run_path(str(POLICY_PATH.with_name('reconcile-hermes-specialists.py')))
            generated = module['_fetch_generated_specialists'](HERMES_HOME)
            current = CONFIG_PATH.read_bytes()
            config = yaml.safe_load(current) or {}
            allowed = set((config.get('gateway') or {}).get('multiplex_profile_allowlist') or [])
            missing = [item['specialist_key'] for item in generated
                       if item['specialist_key'] not in allowed
                       or not all((HERMES_HOME/'profiles'/item['specialist_key']/name).is_file()
                                  for name in ('config.yaml','profile.yaml','SOUL.md','.env'))]
            # 0.21.4 serves every directory under profiles/, so a generated profile retired
            # after boot keeps costing startup time until the reconciler archives it; the
            # reconciler records retired keys in its durable cache, so treat one still on
            # disk as work for this loop (2026-09-23).
            stale = []
            try:
                cache = json.loads((HERMES_HOME/'.calliope-seeds'/'generated-specialists.json').read_text(encoding='utf-8'))
                stale = [key for key in (cache.get('retired') or []) if isinstance(key, str)
                         and (HERMES_HOME/'profiles'/key).is_dir()]
            except Exception:
                stale = []
            if not missing and not stale:
                return {'status': 'current'}
            module['reconcile'](HERMES_HOME)
            POLICY.reconcile_payload(config)
            # Recheck after disk/network work: don't knowingly interrupt a turn.
            health = self._gateway_probe()
            if health.get('gateway_busy') is not False or health.get('active_agents') != 0:
                return {'status': 'waiting_for_idle'}
            result = self.apply(yaml.safe_dump(config, sort_keys=False).encode(),
                                _sha(current), 'generated-profiles', dict(self.runtime_env))
            return {'status': 'reloaded' if result.get('ok') else 'retry',
                    'profiles': missing, 'ok': bool(result.get('ok'))}

    def _generated_profiles_loop(self) -> None:
        while not self.stopping:
            time.sleep(60)
            if self.stopping:
                break
            try:
                self.sync_instance_key()
            except Exception as exc:
                print('[calliope-control] instance key synchronization pending: ' + type(exc).__name__, flush=True)
            try:
                result = self.reconcile_generated_profiles()
                if result['status'] not in ('current', 'waiting_for_idle'):
                    print('[calliope-control] generated profiles: ' + json.dumps(result), flush=True)
            except Exception as exc:
                print('[calliope-control] generated profile reconciliation failed: '
                      + type(exc).__name__, flush=True)

    def monitor(self) -> None:
        try:
            self.sync_instance_key(restart=False)
        except Exception as exc:
            print('[calliope-control] instance key synchronization pending: ' + type(exc).__name__, flush=True)
        self.start_child()
        threading.Thread(target=self._generated_profiles_loop,
                         name='calliope-generated-profiles', daemon=True).start()
        while not self.stopping:
            with self.lock:
                if self.child and self.child.poll() is not None:
                    self.last_exit_code = self.child.returncode
                    print(
                        f"[calliope-control] gateway exited rc={self.last_exit_code}; restarting",
                        flush=True,
                    )
                    time.sleep(1)
                    self.start_child()
            time.sleep(1)

    def shutdown(self) -> None:
        self.stopping = True
        with self.lock:
            self.stop_child()


SUPERVISOR = GatewaySupervisor()


class Handler(BaseHTTPRequestHandler):
    server_version = "CalliopeHermesControl/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[calliope-control] {self.address_string()} {fmt % args}", flush=True)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return bool(CONTROL_KEY) and supplied == f"Bearer {CONTROL_KEY}"

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if size <= 0 or size > (36 * 1024 * 1024 if self.path in {"/v1/speech/transcriptions", "/v1/reviewed-mail/send"} else MAX_CONFIG_BYTES * 2):
            raise ValueError("request body is empty or too large")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/health":
                self._reply(200, SUPERVISOR.status())
            elif parsed.path == "/v1/config":
                self._reply(200, SUPERVISOR.current_config())
            elif parsed.path == "/v1/profiles":
                self._reply(200, SUPERVISOR.profiles())
            elif parsed.path == "/v1/profile":
                profile = str((query.get("profile") or ["default"])[0])
                self._reply(200, SUPERVISOR.profile(profile))
            elif parsed.path == "/v1/profile/skill":
                profile = str((query.get("profile") or ["default"])[0])
                skill = str((query.get("skill") or [""])[0])
                self._reply(200, SUPERVISOR.profile_skill(profile, skill))
            elif parsed.path == "/v1/profile/document":
                profile = str((query.get("profile") or ["default"])[0])
                kind = str((query.get("document") or [""])[0])
                self._reply(200, SUPERVISOR.profile_document(profile, kind))
            elif parsed.path == "/v1/speech":
                import speech_bridge
                self._reply(200, speech_bridge.settings())
            elif parsed.path == "/v1/providers":
                refresh = str((query.get("refresh") or ["false"])[0]).lower() in {"1", "true", "yes"}
                self._reply(200, SUPERVISOR.provider_inventory(refresh=refresh))
            elif parsed.path == "/v1/providers/oauth/poll":
                provider = str((query.get("provider") or [""])[0])
                session_id = str((query.get("session_id") or [""])[0])
                self._reply(200, SUPERVISOR.poll_provider_oauth(provider, session_id))
            else:
                self._reply(404, {"error": "not found"})
        except ValueError as exc:
            self._reply(400, {"error": str(exc)})
        except Exception as exc:
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            body = self._body()
            raw = str(body.get("config_yaml") or "").encode("utf-8")
            if self.path in {"/v1/reviewed-mail/probe", "/v1/reviewed-mail/poll", "/v1/reviewed-mail/send"}:
                import reviewed_mail_bridge
                try:
                    result = getattr(reviewed_mail_bridge, self.path.rsplit('/', 1)[-1])(body)
                    self._reply(200, result)
                except Exception:
                    self._reply(503, {"error": "Reviewed email transport unavailable"})
            elif self.path in {"/v1/sms/probe", "/v1/sms/validate", "/v1/sms/media", "/v1/sms/format_reply", "/v1/sms/send"}:
                import sms_bridge
                try:
                    self._reply(200, getattr(sms_bridge, self.path.rsplit('/', 1)[-1])(body))
                except Exception:
                    self._reply(503, {"error": "SMS transport unavailable"})
            elif self.path in {"/v1/speech", "/v1/speech/realtime", "/v1/speech/transcriptions"}:
                import speech_bridge
                action = {"/v1/speech": speech_bridge.save, "/v1/speech/realtime": speech_bridge.realtime,
                          "/v1/speech/transcriptions": speech_bridge.transcribe}[self.path]
                self._reply(200, action(body))
            elif self.path == "/v1/validate":
                result = validate_candidate(raw)
                self._reply(200 if result["valid"] else 422, result)
            elif self.path == "/v1/reconcile":
                result = reconcile_candidate(raw)
                self._reply(200 if result["valid"] else 422, result)
            elif self.path == "/v1/apply":
                result = SUPERVISOR.apply(
                    raw,
                    str(body.get("expected_hash") or "") or None,
                    str(body.get("revision_id") or "") or None,
                    body.get("resolved_env"),
                )
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/fallback":
                result = SUPERVISOR.set_profile_fallback(body.get("provider"), body.get("model"))
                # The branch computed the result and returned without answering, so the
                # client saw "Remote end closed connection without response" after the
                # verified restart had already applied the fallback to every specialist.
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/auxiliary":
                result = SUPERVISOR.set_profile_auxiliary(body.get("provider"), body.get("model"))
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/models":
                result = SUPERVISOR.set_profile_models(
                    body.get("changes"),
                    body["auxiliary"] if "auxiliary" in body else _MISSING,
                )
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/model":
                result = SUPERVISOR.set_profile_model(
                    str(body.get("profile") or ""),
                    str(body.get("provider") or ""),
                    str(body.get("model") or ""),
                    body.get("context_length"),
                    body.get("max_tokens"),
                    body.get("reasoning_effort"),
                )
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/skill":
                result = SUPERVISOR.toggle_profile_skill(
                    str(body.get("profile") or "default"),
                    str(body.get("skill") or ""),
                    bool(body.get("enabled")),
                )
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/profile/validate":
                result = SUPERVISOR.validate_profile_document(
                    str(body.get("profile") or "default"),
                    str(body.get("document") or ""),
                    str(body.get("content") or ""),
                )
                self._reply(200 if result.get("valid") else 422, result)
            elif self.path == "/v1/profile/document":
                result = SUPERVISOR.save_profile_document(
                    str(body.get("profile") or "default"),
                    str(body.get("document") or ""),
                    str(body.get("content") or ""),
                    str(body.get("expected_hash") or "") or None,
                )
                self._reply(200 if result.get("ok") else 409, result)
            elif self.path == "/v1/providers/instance-key":
                self._reply(200, SUPERVISOR.sync_instance_key())
            elif self.path == "/v1/providers/key":
                result = SUPERVISOR.install_provider_key(
                    str(body.get("provider") or ""),
                    str(body.get("api_key") or ""),
                )
                self._reply(200, result)
            elif self.path == "/v1/providers/oauth/code":
                self._reply(200, SUPERVISOR.submit_provider_oauth_code(
                    str(body.get("provider") or ""),
                    str(body.get("session_id") or ""),
                    str(body.get("code") or ""),
                ))
            elif self.path == "/v1/providers/oauth/start":
                result = SUPERVISOR.start_provider_oauth(str(body.get("provider") or ""))
                self._reply(200, result)
            elif self.path == "/v1/providers/bench/clear":
                self._reply(200, SUPERVISOR.clear_provider_bench(
                    str(body.get("provider") or "")
                ))
            else:
                self._reply(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._reply(400, {"error": str(exc)})
        except Exception as exc:
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            provider = str((query.get("provider") or [""])[0])
            if parsed.path == "/v1/providers/key":
                self._reply(200, SUPERVISOR.remove_provider_key(provider))
            elif parsed.path == "/v1/providers/oauth":
                result = SUPERVISOR.disconnect_provider_oauth(provider)
                self._reply(200 if result.get("ok") else 409, result)
            elif parsed.path == "/v1/providers/oauth/session":
                session_id = str((query.get("session_id") or [""])[0])
                result = SUPERVISOR.cancel_provider_oauth(provider, session_id)
                self._reply(200 if result.get("ok") else 409, result)
            else:
                self._reply(404, {"error": "not found"})
        except ValueError as exc:
            self._reply(400, {"error": str(exc)})
        except Exception as exc:
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})


STANDING_JOBS_URL = os.environ.get(
    "CALLIOPE_STANDING_JOBS_URL",
    "http://calliope:8765/api/calliope/standing-jobs",
)
STANDING_JOBS_KEY = os.environ.get("WAREHOUSE_HERMES_MCP_KEY", "")
STANDING_JOBS_INTERVAL = int(os.environ.get("CALLIOPE_STANDING_JOBS_INTERVAL", "60"))
STANDING_JOB_PREFIX = "thread-"


def _standing_default_model_pin() -> tuple[str | None, str | None]:
    """Pin standing jobs to the default profile's model so Hermes' cron
    drift guard (silently skips UNPINNED jobs when the global inference
    config changes) can never dead-stop a user's heartbeat — the exact
    failure mode of the 2026-09-03 duty-cycle incident."""
    try:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        provider = str(model.get("provider") or "").strip() or None
        name = str(model.get("default") or model.get("name") or "").strip() or None
        return (provider, name) if provider and name else (None, None)
    except Exception:  # noqa: BLE001 — unpinned beats crashed
        return (None, None)


def _standing_jobs_reconcile_once() -> None:
    request = urllib.request.Request(
        f"{STANDING_JOBS_URL}/desired",
        headers={"Authorization": f"Bearer {STANDING_JOBS_KEY}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        desired_rows = json.loads(response.read().decode("utf-8")).get("jobs") or []
    desired = {
        str(row.get("job_name") or ""): row
        for row in desired_rows
        if isinstance(row, dict) and str(row.get("job_name") or "").startswith(STANDING_JOB_PREFIX)
    }

    hermes_root = Path(__file__).resolve().parent.parent
    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))
    from cron.jobs import create_job, list_jobs, update_job

    provider, model = _standing_default_model_pin()
    existing = {
        str(job.get("name") or ""): job
        for job in list_jobs(include_disabled=True)
        if str(job.get("name") or "").startswith(STANDING_JOB_PREFIX)
    }
    acks: list[dict[str, str]] = []
    for name, row in desired.items():
        spec: dict[str, Any] = {
            "prompt": str(row.get("prompt") or ""),
            "schedule": str(row.get("schedule") or ""),
            "skills": [],
            "deliver": "local",
            "enabled": bool(row.get("enabled")),
            "attach_to_session": False,
        }
        if provider and model:
            spec["provider"] = provider
            spec["model"] = model
        current = existing.get(name)
        if current is None:
            create_job(
                prompt=spec["prompt"],
                schedule=spec["schedule"],
                name=name,
                skills=[],
                deliver="local",
                attach_to_session=False,
                provider=provider,
                model=model,
            )
            current = next(
                (job for job in list_jobs(include_disabled=True)
                 if str(job.get("name") or "") == name),
                None,
            )
            if current is not None and not spec["enabled"]:
                update_job(current["id"], {"enabled": False})
        else:
            drifted = (
                str(current.get("prompt") or "") != spec["prompt"]
                or str(current.get("schedule") or "") != spec["schedule"]
                or bool(current.get("enabled", True)) != spec["enabled"]
                or (provider and str(current.get("provider") or "") != provider)
                or (model and str(current.get("model") or "") != model)
            )
            if drifted:
                update_job(current["id"], spec)
        if current is not None:
            job_id = str(current.get("id") or "")
            if job_id and str(row.get("hermes_job_id") or "") != job_id:
                acks.append({"id": str(row.get("id") or ""), "hermes_job_id": job_id})
    # Canon rows deleted (cancelled) leave orphaned thread-* jobs: disable.
    for name, job in existing.items():
        if name not in desired and job.get("enabled", True):
            update_job(job["id"], {"enabled": False})
    if acks:
        payload = json.dumps({"acks": acks}).encode("utf-8")
        ack_request = urllib.request.Request(
            f"{STANDING_JOBS_URL}/ack",
            data=payload,
            headers={
                "Authorization": f"Bearer {STANDING_JOBS_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(ack_request, timeout=20):
            pass


def standing_jobs_loop() -> None:
    """Converge the default cron store to Calliope's session-schedule canon
    (0398). Canon declares; this loop, living inside the Hermes container
    where jobs.json is writable and the cron package importable, reconciles."""
    if not STANDING_JOBS_KEY:
        print("[standing-jobs] WAREHOUSE_HERMES_MCP_KEY missing; loop disabled", flush=True)
        return
    time.sleep(45)
    while True:
        try:
            _standing_jobs_reconcile_once()
        except Exception as exc:  # noqa: BLE001 — retried next interval
            print(f"[standing-jobs] reconcile failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(20, STANDING_JOBS_INTERVAL))


def _find_full_chromium() -> str | None:
    """Prefer image-owned Chromium; retain legacy Playwright installations."""
    import glob
    for candidate in (os.environ.get("CALLIOPE_CHROMIUM_PATH", ""),
                      "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    candidates = sorted(
        glob.glob("/opt/data/playwright/chromium-*/chrome-linux*/chrome")
        + glob.glob("/opt/data/.cache/playwright/chromium-*/chrome-linux*/chrome")
    )
    return candidates[-1] if candidates else None


def _cdp_alive() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as response:
            return bool(json.loads(response.read()).get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001 — any failure means "not serving CDP"
        return False


def _chromium_profile_lock_is_live(profile_dir: Path) -> bool:
    """Return whether SingletonLock names a live Chromium in this container."""
    lock_path = profile_dir / "SingletonLock"
    try:
        owner = os.readlink(lock_path)
        hostname, pid_text = owner.rsplit("-", 1)
        pid = int(pid_text)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False
    if hostname != socket.gethostname():
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"chrome" in command and os.fsencode(str(profile_dir)) in command


def _clear_stale_chromium_profile_locks(profile_dir: Path) -> bool:
    """Remove only singleton markers not owned by a live local Chromium.

    The profile is durable but the Docker hostname is not. A normal container
    replacement therefore leaves Chromium's symlinks pointing at an
    unreachable process on the previous hostname, and Chromium refuses to
    start until those markers are removed.
    """
    if _chromium_profile_lock_is_live(profile_dir):
        return False
    removed = False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
        try:
            (profile_dir / name).unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


def _stop_browser_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def browser_keeper_loop() -> None:
    """Keep a Chromium CDP endpoint alive on :9222 for agents' browser_exec.

    The Browser Use harness attaches via BU_CDP_URL (set in compose) or, as a
    fallback, by scanning $HOME/.config profile locks. The gateway and this
    supervisor run as the hermes user (HOME=/opt/data), so the profile lives
    at ~/.config/chromium — a name on the harness's scan list AND on the
    persistent volume. Headless=new still writes SingletonLock/DevToolsActivePort
    there; --no-sandbox because there is no user namespace in the container.

    The loop's contract is the ENDPOINT, not the process: if any healthy
    chrome already serves :9222 (an operator's, a test's), leave it alone —
    launching a second instance would just lose the port bind race."""
    profile_dir = Path(os.path.expanduser("~/.config/chromium"))
    process: subprocess.Popen[bytes] | None = None
    while True:
        try:
            if _cdp_alive():
                time.sleep(5)
                continue
            chrome = _find_full_chromium()
            if not chrome:
                print("[browser-keeper] Chromium missing; retrying in 30s", flush=True)
                time.sleep(30)
                continue
            args = [
                chrome, "--no-sandbox", "--disable-gpu", "--headless=new",
                "--disable-dev-shm-usage", "--remote-debugging-port=9222",
                "--user-data-dir=" + str(profile_dir), "about:blank",
            ]
            if process is not None and process.poll() is None:
                _stop_browser_process(process)
            process = None
            profile_dir.mkdir(parents=True, exist_ok=True)
            if _clear_stale_chromium_profile_locks(profile_dir):
                print("[browser-keeper] cleared stale Chromium profile lock", flush=True)
            process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                if _cdp_alive():
                    break
                time.sleep(0.25)
            if _cdp_alive():
                print(f"[browser-keeper] chromium ready (pid {process.pid}, cdp :9222)", flush=True)
            else:
                return_code = process.poll()
                if return_code is None:
                    _stop_browser_process(process)
                    return_code = process.returncode
                print(
                    f"[browser-keeper] chromium failed readiness (rc={return_code}); retrying",
                    flush=True,
                )
                process = None
        except Exception as exc:  # noqa: BLE001 — retried next interval
            print(f"[browser-keeper] launch failed: {type(exc).__name__}: {exc}", flush=True)
            process = None
        time.sleep(5)


def main() -> int:
    if not CONTROL_KEY:
        raise RuntimeError("HERMES_CONTROL_KEY or API_SERVER_KEY is required")
    server = ThreadingHTTPServer((CONTROL_HOST, CONTROL_PORT), Handler)
    server.daemon_threads = True

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()
        SUPERVISOR.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    from calliope_browser_origin import start_from_env
    browser_origin = start_from_env()
    monitor = threading.Thread(target=SUPERVISOR.monitor, name="hermes-gateway-monitor", daemon=True)
    monitor.start()
    threading.Thread(
        target=standing_jobs_loop, name="standing-jobs-reconcile", daemon=True
    ).start()
    threading.Thread(
        target=browser_keeper_loop, name="browser-keeper", daemon=True
    ).start()
    print(f"[calliope-control] listening on {CONTROL_HOST}:{CONTROL_PORT}", flush=True)
    server.serve_forever(poll_interval=0.5)
    server.server_close()
    if browser_origin is not None:
        browser_origin.shutdown()
        browser_origin.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
