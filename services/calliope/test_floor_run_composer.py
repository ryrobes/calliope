"""Real Floor modals and the shared CodeMirror bundle, with local API fixtures."""
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_floor_veil import floor_page, ORIGIN, PAYLOAD, ROOT


@pytest.fixture
def run_page(floor_page):
    page, held = floor_page
    requests, errors = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    agents = [{"key": "calliope", "name": "Calliope", "primary": True},
              {"key": "iris", "name": "Iris", "description": "Design specialist"}]

    def source(route):
        script = (ROOT / "calliope/floor.js").read_text().replace("\nload();\n})();", """
window.qa={openDirectionModal,openScheduleModal,scheduleModalState,loadPlaybookPanel};
window.receipts=[];
load();
})();""")
        route.fulfill(body=script, content_type="text/javascript")
    page.route("**/floor.js*", source)
    page.route("**/api/calliope/config", lambda r: r.fulfill(json={"chat_agents": agents}))

    def people(route):
        query = parse_qs(urlsplit(route.request.url).query).get("q", [""])[0].lower()
        rows = [{"display_name": "Iris Person", "email": "iris@example.com"},
                {"display_name": "Zoë Example", "email": "zoe@example.com"}]
        route.fulfill(json={"people": [p for p in rows if query in (p["display_name"] + p["email"]).lower()]})
    page.route("**/api/calliope/team-people?*", people)
    page.route("**/api/calliope/objects?*", lambda r: r.fulfill(json={"objects": [
        {"kind": "metric", "ref_id": "sales", "label": "Revenue", "description": "Net revenue"}]}))
    page.goto(ORIGIN + "/floor", wait_until="domcontentloaded")
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json=PAYLOAD)
    page.wait_for_selector("#floorInitialVeil", state="detached")
    page.add_style_tag(content="""body{--page:#170c08;--panel:#251a16;--card:#2c211b;
      --ink:#f4eadf;--muted:#baaea4;--line:#5d4034;--accent:#ff783e;--main-foreground:#fff;
      --font:var(--font-superior-sans, sans-serif)}""")
    yield page, requests
    assert errors == []


def direction(page):
    page.evaluate("qa.openDirectionModal({title:'Weekly review',onSubmit:async value=>receipts.push(value)})")
    editor = page.locator("#dirTextEditor .cm-content")
    expect(editor).to_be_focused()
    return editor


@pytest.mark.parametrize("has_sketch", [False, True])
def test_playbook_written_method_is_readable_without_a_sketch(run_page, has_sketch):
    page, requests = run_page
    page.add_style_tag(content="body{--amber:#c89345;--sans:Arial,sans-serif;--mono:monospace;--rvbbit-type-large:20px}")
    playbook_id = "b2c2a869-f01a-5bd7-9329-95874595d6cc"
    contract = {
        "outcome": "A dashboard grounded in evidence.",
        "deliverable": "A working live artifact.",
        "method": [f"Step {i}: inspect <source> & verify." for i in range(1, 9)],
        "guardrails": ["<img src=x onerror=alert(1)> stays plain text."],
        "completion_criteria": ["Check all figures."],
        "fallbacks": ["Ask for missing scope."],
    }
    page.route("**/api/calliope/playbooks/" + playbook_id, lambda route: route.fulfill(json={
        "playbook": {"id": playbook_id, "title": "Factory method", "version": 3,
                     "synopsis": "Build an evidence-based dashboard.", "contract": contract,
                     "has_sketch": has_sketch, "approved": True},
    }))
    page.evaluate("""async id => {
        document.querySelector('#drawerBody').innerHTML='<div id="pieceContext"></div>';
        document.querySelector('#drawerBackdrop').classList.add('open');
        await qa.loadPlaybookPanel({item_id:'playbook:'+id,item_kind:'playbook'});
    }""", playbook_id)
    details = page.locator(".drawer-playbook-contract")
    expect(details.locator("summary")).to_have_text("Read the full playbook · v3")
    details.locator("summary").click()
    expect(details.locator("ol li")).to_have_count(8)
    expect(details).to_contain_text(contract["method"][-1])
    expect(details).to_contain_text(contract["guardrails"][0])
    expect(details).to_contain_text(contract["fallbacks"][0])
    assert details.locator("img,script").count() == 0
    visual = page.locator('#pieceContext a[href*="/sketch"]')
    if has_sketch:
        expect(visual).to_have_attribute("href", f"/calliope/playbooks/{playbook_id}/sketch?version=3")
    else:
        expect(visual).to_have_count(0)
    assert not any("/sketch" in url for url in requests)
    page.screenshot(path=f"/tmp/floor-playbook-{'visual' if has_sketch else 'written'}.png")


def test_run_composer_size_newlines_and_no_discovery(run_page):
    page, requests = run_page
    editor = direction(page)
    expect(editor).to_be_focused()
    assert page.locator("#floorDirectionModal .sched-modal").bounding_box()["width"] >= 800
    assert float(editor.evaluate("e=>getComputedStyle(e).fontSize").removesuffix("px")) >= 18
    editor.press_sequentially("Review this period")
    editor.press("Enter")
    editor.press_sequentially("Focus on sales")
    page.wait_for_timeout(850)
    assert page.evaluate("receipts") == []
    assert not any("evidence-discovery" in url or "object-hints" in url for url in requests)
    page.screenshot(path="/tmp/floor-run-desktop.png")
    editor.press("Control+Enter")
    expect(page.locator("#floorDirectionModal")).to_have_count(0)
    assert page.evaluate("receipts") == ["Review this period\nFocus on sales"]


