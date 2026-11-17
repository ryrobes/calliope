"""Conservative repairs at known schema positions, shared with hosted Hermes.

Never infer types from field names or walk arbitrary user JSON. In particular,
an ``item`` property is not a transport wrapper if it is valid business data.
"""
import json
import math

from jsonschema import Draft202012Validator
from referencing import Registry


def normalize(value, schema, *, _root=None, _depth=0):
    if not isinstance(schema, dict) or _depth >= 40:
        return value
    root = schema if _root is None else _root
    ref = schema.get("$ref")
    if ref:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return value
        target = root
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            return value
        return normalize(value, target, _root=root, _depth=_depth + 1)
    expected = schema.get("type")
    if isinstance(expected, list):
        kinds = [kind for kind in expected if kind != "null"]
        expected = kinds[0] if len(kinds) == 1 else None
    if expected is not None and not isinstance(expected, str):
        return value
    if expected is None:
        # A nullable field has one concrete branch. Ambiguous unions stay as-is.
        branches = schema.get("anyOf", schema.get("oneOf", []))
        if not isinstance(branches, list) or any(not isinstance(branch, dict) for branch in branches):
            return value
        concrete = [branch for branch in branches if branch.get("type") != "null"]
        if len(concrete) == 1:
            siblings = {key: item for key, item in schema.items() if key not in {"anyOf", "oneOf"}}
            return normalize(value, {**concrete[0], **siblings}, _root=root, _depth=_depth + 1)
    if expected == "array":
        items = schema.get("items")
        if not isinstance(items, dict) or not items:
            return value
        original = value
        item_schema = {"$defs": root.get("$defs", {}), **items}
        # An empty registry resolves local refs but never fetches remote schemas.
        try:
            validator = Draft202012Validator(item_schema, registry=Registry())
        except Exception:
            return original
        for _ in range(8):
            if not isinstance(value, dict) or set(value) != {"item"}:
                break
            # Some array elements legitimately contain an `item` field. Do not
            # guess in that case, even when the field happens to hold a list.
            try:
                if validator.is_valid(value):
                    break
            except Exception:
                return original
            value = value["item"]
        if not isinstance(value, list):
            if value is not original:
                item = normalize(value, items, _root=root, _depth=_depth + 1)
                try:
                    if validator.is_valid(item):
                        return [item]
                except Exception:
                    pass
            return original
        result = []
        for item in value:
            # Some providers form a linked chain: [a, {item: [b, {item: c}]}].
            # Expand only explicit wrappers that cannot be legitimate items;
            # native nested arrays and arbitrary objects retain their shape.
            if isinstance(item, dict) and set(item) == {"item"}:
                repaired = normalize(item, schema, _root=root, _depth=_depth + 1)
                if isinstance(repaired, list):
                    result.extend(repaired)
                    continue
            result.append(normalize(item, items, _root=root, _depth=_depth + 1))
        return result
    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return value
        additional = schema.get("additionalProperties")
        return {
            key: normalize(item, properties.get(key, additional), _root=root, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, str) and expected in {"integer", "number", "boolean"}:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        if expected == "boolean":
            return parsed if isinstance(parsed, bool) else value
        if isinstance(parsed, bool) or not isinstance(parsed, (int, float)) or (isinstance(parsed, float) and not math.isfinite(parsed)):
            return value
        if expected == "integer":
            return int(parsed) if parsed == int(parsed) else value
        return parsed
    return value
