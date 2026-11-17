"""Spatial Stage: real graph contracts, renderer lifecycle and camera interactions."""
import subprocess
from pathlib import Path
import pytest
from test_zen_chat_layout import zen_page

ROOT = Path(__file__).parent


@pytest.mark.parametrize('name,mime', [('stage-canvas.js', 'text/javascript'), ('stage-canvas.css', 'text/css'), ('stage-search.js', 'text/javascript'), ('stage-timeline.js', 'text/javascript'), ('stage-unwrap.js', 'text/javascript')])
def test_canvas_assets_are_registered_authenticated_and_cache_versioned(name, mime):
    import ast
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import Response
    import calliope
    path = '/calliope/' + name
    tree = ast.parse((ROOT / 'calliope.py').read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
              and any(isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                      and d.args[0].value == path for d in n.decorator_list))
    fn.decorator_list = []
    namespace = {'_canonical_owner': lambda r: (r.owner, None), 'Response': Response,
                 'browser_asset': lambda request, asset, media_type: Response(
                     (ROOT / 'calliope' / asset).read_bytes(), media_type=media_type)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<canvas-asset-route>', 'exec'), namespace)
    request = SimpleNamespace(owner=None, url=SimpleNamespace(path=path))
    assert asyncio.run(namespace[fn.name](request)).status_code == 401
    request.owner = 'viewer@example.com'
    response = asyncio.run(namespace[fn.name](request))
    assert response.status_code == 200
    assert response.body == (ROOT / 'calliope' / name).read_bytes()
    assert response.headers['content-type'].startswith(mime)
    assert name in calliope._CACHE_VERSIONED_ASSETS


def test_recorded_edges_versions_scope_and_stable_arrivals():
    result = subprocess.run(['node', '-'], input=r'''
      const assert=require('node:assert/strict');
      require('./calliope/stage-canvas.js');
      const {buildGraph,projection,layout}=globalThis.CalliopeStageCanvas;
      const q=(id,extra={})=>({id,kind:'query',title:id,turn_id:'t',created_at:'2026-09-28',...extra});
      const surfaces=[q('q1',{lineage_key:'q',payload:{evidence_refs:['board']}}),q('q2',{lineage_key:'q',parent_surface_id:'q1',ordinal:2}),
        q('derived',{source:{derived_from:{surface_id:'q2'}}}),q('same-table',{spatial:{tables:['public.sales']}}),
        {id:'answer',kind:'answer',payload:{evidence_refs:[{surface_id:'q2'},'hidden-other-chat']}},
        {id:'board',kind:'board',payload:{members:['answer'],cells:[{ref:'answer'}]}},
        ...Array.from({length:7},(_,i)=>({id:`app${i+1}`,kind:'artifact',artifact_slug:'report',
          lineage_key:'app',artifact_version:i+1,parent_surface_id:i?`app${i}`:null})),
        {id:'capture',kind:'image',artifact_slug:'report',artifact_version:1}];
      const g=buildGraph(surfaces);
      assert(g.edges.some(e=>e.from==='q1'&&e.to==='q2'&&e.kind==='revision'));
      assert(g.edges.some(e=>e.from==='q2'&&e.to==='derived'&&e.kind==='derived'));
      assert.equal(g.edges.filter(e=>e.kind==='member').length,1);
      assert(g.edges.some(e=>e.from==='app1'&&e.to==='capture'));
      assert(!g.edges.some(e=>e.from==='app2'&&e.to==='capture'));
      assert(!g.edges.some(e=>e.from==='hidden-other-chat'||e.from==='same-table'));
      assert(!projection(g,'topics',null).nodes.some(s=>s.id==='q1'));
      assert(projection(g,'journey',null).nodes.some(s=>s.id==='q1'));
      assert.deepEqual(projection(g,'evidence','q2').nodes.map(s=>s.id).sort(),['answer','board','derived','q1','q2']);
      assert.deepEqual(projection(g,'evidence','app7').nodes.map(s=>s.id).sort(),['app1','app2','app3','app4','app5','app6','app7','capture']);
      const first=layout(g,projection(g,'topics',null),'topics');
      const expanded=buildGraph([...surfaces,q('new',{created_at:'2020-01-01'})]);
      const next=layout(expanded,projection(expanded,'topics',null),'topics',null,first.positions);
      for(const [id,p] of first.positions)assert.deepEqual(next.positions.get(id),p);
      console.log('Graph relationships and stable slots passed');
    ''', text=True, cwd=ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr


def prepare(page, width=1500):
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate('''() => {
      qa.setupEvents();
      qa.state.turns=[{id:'one',ordinal:1,user_message:'Where do reports overlap?',status:'complete'},
        {id:'two',ordinal:2,user_message:'Check the dates and publish',status:'complete'}];
      const q=(id,title,extra={})=>({id,kind:'query',title,turn_id:'one',ordinal:1,
        created_at:'2026-09-28T12:00:00Z',spatial:{tables:['public.reports']},
        payload:{columns:['state','reports'],rows:[['Oregon',12],['California',25]],row_count:2},...extra});
      qa.state.surfaces=[q('q1','Monthly reports · v1',{lineage_key:'months'}),
        q('q2','Stated months only',{lineage_key:'months',parent_surface_id:'q1',ordinal:2}),
        q('geo','Geographic overlap'),
        {id:'answer',kind:'answer',title:'What the archives show',turn_id:'two',
         payload:{answer:'The archives share places; the evidence does not establish a shared cause.',
         evidence_refs:[{surface_id:'q2'},{surface_id:'geo'}]},created_at:'2026-09-28T12:05:00Z'},
        {id:'page',kind:'artifact',title:'Two archives',turn_id:'two',artifact_slug:'two-archives',artifact_version:1,
          lineage_key:'app',payload:{url:'/apps/two-archives',display_url:'/apps/two-archives'},created_at:'2026-09-28T12:06:00Z'}];
      qa.renderChat(true);qa.renderStage();
    }''')


def mode(page, name):
    page.locator('.stage-view-trigger').click()
    page.get_by_role('menuitemradio', name=name, exact=False).click()


def topics(page):
    mode(page, 'Topics')
    group = page.locator('[data-canvas-group="table:public.reports"]')
    if group.count(): group.click()


def test_canvas_is_lazy_and_native_query_works_on_demand(zen_page):
    page=zen_page; prepare(page)
    from test_data_panel import DRAFT, RESULT
    calls=[]
    def data_route(route):
        path=route.request.url.split('?')[0]
        calls.append(path)
        if path.endswith('/explore'):
            value={**DRAFT,'context':{'origin':{'surface_id':'q2','session_id':'notebook','title':'Stated months only'}},
                   'latest_run':{'id':'snapshot','status':'complete','result':RESULT}}
        elif path.endswith('/search'): value={'items':[],'areas':[]}
        else: value={'drafts':[]}
        route.fulfill(json=value)
    page.route('**/api/calliope/data/**',data_route)
    topics(page)
    assert page.locator('.stage-canvas').is_visible()
    assert page.locator('.stage-canvas-viewport').evaluate('(e)=>{e.scrollTop=200;e.scrollLeft=200;return e.scrollTop+e.scrollLeft}')==0
    assert page.locator('.canvas-card').count()==3
    assert page.locator('#stage iframe').count()==0
    page.locator('[data-canvas-select=q2]').click()
    assert 'Earlier version' in page.locator('.canvas-connections').inner_text()
    page.locator('[data-canvas-action=inspect]').click()
    assert page.locator('.canvas-inspector [data-surface-id=q2]').is_visible()
    assert page.locator('.canvas-inspector iframe[data-query-result]').count()==1
    page.locator('.canvas-inspector [data-open-query-surface=q2]').click()
    assert page.locator('#workspace-tab-data').get_attribute('aria-selected')=='true'
    page.frame_locator('#data-panel-frame').get_by_role('textbox',name='What would you like to see?').wait_for()
    assert not any(path.endswith('/run') for path in calls)
    page.locator('[data-canvas-action=close]').click()
    assert page.locator('#stage iframe').count()==0
    page.locator('[data-canvas-action=stage]').click()
    assert page.locator('.stage-canvas').count()==0
    assert page.locator('#stage .surface[data-surface-id=q2]').is_visible()


def test_evidence_uses_connections_and_journey_includes_history(zen_page):
    page=zen_page;prepare(page)
    topics(page)
    page.locator('[data-canvas-select=answer]').click()
    mode(page,'Evidence')
    assert sorted(page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.canvasSelect)'))==['answer','geo','q1','q2']
    assert page.get_by_role('button',name='Expand connections',exact=True).count()==0
    assert page.locator('.canvas-connections').inner_text().count('Cites')==2
    positions = page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>[n.dataset.canvasSelect,n.style.left,n.style.top])')
    page.locator('[data-canvas-link=q2]').click()
    assert page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>[n.dataset.canvasSelect,n.style.left,n.style.top])') == positions
    page.get_by_role('button',name='Follow connections',exact=True).click()
    assert 'Stated months only' in page.locator('#stage-view-context').inner_text()
    assert page.locator('[data-canvas-select=geo]').count()==1
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    assert page.locator('[data-canvas-select=geo]').count()==1
    mode(page,'Journey')
    assert page.locator('.canvas-card').count()==5
    assert page.locator('.canvas-group').count()==2
    assert page.locator('.canvas-edge.revision').count()==1
    page.locator('[data-canvas-select=page]').click()
    mode(page,'Evidence')
    assert page.locator('.canvas-card').count()==1
    assert 'No explicit connections recorded' in page.locator('.canvas-connections').inner_text()


