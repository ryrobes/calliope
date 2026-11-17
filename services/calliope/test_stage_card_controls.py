"""Stage card header controls: three-size width cycle, pin-to-top, hover state.

All three are viewer-local choices kept in the browser. They run against the
real page in Chromium (the zen fixture + the packing helpers) so the packer's
column spans, DOM order and persisted values are what is pinned, not markup.
"""
from __future__ import annotations

from pathlib import Path

from test_zen_chat_layout import zen_page  # noqa: F401

HERE = Path(__file__).resolve().parent
JS = (HERE / "calliope" / "calliope.js").read_text(encoding="utf-8")
CSS = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")


def _width(page, card):
    return page.locator(f'[data-surface-id={card}]').evaluate('(n)=>n.getBoundingClientRect().width')


def _stored(page, key):
    return page.evaluate(f'() => JSON.parse(localStorage.getItem("{key}") || "null")')


def test_width_toggle_cycles_one_two_and_all_columns_and_persists(zen_page):
    """small → medium → large → small. Sizes are whole packed columns so cards
    tile: on a two-column Stage medium already spans the row. Only a choice
    that differs from the card's default is stored, and legacy half/full
    values read back as small/large."""
    from test_stage_packing import prepare, populate
    page = zen_page
    prepare(page, 1100)                                              # two packed columns
    populate(page, [200, 200, 200])
    small = _width(page, 'card-0')
    toggle = page.locator('[data-surface-id=card-0] [data-toggle-surface-width]')
    assert toggle.get_attribute('data-surface-width') == 'small'   # images default to one column
    assert toggle.inner_text() == '▯'
    toggle.click(); page.wait_for_timeout(300)
    medium = _width(page, 'card-0')
    assert medium > small * 1.8, (small, medium)                    # two columns = the whole row here
    assert page.locator('[data-surface-id=card-0]').evaluate('(n)=>n.classList.contains("stage-width-medium")')
    assert page.locator('[data-surface-id=card-0] [data-toggle-surface-width]').get_attribute('data-surface-width') == 'medium'
    assert _stored(page, 'rvbbit-calliope-surface-width-v1') == {'card-0': 'medium'}
    page.locator('[data-surface-id=card-0] [data-toggle-surface-width]').click(); page.wait_for_timeout(300)
    assert page.locator('[data-surface-id=card-0]').evaluate('(n)=>n.classList.contains("stage-width-large")')
    assert _width(page, 'card-0') > small * 1.8
    assert _stored(page, 'rvbbit-calliope-surface-width-v1') == {'card-0': 'large'}
    page.locator('[data-surface-id=card-0] [data-toggle-surface-width]').click(); page.wait_for_timeout(300)
    assert _stored(page, 'rvbbit-calliope-surface-width-v1') == {}   # back to the default → nothing stored
    assert _width(page, 'card-0') < medium * 0.6
    # legacy values migrate on load
    assert 'SURFACE_WIDTH_LEGACY = { half: "small", full: "large" }' in JS
    assert '.zen-packed>.stage-width-small{--stage-span:1}' in CSS
    assert '.zen-packed>.stage-width-medium{--stage-span:2}' in CSS
    assert '.zen-packed>.stage-width-large{--stage-span:var(--stage-columns)}' in CSS


def test_pin_toggle_moves_a_card_to_the_head_of_the_stage_and_persists(zen_page):
    """Pinning an older card puts it first in the packed timeline; the rest keep
    their chronology and flow in after it. Unpinning restores the order."""
    from test_stage_packing import prepare, populate, geometry
    page = zen_page
    prepare(page, 1100)
    populate(page, [200, 200, 200])                                   # card-0 newest … card-2 oldest
    assert [c['id'] for c in geometry(page)] == ['card-0', 'card-1', 'card-2']
    pin = page.locator('[data-surface-id=card-2] [data-toggle-surface-pin]')
    assert pin.get_attribute('aria-pressed') == 'false'
    pin.click(); page.wait_for_timeout(300)
    cards = geometry(page)
    assert [c['id'] for c in cards] == ['card-2', 'card-0', 'card-1']
    assert cards[0]['x'] == 0 and cards[0]['y'] == 0                  # leads the grid
    assert page.locator('[data-surface-id=card-2]').evaluate('(n)=>n.classList.contains("stage-pinned")')
    assert page.locator('[data-surface-id=card-2] [data-toggle-surface-pin]').get_attribute('aria-pressed') == 'true'
    assert _stored(page, 'rvbbit-calliope-surface-pin-v1') == ['card-2']
    page.locator('[data-surface-id=card-2] [data-toggle-surface-pin]').click(); page.wait_for_timeout(300)
    assert [c['id'] for c in geometry(page)] == ['card-0', 'card-1', 'card-2']
    assert _stored(page, 'rvbbit-calliope-surface-pin-v1') == []


def test_zen_cards_are_translucent_at_rest_and_solid_under_the_pointer():
    """The shared theme's :hover rule (panel-raised 59%) used to beat the zen
    97% rest state, so cards dimmed on hover and solidified on mouse-out. The
    same two values are kept; only the triggers are swapped."""
    rest = 'html:not([data-warehouse-color-mode="light"]) body.zen[data-warehouse-page="calliope"] .surface:not(.kind-artifact){background:color-mix(in oklch,var(--panel-raised) 59%,transparent)}'
    hover = 'html:not([data-warehouse-color-mode="light"]) body.zen[data-warehouse-page="calliope"] .surface:not(.kind-artifact):hover{background:color-mix(in oklch,var(--panel) 97%,transparent)}'
    assert rest in CSS and hover in CSS
    assert CSS.index(rest) < CSS.index(hover)
    theme = (HERE / "theme" / "warehouse-theme.css").read_text(encoding="utf-8")
    assert 'body[data-warehouse-page="calliope"] .surface:not(.kind-artifact):hover {\n  background: color-mix(in oklch, var(--panel-raised) 59%, transparent);' in theme


def test_zen_cards_render_translucent_then_solid_in_chromium(zen_page):
    from test_stage_packing import prepare, populate
    page = zen_page
    page.evaluate("() => document.documentElement.setAttribute('data-warehouse-theme', 'default')")
    prepare(page, 1100)
    populate(page, [200, 200])
    card = page.locator('[data-surface-id=card-0]')
    rest = card.evaluate('(n)=>getComputedStyle(n).backgroundColor')
    card.hover(); page.wait_for_timeout(250)
    hovered = card.evaluate('(n)=>getComputedStyle(n).backgroundColor')
    alpha = lambda color: float(color.split('/')[-1].strip(' )')) if '/' in color else (float(color.split(',')[-1].strip(' )')) if color.startswith('rgba') else 1.0)
    assert alpha(rest) < alpha(hovered), (rest, hovered)
