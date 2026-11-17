"""Real Meaning controller, diagram module and ELK: leveled maps (L1/L2/L3)."""
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import warehouse_theme

ROOT = Path(__file__).parent
AREA = 'subject:orders'
AREA_ID = 'area:' + AREA


def node(kind, key, title, **props):
    ident = f'{kind}:{key}'
    return dict(id=ident, kind=kind, title=title, status='provisional',
                reference={'kind': 'meaning', 'area': AREA, 'node': ident}, **props)


def edge(ident, a, b, label, state='declared', **evidence):
    return {'id': ident, 'from': a, 'to': b, 'label': label, 'state': state, 'evidence': evidence}


AREA_NODE = dict(node('area', AREA, 'Customer orders', area=AREA, subtitle='3 sources · 1 metric idea',
                      description='Orders, their lines and the stores that sell them.', questions=['Which stores sell most?'], gaps=[]),
                 id=AREA_ID, groups=[{'id': 'group:capture', 'title': 'Order capture', 'size': 2},
                                     {'id': 'group:stores', 'title': 'Store records', 'size': 1}])
NODES = [AREA_NODE,
         node('relation', 's.orders', 'Orders', source='s.orders', preview={'value': 2000, 'label': 'Profiled rows'},
              object_role='event', subject_role='core', grain='One row represents an order placed at a point in time.'),
         node('relation', 's.items', 'Order items', source='s.items', object_role='junction', grain='One row represents a product line within an order.'),
         node('relation', 's.stores', 'Stores', source='s.stores'),
         node('cube', 'cubes.daily', 'Daily orders', source='cubes.daily', grain='day'),
         node('metric', 'revenue', 'Order revenue', version=1, defaults={},
              preview={'value': 1234567, 'label': 'Saved daily snapshot'}),
         node('idea', 'perday', 'Orders per day'),
         node('artifact', 'desk', 'Order desk', slug='order-desk', subtitle='app · v2')]
EDGES = [*(edge('inc' + r, AREA_ID, 'relation:' + r, 'includes', 'published') for r in ('s.orders', 's.items', 's.stores')),
         edge('j1', 'relation:s.items', 'relation:s.orders', 'joins to', 'verified', from_column='order_id', to_column='order_id', basis='recognition'),
         edge('j2', 'relation:s.orders', 'relation:s.stores', 'joins to', 'verified', from_column='store_id', to_column='store_id', basis='naming'),
         edge('c0', AREA_ID, 'cube:cubes.daily', 'analyzed through'),
         edge('f1', 'relation:s.orders', 'cube:cubes.daily', 'feeds'),
         edge('m0', AREA_ID, 'metric:revenue', 'measured by'),
         edge('m1', 'cube:cubes.daily', 'metric:revenue', 'measured by'),
         edge('i0', AREA_ID, 'idea:perday', 'suggests', 'unverified'),
         edge('a0', AREA_ID, 'artifact:desk', 'explored in'),
         edge('a1', 'metric:revenue', 'artifact:desk', 'used by')]
AREA_GRAPH = {'area': AREA, 'nodes': NODES, 'edges': EDGES,
              'groups': [{'id': 'group:capture', 'title': 'Order capture', 'description': 'Orders and their lines',
                          'members': ['relation:s.orders', 'relation:s.items']},
                         {'id': 'group:stores', 'title': 'Store records', 'description': '', 'members': ['relation:s.stores']}],
              'neighbors': [{'id': 'area:subject:sales', 'kind': 'area', 'area': 'subject:sales', 'title': 'Sales',
                             'subtitle': '4 sources', 'reference': {'kind': 'meaning', 'area': 'subject:sales', 'node': 'area:subject:sales'},
                             'ties': [{'local': 'relation:s.stores', 'remote': 't.stores', 'label': 'joins on store_id',
                                       'state': 'verified', 'basis': 'recognition'}]}]}
SALES = {'id': 'area:subject:sales', 'kind': 'area', 'title': 'Sales', 'area': 'subject:sales', 'subtitle': '4 sources',
         'groups': [{'id': 'group:facts', 'title': 'Sales facts', 'size': 4}]}
HOME = {'area': '', 'nodes': [AREA_NODE, SALES], 'edges': [],
        'area_links': [edge('al', AREA_ID, 'area:subject:sales', 'joins on store_id', 'verified')]}


