from test_zen_chat_layout import zen_page
from test_floor_metrics_browser import metrics_site
import pytest

SPEECH = {'provider':'openai-api','model':'gpt-live-transcribe','models':['gpt-live-transcribe'],
          'revision':'first','credential_available':True,'enabled':True,'realtime':{'enabled':False}}


def test_speech_save_preserves_staged_agent_edits_and_unavailable_provider(zen_page):
    page = zen_page
    writes = []
    providers = {'providers':[{'provider':'openai-api','display_name':'OpenAI API','auth_state':'configured','auth_type':'api_key'}],
                 'models':[{'provider':'openai-api','model':'test-model','display_name':'Test model'}],
                 'agents':[{'key':'default','name':'Calliope','provider':'openai-api','model':'test-model'},
                           {'key':'iris','name':'Iris','provider':'temporarily-missing','model':'keep-this-model'}],
                 'inventory_initialized':True}
    info = dict(SPEECH)
    def api(route):
        path = route.request.url.split('?')[0]
        if route.request.method == 'POST':
            writes.append((path, route.request.post_data_json))
            assert path.endswith('/admin/providers/speech')
            info.update(route.request.post_data_json, revision='second')
            route.fulfill(json=info)
        elif path.endswith('/speech'):
            route.fulfill(json=info)
        else:
            route.fulfill(json=providers)
    page.route('**/api/calliope/admin/providers**', api)
    page.route('**/api/calliope/speech', api)
    page.evaluate('''({providers,speech}) => {
      qa.state.config.speech_to_text=speech;
      qa.state.providerSettings.data=providers;
      qa.state.providerSettings.agentEdits={default:{provider:'openai-api',model:'changed-model'}};
      qa.state.expertPortraits={error:true};
      qa.setupEvents();qa.renderProviderSettings();
      qa.els.providerSettingsDialog.showModal();
    }''', {'providers':providers,'speech':SPEECH})
    assert page.locator('[data-agent-provider="iris"]').input_value() == 'temporarily-missing'
    assert page.evaluate('qa.agentEdit(qa.state.providerSettings.data.agents[1]).model') == 'keep-this-model'
    assert page.evaluate('qa.pendingAgentChanges().map(x=>x.agent)') == ['default']
    page.locator('[data-speech-provider]').select_option('off')
    page.locator('[data-speech-save]').click()
    page.wait_for_function('document.querySelector("#provider-settings-status").textContent === "Dictation saved."')
    assert len(writes) == 1
    assert writes[0][1] == {'provider':'off','model':'gpt-live-transcribe','revision':'first'}
    assert page.evaluate('qa.state.providerSettings.agentEdits.default.model') == 'changed-model'
    assert page.evaluate('qa.pendingAgentChanges().map(x=>x.agent)') == ['default']
    page.set_viewport_size({'width':1366,'height':768})
    page.screenshot(path='/tmp/calliope-dictation-settings.png')
    assert page.locator('[data-speech-settings]').evaluate('(e)=>e.scrollWidth <= e.clientWidth+1')


FAKE_MIC = '''() => {
  Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{stop(){}}]})},configurable:true});
  window.MediaRecorder=class extends EventTarget {
    static isTypeSupported(){return true}
    constructor(){super();this.state='inactive';this.mimeType='audio/webm'}
    start(){this.state='recording'}
    stop(){this.state='inactive';queueMicrotask(()=>{
      this.dispatchEvent(Object.assign(new Event('dataavailable'),{data:new Blob(['audio fixture'])}));
      this.dispatchEvent(new Event('stop'));
    })}
  };
}'''


def test_floor_dictation_inserts_draft_without_creating_chat(metrics_site):
    pw = pytest.importorskip('playwright.sync_api')
    url, state = metrics_site
    state['responses']={'/api/calliope/speech':SPEECH}
    with pw.sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script('('+FAKE_MIC+')()')
        page.route('**/api/calliope/transcriptions',lambda r:r.fulfill(json={'text':'Show me submitted leads.'}))
        page.goto(url+'/floor')
        page.locator('#floorIntakeOpen').click()
        page.locator('#floorIntakeDictate').click()
        page.wait_for_function('document.querySelector("#floorIntakeDictate").textContent === "Stop dictation"')
        assert page.locator('#floorIntakeSend').is_disabled()
        page.locator('#floorIntakeDictate').click()
        page.wait_for_function('document.querySelector("#floorIntakeInput").value.includes("submitted leads")')
        assert page.locator('#floorIntakeSend').is_enabled()
        assert page.locator('#floorIntakeDictateCancel').is_hidden()
        assert not state['posts']
        page.screenshot(path='/tmp/calliope-floor-dictation.png')
        assert not errors
        browser.close()


