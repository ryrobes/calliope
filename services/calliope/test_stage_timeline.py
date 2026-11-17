"""Saved-history cutoff, version resolution, camera continuity and rail discovery."""
import subprocess
from pathlib import Path
import pytest
from test_zen_chat_layout import zen_page
from test_stage_canvas import prepare, mode, topics

ROOT=Path(__file__).parent


def history(page):
    field=page.get_by_role('slider',name='Stage history',exact=True)
    if not field.is_visible():page.locator('[data-stage-time-toggle]').click()
    return field


def prepare_history(page,width=1500):
    prepare(page,width)
    page.evaluate('''()=>{
      qa.state.surfaces[1].created_at='2026-09-28T12:02:00Z';
      qa.state.surfaces[2].created_at='2026-09-28T12:03:00Z';
      qa.state.surfaces.push({...qa.state.surfaces.at(-1),id:'page2',parent_surface_id:'page',
        artifact_version:2,created_at:'2026-09-28T12:10:00Z'});
      qa.renderStage();
    }''')


def test_stops_group_simultaneous_records_and_use_saved_times():
    result=subprocess.run(['node','-'],cwd=ROOT,text=True,capture_output=True,input='''
      const assert=require('node:assert/strict');require('./calliope/stage-timeline.js');
      const {stops}=CalliopeStageTimeline;
      const events=stops([{id:'late',created_at:'2026-09-28T12:00:00Z'},
        {id:'first',created_at:'2026-09-27T12:00:00Z',payload:{file_modified_at:'2027-01-01'}},
        {id:'same',created_at:'2026-09-27T12:00:00Z'},
        {id:'fallback',turn_id:'t'}, {id:'undated',created_at:'invalid'}],
        [{id:'t',ordinal:3,created_at:'2026-09-28T13:00:00Z'}]);
      assert.equal(events.length,3);assert.deepEqual(events[0].items.map(s=>s.id),['first','same']);
      assert.equal(events[2].turn.ordinal,3);assert.equal(events[2].items[0].id,'fallback');
    ''')
    assert result.returncode==0,result.stderr


def test_cutoff_precedes_versions_and_combines_with_search_without_changing_chat(zen_page):
    page=zen_page;prepare_history(page)
    topics(page)
    assert page.locator('[data-canvas-select=q1]').count()==0
    history(page).press('Home')
    assert page.locator('[data-canvas-select=q1]').count()==1
    assert page.locator('[data-canvas-select=q2]').count()==0
    assert 'Viewing earlier work' in history(page).get_attribute('aria-valuetext')
    assert page.evaluate('qa.state.surfaces.length')==6
    assert page.evaluate('qa.state.turns.length')==2
    history(page).press('ArrowRight')
    assert page.locator('[data-canvas-select=q1]').count()==0
    assert page.locator('[data-canvas-select=q2]').count()==1
    field=page.get_by_role('searchbox',name='Search Stage',exact=True)
    field.fill('archives')
    assert page.locator('.canvas-card').count()==0
    history(page).press('End')
    assert sorted(page.locator('.canvas-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.canvasSelect)'))==['answer','page','page2']
    history(page).press('Home')
    mode(page,'Stage')
    assert page.locator('.stage-search-results .surface').count()==0
    field.fill('monthly')
    assert page.locator('.stage-search-results [data-surface-id=q1]').count()==1
    field.press('Escape')
    assert 'Viewing earlier work' in history(page).get_attribute('aria-valuetext')
    assert page.locator('#stage [data-surface-id=q2]').count()==0


def test_scrubbing_preserves_camera_slots_and_remaps_future_evidence_selection(zen_page):
    page=zen_page;prepare_history(page)
    mode(page,'Journey')
    history(page).press('Home')
    history(page).press('End')
    before=page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')
    slots=page.locator('.canvas-card').evaluate_all('(nodes)=>Object.fromEntries(nodes.map(n=>[n.dataset.canvasSelect,[n.style.left,n.style.top]]))')
    history(page).press('Home')
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')==before
    assert page.locator('[data-canvas-select=q1]').evaluate('(n)=>[n.style.left,n.style.top]')==slots['q1']
    history(page).press('End')
    assert page.locator('.canvas-card').evaluate_all('(nodes)=>Object.fromEntries(nodes.map(n=>[n.dataset.canvasSelect,[n.style.left,n.style.top]]))')==slots
    page.locator('[data-canvas-select=page2]').click()
    mode(page,'Evidence')
    before=page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')
    history(page).press('ArrowLeft')  # Explicitly hold the last saved moment.
    history(page).press('ArrowLeft')  # First published version.
    assert page.locator('[data-canvas-select=page2]').count()==0
    assert page.locator('[data-canvas-select=page]').get_attribute('aria-pressed')=='true'
    assert page.locator('.stage-canvas-world').evaluate('(n)=>n.style.transform')==before
    assert page.locator('.canvas-edge').count()==0
    history(page).press('Home')
    assert page.locator('.canvas-card').count()==0
    assert 'No matching saved work' in page.locator('.canvas-empty').inner_text()
    history(page).press('End')
    assert page.locator('[data-canvas-select=page2]').count()==1


