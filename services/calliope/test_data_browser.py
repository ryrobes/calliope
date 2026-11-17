"""Connected Data keeps its own layout while sharing Floor's visual chrome."""
import re

import pytest

from test_floor_veil import ROOT, ORIGIN, floor_page


@pytest.mark.parametrize('width', [1500, 600, 390])
def test_connected_data_controls_and_inventory_stay_in_view(floor_page, width):
    page, _ = floor_page
    page.set_viewport_size({'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def data_route(route):
        path = route.request.url.split('?', 1)[0].removeprefix(ORIGIN)
        if path == '/data':
            html = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/data.html').read_text())
            html = html.replace('</head>', '<style>:root{--background:#170c08;--foreground:#f4eadf;--block-bg:#2a1b15;--secondary-background:#21140f;--chrome-text:#b5a69a;--block-border:#514035;--rvbbit-accent:#ff783e;--rvbbit-type-body:14px;--rvbbit-type-small:12px}</style></head>')
            route.fulfill(body=html, content_type='text/html')
        else:
            asset = ROOT/'calliope'/path.rsplit('/', 1)[-1]
            route.fulfill(body=asset.read_bytes(), content_type='text/css' if path.endswith('.css') else 'text/javascript')

    page.route(ORIGIN+'/data**', data_route)
    page.route(ORIGIN+'/api/calliope/data-inventory', lambda route: route.fulfill(json={
        'schemas': [{'name': 'sales', 'total_rows': 420, 'tables': [
            {'name': 'orders', 'rows': 420}, {'name': 'customers', 'rows': 20},
        ]}],
    }))
    page.goto(ORIGIN+'/data', wait_until='networkidle')
    page.locator('#dataTree').get_by_text('sales', exact=True).wait_for()
    page.screenshot(path=f'/tmp/calliope-data-{width}.png')
    rail = page.locator('.floor-rail').bounding_box()
    toolbar = page.locator('.floor-toolbar').bounding_box()
    context = page.locator('.contextbar').bounding_box()
    tree = page.locator('#dataTree').bounding_box()
    diagram = page.locator('#dataMap').bounding_box()
    assert toolbar['y'] >= rail['y'] + rail['height'] - 1
    assert context['y'] >= toolbar['y'] + toolbar['height'] - 1
    assert tree['y'] >= context['y'] + context['height'] - 1
    for box in (rail, toolbar, context, tree, diagram):
        assert box['width'] > 0 and box['height'] > 0
        assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1
        assert box['y'] + box['height'] <= 901
    search = page.locator('#search').bounding_box()
    refresh = page.locator('#reload').bounding_box()
    assert search['width'] >= 180
    assert search['x'] + search['width'] <= width
    assert refresh['x'] + refresh['width'] <= width
    # The centered Floor launcher must not cover navigation on narrow screens.
    for link in page.locator('.floor-rail-nav a').all():
        assert link.evaluate('(el) => {const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}')
    page.locator('#dataTree').get_by_text('sales', exact=True).click()
    page.get_by_text('orders', exact=True).wait_for()
    page.locator('#search').fill('orders')
    assert page.get_by_text('orders', exact=True).is_visible()
    assert page.get_by_text('customers', exact=True).count() == 0
    page.locator('#reload').click()
    assert not errors
