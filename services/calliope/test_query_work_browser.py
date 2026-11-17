"""Exercise receipt shadows through the actual chat renderer and specialist events."""
from test_zen_chat_layout import zen_page
from test_data_panel import DRAFT, RESULT
from urllib.parse import urlsplit
import pytest


def test_query_shadows_reconcile_parallel_calls_and_survive_parent_completion(zen_page):
    page = zen_page
    data_calls = []
    def data_route(route):
        path = urlsplit(route.request.url).path
        data_calls.append(path)
        if path.endswith('/search'):
            value = {'items': [], 'areas': [], 'metrics': [], 'meaning_areas': []}
        elif path.endswith('/explore'):
            value = {**DRAFT, 'id': 'import-query', 'context': {'origin': {'surface_id': 'query-a', 'session_id': 'notebook'}},
                     'latest_run': {'id': 'saved', 'status': 'complete', 'result': {**RESULT, 'saved_snapshot': True}}}
        else:
            value = {'drafts': []}
        route.fulfill(json=value)
    page.route('**/api/calliope/data/**', data_route)
    page.evaluate('qa.setupEvents()')
    page.evaluate('''() => {
      qa.state.turns[0].status='running'; qa.state.surfaces=[];
      qa.renderChat();
      for (const run of ['iris-run','cassandra-run']) qa.applyLiveSpecialistEvent(qa.state.turns[0], {
        run_id:run,type:'tool.started',call_id:'same',ordinal:1,tool_name:'run_sql',
        specialist:{key:run,name:run},created_at:new Date().toISOString()
      });
      qa.renderStage();
    }''')
    assert page.locator('.query-shadow:not(.is-leaving)').count() == 2
    assert page.locator('.query-shadow[data-status=running]:not(.is-leaving)').count() == 2
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'query-a',turn_id:'one',kind:'query',title:'Sighting totals',tool_call_id:'same',
        source:{specialist_run_id:'iris-run',specialist_key:'iris-run'},
        payload:{columns:['total'],rows:[[0]],row_count:1,elapsed_ms:25},created_at:new Date().toISOString()}];
      qa.renderStage(); qa.renderStage();
    }''')
    assert page.locator('.query-shadow:not(.is-leaving)').count() == 2
    assert page.locator('.query-shadow[data-status=running]:not(.is-leaving)').count() == 1
    assert page.locator('.query-shadow-value b').inner_text() == '0'
    assert '1 row' in page.locator('[data-query-shadow="receipt:query-a"] footer').inner_text()
    # A lead reply is not the end of outstanding specialist work.
    page.evaluate('''() => {
      qa.state.turns[0].status='complete';
      qa.acceptAgentWork({session_id:'notebook',available:true,pending:[{source_turn_id:'one',run_id:'cassandra-run',status:'running'}],
        activity:[],queued_messages:0,observed_at:new Date().toISOString(),activity_state:{phase:'team_working'}});
      qa.renderStage();
    }''')
    assert page.locator('#stage-queries').is_visible()
    assert not page.locator('#stage-queries').evaluate("el=>el.classList.contains('is-settled')")
    page.get_by_role('button', name='Explore Sighting totals in Data', exact=True).click()
    assert page.locator('#data-panel-frame').is_visible()
    page.frame_locator('#data-panel-frame').get_by_text('Saved Stage result · 2 rows shown · more available', exact=True).wait_for()
    assert '/api/calliope/data/surfaces/query-a/explore' in data_calls
    assert not any(path.endswith('/run') for path in data_calls)
    page.evaluate('''() => {
      qa.acceptAgentWork({session_id:'notebook',available:true,pending:[],activity:[],queued_messages:0,
        observed_at:new Date().toISOString(),activity_state:{phase:'ready'}}); qa.renderStage();
    }''')
    page.mouse.move(0, 0)
    page.locator('#stage-queries').wait_for(state='hidden', timeout=8000)
    assert page.evaluate("qa.state.surfaces.some(s=>s.id==='query-a')")
    assert page.locator('#data-panel-frame').is_visible(), 'An open Data result outlives its temporary shadow'


def test_query_shadows_reload_rollover_and_safe_cells(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.turns[0].status='running';
      qa.state.surfaces=Array.from({length:24},(_,i)=>({id:'q'+i,turn_id:'one',kind:'query',title:'Query '+i,
        tool_call_id:'call'+i,ordinal:i,created_at:new Date().toISOString(),source:{},
        payload:{columns:['name','value'],rows:[['<img src=x onerror=alert(1)>',false],[null,0]],row_count:2}}));
      qa.renderStage();
    }''')
    assert page.locator('.query-shadow:not(.is-leaving)').count() == 4
    assert '4 recent' in page.locator('.query-work-count').inner_text()
    assert page.locator('.query-shadow:not(.is-leaving)').evaluate_all('els=>els.map(el=>el.dataset.queryShadow)') == ['receipt:q23','receipt:q22','receipt:q21','receipt:q20']
    assert page.locator('.query-shadow-preview img').count() == 0
    assert '<img src=x' in page.locator('.query-shadow-preview').first.inner_text()
    before = page.locator('.query-shadow:not(.is-leaving)').evaluate_all('els=>els.map(el=>el.dataset.queryShadow)')
    page.locator('.query-shadow-title').first.hover()
    page.evaluate('''() => {
      qa.state.surfaces.push({...qa.state.surfaces[0],id:'newest',ordinal:99,tool_call_id:'new',title:'New arrival',created_at:new Date().toISOString()});
      qa.renderStage();
    }''')
    page.wait_for_timeout(1200)
    assert page.locator('.query-shadow:not(.is-leaving)').evaluate_all('els=>els.map(el=>el.dataset.queryShadow)') == before
    page.mouse.move(0, 0)
    page.locator('[data-query-shadow="receipt:newest"]').wait_for()
    assert page.locator('.query-shadow:not(.is-leaving)').count() == 4
    assert page.locator('.query-shadow:not(.is-leaving)').first.get_attribute('data-query-shadow') == 'receipt:newest'
    # Changing notebooks disposes the transient view, not the receipts.
    page.evaluate("qa.state.current={id:'other'};qa.state.turns=[];qa.state.surfaces=[];qa.renderStage()")
    assert page.locator('.query-shadow:not(.is-leaving)').count() == 0


