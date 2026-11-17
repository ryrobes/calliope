"""MCP gateway — bridges the rvbbit pg extension to MCP servers.

Why a sidecar: PG backends can't safely fork long-lived subprocesses (one
per backend wastes memory; orphans on backend crash). This gateway holds a
singleton subprocess per registered MCP server, shared across every PG
backend. PG backends only ever talk HTTP to this process.

Architecture:
- Server configs live in `rvbbit.mcp_servers` (the source of truth). The
  gateway reads them via libpq on demand.
- One `MCPServerProcess` per server, lazy-spawned on first call, held in a
  pool. A per-server asyncio.Lock serializes JSON-RPC calls.
- On crash / timeout / refresh, the subprocess is reset; next call respawns.

Endpoints:
  POST /call            — body {server, tool, arguments}     -> tool result
  POST /refresh/{name}  — re-introspect tools, drop cached client
  POST /drop/{name}     — drop cached client (config stays in DB)
  GET  /tools/{name}    — list tools (cached after first list)
  GET  /health          — gateway liveness
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import time
from contextlib import AsyncExitStack
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import asyncpg
import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import AnyUrl, BaseModel, Field, SecretStr

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from mcp.client.streamable_http import streamablehttp_client

    HAS_HTTP_TRANSPORT = True
except ImportError:  # SDK without streamable-http
    HAS_HTTP_TRANSPORT = False


log = logging.getLogger("mcp-gateway")
logging.basicConfig(level=logging.INFO)

app = FastAPI()

DSN = os.environ.get(
    "RVBBIT_DSN", "postgresql://postgres:rvbbit@postgres:5432/rvbbit"
)

pool: dict[str, "MCPServerProcess"] = {}
pool_lock = asyncio.Lock()
db_pool: asyncpg.Pool | None = None


# ---- env substitution -----------------------------------------------------


_ENV_REF = re.compile(r"\$\{(\w+)\}")


# ---- secret store ---------------------------------------------------------
#
# New installations persist install-time secrets in rvbbit.credentials through
# the purpose-specific SQL API. This object is the in-memory runtime cache and a
# compatibility reader for gateways that start before migration 0286. Existing
# encrypted files are copied into the canonical store once, but never overwrite
# a newer canonical value. Only ${VAR} references remain in mcp_servers.

SECRETS_PATH = os.environ.get("RVBBIT_GATEWAY_SECRETS_PATH", "/app/data/mcp-secrets.bin")
GATEWAY_TOKEN = os.environ.get("RVBBIT_GATEWAY_TOKEN") or None
MAX_TIMEOUT_MS = 600_000
REQUIRE_CANONICAL_CREDENTIALS = os.environ.get(
    "RVBBIT_REQUIRE_CANONICAL_CREDENTIALS", ""
).strip().lower() in {"1", "true", "yes", "on"}
REQUIRE_LEGACY_ENCRYPTION = os.environ.get(
    "RVBBIT_GATEWAY_REQUIRE_ENCRYPTION", ""
).strip().lower() in {"1", "true", "yes", "on"}
canonical_credentials = False
revoked_credentials: set[tuple[str, str]] = set()

try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


def _load_fernet():
    if not _HAS_CRYPTO:
        if REQUIRE_LEGACY_ENCRYPTION:
            raise RuntimeError(
                "mcp-gateway: cryptography is required for the legacy secret store"
            )
        log.warning("mcp-gateway: `cryptography` not installed; secret store is UNENCRYPTED")
        return None
    key = os.environ.get("RVBBIT_GATEWAY_SECRET_KEY")
    if key:
        return Fernet(key.encode() if isinstance(key, str) else key)
    # No explicit key: generate + persist one beside the store so restarts can
    # still decrypt. A mounted/explicit RVBBIT_GATEWAY_SECRET_KEY is recommended
    # for production (a rebuilt container without a volume loses the key file).
    key_path = SECRETS_PATH + ".key"
    try:
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                cipher = Fernet(f.read().strip())
            os.chmod(key_path, 0o600)
            return cipher
        os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
        k = Fernet.generate_key()
        with open(key_path, "wb") as f:
            f.write(k)
        os.chmod(key_path, 0o600)
        log.warning("mcp-gateway: generated a secret-store key at %s; set "
                    "RVBBIT_GATEWAY_SECRET_KEY (mounted) for durable production use", key_path)
        return Fernet(k)
    except Exception as e:
        if REQUIRE_LEGACY_ENCRYPTION:
            raise RuntimeError(
                "mcp-gateway: encrypted legacy secret-store setup failed"
            ) from e
        log.warning("mcp-gateway: encryption setup failed (%s); secrets UNENCRYPTED", e)
        return None


class SecretStore:
    def __init__(self):
        self._data: dict[str, dict[str, str]] = {}
        self._fernet = _load_fernet()
        self._load()

    def _load(self):
        try:
            if not os.path.exists(SECRETS_PATH):
                return
            with open(SECRETS_PATH, "rb") as f:
                raw = f.read()
            if not raw:
                return
            if self._fernet is not None:
                raw = self._fernet.decrypt(raw)
            self._data = json.loads(raw.decode("utf-8"))
            log.info("mcp-gateway: loaded secrets for %d server(s)", len(self._data))
        except Exception as e:
            log.warning("mcp-gateway: could not load secret store (%s); starting empty", e)
            self._data = {}

    def _persist(self):
        try:
            os.makedirs(os.path.dirname(SECRETS_PATH) or ".", exist_ok=True)
            raw = json.dumps(self._data).encode("utf-8")
            if self._fernet is not None:
                raw = self._fernet.encrypt(raw)
            tmp = SECRETS_PATH + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.chmod(tmp, 0o600)
            os.replace(tmp, SECRETS_PATH)
        except Exception as e:
            log.warning("mcp-gateway: could not persist secret store: %s", e)

    def for_server(self, server: str) -> dict[str, str]:
        return dict(self._data.get(server, {}))

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {server: dict(values) for server, values in self._data.items()}

    def replace(self, data: dict[str, dict[str, str]]):
        self._data = {
            str(server): {str(name): str(value) for name, value in values.items()}
            for server, values in data.items()
            if isinstance(values, dict)
        }

    def set(self, server: str, name: str, value: str, *, persist: bool = True):
        self._data.setdefault(server, {})[name] = value
        if persist:
            self._persist()

    def delete(self, server: str, name: str, *, persist: bool = True):
        if name in self._data.get(server, {}):
            del self._data[server][name]
            if not self._data[server]:
                del self._data[server]
            if persist:
                self._persist()

    def names(self, server: str) -> list[str]:
        return sorted(self._data.get(server, {}).keys())


secrets = SecretStore()


async def _initialize_canonical_credentials() -> None:
    """Hydrate the runtime cache and migrate missing legacy file entries."""
    global canonical_credentials, revoked_credentials
    if db_pool is None:
        raise RuntimeError("mcp-gateway database pool is not initialized")
    canonical_credentials = False
    revoked_credentials = set()
    try:
        async with db_pool.acquire() as conn:
            supported = await conn.fetchval(
                "SELECT to_regprocedure('rvbbit.set_mcp_credential(text,text,text)') "
                "IS NOT NULL"
            )
            key_ready = bool(supported) and bool(
                await conn.fetchval("SELECT rvbbit.credential_key_available()")
            )
            if not supported or not key_ready:
                reason = (
                    "migration 0286 is not installed"
                    if not supported
                    else "RVBBIT_CREDENTIAL_KEY is not configured"
                )
                if REQUIRE_CANONICAL_CREDENTIALS:
                    raise RuntimeError(
                        f"canonical MCP credentials are required but {reason}"
                    )
                log.warning(
                    "mcp-gateway: canonical credential store unavailable (%s); "
                    "using the legacy encrypted file",
                    reason,
                )
                return

            rows = await conn.fetch(
                "SELECT namespace AS server_name,name AS secret_name,status "
                "FROM rvbbit.list_credentials('mcp',NULL)"
            )
            known = {
                (str(row["server_name"]), str(row["secret_name"])) for row in rows
            }
            active = {
                (str(row["server_name"]), str(row["secret_name"]))
                for row in rows if str(row["status"]) == "active"
            }
            revoked = known.difference(active)
            legacy = secrets.snapshot()
            for server, values in legacy.items():
                for name, value in values.items():
                    if (server, name) in known:
                        continue
                    await conn.fetchval(
                        "SELECT rvbbit.set_mcp_credential($1, $2, $3)",
                        server,
                        name,
                        value,
                    )
                    known.add((server, name))
                    active.add((server, name))

            hydrated: dict[str, dict[str, str]] = {}
            for server, name in sorted(active):
                value = await conn.fetchval(
                    "SELECT rvbbit.resolve_mcp_credential($1, $2)", server, name
                )
                if value:
                    hydrated.setdefault(server, {})[name] = str(value)
            secrets.replace(hydrated)
            revoked_credentials = revoked
            canonical_credentials = True
            log.info(
                "mcp-gateway: canonical credential store ready for %d server(s)",
                len(hydrated),
            )
    except Exception:
        canonical_credentials = False
        revoked_credentials = set()
        if REQUIRE_CANONICAL_CREDENTIALS:
            raise
        log.exception(
            "mcp-gateway: canonical credential initialization failed; "
            "using the legacy encrypted file"
        )


def _expand_refs(value: str, server_name: str | None) -> str:
    """Expand ${VAR} refs in a string. Resolution order, highest first: the per-server secret store
    (canonical credentials cached in memory), then the gateway's process env."""
    store = secrets.for_server(server_name) if server_name else {}

    def _sub(m):
        var = m.group(1)
        if server_name and (server_name, var) in revoked_credentials:
            return ""
        return store[var] if var in store else os.environ.get(var, "")

    return _ENV_REF.sub(_sub, value)


