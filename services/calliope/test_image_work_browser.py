"""Actual Floor polling, receipt reconciliation and image reveal in Chromium."""
import pytest
from test_zen_chat_layout import zen_page
from test_artifact_work_browser import png


@pytest.mark.parametrize('kind', ['image', 'document'])
def test_image_request_becomes_its_saved_card_after_image_load(zen_page, kind):
    page = zen_page
    item = {'kind': 'image', 'key': 'one.image-a', 'build_id': 'image-a', 'turn_id': 'one',
            'agent_run_id': 'one', 'agent_key': 'calliope', 'call_id': 'image-call',
            'phase': 'generating', 'title': 'Ground and sky', 'prompt': 'A quiet map of <forests> & distant stars',
            'aspect_ratio': '16:9', 'updated_at': 1}
    items = [item]
    page.route('**/api/calliope/sessions/notebook/artifact-work', lambda r: r.fulfill(json={'items': items}))
    image_requests = []
    page.route('**/finished.png*', lambda r: image_requests.append(r))
    page.evaluate('qa.state.surfaces=[];qa.state.turns[0].status="running";qa.renderStage()')
    card = page.locator('.image-work-card')
    card.wait_for()
    assert card.get_by_text(item['prompt'], exact=True).count() == 1
    assert card.locator('forests').count() == 0
    assert card.locator('p').last.evaluate('(el)=>Number(getComputedStyle(el).opacity)') < .5
    assert page.locator('.artifact-build').count() == 0
    page.evaluate("window.pendingImage=document.querySelector('.image-work-card')")
    page.evaluate('''kind => {
      qa.state.surfaces=[{id:'saved-image',turn_id:'one',kind,title:'Ground and sky',tool_call_id:'durable-id',
        source:{receipt_call_id:'image-call'},created_at:new Date().toISOString(),
        payload:{image_url:'/finished.png',download_url:'/finished.png',mime_type:'image/png',filename:'finished.png'}}];
      qa.renderStage();qa.renderStage();
    }''', kind)
    page.wait_for_function("document.querySelector('[data-image-work-result] img')!==null")
    assert page.evaluate("pendingImage===document.querySelector('[data-surface-id=saved-image]')")
    assert page.locator('.image-work-reveal').get_attribute('data-state') == 'loading'
    assert page.locator('.image-work-result').evaluate('el=>getComputedStyle(el).opacity') == '0'
    for request in image_requests:
        request.fulfill(body=png((30,70,100)), content_type='image/png')
    page.locator('[data-image-work-result][data-state=revealing]').wait_for()
    page.locator('[data-image-work-result][data-state=complete]').wait_for()
    assert page.locator('.image-work-result').evaluate('el=>getComputedStyle(el).opacity') == '1'
    assert page.locator('.image-work-cover').evaluate('el=>getComputedStyle(el).opacity') == '0'
    assert page.locator('.image-work-card').count() == 0
    assert page.evaluate('qa.state.surfaces.length') == 1
    # Discarding transient telemetry cannot recreate the placeholder or image.
    page.evaluate("window.savedImage=document.querySelector('[data-image-work-result] img')")
    items.clear()
    page.wait_for_timeout(2200)
    assert page.evaluate("pendingImage===document.querySelector('[data-surface-id=saved-image]') && savedImage===document.querySelector('[data-image-work-result] img')")


def test_parallel_image_calls_are_scoped_and_notebook_change_clears_them(zen_page):
    page = zen_page
    items = [{'kind':'image','key':run+'.image','agent_run_id':run,'turn_id':'one','call_id':'same',
              'agent_key':run,'phase':'generating','prompt':run+' prompt'} for run in ['iris','calliope']]
    page.route('**/api/calliope/sessions/notebook/artifact-work', lambda r:r.fulfill(json={'items':items}))
    page.route('**/done.png', lambda r:r.fulfill(body=png((50,90,30)),content_type='image/png'))
    page.emulate_media(reduced_motion='reduce')
    page.evaluate('qa.state.surfaces=[];qa.renderStage()')
    page.wait_for_function("document.querySelectorAll('.image-work-card').length===2")
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'iris-result',turn_id:'one',kind:'image',title:'Iris image',
        source:{specialist_run_id:'iris',receipt_call_id:'same'},payload:{image_url:'/done.png'}}];qa.renderStage();
    }''')
    assert page.locator('.image-work-card').count() == 1
    assert 'calliope prompt' in page.locator('.image-work-card').inner_text()
    page.locator('[data-image-work-result][data-state=complete]').wait_for()
    items[1]['phase'] = 'failed'
    page.locator('.image-work-card .image-work-status').get_by_text('Image generation failed').wait_for()
    page.evaluate("qa.state.current={id:'other'};qa.state.surfaces=[];qa.renderStage()")
    assert page.locator('.image-work-card,[data-image-work-result]').count() == 0