def test_query_row_fits_width_expires_and_releases_stage_space(zen_page):
    page = zen_page
    page.clock.install()
    page.evaluate('''() => {
      qa.state.turns[0].status='running';
      qa.state.surfaces=Array.from({length:12},(_,i)=>({id:'q'+i,turn_id:'one',kind:'query',title:'Query '+i,
        tool_call_id:'call'+i,ordinal:i,created_at:new Date(Date.now()+i-20).toISOString(),source:{},
        payload:{columns:['name','value'],rows:[['June',12]],row_count:1}}));
      qa.renderChat(); qa.renderStage();
    }''')
    cards=page.locator('.query-shadow:not(.is-leaving)')
    assert cards.count()==4
    metrics=cards.evaluate_all('els=>els.map(el=>({top:el.offsetTop,width:el.offsetWidth}))')
    assert len({m['top'] for m in metrics})==1
    assert all(210<=m['width']<=260 for m in metrics)
    assert page.locator('.stage-live-work').evaluate("el=>getComputedStyle(el).position")=='relative'
    before=page.locator('.stage-live-work').bounding_box()['height']
    assert before<230
    page.set_viewport_size({'width':950,'height':900})
    page.wait_for_function("document.querySelectorAll('.query-shadow:not(.is-leaving)').length===1")
    assert cards.evaluate_all('els=>els.map(el=>el.dataset.queryShadow)')==['receipt:q11']
    assert cards.first.bounding_box()['width']<=page.locator('#stage-queries').bounding_box()['width']
    page.clock.fast_forward(92000)
    page.locator('#stage-queries').wait_for(state='hidden')
    assert page.locator('.stage-live-work').bounding_box() is None
    assert page.evaluate('qa.state.surfaces.length')==12
    page.evaluate('qa.renderStage()')
    assert cards.count()==0


