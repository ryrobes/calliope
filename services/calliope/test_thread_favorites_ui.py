from test_zen_chat_layout import zen_page  # noqa:F401
from test_floor_veil import floor_page, ORIGIN, PAYLOAD  # noqa:F401


def test_chat_favorite_click_keyboard_failure_and_polling(zen_page):
    page=zen_page
    page.evaluate('qa.setupEvents()')
    requests=[];failed=[False]
    def save(r):
        requests.append(r.request.post_data_json)
        if failed[0]:r.fulfill(status=503,json={'error':{'message':'Try again'}})
        else:r.fulfill(json=r.request.post_data_json)
    page.route('**/sessions/notebook/favorite',save)
    star=page.locator('#chat-topic-favorite')
    assert star.get_attribute('aria-pressed')=='false'
    star.click();page.wait_for_function('document.querySelector("#chat-topic-favorite").getAttribute("aria-pressed")==="true"')
    assert page.locator('#chat-topic').get_attribute('open') is None
    assert requests==[{'favorite':True}]
    page.wait_for_function('getComputedStyle(document.querySelector("#chat-topic-favorite svg")).fill!=="rgba(0, 0, 0, 0)"')
    star.focus();page.keyboard.press('Space')
    page.wait_for_function('document.querySelector("#chat-topic-favorite").getAttribute("aria-pressed")==="false"')
    assert requests[-1]=={'favorite':False}
    failed[0]=True;star.click();page.wait_for_function('!document.querySelector("#chat-topic-favorite").disabled')
    assert star.get_attribute('aria-pressed')=='false'
    page.route('**/sessions/notebook/presentation',lambda r:r.fulfill(json={'presentation':{'title':'New title','summary':'New summary','favorite':True}}))
    page.evaluate('qa.pollChatPresentation()')
    assert star.get_attribute('aria-pressed')=='true'
    assert page.locator('#chat-topic-title').inner_text()=='New title'
    page.locator('#calliope-chat').screenshot(path='/tmp/chat-favorite.png')


def test_floor_favorites_highlight_and_sort_on_board_and_grid(floor_page):
    page,held=floor_page
    cards=[{**PAYLOAD['cards'][0],'item_id':'chat:new','display_title':'Recent thread','favorite':False,'session_id':'new','last_activity_at':'2026-09-15'},
           {**PAYLOAD['cards'][0],'item_id':'chat:old','display_title':'Favorite thread','favorite':True,'session_id':'old','last_activity_at':'2020-01-01'}]
    page.goto(ORIGIN+'/floor',wait_until='domcontentloaded')
    while not held:page.wait_for_timeout(10)
    held.pop().fulfill(json={**PAYLOAD,'cards':cards,'total':2,'matched_total':2,'lanes':[{'column_state':'done','pieces':2,'matched':2}]})
    page.locator('.piece.is-favorite').wait_for()
    assert page.locator('.piece').first.get_attribute('data-id')=='chat:old'
    assert page.locator('.piece-favorite').count()==1
    page.add_style_tag(content=':root{--secondary-background:#201612;--block-bg:#201612;--block-border:#60402d;--chrome-text:#bfa18e;}')
    page.screenshot(path='/tmp/floor-favorite.png')
    page.locator('#listBtn').click()
    assert page.locator('.floor-list-row[data-open]').first.get_attribute('data-open')=='chat:old'
    payload={**PAYLOAD,'cards':cards,'total':2,'matched_total':2,'lanes':[{'column_state':'done','pieces':2,'matched':2}]}
    page.route('**/api/calliope/floor/pieces?*',lambda r:r.fulfill(json=payload))
    page.locator('#meToggle').click()
    page.locator('.grid-tile.is-favorite').wait_for()
    assert page.locator('.grid-tile').first.get_attribute('data-id')=='chat:old'
    assert page.locator('.grid-tile.is-favorite .piece-favorite').count()==1


def test_late_presentation_cannot_undo_a_saved_favorite(zen_page):
    page=zen_page;page.evaluate('qa.setupEvents()')
    held=[]
    page.route('**/sessions/notebook/presentation',lambda r:held.append(r))
    page.route('**/sessions/notebook/favorite',lambda r:r.fulfill(json={'favorite':True}))
    page.evaluate('void qa.pollChatPresentation()')
    while not held:page.wait_for_timeout(10)
    page.locator('#chat-topic-favorite').click()
    page.wait_for_function('document.querySelector("#chat-topic-favorite").getAttribute("aria-pressed")==="true"')
    held.pop().fulfill(json={'presentation':{'title':'Older poll','summary':'','favorite':False}})
    page.wait_for_function('document.querySelector("#chat-topic-title").textContent==="Older poll"')
    assert page.locator('#chat-topic-favorite').get_attribute('aria-pressed')=='true'
