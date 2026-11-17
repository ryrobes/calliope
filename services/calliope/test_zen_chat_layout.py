"""Real chat renderer/CSS regression coverage; all data and network are local."""
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parent


@pytest.fixture
def zen_page():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                   args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1500, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(r):
            url = urlsplit(r.request.url)
            if url.netloc != 'zen.test':
                r.abort(); return
            if url.path == '/calliope':
                html = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/index.html').read_text())
                r.fulfill(body=html, content_type='text/html'); return
            if url.path.startswith('/api/'):
                r.fulfill(json={}); return
            path = ROOT/url.path.lstrip('/')
            if path.is_file() and path.resolve().is_relative_to(ROOT):
                if path.name == 'calliope.js':
                    source = path.read_text().replace('  init();\n})();', '''
  window.qa={voiceClipStartAt,noteVoiceClipEnd,stopVoicePlayback,expectTake,processProgressVoiceQueue,rememberTakeNote,takeNoteFor,renderSessionAccess,renderMentionInvites,currentReadOnly,currentCanManage,openSessionSharing,saveSessionSharing,submitStageSecureAction,applyStagePlan,applyLiveSpecialistEvent,clearStageWorkingSet,renderStageWorkingSet,currentTeamPlans,renderComposerPlaybook,openIntake,composerObjectRefs,composerValue,syncSpeechControls,setTurnConnection,adaptiveDesignProfile,renderDesignEditor,applyDesignPreviewVariables,renderDesignPreview,renderProviderSettings,pendingAgentChanges,agentEdit,startSpeechRecording,stopSpeechRecording,cancelSpeechRecording,renderCalliopeMetric,renderZenTeamToggle,toggleZenTeamMode,initializeComposerEditor,installAgentSignatureFonts,setChatWidth,renderComposerRail,renderComposerRailToggle,toggleComposerRail,toggleComposerRailPick,composerRailPins,scheduleComposerRail,composerRailTiles,clearComposerRailPicks,composerSetValue,handleSketchCardClick,renderStageTurnPlan,state,els,renderChat,renderChatZen,renderChatDecisions,renderStage,
    acceptChatPresentation,pollChatPresentation,unansweredChatInputs,retainTeamActivity,maybeFinishTeamActivity,syncCurrentRunControls,syncNotebookActivityStatus,notebookActivityPhase,outstandingAgentWork,
    activeRunProjection,mergeInputRequestStates,rememberInputRequestReceipt,openFileSurface,closeSurfaceViewer,openQuerySurface,hydrateDocumentPreviews,
    turnElapsedMilliseconds,turnClockMarkup,syncTurnClocks,launchInitialTurn,
    computer:()=>computerShelf,desktopStage:()=>desktopStage,
    beginIntakeTurn,sendTurn,setupEvents,init,setWorkingDraft,commitWorkingUpdate,safeMarkdown,openSpecialistThread,selectSession,scrollChatToLiveEdge,turnPhaseLabel,syncVoiceUi,applyVoicePreferences,readVoicePreferences,renderVoiceToggle,acceptAgentWork,pollAgentWork,liveSpecialistToolPulses,renderAgentWork,startTurnPresentation,voicePresentationPending,finishProgressVoiceBeforeFinal,surfaceCard,renderSketchShelf,openSketchWorkspace,closeSketchWorkspace,revealUpdatedCalliopeSketch,toggleSketchCollapsed,maybeSpeakTurn,repairMissedPresentation,playVoiceAudio,stopVoicePlayback,openVoiceDialog,renderVoiceTuning,retakeVoiceDialog,voiceTakes,initializeVoiceKaraoke,absorbVoiceAlignment};
})();''')
                    r.fulfill(body=source, content_type='text/javascript'); return
                r.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream'); return
            r.fulfill(status=404)

        page.route('**/*', route)
        page.goto('http://zen.test/calliope', wait_until='domcontentloaded')
        page.wait_for_function('Boolean(window.qa)')
        page.evaluate('''() => {
          document.body.classList.add('zen');
          qa.state.zen=true;qa.state.current={id:'notebook',owner_email:'viewer@example.com',title:'Layout check'};
          qa.state.config={viewer:{email:'viewer@example.com',display_name:'Viewer'},specialists:[]};
          qa.state.agentWork={pending:[],queued_messages:0};qa.state.agentWorkSessionId='notebook';
          qa.state.voice.presentationMode='off';
          qa.state.turns=[{id:'one',ordinal:1,turn_kind:'chat',status:'complete',user_message:'Build a dashboard',
            assistant_message:'A quick question before I start.',created_at:new Date(Date.now()-60000).toISOString(),
            completed_at:new Date(Date.now()-30000).toISOString()}];
          qa.state.surfaces=[{id:'question',turn_id:'one',kind:'input',title:'Choose a direction',payload:{
            request_state:{id:'question',status:'open',revision:1},title:'Choose a direction',prompt:'Choose what matters most to your team.',fields:[
              {key:'audience',label:'Who needs this dashboard?',type:'choice',required:true,options:[
                {value:'leaders',label:'Leadership: show a broad overview with all the context they need to make an informed decision.'},
                {value:'operators',label:'Operations: help teams spot exceptions and act on them.'}]},
              {key:'notes',label:'Anything else?',type:'textarea'}]}}];
          qa.renderChat(true);
        }''')
        try:
            yield page
            assert not errors
        finally:
            browser.close()


def test_tab_title_follows_latest_published_page_across_reload_and_navigation(zen_page):
    page = zen_page
    payload = page.evaluate('''() => ({session:qa.state.current,turns:qa.state.turns,surfaces:[
      {id:'older',turn_id:'one',kind:'artifact',title:'Older page',ordinal:99,
       artifact_slug:'older',artifact_version:8,created_at:'2026-09-09T18:00:00Z',updated_at:'2026-09-12T00:00:00Z',payload:{}},
      {id:'capture',turn_id:'one',kind:'image',title:'Latest screenshot',ordinal:4,
       created_at:'2026-09-11T19:00:00Z',payload:{}},
      {id:'latest',turn_id:'one',kind:'artifact',title:'Revenue & Growth',ordinal:3,
       artifact_slug:'revenue',artifact_version:1,created_at:'2026-09-10T18:00:00Z',payload:{}}
    ],agent_work:{pending:[],queued_messages:0}})''')
    page.route('**/api/calliope/sessions/notebook', lambda r: r.fulfill(json=payload))
    page.evaluate("qa.selectSession('notebook',{focusComposer:false})")
    assert page.title() == 'Revenue & Growth - Calliope'
    # Refreshing the same notebook can skip Stage rendering entirely.
    page.evaluate('''async () => {
      document.title='Calliope';
      await qa.selectSession('notebook',{preserveActivity:true,focusComposer:false});
    }''')
    assert page.title() == 'Revenue & Growth - Calliope'
    # Publication events refresh the Stage while staying in the current chat.
    page.evaluate('''() => {
      qa.state.surfaces.push({id:'new-page',turn_id:'one',kind:'artifact',title:'Operations overview',
        artifact_slug:'operations',artifact_version:1,ordinal:5,created_at:'2026-09-11T20:00:00Z',payload:{}});
      qa.renderStage();
    }''')
    assert page.title() == 'Operations overview - Calliope'
    page.route('**/api/calliope/sessions/empty', lambda r: r.fulfill(json={
        'session': {'id': 'empty', 'title': 'Another chat', 'owner_email': 'viewer@example.com'},
        'turns': [], 'surfaces': [], 'agent_work': {'pending': [], 'queued_messages': 0},
    }))
    page.evaluate("qa.selectSession('empty',{focusComposer:false})")
    assert page.title() == 'Calliope'
    page.evaluate("qa.selectSession('notebook',{focusComposer:false})")
    assert page.title() == 'Revenue & Growth - Calliope'


def test_voice_label_stays_single_after_playback_sync_and_chat_refresh(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.state.turns[0].response_receipt={presentation:{id:'render-one',version:1,script:'A spoken answer'}};
      qa.renderChat();
      window.voiceButton=document.querySelector('[data-open-voice-turn]');
      window.answerBody=document.querySelector('[data-assistant-turn-id="one"] .message-body');
    }''')
    label = page.locator('[data-open-voice-turn="one"] [data-voice-label]')
    # With the take on screen, preparing means its audio is on the way: 'Voicing'.
    for phase, expected in [('preparing', 'Voicing'), ('playing', 'Playing'), ('idle', 'Full answer')] * 3:
        page.evaluate('''phase => {
          qa.state.voice.turnId='one';qa.state.voice.phase=phase;
          qa.syncVoiceUi(); // replaces Text nodes without rebuilding the row
          qa.renderChat();qa.renderChat();
        }''', phase)
        assert label.text_content() == expected
        assert label.evaluate('(node)=>node.childNodes.length') == 1
        assert page.evaluate('document.querySelector("[data-open-voice-turn]")===voiceButton')
        assert page.evaluate('document.querySelector(\'[data-assistant-turn-id="one"] .message-body\')===answerBody')


@pytest.mark.parametrize('playback', [False, True])
@pytest.mark.parametrize('width', [1500, 390])
def test_consecutive_rewritten_answers_each_open_their_own_original(zen_page, playback, width):
    page = zen_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate('''playback => {
      document.body.classList.add('mobile-chat-open');
      qa.setupEvents();qa.state.surfaces=[];
      qa.state.config.text_to_speech={enabled:playback};
      qa.state.config.answer_presentation={enabled:true};
      qa.state.voice.preferences.mode=playback ? 'fast' : 'off';
      qa.state.voice.preferences.presentation=true;
      const base=qa.state.turns[0];
      qa.state.turns=['one','two','three'].map((id,index)=>({...base,id,ordinal:index+1,
        turn_kind:index ? 'agent_message' : 'chat',user_message:index ? '' : base.user_message,
        assistant_message:`Original ${id}: **required detail ${index+1}**`,
        response_receipt:index<2 ? {presentation:{id:`render-${id}`,version:1,script:`Short rewrite ${id}.`}} : {}}));
      qa.renderChat();
    }''', playback)
    label = 'Voice' if playback else 'Full answer'
    for turn_id, detail in [('one', 'required detail 1'), ('two', 'required detail 2')]:
        row = page.locator(f'[data-assistant-turn-id="{turn_id}"]')
        assert row.locator('.message-body').inner_text() == f'Short rewrite {turn_id}.'
        button = row.get_by_role('button', name=label, exact=True)
        assert button.is_visible()
        button.click()
        assert page.locator('#voice-dialog').is_visible()
        assert page.locator('#voice-dialog-script').inner_text() == f'Original {turn_id}: {detail}'
        assert page.locator('#voice-dialog-script strong').inner_text() == detail
        page.get_by_role('button', name='Close complete response', exact=True).click()
        # A polling redraw must retain the control and its per-turn target.
        page.evaluate('qa.syncVoiceUi();qa.renderChat();qa.renderChat()')
        assert button.is_visible()
    second = page.locator('[data-assistant-turn-id="two"]')
    assert 'zen-cont' in second.get_attribute('class')
    assert second.locator('.zen-name').is_hidden()
    assert second.locator('.zen-av-slot').is_hidden()
    # An answer without a take offers one, in the same quiet slot as the voice control.
    third = page.locator('[data-assistant-turn-id="three"]')
    assert third.locator('.zen-name').is_hidden()
    assert third.get_by_role('button', name='Callie’s take', exact=True).is_visible()
    assert second.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
    if width == 390 and playback:
        page.locator('#messages').screenshot(path='/tmp/consecutive-voice-controls.png')


def test_questions_keep_focus_and_geometry_across_agent_delivery(zen_page):
    page = zen_page
    dock = page.locator('#chat-decisions')
    form = dock.locator('form')
    before = page.locator('.chat-column').bounding_box()
    page.locator('#chat-decisions textarea').fill('Keep this draft')
    page.evaluate('window.keptForm=document.querySelector("#chat-decisions form")')
    page.locator('#chat-decisions .stage-input-choices label').first.click()
    page.evaluate('''() => {
      qa.state.turns.push({id:'agent',ordinal:2,turn_kind:'agent_message',user_message:'Late specialist reply',
        status:'complete',assistant_message:'',created_at:new Date().toISOString()});
      qa.renderChat();qa.renderStage();qa.renderChat();
    }''')
    assert page.evaluate('document.querySelector("#chat-decisions form")===keptForm')
    assert form.locator('textarea').input_value() == 'Keep this draft'
    assert form.locator('input[value=leaders]').is_checked()
    assert page.evaluate('document.activeElement===keptForm.querySelector("input[value=leaders]")')
    after = page.locator('.chat-column').bounding_box()
    assert all(abs(before[k]-after[k]) < 1 for k in before)
    assert page.evaluate('document.scrollingElement.scrollTop') == 0
    # Keyboard focus belongs to a radio inside its own label, not the body.
    form.locator('input[value=operators]').focus()
    assert page.evaluate('document.scrollingElement.scrollTop') == 0
    assert dock.bounding_box()['height'] <= page.viewport_size['height']*.72+1
    assert page.locator('#composer').is_hidden()
    assert form.bounding_box()['width'] > dock.bounding_box()['width']*.85
    page.screenshot(path='/tmp/zen-chat-questions.png')
    page.evaluate('''() => {
      qa.state.turns.push({id:'answer',ordinal:3,status:'complete',turn_kind:'chat',
        selected_surface_id:'question',user_message:'Here are my answers for “Choose a direction”:\\n- Audience: leaders'});
      qa.state.surfaces[0].payload.request_state={id:'question',status:'answered',revision:2,answers:{audience:'leaders'}};
      qa.renderChat();
    }''')
    assert dock.is_hidden()
    # Reload projection: no in-memory completed-surface set is necessary.
    assert page.evaluate('qa.unansweredChatInputs().length') == 0


def test_chat_contains_wide_content_and_long_choices(zen_page):
    page = zen_page
    page.evaluate('''() => {
      document.querySelector('.message-body').innerHTML='<pre>'+ 'x'.repeat(2000)+'</pre><table><tr><td>'+ 'url'.repeat(800)+'</td></tr></table><img width="2400" height="30"><iframe width="3000" height="30"></iframe>';
    }''')
    for width in (1500, 950, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        page.evaluate("document.body.classList.add('mobile-chat-open')")
        page.wait_for_timeout(350)
        assert page.evaluate('''() => {
          const chat=document.querySelector('.chat-column'),messages=document.querySelector('#messages');
          return chat.scrollWidth<=chat.clientWidth+1 && messages.scrollWidth<=messages.clientWidth+1;
        }''')
        assert page.evaluate('document.scrollingElement.scrollTop') == 0


@pytest.mark.parametrize('mode', ['dark', 'light'])
def test_answer_tables_line_up_figures_and_code_reads_as_one_block(zen_page, mode):
    page = zen_page
    found = page.evaluate('''mode => {
      document.documentElement.dataset.warehouseColorMode = mode;
      qa.state.surfaces = [];
      qa.state.turns[0].assistant_message = [
        '| Region | Orders | Change | Note |', '|---|---|---|:-:|',
        '| North | 1,204 | +12% | **new** |', '| West | 220 | −6% | — |', '',
        'Revenue is each `total`.', '', '```sql', 'SELECT region, count(*) FROM shop.orders GROUP BY 1;', '```'].join('\\n');
      qa.renderChat(true);
      const body = document.querySelector('.message.assistant .message-body');
      const pre = body.querySelector('pre'), code = pre.querySelector('code');
      const light = (node) => {
        const pixel = document.createElement('canvas').getContext('2d');
        pixel.fillStyle = getComputedStyle(node).backgroundColor; pixel.fillRect(0, 0, 1, 1);
        const [r, g, b] = pixel.getImageData(0, 0, 1, 1).data; return r + g + b;
      };
      return {
        aligns: [...body.querySelectorAll('tr:last-child td')].map(cell => cell.dataset.align || ''),
        fills: body.querySelector('table').getBoundingClientRect().width >= body.querySelector('.document-table-wrap').clientWidth - 1,
        language: pre.dataset.lang,
        codeChip: getComputedStyle(code).backgroundColor,
        sameSize: getComputedStyle(code).fontSize === getComputedStyle(pre).fontSize,
        inlineChip: getComputedStyle(body.querySelector('p code')).backgroundColor,
        preIsLight: light(pre) > 600,
      };
    }''', mode)
    # Figures line up on the right; an explicit Markdown alignment wins.
    assert found['aligns'] == ['', 'right', 'right', 'center']
    assert found['fills'] and found['language'] == 'sql'
    # The inline-code chip stays inline: inside a block it would repeat on every wrapped line.
    assert found['codeChip'] == 'rgba(0, 0, 0, 0)' and found['sameSize']
    assert found['inlineChip'] != 'rgba(0, 0, 0, 0)'
    # The block follows the theme (it used to stay black, with dark text, in light mode).
    assert found['preIsLight'] == (mode == 'light')


def test_parent_completion_keeps_clock_and_trace_running(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const turn=qa.state.turns[0];
      qa.state.agentWork={pending:[{specialist_name:'Iris'}],queued_messages:0};
      const run={sessionId:'notebook',pending:turn,status:'complete',startedAt:Date.now()-60000,
        stageTools:[],toolTraceSamples:[1,2,3],toolTraceLastSampleAt:0};
      qa.retainTeamActivity(run,turn,{});qa.renderChat();
      window.elapsedBefore=qa.turnElapsedMilliseconds(turn);
    }''')
    clock = page.locator('.turn-clock')
    assert clock.locator('[data-turn-tool-trace]').count() == 1
    assert clock.locator('[data-turn-phase]').inner_text() == 'Iris is working'
    assert clock.locator('[data-turn-clock]').inner_text() == '1:00'
    page.wait_for_timeout(1100)
    page.evaluate('qa.syncTurnClocks()')
    assert page.evaluate('qa.turnElapsedMilliseconds(qa.state.turns[0])-elapsedBefore') >= 1000
    page.evaluate('''() => {
      qa.state.agentWork={pending:[],queued_messages:0};qa.maybeFinishTeamActivity();
      window.ended=qa.turnElapsedMilliseconds(qa.state.turns[0]);
    }''')
    page.wait_for_timeout(1100)
    assert page.evaluate('qa.turnElapsedMilliseconds(qa.state.turns[0])') == page.evaluate('ended')
    assert page.evaluate('ended') >= 61000


