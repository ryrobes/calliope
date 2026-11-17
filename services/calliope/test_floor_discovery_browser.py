"""Floor ambient search keeps its displayed snapshot through debounce and races."""
import base64
import pytest
from test_floor_veil import floor_page, ROOT, ORIGIN, PAYLOAD


def result(key, **extra):
    return {'id':key,'kind':'document','group':'evidence','title':key.title(),
            'summary':'A useful existing piece of company knowledge.',**extra}


@pytest.fixture
def discovery_page(floor_page):
    page,held=floor_page
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def source(route):
        script=(ROOT/'calliope/floor.js').read_text().replace('\nload();\n})();',
            '\nwindow.qa={state,renderFloorIntakeDiscovery,openFloorIntake,closeFloorIntake,scheduleFloorIntakeDiscovery};\nload();\n})();')
        script=script.replace('function chooseFloorDiscoveryItem(item){','function chooseFloorDiscoveryItem(item){window.chosen=item;return;')
        route.fulfill(body=script,content_type='text/javascript')
    page.route('**/floor.js*',source)
    page.route('**/qa-thumbnail.png',lambda r:r.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY9sAAAAASUVORK5CYII=')))
    page.goto(ORIGIN+'/floor',wait_until='domcontentloaded')
    page.wait_for_function('Boolean(window.qa)')
    while not held:page.wait_for_timeout(10)
    held.pop().fulfill(json=PAYLOAD)
    page.wait_for_selector('#floorInitialVeil',state='detached')
    page.evaluate('''() => {
      const original=window.fetch;window.searchRequests=[];
      window.fetch=(url,options)=>String(url).includes('/desk/search')
        ? new Promise(resolve=>searchRequests.push({url,query:JSON.parse(options.body).query,resolve,signal:options.signal}))
        : original(url,options);
      window.resolveSearch=(query,conversation,items,status=200)=>{
        const request=searchRequests.find(r=>r.query===query && r.url.endsWith('/conversations')===conversation && !r.done);
        if(!request)throw new Error('Missing request '+query);request.done=true;
        request.resolve(new Response(JSON.stringify({items,searched:[],error:{message:'Unavailable'}}),{status,headers:{'content-type':'application/json'}}));
      };
      qa.openFloorIntake();
    }''')
    seed=[result('alpha',thumbnail_url='/qa-thumbnail.png'),result('beta')]
    page.evaluate('''seed=>{
      qa.state.intakeDiscovery.cache.set('original',{items:seed,searched:[]});
      qa.state.intakeEditor.setValue('original');qa.scheduleFloorIntakeDiscovery();
    }''',seed)
    page.wait_for_timeout(250)
    yield page
    assert errors==[]


def search(page,query):
    page.locator('#floorIntakeEditor .cm-content').fill(query)


def wait_requests(page,query):
    page.wait_for_function('(q)=>searchRequests.filter(r=>r.query===q).length===2',arg=query)


def finish(page,query,items):
    page.evaluate('''({query,items})=>{resolveSearch(query,false,items);resolveSearch(query,true,[])}''',{'query':query,'items':items})
    page.wait_for_function("qa.state.intakeDiscovery.pending===0")


def cards(page):
    return page.locator('#floorIntakeDiscoveryItems > .floor-intake-discovery-card:not(.is-leaving)')


def test_results_survive_keystrokes_and_both_lookups_then_reconcile(discovery_page):
    page=discovery_page
    page.evaluate('''() => {
      window.alpha=document.querySelector('[data-result-id="alpha"]');
      window.beta=document.querySelector('[data-result-id="beta"]');
      window.blankFrames=0;window.watching=true;
      function sample(){if(!watching)return;if(!document.querySelector('#floorIntakeDiscoveryItems>.floor-intake-discovery-card:not(.is-leaving)'))blankFrames++;requestAnimationFrame(sample)}sample();
    }''')
    for q in ['reven','revenu','revenue']:
        search(page,q);page.wait_for_timeout(80)
        assert cards(page).count()==2
        assert page.evaluate('alpha===document.querySelector(\'[data-result-id="alpha"]\')')
    assert page.evaluate('searchRequests.length')==0
    wait_requests(page,'revenue')
    page.evaluate("resolveSearch('revenue',false,[{id:'gamma',kind:'document',title:'Gamma'}])")
    page.wait_for_timeout(100)
    assert cards(page).evaluate_all('(nodes)=>nodes.map(n=>n.dataset.resultId)')==['alpha','beta']
    # Retained stale results still point to the displayed item, not a pending index.
    page.locator('[data-floor-discovery-id="beta"]').click()
    assert page.evaluate('chosen.id')=='beta'
    page.evaluate("resolveSearch('revenue',true,[{id:'beta',kind:'document',group:'evidence',title:'Beta',summary:'A useful existing piece of company knowledge.'}])")
    page.wait_for_function("qa.state.intakeDiscovery.status==='ready'")
    assert cards(page).evaluate_all('(nodes)=>nodes.map(n=>n.dataset.resultId)')==['gamma','beta']
    assert page.evaluate('beta===document.querySelector(\'[data-result-id="beta"]\')')
    page.wait_for_timeout(300)
    assert page.locator('.is-leaving').count()==0
    assert page.evaluate('blankFrames')==0
    page.evaluate('watching=false')


def test_late_superseded_response_cannot_replace_new_results(discovery_page):
    page=discovery_page
    search(page,'older');wait_requests(page,'older')
    search(page,'newer');wait_requests(page,'newer')
    finish(page,'newer',[result('new')])
    finish(page,'older',[result('obsolete')]) # fake transport deliberately ignores AbortSignal
    assert cards(page).evaluate_all('(nodes)=>nodes.map(n=>n.dataset.resultId)')==['new']
    assert page.evaluate("qa.state.intakeDiscovery.cache.has('older')") is False


def test_errors_preserve_old_results_without_caching_and_empty_success_replaces_them(discovery_page):
    page=discovery_page
    search(page,'broken');wait_requests(page,'broken')
    page.evaluate("resolveSearch('broken',false,[],503);resolveSearch('broken',true,[],503)")
    page.wait_for_function("qa.state.intakeDiscovery.status==='error'")
    assert cards(page).count()==2
    assert page.evaluate("qa.state.intakeDiscovery.cache.has('broken')") is False
    assert 'Previous matches' in page.locator('#floorIntakeDiscoveryHint').inner_text()
    page.evaluate('qa.scheduleFloorIntakeDiscovery()')
    page.wait_for_function("searchRequests.filter(r=>r.query==='broken').length===4")
    finish(page,'broken',[])
    assert cards(page).count()==0
    assert 'No clear matches' in page.locator('#floorIntakeDiscoveryStatus').text_content()


@pytest.mark.parametrize('reduced',[False,True])
def test_reorder_preserves_card_button_identity_and_cached_search_is_immediate(discovery_page,reduced):
    page=discovery_page;page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
    page.evaluate('''() => {
      window.alpha=document.querySelector('[data-result-id="alpha"]');
      window.button=alpha.querySelector('button');window.thumbnail=alpha.querySelector('img');
      const d=qa.state.intakeDiscovery;d.cache.set('reordered',{items:[...d.items].reverse(),searched:[]});
    }''')
    search(page,'reordered')
    assert cards(page).evaluate_all('(nodes)=>nodes.map(n=>n.dataset.resultId)')==['beta','alpha']
    assert page.evaluate('button===document.querySelector(\'[data-floor-discovery-id="alpha"]\')')
    assert page.evaluate('searchRequests.length')==0
    assert page.evaluate('thumbnail && thumbnail===document.querySelector(\'[data-result-id="alpha"] img\')')
    moving=page.evaluate("[...document.querySelector('#floorIntakeDiscoveryItems').children].some(n=>n.getAnimations().length)")
    assert moving is (not reduced)
    page.locator('[data-floor-discovery-id="alpha"]').click()
    assert page.evaluate('chosen.id')=='alpha'
    page.screenshot(path=f'/tmp/floor-discovery-stable-{reduced}.png')


def test_clear_or_close_discards_results_and_ignores_pending_reply(discovery_page):
    page=discovery_page
    search(page,'pending');wait_requests(page,'pending')
    search(page,'')
    assert page.locator('#floorIntakeDiscovery').is_hidden()
    finish(page,'pending',[result('late')])
    assert page.locator('#floorIntakeDiscovery').is_hidden()
    assert cards(page).count()==0