def resolve_env(env_template: dict[str, Any] | None, server_name: str | None = None) -> dict[str, str]:
    """Expand ${VAR} refs in env VALUES (see _expand_refs)."""
    if not env_template:
        return {}
    out: dict[str, str] = {}
    for k, v in env_template.items():
        out[k] = _expand_refs(v, server_name) if isinstance(v, str) else str(v)
    return out


def resolve_args(args: list | None, server_name: str | None = None) -> list:
    """Expand ${VAR} refs in command ARGS too — so remote servers bridged via `npx mcp-remote … --header
    'Authorization: Bearer ${API_KEY}'` get their secret injected, exactly like env-based servers."""
    return [_expand_refs(a, server_name) if isinstance(a, str) else a for a in (args or [])]


def resolve_auth_headers(config: "MCPServerConfig") -> dict[str, str]:
    """Resolve the HTTP MCP bearer token named by auth_header_env."""
    if not config.auth_header_env:
        return {}
    store = secrets.for_server(config.name)
    if (config.name, config.auth_header_env) in revoked_credentials:
        token = None
    else:
        token = store.get(config.auth_header_env) or os.environ.get(config.auth_header_env)
    if not token:
        log.warning(
            "mcp-gateway: HTTP MCP server %r configured auth_header_env=%r, "
            "but no matching secret or process env var is set",
            config.name,
            config.auth_header_env,
        )
        return {}
    return {"Authorization": f"Bearer {token}"}