def test_launch_handshake_waits_for_user_message_not_full_response(zen_page):
    page = zen_page
    page.evaluate('''() => {
      window.revealed=false;
      qa.launchInitialTurn(({onTurnStarted})=>new Promise(resolve=>{
        window.firstMessage=onTurnStarted;window.finishResponse=resolve;
      })).then(()=>{revealed=true});
    }''')
    page.wait_for_timeout(100)
    assert page.evaluate('revealed') is False
    page.evaluate('firstMessage()')
    page.wait_for_function('revealed')
    page.evaluate('finishResponse()')


def test_floor_handoff_holds_veil_through_slow_session_creation(zen_page):
    page = zen_page
    held = []
    turns = []
    session = {'id': 'new-notebook', 'owner_email': 'viewer@example.com', 'title': 'New notebook', 'agent_key': 'calliope'}
    created = []

    def api(r):
        path = urlsplit(r.request.url).path
        if path == '/api/calliope/config':
            r.fulfill(json={'healthy': True, 'viewer': {'email': 'viewer@example.com'}, 'specialists': []})
        elif path == '/api/calliope/sessions':
            if r.request.method == 'POST':
                held.append(r)
            else:
                r.fulfill(json={'sessions': [session] if created else []})
        elif path == '/api/calliope/sessions/new-notebook':
            r.fulfill(json={'session': session, 'turns': [], 'surfaces': []})
        elif path.endswith('/turn'):
            turns.append(r)  # The agent has not answered when the veil reveals.
        else:
            r.fulfill(json={})

    page.route('**/api/**', api)
    page.evaluate('''() => {
      qa.state.current=null;qa.state.turns=[];qa.state.surfaces=[];
      sessionStorage.setItem('calliope.floorIntake.v1', JSON.stringify({prompt:'Build our dashboard',created_at:Date.now(),team_mode:true}));
      history.replaceState({},'', '/calliope?start=1&view=zen');
    }''')
    page.add_script_tag(url='http://zen.test/calliope/boot-veil.js')
    page.evaluate('void qa.init()')
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(25)
    assert held, page.locator('#toast').inner_text()
    # The previous fixed timeout exposed a blank shell during this wait.
    page.wait_for_timeout(4700)
    assert page.locator('body').evaluate('(el)=>el.classList.contains("boot-veil")')
    page.evaluate('''() => {
      window.revealState=null;
      new MutationObserver(()=>{
        if(!document.body.classList.contains('boot-veil') && !window.revealState)
          window.revealState={intake:document.body.classList.contains('intake'),
            user:document.querySelector('#messages .user .message-body')?.textContent,
            working:!!document.querySelector('#messages .turn-clock.is-running')};
      }).observe(document.body,{attributes:true,attributeFilter:['class']});
    }''')
    created.append(True)
    held.pop().fulfill(json={'session': session})
    page.wait_for_function('window.revealState', timeout=10000)
    assert page.evaluate('revealState') == {'intake': False, 'user': 'Build our dashboard', 'working': True}
    assert turns
    # Leave the held stream pending; this test never waits for AI completion.


def test_stream_completion_and_team_delivery_keep_the_same_answer(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const turn=qa.state.turns[0];
      Object.assign(turn,{status:'running',assistant_message:'',working_updates:['I will check the figures.'],working_draft:''});
      qa.state.current.specialist_threads=[{thread_id:'11111111-1111-1111-1111-111111111111',root_turn_id:'one',
        specialist:{name:'Iris'},last_run_status:'running',round_count:1}];
      qa.renderChat();
      window.answer=document.querySelector('[data-assistant-turn-id="one"]');
      window.body=answer.querySelector('.message-body');
      window.note=body.firstElementChild;
      window.link=document.querySelector('[data-open-specialist-thread]');
      qa.setWorkingDraft(turn,'**Here are the figures**\\n\\nRevenue grew.');
    }''')
    page.wait_for_timeout(120)
    assert page.locator('.is-draft').inner_text() == '**Here are the figures**\n\nRevenue grew.'
    assert page.locator('.is-draft strong').count() == 0
    page.evaluate('''() => {
      window.draft=body.lastElementChild;
      window.textNode=draft.firstChild;
      qa.setWorkingDraft(qa.state.turns[0],'**Here are the figures**\\n\\nRevenue grew. Margins fell.');
    }''')
    page.wait_for_timeout(120)
    assert page.evaluate('draft===body.lastElementChild && draft.firstChild===textNode')
    page.evaluate('''() => {
      const turn=qa.state.turns[0];
      // An unrelated Stage update must not roll the draft back.
      qa.state.surfaces.push({id:'unrelated',turn_id:'one',kind:'query',title:'New result',payload:{}});
      qa.renderChat();
      qa.state.turns.push({id:'mail',ordinal:2,turn_kind:'agent_message',status:'complete',assistant_message:'',
        user_message:'[AGENT MESSAGE — reply from Iris · thread 11111111-1111-1111-1111-111111111111]\\nAn internal essay.\\n[/AGENT MESSAGE]',created_at:new Date().toISOString()});
      qa.state.current.specialist_threads[0].last_run_status='complete';
      qa.renderChat();
      turn.status='complete';turn.assistant_message=turn.working_draft;qa.renderChat();
    }''')
    assert page.evaluate('answer===document.querySelector(\'[data-assistant-turn-id="one"]\') && body===answer.querySelector(".message-body") && draft===body.lastElementChild')
    assert page.evaluate('link===document.querySelector("[data-open-specialist-thread]")')
    assert page.locator('.zen-team-conversations').count() == 1
    assert 'Complete' not in page.locator('.zen-team-conversations').inner_text()
    assert 'round 1' in page.locator('.zen-team-conversations').inner_text()
    assert 'An internal essay' not in page.locator('#messages').inner_text()
    assert 'I will check the figures.' in page.locator('.zen-progress-history').text_content()
    assert page.locator('.zen-progress-history .zen-speech-piece').is_hidden()
    assert page.locator('[data-assistant-turn-id=one] strong').inner_text() == 'Here are the figures'
    # A fresh server projection contains saved display notes, not browser-only state.
    page.evaluate('''() => {
      qa.state.turns=JSON.parse(JSON.stringify(qa.state.turns));
      delete qa.state.turns[0].working_draft;qa.renderChat();
    }''')
    assert 'Revenue grew. Margins fell.' in page.locator('[data-assistant-turn-id=one]').inner_text()
    assert 'I will check the figures.' in page.locator('.zen-progress-history').text_content()
    assert page.locator('.zen-progress-history .zen-speech-piece').is_hidden()
    page.screenshot(path='/tmp/zen-clean-chat.png')


def test_unchanged_chat_preserves_iframe_and_scrolled_reading_position(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.state.turns=Array.from({length:20},(_,i)=>({id:'t'+i,ordinal:i,turn_kind:'chat',status:'complete',
        user_message:'Question '+i,assistant_message:'Answer '+i+'\\n\\nSecond paragraph.',created_at:new Date().toISOString()}));
      qa.renderChat();
      const first=document.querySelector('[data-assistant-turn-id=t0] .message-body');
      window.frame=document.createElement('iframe');frame.srcdoc='<p>Keep this document</p>';first.append(frame);
    }''')
    page.wait_for_timeout(100)
    page.evaluate('''() => {
      qa.state.chatAtLiveEdge=false;qa.els.messages.scrollTop=600;
      window.scrollBefore=qa.els.messages.scrollTop;
      window.frameDocument=frame.contentDocument;
      qa.renderChat();qa.renderChat();
      qa.state.turns[19].assistant_message+=' New late text.';qa.renderChat();
    }''')
    assert page.evaluate('frame.isConnected && frame.contentDocument===frameDocument')
    assert abs(page.evaluate('qa.els.messages.scrollTop-scrollBefore')) < 2


def test_direct_dynamic_agent_keeps_its_primary_identity(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.config.chat_agents=[{key:'aletheia',name:'Aletheia',role:'Identity expert'}];
      qa.state.current.specialist_key='aletheia';qa.renderChat();
    }''')
    assert 'Aletheia' in page.locator('[data-assistant-turn-id=one] .zen-who').inner_text()


def test_conversation_link_loads_canonical_history_without_a_saved_surface(zen_page):
    page=zen_page
    thread='11111111-1111-1111-1111-111111111111'
    page.route('**/api/calliope/specialist-threads/'+thread, lambda r: r.fulfill(json={'thread': {
        'thread_id':thread,'turn_id':'one','run_id':'run-one','round':1,'round_count':1,'status':'complete',
        'specialist':{'name':'Iris','key':'iris'},'transcript':[
            {'role':'calliope','kind':'handoff','text':'Check these figures.','ordinal':1,'round':1},
            {'role':'specialist','kind':'assistant','text':'These are the canonical details.','ordinal':2,'round':1}],
    }}))
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.state.current.specialist_threads=[{
        thread_id:'11111111-1111-1111-1111-111111111111',root_turn_id:'one',specialist:{name:'Iris'},
        last_run_status:'complete',round_count:1}];
      qa.setupEvents();qa.renderChat();
    }''')
    page.locator('button.zen-team-conversation').click()
    page.wait_for_selector('.viewer-conversation')
    assert 'These are the canonical details.' in page.locator('.viewer-conversation').inner_text()
    assert 'Check these figures.' in page.locator('.viewer-conversation').inner_text()


def test_user_scroll_cancels_queued_follow_and_survives_background_refresh(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.setupEvents();qa.state.surfaces=[];
      qa.state.turns=Array.from({length:20},(_,i)=>({id:'t'+i,ordinal:i,turn_kind:'chat',status:'complete',
        user_message:'Question '+i,assistant_message:'Answer '+i+'\\n\\nMore details.',created_at:new Date().toISOString()}));
      qa.renderChat(true);
    }''')
    page.wait_for_timeout(150)
    page.evaluate('''() => {
      qa.scrollChatToLiveEdge();
      qa.els.messages.dispatchEvent(new WheelEvent('wheel',{deltaY:-100}));
      qa.els.messages.scrollTop-=300;
    }''')
    page.wait_for_timeout(100)
    before=page.evaluate('qa.els.messages.scrollTop')
    payload=page.evaluate('({session:qa.state.current,turns:qa.state.turns,surfaces:[],agent_work:{pending:[],queued_messages:0}})')
    page.route('**/api/calliope/sessions/notebook',lambda r:r.fulfill(json=payload))
    page.evaluate('''async () => {
      await qa.selectSession('notebook',{preserveActivity:true,allowDuringTurn:true,focusComposer:false});
      qa.scrollChatToLiveEdge();
    }''')
    page.wait_for_timeout(150)
    assert page.evaluate('qa.state.chatAtLiveEdge') is False
    assert abs(page.evaluate('qa.els.messages.scrollTop')-before)<2


def test_phase_and_agent_rows_distinguish_waiting_tools_and_model_silence(zen_page):
    page=zen_page
    page.evaluate('''() => {
      const turn=qa.state.turns[0];turn.status='running';turn.assistant_message='';
      const run={sessionId:'notebook',pending:turn,status:'running',startedAt:Date.now()-60000,
        lastActivityAt:Date.now()-20000,stageTools:[]};
      qa.state.activeRuns.set('notebook',run);qa.renderChat();
    }''')
    assert 'Calliope is thinking (' in page.locator('[data-turn-phase]').inner_text()
    assert '0:20' in page.locator('[data-turn-phase]').inner_text()
    page.evaluate('''() => {
      const run=qa.state.activeRuns.get('notebook');
      run.stageTools=[{status:'active',toolName:'mcp__Warehouse__await_calliope_specialists'}];
      qa.state.agentWork={pending:[{thread_id:'thread',specialist_name:'Iris',round:2,status:'running'}]};
      qa.state.current.specialist_threads=[{thread_id:'thread',root_turn_id:'one',specialist:{name:'Iris'},last_run_status:'complete',round_count:1}];
      qa.renderChat();
    }''')
    assert page.locator('[data-turn-phase]').inner_text()=='Iris is working'
    row_text = page.locator('button.zen-team-conversation').inner_text()
    assert 'Running' in row_text and 'round 2' in row_text
    assert 'Open conversation' not in page.locator('button.zen-team-conversation').inner_text()
    page.evaluate('''() => {
      qa.state.agentWork={pending:[],queued_messages:0};
      qa.state.activeRuns.get('notebook').stageTools=[];
      qa.setWorkingDraft(qa.state.turns[0],'Writing this now');qa.syncTurnClocks();
    }''')
    assert page.locator('[data-turn-phase]').inner_text()=='Calliope is writing'


def test_team_activity_survives_parent_turn_change_and_uses_one_avatar_row(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.state.config.chat_agents=[{key:'calliope',name:'Calliope'},{key:'iris',name:'Iris',avatar_url:'/calliope/iris.png'}];
      qa.state.turns.push({id:'followup',ordinal:2,turn_kind:'agent_message',status:'complete',
        user_message:'[AGENT MESSAGE — reply from Athena · thread athena]Review[/AGENT MESSAGE]',
        assistant_message:'I requested a correction.',created_at:new Date().toISOString(),completed_at:new Date().toISOString()});
      window.teamWork={pending:[{run_id:'iris-run',thread_id:'iris-thread',specialist_key:'iris',specialist_name:'Iris',round:1,status:'running'}],
        queued_messages:0,activity:[{run_id:'iris-run',thread_id:'iris-thread',root_turn_id:'one',source_turn_id:'one',
          specialist_key:'iris',specialist_name:'Iris',round:1,status:'running',tool_count:45,active_count:1,active_tools:['run_sql']}],
        followups:[{thread_id:'iris-thread',count:1}]};
      qa.acceptAgentWork(teamWork);qa.renderAgentWork();qa.renderChat();
      window.keptAnswer=document.querySelector('[data-assistant-turn-id="followup"] .message-body');
      window.keptAvatar=document.querySelector('[data-open-specialist-thread="iris-thread"] img');
    }''')
    row=page.locator('[data-open-specialist-thread="iris-thread"]')
    assert row.count()==1
    assert row.locator('img').get_attribute('src')=='/calliope/iris.png'
    assert '45 calls' in row.inner_text() and '1 correction queued' in row.inner_text()
    assert page.locator('#agent-work').is_hidden()
    assert page.locator('[data-turn-phase="followup"]').inner_text()=='Iris is working'
    assert page.locator('[data-turn-tool-trace="followup"]').get_attribute('data-tool-count')=='1'
    assert 'sql' in page.locator('[data-turn-tool-names="followup"]').inner_text().lower()
    assert 'Team 45' in page.locator('[data-assistant-turn-id="one"] .message-receipt').inner_text()
    row.locator('img').evaluate('(image)=>image.decode()')
    page.locator('.chat-column').screenshot(path='/tmp/zen-team-activity-running.png')
    # Quick calls between polls still leave a small recent-activity pulse.
    page.evaluate('''() => {
      teamWork.activity[0].tool_count=46;teamWork.activity[0].active_count=0;teamWork.activity[0].active_tools=[];
      qa.acceptAgentWork(teamWork);qa.renderChat();
    }''')
    assert '46 calls' in row.inner_text()
    assert page.locator('[data-turn-tool-trace="followup"]').get_attribute('data-tool-count')=='1'
    assert page.evaluate("document.querySelector('[data-assistant-turn-id=\"followup\"] .message-body')===keptAnswer")
    assert page.evaluate("document.querySelector('[data-open-specialist-thread=\"iris-thread\"] img')===keptAvatar")
    # A completed run ceases contributing immediately, even with a lingering pulse.
    page.evaluate('''() => {
      teamWork.activity[0].status='complete';teamWork.pending=[];teamWork.followups=[];
      qa.acceptAgentWork(teamWork);qa.renderChat();
    }''')
    assert 'Complete' not in row.inner_text()
    facts = row.locator('.team-facts').inner_text()
    assert 'round 1' in facts and '46 calls' in facts
    assert row.locator('..').evaluate('(node)=>getComputedStyle(node).borderTopWidth')=='0px'
    assert page.locator('[data-turn-tool-trace="followup"]').count()==0
    page.screenshot(path='/tmp/zen-team-activity.png')