def test_completed_specialist_retires_while_other_agent_keeps_working(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.turns[0].status='running'; qa.state.surfaces=[]; qa.renderChat();
      for(const id of ['a','b']) qa.applyLiveSpecialistEvent(qa.state.turns[0], {
        run_id:id,type:'tool.started',call_id:'same',ordinal:1,tool_name:'run_sql',
        specialist:{key:id,name:id},created_at:new Date().toISOString()});
      qa.renderStage();
      qa.applyLiveSpecialistEvent(qa.state.turns[0],{run_id:'a',type:'completed',ordinal:2});
    }''')
    page.wait_for_function("document.querySelectorAll('.query-shadow:not(.is-leaving)').length===1")
    assert page.locator('.query-shadow:not(.is-leaving)').get_attribute('data-query-shadow')=='call:b:same'
    assert page.locator('#stage-queries').is_visible()


def test_query_updates_keep_identity_and_arrivals_move_older_cards_right(zen_page):
    page = zen_page
    page.evaluate('''() => {
      window.snapshot={session:'notebook',runs:{one:'running'},surfaces:[],tasks:[
        {scope:'one',call:'one:first',name:'run_sql',status:'running',started:Date.now()-1000},
        {scope:'one',call:'one:second',name:'run_sql',status:'running',started:Date.now()}]};
      CalliopeQueryWork.sync(snapshot);
      window.pendingCard=document.querySelector('[data-query-shadow="call:one:first"]');
    }''')
    page.wait_for_timeout(400)
    page.wait_for_function('pendingCard.getAnimations().length===0')
    page.evaluate('''() => {
      snapshot.surfaces.push({id:'first-result',turn_id:'one',kind:'query',tool_call_id:'first',
        created_at:new Date().toISOString(),payload:{columns:['total'],rows:[[42]]}});
      CalliopeQueryWork.sync(snapshot);
    }''')
    assert page.evaluate("pendingCard===document.querySelector('[data-query-shadow=\"receipt:first-result\"]')")
    assert page.locator('.query-work-grid > .query-shadow').evaluate_all('els=>els.map(el=>el.dataset.queryShadow)') == ['call:one:second', 'receipt:first-result']
    assert page.locator('.query-shadow.is-leaving').count() == 0
    assert page.evaluate('pendingCard.getAnimations().length') == 0
    page.evaluate('''() => {
      snapshot.tasks.push({scope:'one',call:'one:third',name:'run_sql',status:'running',started:Date.now()+10});
      CalliopeQueryWork.sync(snapshot);
      window.sliding=pendingCard.getAnimations()[0];
      sliding.pause();sliding.currentTime=140;
      window.inFlightLeft=pendingCard.getBoundingClientRect().left;
      // A poll during a slide should neither restart it nor reverse it.
      CalliopeQueryWork.sync(snapshot);CalliopeQueryWork.sync(snapshot);
    }''')
    assert page.evaluate('pendingCard.getAnimations()[0]===sliding')
    assert page.evaluate('Math.abs(pendingCard.getBoundingClientRect().left-inFlightLeft)') < 1
    page.evaluate('''() => {
      snapshot.tasks.push({scope:'one',call:'one:fourth',name:'run_sql',status:'running',started:Date.now()+20});
      CalliopeQueryWork.sync(snapshot);
      window.redirect=pendingCard.getAnimations()[0];redirect.pause();redirect.currentTime=0;
    }''')
    assert page.evaluate('Math.abs(pendingCard.getBoundingClientRect().left-inFlightLeft)') < 1
    direction = page.evaluate('''() => {
      const start=pendingCard.getBoundingClientRect().left;
      redirect.currentTime=170;const middle=pendingCard.getBoundingClientRect().left;
      redirect.finish();return [start,middle,pendingCard.getBoundingClientRect().left];
    }''')
    assert direction[0] < direction[1] < direction[2]
    assert page.locator('.query-work-grid > .query-shadow').first.get_attribute('data-query-shadow') == 'call:one:fourth'


@pytest.mark.parametrize('offscreen', [False, True])
def test_retiring_receipts_iconify_and_fly_to_evidence_in_sequence(zen_page,offscreen):
    page=zen_page
    page.evaluate('''() => {
      qa.state.turns[0].status='running';
      qa.state.surfaces=Array.from({length:3},(_,i)=>({id:'q'+i,turn_id:'one',kind:'query',presentation:{phase:'exploration'},title:'Query '+i,
        tool_call_id:'call'+i,created_at:new Date().toISOString(),source:{},
        payload:{columns:['total'],rows:[[12]],row_count:1}}));
      qa.renderChat();qa.renderStage();
    }''')
    target=page.locator('[data-zen-ledger="one"] .evidence-ledger-head')
    target.wait_for()
    assert '0 queries' in target.inner_text().lower()
    assert page.locator('[data-zen-ledger-surface]').count() == 0
    if offscreen:
        page.locator('[data-zen-ledger="one"]').evaluate("el=>el.style.marginTop='1600px'")
    else:
        assert target.bounding_box()['y']<page.locator('#stage-scroll').bounding_box()['y']+page.locator('#stage-scroll').bounding_box()['height']
    page.evaluate("qa.state.turns[0].status='complete';qa.renderChat();qa.renderStage()")
    page.wait_for_function("document.querySelectorAll('.query-work-flight').length===3")
    flights=page.locator('.query-work-flight')
    assert flights.evaluate_all('els=>els.map(el=>el.dataset.destination)')==[('below' if offscreen else 'evidence')]*3
    delays=flights.evaluate_all('els=>els.map(el=>el.getAnimations()[0].effect.getTiming().delay)')
    assert delays[1]-delays[0]>80 and delays[2]-delays[1]>80
    assert page.locator('.query-work-token svg').count()==3
    assert page.locator('.query-work-flight-layer').evaluate('el=>el.inert')
    assert '0 queries' in target.inner_text().lower(), 'Flying receipts are filed when they arrive'
    # Land one token while the others wait: its row/count should appear without
    # packing a new activity card above their shared destination.
    page.evaluate("window.filingFlights=[...document.querySelectorAll('.query-work-flight')].map(el=>el.getAnimations()[0]);filingFlights.forEach(a=>a.pause())")
    target_top = target.bounding_box()['y']
    page.evaluate('filingFlights[0].finish()')
    page.wait_for_function("document.querySelectorAll('[data-zen-ledger-surface]').length===1")
    assert '1 query' in target.inner_text().lower()
    assert abs(target.bounding_box()['y'] - target_top) < 2
    page.evaluate('filingFlights.slice(1).forEach(a=>a.play())')
    page.locator('.query-work-flight-layer').wait_for(state='detached')
    page.locator('#stage-queries').wait_for(state='hidden')
    assert page.locator('[data-zen-ledger="one"]').count()==1
    assert '3 queries' in target.inner_text().lower()
    assert page.locator('[data-zen-ledger-surface]').count() == 3
    assert page.evaluate('qa.state.surfaces.length')==3


def test_retirement_flights_clear_on_notebook_change_and_skip_reduced_motion(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.turns[0].status='running';
      qa.state.surfaces=[{id:'q',turn_id:'one',kind:'query',presentation:{phase:'exploration'},tool_call_id:'call',created_at:new Date().toISOString(),
        payload:{columns:['total'],rows:[[12]],row_count:1}}];
      qa.renderChat();qa.renderStage();
      qa.state.turns[0].status='complete';qa.renderChat();qa.renderStage();
    }''')
    page.locator('.query-work-flight').wait_for()
    page.evaluate("qa.state.current={id:'other'};qa.state.turns=[];qa.state.surfaces=[];qa.renderStage()")
    assert page.locator('.query-work-flight-layer').count()==0
    page.emulate_media(reduced_motion='reduce')
    page.evaluate('''() => {
      const query={id:'q',turn_id:'other',kind:'query',presentation:{phase:'exploration'},created_at:new Date().toISOString(),payload:{columns:['total'],rows:[[1]]}};
      CalliopeQueryWork.sync({session:'other',runs:{other:'running'},surfaces:[query]});
      CalliopeQueryWork.sync({session:'other',runs:{other:'ended'},surfaces:[query]});
    }''')
    page.locator('#stage-queries').wait_for(state='hidden')
    assert page.locator('.query-work-flight-layer').count()==0