# ---- OAuth 2.1 client (MCP authorization) ---------------------------------
#
# Remote MCP servers increasingly require the MCP authorization spec's OAuth
# 2.1 flow (authorization code + PKCE, discovery per RFC 9728/8414, optional
# dynamic client registration per RFC 7591) instead of a static bearer token.
# The interactive leg cannot happen here (this process is headless): the
# Warehouse UI drives it. Split of responsibilities:
#   POST /oauth/start   — discovery + client registration + PKCE; returns the
#                         authorization URL the operator's browser must visit.
#   POST /oauth/finish  — single-use state + code -> token exchange; tokens are
#                         sealed into the same per-server credential store as
#                         every other MCP secret.
#   GET  /oauth/status  — connection state for UIs to poll.
# At runtime the http transport injects "Authorization: Bearer <access>" and
# refreshes proactively; a dead refresh token marks the connection
# needs_reconnect instead of silently retrying forever.
#
# The two credential names below live in the server's namespace beside API
# keys, so revocation, /secrets listing, and gateway cache mechanics all apply
# unchanged. Values are JSON documents, never raw tokens.

OAUTH_TOKENS_NAME = "OAUTH_TOKENS"
OAUTH_CLIENT_NAME = "OAUTH_CLIENT"
OAUTH_FLOW_TTL_S = 600
OAUTH_REFRESH_SKEW_S = 60
OAUTH_CLIENT_NAME_HUMAN = os.environ.get(
    "RVBBIT_OAUTH_CLIENT_NAME", "RVBBIT Warehouse"
)

oauth_flows: dict[str, dict[str, Any]] = {}
oauth_flow_lock = asyncio.Lock()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _oauth_random(n_bytes: int = 32) -> str:
    return _b64url(os.urandom(n_bytes))


