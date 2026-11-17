"""Build-time gate for the Calliope memory scope (apply-hermes-memory-scope.py).
Runs inside the image with cwd /opt/hermes and the Hermes venv python."""
import inspect
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, "/opt/hermes")

from gateway import calliope_memory_scope as scope  # noqa: E402
from gateway.platforms import api_server_runs as runs  # noqa: E402
from agent.memory_manager import MemoryManager  # noqa: E402
import plugins.memory.hindsight as hindsight  # noqa: E402

ALICE, BOB = "person:alice@example.test", "person:bob@example.test"

# -- request field ----------------------------------------------------------
assert scope.parse(None) is None and scope.parse("text") is None and scope.parse({}) is None
parsed = scope.parse({"text": "  what did we decide?  ", "audience": [BOB, ALICE.upper(), "evil", 3]})
assert parsed == {"text": "what did we decide?", "audience": [ALICE, BOB]}, parsed
assert scope.parse({"text": None}) == {"text": ""}
assert scope.parse({"audience": [ALICE, scope.EVERYONE]}) == {"audience": [scope.EVERYONE]}
assert "audience" not in (scope.parse({"text": "x", "audience": [f"person:{i}@x" for i in range(65)]}) or {})

# -- tags and filters -------------------------------------------------------
room = {"text": "hi", "audience": [ALICE, BOB]}
assert scope.retain_tags(room) == [scope.SCOPED_TAG, ALICE, BOB]
assert scope.retain_tags({"text": "hi"}) == [] and scope.retain_tags(None) == []
# Shared memory is the default: one memory per agent, every conversation, except
# that a sensitive memory stays with the people who could see it.
insensitive = {"not": {"tags": [scope.SENSITIVE_TAG], "match": "any_strict"}}
shared_room = [{"or": [insensitive, {"tags": [ALICE, BOB], "match": "all_strict"}]}]
os.environ.pop("CALLIOPE_MEMORY_SCOPE", None)
assert scope.recall_tag_groups(room) == shared_room and scope.recall_tag_groups(None) == [insensitive]
os.environ["CALLIOPE_MEMORY_SCOPE"] = "shared"
assert scope.recall_tag_groups(room) == shared_room
os.environ["CALLIOPE_MEMORY_SCOPE"] = "private"  # unknown values fail closed
assert scope.recall_tag_groups(room) is not None
os.environ["CALLIOPE_MEMORY_SCOPE"] = "audience"
unscoped = {"not": {"tags": [scope.SCOPED_TAG], "match": "any_strict"}}
assert scope.recall_tag_groups(None) == [unscoped]
assert scope.recall_tag_groups(room) == [{"or": [
    unscoped, {"tags": [scope.EVERYONE], "match": "any_strict"},
    {"tags": [ALICE, BOB], "match": "all_strict"}]}]
assert scope.recall_tag_groups({"audience": [scope.EVERYONE]}) == [{"or": [
    unscoped, {"tags": [scope.EVERYONE], "match": "any_strict"}]}]

# -- /v1/runs carries and binds the field -----------------------------------
assert "calliope_memory" in runs._RunLaunch.__dataclass_fields__
assert '_calliope_memory_scope.parse(body.get("calliope_memory"))' in inspect.getsource(runs._handle_runs)
assert "bind_memory_scope(run.calliope_memory)" in inspect.getsource(runs._run_agent_sync)

# -- Calliope's own extractor can take over the writing ----------------------
assert scope.parse({"text": "x", "retain": False}) == {"text": "x", "retain": False}
assert "retain" not in scope.parse({"text": "x", "retain": True})
assert scope.retains()
with scope.bind({"text": "x", "retain": False}):
    assert not scope.retains()
with scope.bind({"text": "x"}):
    assert scope.retains()
assert "retains()" in inspect.getsource(MemoryManager.sync_all)

# -- memory providers read the person's words -------------------------------
full = "[Alice] what did we decide?\n\n[CALLIOPE SOLO MODE]\nHandle this yourself.\n[/CALLIOPE SOLO MODE]"
assert MemoryManager._strip_skill_scaffolding(full) == full  # unbound: upstream behaviour
with scope.bind({"text": "[Alice] what did we decide?", "audience": [ALICE]}):
    assert MemoryManager._strip_skill_scaffolding(full) == "[Alice] what did we decide?"
