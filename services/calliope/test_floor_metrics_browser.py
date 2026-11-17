"""Browser contracts for the real Floor/Gallery assets, with isolated API fixtures."""
import json
import mimetypes
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).parent


def metric_fixture(index):
    now = datetime.now(timezone.utc)
    return dict(name=f'core_{index:02}', title=f'Customer measure {index + 1}', category=['Core', ' core ', 'CORE'][index % 3],
                version=1, params={'region': 'North'} if index == 0 else {}, grain='month', description='A useful business measure with observed history.',
                display={'decimals': 0}, snapshot={'value': 210 + index, 'status': 'ok', 'ok': True, 'observed_at': now.isoformat()},
                trend={'direction': 'down', 'meaning': 'good', 'percent': 8},
                series=[{'value': 240 - i * 10 + index, 'observation_id': i + 1, 'observed_at': (now - timedelta(days=60-i*15)).isoformat()} for i in range(5)])


@pytest.fixture
def metrics_site():
    now = datetime.now(timezone.utc)
    card = dict(item_id='chat:one', item_kind='chat', display_title='A useful conversation', column_state='done',
                owner_email='viewer@example.com', agents=[], summary='An existing piece of work.', last_activity_at=now.isoformat())
    floor = dict(cards=[card], total=1, matched_total=1, lanes=[{'column_state': 'done', 'pieces': 1}],
                 people=[], titles=[], viewer={'email': 'viewer@example.com'}, starters=[], pages={}, facets={},
                 timeline_live={'total_items': 1, 'counts_by_column_state': {'done': 1}},
                 snapshots=[{'snapshot_at': (now-timedelta(days=i)).isoformat(), 'total_items': 1,
                             'counts_by_column_state': {'done': 1}, 'generation': 3-i} for i in [2, 1]])
    state = {'metrics': [metric_fixture(i) for i in range(12)], 'floor': floor, 'fail': False, 'fail_pieces': False, 'posts': [], 'queries': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, data, kind='application/json', status=200):
            if not isinstance(data, bytes): data = json.dumps(data).encode()
            self.send_response(status); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/floor':
                html = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/floor.html').read_text())
                self.send(html.encode(), 'text/html')
            elif url.path == '/gallery':
                import server, calliope
                with patch.object(calliope, 'is_enabled', return_value=False):
                    html = server._landing_html([], 'viewer@example.com')
                self.send(html.encode(), 'text/html')
            elif url.path == '/calliope': self.send(b'<h1>Metric notebook</h1>', 'text/html')
            elif url.path in ['/api/calliope/floor', '/api/calliope/floor/pieces']:
                self.send({'error': {'message': 'Live view unavailable'}} if state['fail_pieces'] else state['floor'], status=503 if state['fail_pieces'] else 200)
            elif url.path == '/api/gallery/metrics':
                state['queries'].append(parse_qs(url.query))
                time.sleep(state.get('metric_delay', 0))
                self.send({'error': {'message': 'Temporarily unavailable'}} if state['fail'] else
                          {'metrics': state['metrics'], 'categories': [{'category': 'Core', 'count': len(state['metrics'])}], 'range_days': 90},
                          status=503 if state['fail'] else 200)
            elif url.path.startswith('/api/gallery/metrics/'):
                metric = next(m for m in state['metrics'] if m['name'] == url.path.rsplit('/', 1)[-1])
                self.send({'metric': metric})
            elif url.path.startswith('/api/'): self.send(state.get('responses', {}).get(url.path, {}))
            else:
                parts = url.path.strip('/').split('/', 1)
                folder = 'calliope' if parts[0] in ['floor', 'calliope'] else parts[0]
                path = ROOT/folder/(parts[1] if len(parts) > 1 else '')
                if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file(): self.send_error(404); return
                self.send(path.read_bytes(), mimetypes.guess_type(path)[0] or 'application/octet-stream')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or '{}')
            state['posts'].append((self.path, body))
            if '/gallery/metrics/' in self.path:
                self.send({'session': {'id': 'metric-session'}, 'surface': {'id': 'metric-stage'},
                           'url': '/calliope?session=metric-session&surface=metric-stage&prompt=Explore+this+metric'})
            else: self.send({})

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{http.server_port}', state
    finally: http.shutdown(); thread.join()