def make_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) per RFC 7636, S256."""
    verifier = _b64url(os.urandom(48))          # 64 chars, within 43..128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def canonical_resource(url: str) -> str:
    """The MCP server's canonical resource URI (RFC 8707): scheme+host+path,
    lowercased host, no fragment, no trailing slash on a bare origin."""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


async def _store_server_secret(server: str, name: str, value: str) -> None:
    """Persist one per-server credential through the same write path as
    POST /secrets (canonical store when available, legacy file otherwise),
    keeping the in-memory cache coherent. No revision preconditions — these
    writes are gateway-internal (token refresh, flow completion)."""
    if canonical_credentials and db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.fetchval(
                "SELECT rvbbit.set_mcp_credential($1, $2, $3)", server, name, value
            )
        secrets.set(server, name, value, persist=False)
        revoked_credentials.discard((server, name))
    else:
        secrets.set(server, name, value)


def _read_server_secret(server: str, name: str) -> str | None:
    if (server, name) in revoked_credentials:
        return None
    return secrets.for_server(server).get(name)


def _read_oauth_json(server: str, name: str) -> dict[str, Any] | None:
    raw = _read_server_secret(server, name)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _oauth_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=8.0),
        trust_env=False,
        follow_redirects=True,
        headers={"MCP-Protocol-Version": "2025-06-18"},
    )


def _wellknown_candidates(base_url: str, suffix: str) -> list[str]:
    """RFC 8414 path-aware well-known locations, most specific first."""
    parts = urlsplit(base_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    candidates = []
    if path:
        candidates.append(f"{origin}/.well-known/{suffix}{path}")
    candidates.append(f"{origin}/.well-known/{suffix}")
    return candidates


_RESOURCE_METADATA_RE = re.compile(r'resource_metadata="([^"]+)"')


async def _oauth_discover(mcp_url: str) -> dict[str, Any]:
    """Resolve the authorization-server metadata for an MCP endpoint.

    Order: the endpoint's own 401 WWW-Authenticate resource_metadata hint,
    then RFC 9728 protected-resource well-knowns, then treating the MCP
    origin itself as the issuer. Raises ValueError with an operator-readable
    message when no authorization server can be found."""
    resource = canonical_resource(mcp_url)
    issuers: list[str] = []
    async with _oauth_http() as client:
        try:
            probe = await client.post(
                mcp_url,
                json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            challenge = probe.headers.get("www-authenticate", "")
            match = _RESOURCE_METADATA_RE.search(challenge)
            if match:
                try:
                    doc = (await client.get(match.group(1))).json()
                    issuers.extend(doc.get("authorization_servers") or [])
                except Exception:
                    pass
        except Exception:
            pass
        if not issuers:
            for candidate in _wellknown_candidates(
                mcp_url, "oauth-protected-resource"
            ):
                try:
                    response = await client.get(candidate)
                    if response.status_code == 200:
                        doc = response.json()
                        issuers.extend(doc.get("authorization_servers") or [])
                        if issuers:
                            break
                except Exception:
                    continue
        parts = urlsplit(mcp_url)
        issuers.append(f"{parts.scheme}://{parts.netloc}")

        for issuer in issuers:
            for suffix in ("oauth-authorization-server", "openid-configuration"):
                for candidate in _wellknown_candidates(str(issuer), suffix):
                    try:
                        response = await client.get(candidate)
                        if response.status_code != 200:
                            continue
                        meta = response.json()
                    except Exception:
                        continue
                    if meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                        return {
                            "issuer": str(meta.get("issuer") or issuer),
                            "authorization_endpoint": str(meta["authorization_endpoint"]),
                            "token_endpoint": str(meta["token_endpoint"]),
                            "registration_endpoint": (
                                str(meta["registration_endpoint"])
                                if meta.get("registration_endpoint") else None
                            ),
                            "resource": resource,
                        }
    raise ValueError(
        "No OAuth authorization server was discoverable for this MCP endpoint "
        "(no WWW-Authenticate hint and no well-known metadata). The server may "
        "use plain bearer tokens instead."
    )


async def _oauth_register_client(
    meta: dict[str, Any], redirect_uri: str, scope: str | None
) -> dict[str, Any]:
    """RFC 7591 dynamic client registration."""
    if not meta.get("registration_endpoint"):
        raise ValueError(
            "The authorization server does not offer dynamic client "
            "registration; supply a pre-registered OAuth client id."
        )
    body: dict[str, Any] = {
        "client_name": OAUTH_CLIENT_NAME_HUMAN,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        body["scope"] = scope
    async with _oauth_http() as client:
        response = await client.post(meta["registration_endpoint"], json=body)
    if response.status_code not in (200, 201):
        raise ValueError(
            f"Dynamic client registration failed "
            f"({response.status_code}): {response.text[:300]}"
        )
    registered = response.json()
    if not registered.get("client_id"):
        raise ValueError("Dynamic client registration returned no client_id")
    return registered


def _token_request_body(
    client_doc: dict[str, Any], extra: dict[str, str]
) -> dict[str, str]:
    body = {"client_id": str(client_doc.get("client_id") or ""), **extra}
    if client_doc.get("client_secret"):
        body["client_secret"] = str(client_doc["client_secret"])
    if client_doc.get("resource"):
        body["resource"] = str(client_doc["resource"])
    return body


def _tokens_from_response(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    expires_in = payload.get("expires_in")
    try:
        expires_at = time.time() + int(expires_in) if expires_in else None
    except (TypeError, ValueError):
        expires_at = None
    return {
        "status": "connected",
        "access_token": str(payload.get("access_token") or ""),
        "token_type": str(payload.get("token_type") or "Bearer"),
        # A rotated refresh token replaces the old one; an omitted one is kept.
        "refresh_token": str(
            payload.get("refresh_token")
            or (previous or {}).get("refresh_token")
            or ""
        ) or None,
        "scope": str(payload.get("scope") or (previous or {}).get("scope") or "") or None,
        "expires_at": expires_at,
        "obtained_at": time.time(),
    }


async def _mark_needs_reconnect(server: str, tokens: dict[str, Any], reason: str) -> None:
    tokens = dict(tokens)
    tokens["status"] = "needs_reconnect"
    tokens["last_error"] = reason[:400]
    try:
        await _store_server_secret(server, OAUTH_TOKENS_NAME, json.dumps(tokens))
    except Exception:
        log.exception("mcp-gateway: could not persist needs_reconnect for %r", server)


async def oauth_access_token(config: "MCPServerConfig") -> str | None:
    """The current access token for an OAuth-connected server, refreshed when
    it is about to expire. None when the server has no OAuth connection."""
    tokens = _read_oauth_json(config.name, OAUTH_TOKENS_NAME)
    if not tokens:
        return None
    if tokens.get("status") == "needs_reconnect":
        raise RuntimeError(
            f"OAuth connection for '{config.name}' needs to be re-authorized "
            f"(last error: {tokens.get('last_error') or 'refresh failed'}). "
            "Run the Connect flow again."
        )
    expires_at = tokens.get("expires_at")
    fresh = not expires_at or time.time() < float(expires_at) - OAUTH_REFRESH_SKEW_S
    if fresh and tokens.get("access_token"):
        return str(tokens["access_token"])
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Expired with nothing to refresh: send what we have; the server's 401
        # is a clearer signal than guessing here.
        return str(tokens.get("access_token") or "") or None
    client_doc = _read_oauth_json(config.name, OAUTH_CLIENT_NAME) or {}
    if not client_doc.get("token_endpoint"):
        raise RuntimeError(
            f"OAuth client metadata for '{config.name}' is missing; run the "
            "Connect flow again."
        )
    body = _token_request_body(client_doc, {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token),
    })
    async with _oauth_http() as client:
        response = await client.post(client_doc["token_endpoint"], data=body)
    if response.status_code == 200:
        refreshed = _tokens_from_response(response.json(), previous=tokens)
        await _store_server_secret(
            config.name, OAUTH_TOKENS_NAME, json.dumps(refreshed)
        )
        return str(refreshed["access_token"])
    detail = response.text[:300]
    if response.status_code in (400, 401) and "invalid_grant" in detail:
        await _mark_needs_reconnect(config.name, tokens, detail)
        raise RuntimeError(
            f"OAuth refresh token for '{config.name}' was rejected "
            "(invalid_grant). Run the Connect flow again to re-authorize."
        )
    raise RuntimeError(
        f"OAuth token refresh for '{config.name}' failed "
        f"({response.status_code}): {detail}"
    )


async def resolve_http_headers(config: "MCPServerConfig") -> dict[str, str]:
    """Auth headers for the http transport: OAuth connection first, static
    bearer token (auth_header_env) otherwise."""
    token = await oauth_access_token(config)
    if token:
        return {"Authorization": f"Bearer {token}"}
    return resolve_auth_headers(config)


def streamable_http_client_kwargs(headers: dict[str, str]) -> dict[str, Any]:
    """Return kwargs compatible with the installed MCP SDK streamable client."""
    if not headers:
        return {}
    params = inspect.signature(streamablehttp_client).parameters
    if "headers" in params:
        return {"headers": headers}
    if "http_client" in params:
        return {"http_client": httpx.AsyncClient(headers=headers)}
    raise RuntimeError(
        "installed mcp SDK streamablehttp_client does not support HTTP headers"
    )


# ---- DB config fetch ------------------------------------------------------


class MCPServerConfig:
    __slots__ = (
        "name",
        "transport",
        "command",
        "args",
        "env",
        "url",
        "auth_header_env",
        "timeout_ms",
    )

    def __init__(self, row):
        self.name = row["name"]
        self.transport = row["transport"]
        self.command = row["command"]
        self.args = list(row["args"] or [])
        self.env = row["env"] or {}
        if isinstance(self.env, str):
            self.env = json.loads(self.env)
        self.url = row["url"]
        self.auth_header_env = row["auth_header_env"]
        self.timeout_ms = _normalize_timeout_ms(row["timeout_ms"])


def _normalize_timeout_ms(value: Any) -> int:
    try:
        timeout_ms = int(value or 30_000)
    except (TypeError, ValueError):
        timeout_ms = 30_000
    return max(1, min(timeout_ms, MAX_TIMEOUT_MS))


async def fetch_config(server_name: str) -> MCPServerConfig:
    if db_pool is None:
        raise HTTPException(503, "gateway db pool not initialized")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, transport, command, args, env, url, "
            "       auth_header_env, timeout_ms "
            "FROM rvbbit.mcp_servers WHERE name = $1",
            server_name,
        )
    if not row:
        raise HTTPException(404, f"mcp server '{server_name}' not registered")
    return MCPServerConfig(row)


# ---- the per-server client ------------------------------------------------


class MCPServerProcess:
    """Wraps one MCP server's client session + per-server lock + lifecycle."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.lock = asyncio.Lock()
        self.session: ClientSession | None = None
        self._tools_cache: list | None = None
        # Lifecycle of the session's async-context stack is owned by a single
        # dedicated task (`_run`); see the comment there for why.
        self._runner: asyncio.Task | None = None
        self._ready: asyncio.Event | None = None
        self._shutdown: asyncio.Event | None = None
        self._error: BaseException | None = None

    def _timeout_s(self) -> float:
        return self.config.timeout_ms / 1000.0

    async def _run(self) -> None:
        # Own the session's async-context lifecycle ENTIRELY within this one
        # task — enter AND exit here, never from a caller's task.
        #
        # The MCP SDK builds on anyio cancel scopes, which may only be exited
        # from the same task that entered them. The previous design entered the
        # stdio_client/ClientSession stack inside whichever HTTP-request task
        # first touched the server, then closed it from a *different* request
        # task during /refresh or an error reset. That cross-task teardown
        # fails to cancel the subprocess reader task, which is then orphaned and
        # busy-loops on the EOF pipe forever — pinning a full vCPU even while
        # the gateway is otherwise idle. (Reproduced: a single /refresh ->
        # permanent 100% CPU.) Doing enter+exit in this one task makes the
        # cancel scopes unwind cleanly.
        stack = AsyncExitStack()
        try:
            if self.config.transport == "stdio":
                params = StdioServerParameters(
                    command=self.config.command,
                    args=resolve_args(self.config.args, self.config.name),
                    env={**os.environ, **resolve_env(self.config.env, self.config.name)},
                )
                read, write = await asyncio.wait_for(
                    stack.enter_async_context(stdio_client(params)),
                    timeout=self._timeout_s(),
                )
            elif self.config.transport == "http":
                if not HAS_HTTP_TRANSPORT:
                    raise RuntimeError(
                        "HTTP MCP transport not available; install a newer mcp SDK"
                    )
                stream_kwargs = streamable_http_client_kwargs(
                    await resolve_http_headers(self.config)
                )
                http_client = stream_kwargs.get("http_client")
                if http_client is not None:
                    stream_kwargs["http_client"] = await stack.enter_async_context(
                        http_client
                    )
                ctx = await asyncio.wait_for(
                    stack.enter_async_context(
                        streamablehttp_client(self.config.url, **stream_kwargs)
                    ),
                    timeout=self._timeout_s(),
                )
                # streamablehttp_client yields (read, write, get_session_id)
                read, write = ctx[0], ctx[1]
            else:
                raise ValueError(f"unknown transport: {self.config.transport!r}")
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self._timeout_s(),
            )
            await asyncio.wait_for(session.initialize(), timeout=self._timeout_s())
            self.session = session
            log.info("started mcp server %r (transport=%s)",
                     self.config.name, self.config.transport)
        except BaseException as e:
            # Startup failed — report it to ensure_started() and unwind here.
            self._error = e
            if self._ready is not None:
                self._ready.set()
            try:
                await stack.aclose()
            except Exception:
                pass
            return
        # Ready; park until reset()/shutdown asks us to tear down. The aclose()
        # in the finally runs in THIS task, so teardown is clean.
        if self._ready is not None:
            self._ready.set()
        try:
            if self._shutdown is not None:
                await self._shutdown.wait()
        finally:
            self.session = None
            try:
                await stack.aclose()
            except Exception as e:
                log.warning("error closing mcp server %r: %s",
                            self.config.name, e)

    async def ensure_started(self) -> None:
        if self.session is not None:
            return
        self._error = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._runner = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._timeout_s())
        except asyncio.TimeoutError:
            await self._reset_locked()
            raise
        if self._error is not None:
            err = self._error
            await self._reset_locked()
            raise err

    async def call_tool(self, name: str, arguments: dict) -> Any:
        async with self.lock:
            await self.ensure_started()
            try:
                return await asyncio.wait_for(
                    self.session.call_tool(name, arguments),
                    timeout=self._timeout_s(),
                )
            except Exception:
                # subprocess may be in a bad state — reset so the next
                # caller respawns it cleanly.
                await self._reset_locked()
                raise

    async def list_tools(self) -> list:
        async with self.lock:
            await self.ensure_started()
            if self._tools_cache is None:
                try:
                    result = await asyncio.wait_for(
                        self.session.list_tools(),
                        timeout=self._timeout_s(),
                    )
                    self._tools_cache = list(result.tools)
                except Exception:
                    await self._reset_locked()
                    raise
            return self._tools_cache

    async def list_resources(self) -> list:
        """List static resources. Servers that don't support resources
        return an empty list (we silently swallow the error)."""
        async with self.lock:
            await self.ensure_started()
            try:
                result = await asyncio.wait_for(
                    self.session.list_resources(),
                    timeout=self._timeout_s(),
                )
                return list(getattr(result, "resources", []))
            except asyncio.TimeoutError:
                await self._reset_locked()
                return []
            except Exception:
                return []

    async def read_resource(self, uri: str) -> Any:
        async with self.lock:
            await self.ensure_started()
            try:
                return await asyncio.wait_for(
                    self.session.read_resource(AnyUrl(uri)),
                    timeout=self._timeout_s(),
                )
            except Exception:
                await self._reset_locked()
                raise

    async def reset(self) -> None:
        async with self.lock:
            await self._reset_locked()

    async def _reset_locked(self) -> None:
        self._tools_cache = None
        self.session = None
        runner = self._runner
        if runner is not None and not runner.done():
            # Ask _run to unwind, then wait for it to tear down in its own task.
            if self._shutdown is not None:
                self._shutdown.set()
            try:
                await asyncio.wait_for(runner, timeout=15)
            except asyncio.TimeoutError:
                # wait_for already cancelled the task; log and move on.
                log.warning("mcp server %r teardown timed out", self.config.name)
            except BaseException:
                pass
        self._runner = None
        self._ready = None
        self._shutdown = None


