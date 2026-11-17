from test_zen_chat_layout import zen_page  # noqa: F401


def test_thread_markdown_download_is_inside_summary_and_keeps_chat(zen_page, tmp_path):
    page = zen_page
    page.evaluate('qa.setupEvents()')
    button = page.locator('#chat-topic-download')
    assert not button.is_visible()
    page.locator('#chat-topic>summary').click()
    assert button.is_visible()
    markdown = '# September enrollment\n\n## 2026-09-01T12:00:00Z — Ryan\n\nCompare regions.\n'
    page.route('**/sessions/notebook/export.md', lambda r: r.fulfill(body=markdown,
        headers={'content-type': 'text/markdown; charset=utf-8', 'content-disposition': 'attachment; filename="september-notebook.md"'}))
    before = page.evaluate('JSON.stringify({current:qa.state.current,turns:qa.state.turns})')
    with page.expect_download() as pending:
        button.click()
    download = pending.value
    assert download.suggested_filename == 'september-notebook.md'
    path = tmp_path / download.suggested_filename
    download.save_as(path)
    assert path.read_text() == markdown
    page.wait_for_function('!qa.state.chatExportBusy')
    assert page.locator('#chat-topic').get_attribute('open') is not None
    assert not button.is_disabled()
    assert page.evaluate('JSON.stringify({current:qa.state.current,turns:qa.state.turns})') == before
    page.locator('#calliope-chat').screenshot(path='/tmp/calliope-thread-download.png')


def test_export_error_recovers_without_collapsing_summary(zen_page):
    page = zen_page
    page.evaluate('qa.setupEvents()')
    page.locator('#chat-topic>summary').click()
    page.route('**/sessions/notebook/export.md', lambda r: r.fulfill(status=503, json={'error': {'message': 'Export temporarily unavailable'}}))
    page.locator('#chat-topic-download').click()
    page.wait_for_function('!qa.state.chatExportBusy && document.querySelector(".toast").textContent.includes("Export temporarily unavailable")')
    assert not page.locator('#chat-topic-download').is_disabled()
    assert page.locator('#chat-topic').get_attribute('open') is not None