@pytest.fixture
def browser_page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000}, reduced_motion='reduce')
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('https://**', lambda route: route.abort())
        yield page
        browser.close()
        assert not errors


def test_core_rail_resizes_pages_and_hands_off_the_selected_metric(metrics_site, browser_page):
    url, state = metrics_site; page = browser_page
    page.goto(url+'/floor')
    cards = page.locator('#floorMetricsTrack .metric-gallery-card')
    page.wait_for_function("document.querySelectorAll('#floorMetricsTrack .metric-gallery-card').length === 12")
    assert page.locator('#floorMetricsPanel').is_visible()
    assert not page.locator('#timebar').is_visible()
    assert state['queries'][0]['category'] == ['Core']
    assert state['queries'][0]['floor_period'] == ['week']
    assert 'days' not in state['queries'][0]
    assert 'ticks' not in state['queries'][0]
    assert cards.locator('svg').count() == 12
    assert cards.locator('.metric-card-delta.good').count() == 12
    assert page.locator('#floorOverview').bounding_box()['y'] < page.locator('.floor-toolbar').bounding_box()['y'] < page.locator('#workspace').bounding_box()['y'] < page.locator('.floor-footer').bounding_box()['y']
    rail = page.locator('.floor-toolbar')
    assert rail.evaluate("node => getComputedStyle(node).backgroundColor") == 'rgba(0, 0, 0, 0)'
    assert rail.locator('#search').bounding_box()['x'] < rail.locator('.controls').bounding_box()['x']
    assert rail.locator('#search').bounding_box()['height'] == rail.locator('#people').bounding_box()['height']
    assert rail.locator('#summaryLine, #status, #reload').count() == 0
    assert page.locator('.floor-footer #summaryLine, .floor-footer #status, .floor-footer #reload').count() == 3
    wide = cards.first.bounding_box()['width']
    page.get_by_role('button', name='Next metrics', exact=True).click()
    page.wait_for_function("document.querySelector('#floorMetricsTrack').scrollLeft > 100")
    page.wait_for_function("!document.querySelector('#floorMetricsPrev').disabled")
    page.set_viewport_size({'width': 980, 'height': 900})
    page.wait_for_function("document.querySelector('#floorMetricsTrack').clientWidth < 1000")
    assert cards.first.bounding_box()['width'] >= 260
    page.wait_for_function("width => document.querySelector('.metric-gallery-card').getBoundingClientRect().width !== width", arg=wide)
    page.get_by_role('button', name='Previous metrics', exact=True).click()
    cards.first.get_by_role('button').click()
    page.wait_for_url('**/calliope?**')
    assert 'session=metric-session' in page.url and 'surface=metric-stage' in page.url
    assert state['posts'][-1] == ('/api/calliope/gallery/metrics/core_00/ask', {'params': {'region': 'North'}, 'origin': 'floor'})


