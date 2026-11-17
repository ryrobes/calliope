"""Hindsight 1.x answers 404 for a bank nothing has been written to yet.

0.9.0 answered recall and config on such a bank with an empty 200, so every
caller treated a 404 as an outage. A fresh install, a person with no memory
yet, or a desktop whose first lesson is still being retained is a miss.
"""
import httpx
import pytest

import desktop_memory
import memory_claims
import memory_routes
import server

KNOWN = "known-bank"


def fake_hindsight(requests):
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        path = request.url.path
        if f"/banks/{KNOWN}" in path or request.method == "PUT":
            if path.endswith("/config"):
                return httpx.Response(200, json={"config": {}})
            return httpx.Response(200, json={"results": [], "success": True})
        return httpx.Response(404, json={"detail": "Bank not found"})
    return httpx.MockTransport(handle)


@pytest.fixture
def requests(monkeypatch):
    seen = []
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs.pop("trust_env", None)
        return real(*args, transport=fake_hindsight(seen), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setenv("RVBBIT_HINDSIGHT_ENDPOINT", "http://hindsight:8888")
    return seen


def test_routes_recall_on_a_never_written_bank_is_empty(requests, monkeypatch):
    monkeypatch.setattr(memory_routes, "ROUTES_BANK", "calliope-routes-fresh")
    assert memory_routes._hindsight("POST", "/memories/recall", {"query": "q"}) == {"results": []}
    with pytest.raises(httpx.HTTPStatusError):  # anything else is still an error
        memory_routes._hindsight("GET", "/config")


def test_claims_create_a_never_written_bank_before_setting_their_strategy(monkeypatch):
    seen, state = [], {"created": False}

    def handle(request):
        seen.append(request.method)
        if request.method == "PUT":
            state["created"] = True
            return httpx.Response(200, json={})
        if not state["created"]:
            return httpx.Response(404, json={"detail": "Bank not found"})
        return httpx.Response(200, json={"config": {}} if request.method == "GET" else {})

    real_client = httpx.Client

    def client(*args, **kwargs):
        kwargs.pop("trust_env", None)
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(memory_claims, "BANK", "hermes-fresh")
    monkeypatch.setattr(memory_claims, "_STRATEGY_READY", False)
    memory_claims.ensure_strategy()
    assert seen == ["GET", "PUT", "GET", "PATCH"] and memory_claims._STRATEGY_READY is True


def test_desktop_recall_on_a_never_written_bank_is_empty(requests):
    assert desktop_memory.request("POST", "calliope-computer-fresh", "memories/recall", {"query": "q"}) == {"results": []}
    assert desktop_memory.request("POST", KNOWN, "memories/recall", {"query": "q"}) == {"results": [], "success": True}


def test_curation_find_in_a_never_written_bank_finds_nothing(requests):
    assert server._hindsight_request("POST", "/v1/default/banks/fresh/memories/recall", {"query": "q"}) == {"results": []}
    with pytest.raises(RuntimeError):
        server._hindsight_request("PATCH", "/v1/default/banks/fresh/memories/abc", {"state": "valid"})
