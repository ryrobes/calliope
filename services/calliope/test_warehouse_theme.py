"""Focused contracts for the shared Warehouse image-theme shell."""
from __future__ import annotations

import asyncio
import ast
import importlib.util
import re
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

from starlette.responses import Response


_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "warehouse_theme_test_module",
    _HERE / "warehouse_theme.py",
)
warehouse_theme = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = warehouse_theme
_SPEC.loader.exec_module(warehouse_theme)


def test_lens_wallpaper_library_is_shipped_as_web_sized_pairs():
    items = warehouse_theme.theme_library()
    assert len(items) == 84
    assert len({item["id"] for item in items}) == len(items)
    assert all(item["thumb"].startswith("/theme/images/thumb/") for item in items)
    assert all(item["url"].startswith("/theme/images/full/") for item in items)

    by_id = {item["id"]: item for item in items}
    assert by_id["callie-bg-bright"]["label"] == "Callie Bright"
    assert by_id["callie-bg-dark"]["label"] == "Callie Dark"

    total = 0
    for item in items:
        full = warehouse_theme._IMAGE_DIR / "full" / f"{item['id']}.webp"
        thumb = warehouse_theme._IMAGE_DIR / "thumb" / f"{item['id']}.webp"
        assert full.is_file() and thumb.is_file()
        assert full.read_bytes()[:4] == b"RIFF"
        assert full.read_bytes()[8:12] == b"WEBP"
        assert thumb.read_bytes()[:4] == b"RIFF"
        total += full.stat().st_size + thumb.stat().st_size
    # The Lens originals are ~171 MB. Keep this standalone copy appropriate
    # for the Warehouse image/container rather than silently re-copying 4K.
    assert total < 30 * 1024 * 1024


def test_login_backgrounds_are_also_available_in_the_appearance_picker():
    # Read the catalog without importing auth's environment-dependent setup.
    module = ast.parse((_HERE / "auth.py").read_text())
    backgrounds = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BACKGROUNDS"
                for target in node.targets)
    )
    ids = {item["id"] for item in warehouse_theme.theme_library()}
    missing = [name for name in backgrounds
               if name not in ids and f"{name}-high" not in ids]
    assert not missing, f"Missing full/thumb WebP pairs for login backgrounds: {missing}"


def test_theme_assets_are_shared_by_every_first_party_warehouse_shell():
    assets = warehouse_theme.head_assets()
    assert '/calliope/callie-favicon.png' in assets
    assert 'rel="icon"' in assets
    assert "family=Homemade+Apple" in assets
    assert "/theme/superior-fonts.css" in assets
    assert "IBM+Plex" not in assets
    assert "Newsreader" not in assets
    assert "/theme/warehouse-theme.css" in assets
    assert "/theme/warehouse-theme.js" in assets

    auth = (_HERE / "auth.py").read_text(encoding="utf-8")
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    calliope = (_HERE / "calliope" / "index.html").read_text(encoding="utf-8")
    assert "warehouse_theme.head_assets()" in auth
    assert server.count("warehouse_theme.head_assets()") >= 2
    assert "/theme/warehouse-theme.js" in calliope
    assert "/calliope/callie-favicon.png" in calliope
    assert all("data-warehouse-theme-anchor" in page for page in (auth, server, calliope))
    assert "auth.register_login_route(m, provider, _RABBIT_SVG)" in server
    assert "<span class=loginbrand>{_CALLIOPE_BRAND}</span>" in auth
    assert 'class="calliope-brand-name">Calliope' in server
    assert 'class="calliope-brand-byline">by RVBBIT.AI' in server
    assert server.count('class="calliope-brand-provenance"') >= 2
    assert 'class="calliope-brand-rabbit" src="/theme/datarabbit-mark.svg"' in server
    assert 'class="calliope-brand-name">Calliope' in calliope
    assert 'class="calliope-brand-provenance"' in calliope
    assert "DATA RABBIT" not in calliope
    assert "var(--amber,#e8b572)" in auth
    favicon = warehouse_theme._FAVICON
    assert favicon.is_file()
    assert 'viewBox="0 0 32 32"' in favicon.read_text(encoding="utf-8")
    mark = favicon.with_name("datarabbit-mark.svg")
    assert mark.is_file()
    assert 'viewBox="0 0 1383 709"' in mark.read_text(encoding="utf-8")
    assert "/calliope/callie-favicon.png" in server


