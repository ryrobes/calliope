"""Browser coverage for canonical questions in chat and directly on Floor."""
from test_zen_chat_layout import zen_page
from test_floor_veil import floor_page, ORIGIN, PAYLOAD


def test_legacy_forms_do_not_reappear_and_unrelated_messages_do_not_close(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.turns.push({id:'reply',ordinal:2,status:'complete',turn_kind:'chat',user_message:'Here are my answers for “Choose a direction”: guessed text'});
      qa.renderChat();
    }''')
    assert page.locator('#chat-decisions form').count()==1
    page.evaluate('''() => {delete qa.state.surfaces[0].payload.request_state;qa.renderChat();qa.renderStage()}''')
    assert page.locator('#chat-decisions').is_hidden()
    assert page.locator('#stage [data-stage-input-form]').count()==0


def test_chat_answer_uses_receipt_and_no_browser_turn(zen_page):
    page=zen_page
    requests=[]
    def answer(route):
        body=route.request.post_data_json
        requests.append(body)
        route.fulfill(json={'id':'question','status':'answered','revision':2,'answers':body['answers'],'delivery_status':'queued'})
    page.route('**/api/calliope/input-requests/question/answer',answer)
    page.evaluate('qa.setupEvents()')
    page.locator('#chat-decisions textarea').fill('For executives')
    page.locator('#chat-decisions label:has(input[value=leaders])').click()
    page.locator('#chat-decisions button[type=submit]').last.click()
    page.wait_for_function("qa.state.surfaces[0].payload.request_state.status==='answered'")
    assert requests==[{'answers':{'audience':'leaders','notes':'For executives'},'revision':1}]
    assert page.locator('#chat-decisions').is_hidden()
    assert page.evaluate('qa.state.turns.length')==1  # continuation belongs to the server


def test_failed_answer_keeps_draft_and_cancel_is_durable(zen_page):
    page=zen_page
    page.route('**/api/calliope/input-requests/question/answer',lambda r:r.fulfill(status=409,json={'error':{'message':'Already answered elsewhere'}}))
    page.route('**/api/calliope/input-requests/question/cancel',lambda r:r.fulfill(json={'id':'question','status':'cancelled','revision':2,'answers':{}}))
    page.evaluate('qa.setupEvents()')
    page.locator('#chat-decisions textarea').fill('Keep my draft')
    page.locator('#chat-decisions label:has(input[value=leaders])').click()
    page.locator('#chat-decisions button[type=submit]').last.click()
    page.wait_for_function("!document.querySelector('#chat-decisions form').dataset.inputPending")
    assert page.locator('#chat-decisions textarea').input_value()=='Keep my draft'
    page.locator('#chat-decisions [data-input-cancel]').click()
    page.wait_for_function("qa.state.surfaces[0].payload.request_state.status==='cancelled'")
    assert page.locator('#chat-decisions').is_hidden()


def test_floor_question_can_be_answered_without_entering_chat(floor_page):
    page,held=floor_page
    qid='00000000-0000-4000-8000-000000000001'
    card={'item_id':'input_request:'+qid,'item_kind':'input_request','column_state':'awaiting_you',
          'display_title':'Who is this for?','agents':['calliope'],'owner_email':'viewer@example.com',
          'action_prompt':'Choose an audience','detail_state':'open','idle_seconds':0}
    payload={**PAYLOAD,'cards':[card],'lanes':[{'column_state':'awaiting_you','pieces':1,'matched':1}]}
    question={'id':qid,'status':'open','revision':1,'can_answer':True,'answers':{},'request':{
        'title':'Who is this for?','prompt':'Choose an audience','fields':[
            {'key':'audience','label':'Audience','type':'choice','required':True,'options':[{'value':'team','label':'The team'},{'value':'leaders','label':'Leadership'}]},
            {'key':'share','label':'Share it','type':'boolean'}]}}
    posted=[]
    def route(r):
        if r.request.method=='POST':
            posted.append(r.request.post_data_json)
            r.fulfill(json={**question,'status':'answered','revision':2,'answers':posted[-1]['answers']})
        else:r.fulfill(json=question)
    page.route('**/api/calliope/input-requests/**',route)
    page.goto(ORIGIN+'/floor',wait_until='domcontentloaded')
    page.wait_for_selector('#floorInitialVeil[data-ready="true"]')
    for r in held[:]:r.fulfill(json=payload)
    held.clear()
    page.locator('[data-piece-action]').first.click()
    form=page.locator('.floor-input-question')
    form.wait_for()
    form.locator('input[value=team]').check()
    page.screenshot(path='/tmp/floor-input-question.png')
    form.locator('button.primary').click()
    page.wait_for_function("!document.querySelector('#drawerBackdrop').classList.contains('open')")
    assert posted==[{'answers':{'audience':'team','share':False},'revision':1}]
    assert page.url==ORIGIN+'/floor'


def test_replayed_stream_cannot_reopen_a_newer_answer(zen_page):
    page=zen_page
    page.evaluate('''() => {
      const stale=JSON.parse(JSON.stringify(qa.state.surfaces[0]));
      const run={status:'running',pending:{...qa.state.turns[0],status:'running'},surfaces:[stale]};
      qa.state.activeRuns.set('notebook',run);
      qa.rememberInputRequestReceipt('question',{id:'question',status:'answered',revision:2,answers:{audience:'leaders'}});
      if(run.surfaces[0].payload.request_state.status!=='answered')throw Error('Run cache retained an open question');
      // An already in-flight GET and a replayed stream both carry revision 1.
      const replay=qa.mergeInputRequestStates([stale],qa.state.surfaces);
      if(replay[0].payload.request_state.status!=='answered')throw Error('Stream reopened the question');
      run.surfaces=[stale];
      qa.state.surfaces=qa.activeRunProjection('notebook',[],qa.state.surfaces).surfaces;
      qa.renderChatDecisions();
    }''')
    assert page.locator('#chat-decisions').is_hidden()


def test_required_panel_queues_questions_preserves_drafts_and_gates_send(zen_page):
    page=zen_page;turns=[];answers=[]
    page.route('**/api/calliope/sessions/*/turn',lambda r:(turns.append(r.request),r.fulfill(json={})))
    def resolve(r):
        answers.append(r.request.post_data_json)
        r.fulfill(json={'id':'question','status':'cancelled','revision':2,'answers':{}})
    page.route('**/api/calliope/input-requests/question/cancel',resolve)
    page.evaluate('''() => {
      qa.setupEvents();qa.state.surfaces.push({...structuredClone(qa.state.surfaces[0]),id:'second',title:'Another question',
        payload:{...structuredClone(qa.state.surfaces[0].payload),title:'Another question',request_state:{id:'second',status:'open',revision:1}}});
      qa.renderChat();qa.renderStage();
    }''')
    panel=page.locator('#chat-decisions')
    assert panel.locator('form').count()==1
    assert 'Request 1 of 2' in panel.text_content()
    assert page.locator('#composer').is_hidden()
    assert page.locator('#stage [data-stage-input-form]').count()==0
    panel.locator('textarea').fill('Keep this draft')
    page.evaluate('''() => {
      window.keptForm=document.querySelector('#chat-decisions form');
      qa.state.turns.push({id:'late',turn_kind:'agent_message',status:'complete',user_message:'An agent update',assistant_message:''});
      qa.renderChat();
    }''')
    assert page.evaluate("keptForm===document.querySelector('#chat-decisions form')")
    assert panel.locator('textarea').input_value()=='Keep this draft'
    page.evaluate("qa.sendTurn({messageOverride:'Bypass the question'})")
    assert not turns
    page.keyboard.press('Escape')
    assert panel.is_visible() and page.locator('#composer').is_hidden()
    panel.get_by_role('button',name='Cancel request',exact=True).click()
    page.wait_for_function("document.querySelector('#chat-decisions').dataset.activeRequest==='second'")
    assert 'Request 2 of 2' in panel.text_content()
    assert page.locator('#composer').is_hidden()
    assert len(answers)==1
    # A server receipt arriving through another tab releases the final blocker.
    page.evaluate("qa.rememberInputRequestReceipt('second',{id:'second',status:'answered',revision:2,answers:{audience:'leaders'}});qa.renderChat()")
    assert panel.is_hidden() and page.locator('#composer').is_visible()


def test_single_choice_needs_submit_optional_skip_and_panel_layout(zen_page):
    page=zen_page;posted=[]
    page.route('**/api/calliope/input-requests/question/cancel',lambda r:(posted.append(r.request.post_data_json),r.fulfill(json={'id':'question','status':'cancelled','revision':2,'answers':{}})))
    page.evaluate('''() => {
      qa.setupEvents();const question=qa.state.surfaces[0];question.payload.blocking=false;
      question.payload.fields=question.payload.fields.slice(0,1);qa.renderChat();
    }''')
    panel=page.locator('#chat-decisions')
    assert page.locator('#composer').is_visible()
    panel.locator('label:has(input[value=leaders])').click()
    assert not posted
    assert panel.get_by_role('button',name='Submit answers').is_visible()
    for zen,width,height in [(True,1500,900),(False,1500,900),(True,390,700),(True,1500,500)]:
        page.set_viewport_size({'width':width,'height':height})
        page.evaluate('''zen=>{qa.state.zen=zen;document.body.classList.toggle('zen',zen);document.body.classList.add('mobile-chat-open');qa.renderChat()}''',zen)
        page.wait_for_timeout(250)
        box=panel.bounding_box()
        assert box and box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1
        assert panel.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
        assert panel.locator('input[value=leaders]').is_checked()
        panel.screenshot(path=f'/tmp/decision-panel-{zen}-{width}-{height}.png')
    panel.get_by_role('button',name='Skip',exact=True).click()
    page.wait_for_function("document.querySelector('#chat-decisions').hidden")
    assert len(posted)==1 and page.locator('#composer').is_visible()


def test_minimized_blocker_keeps_draft_and_gate_but_reveals_stage(zen_page):
    page=zen_page;posted=[]
    page.on('request', lambda r: posted.append(r.url) if r.method=='POST' else None)
    page.evaluate('qa.setupEvents()')
    panel=page.locator('#chat-decisions')
    panel.locator('textarea').fill('Let me inspect the mock first')
    panel.locator('label:has(input[value=leaders])').click()
    page.evaluate("window.originalForm=document.querySelector('#chat-decisions form')")
    expanded=panel.bounding_box()
    toggle=panel.get_by_role('button',name='Minimize',exact=True)
    toggle.focus()
    page.keyboard.press('Enter')
    assert panel.get_by_role('button',name='Expand',exact=True).get_attribute('aria-expanded')=='false'
    assert panel.locator('form').is_hidden()
    assert panel.bounding_box()['height']<expanded['height']/2
    assert page.locator('#composer').is_hidden()
    assert 'your input is needed' in panel.inner_text().lower()
    # Unrelated updates must not reopen the panel or discard the live form.
    page.evaluate('qa.renderChat();qa.sendTurn({messageOverride:"Cannot bypass the blocker"})')
    assert page.evaluate("originalForm===document.querySelector('#chat-decisions form')")
    assert panel.locator('form').is_hidden()
    assert not posted
    page.wait_for_function("!document.querySelector('#toast').classList.contains('show') && getComputedStyle(document.querySelector('#toast')).opacity==='0'")
    for width,height in [(1500,900),(390,700)]:
        page.set_viewport_size({'width':width,'height':height})
        page.wait_for_timeout(150)
        box=panel.bounding_box()
        assert box and box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1
        assert box['height']<110 and box['y']+box['height']<=height+1
        panel.screenshot(path=f'/tmp/decision-minimized-{width}.png')
    panel.get_by_role('button',name='Expand',exact=True).click()
    assert panel.locator('textarea').input_value()=='Let me inspect the mock first'
    assert panel.locator('input[value=leaders]').is_checked()
    panel.get_by_role('button',name='Minimize',exact=True).click()
    # A changed contract needs attention again, even if it keeps the same ID.
    page.evaluate('qa.state.surfaces[0].payload.request_state.revision=2;qa.renderChat()')
    assert panel.locator('form').is_visible()
    panel.get_by_role('button',name='Minimize',exact=True).click()
    page.evaluate('''() => {
      qa.state.surfaces.push({...structuredClone(qa.state.surfaces[0]),id:'next',
        payload:{...structuredClone(qa.state.surfaces[0].payload),request_state:{id:'next',status:'open',revision:1}}});
      qa.rememberInputRequestReceipt('question',{id:'question',status:'answered',revision:3,answers:{audience:'leaders'}});
      qa.renderChat();
    }''')
    assert panel.get_attribute('data-active-request')=='next'
    assert panel.locator('form').is_visible() and page.locator('#composer').is_hidden()
    page.evaluate("qa.rememberInputRequestReceipt('next',{id:'next',status:'answered',revision:2,answers:{audience:'leaders'}});qa.renderChat()")
    assert panel.is_hidden() and page.locator('#composer').is_visible()
    assert not posted