def test_completed_answer_rewrites_without_waiting_for_team(zen_page):
    page = zen_page
    requests = []
    page.route('**/api/calliope/voice/renders', lambda route: requests.append(route))
    with page.expect_request('**/api/calliope/voice/renders'):
        page.evaluate('''() => {
          qa.state.surfaces=[];
          qa.state.config.answer_presentation={enabled:true,render_version:1};
          window.WarehouseTheme={getVoice:()=>({mode:'off',presentation:true})};
          qa.state.agentWork={pending:[{thread_id:'iris-thread',specialist_key:'iris',specialist_name:'Iris'}],queued_messages:0};
          const turn=qa.state.turns[0];
          turn.assistant_message='The completed answer is readable now.';
          const run={sessionId:'notebook',pending:turn,status:'complete',startedAt:Date.now()-60000,stageTools:[]};
          qa.retainTeamActivity(run,turn,{});
          qa.startTurnPresentation(turn);qa.startTurnPresentation(turn);qa.renderChat();
        }''')
    body = page.locator('[data-assistant-turn-id="one"] .message-body')
    assert 'The completed answer is readable now.' not in body.inner_text()
    assert body.locator('.voice-preparing').count() == 1
    assert page.locator('[data-turn-phase]').inner_text() == 'Iris is working'
    assert len(requests) == 1
    # Resolve just the rewrite; the specialist is still running.
    requests[0].fulfill(json={'render': {'id':'render-now','version':1,'script':'Here is the conversational answer.'}})
    page.wait_for_function('!qa.voicePresentationPending(qa.state.turns[0])')
    assert 'Here is the conversational answer.' in body.inner_text()
    assert page.locator('[data-turn-phase]').inner_text() == 'Iris is working'
    page.evaluate('''() => {
      qa.state.agentWork={pending:[],queued_messages:0};qa.maybeFinishTeamActivity();
      qa.startTurnPresentation(qa.state.turns[0]);
    }''')
    assert len(requests) == 1  # Draining the team never replays an old answer.


# One kind of take: Callie's take decides whether there is one (read aloud plays it,
# so it needs the take on); with a voice installed it is always the expressive take.
@pytest.mark.parametrize("mode,presentation,expected", [
    ("off", False, None), ("off", True, "expressive"),
    ("fast", False, None), ("fast", True, "expressive"),
    ("expressive", False, None), ("expressive", True, "expressive"),
])
def test_audio_and_short_answers_share_one_rewrite(zen_page, mode, presentation, expected):
    page = zen_page
    requests = []
    page.route('**/api/calliope/voice/renders', lambda route: requests.append(route))
    page.evaluate('''prefs => {
      qa.state.config.answer_presentation={enabled:true};
      qa.state.config.text_to_speech={enabled:true};
      window.WarehouseTheme={getVoice:()=>prefs};
      qa.startTurnPresentation(qa.state.turns[0]);
      qa.startTurnPresentation(qa.state.turns[0]);
      qa.renderChat();qa.renderChat();
    }''', {"mode": mode, "presentation": presentation})
    if expected is None:
        page.wait_for_timeout(100)
        assert requests == []
    else:
        page.wait_for_function("document.querySelector('.voice-preparing') !== null")
        page.wait_for_timeout(100)
        assert len(requests) == 1
        assert requests[0].request.post_data_json["mode"] == expected


def test_rewrite_timeout_restores_answer_while_team_runs(zen_page):
    page = zen_page
    page.clock.install()
    page.route('**/api/calliope/voice/renders', lambda route: None)
    with page.expect_request('**/api/calliope/voice/renders'):
        page.evaluate('''() => {
          qa.state.surfaces=[];
          qa.state.config.answer_presentation={enabled:true};
          qa.state.config.text_to_speech={prepare_timeout_seconds:5};
          window.WarehouseTheme={getVoice:()=>({mode:'off',presentation:true})};
          qa.state.agentWork={pending:[{thread_id:'iris-thread',specialist_key:'iris',specialist_name:'Iris'}],queued_messages:0};
          qa.state.turns[0].assistant_message='Read this without waiting for audio.';
          qa.startTurnPresentation(qa.state.turns[0]);qa.renderChat();
        }''')
    body = page.locator('[data-assistant-turn-id="one"] .message-body')
    assert 'Read this without waiting for audio.' not in body.inner_text()
    page.clock.run_for(5100)
    page.wait_for_function('qa.state.voice.failures.has("one")')
    assert body.locator('.voice-preparing').count() == 0
    assert 'Read this without waiting for audio.' in body.inner_text()
    assert page.locator('[data-turn-phase]').inner_text() == 'Iris is working'


def test_progress_audio_cannot_block_final_presentation_indefinitely(zen_page):
    page = zen_page
    page.clock.install()
    page.evaluate('''() => {
      qa.state.voice.progressProcessing=true;
      window.progressFinished=false;
      qa.finishProgressVoiceBeforeFinal('one').then(()=>{window.progressFinished=true;});
    }''')
    page.clock.run_for(5100)
    assert not page.evaluate('window.progressFinished')  # spoken updates play out; no 5 s cut-off
    page.clock.run_for(85_000)
    page.wait_for_function('window.progressFinished')
    assert not page.evaluate('qa.state.voice.progressProcessing')


def test_running_timer_and_chart_have_separate_space(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.state.turns[0].status='running';qa.state.turns[0].assistant_message='';
      qa.state.turns[0].working_draft='A response arriving with several lines.\\nThis is the final line.';
      qa.renderChat();
    }''')
    for width in (1500, 780, 420):
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate('qa.renderChat();qa.syncTurnClocks()')
        clock = page.locator('.turn-clock.is-running')
        time = clock.locator('[data-turn-clock]').bounding_box()
        trace = clock.locator('[data-turn-tool-trace]').bounding_box()
        phase = clock.locator('[data-turn-phase]').bounding_box()
        body = page.locator('[data-assistant-turn-id="one"] .message-body').bounding_box()
        assert time['x'] + time['width'] + 10 <= trace['x']
        assert phase['y'] + phase['height'] + 4 <= trace['y']
        assert time['y'] >= body['y'] + body['height'] + 10
        assert page.evaluate('document.querySelector(".chat-column").scrollWidth<=document.querySelector(".chat-column").clientWidth+1')
    page.set_viewport_size({'width':1500,'height':900})
    page.locator('.chat-column').screenshot(path='/tmp/zen-answer-spacing.png')


def test_quiet_lead_reports_active_team_and_uses_direct_agent_name(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      const turn=qa.state.turns[0];turn.status='running';turn.assistant_message='';
      const run={sessionId:'notebook',pending:turn,status:'running',startedAt:Date.now()-60000,
        lastActivityAt:Date.now()-20000,stageTools:[]};
      qa.state.activeRuns.set('notebook',run);
      qa.state.agentWork={pending:['Iris','Athena','Cassandra'].map(name=>({specialist_name:name,status:'running'}))};
      qa.renderChat();
    }''')
    phase = page.locator('[data-turn-phase]')
    assert phase.inner_text() == 'Iris, Athena +1 are working'
    page.evaluate('''() => {
      qa.state.config.chat_agents=[{key:'calliope',name:'Calliope'},{key:'aletheia',name:'Aletheia'}];
      qa.state.current.specialist_key='aletheia';qa.renderChat();
    }''')
    assert phase.inner_text() == 'Iris, Athena +1 are working'
    page.evaluate('''() => {
      qa.state.activeRuns.get('notebook').stageTools=[{status:'active',toolName:'run_sql'}];
      qa.syncTurnClocks();
    }''')
    assert phase.inner_text() == 'Aletheia is using tools'
    page.evaluate('''() => {
      qa.state.activeRuns.get('notebook').stageTools=[];
      qa.setWorkingDraft(qa.state.turns[0],'Here is what I found');qa.syncTurnClocks();
    }''')
    assert phase.inner_text() == 'Aletheia is writing'
    page.evaluate('''() => {
      qa.state.agentWork={pending:[],queued_messages:0};
      qa.state.activeRuns.get('notebook').lastActivityAt=Date.now()-20000;
      qa.syncTurnClocks();
    }''')
    assert phase.inner_text() == 'Aletheia is thinking (0:20)'
    page.evaluate("qa.state.agentWork={pending:[{status:'running'}]};qa.syncTurnClocks()")
    assert phase.inner_text() == 'The team is working'
    page.evaluate("qa.state.agentWork={pending:[{specialist_key:'aletheia',status:'running'}]};qa.syncTurnClocks()")
    assert phase.inner_text() == 'Aletheia is working'


def test_status_and_tool_type_rows_keep_their_space_when_empty(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      const turn=qa.state.turns[0];turn.status='running';turn.assistant_message='';
      qa.state.activeRuns.set('notebook',{sessionId:'notebook',pending:turn,status:'running',
        startedAt:Date.now()-60000,stageTools:[]});
      qa.renderChat();
    }''')
    for width in (1500, 780, 420):
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate('qa.renderChat()')
        clock = page.locator('.turn-clock.is-running')
        phase = clock.locator('[data-turn-phase]')
        trace = clock.locator('[data-turn-tool-trace]')
        names = clock.locator('[data-turn-tool-names]')
        before = [x.bounding_box() for x in (clock, phase, trace, names)]
        assert before[1]['y'] + before[1]['height'] < before[2]['y']
        assert before[2]['y'] + before[2]['height'] < before[3]['y']
        for text in ('', 'run sql · search company knowledge · build a dashboard · verify browser screenshots ' * 3, ''):
            page.evaluate('''text => {
              document.querySelector('[data-turn-phase]').textContent=text;
              document.querySelector('[data-turn-tool-names]').textContent=text;
            }''', text)
            after = [x.bounding_box() for x in (clock, phase, trace, names)]
            for initial, current in zip(before, after):
                assert abs(initial['y'] - current['y']) < 1
                assert abs(initial['height'] - current['height']) < 1
                assert abs(initial['width'] - current['width']) < 1
        # Exercise the real activity update path, too.
        page.evaluate('''() => {
          qa.state.activeRuns.get('notebook').stageTools=[{status:'active',toolName:'run_sql'}];
          qa.renderChat();qa.syncTurnClocks();
        }''')
        assert 'sql' in names.inner_text().lower()
        assert abs(clock.bounding_box()['height'] - before[0]['height']) < 1
        page.evaluate('''() => {
          qa.state.activeRuns.get('notebook').stageTools=[];qa.renderChat();qa.syncTurnClocks();
        }''')
        assert not names.inner_text()
        assert abs(clock.bounding_box()['height'] - before[0]['height']) < 1


@pytest.mark.parametrize('zen', [False, True])
def test_sketch_omits_exploration_note_and_retains_canvas_on_stage_updates(zen_page, zen):
    page = zen_page
    page.route('**/calliope/sketches/**', lambda route: route.fulfill(
        content_type='text/html', body='<script>window.documentIdentity=Math.random();window.localEdit="unsaved";parent.sketchLoads=(parent.sketchLoads||0)+1;</script><canvas></canvas>'))
    page.evaluate('''zen => {
      qa.state.zen=zen;document.body.classList.toggle('zen',zen);
      qa.state.zenExpandedSurfaces.add('note');
      qa.state.surfaces=[{id:'sketch-surface',kind:'sketch',turn_id:'one',title:'Working sketch',
        created_at:new Date().toISOString(),presentation:{phase:'exploration'},
        source:{exploration:{intent:'This metadata must not occupy a canvas grid row.'}},
        payload:{sketch_id:'shared-sketch',revision:2,element_count:4,last_actor:'calliope'}},
        {id:'note',kind:'document',turn_id:'one',title:'Other work',payload:{text:'Unrelated output'},
         presentation:{phase:'exploration'},source:{exploration:{intent:'Still show this explanation for documents.'}}}];
      qa.renderStage();qa.setupEvents();
      document.querySelector('#workspace-tab-sketch').click();
      if(zen)qa.openSketchWorkspace('shared-sketch');
    }''', zen)
    page.wait_for_function('window.sketchLoads>0 && document.querySelector("iframe[data-sketch-id]").contentWindow.documentIdentity')
    page.wait_for_load_state('networkidle')
    assert page.locator('[data-sketch-card] .surface-exploration-note').count() == 0
    assert page.locator('[data-sketch-card] [data-source-turn]').count() == 0
    assert page.locator('[data-surface-id="note"] .surface-exploration-note').count() == 1
    page.evaluate('''() => {
      window.keptSketch=document.querySelector('[data-sketch-card]');
      window.keptSketchFrame=keptSketch.querySelector('iframe');
      window.keptSketchDocument=keptSketchFrame.contentDocument;
      window.initialSketchLoads=window.sketchLoads;window.sketchMutations=0;
      new MutationObserver(records=>window.sketchMutations+=records.length)
        .observe(keptSketch,{subtree:true,childList:true,attributes:true,characterData:true});
      for(let i=0;i<5;i++){
        qa.state.surfaces[1].title='Other work '+i;
        qa.state.surfaces[0].source.exploration.intent='Changed ignored metadata '+i;
        qa.renderStage();
      }
    }''')
    assert page.evaluate('sketchMutations') == 0
    assert page.evaluate('''() => document.querySelector('[data-sketch-card]')===keptSketch
      && keptSketch.querySelector('iframe')===keptSketchFrame
      && keptSketchFrame.contentDocument===keptSketchDocument
      && keptSketchFrame.contentWindow.localEdit==='unsaved'
      && sketchLoads===initialSketchLoads''')
    # A genuine sketch revision updates its metadata without remounting the editor.
    page.evaluate('''() => {
      qa.state.surfaces[0].payload.revision=3;
      qa.state.surfaces[0].payload.element_count=5;qa.renderStage();
    }''')
    assert 'r3' in page.locator('.sketch-panel-head').get_attribute('title')
    assert '5 elements' in page.locator('.sketch-panel-head').get_attribute('title')
    assert page.evaluate('keptSketchFrame.contentDocument===keptSketchDocument && sketchLoads===initialSketchLoads')
    if zen:
        assert page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')
        body = page.locator('[data-sketch-card] > .surface-body').bounding_box()
        frame = page.locator('[data-sketch-card] iframe').bounding_box()
        assert frame['height'] > 200 and frame['y'] >= body['y']
        assert frame['y'] + frame['height'] <= body['y'] + body['height'] + 1


def test_zen_agent_update_keeps_inactive_sketch_mounted_without_rebuilding(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'sketch-surface',kind:'sketch',turn_id:'one',title:'Sketch',
        payload:{sketch_id:'shared-sketch',revision:2,element_count:4,last_actor:'calliope'}}];
      qa.renderStage();
      const incoming=structuredClone(qa.state.surfaces);incoming[0].payload.revision=3;
      qa.revealUpdatedCalliopeSketch(incoming);qa.state.surfaces=incoming;qa.renderStage();
      window.foldedSketch=document.querySelector('[data-sketch-card]');
      window.foldedMutations=0;
      new MutationObserver(records=>window.foldedMutations+=records.length)
        .observe(foldedSketch.parentNode,{subtree:true,childList:true});
      qa.renderStage();qa.renderStage();qa.renderStage();
    }''')
    assert page.locator('#workspace-sketch').is_hidden()
    assert page.locator('iframe[data-sketch-id]').count() == 1
    assert page.evaluate('document.querySelector("[data-sketch-card]")===foldedSketch && foldedMutations===0')
    page.evaluate('qa.setupEvents();document.querySelector("#workspace-tab-sketch").click()')
    assert not page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')
    assert page.locator('#sketch-shelf iframe[data-sketch-id]').count() == 1


