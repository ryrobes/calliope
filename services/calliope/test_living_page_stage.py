"""Living Page notebook behavior in the real renderer; no remote generation."""
import copy
from test_zen_chat_layout import zen_page
from test_stage_packing import prepare, populate, no_overlaps


def document():
    return {'id': '70000000-0000-4000-8000-000000000001', 'session_id': 'notebook',
            'title': 'Project Lantern', 'question': 'Where do we stand, and what needs attention?',
            'definition_version': 1, 'standing_brief': 'Keep decisions separate from open questions.',
            'revision': {'id': 'revision-1', 'version': 1,
                         'body': '## Current status\n\nLaunch remains on track for Friday [1].\n\n'
                                 '## Needs attention\n\nConfirm the training owner before the next meeting.\n\n'
                                 + '\n\n'.join('The team is validating the release checklist and reviewing customer readiness.' for _ in range(6))},
            'evidence': [], 'last_run': {'status': 'complete'}, 'last_refreshed_at': '2026-09-13T11:00:00Z'}


def boxes(page):
    return page.locator('.zen-packed>*').evaluate_all('''nodes=>nodes.map(n=>({
      x:n.offsetLeft,y:n.offsetTop,w:n.offsetWidth,h:n.offsetHeight}))''')


def test_page_is_first_readable_and_packs_with_artifacts(zen_page):
    page = zen_page
    prepare(page, 1500)
    populate(page, [140, 160, 90, 120, 100])
    page.evaluate('d=>{qa.state.threadPage=d;qa.renderStage()}', document())
    page.wait_for_timeout(350)
    assert page.locator('.zen-packed>:first-child').get_attribute('class') == 'living-page'
    assert page.title() == 'Project Lantern - Calliope'
    assert page.locator('.living-page .living-page-body').inner_text().startswith('Current status')
    assert not page.locator('#stage-empty').is_visible()
    for width in [1500, 1100, 650, 1450]:
        page.evaluate("w=>document.body.style.setProperty('--test-stage-width',w+'px')", width)
        page.wait_for_timeout(300)
        geometry = boxes(page)
        no_overlaps(geometry)
        assert geometry[0]['w'] <= 760
        assert geometry[0]['x'] == geometry[0]['y'] == 0
        assert page.locator('.living-page').evaluate('(n)=>!n.style.height && n.scrollHeight<=n.clientHeight+1')
        if width > 759:
            assert geometry[1]['y'] == 0 and geometry[1]['x'] > 0
    page.screenshot(path='/tmp/calliope-living-page-stage.png')
    page.evaluate('''()=>{
      window.keptPage=document.querySelector('.living-page');
      qa.state.surfaces.push({id:'new',turn_id:'one',kind:'image',title:'New arrival',ordinal:99,payload:{}});
      qa.renderStage();
    }''')
    assert page.evaluate("keptPage===document.querySelector('.zen-packed>:first-child')")


def test_refresh_preserves_body_then_swaps_revision_and_navigation_clears_page(zen_page):
    page = zen_page
    saved = document()
    payload = page.evaluate('()=>({session:qa.state.current,turns:qa.state.turns,surfaces:[],agent_work:{pending:[],queued_messages:0}})')
    payload['page'] = saved
    calls = []
    def refresh(route):
        calls.append(route.request.method)
        saved['last_run'] = {'status': 'running'}
        route.fulfill(status=202, json={'page': copy.deepcopy(saved)})
    page.route('**/api/calliope/sessions/notebook', lambda r: r.fulfill(json=copy.deepcopy(payload)))
    page.route('**/api/calliope/pages/'+saved['id']+'/refresh', refresh)
    page.route('**/api/calliope/pages/'+saved['id'], lambda r: r.fulfill(json={'page': copy.deepcopy(saved)}))
    page.evaluate("qa.setupEvents();qa.selectSession('notebook',{focusComposer:false})")
    assert calls == []  # Opening does not spend inference.
    page.locator('[data-living-page-refresh]').click()
    page.wait_for_function("document.querySelector('[data-living-page-refresh]').textContent==='Refreshing…'")
    assert 'Friday' in page.locator('.living-page .living-page-body').inner_text()
    assert calls == ['POST']
    saved['revision'] = {'id': 'revision-2', 'version': 2, 'body': '## Current status\n\nLaunch moved to Monday [1].'}
    saved['last_run'] = {'status': 'complete', 'outcome_summary': 'Launch moved to Monday.'}
    page.wait_for_function("document.querySelector('.living-page .living-page-body').textContent.includes('Monday')")
    assert 'Friday' not in page.locator('.living-page .living-page-body').inner_text()
    # A changed Page must rerender even when all ordinary surfaces are identical.
    saved['acl_stale'] = True
    saved['revision']['body'] = None
    page.evaluate("qa.selectSession('notebook',{preserveActivity:true,focusComposer:false})")
    assert 'Source access has changed' in page.locator('.living-page .living-page-body').inner_text()
    assert 'Monday' not in page.locator('.living-page .living-page-body').inner_text()
    page.route('**/api/calliope/sessions/another', lambda r: r.fulfill(json={
        'session': {'id':'another','title':'Another chat'}, 'turns':[], 'surfaces':[],
        'agent_work': {'pending':[], 'queued_messages':0}}))
    page.evaluate("qa.selectSession('another',{focusComposer:false})")
    assert page.locator('.living-page').count() == 0
    assert page.title() == 'Calliope'


