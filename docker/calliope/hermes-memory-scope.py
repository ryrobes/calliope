"""Calliope memory scope for one /v1/runs execution (Hindsight).

Calliope sends ``calliope_memory`` with a run: ``text`` is what the person
actually said this turn, and ``audience`` names everyone who can read the
conversation as tag tokens (``person:<email>``, ``team:<id>`` or
``audience:everyone``). The turn's user message also carries Calliope's per-turn
context and tool guidance; without the scope Hindsight stored that scaffolding
as "facts" and recalled on it.

* Memory reads and writes use ``text`` instead of the whole message; an empty
  text (a system-driven run) writes nothing and recalls nothing.
* New memories always carry ``calliope:scoped`` plus the audience tokens.
* ``retain: false`` means Calliope writes this turn's memories itself (its own
  extractor): recall runs as usual, Hindsight's own retain is skipped.
* A memory tagged ``calliope:sensitive`` (Calliope's extractor marks personal or
  restricted remarks) is recalled only where everyone present was in its
  source's audience, even when memory is otherwise shared.
* ``CALLIOPE_MEMORY_SCOPE`` decides what recall may show. ``shared`` (the
  default) keeps one memory per agent across every conversation: what Callie
  worked out with one person helps the next. ``audience`` shows a memory only
  when everyone in the current audience could read its source (its tokens cover
  the current audience, or it was shared with everyone); memories written
  without the tags stay visible as they always were, and a run without a scope
  sees only those. Any other value is treated as ``audience``.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, Callable, Optional

SCOPED_TAG = "calliope:scoped"
SENSITIVE_TAG = "calliope:sensitive"
EVERYONE = "audience:everyone"
_TOKEN_PREFIXES = ("person:", "team:", "audience:")
_MAX_TOKENS = 64
_MAX_TEXT = 20000

_scope: ContextVar[Optional[dict]] = ContextVar("calliope_memory_scope", default=None)


def parse(raw: Any) -> Optional[dict]:
    """Normalize the request field; None when absent or unusable."""
    if not isinstance(raw, dict):
        return None
    scope: dict = {}
    if "text" in raw:
        text = raw.get("text")
        scope["text"] = text.strip()[:_MAX_TEXT] if isinstance(text, str) else ""
    if raw.get("retain") is False:
        scope["retain"] = False
    tokens = raw.get("audience")
    if isinstance(tokens, list):
        audience = sorted({
            token.strip().lower() for token in tokens
            if isinstance(token, str) and token.strip().lower().startswith(_TOKEN_PREFIXES)
        })
        if EVERYONE in audience:
            audience = [EVERYONE]
        if audience and len(audience) <= _MAX_TOKENS:
            scope["audience"] = audience
    return scope or None


def current() -> Optional[dict]:
    return _scope.get()


@contextmanager
def bind(scope: Optional[dict]):
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


def retains() -> bool:
    """False when Calliope writes this turn's memories itself."""
    scope = _scope.get()
    return not (scope is not None and scope.get("retain") is False)


def memory_text(message: Any, default: Callable[[Any], Any]) -> Any:
    """What memory providers read for this turn (MemoryManager seam)."""
    scope = _scope.get()
    if scope is not None and "text" in scope:
        return scope["text"]
    return default(message)


def retain_tags(scope: Optional[dict]) -> list[str]:
    audience = (scope or {}).get("audience") or []
    return [SCOPED_TAG, *audience] if audience else []


def scoped_recall() -> bool:
    return os.environ.get("CALLIOPE_MEMORY_SCOPE", "shared").strip().lower() not in ("", "shared")


def recall_tag_groups(scope: Optional[dict]) -> Optional[list[dict]]:
    """The recall filter. Shared memory still keeps sensitive memories to the
    people who could see them."""
    audience = (scope or {}).get("audience") or []
    if not scoped_recall():
        insensitive = {"not": {"tags": [SENSITIVE_TAG], "match": "any_strict"}}
        if not audience:
            return [insensitive]
        return [{"or": [insensitive, {"tags": list(audience), "match": "all_strict"}]}]
    unscoped = {"not": {"tags": [SCOPED_TAG], "match": "any_strict"}}
    if not audience:
        return [unscoped]
    branches = [unscoped, {"tags": [EVERYONE], "match": "any_strict"}]
    if audience != [EVERYONE]:
        branches.append({"tags": list(audience), "match": "all_strict"})
    return [{"or": branches}]