@pytest.fixture
def desk():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.set_default_timeout(8000)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            url = urlsplit(r.request.url)
            if url.netloc != 'meaning.test':
                r.abort(); return
            if url.path == '/meaning':
                html = (ROOT/'calliope/meaning.html').read_text().replace('__CALLIOPE_WORKSPACE_NAV__', warehouse_theme.workspace_navigation(active='meaning', can_administer=True))
                r.fulfill(body=re.sub(r'__[A-Z_]+__', '', html), content_type='text/html'); return
            if url.path.endswith('/graph'):
                area = parse_qs(url.query).get('area', [''])[0]
                r.fulfill(json=AREA_GRAPH if area == AREA else HOME if not area else {'area': area, 'nodes': [dict(SALES, reference={'kind': 'meaning', 'area': area, 'node': SALES['id']})], 'edges': []}); return
            if url.path.endswith('/inspect'):
                r.fulfill(json={'detail': {'observations': [], 'sql': 'SELECT 1', 'defaults': {}, 'fields': [
                    {'col_name': 'order_id', 'data_type': 'bigint', 'role': 'key', 'what': 'Identifies the order'}]}}); return
            if url.path.endswith('/data'):
                r.fulfill(json={'rows': [{'order_id': 7, 'status': 'COMPLETE'}], 'fields': [], 'catalog': None}); return
            if url.path.endswith('/pins'):
                r.fulfill(json={'pins': []}); return
            if url.path.startswith('/api/'):
                r.fulfill(json={}); return
            path = ROOT/url.path.lstrip('/')
            if url.path.startswith('/meaning/'):
                path = ROOT/'calliope'/url.path.rsplit('/', 1)[-1]
            if path.is_file() and path.resolve().is_relative_to(ROOT):
                r.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'text/plain'); return
            r.fulfill(status=404)
        page.route('**/*', route)
        yield page
        assert not errors
        browser.close()


def card(page, ident):
    return page.locator(f'[data-dg-node="{ident}"]')


def inside(inner, outer):
    a, b = inner.bounding_box(), outer.bounding_box()
    return a['x'] >= b['x'] - 1 and a['y'] >= b['y'] - 1 and a['x'] + a['width'] <= b['x'] + b['width'] + 1 and a['y'] + a['height'] <= b['y'] + b['height'] + 1


def test_area_opens_on_its_map_and_levels_are_shareable(desk):
    page = desk
    page.goto(f'http://meaning.test/meaning?area={AREA}')
    card(page, 'group:capture').wait_for()
    assert page.get_by_role('button', name='Level 1: Areas & groups').get_attribute('aria-pressed') == 'true'
    assert card(page, 'relation:s.orders').count() == 0
    assert card(page, 'area:subject:sales').is_visible(), 'neighbouring areas stay in view'
    page.get_by_role('button', name='Level 2: Sources & cubes').click()
    card(page, 'relation:s.orders').wait_for()
    assert 'level=2' in page.url
    page.wait_for_timeout(700)  # cards glide into their new boxes
    assert inside(card(page, 'relation:s.items'), page.locator('.dg-box[data-kind=group]').filter(has_text='Order capture'))
    page.wait_for_timeout(500)  # lines draw once the shapes settle
    assert page.locator('.dg-edge .dg-label', has_text='part of · order_id').count() == 1
    assert 'Each row: a product line within an order' in card(page, 'relation:s.items').inner_text()
    assert card(page, 'relation:s.items').get_attribute('data-role') == 'junction'
    page.reload()
    card(page, 'relation:s.orders').wait_for()
    page.keyboard.press('3')
    card(page, 'metric:revenue').wait_for()
    assert 'level=3' in page.url
    page.go_back()
    page.wait_for_function("!document.querySelector('[data-dg-node=\"metric:revenue\"]')")
    # Each map type remembers its last level for this viewer.
    page.goto(f'http://meaning.test/meaning?area={AREA}')
    card(page, 'relation:s.orders').wait_for()


def test_legend_inspector_and_object_map(desk):
    page = desk
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=3')
    card(page, 'cube:cubes.daily').wait_for()
    page.locator('.dg-legend').get_by_role('button', name=re.compile('Cubes')).click()
    page.wait_for_function("!document.querySelector('[data-dg-node=\"cube:cubes.daily\"]')")
    assert page.locator('.dg-edge[data-bridged] .dg-label', has_text='via Daily orders').count() == 1
    page.locator('.dg-legend').get_by_role('button', name=re.compile('Cubes')).click()
    card(page, 'cube:cubes.daily').wait_for()
    page.get_by_role('button', name='Cross-area links').click()
    assert page.locator('.dg-canvas.dg-crossing').count() == 1
    card(page, 'metric:revenue').click()
    inspector = page.locator('.dg-inspector')
    inspector.get_by_role('heading', name='Order revenue').wait_for()
    assert 'pick=metric%3Arevenue' in page.url and '1.2M' in inspector.inner_text()
    inspector.get_by_role('button', name='Daily orders').click()
    inspector.get_by_role('heading', name='Daily orders').wait_for()
    inspector.get_by_role('button', name='Open details').click()
    page.get_by_role('heading', name='Daily orders', exact=True).wait_for()
    page.locator('.desk-local').get_by_role('button', name='Map').click()
    card(page, 'cube:cubes.daily').wait_for()
    assert page.get_by_role('button', name='Level 1: Areas & groups').is_disabled()
    assert card(page, 'cube:cubes.daily').locator('.dg-viewing').count() == 1
    assert card(page, 'relation:s.stores').count() == 0, 'an object map is its neighbourhood'
    page.get_by_role('button', name='Level 3: Measures & work').click()
    card(page, 'artifact:desk').wait_for()


