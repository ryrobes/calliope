"""SQL workspace behavior against the real compiled Data iframe."""
import json
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import expect
from test_zen_chat_layout import zen_page  # noqa:F401
from test_data_panel import DATASET, DRAFT, RESULT, import_setup
CATALOG={'datasets':[{**DATASET,'schema':'sales','name':'orders'}],'functions':[],'operators':[]}
PLAN={'plan':{'Plan':{'Node Type':'Aggregate','Total Cost':12,'Plan Rows':1,
 'Plans':[{'Node Type':'Seq Scan','Relation Name':'orders','Plan Rows':500,'Total Cost':11}]}},'elapsed_ms':3}

def sql_setup(page,width=620):
    page.set_default_timeout(7000);page.set_viewport_size({'width':2000,'height':1000})
    page.evaluate('width=>{qa.state.surfaces=[];qa.state.zen=false;document.body.classList.remove("zen");qa.setChatWidth(width,false);qa.setupEvents();qa.renderStage()}',width)
    calls=[]
    def route(r):
        path=urlsplit(r.request.url).path
        body=json.loads(r.request.post_data) if r.request.post_data else None
        calls.append((path,body))
        if path.endswith('/sql/catalog'):value=CATALOG
        elif path.endswith('/sql/explain'):value=PLAN
        elif path.endswith('/sql/run'):value={**DRAFT,'context':{'sql_editor':True},
          'proposal':{**DRAFT['proposal'],'title':body['title'],'sql':body['sql']},
          'latest_run':{'id':'sql-run','status':'complete','result':RESULT}}
        elif path.endswith('/search'):value={'items':[DATASET],'areas':[]}
        else:value={'drafts':[]}
        r.fulfill(json=value)
    page.route('**/api/calliope/data/**',route)
    page.locator('#workspace-tab-data').click()
    f=page.frame_locator('#data-panel-frame');f.get_by_role('button',name='SQL workspace',exact=True).click()
    f.locator('.cm-content').wait_for()
    return f,calls


def test_incoming_sql_is_formatted_before_display_and_execution(zen_page):
    page=zen_page;f,calls=sql_setup(page)
    page.evaluate('''() => document.getElementById('data-panel-frame').contentWindow.postMessage({
      type:'calliope.data.sql',request_id:'format-test',title:'Incoming SQL',
      sql:"select region, sum(amount) as total, 'do not change this text' as note from sales.orders group by region"},location.origin)''')
    f.get_by_text('West',exact=True).wait_for()
    runs=[body for path,body in calls if path.endswith('/sql/run')]
    assert len(runs)==1 and '\nFROM\n  sales.orders' in runs[0]['sql']
    assert "'do not change this text'" in runs[0]['sql']
    assert f.locator('.cm-line').count()>3
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()

@pytest.mark.parametrize('width',[320,620,840])
@pytest.mark.parametrize('theme',['dark','light'])
def test_sql_editor_result_explain_and_responsive_layout(zen_page,width,theme):
    page=zen_page;f,calls=sql_setup(page,width)
    page.evaluate('''theme=>{document.documentElement.dataset.theme=theme;
      if(theme==='light')for(const [k,v] of Object.entries({'--panel':'#faf9f6','--panel-raised':'#f0efeb',
       '--bone':'#272a30','--bone-bright':'#16191e','--fog':'#60656c','--line':'#d7dadd','--amber':'#446ea4'}))document.documentElement.style.setProperty(k,v)}''',theme)
    editor=f.locator('.cm-content');editor.fill('select region,sum(amount) as amount from sales.orders group by region')
    f.get_by_role('button',name='Format',exact=True).click();expect(editor).to_contain_text('SELECT')
    editor.press('Control+Enter');f.get_by_text('West',exact=True).wait_for()
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_enabled()
    editor.press('Control+Shift+Enter');f.get_by_text('Estimated plan · 3 ms',exact=True).wait_for()
    assert f.locator('.sql-explain svg').count()>0
    assert len([1 for p,_ in calls if p.endswith('/sql/run')])==1
    assert len([1 for p,_ in calls if p.endswith('/sql/explain')])==1
    assert f.locator('body').evaluate('(e)=>e.scrollWidth<=e.clientWidth+2')
    assert f.locator('.sql-editor-pane').bounding_box()['y']<f.locator('.sql-results').bounding_box()['y']
    if width==620 and theme=='dark':page.locator('#data-panel-frame').screenshot(path='/tmp/calliope-sql-workspace-explain.png')
    f.get_by_role('tab',name='Results',exact=True).click();editor.fill('SELECT 99')
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_disabled()
    f.get_by_role('button',name='Back',exact=True).click();f.get_by_role('button',name='Forward',exact=True).click()
    expect(f.locator('.cm-content')).to_have_text('SELECT 99')
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_disabled()

def test_sql_pending_response_preserves_newer_editor_and_title(zen_page):
    page=zen_page;f,calls=sql_setup(page);pending=[]
    page.route('**/api/calliope/data/sql/run',lambda r:pending.append(r))
    editor=f.locator('.cm-content');editor.fill('SELECT 42');f.get_by_role('button',name='Run',exact=True).click()
    editor.fill('SELECT 99');f.get_by_role('textbox',name='Query title',exact=True).fill('New query')
    assert len(pending)==1
    pending[0].fulfill(json={**DRAFT,'context':{'sql_editor':True},'proposal':{**DRAFT['proposal'],'sql':'SELECT 42','title':'SQL exploration'},
      'latest_run':{'id':'old','status':'complete','result':RESULT}})
    f.get_by_text('West',exact=True).wait_for();expect(editor).to_have_text('SELECT 99')
    expect(f.get_by_role('textbox',name='Query title',exact=True)).to_have_value('New query')
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_disabled()

def test_stage_sql_escape_opens_a_copy_without_execution(zen_page):
    page=zen_page;f,calls=import_setup(page,read_only=True)
    page.route('**/api/calliope/data/sql/catalog',lambda r:r.fulfill(json=CATALOG))
    before=page.evaluate('JSON.stringify(qa.state.surfaces)');page.locator('#stage [data-open-query-sql]').click()
    expect(f.locator('.cm-content')).to_contain_text('SELECT')
    expect(f.get_by_role('button',name='Run',exact=True)).to_be_enabled()
    expect(f.get_by_role('button',name='Add to Stage',exact=True)).to_be_disabled()
    assert len([1 for method,path,_ in calls if method=='POST'])==1
    assert page.evaluate('JSON.stringify(qa.state.surfaces)')==before


def test_sql_schema_completions_and_rvbbit_operator_decoration(zen_page):
    page=zen_page
    f,calls=sql_setup(page)
    editor=f.locator('.cm-content')
    editor.fill('SELECT * FROM sales.');editor.press('Control+Space')
    f.get_by_role('option',name='orders',exact=False).wait_for()
    editor.press('Escape')
    f.get_by_role('button',name='Back',exact=True).click()
    op={'name':'judge','shape':'scalar','argNames':['input'],'argTypes':['text'],'returnType':'text','description':'Test operator','model':'test'}
    page.route('**/api/calliope/data/sql/catalog',lambda r:r.fulfill(json={**CATALOG,'operators':[op]}))
    f.get_by_role('button',name='SQL workspace',exact=True).click()
    f.locator('.cm-content').fill("SELECT rvbbit.judge('test')")
    f.locator('.cm-semantic-op').wait_for()
    assert f.locator('.cm-semantic-op').inner_text()=='rvbbit.judge'