def _request(provider: Any, method: str, path: str, body: Optional[dict] = None) -> dict:
    """Call the provider's Hindsight bank with its own URL, key and timeout."""
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    base = str(getattr(provider, "_api_url", "") or "").rstrip("/")
    bank = urllib.parse.quote(str(provider._bank_id), safe="")
    headers = {"Content-Type": "application/json"}
    if getattr(provider, "_api_key", None):
        headers["Authorization"] = f"Bearer {provider._api_key}"
    request = urllib.request.Request(
        f"{base}/v1/default/banks/{bank}" + (f"/{path}" if path else ""),
        data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
    timeout = float(getattr(provider, "_timeout", 0) or 30)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "POST" and path in ("memories/recall", "reflect"):
                return {}  # Hindsight 1.x answers 404 for a bank nothing has been written to yet
            # Hindsight fails the whole recall when its reranker's upstream
            # does; one quick retry rides out a dropped connection.
            if attempt == 2 or exc.code < 500:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
    return {}


# hindsight-client 0.6.1 cannot send tag_groups (it imports a model module the
# package does not ship), so filtered recall and reflect go over HTTP directly.
def _post(provider: Any, path: str, body: dict) -> dict:
    return _request(provider, "POST", path, body)


def recall(provider: Any, kwargs: dict) -> list:
    body = {key: value for key, value in kwargs.items() if key != "bank_id" and value is not None}
    results = _post(provider, "memories/recall", body).get("results") or []
    return [SimpleNamespace(**{k: v for k, v in item.items() if k.isidentifier()})
            for item in results if isinstance(item, dict)]


def reflect(provider: Any, body: dict) -> Optional[str]:
    return _post(provider, "reflect", {k: v for k, v in body.items() if v is not None}).get("text")


def observation_scopes(scope: Optional[dict]) -> Any:
    """Where Hindsight merges a new memory into observations. Its default merges
    within the memory's whole tag set, and every memory carries its chat's
    session tag, so the same fact became one observation per chat. Shared
    memory merges across the bank; scoped memory merges per audience."""
    audience = (scope or {}).get("audience") or []
    if not audience:
        return None
    return [[SCOPED_TAG, *audience]] if scoped_recall() else "shared"


RETAIN_MISSION = (
    "Record durable facts about the business, its data, its people, and their decisions, "
    "preferences and plans, and note who stated each one, even when the data may disagree. "
    "Record what the assistant built or found for people. Skip what the assistant says about "
    "its own memory, knowledge or uncertainty (for example that it does not know something or "
    "that its notes conflict), facts the assistant only repeated from memory or earlier context, "
    "and instructions about tools, formatting or how the assistant should work."
)
_missions_applied: set = set()


def ensure_retain_mission(provider: Any) -> None:
    """Give the bank its retain mission once per process; Hindsight's extractor
    reads it on every retain. The upstream provider reads ``bank_retain_mission``
    but never sends it. A Calliope chat bank gets RETAIN_MISSION unless its
    config names one; other banks are left alone."""
    mission = getattr(provider, "_bank_retain_mission", None) or (
        RETAIN_MISSION if provider_scope(provider) else None)
    key = (str(getattr(provider, "_api_url", "")), str(provider._bank_id), mission)
    if not mission or key in _missions_applied:
        return
    import urllib.error

    try:
        current = ((_request(provider, "GET", "config") or {}).get("config") or {}).get("retain_mission")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        # Hindsight 1.x answers 404 for a bank nothing has been written to yet.
        _request(provider, "PUT", "", {})
        current = None
    if current != mission:
        _request(provider, "PATCH", "config", {"updates": {"retain_mission": mission}})
    _missions_applied.add(key)


def provider_scope(provider: Any) -> Optional[dict]:
    """The run's scope, remembered on the provider instance for work that runs
    after the run's context is gone (the retain writer thread)."""
    scope = _scope.get()
    if scope is not None:
        try:
            provider._calliope_memory_scope = scope
        except AttributeError:
            pass
        return scope
    return getattr(provider, "_calliope_memory_scope", None)
