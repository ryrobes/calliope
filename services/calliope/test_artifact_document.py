"""Served artifact pages keep the author's doctype first, so they stay in standards mode."""
import inspect

import pytest

import artifact_document
from artifact_document import with_injected_head

SHIM = '<script>window.injected=true</script>'
DOC = '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>T</title></head><body>x</body></html>'


def test_injection_follows_the_doctype():
    out = with_injected_head(SHIM, DOC)
    assert out.startswith('<!DOCTYPE html>') and out.index(SHIM) < out.index('<html')
    assert out == '<!DOCTYPE html>\n' + SHIM + DOC[len('<!DOCTYPE html>'):]


@pytest.mark.parametrize('lead', ['', '\ufeff', '\n  \n', '<!-- built by Callie -->\n', '\ufeff <!-- a --> <!--b-->'])
def test_bom_whitespace_and_comments_stay_ahead_of_the_injection(lead):
    out = with_injected_head(SHIM, lead + '<!doctype HTML>' + '<p>x</p>')
    assert out.startswith(lead + '<!doctype HTML>') and SHIM in out


def test_document_without_doctype_keeps_its_previous_form():
    assert with_injected_head(SHIM, '<html><body>x</body></html>') == SHIM + '<html><body>x</body></html>'
    assert with_injected_head(SHIM, None) == SHIM


def test_title_fallback_does_not_push_the_doctype_down():
    import server
    out = server._ensure_html_document_title('<!doctype html><div>chart</div>', 'Revenue')
    assert out.startswith('<!doctype html>') and '<title>Revenue</title>' in out


def test_every_served_composition_uses_the_helper():
    import calliope
    import server
    assert 'artifact_document.with_injected_head' in inspect.getsource(calliope._artifact_version_document)
    source = inspect.getsource(server)
    # The hosted /d/ and /apps/ route and the headless capture document.
    assert source.count('artifact_document.with_injected_head(') >= 3
    assert '+ _ensure_html_document_title(app_html' not in source


@pytest.mark.parametrize('embedded', [False, True])
def test_composed_documents_render_in_standards_mode(embedded):
    pw = pytest.importorskip('playwright.sync_api')
    import calliope
    import server
    artifact = ('<!DOCTYPE html><html><head><style>body{background:#111;color:#fff}'
                'td{background:#fff}</style></head><body><table><tr><td id="c">Petit larceny</td></tr></table></body></html>')
    document = calliope._artifact_version_document(
        'quirks-check', 1, artifact, server._dash_shim, embedded, {}, server._dash_runtime_shim)
    assert document.lstrip('\ufeff').lower().startswith('<!doctype html>')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.route('**/*', lambda r: r.fulfill(status=204))
        page.set_content(document, wait_until='domcontentloaded')
        assert page.evaluate('document.compatMode') == 'CSS1Compat'
        browser.close()
