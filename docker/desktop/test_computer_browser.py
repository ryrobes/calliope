from types import SimpleNamespace

import pytest

from computer_browser import BrowserNeedsAttention, browser_page, browser_diagnostics


class Page:
    def __init__(self, failures=(), url='https://lab.test/apps/example'):
        self.failures = list(failures)
        self.url = url
        self.calls = 0
        self.waits = []
        self.state = {'filter': 'July', 'map_loaded': True}

    def bring_to_front(self):
        pass

    def goto(self, url, **kwargs):
        assert 'ignore_https_errors' not in kwargs
        self.calls += 1
        if self.failures:
            raise RuntimeError(self.failures.pop(0))
        self.url = url
        self.state = {}
        return SimpleNamespace(status=200)

    def wait_for_load_state(self, state, **kwargs):
        self.waits.append((state, kwargs))


class Context:
    def __init__(self, pages):
        self.pages = pages
        self.created = 0

    def new_page(self):
        self.created += 1
        page = Page(url='about:blank')
        self.pages.append(page)
        return page


def browser(page):
    return SimpleNamespace(contexts=[Context([page])])


def test_repeated_lookup_preserves_filters_and_map_without_navigation():
    page = Page()
    connection = browser(page)
    for _ in range(3):
        assert browser_page(connection, page.url, timeout=1234) is page
    assert page.calls == 0
    assert page.state == {'filter': 'July', 'map_loaded': True}
    assert page.waits == [('domcontentloaded', {'timeout': 1234})] * 3
    assert connection.contexts[0].created == 0


def test_explicit_reload_navigates_once_in_the_same_tab():
    page = Page()
    connection = browser(page)
    assert browser_page(connection, page.url, reload=True) is page
    assert page.calls == 1
    assert page.state == {}
    assert connection.contexts[0].created == 0


@pytest.mark.parametrize('suffix', ['/another', '?year=2012', '#detail'])
def test_different_url_opens_once_without_changing_existing_tab(suffix):
    page = Page()
    connection = browser(page)
    target = page.url + suffix
    new = browser_page(connection, target)
    assert new is not page
    assert new.url == target
    assert new.calls == 1
    assert browser_page(connection, target) is new
    assert new.calls == 1
    assert page.calls == 0
    assert page.state['filter'] == 'July'
    assert connection.contexts[0].created == 1


def test_expired_connection_retries_in_same_authenticated_page():
    page = Page(['net::ERR_CERT_DATE_INVALID'])
    assert browser_page(browser(page), page.url, reload=True) is page
    assert page.calls == 2


@pytest.mark.parametrize('code,calls', [('ERR_CERT_DATE_INVALID', 2), ('ERR_CERT_AUTHORITY_INVALID', 1), ('ERR_CONNECTION_REFUSED', 1)])
def test_failure_is_bounded_and_never_exposes_request_credentials(code, calls):
    page = Page([f'net::{code}\nCookie: secret-cookie'] * 3)
    with pytest.raises(BrowserNeedsAttention) as raised:
        browser_page(browser(page), page.url, reload=True)
    assert page.calls == calls
    assert code in str(raised.value)
    assert 'secret-cookie' not in str(raised.value)


def test_login_redirect_does_not_create_another_context():
    page = Page()
    def goto(*args, **kwargs):
        page.url = 'https://lab.test/login?next=/apps/example'
        return SimpleNamespace(status=200)
    page.goto = goto
    with pytest.raises(BrowserNeedsAttention, match='browser_auth'):
        browser_page(browser(page), page.url, reload=True)


def test_reused_page_wait_error_is_sanitized_without_navigation():
    page = Page()
    def wait(*args, **kwargs):
        raise RuntimeError('Timeout waiting for document; Cookie: secret-cookie')
    page.wait_for_load_state = wait
    with pytest.raises(BrowserNeedsAttention) as raised:
        browser_page(browser(page), page.url)
    assert page.calls == 0
    assert 'secret-cookie' not in str(raised.value)


def test_http_error_is_reported_after_explicit_reload():
    page = Page()
    page.goto = lambda *args, **kwargs: SimpleNamespace(status=503)
    with pytest.raises(BrowserNeedsAttention, match='HTTP 503'):
        browser_page(browser(page), page.url, reload=True)


def test_blank_map_diagnostics_classify_errors_without_echoing_secrets():
    page = SimpleNamespace(
        evaluate=lambda _: dict(webgl2=False, visible=True, map_canvases=1, map_error=False, login=False, certificate_error=False),
        console_messages=lambda: [SimpleNamespace(type='error', text='Failed to initialize WebGL https://map.test?access_token=SECRET')],
        page_errors=lambda: [])
    result = browser_diagnostics(page)
    assert result['needs_attention']
    assert result['console_error_categories'] == ['webgl_failure']
    assert 'SECRET' not in str(result)


def test_healthy_page_does_not_claim_rendering_failure():
    page = SimpleNamespace(
        evaluate=lambda _: dict(webgl2=True, visible=True, map_canvases=1, map_error=False, login=False, certificate_error=False),
        console_messages=lambda: [], page_errors=lambda: [])
    assert not browser_diagnostics(page)['needs_attention']


def test_matching_expected_version_keeps_filters():
    page=Page()
    page.evaluate=lambda js,version: version==7
    assert browser_page(browser(page),page.url,expected_version=7) is page
    assert page.calls==0 and page.state['filter']=='July'


def test_stale_expected_version_reloads_once_then_keeps_new_filters():
    page=Page(); connection=browser(page)
    page.evaluate=lambda js,version: page.calls==1
    assert browser_page(connection,page.url,expected_version=7) is page
    assert page.calls==1 and connection.contexts[0].created==0
    page.state['filter']='August'
    browser_page(connection,page.url,expected_version=7)
    assert page.calls==1 and page.state['filter']=='August'


def test_missing_or_still_wrong_version_stops_after_one_reload():
    page=Page();page.evaluate=lambda *args:False
    with pytest.raises(BrowserNeedsAttention,match='expected version 7'):
        browser_page(browser(page),page.url,expected_version=7)
    assert page.calls==1


def test_unknown_version_evaluation_error_is_sanitized():
    page=Page()
    def evaluate(*args):raise RuntimeError('Cookie: private-cookie')
    page.evaluate=evaluate
    with pytest.raises(BrowserNeedsAttention) as e:
        browser_page(browser(page),page.url,expected_version=7)
    assert 'private-cookie' not in str(e.value)


@pytest.mark.parametrize('version',[True,0,-1,'7',7.1])
def test_invalid_expected_version_is_rejected_before_navigation(version):
    page=Page()
    with pytest.raises(ValueError):browser_page(browser(page),page.url,expected_version=version)
    assert page.calls==0
