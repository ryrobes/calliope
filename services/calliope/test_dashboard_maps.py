"""Hosted dashboards get maps and charts through the page shim, never through the artifact.

The appliance's Mapbox public token (URL-restricted per client) is injected
into `window.RVBBIT_MAPS` at render time by `_dash_shim`; `window.rvbbitMaps`
loads Mapbox GL JS on demand, themes it to the design profile, converts SQL
rows to GeoJSON, and exposes `pending` so captures wait for tiles like they
wait for queries. Stored HTML never contains the token.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


def test_shim_carries_maps_config_only_when_a_token_exists(monkeypatch):
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "")
    shim = server._dash_shim("slug", None, {})
    assert 'window.RVBBIT_MAPS={"enabled":false,"provider":null};' in shim
    assert "window.rvbbitMaps=api;" in shim          # authors can always feature-detect

    monkeypatch.setattr(server, "MAPBOX_TOKEN", "pk.test-token")
    monkeypatch.setattr(server, "MAPBOX_GL_VERSION", "v3.15.0")
    shim = server._dash_shim("slug", None, {})
    assert '"token":"pk.test-token"' in shim
    assert '"gl_js_url":"/vendor/mapbox-gl@3.15.0/mapbox-gl.js"' in shim          # vendored, immutable
    assert '"gl_css_url":"/vendor/mapbox-gl@3.15.0/mapbox-gl.css"' in shim
    assert '"styles":{"light":"mapbox://styles/mapbox/light-v11","dark":"mapbox://styles/mapbox/dark-v11"}' in shim
    # The helper's contract the template documents.
    for needle in ("async map(container,opts)", "preserveDrawingBuffer:true", "geojson(rows,o)", "bounds(fc)",
                   "api.pending+=1", "map.once('idle',done)", "onThemeChange", "data-rvbbit-color-mode",
                   "function color(v,fb)", "getImageData(0,0,1,1)", ",color,"):
        assert needle in shim, needle


def test_capture_waits_for_map_tiles_and_sends_a_referer_for_restricted_tokens(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_PUBLIC_URL", "https://calliope.example.com")
    assert server._capture_extra_headers() == {"Referer": "https://calliope.example.com/"}
    monkeypatch.delenv("WAREHOUSE_PUBLIC_URL", raising=False)
    assert server._capture_extra_headers() == {}
    assert "--use-angle=swiftshader" in server._CAPTURE_CHROMIUM_ARGS   # WebGL in headless Chromium
    import inspect
    src = inspect.getsource(server._wait_for_capture_bridge)
    assert "window.rvbbitMaps.pending" in src and "mapsPending===0" in src


def test_dashboard_template_advertises_maps_honestly(monkeypatch):
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "")
    block = server._dashboard_template_maps_block()
    assert block["available"] is False and "WAREHOUSE_MAPBOX_TOKEN" in block["hint"]

    monkeypatch.setattr(server, "MAPBOX_TOKEN", "pk.test-token")
    block = server._dashboard_template_maps_block()
    assert block["available"] is True and block["provider"] == "mapbox"
    assert "rvbbitMaps.map(container, opts)" in block["api"]
    assert "rvbbitMaps.map(" in block["recipe"] and "addLayer" in block["recipe"]
    assert "rvbbitMaps.color('--map-point-color'" in block["recipe"]      # oklch tokens never reach Mapbox raw
    assert "rvbbitMaps.color(cssColorOrToken, fallback?)" in block["api"]
    assert "pk.test-token" not in str(block)          # the token travels in the shim, not tool output
    assert any("Aggregate in SQL first" in line for line in block["guidance"])

    md = (_HERE / "DASHBOARD_TEMPLATE.md").read_text(encoding="utf-8")
    assert "### Maps (hosted appliances)" in md and "rvbbitMaps.geojson" in md


def test_shim_ships_theme_and_charts_helpers_and_capture_waits_for_charts():
    shim = server._dash_shim("slug", None, {})
    assert "window.rvbbitTheme={mode,token,color,palette,onModeChange," in shim
    assert "getImageData(0,0,1,1)" in shim                    # colour normalization by pixel readback
    assert '"provider":"vega-lite"' in shim and '"/vendor/vega-lite@6.4.3/vega-lite.min.js"' in shim
    assert '"/vendor/vega@6.4.0/vega.min.js"' in shim and '"/vendor/vega-embed@7.1.0/vega-embed.min.js"' in shim
    assert 'window.RVBBIT_VENDOR={"route":"/vendor","libraries":{"gridjs":{"version":"5.0.2","files":{"gridjs.umd.js":"/vendor/gridjs@5.0.2/gridjs.umd.js"' in shim
    for needle in ("window.rvbbitCharts=api;", "async embed(el,spec,opts)", "renderer:'svg'", "width='container'",
                   "range:t.palette.length?{category:t.palette", "T.onModeChange(()=>{for(const h of live)", "opts.onSelect"):
        assert needle in shim, needle
    import inspect
    assert "chartsPending===0" in inspect.getsource(server._wait_for_capture_bridge)


def test_runtime_shim_ships_the_helpers_without_the_cookie_transport(monkeypatch):
    """The Calliope stage embeds artifacts in a sandboxed iframe whose queries
    travel over a postMessage bridge. That document must still get rvbbitMaps /
    rvbbitCharts / rvbbitTheme (the bug: a map that feature-detected
    window.rvbbitMaps rendered in a full tab and silently skipped on the stage)."""
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "pk.test-token")
    monkeypatch.setattr(server, "MAPBOX_GL_VERSION", "v3.15.0")
    runtime = server._dash_runtime_shim("slug", None, {"design_profile": {"id": "p1"}})
    full = server._dash_shim("slug", None, {"design_profile": {"id": "p1"}})

    # merges into the bridge's RVBBIT_DASHBOARD instead of clobbering it
    assert "window.RVBBIT_DASHBOARD=Object.assign({slug:\"slug\",version:null,calliope_enabled:" in runtime
    assert "if(window.RVBBIT_DASHBOARD.manifest==null)window.RVBBIT_DASHBOARD.manifest=" in runtime
    for needle in ('"token":"pk.test-token"', "window.rvbbitMaps=api;", "window.rvbbitCharts=api;",
                   "window.rvbbitTheme={mode,token,color,palette,onModeChange,",
                   'window.RVBBIT_VENDOR={"route":"/vendor"', "_recordQueryTrace=", "root.semanticMap=()=>semantic;"):
        assert needle in runtime, needle
        assert needle in full, needle
    # no cookie-bearing transport, no Lens/theme scripts: the embed brings its own
    # No transport of its own: the embed's bridge supplies one, and the shared
    # queue only wraps whichever transport is already there.
    assert "fetch('/api/d/'" not in runtime and "window.rvbbitQuery=async function" not in runtime
    assert runtime.count("window.rvbbitQuery=") == 1 and "window.rvbbitQuery=queued" in runtime
    assert "artifact-lens" not in runtime and "adaptive-artifact.js" not in runtime
    assert "window.cowork" not in runtime
    # the direct shim is exactly root + runtime + transport + theme scripts
    assert server._DASH_SHIM == (
        server._DASH_SHIM_HEAD + "<script>\n" + server._DASH_SHIM_DIRECT_ROOT
        + server._DASH_SHIM_RUNTIME + server._DASH_SHIM_TRANSPORT + server._DASH_SHIM_QUEUE + server._APP_ACTION_SHIM + "</script>\n" + server._DASH_SHIM_TAIL
    )
    assert "fetch('/api/d/'" in full and 'src="/theme/artifact-lens.js"' in full


def test_dashboard_template_advertises_charts():
    block = server._dashboard_template_charts_block()
    assert block["provider"] == "vega-lite" and block["versions"]["vega_lite"] == server.CHARTS_VEGA_LITE_VERSION
    assert "rvbbitCharts.embed(container, vegaLiteSpec, opts?)" in block["api"]
    assert "rvbbitCharts.embed('#trend'" in block["recipe"]
    assert any("Without a profile, author" in g for g in block["guidance"])
    md = (_HERE / "DASHBOARD_TEMPLATE.md").read_text(encoding="utf-8")
    assert "### Charts on hosted pages (Vega-Lite via `rvbbitCharts`)" in md


def test_vendor_manifest_is_the_only_source_of_runtime_urls_and_falls_back_upstream(monkeypatch, tmp_path):
    import json, subprocess, sys as _sys
    root = _HERE.parents[1]
    # Every manifest file is present and hash-verified in this checkout/image.
    check = subprocess.run([_sys.executable, str(root / "scripts" / "vendor-dashboard-assets.py"), "--check"],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    manifest = json.loads((_HERE / "vendor" / "manifest.json").read_text(encoding="utf-8"))
    for name, lib in manifest["libraries"].items():
        for fname, spec in lib["files"].items():
            asset = server._vendor_asset(name, fname)
            assert asset["vendored"] is True and asset["url"] == f"/vendor/{name}@{lib['version']}/{fname}"
            assert spec["sha256"] and spec["url"].startswith("https://")
    # Without the files on disk the shim degrades to the manifest's upstream URL.
    monkeypatch.setattr(server, "_VENDOR_DIR", tmp_path)
    server._vendor_manifest.cache_clear()
    try:
        (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        asset = server._vendor_asset("vega", "vega.min.js")
        assert asset["vendored"] is False and asset["url"] == manifest["libraries"]["vega"]["files"]["vega.min.js"]["url"]
        assert server._charts_runtime_config()["vendored"] is False
    finally:
        server._vendor_manifest.cache_clear()


def test_vendor_route_is_path_safe_and_immutable():
    import warehouse_theme
    assert warehouse_theme._VENDOR_LIB.fullmatch("vega-lite@6.4.3")
    assert not warehouse_theme._VENDOR_LIB.fullmatch("../etc")
    assert not warehouse_theme._VENDOR_LIB.fullmatch("vega-lite")            # version is required
    assert warehouse_theme._VENDOR_FILE.fullmatch("mapbox-gl.css")
    assert not warehouse_theme._VENDOR_FILE.fullmatch("../../server.py")
    assert not warehouse_theme._VENDOR_FILE.fullmatch("evil.html")
    src = (_HERE / "warehouse_theme.py").read_text(encoding="utf-8")
    route = src[src.index('custom_route("/vendor/{lib}/{filename}"'):]
    assert "public, max-age=31536000, immutable" in route[:1600]
    caddy = (_HERE.parents[1] / "docker" / "origin" / "Caddyfile.calliope").read_text(encoding="utf-8")
    assert caddy.count("/vendor/*") == 2                                     # both origin doors forward it


def test_origin_forwards_connected_data_through_both_entrypoints():
    caddy = (_HERE.parents[1] / "docker" / "origin" / "Caddyfile.calliope").read_text(
        encoding="utf-8"
    )
    assert caddy.count("/data /data/*") == 2


def test_publish_advisories_flag_cdn_maps_and_default_limits(monkeypatch):
    """The first Hermes-built page on the live source drew its map with Leaflet from
    unpkg + OSM tiles and plotted 1,000 of 2,508 stations because rvbbitQuery's
    default limit was never raised. publish/update now say so."""
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "pk.test-token")
    html = ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
            '<link href="https://fonts.googleapis.com/css2?family=Newsreader" rel="stylesheet">'
            '<script>const rows = await rvbbitQuery("select lat, lon from live_bikeshare.station_information");'
            'L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map)</script>')
    codes = {a["code"]: a for a in server._dashboard_advisories(html)}
    assert set(codes) == {"EXTERNAL_RUNTIME_ASSETS", "MAP_NOT_RVBBITMAPS", "POINT_LAYER_DEFAULT_LIMIT"}
    assert codes["EXTERNAL_RUNTIME_ASSETS"]["assets"] and all("unpkg.com" in a for a in codes["EXTERNAL_RUNTIME_ASSETS"]["assets"])
    assert "fonts.googleapis.com" not in json.dumps(codes["EXTERNAL_RUNTIME_ASSETS"])   # fonts are fine
    assert "rvbbitMaps" in codes["MAP_NOT_RVBBITMAPS"]["fix"]
    # A page that does it right is quiet.
    good = ('<script>const rows = await rvbbitQuery("select lat, lon from t", {limit: 5000});'
            'const map = await rvbbitMaps.map(el, {}); const fc = rvbbitMaps.geojson(rows, {lon:"lon", lat:"lat"});</script>')
    assert server._dashboard_advisories(good) == []
    # Without a Mapbox token, Leaflet is not called out (there is nothing better to offer).
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "")
    assert "MAP_NOT_RVBBITMAPS" not in {a["code"] for a in server._dashboard_advisories(html)}
    # And the template says the two rules out loud.
    monkeypatch.setattr(server, "MAPBOX_TOKEN", "pk.test-token")
    guidance = " ".join(server._dashboard_template_maps_block()["guidance"])
    assert "Do not load Leaflet" in guidance and "1,000 rows by default" in guidance
