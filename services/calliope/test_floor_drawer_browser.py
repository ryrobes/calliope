"""Real Floor drawer rendering: readable type, scrolling, and run controls."""
import pytest

from test_floor_metrics_browser import browser_page, metrics_site


@pytest.mark.parametrize('width', [1600, 390])
def test_drawer_type_hierarchy_and_run_controls(metrics_site, browser_page, width):
    url, state = metrics_site
    page = browser_page
    page.set_viewport_size({'width': width, 'height': 1000})
    base = state['floor']['cards'][0]
    state['floor']['cards'] = [
        dict(base, item_id='chat:detail', session_id='detail',
             display_title='Art-Directed Natterbox Operational Dashboard',
             semantic_status='The v18 black field design is the live floor and the project is complete.',
             summary='The objective was to create a polished operational dashboard for the Natterbox system. After iterating through several versions, the project established a data layer with eight live query bindings. The work is now parked with v18 as the live floor.',
             surface_count=163, related_signal_count=3, open_url='/calliope?session=detail'),
        dict(base, item_id='duty_cycle:knowledge-gardener:nightly', item_kind='duty_cycle',
             display_title='Calliope nightly knowledge gardening', column_state='standing',
             summary='Enabled and waiting', state_reason='Enabled and waiting',
             schedule='53 0 * * *', agents=['demeter'], streak=0),
    ]
    state['floor']['titles'] = [{'item_id': 'chat:detail', 'semantic_status': 'The v18 black field design is the live floor and the project is complete.'}]
    state['floor']['lanes'] = [{'column_state': 'done', 'pieces': 1}, {'column_state': 'standing', 'pieces': 1}]
    state['responses'] = {
        '/api/calliope/floor/piece-context': {
            'turns': [
                {'user_message': 'Let’s try the final design. Keep the useful structure and make the hierarchy clearer.',
                 'assistant_message': 'The new version is live. It keeps the same data and gives the team a calmer place to work.'},
                {'user_message': 'This is much easier to follow. Keep this as the starting point for the next iteration.',
                 'assistant_message': 'Saved as the current version. The dashboard and its query contracts are available below.'},
            ],
            'surfaces': [
                {'kind': 'document', 'title': 'Natterbox SQL contract'},
                {'kind': 'image', 'title': 'Capture · Natterbox voice intelligence cockpit'},
                {'kind': 'artifact', 'title': 'Natterbox voice intelligence cockpit', 'open_url': '/gallery'},
                {'kind': 'query', 'title': 'Query · salesforce.nbavs_call_reporting_c'},
            ],
        },
        '/api/calliope/floor/duty-runs': {'runs': [
            {'status': 'completed', 'started_at': f'2026-09-{day:02}T04:53:00Z', 'api_calls': calls, 'hermes_session_id': f'run-{day}'}
            for day, calls in [(11, 28), (10, 22), (9, 30), (8, 14)]
        ], 'notes': []},
        '/api/calliope/hermes-transcript/run-11': {'messages': [
            {'kind': 'assistant_text', 'content': 'Reviewed recent knowledge and refreshed the summaries.'},
        ]},
    }
    page.goto(url+'/floor')
    page.locator('#floorInitialVeil').wait_for(state='detached')
    page.add_style_tag(content=':root{--amber:#efb77e;--background:#180d12;--foreground:#eadfe4;--block-bg:#21121a;--secondary-background:#21121a;--chrome-text:#af9da6;--block-border:#49313e}')
    page.locator('.piece[data-id="chat:detail"]').click()
    page.locator('.drawer-bubble').first.wait_for()
    page.evaluate('document.fonts.ready')
    title = page.locator('#drawerTitle')
    styles = title.evaluate('(el)=>{const s=getComputedStyle(el);return {size:parseFloat(s.fontSize),weight:s.fontWeight,font:s.fontFamily}}')
    assert styles['size'] >= 22 and styles['weight'] == '700' and 'Superior' in styles['font']
    assert page.locator('.drawer-lede').evaluate('(el)=>parseFloat(getComputedStyle(el).lineHeight)/parseFloat(getComputedStyle(el).fontSize)') >= 1.55
    drawer = page.locator('.drawer')
    assert drawer.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    assert page.locator('#drawerBody').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    page.screenshot(path=f'/tmp/floor-detail-chat-{width}.png')
    page.locator('#openPiece').scroll_into_view_if_needed()
    assert page.locator('#openPiece').is_enabled()
    page.locator('#drawerClose').click()
    page.locator('.piece[data-id="duty_cycle:knowledge-gardener:nightly"]').click()
    page.locator('.duty-run').first.wait_for()
    assert page.locator('.duty-run').count() == 4
    assert page.locator('#drawerBody').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    page.locator('[data-duty-transcript="run-11"]').click()
    page.get_by_text('Reviewed recent knowledge and refreshed the summaries.').wait_for()
    page.locator('[data-duty-transcript="run-11"]').click()
    field = page.locator('[data-duty-note-input]')
    field.fill('Focus on the refreshed dashboard contracts.')
    page.locator('[data-duty-note-send]').click()
    page.wait_for_function('document.querySelector("[data-duty-note-input]").value===""')
    assert any(path == '/api/calliope/floor/duty-notes' and body['note'] == 'Focus on the refreshed dashboard contracts.' for path, body in state['posts'])
    page.locator('#drawerBody').evaluate('el=>el.scrollTop=0')
    page.screenshot(path=f'/tmp/floor-detail-cycle-{width}.png')
    page.locator('#drawerClose').click()
    assert page.locator('#drawerBackdrop').get_attribute('class') == 'drawer-backdrop'
