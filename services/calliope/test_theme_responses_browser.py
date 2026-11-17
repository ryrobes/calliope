"""Personal settings layout and response preferences using the shipped bundle."""
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import warehouse_theme


def test_compact_settings_and_audio_includes_short_answers():
    pw = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parent
    items = warehouse_theme.theme_library()
    item = next((row for row in items if row["id"] == "callie-bg-dark"), items[0])
    appearance = {
        "version": 1, "source": {"kind": "library", **{k: item[k] for k in ("id", "url", "label")}},
        "palette": {"baseHue": 205, "chroma": .08, "vibrant": "#3e8aa9", "darkVibrant": "#194962",
                    "lightVibrant": "#8fb8ca", "muted": "#5d7d91", "darkMuted": "#29334b", "lightMuted": "#b2cbd4"},
        "tokens": {"--main": "#3e8aa9", "--background": "#03131c"},
    }
    prefs = {"conversational_answers": True, "read_aloud_mode": "expressive",
             "response_personality": "Warm, concise, and slightly playful. Keep the facts clear.",
             "speak_working_updates": False}
    writes, errors = [], []
    html = '''<!doctype html><html><head><link rel="stylesheet" href="/theme/superior-fonts.css">
      <link rel="stylesheet" href="/theme/warehouse-theme.css"><style>
      :root{--void:#03131c;--panel:#071e2b;--bone:#d8e4eb;--fog:#a0b5c1;--dim:#7896a6;--line:#234152;--amber:#3e8aa9;--main:#3e8aa9}
      body{background:#071119;font-family:Arial;color:#d8e4eb}header{padding:20px}</style>
      <script defer src="/theme/warehouse-theme.js"></script></head>
      <body><header>Calliope <span data-warehouse-theme-anchor></span></header></body></html>'''

    def route(request):
        path = urlsplit(request.request.url).path
        if path == "/test":
            return request.fulfill(body=html, content_type="text/html")
        if path == "/theme/library":
            return request.fulfill(json={"items": items})
        if path == "/api/calliope/appearance":
            return request.fulfill(json={"appearance": appearance, "mode": "dark", "personal_mode": "dark",
                                         "source": "personal", "owner": "viewer@example.com", "has_upload": False})
        if path == "/api/calliope/response-preferences":
            if request.request.method == "PUT":
                prefs.update(request.request.post_data_json["preferences"])
                writes.append(dict(prefs))
            return request.fulfill(json={"owner": "viewer@example.com", "exists": True, "preferences": prefs})
        file = root / path.lstrip("/")
        if file.is_file() and file.resolve().is_relative_to(root):
            return request.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
        request.fulfill(status=404)

    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route)
        page.goto("http://settings.test/test")
        page.wait_for_function("WarehouseTheme.getVoice().mode === 'expressive'")
        page.wait_for_function("WarehouseTheme.getState()?.source?.id")
        page.evaluate("WarehouseTheme.open()")
        page.wait_for_function("document.querySelectorAll('[data-theme-id]').length > 20")
        page.evaluate("document.fonts.ready")
        short = page.locator("[data-theme-voice-presentation]")
        personality = page.locator("[data-theme-voice-personality]")
        pw.expect(short).to_be_checked()
        pw.expect(short).to_be_disabled()
        pw.expect(short).to_be_hidden()
        pw.expect(page.locator("[data-theme-audio-note]")).to_be_visible()
        pw.expect(personality).to_be_enabled()
        pw.expect(page.locator("[data-theme-presentation-hint]")).to_have_text("Included with read aloud")
        assert not writes
        for width, height in [(1788, 1376), (1440, 900), (1366, 768), (1024, 768)]:
            page.set_viewport_size({"width": width, "height": height})
            for background in ("image", "solid"):
                page.locator(f'[data-theme-background-mode="{background}"]').click()
                sizes = page.locator(".warehouse-theme-preview").evaluate("e=>({client:e.clientHeight,scroll:e.scrollHeight})")
                assert sizes["scroll"] <= sizes["client"] + 1, (width, height, background, sizes)
                pw.expect(page.locator("[data-theme-apply]")).to_be_in_viewport()
            assert page.locator(".warehouse-theme-library").evaluate("e=>e.scrollHeight>e.clientHeight")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator('[data-theme-background-mode="image"]').click()
        page.screenshot(path="/tmp/calliope-settings-compact.png")
        page.locator('[data-theme-voice-mode="fast"]').click()
        pw.expect(personality).to_be_disabled()
        pw.expect(short).to_be_checked()
        pw.expect(short).to_be_disabled()
        page.locator('[data-theme-voice-mode="off"]').click()
        pw.expect(short).to_be_enabled()
        pw.expect(short).to_be_checked()
        pw.expect(personality).to_be_enabled()
        short.uncheck()
        pw.expect(personality).to_be_disabled()
        page.locator('[data-theme-voice-mode="expressive"]').click()
        pw.expect(short).to_be_checked()
        page.locator('[data-theme-voice-mode="off"]').click()
        pw.expect(short).not_to_be_checked()
        page.wait_for_function("WarehouseTheme.getVoice().mode === 'off'")
        page.wait_for_timeout(100)
        assert writes[-1]["read_aloud_mode"] == "off"
        assert writes[-1]["conversational_answers"] is False
        assert writes[-1]["response_personality"] == prefs["response_personality"]
        page.reload()
        page.wait_for_function("WarehouseTheme.getVoice().personality.length > 0")
        assert page.evaluate("WarehouseTheme.getVoice().presentation") is False
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("WarehouseTheme.open()")
        page.locator("[data-theme-apply]").scroll_into_view_if_needed()
        pw.expect(page.locator("[data-theme-apply]")).to_be_in_viewport()
        assert page.locator(".warehouse-theme-content").evaluate("e=>e.scrollWidth<=e.clientWidth+1")
        assert errors == []
        browser.close()
