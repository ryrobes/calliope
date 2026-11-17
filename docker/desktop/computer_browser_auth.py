"""Install a server-issued first-party cookie into the existing visible Chrome."""
import time
from urllib.parse import urlsplit

COOKIE = '__Host-calliope_computer'


def install(origin, token, expires_at):
    from playwright.sync_api import sync_playwright
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or not isinstance(token, str) or not 1 <= len(token) <= 8192
            or not time.time() < float(expires_at) <= time.time() + 600):
        raise ValueError('Invalid Computer browser identity')
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://127.0.0.1:9223', timeout=4000)
        context = browser.contexts[0]
        context.set_default_timeout(4000)
        # URL-scoped, host-only, HttpOnly, Secure. Retain the expired JWT as a
        # marker so expiry cannot reveal an older human session underneath it.
        # Server-side JWT lifetime is five minutes, independent of persistence.
        cookies = context.cookies([origin])
        for human in (c for c in cookies if c['name'] == 'wh_session'):
            context.clear_cookies(name='wh_session', domain=human['domain'], path=human['path'])
        current = next((c for c in cookies if c['name'] == COOKIE), None)
        if not current or current['value'] != token:
            context.add_cookies([{'name': COOKIE, 'value': token, 'url': origin + '/',
                                 'httpOnly': True, 'secure': True, 'sameSite': 'Lax',
                                 'expires': int(time.time()) + 30 * 86400}])
        # Never return cookie values or Playwright exception text to receipts.
        return {'status': 'ready', 'origin': origin, 'expires_at': expires_at}
