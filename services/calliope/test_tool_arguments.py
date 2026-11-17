from copy import deepcopy

import pytest
import tool_arguments


def test_nested_wrappers_repair_only_typed_fields_without_mutating_input():
    schema = {"type": "object", "properties": {
        "values": {"type": "array", "items": {"type": "number"}},
        "enabled": {"type": "boolean"}, "opaque": {"type": "object"},
    }}
    args = {"values": {"item": {"item": ["1", "2.5"]}}, "enabled": "false",
            "opaque": {"values": {"item": ["003", "true"]}}}
    original = deepcopy(args)
    assert tool_arguments.normalize(args, schema) == {
        "values": [1, 2.5], "enabled": False, "opaque": original["opaque"],
    }
    assert args == original


@pytest.mark.parametrize("schema", [
    {"type": "array", "items": {}},
    {"type": "array", "items": {"type": "object"}},
    {"type": ["array", "object"], "items": {"type": "string"}},
    {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "object"}]},
])
def test_ambiguous_business_objects_are_never_unwrapped(schema):
    assert tool_arguments.normalize({"item": ["x"]}, schema) == {"item": ["x"]}


def test_nullable_refs_and_sibling_properties():
    schema = {"$defs": {"strings": {"type": "array", "items": {"type": "string"}}},
              "anyOf": [{"type": "object"}, {"type": "null"}],
              "properties": {"names": {"$ref": "#/$defs/strings"}}}
    assert tool_arguments.normalize({"names": {"item": ["x"]}}, schema) == {"names": ["x"]}
    assert tool_arguments.normalize(None, schema) is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "true", "12px", "1.5", "01"])
def test_invalid_numbers_are_left_for_validation(value):
    assert tool_arguments.normalize(value, {"type": "integer"}) == value


def test_bad_wrapper_and_siblings_are_not_discarded():
    schema = {"type": "array", "items": {"type": "string"}}
    for value in ({"item": 42}, {"item": ["x"], "metadata": 1}):
        assert tool_arguments.normalize(value, schema) == value


def test_linked_item_wrappers_preserve_order_and_do_not_flatten_native_arrays():
    schema = {"type": "array", "items": {"type": "string"}}
    value = {'item': ['a', {'item': ['b', {'item': 'c'}]}]}
    assert tool_arguments.normalize(value, schema) == ['a', 'b', 'c']
    assert tool_arguments.normalize(['a', ['b']], schema) == ['a', ['b']]


@pytest.mark.parametrize('schema', [None, {'$ref': 1}, {'type': {}}, {'anyOf': [True]}, {'type': 'object', 'properties': []}])
def test_malformed_schemas_do_not_guess_or_break_dispatch(schema):
    value = {'item': ['literal data']}
    assert tool_arguments.normalize(value, schema) == value


def test_external_schema_references_are_not_fetched(monkeypatch):
    import urllib.request
    def no_network(*args, **kwargs):
        pytest.fail('argument normalization must not fetch a schema')
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    value = {'item': ['x']}
    schema = {'type': 'array', 'items': {'$ref': 'https://schema.invalid/example.json'}}
    assert tool_arguments.normalize(value, schema) == value
