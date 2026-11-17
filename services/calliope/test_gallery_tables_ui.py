"""Exercise Tables inside the actual Gallery shell on desktop and narrow screens."""
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest
import server
import calliope


def test_table_gallery_navigation_preview_filter_and_handoff(monkeypatch, tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    monkeypatch.setattr(calliope,'is_enabled',lambda:True)
    html=server._landing_html([], 'admin@example.com', can_administer=True)
    root=Path(__file__).resolve().parent
    history=[dict(run_id=i,captured_at=f'2026-09-{i:02}T12:00:00Z',rows=200000+i*1000,sampled=True,sample_rows=1000,column_count=3) for i in range(14,6,-1)]
    base=dict(ref='table:123',schema='salesforce',name='person',qualified_name='salesforce.person',kind='Table',relkind='r',description='People, enrollment status and the source of each inquiry.',column_count=3,rows=214000,row_source='Catalog observation',observed_at=history[0]['captured_at'],history=history,is_partition=False,analyzed_at='2026-09-14T10:00:00Z',size_bytes=58332163,unavailable=[],catalog_description=None,
     columns=[dict(name=n,type=t,nullable=n!='id',primary_key=n=='id',description=d,planner_null_frac=0,planner_distinct=1) for n,t,d in [('id','uuid','Person identifier'),('status','text','Enrollment status'),('source','text','Inquiry source')]],
     profiles=[dict(name='status',null_frac=.015,distinct=5,method='sampled',min='Active',max='Withdrawn',examples=['Active','Applied','Enrolled'],quantiles=None)],
     drift=dict(comparable=True,changes=[dict(column='status',field='Null fraction',before='0.002',after='0.015')],total=1),
     relationships=[dict(source='salesforce.person',target='salesforce.campaign',definition='FOREIGN KEY (campaign_id) REFERENCES salesforce.campaign(id)')],
     business_context=[dict(name='Enrollment funnel',status='approved',updated_at='2026-09-13T12:00:00Z',summary='The person grain supports inquiry-to-enrollment conversion and source comparisons.',memberships=[dict(role='event_stream',confidence=.92,rationale='One person follows the funnel from initial inquiry to enrollment.')],questions=['Confirm the treatment of re-enrollments.'])],acceleration=dict(last_refresh_at='2026-09-14T12:05:00Z',parquet_rows=214000,drift_rows=12,shadow_heap_dirty=True))
    base['business_assessments']=[dict(graph_id='test',name='Enrollment funnel',status='accepted',lifecycle='canonical',updated_at='2026-09-13T12:00:00Z',role='event_stream',confidence=.92,rationale='One person follows the funnel from initial inquiry to enrollment.')]
    tables=[base,dict(base,ref='table:124',name='campaign',qualified_name='salesforce.campaign',description='Campaign attribution and spend.',rows=1800),dict(base,ref='table:125',schema='calliope',name='tiktok_campaign_dim',qualified_name='calliope.tiktok_campaign_dim',rows=2,description='TikTok campaign dimensions from the managed ETL collector.',history=[],row_source='Planner estimate',observed_at=None)]
    with sync_playwright() as pw:
     b=pw.chromium.launch(headless=True);page=b.new_page(viewport={'width':1440,'height':1080});errors=[];asks=[]
     page.on('pageerror',lambda e:errors.append(str(e)))
     def route(r):
      u=urlsplit(r.request.url)
      if u.path=='/gallery':r.fulfill(body=html,content_type='text/html');return
      if u.path.startswith('/api/calliope/gallery/tables'):
       if u.path.endswith('/ask'):asks.append(r.request.post_data_json);r.fulfill(json={'url':'/calliope?session=test'});return
       if u.path.endswith('/sample'):r.fulfill(json={'columns':['id','status','source'],'rows':[{'id':'abc-123','status':'Applied','source':'Paid search'},{'id':'abc-456','status':'Enrolled','source':None}],'note':'Up to 8 rows · first 20 columns · cells capped at 256 characters. Unordered preview, not a representative sample.'});return
       params=parse_qs(u.query);ref=params.get('ref',[None])[0];filtered=[t for t in tables if (not params.get('schema') or t['schema']==params['schema'][0]) and (not params.get('q') or params['q'][0].lower() in t['qualified_name'].lower())];r.fulfill(json=next(t for t in tables if t['ref']==ref) if ref else {'tables':filtered,'total':len(filtered),'schemas':['calliope','salesforce'],'next_offset':None,'unavailable':[]});return
      if u.path.startswith('/api/'):
       r.fulfill(json={'metrics':[],'items':[],'home':[],'jobs':[]});return
      if u.path=='/calliope':r.fulfill(body='New table chat',content_type='text/html');return
      p=root/u.path.lstrip('/')
      if p.is_file() and p.is_relative_to(root):r.fulfill(body=p.read_bytes(),content_type=mimetypes.guess_type(p)[0] or 'application/octet-stream');return
      r.fulfill(status=204)
     page.route('**/*',route)
     page.goto('http://gallery.test/gallery?view=data&inventory=tables',wait_until='networkidle')
     page.locator('.table-card').first.wait_for();assert page.locator('.table-card').count()==3
     assert page.locator('#gallery-jobs').is_hidden() and page.locator('#artifact-browser').is_hidden()
     assert page.locator('#gallery-kicker').text_content()=='Data · Tables'
     assert 'NaN' not in page.locator('.table-card svg path').first.get_attribute('d');page.screenshot(path=str(tmp_path/'tables.png'),full_page=True)
     assert 'One person follows' in page.locator('.table-card .metric-card-description').first.inner_text()
     page.locator('[data-table-open]').first.click();page.locator('.table-preview table').wait_for()
     assert '92.0% recorded confidence' in page.locator('.table-assessment-note').inner_text()
     assert 'Saved business assessment' in page.locator('.table-assessment-note').inner_text()
     assert 'Table comment:' in page.locator('.table-dialog').inner_text()
     page.screenshot(path=str(tmp_path/'table-detail.png'))
     page.get_by_role('button',name='Close',exact=True).click()
     page.locator('[data-data-view="jobs"]').click();assert page.locator('#gallery-tables').is_hidden()
     page.locator('[data-data-view="tables"]').click();assert page.locator('#gallery-jobs').is_hidden()
     page.get_by_role('tab',name='Artifacts',exact=True).click();assert page.locator('.gallery-data-nav').is_hidden()
     page.get_by_role('tab',name='Data',exact=True).click();assert page.locator('#gallery-tables').is_visible()
     page.get_by_label('Table schema',exact=True).select_option('calliope');page.wait_for_function("document.querySelectorAll('.table-card').length===1")
     page.get_by_label('Table schema',exact=True).select_option('');page.locator('#q').fill('person');page.wait_for_function("document.querySelectorAll('.table-card').length===1")
     page.locator('#q').fill('');page.wait_for_function("document.querySelectorAll('.table-card').length===3");page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(tmp_path/'tables-mobile.png'),full_page=True)
     assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
     page.locator('[data-table-open]').first.click();page.locator('.table-preview table').wait_for();page.screenshot(path=str(tmp_path/'table-mobile-detail.png'))
     assert page.locator('.table-dialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth')
     page.locator('.table-dialog textarea').fill('Are those new null values a problem?');page.locator('.table-dialog [data-table-ask]').click();page.wait_for_url('**/calliope?session=test')
     assert asks[0]=={'ref':'table:123','question':'Are those new null values a problem?'}
     page.goto('http://gallery.test/gallery?view=data&inventory=tables',wait_until='networkidle');page.locator('[data-table-ask]').first.focus();page.locator('[data-table-ask]').first.click();page.wait_for_url('**/calliope?session=test');assert len(asks)==2
     assert not errors,errors
     print(json.dumps({'desktop_mobile':'passed','tabs_search_schema_filter':'passed','detail_preview':'passed','ask_handoff':'passed','page_errors':errors}))
     b.close()
