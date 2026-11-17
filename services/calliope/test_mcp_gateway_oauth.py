"""Contract tests for the mcp-gateway's OAuth client machinery.

The gateway is a separate FastAPI sidecar (capabilities/packs/runtimes/
mcp-gateway/main.py) — imported here by path so its OAuth helpers stay
covered by the same suite that owns the Calliope connect flow. Network and
database access are stubbed; these tests pin the protocol mechanics: PKCE
shape, canonical resource URIs, well-known candidate order, token-document
lifecycle (rotation, expiry, needs_reconnect), and single-use flow state.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GATEWAY_MAIN = (
    HERE.parents[1] / "capabilities" / "packs" / "runtimes" / "mcp-gateway" / "main.py"
)


@pytest.fixture(scope="module")
def gateway():
    spec = importlib.util.spec_from_file_location("mcp_gateway_main", GATEWAY_MAIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_gateway_main"] = module
    spec.loader.exec_module(module)
    return module


def test_pkce_pair_is_s256_and_within_rfc_bounds(gateway):
    verifier, challenge = gateway.make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert gateway.make_pkce_pair()[0] != verifier   # not deterministic


def test_canonical_resource_normalizes_per_rfc8707(gateway):
    assert gateway.canonical_resource("HTTPS://MCP.Example.com/api/mcp/") == (
        "https://mcp.example.com/api/mcp"
    )
    assert gateway.canonical_resource("https://mcp.example.com/") == (
        "https://mcp.example.com"
    )
    assert gateway.canonical_resource(
        "https://mcp.example.com/mcp?tenant=x#frag"
    ) == "https://mcp.example.com/mcp"


def test_wellknown_candidates_are_path_aware_most_specific_first(gateway):
    assert gateway._wellknown_candidates(
        "https://auth.example.com/tenant1", "oauth-authorization-server"
    ) == [
        "https://auth.example.com/.well-known/oauth-authorization-server/tenant1",
        "https://auth.example.com/.well-known/oauth-authorization-server",
    ]
    assert gateway._wellknown_candidates(
        "https://auth.example.com", "openid-configuration"
    ) == ["https://auth.example.com/.well-known/openid-configuration"]


def test_token_document_lifecycle_rotation_and_expiry(gateway):
    first = gateway._tokens_from_response({
        "access_token": "at-1", "refresh_token": "rt-1",
        "expires_in": 3600, "scope": "mcp.read",
    })
    assert first["status"] == "connected"
    assert first["refresh_token"] == "rt-1"
    assert first["expires_at"] == pytest.approx(time.time() + 3600, abs=5)

    # A refresh that omits the refresh token keeps the old one; a rotated
    # one replaces it; scope carries forward when omitted.
    kept = gateway._tokens_from_response({"access_token": "at-2"}, previous=first)
    assert kept["refresh_token"] == "rt-1"
    assert kept["scope"] == "mcp.read"
    assert kept["expires_at"] is None
    rotated = gateway._tokens_from_response(
        {"access_token": "at-3", "refresh_token": "rt-2"}, previous=first
    )
    assert rotated["refresh_token"] == "rt-2"


def test_oauth_access_token_decisions(gateway, monkeypatch):
    config = type("Cfg", (), {"name": "acme", "transport": "http"})()
    store: dict[str, str] = {}
    monkeypatch.setattr(
        gateway.secrets, "for_server", lambda server: dict(store)
    )
    monkeypatch.setattr(gateway, "revoked_credentials", set())

    # No OAuth connection: falls through to the static-bearer path.
    assert asyncio.run(gateway.oauth_access_token(config)) is None

    # Fresh token: returned without any network use.
    store[gateway.OAUTH_TOKENS_NAME] = json.dumps({
        "status": "connected", "access_token": "fresh",
        "expires_at": time.time() + 3600,
    })
    assert asyncio.run(gateway.oauth_access_token(config)) == "fresh"

    # Expired without a refresh token: sent as-is (the server's 401 is the
    # clearer signal), never a crash.
    store[gateway.OAUTH_TOKENS_NAME] = json.dumps({
        "status": "connected", "access_token": "stale",
        "expires_at": time.time() - 10,
    })
    assert asyncio.run(gateway.oauth_access_token(config)) == "stale"

    # needs_reconnect fails loudly with re-run guidance, not a silent retry.
    store[gateway.OAUTH_TOKENS_NAME] = json.dumps({
        "status": "needs_reconnect", "access_token": "dead",
        "last_error": "invalid_grant",
    })
    with pytest.raises(RuntimeError, match="re-authorized"):
        asyncio.run(gateway.oauth_access_token(config))


def test_oauth_flow_state_is_single_use_and_ttl_pruned(gateway):
    async def scenario():
        gateway.oauth_flows.clear()
        state = gateway._oauth_random(32)
        async with gateway.oauth_flow_lock:
            gateway.oauth_flows[state] = {
                "server": "acme", "verifier": "v", "created": time.monotonic(),
            }
            gateway.oauth_flows["expired"] = {
                "server": "acme", "verifier": "v",
                "created": time.monotonic() - gateway.OAUTH_FLOW_TTL_S - 1,
            }
            await gateway._prune_oauth_flows_locked()
        assert "expired" not in gateway.oauth_flows
        assert gateway.oauth_flows.pop(state, None) is not None
        assert gateway.oauth_flows.pop(state, None) is None   # replay = miss
    asyncio.run(scenario())


def test_token_request_body_carries_client_and_resource(gateway):
    doc = {
        "client_id": "cid", "client_secret": "cs",
        "resource": "https://mcp.example.com/mcp",
        "token_endpoint": "https://auth.example.com/token",
    }
    body = gateway._token_request_body(doc, {
        "grant_type": "refresh_token", "refresh_token": "rt",
    })
    assert body == {
        "client_id": "cid", "client_secret": "cs",
        "resource": "https://mcp.example.com/mcp",
        "grant_type": "refresh_token", "refresh_token": "rt",
    }
    # Public clients (dynamic registration) send no secret.
    public = gateway._token_request_body({"client_id": "cid"}, {"grant_type": "x"})
    assert "client_secret" not in public and "resource" not in public
