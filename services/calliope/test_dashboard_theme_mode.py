"""Real-browser checks for map/chart theme parity across viewing contexts."""
import html
import json
from pathlib import Path
import sys

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import server


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                     args=['--no-sandbox'])
        yield browser
        browser.close()


def document(scheme='dark', override=None):
    mode = f' data-rvbbit-color-mode="{override}"' if override else ''
    # Use the real serving runtime, but no external resources or data calls.
    runtime = server._dash_runtime_shim('theme-check', 1, {})
    config = {'styles': {'dark': 'mapbox://styles/mapbox/dark-v11',
                         'light': 'mapbox://styles/mapbox/light-v11'}}
    runtime = runtime.replace('window.RVBBIT_MAPS=' + json.dumps(server._maps_runtime_config(), separators=(',', ':')),
                              'window.RVBBIT_MAPS=' + json.dumps(config))
    return (f'<html{mode}><head><style>:root{{color-scheme:{scheme}}}'
            'html.light{color-scheme:light}</style>' + runtime + '</head><body></body></html>')


@pytest.mark.parametrize('scheme,preference,override,expected', [
    ('dark', 'light', None, 'dark'),
    ('light', 'dark', None, 'light'),
    ('only dark', 'light', None, 'dark'),
    ('only light', 'dark', None, 'light'),
    ('dark light', 'light', None, 'light'),
    ('light dark', 'dark', None, 'dark'),
    ('normal', 'dark', None, 'dark'),
    ('normal', 'light', None, 'light'),
    ('dark', 'dark', 'light', 'light'),
    ('light', 'light', 'dark', 'dark'),
    ('dark', 'light', 'invalid', 'dark'),
])
@pytest.mark.parametrize('embedded', [False, True])
def test_declared_scheme_and_profile_override(browser, scheme, preference, override, expected, embedded):
    page = browser.new_page(color_scheme=preference)
    try:
        doc = document(scheme, override)
        page.set_content('<iframe sandbox="allow-scripts" srcdoc="' + html.escape(doc, quote=True) + '"></iframe>'
                         if embedded else doc)
        frame = page.frames[1] if embedded else page.main_frame
        frame.wait_for_function('!!window.rvbbitMaps')
        result = frame.evaluate('''() => ({mode:rvbbitTheme.mode(),style:rvbbitMaps.style(),
            text:rvbbitTheme.tokens().text,authored:getComputedStyle(document.body).color})''')
        assert result == {'mode': expected, 'style': f'mapbox://styles/mapbox/{expected}-v11',
                          'text': result['authored'], 'authored': result['authored']}
    finally:
        page.close()


def test_mode_changes_follow_css_profile_and_browser_without_duplicate_events(browser):
    page = browser.new_page(color_scheme='light')
    try:
        page.set_content(document())
        page.evaluate('window.changes=[];rvbbitTheme.onModeChange(m=>changes.push(m));document.documentElement.classList.add("light")')
        page.wait_for_function('changes.length===1')
        page.evaluate('document.documentElement.dataset.rvbbitColorMode="dark"')
        page.wait_for_function('changes.length===2')
        page.evaluate('''delete document.documentElement.dataset.rvbbitColorMode;
            document.documentElement.style.colorScheme='dark light';''')
        page.wait_for_function('changes.length===3')
        page.emulate_media(color_scheme='dark')
        page.wait_for_function('changes.length===4')
        assert page.evaluate('changes') == ['light', 'dark', 'light', 'dark']
    finally:
        page.close()


def test_capture_theme_reports_resolved_mode_and_redacts_style_credentials(browser):
    page = browser.new_page(color_scheme='light')
    try:
        page.set_content(document())
        evidence = server._capture_theme_state(page)
        assert evidence['resolved_color_mode'] == 'dark'
        assert evidence['declared_color_scheme'] == 'dark'
        assert evidence['browser_color_mode'] == 'light'
        assert evidence['map_default_style'] == 'mapbox://styles/mapbox/dark-v11'
        assert evidence['healthy'] is True
        page.evaluate("rvbbitMaps.style=()=> 'https://user:secret@maps.example/style.json?access_token=secret#secret'")
        evidence = server._capture_theme_state(page)
        assert evidence['map_default_style'] == 'https://maps.example/style.json'
        assert 'secret' not in json.dumps(evidence)
    finally:
        page.close()