def test_pending_sketch_save_keeps_canvas_until_fold_is_safe(zen_page):
    page = zen_page
    page.route('**/calliope/sketches/**', lambda route: route.fulfill(content_type='text/html',body='<canvas></canvas>'))
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'sketch-surface',kind:'sketch',turn_id:'one',title:'Sketch',
        payload:{sketch_id:'shared-sketch',revision:2,element_count:4}}];
      qa.state.sketchCollapseInitialized.add('shared-sketch');
      qa.state.sketchPendingCollapse='shared-sketch';
      qa.renderSketchShelf();window.savingFrame=document.querySelector('iframe[data-sketch-id]');
      qa.renderStage();qa.renderStage();
    }''')
    assert page.evaluate('savingFrame && document.querySelector("iframe[data-sketch-id]")===savingFrame')


@pytest.mark.parametrize('zen', [True, False])
def test_completed_duration_lives_in_each_receipt_total(zen_page, zen):
    page = zen_page
    page.evaluate("""zen => {
      qa.state.zen=zen;document.body.classList.toggle('zen',zen);
      qa.state.surfaces=[];
      const base=Date.now()-600000;
      qa.state.turns=[
        {id:'old',status:'complete',seconds:75,response_receipt:{tools:[{name:'query',count:3}]}},
        {id:'plain',status:'complete',seconds:12},
        {id:'failed',status:'failed',seconds:9},
        {id:'unknown',status:'complete',seconds:null}
      ].map((item,i)=>({...item,turn_kind:'chat',ordinal:i+1,user_message:'Question',assistant_message:'Answer',
        created_at:new Date(base+i*90000).toISOString(),
        completed_at:item.seconds===null?null:new Date(base+i*90000+item.seconds*1000).toISOString()}));
      qa.renderChat();
    }""", zen)
    assert page.locator('.turn-clock').count() == 0
    old = page.locator('[data-assistant-turn-id="old"] .message-receipt summary')
    assert 'Ran 3 · Time 1:15' in old.inner_text()
    assert 'Time 0:12' in page.locator('[data-assistant-turn-id="plain"] .receipt-totals').inner_text()
    assert page.locator('[data-assistant-turn-id="plain"] details.message-receipt').count() == 0
    assert 'Time 0:09' in page.locator('[data-assistant-turn-id="failed"] .receipt-totals').inner_text()
    assert page.locator('[data-assistant-turn-id="unknown"] .message-receipt').count() == 0
    page.evaluate('qa.syncTurnClocks();qa.renderChat()')
    assert 'Time 1:15' in old.inner_text()
    page.evaluate("""() => {
      qa.state.turns=qa.state.turns.slice(0,1);
      qa.state.agentWork={pending:[{specialist_name:'Iris'}],queued_messages:0};qa.renderChat();
    }""")
    assert page.locator('.turn-clock.is-running [data-turn-clock]').count() == 1
    assert 'Time ' not in old.inner_text()
    page.evaluate("qa.state.agentWork={pending:[],queued_messages:0};qa.renderChat()")
    assert page.locator('.turn-clock').count() == 0
    assert 'Time ' in old.inner_text()


@pytest.mark.parametrize('zen', [True, False])
def test_saved_sketch_opens_in_workspace_and_large_is_separate(zen_page,zen):
    page=zen_page
    page.route('**/calliope/sketches/**',lambda route:route.fulfill(content_type='text/html',body='<canvas></canvas>'))
    setup="""zen => {
      qa.state.zen=zen;document.body.classList.toggle('zen',zen);
      qa.state.current={id:'saved',owner_email:'viewer@example.com'};
      qa.state.config={viewer:{email:'viewer@example.com'},specialists:[]};
      qa.state.surfaces=[{id:'saved-sketch',kind:'sketch',turn_id:'old',title:'Saved sketch',
        payload:{sketch_id:'saved-canvas',revision:4,element_count:8,default_collapsed:true}}];
      qa.setupEvents();
      qa.renderChat();qa.renderStage();
    }"""
    page.evaluate(setup,zen)
    for restored in [False,True]:
        if restored:
            page.reload(wait_until='domcontentloaded');page.wait_for_function('window.qa');page.evaluate(setup,zen)
        card=page.locator('#sketch-shelf [data-sketch-card]')
        assert card.locator('[data-source-turn]').count()==0
        page.locator('#workspace-tab-sketch').click()
        assert not page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')
        assert card.locator('iframe[data-sketch-id]').count()==1
        assert card.get_by_role('button',name='Collapse',exact=True).is_hidden()
        card.get_by_role('button',name='Large',exact=True).click()
        assert page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')
        page.get_by_role('button',name='Close large Sketch workspace',exact=True).click()
        assert not page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')
        assert card.locator('iframe[data-sketch-id]').count()==1
        page.locator('#workspace-tab-chat').click()
        assert card.locator('iframe[data-sketch-id]').count()==1
    # Reload defaults to Chat, with the Sketch ready in its own panel.
    page.locator('#workspace-tab-sketch').click()
    page.reload(wait_until='domcontentloaded');page.wait_for_function('window.qa');page.evaluate(setup,zen)
    assert page.locator('#sketch-shelf iframe[data-sketch-id]').count()==1
    assert not page.locator('#sketch-workspace-dialog').evaluate('(node)=>node.open')


@pytest.mark.parametrize('mode',['dark','light'])
def test_live_plan_readability_and_containment(zen_page,mode):
    page=zen_page
    page.evaluate("""mode => {
      qa.state.surfaces=[];qa.renderChat();
      document.documentElement.dataset.warehouseColorMode=mode;
      document.documentElement.style.setProperty('--amber',mode==='dark'?'#ef8a4b':'#9c5010');
      qa.els.stageWorkingSet.hidden=false;qa.els.stageWorkingSet.classList.add('is-plan-drawer','has-activity','is-reserved');
      qa.renderStageTurnPlan({revision:3,todos:[
        {id:'done',status:'completed',content:'Review the source data'},
        {id:'active',status:'in_progress',content:'Build the regional margin comparison and explain the largest changes'},
        {id:'check',parent:'active',status:'pending',content:'Check the finished dashboard against the source totals'},
        {id:'share',status:'pending',content:'Publish the dashboard and share the findings'}]});
    }""",mode)
    for width in [1500,390]:
        page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(300)
        panel=page.locator('#stage-turn-plan')
        assert panel.is_visible()
        assert page.locator('#stage-turn-plan li.is-in_progress').evaluate('(node)=>parseFloat(getComputedStyle(node).fontSize)')>=16
        assert panel.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
        box=panel.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+1
        panel.screenshot(path=f'/tmp/calliope-live-plan-{mode}-{width}.png')


def test_team_rows_use_registered_signature_fonts_and_keep_failure_status(zen_page):
    page=zen_page
    page.evaluate("""() => {
      qa.state.surfaces=[];
      qa.state.config.chat_agents=[{key:'calliope',name:'Calliope'},
        {key:'iris',name:'Iris',signature_font:'Permanent Marker'},
        {key:'platform-access',name:'Aletheia',signature_font:'Charmonman'}];
      qa.installAgentSignatureFonts();
      qa.state.current.specialist_threads=[
        {thread_id:'iris',root_turn_id:'one',specialist:{key:'iris',name:'Iris'},last_run_status:'complete',round_count:2},
        {thread_id:'dynamic',root_turn_id:'one',specialist:{key:'platform-access',name:'Aletheia'},last_run_status:'failed',round_count:1}];
      qa.renderChat();
    }""")
    for thread,key,family in [('iris','iris','Permanent Marker'),('dynamic','platform-access','Charmonman')]:
        name=page.locator(f'[data-open-specialist-thread="{thread}"] .zen-name-agent')
        assert name.get_attribute('data-agent-signature-key')==key
        assert family in name.evaluate('(node)=>getComputedStyle(node).fontFamily')
    assert 'Complete' not in page.locator('[data-open-specialist-thread="iris"]').inner_text()
    assert 'Needs attention' in page.locator('[data-open-specialist-thread="dynamic"]').inner_text()
    page.locator('.chat-column').screenshot(path='/tmp/agent-signature-rows.png')


@pytest.mark.parametrize('rich_editor',[True,False])
def test_chat_composer_room_and_consistent_controls(zen_page,rich_editor):
    page=zen_page
    page.evaluate("""rich => {
      qa.state.surfaces=[];qa.renderChat();
      qa.els.input.disabled=false;
      if(rich)qa.initializeComposerEditor();
      for(const id of ['zen-team-toggle','zen-design-control'])document.getElementById(id).hidden=false;
      document.querySelector('#zen-design-button span').textContent='Adaptive Calliope';
      document.getElementById('send-message').disabled=false;
    }""",rich_editor)
    editor=page.locator('.cm-editor' if rich_editor else '#message-input')
    editor.wait_for(state='visible')
    assert editor.bounding_box()['height']>=95.5  # room for three lines at the conversation's text size
    if rich_editor:
        page.locator('.cm-content').fill('Compare revenue by region.\nExplain the biggest changes.\nUse last quarter as the baseline.')
        assert page.evaluate('qa.els.input.value').count('\n')==2
    else:
        editor.fill('Compare revenue by region.\nExplain the biggest changes.')
    page.evaluate("document.getElementById('send-message').disabled=false")
    page.evaluate("document.getElementById('send-message').disabled=false")
    for width in [1500,1100,390]:
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate("document.body.classList.add('mobile-chat-open')")
        page.wait_for_timeout(250)
        assert page.locator('#composer').evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
        sizes=page.locator('#composer :is(.attach-action,#zen-design-button,#zen-team-toggle,#send-message)').evaluate_all('(nodes)=>nodes.map(n=>({height:n.getBoundingClientRect().height,radius:getComputedStyle(n).borderRadius}))')
        assert all(abs(size['height']-32)<1 and size['radius']=='9px' for size in sizes)
        send=page.locator('#send-message').bounding_box()
        toolbar=page.locator('#composer .compose-actions').bounding_box()
        assert abs(send['x']+send['width']-toolbar['x']-toolbar['width'])<1
        assert abs(send['y']+send['height']-toolbar['y']-toolbar['height'])<1
        page.locator('#composer').screenshot(path=f'/tmp/chat-composer-{rich_editor}-{width}.png')
    # More text scrolls inside a bounded editor instead of consuming the chat.
    if rich_editor:
        page.locator('.cm-content').fill('A long draft line.\n'*30)
        page.wait_for_timeout(150)
        assert editor.bounding_box()['height']<=241


def test_team_toggle_has_explicit_state_and_restores_it_on_save_failure(zen_page):
    page=zen_page
    page.evaluate("qa.state.surfaces=[];qa.renderChat();qa.state.current.team_mode=true;qa.renderZenTeamToggle(true)")
    toggle=page.locator('#zen-team-toggle')
    assert toggle.locator('.label').text_content()=='Team on'  # the label can be tier-hidden in a narrow chat column; the state text is still there
    enabled_color=toggle.evaluate('(node)=>getComputedStyle(node).color')
    assert toggle.get_attribute('aria-pressed')=='true'
    page.evaluate('qa.toggleZenTeamMode()')
    assert toggle.locator('.label').text_content()=='Team off'
    assert toggle.get_attribute('aria-pressed')=='false'
    page.wait_for_timeout(200)
    assert toggle.evaluate('(node)=>getComputedStyle(node).color')!=enabled_color
    page.route('**/api/calliope/sessions/notebook',lambda r:r.fulfill(status=503,json={'error':'Offline'}))
    page.evaluate('qa.toggleZenTeamMode()')
    assert toggle.locator('.label').text_content()=='Team off'
    assert toggle.get_attribute('aria-pressed')=='false'


@pytest.mark.parametrize('failed',[False,True])
def test_thread_refresh_veils_until_restore_settles(zen_page,failed):
    page=zen_page;held=[]
    session={'id':'remembered','owner_email':'viewer@example.com','title':'Saved conversation','agent_key':'calliope'}
    def api(r):
        path=urlsplit(r.request.url).path
        if path=='/api/calliope/config':r.fulfill(json={'healthy':True,'viewer':{'email':'viewer@example.com'},'specialists':[]})
        elif path=='/api/calliope/sessions':r.fulfill(json={'sessions':[session]})
        elif path=='/api/calliope/sessions/remembered':held.append(r)
        else:r.fulfill(json={})
    page.route('**/api/**',api)
    page.route('**/calliope/ascii-veil.js',lambda r:r.fulfill(content_type='text/javascript',body="export function mountAsciiVeil(host){host.dataset.ready='true';return {dispose(){}}}"))
    page.add_init_script("""const originalTimeout=window.setTimeout;
      window.setTimeout=function(fn,ms,...args){if(ms===30000 && !window.bootTimeoutCallback)window.bootTimeoutCallback=fn;return originalTimeout(fn,ms,...args)};""")
    page.evaluate("history.replaceState({},'', '/calliope?view=zen&session=remembered')")
    page.reload(wait_until='domcontentloaded');page.wait_for_function('window.qa')
    assert page.locator('body').evaluate('(node)=>node.classList.contains("boot-veil")')
    assert page.locator('.notebook').evaluate('(node)=>node.inert')
    assert page.locator('body').evaluate('(node)=>getComputedStyle(node,"::after").pointerEvents')=='auto'
    page.evaluate('''() => {
      window.revealState=null;
      new MutationObserver(()=>{
        if(!document.body.classList.contains('boot-veil') && !window.revealState)
          window.revealState={user:document.querySelector('#messages .user .message-body')?.textContent||'',
            locked:document.querySelector('.notebook').inert};
      }).observe(document.body,{attributes:true,attributeFilter:['class']});
      void qa.init();
    }''')
    for _ in range(160):
        if held:break
        page.wait_for_timeout(25)
    assert held,page.locator('#toast').inner_text()
    page.wait_for_timeout(7100 if not failed else 100)
    assert page.locator('body').evaluate('(node)=>node.classList.contains("boot-veil")')
    page.evaluate('window.bootTimeoutCallback()')
    assert page.get_by_role('button',name='Reload',exact=True).is_visible()
    assert page.locator('.notebook').evaluate('(node)=>node.inert')
    if failed:held.pop().fulfill(status=503,json={'error':{'message':'Restore failed'}})
    else:held.pop().fulfill(json={'session':session,'surfaces':[], 'turns':[
        {'id':'saved-turn','turn_kind':'chat','status':'complete','user_message':'Our saved question',
         'assistant_message':'The saved answer.','created_at':'2026-09-09T09:00:00Z','completed_at':'2026-09-09T09:01:00Z'}]})
    page.wait_for_function('window.revealState',timeout=10000)
    assert page.evaluate('revealState.locked') is False
    assert page.locator('#bootVeilRecovery').count()==0
    assert page.locator('body').get_attribute('aria-busy') is None
    if not failed:
        assert page.evaluate('revealState.user')=='Our saved question'
        assert 'The saved answer.' in page.locator('#messages').inner_text()
    else:assert 'Restore failed' in page.locator('#toast').inner_text()


def test_plain_address_is_a_fresh_chat_even_with_a_remembered_thread(zen_page):
    page=zen_page;opened=[]
    session={'id':'remembered','owner_email':'viewer@example.com','title':'Saved conversation','agent_key':'calliope'}
    def api(r):
        path=urlsplit(r.request.url).path
        if path=='/api/calliope/config':r.fulfill(json={'healthy':True,'viewer':{'email':'viewer@example.com'},'specialists':[]})
        elif path=='/api/calliope/sessions':r.fulfill(json={'sessions':[session]})
        elif path.startswith('/api/calliope/sessions/'):opened.append(path);r.fulfill(json={'session':session,'turns':[],'surfaces':[]})
        else:r.fulfill(json={})
    page.route('**/api/**',api)
    page.evaluate("localStorage.setItem('rvbbit-calliope-last-session-v1','remembered');history.replaceState({},'', '/calliope?view=zen')")
    page.reload(wait_until='domcontentloaded');page.wait_for_function('window.qa')
    assert not page.locator('body').evaluate('(node)=>node.classList.contains("boot-veil")'),'nothing to restore, nothing to veil'
    page.evaluate('window.initDone=false;qa.init().then(()=>{initDone=true})')
    page.wait_for_function('window.initDone')
    assert opened==[],'the remembered thread is not opened from memory'
    assert page.evaluate('Boolean(qa.state.intake) && !qa.state.current')
    assert page.evaluate('location.search')=='?view=zen'
    page.evaluate("qa.selectSession('remembered')")
    page.wait_for_function("location.search.includes('session=remembered')")
    page.go_back();page.wait_for_function('Boolean(qa.state.intake) && !qa.state.current')
    assert page.evaluate('location.search')=='?view=zen','Back returns to the fresh chat'
    page.go_forward();page.wait_for_function("qa.state.current?.id==='remembered'")

def test_earlier_progress_folds_without_disturbing_draft_or_answer(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      Object.assign(qa.state.turns[0],{status:'running',assistant_message:'',
        working_updates:['Checking the grain.','Comparing regions.'],working_draft:'The largest change is'});
      qa.renderChat();
      window.draftNode=document.querySelector('.is-draft');
    }''')
    history=page.locator('.zen-progress-history')
    assert not history.evaluate('(node)=>node.open')
    assert history.locator('summary').inner_text()=='Earlier progress · 2'
    assert history.locator('.zen-speech-piece').first.is_hidden()
    assert page.locator('.is-draft').is_visible()
    history.locator('summary').click()
    page.evaluate("qa.setWorkingDraft(qa.state.turns[0],'The largest change is in the East.')")
    page.wait_for_timeout(160)
    assert page.evaluate("draftNode===document.querySelector('.is-draft')")
    assert history.evaluate('(node)=>node.open')
    page.evaluate('''() => {
      qa.renderChat();
      const turn=qa.state.turns[0];turn.status='complete';turn.assistant_message='**East:** margins fell.';
      qa.renderChat();
    }''')
    assert history.evaluate('(node)=>node.open')
    assert page.locator('.message-body > .zen-speech-piece strong').inner_text()=='East:'
    assert page.locator('#calliope-token-meter').count()==0
    history.locator('summary').click()
    assert page.locator('.message-body > .zen-speech-piece').is_visible()