with scope.bind({"text": "", "audience": [ALICE]}):
    assert MemoryManager._strip_skill_scaffolding(full) == ""  # system turn: no read, no write
with scope.bind({"audience": [ALICE]}):
    assert MemoryManager._strip_skill_scaffolding(full) == full

# -- the HTTP path the provider uses (the bundled client cannot send tag_groups) --
import io
import json
import urllib.error
import urllib.request

sent, failures = [], [500]


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(request, timeout):
    sent.append((request.full_url, json.loads(request.data), request.get_header("Authorization"), timeout))
    if failures:
        raise urllib.error.HTTPError(request.full_url, failures.pop(), "rerank upstream", {}, None)
    return _Reply(json.dumps({"results": [{"text": "kept", "type": "observation", "weird-key": 1}]}).encode())


real_urlopen, urllib.request.urlopen = urllib.request.urlopen, _urlopen
http_provider = SimpleNamespace(_api_url="http://hindsight:8888/", _bank_id="team bank", _api_key="k", _timeout=7)
got = scope.recall(http_provider, {"bank_id": "team bank", "query": "q", "types": ["observation"],
                                   "tag_groups": scope.recall_tag_groups(room), "budget": None})
assert [r.text for r in got] == ["kept"] and len(sent) == 2, sent  # one retry after a 5xx
url, body, auth, timeout = sent[-1]
assert url == "http://hindsight:8888/v1/default/banks/team%20bank/memories/recall", url
assert body == {"query": "q", "types": ["observation"], "tag_groups": scope.recall_tag_groups(room)}, body
assert auth == "Bearer k" and timeout == 7.0
failures[:] = [400]
try:
    scope.recall(http_provider, {"query": "q"})
    raise AssertionError("a 4xx must not be retried or swallowed")
except urllib.error.HTTPError as exc:
    assert exc.code == 400 and len(sent) == 3, sent
urllib.request.urlopen = real_urlopen

# -- Hindsight tags writes and filters reads --------------------------------
calls = []


def fake_post(_provider, path, body):
    calls.append(("recall" if path == "memories/recall" else "reflect", body))
    return {"results": [{"text": "Priya manages Northwind.", "type": "observation"}], "text": "reflected"}


scope._post = fake_post


def provider():
    p = hindsight.HindsightMemoryProvider()
    p._bank_id, p._budget, p._recall_max_tokens = "hermes", "mid", 1024
    p._recall_tags, p._recall_types, p._retain_tags = None, ["observation"], []
    p._observation_scopes = None
    return p


bare = provider()
assert [r.text for r in bare._recall("q")] == ["Priya manages Northwind."]
assert calls[-1][1]["tag_groups"] == [unscoped] and calls[-1][1]["types"] == ["observation"], calls[-1]
assert "bank_id" not in calls[-1][1]

p = provider()
with scope.bind(room):
    p._recall("q")
    assert calls[-1][1]["tag_groups"] == scope.recall_tag_groups(room)
    assert p._reflect("q") == "reflected"
    assert calls[-1][0] == "reflect" and calls[-1][1]["tag_groups"] == scope.recall_tag_groups(room)
    item = p._build_retain_kwargs("fact", tags=["mine"])
    assert set(item["tags"]) >= {"mine", scope.SCOPED_TAG, ALICE, BOB}, item
    p._session_id, p._parent_session_id, p._bank_id = "s1", "", "hermes"
    p._retain_async, p._retain_context, p._turn_index = False, None, 0
    p._build_metadata = lambda **_k: {}
    job = p._make_turn_retain_job(["{}"], document_id="d1", update_mode="append", label="retain", track_ops=False)

shipped = []
p._retain_batch = lambda item, **_k: shipped.append(item)
writer = threading.Thread(target=job)  # the retain writer runs outside the run's context
writer.start()
writer.join()
assert set(shipped[0]["tags"]) >= {"session:s1", scope.SCOPED_TAG, ALICE, BOB}, shipped
p._recall("q")  # the same per-run provider keeps the scope it learned in the run
assert calls[-1][1]["tag_groups"] == scope.recall_tag_groups(room)