def blank_canvas_point(page):
    return page.locator('.stage-canvas-viewport').evaluate('''viewport=>{
      const r=viewport.getBoundingClientRect();
      for(const x of [r.x+8,r.right-8])for(const y of [r.y+8,r.bottom-8]){
        const target=document.elementFromPoint(x,y);
        if(viewport.contains(target)&&!target.closest('.canvas-card,.canvas-topic,button'))return {x,y};
      }
      throw Error('No empty canvas corner');
    }''')


@pytest.mark.parametrize('name',['Evidence','Journey','Topics'])
def test_group_headings_stay_above_cards_and_within_columns_when_zooming(zen_page,name):
    page=zen_page;prepare(page)
    topics(page)
    if name!='Topics':
        page.locator('[data-canvas-select=q2]').click()
        mode(page,name)
    for zoom in [3,2,1.5,1,.79,.73,.70,.57,.41,.25,1,3]:
        page.locator('.stage-canvas-viewport').evaluate('''(viewport,zoom)=>{
          const current=new DOMMatrix(getComputedStyle(viewport.querySelector('.stage-canvas-world')).transform).a;
          const r=viewport.getBoundingClientRect();
          viewport.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,
            deltaY:Math.log(current/zoom)/.0025,clientX:r.x+r.width/2,clientY:r.y+r.height/2}));
        }''',zoom)
        headings=page.locator('.canvas-group').evaluate_all('''groups=>groups.map(group=>{
          const r=group.getBoundingClientRect(),header=group.querySelector('header'),h=header.getBoundingClientRect();
          const cards=[...group.parentElement.querySelectorAll('.canvas-card')].map(n=>n.getBoundingClientRect())
            .filter(c=>c.left>=r.left&&c.right<=r.right&&c.top>=r.top);
          const title=header.querySelector('b').getBoundingClientRect();
          return {title:header.querySelector('b').textContent,left:h.left-r.left,right:r.right-h.right,
            titleHeight:title.height,titleInset:cards.length?title.left-Math.min(...cards.map(c=>c.left)):null,
            gap:cards.length?Math.min(...cards.map(c=>c.top))-h.bottom:null,
            subtitle:getComputedStyle(header.querySelector('small')).display!=='none'};
        })''')
        assert headings
        for heading in headings:
            assert heading['left']>=-1 and heading['right']>=-1,(name,zoom,heading)
            assert heading['gap'] is None or 0<=heading['gap']<=12*zoom,(name,zoom,heading)
            assert heading['titleInset'] is None or abs(heading['titleInset'])<=1,(name,zoom,heading)
            if zoom>=1:
                assert heading['titleHeight']>=20*zoom,(name,zoom,heading)
            assert heading['subtitle']==(zoom>=.72),(name,zoom,heading)
        if zoom in [3,2,1,.79,.57,.41]:
            page.screenshot(path=f'/tmp/stage-headings-{name.lower()}-{round(zoom*100)}.png')


