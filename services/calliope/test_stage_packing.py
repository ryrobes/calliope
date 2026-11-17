"""Measured Stage packing against the real renderer and CSS in Chromium."""
from pathlib import Path
import pytest
from test_zen_chat_layout import zen_page
from test_stage_reconciliation import stage_page

ROOT = Path(__file__).parent


def prepare(page, width=1100):
    page.evaluate('''width => {
      document.body.append(qa.els.stageScroll);
      document.body.style.setProperty('--test-stage-width', width+'px');
      qa.state.surfaces=[];
    }''', width)
    page.add_style_tag(content='''
      body>.notebook,body>.topbar{display:none!important}
      html body.zen #stage-scroll{width:var(--test-stage-width);height:700px;position:relative;overflow:auto;flex:none}
      html body.zen #stage{padding:0!important}
    ''')


def populate(page, heights):
    page.evaluate('''heights => {
      qa.state.surfaces=heights.map((height,index)=>({id:'card-'+index,turn_id:'one',kind:'image',
        title:'Measured card '+index,ordinal:heights.length-index,payload:{},created_at:'2026-09-11T12:00:00Z'}));
      qa.renderStage();
      heights.forEach((height,index)=>{
        const body=document.querySelector('[data-surface-id="card-'+index+'"] .surface-body');
        body.innerHTML='<div class="test-content" style="height:'+height+'px">Natural content</div>';
      });
    }''', heights)
    page.wait_for_timeout(400)


def geometry(page):
    return page.locator('.zen-packed > :not([data-stage-build-slot], [data-stage-desktop-slot]), .zen-packed .artifact-build, .zen-packed .desktop-stage:not([hidden]) > .desktop-stage-card').evaluate_all('''cards=>cards.map(card=>({
      id:card.dataset.surfaceId, x:card.offsetLeft,y:card.offsetTop,w:card.offsetWidth,h:card.offsetHeight
    }))''')


def no_overlaps(boxes):
    for i, a in enumerate(boxes):
        for b in boxes[i+1:]:
            assert (a['x']+a['w'] <= b['x']+1 or b['x']+b['w'] <= a['x']+1
                    or a['y']+a['h'] <= b['y']+1 or b['y']+b['h'] <= a['y']+1), (a, b)


@pytest.mark.parametrize('reduced', [False, True])
def test_natural_heights_pack_holes_but_bound_chronological_displacement(zen_page, reduced):
    page=zen_page
    page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
    prepare(page)
    populate(page,[520,100,110,90,95,85,120,130,140,90,90,90])
    cards=geometry(page)
    assert [c['id'] for c in cards]==['card-'+str(i) for i in range(12)]
    assert cards[2]['y'] < cards[0]['y']+cards[0]['h'] # actual vertical hole filled
    assert min(c['y'] for c in cards[6:]) >= max(c['y']+c['h'] for c in cards[:6])+9
    no_overlaps(cards)
    assert page.locator('[data-surface-id=card-0] .test-content').evaluate('(n)=>n.offsetHeight')==520
    assert page.locator('.zen-packed>.surface').evaluate_all('(nodes)=>nodes.every(n=>!n.style.height && !n.style.maxHeight)')
    page.screenshot(path=f'/tmp/stage-packing-{reduced}.png')
    animated=page.evaluate('''() => {
      qa.state.surfaces.push({id:'motion-check',turn_id:'one',kind:'image',title:'Arrival',ordinal:999,payload:{}});
      qa.renderStage();
      return document.querySelector('.zen-packed').getAnimations({subtree:true}).length;
    }''')
    assert (animated==0) if reduced else (animated>0)


