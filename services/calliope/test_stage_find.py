"""Found tiles: `find` results become exploration-phase Stage evidence.

A find hit is a receipt the way a query result is — the relation chosen, why
it matched, where it came from, how fresh it is, and the next query. These
tests pin the projection contract without a database: weak or empty results
never reach the Stage, judge-cut and low-confidence hits are dropped, hits are
bounded, the terminal projection stamps which relations the turn went on to
use, and the renderer is wired for the new kind.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import calliope  # noqa: E402

JS = (HERE / "calliope" / "calliope.js").read_text(encoding="utf-8")
CSS = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")


def _hit(obj, kind="db_table", confidence="high", **extra):
    base = {
        "object": obj, "kind": kind, "score": 0.9, "confidence": confidence,
        "why": ["name", "meaning"], "seams": {"dense": {"rank": 1}},
        "lineage": {"adapter": "fivetran:salesforce", "role": "business",
                    "last_load_at": "2026-09-18T00:00:00+00:00", "last_load_status": "measured"},
        "fresh": {"rows_est": 225279}, "usage": {"touches_30d": 12},
        "meaning": {"ref_counts": {"dashboard": 2, "domain": 1}},
        "ask": {"sample_sql": f"SELECT * FROM {obj} LIMIT 20"},
    }
    base.update(extra)
    return base


def _result(hits, **extra):
    out = {"query": "student payment balance overdue", "confidence": "high", "degraded": [],
           "gates": {"rerank": {"applied": True}, "judge": {"applied": True, "suppressed": 1}},
           "suppressed": [{"object": "x.y"}], "elapsed_ms": 812, "matches": hits}
    out.update(extra)
    return out


def test_find_projects_a_found_tile_with_bounded_receipts():
    hits = [_hit(f"salesforce.t{i}") for i in range(12)]
    hits[1] = _hit("salesforce.lead.company", kind="db_column",
                   matched_values=[{"value": f"v{i}", "n": i} for i in range(6)], answers_ask=0.91)
    surfaces = calliope._project_tool_result("find", _result(hits), {"query": "student payment balance overdue", "limit": 8, "judge": True}, "call-1")
    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface["kind"] == "find"
    assert surface["title"] == "Found: student payment balance overdue"
    assert surface["presentation"] == {"view": "find"}
    assert surface["source"]["origin"] == "find"
    assert surface["source"]["args"] == {"query": "student payment balance overdue", "limit": 8, "judge": True}
    payload = surface["payload"]
    assert len(payload["hits"]) == 8                       # bounded
    assert payload["rerank"] is True and payload["judge"] is True and payload["suppressed"] == 1
    column = payload["hits"][1]
    assert column["relation"] == "salesforce.lead"
    assert len(column["matched_values"]) == 3              # bounded
    assert column["answers_ask"] == 0.91
    assert column["adapter"] == "fivetran:salesforce" and column["rows_est"] == 225279
    assert column["refs"] == {"dashboard": 2, "domain": 1}
    assert column["sample_sql"].startswith("SELECT * FROM salesforce.lead.company")
    assert all(hit["used"] is False for hit in payload["hits"])


def test_weak_or_empty_or_failed_results_stay_off_the_stage():
    assert calliope._project_tool_result("find", {"error": {"code": "NOT_AVAILABLE"}}, {}, "c") == []
    assert calliope._project_tool_result("find", _result([]), {}, "c") == []
    weak = _result([_hit("gc_walk.rooms.bullets", confidence="low")], confidence="low", weak=True)
    assert calliope._project_tool_result("find", weak, {}, "c") == []
    # a medium hit inside an otherwise low result still counts as evidence
    mixed = _result([_hit("a.b", confidence="low"), _hit("c.d", confidence="medium")], confidence="medium")
    tile = calliope._project_tool_result("find", mixed, {}, "c")
    assert [h["object"] for h in tile[0]["payload"]["hits"]] == ["c.d"]   # low hits dropped


def test_terminal_projection_marks_hits_the_turn_went_on_to_use():
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "f1", "function": {"name": "find", "arguments": '{"query": "overdue balance"}'}},
            {"id": "d1", "function": {"name": "describe_table", "arguments": '{"table": "salesforce.student_c"}'}},
            {"id": "q1", "function": {"name": "tool_call", "arguments":
                '{"name": "run_sql", "arguments": {"sql": "SELECT student_overdue_balance FROM cubes.student_payment_transactions_cube WHERE 1=1"}}'}},
            {"id": "p1", "function": {"name": "cube_pivot", "arguments": '{"cube": "student_finance_risk_cube"}'}},
        ]},
        {"role": "tool", "tool_call_id": "f1", "tool_name": "find", "content": _result([
            _hit("cubes.student_payment_transactions_cube.student_overdue_balance", kind="db_column"),
            _hit("salesforce.student_c.overdue_balance_c", kind="db_column"),
            _hit("cubes.student_finance_risk_cube", kind="cube"),
            _hit("public.student_semantic_risk_signals.overdue_balance", kind="db_column"),
        ])},
    ]
    projected = calliope.project_messages(messages)
    found = [s for s in projected if s["kind"] == "find"]
    assert len(found) == 1
    hits = found[0]["payload"]["hits"]
    assert [h["used"] for h in hits] == [True, True, True, False]
    assert found[0]["payload"]["used_count"] == 3
    assert found[0]["tool_call_id"] == "f1"


def test_find_is_progressive_for_turns_and_specialists_and_stays_exploration():
    assert "find" in calliope._PROGRESSIVE_TURN_TOOLS
    assert "find" in calliope._PROGRESSIVE_SPECIALIST_TOOLS
    assert "find" not in calliope._PUBLISHED_STAGE_TOOLS
    live = calliope._project_progressive_sql_result("find", _result([_hit("salesforce.lead")]), {"query": "leads"}, "f9", intent="find the lead table")
    assert live and live[0]["presentation"]["phase"] == "exploration"
    assert live[0]["source"]["exploration"] == {"intent": "find the lead table"}
    spec = calliope._project_progressive_specialist_result(
        "find", _result([_hit("salesforce.lead")]), {"query": "leads"}, "f10",
        specialist_key="analytical-skeptic", specialist_run_id="6d5c3f4e-8a9b-4c1d-9e2f-0a1b2c3d4e5f",
    )
    assert spec and spec[0]["source"]["origin"] == "calliope_specialist"
    assert spec[0]["presentation"]["specialist_key"] == "analytical-skeptic"


def test_stage_renderer_knows_the_find_kind():
    assert "find: renderFind," in JS
    assert "function renderFind(surface)" in JS
    assert 'find: "⌕"' in JS
    assert "Also considered (" in JS
    assert ".find-hit.used{" in CSS


def test_render_find_markup_in_chromium():
    """The renderer runs against a realistic payload without page errors."""
    from playwright.sync_api import sync_playwright
    start = JS.index("  function renderFind(surface)")
    end = JS.index("  function googleSheetsGlyph(", start)
    source = JS[start:end]
    payload = {
        "query": "student payment balance overdue", "confidence": "high", "rerank": True, "judge": True,
        "suppressed": 1, "elapsed_ms": 812, "used_count": 1, "degraded": [],
        "hits": [
            {"object": "cubes.student_payment_transactions_cube", "relation": "cubes.student_payment_transactions_cube",
             "kind": "cube", "why": ["name", "meaning"], "confidence": "high", "answers_ask": 0.91, "used": True,
             "adapter": "transform", "last_load_at": "2026-09-18T00:00:00+00:00", "rows_est": 344612,
             "touches_30d": 43, "refs": {"dashboard": 2}, "sample_sql": "SELECT * FROM cubes.student_payment_transactions_cube LIMIT 20",
             "matched_values": []},
            {"object": "salesforce.lead.company", "relation": "salesforce.lead", "kind": "db_column", "why": ["value"],
             "confidence": "high", "answers_ask": 0.12, "used": False, "adapter": "fivetran:salesforce",
             "matched_values": [{"value": "Coast Dental", "n": 31}], "sample_sql": None},
        ] + [{"object": f"a.t{i}", "relation": f"a.t{i}", "kind": "db_table", "why": ["meaning"], "confidence": "medium",
              "used": False, "matched_values": []} for i in range(6)],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.set_content("<div id='root'></div>")
        page.add_script_tag(content="""
          function escapeHtml(s){return String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;')}
          function relativeTime(){return '3 hours ago'}
          function highlightSql(s){return s}
          function formatSql(s){return s}
        """)
        page.add_script_tag(content=source + "\nwindow.renderFind = renderFind;")
        html = page.evaluate("(payload) => renderFind({kind: 'find', payload})", payload)
        page.evaluate("(html) => { document.getElementById('root').innerHTML = html }", html)
        used_rows = page.locator("li.find-hit.used").count()
        folded = page.locator("details.find-more summary").inner_text()
        judge = page.locator(".find-judge").first.inner_text()
        browser.close()
    assert not errors, errors
    assert "Found" not in html                       # title lives in the card header, not the body
    assert used_rows == 1
    assert "student_payment_transactions_cube" in html and "Coast Dental" in html and "×31" in html
    assert "1 cut by the judge" in html and "reranked" in html and "judged" in html
    assert judge == "91%"
    assert folded.startswith("Also considered (7)")   # 8 hits, 1 used → 7 fold
    assert "Next query" in html
