"""Run against actual upstream coercion, before and after deferred validation."""
from copy import deepcopy
from unittest.mock import patch

from tools.arg_coercion import coerce_tool_args
from tools.registry import registry
from tools.tool_search_validation import validate_deferred_call_args

params = {"type": "object", "properties": {
    "todos": {"type": "array", "items": {
        "type": "object", "properties": {
            "id": {"type": "string"}, "content": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "completed"]},
        }, "required": ["id", "content", "status"],
    }},
    "merge": {"type": "boolean"},
}}
schema = {"name": "todo_list", "parameters": params}
todo = {"id": "one", "content": "Build the app", "status": "pending"}
args = {"todos": {"item": {"item": [todo]}}, "merge": "false"}
with patch.object(registry, "get_schema", return_value=schema):
    original = deepcopy(args)
    assert validate_deferred_call_args("todo_list", args) is None
    assert args == original
    assert coerce_tool_args("todo_list", args) == {"todos": [todo], "merge": False}
    # Repair the wrapper but still reject missing fields and invalid enum values.
    error = validate_deferred_call_args("todo_list", {"todos": {"item": [{"id": "one"}]}})
    assert error and "content" in error
    error = validate_deferred_call_args("todo_list", {"todos": {"item": [{**todo, "status": "guess"}]}})
    assert error and "status" in error

# A tool that really accepts an `item` object must retain upstream's ordinary
# single-item wrapping, rather than treating business data as a transport array.
schema["parameters"] = {"type": "object", "properties": {
    "records": {"type": "array", "items": {"type": "object", "properties": {"item": {"type": "array"}}}},
}}
with patch.object(registry, "get_schema", return_value=schema):
    record = {"item": ["customer data"]}
    assert coerce_tool_args("example", {"records": record}) == {"records": [record]}
print("Schema-bound argument repair and deferred validation passed")