async def get_server(name: str) -> MCPServerProcess:
    async with pool_lock:
        if name in pool:
            return pool[name]
        cfg = await fetch_config(name)
        proc = MCPServerProcess(cfg)
        pool[name] = proc
        return proc


async def evict_server(name: str) -> None:
    async with pool_lock:
        proc = pool.pop(name, None)
    if proc is not None:
        await proc.reset()


# ---- HTTP API -------------------------------------------------------------


class CallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict = Field(default_factory=dict)


def _serialize_content(c: Any) -> dict:
    """Convert an MCP content block to a JSON-friendly dict."""
    out: dict[str, Any] = {}
    t = getattr(c, "type", None)
    if t is not None:
        out["type"] = t
    text = getattr(c, "text", None)
    if text is not None:
        out["text"] = text
    data = getattr(c, "data", None)
    if data is not None:
        out["data"] = data
    mime = getattr(c, "mimeType", None)
    if mime is not None:
        out["mimeType"] = mime
    if not out:
        # Fallback: best-effort dump.
        out = {"type": "unknown", "repr": repr(c)}
    return out


@app.post("/call")
async def call(req: CallRequest, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    proc = await get_server(req.server)
    try:
        result = await proc.call_tool(req.tool, req.arguments)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"tool '{req.tool}' on '{req.server}' timed out")
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {
        "content": [_serialize_content(c) for c in result.content],
        "isError": bool(getattr(result, "isError", False)),
    }


