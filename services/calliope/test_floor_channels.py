"""External thread provenance stays visible without marking native/private work."""
from test_floor_veil import ORIGIN, PAYLOAD, floor_page


def test_floor_channel_badge_is_subtle_and_does_not_block_opening(floor_page):
    page, held = floor_page
    cards = [
        {**PAYLOAD['cards'][0], 'item_id': 'chat:google', 'channel': 'google_chat'},
        {**PAYLOAD['cards'][0], 'item_id': 'chat:web', 'channel': 'web'},
        {**PAYLOAD['cards'][0], 'item_id': 'chat:legacy'},
        {**PAYLOAD['cards'][0], 'item_id': 'shadow:private', 'silhouette': True,
         'channel': 'google_chat', 'column_state': 'in_motion'},
    ]
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json={**PAYLOAD, 'total': 4, 'matched_total': 4, 'cards': cards,
                            'lanes': [{'column_state': 'done', 'pieces': 3, 'matched': 3},
                                      {'column_state': 'in_motion', 'pieces': 1, 'matched': 1}]})
    badge = page.locator('.piece-channel')
    badge.wait_for()
    assert badge.count() == 1
    assert badge.get_attribute('aria-label') == 'Google Chat · mirrored conversation'
    assert badge.get_attribute('title') is None
    assert badge.get_attribute('data-floor-tooltip') == 'Google Chat · mirrored conversation'
    assert badge.evaluate('(el)=>Number(getComputedStyle(el).opacity)') == .45
    card = page.locator('.piece[data-id="chat:google"]')
    a, b = card.bounding_box(), badge.bounding_box()
    assert a['x']+a['width']-30 <= b['x'] < a['x']+a['width']
    assert a['y'] <= b['y'] < a['y']+25
    page.screenshot(path='/tmp/floor-channel-badge.png')
    badge.click()
    assert 'open' in page.locator('#drawerBackdrop').get_attribute('class')
