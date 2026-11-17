"""Contract test for hermes-tool-baffles.patch (runs in the image build).

Two redirections in the tool loop, measured on the pilot's session mirror
(2026-09-10): reasoning tokens were 0.1–4% of spend; the burn was call count
times replayed context. The echo baffle serves a THIRD identical read-only
call from the result the model already has; the narration baffle asks for
one status paragraph after 30 consecutive silent tool turns. Neither stops
the turn. This exercises the module-level helpers the patch adds to
agent.conversation_loop, plus the compressor's recognition of the nudge.
"""

import json
from types import SimpleNamespace

from agent.context_compressor import ContextCompressor
from agent.conversation_loop import (
    _ECHO_BAFFLE_THIRD_CALL,
    _LENGTH_CONTINUATION_DROPPED_TOOLS_PREFIX,
    _NARRATION_NUDGE_EVERY,
    _echo_baffle_record,
    _echo_baffle_split,
    _echo_is_read_only,
    _echo_key,
    _narration_nudge_due,
    _narration_nudge_text,
    _narration_streak_update,
)


def call(name, args, call_id):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ── keys: argument order is irrelevant, the deferred wrapper is unwrapped ──
direct = call("mcp__Warehouse__run_sql", {"sql": "select 1", "limit": 5}, "a")
reordered = call("mcp__Warehouse__run_sql", '{"limit": 5, "sql": "select 1"}', "b")
wrapped = call(
    "tool_call",
    {"name": "mcp__Warehouse__run_sql", "arguments": {"limit": 5, "sql": "select 1"}},
    "c",
)
assert _echo_key(direct) == _echo_key(reordered) == _echo_key(wrapped)
assert _echo_key(direct)[0] == "mcp__Warehouse__run_sql"
assert _echo_key(call("", {}, "z")) is None

# ── read-only classification ─────────────────────────────────────────────
assert _echo_is_read_only(direct)
assert _echo_is_read_only(call("mcp__Specialist__get_metric", {"name": "leads"}, "d"))
assert _echo_is_read_only(call("mcp__Warehouse__metric", {}, "e"))
assert _echo_is_read_only(call("tool_describe", {"name": "x"}, "f"))
assert not _echo_is_read_only(call("write_file", {"path": "a"}, "g"))
assert not _echo_is_read_only(call("terminal", {"command": "ls"}, "h"))
assert not _echo_is_read_only(
    call("tool_call", {"name": "mcp__Warehouse__create_metric", "arguments": {}}, "i")
)
assert not _echo_is_read_only(call("mcp__Warehouse__update_live_app", {}, "j"))


def tool_result(tc, content):
    return {"role": "tool", "name": tc.function.name, "tool_call_id": tc.id, "content": content}


# ── first two identical reads execute; the third is served ───────────────
agent = SimpleNamespace()
messages = []
served, execute = _echo_baffle_split(agent, [direct])
assert served == [] and execute == [direct]
messages.append(tool_result(direct, "rows: 1"))
assert _echo_baffle_record(agent, execute, messages) == 1

served, execute = _echo_baffle_split(agent, [reordered])
assert served == [] and execute == [reordered], "second identical call still runs"
messages.append(tool_result(reordered, "rows: 1"))
_echo_baffle_record(agent, execute, messages)

served, execute = _echo_baffle_split(agent, [wrapped])
assert execute == [] and len(served) == 1, "third identical call is served"
served_tc, content = served[0]
assert served_tc is wrapped
assert content.startswith("[Echo baffle]")
assert f"#{_ECHO_BAFFLE_THIRD_CALL}" in content and "mcp__Warehouse__run_sql" in content
assert content.endswith("rows: 1"), "the served body is the earlier result, unchanged"

# a served result must never become the cached body for a later serve
messages.append(tool_result(wrapped, content))
assert _echo_baffle_record(agent, [wrapped], messages) == 0
assert agent._echo_baffle["results"][_echo_key(direct)] == "rows: 1"

# ── a write clears the session's echo state ──────────────────────────────
write = call("write_file", {"path": "x", "content": "y"}, "w")
served, execute = _echo_baffle_split(agent, [write, direct])
assert served == [] and execute == [write, direct], "after a write the read runs again"
assert agent._echo_baffle["counts"][_echo_key(direct)] == 1

# ── mixed batch: only the echoed call is served, others execute ──────────
agent2 = SimpleNamespace()
msgs2 = []
for n in range(2):
    _, ex = _echo_baffle_split(agent2, [direct])
    msgs2.append(tool_result(direct, "rows: 1"))
    _echo_baffle_record(agent2, ex, msgs2)
other = call("mcp__Warehouse__search_data", {"q": "leads"}, "o")
served, execute = _echo_baffle_split(agent2, [direct, other])
assert [tc.id for tc, _ in served] == ["a"] and [tc.id for tc in execute] == ["o"]

# ── narration: 30 silent tool turns, then every 30 ───────────────────────
agent3 = SimpleNamespace()
streak = 0
for i in range(1, _NARRATION_NUDGE_EVERY + 1):
    streak = _narration_streak_update(agent3, True, api_call_count=i)
assert streak == _NARRATION_NUDGE_EVERY and _narration_nudge_due(streak)
assert not _narration_nudge_due(_NARRATION_NUDGE_EVERY - 1)
assert not _narration_nudge_due(_NARRATION_NUDGE_EVERY + 5)
assert _narration_nudge_due(2 * _NARRATION_NUDGE_EVERY)
# visible text resets the streak; a new turn (api_call_count 1) starts over
assert _narration_streak_update(agent3, False, api_call_count=40) == 0
_narration_streak_update(agent3, True, api_call_count=41)
assert _narration_streak_update(agent3, True, api_call_count=1) == 1

# ── the nudge is compressor-recognized ephemeral scaffolding ────────────
nudge = _narration_nudge_text(_NARRATION_NUDGE_EVERY)
assert nudge.startswith(_LENGTH_CONTINUATION_DROPPED_TOOLS_PREFIX)
assert "30 consecutive turns" in nudge and "one short paragraph" in nudge
assert ContextCompressor._is_synthetic_compression_user_turn(
    {"role": "user", "content": nudge}
), "the pinned compressor must treat the nudge as internal, not a human turn"
assert not ContextCompressor._is_synthetic_compression_user_turn(
    {"role": "user", "content": "what did we learn?"}
)

# ── the loop-level seam left agent/conversation_loop.py in 0.21.4 ───────
# run_tool_round (agent/turn_tool_round.py) is where the echo baffle wraps
# _execute_tool_calls and the narration streak is counted. A `git apply
# --include=` narrowed to conversation_loop.py drops that hunk silently.
import inspect

from agent import turn_tool_round

_round_src = inspect.getsource(turn_tool_round)
assert "_echo_baffle_split" in _round_src and "_narration_nudge_due" in _round_src, \
    "tool-baffle seams missing from agent/turn_tool_round.py (patch hunk dropped?)"

print("hermes-tool-baffles: OK")