def _serialize_tool(t: Any) -> dict:
    return {
        "name": t.name,
        "description": getattr(t, "description", None),
        "input_schema": getattr(t, "inputSchema", {}) or {},
    }


def _serialize_resource(r: Any) -> dict:
    return {
        "uri": str(getattr(r, "uri", "")),
        "name": getattr(r, "name", None),
        "description": getattr(r, "description", None),
        "mime_type": getattr(r, "mimeType", None),
    }


def _serialize_resource_content(c: Any) -> dict:
    out: dict[str, Any] = {}
    uri = getattr(c, "uri", None)
    if uri is not None:
        out["uri"] = str(uri)
    mime = getattr(c, "mimeType", None)
    if mime is not None:
        out["mimeType"] = mime
    text = getattr(c, "text", None)
    if text is not None:
        out["text"] = text
    blob = getattr(c, "blob", None)
    if blob is not None:
        out["blob"] = blob
    return out


class ReadResourceRequest(BaseModel):
    server: str
    uri: str


@app.post("/refresh/{server}")
async def refresh(server: str, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    # Drop any cached subprocess + tools cache, then re-list everything.
    await evict_server(server)
    proc = await get_server(server)
    try:
        tools = await proc.list_tools()
    except asyncio.TimeoutError:
        raise HTTPException(504, f"refresh for '{server}' timed out")
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    # Resources may not be supported — silent fall-through to [].
    resources = await proc.list_resources()
    return {
        "tools": [_serialize_tool(t) for t in tools],
        "resources": [_serialize_resource(r) for r in resources],
    }


@app.post("/drop/{server}")
async def drop(server: str, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    await evict_server(server)
    return {"dropped": server}


@app.get("/tools/{server}")
async def get_tools(server: str, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    proc = await get_server(server)
    try:
        tools = await proc.list_tools()
    except asyncio.TimeoutError:
        raise HTTPException(504, f"tools/list for '{server}' timed out")
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"tools": [_serialize_tool(t) for t in tools]}


@app.post("/resource")
async def read_resource(req: ReadResourceRequest, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    proc = await get_server(req.server)
    try:
        result = await proc.read_resource(req.uri)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"resource '{req.uri}' on '{req.server}' timed out")
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"contents": [_serialize_resource_content(c) for c in result.contents]}


@app.post("/probe/{server}")
async def probe(server: str, authorization: str | None = Header(default=None)):
    """Active health probe — forces tools/list, returns latency + result.
    A reachable server returns reachable=true even if no tools (degenerate
    but valid)."""
    _check_token(authorization)
    import time as _time
    try:
        proc = await get_server(server)
    except HTTPException:
        raise
    t0 = _time.monotonic()
    try:
        tools = await proc.list_tools()
        latency_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "reachable": True,
            "latency_ms": latency_ms,
            "n_tools": len(tools),
            "error": None,
        }
    except Exception as e:
        latency_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "reachable": False,
            "latency_ms": latency_ms,
            "n_tools": 0,
            "error": f"{type(e).__name__}: {e}",
        }


