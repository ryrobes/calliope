"""Exercise the actual shared grid bundle and notebook tab integration."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from test_zen_chat_layout import zen_page  # noqa:F401

DATASET={'relation':'sales.orders','title':'Sales orders','description':'One row per order.','estimated_rows':500,
         'columns':[{'name':'region','type':'text'},{'name':'amount','type':'numeric'}]}
RESULT={'columns':[{'name':'region','type':'text'},{'name':'amount','type':'numeric'}],
        'rows':[['West',42],['East',17]],'row_count':2,'truncated':True,'elapsed_ms':12}
DRAFT={'id':'draft-one','question':'Sales by region','sources':['sales.orders'],'status':'ready',
       'created_at':datetime.now(timezone.utc).isoformat(),'proposal':{'title':'Regional sales','summary':'Sum order amounts by region.',
       'sql':'SELECT region,sum(amount) AS amount FROM sales.orders GROUP BY region','assumptions':[]}}

AREA={'key':'subject:sales','name':'Retail sales & fulfillment','about':'Sales, customers, and shipping. A shared subject with multiple datasets.',
      'members':['sales.orders','sales.shipping'],'published_at':DRAFT['created_at'],'roles':{},
      'subareas':[{'name':'Sales performance','about':'Orders without a shipping join.','tables':['sales.orders']}],
      'questions':['Which region has the most sales?'],'gaps':['Who owns shipping data?'],
      'candidate_breakdowns':[{'name':'Regional contribution','purpose':'Find where sales are concentrated.','measures':['SUM(amount)'],'dimensions':['region'],'grain':'One row per region','caveats':'Orders only.'}],
      'breakdown_count':1,'candidate_metrics':[{'name':'Margin idea','formula':'profit / sales','grain':'All orders','why':'Compare efficiency.'}],
      'datasets':[DATASET,{'relation':'sales.shipping','title':'Shipping','description':'Shipping events.'}],
      'metrics':[],'cubes':[], 'table_count':2,'subarea_count':1,'question_count':1,'gap_count':1,
      'subtopics':['Sales performance'],'sample_question':'Which region has the most sales?','metric_idea_count':1}


def area_setup(page,open_area=True):
    page.set_default_timeout(7000)
    page.evaluate('qa.setupEvents();qa.renderStage()')
    calls=[]
    def route(r):
        path=urlsplit(r.request.url).path
        calls.append((r.request.method,path,r.request.post_data))
        if path.endswith('/search'): value={'items':[DATASET],'areas':[],'meaning_areas':[AREA]}
        elif path.endswith('/detail'):value=AREA
        elif path.endswith('/handoff'):
            value={'prompt':'Explore Margin idea\nProposed calculation: profit / sales\nArea key: subject:sales' if json.loads(r.request.post_data)['kind']=='metric'
                   else 'Explore Regional contribution\nPurpose: Find where sales are concentrated.\nGroup by: region' if json.loads(r.request.post_data)['kind']=='breakdown'
                   else 'Investigate Who owns shipping data?\nArea key: subject:sales'}
        elif path.endswith('/preview'):value={'dataset':DATASET,'result':RESULT}
        elif path.endswith('/run'):value={'id':'area-run','status':'complete','result':RESULT,'completed_at':DRAFT['created_at']}
        elif path.endswith('/drafts') and r.request.method=='POST':
            body=json.loads(r.request.post_data)
            value={**DRAFT,'question':body['question'],'sources':body['sources'],
                   'context':{'meaning_area':{'key':AREA['key'],'name':AREA['name']},
                              'datasets':[d for d in AREA['datasets'] if d['relation'] in body['sources']]}}
        elif path.endswith('/drafts'):value={'drafts':[]}
        else:value={}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    page.locator('#workspace-tab-data').click()
    frame=page.frame_locator('#data-panel-frame')
    if not open_area:
        frame.get_by_role('button',name='Retail sales & fulfillment').wait_for()
        return frame,calls
    frame.get_by_role('button',name='Retail sales & fulfillment').click()
    frame.get_by_role('region',name='Area exploration').wait_for()
    frame.get_by_role('region',name='Open questions',exact=True).wait_for()
    return frame,calls


@pytest.mark.parametrize('width',[320,620,840])
def test_area_drilldown_history_responsive_and_sample_query(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':2000,'height':1000})
    frame,calls=area_setup(page)
    page.evaluate('w=>{qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(w,false)}',width)
    assert frame.get_by_role('heading',name=AREA['name'],exact=True).is_visible()
    assert frame.get_by_role('region',name='Area datasets').get_by_role('button').count()==2
    assert frame.locator('details').count()==0
    frame.locator('body').evaluate('(n)=>{if(n.scrollWidth>n.clientWidth+1)throw Error("horizontal page overflow")}')
    frame.get_by_role('region',name='Subtopics').get_by_role('button',name='Sales performance').click()
    assert frame.get_by_role('region',name='Area datasets').get_by_role('button').count()==1
    frame.get_by_role('region',name='Sample questions').get_by_role('button',name='Show results').click()
    frame.get_by_role('button',name='Add to Stage').wait_for()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==AREA['questions'][0]
    payload=json.loads(next(body for method,path,body in calls if method=='POST' and path.endswith('/drafts')))
    assert payload['area_key']=='subject:sales' and payload['sources']==['sales.orders']
    assert payload['area_topic']=='Sales performance'
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('region',name='Subtopics').get_by_role('button',name='Sales performance').get_attribute('aria-pressed')=='true'
    frame.get_by_role('button',name='Forward',exact=True).click()
    frame.get_by_role('textbox',name='Search these rows').wait_for()
    assert len([x for x in calls if x[1].endswith('/run')])==1
    page.screenshot(path=f'/tmp/data-area-query-{width}.png')


def test_area_gap_prepares_chat_preserves_draft_and_never_sends_automatically(zen_page):
    page=zen_page;frame,calls=area_setup(page)
    page.evaluate('qa.composerSetValue("Keep my existing thought.")')
    before=page.evaluate('qa.state.turns.length')
    frame.get_by_role('region',name='Open questions',exact=True).get_by_role('button',name='Ask Callie').click()
    page.wait_for_function('document.body.dataset.workspaceView==="chat"')
    assert page.evaluate('qa.composerValue()')=='Keep my existing thought.\n\nInvestigate Who owns shipping data?\nArea key: subject:sales'
    assert page.evaluate('qa.state.turns.length')==before
    assert [x[1] for x in calls if x[0]=='POST']==['/api/calliope/data/areas/handoff']
    assert json.loads(calls[-1][2])=={'key':'subject:sales','kind':'gap','index':0}
    page.locator('#workspace-tab-data').click()
    assert frame.get_by_role('heading',name=AREA['name'],exact=True).is_visible()
    assert not frame.get_by_role('region',name='Open questions',exact=True).get_by_role('button',name='Ask Callie').is_disabled()


def test_area_handoff_rejects_readonly_chat_without_overwriting_composer(zen_page):
    page=zen_page;frame,calls=area_setup(page)
    page.evaluate('qa.state.current.read_only=true;qa.composerSetValue("Existing thought")')
    frame.get_by_role('region',name='Open questions',exact=True).get_by_role('button',name='Ask Callie').click()
    page.get_by_text('Open a chat you can participate in to ask Callie.',exact=True).wait_for()
    assert page.evaluate('qa.composerValue()')=='Existing thought'
    assert not any(method=='POST' for method,_,_ in calls)


@pytest.mark.parametrize('width',[320,620,840])
def test_area_tiles_show_dossier_metadata_without_overflow(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':2000,'height':1000})
    frame,calls=area_setup(page,open_area=False)
    page.evaluate('w=>{qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(w,false)}',width)
    tile=frame.get_by_role('button',name='Retail sales & fulfillment')
    assert tile.get_by_text('Sales performance',exact=True).is_visible()
    assert tile.get_by_text('Which region has the most sales?',exact=True).is_visible()
    assert tile.get_by_text('1 metric idea',exact=True).is_visible()
    dataset=frame.get_by_role('region',name='Datasets',exact=True).get_by_role('button')
    assert tile.bounding_box()['height']>dataset.bounding_box()['height']
    if width>=580:assert tile.bounding_box()['width']>dataset.bounding_box()['width']
    frame.locator('body').evaluate('(n)=>{if(n.scrollWidth>n.clientWidth+1)throw Error("horizontal page overflow")}')
    page.screenshot(path=f'/tmp/data-area-tiles-{width}.png')
    tile.click()
    frame.get_by_role('region',name='Area exploration').wait_for()
    assert not any(method=='POST' for method,_,_ in calls)


def test_metric_idea_preview_uses_whole_area_and_runs_once(zen_page):
    frame,calls=area_setup(zen_page)
    frame.get_by_role('region',name='Subtopics').get_by_role('button',name='Sales performance').click()
    frame.get_by_role('region',name='Metric ideas',exact=True).get_by_role('button',name='Try in Data').click()
    frame.get_by_role('button',name='Add to Stage').wait_for()
    payload=json.loads(next(body for method,path,body in calls if method=='POST' and path.endswith('/drafts')))
    assert payload['area_key']=='subject:sales' and payload['sources']==AREA['members'] and payload['area_topic']==''
    assert all(text in payload['question'] for text in ['Margin idea','profit / sales','All orders','Compare efficiency.','not an approved metric','one scalar value','no grouping'])
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('region',name='Subtopics').get_by_role('button',name='Sales performance').get_attribute('aria-pressed')=='true'
    assert len([x for x in calls if x[1].endswith('/run')])==1


def test_metric_idea_callie_handoff_prepares_chat_without_sending(zen_page):
    page=zen_page;frame,calls=area_setup(page)
    before=page.evaluate('qa.state.turns.length')
    frame.get_by_role('region',name='Metric ideas',exact=True).get_by_role('button',name='Explore with Callie').click()
    page.wait_for_function('document.body.dataset.workspaceView==="chat"')
    assert 'Proposed calculation: profit / sales' in page.evaluate('qa.composerValue()')
    assert page.evaluate('qa.state.turns.length')==before
    payload=json.loads(next(body for method,path,body in calls if path.endswith('/handoff')))
    assert payload=={'key':'subject:sales','kind':'metric','index':0}
    assert [x[1] for x in calls if x[0]=='POST']==['/api/calliope/data/areas/handoff']


def setup(page):
    page.set_default_timeout(7000)
    page.evaluate('qa.setupEvents();qa.renderStage()')
    calls=[]
    def route(r):
        path=urlsplit(r.request.url).path
        calls.append((r.request.method,path,r.request.post_data))
        if path.endswith('/search'): value={'items':[DATASET],'areas':[]}
        elif path.endswith('/preview'): value={'dataset':DATASET,'result':RESULT}
        elif path.endswith('/run'): value={'id':'run-one','status':'complete','result':RESULT,'completed_at':DRAFT['created_at']}
        elif path.endswith('/drafts') and r.request.method=='POST':value=DRAFT
        elif path.endswith('/drafts'): value={'drafts':[],'owner':'viewer@example.com'}
        else:value={}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    page.locator('#workspace-tab-data').click()
    frame=page.frame_locator('#data-panel-frame')
    frame.get_by_role('button',name='Sales orders').click()
    frame.get_by_role('textbox',name='Search these rows').wait_for()
    return frame,calls


@pytest.mark.parametrize('width,panel_width',[(1000,320),(1500,420),(2400,840)])
def test_preview_and_responsive_grid_keep_sql_hidden(zen_page,width,panel_width):
    page=zen_page;page.set_viewport_size({'width':width,'height':950})
    frame,calls=setup(page)
    page.evaluate('width=>{qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(width,false)}',panel_width)
    assert frame.get_by_text('Dataset preview · 2 rows shown · more available').is_visible()
    assert frame.get_by_role('button',name='Run query',exact=True).count()==0
    assert frame.locator('details').count()==0
    grid=frame.locator('.data-result').bounding_box()
    composer=frame.get_by_role('form',name='Query request').bounding_box()
    assert grid['height']>=220 and grid['y']+grid['height']<=composer['y']+1
    assert composer['y']+composer['height']<=page.viewport_size['height']
    page.screenshot(path=f'/tmp/data-query-preview-{panel_width}.png')
    frame.get_by_role('button',name='Datasets and fields').click()
    sources=frame.get_by_role('complementary',name='Datasets and fields')
    assert sources.get_by_text('One row per order.',exact=True).is_visible()
    assert sources.locator('dt').all_text_contents()==['region','amount']
    if panel_width>=580:
        assert sources.bounding_box()['x']+sources.bounding_box()['width']<frame.locator('.data-result').bounding_box()['x']
    else:
        assert sources.bounding_box()['y']+sources.bounding_box()['height']<=frame.locator('.data-result').bounding_box()['y']+1
    frame.get_by_role('button',name='Close details').click()
    frame.get_by_role('textbox',name='Search these rows').fill('West')
    assert frame.get_by_text('West',exact=True).is_visible()
    assert frame.get_by_text('East',exact=True).count()==0
    page.locator('#workspace-tab-chat').click()
    assert page.locator('#workspace-chat').is_visible()
    page.locator('#workspace-tab-data').click()
    assert frame.get_by_role('textbox',name='Search these rows').input_value()=='West'
    assert not any(method=='POST' for method,_,_ in calls)
    frame.locator('body').evaluate('(n)=>{if(n.scrollWidth>n.clientWidth+1)throw Error("horizontal page overflow")}')


def test_generation_automatically_runs_once_and_edits_disable_publication(zen_page):
    frame,calls=setup(zen_page)
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region')
    frame.get_by_role('button',name='Show results',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    assert frame.get_by_role('button',name='Run query',exact=True).count()==0
    assert len([x for x in calls if x[1].endswith('/run')])==1
    assert not frame.get_by_text(DRAFT['proposal']['sql'],exact=True).is_visible()
    frame.get_by_role('button',name='View SQL',exact=True).click()
    sql=frame.get_by_role('region',name='Query SQL')
    assert sql.locator('.tok-keyword').count()>0
    assert 'SELECT' in sql.inner_text() and '\n' in sql.locator('pre').inner_text()
    assert frame.locator('details').count()==0
    frame.get_by_role('button',name='Close details').click()
    frame.get_by_role('button',name='Query details',exact=True).click()
    assert frame.get_by_role('region',name='Query details').get_by_text(DRAFT['proposal']['summary'],exact=True).is_visible()
    frame.get_by_role('button',name='Close details').click()
    assert len([x for x in calls if x[1].endswith('/run')])==1
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region for 2023')
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_disabled()
    assert frame.get_by_role('button',name='Refresh results',exact=True).is_disabled()
    assert frame.get_by_text('Previous result · request changed · 2 rows shown · more available').is_visible()


def test_back_forward_restores_preview_and_results_without_querying_again(zen_page):
    frame,calls=setup(zen_page)
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('searchbox',name='Find data or ask a question',exact=True).fill('orders')
    frame.get_by_role('button',name='Forward',exact=True).click()
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region')
    frame.get_by_role('button',name='Show results',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_text('Dataset preview · 2 rows shown · more available').is_visible()
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('searchbox',name='Find data or ask a question',exact=True).input_value()=='orders'
    frame.get_by_role('button',name='Forward',exact=True).click()
    frame.get_by_role('button',name='Forward',exact=True).click()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_visible()
    assert len([x for x in calls if x[1].endswith('/run')])==1
    assert len([x for x in calls if x[1].endswith('/preview')])==1


@pytest.mark.parametrize('freeform',[False,True])
def test_clarification_rewrites_request_and_automatically_runs(zen_page,freeform):
    page=zen_page;frame,calls=setup(page)
    clarified={**DRAFT,'status':'clarification','proposal':{'clarification':{'question':'Which period?', 'options':['All dates','Last year']}}}
    rewritten={**DRAFT,'id':'refined','question':'Sales by region across all dates'}
    page.route('**/api/calliope/data/drafts',lambda r:r.fulfill(json=clarified if r.request.method=='POST' else {'drafts':[], 'owner':'viewer@example.com'}))
    answers=[]
    def refine(r):
        answers.append(json.loads(r.request.post_data));r.fulfill(json=rewritten)
    page.route('**/api/calliope/data/drafts/draft-one/refine',refine)
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region')
    frame.get_by_role('button',name='Show results',exact=True).click()
    frame.get_by_role('button',name='All dates',exact=True).wait_for()
    assert not any(x[1].endswith('/run') for x in calls)
    if freeform:
        frame.get_by_role('textbox',name='Your answer',exact=True).fill('All dates')
        frame.get_by_role('button',name='Use answer',exact=True).click()
    else:frame.get_by_role('button',name='All dates',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    assert answers==[{'answer':'All dates'}]
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==rewritten['question']
    assert len([x for x in calls if x[1].endswith('/run')])==1
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('button',name='All dates',exact=True).is_visible()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()=='Sales by region'
    frame.get_by_role('button',name='Forward',exact=True).click()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_visible()
    assert len([x for x in calls if x[1].endswith('/run')])==1


def test_discarded_forward_branch_cannot_run_or_replace_new_selection(zen_page):
    page=zen_page;frame,calls=setup(page);held=[]
    def hold(r):
        if r.request.method=='POST':held.append(r)
        else:r.fallback()
    page.route('**/api/calliope/data/drafts',hold)
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region')
    frame.get_by_role('button',name='Show results',exact=True).click()
    frame.get_by_text('Building your query…',exact=True).wait_for()
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('button',name='Sales orders').click()
    assert frame.get_by_role('button',name='Forward',exact=True).is_disabled()
    held[0].fulfill(json=DRAFT)
    frame.get_by_text('Dataset preview · 2 rows shown · more available').wait_for()
    assert not any(x[1].endswith('/run') for x in calls)
    assert frame.get_by_role('button',name='Add to Stage',exact=True).count()==0


def test_notifications_use_notebook_toast_and_check_the_sender(zen_page):
    page=zen_page;frame,_=setup(page)
    page.evaluate("window.postMessage({type:'calliope.data.notify',message:'Wrong sender'},location.origin)")
    assert 'Wrong sender' not in page.locator('#toast').inner_text()
    frame.get_by_role('button',name='Back',exact=True).click()
    page.route('**/api/calliope/data/preview?*',lambda r:r.fulfill(status=503,json={'error':{'message':'Preview unavailable'}}))
    frame.get_by_role('button',name='Sales orders').click()
    page.get_by_text('Preview unavailable',exact=True).wait_for()
    assert 'show' in page.locator('#toast').get_attribute('class')
    assert frame.get_by_role('alert').count()==0


def test_data_is_available_without_a_conversation(zen_page):
    page=zen_page
    page.evaluate("qa.state.current=null;qa.state.intake={};document.body.classList.add('intake')")
    frame,calls=setup(page)
    assert page.locator('#workspace-data').is_visible()
    assert frame.get_by_role('textbox',name='What would you like to see?').is_visible()
    assert not any('/sessions' in path for _,path,_ in calls)


def test_stage_query_has_one_explore_action_and_transpose_defaults_off(zen_page):
    page=zen_page;frame,calls=import_setup(page)
    stage=page.locator('#stage')
    assert stage.get_by_role('button',name='Explore in Data',exact=True).count()==1
    assert stage.get_by_role('button',name='Open',exact=True).count()==0
    grid=page.frame_locator('#stage iframe[data-query-result="query-shared"]')
    toggle=grid.get_by_role('switch',name='Transpose',exact=True)
    assert toggle.get_attribute('aria-checked')=='false'
    toggle.click()
    assert toggle.get_attribute('aria-checked')=='true'
    grid.get_by_text('Field',exact=True).wait_for()
    toggle.click()
    assert toggle.get_attribute('aria-checked')=='false'
    assert grid.get_by_text('Field',exact=True).count()==0
    stage.get_by_role('button',name='Explore in Data',exact=True).click()
    frame.get_by_text('Saved Stage result · 2 rows shown · more available',exact=True).wait_for()
    assert not page.locator('#surface-viewer-dialog').is_visible()
    assert not any(path.endswith('/run') for _,path,_ in calls)


def test_fresh_discovery_and_new_datasets_clear_prompt_but_history_keeps_it(zen_page):
    frame,calls=setup(zen_page)
    frame.get_by_role('textbox',name='What would you like to see?').fill('Sales by region')
    frame.get_by_role('button',name='Show results',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    frame.get_by_role('button',name='Find data',exact=True).click()
    assert frame.get_by_role('searchbox').input_value()==''
    frame.get_by_role('button',name='Sales orders').click()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==''
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()=='Sales by region'
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_visible()
    assert len([x for x in calls if x[1].endswith('/run')])==1


def test_old_global_prompt_is_not_restored(zen_page):
    zen_page.evaluate("localStorage.setItem('calliope-data-input:viewer@example.com',JSON.stringify({question:'Old unrelated request'}))")
    frame,_=setup(zen_page)
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==''


def entry_setup(page,action='query',question='Sales by region',message=''):
    frame,calls=setup(page)
    frame.get_by_role('button',name='Find data',exact=True).click()
    routes=[]
    def resolve(r):
        routes.append(json.loads(r.request.post_data))
        r.fulfill(json={'action':action,'items':[DATASET],'sources':[DATASET] if action=='query' else [],
                        'question':question,'message':message,'entry_route_id':'entry-one'})
    page.route('**/api/calliope/data/resolve',resolve)
    return frame,calls,routes


@pytest.mark.parametrize('submit',['Enter','Go'])
def test_entry_query_builds_and_runs_once_and_retains_its_prompt(zen_page,submit):
    frame,calls,routes=entry_setup(zen_page)
    box=frame.get_by_role('searchbox');box.fill('Sales by region')
    if submit=='Enter':box.press('Enter')
    else:frame.get_by_role('button',name='Go',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    assert routes==[{'q':'Sales by region'}]
    drafts=[json.loads(body) for method,path,body in calls if path.endswith('/drafts') and method=='POST']
    assert drafts==[{'question':'Sales by region','sources':['sales.orders'],'entry_route_id':'entry-one'}]
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()=='Sales by region'
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('button',name='Forward',exact=True).click()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_visible()
    assert len([x for x in calls if x[1].endswith('/run')])==1


@pytest.mark.parametrize('question',['','Sales by region'])
def test_search_or_ambiguous_entry_never_generates_until_requested(zen_page,question):
    frame,calls,routes=entry_setup(zen_page,'search',question,'Choose data to use for this request.')
    frame.get_by_role('searchbox').fill(question or 'sales')
    frame.get_by_role('button',name='Go',exact=True).click()
    frame.get_by_text('Choose data to use for this request.',exact=True).wait_for()
    assert not any(method=='POST' for method,_,_ in calls)
    frame.get_by_role('button',name='Sales orders').click()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==question
    assert not any(method=='POST' for method,_,_ in calls)


def test_discarded_entry_route_cannot_start_a_query(zen_page):
    frame,calls,_=entry_setup(zen_page);held=[]
    zen_page.route('**/api/calliope/data/resolve',lambda r:held.append(r))
    frame.get_by_role('searchbox').fill('Sales by region')
    frame.get_by_role('button',name='Go',exact=True).click()
    frame.get_by_text('Finding the right data for your request…',exact=True).wait_for()
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_role('button',name='Sales orders').click()
    held[0].fulfill(json={'action':'query','items':[DATASET],'sources':[DATASET],
                         'question':'Sales by region','message':'','entry_route_id':'entry-one'})
    frame.get_by_text('Dataset preview · 2 rows shown · more available').wait_for()
    assert not any(method=='POST' for method,_,_ in calls)
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==''


def stage_query(page):
    page.evaluate("""payload=>{
      qa.state.surfaces=[{id:'query-shared',session_id:'notebook',turn_id:'one',kind:'query',title:'Saved result',
        source:{sql:'SELECT region,amount FROM sales.orders'},payload,presentation:{phase:'published'}}];
      qa.state.zenExpandedSurfaces.add('query-shared');qa.renderStage();qa.renderChat();
    }""",RESULT)


def test_inline_grid_keeps_mount_and_state_across_stage_updates(zen_page):
    page=zen_page;page.set_default_timeout(7000);page.evaluate('qa.setupEvents()');stage_query(page)
    frame=page.frame_locator('#stage iframe[data-query-result="query-shared"]')
    frame.get_by_role('region',name='Saved query result',exact=True).wait_for()
    assert frame.get_by_role('textbox',name='Search these rows').count()==0
    assert page.locator('#stage .data-table').count()==0
    assert frame.get_by_text('West',exact=True).is_visible()
    frame.get_by_role('switch',name='Transpose',exact=True).click()
    frame.get_by_text('Field',exact=True).wait_for()
    page.evaluate("""() => {
      window.originalGrid=document.querySelector('#stage iframe[data-query-result]');
      window.originalGridDocument=originalGrid.contentDocument;
      qa.state.surfaces.push({id:'new',turn_id:'one',kind:'answer',title:'New answer',payload:{text:'More results'}});
      qa.renderStage();qa.renderChat();qa.renderStage();
    }""")
    assert page.evaluate("document.querySelector('#stage iframe[data-query-result]')===originalGrid && originalGrid.contentDocument===originalGridDocument")
    assert frame.get_by_text('Field',exact=True).is_visible()
    page.evaluate("qa.state.surfaces[0].payload={...qa.state.surfaces[0].payload,rows:[['North',42],['South',17]]};qa.renderStage()")
    frame.get_by_text('North',exact=True).wait_for()
    assert frame.get_by_text('Field',exact=True).is_visible()
    # The shared viewer only accepts results from its real parent.
    frame.locator('body').evaluate("() => window.postMessage({type:'calliope.data.result',result:{columns:[],rows:[]}},location.origin)")
    assert frame.get_by_text('Field',exact=True).is_visible()


def import_setup(page,read_only=False):
    page.set_default_timeout(7000);page.evaluate('qa.setupEvents()');stage_query(page)
    calls=[]
    origin={'surface_id':'query-shared','session_id':'notebook','title':'Saved result','turn_ordinal':1,
            'observed_at':DRAFT['created_at'],'read_only_reason':'The complete SQL is unavailable.' if read_only else None}
    imported={**DRAFT,'id':'import-one','context':{'origin':origin},'status':'error' if read_only else 'ready',
              'latest_run':{'id':'snapshot','status':'complete','completed_at':DRAFT['created_at'],'result':{**RESULT,'saved_snapshot':True}}}
    def route(r):
        path=urlsplit(r.request.url).path;calls.append((r.request.method,path,r.request.post_data))
        if path.endswith('/explore'):value=imported
        elif path.endswith('/search'):value={'items':[DATASET],'areas':[]}
        elif path.endswith('/run'):value={'id':'fresh','status':'complete','completed_at':DRAFT['created_at'],'result':RESULT}
        elif path.endswith('/drafts') and r.request.method=='POST':value={**DRAFT,'id':'updated','context':{'origin':origin}}
        else:value={'drafts':[]}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    return page.frame_locator('#data-panel-frame'),calls


@pytest.mark.parametrize('expanded',[False,True])
def test_explore_saved_query_opens_without_execution_then_branches(zen_page,expanded):
    page=zen_page;frame,calls=import_setup(page)
    original=page.evaluate('JSON.stringify(qa.state.surfaces[0])')
    if expanded:page.evaluate("qa.openQuerySurface('query-shared')")
    else:page.locator('#stage').get_by_role('button',name='Explore in Data',exact=True).click()
    frame.get_by_text('Saved Stage result · 2 rows shown · more available',exact=True).wait_for()
    assert not page.locator('#surface-viewer-dialog').is_visible()
    assert frame.get_by_role('textbox',name='What would you like to see?').input_value()==DRAFT['question']
    assert frame.get_by_role('textbox',name='Search these rows').is_visible()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).count()==0
    assert [path for method,path,_ in calls if method=='POST']==['/api/calliope/data/surfaces/query-shared/explore']
    frame.get_by_role('textbox',name='What would you like to see?').fill('Same thing, largest first')
    frame.get_by_role('button',name='Update results',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    sent=[json.loads(body) for method,path,body in calls if method=='POST' and path.endswith('/drafts')]
    assert sent[0]['previous_id']=='import-one' and sent[0]['question']=='Same thing, largest first'
    assert len([path for _,path,_ in calls if path.endswith('/run')])==1
    frame.get_by_role('button',name='Back',exact=True).click()
    frame.get_by_text('Saved Stage result · 2 rows shown · more available',exact=True).wait_for()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).count()==0
    assert page.evaluate('JSON.stringify(qa.state.surfaces[0])')==original
    frame.get_by_role('button',name='Saved result · Turn 1 ↗',exact=True).click()
    page.locator('[data-surface-id="query-shared"][aria-current="true"]').wait_for()


def test_read_only_import_can_be_viewed_without_query_controls(zen_page):
    frame,calls=import_setup(zen_page,True)
    zen_page.locator('#stage').get_by_role('button',name='Explore in Data',exact=True).click()
    frame.get_by_text('The complete SQL is unavailable.',exact=True).wait_for()
    assert frame.get_by_text('West',exact=True).is_visible()
    for name in ['Update results','Refresh results','Add to Stage']:
        assert frame.get_by_role('button',name=name,exact=True).count()==0
    assert frame.get_by_role('textbox',name='What would you like to see?').count()==0
    assert not any(path.endswith('/run') for _,path,_ in calls)


def test_refresh_import_runs_once_without_model_generation(zen_page):
    frame,calls=import_setup(zen_page)
    zen_page.locator('#stage').get_by_role('button',name='Explore in Data',exact=True).click()
    frame.get_by_role('button',name='Refresh results',exact=True).click()
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for()
    assert [path for method,path,_ in calls if method=='POST']==[
        '/api/calliope/data/surfaces/query-shared/explore','/api/calliope/data/drafts/import-one/run']


CUBE={**DATASET,'relation':'cubes.sales_rollup','title':'Sales rollup','catalog_kind':'cube','grain':'One row per order','area':'Sales'}
METRIC={'name':'sales_total','title':'Total sales','description':'Recognized sales value.','category':'Sales','version':1,
        'display':{'format':'currency','currency':'USD','decimals':0},
        'snapshot':{'value':12500,'observed_at':DRAFT['created_at'],'observation_id':3},
        'series_scope':'history','series':[{'numeric_value':value,'observed_at':DRAFT['created_at']} for value in [10000,12000,12500]],
        'trend':{'direction':'up','meaning':'good','percent':25}}


def catalog_setup(page,metric_error=False):
    page.set_default_timeout(7000);page.evaluate('qa.setupEvents();qa.renderStage()')
    calls=[]
    def route(r):
        path=urlsplit(r.request.url).path;calls.append((r.request.method,path,r.request.post_data))
        catalog={'items':[DATASET,CUBE],'metrics':[METRIC,{**METRIC,'name':'order_value','title':'Order value'}],'areas':['Sales','Other']}
        if metric_error:catalog.update(metrics=[],metrics_error='Metrics are temporarily unavailable.')
        if path.endswith('/resolve'):value={**catalog,'action':'search','sources':[],'question':'','message':'','entry_route_id':'search-one'}
        elif path.endswith('/search'):value=catalog
        elif path.endswith('/preview'):value={'dataset':CUBE,'result':RESULT}
        elif path.endswith('/metadata'):value={'relation':CUBE['relation'],'title':CUBE['title'],'version':1,'grain':CUBE['grain'],'pivot_cap':24,
            'fields':[{'name':'region','type':'text','role':'dimension','aggregates':[],'default_aggregate':None},
                      {'name':'amount','type':'numeric','role':'measure','aggregates':['sum'],'default_aggregate':'sum'}]}
        elif path.endswith('/shelf'):
            spec=json.loads(r.request.post_data)['spec']
            value={**DRAFT,'sources':[CUBE['relation']],'context':{'cube_shelf':{'relation':CUBE['relation'],'spec':spec,'definition_version':1}},
                   'latest_run':{'id':'cube-run','status':'complete','result':RESULT,'completed_at':DRAFT['created_at']}}
        elif path.endswith('/drafts'):value={'drafts':[],'owner':'viewer@example.com'}
        else:value={}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    def metric_route(r):
        calls.append((r.request.method,urlsplit(r.request.url).path,r.request.post_data))
        r.fulfill(json={'metric':METRIC})
    page.route('**/api/gallery/metrics/sales_total?*',metric_route)
    page.locator('#workspace-tab-data').click()
    frame=page.frame_locator('#data-panel-frame')
    frame.get_by_role('region',name='Cubes',exact=True).get_by_role('button',name='Sales rollup').wait_for()
    return frame,calls


def test_search_groups_and_cube_opens_workspace_directly(zen_page):
    page=zen_page;frame,calls=catalog_setup(page)
    assert frame.get_by_role('region',name='Datasets',exact=True).get_by_role('button',name='Sales orders').count()==1
    assert frame.get_by_role('region',name='Datasets',exact=True).get_by_role('button',name='Sales rollup').count()==0
    assert frame.locator('.metric-gallery-card').count()==2
    assert frame.locator('.metric-gallery-card .metric-card-chart svg').count()==2
    assert frame.get_by_text('$13K',exact=True).count()==2
    frame.get_by_role('button',name='Sales rollup').click()
    frame.get_by_role('textbox',name='Search these rows').wait_for()
    assert not frame.get_by_role('textbox',name='What would you like to see?').is_visible()
    assert frame.locator('details').count()==0
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('button',name='Inspect Total sales',exact=True).is_visible()
    frame.get_by_role('button',name='Inspect Total sales',exact=True).click()
    page.locator('#calliope-metric-dialog').wait_for(state='visible')
    assert page.locator('#calliope-metric-title').inner_text()=='Total sales'
    assert page.locator('#workspace-data').is_visible()
    assert any(c[1]=='/api/gallery/metrics/sales_total' for c in calls)
    assert [c[1] for c in calls if c[0]=='POST']==['/api/calliope/data/cubes/shelf']


def test_resolved_search_keeps_metric_sections_on_back_and_forward(zen_page):
    frame,calls=catalog_setup(zen_page)
    frame.get_by_role('searchbox',name='Find data or ask a question',exact=True).fill('sales')
    frame.get_by_role('button',name='Go',exact=True).click()
    frame.get_by_role('button',name='Inspect Total sales',exact=True).wait_for()
    frame.get_by_role('button',name='Sales rollup').click()
    frame.get_by_role('textbox',name='Search these rows').wait_for()
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('searchbox',name='Find data or ask a question',exact=True).input_value()=='sales'
    assert frame.get_by_role('button',name='Inspect Total sales',exact=True).is_visible()
    frame.get_by_role('button',name='Forward',exact=True).click()
    assert frame.get_by_role('textbox',name='Search these rows').is_visible()
    assert sum(c[1].endswith('/metadata') for c in calls)==1
    assert sum(c[1].endswith('/shelf') for c in calls)==1
    assert not any(c[1].endswith('/preview') for c in calls)


def test_metric_catalog_failure_keeps_table_and_cube_discovery(zen_page):
    frame,_=catalog_setup(zen_page,metric_error=True)
    assert frame.get_by_role('region',name='Metrics',exact=True).get_by_text('Metrics are temporarily unavailable.').is_visible()
    assert frame.get_by_role('button',name='Sales orders').is_visible()
    assert frame.get_by_role('button',name='Sales rollup').is_visible()


@pytest.mark.parametrize('width',[320,420,640])
@pytest.mark.parametrize('theme',['dark','light'])
def test_gallery_metric_tiles_fit_resized_data_panel(zen_page,width,theme):
    page=zen_page
    page.evaluate('''width=>{qa.state.zen=false;document.body.classList.remove('zen');qa.setChatWidth(width,false)}''',width)
    frame,_=catalog_setup(page)
    page.evaluate("""theme=>{
      document.documentElement.dataset.theme=theme;
      if(theme==='light')for(const [k,v] of Object.entries({'--panel':'#faf9f6','--panel-raised':'#f0efeb',
        '--bone':'#272a30','--bone-bright':'#16191e','--fog':'#60656c','--line':'#d7dadd','--amber':'#446ea4'}))
        document.documentElement.style.setProperty(k,v);
    }""",theme)
    metrics=frame.get_by_role('region',name='Metrics',exact=True)
    metrics.scroll_into_view_if_needed()
    assert metrics.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
    assert frame.locator('.metric-gallery-card').count()==2
    widths=frame.locator('.metric-gallery-card').evaluate_all('(cards)=>cards.map(c=>c.getBoundingClientRect().width)')
    assert all(w>=174 for w in widths)
    assert frame.locator('.metric-card-open').first.is_visible()
    page.screenshot(path=f'/tmp/data-search-catalog-{width}-{theme}.png')


def cube_setup(page, width=420):
    page.set_default_timeout(7000)
    page.evaluate('qa.setupEvents();qa.renderStage()')
    cube={**DATASET,'relation':'cubes.retail','title':'Retail cube','catalog_kind':'cube',
          'columns':[{'name':'region','type':'text'},{'name':'category','type':'text'},{'name':'sales','type':'numeric'}]}
    catalog={'relation':cube['relation'],'title':cube['title'],'version':1,'grain':'region and category','pivot_cap':24,
             'fields':[{**c,'role':'measure' if c['name']=='sales' else 'dimension',
                       'aggregates':['sum','min','max'] if c['name']=='sales' else ['count_distinct','count'],
                       'default_aggregate':'sum' if c['name']=='sales' else 'count_distinct'} for c in cube['columns']]}
    requests=[];drafts=[]
    def route(r):
        path=urlsplit(r.request.url).path
        if path.endswith('/search'):value={'items':[cube],'areas':[]}
        elif path.endswith('/preview'):value={'dataset':cube,'result':RESULT}
        elif path.endswith('/metadata'):value=catalog
        elif path.endswith('/values'):value={'values':['West','East',None],'truncated':False}
        elif path.endswith('/shelf'):
            body=json.loads(r.request.post_data);requests.append(body)
            spec=body['spec']
            if spec.get('pivot'):spec['pivot']['values']=['Office','Tech']
            value={**DRAFT,'id':'cube-'+str(len(requests)),'sources':[cube['relation']],
                   'context':{'cube_shelf':{'relation':cube['relation'],'spec':spec,'definition_version':1}},
                   'latest_run':{'id':'run-'+str(len(requests)),'status':'complete','result':RESULT,'completed_at':DRAFT['created_at']}}
            drafts.append(value)
        elif path.endswith('/drafts'):value={'drafts':drafts}
        elif '/drafts/' in path:value=next(d for d in drafts if path.endswith('/'+d['id']))
        else:value={}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    page.locator('#workspace-tab-data').click()
    page.evaluate('width=>{qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(width,false)}',width)
    frame=page.frame_locator('#data-panel-frame')
    frame.get_by_role('button',name='Retail cube').click()
    from playwright.sync_api import expect
    expect(frame.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()
    return frame,requests


def add_cube_field(frame, name, lane):
    if not frame.locator('.cube-fields').is_visible():
        frame.get_by_role('button',name='Fields',exact=True).first.click()
    frame.locator('.cube-field').filter(has_text=name).click()
    frame.locator('.cube-field-actions').get_by_role('button',name=lane,exact=True).click()


@pytest.mark.parametrize('width',[320,420,640])
def test_cube_shelves_auto_pivot_and_back_forward_preserve_results(zen_page,width):
    page=zen_page;frame,requests=cube_setup(page,width)
    assert len(requests)==1
    assert requests[0]['spec']['measures'][0]['column']['name']=='sales'
    add_cube_field(frame,'category','Columns')
    frame.get_by_role('button',name='Add to Stage',exact=True).wait_for(state='visible')
    from playwright.sync_api import expect
    expect(frame.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()
    assert len(requests)==2 and requests[-1]['spec']['pivot']['column']['name']=='category'
    frame.get_by_role('button',name='Back',exact=True).click()
    assert frame.get_by_role('region',name='Columns shelf',exact=True).get_by_text('Drop a field to pivot').is_visible()
    frame.get_by_role('button',name='Forward',exact=True).click()
    assert frame.get_by_role('region',name='Columns shelf',exact=True).get_by_text('category · 2').is_visible()
    assert len(requests)==2
    assert not frame.get_by_text(DRAFT['proposal']['sql'],exact=True).is_visible()
    frame.locator('body').evaluate('(n)=>{if(n.scrollWidth>n.clientWidth+1)throw Error("horizontal page overflow")}')
    assert frame.locator('details').count()==0
    fields=frame.locator('.cube-fields')
    assert fields.is_visible()==(width>=580)
    grid=frame.locator('.data-result').bounding_box()
    assert grid['height']>=220
    assert grid['y']+grid['height']<=page.viewport_size['height']
    if width>=580:
        assert fields.bounding_box()['x']+fields.bounding_box()['width']<grid['x']
        assert frame.get_by_role('region',name='Columns shelf').bounding_box()['y']<grid['y']
    frame.get_by_role('button',name='View SQL',exact=True).click()
    assert frame.get_by_role('region',name='Query SQL',exact=True).is_visible()
    frame.get_by_role('button',name='Close details').click()
    page.screenshot(path=f'/tmp/cube-worksheet-{width}.png')
    if width==640:
        page.evaluate('qa.setChatWidth(420,false)')
        expect(fields).to_be_hidden()
        page.evaluate('qa.setChatWidth(640,false)')
        expect(fields).to_be_visible()
        assert len(requests)==2  # responsive layout does not rerun or reset the query


def test_cube_pending_response_cannot_overwrite_a_newer_edit(zen_page):
    page=zen_page;frame,requests=cube_setup(page);held=[]
    page.route('**/api/calliope/data/cubes/shelf',lambda r:held.append(r))
    add_cube_field(frame,'category','Columns')
    frame.get_by_text('Updating cube summary…',exact=True).wait_for()
    frame.get_by_role('button',name='Back',exact=True).click()
    held[0].fulfill(json={**DRAFT,'id':'obsolete'})
    assert frame.get_by_role('region',name='Columns shelf',exact=True).get_by_text('Drop a field to pivot').is_visible()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_enabled()


@pytest.mark.parametrize('width',[420,640])
def test_cube_filter_values_and_recent_restore(zen_page,width):
    page=zen_page;frame,requests=cube_setup(page,width)
    add_cube_field(frame,'region','Filters')
    assert len(requests)==1  # adding a filter does not silently discard null rows
    frame.get_by_role('region',name='Filters shelf',exact=True).get_by_role('button').first.click()
    frame.get_by_role('button',name='West',exact=True).click()
    frame.get_by_role('button',name='Apply',exact=True).click()
    from playwright.sync_api import expect
    expect(frame.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()
    assert requests[-1]['spec']['filters'][0]['values']==['West']
    frame.get_by_role('button',name='Recent',exact=True).click()
    frame.locator('.history-item').last.click()
    frame.get_by_role('region',name='Cube query shelves').wait_for()
    assert len(requests)==2


def test_cube_drag_field_and_query_error_keep_previous_result(zen_page):
    page=zen_page;frame,requests=cube_setup(page,320)
    frame.get_by_role('button',name='Fields',exact=True).first.click()
    source=frame.locator('.cube-field').filter(has_text='category')
    target=frame.get_by_role('region',name='Columns shelf',exact=True)
    source.drag_to(target)
    from playwright.sync_api import expect
    expect(frame.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()
    assert requests[-1]['spec']['pivot']['column']['name']=='category'
    page.route('**/api/calliope/data/cubes/shelf',lambda r:r.fulfill(status=400,json={'error':{'message':'This pivot has more than 24 columns. Filter values.'}}))
    frame.get_by_role('button',name='Refresh results',exact=True).click()
    frame.get_by_role('alert').wait_for()
    assert frame.get_by_text('West',exact=True).is_visible()
    assert frame.get_by_role('button',name='Add to Stage',exact=True).is_disabled()
    assert 'more than 24' in frame.get_by_role('alert').inner_text()


@pytest.mark.parametrize('width',[320,620,840])
def test_breakdown_has_purpose_grouping_and_own_query_action(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':2000,'height':1000})
    frame,calls=area_setup(page)
    page.evaluate('w=>{qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(w,false)}',width)
    section=frame.get_by_role('region',name='Breakdowns',exact=True)
    assert 'Find where sales are concentrated.' in section.inner_text()
    assert 'Group by' in section.inner_text() and 'SUM(amount)' in section.inner_text()
    assert 'Regional contribution' not in frame.get_by_role('region',name='Metric ideas',exact=True).inner_text()
    assert section.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
    section.screenshot(path=f'/tmp/meaning-breakdowns-{width}.png')
    section.get_by_role('button',name='Try in Data').click()
    frame.get_by_role('button',name='Add to Stage').wait_for()
    payload=json.loads(next(body for method,path,body in calls if method=='POST' and path.endswith('/drafts')))
    assert payload['area_topic']=='' and payload['sources']==AREA['members']
    assert all(t in payload['question'] for t in ['Regional contribution','Find where sales are concentrated.','Group by: region','SUM(amount)','not a scalar metric'])
    assert len([x for x in calls if x[1].endswith('/run')])==1


def test_breakdown_callie_handoff_prepares_narrative_without_sending(zen_page):
    page=zen_page;frame,calls=area_setup(page)
    before=page.evaluate('qa.state.turns.length')
    frame.get_by_role('region',name='Breakdowns',exact=True).get_by_role('button',name='Explore with Callie').click()
    page.wait_for_function('document.body.dataset.workspaceView==="chat"')
    assert 'Group by: region' in page.evaluate('qa.composerValue()')
    assert page.evaluate('qa.state.turns.length')==before
    payload=json.loads(next(body for method,path,body in calls if path.endswith('/handoff')))
    assert payload=={'key':'subject:sales','kind':'breakdown','index':0}


@pytest.mark.parametrize('engine,label',[('rvbbit_native','RVBBIT Native'),('datafusion_vector','DataFusion Vector'),('postgres','PostgreSQL')])
def test_stage_query_uses_readable_engine_names(zen_page,engine,label):
    page=zen_page;page.evaluate('qa.setupEvents()');stage_query(page)
    page.evaluate('(engine)=>{qa.state.surfaces[0].payload.engine=engine;qa.renderStage()}',engine)
    assert label+' engine' in page.locator('#stage .query-meta').inner_text()


def test_cube_stage_shows_recorded_engine_and_missing_receipt(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.setupEvents();qa.state.surfaces=[{id:'cube',turn_id:'one',kind:'cube',title:'Cube result',
        payload:{mode:'pivot',display_mode:'table',engine:'datafusion_vector',elapsed_ms:12,
          row_dimensions:['region'],measures:[{key:'sales',label:'Sales'}],
          table_columns:[{key:'region',kind:'dimension'},{key:'sales',kind:'measure'}],
          table_rows:[{dimensions:{region:'West'},values:{sales:42}}],grand_totals:{sales:42}}}];
      qa.renderStage();
    }''')
    assert 'DataFusion Vector engine' in page.locator('#stage .query-meta').inner_text()
    assert '12ms' in page.locator('#stage .query-meta').inner_text()
    page.evaluate('delete qa.state.surfaces[0].payload.engine;qa.renderStage()')
    assert 'Engine not recorded' in page.locator('#stage .query-meta').inner_text()
