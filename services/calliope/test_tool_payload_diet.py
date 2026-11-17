"""Fat tool payloads get re-read on every later agent step; keep the grounding, shed the bulk.

Measured on the pilot appliance (Hermes state.db, Jul-Aug 2026): ~100k cached
tokens per API call, 93% of the transcript was tool results, and the heaviest
warehouse tools averaged describe_cube 45k, get_live_app 38k, ask_brain 35k,
get_dashboard 34k, search_calliope_actions 22k chars per call.
"""
from __future__ import annotations

import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402
import calliope  # noqa: E402


def test_describe_cube_summary_drops_sql_and_raw_health_but_keeps_grounding():
    full = {
        "name": "sh_value", "grain": "month x metric", "rows": 14616, "refreshed_at": "t",
        "sql": "SELECT " + "x" * 6000,
        "columns": [{"name": "metric_key", "type": "text", "doc": None, "semantics": None, "kind": "dimension"}],
        "sample": [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}, {"a": 5}],
        "health": {
            "drift": {"drift_rows": 12, "recommendation": "refresh snapshot"},
            "usage": {"heap_seq_scans": 34},
            "sources": {"tables": ["sh.sales"], "dirty_count": 1},
            "autopilot": {"refreshed_at": "2026-08-15", "refresh_mode": "auto", "last_error": "deadlock detected", "query_threads": 2},
        },
    }
    compact = server._compact_cube_description(full)
    assert "sql" not in compact and compact["sql_chars"] == 6007
    assert compact["columns"] == [{"name": "metric_key", "type": "text", "kind": "dimension"}]
    assert len(compact["sample"]) == 3
    assert compact["health"] == {
        "refreshed_at": "2026-08-15", "refresh_mode": "auto", "source_tables": ["sh.sales"],
        "dirty_sources": 1, "drift_rows": 12, "recommendation": "refresh snapshot",
        "last_error": "deadlock detected",
    }
    assert "detail='full'" in compact["hint"]


def test_artifact_reads_shed_frozen_design_tokens_and_cap_lineage_sql():
    frozen = {"schema_version": "rvbbit.artifact-design-profile.v1", "name": "Adaptive Calliope",
              "version": 2, "version_id": "v2", "adaptive": True, "tokens": {"big": "x" * 5000}, "behavior": {}}
    doc = {
        "manifest": {"design_profile": frozen, "semantic_map": {"objects": []}},
        "version": {"html": "<html>" + "y" * 100, "manifest": {"design_profile": dict(frozen)},
                    "authored_manifest": {"design_profile": {"name": "Adaptive Calliope"}}},
        "sources": [{"kind": "table", "object_ref": "public.t", "base_sql": "select " + "z" * 1000}],
        "recent_queries": [{"ts": i, "sql": "select " + "q" * 900} for i in range(20)],
    }
    out = server._compact_artifact_read(doc)
    assert out["version"]["html"].startswith("<html>")                    # edits still get the source
    assert out["manifest"]["design_profile"] == {"name": "Adaptive Calliope", "version": 2, "version_id": "v2", "adaptive": True, "frozen": True}
    assert out["version"]["manifest"]["design_profile"]["frozen"] is True
    assert out["version"]["authored_manifest"] == {"design_profile": {"name": "Adaptive Calliope"}}   # untouched (no tokens)
    assert out["sources"][0]["base_sql"].endswith("…") and out["sources"][0]["base_sql_truncated"] is True
    assert len(out["recent_queries"]) == 8 and out["recent_queries_omitted"] == 12
    assert all(len(row["sql"]) <= server._ARTIFACT_READ_SQL_CAP + 1 for row in out["recent_queries"])


def test_ask_brain_related_breadcrumbs_are_compact():
    item = {"score": 74.15, "title": "IT-290", "doc_id": 510, "shared": 13,
            "shared_entities": ["a", "b", "c", "d", "e"]}
    assert server._compact_related_doc(item) == {"doc_id": 510, "title": "IT-290", "shared": 13, "shared_entities": ["a", "b", "c"]}
    assert server._ASK_BRAIN_RELATED_PER_DOC == 3


def test_action_search_results_keep_choice_signals_and_drop_execution_internals():
    ready = {
        "id": "admin.connect_linear", "title": "Connect Linear", "summary": "Connect the Linear workspace.",
        "description": "Connect the Linear workspace." , "category": "integrations", "category_label": "Integrations",
        "executor": "mcp_connect", "risk": "reversible", "state": "ready", "state_label": "Ready",
        "tags": ["linear", "tickets"], "fields": [{"name": "api_key", "type": "secret"}],
        "prompt_template": "P" * 3000, "config": {"internal": True}, "sort_order": 10, "score": 40,
        "requirement_states": [{"ref": "warehouse", "available": True}], "missing_requirements": [],
        "resolves": ["mcp:linear"],
    }
    compact = calliope._compact_action_for_search(ready)
    assert compact["fields"] == [{"name": "api_key", "type": "secret"}]
    assert "description" not in compact                     # identical to summary → dropped
    for key in ("prompt_template", "config", "sort_order", "score", "requirement_states", "resolves"):
        assert key not in compact

    blocked = {**ready, "id": "x", "state": "blocked", "state_label": "Needs setup",
               "description": "D" * 1000,
               "missing_requirements": [{"ref": "mcp:linear", "available": False, "remediation_action_id": "mcp.connect:linear"}]}
    compact = calliope._compact_action_for_search(blocked)
    assert "fields" not in compact
    assert compact["missing_requirements"] == [{"ref": "mcp:linear", "remediation_action_id": "mcp.connect:linear"}]
    assert compact["description"].endswith("…") and len(compact["description"]) <= calliope._ACTION_SEARCH_DESCRIPTION_CAP + 1


def test_describe_cube_full_mode_survives_the_column_loop(monkeypatch):
    """Regression: the column-enrichment loop rebinds a local named `detail`,
    which used to shadow the mode argument and force the summary shape."""
    import contextlib

    class _Conn:
        def execute(self, sql, params=()):
            self._sql = sql
            return self

        def fetchone(self):
            return {"d": {"name": "c", "sql": "select 1", "columns": [{"name": "a", "type": "text"}],
                          "health": {"autopilot": {"refreshed_at": "t"}}, "sample": []}}

        def fetchall(self):
            return [{"column_name": "a", "data_type": "text", "kind": "dimension", "groupable": True,
                     "distinct_est": 3, "semantics": None}]

    @contextlib.contextmanager
    def _conn(**_kw):
        yield _Conn()

    monkeypatch.setattr(server, "_conn", _conn)
    monkeypatch.setattr(server, "_session_pg_role", lambda: None)
    full = server.tool_describe_cube("c", "full")
    assert full["sql"] == "select 1" and "sql_chars" not in full
    summary = server.tool_describe_cube("c")
    assert "sql" not in summary and summary["sql_chars"] == 8
