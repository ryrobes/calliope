"""Reader intent, hover and moving content in the real chat scroller."""
import pytest
from test_zen_chat_layout import zen_page


def prepare(page):
    page.mouse.move(0,0)
    page.evaluate('''() => {
      qa.state.surfaces=[];qa.state.config.healthy=true;qa.setupEvents();
      qa.state.turns=Array.from({length:16},(_,i)=>({id:'scroll-'+i,ordinal:i,turn_kind:'chat',status:'complete',
        user_message:'Question '+i,assistant_message:'An answer with context.\\n\\nMore details to read.',created_at:new Date().toISOString()}));
      qa.renderChat(true);
      window.addChatContent=()=>{
        const row=document.createElement('div');row.className='test-content';row.style.height='240px';
        row.textContent='New content';qa.els.messages.append(row);return row;
      };
    }''')
    page.wait_for_timeout(400)


def gap(page):
    return page.evaluate('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop')


def test_follow_eases_towards_growing_content_and_hover_stops_in_flight(zen_page):
    page=zen_page;prepare(page)
    assert gap(page)<2
    before=page.evaluate('qa.els.messages.scrollTop')
    page.evaluate('addChatContent()')
    page.wait_for_timeout(90)
    position=page.evaluate('qa.els.messages.scrollTop')
    assert before<position<before+239
    # Hover in the middle of an animation; incoming rows cannot restart it.
    page.locator('#messages').hover(position={'x':100,'y':100})
    paused=page.evaluate('qa.els.messages.scrollTop')
    page.evaluate('addChatContent();qa.scrollChatToLiveEdge()')
    page.wait_for_timeout(300)
    assert abs(page.evaluate('qa.els.messages.scrollTop')-paused)<2
    assert page.locator('#chat-jump-latest').is_visible()
    page.mouse.move(0,0)
    page.wait_for_function('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop<2')
    assert page.locator('#chat-jump-latest').is_hidden()


def test_reading_history_stays_put_on_pointer_leave_until_explicit_jump(zen_page):
    page=zen_page;prepare(page)
    page.locator('#messages').hover(position={'x':100,'y':100})
    page.mouse.wheel(0,-420)
    page.wait_for_timeout(200)
    before=page.evaluate('qa.els.messages.scrollTop')
    assert page.evaluate('qa.state.chatAtLiveEdge') is False
    page.evaluate('addChatContent()')
    page.mouse.move(0,0)
    page.wait_for_timeout(350)
    assert abs(page.evaluate('qa.els.messages.scrollTop')-before)<2
    # Explicit navigation is allowed even with the pointer over the transcript.
    page.locator('#messages').hover(position={'x':100,'y':100})
    page.locator('#chat-jump-latest').click()
    page.wait_for_function('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop<2')
    assert page.evaluate('qa.state.chatAtLiveEdge') is True


@pytest.mark.parametrize('width',[1500,390])
def test_late_layout_growth_follows_but_respects_hover(zen_page,width):
    page=zen_page
    page.set_viewport_size({'width':width,'height':900})
    page.evaluate("document.body.classList.add('mobile-chat-open')")
    prepare(page)
    page.evaluate('window.lateRow=addChatContent()')
    page.wait_for_function('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop<2')
    page.evaluate("lateRow.style.height='480px'")
    page.wait_for_function('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop<2')
    page.locator('#messages').hover(position={'x':100,'y':100})
    before=page.evaluate('qa.els.messages.scrollTop')
    page.evaluate("lateRow.style.height='700px'")
    page.wait_for_timeout(250)
    assert abs(page.evaluate('qa.els.messages.scrollTop')-before)<2
    assert gap(page)>200
    assert page.locator('.chat-column').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
    page.locator('.chat-column').screenshot(path=f'/tmp/chat-follow-paused-{width}.png')


def test_reduced_motion_and_initial_navigation_skip_the_scroll_animation(zen_page):
    page=zen_page;prepare(page)
    assert gap(page)<2
    page.emulate_media(reduced_motion='reduce')
    page.evaluate('addChatContent();qa.scrollChatToLiveEdge()')
    assert gap(page)<2
    page.locator('#messages').hover(position={'x':100,'y':100})
    page.evaluate('addChatContent();qa.scrollChatToLiveEdge()')
    assert gap(page)>200
    page.evaluate('qa.scrollChatToLiveEdge({explicit:true})')
    assert gap(page)<2


def test_scrollbar_or_selection_gesture_cancels_follow(zen_page):
    page=zen_page;prepare(page)
    page.locator('#messages').hover(position={'x':100,'y':100})
    page.evaluate('''() => {
      qa.els.messages.dispatchEvent(new PointerEvent('pointerdown',{button:0,pointerType:'mouse'}));
      qa.els.messages.scrollTop-=250;addChatContent();
    }''')
    page.wait_for_timeout(100)
    before=page.evaluate('qa.els.messages.scrollTop')
    page.mouse.move(0,0)
    page.wait_for_timeout(300)
    assert abs(page.evaluate('qa.els.messages.scrollTop')-before)<2


def test_streaming_retargets_follow_and_navigation_cancels_old_destination(zen_page):
    page=zen_page;prepare(page)
    page.evaluate('window.growing=addChatContent()')
    for height in [300,370,450,520]:
        page.wait_for_timeout(45)
        page.evaluate('(height)=>{growing.style.height=height+"px";qa.scrollChatToLiveEdge()}',height)
        assert page.evaluate('qa.state.chatAtLiveEdge') is True
    page.wait_for_function('qa.els.messages.scrollHeight-qa.els.messages.clientHeight-qa.els.messages.scrollTop<2')
    page.evaluate('''() => {
      addChatContent();qa.scrollChatToLiveEdge();
      qa.state.current={...qa.state.current,id:'other'};
      qa.state.turns=[];qa.renderChat(true);
    }''')
    page.wait_for_timeout(200)
    assert page.evaluate('qa.els.messages.scrollTop')==0
