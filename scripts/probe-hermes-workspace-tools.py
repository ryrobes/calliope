#!/usr/bin/env python3
"""Verify workspace SQL tool discovery using Hermes's real MCP client, without inference.

Run in a separate process inside the configured Hermes environment, for example:
  docker exec -i -w /opt/hermes calliope-hermes /opt/hermes/.venv/bin/python - \
      < scripts/probe-hermes-workspace-tools.py

Only this process's registry is changed. No gateway restart, model invocation,
Page mutation, configuration change, or schema-cache write is performed.
"""
import json
import time

import tools.mcp_schema_cache as cache
from tools.mcp_tool_config import _load_mcp_config
from tools.mcp_tool_discovery import register_mcp_servers
from tools.mcp_tool_lifecycle import shutdown_mcp_servers
from tools.registry import registry


def main():
    # Hermes normally persists discovery results. Keep this probe isolated from
    # the gateway's on-disk cache as well as its in-memory registry.
    cache.write_cache_entry = lambda *args, **kwargs: None
    config = dict(_load_mcp_config()['Warehouse'])
    config['lazy'] = False
    required = ['mcp__Warehouse__'+name for name in ['run_workspace_sql', 'save_workspace_job', 'manage_workspace_job', 'workspace_job_status']]
    try:
        register_mcp_servers({'Warehouse': config})
        assert all(registry.get_entry(name) for name in required), \
            'Live Warehouse catalog is missing workspace SQL/job tools'
        for name in required:
            registry.deregister(name)
        assert all(not registry.get_entry(name) for name in required)
        result = registry.dispatch('mcp__Warehouse__validate_sql', {'sql':'SELECT 1'})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all(registry.get_entry(name) for name in required):
                break
            time.sleep(.1)
        restored = {name: bool(registry.get_entry(name)) for name in required}
        print(json.dumps({'live_catalog': 'verified', 'stale_registry_restored': restored}))
        assert all(restored.values()), \
            'Tool-list-change notification did not restore the Hermes registry'
    finally:
        shutdown_mcp_servers()


if __name__ == '__main__':
    main()