def test_tick_charts_span_card_for_same_business_date_and_irregular_ticks(metrics_site, browser_page):
    url, state = metrics_site
    metric = state['metrics'][0]
    state['metrics'] = [metric]
    metric['series_scope'] = 'ticks'
    metric['trend']['basis'] = 'First to latest of 3 recent readings'
    metric['series'] = [
        {'value': value, 'data_as_of': '2025-01-01T00:00:00Z', 'observed_at': observed}
        for value, observed in [(100, '2025-01-01T00:00:00Z'),
                                (80, '2025-02-01T00:00:00Z'),
                                (120, '2025-02-01T00:01:00Z')]
    ]
    metric['snapshot']['data_as_of'] = '2025-01-01T00:00:00Z'
    page = browser_page
    page.add_init_script("localStorage.setItem('calliope.floor.metric-period.v1:viewer@example.com','current')")
    page.goto(url+'/floor')
    chart = page.locator('.metric-card-chart')
    chart.wait_for(state='visible')
    path = chart.locator('.metric-card-line').get_attribute('d')
    assert path.startswith('M 0.00 ')
    assert len(re.findall(r' C ', path)) == 2
    endpoints = re.findall(r'C [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ ([-\d.]+) ([-\d.]+)', path)
    assert [float(point[0]) for point in endpoints] == [300, 600]
    assert chart.get_attribute('aria-description') == 'Latest 3 readings · evenly spaced'
    assert 'First to latest of 3 recent readings' in page.locator('.metric-card-delta').get_attribute('aria-label')
    assert page.locator('.metric-card-foot').inner_text().lower().endswith('just now')
    assert page.locator('#floorOverviewHint').inner_text() == 'Latest 24 readings · headline period shown on each tile'


def test_metric_period_cycle_keeps_cards_and_remembers_success_only(metrics_site, browser_page):
    url, state = metrics_site; page = browser_page
    now = datetime.now(timezone.utc)
    for metric in state['metrics']:
        metric['series_scope'] = 'observed_window'
        metric['series'] = [{'value': value, 'data_as_of': '2025-01-01',
                            'observed_at': (now-timedelta(days=days)).isoformat()}
                           for days, value in [(6,240),(5,150),(3,195),(1,180),(.1,210)]]
    page.goto(url+'/floor')
    button = page.locator('#floorMetricPeriod')
    button.wait_for(state='visible')
    page.wait_for_function("!document.querySelector('#floorMetricPeriod').disabled")
    assert button.inner_text() == '↻ Weekly'
    line = page.locator('.metric-card-line').first
    assert line.get_attribute('d').count(' C ') == 4
    bounds = line.evaluate('''line => Array.from({length:201},(_,i)=>line.getPointAtLength(line.getTotalLength()*i/200).y)''')
    assert min(bounds) >= 11.99 and max(bounds) <= 148.01
    page.screenshot(path='/tmp/floor-metrics-weekly.png')
    before = page.locator('#floorMetricsTrack').inner_html()
    state['metric_delay'] = .3
    button.click()
    assert page.locator('#floorMetricsTrack').inner_html() == before
    assert 'Monthly' in button.inner_text() and button.is_disabled()
    page.wait_for_function("!document.querySelector('#floorMetricPeriod').disabled")
    assert button.inner_text() == '↻ Monthly'
    assert state['queries'][-1]['floor_period'] == ['month']
    page.reload();button.wait_for(state='visible')
    page.wait_for_function("!document.querySelector('#floorMetricPeriod').disabled")
    assert button.inner_text() == '↻ Monthly'
    for label, key, value in [('Quarterly','floor_period','quarter'),('Readings','ticks','24'),('Daily','floor_period','day'),('Weekly','floor_period','week')]:
        button.click();page.wait_for_function("!document.querySelector('#floorMetricPeriod').disabled")
        assert button.inner_text() == '↻ '+label
        assert state['queries'][-1][key] == [value]
    state['fail'] = True
    before = page.locator('#floorMetricsTrack').inner_html()
    button.click();page.wait_for_function("!document.querySelector('#floorMetricPeriod').disabled")
    assert button.inner_text() == '↻ Weekly'
    assert page.locator('#floorMetricsTrack').inner_html() == before
    assert 'Still showing Weekly' in page.locator('#floorMetricStatus').inner_text()
    page.get_by_role('button', name='History', exact=True).click()
    assert button.is_hidden()
    page.screenshot(path='/tmp/floor-metric-period-history.png')


