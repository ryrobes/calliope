"""Chips and spotlights in the real Calliope page, against a stubbed session."""
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
NEXT_TURN = "94da7082-b64c-4f3b-8bc4-63e59fcb7d58"
S_CAND = "0b4c9b7e-5a5e-4d1e-9c2a-2d8f0e7a1c01"
S_WIDE = "0b4c9b7e-5a5e-4d1e-9c2a-2d8f0e7a1c02"
S_TOTALS = "0b4c9b7e-5a5e-4d1e-9c2a-2d8f0e7a1c03"
CANON = ("Start with Fletcher Great Smiles and Tel Aviv. About 1.4M orders sit behind that. "
         "I placed the five-candidate comparison on Stage.")
SCRIPT = "So, start with Fletcher Great Smiles and Tel Aviv. About 1.4M orders sit behind that.\n\nThe comparison is on the Stage."
CITATIONS = {
    "version": 4, "refs": [], "unresolved_refs": [],
    "spotlight": [{"surface_id": S_CAND, "reason": "inferred", "score": 3.0}],
    "values": [
        {"text": "Fletcher Great Smiles", "loose_case": True, "occurrences": 1, "surfaces": [S_CAND, S_WIDE], "columns": ["candidate"]},
        {"text": "Tel Aviv", "loose_case": True, "occurrences": 1, "surfaces": [S_CAND], "columns": ["store"]},
        {"text": "1.4M", "loose_case": True, "occurrences": 1, "surfaces": [S_TOTALS], "columns": ["orders"]},
    ],
    "vocabulary_size": 12, "query_surfaces": 3,
}


def _surface(sid, ordinal, title, rows):
    return {
        "id": sid, "session_id": SESSION, "turn_id": TURN, "ordinal": ordinal, "kind": "query", "title": title,
        "tool_name": "run_sql", "tool_call_id": f"call-{ordinal}", "payload": {"rows": rows, "row_count": len(rows), "columns": [{"name": k, "type": "text"} for k in rows[0]]},
        "source": {"phase": "exploration"}, "presentation": {"phase": "exploration"}, "created_at": "2026-09-20T12:00:00Z",
    }


