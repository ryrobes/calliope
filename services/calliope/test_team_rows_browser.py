"""The specialist rows under a turn: who, their specialty, rounds, the tool mix, time."""
from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

HERE = Path(__file__).parent
SESSION = "73ea6745-ee4f-48eb-bc8c-350fb99d096a"
TURN = "94da7082-b64c-4f3b-8bc4-63e59fcb7d57"
THREAD = "3dbbe8a6-1e1c-4743-b12c-49bef0d58c9b"


GEOMETRY = """el => {
  const box = sel => { const r = el.querySelector(sel).getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
  return {identity: box('.team-identity'), facts: box('.team-facts'), mix: box('.team-mix-row')};
}"""


@pytest.mark.parametrize("with_progress", [False, True])
def test_team_row_names_the_specialty_rounds_tool_mix_and_time(with_progress):
    playwright = pytest.importorskip("playwright.sync_api")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(HERE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_errors = []
    runs = [
        {"run_id": "r1", "thread_id": THREAD, "source_turn_id": TURN, "root_turn_id": TURN, "round": 1, "status": "complete",
         "specialist_key": "analytical-skeptic", "specialist_name": "Cassandra", "started_at": "2026-09-20T12:00:00Z",
         "completed_at": "2026-09-20T12:02:10Z", "heartbeat_at": "2026-09-20T12:02:10Z", "tool_count": 12, "active_count": 0, "active_tools": [],
         "last_activity_at": "2026-09-20T12:02:10Z", "tool_mix": [{"name": "mcp__Warehouse__run_sql", "n": 9}, {"name": "mcp__Warehouse__find", "n": 2}, {"name": "mcp__Warehouse__describe_table", "n": 1}]},
        {"run_id": "r2", "thread_id": THREAD, "source_turn_id": TURN, "root_turn_id": TURN, "round": 2, "status": "complete",
         "specialist_key": "analytical-skeptic", "specialist_name": "Cassandra", "started_at": "2026-09-20T12:03:00Z",
         "completed_at": "2026-09-20T12:04:32Z", "heartbeat_at": "2026-09-20T12:04:32Z", "tool_count": 8, "active_count": 0, "active_tools": [],
         "last_activity_at": "2026-09-20T12:04:32Z", "tool_mix": [{"name": "mcp__Warehouse__run_sql", "n": 5}, {"name": "mcp__Warehouse__publish_chart", "n": 1}, {"name": "mcp__Warehouse__read_chart", "n": 1}, {"name": "mcp__Warehouse__find", "n": 1}]},
    ]

    if with_progress:
        runs[-1]["work_receipts_version"] = 1
        runs[-1]["work_progress"] = {"milestones": [
            {"milestone": "file_written", "label": "File write confirmed"},
            {"milestone": "validation_failed", "label": "Web check failed"}],
            "scope_deviation": {"decision": "Use state totals", "evidence": "Duplicate keys"}}

    def route_api(route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/calliope/config":
            payload = {"healthy": True, "personal_briefs": False, "google_calendar": False,
                       "speech_to_text": {"enabled": False}, "text_to_speech": {"enabled": False},
                       "chat_agents": [
                           {"key": "calliope", "profile": None, "name": "Calliope", "role_label": "Warehouse muse", "primary": True, "capabilities": []},
                           {"key": "analytical-skeptic", "profile": "analytical-skeptic", "name": "Cassandra", "role_label": "Analytical Skeptic", "primary": False, "capabilities": []},
                       ]}
        elif path == "/api/calliope/inbox":
            payload = {"items": [], "counts": {"unread": 0, "open": 0, "shown": 0, "by_kind": {}}}
        elif path in {"/api/calliope/styles", "/api/calliope/instruments", "/api/calliope/workflows"}:
            payload = {path.rsplit("/", 1)[-1]: []}
        elif path == "/api/calliope/sessions" and request.method == "GET":
            payload = {"sessions": [{"id": SESSION, "title": "Team", "updated_at": "2026-09-20T12:00:00Z", "access_role": "owner", "read_only": False}]}
        elif path == f"/api/calliope/sessions/{SESSION}" and request.method == "GET":
            payload = {
                "session": {"id": SESSION, "title": "Team", "access_role": "owner", "read_only": False, "team_mode": True,
                            "specialist_threads": [{"thread_id": THREAD, "root_turn_id": TURN, "latest_turn_id": TURN, "status": "complete",
                                                    "specialist": {"key": "analytical-skeptic", "name": "Cassandra", "profile": "analytical-skeptic"},
                                                    "last_run_status": "complete", "round_count": 2}]},
                "turns": [{"id": TURN, "session_id": SESSION, "ordinal": 1, "user_message": "Which partners?", "assistant_message": "Start with the top three.",
                           "attachments": [], "status": "complete", "turn_kind": "chat", "evidence_refs": [], "object_refs": [],
                           "response_receipt": {}, "created_at": "2026-09-20T12:00:00Z", "completed_at": "2026-09-20T12:05:00Z"}],
                "surfaces": [],
                "specialist_threads": [{"thread_id": THREAD, "root_turn_id": TURN, "latest_turn_id": TURN, "status": "complete",
                                        "specialist": {"key": "analytical-skeptic", "name": "Cassandra", "profile": "analytical-skeptic"},
                                        "last_run_status": "complete", "round_count": 2}],
                "agent_work": {"session_id": SESSION, "available": True, "observed_at": "2026-09-20T12:06:00Z", "parent_turns": [], "pending_publications": [],
                               "pending": [], "queued_messages": 0, "delivering_messages": 0, "ready_messages": [], "activity": runs, "followups": [],
                               "activity_state": {"phase": "idle"}},
            }
        elif path == f"/api/calliope/specialist-threads/{THREAD}":
            payload = {"thread": {"run_id": runs[-1]["run_id"], "thread_id": THREAD, "turn_id": TURN,
                "status": "complete", "round": 2, "round_count": 2,
                "specialist": {"key": "analytical-skeptic", "name": "Cassandra"},
                "work_receipts_version": 1, "work_progress": runs[-1].get("work_progress", {}),
                "assignment": {"settled_decisions": ["Use <state> totals"], "supplied_evidence": ["Query Q"],
                    "implementation_questions": [], "deliverables": ["Published app"]}}}
        elif path == "/api/calliope/session-events":
            route.fulfill(status=204, body="")
            return
        else:
            route.fulfill(status=404, content_type="application/json", body='{"error":{"message":"fixture"}}')
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    try:
        with playwright.sync_playwright() as runtime:
            launch = {"headless": True}
            if Path("/usr/bin/chromium").exists():
                launch["executable_path"] = "/usr/bin/chromium"
            browser = runtime.chromium.launch(**launch)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/**", route_api)
            page.goto(f"http://127.0.0.1:{server.server_port}/calliope/index.html?session={SESSION}&view=zen", wait_until="networkidle")
            row = page.locator(f'[data-open-specialist-thread="{THREAD}"]')
            row.wait_for(timeout=15000)
            text = row.inner_text()
            facts = {
                "specialty": row.locator(".team-specialty").inner_text(),
                "pips": row.locator(".team-pip").count(),
                "mix": row.locator(".team-mix em").all_text_contents(),
                "text": text,
                "aria": row.get_attribute("aria-label"),
                "narrow": row.evaluate(GEOMETRY),
            }
            # Given room (the wide receipt column), the row spreads out: identity on the left, the facts
            # and the tool mix stacked beside it instead of beneath it.
            page.evaluate("document.querySelector('.zen-team-conversations').style.width = '880px'")
            page.wait_for_timeout(100)
            facts["wide"] = row.evaluate(GEOMETRY)
            if with_progress:
                assert "File write confirmed" in facts["text"]
                assert "Web check failed" in facts["text"]
                assert "Settled decision questioned" in facts["text"]
                assert "App version published" not in facts["text"]
                page.evaluate("document.querySelector('.zen-team-conversations').style.width = ''")
                row.screenshot(path="/tmp/calliope-scope-progress.png")
                row.click()
                panel = page.locator(".viewer-conversation .specialist-work-progress")
                panel.wait_for()
                assert "Duplicate keys" in panel.inner_text()
                panel.locator("summary", has_text="Assignment boundaries").click()
                assert "Use <state> totals" in panel.inner_text()
                assert panel.locator("state").count() == 0
                assert panel.locator("li").first.bounding_box()["height"] < 120
                assert "Published app" in panel.inner_text()
                panel.screenshot(path="/tmp/calliope-scope-detail.png")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    assert page_errors == [], page_errors
    assert facts["specialty"] == "Analytical Skeptic"                 # the full specialty name, not the stub
    assert facts["pips"] == 2 and "rounds 1–2" in facts["text"]      # both rounds of this turn, as pips and as words
    assert "20 calls" in facts["text"]                                # calls summed across the rounds
    assert facts["mix"][:3] == ["SQL14", "find3", "chart2"]           # the tool mix, most used first, friendly names
    assert "3:42" in facts["text"]                                    # 2:10 + 1:32 of specialist time
    assert "token" not in facts["text"].lower()
    assert "Analytical Skeptic" in facts["aria"] and "rounds 1–2" in facts["aria"]
    narrow, wide = facts["narrow"], facts["wide"]
    assert narrow["facts"]["top"] > narrow["identity"]["bottom"] - 2 and narrow["mix"]["top"] > narrow["facts"]["bottom"] - 2   # narrow: three stacked lines
    assert wide["facts"]["left"] > wide["identity"]["right"] and wide["mix"]["left"] == wide["facts"]["left"]                     # wide: beside the identity, aligned
    assert wide["mix"]["top"] > wide["facts"]["bottom"] - 2                                                                       # the mix keeps its own line
    assert abs(((wide["facts"]["top"] + wide["mix"]["bottom"]) / 2) - ((wide["identity"]["top"] + wide["identity"]["bottom"]) / 2)) < 6
