"""Listing tools stay light over MCP: counts and handles by default, detail on request."""
from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


def test_live_app_list_rows_summarise_manifest_queries_and_semantic_objects():
    row = {
        "slug": "traffic", "name": "Traffic", "description": "d", "owner_email": "o@x", "team": None,
        "status": "live", "runtime_kind": "html", "app_kind": "dashboard", "latest_version": 3,
        "manifest": {
            "design_profile": {"name": "Adaptive Calliope", "version": 2, "adaptive": True, "tokens": {"big": "x" * 5000}},
            "semantic_map": {"objects": [{"id": "a"}, {"id": "b"}]},
        },
        "last_health": {"ok": True, "state": "runnable", "status": "live", "issues": [], "checked_at": "t"},
        "last_debug_at": "t", "queries": [{"sql": "select 1"}] * 41, "tables": ["public.traffic_violations"],
        "metrics": [], "semantic_objects": [{"id": "a"}, {"id": "b"}], "area_id": "revenue",
        "area_label": "Revenue", "area_source": "auto", "area_confidence": 0.9, "area_updated_at": "t",
        "updated_at": "t", "url": "/apps/traffic", "hub_url": "/hub?sel=x",
    }
    compact = server._live_app_list_summary(row)
    assert compact["slug"] == "traffic" and compact["url"] == "/apps/traffic"
    assert compact["query_count"] == 41
    assert compact["semantic_object_count"] == 2
    assert compact["health"] == {"ok": True, "state": "runnable", "status": "live"}
    assert compact["design_profile"] == {"name": "Adaptive Calliope", "version": 2, "adaptive": True}
    assert "manifest" not in compact and "queries" not in compact and "semantic_objects" not in compact
    assert compact["tables"] == ["public.traffic_violations"]


def test_listing_tools_expose_a_detail_switch_and_default_to_summary():
    mcp = FastMCP("list-payloads")
    server._register(mcp)
    apps = mcp._tool_manager._tools["list_live_apps"].parameters["properties"]
    assert apps["detail"]["default"] == "summary"
    threads = mcp._tool_manager._tools["list_calliope_specialist_threads"].parameters["properties"]
    assert threads["detail"]["default"] == "summary"
    assert threads["limit"]["default"] == 40
