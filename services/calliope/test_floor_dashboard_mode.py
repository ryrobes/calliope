"""Floor defaults and exact Playbook references survive the real browser handoff."""
import pytest
from test_floor_veil import floor_page, ORIGIN, PAYLOAD
from test_dashboard_mode_ui import BOOKS, BOOK_ID

@pytest.mark.parametrize('width', [1500,390])
def test_floor_playbook_survives_real_handoff(floor_page,width):
    page,held=floor_page
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/calliope?*',lambda r:r.fulfill(body='Chat destination',content_type='text/html'))
    page.route('**/api/calliope/playbooks?*',lambda r:r.fulfill(json=BOOKS))
    page.goto(ORIGIN+'/floor',wait_until='domcontentloaded')
    while not held:page.wait_for_timeout(10)
    held.pop().fulfill(json=PAYLOAD)
    page.wait_for_selector('#floorInitialVeil',state='detached')
    page.locator('#floorIntakeOpen').click()
    page.set_viewport_size({'width':width,'height':900})
    assert page.locator('#floorIntakeDashboard').count()==0
    assert 'no design' in page.locator('#floorIntakeDesignToggle').inner_text()
    page.locator('#floorIntakeEditor .cm-content').fill('Build September report')
    page.locator('#floorIntakePlaybook').click()
    page.locator('#playbook-picker .playbook-picker-item').first.click()
    assert page.locator('#floorIntakeEditor .cm-content').inner_text()=='Build September report'
    assert 'Composition-led dashboard · v3' in page.locator('#floorIntakePlaybooks').inner_text()
    assert page.locator('#floorIntakeEditor .cm-object-chip').count()==0
    page.locator('#floorIntakeSend').click()
    page.wait_for_url('**/calliope?*')
    handoff=page.evaluate('''() => new Promise((resolve,reject)=>{
      const request=indexedDB.open('calliope-floor-intake-v1');
      request.onsuccess=()=>{const db=request.result;const read=db.transaction('handoffs').objectStore('handoffs').get(sessionStorage.getItem('calliope.floorIntake.v1'));read.onsuccess=()=>{resolve(read.result);db.close();};read.onerror=reject;};request.onerror=reject;
    })''')
    assert handoff['authoring_mode']=='html'
    assert handoff['design_version_id'] is None
    assert handoff['prompt'].startswith('Build September report')
    assert '[[playbook:'+BOOK_ID+'@3|' in handoff['prompt']
    assert handoff['team_mode'] is False  # Team is opt-in
    assert errors==[]