def test_complete_superior_family_is_self_hosted_with_native_weights():
    font_dir = _HERE / "theme" / "fonts"
    css = (_HERE / "theme" / "superior-fonts.css").read_text(encoding="utf-8")
    expected = {
        "LTSuperior-Thin.otf",
        "LTSuperior-ExtraLight.otf",
        "LTSuperior-Light.otf",
        "LTSuperior-Regular.otf",
        "LTSuperior-Medium.otf",
        "LTSuperior-SemiBold.otf",
        "LTSuperior-Bold.otf",
        "LTSuperior-ExtraBold.otf",
        "LTSuperior-Black.otf",
        "LTSuperiorSerif-Regular.otf",
        "LTSuperiorSerif-Medium.otf",
        "LTSuperiorSerif-Semibold.otf",
        "LTSuperiorSerif-Bold.otf",
        "LTSuperiorSerif-ExtraBold.otf",
        "LTSuperiorMono-Regular.otf",
        "LTSuperiorMono-Medium.otf",
        "LTSuperiorMono-Semibold.otf",
        "LTSuperiorMono-Bold.otf",
    }
    assert {path.name for path in font_dir.glob("*.otf")} == expected
    assert css.count("@font-face") == len(expected)
    assert all(f'/theme/fonts/{name}' in css for name in expected)
    assert all(f"font-weight:{weight}" in css for weight in range(100, 1000, 100))
    assert 'font-family:"Superior Serif"' in css
    assert 'font-family:"Superior Mono"' in css
    assert css.count("font-display:swap") == len(expected)
    assert "--rvbbit-type-small:11px" in css
    assert "--rvbbit-type-body:14px" in css
    assert "--rvbbit-type-large:18px" in css


def test_shared_theme_chrome_respects_the_product_type_floor():
    css = (_HERE / "theme" / "warehouse-theme.css").read_text(encoding="utf-8")
    declarations = re.findall(r"(?:font-size|font)\s*:\s*([^;}]+)", css)
    subminimum = [
        float(size)
        for declaration in declarations
        for size in re.findall(r"(?<![\w-])(\d+(?:\.\d+)?)px", declaration)
        if 0 < float(size) < 11
    ]

    assert subminimum == []


def test_font_routes_allow_cross_origin_fetches_for_originless_captures():
    """Captures, PDFs, and thumbnails render artifacts from about:blank, so the
    @font-face requests are cross-origin: without CORS the Superior family
    silently falls back in every screenshot the agent inspects."""
    source = (_HERE / "warehouse_theme.py").read_text(encoding="utf-8")
    font_route = source[source.index('custom_route("/theme/fonts/{font_name}"'):]
    font_route = font_route[: font_route.index("custom_route(", 40)]
    assert '"access-control-allow-origin": "*"' in font_route
    css_route = source[source.index('custom_route("/theme/superior-fonts.css"'):]
    css_route = css_route[: css_route.index("custom_route(", 40)]
    assert '"access-control-allow-origin": "*"' in css_route


