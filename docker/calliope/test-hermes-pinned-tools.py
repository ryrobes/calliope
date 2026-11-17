"""Contract test for hermes-pinned-tools.patch (runs in the image build).

The 2026-08-21 audit showed ~30% of all tool executions were tool_describe
round-trips, overwhelmingly re-fetching the same core verbs (run_sql_multi
was described 182 times in two days). tools.tool_search.pinned keeps an
operator-chosen working set in the model-facing array — stable, cacheable
prompt prefix — while the deferred catalog stays complete for discovery.
"""

import json
from unittest.mock import patch
from types import SimpleNamespace

from tools.registry import registry
from tools.tool_search import (
    ToolSearchConfig,
    assemble_tool_defs,
    classify_tools,
    dispatch_tool_describe,
    dispatch_tool_search,
)


def tool_def(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


MCP_DEFS = [
    tool_def("mcp__Warehouse__run_sql_multi"),
    tool_def("mcp__Warehouse__capability_search"),
    tool_def("mcp__Warehouse__brain_facets"),
]


def mcp_registry():
    return patch.object(
        registry, "get_entry",
        side_effect=lambda name: SimpleNamespace(toolset="mcp-Warehouse"),
    )


# ── config parsing: validated, deduped, capped ──────────────────────────
config = ToolSearchConfig.from_raw({
    "enabled": "on",
    "pinned": [
        "mcp__Warehouse__run_sql_multi",
        "mcp__Warehouse__run_sql_multi",          # dupe dropped
        "mcp__Warehouse__capability_search",
        "bad name with spaces",                    # invalid dropped
        "",                                        # empty dropped
    ],
})
assert config.pinned == (
    "mcp__Warehouse__run_sql_multi",
    "mcp__Warehouse__capability_search",
)
assert ToolSearchConfig.from_raw({"enabled": "on"}).pinned == ()
assert ToolSearchConfig.from_raw(True).pinned == ()

with mcp_registry():
    # ── classification: pinned stays visible, rest defers ───────────────
    visible, deferrable = classify_tools(MCP_DEFS, pinned=config.pinned)
    assert [t["function"]["name"] for t in visible] == list(config.pinned)
    assert [t["function"]["name"] for t in deferrable] == ["mcp__Warehouse__brain_facets"]

    # Without pinned (the catalog/search call sites), everything still
    # defers — pinned tools remain discoverable and describable.
    _, full_catalog = classify_tools(MCP_DEFS)
    assert len(full_catalog) == 3

    # ── assembly: pinned schemas ride the model-facing array ────────────
    result = assemble_tool_defs(MCP_DEFS, config=config, context_length=262144)
    names = [t["function"]["name"] for t in result.tool_defs]
    assert "mcp__Warehouse__run_sql_multi" in names
    assert "mcp__Warehouse__capability_search" in names
    assert "mcp__Warehouse__brain_facets" not in names      # still deferred
    assert any(n.startswith("tool_") for n in names)        # bridge present

    # ── search/describe steer pinned tools back to direct calls ─────────
    # The catalog stays complete, but presenting a pinned tool as deferred
    # taught grok a per-iteration search -> describe -> tool_call ritual
    # for schemas it already had (2026-08-21 run). Results now say so.
    searched = json.loads(dispatch_tool_search(
        {"queries": ["run sql multi"]}, current_tool_defs=MCP_DEFS, config=config,
    ))
    assert "mcp__Warehouse__run_sql_multi" in searched["results"][0]["matches"]
    assert searched["tools"]["mcp__Warehouse__run_sql_multi"]["already_available"] is True
    assert "call them directly" in searched["note"]

    described = json.loads(dispatch_tool_describe(
        {"names": ["mcp__Warehouse__run_sql_multi"]}, current_tool_defs=MCP_DEFS, config=config,
    ))
    assert described["tools"]["mcp__Warehouse__run_sql_multi"]["already_available"] is True
    assert "call them directly" in described["note"]
    plain = json.loads(dispatch_tool_describe(
        {"names": ["mcp__Warehouse__brain_facets"]}, current_tool_defs=MCP_DEFS, config=config,
    ))
    assert "already_available" not in plain["tools"]["mcp__Warehouse__brain_facets"]
    assert "parameters" in plain["tools"]["mcp__Warehouse__brain_facets"]

print("hermes-pinned-tools: OK")