def test_live_plan_minimize_survives_updates_but_new_turn_starts_expanded(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.renderChat();qa.setupEvents();
      const run={sessionId:'notebook',pending:qa.state.turns[0],status:'running'};
      qa.state.activeRuns.set('notebook',run);
      qa.els.stageWorkingSet.hidden=false;qa.els.stageWorkingSet.classList.add('is-plan-drawer');
      window.plan={confirmed:true,revision:1,todos:[{id:'one',status:'in_progress',content:'Review the regional trends'}]};
      qa.applyStagePlan(run,plan);run.pending.status='running';
      qa.renderStage();
    }''')
    panel=page.locator('#stage-turn-plan')
    full=panel.bounding_box()['height']
    page.get_by_role('button',name='Minimize',exact=True).click()
    assert page.locator('#stage-turn-plan-items').is_hidden()
    assert panel.bounding_box()['height']<full
    page.evaluate('plan.revision=2;qa.renderStageTurnPlan(plan)')
    assert page.get_by_role('button',name='Expand',exact=True).get_attribute('aria-expanded')=='false'
    page.get_by_role('button',name='Expand',exact=True).click()
    assert page.locator('#stage-turn-plan-items').is_visible()
    page.get_by_role('button',name='Minimize',exact=True).click()
    page.evaluate("qa.state.activeRuns.get('notebook').pending={id:'new-turn'};qa.renderStageTurnPlan(plan)")
    assert page.locator('#stage-turn-plan-items').is_visible()
    page.set_viewport_size({'width':390,'height':900});page.wait_for_timeout(300)
    assert panel.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
    panel.screenshot(path='/tmp/live-plan-minimize-control.png')


@pytest.mark.parametrize('zen',[False,True])
@pytest.mark.parametrize('replacement',['snapshot','missing_snapshot','image'])
def test_artifact_conversion_drops_live_height_without_reloading_neighbors(zen_page,zen,replacement):
    page=zen_page
    page.route('**/apps/**',lambda r:r.fulfill(content_type='text/html',body='<p>Dashboard revision</p>'))
    page.evaluate('''zen => {
      qa.setupEvents();qa.state.zen=zen;document.body.classList.toggle('zen',zen);
      qa.state.surfaces=[{id:'old',kind:'artifact',turn_id:'one',title:'Sales v1',artifact_slug:'sales',artifact_version:1,payload:{url:'/apps/sales?v=1'}},
        {id:'neighbor',kind:'artifact',turn_id:'one',title:'Inventory',artifact_slug:'inventory',artifact_version:1,payload:{url:'/apps/inventory?v=1'}}];
      qa.renderStage();
      window.oldCard=document.querySelector('[data-surface-id="old"]');
      window.oldFrame=oldCard.querySelector('iframe');
      window.neighbor=document.querySelector('[data-surface-id="neighbor"] iframe');
      window.neighborWindow=neighbor.contentWindow;
      window.dispatchEvent(new MessageEvent('message',{source:oldFrame.contentWindow,
        data:{type:'calliope.artifact.resize',height:1800}}));
      window.dispatchEvent(new MessageEvent('message',{source:neighbor.contentWindow,
        data:{type:'calliope.artifact.resize',height:700}}));
    }''',zen)
    assert page.locator('[data-surface-id="old"] .artifact-frame').evaluate('(node)=>node.style.height')=='1800px'
    page.evaluate('''replacement => {
      const old=qa.state.surfaces[0];
      const image='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="teal"/></svg>');
      if(replacement==='image') {old.kind='image';old.payload={image_url:image};}
      else {
        if(replacement==='snapshot')qa.state.surfaces.push({id:'capture',kind:'image',turn_id:'one',title:'Sales capture',artifact_slug:'sales',artifact_version:1,tool_name:'capture_live_app',payload:{image_url:image}});
        qa.state.surfaces.push({...old,id:'new',artifact_version:2,title:'Sales v2',payload:{url:'/apps/sales?v=2'}});
      }
      window.abortCount=0;
      qa.state.artifactQueryControllers.set('old',new Set([{abort(){abortCount++}}]));
      qa.renderStage();
    }''',replacement)
    card=page.locator('[data-surface-id="old"]')
    assert card.locator('[data-auto-height]').count()==0
    assert card.locator('[style*="1800px"]').count()==0
    assert card.locator('iframe').count()==0
    assert page.evaluate("oldCard===document.querySelector('[data-surface-id=old]')")
    assert page.evaluate("neighbor===document.querySelector('[data-surface-id=neighbor] iframe') && neighbor.contentWindow===neighborWindow")
    assert page.locator('[data-surface-id="neighbor"] .artifact-frame').evaluate('(node)=>node.style.height')=='700px'
    assert page.evaluate("qa.state.artifactFrameHeights.get('neighbor')")==700
    assert page.evaluate('abortCount')==1
    assert not page.evaluate("qa.state.artifactFrameHeights.has('old')")
    before=card.bounding_box()['height']
    # A fresh render tree must have the same size as the incremental conversion.
    page.evaluate('qa.els.stage.replaceChildren();qa.renderStage()')
    assert abs(card.bounding_box()['height']-before)<2


@pytest.mark.parametrize('root_loaded', [True,False])
def test_active_team_row_follows_latest_exchange_even_with_missing_root(zen_page,root_loaded):
    page=zen_page
    page.evaluate("""rootLoaded => {
      qa.state.surfaces=[];
      const first=qa.state.turns[0];
      qa.state.turns=[...(rootLoaded?[first]:[]),{...first,id:'latest',ordinal:2,assistant_message:'The team is checking.'}];
      qa.state.current.specialist_threads=[{thread_id:'iris',root_turn_id:'one',latest_turn_id:'one',
        specialist:{key:'iris',name:'Iris'},last_run_status:'complete',round_count:1}];
      qa.acceptAgentWork({pending:[{thread_id:'iris',specialist_name:'Iris',status:'running',round:2}],
        activity:[{thread_id:'iris',source_turn_id:'one',root_turn_id:'one',round:2,status:'running',tool_count:7}]});
      qa.renderAgentWork();qa.renderChat();
    }""",root_loaded)
    row=page.locator('[data-open-specialist-thread="iris"]')
    assert row.count()==1
    assert row.locator('..').get_attribute('data-stage-key')=='team:latest'
    assert '7 calls' in row.inner_text()
    assert page.locator('#agent-work').is_hidden()
    # Without any renderable chat turn, retain the live roster as a fallback.
    page.evaluate('qa.state.turns=[];qa.renderAgentWork();qa.renderChat()')
    assert page.locator('#agent-work').is_visible()
    assert page.locator('#agent-work [data-open-specialist-thread="iris"]').count()==1


def test_team_plans_handoff_multiple_agents_history_and_reconnect(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.setupEvents();qa.state.surfaces=[];
      const turn=qa.state.turns[0];turn.status='running';
      window.planRun={sessionId:'notebook',pending:turn,status:'running'};
      qa.state.activeRuns.set('notebook',planRun);
      qa.applyStagePlan(planRun,{confirmed:true,revision:1,todos:[{id:'compose',content:'Assemble the final answer',status:'pending'}]});
      window.teamSnapshot={session_id:'notebook',available:true,plans_available:true,observed_at:'2099-01-01T00:00:00Z',
        parent_turns:[],pending:[{run_id:'iris-one',status:'running'}],activity:[],
        activity_state:{phase:'team_working'},plans:[
          {key:'turn:one',turn_id:'one',specialist:{key:'calliope',name:'Calliope'},active:true,phase:'waiting',confirmed:true,revision:1,
            todos:[{id:'compose',content:'Assemble the final answer',status:'pending'}]},
          {key:'run:iris-one',run_id:'iris-one',turn_id:'one',round:1,specialist:{key:'iris',name:'Iris'},active:true,phase:'running',confirmed:true,revision:4,
            todos:[{id:'design',content:'Build a readable regional comparison',status:'in_progress'}]},
          {key:'run:iris-two',run_id:'iris-two',turn_id:'one',round:2,specialist:{key:'iris',name:'Iris'},active:true,phase:'running',confirmed:true,revision:1,
            todos:[{id:'check',content:'Review the source totals',status:'completed'}]}
        ]};
      qa.acceptAgentWork(teamSnapshot);
      turn.status='complete';qa.state.activeRuns.delete('notebook');qa.clearStageWorkingSet(planRun);qa.renderChat();
    }''')
    sections = page.locator('#stage-turn-plan .team-plan-section')
    assert sections.count() == 3
    assert sections.nth(0).get_attribute('open') is None
    assert 'Waiting for team' in sections.nth(0).inner_text()
    assert sections.nth(1).get_attribute('open') is not None
    assert sections.nth(2).get_attribute('open') is not None  # All checked still belongs to a running agent.
    assert page.locator('#stage-turn-plan .team-plan-avatar').count() == 3
    # Each name is a signature wordmark: a specialist signs in its own font,
    # Calliope in her hand, and neither falls back to the house sans.
    names = page.locator('#stage-turn-plan .team-plan-section strong')
    assert names.nth(0).get_attribute('class') == 'is-calliope'          # the parent plan is hers
    assert names.nth(1).get_attribute('data-agent-signature-key') == 'iris'
    fonts = [names.nth(i).evaluate('el => getComputedStyle(el).fontFamily') for i in range(3)]
    assert all('sans' not in f.lower() for f in fonts), fonts
    page.locator('#stage-turn-plan').screenshot(path='/tmp/calliope-team-plans-expanded.png')
    sections.nth(1).locator('summary').click()
    page.wait_for_timeout(30)
    page.evaluate('''() => {
      teamSnapshot.observed_at='2099-01-01T00:00:01Z';
      teamSnapshot.plans[1].revision=5;
      teamSnapshot.plans[1].todos.push({id:'finish',content:'Publish the approved design',status:'pending'});
      qa.acceptAgentWork(teamSnapshot);
    }''')
    assert sections.nth(1).get_attribute('open') is None
    assert page.locator('#stage-turn-plan').is_visible()
    page.evaluate("qa.acceptAgentWork({session_id:'notebook',available:false,observed_at:'2099-01-01T00:00:02Z'})")
    assert page.locator('#stage-turn-plan').is_visible()
    assert 'Status unavailable' in sections.nth(0).inner_text()
    page.evaluate('''() => {
      teamSnapshot.observed_at='2099-01-01T00:00:03Z';teamSnapshot.pending=[];
      teamSnapshot.activity_state={phase:'ready'};
      teamSnapshot.plans.forEach(plan=>{plan.active=false;plan.phase='finished';});
      qa.acceptAgentWork(teamSnapshot);qa.renderChat();
    }''')
    assert page.locator('#stage-working-set').is_hidden()
    assert page.evaluate('qa.notebookActivityPhase()') == 'ready'
    history = page.locator('.team-plan-history')
    assert history.locator('.team-plan-section').count() == 3
    page.locator('.message-receipt > summary').click()
    assert 'Finished · 2 unchecked' in history.inner_text()
    history.locator('.team-plan-section').nth(1).locator('summary').click()
    assert 'Publish the approved design' in history.inner_text()
    assert history.locator('.team-plan-section').nth(1).bounding_box()['width'] > 250
    assert history.bounding_box()['height'] < 650
    history.screenshot(path='/tmp/calliope-team-plan-history.png')
    # A different notebook cannot see these plans; returning needs no live stream.
    page.evaluate("qa.state.current={id:'other'};qa.renderStageWorkingSet()")
    assert page.locator('#stage-working-set').is_hidden()
    page.evaluate("qa.state.current={id:'notebook'};teamSnapshot.plans[1].active=true;teamSnapshot.plans[1].phase='running';teamSnapshot.observed_at='2099-01-01T00:00:04Z';qa.acceptAgentWork(teamSnapshot)")
    assert page.locator('#stage-turn-plan .team-plan-section').count() == 1
    for width in [1500,390]:
        page.set_viewport_size({'width':width,'height':900})
        page.wait_for_timeout(250)
        panel=page.locator('#stage-turn-plan')
        assert panel.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
        panel.screenshot(path=f'/tmp/calliope-team-plans-{width}.png')


