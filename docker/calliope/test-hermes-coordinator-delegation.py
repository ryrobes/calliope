"""Verify coordinator isolation using the shipping Hermes tool resolver."""
import os
from pathlib import Path
import runpy

from toolsets import resolve_multiple_toolsets
from tools.tool_search import resolve_underlying_call

root = Path(os.environ.get("CALLIOPE_HERMES_POLICY_ROOT", "/opt/hermes/calliope"))
policy = runpy.run_path(str(root / "reconcile-hermes-config.py"))
profiles = runpy.run_path(str(root / "reconcile-hermes-specialists.py"))
parent_tools = resolve_multiple_toolsets(policy["API_SERVER_TOOLSET_INVARIANTS"])
assert "delegate_task" not in parent_tools
for key in ("dashboard-studio", "generated-retention-expert"):
    lane = profiles["_profile_config"](key)["platform_toolsets"]["api_server"]
    assert "delegate_task" in resolve_multiple_toolsets(lane)
# Progressive disclosure must not offer a way around the native toolset choice.
name, args, error = resolve_underlying_call({
    "name": "delegate_task", "arguments": {"goal": "must not execute"},
})
assert name is None and error
print("Coordinator delegation isolation: OK")
