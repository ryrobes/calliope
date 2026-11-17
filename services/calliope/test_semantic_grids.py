"""The semantic compiler keeps callouts as values and tables, charts and
repeated runs as grids: page roles, the grid contract, and how a grid is held
to the numbers the page shows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402
import stage_values as sv  # noqa: E402


def cand(n, value, label, selector, **over):
    return {"candidate_id": f"candidate_{n:03d}", "kind": "value", "tag": "div", "classes": "bar", "selector": selector,
            "label": label, "value": value, "represented_elements": 1, **over}


def page():
    months = [cand(i, str(100 + i), "Enrolled by month", f"div#chart > div:nth-of-type({i}) > div") for i in range(1, 7)]
    strip = [cand(10 + i, str(i), label, f"div#kpis > div:nth-of-type({i}) > b", classes="kpi")
             for i, label in enumerate(["Leads", "Enrolled", "Cost per lead"], start=1)]
    alone = [cand(20, "$168k", "Overdue", "div#overdue > b", classes="big"),
             cand(21, "12", "Row", "table#t > tbody > tr:nth-of-type(1) > td:nth-of-type(2)", tag="td", classes="")]
    return {"candidates": months + strip + alone,
            "tables": [{"selector": "table#t", "columns": ["Month", "Enrolled", "Share"],
                        "sample_rows": [["Jan 2026", "1,204", "42.5%"], ["Feb 2026", "980", "34.6%"], ["Mar 2026", "$2.1k", "22.9%"]]}],
            "canvasCharts": [{"datasets": [{"values": [5, 7.25, None, 9]}]}]}


def test_page_candidates_are_callouts_or_members_of_a_grid():
    dom = server._semantic_annotate_candidates(page())
    roles = {c["candidate_id"]: c["role"] for c in dom["candidates"]}
    assert all(roles[f"candidate_{i:03d}"] == "grid_member" for i in range(1, 7)), "six bars under one heading are one grid"
    assert all(roles[f"candidate_{i:03d}"] == "callout" for i in (11, 12, 13)), "a strip of different KPIs stays callouts"
    assert roles["candidate_020"] == "callout" and roles["candidate_021"] == "grid_member", "a table cell is never a callout"
    families = {f["family"]: f for f in dom["families"]}
    assert [f["members"] for f in families.values()] == [6, 3]
    assert families["family_1"]["label"] == "Enrolled by month" and families["family_2"]["labels"] == ["Leads", "Enrolled", "Cost per lead"]
    assert server._semantic_candidate_role({"kind": "repeated"}) == "grid_member"
    assert server._semantic_candidate_role({"tag": "rect", "selector": "svg > rect"}) == "grid_member"


@pytest.mark.parametrize("rendered, replayed, same", [
    ("12.3%", 12.34, True), ("12.3%", 0.1234, True), ("12.3%", 12.36, False),
    ("$168k", 168_400, True), ("$168k", 169_000, False), ("1,204", 1204, True), ("1,204", 1204.4, True), ("1,204", 1205, False),
    ("(3)", -3, True), ("n/a", 3, False), (7.25, "7.25", True),
])
def test_a_replayed_number_matches_to_the_precision_the_page_shows(rendered, replayed, same):
    assert server._semantic_rendered_matches(rendered, replayed) is same


def test_a_callout_shown_in_thousands_accepts_a_query_that_returns_thousands():
    assert server._semantic_rendered_scalar_matches("$168k", 168.4)
    assert server._semantic_rendered_scalar_matches("$168k", 168_400)
    assert not server._semantic_rendered_scalar_matches("$168k", 170)


def test_grid_evidence_is_the_numbers_of_what_it_claims_to_recreate():
    dom = server._semantic_annotate_candidates(page())
    assert server._semantic_grid_evidence({"family": "family_1"}, dom) == ["101", "102", "103", "104", "105", "106"]
    assert server._semantic_grid_evidence({"candidate_ids": ["candidate_001", "candidate_020"]}, dom) == ["101", "$168k"]
    assert server._semantic_grid_evidence({"table": 0}, dom) == ["1,204", "42.5%", "980", "34.6%", "$2.1k", "22.9%"], "month names are not numbers"
    assert server._semantic_grid_evidence({"chart": 0}, dom) == [5, 7.25, 9]
    assert server._semantic_grid_evidence({"table": 9}, dom) == [] and server._semantic_grid_evidence({}, dom) == []


def grid(**over):
    base = {"id": "enrolled_by_month", "source": "chart", "anchor": {"family": "family_1"},
            "meaning": {"label": "Enrolled by month", "grain": "one row per month", "measures": ["Enrolled Students!"]},
            "parameters": {"term": {"type": "text", "default": "Fall 2026", "dimension": "cohort", "fixed": True}},
            "columns": [{"name": "month", "role": "dimension"}, {"name": "enrolled", "role": "measure", "measure": "enrolled students"}],
            "evaluator": {"sql": "select month, enrolled from m where term = {{term}}"}}
    base.update(over)
    return base


def test_a_grid_is_normalized_like_a_value_with_columns_and_an_anchor():
    out = server._normalize_semantic_grid(grid())
    assert out["kind"] == "grid" and out["evaluator"] == {"sql": "select month, enrolled from m where term = {{term}}", "shape": "table"}
    assert out["meaning"]["measures"] == ["enrolled students"] and out["meaning"]["grain"] == "one row per month"
    assert out["columns"] == [{"name": "month", "role": "dimension"}, {"name": "enrolled", "role": "measure", "measure": "enrolled students"}]
    assert out["parameters"]["term"]["fixed"] is True and out["anchor"] == {"family": "family_1"} and len(out["definition_hash"]) == 20
    assert "term='Fall 2026'" in server._render_semantic_sql(out)[0].replace(" = ", "=")
    for bad, message in (({"evaluator": {}}, "evaluator.sql is required"), ({"id": "1bad"}, "id is not valid"),
                         ({"evaluator": {"sql": "select {{nope}}"}}, "undeclared SQL parameters")):
        with pytest.raises(ValueError, match=message):
            server._normalize_semantic_grid(grid(**bad))


def test_a_grid_is_kept_only_when_it_reproduces_what_the_page_shows(monkeypatch):
    dom = server._semantic_annotate_candidates(page())
    monkeypatch.setattr(server, "tool_validate_sql", lambda sql, *a, **k: {"valid": True, "safe_select": True})
    rows = {"good": [["2026-01", 101], ["2026-02", 102], ["2026-03", 103], ["2026-04", 104], ["2026-05", 105], ["2026-06", 106]],
            "short": [["2026-01", 101], ["2026-02", 102], ["2026-03", 999]], "empty": []}
    which = {"name": "good"}
    monkeypatch.setattr(server, "tool_run_sql", lambda sql, *a, **k: {"columns": ["month", "enrolled"], "rows": rows[which["name"]]})
    check = server._verify_semantic_grid(server._normalize_semantic_grid(grid()), dom)
    assert check == {"rows": 6, "columns": 2, "evidence": 6, "matched": 6}
    which["name"] = "short"
    with pytest.raises(ValueError, match="reproduced 2 of 6 rendered numbers"):
        server._verify_semantic_grid(server._normalize_semantic_grid(grid()), dom)
    which["name"] = "empty"
    with pytest.raises(ValueError, match="returned no rows"):
        server._verify_semantic_grid(server._normalize_semantic_grid(grid()), dom)
    which["name"] = "good"
    with pytest.raises(ValueError, match="no anchor with rendered numbers"):
        server._verify_semantic_grid(server._normalize_semantic_grid(grid(anchor={})), dom)
    with pytest.raises(ValueError, match="columns its SQL does not return: share"):
        server._verify_semantic_grid(server._normalize_semantic_grid(grid(columns=[{"name": "share", "role": "measure"}])), dom)


def value(id_, measure, **defaults):
    return {"id": id_, "meaning": {"label": id_, "measure": measure},
            "parameters": {k: {"type": "text", "default": v} for k, v in defaults.items()}}


def test_the_same_measure_told_apart_only_by_a_slice_is_a_grid_not_values():
    monthly = [value(f"m{i}", "enrolled students", start=f"2026-0{i}-01") for i in range(1, 5)]
    others = [value("total", "enrolled students", term="Fall 2026"), value("leads", "leads", term="Fall 2026"),
              value("twice_a", "open seats", term="Fall 2026"), value("twice_b", "open seats", term="Fall 2026"),
              value("plain", "cost per lead")]
    series = server._semantic_value_series(monthly + others)
    assert [[o["id"] for o in group] for group in series] == [["m1", "m2", "m3", "m4"]]
    assert server._semantic_value_series(monthly[:2] + others) == [], "two settings are a comparison, not a series"


def test_grids_ride_beside_objects_and_are_listed_for_the_library():
    stored = server._normalize_semantic_grid(grid())
    merged = server._merge_semantic_overlay({}, {"status": "ready", "semantic_map": {"objects": [], "grids": [stored]}})
    assert merged["semantic_map"]["grids"] == [stored] and merged["semantic_map"]["objects"] == []
    row = {"slug": "fall", "name": "Fall console", "owner_email": "r@x", "latest_version": 2, "version": 2, "updated_at": "2026-11-12",
           "authored": None, "generated": {"objects": [
               {"id": "cell", "kind": "cell", "meaning": {"label": "Jan", "measure": "enrolled students"}, "evaluator": {"sql": "select 1"}},
               {"id": "total", "kind": "scalar", "meaning": {"label": "Enrolled", "measure": "enrolled students"}, "evaluator": {"sql": "select 1"}}],
               "grids": [stored]}}
    items = sv.flatten([row])
    assert [(i["key"], i["shape"]) for i in items] == [("fall:total", "value"), ("fall:grid:enrolled_by_month", "grid")], "cells are not building blocks"
    listed = items[1]
    assert listed["measures"] == ["enrolled students"] and listed["grain"] == "one row per month"
    assert listed["columns"][1] == {"name": "enrolled", "role": "measure", "measure": "enrolled students"}
    assert listed["slices"][0]["name"] == "term" and "sql" not in str(listed).lower()
    assert [i["shape"] for i in sv.search(items, "grid enrolled")[0]] == ["grid"]
    assert sv.match_object(sv.grids_of(row["generated"]), "enrolled_by_month")[1] == "id"
    assert sv.shape(None) == "value" and sv.shape("grid") == "grid"
    with pytest.raises(ValueError):
        sv.shape("chart")


def test_the_evidence_packet_is_bounded_whatever_a_query_returns():
    """A page whose queries return megabytes in one cell (a packed map) must not
    turn every model call of its analysis into a megabyte prompt."""
    import json
    huge = "H4sI" + "A" * 3_000_000
    queries = [{"query_id": "runtime_1", "sql": "select payload from m", "columns": ["payload"], "sample_rows": [[huge]], "row_count": 1},
               {"query_id": "runtime_2", "sql": "select " + "x, " * 30_000 + "1", "columns": ["a", "b"],
                "sample_rows": [{"a": i, "b": "note " * 30} for i in range(12)], "row_count": 12, "sql_truncated": False}]
    bounded = server._semantic_packet_queries(queries)
    assert len(json.dumps(bounded)) < 40_000
    cell = bounded[0]["sample_rows"][0][0]
    assert cell.startswith("H4sIAAAA") and cell.endswith("[3,000,004 characters]") and len(cell) < 300
    assert bounded[1]["sql_truncated"] is True and len(bounded[1]["sql"]) < 12_100 and len(bounded[1]["sample_rows"]) <= 8
    assert queries[0]["sample_rows"][0][0] is huge and len(queries[1]["sql"]) > 90_000, "the capture keeps the originals for replay"
    assert server._semantic_bounded_cell(42) == 42 and server._semantic_bounded_cell(None) is None
    packet = {"source": "s" * 500_000, "runtime_queries": bounded, "dom": {"candidates": [{"candidate_id": f"c{i}", "label": "x" * 400} for i in range(140)]},
              "business_context": {"conversation": [{"asked": "y" * 5_000}], "sources": {"t": {"what": "z" * 5_000}}, "known_measures": [{"measure": "leads"}]}}
    fitted = server._semantic_fit_packet(packet, 120_000)
    assert len(json.dumps(fitted)) <= 120_000 and "source" in fitted["evidence_trimmed"]
    assert fitted["business_context"]["known_measures"] == [{"measure": "leads"}], "names in use are kept"
    small = {"source": "tiny", "runtime_queries": [], "dom": {}}
    assert server._semantic_fit_packet(small, 120_000) is small and "evidence_trimmed" not in small
    route = open(server.__file__).read()
    assert '"runtime_queries": _semantic_packet_queries(queries),' in route and "_semantic_fit_packet(packet, _env_int(" in route
