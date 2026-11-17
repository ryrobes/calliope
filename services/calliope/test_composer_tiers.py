"""The composer action row never wraps: labels collapse by the row's own width.

Renders the real #composer markup with the real stylesheet at several widths
(container queries, so the chat column's width decides, not the viewport) and
checks one row of buttons everywhere, with labels shed in tiers: utilities
first, then Playbook and the Design Profile, then Team. Icon-only buttons keep
their accessible names.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
INDEX = (HERE / "calliope" / "index.html").read_text(encoding="utf-8")
CSS = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")


def _composer_markup() -> str:
    start = INDEX.index('<form id="composer"')
    form = INDEX[start:INDEX.index("</form>", start) + 7]
    for ident in ("speech-record", "composer-playbook-open", "composer-rail-toggle",
                  "zen-team-toggle", "zen-design-control"):
        form = re.sub(rf'(id="{ident}"[^>]*?) hidden', r"\1", form, count=1)
    return form.replace('<span class="label">design</span>', '<span class="label">Adaptive Calliope</span>')


def test_every_composer_button_has_an_accessible_name_without_its_label():
    form = _composer_markup()
    for ident, name in (("visual-intent-open", "Visual intent"), ("composer-playbook-open", "Playbook"),
                        ("zen-team-toggle", "Team mode"), ("zen-design-button", "Design Profile"),
                        ("composer-rail-toggle", "Suggest metrics"),
                        ("room-address-toggle", "Who this message is for")):
        tag = re.search(rf'<button id="{ident}"[^>]*>', form).group(0)
        assert f'aria-label="{name}"' in tag, ident
    assert 'class="attach-action" title="Attach files" aria-label="Attach files"' in form
    assert 'id="shared-files-open"' in form
    assert form.count('class="label"') == 9


@pytest.mark.parametrize("width,expected_labels", [
    (1000, ["Attach", "Visual intent", "Dictate", "Adaptive Calliope", "Suggest", "Playbook", "Team off"]),
    (900, ["Adaptive Calliope", "Suggest", "Playbook", "Team off"]),
    (760, ["Team off"]),
    (500, []),
])
def test_composer_row_stays_single_line_and_sheds_labels_by_tier(width, expected_labels):
    from playwright.sync_api import sync_playwright
    page_html = (
        f"<!doctype html><html><head><meta charset=utf-8><style>{CSS}"
        ".harness{width:100%;display:block} .harness #composer{width:100%;display:block;box-sizing:border-box}</style></head>"
        f'<body data-warehouse-page="calliope" style="margin:0"><div style="width:{width}px"><div class="harness">{_composer_markup()}</div></div></body></html>'
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1100, "height": 600})
        page.set_content(page_html)
        page.wait_for_timeout(150)
        report = page.evaluate("""() => {
            const row = document.querySelector('.compose-actions');
            const buttons = [...row.querySelectorAll('button:not([hidden]), label.attach-action')].filter(b => b.offsetParent !== null);
            const tops = new Set(buttons.map(b => Math.round(b.getBoundingClientRect().top)));
            const labels = [...row.querySelectorAll('.label')].filter(l => l.offsetParent !== null && getComputedStyle(l).display !== 'none').map(l => l.textContent.trim());
            return {rows: tops.size, labels, buttons: buttons.length};
        }""")
        browser.close()
    assert report["rows"] == 1, report
    assert report["labels"] == expected_labels, report
    assert report["buttons"] == 9          # attach, files, visual intent, dictate, design, suggest, playbook, team, send
