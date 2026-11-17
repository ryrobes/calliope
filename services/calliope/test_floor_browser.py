"""Optional real Chromium QA: FLOOR_TEST_DSN plus playwright and Chromium."""
import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

import floor_data
from test_floor_data import graph  # shared isolated PostgreSQL fixture

ROOT = Path(__file__).parent


@pytest.fixture
def floor_site(graph):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            params = dict(parse_qsl(url.query))
            content_type = 'application/json'
            if url.path == '/floor':
                data = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/floor.html').read_text()).encode()
                content_type = 'text/html'
            elif url.path in ('/api/calliope/floor', '/api/calliope/floor/pieces'):
                if url.path == '/api/calliope/floor':
                    params['thumbs'] = '1'  # Match the initial production projection/cursor scope.
                result = floor_data.read_page(*graph, 'viewer@example.com', floor_data.request_options(params))
                if url.path == '/api/calliope/floor':
                    result.update(people=[], titles=[], timeline_live={'total_items':3600,'counts_by_column_state':{'awaiting_you':60,'in_motion':15,'standing':20,'done':3505}}, viewer={'email':'viewer@example.com'}, starters=[], snapshots=[
                        {'snapshot_at':'2021-01-01T00:00:00Z','total_items':366,'counts_by_column_state':{'done':366},'generation':1},
                        {'snapshot_at':'2022-01-01T00:00:00Z','total_items':731,'counts_by_column_state':{'done':731},'generation':2}])
                data = json.dumps(result).encode()
            elif url.path.startswith('/api/'):
                data = b'{}'
            else:
                parts = url.path.strip('/').split('/',1)
                folder = 'calliope' if parts[0] in ('floor','calliope') else parts[0]
                path = ROOT/folder/(parts[1] if len(parts)>1 else '')
                if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
                    self.send_error(404);return
                data = path.read_bytes();content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',content_type);self.end_headers();self.wfile.write(data)

        def do_POST(self):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{}')

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown();thread.join()


def test_grid_notices_toggle_defaults_hidden_and_restores_board_scope(floor_site, graph):
    factory, projection = graph
    table = projection.split('FROM ')[1].split()[0]
    with factory() as conn:
        conn.execute(f"DELETE FROM {table} WHERE item_id NOT IN (SELECT 'piece:'||g FROM generate_series(1,8) g)")
        conn.execute(f"UPDATE {table} SET item_kind='dream',column_state='noticed' WHERE item_id='piece:1'")
        conn.execute(f"UPDATE {table} SET item_kind='dream',column_state='awaiting_you' WHERE item_id='piece:2'")
        conn.execute(f"UPDATE {table} SET item_kind='dream' WHERE item_id='piece:3'")
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 900}, reduced_motion='reduce')
        page.route('https://**', lambda route: route.abort())
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        requests = []; page.on('request', lambda request: requests.append(request.url))
        page.goto(floor_site+'/floor')
        page.locator('.piece').first.wait_for()
        page.locator('#floorInitialVeil').wait_for(state='detached')
        toggle = page.get_by_role('button', name='Hide notices', exact=True)
        assert page.locator('#noticesToggle').is_hidden()
        page.locator('#meToggle').click()
        page.wait_for_function("document.querySelectorAll('.grid-tile').length===7")
        assert toggle.is_visible() and toggle.get_attribute('aria-pressed') == 'true'
        for width in (1400, 600):
            page.set_viewport_size({'width': width, 'height': 900})
            assert 0 <= toggle.bounding_box()['x'] < page.locator('#meToggle').bounding_box()['x']
            assert page.locator('#meToggle').bounding_box()['x'] + page.locator('#meToggle').bounding_box()['width'] <= width
            page.screenshot(path=f'/tmp/calliope-floor-notices-{width}.png')
        page.set_viewport_size({'width': 1400, 'height': 900})
        assert page.locator('.grid-tile[data-id="piece:1"]').count() == 0
        assert page.locator('.grid-tile[data-id="piece:2"]').count() == 1
        assert page.locator('.grid-tile[data-id="piece:3"]').count() == 1
        assert 'hide_notices=1' in next(url for url in reversed(requests) if '/floor/pieces?' in url)
        toggle.click()
        page.wait_for_function("document.querySelectorAll('.grid-tile').length===8")
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert page.locator('.grid-tile[data-id="piece:1"]').count() == 1
        toggle.click()
        page.wait_for_function("document.querySelectorAll('.grid-tile').length===7")
        page.locator('#boardBtn').click()
        page.locator('#board').wait_for()
        assert page.locator('#noticesToggle').is_hidden()
        assert 'hide_notices' not in next(url for url in reversed(requests) if '/floor/pieces?' in url)
        assert page.locator('[data-expand="noticed"] .rail-count').inner_text() == '1'
        assert not errors
        browser.close()