def test_container_widths_and_large_spans_without_clipping(zen_page):
    page=zen_page
    prepare(page,1100)
    populate(page,[450,100,120,140,160,180])
    # A current artifact has this role; preserve its width even as the viewport
    # stays fixed and the Stage alone changes size (chat-rail resizing).
    page.evaluate('''() => {
      document.querySelector('[data-surface-id=card-0]').classList.add('artifact-live');qa.renderStage();
    }''')
    for width, columns in [(1500,3),(1100,2),(650,1),(1450,3)]:
        page.evaluate("w=>document.body.style.setProperty('--test-stage-width',w+'px')",width)
        page.wait_for_timeout(350)
        cards=geometry(page)
        inner=page.locator('.zen-packed').evaluate('(n)=>n.clientWidth')
        unit=(inner-10*(columns-1))/columns
        assert abs(cards[1]['w']-unit)<=1
        assert abs(cards[0]['w']-(unit*min(2,columns)+10*(min(2,columns)-1)))<=1
        no_overlaps(cards)
        assert page.locator('#stage-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
    page.evaluate("document.querySelector('[data-surface-id=card-0]').classList.add('stage-full-width');qa.renderStage()")
    assert abs(geometry(page)[0]['w']-page.locator('.zen-packed').evaluate('(n)=>n.clientWidth'))<=1


def test_late_growth_stays_in_columns_and_new_arrivals_preserve_reading_anchor(zen_page):
    page=zen_page
    prepare(page)
    populate(page,[220,110,130,100,180,120,250,180,120,170,160,140,200,190,140,130,120,130])
    page.wait_for_timeout(1000) # arrival window is over
    original=geometry(page)
    page.evaluate("document.querySelector('[data-surface-id=card-1] .test-content').style.height='820px'")
    page.wait_for_timeout(350)
    grown=geometry(page)
    assert [c['x'] for c in grown]==[c['x'] for c in original]
    assert grown[1]['h']>800
    no_overlaps(grown)
    page.evaluate('''() => {
      qa.els.stageScroll.scrollTo({top:document.querySelector('[data-surface-id=card-6]').offsetTop,behavior:'instant'});
      window.anchor=document.querySelector('[data-surface-id=card-6]');
      window.anchorTop=anchor.getBoundingClientRect().top;
      window.retained=document.querySelector('[data-surface-id=card-10]');
      qa.state.surfaces.push({id:'arrival',turn_id:'one',kind:'image',title:'New arrival',ordinal:999,payload:{}});
      qa.renderStage();
    }''')
    page.wait_for_timeout(350)
    assert page.evaluate("retained===document.querySelector('[data-surface-id=card-10]')")
    assert page.evaluate('Math.abs(anchor.getBoundingClientRect().top-anchorTop)')<2
    no_overlaps(geometry(page))
    # No needless mutations or recurring observer work on an unchanged render.
    page.evaluate('''() => {
      window.changes=[];window.observer=new MutationObserver(items=>changes.push(...items));
      observer.observe(document.querySelector('.zen-packed'),{attributes:true,subtree:true,childList:true});
      qa.renderStage();qa.renderStage();
    }''')
    page.wait_for_timeout(150)
    assert page.evaluate('changes.length')==0


def test_live_drafts_share_columns_with_saved_work_and_repack_on_removal(zen_page):
    page = zen_page
    prepare(page, 1500)
    drafts = [{'key': 'draft', 'turn_id': 'one', 'agent_key': 'iris', 'filename': 'draft.html',
               'phase': 'preview', 'revision': 1, 'updated_at': 1, 'lines': [40]*30,
               'preview_html': '<h1>Living draft</h1>'}]
    page.route('**/api/calliope/sessions/notebook/artifact-work', lambda r: r.fulfill(json={'items': drafts}))
    populate(page, [100, 180, 150])
    page.locator('.zen-packed .artifact-build iframe').wait_for()
    page.evaluate("window.draftFrame=document.querySelector('.artifact-build iframe')")
    for width, columns in [(1500, 3), (1100, 2), (650, 1), (1450, 3)]:
        page.evaluate("w=>document.body.style.setProperty('--test-stage-width',w+'px')", width)
        page.wait_for_timeout(400)
        boxes = geometry(page)
        no_overlaps(boxes)
        draft = next(box for box in boxes if not box.get('id'))
        unit = next(box['w'] for box in boxes if box.get('id') == 'card-0')
        assert abs(draft['w'] - (unit*min(2, columns) + 10*(min(2, columns)-1))) <= 2
        if columns == 3:
            saved = next(box for box in boxes if box.get('id') == 'card-0')
            assert saved['y'] == draft['y'], 'Saved work must fill the space beside the draft'
        assert page.locator('#stage-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
        assert page.evaluate("draftFrame===document.querySelector('.artifact-build iframe')")
    page.screenshot(path='/tmp/stage-mixed-draft-packing.png')
    page.evaluate('''() => {
      qa.state.surfaces.push({id:'new',turn_id:'one',kind:'image',title:'Fresh work',ordinal:999,payload:{}});
      qa.renderStage();qa.renderStage();
    }''')
    assert page.evaluate("draftFrame===document.querySelector('.artifact-build iframe')")
    page.wait_for_timeout(350)
    no_overlaps(geometry(page))
    drafts.clear()
    page.locator('.artifact-build').wait_for(state='detached')
    page.wait_for_timeout(350)
    assert min(box['y'] for box in geometry(page)) == 0
    no_overlaps(geometry(page))


def test_draft_only_stage_and_projection_changes_preserve_live_host(zen_page):
    page = zen_page
    prepare(page)
    drafts = [{'key': 'draft', 'turn_id': 'one', 'filename': 'draft.html', 'phase': 'composing',
               'revision': 1, 'updated_at': 1, 'lines': [20, 50]}]
    page.route('**/api/calliope/sessions/notebook/artifact-work', lambda r: r.fulfill(json={'items': drafts}))
    page.evaluate('qa.renderStage()')
    page.locator('.zen-packed .artifact-build').wait_for()
    page.evaluate("window.draftCard=document.querySelector('.artifact-build')")
    page.evaluate('qa.state.zen=false;qa.renderStage()')
    assert page.locator('.stage-live-work > #stage-builds .artifact-build').count() == 1
    assert page.locator('.artifact-build').evaluate("el=>!el.style.width && !el.style.left && !el.style.top")
    page.evaluate('qa.state.zen=true;qa.renderStage()')
    assert page.evaluate("draftCard===document.querySelector('.zen-packed .artifact-build')")
    # A fresh notebook must clear the independent live renderer as well.
    page.evaluate("qa.state.current={id:'other'};qa.renderStage()")
    assert page.locator('.artifact-build').count() == 0
    assert page.locator('.zen-packed').count() == 0


def test_arrival_settling_reconsiders_columns_and_removal_cleans_layout(zen_page):
    page=zen_page
    prepare(page)
    populate(page,[400,100,100,90,90,90])
    original=geometry(page)
    page.evaluate("document.querySelector('[data-surface-id=card-1] .test-content').style.height='850px'")
    page.wait_for_timeout(350)
    changed=geometry(page)
    assert changed[2]['x']!=original[2]['x']
    no_overlaps(changed)
    page.evaluate("qa.state.surfaces=[];qa.renderStage()")
    assert page.locator('.zen-packed').count()==0
    populate(page,[100,130,90])
    no_overlaps(geometry(page))


def test_ancestor_animation_does_not_change_measured_layout_height(zen_page):
    page=zen_page
    prepare(page)
    page.evaluate("qa.els.stageScroll.style.transform='scale(.8)'")
    populate(page,[500,100,110,120,130,140,140,120])
    no_overlaps(geometry(page))
    page.evaluate("qa.els.stageScroll.style.transform='none'")
    # Transforms don't notify ResizeObserver: layout must already be correct.
    no_overlaps(geometry(page))


def test_arrival_preserves_reader_in_evidence_below_packed_cards(zen_page):
    page=zen_page
    prepare(page)
    populate(page,[400,200,300,250,200,300])
    page.evaluate('''() => {
      qa.state.surfaces.push({id:'evidence-query',turn_id:'one',kind:'query',title:'Investigation',
        ordinal:0,presentation:{phase:'exploration'},payload:{columns:['value'],rows:[[1]]}});
      qa.renderStage();
    }''')
    page.wait_for_timeout(350)
    page.add_style_tag(content='.zen-evidence{min-height:900px}')
    page.evaluate('''() => {
      window.evidence=document.querySelector('.zen-evidence');
      qa.els.stageScroll.scrollTo({top:evidence.offsetTop,behavior:'instant'});
      window.evidenceTop=evidence.getBoundingClientRect().top;
      qa.state.surfaces.push({id:'next',turn_id:'one',kind:'image',title:'New arrival',ordinal:999,payload:{}});
      qa.renderStage();
    }''')
    page.wait_for_timeout(350)
    assert page.evaluate('Math.abs(evidence.getBoundingClientRect().top-evidenceTop)')<2


def test_live_iframe_survives_packing_resize_and_insert(stage_page):
    page,loads=stage_page
    page.add_style_tag(content=(ROOT/'calliope/calliope.css').read_text())
    page.add_style_tag(content='''#stageScroll{width:1500px;height:700px}#stage{padding:0!important}
      iframe{width:100%;height:100px}''')
    page.evaluate('''() => {
      document.body.classList.add('zen');document.body.dataset.warehousePage='calliope';
      els.stage.classList.add('stage');els.stageScroll.classList.add('stage-scroll');state.zen=true;
      state.surfaces=[{id:'dashboard',turn_id:'turn',kind:'artifact',title:'Live',url:'/artifact/v1'},
        {id:'note',turn_id:'turn',kind:'document',title:'Note',text:'Natural text'}];renderStage();
      document.querySelector('[data-surface-id=dashboard]').classList.add('artifact-live');renderStage();
    }''')
    page.wait_for_function("document.querySelector('iframe').contentWindow.documentIdentity")
    page.evaluate('''() => {
      window.kept=document.querySelector('iframe');window.identity=kept.contentWindow.documentIdentity;
      kept.contentWindow.document.querySelector('input').value='Retain filter';
      // The same shell height write used by calliope.artifact.resize.
      kept.closest('.artifact-frame').style.height='940px';
      els.stageScroll.style.width='1100px';
      state.surfaces.unshift({id:'new',turn_id:'turn',kind:'document',title:'New',text:'Arrival'});renderStage();
    }''')
    page.wait_for_timeout(400)
    assert len(loads)==1
    assert page.evaluate("kept===document.querySelector('iframe') && kept.contentWindow.documentIdentity===identity")
    assert page.evaluate("kept.contentWindow.document.querySelector('input').value")=='Retain filter'
    assert page.locator('.artifact-frame').evaluate('(n)=>n.offsetHeight')>=940
    no_overlaps(geometry(page))
