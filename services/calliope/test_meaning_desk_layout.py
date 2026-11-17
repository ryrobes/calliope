"""Real Meaning controller and CSS: shared collection layout and navigation."""
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import warehouse_theme

ROOT = Path(__file__).parent
AREA = 'subject:service'


def node(kind, key, title, **props):
    return dict(id=f'{kind}:{key}', kind=kind, title=title, status='governed',
                reference={'kind':'meaning','area':AREA,'node':f'{kind}:{key}'}, **props)


@pytest.fixture
def desk():
    pw = pytest.importorskip('playwright.sync_api')
    area = node('area', AREA, 'Service operations', area=AREA, subtitle='2 sources · 1 metric idea')
    metric = node('metric', 'revenue', 'Service revenue', version=2,
                  preview={'label':'Saved daily snapshot','value':'654321.25'},
                  subtitle='Total service revenue', defaults={})
    source = node('relation','public.jobs','Service jobs', source='public.jobs')
    cube = node('cube','cubes.jobs','Monthly service',source='cubes.jobs')
    artifact = node('artifact','desk','Service desk',slug='service-desk',subtitle='Published company app')
    idea = node('idea','followups','Follow-up opportunities')
    nodes = [area,metric,source,cube,artifact,idea]
    observations = [{'value':620000+i*1000,'time':1790812800000+i*86400000,'observed_at':f'2026-10-{i+1:02}T00:00:00Z'} for i in range(12)]
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1600,'height':1000})
        page.set_default_timeout(7000)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            url=urlsplit(r.request.url)
            if url.netloc!='meaning.test':r.abort();return
            if url.path=='/meaning':
                html=(ROOT/'calliope/meaning.html').read_text().replace('__CALLIOPE_WORKSPACE_NAV__',warehouse_theme.workspace_navigation(active='meaning',can_administer=True))
                html=re.sub(r'__[A-Z_]+__','',html)
                r.fulfill(body=html,content_type='text/html');return
            if url.path.endswith('/graph'):
                r.fulfill(json={'nodes':nodes if parse_qs(url.query).get('area',[''])[0] else [area], 'edges':[{'from':source['id'],'to':metric['id'],'label':'measured by','state':'declared','evidence':{'source':'registry'}}]});return
            if url.path.endswith('/inspect'):
                r.fulfill(json={'detail':{'observations':observations,'sql':'SELECT sum(amount) FROM public.jobs','grain':'All completed jobs','defaults':{},'fields':[{'col_name':'amount','data_type':'numeric','role':'measure'}]}});return
            if url.path.endswith('/pins'):
                r.fulfill(json={'pins':[{'title':metric['title'],'reference':metric['reference']}]});return
            if url.path.endswith('/data'):
                spec=r.request.post_data_json['spec']
                fields=[{'name':name,'type':'text','role':'dimension','aggregates':['count_distinct'],'default_aggregate':'count_distinct'} for name in ['branch','region','status','technician']]+[{'name':'amount','type':'numeric','role':'measure','aggregates':['sum','avg','min','max'],'default_aggregate':'sum'}]
                r.fulfill(json={'source':'cubes.jobs','as_of':'2026-10-12T00:00:00Z','catalog':{'relation':'cubes.jobs','title':'Monthly service','grain':'job','version':1,'fields':fields,'pivot_cap':12},'fields':fields,'rows':[{'amount':'123'}],'offset':0,'has_more':False,'elapsed_ms':21,'rollup':{'columns':['Record count'],'column_types':[{'name':'Record count','oid':20}],'rows':[{'Record count':'123'}],'spec':spec,'has_more':False}});return
            if url.path.startswith('/api/'):
                r.fulfill(json={});return
            path=ROOT/url.path.lstrip('/')
            if url.path.startswith('/meaning/'):
                path=ROOT/'calliope'/url.path.rsplit('/',1)[-1]
            if path.is_file() and path.resolve().is_relative_to(ROOT):
                r.fulfill(body=path.read_bytes(),content_type=mimetypes.guess_type(path)[0] or 'text/plain');return
            r.fulfill(status=404)
        page.route('**/*',route)
        page.goto('http://meaning.test/meaning')
        page.locator('.desk-area-card').wait_for()
        yield page
        assert not errors
        browser.close()


