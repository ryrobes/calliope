"""Demo mode stays off by default and does not widen a guest session."""
import asyncio
import json
import os
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response

import auth
import demo_mode
import server

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "crates/pg_rvbbit/sql/migrations/0509_calliope_demo_guest.sql"


def _request(method, path, body=None, cookie=None):
    raw = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    headers = [(b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "server": ("app.test", 80),
        "headers": headers,
        "path_params": {"slug": path.split("/")[3] if path.startswith("/api/d/") else "public-demo-revenue"},
        "query_string": b"",
    }, receive)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return {"demo_public": True, "archived": False, "app_kind": "dashboard", "latest_version": 1}

    def fetchall(self):
        return []

    def transaction(self):
        from contextlib import nullcontext
        return nullcontext()


def _routes():
    found = {}

    class MCP:
        def custom_route(self, path, methods):
            def register(fn):
                found[path] = fn
                return fn
            return register

    server.register_dashboard_routes(MCP())
    return found


def _guest_cookie():
    auth.JWT_SECRET = "demo-test-secret"
    response = Response()
    auth.set_session(
        response, demo_mode.ROLE, False,
        identity=demo_mode.GUEST_EMAIL, mapped=True, via="demo", guest=True,
    )
    header = response.headers["set-cookie"]
    pair = header.split(";", 1)[0]
    assert pair.startswith("wh_session=")
    assert "HttpOnly" in header
    return pair


def test_demo_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CALLIOPE_DEMO_MODE", raising=False)
    assert demo_mode.enabled() is False
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "0")
    assert demo_mode.enabled() is False
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    assert demo_mode.enabled() is True


def test_guest_cookie_is_ignored_when_demo_mode_is_off(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    cookie = _guest_cookie()
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "0")
    request = _request("GET", "/gallery", cookie=cookie)
    assert auth.read_session_full(request) is None
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    session = auth.read_session_full(request)
    assert session["guest"] is True
    assert session["sub"] == demo_mode.ROLE
    assert session["identity"] == demo_mode.GUEST_EMAIL
    assert session["mapped"] is True


@pytest.mark.parametrize("method,path,allowed", [
    ("GET", "/gallery", True),
    ("GET", "/", True),
    ("GET", "/demo", True),
    ("GET", "/d/public-demo-revenue", True),
    ("GET", "/d/public-demo-revenue/versions/2", True),
    ("GET", "/api/d/public-demo-revenue/provenance", True),
    ("GET", "/api/gallery/metrics", True),
    ("GET", "/theme/artifact-lens.js", True),
    ("GET", "/thumbs/dashboard/public-demo-revenue.png", True),
    ("POST", "/api/d/public-demo-revenue/q", True),
    ("GET", "/floor", False),
    ("GET", "/calliope", False),
    ("GET", "/data", False),
    ("GET", "/team", False),
    ("GET", "/desk", False),
    ("GET", "/ops", False),
    ("POST", "/mcp", False),
    ("POST", "/api/d/public-demo-revenue/actions/save", False),
    ("GET", "/api/gallery/metrics/revenue", False),
    ("GET", "/api/calliope/home", False),
    ("GET", "/apps/secret", False),
])
def test_guest_route_allowlist(method, path, allowed):
    assert demo_mode.route_allowed(path, method) is allowed


@pytest.mark.parametrize("sql", [
    "SELECT * FROM rvbbit.secrets",
    "SELECT rvbbit.get_secret('x')",
    "SELECT get_secret('x')",
    "SELECT classify('hello')",
    "SELECT means(1)",
    "SELECT mcp_call('tool')",
    "SELECT synth_sql('select 1')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM public_demo.orders; SELECT * FROM rvbbit.secrets",
    "INSERT INTO public_demo.orders VALUES (9, 'West', 1)",
    "SELECT * FROM orders",
    "SELECT * FROM public.customers",
    "SELECT set_config('role', 'postgres', false)",
])
def test_guest_sql_rejects_operator_and_rvbbit_access(sql):
    with pytest.raises(demo_mode.DemoSQLError):
        demo_mode.prepare_sql(sql)


