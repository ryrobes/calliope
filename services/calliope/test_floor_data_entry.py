"""A fixed-size Floor action can open an empty Data notebook without inference."""
import base64
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect
from test_floor_veil import floor_page, ORIGIN, ROOT  # noqa:F401
from test_floor_tooltips import load_floor
from test_dashboard_mode_ui import BOOKS


def open_intake(page,held):
    load_floor(page,held)
    page.locator('#floorIntakeOpen').click()
    page.locator('#floorIntakeEditor .cm-content').wait_for()


@pytest.mark.parametrize('width',[390,1500])
def test_action_geometry_empty_enter_and_attachments(floor_page,width):
    page,held=floor_page
    page.emulate_media(reduced_motion='reduce')
    open_intake(page,held)
    page.set_viewport_size({'width':width,'height':950})
    send=page.locator('#floorIntakeSend');editor=page.locator('#floorIntakeEditor .cm-content')
    expect(send).to_have_text('→');expect(send).to_have_attribute('aria-label','Explore data');expect(send).to_be_enabled()
    rects=lambda:[page.locator(selector).bounding_box() for selector in ('#floorIntakeForm','.floor-intake-actions','#floorIntakeSend')]
    before=rects()
    editor.fill('Hello')
    expect(send).to_have_text('↑');expect(send).to_have_attribute('aria-label','Send message')
    assert rects()==before
    editor.fill('   ');expect(send).to_have_text('→')
    assert rects()==before
    editor.press('Enter')
    assert page.url==ORIGIN+'/floor'
    assert page.evaluate("sessionStorage.getItem('calliope.floorIntake.v1')") is None
    # A file without message text remains a draft, not a Data launch.
    page.locator('#floorIntakeImages').set_input_files({'name':'pixel.png','mimeType':'image/png',
      'buffer':base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY9sAAAAASUVORK5CYII=')})
    expect(send).to_have_text('↑');expect(send).to_be_disabled()
    page.get_by_role('button',name='Remove pixel.png',exact=True).click()
    expect(send).to_have_text('→');expect(send).to_be_enabled()
    send.hover();expect(page.locator('#floorTooltip')).to_have_text('Explore data')


def test_playbook_keeps_send_mode_without_draft_text(floor_page):
    page,held=floor_page
    page.route('**/api/calliope/playbooks?*',lambda r:r.fulfill(json=BOOKS))
    open_intake(page,held)
    page.locator('#floorIntakePlaybook').click();page.locator('#playbook-picker .playbook-picker-item').first.click()
    expect(page.locator('#floorIntakeSend')).to_have_text('↑')
    expect(page.locator('#floorIntakeSend')).to_be_disabled()


@pytest.mark.parametrize('entry',['data','message'])
def test_floor_launch_creates_exact_session_and_data_never_sends(floor_page,entry):
    page,held=floor_page;page.set_default_timeout(12000)
    errors=[];calls=[];page.on('pageerror',lambda e:errors.append(str(e)))
    open_intake(page,held)
    page.locator('#floorIntakeTeam').click()  # Team is opt-in; an explicit choice is preserved.
    new={'id':'new-data-notebook','title':'New notebook','owner_email':'viewer@example.com','agent_key':'calliope'}
    old={**new,'id':'remembered-old'}
    def api(r):
        path=urlsplit(r.request.url).path;method=r.request.method;body=r.request.post_data_json if r.request.post_data else None
        calls.append((method,path,body))
        if path=='/api/calliope/config':value={'healthy':True,'viewer':{'email':'viewer@example.com'},'specialists':[]}
        elif path=='/api/calliope/sessions':value={'session':new} if method=='POST' else {'sessions':[old]}
        elif path=='/api/calliope/sessions/new-data-notebook':
            if method=='PATCH':new.update(body)
            value={'session':new,'turns':[],'surfaces':[]}
        elif path.endswith('/turn'):
            r.fulfill(status=400,json={'error':{'message':'Stopped before inference by test'}});return
        elif path.endswith('/data/search'):value={'items':[],'areas':[]}
        elif path.endswith('/data/drafts'):value={'drafts':[]}
        else:value={}
        r.fulfill(json=value)
    page.route('**/api/**',api)
    page.route('**/calliope?*',lambda r:r.fulfill(body=re.sub(r'__CALLIOPE_[A-Z_]+__','',(ROOT/'calliope/index.html').read_text()),content_type='text/html'))
    if entry=='message':page.locator('#floorIntakeEditor .cm-content').fill('Test first message')
    page.locator('#floorIntakeSend').click()
    if entry=='data':
        expect(page.locator('body')).to_have_attribute('data-workspace-view','data')
        search=page.frame_locator('#data-panel-frame').get_by_role('searchbox',name='Find data or ask a question')
        search.wait_for()
        assert search.evaluate('(e)=>e===e.ownerDocument.activeElement')
        assert not [c for c in calls if c[1].endswith('/turn')]
        expect(page.locator('#workspace-tab-data')).to_have_attribute('aria-selected','true')
        assert page.locator('#messages .chat-message').count()==0
        page.wait_for_function("!document.body.classList.contains('boot-veil')")
        page.screenshot(path='/tmp/floor-data-entry.png')
    else:
        page.wait_for_function("document.body.textContent.includes('Stopped before inference by test')")
        sends=[c for c in calls if c[1].endswith('/turn')]
        assert len(sends)==1 and sends[0][2]['message']=='Test first message'
    creates=[c for c in calls if c[:2]==('POST','/api/calliope/sessions')]
    assert len(creates)==1 and creates[0][2]['agent_key']=='calliope'
    assert new['team_mode'] is True and new['authoring_mode']=='html'
    assert not [c for c in calls if c[1]=='/api/calliope/sessions/remembered-old']
    assert page.evaluate("sessionStorage.getItem('calliope.floorIntake.v1')") is None
    page.reload();page.locator('#workspace-tab-chat').wait_for()
    assert len([c for c in calls if c[:2]==('POST','/api/calliope/sessions')])==1
    assert len([c for c in calls if c[1].endswith('/turn')])==(0 if entry=='data' else 1)
    assert not errors
