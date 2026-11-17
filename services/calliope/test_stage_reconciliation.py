"""Exercise the real Stage renderer and iframe lifecycle in Chromium."""
from pathlib import Path
import pytest

SOURCE = (Path(__file__).parent/'calliope/calliope.js').read_text()


def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


@pytest.fixture
def stage_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        loads = []
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def route(r):
            if '/artifact/' in r.request.url:
                loads.append(r.request.url)
                r.fulfill(content_type='text/html', body='<input value="original"><script>window.documentIdentity=Math.random()</script>')
            else:
                r.fulfill(content_type='text/html', body='<style>#stageScroll{height:650px;overflow:auto}iframe{width:600px;height:100px}.surface{padding:4px}</style><div id="stageScroll"><div id="stage"></div></div><span id="surfaceCount"></span>')
        page.route('**/*', route)
        page.goto('http://stage.test')
        page.add_script_tag(content='''
          const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
          const els={stage:$('#stage'),stageScroll:$('#stageScroll'),surfaceCount:$('#surfaceCount')};
          const state={current:{id:'one'},turns:[{id:'turn',status:'complete'}],surfaces:[],zen:false,
            brief:{noteEditors:new Map()},speech:{phase:'idle'},valueSystemViews:new Map(),
            artifactFrameHeights:new Map(),artifactFrameObserver:null,artifactFrameUnloadTimers:new Map(),
            artifactQueryControllers:new Map(),artifactEdit:{},zenExpandedSurfaces:new Set()};
          const SETUP_MODE=false,ARTIFACT_FRAME_UNLOAD_DELAY_MS=60000;
          let workflowNodeTooltipTarget=null,desktopStage=null;
          function escapeHtml(s){return String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;')}
          function hideWorkflowNodeTooltip(){}
          function intakeActive(){return false}
          function visibleStageSurfaces(){return state.surfaces}
          function surfacesForTurn(id){return state.surfaces.filter(s=>s.turn_id===id)}
          function stratumHeadMarkup(turn){return '<header>'+turn.status+'</header>'}
          function zenFoldedSurface(){return false}
          function archivedEvidenceSurfaces(surfaces){return surfaces}
          function surfacePinned(){return false}
          function spotlightFor(){return false}
          function compareStageSurfaces(){return 0}
          function zenTurnTagText(){return 'Turn 1'}
          function surfaceCard(s){return `<article class="surface kind-${s.kind}" data-surface-id="${s.id}"><header><h3>${s.title}</h3><template class="calliope-tooltip-source"><h4>${s.title}</h4></template></header><div class="surface-body">${s.kind==='artifact'
            ? `<div class="artifact-frame" data-frame-state="dormant"><iframe title="${s.title}" data-artifact-src="${s.url}" data-artifact-active="false"></iframe></div>`
            : `<p>${s.text||''}</p><details><summary>Details</summary>Keep open</details><input value=""><div data-brief-notes data-surface-id="${s.id}"><div data-brief-note-editor></div></div><div data-document-preview-url="/doc">Loading</div>`}</div></article>`}
          for(const name of ['renderSketchShelf','syncGoogleSheetImportControls','renderStageWorkingSet',
            'syncStageEmptyVisibility','syncComputerControls','syncStageEmptyHeadlineRotation',
            'applyCommandLibraryView','syncArtifactTextEdit','hydrateDocumentPreviews','hydrateEvidenceThumbnails','hydrateChartSurfaces','hydrateQueryCharts',
            'syncEvidenceSelectionCards','initializeCubeBuilders','initializeBriefNoteEditors'])window[name]=()=>{};
          function cancelSpeechRecording(){state.speech.phase='idle'}
        ''')
        page.add_script_tag(content=section('  function surfaceIsImageFile(', '  // A specialist is a participant'))
        page.add_script_tag(content=section('  function captureStageInputDrafts(', '  function renderInputRequest('))
        page.add_script_tag(content=section('  function artifactFrameSurfaceId(', '  async function sendViewerThemeToArtifact('))
        page.add_script_tag(content=section('  function currentThreadPage(', '  let threadPageTimer'))
        page.add_script_tag(content=section('  function livingPageMarkup(', '  async function refreshThreadPage('))
        page.add_script_tag(content=section('  const stageRenderTrees =', '  function evidenceItem('))
        try:
            yield page, loads
            assert not errors
        finally:
            browser.close()


