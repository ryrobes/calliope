"""Navigation and bounded diagnostics for the existing, authenticated desktop.

Never return raw Playwright exceptions: request logs can contain session cookies.
Never recover a TLS failure by disabling verification or creating a context.
"""
import re
from urllib.parse import urlsplit


class BrowserNeedsAttention(RuntimeError):
    pass


def browser_page(connection, url, *, timeout=30000, reload=False, expected_version=None):
    """Open/reuse a tab without resetting its state; reload=True navigates again.

    Reuse matches the complete URL, including its query and fragment. Wait for
    the document, then let callers wait for their app's data/rendering readiness.
    expected_version verifies a published artifact; a stale reused document is
    reloaded once, in the same tab. Matching documents retain filter state.
    Authentication and TLS verification stay in the delegated browser profile.
    """
    target = urlsplit(url)
    if target.scheme not in ('http', 'https') or not target.netloc or target.username or target.password:
        raise ValueError('Use an HTTP(S) URL without embedded credentials')
    if expected_version is not None and (isinstance(expected_version, bool) or
            not isinstance(expected_version, int) or expected_version < 1):
        raise ValueError('expected_version must be a positive artifact version integer')
    if not connection.contexts:
        raise BrowserNeedsAttention('The shared browser profile is unavailable; do not create a new context.')
    context = connection.contexts[0]
    page = next((p for p in context.pages if p.url == url), None)
    reuse = page is not None and not reload
    if page is None:
        page = context.new_page()
    page.bring_to_front()
    for attempt in range(2):
        try:
            if reuse:
                page.wait_for_load_state('domcontentloaded', timeout=timeout)
                response = None
            else:
                response = page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            break
        except Exception as exc:
            # A renewed short-lived lab certificate can leave an old connection
            # in Chrome. Only retry this navigation, once, with normal TLS checks.
            match = re.search(r'net::(ERR_[A-Z_]+)', str(exc))
            code = match.group(1) if match else 'navigation_failed'
            if code == 'ERR_CERT_DATE_INVALID' and attempt == 0:
                continue
            raise BrowserNeedsAttention(
                f'Browser navigation failed ({code}). Check the origin certificate/connection. '
                'Keep this profile; do not bypass TLS or create a fresh browser context.') from None
    if response and response.status >= 400:
        raise BrowserNeedsAttention(f'Page returned HTTP {response.status}; inspect the response before clicking.')
    if urlsplit(page.url).path.rstrip('/') == '/login' and target.path.rstrip('/') != '/login':
        raise BrowserNeedsAttention(
            'Navigation reached login. For this appliance, check open_desktop.browser_auth and use its '
            'exact origin in the existing profile. External sites require their own sign-in; do not copy credentials.')
    if expected_version is not None:
        try:
            matches = page.evaluate("""expected => {
              const value = window.RVBBIT_DASHBOARD;
              const parts = location.pathname.split('/').filter(Boolean);
              return value?.version === expected && ['apps','d'].includes(parts[0])
                && value.slug === decodeURIComponent(parts[1] || '');
            }""", expected_version)
        except Exception:
            raise BrowserNeedsAttention('Cannot read the loaded artifact version; inspect this tab before testing.') from None
        if not matches:
            if reuse:
                return browser_page(connection, url, timeout=timeout, reload=True, expected_version=expected_version)
            raise BrowserNeedsAttention(
                f'Loaded artifact does not match expected version {expected_version} after navigation. '
                'Check the publication receipt and URL before testing; do not repeat interaction retries.')
    return page


def browser_diagnostics(page):
    """Report capability/error categories, never cookies, token URLs or page text."""
    result = page.evaluate(r"""() => {
      const c = document.createElement('canvas'), gl = c.getContext('webgl2');
      const webgl2 = !!gl;
      gl?.getExtension('WEBGL_lose_context')?.loseContext();
      return {webgl2, visible:document.visibilityState === 'visible',
        map_canvases:document.querySelectorAll('canvas.mapboxgl-canvas').length,
        map_error:!!document.querySelector('[data-rvbbit-map-error]'),
        maps:window.rvbbitMaps?.diagnostics?.() || null,
        artifact_version:Number.isInteger(window.RVBBIT_DASHBOARD?.version) ? window.RVBBIT_DASHBOARD.version : null,
        login:location.pathname.replace(/\/$/,'') === '/login',
        certificate_error:document.documentURI.startsWith('chrome-error:')};
    }""")
    categories = set()
    # Error text is used only for classification, never emitted to the model.
    for message in page.console_messages():
        if message.type != 'error':
            continue
        text = message.text.lower()
        if 'webgl' in text or 'context lost' in text:
            categories.add('webgl_failure')
        if 'err_cert_' in text:
            categories.add('certificate_failure')
        if 'failed to load resource' in text:
            categories.add('resource_load_failure')
    result['page_error_count'] = len(page.page_errors())
    result['console_error_categories'] = sorted(categories)
    result['needs_attention'] = bool(result['certificate_error'] or result['map_error'] or
                                     (result.get('maps') or {}).get('healthy') is False or
                                     (result['map_canvases'] and not result['webgl2']))
    if result['needs_attention']:
        result['next'] = ('Stop coordinate retries. The page has a rendering or connection failure; '
                          'repair that failure and reload in the same authenticated profile.')
    return result