def test_published_query_is_canonical_and_limited():
    canonical = demo_mode.prepare_sql(demo_mode.SAMPLE_SQL)
    assert "public_demo.orders" in canonical
    assert canonical in demo_mode.statements_from_publication(demo_mode.sample_html())
    limited = demo_mode.with_limit(canonical, 500)
    assert "LIMIT 500" in limited
    assert limited.lower().count("select") >= 2
    migration = MIGRATION.read_text()
    assert "CREATE ROLE calliope_demo_guest NOLOGIN" in migration
    assert "REVOKE ALL ON SCHEMA rvbbit FROM calliope_demo_guest" in migration
    assert "GRANT USAGE ON SCHEMA rvbbit TO calliope_demo_guest" not in migration
    assert "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rvbbit TO calliope_demo_guest" not in migration
    registry = (ROOT / "crates/pg_rvbbit/src/migrations.rs").read_text()
    assert "0509_calliope_demo_guest" in registry
    assert "0510_calliope_demo_login" in registry
    seed = (ROOT / "deploy/demo/seed_public_dashboard.sql").read_text()
    assert canonical in seed


def test_provenance_scrub_drops_secrets_and_paths():
    clean = demo_mode.scrub_provenance({
        "thread_id": "not-a-uuid",
        "models": ["openai/gpt-test", "/opt/calliope/secret", "sk-secret-key"],
        "token_cost": 0.125,
        "token_count": 40,
        "wall_ms": 1500,
        "steps": [
            {"tool": "run_sql", "count": 3},
            {"tool": "hindsight.recall", "count": 1},
            {"tool": "cat /etc/passwd", "count": 1},
            {"tool": "get_secret", "count": 1},
        ],
        "prompt": "ignore",
    })
    assert clean["thread_id"] is None
    assert clean["models"] == ["openai/gpt-test"]
    assert clean["steps"] == [
        {"tool": "run_sql", "count": 3},
        {"tool": "hindsight.recall", "count": 1},
    ]
    assert "prompt" not in clean
    metrics = demo_mode.scrub_metric_index({
        "metrics": [{"name": "revenue", "definition_sql": "SELECT secret", "snapshot": {"value": 3}}],
    })
    assert metrics["metrics"][0]["name"] == "revenue"
    assert "definition_sql" not in metrics["metrics"][0]