def test_team_plan_stream_ordering_and_hidden_session_isolation(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const turn=qa.state.turns[0];turn.status='running';
      qa.state.surfaces=[];qa.renderChat();
      window.emitPlan=(revision,content,sessionId='notebook')=>qa.applyLiveSpecialistEvent(turn,
        {type:'plan',run_id:'worker',specialist:{name:'Hestia'},created_at:'2099-01-01T00:00:02Z',
          plan:{confirmed:true,revision,todos:[{id:'a',content,status:'pending'}]}}, {sessionId});
      emitPlan(3,'New store');emitPlan(2,'Late old store');
    }''')
    assert 'New store' in page.locator('#stage-turn-plan').inner_text()
    assert 'Late old store' not in page.locator('#stage-turn-plan').inner_text()
    page.evaluate('''() => {
      qa.acceptAgentWork({session_id:'notebook',available:true,pending:[],plans_available:true,
        observed_at:'2099-01-01T00:00:01Z',plans:[]});
      emitPlan(6,'Other notebook private task','other');
    }''')
    assert 'New store' in page.locator('#stage-turn-plan').inner_text()
    assert 'Other notebook' not in page.locator('#stage-turn-plan').inner_text()
    # Rendering a failed tool's argument preview must not erase a confirmed store.
    page.evaluate('''() => {
      const run={sessionId:'notebook',pending:qa.state.turns[0]};
      qa.applyStagePlan(run,{confirmed:false,revision:8,todos:[]});
    }''')
    assert page.locator('#stage-turn-plan').is_visible()


def test_chat_topic_refresh_navigation_and_optional_cache_failure(zen_page):
    page = zen_page
    title = 'September enrollment: resolving the regional conversion gap'
    page.evaluate('(title) => qa.acceptChatPresentation({title,summary:"The regional comparison is complete; checking source freshness before publishing."})', title)
    topic = page.locator('#chat-topic')
    assert topic.is_visible()
    assert not topic.get_attribute('open')
    assert not page.locator('#chat-topic-summary').is_visible()
    topic.locator('summary').click()
    assert page.locator('#chat-topic-summary').is_visible()
    page.route('**/sessions/notebook/presentation', lambda r: r.fulfill(json={
        'presentation': {'title': 'September enrollment: ready to publish', 'summary': 'Freshness is verified.'}}))
    page.evaluate('qa.pollChatPresentation()')
    assert page.locator('#chat-topic-title').inner_text() == 'September enrollment: ready to publish'
    assert page.locator('#chat-topic-summary').inner_text() == 'Freshness is verified.'
    assert topic.get_attribute('open') is not None
    # An unavailable optional cache retains the text, expansion, and readiness.
    page.route('**/sessions/notebook/presentation', lambda r: r.fulfill(json={'presentation': None}))
    before = page.evaluate('qa.state.busy')
    page.evaluate('qa.pollChatPresentation()')
    assert page.locator('#chat-topic-summary').inner_text() == 'Freshness is verified.'
    assert page.evaluate('qa.state.busy') == before
    # A slow response from a previous thread must never paint onto the new one.
    def late_response(r):
        page.evaluate("() => {qa.state.current={id:'another',title:'Another thread'};qa.renderChat();}")
        r.fulfill(json={'presentation': {'title': 'Wrong thread', 'summary': 'Old'}})
    page.route('**/sessions/notebook/presentation', late_response)
    page.evaluate('qa.pollChatPresentation()')
    assert page.locator('#chat-topic-title').inner_text() == 'Another thread'
    assert not page.locator('#chat-topic-summary').is_visible()
    assert topic.get_attribute('open') is None
    page.evaluate('() => {qa.state.current=null;qa.renderChat();}')
    assert not topic.is_visible()


def test_chat_topic_narrow_width_and_literal_text(zen_page):
    page = zen_page
    page.set_viewport_size({'width': 540, 'height': 900})
    page.evaluate('''() => {
      qa.els.messages.innerHTML='<p>A readable conversation</p>';
      qa.els.chatDecisions.hidden=true;
      document.querySelector('.chat-column').style.cssText='position:fixed!important;left:10px!important;top:65px!important;height:800px!important;display:flex!important;width:340px!important;max-width:340px!important';
      qa.acceptChatPresentation({title:'<img src=x onerror=alert(1)> '+ 'Long thread title '.repeat(12),summary:'Saved summary '.repeat(50)});
    }''')
    topic = page.locator('#chat-topic')
    assert topic.locator('img').count() == 0
    assert page.locator('#chat-topic-title').evaluate('(el)=>el.scrollWidth>el.clientWidth')
    assert topic.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    topic.locator('summary').focus()
    page.keyboard.press('Enter')
    assert topic.get_attribute('open') is not None
    assert topic.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    page.screenshot(path='/tmp/calliope-chat-topic-narrow.png')


def test_visibility_repair_does_not_re_ask_while_the_first_rewrite_is_in_flight(zen_page):
    """The double-voice bug: a turn completes, its rewrite is still running, the tab comes
    back to the foreground and the repair sweep asks again. Two renders, two takes, both
    played. One preparation per turn per tab."""
    page = zen_page
    requests = []
    page.route('**/api/calliope/voice/renders', lambda route: requests.append(route))
    page.evaluate('''() => {
      qa.state.config.answer_presentation={enabled:true};
      qa.state.config.text_to_speech={enabled:true};
      window.WarehouseTheme={getVoice:()=>({mode:'fast',presentation:true})};
      qa.state.turns[0].assistant_message='Lead volume rose eleven percent last week.';
      qa.state.turns[0].completed_at=new Date(Date.now()+60000).toISOString();
      qa.startTurnPresentation(qa.state.turns[0]);
      qa.renderChat();
    }''')
    page.wait_for_function("document.querySelector('.voice-preparing') !== null")
    page.evaluate('''() => {
      qa.repairMissedPresentation();
      qa.repairMissedPresentation();
      return qa.maybeSpeakTurn('one');
    }''')
    page.wait_for_timeout(150)
    assert len(requests) == 1
    assert page.evaluate("qa.state.voice.preparingTurns.has('one')") is True


def test_a_second_tab_claiming_the_turn_stops_this_tab_and_blocks_a_re_ask(zen_page):
    page = zen_page
    requests = []
    page.route('**/api/calliope/voice/renders', lambda route: requests.append(route))
    page.evaluate('''() => {
      qa.state.config.answer_presentation={enabled:true};
      qa.state.config.text_to_speech={enabled:true};
      window.WarehouseTheme={getVoice:()=>({mode:'fast',presentation:true})};
      qa.state.turns[0].assistant_message='Lead volume rose eleven percent last week.';
      qa.startTurnPresentation(qa.state.turns[0]);
      qa.renderChat();
    }''')
    page.wait_for_function("document.querySelector('.voice-preparing') !== null")
    assert len(requests) == 1
    # Another tab on the same session announces it is speaking this turn.
    page.evaluate('''() => new Promise((resolve) => {
      const other = new BroadcastChannel('calliope-voice');
      other.postMessage({type:'playing', tab:'zzzz-other', sessionId:'notebook', turnId:'one', renderId:'r2'});
      setTimeout(resolve, 50);
    })''')
    page.wait_for_function("qa.state.voice.phase === 'idle'")
    assert page.evaluate("qa.state.voice.pendingTurns.has('one')") is False
    # The stop released the in-flight preparation; a later re-ask is refused, not re-issued.
    page.wait_for_function("!qa.state.voice.preparingTurns.has('one')")
    page.evaluate("() => { qa.repairMissedPresentation(); return qa.maybeSpeakTurn('one'); }")
    page.wait_for_timeout(100)
    assert len(requests) == 1


def test_voice_tuning_shows_the_tagged_take_and_earlier_takes(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.config.text_to_speech={enabled:true, render_version:1};
      window.WarehouseTheme={getVoice:()=>({mode:'expressive',presentation:true})};
      qa.applyVoicePreferences && qa.applyVoicePreferences();
      qa.state.turns[0].response_receipt={
        presentation:{id:'r2',version:1,mode:'expressive',script:'[excited] Leads rose! [thoughtful] Mostly Meta.',tts_voice:'e'.repeat(32),
                      tts_profile:{voice:'d'.repeat(32),profile_revision:'abcdef123456'},tts_model:'fish',rewrite_elapsed_ms:7620,
                      presentation_memory:{recall_status:'hit'},direction:'slower'},
        presentation_takes:[{id:'r1',version:1,mode:'expressive',script:'[pleased] Leads went up eleven percent.',tts_voice:'d'.repeat(32)}]};
      qa.openVoiceDialog('one');
    }''')
    assert page.evaluate("document.querySelector('#voice-tuning').hidden") is False
    assert page.locator('#voice-tuning-script b').count() == 2                     # cues stand out
    assert '[excited]' in page.locator('#voice-tuning-script').text_content()
    facts = page.locator('#voice-tuning-facts').text_content()
    assert 'custom' in facts and 'rewrite 7620 ms' in facts and 'memory hit' in facts and 'direction on' in facts
    assert page.locator('.voice-tuning-take').count() == 2                         # now + one earlier take
    assert page.locator('[data-play-take="r1"]').count() == 1


def test_voice_tuning_regenerate_and_respeak_send_the_operator_fields(zen_page):
    page = zen_page
    requests = []

    def answer(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=201, content_type='application/json',
                      body='{"render":{"id":"r3","version":1,"mode":"expressive","script":"[calm] New take.","tts_voice":"' + 'e' * 32 + '"},'
                           '"takes":[{"id":"r2","version":1,"mode":"expressive","script":"old take"}],"reused":false}')

    page.route('**/api/calliope/voice/renders', answer)
    page.route('**/api/calliope/voice/turns/**', lambda route: route.abort())
    page.evaluate('''() => {
      qa.state.config.text_to_speech={enabled:true, render_version:1};
      window.WarehouseTheme={getVoice:()=>({mode:'expressive',presentation:true})};
      qa.state.turns[0].response_receipt={presentation:{id:'r2',version:1,mode:'expressive',script:'[excited] Leads rose!'}};
      qa.openVoiceDialog('one');
      document.querySelector('#voice-tuning-voice').value='e'.repeat(32);
      document.querySelector('#voice-tuning-direction').value='slower, land the numbers';
    }''')
    page.evaluate("() => qa.retakeVoiceDialog({respeak:false})")
    page.wait_for_function("qa.state.turns[0].response_receipt.presentation.id === 'r3'")
    assert requests[-1]['regenerate'] is True and 'respeak' not in requests[-1]
    assert requests[-1]['voice_id'] == 'e' * 32 and requests[-1]['direction'] == 'slower, land the numbers'
    assert page.locator('.voice-tuning-take').count() == 2                         # takes from the response
    assert page.evaluate("JSON.parse(localStorage.getItem('rvbbit-calliope-voice-tuning-v1')).recent[0]") == 'e' * 32
    page.evaluate("() => qa.retakeVoiceDialog({respeak:true})")
    page.wait_for_function(f"({len(requests) + 1}) <= {len(requests) + 1}")
    page.wait_for_timeout(150)
    assert requests[-1]['respeak'] is True and 'regenerate' not in requests[-1]
    # The audio fetch was aborted, so the take ended; the buttons must come back on their
    # own, without closing and reopening the dialog.
    page.wait_for_function("qa.state.voice.phase === 'idle'")
    assert page.evaluate("document.querySelector('#voice-tuning-regenerate').disabled") is False
    assert page.evaluate("document.querySelector('#voice-tuning-respeak').disabled") is False


def test_automatic_play_is_marked_and_yields_quietly_when_another_client_is_speaking(zen_page):
    page = zen_page
    audio_requests = []
    page.route('**/api/calliope/voice/renders', lambda route: route.fulfill(
        status=201, content_type='application/json',
        body='{"render":{"id":"r5","version":1,"mode":"expressive","script":"[happy] Hi there."},"reused":false}'))

    def refuse(route):
        audio_requests.append(route.request.url)
        route.fulfill(status=409, content_type='application/json',
                      body='{"error":{"code":"VOICE_TAKE_PLAYING","stage":"audio","message":"Another take of this answer is already being spoken."}}')

    page.route('**/api/calliope/voice/turns/**', refuse)
    page.evaluate('''() => {
      qa.state.config.answer_presentation={enabled:true};
      qa.state.config.text_to_speech={enabled:true, render_version:1};
      window.WarehouseTheme={getVoice:()=>({mode:'expressive',presentation:true})};
      qa.state.turns[0].assistant_message='Hi there.';
      qa.startTurnPresentation(qa.state.turns[0]);
      qa.renderChat();
    }''')
    page.wait_for_function("qa.state.voice.phase === 'idle' && !qa.state.voice.pendingTurns.has('one')")
    assert audio_requests and audio_requests[0].endswith('&auto=1')
    assert page.evaluate("qa.state.voice.failures.has('one')") is False          # no failure chip: someone else is speaking
    # A person's own replay is not automatic.
    page.evaluate("() => qa.playVoiceAudio(qa.state.turns[0], {id:'r5', script:'x'}, {}).catch(()=>{})")
    page.wait_for_function(f"({len(audio_requests)}) >= 1")
    page.wait_for_timeout(100)
    assert not audio_requests[-1].endswith('&auto=1')