# Shared: the same run recalls and reflects over everything, still tagging writes.
os.environ["CALLIOPE_MEMORY_SCOPE"] = "shared"
with scope.bind(room):
    p._recall("q")
    assert calls[-1][1]["tag_groups"] == shared_room, calls[-1]
    p._reflect("q")
    assert calls[-1][0] == "reflect" and calls[-1][1]["tag_groups"] == shared_room
    assert set(p._build_retain_kwargs("fact")["tags"]) >= {scope.SCOPED_TAG, ALICE, BOB}

# -- observations merge across chats (per audience when scoped), not per chat --
os.environ["CALLIOPE_MEMORY_SCOPE"] = "shared"
assert scope.observation_scopes(room) == "shared" and scope.observation_scopes(None) is None
with scope.bind(room):
    assert p._build_retain_kwargs("fact")["observation_scopes"] == "shared"
os.environ["CALLIOPE_MEMORY_SCOPE"] = "audience"
assert scope.observation_scopes(room) == [[scope.SCOPED_TAG, ALICE, BOB]]
with scope.bind(room):
    assert p._build_retain_kwargs("fact")["observation_scopes"] == [[scope.SCOPED_TAG, ALICE, BOB]]
plain = provider()
plain._observation_scopes = ["mine"]  # a run without a scope keeps the configured scopes
assert plain._build_retain_kwargs("fact")["observation_scopes"] == ["mine"]
os.environ["CALLIOPE_MEMORY_SCOPE"] = "shared"

# -- Hindsight 1.x answers 404 for a bank nothing has been written to yet ----
import io
import urllib.error
import urllib.request

_real_urlopen = urllib.request.urlopen
opened = []


def missing_bank(request, timeout=None):
    opened.append((request.get_method(), request.full_url))
    raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"detail":"Bank not found"}'))


urllib.request.urlopen = missing_bank
fresh = provider()
fresh._bank_id = "never-written"
assert scope._request(fresh, "POST", "memories/recall", {"query": "q"}) == {}
assert scope._request(fresh, "POST", "reflect", {"query": "q"}) == {}
try:
    scope._request(fresh, "GET", "config")
    raise AssertionError("a 404 on config must still raise")
except urllib.error.HTTPError as exc:
    assert exc.code == 404
assert opened[-1][1].endswith("/banks/never-written/config"), opened
urllib.request.urlopen = _real_urlopen

# -- the bank gets its retain mission once per process ----------------------
sent_requests = []


def fake_request(_provider, method, path, body=None):
    sent_requests.append((method, path, body))
    return {"config": {"retain_mission": None}} if method == "GET" else {}


scope._request = fake_request
scope.ensure_retain_mission(p)  # p learned the room's scope during its run
assert sent_requests == [("GET", "config", None),
                         ("PATCH", "config", {"updates": {"retain_mission": scope.RETAIN_MISSION}})], sent_requests
scope.ensure_retain_mission(p)
assert len(sent_requests) == 2  # once per process
other = provider()
other._bank_id = "calliope-skeptic"
scope.ensure_retain_mission(other)  # no Calliope scope and no configured mission: left alone
assert len(sent_requests) == 2
named = provider()
named._bank_id, named._bank_retain_mission = "named", "Keep pricing decisions."
scope.ensure_retain_mission(named)
assert sent_requests[-1] == ("PATCH", "config", {"updates": {"retain_mission": "Keep pricing decisions."}})
assert "ensure_retain_mission(self)" in inspect.getsource(hindsight.HindsightMemoryProvider._make_turn_retain_job)


def fake_missing_bank(_provider, method, path, body=None):
    sent_requests.append((method, path, body))
    if method == "GET":
        raise urllib.error.HTTPError("x", 404, "Not Found", {}, io.BytesIO(b"{}"))
    return {}


scope._request = fake_missing_bank
del sent_requests[:]
first = provider()
first._bank_id, first._bank_retain_mission = "brand-new", "Keep pricing decisions."
scope.ensure_retain_mission(first)  # a fresh install's bank is created, then given its mission
assert sent_requests == [("GET", "config", None), ("PUT", "", {}),
                         ("PATCH", "config", {"updates": {"retain_mission": "Keep pricing decisions."}})], sent_requests

print("calliope memory scope gate: OK")