@pytest.mark.parametrize('gesture', ['button', 'drag', 'join'])
def test_dream_background_start_replaces_idea_with_regular_thread(floor_site, graph, gesture):
    factory, projection = graph
    table = projection.split('FROM ')[1].split()[0]
    dream_id = 'cc63777b-a18f-44cd-8b51-7921e8603b10'
    session_id = '032df085-f4f1-4655-ae2c-042a2ec7b718'
    with factory() as conn:
        conn.execute(f"DELETE FROM {table} WHERE item_id<>'piece:1'")
        conn.execute(f'ALTER TABLE {table} ADD COLUMN session_id uuid, ADD COLUMN open_url text, ADD COLUMN dream_origin jsonb')
        conn.execute(f"UPDATE {table} SET item_id=%s,item_kind='curated_dream',column_state='noticed',display_title='Explore retention',active_now=false", ('curated_dream:'+dream_id,))
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1400,'height':900}, reduced_motion='reduce')
        page.route('https://**', lambda route: route.abort())
        errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
        calls=[]
        def route_dream(route):
            if route.request.method == 'GET':
                route.fulfill(json={'dream':{'title':'Explore retention','output':{'summary':'Saved evidence'}}})
                return
            calls.append(route.request.post_data_json)
            with factory() as conn:
                conn.execute(f"UPDATE {table} SET item_id=%s,item_kind='chat',column_state='in_motion',active_now=true,session_id=%s,open_url=%s,dream_origin=%s::jsonb", ('chat:'+session_id, session_id, '/calliope?session='+session_id, json.dumps({'dream_id':dream_id})))
            route.fulfill(status=201,json={'queued':True,'session':{'id':session_id},'url':'/calliope?session='+session_id})
        page.route('**/api/calliope/dreams/**', route_dream)
        page.goto(floor_site+'/floor')
        page.locator('#floorInitialVeil').wait_for(state='detached')
        if page.locator('[data-expand="noticed"]').count(): page.locator('[data-expand="noticed"]').click()
        tile = page.locator('.piece[data-id="curated_dream:'+dream_id+'"]')
        if gesture in ('button','join'):
            tile.click()
            page.locator('#dreamDirection').fill('Focus on September; investigate only.')
            if gesture=='join':
                page.route('**/calliope?session=*',lambda route:route.fulfill(content_type='text/html',body='Thread opened'))
                page.get_by_role('button',name='Explore with Callie →',exact=True).click()
                page.wait_for_url('**/calliope?session='+session_id)
                assert calls == [{'origin':'floor','direction':'Focus on September; investigate only.'}]
                assert not errors
                browser.close()
                return
            page.get_by_role('button',name='Have Callie start without me',exact=True).click()
        else:
            tile.drag_to(page.locator('.lane[data-lane="in_motion"]'))
        page.locator('.lane[data-lane="in_motion"] .piece').wait_for()
        assert calls == [{'origin':'floor','background':True,'direction':'Focus on September; investigate only.' if gesture=='button' else ''}]
        assert page.locator('.piece').count() == 1
        assert page.url == floor_site+'/floor'
        assert tile.count() == 0
        thread=page.locator('.piece[data-id="chat:'+session_id+'"]')
        assert thread.get_by_text('From a Dream',exact=True).count()==1
        thread.click()
        page.get_by_role('button',name='Open piece',exact=True).wait_for()
        assert page.get_by_role('button',name='Have Callie start without me',exact=True).count() == 0
        assert not errors
        browser.close()


