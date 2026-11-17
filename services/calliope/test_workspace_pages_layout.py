"""Account and review pages use real controllers/CSS with isolated API fixtures."""
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import app_review
import warehouse_theme

ROOT = Path(__file__).parent


@pytest.fixture
def workspace(monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    monkeypatch.setattr('auth.background_layer', lambda *a, **kw: '')
    monkeypatch.setattr('calliope_dreams.is_company_admin', lambda *a: True)
    session = {'identity':'admin@example.test','via':'password'}
    people = [dict(email='admin@example.test',display_name='Alex Admin',enabled=True,has_password=True,google_connected=True,groups=['Admins'],last_seen_at='2026-10-12T00:00:00Z'),dict(email='tech@example.test',display_name='Taylor Technician',enabled=False,has_password=True,google_connected=False,groups=['Field team'],last_seen_at=None)]
    profile = dict(email='admin@example.test',display_name='Alex Admin',can_administer=True,via='password',has_password=True,google_enabled=True,google_only=False,groups=[{'name':'Admins'}])
    requests = [dict(id='request-1',slug='visits',title='Field visit desk',requested_by='tech@example.test',user_message='Keep the equipment serial number visible while I add photos.',origin_context={'version':1,'runtime':'native'},latest_version=2,status='new',needs_attention=True,releases=[],review=dict(can_triage=True,can_confirm=False,is_requester=False,owner_status='new',revision=1)),dict(id='request-2',slug='sales',title='Sales dashboard',requested_by='admin@example.test',user_message='Compare this quarter with last year.',origin_context={'version':2,'runtime':'html'},latest_version=3,status='published',needs_attention=True,releases=[{'slug':'sales','name':'Sales dashboard','version':3,'notes':'Adds prior year comparison'}],review=dict(can_triage=False,can_confirm=True,is_requester=True,owner_status='working',revision=2,release_key='release-3'))]
    writes=[]
    with pw.sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1600,'height':1000})
        page.set_default_timeout(7000)
        page.emulate_media(reduced_motion='reduce')
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            url=urlsplit(r.request.url)
            if url.netloc!='workspace.test':r.abort();return
            path=url.path
            if r.request.method=='POST':writes.append((path,r.request.post_data_json))
            if path in ['/apps/improvements','/apps/visits/changes']:
                data={'slug':'visits'} if path.endswith('/changes') else {}
                r.fulfill(body=app_review.page(data,session['identity'],session=session,factory=lambda:None).body,content_type='text/html');return
            if path in ['/account','/account/setup']:
                setup=path.endswith('/setup')
                html=(ROOT/'calliope/accounts.html').read_text().replace('__CALLIOPE_HEADER__',warehouse_theme.workspace_header(None if setup else session,can_administer=not setup)).replace('__CALLIOPE_BACKGROUND__','').replace('__ACCOUNT_MODE__','setup' if setup else 'account').replace('__ACCOUNT_VERSION__','test')
                r.fulfill(body=html,content_type='text/html');return
            if path=='/api/app-improvements' or path.endswith('/changes'):
                r.fulfill(json={'requests':requests});return
            if '/replay' in path:
                r.fulfill(body='<html><body><h1>Captured field visit</h1><p>Unit AC-17 · photo draft</p></body></html>',content_type='text/html');return
            if path.endswith('/review'):
                body=r.request.post_data_json
                item=next(v for v in requests if v['id'] in path)
                item['status']=body.get('owner_status') or body['requester_status']
                r.fulfill(json={'requests':[item]});return
            if path.endswith('/scenarios'):
                r.fulfill(json={'id':'scenario-1'} if r.request.method=='POST' else {'scenarios':[{'id':'scenario-1','name':'Captured equipment visit','version':1,'request_id':'request-1','last_check':{'id':'check-1','version':1,'ready':True}}]});return
            if path.endswith('/checks'):
                r.fulfill(json={'id':'check-2','ready':True,'checks':[{'ok':True}],'captures':{},'definition_sha256':'abcd12345678','provenance':{'notice':'Captured fixture'}});return
            if path.startswith('/api/calliope/account/'):
                action=path.rsplit('/',1)[-1]
                if action=='people' and r.request.method=='POST':
                    body=r.request.post_data_json;people.append({**people[1],'email':body['email'],'display_name':body['display_name'],'enabled':True});r.fulfill(json={'email':body['email']});return
                values={'me':profile,'people':{'people':people},'groups':{'teams':[dict(id='admins',name='Admins',description='Instance administrators',protected=True,archived=False,dynamic_membership=False,members=['admin@example.test'],revision=1)]},'settings':dict(google_only=False,google_client_id='fixture-client',google_domain='',allowed_emails=['admin@example.test'],redirect_uri='http://workspace.test/auth/google/callback',google_enabled=True,has_google_secret=True,revision=1),'events':{'events':[dict(created_at='2026-10-12T00:00:00Z',actor='admin@example.test',action='account_created',email='tech@example.test')]}}
                r.fulfill(json=values.get(action,{}));return
            static=ROOT/path.lstrip('/')
            if path.startswith('/account/assets/'):
                static=ROOT/'calliope'/path.rsplit('/',1)[-1]
            if path in ['/api/apps/review.css','/api/apps/review.js']:
                static=ROOT/'calliope'/('app-'+path.rsplit('/',1)[-1])
            if static.is_file() and static.resolve().is_relative_to(ROOT):
                r.fulfill(body=static.read_bytes(),content_type=mimetypes.guess_type(static)[0] or 'text/plain');return
            if path.startswith('/api/') or path.startswith('/theme/'):
                r.fulfill(json={});return
            r.fulfill(status=404)
        page.route('**/*',route)
        yield page,writes
        assert not errors
        browser.close()