def test_guest_cannot_run_arbitrary_sql_and_can_run_the_published_one(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    monkeypatch.setattr(server, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(demo_mode, "artifact_public", lambda conn, slug: slug == "public-demo-revenue")
    allowed = {"ok": False}
    monkeypatch.setattr(demo_mode, "on_allowlist", lambda conn, slug, version, canonical: allowed["ok"])
    calls = []

    def run_sql(*args, **kwargs):
        calls.append((args, kwargs))
        return {"columns": [{"name": "region"}], "rows": [["North"]], "row_count": 1, "truncated": False}

    monkeypatch.setattr(server, "tool_run_sql", run_sql)
    session = {
        "sub": demo_mode.ROLE,
        "identity": demo_mode.GUEST_EMAIL,
        "mapped": True,
        "via": "demo",
        "guest": True,
    }
    monkeypatch.setattr(auth, "read_session_full", lambda request: session)
    handler = routes["/api/d/{slug}/q"]

    denied = asyncio.run(handler(_request(
        "POST", "/api/d/public-demo-revenue/q",
        {"sql": "SELECT * FROM rvbbit.secrets", "timeout_ms": 180000},
    )))
    assert denied.status_code == 403
    assert json.loads(denied.body)["error"]["code"] in {"SCHEMA_DENIED", "FUNCTION_DENIED", "QUERY_NOT_PUBLISHED"}
    assert calls == []

    unpublished = asyncio.run(handler(_request(
        "POST", "/api/d/public-demo-revenue/q",
        {"sql": "SELECT 1", "timeout_ms": 180000},
    )))
    assert unpublished.status_code == 403
    assert json.loads(unpublished.body)["error"]["code"] == "QUERY_NOT_PUBLISHED"
    assert calls == []

    allowed["ok"] = True
    ok = asyncio.run(handler(_request(
        "POST", "/api/d/public-demo-revenue/q",
        {"sql": demo_mode.SAMPLE_SQL, "timeout_ms": 180000, "version": 1},
    )))
    assert ok.status_code == 200
    args, kwargs = calls[0]
    assert kwargs["_guest"] is True
    assert kwargs.get("_role") in (None, demo_mode.ROLE)
    assert kwargs["_statement_timeout_ms"] == demo_mode.timeout_ms()
    assert kwargs["timeout_ms"] is None
    assert "LIMIT 500" in args[0]
    assert "rvbbit" not in args[0].lower()
    assert args[1] is None  # client as_of is ignored
    body = json.loads(ok.body)
    assert body["bridge_cache"]["status"] == "miss"
    assert body["bridge_cache"]["ttl_seconds"] == demo_mode.guest_cache_ttl()

    again = asyncio.run(handler(_request(
        "POST", "/api/d/public-demo-revenue/q",
        {"sql": demo_mode.SAMPLE_SQL, "timeout_ms": 1, "cache": "no-store", "version": 1},
    )))
    assert again.status_code == 200
    assert len(calls) == 1
    assert json.loads(again.body)["bridge_cache"]["status"] == "hit"
    same = demo_mode.guest_cache_key("public-demo-revenue", 1, demo_mode.prepare_sql(demo_mode.SAMPLE_SQL))
    other_viewer = demo_mode.guest_cache_key("public-demo-revenue", 1, demo_mode.prepare_sql(demo_mode.SAMPLE_SQL))
    assert same == other_viewer
    assert same.startswith("guest:")


def test_guest_actions_and_private_routes_are_denied(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    monkeypatch.setattr(demo_mode, "artifact_public", lambda conn, slug: True)
    monkeypatch.setattr(auth, "read_session_full", lambda request: {
        "sub": demo_mode.ROLE, "identity": demo_mode.GUEST_EMAIL,
        "mapped": True, "via": "demo", "guest": True,
    })
    action = asyncio.run(routes["/api/d/{slug}/actions/{action}"](_request(
        "POST", "/api/d/public-demo-revenue/actions/save", {"inputs": {}},
    )))
    assert action.status_code == 403
    detail = asyncio.run(routes["/api/gallery/metrics/{name}"](_request("GET", "/api/gallery/metrics/revenue")))
    assert detail.status_code == 403


def test_signed_in_query_is_unchanged_when_demo_mode_is_off(monkeypatch):
    monkeypatch.delenv("CALLIOPE_DEMO_MODE", raising=False)
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    monkeypatch.setattr(server, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.artifact_access, "can_view", lambda *args, **kwargs: True)
    calls = []

    def run_sql(*args, **kwargs):
        calls.append(kwargs)
        return {"columns": [], "rows": [[1]], "row_count": 1, "truncated": False}

    monkeypatch.setattr(server, "tool_run_sql", run_sql)
    monkeypatch.setattr(auth, "read_session_full", lambda request: {
        "sub": "ada@example.com", "identity": "ada@example.com", "mapped": True, "via": "password",
    })
    response = asyncio.run(routes["/api/d/{slug}/q"](_request(
        "POST", "/api/d/tasks/q", {"sql": "SELECT 1", "cache": "no-store"},
    )))
    assert response.status_code == 200
    assert calls[0].get("_guest") is False
    assert calls[0].get("_role") is None
    assert calls[0]["_statement_timeout_ms"] is None


def test_guest_middleware_blocks_denied_routes_only_while_enabled(monkeypatch):
    cookie = None

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = demo_mode.guest_middleware(app)

    async def call(path, method="GET"):
        messages = []

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"cookie", cookie.encode())] if cookie else [],
            "client": ("203.0.113.10", 9),
            "server": ("app.test", 80),
        }
        await wrapped(scope, None, send)
        return messages[0]["status"]

    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    cookie = _guest_cookie()
    assert asyncio.run(call("/floor")) == 403
    assert asyncio.run(call("/calliope")) == 403
    assert asyncio.run(call("/mcp", "POST")) == 403
    assert asyncio.run(call("/gallery")) == 204
    assert asyncio.run(call("/api/d/public-demo-revenue/q", "POST")) == 204
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "0")
    assert asyncio.run(call("/floor")) == 204


def test_demo_rate_limit_applies_only_when_enabled(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setenv("CALLIOPE_DEMO_QUERY_RATE", "2")
    hits = {"n": 0}

    async def app(scope, receive, send):
        hits["n"] += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = demo_mode.guest_middleware(app)

    async def post(ip):
        messages = []

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/d/public-demo-revenue/q",
            "raw_path": b"/api/d/public-demo-revenue/q", "query_string": b"",
            "headers": [], "client": (ip, 9), "server": ("app.test", 80),
        }
        await wrapped(scope, None, send)
        return messages[0]["status"]

    assert asyncio.run(post("203.0.113.20")) == 204
    assert asyncio.run(post("203.0.113.20")) == 204
    assert asyncio.run(post("203.0.113.20")) == 429
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "0")
    assert asyncio.run(post("203.0.113.20")) == 204


