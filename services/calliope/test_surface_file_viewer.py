"""Stage links open local image, artifact, Markdown and readonly JSON previews."""
from pathlib import Path

from test_zen_chat_layout import zen_page  # noqa: F401

ROOT = Path(__file__).parent
MARKDOWN = '# Project notes\n\nA **bold** plan.\n\n| Step | Status |\n| --- | --- |\n| Build | Done |\n\n<script>window.injection=true</script>'
JSON_TEXT = '{"id":9007199254740993,"exponent":1e400,"enabled":true,"empty":[],"label":"a, b: [c] \\\"quoted\\\""}'


def seed_files(page):
    page.route('**/preview/picture.jpg', lambda r: r.fulfill(
        body=(ROOT/'calliope/callie-avatar-day.jpg').read_bytes(), content_type='image/jpeg'))
    page.route('**/preview/notes.md*', lambda r: r.fulfill(body=MARKDOWN, content_type='text/markdown'))
    page.route('**/preview/record.json*', lambda r: r.fulfill(body=JSON_TEXT, content_type='application/json'))
    page.route('**/api/d/example/q', lambda r: r.fulfill(json={'rows': [[42]]}))
    page.route('**/calliope/artifacts/example*', lambda r: r.fulfill(body='''<h1>Exact artifact revision</h1><p id="result">Loading data</p>
        <script>
        window.addEventListener('message',e=>{
          if(e.data.type==='calliope.query.result')document.querySelector('#result').textContent=JSON.stringify(e.data.result);
        });
        parent.postMessage({type:'calliope.query',id:'preview-query',sql:'select 42'},'*');
        </script>''', content_type='text/html'))
    page.evaluate('''() => {
      qa.state.surfaces=[
        {id:'image',kind:'image',title:'Picture',payload:{image_url:'/preview/picture.jpg',remote:true}},
        {id:'md',kind:'document',title:'Notes',payload:{filename:'notes.md',download_url:'/preview/notes.md'}},
        {id:'json',kind:'document',title:'Record',payload:{filename:'record.json',download_url:'/preview/record.json'}},
        {id:'artifact',kind:'artifact',title:'Dashboard',artifact_slug:'example',artifact_version:3,payload:{url:'/calliope/artifacts/example?version=3'}}
      ];
      qa.state.surfaces.forEach(s=>{s.turn_id='one';s.created_at=new Date().toISOString()});
      qa.renderChat(true); // Clear the base fixture's question overlay.
      qa.els.stage.innerHTML=qa.state.surfaces.map(s=>qa.surfaceCard(s)).join('');
      qa.setupEvents();qa.hydrateDocumentPreviews();
    }''')


def test_stage_image_and_rich_artifact_open_in_large_viewer(zen_page):
    page = zen_page
    seed_files(page)
    page.locator('.image-preview-link').click()
    dialog = page.locator('#surface-viewer-dialog')
    assert dialog.is_visible()
    page.locator('.viewer-image img').evaluate('(img)=>img.decode()')
    assert len(page.context.pages) == 1
    assert page.locator('#surface-viewer-external').get_attribute('href') == '/preview/picture.jpg'
    page.set_viewport_size({'width': 390, 'height': 900})
    assert dialog.bounding_box()['width'] <= 390
    assert page.locator('.viewer-image').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    page.screenshot(path='/tmp/surface-image-modal-mobile.png')
    page.keyboard.press('Escape')
    assert not dialog.is_visible()
    assert page.locator('.viewer-image img').count() == 0
    page.set_viewport_size({'width': 1500, 'height': 900})
    page.locator('[data-surface-id="artifact"] .surface-tools [data-open-file-surface]').click()
    frame = page.locator('.viewer-artifact iframe')
    assert 'version=3' in frame.get_attribute('src')
    assert 'embed=1' in frame.get_attribute('src')
    assert 'session=notebook' in frame.get_attribute('src')
    assert 'allow-same-origin' not in frame.get_attribute('sandbox')
    assert page.frame_locator('.viewer-artifact iframe').locator('h1').inner_text() == 'Exact artifact revision'
    page.frame_locator('.viewer-artifact iframe').get_by_text('{"rows":[[42]]}', exact=True).wait_for()
    page.locator('#surface-viewer-close').click()
    assert page.locator('.viewer-artifact iframe').count() == 0
    assert len(page.context.pages) == 1


def test_markdown_matches_stage_and_json_is_formatted_highlighted_and_readonly(zen_page):
    page = zen_page
    seed_files(page)
    stage_md = page.locator('[data-surface-id="md"] .document-preview-copy')
    stage_md.wait_for()
    page.locator('[data-surface-id="md"] .document-summary [data-open-file-surface]').click()
    body = page.locator('#surface-viewer-content .document-preview-copy')
    body.locator('table').wait_for()
    assert body.inner_html() == stage_md.inner_html()
    assert body.locator('strong').inner_text() == 'bold'
    assert body.locator('script').count() == 0
    assert page.evaluate('window.injection || false') is False
    page.screenshot(path='/tmp/surface-markdown-modal.png')
    page.keyboard.press('Escape')
    page.locator('[data-surface-id="json"] .document-summary [data-open-file-surface]').click()
    content = page.locator('.viewer-json .cm-content')
    content.wait_for()
    text = content.inner_text()
    assert '9007199254740993' in text and '1e400' in text
    assert '\n  "enabled": true,' in text
    assert content.get_attribute('contenteditable') == 'false'
    assert content.get_attribute('aria-readonly') == 'true'
    assert page.locator('.viewer-json .tok-propertyName').count() > 0
    content.focus()
    page.keyboard.type('DO NOT CHANGE')
    assert content.inner_text() == text
    page.screenshot(path='/tmp/surface-json-modal.png')
    page.locator('#surface-viewer-close').click()
    assert page.locator('.viewer-json .cm-editor').count() == 0


def test_file_failures_and_late_response_do_not_overwrite_another_viewer(zen_page):
    page = zen_page
    seed_files(page)
    page.route('**/preview/record.json*', lambda r: r.fulfill(body='{broken json', content_type='application/json'))
    page.evaluate("qa.openFileSurface('json')")
    assert 'not valid JSON' in page.locator('.document-preview-note').text_content()
    assert page.locator('.viewer-json .cm-content').inner_text() == '{broken json'
    page.route('**/preview/notes.md*', lambda r: r.fulfill(status=403, body='Not allowed'))
    page.evaluate("qa.openFileSurface('md')")
    assert '403' in page.locator('.viewer-error').inner_text()
    # Hold the network read, then replace the entire modal with another file.
    held = []
    page.route('**/preview/notes.md*', lambda r: held.append(r))
    page.evaluate("void qa.openFileSurface('md')")
    page.wait_for_timeout(100)
    page.evaluate("qa.openFileSurface('json')")
    for route in held:
        try:
            route.fulfill(body='LATE FILE', content_type='text/markdown')
        except Exception:
            pass  # The obsolete read was aborted when the viewer changed.
    page.wait_for_timeout(100)
    assert page.locator('#surface-viewer-title').inner_text() == 'Record'
    assert 'LATE FILE' not in page.locator('#surface-viewer-content').inner_text()