def test_new_arrivals_do_not_move_historical_cutoff_and_other_chat_returns_to_now(zen_page):
    page=zen_page;prepare_history(page);mode(page,'Journey')
    history(page).press('Home')
    label=history(page).get_attribute('aria-valuetext')
    page.evaluate('''()=>{qa.state.surfaces.push({id:'arrival',kind:'query',title:'New arrival',turn_id:'two',
      created_at:'2026-09-28T14:00:00Z',payload:{}});qa.renderStage()}''')
    assert history(page).get_attribute('aria-valuetext')==label
    assert page.locator('[data-canvas-select=arrival]').count()==0
    assert 'newer items' in page.locator('[data-stage-time-detail]').text_content()
    page.locator('[data-stage-time-now]').click()
    assert page.locator('[data-canvas-select=arrival]').count()==1
    history(page).press('Home')
    page.evaluate("qa.state.current={id:'other'};qa.renderStage()")
    assert history(page).get_attribute('aria-valuetext')=='Now · all saved work'
    assert page.locator('.stage-time').get_attribute('data-earlier')=='false'


def test_historical_stage_uses_exact_saved_artifact_and_capture_not_a_future_preview(zen_page):
    page=zen_page;prepare_history(page)
    page.evaluate('''()=>{
      qa.state.surfaces.push({id:'capture',kind:'image',title:'Capture',artifact_slug:'two-archives',artifact_version:1,
        turn_id:'two',created_at:'2026-09-28T12:07:00Z',presentation:{companion:true},payload:{image_url:'/v1.png'}});
      qa.renderStage();
    }''')
    history(page).press('Home')
    for _ in range(4):history(page).press('ArrowRight')
    assert page.locator('#stage [data-surface-id=page]').count()==1
    assert page.locator('#stage [data-surface-id=page2]').count()==0
    assert page.locator('#stage [data-surface-id=page] iframe').count()==0
    assert page.locator('#stage [data-surface-id=page] img').count()==0
    assert '/versions/1' in page.locator('#stage [data-surface-id=page] .artifact-snapshot a').first.get_attribute('href')
    history(page).press('ArrowRight')
    assert page.locator('#stage [data-surface-id=page] img').get_attribute('src')=='/v1.png'


@pytest.mark.parametrize('width',[390,650,1100,2100])
def test_compact_rail_history_discovery_and_filter_popover(zen_page,width):
    page=zen_page;prepare_history(page,width)
    if width==390:assert not page.get_by_role('slider',name='Stage history',exact=True).is_visible()
    before=history(page).bounding_box()
    history(page).press('Home')
    after=history(page).bounding_box()
    assert abs(before['x']-after['x'])<1 and abs(before['width']-after['width'])<1
    assert page.locator('[data-stage-time-state]').is_visible()
    assert page.locator('.stage-time input').evaluate('(n)=>n===document.activeElement')
    history(page).press('Escape')
    page.locator('.stage-kind-filter summary').click()
    page.select_option('#stage-search-kind','query')
    assert not page.locator('.stage-kind-filter').evaluate('(n)=>n.open')
    assert page.locator('.stage-kind-filter').get_attribute('data-active')=='true'
    sizes=page.evaluate('''()=>{
      const rail=document.querySelector('#stage-rail'),r=rail.getBoundingClientRect(),s=document.querySelector('#stage-scroll').getBoundingClientRect();
      return {overflow:rail.scrollWidth-rail.clientWidth,bottom:r.bottom,top:s.top};
    }''')
    assert sizes['overflow']<=1
    assert sizes['bottom']<=sizes['top']+1
    page.screenshot(path=f'/tmp/stage-timeline-{width}.png')