# ---- secrets API ----------------------------------------------------------
#
# The native install/admin UI pushes API keys here. In canonical mode this is
# the sole write boundary into encrypted Postgres ciphertext; plaintext is
# retained only in the gateway's in-memory runtime cache. Guarded by a shared
# bearer token when RVBBIT_GATEWAY_TOKEN is set; open (with a log note) in dev.


def _check_token(authorization: str | None) -> None:
    if GATEWAY_TOKEN is None:
        return
    if authorization != f"Bearer {GATEWAY_TOKEN}":
        raise HTTPException(401, "invalid or missing gateway token")


class SecretRequest(BaseModel):
    server: str
    name: str
    value: SecretStr
    expected_version: int | None = Field(default=None, ge=1)
    expected_status: Literal["active", "revoked"] | None = None
    expect_absent: bool = False


class SecretRef(BaseModel):
    server: str
    name: str
    expected_version: int | None = Field(default=None, ge=1)
    expected_status: Literal["active", "revoked"] | None = None


async def _check_credential_revision(conn, req: SecretRequest | SecretRef) -> None:
    current = await conn.fetchrow(
        "SELECT version,status FROM rvbbit.credentials "
        "WHERE credential_ref=rvbbit.credential_ref('mcp',$1,$2) FOR UPDATE",
        req.server,
        req.name,
    )
    if getattr(req, "expect_absent", False):
        if current:
            raise HTTPException(409, "credential changed since the approved plan")
        return
    if req.expected_version is None:
        return
    if (
        not current
        or int(current["version"]) != int(req.expected_version)
        or (
            req.expected_status is not None
            and str(current["status"]) != str(req.expected_status)
        )
    ):
        raise HTTPException(409, "credential changed since the approved plan")


