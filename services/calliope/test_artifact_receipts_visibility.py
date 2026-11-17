"""Human Receipts controls stay out of computer sessions and captured artifacts."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import auth
import server


@pytest.fixture
def artifact_routes(monkeypatch):
    routes, viewers = {}, []

    class MCP:
        def custom_route(self, path, methods):
            def register(handler):
                routes[path] = handler
                return handler
            return register

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            if "SELECT id,slug,name" in sql:
                row = {"id": 1, "slug": "receipt-test", "name": "Revenue",
                       "latest_version": 2, "app_kind": "dashboard"}
            elif "SELECT html,manifest" in sql:
                row = {"html": "<main><button id='filter'>Filter</button></main>", "manifest": {}}
            else:
                # No additional semantic overlay in this fixture.
                row = None
            return SimpleNamespace(fetchone=lambda: row)

    monkeypatch.setattr(server, "_conn", Connection)
    monkeypatch.setattr(server.artifact_access, "require_view",
                        lambda conn, slug, viewer: viewers.append(viewer))
    monkeypatch.setattr(server.artifact_access, "can_view", lambda *args, **kwargs: True)
    monkeypatch.setattr(server, "_record_artifact_view", lambda *args: None)
    monkeypatch.setattr(server, "_live_app_runner_status", lambda *args, **kwargs: {"running": False})
    server.register_dashboard_routes(MCP())
    return routes, viewers


@pytest.mark.parametrize("route", [
    "/d/{slug}", "/d/{slug}/versions/{version}", "/apps/{slug}",
    "/apps/{slug}/versions/{version}", "/apps/{slug}/{path:path}",
])
@pytest.mark.parametrize("via", ["google", "password", "computer"])
def test_published_routes_omit_only_the_human_overlay(artifact_routes, monkeypatch, route, via):
    routes, viewers = artifact_routes
    monkeypatch.setattr(auth, "read_session_full", lambda request: {
        "sub": "warehouse_role", "identity": "owner@example.com", "mapped": True, "via": via,
    })
    request = Request({
        "type": "http", "scheme": "https", "method": "GET", "path": "/d/receipt-test",
        "headers": [(b"accept", b"text/html")], "query_string": b"",
        "path_params": {"slug": "receipt-test", "version": "1", "path": "detail"},
    })
    response = asyncio.run(routes[route](request))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert viewers == ["owner@example.com"]
    document = response.body.decode()
    assert ("/theme/artifact-lens.js" in document) is (via != "computer")
    assert ("/theme/artifact-lens.css" in document) is (via != "computer")
    # Only the human overlay is suppressed; the actual app and its diagnostics
    # are exactly what the agent is supposed to test.
    assert "id='filter'" in document
    assert "window.rvbbitQuery" in document
    assert "window.RVBBIT_DASHBOARD.queryTrace" in document
    assert "window.rvbbitMaps" in document
    assert "/theme/adaptive-artifact.js" in document
    assert "window.RVBBIT_ACTIONS_DISABLED=true" not in document


def test_receipts_dom_and_query_traces_for_human_computer_and_capture(monkeypatch):
    from playwright.sync_api import sync_playwright

    # Avoid any external services while executing the real injected runtime
    # and Receipts script. Both still use the same published query transport.
    monkeypatch.setattr(server, "_maps_runtime_config", lambda: {})
    monkeypatch.setattr(server, "_charts_runtime_config", lambda: {})
    monkeypatch.setattr(server, "_vendor_runtime_config", lambda: {})
    script = (Path(__file__).parent / "theme/artifact-lens.js").read_text()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for mode in ("human", "computer", "capture"):
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            def respond(route):
                url = route.request.url
                if url.endswith("/theme/artifact-lens.js"):
                    route.fulfill(content_type="text/javascript", body=script)
                elif url.endswith("/q"):
                    route.fulfill(content_type="application/json",
                                  body='{"rows":[{"value":42}],"columns":[{"name":"value"}],"row_count":1}')
                elif "/theme/" in url:
                    route.fulfill(content_type="text/css" if url.endswith(".css") else "text/javascript", body="")
                else:
                    route.fulfill(content_type="application/json", body='{"eligible":false}')

            page.route("**/*", respond)
            page.goto("https://receipts.example/")
            app = "<main><button id='filter' onclick='window.clicked=true'>Filter</button></main>"
            document = (
                server._capturable_dashboard_html("receipt-test", {"html": app})
                if mode == "capture" else server._dash_shim("receipt-test", receipts=mode == "human") + app
            )
            page.set_content(document, wait_until="load")
            assert page.locator("rvbbit-artifact-lens").count() == (1 if mode == "human" else 0)
            page.locator("#filter").click()
            assert page.evaluate("window.clicked") is True
            assert page.evaluate("async () => (await rvbbitQuery('select 42 as value')).rows[0].value") == 42
            assert page.evaluate("RVBBIT_DASHBOARD.queryTrace().length") == 1
            page.close()
        browser.close()
    assert errors == []
