"""The real server sanitizer feeding the real browser's live reply renderer."""
import calliope
from test_zen_chat_layout import zen_page


def test_streamed_reply_preserves_plain_text_then_formats_on_completion(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const turn=qa.state.turns[0];turn.status='running';turn.assistant_message='';
      qa.renderChat();window.keptDecision=document.querySelector('#chat-decisions form');
    }''')
    draft = page.locator('[data-assistant-turn-id="one"] .working-update.is-draft')
    accumulated = ''
    chunks = ['Hello', ' ', '**team', '**', '\n', 'Next line.', '\n\n',
              '# ', 'Plan', '\n\n', '- ', 'First', '\n', '- ', 'Second', '\n\n',
              '```sql', '\n', 'SELECT', ' ', '1;', '\n', '  -- indented']
    for chunk in chunks:
        accumulated = calliope._sanitize_working_note(accumulated + chunk, streaming=True)
        page.evaluate('(text)=>qa.setWorkingDraft(qa.state.turns[0],text)', accumulated)
    page.wait_for_selector('.working-update.is-draft')
    assert draft.inner_text() == accumulated
    assert draft.locator('strong,li,pre,h2').count() == 0
    assert draft.evaluate('(node)=>getComputedStyle(node).whiteSpace') == 'pre-wrap'
    assert page.evaluate('qa.state.turns[0].status') == 'running'
    assert page.evaluate('document.querySelector("#chat-decisions form")===keptDecision')
    accumulated += '\n```\n\n<img src=x onerror="alert(1)">'
    page.evaluate('(text)=>qa.setWorkingDraft(qa.state.turns[0],text)', accumulated)
    page.wait_for_function('document.querySelector(".working-update.is-draft")?.textContent.includes("onerror")')
    assert draft.locator('img,script').count() == 0
    page.evaluate('(text)=>{const turn=qa.state.turns[0];turn.status="complete";turn.assistant_message=text;qa.renderChat()}', accumulated)
    complete = page.locator('[data-assistant-turn-id="one"] .zen-speech-piece')
    assert complete.inner_html() == page.evaluate('(text)=>{const t=document.createElement("template");t.innerHTML=qa.safeMarkdown(text);return t.innerHTML}', accumulated)
    page.screenshot(path='/tmp/calliope-streaming-markdown.png')


def test_streaming_preserves_readers_scroll_position(zen_page):
    page = zen_page
    page.evaluate('''() => {
      qa.state.turns[0].status='running';qa.state.turns[0].assistant_message='';
      qa.state.turns.unshift({id:'older',ordinal:0,turn_kind:'chat',status:'complete',
        user_message:'Earlier discussion',assistant_message:'A previous paragraph.\\n\\n'.repeat(70)});
      qa.renderChat();
    }''')
    page.wait_for_timeout(200)
    before=page.evaluate('''() => {
      qa.state.chatAtLiveEdge=false;qa.els.messages.scrollTop=40;
      return qa.els.messages.scrollTop;
    }''')
    page.evaluate('qa.setWorkingDraft(qa.state.turns[1],"# New reply\\n\\n- First item\\n- Second item")')
    page.wait_for_selector('.working-update.is-draft')
    assert page.evaluate('qa.els.messages.scrollTop') == before