def prepare_chat_dictation(page, draft='', use_editor=False, start=True):
    page.evaluate(FAKE_MIC)
    page.evaluate('''async ({draft,useEditor,startRecording}) => {
      qa.state.surfaces=[];qa.renderChat();qa.setupEvents();
      qa.state.config.speech_to_text={enabled:true,max_audio_seconds:600,realtime:{enabled:false}};
      qa.els.input.disabled=false;qa.els.input.value=draft;
      qa.els.input.setSelectionRange(draft.length,draft.length);
      if(useEditor) qa.initializeComposerEditor();
      if(startRecording) await qa.startSpeechRecording({kind:'chat',sessionId:'notebook',selection:{from:draft.length,to:draft.length}});
      else qa.syncSpeechControls();
    }''',{'draft':draft,'useEditor':use_editor,'startRecording':start})


@pytest.mark.parametrize('mode',['dark','light'])
def test_chat_dictation_is_inside_input_and_finish_leaves_a_draft(zen_page,mode):
    page=zen_page
    page.evaluate('''mode => {
      document.documentElement.dataset.theme=mode;
      if(mode==='light'){
        for(const [name,value] of Object.entries({panel:'#eee8dc',bone:'#28322b','bone-bright':'#17231b',dim:'#687269',jade:'#276c66'}))
          document.documentElement.style.setProperty('--'+name,value);
      }
    }''',mode)
    uploads=[]
    page.route('**/api/calliope/transcriptions',lambda r:(uploads.append(r.request.url),r.fulfill(json={'text':'Compare the last three quarters.'})))
    prepare_chat_dictation(page)
    assert page.locator('.compose-box #speech-preview').is_visible()
    assert page.locator('#message-input').is_hidden()
    assert page.locator('#speech-record').is_hidden()
    assert page.locator('#send-message').is_disabled()
    assert page.locator('#speech-status').inner_text()=='Listening'
    assert page.locator('.dictation-controls').bounding_box()['height'] < 40
    # Exercise a live partial while the shared recorder remains active.
    page.evaluate("qa.state.speech.liveTranscript='Compare the last three quarters, and show me what changed in revenue.'")
    page.wait_for_function("document.querySelector('#speech-preview p').textContent.includes('revenue')")
    assert page.locator('#message-input').input_value()==''
    page.mouse.move(20,20)
    page.screenshot(path=f'/tmp/dictation-composer-{mode}.png')
    page.locator('#speech-finish').click()
    page.wait_for_function("qa.state.speech.phase==='idle'")
    assert page.locator('#message-input').input_value()=='Compare the last three quarters.'
    assert page.locator('#speech-preview').is_hidden()
    assert page.locator('#send-message').is_enabled()
    assert not page.locator('#message-input').evaluate('(n)=>n.inert')
    assert len(uploads)==1
    assert page.evaluate('qa.state.turns.length')==1


def test_chat_discard_preserves_existing_draft_and_fits_narrow_input(zen_page):
    page=zen_page
    uploads=[]
    page.route('**/api/calliope/transcriptions',lambda r:(uploads.append(r.request.url),r.abort()))
    page.set_viewport_size({'width':390,'height':844})
    page.evaluate("document.body.classList.add('mobile-chat-open')")
    page.emulate_media(reduced_motion='reduce')
    prepare_chat_dictation(page,'Keep this draft.')
    assert page.locator('#message-input').is_visible()
    assert page.locator('#message-input').evaluate('(n)=>n.inert')
    assert page.locator('.dictation-controls').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
    assert page.locator('#speech-preview').evaluate('(n)=>getComputedStyle(n).animationName')=='none'
    page.locator('#speech-discard').click()
    page.wait_for_function("qa.state.speech.phase==='idle'")
    assert page.locator('#message-input').input_value()=='Keep this draft.'
    assert uploads==[]
    assert page.locator('#speech-record').evaluate('(n)=>n===document.activeElement')