def test_empty_click_clears_selection_but_pan_and_cancel_preserve_it(zen_page):
    page=zen_page;prepare(page)
    mode(page,'Journey')
    page.locator('[data-canvas-select=q2]').click()
    before=page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')
    p=blank_canvas_point(page)
    page.mouse.move(**p);page.mouse.down()
    page.mouse.move(p['x']+3,p['y']+1);page.mouse.up()
    assert page.locator('.canvas-card.is-selected').count()==0
    assert page.locator('.canvas-selection').count()==0
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')==before

    page.locator('[data-canvas-select=q2]').click()
    p=blank_canvas_point(page)
    page.mouse.move(**p);page.mouse.down()
    page.mouse.move(p['x']+60,p['y']+40,steps=5);page.mouse.up()
    assert page.locator('[data-canvas-select=q2]').get_attribute('aria-pressed')=='true'
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')!=before
    # Returning to the starting point is still a drag, not a click.
    p=blank_canvas_point(page)
    page.mouse.move(**p);page.mouse.down()
    page.mouse.move(p['x']+60,p['y']+40,steps=5);page.mouse.move(**p);page.mouse.up()
    assert page.locator('[data-canvas-select=q2]').get_attribute('aria-pressed')=='true'
    # A browser-cancelled gesture must not clear a selection either.
    page.locator('.stage-canvas-viewport').evaluate("n=>n.addEventListener('pointerdown',e=>window.gestureId=e.pointerId,{once:true})")
    page.mouse.move(**p);page.mouse.down()
    page.locator('.stage-canvas-viewport').dispatch_event('pointercancel',{'pointerId':page.evaluate('window.gestureId')})
    page.mouse.up()
    assert page.locator('[data-canvas-select=q2]').get_attribute('aria-pressed')=='true'

    mode(page,'Evidence')
    ids=page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.canvasSelect)')
    page.mouse.click(**blank_canvas_point(page))
    assert page.locator('.canvas-card.is-selected').count()==0
    assert page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.canvasSelect)')==ids
    assert 'Stated months only' in page.locator('#stage-view-context').inner_text()


