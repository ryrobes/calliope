from unittest.mock import patch
from types import SimpleNamespace

from tools.registry import registry
from tools.tool_search import resolve_underlying_call


RUN_SQL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "mcp__Warehouse__run_sql",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
}


def resolve(payload, schema=RUN_SQL_SCHEMA):
    with (
        patch.object(registry, "get_schema", return_value=schema),
        patch.object(
            registry,
            "get_entry",
            return_value=SimpleNamespace(toolset="mcp-Warehouse"),
        ),
    ):
        return resolve_underlying_call(payload)


name, arguments, error = resolve({
    "name": "mcp__Warehouse__run_sql",
    "arguments": {"arguments": {"sql": "SELECT 1"}},
})
assert name == "mcp__Warehouse__run_sql"
assert arguments == {"sql": "SELECT 1"}
assert error is None

# Correct calls remain byte-for-byte equivalent at the argument level.
correct = {"sql": "SELECT 1", "limit": "25"}
assert resolve({
    "name": "mcp__Warehouse__run_sql",
    "arguments": correct,
})[1] is correct

# Never unwrap when the underlying tool legitimately owns an arguments field.
legitimate_arguments_schema = {
    "type": "function",
    "function": {
        "name": "mcp__Example__invoke",
        "parameters": {
            "type": "object",
            "properties": {"arguments": {"type": "object"}},
        },
    },
}
wrapped = {"arguments": {"sql": "SELECT 1"}}
assert resolve({
    "name": "mcp__Example__invoke",
    "arguments": wrapped,
}, legitimate_arguments_schema)[1] is wrapped

# Unknown or non-matching target parameters are not guesses.
assert resolve({
    "name": "mcp__Warehouse__run_sql",
    "arguments": {"arguments": {"surprise": True}},
})[1] == {"arguments": {"surprise": True}}
assert resolve({
    "name": "mcp__Warehouse__run_sql",
    "arguments": {"arguments": {"sql": "SELECT 1"}},
}, None)[1] == {"arguments": {"sql": "SELECT 1"}}

# Sibling keys prove this is not the accidental single-wrapper shape.
with_sibling = {"arguments": {"sql": "SELECT 1"}, "limit": "25"}
assert resolve({
    "name": "mcp__Warehouse__run_sql",
    "arguments": with_sibling,
})[1] is with_sibling

print("Hermes deferred-tool argument unwrap tests passed")
