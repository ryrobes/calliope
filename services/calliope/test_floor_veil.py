"""Browser coverage for the initial Floor veil; no database required."""
import json
import mimetypes
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
ORIGIN = 'http://floor.test'
PAYLOAD = {
    'total': 1, 'matched_total': 1,
    'cards': [{'item_id': 'chat:1', 'item_kind': 'chat', 'column_state': 'done',
               'display_title': 'A loaded notebook', 'agents': ['calliope'],
               'owner_email': 'viewer@example.com', 'idle_seconds': 0}],
    'lanes': [{'column_state': 'done', 'pieces': 1, 'matched': 1}],
    'viewer': {'email': 'viewer@example.com'},
}


@pytest.fixture
def floor_page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                   args=['--no-sandbox', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 1500, 'height': 900})
        held = []

        def route(request):
            from urllib.parse import urlsplit
            url = urlsplit(request.request.url)
            if url.netloc != 'floor.test':
                request.abort(); return
            if url.path == '/api/calliope/floor':
                held.append(request); return
            if url.path.startswith('/api/'):
                request.fulfill(json=PAYLOAD if '/pieces' in url.path else {}); return
            if url.path == '/floor':
                html = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/floor.html').read_text())
                # Deterministic theme without fetching an account's preferences.
                html = html.replace('</head>', '<style>:root{--background:#170c08;--foreground:#f4eadf;--rvbbit-accent:#ff783e;--rvbbit-type-body:14px;--rvbbit-type-small:12px}</style></head>')
                request.fulfill(body=html, content_type='text/html'); return
            parts = url.path.strip('/').split('/', 1)
            folder = 'calliope' if parts[0] in ('floor', 'calliope') else parts[0]
            path = ROOT/folder/(parts[1] if len(parts) > 1 else '')
            if path.is_file() and path.resolve().is_relative_to(ROOT):
                request.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            else:
                request.fulfill(status=404)

        page.route('**/*', route)
        try:
            yield page, held
        finally:
            browser.close()


def test_initial_veil_is_scoped_and_refresh_does_not_rearm(floor_page):
    page, held = floor_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    page.wait_for_selector('#floorInitialVeil[data-ready="true"]')
    veil = page.locator('#floorInitialVeil')
    for width, height in [(1500, 900), (1100, 600), (1800, 750)]:
        page.set_viewport_size({'width': width, 'height': height})
        box = veil.bounding_box()
        rail = page.locator('.floor-rail').bounding_box()
        assert box['height'] > 200
        assert abs(box['y'] - (rail['y']+rail['height'])) <= 1
        assert abs(box['y']+box['height']-height) <= 1
        assert page.locator('#search').is_hidden()
        assert page.locator('.floor-footer').is_hidden()
        assert page.locator('.floor-rail').is_visible()
    page.screenshot(path='/tmp/floor-initial-veil.png')
    page.evaluate('window.loadingCanvas=document.querySelector("#floorInitialVeil canvas")')
    # The full-size video keeps its framing, then moves down as content fades in.
    page.evaluate('''() => {
        const timeline = document.querySelector('#timebar');
        const observer = new MutationObserver(() => {
            if (timeline.hidden) return;
            const veil = document.querySelector('#floorInitialVeil');
            const box = veil.getBoundingClientRect();
            window.timelineOverlaySample = {
                height: box.height, appHeight: document.querySelector('.app').clientHeight,
                push: parseFloat(veil.style.getPropertyValue('--floor-veil-push')),
                leaving: veil.classList.contains('is-leaving')
            };
            observer.disconnect();
        });
        observer.observe(timeline, {attributes: true, attributeFilter: ['hidden']});
    }''')
    held.pop().fulfill(json={**PAYLOAD, 'snapshots': [
        {'snapshot_at': '2026-09-07T00:00:00Z', 'total_items': 1, 'counts_by_column_state': {'done': 1}},
        {'snapshot_at': '2026-09-08T00:00:00Z', 'total_items': 1, 'counts_by_column_state': {'done': 1}},
    ]})
    page.wait_for_function('window.timelineOverlaySample')
    after = page.evaluate('window.timelineOverlaySample')
    assert after['leaving']
    assert after['push'] > 100
    assert abs(after['height']-after['appHeight']) <= 1
    page.wait_for_selector('.piece')
    page.wait_for_selector('#floorInitialVeil', state='detached')
    assert not page.evaluate('window.loadingCanvas.isConnected')
    assert page.locator('#search').is_visible()
    assert page.locator('.floor-footer').is_visible()
    page.locator('#reload').click()
    page.wait_for_timeout(100)
    assert held and page.locator('.piece').count() == 1
    assert page.locator('#floorInitialVeil').count() == 0
    held.pop().fulfill(json=PAYLOAD)
    page.screenshot(path='/tmp/floor-initial-veil-loaded.png')
    assert not errors


@pytest.mark.parametrize('outcome', ['empty', 'error', 'late_import', 'failed_import', 'reduced_motion'])
def test_initial_veil_always_settles(floor_page, outcome):
    page, held = floor_page
    imports = []
    if outcome == 'late_import':
        page.route('**/ascii-veil.js', lambda route: imports.append(route))
    elif outcome == 'failed_import':
        page.route('**/ascii-veil.js', lambda route: route.abort())
    elif outcome == 'reduced_motion':
        page.emulate_media(reduced_motion='reduce')
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    page.wait_for_function('typeof window.floorInitialVeil?.finish === "function"')
    while not held:
        page.wait_for_timeout(10)
    if outcome == 'reduced_motion':
        page.wait_for_selector('#floorInitialVeil[data-ready="true"]')
    if outcome == 'error':
        held.pop().fulfill(status=500, json={'error': {'message': 'Unavailable'}})
        page.wait_for_selector('#workspace .empty')
    else:
        held.pop().fulfill(json={'total': 0, 'cards': [], 'lanes': []} if outcome == 'empty' else PAYLOAD)
        page.wait_for_function('document.querySelector("#status").textContent.startsWith("Live")')
    page.wait_for_selector('#floorInitialVeil', state='detached')
    if outcome == 'late_import':
        assert imports
        imports.pop().fulfill(body=(ROOT/'calliope/ascii-veil.js').read_text(), content_type='text/javascript')
        page.wait_for_timeout(100)
    assert page.locator('.ascii-veil-canvas').count() == 0
    assert page.locator('#search').is_visible()
    assert page.locator('.floor-footer').is_visible()