def test_estimated_alignment_drives_word_highlighting_with_absolute_offsets(zen_page):
    page = zen_page
    out = page.evaluate('''() => {
      qa.state.config.text_to_speech={enabled:true, render_version:1};
      const turn = qa.state.turns[0];
      const render = {id:'r9', version:1, mode:'expressive', alignment:'estimated', script:'[happy] Hello world.\\n\\nSecond line.'};
      qa.initializeVoiceKaraoke(turn, render, 100);
      const before = qa.state.voice.wordCues.map(c => [c.start, c.end, c.actual]);
      // paragraph 1 timing arrives on the silence frame at stream offset 2.0 s, absolute times 0..1.5
      const chars = '[happy] Hello world.'.split('');
      const starts = chars.map((_, i) => i * 1.5 / chars.length), ends = chars.map((_, i) => (i + 1) * 1.5 / chars.length);
      qa.absorbVoiceAlignment(render, {characters: chars, character_start_times_seconds: starts, character_end_times_seconds: ends, absolute: true},
                              {startAt: 102.0, duration: 0.9}, 2.0);
      return {before, after: qa.state.voice.wordCues.map(c => [c.start, c.end, c.actual]), words: qa.state.voice.wordRanges.map(w => w.text)};
    }''')
    assert out["words"][:2] == ["Hello", "world."] and out["before"][0][2] is False        # cadence estimate first
    hello, world, second = out["after"][0], out["after"][1], out["after"][2]
    assert hello[2] is True and world[2] is True and second[2] is False                    # paragraph 1 snapped, paragraph 2 still cadence
    assert abs(hello[0] - 100.0 - 8 * 1.5 / 20) < 0.02                                    # "Hello" starts after the 8-char cue, on the stream clock
    assert world[1] > hello[1] > hello[0]


def test_finished_plans_from_an_older_turn_fold_away_and_progress_reads_as_a_rail(zen_page):
    """Hermes keeps reporting a run after it finishes, so the panel used to grow
    without bound. A finished plan from an older turn folds into one line; the
    turn in front of you keeps its plans, with progress as a hairline."""
    page = zen_page
    page.evaluate('''() => {
      qa.setupEvents(); qa.state.surfaces=[];
      qa.state.turns=[{id:'one',ordinal:1,turn_kind:'chat',status:'complete',user_message:'a',assistant_message:'a',
                       attachments:[],evidence_refs:[],object_refs:[],response_receipt:{}},
                      {id:'two',ordinal:2,turn_kind:'chat',status:'complete',user_message:'b',assistant_message:'b',
                       attachments:[],evidence_refs:[],object_refs:[],response_receipt:{}}];
      qa.acceptAgentWork({session_id:'notebook',available:true,plans_available:true,observed_at:'2099-01-01T00:00:00Z',
        parent_turns:[],pending:[],activity:[],activity_state:{phase:'team_working'},plans:[
          {key:'run:old-a',run_id:'old-a',turn_id:'one',round:1,specialist:{key:'iris',name:'Iris'},active:true,
           phase:'finished',confirmed:true,revision:1,
           todos:[{id:'x',content:'Old work',status:'completed'}]},
          {key:'run:old-b',run_id:'old-b',turn_id:'one',round:2,specialist:{key:'iris',name:'Iris'},active:true,
           phase:'finished',confirmed:true,revision:1,
           todos:[{id:'y',content:'More old work',status:'completed'}]},
          {key:'run:now',run_id:'now',turn_id:'two',round:1,specialist:{key:'iris',name:'Iris'},active:true,
           phase:'running',confirmed:true,revision:1,
           todos:[{id:'a',content:'Half done',status:'completed'},{id:'b',content:'Still going',status:'in_progress'}]}
        ]});
    }''')
    panel = page.locator('#stage-turn-plan')
    # The newest turn's plan stays; both finished plans from turn one fold into one line.
    assert page.locator('#stage-turn-plan-items > .team-plan-section').count() == 1
    earlier = panel.locator('.team-plan-earlier')
    assert earlier.count() == 1
    assert '2 earlier plans' in earlier.locator('> summary').inner_text()
    assert earlier.locator('.team-plan-section').count() == 2
    # The header counts live work, not everything ever seen.
    assert '1 working' in panel.locator('header').inner_text()
    # Progress is a rail at the real fraction, and a finished plan drops "0 unchecked".
    rail = page.locator('#stage-turn-plan-items > .team-plan-section .team-plan-rail').first
    assert rail.evaluate("el => el.style.getPropertyValue('--plan-done')") == '50%'
    finished_text = earlier.locator('.team-plan-section').first.inner_text()
    assert 'unchecked' not in finished_text and '1 done' in finished_text
    # The run id is gone; turn and round already identify it.
    assert 'old-a' not in panel.inner_text()


def test_the_avatar_voice_toggle_cycles_reading_mode_in_one_click(zen_page):
    """Read aloud lives in the appearance dialog; this is the same setter
    reachable from the chat header. The theme keeps ownership of the value."""
    page = zen_page
    page.evaluate('''() => {
      qa.setupEvents();
      qa.state.config = {...(qa.state.config||{}), text_to_speech:{enabled:true}};
      window.__voiceMode = 'off';
      window.__take = true;
      window.__opened = 0;
      window.WarehouseTheme = {
        getVoice: () => ({version:3, mode: window.__voiceMode, personality:'', progress:false, presentation: window.__take}),
        cycleVoiceMode: () => {
          window.__voiceMode = window.__voiceMode === 'off' ? 'expressive' : 'off';
          return {mode: window.__voiceMode};
        },
        open: () => { window.__opened += 1; },
      };
      qa.applyVoicePreferences(qa.readVoicePreferences());
    }''')
    toggle = page.locator('#chat-voice-toggle')
    page.wait_for_function("() => !document.querySelector('#chat-voice-toggle').hidden")
    assert toggle.get_attribute('data-voice-mode') == 'off'
    assert 'Click to turn it on' in toggle.get_attribute('aria-label')
    toggle.click()
    assert toggle.get_attribute('data-voice-mode') == 'expressive'
    assert 'Read aloud on' in toggle.get_attribute('title')
    toggle.click()
    assert toggle.get_attribute('data-voice-mode') == 'off'      # on and off, never dead-ends
    assert page.evaluate("() => window.__voiceMode") == 'off'    # the theme holds the value
    # Read aloud plays Callie's take: with the take off, the button opens the settings.
    page.evaluate("() => { window.__take = false; qa.applyVoicePreferences(qa.readVoicePreferences()); }")
    page.wait_for_function("() => document.querySelector('#chat-voice-toggle').title.includes('needs Callie')")
    toggle.click()
    assert page.evaluate("() => [window.__voiceMode, window.__opened]") == ['off', 1]
    page.evaluate("() => { window.__take = true; qa.applyVoicePreferences(qa.readVoicePreferences()); }")
    toggle.click()
    page.locator('#chat-agent-picker').screenshot(path='/tmp/calliope-voice-toggle.png')


def test_the_voice_toggle_appears_when_the_config_arrives_after_load(zen_page):
    """The config fetch finishes after the first preference read, so the toggle
    has to re-decide whether speech exists rather than only deciding at load."""
    page = zen_page
    page.evaluate("""() => {
      qa.setupEvents();
      qa.state.config = {...(qa.state.config||{}), text_to_speech:{enabled:false}};
      qa.applyVoicePreferences(qa.readVoicePreferences());
    }""")
    page.wait_for_timeout(50)
    assert page.locator('#chat-voice-toggle').is_hidden()
    page.evaluate("""() => {
      qa.state.config = {...qa.state.config, text_to_speech:{enabled:true}};
      qa.syncVoiceUi();
    }""")
    assert page.locator('#chat-voice-toggle').is_visible()
    # loadConfig is the real moment the answer changes, so it must render too.
    page.evaluate("""() => {
      qa.state.config = {...qa.state.config, text_to_speech:{enabled:false}};
      qa.renderVoiceToggle();
    }""")
    assert page.locator('#chat-voice-toggle').is_hidden()


def test_the_voice_toggle_stays_hidden_where_speech_is_not_configured(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.config = {...(qa.state.config||{}), text_to_speech:{enabled:false}};
      qa.applyVoicePreferences(qa.readVoicePreferences());
    }''')
    page.wait_for_timeout(60)
    assert page.locator('#chat-voice-toggle').is_hidden()


def _rail_index(page):
    """Seed the answerable index the rail matches against, as the API would."""
    page.evaluate('''() => {
      qa.state.composerRail.index={entries:[
        {kind:'metric',name:'gross_revenue',title:'Gross revenue',category:'Finance',aliases:['top line'],
         bindings:[{term:'revenue',gloss:'Booked revenue before refunds'}],description:'Money booked'},
        {kind:'metric',name:'active_seats',title:'Active seats',category:'Product',aliases:[],bindings:[],
         description:'Seats used in the last 30 days'},
        {kind:'artifact',name:'revenue-board',title:'Revenue board',category:'Dashboard',aliases:[],
         bindings:[],description:'Revenue by region and segment',app_kind:'dashboard',version:4},
      ].map(window.CalliopeAnswerable.prepare)};
      qa.state.composerRail.indexAt=Date.now();
      qa.setupEvents();
    }''')


def _draft(page, text):
    page.evaluate(f'''() => {{
      qa.composerSetValue({text!r});
      qa.state.composerRail.query={text!r};
      qa.state.composerRail.applied={text!r};
    }}''')
    page.evaluate('() => { qa.state.composerRail.matches = window.CalliopeAnswerable.matches('
                  'qa.state.composerRail.index, qa.state.composerRail.query, 6); qa.renderComposerRail(); }')


def test_suggestion_rail_stays_hidden_until_the_toggle_is_on(zen_page):
    page = zen_page
    _rail_index(page)
    _draft(page, 'how is revenue doing')
    assert page.locator('#composer-suggestion-rail').is_hidden()
    assert page.evaluate('() => qa.state.composerRail.on') is False
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    assert page.locator('#composer-suggestion-rail').is_visible()
    assert page.locator('#composer-suggestion-rail .composer-rail-tile').count() >= 1


def test_suggestion_rail_leaves_when_the_draft_is_cleared(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    assert page.locator('#composer-suggestion-rail').is_visible()
    _draft(page, '')
    assert page.locator('#composer-suggestion-rail').is_hidden()


def test_picking_a_suggestion_does_not_send_and_rides_the_next_turn(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    before = page.evaluate('() => qa.state.turns.length')
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    assert page.evaluate('() => qa.state.turns.length') == before
    assert page.evaluate('() => qa.composerRailPins()') == [
        {'kind': 'metric', 'ref_id': 'gross_revenue'},
    ]
    tile = page.locator('#composer-suggestion-rail .composer-rail-tile[data-name="metric:gross_revenue"]')
    assert 'is-picked' in tile.get_attribute('class')
    assert tile.locator('[data-rail-pick]').get_attribute('aria-pressed') == 'true'


def test_picked_suggestion_survives_a_draft_that_no_longer_matches_it(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    _draft(page, 'and how many active seats')
    names = page.evaluate('() => qa.composerRailTiles().map(t => t.item.name)')
    # Picks lead, so a chosen metric does not slide away under the new matches.
    assert names[0] == 'gross_revenue'
    assert 'active_seats' in names
    assert page.evaluate('() => qa.composerRailPins().length') == 1


def test_switching_notebooks_drops_the_picks(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    assert page.evaluate('() => qa.composerRailPins().length') == 1
    page.route('**/api/calliope/sessions/other', lambda r: r.fulfill(json={
        'session': {'id': 'other', 'owner_email': 'viewer@example.com', 'title': 'Another'},
        'turns': [], 'surfaces': [], 'agent_work': {'pending': [], 'queued_messages': 0},
    }))
    page.evaluate("qa.selectSession('other',{focusComposer:false})")
    assert page.evaluate('() => qa.composerRailPins()') == []


def test_toggle_counts_the_picks_it_will_carry(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'revenue and active seats')
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    page.locator('#composer-suggestion-rail [data-rail-pick="active_seats"]').click()
    toggle = page.locator('#composer-rail-toggle')
    assert toggle.locator('.label').inner_text() == 'Suggest 2'
    assert 'has-picks' in toggle.get_attribute('class')


def test_rail_clears_the_chat_column_at_any_conversation_width(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    for width in (360, 520, 680):
        page.evaluate(f'() => qa.state.zen ? qa.setChatWidth({width}, false) : null')
        page.evaluate('() => qa.renderComposerRail()')
        box = page.evaluate('''() => {
          const rail=document.getElementById('composer-suggestion-rail').getBoundingClientRect();
          const column=document.getElementById('calliope-chat').getBoundingClientRect();
          return {rail_right: rail.right, column_left: column.left, rail_left: rail.left};
        }''')
        # The rail floats over the Stage, never under the conversation.
        assert box['rail_right'] <= box['column_left'], (width, box)
        assert box['rail_left'] > 0, (width, box)


def test_toggle_appears_when_a_notebook_opens(zen_page):
    page = zen_page
    page.evaluate('() => { qa.state.current = null; qa.renderComposerRailToggle(); }')
    assert page.locator('#composer-rail-toggle').is_hidden()
    page.route('**/api/calliope/sessions/notebook', lambda r: r.fulfill(json={
        'session': {'id': 'notebook', 'owner_email': 'viewer@example.com', 'title': 'Layout check'},
        'turns': [], 'surfaces': [], 'agent_work': {'pending': [], 'queued_messages': 0},
    }))
    page.evaluate("qa.selectSession('notebook',{focusComposer:false})")
    assert page.locator('#composer-rail-toggle').is_visible()


def test_closing_the_rail_drops_what_it_had_pinned(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    assert page.evaluate('() => qa.composerRailPins().length') == 1
    # Nothing stays attached to the message once its only control is gone.
    page.evaluate('() => qa.toggleComposerRail()')
    assert page.evaluate('() => qa.composerRailPins()') == []
    assert page.locator('#composer-suggestion-rail').is_hidden()


def test_rail_ranks_artifacts_beside_metrics(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    kinds = page.evaluate('() => qa.composerRailTiles().map(t => [t.item.kind, t.item.name])')
    assert ['artifact', 'revenue-board'] in kinds
    assert ['metric', 'gross_revenue'] in kinds
    tile = page.locator('#composer-suggestion-rail .composer-rail-tile[data-name="artifact:revenue-board"]')
    assert 'is-artifact' in tile.get_attribute('class')
    # A dashboard has no reading, so it must not render the metric value row.
    assert tile.locator('.composer-rail-value').count() == 0
    assert 'Revenue by region' in tile.inner_text()


def test_picking_an_artifact_pins_it_by_slug(zen_page):
    page = zen_page
    _rail_index(page)
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    page.locator('#composer-suggestion-rail [data-rail-pick="revenue-board"]').click()
    page.locator('#composer-suggestion-rail [data-rail-pick="gross_revenue"]').click()
    assert page.evaluate('() => qa.composerRailPins()') == [
        {'kind': 'artifact', 'ref_id': 'revenue-board'},
        {'kind': 'metric', 'ref_id': 'gross_revenue'},
    ]


def test_a_slug_and_a_metric_name_that_match_stay_separate(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.composerRail.index={entries:[
        {kind:'metric',name:'churn',title:'Churn',category:'Finance',aliases:[],bindings:[],description:'Lost revenue'},
        {kind:'artifact',name:'churn',title:'Churn board',category:'Dashboard',aliases:[],bindings:[],description:'Churn by cohort'},
      ].map(window.CalliopeAnswerable.prepare)};
      qa.state.composerRail.indexAt=Date.now();
      qa.setupEvents();
    }''')
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'what is churn doing')
    page.locator('#composer-suggestion-rail .composer-rail-tile[data-name="metric:churn"] [data-rail-pick]').click()
    # Same identifier, different kinds: pinning one must not mark the other.
    assert page.evaluate('() => qa.composerRailPins()') == [{'kind': 'metric', 'ref_id': 'churn'}]
    other = page.locator('#composer-suggestion-rail .composer-rail-tile[data-name="artifact:churn"]')
    assert 'is-picked' not in other.get_attribute('class')