def test_review_selection_replay_and_existing_actions(workspace):
    page,writes=workspace
    page.goto('http://workspace.test/apps/improvements')
    page.locator('.request-item').first.wait_for()
    page.get_by_role('heading',name='Field visit desk',exact=True).wait_for()
    page.get_by_role('button',name='Phone',exact=True).click()
    assert page.locator('#review-preview').get_attribute('data-size')=='phone'
    page.get_by_role('combobox',name='Revision to preview').select_option('2')
    assert 'version=2' in page.locator('iframe').get_attribute('src')
    page.get_by_role('button',name='Check this revision',exact=True).click()
    page.get_by_text('Checks passed',exact=True).wait_for()
    assert writes[-1]==('/api/apps/visits/checks',{'version':2,'scenario_id':'scenario-1'})
    page.get_by_role('button',name='Working on it',exact=True).click()
    page.locator('#review-detail .status').filter(has_text='In progress').wait_for()
    assert any(body.get('owner_status')=='working' and body['revision']==1 for _,body in writes)
    page.get_by_role('searchbox',name='Find a request').fill('Sales')
    page.get_by_role('heading',name='Sales dashboard',exact=True).wait_for()
    assert page.locator('iframe').count()==0
    page.get_by_role('button',name='This fixed it',exact=True).click()
    page.locator('#review-detail .status').filter(has_text='Confirmed fixed').wait_for()
    assert any(body.get('requester_status')=='fixed' and body['release_key']=='release-3' for _,body in writes)
    page.get_by_role('searchbox',name='Find a request').fill('no match')
    page.get_by_role('heading',name='No requests in this view').wait_for()
    assert page.locator('.request-item').count()==0
    page.get_by_role('button',name='Show all requests').click()
    assert page.locator('.request-item').count()==2


