"""Themed hints survive dynamic Floor rendering without native title popups."""
import pytest

expect = pytest.importorskip('playwright.sync_api').expect

from test_floor_veil import ORIGIN, PAYLOAD, floor_page


def load_floor(page, held):
    page.goto(ORIGIN+'/floor', wait_until='domcontentloaded')
    while not held:
        page.wait_for_timeout(10)
    held.pop().fulfill(json=PAYLOAD)
    page.locator('.piece').wait_for()
    page.wait_for_selector('#floorInitialVeil', state='detached')


def test_floor_hints_hover_focus_escape_and_overlay(floor_page):
    page, held = floor_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    load_floor(page, held)
    hint = page.locator('#floorTooltip')
    me = page.locator('#meToggle')
    expect(page.locator('body [title]')).to_have_count(0)
    me.hover()
    expect(hint).to_be_visible()
    expect(hint).to_have_text('My threads as a grid — newest first, click a tile to open it')
    assert hint.bounding_box()['y'] > me.bounding_box()['y']
    hint.hover()
    page.wait_for_timeout(250)
    expect(hint).to_be_visible()
    page.keyboard.press('Escape')
    expect(hint).not_to_be_visible()
    me.focus()
    expect(hint).to_be_visible()
    expect(me).to_have_attribute('aria-describedby', 'floorTooltip')
    page.keyboard.press('Escape')
    expect(hint).not_to_be_visible()
    page.wait_for_timeout(250)
    expect(hint).not_to_be_visible()
    assert me.get_attribute('aria-describedby') is None
    # The original launcher keeps its richer wording and themed treatment.
    page.locator('#floorIntakeOpen').hover()
    expect(page.locator('#floorLaunchTooltip')).to_be_visible()
    page.locator('#floorIntakeOpen').click()
    team = page.locator('#floorIntakeTeam')
    team.hover()
    expect(hint).to_be_visible()
    expect(hint).to_contain_text('Team mode:')
    assert page.evaluate('''() => {
        const box=document.querySelector('#floorTooltip').getBoundingClientRect();
        return document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)
            .closest('#floorTooltip')!==null;
    }''')
    # Disabled controls also retain their explanation.
    page.get_by_role('button', name='Desktop Lab', exact=True).hover()
    expect(hint).to_contain_text('Desktop Lab becomes available')
    assert not errors


def test_dynamic_hints_update_preserve_aria_and_stay_inside_viewport(floor_page):
    page, held = floor_page
    load_floor(page, held)
    page.evaluate('''() => {
        const host=document.createElement('div');host.id='hint-fixture';
        host.style.cssText='position:fixed;bottom:14px;right:14px;z-index:100;overflow:hidden';
        host.innerHTML='<button id="dynamic-hint" aria-describedby="existing-description" title="A dynamic hint"><svg width="24" height="24"></svg></button>';
        document.body.append(host);
    }''')
    button = page.locator('#dynamic-hint')
    hint = page.locator('#floorTooltip')
    expect(button).to_have_attribute('aria-label', 'A dynamic hint')
    button.focus()
    expect(hint).to_be_visible()
    expect(button).to_have_attribute('aria-describedby', 'existing-description floorTooltip')
    expect(hint).to_have_attribute('data-side', 'above')
    for width in (1500, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        page.locator('#search').focus()
        button.focus()
        expect(hint).to_be_visible()
        # A live title assignment updates the existing hint as plain text.
        button.evaluate('(el)=>el.title="<img src=x onerror=alert(1)> " + "Long summary ".repeat(20)')
        expect(hint).to_contain_text('<img src=x onerror=alert(1)>')
        expect(hint.locator('img')).to_have_count(0)
        assert button.get_attribute('title') is None
        page.wait_for_function('''() => {
            const box=document.querySelector('#floorTooltip').getBoundingClientRect();
            return box.x>=11 && box.right<=innerWidth-11 && box.y>=11 && box.bottom<=innerHeight-11;
        }''')
        box = hint.bounding_box()
        assert 11 <= box['x'] and box['x']+box['width'] <= width-11
        assert box['y'] >= 11 and box['y']+box['height'] <= 889
    page.keyboard.press('Escape')
    expect(hint).not_to_be_visible()
    expect(button).to_have_attribute('aria-describedby', 'existing-description')
    page.locator('#search').focus()
    button.focus()
    expect(hint).to_be_visible()
    button.evaluate('(el)=>el.remove()')
    expect(hint).not_to_be_visible()
    expect(page.locator('body [title]')).to_have_count(0)