@pytest.mark.parametrize('zen', [False, True])
def test_stage_preserves_live_frames_and_updates_only_changed_content(stage_page, zen):
    page, loads = stage_page
    page.evaluate('''zen=>{
      state.zen=zen;
      state.surfaces=[{id:'dashboard',turn_id:'turn',kind:'artifact',title:'Dashboard',url:'/artifact/v1'},
        {id:'note',turn_id:'turn',kind:'document',title:'Note',text:'Before'}];
      renderStage();
    }''', zen)
    page.wait_for_function("document.querySelector('iframe').contentWindow.documentIdentity")
    page.evaluate('''()=>{
      window.keptFrame=document.querySelector('iframe');window.keptWindow=keptFrame.contentWindow;
      window.identity=keptWindow.documentIdentity;keptWindow.document.querySelector('input').value='Keep this filter';
      window.keptNote=document.querySelector('[data-surface-id=note]');
      keptNote.querySelector('input').value='Unsent draft';keptNote.querySelector('input').focus();
      keptNote.querySelector('input').setSelectionRange(2,5);keptNote.querySelector('details').open=true;
      keptNote.querySelector('[data-document-preview-url]').innerHTML='<b>Hydrated document</b>';
      window.destroyed=0;state.brief.noteEditors.set('note',{destroy(){destroyed++}});
      window.keptObserver=state.artifactFrameObserver;
      window.aborted=0;state.artifactQueryControllers.set('dashboard',new Set([{abort(){aborted++}}]));
      window.mutations=[];window.watcher=new MutationObserver(rows=>mutations.push(...rows));
      watcher.observe(document.querySelector('#stage'),{childList:true,attributes:true,characterData:true,subtree:true});
      renderStage();renderStage();
    }''')
    page.wait_for_timeout(80)
    assert page.evaluate('mutations.length') == 0
    page.evaluate('''()=>{
      state.surfaces[1].text='After';state.surfaces[0].title='Renamed dashboard';renderStage();
    }''')
    assert page.locator('[data-surface-id=note] p').inner_text() == 'After'
    assert page.locator('[data-surface-id=dashboard] h3').inner_text() == 'Renamed dashboard'
    assert page.locator('[data-surface-id=dashboard] template').evaluate('(t)=>t.content.textContent') == 'Renamed dashboard'
    assert page.evaluate('''()=>document.querySelector('iframe')===keptFrame
      && keptFrame.contentWindow===keptWindow && keptWindow.documentIdentity===identity
      && keptWindow.document.querySelector('input').value==='Keep this filter'
      && keptNote.querySelector('input').value==='Unsent draft'
      && document.activeElement===keptNote.querySelector('input')
      && keptNote.querySelector('input').selectionStart===2
      && keptNote.querySelector('details').open && destroyed===0
      && keptNote.querySelector('[data-document-preview-url]').textContent==='Hydrated document'
      && state.artifactFrameObserver===keptObserver && aborted===0''')
    page.evaluate('''()=>{
      state.surfaces.unshift({id:'new',turn_id:'turn',kind:'document',title:'New'});renderStage();
      state.surfaces.reverse();renderStage();
    }''')
    page.wait_for_timeout(80)
    assert page.evaluate('keptFrame.contentWindow.documentIdentity===identity')
    assert len(loads) == 1
    page.evaluate("state.surfaces.find(s=>s.id==='dashboard').url='/artifact/v2';renderStage()")
    page.wait_for_function("document.querySelector('iframe').contentWindow.documentIdentity && document.querySelector('iframe')!==keptFrame")
    assert len(loads) == 2
    assert page.evaluate('aborted') == 1
    page.evaluate("state.surfaces=state.surfaces.filter(s=>s.id!=='note');renderStage()")
    assert page.evaluate('destroyed') == 1
    page.evaluate("window.frameTwo=document.querySelector('iframe');state.current={id:'two'};renderStage()")
    assert page.evaluate("document.querySelector('iframe')!==frameTwo")
