"""Calliope's "fork from here": copy a session's transcript up to a moment.

Upstream ``POST /api/sessions/{id}/fork`` copies the whole live history and ends
the source session as "branched". Calliope forks a chat at a chosen message and
keeps the original going, so the request body may add:

* ``until_ts`` (epoch seconds): copy only messages written at or before it;
* ``keep_source: true``: leave the source session open;
* ``replacements`` ({old: new}): whole-token text replacements applied to the
  copy (the original chat's artifact slugs become the fork's own copies).

With any of those keys the copy is the session's full transcript (compacted
history included, compaction summaries left out), so the fork starts with what
was said up to that moment; Hermes compacts it again on its own schedule.
Without them the upstream behaviour is unchanged.
"""
from __future__ import annotations

import re
from typing import Any

CALLIOPE_KEYS = ("until_ts", "keep_source", "replacements")
_DROP = {"id", "session_id", "active", "compacted", "_compressed_summary", "_row_id",
         "display_identity", "display_order"}


def requested(body: dict) -> bool:
    return isinstance(body, dict) and any(key in body for key in CALLIOPE_KEYS)


def keeps_source(body: dict) -> bool:
    return isinstance(body, dict) and body.get("keep_source") is True


def _pattern(replacements: dict) -> tuple[re.Pattern | None, dict]:
    pairs = {str(k): str(v) for k, v in (replacements or {}).items() if str(k or "").strip()}
    if not pairs:
        return None, {}
    alternatives = "|".join(re.escape(key) for key in sorted(pairs, key=len, reverse=True))
    # Whole tokens only: "orders" must not rewrite "orders-2024" or "regional-orders".
    return re.compile(rf"(?<![A-Za-z0-9_-])(?:{alternatives})(?![A-Za-z0-9_-])"), pairs


def rewrite(value: Any, pattern: re.Pattern | None, pairs: dict) -> Any:
    if pattern is None:
        return value
    if isinstance(value, str):
        return pattern.sub(lambda m: pairs[m.group(0)], value)
    if isinstance(value, list):
        return [rewrite(item, pattern, pairs) for item in value]
    if isinstance(value, dict):
        return {key: rewrite(item, pattern, pairs) for key, item in value.items()}
    return value


def messages_for_fork(db: Any, source_id: str, body: dict) -> list:
    """The messages a fork starts with."""
    if not requested(body):
        return db.get_messages(source_id)
    until = body.get("until_ts")
    until = None if until is None else float(until)
    pattern, pairs = _pattern(body.get("replacements") or {})
    out, called, answered = [], set(), set()
    for message in db.get_messages(source_id, include_compacted=True):
        if message.get("_compressed_summary"):
            continue  # the copy holds what was said, not an earlier summary of it
        if until is not None and float(message.get("timestamp") or 0) > until:
            continue
        if message.get("role") == "assistant":
            ids = {str(call["id"]) for call in message.get("tool_calls") or []
                   if isinstance(call, dict) and call.get("id")}
            if ids and ids <= called:
                continue  # a compaction carried this call forward; the original is already here
            called |= ids
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            # Compaction carries "[Retained tool evidence]" copies of earlier results
            # forward; keep the original, and never a result whose call is not here.
            if not call_id or call_id in answered or call_id not in called:
                continue
            answered.add(call_id)
        copy = {key: value for key, value in message.items() if key not in _DROP}
        for key in ("content", "api_content", "tool_calls", "display_metadata"):
            if key in copy:
                copy[key] = rewrite(copy[key], pattern, pairs)
        out.append(copy)
    return out