def test_artifact_not_found_is_generic_adaptive_system_chrome(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "auth",
        SimpleNamespace(
            background_layer=lambda opacity, veil, scene_key=None: (
                calls.append((opacity, veil, scene_key)) or '<div class="warehouse-desktop-background"></div>'
            )
        ),
    )

    html = warehouse_theme.artifact_not_found_document(
        "Ada@Example.com",
        {"identity": "Ada@Example.com", "name": "Ada", "via": "google"},
    )

    assert 'data-warehouse-page="artifact-not-found"' in html
    assert "This does not exist or you might not have permission." in html
    assert "no such artifact" not in html.lower()
    assert "artifact version" not in html.lower()
    assert "/theme/warehouse-theme.js" in html
    assert "font:500 clamp(38px,6.2vw,76px)/.96 var(--font-superior-serif)" in html
    assert 'href="/gallery"' in html
    assert 'href="/calliope"' in html
    assert calls[0][2] == "ada@example.com"

    server_source = (_HERE / "server.py").read_text(encoding="utf-8")
    calliope_source = (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert "warehouse_theme.artifact_not_found_document(identity, session)" in server_source
    assert "warehouse_theme.artifact_not_found_document(owner, session)" in calliope_source
    assert "404 — no such artifact version" not in server_source


def test_authenticated_shells_share_a_right_aligned_accessible_account_menu():
    markup = warehouse_theme.account_control({
        "identity": "long.person@example.com",
        "name": "Long Person",
        "via": "google",
        "picture": "https://lh3.googleusercontent.com/a/example",
    })
    fallback = warehouse_theme.account_control({
        "identity": "warehouse.user@example.com",
        "via": "password",
    })
    initials_only = warehouse_theme.account_control({
        "identity": "warehouse_role",
        "via": "pg",
    })
    workspace = warehouse_theme.account_control({
        "identity": "admin@example.com",
        "via": "password",
    }, workspace_menu=True, can_administer=True)
    workspace_member = warehouse_theme.account_control({
        "identity": "member@example.com",
        "via": "password",
    }, workspace_menu=True, can_administer=False)
    calliope_page = (_HERE / "calliope" / "index.html").read_text(encoding="utf-8")
    calliope_source = (_HERE / "calliope.py").read_text(encoding="utf-8")
    server_source = (_HERE / "server.py").read_text(encoding="utf-8")
    theme_source = (_HERE / "theme" / "warehouse-theme.src.js").read_text(encoding="utf-8")
    theme_bundle = (_HERE / "theme" / "warehouse-theme.js").read_text(encoding="utf-8")
    theme_css = (_HERE / "theme" / "warehouse-theme.css").read_text(encoding="utf-8")
    calliope_css = (_HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")

    assert 'data-warehouse-account' in markup
    assert 'data-warehouse-owner="long.person@example.com"' in markup
    assert 'aria-haspopup="menu"' in markup
    assert 'aria-expanded="false"' in markup
    assert 'src="/auth/avatar"' in markup
    assert "googleusercontent.com" not in markup
    assert "Long Person" in markup
    assert 'href="/auth/logout" role="menuitem"' in markup
    assert "WU" in fallback
    assert 'src="/auth/avatar"' in fallback
    assert "gravatar.com" not in fallback
    assert "WR" in initials_only
    assert 'src="/auth/avatar"' not in initials_only
    assert 'data-warehouse-owner="warehouse_role"' in initials_only
    assert 'class="warehouse-account-menu workspace-account-menu"' in workspace
    assert 'data-warehouse-account-action="design-profiles"' in workspace
    assert 'href="/data"' in workspace
    assert "Connected data" in workspace
    assert 'data-warehouse-account-admin' in workspace
    assert 'data-warehouse-account-action="library"' in workspace
    assert "Capabilities and global permissions" in workspace
    assert 'data-warehouse-account-action="design-profiles"' in workspace_member
    assert 'href="/data"' in workspace_member
    assert 'data-warehouse-account-admin' not in workspace_member
    assert 'data-warehouse-account-action="library"' not in workspace_member

    header = calliope_page.split('<nav class="topbar"', 1)[1].split("</nav>", 1)[0]
    assert "__CALLIOPE_ACCOUNT__" in header
    assert "/auth/logout" not in header
    assert 'workspace_menu=True' in calliope_source
    assert 'can_administer=can_administer' in calliope_source
    assert "can_administer=can_administer" in server_source
    assert "installAccountControls();" in theme_source
    assert 'event.key !== "Escape"' in theme_source
    assert '["ArrowDown", "ArrowUp", "Home", "End"]' in theme_source
    assert 'avatar?.classList.add("has-image")' in theme_source
    assert "warehouse-account-avatar-image" in theme_bundle
    assert ".warehouse-account-menu" in theme_css
    assert ".warehouse-account-menu.workspace-account-menu" in theme_css
    assert ".warehouse-account-action" in theme_css
    assert ".warehouse-account-avatar.has-image" in theme_css
    assert "border: 0" in theme_css
    assert "body[data-warehouse-page=\"calliope\"] .warehouse-account-email" in theme_css
    assert ".top-context .instrument-library-open" in calliope_css
    assert ".brand .calliope-brand-name{font-size:var(--rvbbit-type-large)}" in calliope_css
    assert ".brand .calliope-brand{display:none}" not in calliope_css


def test_google_profile_claims_are_bounded_and_round_trip_only_in_google_sessions(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "warehouse_account_auth_test_module",
        _HERE / "auth.py",
    )
    account_auth = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = account_auth
    spec.loader.exec_module(account_auth)
    monkeypatch.setattr(account_auth, "JWT_SECRET", "account-control-test-secret")

    picture = "https://lh3.googleusercontent.com/a/profile-photo"
    profile = account_auth.google_profile({
        "name": "  Ada   Lovelace  ",
        "picture": picture,
    })
    assert profile == {"name": "Ada Lovelace", "picture": picture}
    assert account_auth._google_picture_url("https://example.com/avatar.png") == ""
    assert account_auth._google_picture_url(
        "https://googleusercontent.com.evil.example/avatar.png"
    ) == ""
    assert account_auth._google_picture_url(
        "https://user@lh3.googleusercontent.com/avatar.png"
    ) == ""
    digest = account_auth.hashlib.sha256(b"user@example.com").hexdigest()
    assert account_auth._gravatar_url("  User@Example.com ") == (
        f"https://www.gravatar.com/avatar/{digest}?s=96&d=404&r=g"
    )
    assert account_auth._gravatar_url("not-an-email") == ""
    assert account_auth._gravatar_picture_url(
        "https://gravatar.com.evil.example/avatar.png"
    ) == ""
    auth_source = (_HERE / "auth.py").read_text(encoding="utf-8")
    assert '"scope": "openid email profile"' in auth_source
    assert '@mcp.custom_route("/auth/avatar", methods=["GET"])' in auth_source
    assert "urljoin(current" in auth_source
    assert "_fetch_gravatar_avatar(identity)" in auth_source

    response = Response()
    account_auth.set_session(
        response,
        "analyst_role",
        secure=False,
        identity="ada@example.com",
        mapped=True,
        via="google",
        name=profile["name"],
        picture=profile["picture"],
    )
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    request = SimpleNamespace(cookies={
        account_auth.SESSION_COOKIE: cookies[account_auth.SESSION_COOKIE].value,
    })
    session = account_auth.read_session_full(request)
    assert session["name"] == "Ada Lovelace"
    assert session["picture"] == picture

    password_response = Response()
    account_auth.set_session(
        password_response,
        "analyst_role",
        secure=False,
        identity="ada@example.com",
        via="password",
        picture=picture,
    )
    cookies = SimpleCookie()
    cookies.load(password_response.headers["set-cookie"])
    password_session = account_auth.read_session_full(SimpleNamespace(cookies={
        account_auth.SESSION_COOKIE: cookies[account_auth.SESSION_COOKIE].value,
    }))
    assert password_session["picture"] == ""

    routes = {}

    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[(path, tuple(methods))] = handler
                return handler
            return register

    class Provider:
        public = "https://warehouse.example"

        @staticmethod
        def has_pending(_txn):
            return True

    active_session = {"value": {
        "identity": "ada@example.com",
        "via": "password",
        "picture": "",
    }}
    avatar_calls = []

    async def google_avatar(value):
        avatar_calls.append(("google", value))
        return b"google-image", "image/jpeg"

    async def gravatar_avatar(value):
        avatar_calls.append(("gravatar", value))
        return b"gravatar-image", "image/png"

    monkeypatch.setattr(
        account_auth, "read_session_full", lambda _request: active_session["value"]
    )
    monkeypatch.setattr(account_auth, "_fetch_google_avatar", google_avatar)
    monkeypatch.setattr(account_auth, "_fetch_gravatar_avatar", gravatar_avatar)
    account_auth.register_login_route(MCP(), Provider())
    avatar_route = routes[("/auth/avatar", ("GET",))]

    result = asyncio.run(avatar_route(SimpleNamespace()))
    assert result.status_code == 200
    assert result.body == b"gravatar-image"
    assert result.media_type == "image/png"
    assert avatar_calls == [("gravatar", "ada@example.com")]

    active_session["value"] = {
        "identity": "ada@example.com",
        "via": "google",
        "picture": picture,
    }
    result = asyncio.run(avatar_route(SimpleNamespace()))
    assert result.body == b"google-image"
    assert avatar_calls[-1] == ("google", picture)

    active_session["value"] = {
        "identity": "warehouse_role",
        "via": "pg",
        "picture": "",
    }
    result = asyncio.run(avatar_route(SimpleNamespace()))
    assert result.status_code == 404
    assert avatar_calls[-1] == ("google", picture)

    async def gravatar_missing(_value):
        raise account_auth._AvatarNotFound

    active_session["value"] = {
        "identity": "nobody@example.com",
        "via": "password",
        "picture": "",
    }
    monkeypatch.setattr(account_auth, "_fetch_gravatar_avatar", gravatar_missing)
    result = asyncio.run(avatar_route(SimpleNamespace()))
    assert result.status_code == 404
    assert result.headers["cache-control"] == "private, no-store"


def test_navigation_prepaints_the_desktop_and_never_uses_cross_document_snapshots():
    auth = (_HERE / "auth.py").read_text(encoding="utf-8")
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    calliope_css = (_HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")
    shared_css = (_HERE / "theme" / "warehouse-theme.css").read_text(encoding="utf-8")

    assert 'class="warehouse-desktop-background" aria-hidden="true"' in auth
    assert 'data-warehouse-background-url="/bg/{bg}.jpg"' in auth
    assert 'data-warehouse-background-opacity="{image_opacity}"' in auth
    assert "pick_background(scene_key" in auth
    assert "scene_key=viewer" in server
    assert "scene_key=owner" in (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert "@view-transition" not in shared_css
    assert "::view-transition" not in shared_css
    assert "background: var(--void, #100d0b)" in shared_css
    assert "body > main" in shared_css
    assert "animation: warehouse-page-in" in shared_css
    assert "cubic-bezier(.16, 1, .3, 1) backwards" in shared_css
    assert "@keyframes warehouse-page-in" in shared_css
    assert "prefers-reduced-motion: reduce" in shared_css
    assert 'html[data-warehouse-background-bridge]::before' in shared_css
    assert 'html[data-warehouse-background-bridge="settling"]::before' in shared_css
    assert 'body[data-warehouse-page="calliope"] > main {' in shared_css
    assert 'body[data-warehouse-page="calliope"] > main > .stage-column > *' in shared_css
    assert '<style>html{background:#100d0b}body{background:transparent}</style>' in (
        _HERE / "calliope" / "index.html"
    ).read_text(encoding="utf-8")
    # No shell may re-enable compositor snapshots locally.
    assert "@view-transition" not in server
    assert "@view-transition" not in calliope_css


def test_theme_pipeline_uses_vibrant_tokens_and_browser_storage():
    source = (_HERE / "theme" / "warehouse-theme.src.js").read_text(encoding="utf-8")
    bundle = (_HERE / "theme" / "warehouse-theme.js").read_text(encoding="utf-8")
    css = (_HERE / "theme" / "warehouse-theme.css").read_text(encoding="utf-8")

    assert 'from "node-vibrant/browser"' in source
    assert "paletteToImagePalette" in source
    assert "deriveWarehouseTokens" in source
    assert "localStorage.setItem" in source
    assert "indexedDB.open" in source
    assert "data-theme-background-mode" in source
    assert "data-theme-solid-color" in source
    assert "backgroundChoice" in source
    assert "warehouseBackground" in source
    assert "rvbbit-warehouse-color-mode" in source
    assert "rvbbit-warehouse-background-bridge-v1" in source
    assert "rvbbit-warehouse-adaptive-theme-v1" in source
    assert "getSnapshot: getAdaptiveSnapshot" in source
    assert "adaptiveThemeSnapshot" in source
    assert "snapshot," in source
    assert "sessionStorage.setItem(BACKGROUND_BRIDGE_KEY" in source
    assert "settleBackgroundBridge" in source
    assert "deriveLightWarehouseTokens" in source
    assert "warehouseColorMode" in source
    assert "warehouse-theme-mode-button" in source
    assert "Switch to light mode" in source
    assert "Switch to dark mode" in source
    selection = source.split("function selectLibraryItem(id)", 1)[1].split(
        "async function applySelectedTheme", 1
    )[0]
    assert "renderLibrarySelection(item.id);" in selection
    assert "renderLibrary();" not in selection
    assert "renderedPreviewBackgroundImage" in source
    assert 'candidate.decoding = "async";' in source
    assert 'preview.setAttribute("aria-busy", "true");' in source
    assert 'data-theme-preview-status' in source
    assert 'setPreviewStatus(selectedPalette ? "" : "Deriving colors…");' in source
    assert 'apply.textContent = selectedPalette ? "Apply" : "Deriving…";' not in source
    assert ".warehouse-theme-preview-status" in css
    assert "flex: 0 0 auto;" in css
    assert "overflow-y: auto;" in css
    assert "rvbbit-warehouse-theme-v1" in bundle
    assert "rvbbit-warehouse-color-mode" in bundle
    assert "rvbbit-warehouse-background-bridge-v1" in bundle
    assert "rvbbit-warehouse-adaptive-theme-v1" in bundle
    assert "/theme/library" in bundle
    assert "data-theme-background-mode" in bundle
    assert "warehouseBackground" in bundle
    assert source.count('<svg viewBox="0 0 24 24"') == 2
    assert "warehouse-theme-button-thumb" in bundle
    assert (_HERE / "theme" / "VIBRANT-LICENSE").is_file()

    # Parent chrome and native charts are themeable; authored app iframe
    # documents remain their own CSS trees.
    assert "html[data-warehouse-theme] .chart-bar" in css
    assert "html[data-warehouse-theme] .artifact-frame iframe" not in css
    assert 'body[data-warehouse-page="calliope"] .surface' in css
    assert '[data-warehouse-background="solid"] .bg' in css
    assert "--warehouse-solid-background" in css
    assert 'body[data-warehouse-page="calliope"] .bg' in css
    assert ':root[data-warehouse-color-mode="light"]' in css
    assert '.warehouse-theme-mode-button' in css
    assert 'html[data-warehouse-color-mode="light"] body[data-warehouse-page="calliope"] .veil' in css
    assert 'html[data-warehouse-color-mode="light"] .artifact-frame iframe' not in css
    assert "position: fixed;" in css
    assert "margin: auto;" in css
    assert "scrollbar-color:" in css
    assert "::-webkit-scrollbar-thumb" in css


def test_container_and_unified_origin_ship_theme_assets():
    dockerfile = (_HERE / "Dockerfile").read_text(encoding="utf-8")
    caddy = (_HERE.parent.parent / "docker" / "origin" / "Caddyfile").read_text(encoding="utf-8")
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    assert "COPY services/calliope/theme ./theme" in dockerfile
    assert "/theme/*" in caddy
    assert "/charts/*" in caddy
    assert "warehouse_theme.register_theme_routes(m)" in server
    assert warehouse_theme._ARTIFACT_LENS_JS.is_file()
    assert warehouse_theme._ARTIFACT_LENS_CSS.is_file()
    assert warehouse_theme._ADAPTIVE_ARTIFACT_JS.is_file()
    assert "/theme/adaptive-artifact.js" in (
        _HERE / "warehouse_theme.py"
    ).read_text(encoding="utf-8")


def test_unified_origin_routes_gallery_api_to_warehouse():
    caddy = (_HERE.parent.parent / "docker" / "origin" / "Caddyfile").read_text(
        encoding="utf-8"
    )
    assert "/gallery /api/gallery/* /calliope" in caddy


def test_optional_tanstack_runtime_is_versioned_public_and_inlineable_for_captures():
    runtime = warehouse_theme._TANSTACK_CHARTS_JS
    assert warehouse_theme.TANSTACK_CHARTS_VERSION == "0.16.2"
    assert warehouse_theme.TANSTACK_CHARTS_SRC == "/charts/rvbbit-tanstack-charts-0.16.2.js"
    assert runtime.is_file()
    assert runtime.stat().st_size > 100_000
    assert "RVBBIT_CHARTS" in runtime.read_text(encoding="utf-8")

    ordinary = '<script src="https://cdn.example/chart.js"></script>'
    assert warehouse_theme.inline_chart_runtime(ordinary) == ordinary
    opted_in = (
        '<html><head><script defer src="/charts/rvbbit-tanstack-charts-0.16.2.js">'
        "</script></head><body></body></html>"
    )
    inlined = warehouse_theme.inline_chart_runtime(opted_in)
    assert warehouse_theme.TANSTACK_CHARTS_SRC not in inlined
    assert 'data-rvbbit-chart-runtime="0.16.2"' in inlined
    assert "RVBBIT_CHARTS" in inlined

    dockerfile = (_HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY services/calliope/charts ./charts" in dockerfile
    assert "COPY services/calliope/examples ./examples" in dockerfile


def test_adaptive_runtime_is_inlined_for_originless_captures():
    ordinary = "<html><head></head><body>ordinary</body></html>"
    assert warehouse_theme.inline_adaptive_runtime(ordinary) == ordinary

    adaptive = (
        '<html><head><script src="/theme/adaptive-artifact.js"></script>'
        "</head><body></body></html>"
    )
    inlined = warehouse_theme.inline_capture_runtimes(adaptive)
    assert 'src="/theme/adaptive-artifact.js"' not in inlined
    assert 'data-rvbbit-adaptive-runtime="1"' in inlined
    assert "rvbbit.viewer-theme.v1" in inlined
    assert 'wallpaper: "/bg/the_black_site.jpg"' in inlined


def test_adaptive_theme_snapshots_are_semantically_deduplicated():
    theme_source = (_HERE / "theme" / "warehouse-theme.src.js").read_text(encoding="utf-8")
    artifact_runtime = (_HERE / "theme" / "adaptive-artifact.js").read_text(encoding="utf-8")

    assert "adaptiveSnapshotFallbackUpdatedAt" in theme_source
    assert "function adaptiveSnapshotFingerprint(snapshot)" in theme_source
    assert "updated_at: _updatedAt" in theme_source
    assert "adaptiveSnapshotFingerprint(previous) === adaptiveSnapshotFingerprint(candidate)" in theme_source
    assert "persistAdaptiveSnapshot(snapshot);" in theme_source
    assert "appliedSnapshotFingerprint" in artifact_runtime
    assert "if (fingerprint === appliedSnapshotFingerprint) return false;" in artifact_runtime
    # Theme application is presentation-only: the runtime deliberately emits no
    # public event, so a CSS-variable change can never be mistaken for a data
    # invalidation signal (see test_adaptive_artifact_runtime_is_opt_in_...).
    assert "public event that dashboard authors can mistake for a data invalidation" in artifact_runtime
    assert 'new CustomEvent("rvbbit:adaptive-theme"' not in artifact_runtime


def test_server_appearance_hydration_never_exposes_the_default_theme():
    source = (_HERE / "theme" / "warehouse-theme.src.js").read_text(encoding="utf-8")
    hydrate = source.split("async function hydrateServerAppearanceOnce()", 1)[1].split(
        "function installAccountControls", 1
    )[0]

    assert "serverAppearanceHydration" in source
    assert "appearanceFingerprint(remoteState) === appearanceFingerprint(current)" in hydrate
    assert "clearAppliedTheme();" not in hydrate
    assert hydrate.index("applyStoredState(current, activeObjectUrl);") < hydrate.index(
        "clearUploadBlob()"
    )
    assert "function stableJson(value)" in source


def test_gallery_calliope_entry_is_a_floating_time_aware_avatar():
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    server += (_HERE / "theme" / "metric-cards.js").read_text(encoding="utf-8")
    server += (_HERE / "theme" / "metric-cards.css").read_text(encoding="utf-8")
    assert 'class="calliope-float"' in server
    assert 'class="calliope-float-avatar"' in server
    assert 'class="calliope-float-name">Calliope</span>' in server
    assert 'data-day-src="/calliope/callie-avatar-day.jpg"' in server
    assert 'data-night-src="/calliope/callie-avatar-night.jpg"' in server
    assert "family=Homemade+Apple" in warehouse_theme.head_assets()
    assert "position:fixed;right:var(--calliope-edge);bottom:var(--calliope-edge)" in server
    assert "--gallery-rail-bg:color-mix(in oklch,var(--void) 85%,transparent)" in server
    assert "background:color-mix(in oklch,var(--void) 85%,transparent)" in server
    assert "--calliope-capsule-bg:color-mix(in oklch,#fffaf1 86%,var(--amber))" in server
    assert "--calliope-capsule-ink:color-mix(in oklch,#100d0b 91%,var(--amber))" in server
    assert "background:linear-gradient(135deg,#fff,var(--calliope-capsule-bg))" in server
    assert "color:var(--calliope-capsule-ink);font-family" in server
    assert "width:44px;height:44px" in server
    assert '<img alt="" width="44" height="44" decoding="async">' in server
    assert 'class="calliope-float-copy"' in server
    assert 'class="calliope-float-action">Open workspace' in server
    assert 'aria-label="Open the full Calliope workspace"' in server
    assert "view-transition" not in server
    assert "sparkGradientSequence" in server
    assert "metric-card-area-gradient-" in server
    assert 'class="metric-card-area-clear" offset="82%"' in server
    assert 'class="metric-card-area-floor" offset="100%"' in server
    assert "--metric-card-floor:var(--panel)" in server
    assert 'id="semantic-launch"' in server
    assert 'id="semantic-launch-button"' in server
    assert "/api/calliope/evidence-explorations" in server
    assert "e.key==='Enter'" in server
    shot_rule = server.split(".shot{", 1)[1].split("}", 1)[0]
    assert "border-bottom" not in shot_rule
    assert (
        '<span class="who"><span data-warehouse-theme-anchor></span>{account}'
    ) in server
    assert (
        '<span class="who"><span data-warehouse-theme-anchor></span>'
        "{_calliope_link}"
    ) not in server


def test_authenticated_page_headers_follow_the_floor_rail_contract():
    floor_css = (_HERE / "calliope" / "floor.css").read_text(encoding="utf-8")
    calliope_css = (_HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")
    calliope_script = (_HERE / "calliope" / "calliope.js").read_text(
        encoding="utf-8"
    )
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    theme_css = (_HERE / "theme" / "warehouse-theme.css").read_text(
        encoding="utf-8"
    )

    shared_geometry = (
        "height:56px;display:flex;align-items:center;gap:24px;"
        "padding:0 max(20px,4vw);border-bottom:1px solid var(--line)"
    )
    shared_glass = (
        "-webkit-backdrop-filter:blur(18px) saturate(1.16);"
        "backdrop-filter:blur(18px) saturate(1.16)"
    )

    assert shared_geometry in floor_css
    assert shared_geometry in calliope_css
    assert "height:56px;padding:0 max(20px,4vw);border-bottom:1px solid var(--line)" in server
    assert shared_glass in floor_css
    assert shared_glass in calliope_css
    assert shared_glass in server
    assert ".floor-brand{display:flex;align-items:center;flex:none;transform:translateY(3px)}" in floor_css
    assert ".brand{display:flex;align-items:center;gap:12px;flex:none;transform:translateY(3px)}" in calliope_css
    assert "nav>.calliope-brand{flex:none;transform:translateY(3px)}" in server
    assert ".floor-rail-actions{margin-left:auto;display:flex;align-items:center;gap:10px}" in floor_css
    assert ".top-context{margin-left:auto;display:flex;align-items:center;gap:10px" in calliope_css
    assert (
        'body:not(.zen)[data-warehouse-page="calliope"] .topbar'
        "{gap:12px;padding-right:20px}"
    ) in calliope_css
    assert (
        'body:not(.zen)[data-warehouse-page="calliope"] .top-context'
        "{gap:4px;letter-spacing:0}"
    ) in calliope_css
    assert ".who{margin-left:auto;display:flex;align-items:center;gap:10px" in server
    assert (
        'body[data-warehouse-page="calliope"] .topbar {\n'
        "  background: color-mix(in oklch, var(--void) 85%, transparent) !important;\n"
        "  -webkit-backdrop-filter: blur(18px) saturate(1.16);\n"
        "  backdrop-filter: blur(18px) saturate(1.16);"
    ) in theme_css

    assert "brand.after(form)" not in calliope_script
    assert "form.hidden = Boolean(zen);" in calliope_script
    assert ".evidence-search[hidden]{display:none!important}" in calliope_css
    assert 'html body.zen[data-warehouse-page="calliope"] .topbar .evidence-search' not in calliope_css


def test_gallery_and_pinned_metric_charts_fade_beneath_legible_text():
    server = (_HERE / "server.py").read_text(encoding="utf-8")
    server += (_HERE / "theme" / "metric-cards.js").read_text(encoding="utf-8")
    server += (_HERE / "theme" / "metric-cards.css").read_text(encoding="utf-8")

    assert "homeSparkGradientSequence" in server
    assert "home-metric-area-gradient-" in server
    assert 'class="home-metric-area-clear" offset="82%"' in server
    assert 'class="home-metric-area-floor" offset="100%"' in server
    assert "--home-metric-floor:var(--void)" in server
    assert ".home-tile.metric .home-tile-main{text-shadow:0 1px 1px" in server
    assert ".metric-card-content{" in server
    assert "0 8px 22px color-mix(in oklch,var(--void) 84%,transparent)" in server


def test_tanstack_assets_serve_and_capture_their_exact_authored_versions():
    routes = {}

    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[path] = handler
                return handler
            return register

    warehouse_theme.register_theme_routes(MCP())
    expected = {
        '/charts/rvbbit-tanstack-charts-0.3.1.js': ('0.3.1', 'RVBBIT_CHARTS'),
        warehouse_theme.TANSTACK_CHARTS_SRC: ('0.16.2', 'RVBBIT_CHARTS'),
        warehouse_theme.TANSTACK_TABLE_SRC: ('9.2.4', 'RVBBIT_TABLE'),
        warehouse_theme.TANSTACK_VIRTUAL_SRC: ('3.17.10', 'RVBBIT_VIRTUAL'),
    }
    for src, (version, global_name) in expected.items():
        response = asyncio.run(routes[src](None))
        assert Path(response.path).name == src.split('/')[-1]
        assert response.headers['cache-control'].endswith('immutable')
        authored = f"<script src='{src}'></script>"
        inlined = warehouse_theme.inline_chart_runtime(authored)
        assert inlined == f'<script data-rvbbit-chart-runtime="{version}">' + re.sub(
            r'</script', r'<\\/script', Path(response.path).read_text(), flags=re.IGNORECASE
        ) + '</script>'
        assert global_name in inlined


def test_tanstack_headless_cores_work_in_a_browser_without_charts_or_react():
    from playwright.sync_api import sync_playwright

    html = warehouse_theme.inline_chart_runtime(
        f'<script src="{warehouse_theme.TANSTACK_TABLE_SRC}"></script>'
        f'<script src="{warehouse_theme.TANSTACK_VIRTUAL_SRC}"></script>'
    )
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(html)
        facts = page.evaluate('''() => {
          const T = RVBBIT_TABLE, V = RVBBIT_VIRTUAL;
          const table = T.constructTable({
            features: T.tableFeatures({coreReactivityFeature: T.storeReactivityBindings()}),
            data: [{name: 'Search', revenue: 420}, {name: 'Social', revenue: 210}],
            columns: [{accessorKey: 'name'}, {accessorKey: 'revenue'}]
          });
          const virtualizer = new V.Virtualizer({
            count: 100, getScrollElement: () => null, estimateSize: () => 30,
            initialRect: {width: 400, height: 300},
            scrollToFn: V.elementScroll, observeElementRect: V.observeElementRect,
            observeElementOffset: V.observeElementOffset
          });
          return {
            versions: [T.version, V.version],
            values: table.getRowModel().rows.map(row => row.getValue('revenue')),
            totalSize: virtualizer.getTotalSize(),
            visible: virtualizer.getVirtualItems().length,
            unrelatedGlobalsAbsent: !window.React && !window.RVBBIT_CHARTS
          };
        }''')
        browser.close()
    assert errors == []
    assert facts['versions'] == ['9.2.4', '3.17.10']
    assert facts['values'] == [420, 210]
    assert facts['totalSize'] == 3000
    assert 0 < facts['visible'] < 100
    assert facts['unrelatedGlobalsAbsent']
