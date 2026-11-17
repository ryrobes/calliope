from test_zen_chat_layout import zen_page  # noqa: F401

OWNER='viewer@example.com'
PERSON='michael@example.com'


def test_share_in_zen_members_managers_and_explicit_mention_invite(zen_page):
    page=zen_page
    audience={'owner':{'email':OWNER,'display_name':'Viewer'},'people':[], 'teams':[],
              'private':True,'participants':[{'email':OWNER}],'participant_count':1,'summary':'Only you'}
    access={'session':{'id':'notebook','access_revision':1},'grants':{'people':[],'teams':[]},'teams':[],
            'audience':audience,'summary':'Only you','private':True,'manage_role':'owner'}
    session={'id':'notebook','title':'Shared analysis','owner_email':OWNER,'access_role':'owner'}
    page.route('**/api/calliope/sessions',lambda r:r.fulfill(json={'sessions':[session]}))
    page.route('**/api/calliope/sessions/notebook',lambda r:r.fulfill(json={'session':session,'turns':[], 'surfaces':[], 'audience':audience,'agent_work':{'pending':[],'queued_messages':0}}))
    saved=[]
    def sharing(r):
        if r.request.method=='PUT':
            saved.append(r.request.post_data_json)
            result={**access,'private':False,'summary':'Shared with Michael',
                    'audience':{**audience,'private':False,'people':[{'email':PERSON,'role':'manager'}]}}
            r.fulfill(json=result)
        else:r.fulfill(json=access)
    page.route('**/sessions/notebook/access',sharing)
    page.route('**/team-people?*',lambda r:r.fulfill(json={'people':[{'email':PERSON,'display_name':'Michael'}]}))
    page.evaluate('qa.setupEvents();qa.state.surfaces=[];qa.renderChat(true);qa.renderSessionAccess()')
    share=page.locator('#share-session')
    assert share.is_visible()
    # Share belongs to the thread it shares: beside the title, not adrift at the
    # far edge under the activity animation.
    title=page.locator('#chat-topic-title')
    gap=share.bounding_box()['x'] - (title.bounding_box()['x'] + title.bounding_box()['width'])
    assert 0 <= gap < 60, gap
    assert share.locator('svg').is_visible()
    share.click()
    assert page.locator('#chat-topic').get_attribute('open') is None
    # Everyone brought in is a member; the owner may also make them a manager.
    assert page.locator('[data-share-manager]').count()==0
    page.locator('[data-share-person]').check()
    page.locator('[data-share-manager]').check()
    page.locator('#session-share-save').click()
    page.wait_for_function('!document.querySelector("#session-share-dialog").open')
    assert saved[0]['people']==[PERSON]
    assert saved[0]['managers']==[PERSON]
    assert 'participants' not in saved[0]
    # Mentioning a person is never itself a grant; it only opens the editable invite.
    page.wait_for_timeout(100)
    page.evaluate("qa.state.audience=null;qa.els.input.value='Discuss with @michael@example.com';qa.renderMentionInvites()")
    button=page.locator('[data-invite-person]')
    assert button.is_visible(), page.evaluate('({current:qa.state.current,value:qa.composerValue(),intake:qa.state.intake,html:document.querySelector("#mention-invites").outerHTML,composerHidden:qa.els.composer.hidden})')
    assert len(saved)==1
    button.click()
    page.locator('[data-share-manager]').wait_for()
    assert page.locator('[data-share-person]').is_checked()
    assert not page.locator('[data-share-manager]').is_checked()
    page.screenshot(path='/tmp/thread-sharing-desktop.png')
    page.set_viewport_size({'width':430,'height':850})
    assert page.locator('[data-share-manager]').is_visible()
    page.screenshot(path='/tmp/thread-sharing-mobile.png')


def test_members_talk_managers_manage_and_read_only_chats_hide_input(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.state.current.access_role='member';qa.state.current.read_only=false;qa.state.current.room=true;
      qa.state.audience={private:false,people:[{email:'michael@example.com'}],participants:[{email:'viewer@example.com'},{email:'michael@example.com'}],summary:'Shared with 1 person'};
      qa.renderChat(true);qa.renderSessionAccess();
    }''')
    assert page.locator('#composer').is_visible()
    assert page.locator('#share-session').is_hidden()
    assert page.locator('#archive-session').is_hidden()
    # A room shows who is in it; there is no separate "Room" badge.
    assert page.locator('#session-access-meta .participant-stack').is_visible()
    assert page.locator('#session-access-meta .room-pill').count() == 0
    # A member who mentions someone outside the room can only suggest them.
    page.evaluate("qa.els.input.value='Ask @nina@example.com';qa.renderMentionInvites()")
    assert page.locator('[data-invite-person]').count()==0
    assert 'Only its owner can bring people in' in page.locator('#mention-invites').inner_text()
    page.evaluate("qa.state.current.access_role='manager';qa.renderSessionAccess()")
    assert page.locator('#share-session').is_visible()
    assert page.locator('#archive-session').get_attribute('hidden') is None
    assert 'you manage it' in page.locator('#session-access-meta').inner_text()
    page.evaluate("qa.state.current.access_role='viewer';qa.state.current.read_only=true;qa.renderSessionAccess()")
    assert page.locator('#composer').is_hidden()
    assert 'read-only here' in page.locator('#read-only-notice').inner_text()