def test_shared_mentions_offer_people_and_agents_without_confusion(run_page):
    page, requests = run_page
    editor = direction(page)
    editor.press_sequentially("@Iri")
    options = page.locator(".cm-tooltip-autocomplete li")
    expect(options).to_have_count(2)
    expect(page.get_by_text("Person · iris@example.com", exact=True)).to_be_visible()
    page.get_by_text("Person · iris@example.com", exact=True).click()
    expect(editor).to_have_text("@iris@example.com ")
    # The shared editor also queries people without an agents callback.
    page.evaluate("""() => {
      document.querySelector('#floorDirectionModal')._closeRunDialog();
      const host=document.createElement('div');document.body.append(host);
      window.peopleEditor=CalliopeObjectEditor.mount(host,{variant:'composer'});
      peopleEditor.focus();
    }""")
    page.keyboard.type("@zo")
    page.get_by_text("Person · zoe@example.com", exact=True).click()
    assert page.evaluate("peopleEditor.getValue()") == "@zoe@example.com "
    assert any("team-people?q=zo" in url for url in requests)
    page.evaluate("peopleEditor.destroy()")


def test_explicit_object_lookup_and_reopening_dispose_editors(run_page):
    page, _ = run_page
    editor = direction(page)
    editor.press_sequentially("[[Rev")
    page.locator(".cm-tooltip-autocomplete li").first.wait_for()
    editor.press("Tab")
    assert "Revenue" in editor.inner_text()
    page.locator("#dirSubmit").click()
    assert "[[metric:sales|Revenue]]" in page.evaluate("receipts[0]")
    direction(page)
    direction(page)
    expect(page.locator("#floorDirectionModal")).to_have_count(1)
    expect(page.locator("#floorDirectionModal .cm-editor")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("#floorDirectionModal")).to_have_count(0)


def test_schedule_builder_monthly_sunday_and_custom(run_page):
    page, _ = run_page
    page.evaluate("qa.openScheduleModal({title:'Weekly review',defaultMission:'Send the report',onSubmit:async value=>receipts.push(value)})")
    expect(page.locator("#schedMissionEditor .cm-content")).to_have_text("Send the report")
    page.locator('[data-preset="weekly"]').click()
    page.locator("#schedDay").select_option("0")
    page.locator("#schedTime").fill("14:35")
    assert page.evaluate("qa.scheduleModalState().cron") == "35 14 * * 0"
    expect(page.locator("#schedEcho")).to_contain_text("Sunday")
    page.screenshot(path="/tmp/floor-schedule-desktop.png")
    page.locator('[data-preset="monthly"]').click()
    page.locator("#schedMonthDay").select_option("31")
    assert page.evaluate("qa.scheduleModalState().cron") == "35 14 31 * *"
    expect(page.locator("#schedEcho")).to_contain_text("Months without this date are skipped")
    page.locator('[data-preset="custom"]').click()
    expect(page.locator("#schedTimeWrap")).to_be_hidden()
    page.locator("#schedCron").fill("0 7 * * 1-5")
    page.locator("#schedSubmit").click()
    page.wait_for_function("receipts.length===1")
    assert page.evaluate("receipts[0].cron") == "0 7 * * 1-5"
    assert page.evaluate("receipts[0].mission") == "Send the report"


def test_modals_fit_mobile_and_keep_submit_reachable(run_page):
    page, _ = run_page
    page.set_viewport_size({"width": 390, "height": 740})
    page.evaluate("qa.openScheduleModal({title:'A long playbook name for the monthly management review',onSubmit:async value=>receipts.push(value)})")
    modal = page.locator(".sched-modal")
    assert modal.bounding_box()["width"] <= 390
    assert modal.evaluate("e=>e.scrollWidth<=e.clientWidth")
    page.locator("#schedSubmit").scroll_into_view_if_needed()
    expect(page.locator("#schedSubmit")).to_be_in_viewport()
    page.screenshot(path="/tmp/floor-schedule-mobile.png")


def test_failed_submit_keeps_draft_and_completion_escape_keeps_dialog(run_page):
    page, _ = run_page
    page.evaluate("""qa.openDirectionModal({title:'Review',onSubmit:async value=>{
      if(!window.tried){window.tried=true;throw Error('Try again')}receipts.push(value);
    }})""")
    editor = page.locator("#dirTextEditor .cm-content")
    expect(editor).to_be_focused()
    editor.press_sequentially("@Iri")
    page.locator(".cm-tooltip-autocomplete li").first.wait_for()
    editor.press("Escape")
    expect(page.locator("#floorDirectionModal")).to_have_count(1)
    editor.press("Control+a")
    editor.press_sequentially("Keep my direction")
    page.locator("#dirSubmit").click()
    expect(page.locator("#dirSubmit")).to_be_enabled()
    expect(editor).to_have_text("Keep my direction")
    page.locator("#dirSubmit").click()
    page.wait_for_function("receipts.length===1")
    assert page.evaluate("receipts[0]") == "Keep my direction"
