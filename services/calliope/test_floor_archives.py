"""Set-aside authorization and browser lane/count reconciliation."""
import ast
import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from test_floor_veil import ORIGIN, PAYLOAD, ROOT, floor_page


@pytest.mark.parametrize('piece,restore,expected', [
    (None, False, 404),
    ({'owner_email': 'someone@example.com', 'column_state': 'awaiting_you'}, False, 403),
    ({'owner_email': 'viewer@example.com', 'column_state': 'in_motion'}, False, 409),
    ({'owner_email': 'viewer@example.com', 'column_state': 'awaiting_you'}, False, 200),
    ({'owner_email': None, 'column_state': 'noticed'}, False, 200),
    ({'owner_email': 'viewer@example.com', 'column_state': 'archived'}, True, 200),
])
def test_archive_route_authorizes_before_writing(piece, restore, expected):
    module = ast.parse((ROOT/'calliope.py').read_text())
    handler = next(n for n in ast.walk(module) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'floor_piece_archive')
    handler.decorator_list = []
    writes = []

    def execute(sql, params):
        writes.append((sql, params))
        return SimpleNamespace(fetchone=lambda: {'item_id': 'notice:1'})

    async def body():
        return {'item_id': 'notice:1', 'restore': restore}

    namespace = dict(asyncio=asyncio, api_owner=lambda _: ('viewer@example.com', None),
                     _floor_piece_for=lambda *_: piece,
                     conn_factory=lambda: contextlib.nullcontext(SimpleNamespace(execute=execute)),
                     json_response=lambda payload, status=200: (payload, status))
    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<archive-route>', 'exec'), namespace)
    payload, status = asyncio.run(namespace['floor_piece_archive'](SimpleNamespace(json=body)))
    assert status == expected
    assert len(writes) == (1 if expected == 200 else 0)
    if expected == 200:
        assert payload['set_aside'] is not restore
        assert 'viewer@example.com' in writes[0][1]


def test_archive_and_restore_reload_authoritative_lanes(floor_page):
    page, held = floor_page
    current = {**PAYLOAD, 'cards': [{**PAYLOAD['cards'][0], 'column_state': 'awaiting_you'}],
               'lanes': [{'column_state': 'awaiting_you', 'pieces': 1, 'matched': 1}]}
    writes = []

    def archive(route):
        request = route.request.post_data_json
        writes.append(request)
        # A restored source can have changed state since it was set aside.
        lane = 'noticed' if request.get('restore') else 'archived'
        current['cards'][0].update(column_state=lane, set_aside=lane == 'archived')
        current['lanes'] = [{'column_state': lane, 'pieces': 1, 'matched': 1}]
        route.fulfill(json={'item_id': request['item_id'], 'set_aside': lane == 'archived'})

    page.route('**/api/calliope/floor/pieces/archive', archive)
    page.route('**/api/calliope/floor/pieces?**', lambda route: route.fulfill(json=current))
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json=current)
    page.locator('.piece').click()
    page.locator('#setAsidePiece').click()
    page.wait_for_function("document.querySelector('[data-expand=archived] .rail-count').textContent==='1'")
    assert page.locator('.lane[data-lane=awaiting_you] .count').inner_text() == '0'
    page.get_by_role('button', name='Expand Archived', exact=True).click()
    page.locator('.lane[data-lane=archived] .piece').click()
    page.locator('#restorePiece').click()
    page.wait_for_function("document.querySelector('[data-expand=noticed] .rail-count').textContent==='1'")
    assert page.locator('.lane[data-lane=archived] .count').inner_text() == '0'
    assert page.locator('.lane[data-lane=awaiting_you] .count').inner_text() == '0'
    assert len(writes) == 2 and writes[-1]['restore'] is True


def test_drag_to_collapsed_archive_uses_receipted_action(floor_page):
    page, held = floor_page
    card = {**PAYLOAD['cards'][0], 'column_state': 'awaiting_you'}
    current = {**PAYLOAD, 'cards': [card, {**PAYLOAD['cards'][0], 'item_id': 'chat:done'}],
               'total': 2, 'matched_total': 2,
               'lanes': [{'column_state': lane, 'pieces': 1, 'matched': 1}
                         for lane in ('awaiting_you', 'done')]}
    writes = []

    def archive(route):
        writes.append(route.request.post_data_json)
        card.update(column_state='archived', set_aside=True)
        current['lanes'][0]['column_state'] = 'archived'
        route.fulfill(json={'item_id': card['item_id'], 'set_aside': True})

    page.route('**/api/calliope/floor/pieces/archive', archive)
    page.route('**/api/calliope/floor/pieces?**', lambda route: route.fulfill(json=current))
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json=current)
    piece = page.locator('.piece[data-id="chat:1"]')
    piece.wait_for()
    assert piece.get_attribute('draggable') == 'true'
    assert page.locator('.piece[data-id="chat:done"]').get_attribute('draggable') == 'false'
    transfer = page.evaluate_handle('new DataTransfer()')
    piece.dispatch_event('dragstart', {'dataTransfer': transfer})
    target = page.locator('.rail[data-lane="archived"]')
    assert 'drop-ok' in target.get_attribute('class')
    assert 'drop-no' in page.locator('.lane[data-lane="done"]').get_attribute('class')
    # An invalid target must never trigger a write.
    page.locator('.lane[data-lane="done"]').dispatch_event('drop', {'dataTransfer': transfer})
    assert not writes
    target.dispatch_event('dragover', {'dataTransfer': transfer})
    target.dispatch_event('drop', {'dataTransfer': transfer})
    page.wait_for_function("document.querySelector('[data-expand=archived] .rail-count').textContent==='1'")
    assert len(writes) == 1 and writes[0]['item_id'] == 'chat:1'
    assert page.locator('.board.drag-live').count() == 0
    assert page.locator('.lane[data-lane=awaiting_you] .count').inner_text() == '0'