def test_chat_dictation_keeps_the_codemirror_document_mounted(zen_page):
    page=zen_page
    page.route('**/api/calliope/transcriptions',lambda r:r.fulfill(json={'text':'by region'}))
    prepare_chat_dictation(page,'Compare revenue',use_editor=True)
    assert page.locator('#message-editor .cm-editor').count()==1
    page.evaluate('window.originalEditor=qa.state.composerEditor')
    assert page.locator('#message-editor').evaluate('(n)=>n.inert')
    page.locator('#speech-finish').click()
    page.wait_for_function("qa.state.speech.phase==='idle'")
    assert page.evaluate('qa.state.composerEditor===window.originalEditor')
    assert page.evaluate('qa.state.composerEditor.getValue()')=='Compare revenue by region'
    assert not page.locator('#message-editor').evaluate('(n)=>n.inert')


@pytest.mark.parametrize('use_editor',[False,True])
def test_dictation_shortcut_starts_finishes_and_advertises_itself(zen_page,use_editor):
    page=zen_page
    uploads=[]
    page.route('**/api/calliope/transcriptions',lambda r:(uploads.append(r.request.url),r.fulfill(json={'text':'by region'})))
    prepare_chat_dictation(page,'Compare revenue',use_editor=use_editor,start=False)
    editor=page.locator('#message-editor .cm-content' if use_editor else '#message-input')
    editor.click()
    page.keyboard.press('End')
    assert 'Alt + Shift + D' in page.locator('#speech-record').get_attribute('title')
    assert page.locator('#speech-record').get_attribute('aria-keyshortcuts')=='Alt+Shift+D'
    page.keyboard.press('Alt+Shift+d')
    page.wait_for_function("qa.state.speech.phase==='recording'")
    assert page.locator('#message-input').input_value()=='Compare revenue'
    assert 'Alt + Shift + D' in page.locator('#speech-finish').get_attribute('title')
    assert page.locator('#speech-finish').get_attribute('aria-keyshortcuts')=='Alt+Shift+D'
    # Holding the shortcut and composition events must not immediately stop it.
    page.evaluate('''() => {
      for(const flags of [{repeat:true},{isComposing:true}])
        document.dispatchEvent(new KeyboardEvent('keydown',{key:'D',code:'KeyD',altKey:true,shiftKey:true,bubbles:true,...flags}));
    }''')
    assert page.evaluate('qa.state.speech.phase')=='recording'
    page.keyboard.press('Alt+Shift+d')
    page.wait_for_function("qa.state.speech.phase==='idle'")
    assert page.locator('#message-input').input_value()=='Compare revenue by region'
    assert len(uploads)==1
    assert page.evaluate('qa.state.turns.length')==1
    assert page.locator('#send-message').is_enabled()


def test_dictation_shortcut_respects_other_editors_dialogs_and_busy_states(zen_page):
    page=zen_page
    prepare_chat_dictation(page,start=False)
    page.evaluate('''() => {
      window.micStarts=0;const original=navigator.mediaDevices.getUserMedia;
      navigator.mediaDevices.getUserMedia=async()=>{micStarts++;return original()};
    }''')
    page.locator('#evidence-query').evaluate('(n)=>n.disabled=false')
    page.locator('#evidence-query').focus()
    assert page.locator('#evidence-query').evaluate('(n)=>n===document.activeElement')
    page.keyboard.press('Alt+Shift+d')
    assert page.evaluate('micStarts')==0
    page.locator('#message-input').focus()
    page.keyboard.press('d')
    assert page.locator('#message-input').input_value()=='d'
    page.evaluate('''() => {
      window.shortcutModal=document.createElement('dialog');
      shortcutModal.innerHTML='<button>Close</button>';document.body.append(shortcutModal);shortcutModal.showModal();
    }''')
    page.keyboard.press('Alt+Shift+d')
    assert page.evaluate('micStarts')==0
    page.evaluate('shortcutModal.close();shortcutModal.remove()')
    page.locator('#message-input').focus()
    page.evaluate('''() => {
      const press=()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'D',code:'KeyD',altKey:true,shiftKey:true,bubbles:true}));
      for(const phase of ['requesting','transcribing','cancelling']){qa.state.speech.phase=phase;press();}
      qa.state.speech.phase='recording';qa.state.speech.target={kind:'daily_note',surfaceId:'note'};press();
      window.notePhase=qa.state.speech.phase;
      qa.state.speech.phase='idle';qa.state.speech.target=null;
      qa.state.current.read_only=true;press();qa.state.current.read_only=false;
      qa.state.busy=true;press();qa.state.busy=false;
      qa.els.input.disabled=true;press();qa.els.input.disabled=false;
    }''')
    assert page.evaluate('micStarts')==0
    assert page.evaluate('notePhase')=='recording'