@app.post("/secrets")
async def set_secret(req: SecretRequest, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    secret_value = req.value.get_secret_value()
    if canonical_credentials:
        if db_pool is None:
            raise HTTPException(503, "canonical credential database is unavailable")
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await _check_credential_revision(conn, req)
                    await conn.fetchval(
                        "SELECT rvbbit.set_mcp_credential($1, $2, $3)",
                        req.server,
                        req.name,
                        secret_value,
                    )
        except HTTPException:
            raise
        except Exception as exc:
            log.warning(
                "mcp-gateway: canonical credential write failed for %s/%s: %s",
                req.server,
                req.name,
                type(exc).__name__,
            )
            raise HTTPException(503, "canonical credential write failed") from exc
        secrets.set(req.server, req.name, secret_value, persist=False)
        revoked_credentials.discard((req.server, req.name))
    else:
        if REQUIRE_CANONICAL_CREDENTIALS:
            raise HTTPException(503, "canonical credential store is unavailable")
        secrets.set(req.server, req.name, secret_value)
    # Respawn on next call so the new secret is picked up.
    await evict_server(req.server)
    return {"ok": True, "server": req.server, "name": req.name}


@app.delete("/secrets")
async def delete_secret(req: SecretRef, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    if canonical_credentials:
        if db_pool is None:
            raise HTTPException(503, "canonical credential database is unavailable")
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await _check_credential_revision(conn, req)
                    await conn.fetchval(
                        "SELECT rvbbit.delete_mcp_credential($1, $2)",
                        req.server,
                        req.name,
                    )
        except HTTPException:
            raise
        except Exception as exc:
            log.warning(
                "mcp-gateway: canonical credential delete failed for %s/%s: %s",
                req.server,
                req.name,
                type(exc).__name__,
            )
            raise HTTPException(503, "canonical credential delete failed") from exc
        secrets.delete(req.server, req.name, persist=False)
        revoked_credentials.add((req.server, req.name))
    else:
        if REQUIRE_CANONICAL_CREDENTIALS:
            raise HTTPException(503, "canonical credential store is unavailable")
        secrets.delete(req.server, req.name)
    await evict_server(req.server)
    return {"ok": True, "server": req.server, "name": req.name}


@app.get("/secrets/{server}")
async def secret_status(server: str, authorization: str | None = Header(default=None)):
    """Which secret names are set for a server (values are never returned)."""
    _check_token(authorization)
    return {"server": server, "set": secrets.names(server)}


# ---- OAuth API ------------------------------------------------------------


class OAuthStartRequest(BaseModel):
    server: str
    redirect_uri: str
    scope: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None


class OAuthFinishRequest(BaseModel):
    state: str
    code: str


async def _prune_oauth_flows_locked() -> None:
    now = time.monotonic()
    for state in [
        s for s, flow in oauth_flows.items()
        if now - flow["created"] > OAUTH_FLOW_TTL_S
    ]:
        oauth_flows.pop(state, None)


@app.post("/oauth/start")
async def oauth_start(
    req: OAuthStartRequest, authorization: str | None = Header(default=None)
):
    _check_token(authorization)
    config = await fetch_config(req.server)
    if config.transport != "http":
        raise HTTPException(400, "OAuth applies to http MCP servers only")
    redirect = urlsplit(req.redirect_uri)
    if redirect.scheme not in ("http", "https") or not redirect.netloc:
        raise HTTPException(400, "redirect_uri must be an absolute HTTP(S) URL")
    try:
        meta = await _oauth_discover(config.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    scope = (req.scope or "").strip() or None
    if req.client_id:
        client_doc: dict[str, Any] = {"client_id": req.client_id.strip()}
        if req.client_secret is not None:
            secret_value = req.client_secret.get_secret_value().strip()
            if secret_value:
                client_doc["client_secret"] = secret_value
    else:
        try:
            client_doc = await _oauth_register_client(meta, req.redirect_uri, scope)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    client_doc = {
        "client_id": str(client_doc.get("client_id") or ""),
        **(
            {"client_secret": str(client_doc["client_secret"])}
            if client_doc.get("client_secret") else {}
        ),
        "issuer": meta["issuer"],
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
        "registration_endpoint": meta.get("registration_endpoint"),
        "resource": meta["resource"],
        "scope": scope,
        "redirect_uri": req.redirect_uri,
    }
    # Persist the client BEFORE the browser leg: /oauth/finish and every later
    # refresh read it from the store, surviving a gateway restart mid-flow.
    await _store_server_secret(
        req.server, OAUTH_CLIENT_NAME, json.dumps(client_doc)
    )
    verifier, challenge = make_pkce_pair()
    state = _oauth_random(32)
    async with oauth_flow_lock:
        await _prune_oauth_flows_locked()
        oauth_flows[state] = {
            "server": req.server,
            "verifier": verifier,
            "created": time.monotonic(),
        }
    params = {
        "response_type": "code",
        "client_id": client_doc["client_id"],
        "redirect_uri": req.redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": meta["resource"],
    }
    if scope:
        params["scope"] = scope
    return {
        "authorization_url": f"{meta['authorization_endpoint']}?{urlencode(params)}",
        "state": state,
        "authorization_server": meta["issuer"],
        "dynamic_registration": not bool(req.client_id),
    }


@app.post("/oauth/finish")
async def oauth_finish(
    req: OAuthFinishRequest, authorization: str | None = Header(default=None)
):
    _check_token(authorization)
    async with oauth_flow_lock:
        await _prune_oauth_flows_locked()
        flow = oauth_flows.pop(req.state, None)   # single use, replay-proof
    if not flow:
        raise HTTPException(
            400, "This sign-in link expired or was already used; start again"
        )
    server = flow["server"]
    client_doc = _read_oauth_json(server, OAUTH_CLIENT_NAME)
    if not client_doc or not client_doc.get("token_endpoint"):
        raise HTTPException(409, f"OAuth client metadata for '{server}' is missing")
    body = _token_request_body(client_doc, {
        "grant_type": "authorization_code",
        "code": req.code,
        "redirect_uri": str(client_doc.get("redirect_uri") or ""),
        "code_verifier": flow["verifier"],
    })
    async with _oauth_http() as client:
        response = await client.post(client_doc["token_endpoint"], data=body)
    if response.status_code != 200:
        raise HTTPException(
            502,
            f"Token exchange failed ({response.status_code}): {response.text[:300]}",
        )
    payload = response.json()
    if not payload.get("access_token"):
        raise HTTPException(502, "Token exchange returned no access token")
    tokens = _tokens_from_response(payload)
    await _store_server_secret(server, OAUTH_TOKENS_NAME, json.dumps(tokens))
    await evict_server(server)
    return {
        "ok": True,
        "server": server,
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "expires_at": tokens.get("expires_at"),
        "scope": tokens.get("scope"),
    }


@app.get("/oauth/status/{server}")
async def oauth_status(
    server: str, authorization: str | None = Header(default=None)
):
    _check_token(authorization)
    client_doc = _read_oauth_json(server, OAUTH_CLIENT_NAME)
    tokens = _read_oauth_json(server, OAUTH_TOKENS_NAME)
    if tokens and tokens.get("status") == "needs_reconnect":
        status = "needs_reconnect"
    elif tokens and tokens.get("access_token"):
        status = "connected"
    elif client_doc:
        status = "authorizing"
    else:
        status = "unconfigured"
    return {
        "server": server,
        "status": status,
        "connected": status == "connected",
        "configured": bool(client_doc),
        "authorization_server": (client_doc or {}).get("issuer"),
        "scope": (tokens or {}).get("scope") or (client_doc or {}).get("scope"),
        "expires_at": (tokens or {}).get("expires_at"),
        "has_refresh_token": bool((tokens or {}).get("refresh_token")),
        "last_error": (tokens or {}).get("last_error"),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "servers_loaded": sorted(pool.keys()),
        "credential_store": "canonical" if canonical_credentials else "legacy",
    }


# ---- lifecycle ------------------------------------------------------------


async def _init_codecs(conn):
    # asyncpg returns jsonb as raw bytes/str unless a codec is set; we want
    # native dicts so MCPServerConfig.__init__ can read fields directly.
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


@app.on_event("startup")
async def startup():
    global db_pool
    # security-01: /call,/refresh,/drop,/tools,/resource,/probe can invoke any
    # registered tool and spawn stdio subprocesses. When RVBBIT_GATEWAY_TOKEN is
    # set those routes require it (and the rvbbit backend sends it); when it is
    # unset the gateway runs OPEN — fine on a trusted internal docker network, but
    # it must not be exposed. We warn loudly rather than fail closed so the
    # turnkey/local flow keeps working; production sets the token on BOTH the
    # gateway and the pg-rvbbit backend and binds the port internal-only.
    if GATEWAY_TOKEN is None:
        log.warning(
            "mcp-gateway running UNAUTHENTICATED: set RVBBIT_GATEWAY_TOKEN (on the "
            "gateway AND the pg-rvbbit backend) and bind port 9180 internal-only "
            "before exposing it beyond a trusted network."
        )
    # pg-rvbbit may not be ready yet when the gateway starts; retry briefly.
    for attempt in range(60):
        try:
            db_pool = await asyncpg.create_pool(
                DSN, min_size=1, max_size=4, init=_init_codecs
            )
            break
        except Exception as e:
            log.info("waiting for rvbbit DB (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(1)
    if db_pool is None:
        raise RuntimeError(f"could not connect to {DSN} after 60 attempts")
    await _initialize_canonical_credentials()
    log.info("mcp-gateway ready on :9180")


@app.on_event("shutdown")
async def shutdown():
    async with pool_lock:
        procs = list(pool.values())
        pool.clear()
    for proc in procs:
        await proc.reset()
    if db_pool is not None:
        await db_pool.close()