def test_floor_continuous_scroll_search_and_history(floor_site):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1696,'height':1200},color_scheme='dark')
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('https://fonts.googleapis.com/**',lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**',lambda route:route.abort())
        page.goto(floor_site+'/floor')
        page.wait_for_function("document.querySelector('#summaryLine').textContent.includes('2,599')")
        done=page.locator('.lane[data-lane="done"]')
        assert done.locator('.count').inner_text()=='2500'
        page.screenshot(path='/tmp/floor-continuous-columns.png',full_page=True)
        assert page.locator('.piece').count()<50
        assert done.locator('[data-window-more]').is_hidden()
        assert done.locator('[data-fit]').count()==0
        body=done.locator('.lane-body')
        for _ in range(4):
            body.evaluate('(el)=>{el.scrollTop=el.scrollHeight}')
            page.wait_for_timeout(150)
        assert body.evaluate('(el)=>el.scrollTop')>0
        assert done.locator('.piece').count()<35
        assert int(done.locator('.piece').last.get_attribute('data-id').split(':')[1])<2471
        scroll_before=body.evaluate('(el)=>el.scrollTop')
        # Re-rendering another lane's controls must retain the scrolled lane.
        page.locator('[data-lane-find="awaiting_you"]').click()
        assert abs(body.evaluate('(el)=>el.scrollTop')-scroll_before)<5
        page.locator('#search').fill('Ancient needle')
        page.wait_for_function("document.querySelector('.lane[data-lane=done] .count').textContent==='1'")
        assert done.locator('.piece').count()==1
        assert 'Ancient needle' in done.inner_text()
        page.locator('#search').fill('')
        page.wait_for_function("document.querySelector('.lane[data-lane=done] .count').textContent==='2500'")
        done.locator('[data-lane-find]').click()
        lane_search=done.locator('[data-lane-query]')
        lane_search.fill('Ancient needle')
        page.wait_for_function("document.querySelector('.lane[data-lane=done] .count').textContent==='1/2500'")
        assert lane_search.evaluate('(el)=>document.activeElement===el')
        # An exact historical frame uses the same server-side search and counts.
        page.locator('#timeViz').press('Home')
        page.wait_for_function("document.querySelector('#status').textContent.startsWith('Past') && document.querySelector('#status').textContent.includes('matching')")
        assert '2,599' not in page.locator('#summaryLine').inner_text()
        assert 'Ancient needle' in done.inner_text()
        page.locator('#liveBtn').click()
        page.wait_for_function("document.querySelector('#summaryLine').textContent.includes('2,599')")
        page.screenshot(path='/tmp/floor-stream-browser.png',full_page=True)
        done.locator('[data-lane-clear]').click()
        page.wait_for_function("document.querySelector('.lane[data-lane=done] .count').textContent==='2500'")
        page.locator('#listBtn').click()
        listing=page.locator('.floor-stream-list')
        assert 'People' in listing.locator('[data-window-header]').text_content()
        for _ in range(4):
            listing.evaluate('(el)=>{el.scrollTop=el.scrollHeight}')
            page.wait_for_timeout(150)
        assert listing.evaluate('(el)=>el.scrollTop')>0
        assert listing.locator('[data-open]').count()<100
        page.locator('#boardBtn').click()
        page.wait_for_function("document.querySelector('.lane[data-lane=done] .count').textContent==='2500'")
        assert not errors, errors
        browser.close()


def test_floor_renders_before_labels_and_updates_without_scroll_reset(floor_site):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1696,'height':1200},color_scheme='dark')
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('https://fonts.googleapis.com/**',lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**',lambda route:route.abort())
        held=[]
        page.route('**/api/calliope/floor/presentations',lambda route:held.append(route))
        page.goto(floor_site+'/floor')
        page.wait_for_function("document.querySelector('#summaryLine').textContent.includes('2,599')")
        done=page.locator('.lane[data-lane="done"]')
        assert done.locator('[data-window-more]').is_hidden()
        assert done.locator('[data-fit]').count()==0
        done.locator('.lane-body').evaluate('el=>{el.scrollTop=400}')
        page.wait_for_timeout(100)
        card=done.locator('.piece').first
        item_id=card.get_attribute('data-id')
        assert item_id and held
        before=done.evaluate("el=>({scroll:el.querySelector('.lane-body').scrollTop,search:document.querySelector('#search').value})")
        assert before['scroll']>0
        held.pop(0).fulfill(status=200,content_type='application/json',body=json.dumps({
            'titles':[{'item_id':item_id,'semantic_title':'New enriched project label','semantic_status':'Ready to review'}],
            'pending':False}))
        page.wait_for_function("document.querySelector('.lane[data-lane=done]').textContent.includes('New enriched project label')")
        after=done.evaluate("el=>({scroll:el.querySelector('.lane-body').scrollTop,search:document.querySelector('#search').value})")
        assert before==after
        assert not errors
        browser.close()



def test_scrubber_keeps_dom_until_frame_arrives_then_animates(floor_site):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1696,'height':1200},reduced_motion='no-preference')
        page.route('https://fonts.googleapis.com/**',lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**',lambda route:route.abort())
        page.add_init_script("""window.floorMoves=[];const original=Element.prototype.animate;
            Element.prototype.animate=function(frames,options){
                if(this.dataset.id&&frames[0]?.transform?.startsWith('translate('))window.floorMoves.push(this.dataset.id);
                return original.call(this,frames,options);
            };""")
        page.goto(floor_site+'/floor')
        page.wait_for_function("document.querySelector('#summaryLine').textContent.includes('2,599')")
        assert '60 wait' in page.locator('#timeComposition').inner_text()
        assert '3,600 across system' in page.locator('#timeMeta').inner_text()
        held=[]
        page.route('**/api/calliope/floor/pieces?**',lambda route:held.append(route))
        page.evaluate("window.oldFloorCard=document.querySelector('.lane[data-lane=done] .piece')")
        item_id=page.evaluate('window.oldFloorCard.dataset.id')
        page.locator('#timeViz').press('Home')
        page.wait_for_function("document.querySelector('#workspace').getAttribute('aria-busy')==='true'")
        for _ in range(40):
            if held: break
            page.wait_for_timeout(25)
        assert held and page.evaluate('window.oldFloorCard.isConnected')
        assert page.locator('.piece').count()>0
        payload=page.request.get(floor_site+'/api/calliope/floor/pieces').json()
        card=next(c for c in payload['cards'] if c['item_id']==item_id)
        card['column_state']='standing'
        next(l for l in payload['lanes'] if l['column_state']=='done')['pieces']-=1
        payload['lanes'].append({'column_state':'standing','pieces':1,'matched':1})
        held.pop(0).fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.wait_for_function("document.querySelector('#workspace').getAttribute('aria-busy')==='false'")
        page.wait_for_function('(id)=>window.floorMoves.includes(id)',arg=item_id)
        assert page.locator(f'.lane[data-lane=standing] .piece[data-id="{item_id}"]').count()==1
        # A later failed lookup leaves the last successful cards visible.
        page.locator('#liveBtn').click()
        page.wait_for_function("document.querySelector('#workspace').getAttribute('aria-busy')==='true'")
        for _ in range(40):
            if held: break
            page.wait_for_timeout(25)
        held.pop(0).fulfill(status=502,content_type='application/json',body='{"error":{"message":"test unavailable"}}')
        page.wait_for_function("document.querySelector('#workspace').classList.contains('view-stale')")
        assert page.locator(f'.piece[data-id="{item_id}"]').count()==1
        browser.close()


def test_living_page_chips_filter_and_grid(floor_site, graph):
    from playwright.sync_api import sync_playwright
    factory, sql=graph
    table=sql.split('FROM ')[1].split()[0]
    with factory() as c:
        c.execute(f"ALTER TABLE {table} ADD COLUMN session_id text")
        c.execute(f"UPDATE {table} SET session_id='70000000-0000-4000-8000-000000000001',item_kind='page',display_title='Project Lantern',column_state='standing' WHERE item_id='piece:2500'")
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1560,'height':1000})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(floor_site+'/floor')
        page.locator('.piece.is-page').wait_for()
        page.locator('#floorInitialVeil').wait_for(state='detached')
        assert page.locator('.piece.is-page .page-chip').inner_text().casefold()=='page'
        page.locator('#floorPages').click()
        page.wait_for_function("document.querySelector('#kinds').value==='page' && document.querySelectorAll('.piece[data-id]').length===1")
        page.screenshot(path='/tmp/calliope-floor-pages.png')
        page.locator('#meToggle').click()
        page.locator('.grid-tile.is-page').wait_for()
        assert 'page' in page.locator('.grid-tile.is-page .grid-kicker').inner_text().casefold()
        page.locator('#floorNewPage').click()
        assert 'living Page' in page.locator('#floorIntakeInput').input_value()
        if page.locator('#floorIntakeEditor').is_visible():
            assert 'living Page' in page.locator('#floorIntakeEditor').inner_text()
        page.locator('#floorIntake').click(position={'x':4,'y':4})
        page.locator('#boardBtn').click()
        saved=page.get_by_role('button',name='Open Page · Project Lantern')
        assert 'Refresh on demand' in saved.inner_text()
        assert 'Always watching' not in saved.inner_text()
        page.route('**/calliope?**',lambda r:r.fulfill(body='Notebook',content_type='text/html'))
        saved.click()
        page.wait_for_url('**/calliope?session=70000000-0000-4000-8000-000000000001&view=zen')
        assert not errors
        browser.close()


