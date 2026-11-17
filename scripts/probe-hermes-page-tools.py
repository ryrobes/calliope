#!/usr/bin/env python3
"""Verify Page tool discovery using Hermes's real MCP client, without inference.

Run in a separate process inside the configured Hermes environment, for example:
  docker exec -i -w /opt/hermes calliope-hermes /opt/hermes/.venv/bin/python - \
      < scripts/probe-hermes-page-tools.py

Only this process's registry is changed. No gateway restart, model invocation,
Page mutation, configuration change, or schema-cache write is performed.
"""
import json
import time

import tools.mcp_schema_cache as cache
import tools.mcp_tool as mcp
from tools.registry import registry


def main():
    # Hermes normally persists discovery results. Keep this probe isolated from
    # the gateway's on-disk cache as well as its in-memory registry.
    cache.write_cache_entry = lambda *args, **kwargs: None
    config = dict(mcp._load_mcp_config()['Warehouse'])
    config['lazy'] = False
    required = ['mcp__Warehouse__' + name for name in ['read_page_refresh','finish_page_refresh',
                'create_calliope_page','list_calliope_pages','read_calliope_page',
                'update_calliope_page','refresh_calliope_page','archive_calliope_page']]
    try:
        mcp.register_mcp_servers({'Warehouse': config})
        assert all(registry.get_entry(name) for name in required), \
            'Live Warehouse catalog is missing Page contract tools'
        for name in required:
            registry.deregister(name)
        assert all(not registry.get_entry(name) for name in required)
        result = registry.dispatch('mcp__Warehouse__get_tool_help', {
            'names': ','.join(name.removeprefix('mcp__Warehouse__') for name in required),
        })
        assert all(name.split('__')[-1] in str(result) for name in required), \
            'Warehouse catalog help did not return the contract schemas'
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
        mcp.shutdown_mcp_servers()


if __name__ == '__main__':
    main()