def test_search_prefers_objects_inside_the_area_and_reveals_deeper_levels(desk):
    page = desk
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=1')
    card(page, 'group:capture').wait_for()
    page.locator('#dg-search').fill('order')  # also in the area's own title
    page.keyboard.press('Enter')
    page.locator('.dg-inspector').get_by_role('heading', name='Order capture').wait_for()
    page.locator('#dg-search').fill('revenue')
    page.keyboard.press('Enter')
    page.locator('.dg-inspector').get_by_role('heading', name='Order revenue').wait_for()
    assert 'level=3' in page.url and 'pick=metric%3Arevenue' in page.url


def test_landscape_drills_into_a_group_of_an_area(desk):
    page = desk
    page.goto('http://meaning.test/meaning')
    page.locator('.desk-landscape .dg-box[data-kind=area]').first.wait_for()
    assert page.locator('.desk-landscape .dg-box[data-kind=area]').count() == 2
    assert page.get_by_role('button', name='Level 2: Sources & cubes').is_disabled()
    assert page.locator('.dg-edge .dg-label', has_text='joins on store_id').count() == 1
    card(page, 'group:capture').dblclick()
    page.locator('.dg-inspector').get_by_role('heading', name='Order capture').wait_for()
    assert 'level=2' in page.url and 'pick=group%3Acapture' in page.url
    card(page, 'relation:s.orders').wait_for()
    page.wait_for_timeout(900)
    # Drilling in frames the group (not the whole area) and keeps its sources lit.
    frame = page.locator('.dg-box[data-kind=group]').filter(has_text='Order capture').bounding_box()
    canvas = page.locator('.dg-canvas').bounding_box()
    assert frame['width'] > canvas['width'] * 0.3, (frame, canvas)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-dg-node=\"relation:s.items\"]')).opacity") == '1'
    page.get_by_role('button', name='Objects', exact=True).click()
    page.locator('.desk-object-page').wait_for()


@pytest.mark.parametrize('width', [390, 1280])
def test_map_fits_the_viewport(desk, width):
    page = desk
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=2&pick=relation:s.orders')
    page.locator('.dg-inspector').wait_for()
    boxes = page.evaluate('''() => {
      const r=s=>document.querySelector(s).getBoundingClientRect();
      return {overflow:document.documentElement.scrollWidth>innerWidth,canvas:r('.dg-canvas'),inspector:r('.dg-inspector'),h:innerHeight};
    }''')
    assert not boxes['overflow'], boxes
    assert boxes['canvas']['height'] >= 280, boxes
    assert boxes['inspector']['right'] <= width + 1 and boxes['inspector']['bottom'] <= boxes['h'] + 1, boxes


def test_inspector_shows_members_columns_rows_and_area_questions(desk):
    page = desk
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=1&pick=group:capture')
    inspector = page.locator('.dg-inspector')
    inspector.get_by_role('heading', name='Order capture').wait_for()
    assert 'Each row: a product line within an order' in inspector.inner_text()
    assert inspector.locator('.dg-chip[data-role]').first.inner_text().lower() in ('event', 'link table')
    assert 'part of' in inspector.inner_text(), 'joins inside the group read as relationships'
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=2&pick=relation:s.orders')
    inspector.get_by_role('heading', name='Orders').wait_for()
    inspector.locator('.dg-columns code', has_text='order_id').wait_for()
    inspector.locator('.dg-sample td', has_text='COMPLETE').wait_for()
    page.goto(f'http://meaning.test/meaning?area={AREA}&level=1&pick={AREA_ID}')
    inspector.get_by_role('heading', name='Customer orders').wait_for()
    text = inspector.inner_text()
    assert 'questions it can answer' in text.lower() and 'Which stores sell most?' in text