def test_collection_detail_history_and_pins_keep_navigation(desk):
    page=desk
    page.locator('.desk-area-card').filter(has_text='Service operations').click()
    # An area opens on its map; Objects keeps the collection and worksheet.
    page.locator('.dg-levels').wait_for()
    page.get_by_role('button',name='Objects',exact=True).click()
    page.locator('.desk-metric').wait_for()
    page.locator('.desk-metric .history-chart').wait_for()
    assert 'mode=objects' in page.url
    assert page.locator('.desk-types[aria-label="Object categories"] button').count()==5
    page.get_by_role('button',name='Definition & source',exact=True).click()
    page.get_by_role('heading',name='Definition & specs',exact=True).wait_for()
    assert page.locator('.desk-object-body > .desk-definition').count()==1
    page.locator('#desk-nav').get_by_role('button',name='Pinned',exact=True).click()
    page.get_by_role('heading',name='Pinned to Home').wait_for()
    assert 'view=pins' in page.url
    page.reload()
    page.get_by_role('heading',name='Pinned to Home').wait_for()
    page.get_by_role('button',name=re.compile('Service revenue')).click()
    page.locator('.desk-metric').wait_for()
    assert 'view=pins' not in page.url
    page.get_by_role('button',name='Back',exact=True).click()
    page.get_by_role('heading',name='Pinned to Home').wait_for()


@pytest.mark.parametrize('width',[390,900,1600,2400])
def test_reading_and_collection_surfaces_fit_viewport(desk,width):
    page=desk
    page.set_viewport_size({'width':width,'height':1000})
    page.locator('.desk-area-card').filter(has_text='Service operations').click()
    page.get_by_role('button',name='Objects',exact=True).click()
    page.locator('.desk-metric .history-chart').wait_for()
    boxes=page.evaluate('''() => {
      const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width}};
      return {width:innerWidth,content:rect('#desk-content'),detail:rect('.desk-object-page'),metric:rect('.desk-metric'),definition:rect('.desk-definition'),pageOverflow:document.documentElement.scrollWidth>innerWidth};
    }''')
    assert page.locator('.history-chart').bounding_box()['height']>=190
    assert not boxes['pageOverflow'],boxes
    assert boxes['detail']['right']<=width+1,boxes
    assert boxes['metric']['right']<=width+1,boxes
    if width>=2400:assert abs(boxes['metric']['top']-boxes['definition']['top'])<2
    if width<=900:assert boxes['definition']['top']>=boxes['metric']['bottom']-1
    page.get_by_role('button',name='Definition & source',exact=True).click()
    assert page.locator('.desk-object-page').bounding_box()['x']==pytest.approx(boxes['detail']['left'])
    page.get_by_role('button',name=re.compile('Apps & reports')).click()
    page.get_by_role('button',name=re.compile('Service desk')).click()
    page.get_by_role('link',name='Open published app ↗').wait_for()
    page.get_by_role('button',name=re.compile('Suggestions')).click()
    page.get_by_role('button',name=re.compile('Follow-up opportunities')).click()
    page.get_by_role('heading',name='Suggested analysis · no result recorded').wait_for()


@pytest.mark.parametrize('size',[(1280,720),(1800,1000)])
def test_worksheet_retains_room_for_fields_and_real_grid(desk,size):
    page=desk
    page.set_viewport_size({'width':size[0],'height':size[1]})
    page.locator('.desk-area-card').filter(has_text='Service operations').click()
    page.get_by_role('button',name='Objects',exact=True).click()
    page.get_by_role('button',name='Cubes 1',exact=True).click()
    page.get_by_role('button',name='Monthly service cubes.jobs',exact=True).click()
    page.get_by_role('region',name='Query result',exact=True).wait_for()
    catalog=page.locator('.cube-field-catalog').bounding_box()
    assert catalog['height']>=50,catalog
    assert page.locator('#meaning-pivot-slot').bounding_box()['height']>=370
    before=page.locator('.desk-object-page').bounding_box()
    page.get_by_role('tab',name='Source rows',exact=True).click()
    page.get_by_role('region',name='Source records',exact=True).wait_for()
    assert page.locator('.desk-object-page').bounding_box()==before
    page.get_by_role('tab',name='Query result',exact=True).click()
    page.get_by_role('button',name='Definition & source',exact=True).click()
    page.get_by_role('heading',name='Definition & provenance',exact=True).wait_for()
    assert page.locator('.desk-object-page').bounding_box()==before