@pytest.mark.parametrize('width',[650,1100,1500])
def test_camera_keyboard_and_resize_arrivals_preserve_view(zen_page,width):
    page=zen_page;prepare(page,width)
    topics(page)
    viewport=page.locator('.stage-canvas-viewport')
    before=page.locator('.stage-canvas-world').get_attribute('style')
    viewport.focus();page.keyboard.press('+');page.keyboard.press('ArrowRight')
    after=page.locator('.stage-canvas-world').get_attribute('style')
    assert before!=after
    existing=page.locator('[data-canvas-select=q2]').get_attribute('style')
    page.evaluate('''()=>{qa.state.surfaces.push({id:'arrival',kind:'query',title:'Another check',turn_id:'one',
      spatial:{tables:['public.reports']},payload:{},created_at:'2026-09-28T13:00:00Z'});qa.renderStage()}''')
    assert page.locator('[data-canvas-select=q2]').get_attribute('style')==existing
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform') in after
    assert page.locator('#stage-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
    page.locator('[data-canvas-action=fit]').click()
    page.screenshot(path=f'/tmp/stage-canvas-{width}.png')
    mode(page,'Stage')
    assert page.locator('#stage-scroll').evaluate('(n)=>!n.classList.contains("is-canvas")')
    topics(page)
    assert page.locator('.canvas-card').count()==4
    page.evaluate("qa.state.current={id:'other'};qa.state.surfaces=[];qa.state.turns=[];qa.renderStage()")
    assert page.locator('.stage-canvas').count()==0
    topics(page)
    assert 'Artifacts will appear' in page.locator('.canvas-empty').inner_text()


def test_titles_are_text_and_do_not_execute_markup(zen_page):
    page=zen_page;prepare(page)
    page.evaluate('''()=>{qa.state.surfaces[1].title='<img src=x onerror="window.injected=true">';qa.renderStage()}''')
    topics(page)
    assert page.locator('[data-canvas-select=q2] strong').inner_text().startswith('<img')
    assert page.evaluate('window.injected') is None


def test_close_zoom_mounts_native_previews_and_releases_them(zen_page):
    page=zen_page;prepare(page)
    topics(page)
    page.locator('[data-canvas-select=q2]').hover()
    page.mouse.wheel(0,-450)
    page.wait_for_function("document.querySelector('.canvas-card-detail iframe[data-query-result]')")
    assert 1 <= page.locator('.canvas-card-detail').count() <= 3
    assert page.locator('.canvas-card-detail').first.get_attribute('inert') is not None
    page.evaluate("window.keptCloseGrid=document.querySelector('.canvas-card-detail iframe[data-query-result]')")
    page.mouse.wheel(0,-10)
    page.wait_for_timeout(100)
    assert page.evaluate('keptCloseGrid.isConnected')
    page.screenshot(path='/tmp/stage-canvas-close-query.png')
    page.mouse.wheel(0,1400)
    page.wait_for_function("!document.querySelector('.canvas-card-detail')")
    assert page.locator('#stage iframe').count()==0

    mode(page,'Stage')
    page.route('**/calliope/artifacts/two-archives/versions/1*',lambda route:route.fulfill(content_type='text/html',body='<h1>Exact dashboard</h1>'))
    page.evaluate("qa.state.surfaces=qa.state.surfaces.filter(s=>s.id==='page');qa.renderStage()")
    mode(page,'Topics')
    page.get_by_role('button',name='← All topics',exact=True).click()
    page.locator('[data-canvas-group="artifact:two-archives"]').click()
    page.locator('[data-canvas-select=page]').hover()
    page.mouse.wheel(0,-450)
    page.wait_for_function("document.querySelector('.canvas-card-detail iframe[data-artifact-src]')?.dataset.artifactActive==='true'")
    page.frame_locator('.canvas-card-detail iframe').get_by_role('heading',name='Exact dashboard').wait_for()
    assert page.locator('.canvas-card.has-detail').count()==1
    page.screenshot(path='/tmp/stage-canvas-close-artifact.png')
    page.locator('[data-canvas-select=page]').click()
    page.locator('[data-canvas-action=inspect]').click()
    page.wait_for_function("!document.querySelector('.canvas-card-detail')")
    assert page.locator('.canvas-inspector iframe').count()==1
    assert page.locator('#stage iframe').count()==1


def test_unwrap_is_version_bound_lazy_and_branches_into_data(zen_page):
    from test_data_panel import DRAFT, RESULT
    page=zen_page;prepare(page)
    calls=[]
    inventory={'surface_id':'page','slug':'two-archives','version':1,'title':'Two archives','tables':['public.reports'],
        'items':[{'id':'query:one','title':'Reports by state','kind':'query','source':'declared',
                  'sql':'SELECT state, count(*) FROM public.reports GROUP BY state',
                  'columns':['state','count'],'tables':['public.reports'],'can_explore':True}],
        'note':'Saved version definitions'}
    def route(r):
        path=r.request.url.split('?')[0];calls.append(path)
        if path.endswith('/contents'): value=inventory
        elif path.endswith('/preview'): value={'preview_id':'cached-one','result':RESULT,'observed_at':'2026-09-28T12:00:00Z'}
        elif path.endswith('/queries/query%3Aone/explore') or path.endswith('/queries/query:one/explore'):
            value={**DRAFT,'context':{'origin':{'surface_id':'page','session_id':'notebook','title':'Two archives · v1 · Reports by state'}},
                   'latest_run':{'id':'fresh-result','status':'complete','result':RESULT}}
        elif path.endswith('/search'): value={'items':[],'areas':[]}
        else: value={'drafts':[]}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    mode(page,'Journey')
    page.locator('[data-canvas-select=page]').click()
    assert not any(p.endswith('/contents') for p in calls)
    before=page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')
    page.get_by_role('button',name='Unwrap',exact=True).click()
    page.get_by_role('button',name='Reports by state',exact=True).wait_for()
    assert page.locator('#stage-search-form').count()==0 or not page.locator('#stage-search-form').is_visible()
    assert page.locator('[data-canvas-select]').count()==0
    assert page.evaluate('qa.state.surfaces.length')==5
    page.locator('.unwrap-card-result iframe').wait_for()
    assert not any(p.endswith('/explore') for p in calls)
    page.get_by_role('button',name='Reports by state',exact=True).click()
    page.get_by_role('button',name='View SQL',exact=True).click()
    page.mouse.click(**blank_canvas_point(page))
    assert page.locator('.unwrap-details').count()==0
    assert page.locator('.unwrap-card.is-selected').count()==0
    assert page.locator('.stage-canvas-inside').count()==1
    page.get_by_role('button',name='Reports by state',exact=True).dblclick()
    page.get_by_role('region',name='Open internal query',exact=True).wait_for()
    assert page.locator('.unwrap-inspector iframe[data-query-result]').count()==1
    page.locator('.unwrap-inspector>header').click(button='right')
    assert page.locator('.unwrap-inspector').count()==0
    page.get_by_role('button',name='View SQL',exact=True).click()
    assert 'SELECT' in page.locator('.unwrap-details pre').inner_text()
    page.get_by_role('button',name='Explore in Data',exact=True).click()
    page.frame_locator('#data-panel-frame').get_by_role('textbox',name='What would you like to see?').wait_for()
    assert any('/queries/' in p and p.endswith('/explore') for p in calls)
    assert not any(p.endswith('/run') or p.endswith('/publish') for p in calls)
    page.frame_locator('#data-panel-frame').get_by_role('button',name='Two archives · v1 · Reports by state ↗',exact=True).click()
    page.locator('.stage-canvas-inside').wait_for(state='detached')
    assert page.locator('.canvas-inspector [data-surface-id=page]').count()==1
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    assert page.locator('.stage-canvas-inside').count()==1
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    assert page.locator('[data-canvas-select=page]').count()==1
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')==before
    assert page.locator('.stage-search').is_visible()
    page.screenshot(path='/tmp/stage-unwrap-restored.png')


def test_unwrap_cancels_stale_load_when_back_or_chat_changes(zen_page):
    page=zen_page;prepare(page)
    held=[]
    page.route('**/surfaces/page/contents',lambda r:held.append(r))
    mode(page,'Journey')
    page.locator('[data-canvas-select=page]').click()
    page.get_by_role('button',name='Unwrap',exact=True).click()
    page.get_by_text('Loading query definitions…',exact=True).wait_for()
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    assert page.locator('[data-canvas-select=page]').count()==1
    held[0].fulfill(json={'surface_id':'page','slug':'two-archives','version':1,'items':[]})
    assert page.locator('.stage-canvas-inside').count()==0
    page.get_by_role('button',name='Unwrap',exact=True).click()
    page.get_by_text('Loading query definitions…',exact=True).wait_for()
    page.evaluate("qa.state.current={id:'other'};qa.state.surfaces=[];qa.state.turns=[];qa.renderStage()")
    held[-1].fulfill(json={'surface_id':'page','slug':'two-archives','version':1,'items':[]})
    assert page.locator('.stage-canvas-inside').count()==0
    assert page.locator('#stage-view-context').is_hidden()


def test_historical_artifact_and_capture_open_exact_source_on_canvas(zen_page):
    page=zen_page;prepare(page)
    page.evaluate("""() => {
      qa.state.surfaces.push({...qa.state.surfaces.at(-1),id:'new-page',artifact_version:2,created_at:'2026-09-28T13:00:00Z'});
      qa.state.surfaces.push({id:'capture',kind:'image',title:'Visual check',artifact_slug:'two-archives',artifact_version:1,
        payload:{image_url:'/old-screenshot.png'},source:{origin:'capture'},created_at:'2026-09-28T12:06:00Z'});
      qa.renderStage();
    }""")
    assert page.locator('[data-surface-id=page] .artifact-snapshot').count()==1
    assert page.locator('[data-surface-id=page] iframe[data-artifact-src]').count()==0
    mode(page,'Journey')
    page.locator('[data-canvas-select=page]').click()
    assert 'Dashboard · v1' in page.locator('[data-canvas-select=page]').inner_text()
    page.locator('[data-canvas-action=inspect]').click()
    frame=page.locator('.canvas-inspector iframe[data-artifact-src]')
    assert '/calliope/artifacts/two-archives/versions/1' in frame.get_attribute('data-artifact-src')
    page.locator('[data-canvas-action=close]').click()
    page.get_by_role('button',name='Fit',exact=True).click()
    page.locator('[data-canvas-select=capture]').click()
    assert 'Screenshot · v1' in page.locator('[data-canvas-select=capture]').inner_text()
    assert page.get_by_role('button',name='Unwrap source',exact=True).is_visible()
    page.get_by_role('button',name='Open source version',exact=True).click()
    assert '/versions/1' in page.locator('.canvas-inspector iframe[data-artifact-src]').get_attribute('data-artifact-src')
    mode(page,'Stage')
    assert page.locator('[data-surface-id=page] .artifact-snapshot').count()==1


def test_unwrap_scalar_retry_preview_budget_and_stale_results(zen_page):
    page=zen_page;prepare(page)
    held=[]
    inventory={'surface_id':'page','slug':'two-archives','version':1,'title':'Two archives','tables':['public.reports'],
        'items':[{'id':f'q{i}','title':f'Report {i}','kind':'calculation','source':'calculation',
                  'sql':'SELECT count(*) FROM public.reports','columns':['count'],'tables':['public.reports'],
                  'can_explore':True} for i in range(6)]}
    page.route('**/surfaces/page/contents',lambda r:r.fulfill(json=inventory))
    page.route('**/surfaces/page/queries/*/preview',lambda r:held.append(r))
    mode(page,'Journey')
    page.locator('[data-canvas-select=page]').click()
    page.get_by_role('button',name='Unwrap',exact=True).click()
    page.wait_for_function("document.querySelectorAll('.unwrap-loading').length===2")
    assert len(held)==2
    value={'result':{'columns':['count'],'rows':[[42]],'row_count':1},'preview_id':'p',
           'observed_at':'2026-09-28T12:00:00Z','scalar':{'value':0.25,'display':{'format':'percent','decimals':1,'unit':'share'}}}
    held[0].fulfill(json=value)
    page.get_by_text('25%',exact=True).first.wait_for()
    assert len(held)<=3  # Only the newly freed slot may start another query.
    held[1].fulfill(status=400,json={'error':{'message':'A narrower date range is needed'}})
    page.get_by_role('button',name='Report 1',exact=True).click()
    page.get_by_role('button',name='Retry',exact=True).wait_for()
    page.screenshot(path='/tmp/stage-unwrap-results.png')
    page.get_by_role('button',name='Report 0',exact=True).dblclick()
    page.locator('.unwrap-inspector .unwrap-metric').wait_for()
    assert '25%' in page.locator('.unwrap-inspector .unwrap-metric').inner_text()
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    page.get_by_role('button',name='Back to previous view',exact=True).click()
    assert page.locator('.stage-canvas-inside').count()==0
    for r in held[2:]: r.fulfill(json=value)
    assert page.locator('.stage-canvas-inside').count()==0
    assert page.evaluate('qa.state.surfaces.length')==5


def test_right_click_retraces_canvas_open_and_mode_history(zen_page):
    page=zen_page;prepare(page)
    mode(page,'Journey')
    page.locator('[data-canvas-select=q2]').dblclick()
    page.locator('.canvas-inspector').wait_for()
    page.locator('.canvas-inspector>header').click(button='right')
    assert page.locator('.canvas-inspector').count()==0
    assert page.locator('.stage-canvas').count()==1
    page.locator('[data-canvas-select=q2]').click(button='right')
    assert page.locator('.stage-canvas').count()==0
    assert page.locator('[data-surface-id=q2]').count()==1