def test_core_rail_orders_by_absolute_percentage_drift(metrics_site, browser_page):
    url, state = metrics_site; page = browser_page
    state['metrics'] = state['metrics'][:7]
    for metric, percent in zip(state['metrics'], [0, None, 12, -30, 30, 'invalid', -5]):
        metric['trend']['percent'] = percent
    # Raw magnitudes have different units and must not determine the order.
    state['metrics'][2]['trend']['absolute'] = 1000000
    page.goto(url+'/floor')
    page.locator('#floorMetricsPanel').wait_for(state='visible')
    names = page.locator('#floorMetricsTrack .metric-gallery-card').evaluate_all('(cards) => cards.map(card => card.dataset.metricCard)')
    assert names == ['core_03', 'core_04', 'core_02', 'core_06', 'core_00', 'core_01', 'core_05']


def test_floor_headline_period_and_snapshot_axis_are_explicit_without_changing_gallery(metrics_site, browser_page):
    url,state=metrics_site;page=browser_page
    now=datetime.now(timezone.utc)
    metric=metric_fixture(0)
    metric.update(title='New Enrolled Persons',subcategory='Enrollment flow',series_scope='period_snapshots',series_period='week',
                  measurement={'kind':'observation_day','label':'Today','time_zone':'America/Chicago'},
                  snapshot={'value':9,'observed_at':now.isoformat()},
                  trend={'direction':'up','percent':None,'absolute':9,'basis':'First to latest weekly snapshot'},
                  series=[{'value':value,'observed_at':(now-timedelta(weeks=weeks)).isoformat(),
                           'period_start':(now-timedelta(weeks=weeks)).isoformat()} for weeks,value in [(2,0),(1,4),(0,9)]])
    state['metrics']=[metric]
    page.goto(url+'/floor');page.locator('.metric-card-measurement').wait_for()
    assert page.locator('.metric-card-measurement').inner_text()=='Today · Chicago'
    assert '9 since' in page.locator('.metric-card-delta').inner_text()
    assert 'Updated' in page.locator('.metric-card-foot').inner_text()
    assert 'Weekly snapshots' in page.locator('#floorOverviewHint').inner_text()
    assert page.locator('.metric-card-endpoint').count()==1
    assert page.locator('.metric-card-axis').bounding_box()['y']+page.locator('.metric-card-axis').bounding_box()['height'] <= page.locator('.metric-card-title').bounding_box()['y']
    raw=page.evaluate('(m)=>window.CalliopeMetricCards.cardMarkup(m)',metric)
    assert 'metric-card-measurement' not in raw and 'metric-card-axis' not in raw
    page.screenshot(path='/tmp/floor-metric-period-labels.png')
    # Stale daily snapshots must never be relabeled as today's business count.
    metric['snapshot']['observed_at']=(now-timedelta(days=2)).isoformat()
    html=page.evaluate('(m)=>window.CalliopeMetricCards.cardMarkup(m,{floor:true})',metric)
    assert 'Today · Chicago' not in html and 'Chicago' in html


def test_shared_card_formatting_respects_metadata_never_metric_names(metrics_site, browser_page):
    url, _ = metrics_site; page = browser_page
    page.goto(url+'/floor'); page.locator('#floorMetricsPanel').wait_for(state='visible')
    actual = page.evaluate('''() => {
      const f = window.CalliopeMetricCards.formatValue;
      return [
        f(3, {format:'integer'}, 'salesforce_submitted_leads'),
        f(3, {format:'integer'}, 'paid_submitted_lead_events'),
        f(3.4, {format:'integer', decimals:2, currency:'USD'}, 'salesforce_submitted_leads'),
        f(3, {}, 'salesforce_submitted_leads'),
        f(3, {}, 'revenue'),
        f(3, {}, 'arrival_rate'),
        f(0.5, {format:'ratio'}, 'conversion_rate'),
        f(3, {format:'currency', currency:'USD'}, 'submitted_leads'),
        f(3, {currency:'USD'}, 'submitted_leads'),
        f(0.5, {format:'percent'}, 'submitted_leads'),
        f(0.5, {unit:'%'}, 'submitted_leads'),
      ];
    }''')
    assert actual == ['3', '3', '3', '3', '3', '3', '0.5', '$3.00', '$3.00', '50%', '50%']