def test_rail_never_clips_a_tile_and_scrolls_when_it_must(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const rows=[];
      for(let i=1;i<=8;i++)rows.push({kind:'metric',name:'revenue_'+i,title:'Revenue '+i,category:'Finance',aliases:[],bindings:[],description:'revenue '+i});
      for(let i=1;i<=6;i++)rows.push({kind:'metric',name:'seats_'+i,title:'Seats '+i,category:'Product',aliases:[],bindings:[],description:'seats '+i});
      qa.state.composerRail.index={entries:rows.map(window.CalliopeAnswerable.prepare)};
      qa.state.composerRail.indexAt=Date.now();
      qa.setupEvents();
    }''')
    page.evaluate('() => qa.toggleComposerRail()')
    _draft(page, 'how is revenue doing')
    six = page.evaluate('''() => {
      const host=document.getElementById('composer-suggestion-rail'),r=host.getBoundingClientRect();
      const tiles=[...host.querySelectorAll('.composer-rail-tile')];
      return {count:tiles.length,overflowing:host.classList.contains('is-overflowing'),
        clipped:tiles.filter(n=>{const b=n.getBoundingClientRect();return b.top<r.top-1||b.bottom>r.bottom+1}).length,
        overflowY:getComputedStyle(host).overflowY};
    }''')
    # A full set of matches fits without a cut; six is the everyday case.
    assert six['count'] == 6 and six['clipped'] == 0 and six['overflowing'] is False, six
    assert six['overflowY'] == 'auto'
    # Pin five, then draft something else so fresh matches stack above them.
    page.evaluate('''() => { for (const m of qa.state.composerRail.matches.slice(0,5)) qa.toggleComposerRailPick(m.item.name, m.item.kind); }''')
    _draft(page, 'how many seats')
    more = page.evaluate('''() => {
      const host=document.getElementById('composer-suggestion-rail'),r=host.getBoundingClientRect();
      const top=host.querySelector('.composer-rail-tile:last-child');
      const beforeScroll=top.getBoundingClientRect().top<r.top-1;
      host.scrollTop=-host.scrollHeight;
      const b=top.getBoundingClientRect();
      return {count:host.querySelectorAll('.composer-rail-tile').length,overflowing:host.classList.contains('is-overflowing'),
        hiddenBeforeScroll:beforeScroll,reachable:b.top>=r.top-1&&b.bottom<=r.bottom+1};
    }''')
    # More than fit: the rail says so and the far tile can be scrolled to.
    assert more['count'] > 6, more
    assert more['overflowing'] is True and more['hiddenBeforeScroll'] is True and more['reachable'] is True, more


def test_trend_delta_is_one_shared_answer(zen_page):
    page = zen_page
    cases = page.evaluate('''() => {
      const d=window.CalliopeAnswerable.trendDelta;
      return [d({direction:'up',percent:3.2,meaning:'good'}),d({direction:'down',percent:-1.44}),
              d({direction:'flat',percent:0}),d({direction:'up',percent:'oops'}),d(null)];
    }''')
    assert [c['text'] for c in cases] == ['▲ 3.2%', '▼ 1.4%', '– 0.0%', '', '']
    assert cases[0]['meaning'] == 'good' and cases[1]['meaning'] == 'neutral'
    # A tile on the rail and a card on the Floor render the same chip text.
    _rail_index(page)
    page.evaluate('''() => {
      qa.state.composerRail.values.set('gross_revenue',{name:'gross_revenue',value:5,display:{},
        trend:{direction:'up',percent:3.2,meaning:'good',basis:'vs last week'},series:[],observed_at:new Date().toISOString(),at:Date.now()});
      qa.toggleComposerRail();
    }''')
    _draft(page, 'how is revenue doing')
    chip = page.locator('#composer-suggestion-rail .composer-rail-tile[data-name="metric:gross_revenue"] .composer-rail-delta')
    assert chip.inner_text() == '▲ 3.2%'
    assert chip.get_attribute('title') == 'vs last week'


@pytest.mark.parametrize("zen", [True, False])
def test_rewritten_reply_keeps_progress_without_flashing_the_final_draft(zen_page, zen):
    page = zen_page
    page.evaluate("zen => {qa.state.zen=zen;document.body.classList.toggle('zen',zen)}", zen)
    page.evaluate("""() => {
      qa.state.surfaces=[];
      qa.state.config.answer_presentation={enabled:true,render_version:1};
      qa.state.voice.preferences={mode:'off',presentation:true};
      const turn=qa.state.turns[0];
      turn.status='running';turn.assistant_message='';turn.working_draft='SECRET RAW DRAFT';
      qa.state.activeRuns.set('notebook',{sessionId:'notebook',pending:turn,status:'running',startedAt:Date.now(),stageTools:[]});
      qa.renderChat();
    }""")
    body = page.locator('[data-assistant-turn-id="one"] .message-body')
    assert 'SECRET RAW DRAFT' not in body.inner_text()
    assert 'Composing the reply' in body.inner_text()
    page.evaluate("qa.commitWorkingUpdate(qa.state.turns[0], 'I found the records; checking totals next.')")
    page.wait_for_function("document.querySelector('[data-assistant-turn-id=one] .message-body').textContent.includes('I found the records; checking totals next.')")
    assert 'I found the records; checking totals next.' in body.inner_text()
    assert 'Progress update' in body.text_content()
    assert not body.locator('.voice-preparing').count()
    body.screenshot(path=f'/tmp/voice-progress-{zen}.png')
    page.evaluate("qa.setWorkingDraft(qa.state.turns[0], 'ANOTHER RAW DELTA')")
    page.wait_for_timeout(80)
    assert 'ANOTHER RAW DELTA' not in body.inner_text()
    # Completion can be rendered before the asynchronous rewrite request is queued.
    page.evaluate("qa.state.turns[0].status='complete';qa.state.turns[0].assistant_message='SECRET FINAL';qa.state.voice.pendingTurns.add('one');qa.renderChat()")
    assert 'SECRET FINAL' not in body.inner_text()
    assert 'Shaping the reply' in body.inner_text()
    page.evaluate("qa.state.voice.failures.set('one',{stage:'text',message:'Try again'});qa.renderChat()")
    assert 'SECRET FINAL' in body.inner_text()  # Failure always restores the useful answer.


def test_tool_trace_curves_scroll_without_replacing_the_plot(zen_page):
    page = zen_page
    page.evaluate("""() => {
      qa.state.surfaces=[];
      const turn=qa.state.turns[0];turn.status='running';turn.assistant_message='';
      qa.state.activeRuns.set('notebook',{sessionId:'notebook',pending:turn,status:'running',startedAt:Date.now(),
        stageTools:[{status:'active',toolName:'run_sql'}],toolTraceSamples:[0,1,3,2,0,1]});
      qa.renderChat();qa.syncTurnClocks();
      window.savedToolPlot=document.querySelector('[data-tool-trace-plot]');
    }""")
    plot = page.locator('[data-tool-trace-plot]').first
    assert ' C' in plot.locator('path').first.get_attribute('d')
    page.wait_for_function("savedToolPlot.getAttribute('transform')?.startsWith('translate(-')")
    before = plot.get_attribute('transform')
    page.wait_for_function("previous => savedToolPlot.getAttribute('transform') !== previous", arg=before)
    page.wait_for_timeout(400)
    page.evaluate('qa.syncTurnClocks()')
    assert page.evaluate("savedToolPlot === document.querySelector('[data-tool-trace-plot]')")
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(60)
    frozen = plot.get_attribute('transform')
    page.wait_for_timeout(100)
    assert plot.get_attribute('transform') == frozen


@pytest.mark.parametrize("interleave", ["none", "same_thread", "next_message", "saved_rewrite"])
def test_automatic_voice_reaches_pcm_playback_after_reveal(zen_page, interleave):
    import base64
    import json
    page = zen_page
    events=[]
    def lifecycle(route):
        events.append(route.request.post_data_json)
        route.fulfill(status=204)
    page.route('**/api/calliope/turn-lifecycle', lifecycle)
    audio_requests = []
    pending_audio = []
    rewrites = []
    page.route('**/api/calliope/voice/renders', lambda r: rewrites.append(r))
    def audio(route):
        audio_requests.append(route.request.url)
        pending_audio.append(route)
    def deliver_audio(route):
        route.fulfill(headers={'x-calliope-audio-protocol': 'timed-pcm-ndjson-v1', 'x-audio-sample-rate': '24000'},
                      content_type='application/x-ndjson', body=json.dumps({
                          'type': 'audio', 'audio_base64': base64.b64encode(b'\x10\x01' * 24000).decode()})+'\n')
    page.route('**/api/calliope/voice/turns/**', audio)
    page.evaluate('''interleave => {
      qa.state.surfaces=[];
      qa.state.config.answer_presentation={enabled:true,render_version:1};
      qa.state.config.text_to_speech={enabled:true,render_version:1};
      window.WarehouseTheme={getVoice:()=>({mode:'expressive',presentation:true})};
      qa.applyVoicePreferences();
      window.playedSources=[];
      window.playbackAnnouncements=[];
      BroadcastChannel.prototype.postMessage=function(message){playbackAnnouncements.push(message)};
      qa.state.voice.context={state:'running',currentTime:0,destination:{},
        createBuffer:(channels,frames,rate)=>({duration:frames/rate,copyToChannel(){}}),
        createBufferSource:()=>{const source={connect(){},disconnect(){},start(){playedSources.push(this)},stop(){}};return source;}};
      qa.state.turns[0].status='running';qa.state.turns[0].assistant_message='';
      qa.renderChat();
      qa.state.turns[0].status='complete';qa.state.turns[0].assistant_message='Canonical answer';
      if(interleave==='saved_rewrite') qa.state.turns[0].response_receipt={presentation:{id:'pcm-take',version:1,script:'[happy] Ready to go.'}};
      const completed=qa.state.turns[0];
      qa.state.turns=[...Array.from({length:60},(_,i)=>({id:'history-'+i,ordinal:i-60,status:'complete',
        user_message:'Previous question',assistant_message:'Earlier answer. '.repeat(30),created_at:new Date().toISOString()})),completed];
      qa.startTurnPresentation(completed);qa.renderChat();
    }''', interleave)
    page.wait_for_timeout(150)
    assert len(rewrites) == 1
    if interleave == 'same_thread':
        payload=page.evaluate('({session:qa.state.current,turns:qa.state.turns,surfaces:[]})')
        page.route('**/api/calliope/sessions/notebook',lambda r:r.fulfill(json=payload))
        page.evaluate("qa.selectSession('notebook',{focusComposer:false})")
    elif interleave == 'next_message':
        turns=[]
        page.route('**/api/calliope/sessions/notebook/turn', lambda r:turns.append(r))
        page.evaluate("void qa.sendTurn({messageOverride:'Keep going',expectedSessionId:'notebook'})")
        page.wait_for_timeout(150)
        assert turns, 'The next turn really was submitted'
    rewrites[0].fulfill(json={'render': {'id':'pcm-take','version':1,'mode':'expressive','script':'[happy] Ready to go.'}})
    # The audio request follows the rewrite after the Stage re-renders for the
    # switched thread; wait for it rather than for a fixed slice of time.
    for _ in range(20):
        if pending_audio:
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(100)
    assert len(pending_audio) == 1
    assert page.evaluate('playbackAnnouncements.length') == 0, 'A pending audio request must not silence another tab'
    deliver_audio(pending_audio[0])
    page.wait_for_function('playedSources.length === 1 && qa.state.voice.streamComplete')
    assert page.evaluate('playbackAnnouncements.length') == 1
    assert audio_requests
    assert page.evaluate('qa.state.voice.phase') == 'streaming'
    assert 'Ready to go.' in page.locator('[data-assistant-turn-id="one"] .message-body').inner_text()
    assert not page.locator('.voice-preparing').count()
    page.evaluate('playedSources[0].onended()')
    page.wait_for_function("qa.state.voice.phase === 'idle'")
    assert page.evaluate('qa.state.voice.lastFinishedTurnId') == 'one'
    page.wait_for_timeout(100)
    assert [e['event'] for e in events if e['turn_id']=='one'] == [
        'voice-queued','voice-rewritten','voice-audio-request','voice-first-audio','voice-played']
    assert all('script' not in e and 'message' not in e for e in events)


def test_a_working_update_shows_callies_take_once_it_lands(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.config.answer_presentation={enabled:true};
      window.WarehouseTheme={getVoice:()=>({version:3,mode:'off',presentation:true,personality:'',progress:false})};
      qa.applyVoicePreferences(qa.readVoicePreferences());
      Object.assign(qa.state.turns[0], {status:'running', assistant_message:'',
        working_updates:['Checking the  orders table now.'], working_take_notes:[]});
      qa.renderChat();
    }''')
    note = page.locator('[data-assistant-turn-id="one"] .voice-progress-note').last
    assert note.inner_text() == 'Checking the  orders table now.'   # the original until the take lands
    page.evaluate("() => qa.rememberTakeNote('one', 'Checking the orders table now.', '[curious] Off to the orders table!')")
    page.wait_for_function("() => document.querySelector('[data-assistant-turn-id=one] .voice-progress-note').textContent.includes('Off to')")
    assert note.inner_text() == 'Off to the orders table!'   # tags are delivery, not words


def test_a_finishing_answer_never_flashes_before_its_take(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.surfaces=[];
      qa.state.config.answer_presentation={enabled:true};
      window.WarehouseTheme={getVoice:()=>({version:3,mode:'off',presentation:true,personality:'',progress:false})};
      qa.applyVoicePreferences(qa.readVoicePreferences());
      const turn=qa.state.turns[0];
      Object.assign(turn,{status:'running',assistant_message:'',response_receipt:{}});
      qa.expectTake(turn);                       // flagged before the chat is redrawn
      Object.assign(turn,{status:'complete',assistant_message:'The plain, unrewritten answer.'});
      qa.renderChat();
    }''')
    body = page.locator('[data-assistant-turn-id="one"] .message-body')
    assert 'The plain, unrewritten answer.' not in body.inner_text()
    assert body.locator('.voice-preparing').count() == 1


def test_a_spoken_update_waits_for_the_answer_being_read(zen_page):
    page = zen_page
    requests = []
    page.route('**/api/calliope/voice/progress', lambda route: (requests.append(route), route.abort()))
    page.evaluate('''() => {
      qa.state.voice.phase='streaming';
      qa.state.voice.progressQueue.push({sessionId:'notebook',turnId:'one',text:'Checking orders.',audio:true});
      qa.processProgressVoiceQueue();
    }''')
    page.wait_for_timeout(700)
    assert requests == []                       # an answer is still being read
    page.evaluate("() => { qa.state.voice.phase='idle'; }")
    page.wait_for_timeout(700)
    assert len(requests) == 1


def test_stacked_voice_clips_get_a_short_pause_between_them(zen_page):
    page = zen_page
    starts = page.evaluate('''() => {
      const context = {currentTime: 10};
      const first = qa.voiceClipStartAt(context);          // nothing played yet: start right away
      qa.noteVoiceClipEnd(12);                             // a clip is scheduled to end at 12 s
      const next = qa.voiceClipStartAt(context);           // the next one waits for it, plus a breath
      context.currentTime = 20;
      const later = qa.voiceClipStartAt(context);          // long after: no added wait
      context.currentTime = 11;
      qa.state.voice.sources.add({stop(){}, disconnect(){}});
      qa.stopVoicePlayback();                              // someone stopped it: no pause behind it
      const afterStop = qa.voiceClipStartAt(context);
      return [first, next, later, afterStop];
    }''')
    assert starts == [pytest.approx(10.045), pytest.approx(12.4), pytest.approx(20.045), pytest.approx(11.045)]