def test_make_page_prepares_callie_request_without_old_modal(zen_page):
    page = zen_page
    posted = []
    page.route('**/api/calliope/pages', lambda r: posted.append(r.request.method) or r.fulfill(json={'pages':[]}))
    page.evaluate('''()=>{
      qa.state.config.pages=true;qa.setupEvents();
      qa.state.turns[0].user_message='What is the status of Project Lantern?';
      qa.renderChat();
    }''')
    page.locator('[data-create-page-turn]').click()
    draft = page.evaluate('qa.composerValue()')
    assert 'What is the status of Project Lantern?' in draft and 'create_calliope_page' in draft
    assert not page.locator('#calliope-pages-dialog').is_visible()
    assert posted == []


def test_library_opens_legacy_page_without_refresh_and_history_checks_access(zen_page):
    page = zen_page
    saved = document()
    saved.update(session_id='living-thread', revisions=[{'version':1,'change_summary':'Established status.'}])
    opened = []
    page.route('**/api/calliope/pages', lambda r:r.fulfill(json={'pages':[{**saved,'session_id':None}]}))
    def open_page(route):
        opened.append(route.request.method)
        route.fulfill(json={'page':saved})
    page.route('**/api/calliope/pages/'+saved['id']+'/open', open_page)
    page.route('**/api/calliope/sessions/living-thread', lambda r:r.fulfill(json={
        'session':{'id':'living-thread','title':'Page · Project Lantern'}, 'page':saved,
        'turns':[], 'surfaces':[], 'agent_work':{'pending':[], 'queued_messages':0}}))
    page.route('**/api/calliope/pages/*/revisions/1', lambda r:r.fulfill(json={'revision':{'acl_stale':True,'body':None}}))
    page.evaluate('()=>{qa.state.config.pages=true;qa.setupEvents();qa.els.pagesOpen.click()}')
    page.locator('#page-library-search').fill('nothing matches')
    assert 'No Pages match' in page.locator('#page-library-list').inner_text()
    page.locator('#page-library-search').fill('Lantern')
    page.locator('[data-open-page]').click()
    page.wait_for_function("qa.state.current.id==='living-thread'")
    assert opened == ['POST']
    assert not page.locator('#calliope-pages-dialog').is_visible()
    assert not page.locator('#page-library-dialog').is_visible()
    assert page.locator('.living-page h2').inner_text() == 'Project Lantern'
    page.locator('.living-page-brief summary').click()
    assert 'Keep decisions separate' in page.locator('.living-page-brief').inner_text()
    page.locator('[data-living-page-edit]').click()
    assert 'Update this living Page:' in page.evaluate('qa.composerValue()')
    page.locator('summary',has_text='Revision history').click()
    page.locator('[data-page-revision]').click()
    page.wait_for_function("document.querySelector('#page-revision-body').textContent.includes('no longer readable')")
    assert 'Friday' not in page.locator('#page-revision-body').inner_text()
    page.locator('#page-revision-dialog [data-page-dialog-close]').click()
    page.locator('[data-page-library-open]').click()
    page.locator('#page-library-search').fill('')
    page.screenshot(path='/tmp/calliope-page-library.png')
    page.set_viewport_size({'width':375,'height':800})
    bounds=page.locator('#page-library-dialog').bounding_box()
    assert bounds['x']>=0 and bounds['x']+bounds['width']<=375
    assert page.locator('#page-library-dialog').evaluate('n=>n.scrollWidth<=n.clientWidth+1')
    page.screenshot(path='/tmp/calliope-page-library-mobile.png')


def test_saved_page_receipt_opens_notebook_and_archive_uses_modern_controls(zen_page):
    page = zen_page
    saved=document()
    page.route('**/api/calliope/pages/*/open',lambda r:r.fulfill(json={'page':saved}))
    page.route('**/api/calliope/sessions/notebook',lambda r:r.fulfill(json={
        'session':{'id':'notebook','title':saved['title']},'page':saved,'turns':[],
        'surfaces':[],'agent_work':{'pending':[],'queued_messages':0}}))
    page.route('**/api/calliope/pages',lambda r:r.fulfill(json={'pages':[]}))
    deleted=[]
    page.route('**/api/calliope/pages/'+saved['id'],lambda r:deleted.append(r.request.method) or r.fulfill(json={'archived':True}))
    page.evaluate('''d=>{qa.setupEvents();qa.state.config.pages=true;
      qa.state.turns[0].response_receipt={saved_pages:[d]};qa.renderChat()}''',saved)
    page.locator('[data-open-saved-page]').click()
    page.locator('.living-page').wait_for()
    page.on('dialog',lambda d:d.accept())
    page.locator('[data-page-archive]').click()
    page.locator('#page-library-dialog').wait_for(state='visible')
    assert deleted==['DELETE']
    assert page.locator('.living-page').count()==0
    assert not page.locator('#calliope-pages-dialog').is_visible()