def _run(presentation_on: bool, next_turn_started: bool = False):
    playwright = pytest.importorskip("playwright.sync_api")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(HERE)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    posted = []
    page_errors = []
    receipt = {"citations": CITATIONS}
    if presentation_on:
        receipt["presentation"] = {"id": "render-1", "version": 12, "script": SCRIPT, "mode": "conversational"}

    def route_api(route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/calliope/config":
            payload = {"healthy": True, "personal_briefs": False, "google_calendar": False,
                       "speech_to_text": {"enabled": False}, "text_to_speech": {"enabled": False, "render_version": 12},
                       "answer_presentation": {"enabled": presentation_on, "render_version": 12}}
        elif path == "/api/calliope/response-preferences":
            payload = {"owner": "wren.test@example.com", "exists": True, "updated_at": "2026-09-20T12:00:00Z",
                       "preferences": {"conversational_answers": presentation_on, "read_aloud_mode": "off",
                                       "response_personality": "", "speak_working_updates": False}}
        elif path == "/api/calliope/inbox":
            payload = {"items": [], "counts": {"unread": 0, "open": 0, "shown": 0, "by_kind": {}}}
        elif path in {"/api/calliope/styles", "/api/calliope/instruments", "/api/calliope/workflows"}:
            payload = {path.rsplit("/", 1)[-1]: []}
        elif path == "/api/calliope/sessions" and request.method == "GET":
            payload = {"sessions": [{"id": SESSION, "title": "Chips", "updated_at": "2026-09-20T12:00:00Z", "access_role": "owner", "read_only": False}]}
        elif path == f"/api/calliope/sessions/{SESSION}" and request.method == "GET":
            payload = {
                "session": {"id": SESSION, "title": "Chips", "access_role": "owner", "read_only": False},
                "turns": [{"id": TURN, "session_id": SESSION, "ordinal": 1, "user_message": "Which partners?", "assistant_message": CANON,
                           "attachments": [], "status": "complete", "turn_kind": "chat", "evidence_refs": [], "object_refs": [],
                           "response_receipt": receipt, "created_at": "2026-09-20T12:00:00Z", "completed_at": "2026-09-20T12:01:00Z"}]
                + ([{"id": NEXT_TURN, "session_id": SESSION, "ordinal": 2, "user_message": "And Perth?", "assistant_message": "Perth is smaller.",
                     "attachments": [], "status": "complete", "turn_kind": "chat", "evidence_refs": [], "object_refs": [],
                     "response_receipt": {}, "created_at": "2026-09-20T12:02:00Z", "completed_at": "2026-09-20T12:03:00Z"}] if next_turn_started else []),
                "surfaces": [
                    _surface(S_CAND, 1, "Candidates", [{"candidate": "Fletcher Great Smiles", "store": "Tel Aviv"}, {"candidate": "Other", "store": "Perth"}]),
                    _surface(S_WIDE, 2, "Account Place", [{"candidate": "Fletcher Great Smiles"}] * 3),
                    _surface(S_TOTALS, 3, "Totals", [{"orders": 1400000}]),
                ],
            }
        elif path == "/api/calliope/citations/events" and request.method == "POST":
            posted.append(json.loads(request.post_data or "{}"))
            payload = {"ok": True, "written": 1}
        elif path == "/api/calliope/session-events":
            route.fulfill(status=204, body="")
            return
        else:
            route.fulfill(status=404, content_type="application/json", body='{"error":{"message":"fixture"}}')
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    result = {}
    try:
        with playwright.sync_playwright() as runtime:
            launch = {"headless": True}
            if Path("/usr/bin/chromium").exists():
                launch["executable_path"] = "/usr/bin/chromium"
            browser = runtime.chromium.launch(**launch)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            # The theme bundle owns the voice preference; trap its global so
            # getVoice answers what this scenario needs, whatever it stores.
            context.add_init_script(
                "localStorage.setItem('rvbbit-calliope-voice-v1', JSON.stringify({version: 2, mode: 'off', personality: '', progress: false, presentation: %s}));"
                % ("true" if presentation_on else "false")
            )
            context.add_init_script(
                "(() => { const wanted = { mode: 'off', personality: '', progress: false, presentation: %s };"
                " let held; Object.defineProperty(window, 'WarehouseTheme', { configurable: true, get() { return held; },"
                " set(value) { held = value; if (held && typeof held === 'object') held.getVoice = () => wanted; } }); })();"
                % ("true" if presentation_on else "false")
            )
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/**", route_api)
            page.goto(f"http://127.0.0.1:{server.server_port}/calliope/index.html?session={SESSION}&view=zen", wait_until="networkidle")
            page.wait_for_selector(".value-chip", timeout=15000)
            result["transcript"] = page.locator(".voice-transcript").count()
            result["debug"] = page.evaluate("() => ({ getVoice: JSON.stringify(window.WarehouseTheme?.getVoice?.()), fallback: document.querySelectorAll('.voice-fallback-note').length, preparing: document.querySelectorAll('.voice-preparing').length, stored: localStorage.getItem('rvbbit-calliope-voice-v1'), themeKeys: Object.keys(window.WarehouseTheme || {}).slice(0, 12) })")
            result["chips"] = page.locator(".value-chip").evaluate_all("els => els.map(e => ({text: e.textContent.trim(), words: e.querySelectorAll('.voice-word').length, surfaces: e.dataset.chipSurfaces.split(',').length}))")
            result["spotlight"] = page.locator(".surface-spotlight").evaluate_all("els => els.map(e => e.textContent.trim())")
            result["spotlit_title"] = (page.locator(".surface.stage-spotlight h3").first.text_content()
                                       if page.locator(".surface.stage-spotlight").count() else None)
            result["chip_texts"] = page.locator(".value-chip").all_text_contents()
            page.locator(".value-chip", has_text="Tel Aviv").first.click()
            page.wait_for_function("() => document.querySelector('.surface.selected') !== null", timeout=10000)
            result["selected"] = page.locator(".surface.selected h3").first.text_content()
            result["ledger_child"] = page.locator("[data-zen-ledger-child]").count()
            page.locator(".value-chip", has_text="Fletcher Great Smiles").first.click()
            page.wait_for_selector(".value-chip-menu button", timeout=5000)
            result["menu"] = page.locator(".value-chip-menu button").evaluate_all("els => els.map(e => e.textContent)")
            page.wait_for_timeout(300)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert page_errors == [], page_errors
    result["posted"] = [e["kind"] for body in posted for e in body.get("events", [])]
    return result


def test_chips_and_spotlight_in_the_canon_zen_reply():
    r = _run(presentation_on=False)
    assert r["transcript"] == 0
    texts = {c["text"] for c in r["chips"]}
    assert {"Fletcher Great Smiles", "Tel Aviv", "1.4M"} <= texts
    assert r["spotlight"] == ["✦Most likely what Calliope meant"] or r["spotlight"] == ["Most likely what Calliope meant"]
    assert r["spotlit_title"] == "Candidates"
    assert r["selected"] == "Candidates"                       # one-surface chip focuses the result directly
    assert r["menu"] == ["Candidates · 2 rows", "Account Place · 3 rows"]
    assert r["posted"][:2] == ["chip_click", "chip_menu_open"]


def test_chips_ride_the_spoken_transcript_words():
    r = _run(presentation_on=True)
    assert r["transcript"] == 1, r["debug"]
    by_text = {c["text"].rstrip(".,;:"): c for c in r["chips"]}   # a chip keeps the word's trailing period
    assert by_text["Fletcher Great Smiles"]["words"] == 3     # one chip wraps three karaoke words
    assert by_text["Tel Aviv"]["words"] == 2
    assert by_text["1.4M"]["words"] == 1
    assert r["selected"] == "Candidates"


def test_spotlights_fall_back_into_the_timeline_once_the_next_turn_starts():
    """A spotlight is the latest reply's pointer. Once a later turn exists the
    earlier turn's cards are ordinary again: no marker, no glow, no lift —
    otherwise "Calliope pointed here" cards pile up at the top forever."""
    r = _run(presentation_on=False, next_turn_started=True)
    assert r["spotlight"] == []
    assert r["spotlit_title"] is None
    # The chips in the earlier reply still open their results.
    assert r["selected"] == "Candidates"

