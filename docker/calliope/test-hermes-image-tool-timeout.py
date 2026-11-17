"""Exercise the patched upstream helper and every registration path (live, shared-scope, cached).

v0.21.4 split tools/mcp_tool.py: the timeout helpers sit beside _resolve_tool_timeout in
tools/mcp_tool_common.py, the one registration seam shared by live discovery, shared-scope
adoption and the schema-cache lazy start is _tool_candidates in tools/mcp_tool_registration.py,
and the Streamable HTTP client budget lives in tools/mcp_tool_transport.py."""
import ast
from pathlib import Path

common_source = Path("/opt/hermes/tools/mcp_tool_common.py").read_text()
tree = ast.parse(common_source)
helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
           and n.name in {"_resolve_named_tool_timeout", "_resolve_transport_read_timeout"}]
assert len(helpers) == 2
scope = {}
exec(compile(ast.Module(body=helpers, type_ignores=[]), "<timeout>", "exec"), scope)
resolve = scope["_resolve_named_tool_timeout"]
config = {"tool_timeouts": {"generate_image": 660}}
assert resolve(config, "generate_image", 90) == 660
assert resolve(config, "run_sql", 90) == 90
assert resolve({}, "generate_image", 90) == 90
assert scope["_resolve_transport_read_timeout"](config) == 660
assert scope["_resolve_transport_read_timeout"]({}) == 300
for bad in [None, True, "660", 0, -1, float("inf"), float("nan"), 3601]:
    assert resolve({"tool_timeouts": {"generate_image": bad}}, "generate_image", 90) == 90

registration_source = Path("/opt/hermes/tools/mcp_tool_registration.py").read_text()
registration_tree = ast.parse(registration_source)
calls = [n for n in ast.walk(registration_tree) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute) and n.func.attr == "_make_tool_handler"]
assert len(calls) == 1
assert all(isinstance(n.args[2], ast.Call)
           and n.args[2].func.id == "_resolve_named_tool_timeout" for n in calls)
# Every entry point hands the server config to the shared candidate builder, so the
# per-tool budget applies whether the tools came from the live SDK, an adopted shared
# connection, or the schema cache.
candidate_calls = [n for n in ast.walk(registration_tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "_tool_candidates"]
assert len(candidate_calls) == 3
assert all(len(n.args) == 5 and isinstance(n.args[4], ast.Name) and n.args[4].id == "config"
           for n in candidate_calls)

transport_source = Path("/opt/hermes/tools/mcp_tool_transport.py").read_text()
assert 'read=_resolve_transport_read_timeout(self._config)' in transport_source
print("Image-only MCP timeout: live and cached registrations passed")

wait_config = {"tool_timeouts": {"await_calliope_specialists": 360}}
assert resolve(wait_config, "await_calliope_specialists", 420) == 360
assert scope["_resolve_transport_read_timeout"](wait_config) == 360
