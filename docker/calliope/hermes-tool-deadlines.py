"""Keep the executor guard outside the operator's per-tool MCP deadline."""
import json
import math


def tool_deadline(default, calls, config=None):
    if default is None:
        return None
    if config is None:
        from hermes_cli.config import load_config
        config = load_config()
    servers = config.get("mcp_servers", {})
    from tools.mcp_tool_common import _resolve_named_tool_timeout, _resolve_tool_timeout

    def budget(name, args, depth=0):
        if depth > 3:
            return 0
        if name == "tool_call":
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (TypeError, ValueError):
                    return 0
            nested = args.get("calls", []) if isinstance(args, dict) else []
            # Native local/MCP bridging accepts one entry; invalid batches get no extension.
            if not isinstance(nested, list) or len(nested) != 1 or not isinstance(nested[0], dict):
                return 0
            return budget(nested[0].get("name", ""), nested[0].get("arguments", {}), depth + 1)
        parts = name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return 0
        server = servers.get(parts[1])
        if not isinstance(server, dict):
            return 0
        value = _resolve_named_tool_timeout(server, parts[2], _resolve_tool_timeout(server))
        return float(value) + 15 if isinstance(value, (int, float)) and math.isfinite(value) and 0 < value <= 3600 else 0

    return max([default] + [budget(name, args) for name, args in calls])