def test_curated_dream_drawer_uses_saved_text_feedback_and_pinned_handoff(floor_site, graph):
    factory, projection = graph
    table = projection.split('FROM ')[1].split()[0]
    dream_id='cc63777b-a18f-44cd-8b51-7921e8603b10'
    metadata={'portfolio_state':'backlog','status':'proposed','impact':'high','confidence':.78,'effort':'small','novelty':.61,'rank_score':.83}
    with factory() as conn:
        conn.execute(f"DELETE FROM {table} WHERE item_id<>'piece:1'")
        conn.execute(f'ALTER TABLE {table} ADD COLUMN dream jsonb')
        conn.execute(f'UPDATE {table} SET dream=%s::jsonb',(json.dumps(metadata),))
        conn.execute(f"UPDATE {table} SET item_id=%s,item_kind='curated_dream',column_state='noticed',display_title='Find overlooked retention opportunities',summary='Compare drop-off patterns across cohorts',active_now=false",('curated_dream:'+dream_id,))
    playwright=pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1400,'height':900},reduced_motion='reduce')
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('https://**',lambda route:route.abort())
        calls=[]
        def dream_route(route):
            req=route.request;calls.append((req.method,req.url,req.post_data_json if req.method in ('POST','PATCH') else None))
            if req.method=='GET':route.fulfill(json={'dream':{**metadata,'title':'Find overlooked retention opportunities','output':{'summary':'Saved analysis, no new inference','sections':[{'title':'A useful comparison','content':'Inspect <script>literal evidence</script> safely.'}]},'evidence':[{'summary':'Existing cohort query'}]}})
            elif req.method=='PATCH':route.fulfill(json={'dream':{'id':dream_id}})
            else:route.fulfill(status=503,json={'error':{'message':'Handoff unavailable'}})
        page.route('**/api/calliope/dreams/**',dream_route)
        page.goto(floor_site+'/floor');page.locator('#floorInitialVeil').wait_for(state='detached')
        page.add_style_tag(content=':root{--plan-paper:#efc18e;--plan-ink:#292416;--amber:#d49b64;--rvbbit-type-body:14px;--rvbbit-type-small:11px;--rvbbit-type-large:24px;--background:#061416;--foreground:#e7e8df;--block-bg:#102225;--secondary-background:#0b1c1f;--chrome-text:#aab9b9;--block-border:#234045}')
        # Notices is collapsed by default; use the actual card after expanding it.
        if page.locator('[data-expand="noticed"]').count():page.locator('[data-expand="noticed"]').click()
        tile=page.locator('.piece[data-id="curated_dream:'+dream_id+'"]')
        assert tile.locator('.chip').inner_text().lower()=='on deck'
        assert tile.locator('.dream-tile-meta').inner_text()=='High impact\n78% confidence'
        tile.screenshot(path='/tmp/calliope-floor-on-deck-tile.png')
        page.locator('.piece[data-id="curated_dream:'+dream_id+'"]').click()
        page.get_by_role('button',name='Snooze a week',exact=True).wait_for()
        assert page.locator('#drawerEyebrow').inner_text().lower()=='noticed · dream'
        assert page.locator('.dream-detail').inner_text().find('Saved analysis, no new inference')>=0
        assert page.locator('.dream-detail script').count()==0
        assert page.locator('.dream-detail-meta').inner_text().find('Small')>=0
        page.screenshot(path='/tmp/calliope-floor-dream-on-deck.png')
        page.set_viewport_size({'width':390,'height':844})
        assert page.locator('.dream-detail-meta').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path='/tmp/calliope-floor-dream-on-deck-mobile.png')
        page.set_viewport_size({'width':1400,'height':900})
        page.get_by_role('button',name='Snooze a week',exact=True).click()
        page.wait_for_function("!document.getElementById('drawerBackdrop').classList.contains('open')")
        assert ('PATCH',floor_site+'/api/calliope/dreams/'+dream_id,{'action':'sleep','days':7}) in calls
        if page.locator('[data-expand="noticed"]').count():page.locator('[data-expand="noticed"]').click()
        page.locator('.piece[data-id="curated_dream:'+dream_id+'"]').click()
        page.get_by_role('button',name='Dismiss Dream',exact=True).click()
        page.wait_for_function("!document.getElementById('drawerBackdrop').classList.contains('open')")
        assert any(c[2]=={'action':'dismiss','days':7} for c in calls)
        if page.locator('[data-expand="noticed"]').count():page.locator('[data-expand="noticed"]').click()
        page.locator('.piece[data-id="curated_dream:'+dream_id+'"]').click()
        page.get_by_role('button',name='Explore with Callie →',exact=True).click()
        page.wait_for_function("document.body.innerText.includes('could not be opened')")
        assert any(c[1].endswith('/'+dream_id+'/handoff') for c in calls)
        assert page.url==floor_site+'/floor' # No empty chat if evidence handoff failed.
        assert not errors
        browser.close()
