"""The retired Dashboard control is now a versioned Playbook reference picker."""
import pytest
from test_zen_chat_layout import zen_page

BOOK_ID='10000000-0000-4000-8000-000000000001'
BOOKS={'playbooks':[
    {'is_owner':True,'playbook':{'id':BOOK_ID,'version':3,'title':'Composition-led dashboard','synopsis':'Design first, then build from validated queries.','approved':False}},
    {'is_owner':False,'playbook':{'id':'10000000-0000-4000-8000-000000000002','version':2,'title':'Weekly review','synopsis':'Review operations.','approved':True}},
]}

@pytest.mark.parametrize('width', [1500,390])
def test_playbook_picker_attaches_versioned_ref_without_editing_or_sending(zen_page,width):
    page=zen_page; page.set_viewport_size({'width':width,'height':900})
    if width < 700: page.evaluate("document.body.classList.add('mobile-chat-open')")
    sent=[]
    page.on('request',lambda req:sent.append(req.url) if req.method=='POST' else None)
    page.route('**/api/calliope/playbooks?*',lambda route:route.fulfill(json=BOOKS))
    page.evaluate("qa.setupEvents();qa.state.surfaces=[];qa.state.current.authoring_mode='dashboard';qa.initializeComposerEditor();qa.renderChat();qa.renderComposerPlaybook();qa.els.input.disabled=false;qa.state.composerEditor.setValue('Build September report');")
    assert page.locator('#dashboard-mode-toggle').count()==0
    page.locator('#composer-playbook-open').click()
    modal=page.locator('#playbook-picker')
    modal.locator('.playbook-picker-item').first.wait_for()
    assert modal.locator('.playbook-picker-item').count()==2
    assert 'Draft · Yours' in modal.inner_text()
    modal.get_by_role('searchbox').fill('composition')
    assert modal.locator('.playbook-picker-item').count()==1
    box=modal.bounding_box(); assert box['x']>=0 and box['x']+box['width']<=width
    page.screenshot(path=f'/tmp/calliope-playbook-picker-{width}.png')
    modal.locator('.playbook-picker-item').click()
    assert not modal.is_visible()
    refs=page.evaluate('qa.state.composerPlaybooks')
    assert refs==[{'kind':'playbook','ref_id':BOOK_ID+'@3','label':'Composition-led dashboard · v3'}]
    assert page.evaluate('qa.composerValue()')=='Build September report'
    assert 'Composition-led dashboard · v3' in page.locator('#composer-playbooks').inner_text()
    assert not any('/turns' in url for url in sent)
    # Selecting the same version twice does not add duplicate references.
    page.locator('#composer-playbook-open').click()
    modal.locator('.playbook-picker-item').first.click()
    assert page.evaluate('qa.composerValue()')=='Build September report'
    assert page.locator('#composer-playbooks .playbook-selection').count()==1
    page.locator('#composer-playbooks button').click()
    assert page.evaluate('qa.state.composerPlaybooks')==[]
    assert page.evaluate('qa.composerValue()')=='Build September report'


def test_new_chat_is_unpinned_and_picker_failure_can_retry(zen_page):
    page=zen_page
    page.evaluate('qa.setupEvents();qa.openIntake()')
    assert page.evaluate('qa.state.intake.designVersionId') is None
    assert 'no design' in page.locator('#zen-design-button').inner_text()
    page.route('**/api/calliope/playbooks?*',lambda r:r.fulfill(status=503,json={}))
    page.locator('#composer-playbook-open').click()
    modal=page.locator('#playbook-picker');modal.get_by_role('button',name='Try again').wait_for()
    page.route('**/api/calliope/playbooks?*',lambda r:r.fulfill(json={'playbooks':[]}))
    modal.get_by_role('button',name='Try again').click()
    page.wait_for_function("document.querySelector('#playbook-picker [role=status]').textContent.includes('No playbooks yet')")
    page.keyboard.press('Escape')
    assert not modal.is_visible()
    assert page.evaluate('Boolean(qa.state.intake)')


def test_first_message_adds_ref_only_on_send_and_restores_clean_draft_on_failure(zen_page):
    page=zen_page
    session={'id':'20000000-0000-4000-8000-000000000001','title':'New notebook',
             'owner_email':'viewer@example.com','agent_key':'calliope','authoring_mode':'html','team_mode':True}
    sent=[]
    page.route('**/api/calliope/playbooks?*',lambda r:r.fulfill(json=BOOKS))
    page.route('**/api/calliope/sessions',lambda r:r.fulfill(json={'session':session} if r.request.method=='POST' else {'sessions':[session]}))
    page.route('**/api/calliope/sessions/'+session['id'],lambda r:r.fulfill(json={'session':session,'turns':[],'surfaces':[]}))
    def reject(route):
        sent.append(route.request.post_data_json)
        route.fulfill(status=400,json={'error':{'code':'TEST_REJECTED','message':'Try again'}})
    page.route('**/api/calliope/sessions/*/turn',reject)
    page.evaluate("qa.setupEvents();qa.state.surfaces=[];qa.openIntake();qa.initializeComposerEditor();qa.state.composerEditor.setValue('Build September report');")
    page.locator('#composer-playbook-open').click()
    page.locator('#playbook-picker .playbook-picker-item').first.click()
    assert page.evaluate('qa.composerValue()')=='Build September report'
    assert sent==[]
    page.evaluate('qa.beginIntakeTurn({onTurnStarted:()=>window.sentDraftState={text:qa.composerValue(),playbooks:qa.state.composerPlaybooks.length}})')
    assert page.evaluate('sentDraftState')=={'text':'','playbooks':0}
    assert len(sent)==1
    assert sent[0]['message']=='Build September report\n\n[[playbook:'+BOOK_ID+'@3|Composition-led dashboard · v3]]'
    assert sent[0]['object_refs']==[{'kind':'playbook','ref_id':BOOK_ID+'@3'}]
    assert page.evaluate('qa.composerValue()')=='Build September report'
    assert page.locator('#composer-playbooks .playbook-selection').count()==1
    # The retry sends the same reference once, without polluting the restored editor.
    page.evaluate('qa.sendTurn()')
    assert len(sent)==2 and sent[1]['message']==sent[0]['message']
    assert sent[1]['object_refs']==sent[0]['object_refs']
    assert page.evaluate('qa.composerValue()')=='Build September report'