def test_signed_in_queries_are_not_in_the_guest_bucket(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setenv("CALLIOPE_DEMO_QUERY_RATE", "1")
    auth.JWT_SECRET = "demo-test-secret-signed-in-user!!"
    response = Response()
    auth.set_session(response, "ada@example.com", False, identity="ada@example.com", mapped=True)
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    hits = {"n": 0}

    async def app(scope, receive, send):
        hits["n"] += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = demo_mode.guest_middleware(app)

    async def post():
        messages = []

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/d/public-demo-revenue/q",
            "raw_path": b"/api/d/public-demo-revenue/q", "query_string": b"",
            "headers": [(b"cookie", cookie.encode())],
            "client": ("203.0.113.77", 9), "server": ("app.test", 80),
        }
        await wrapped(scope, None, send)
        return messages[0]["status"]

    assert asyncio.run(post()) == 204
    assert asyncio.run(post()) == 204
    assert hits["n"] == 2


def test_publish_to_demo_requires_same_origin_json(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    request = Request({
        "type": "http", "method": "POST", "path": "/api/gallery/artifacts/x/demo",
        "scheme": "http", "server": ("app.test", 80),
        "headers": [
            (b"content-type", b"text/plain"),
            (b"origin", b"https://evil.test"),
        ],
        "path_params": {"slug": "x"},
    }, None)
    response = asyncio.run(routes["/api/gallery/artifacts/{slug}/demo"](request))
    assert response.status_code == 403


def test_cookieless_public_pages_do_not_bounce():
    os.environ["CALLIOPE_DEMO_MODE"] = "1"
    try:
        assert demo_mode.anonymous_guest_page("/d/public-demo-revenue") is True
        assert demo_mode.anonymous_guest_page("/gallery") is True
        assert demo_mode.anonymous_guest_page("/floor") is False
        assert demo_mode.signed_in_front_door("/") == "/floor"
        assert demo_mode.signed_in_front_door("/gallery") is None
    finally:
        os.environ.pop("CALLIOPE_DEMO_MODE", None)


def test_widget_has_a_build_panel_beside_the_data_overlay():
    script = (ROOT / "services/calliope/theme/artifact-lens.js").read_text()
    style = (ROOT / "services/calliope/theme/artifact-lens.css").read_text()
    assert 'data-view="built"' in script
    assert "How this was built" in script
    assert "/provenance" in script
    assert "dashboard.guest || !dashboard.calliope_enabled" in script
    assert ".built-view" in style
    caddy = (ROOT / "docker/origin/Caddyfile.calliope").read_text()
    assert "redir * {$CALLIOPE_ROOT_REDIRECT:/floor} 302" in caddy
    assert "rate_limit {" not in caddy


class _ProvenanceConn:
    def __init__(self):
        self.rows = {}
        self.provenance_writes = []

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        if "INSERT INTO rvbbit.demo_build_provenance" in sql:
            self.provenance_writes.append(params)
            import json as _json
            self.rows[(params[0], params[1])] = {
                "thread_id": params[2],
                "models": _json.loads(params[3]),
                "token_cost": params[4],
                "token_count": params[5],
                "wall_ms": params[6],
                "steps": _json.loads(params[7]),
            }
        return self

    def fetchall(self):
        if "demo_build_provenance" in getattr(self, "sql", "") and self.params:
            row = self.rows.get((self.params[0], self.params[1]))
            return [row] if row else []
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


def test_toggle_keeps_existing_provenance_and_does_not_copy_the_previous_version():
    conn = _ProvenanceConn()
    conn.rows[(7, 1)] = {
        "thread_id": None,
        "models": ["fixture"],
        "token_cost": 0,
        "token_count": 0,
        "wall_ms": 0,
        "steps": [{"tool": "publish_dashboard", "count": 1}],
    }
    demo_mode.record_version(conn, 7, 1, demo_mode.sample_html(), None, None)
    assert conn.provenance_writes == []
    assert conn.rows[(7, 1)]["models"] == ["fixture"]
    demo_mode.record_version(conn, 7, 2, demo_mode.sample_html(), None, None)
    assert conn.rows[(7, 1)]["models"] == ["fixture"]
    assert conn.rows[(7, 2)]["models"] == []
    assert conn.rows[(7, 2)]["steps"] == []


def test_seed_updates_the_existing_version(monkeypatch):
    class Conn:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            self.sql = sql
            return self

        def fetchone(self):
            if "FROM rvbbit.dashboards WHERE slug" in self.sql:
                return {"id": 4, "latest_version": 2}
            return None

    monkeypatch.setattr(demo_mode, "configure_guest", lambda *args, **kwargs: "public_demo")
    monkeypatch.setattr(demo_mode, "ensure_tables", lambda conn: None)
    monkeypatch.setattr(demo_mode, "record_version", lambda *args, **kwargs: {"recorded": 1})
    monkeypatch.setattr(demo_mode, "ensure_card", lambda *args, **kwargs: None)
    conn = Conn()
    result = demo_mode.seed_sample(conn)
    assert result["version"] == 2
    assert not any("INSERT INTO rvbbit.dashboard_versions" in sql for sql in conn.statements)
    assert any("UPDATE rvbbit.dashboard_versions" in sql for sql in conn.statements)


def test_publish_rejects_a_foreign_referer_when_origin_is_absent():
    foreign = Request({
        "type": "http", "method": "POST", "path": "/api/gallery/artifacts/x/demo",
        "scheme": "http", "server": ("app.test", 80), "headers": [
            (b"content-type", b"application/json"),
            (b"referer", b"https://evil.test/gallery"),
        ],
    }, None)
    assert demo_mode.same_origin_json(foreign) is False
    same = Request({
        "type": "http", "method": "POST", "path": "/api/gallery/artifacts/x/demo",
        "scheme": "http", "server": ("app.test", 80), "headers": [
            (b"content-type", b"application/json"),
            (b"referer", b"http://app.test/gallery"),
        ],
    }, None)
    assert demo_mode.same_origin_json(same) is True


def test_signed_in_demo_entry_does_not_replace_the_session(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    auth.JWT_SECRET = "demo-test-secret-signed-in-user!!"
    issued = Response()
    auth.set_session(issued, "ada@example.com", False, identity="ada@example.com", mapped=True)
    cookie = issued.headers["set-cookie"].split(";", 1)[0]
    request = _request("GET", "/demo", cookie=cookie)
    response = asyncio.run(routes["/demo"](request))
    assert response.status_code == 302
    assert response.headers["location"] == "/gallery"
    assert "set-cookie" not in {name.lower() for name in response.headers}


def test_guest_shim_disables_calliope_before_the_lens_runs():
    html = server._dash_shim("public-demo-revenue", guest=True)
    assert "RVBBIT_DASHBOARD.guest=true" in html
    assert "RVBBIT_DASHBOARD.calliope_enabled=false" in html
    assert html.index("calliope_enabled=false") < html.index('src="/theme/artifact-lens.js"')


def test_guest_gallery_has_site_share_tags(monkeypatch):
    monkeypatch.setenv("CALLIOPE_DEMO_SITE_NAME", "Dataslop")
    monkeypatch.setenv("CALLIOPE_DEMO_SITE_DESCRIPTION", "Sample dashboards")
    monkeypatch.setenv("WAREHOUSE_PUBLIC_URL", "https://dataslop.test")
    html = server._landing_html([], "guest@calliope.local", {"guest": True}, guest=True)
    assert "<title>Dataslop</title>" in html
    assert 'property="og:title" content="Dataslop"' in html
    assert 'property="og:description" content="Sample dashboards"' in html
    assert 'content="https://dataslop.test/thumbs/demo/site.png"' in html
    assert 'name="twitter:card"' in html


def test_public_dashboard_card_is_served_without_a_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "_live_app_capture_root", lambda: tmp_path)
    monkeypatch.setattr(server, "_conn", lambda: _Conn())
    routes = _routes()
    request = Request({
        "type": "http", "method": "GET",
        "path": "/thumbs/dashboard/public-demo-revenue.png",
        "scheme": "http", "server": ("app.test", 80), "headers": [],
        "path_params": {"kind": "dashboard", "slug": "public-demo-revenue"},
        "query_string": b"",
    }, None)
    response = asyncio.run(routes["/thumbs/{kind}/{slug}.png"](request))
    assert response.status_code == 200
    assert response.body.startswith(b"\x89PNG")
    assert response.headers["cache-control"].startswith("public")
    site = Request({
        "type": "http", "method": "GET", "path": "/thumbs/demo/site.png",
        "scheme": "http", "server": ("app.test", 80), "headers": [],
        "path_params": {"kind": "demo", "slug": "site"}, "query_string": b"",
    }, None)
    site_response = asyncio.run(routes["/thumbs/{kind}/{slug}.png"](site))
    assert site_response.status_code == 200
    assert site_response.body.startswith(b"\x89PNG")