def test_history_preference_and_return_to_live(metrics_site, browser_page):
    url, _ = metrics_site; page = browser_page
    page.goto(url+'/floor'); page.locator('#floorHistoryBtn').wait_for(state='visible')
    page.get_by_role('button', name='History', exact=True).click()
    page.locator('#timeViz').focus(); page.keyboard.press('Home')
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Past')")
    page.get_by_role('button', name='Metrics', exact=True).click()
    page.wait_for_function("!document.querySelector('#floorMetricsPanel').hidden && !document.body.classList.contains('history-mode')")
    assert page.locator('#status').inner_text().startswith('Live')
    page.get_by_role('button', name='History', exact=True).click()
    page.reload(); page.locator('#floorHistoryBtn').wait_for(state='visible')
    assert page.locator('#timebar').is_visible() and page.locator('#floorMetricsPanel').is_hidden()


@pytest.mark.parametrize('failure', [False, True])
def test_without_core_or_on_fetch_failure_history_remains_available(metrics_site, browser_page, failure):
    url, state = metrics_site; state['metrics'] = []; state['fail'] = failure
    page = browser_page; page.goto(url+'/floor')
    page.locator('#timebar').wait_for(state='visible')
    assert page.locator('#floorOverviewSwitch').is_hidden()
    assert page.locator('#floorMetricsPanel').is_hidden()
    assert page.locator('.piece').count() == 1


def test_gallery_uses_shared_cards_and_still_opens_metric_lens(metrics_site, browser_page):
    url, state = metrics_site; page = browser_page
    metric = state['metrics'][0]
    metric.update(name='salesforce_submitted_leads', title='Salesforce Submitted Leads', display={'format':'integer'})
    metric['snapshot']['value'] = 3
    page.goto(url+'/gallery?view=metrics')
    card = page.locator('[data-metric-card="salesforce_submitted_leads"]')
    assert card.locator('.metric-card-value strong').inner_text() == '3'
    card.locator('[data-metric-open]').click()
    page.wait_for_function("document.querySelector('#metric-lens').open && document.querySelector('#metric-lens-title').textContent === 'Salesforce Submitted Leads'")
    assert page.locator('.metric-lens-chart svg').is_visible()
    assert page.locator('.metric-lens-hero-copy strong').inner_text() == '3'
    assert '$' not in page.locator('.metric-lens-chart').inner_text()


def test_failed_live_refresh_keeps_history_visible(metrics_site, browser_page):
    url, state = metrics_site; page = browser_page
    page.goto(url+'/floor'); page.locator('#floorHistoryBtn').wait_for(state='visible')
    page.get_by_role('button', name='History', exact=True).click()
    page.locator('#timeViz').focus(); page.keyboard.press('Home')
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Past')")
    state['fail_pieces'] = True
    page.get_by_role('button', name='Metrics', exact=True).click()
    page.wait_for_function("document.querySelector('#floorMetricStatus').textContent.includes('Could not return')")
    assert page.locator('#timebar').is_visible()
    assert page.locator('#floorMetricsPanel').is_hidden()
    assert 'history-mode' in page.locator('body').get_attribute('class')


def test_narrow_window_fits_complete_metrics(metrics_site, browser_page):
    url, _ = metrics_site; page = browser_page
    page.set_viewport_size({'width': 600, 'height': 900})
    page.goto(url+'/floor'); page.locator('#floorMetricsPanel').wait_for(state='visible')
    page.wait_for_function("document.querySelector('#floorMetricsTrack').style.getPropertyValue('--metric-card-width') !== ''")
    box = page.locator('#floorMetricsTrack').bounding_box()
    assert box['x'] + box['width'] <= 600
    assert page.locator('#floorMetricsTrack .metric-gallery-card').first.bounding_box()['width'] >= 260
    for control in ['#search', '#people', '#kinds', '#freshness', '#meToggle', '#listBtn']:
        control_box = page.locator(control).bounding_box()
        assert control_box['x'] >= 0 and control_box['x'] + control_box['width'] <= 600
