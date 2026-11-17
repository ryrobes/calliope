"""Build-time check for hermes-tool-result-wire-name.patch.

Hermes's ``tool_call`` wrapper expands an MCP call before execution and records
the expanded tool (``mcp__Warehouse__run_sql``) as the result's ``name`` /
``tool_name``, while the assistant's wire call was ``tool_call``. Gemini rejected
that mismatch. Since Hermes 0.21.4 the chat-completions transport strips ``name``
from every tool result on the wire (strict providers reject it outright), so the
provider resolves the result by ``tool_call_id`` and no stale display identity can
reach it. Re-ported to v0.21.4 (d337b736) on 2026-09-23: the assertions moved
from "name is rewritten to the wire name" to "no name reaches the wire".
"""

from agent.transports.chat_completions import ChatCompletionsTransport


def convert(messages):
    return ChatCompletionsTransport.convert_messages(
        None,
        messages,
        model="google/gemini-3.7-flash",
    )


parallel_messages = [
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_one",
                "type": "function",
                "function": {"name": "tool_call", "arguments": "{}"},
            },
            {
                "id": "call_two",
                "type": "function",
                "function": {"name": "tool_call", "arguments": "{}"},
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_one",
        "name": "mcp__Warehouse__run_sql",
        "tool_name": "mcp__Warehouse__run_sql",
        "content": "first result",
    },
    {
        "role": "tool",
        "tool_call_id": "call_two",
        "name": "mcp__Warehouse__dashboard_template",
        "tool_name": "mcp__Warehouse__dashboard_template",
        "content": "second result",
    },
]

converted = convert(parallel_messages)
assert converted is not parallel_messages
# The originating call keeps its wire name; the results carry no stale identity.
assert converted[0]["tool_calls"][0]["function"]["name"] == "tool_call"
assert converted[0]["tool_calls"][1]["function"]["name"] == "tool_call"
for i in (1, 2):
    assert "name" not in converted[i], converted[i]
    assert "tool_name" not in converted[i], converted[i]
assert converted[1]["tool_call_id"] == "call_one"
assert converted[2]["tool_call_id"] == "call_two"
assert converted[1]["content"] == "first result"

# Provider projection must not corrupt the durable/display form.
assert parallel_messages[1]["name"] == "mcp__Warehouse__run_sql"
assert parallel_messages[1]["tool_name"] == "mcp__Warehouse__run_sql"

# Native calls already using their original name reach the wire the same way:
# resolved by tool_call_id, with the durable form untouched.
native_messages = [
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "native_one",
                "type": "function",
                "function": {"name": "run_sql", "arguments": "{}"},
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "native_one",
        "name": "run_sql",
        "content": "ok",
    },
]
native = convert(native_messages)
assert "name" not in native[1]
assert native[1]["tool_call_id"] == "native_one"
assert native_messages[1]["name"] == "run_sql"

# A persisted result with the display-only tool_name is normalized too.
persisted_messages = [
    parallel_messages[0],
    dict(parallel_messages[1]),
]
persisted = convert(persisted_messages)
assert "name" not in persisted[1]
assert "tool_name" not in persisted[1]

print("Hermes tool-result wire-name tests passed")