def test_people_navigation_forms_and_public_setup(workspace):
    page,writes=workspace
    page.goto('http://workspace.test/account?view=people')
    page.get_by_text('Taylor Technician',exact=True).wait_for()
    page.get_by_role('searchbox',name='Find a person').fill('Taylor')
    assert page.locator('#people-rows tr').count()==1
    page.get_by_role('button',name='Add person',exact=True).click()
    page.get_by_role('textbox',name='Email',exact=True).fill('new@example.test')
    page.get_by_role('textbox',name='Display name',exact=True).fill('New Person')
    page.get_by_role('button',name='Create account',exact=True).click()
    page.locator('#person-dialog').wait_for(state='hidden')
    page.get_by_role('searchbox',name='Find a person').fill('New Person')
    page.get_by_text('New Person',exact=True).wait_for()
    assert writes[-1]==('/api/calliope/account/people',{'email':'new@example.test','display_name':'New Person'})
    page.get_by_role('link',name='Groups',exact=True).click()
    page.get_by_role('heading',name='Admins',exact=True).wait_for()
    page.get_by_role('link',name='Sign-in',exact=True).click()
    page.get_by_role('textbox',name='Client ID',exact=True).wait_for()
    page.get_by_role('link',name='Activity',exact=True).click()
    page.get_by_text('account created',exact=True).wait_for()
    page.go_back()
    page.get_by_role('heading',name='Sign-in settings.').wait_for()
    page.goto('http://workspace.test/account/setup#token=fixture-only')
    page.get_by_role('heading',name='Choose your password').wait_for()
    assert page.locator('.workspace-nav a').count()==0
    assert page.locator('.accounts-workspace').is_hidden()
    assert '#token' not in page.url


@pytest.mark.parametrize('route',['/account?view=people','/apps/improvements'])
@pytest.mark.parametrize('width',[390,900,1600,2400])
def test_responsive_geometry_and_shared_theme(workspace,route,width,tmp_path):
    page,_=workspace
    page.set_viewport_size({'width':width,'height':1000})
    page.goto('http://workspace.test'+route)
    page.locator('#people-rows tr' if route.startswith('/account') else '.request-item').first.wait_for()
    page.evaluate('() => Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))')
    for mode in ['dark','light']:
        if mode=='light':page.get_by_role('button',name='Switch to light mode',exact=True).click()
        result=page.evaluate('''() => {
          const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width}};
          return {width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,main:rect('body>main'),rail:rect('.workspace-rail'),system:[...document.querySelectorAll('.workspace-nav a')].find(a=>a.textContent==='System').getBoundingClientRect().width,mode:document.documentElement.dataset.warehouseColorMode};
        }''')
        assert not result['overflow'],result
        assert page.evaluate('getComputedStyle(document.documentElement).scrollbarWidth')=='thin'
        assert result['main']['right']<=width and result['main']['left']>=0,result
        assert result['rail']['width']==width and result['system']>0,result
        assert result['mode']==mode,result
        if route.startswith('/apps') and width>900:
            geometry=page.evaluate('''() => {const l=document.querySelector('#review-list'),d=document.querySelector('#review-detail');return {left:l.getBoundingClientRect().right,right:d.getBoundingClientRect().left,bottom:d.getBoundingClientRect().bottom,scroll:getComputedStyle(d).overflowY}}''')
            assert geometry['left']<geometry['right'] and geometry['bottom']<=1000,geometry
            assert geometry['scroll']=='auto'
        page.screenshot(path=str(tmp_path/f'{"people" if route.startswith("/account") else "review"}-{width}-{mode}.png'))


@pytest.mark.parametrize('width',[390,1600])
def test_account_forms_and_dialog_fit_the_same_shell(workspace,width,tmp_path):
    page,_=workspace
    page.set_viewport_size({'width':width,'height':1000})
    for name,ready in [('me','#profile-form'),('settings','#google-status'),('groups','.group-card'),('events','#event-rows tr')]:
        page.goto('http://workspace.test/account?view='+name)
        page.locator(ready).first.wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),name
        page.screenshot(path=str(tmp_path/f'account-{name}-{width}.png'))
    page.goto('http://workspace.test/account?view=people')
    page.locator('#people-rows tr').first.wait_for()
    page.get_by_role('button',name='Add person',exact=True).click()
    rect=page.locator('#person-dialog').bounding_box()
    assert rect['x']>=0 and rect['x']+rect['width']<=width
    page.screenshot(path=str(tmp_path/f'account-add-person-{width}.png'))
