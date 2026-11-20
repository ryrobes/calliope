"""Opt-in public demo mode.

Off unless ``CALLIOPE_DEMO_MODE`` is ``1``, ``true``, ``yes``, or ``on``.
Nothing here changes an install that leaves the flag unset. When it is on,
anonymous visitors receive a marked guest session. Dashboard reads run as the
``calliope_demo_guest`` role and only if they match a statement recorded for
that published version.

The stock ``caddy:2-alpine`` image does not include a rate-limit module, so
the limit for ``/api/d/*/q``, ``/login``, and ``/demo`` lives in this process.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Any

ROLE = "calliope_demo_guest"
GUEST_EMAIL = "guest@demo.calliope"
GUEST_NAME = "Guest"

_TRUE = {"1", "true", "yes", "on"}
_SYSTEM_SCHEMAS = {
    "rvbbit",
    "rvbbit_meta",
    "hindsight",
    "information_schema",
    "pg_catalog",
    "pg_toast",
    "public",
}
_DENIED_FUNCTIONS = {
    "classify",
    "means",
    "mcp_call",
    "synth_sql",
    "get_secret",
    "set_secret",
    "list_secrets",
    "delete_secret",
    "credential_unseal",
    "credential_seal",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "copy",
    "query_to_xml",
}
_ALLOWED_FUNCTIONS = {
    "sum",
    "count",
    "avg",
    "min",
    "max",
    "coalesce",
    "nullif",
    "abs",
    "round",
    "ceil",
    "ceiling",
    "floor",
    "trunc",
    "cast",
    "date_trunc",
    "date_bin",
    "to_char",
    "to_date",
    "to_timestamp",
    "extract",
    "date_part",
    "lower",
    "upper",
    "btrim",
    "ltrim",
    "rtrim",
    "length",
    "substring",
    "substr",
    "replace",
    "concat",
    "concat_ws",
    "greatest",
    "least",
    "current_timestamp",
    "current_date",
    "current_time",
    "localtimestamp",
    "now",
    "clock_timestamp",
    "statement_timestamp",
    "transaction_timestamp",
    "generate_series",
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "first_value",
    "last_value",
    "nth_value",
    "ntile",
    "string_agg",
    "array_agg",
    "json_agg",
    "jsonb_agg",
    "json_build_object",
    "jsonb_build_object",
    "json_build_array",
    "jsonb_build_array",
    "bool_and",
    "bool_or",
    "width_bucket",
    "mod",
    "power",
    "sqrt",
    "log",
    "ln",
    "exp",
    "sign",
    "left",
    "right",
    "split_part",
    "regexp_replace",
    "age",
    "make_date",
    "make_timestamp",
    "percentile_cont",
    "percentile_disc",
    "unnest",
}
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MODEL = re.compile(r"[A-Za-z0-9_.:@+-]+(?:/[A-Za-z0-9_.:@+-]+)?")
_TOOL = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_SECRET = re.compile(r"(secret|password|token|api[_-]?key|authorization|bearer|prompt)", re.I)
_QUERY_CALL = re.compile(
    r"""rvbbitQuery\s*\(\s*(?P<lit>'(?:\\'|[^'])*'|"(?:\\"|[^"])*"|`(?:\\`|[^`])*`)""",
    re.I | re.S,
)
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

SAMPLE_SQL = (
    "SELECT region, SUM(amount) AS revenue FROM public_demo.orders "
    "GROUP BY region ORDER BY revenue DESC"
)
SAMPLE_SLUG = "public-demo-revenue"

TABLE_DDL = """
ALTER TABLE rvbbit.dashboards
    ADD COLUMN IF NOT EXISTS demo_public boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS rvbbit.demo_query_allowlist (
    dashboard_id bigint NOT NULL,
    version integer NOT NULL,
    sql_hash text NOT NULL,
    canonical_sql text NOT NULL,
    PRIMARY KEY (dashboard_id, version, sql_hash)
);
CREATE TABLE IF NOT EXISTS rvbbit.demo_build_provenance (
    dashboard_id bigint NOT NULL,
    version integer NOT NULL,
    thread_id text,
    models jsonb NOT NULL DEFAULT '[]'::jsonb,
    token_cost numeric,
    token_count bigint,
    wall_ms bigint,
    steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dashboard_id, version)
);
"""


class DemoSQLError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def enabled() -> bool:
    return os.environ.get("CALLIOPE_DEMO_MODE", "").strip().lower() in _TRUE


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def row_limit() -> int:
    return _bounded_int("CALLIOPE_DEMO_ROW_LIMIT", 500, 1, 2_000)


def timeout_ms() -> int:
    return _bounded_int("CALLIOPE_DEMO_QUERY_TIMEOUT_MS", 5_000, 1_000, 15_000)


def schemas() -> tuple[str, ...]:
    raw = os.environ.get("CALLIOPE_DEMO_SCHEMAS", "public_demo")
    names = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name or not _SCHEMA.fullmatch(name):
            continue
        if name in _SYSTEM_SCHEMAS or name.startswith("pg_"):
            continue
        if name not in names:
            names.append(name)
    return tuple(names or ("public_demo",))


def is_guest(session: dict | None) -> bool:
    return bool(session and session.get("guest") is True and enabled())


def route_allowed(path: str, method: str) -> bool:
    """Guest HTTP allowlist. Everything else is denied."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or ".." in path:
        return False
    path = path.split("?", 1)[0].rstrip("/") or "/"
    method = (method or "GET").upper()
    if method in {"GET", "HEAD"} and (
        path in {"/", "/gallery", "/demo", "/favicon.ico", "/icon"}
        or path.startswith("/theme/")
        or path.startswith("/charts/")
        or path.startswith("/vendor/")
        or path.startswith("/bg/")
    ):
        return True
    slug = _SLUG.pattern
    if method in {"GET", "HEAD"} and re.fullmatch(rf"/d/{slug}", path):
        return True
    if method in {"GET", "HEAD"} and re.fullmatch(rf"/d/{slug}/versions/[1-9][0-9]{{0,8}}", path):
        return True
    if method == "GET" and re.fullmatch(rf"/api/d/{slug}/provenance", path):
        return True
    if method == "GET" and path == "/api/gallery/metrics":
        return True
    if method == "GET" and re.fullmatch(rf"/thumbs/dashboard/{slug}\.png", path):
        return True
    if method == "POST" and re.fullmatch(rf"/api/d/{slug}/q", path):
        return True
    return False


def _client_ip(scope: dict) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or []) if isinstance(k, bytes)}
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:80] or "?"
    client = scope.get("client") or ("?", 0)
    return str(client[0] if isinstance(client, tuple) else client)[:80]


class _Limiter:
    def __init__(self):
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 20_000:
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < window}
            return True


LIMITER = _Limiter()


def _rate_bucket(path: str, method: str) -> tuple[str, int] | None:
    path = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    method = (method or "GET").upper()
    if method == "POST" and re.fullmatch(rf"/api/d/{_SLUG.pattern}/q", path):
        return "query", _bounded_int("CALLIOPE_DEMO_QUERY_RATE", 30, 1, 600)
    if method == "POST" and path == "/login":
        return "login", _bounded_int("CALLIOPE_DEMO_LOGIN_RATE", 10, 1, 120)
    if method == "GET" and path == "/demo":
        return "enter", _bounded_int("CALLIOPE_DEMO_ENTER_RATE", 20, 1, 120)
    return None


def guest_middleware(app):
    """Deny a guest session outside the allowlist, and rate-limit demo traffic."""

    async def application(scope, receive, send):
        if scope.get("type") != "http" or not enabled():
            await app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        method = scope.get("method") or "GET"
        bucket = _rate_bucket(path, method)
        if bucket and not LIMITER.allow(f"{bucket[0]}:{_client_ip(scope)}", bucket[1], 60):
            await _send_json(send, 429, {
                "error": {"code": "RATE_LIMITED", "message": "Too many requests. Wait a minute and try again."},
            })
            return
        session = None
        try:
            from starlette.requests import Request
            import auth
            session = auth.read_session_full(Request(scope))
        except Exception:
            session = None
        if session and session.get("guest") and not route_allowed(path, method):
            await _send_json(send, 403, {
                "error": {"code": "FORBIDDEN", "message": "Demo guests cannot open that page."},
            })
            return
        await app(scope, receive, send)

    return application


async def _send_json(send, status: int, payload: dict) -> None:
    import json
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _function_name(node) -> str:
    import sqlglot.expressions as exp
    if isinstance(node, exp.Anonymous):
        return str(node.this or "").lower()
    return str(node.sql_name() or "").lower()


def _function_schema(node) -> str:
    import sqlglot.expressions as exp
    parent = getattr(node, "parent", None)
    if isinstance(parent, exp.Dot) and isinstance(parent.this, exp.Identifier):
        return str(parent.this.name or "").lower()
    return ""


def _cte_names(tree) -> set[str]:
    import sqlglot.expressions as exp
    names = set()
    for cte in tree.find_all(exp.CTE):
        alias = str(cte.alias or "").lower()
        if alias:
            names.add(alias)
    return names


def guard(tree) -> None:
    """Reject rvbbit objects, operator calls, and anything outside the demo schemas."""
    import sqlglot.expressions as exp
    if not isinstance(tree, exp.Query):
        raise DemoSQLError("NOT_SELECT", "Only a single read-only SELECT is allowed.")
    ctes = _cte_names(tree)
    allowed_schemas = set(schemas())
    for node in tree.walk():
        if isinstance(node, exp.Table):
            schema = str(node.db or "").lower()
            name = str(node.name or "").lower()
            if not schema:
                if name not in ctes:
                    raise DemoSQLError(
                        "UNQUALIFIED_TABLE",
                        "Demo queries must name a demo schema on every table.",
                    )
                continue
            if schema not in allowed_schemas:
                raise DemoSQLError("SCHEMA_DENIED", f"Schema {schema} is not available in the demo.")
        elif isinstance(node, exp.Func):
            name = _function_name(node)
            schema = _function_schema(node)
            if name in _DENIED_FUNCTIONS or schema in _SYSTEM_SCHEMAS or schema.startswith("pg_"):
                raise DemoSQLError("FUNCTION_DENIED", "That function is not available in the demo.")
            if schema and schema not in {"pg_catalog"}:
                raise DemoSQLError("FUNCTION_DENIED", "That function is not available in the demo.")
            if name and name not in _ALLOWED_FUNCTIONS:
                raise DemoSQLError("FUNCTION_DENIED", "That function is not available in the demo.")


def prepare_sql(sql: str) -> str:
    """Return the canonical statement, or raise DemoSQLError."""
    import sqlglot
    text = str(sql or "").strip()
    if not text or len(text) > 20_000 or "\x00" in text:
        raise DemoSQLError("BAD_SQL", "That query cannot be run.")
    try:
        trees = sqlglot.parse(text, read="postgres")
    except Exception as exc:
        raise DemoSQLError("BAD_SQL", "That query cannot be run.") from exc
    if len(trees) != 1 or trees[0] is None:
        raise DemoSQLError("NOT_SELECT", "Only a single read-only SELECT is allowed.")
    guard(trees[0])
    return trees[0].sql(dialect="postgres", normalize=True, comments=False)


def with_limit(canonical: str, limit: int | None = None) -> str:
    cap = row_limit() if limit is None else max(1, min(int(limit), row_limit()))
    body = canonical.strip().rstrip(";").strip()
    return f"SELECT * FROM (\n{body}\n) AS demo_guest_rows LIMIT {cap}"


def _unescape_js(literal: str) -> str:
    quote = literal[0]
    body = literal[1:-1]
    if quote == '"':
        body = body.replace('\\"', '"')
    elif quote == "'":
        body = body.replace("\\'", "'")
    else:
        body = body.replace("\\`", "`")
    return (
        body.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def _walk_sql(node, found: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"sql", "query_sql"} and isinstance(value, str):
                found.append(value)
            else:
                _walk_sql(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_sql(item, found)


def statements_from_publication(html: str | None, manifest: Any = None, captured: Any = None) -> list[str]:
    """Canonical statements worth remembering. Unsafe SQL is dropped, not stored."""
    found: list[str] = []
    for match in _QUERY_CALL.finditer(html or ""):
        found.append(_unescape_js(match.group("lit")))
    _walk_sql(manifest, found)
    for item in captured or []:
        if isinstance(item, dict) and isinstance(item.get("sql"), str) and not item.get("sql_truncated"):
            found.append(item["sql"])
    canonical = []
    seen = set()
    for statement in found:
        try:
            prepared = prepare_sql(statement)
        except DemoSQLError:
            continue
        if prepared not in seen:
            seen.add(prepared)
            canonical.append(prepared)
    return canonical


def _optional(conn, sql: str, params: tuple = ()):
    try:
        with conn.transaction():
            return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _scrub_model(value) -> str | None:
    name = str(value or "").strip()[:80]
    if not name or not _MODEL.fullmatch(name) or _SECRET.search(name) or name.startswith(("/", ".")):
        return None
    return name


def _scrub_steps(steps) -> list[dict]:
    cleaned = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "").strip()[:80]
        if not _TOOL.fullmatch(tool) or _SECRET.search(tool) or "/" in tool or "\\" in tool:
            continue
        try:
            count = int(step.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        cleaned.append({"tool": tool, "count": max(0, min(count, 100_000))})
        if len(cleaned) >= 12:
            break
    return cleaned


def scrub_provenance(document: dict | None) -> dict:
    """Guest-safe build summary. No prompts, secrets, paths, or raw model payloads."""
    document = document or {}
    thread = str(document.get("thread_id") or "")
    models = []
    for name in document.get("models") or []:
        clean = _scrub_model(name)
        if clean and clean not in models:
            models.append(clean)
    cost = document.get("token_cost")
    try:
        cost = round(float(cost), 6) if cost is not None else None
        if cost is not None and cost < 0:
            cost = None
    except (TypeError, ValueError):
        cost = None
    try:
        tokens = int(document.get("token_count")) if document.get("token_count") is not None else None
        if tokens is not None and tokens < 0:
            tokens = None
    except (TypeError, ValueError):
        tokens = None
    try:
        wall = int(document.get("wall_ms")) if document.get("wall_ms") is not None else None
        if wall is not None and wall < 0:
            wall = None
    except (TypeError, ValueError):
        wall = None
    version = document.get("version")
    try:
        version = int(version) if version is not None else None
    except (TypeError, ValueError):
        version = None
    return {
        "version": version,
        "thread_id": thread if _UUID.fullmatch(thread) else None,
        "models": models[:8],
        "token_cost": cost,
        "token_count": tokens,
        "wall_ms": wall,
        "steps": _scrub_steps(document.get("steps")),
    }


def build_provenance(conn, session_id) -> dict:
    """Best-effort summary from the creation thread. Missing tables stay empty."""
    thread = str(session_id or "")
    models: list[str] = []
    cost = None
    tokens = None
    wall = None
    steps: list[dict] = []
    if _UUID.fullmatch(thread):
        usage = _optional(
            conn,
            "SELECT model, SUM(cost_usd) AS cost, "
            "SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)) AS tokens "
            "FROM rvbbit.model_call_usage WHERE session_id=%s GROUP BY model",
            (thread,),
        )
        total_cost = 0.0
        total_tokens = 0
        saw_cost = False
        for row in usage:
            model = _scrub_model(row.get("model") if isinstance(row, dict) else None)
            if model and model not in models:
                models.append(model)
            raw_cost = row.get("cost") if isinstance(row, dict) else None
            raw_tokens = row.get("tokens") if isinstance(row, dict) else None
            if raw_cost is not None:
                saw_cost = True
                total_cost += float(raw_cost)
            if raw_tokens is not None:
                total_tokens += int(raw_tokens)
        if saw_cost:
            cost = total_cost
        if usage:
            tokens = total_tokens
        for row in _optional(
            conn,
            "SELECT tool_name, COUNT(*)::int AS n FROM rvbbit.calliope_surfaces "
            "WHERE session_id=%s::uuid GROUP BY tool_name ORDER BY n DESC LIMIT 12",
            (thread,),
        ):
            if isinstance(row, dict):
                steps.append({"tool": row.get("tool_name"), "count": row.get("n")})
        for row in _optional(
            conn,
            "SELECT e.event_type, COUNT(*)::int AS n "
            "FROM rvbbit.calliope_turn_events e "
            "JOIN rvbbit.calliope_turns t ON t.id=e.turn_id "
            "WHERE t.session_id=%s::uuid AND e.event_type ILIKE %s "
            "GROUP BY e.event_type ORDER BY n DESC LIMIT 4",
            (thread, "%hindsight%"),
        ):
            if isinstance(row, dict):
                steps.append({"tool": row.get("event_type"), "count": row.get("n")})
        span = _optional(
            conn,
            "SELECT EXTRACT(EPOCH FROM (MAX(COALESCE(completed_at, created_at)) - MIN(created_at))) * 1000 AS wall "
            "FROM rvbbit.calliope_turns WHERE session_id=%s::uuid",
            (thread,),
        )
        if span and isinstance(span[0], dict) and span[0].get("wall") is not None:
            wall = int(span[0]["wall"])
    return scrub_provenance({
        "thread_id": thread,
        "models": models,
        "token_cost": cost,
        "token_count": tokens,
        "wall_ms": wall,
        "steps": steps,
    })


def ensure_tables(conn) -> None:
    conn.execute(TABLE_DDL)


def record_version(conn, dashboard_id, version, html, manifest=None, session_id=None, captured=None, *, _retry=True) -> dict:
    """Remember the version's runnable statements and a scrubbed build summary.

    A failure here must not discard the publication, so the work sits in a
    savepoint. Tables are created if this server is ahead of the extension.
    """
    version = int(version)
    statements = statements_from_publication(html, manifest, captured)
    try:
        with conn.transaction():
            try:
                provenance = build_provenance(conn, session_id) if session_id else None
            except Exception:
                provenance = None
            if provenance is None:
                previous = _optional(
                    conn,
                    "SELECT thread_id, models, token_cost, token_count, wall_ms, steps "
                    "FROM rvbbit.demo_build_provenance WHERE dashboard_id=%s AND version=%s",
                    (dashboard_id, version - 1),
                )
                provenance = scrub_provenance(dict(previous[0]) if previous else {})
            provenance["version"] = version
            conn.execute(
                "DELETE FROM rvbbit.demo_query_allowlist WHERE dashboard_id=%s AND version=%s",
                (dashboard_id, version),
            )
            for statement in statements:
                conn.execute(
                    "INSERT INTO rvbbit.demo_query_allowlist "
                    "(dashboard_id, version, sql_hash, canonical_sql) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (dashboard_id, version, sql_hash) DO NOTHING",
                    (dashboard_id, version, hashlib.sha256(statement.encode()).hexdigest(), statement),
                )
            import json
            conn.execute(
                "INSERT INTO rvbbit.demo_build_provenance "
                "(dashboard_id, version, thread_id, models, token_cost, token_count, wall_ms, steps) "
                "VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (dashboard_id, version) DO UPDATE SET "
                "thread_id=EXCLUDED.thread_id, models=EXCLUDED.models, token_cost=EXCLUDED.token_cost, "
                "token_count=EXCLUDED.token_count, wall_ms=EXCLUDED.wall_ms, steps=EXCLUDED.steps, "
                "recorded_at=now()",
                (
                    dashboard_id, version, provenance.get("thread_id"),
                    json.dumps(provenance.get("models") or []),
                    provenance.get("token_cost"), provenance.get("token_count"),
                    provenance.get("wall_ms"), json.dumps(provenance.get("steps") or []),
                ),
            )
    except Exception:
        if not _retry:
            return {"recorded": 0, "error": "unavailable"}
        try:
            with conn.transaction():
                ensure_tables(conn)
        except Exception:
            return {"recorded": 0, "error": "unavailable"}
        return record_version(
            conn, dashboard_id, version, html, manifest, session_id, captured, _retry=False,
        )
    return {"recorded": len(statements), "provenance": scrub_provenance(provenance)}


def artifact_public(conn, slug: str) -> bool:
    try:
        row = conn.execute(
            "SELECT demo_public, archived, app_kind FROM rvbbit.dashboards WHERE slug=%s",
            (slug,),
        ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    if isinstance(row, dict):
        public, archived, kind = row.get("demo_public"), row.get("archived"), row.get("app_kind")
    else:
        public, archived, kind = row[0], row[1], row[2]
    return bool(public) and not archived and str(kind or "dashboard").lower() == "dashboard"


def published_version(conn, slug: str, version) -> int | None:
    row = conn.execute(
        "SELECT latest_version FROM rvbbit.dashboards WHERE slug=%s AND demo_public AND NOT archived "
        "AND app_kind='dashboard'",
        (slug,),
    ).fetchone()
    if not row:
        return None
    latest = int(row.get("latest_version") if isinstance(row, dict) else row[0])
    if version in (None, ""):
        return latest
    try:
        selected = int(version)
    except (TypeError, ValueError):
        return None
    if selected < 1:
        return None
    return selected


def on_allowlist(conn, slug: str, version, canonical: str) -> bool:
    selected = published_version(conn, slug, version)
    if selected is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM rvbbit.demo_query_allowlist a "
        "JOIN rvbbit.dashboards d ON d.id=a.dashboard_id "
        "WHERE d.slug=%s AND a.version=%s AND a.canonical_sql=%s "
        "AND d.demo_public AND NOT d.archived AND d.app_kind='dashboard'",
        (slug, selected, canonical),
    ).fetchone()
    return bool(row)


def provenance_document(conn, slug: str, version) -> dict | None:
    selected = published_version(conn, slug, version)
    if selected is None:
        return None
    row = conn.execute(
        "SELECT p.version, p.thread_id, p.models, p.token_cost, p.token_count, p.wall_ms, p.steps "
        "FROM rvbbit.demo_build_provenance p "
        "JOIN rvbbit.dashboards d ON d.id=p.dashboard_id "
        "WHERE d.slug=%s AND p.version=%s AND d.demo_public AND NOT d.archived",
        (slug, selected),
    ).fetchone()
    if not row:
        return scrub_provenance({"version": selected})
    document = dict(row) if isinstance(row, dict) else {
        "version": row[0], "thread_id": row[1], "models": row[2], "token_cost": row[3],
        "token_count": row[4], "wall_ms": row[5], "steps": row[6],
    }
    return scrub_provenance(document)


def public_rows(conn) -> list:
    try:
        return conn.execute(
            "SELECT slug, name, description, owner_email, team, status, "
            "COALESCE(runtime_kind,'html') AS runtime_kind, "
            "COALESCE(app_kind,'dashboard') AS app_kind, latest_version, updated_at, "
            "false AS archived, false AS is_owner, NULL::int AS queries, NULL::int AS tables, "
            "NULL::int AS metrics, NULL::int AS semantic_objects "
            "FROM rvbbit.dashboards "
            "WHERE demo_public AND NOT archived AND app_kind='dashboard' "
            "ORDER BY updated_at DESC",
        ).fetchall()
    except Exception:
        return []


def configure_guest(conn, schema_names: list[str] | None = None) -> str:
    chosen = list(schema_names or schemas())
    row = conn.execute("SELECT to_regprocedure('rvbbit.configure_demo_guest(text[])') IS NOT NULL AS ok").fetchone()
    ready = bool(row and (row.get("ok") if isinstance(row, dict) else row[0]))
    if not ready:
        raise DemoSQLError(
            "MIGRATION_REQUIRED",
            "Apply crates/pg_rvbbit/sql/migrations/0509_calliope_demo_guest.sql before opening the demo.",
        )
    conn.execute("SELECT rvbbit.configure_demo_guest(%s::text[])", (chosen,))
    return ",".join(chosen)


def publish_to_demo(conn, slug: str, public: bool = True) -> dict:
    """Admin action: mark one artifact and backfill its saved-query allowlist."""
    ensure_tables(conn)
    row = conn.execute(
        "SELECT id, latest_version, app_kind FROM rvbbit.dashboards WHERE slug=%s",
        (slug,),
    ).fetchone()
    if not row:
        raise DemoSQLError("NOT_FOUND", "That artifact is not available.")
    dashboard_id = row.get("id") if isinstance(row, dict) else row[0]
    kind = row.get("app_kind") if isinstance(row, dict) else row[2]
    if str(kind or "dashboard").lower() != "dashboard":
        raise DemoSQLError("NOT_DASHBOARD", "Only a dashboard can be published to the demo.")
    versions = conn.execute(
        "SELECT version, html, manifest FROM rvbbit.dashboard_versions WHERE dashboard_id=%s ORDER BY version",
        (dashboard_id,),
    ).fetchall()
    recorded = 0
    for version_row in versions:
        version = version_row.get("version") if isinstance(version_row, dict) else version_row[0]
        html = version_row.get("html") if isinstance(version_row, dict) else version_row[1]
        manifest = version_row.get("manifest") if isinstance(version_row, dict) else version_row[2]
        captured = _optional(
            conn,
            "SELECT queries FROM rvbbit.artifact_query_captures WHERE dashboard_id=%s AND version=%s",
            (dashboard_id, version),
        )
        queries = []
        if captured:
            raw = captured[0].get("queries") if isinstance(captured[0], dict) else captured[0][0]
            if isinstance(raw, list):
                queries = raw
        result = record_version(conn, dashboard_id, version, html, manifest, captured=queries)
        recorded += int(result.get("recorded") or 0)
    conn.execute(
        "UPDATE rvbbit.dashboards SET demo_public=%s, updated_at=now() WHERE id=%s",
        (bool(public), dashboard_id),
    )
    configured = None
    if public:
        configured = configure_guest(conn)
    return {"slug": slug, "public": bool(public), "statements": recorded, "schemas": configured}


def scrub_metric_index(payload: dict) -> dict:
    """Drop anything that could carry definition SQL from the values index."""
    def walk(node):
        if isinstance(node, dict):
            return {
                key: walk(value)
                for key, value in node.items()
                if "sql" not in str(key).lower() and str(key).lower() not in {"prompt", "definition", "check"}
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node
    return walk(payload or {})


def sample_html() -> str:
    return (
        "<!doctype html><html><head><title>Regional revenue</title></head><body>"
        "<h1>Regional revenue</h1><pre id=chart></pre><script>"
        f"rvbbitQuery({SAMPLE_SQL!r}).then(function(result){{"
        "document.getElementById('chart').textContent=JSON.stringify(result.rows);});"
        "</script></body></html>"
    )


def seed_sample(conn) -> dict:
    """One public dashboard over a tiny public_demo.orders table. No model call."""
    ensure_tables(conn)
    configure_guest(conn, ["public_demo"])
    conn.execute("CREATE SCHEMA IF NOT EXISTS public_demo")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public_demo.orders ("
        "id int PRIMARY KEY, region text NOT NULL, amount numeric NOT NULL)"
    )
    conn.execute("TRUNCATE public_demo.orders")
    conn.execute(
        "INSERT INTO public_demo.orders (id, region, amount) VALUES "
        "(1,'North',120),(2,'North',80),(3,'South',50),(4,'East',200)"
    )
    configure_guest(conn, ["public_demo"])
    html = sample_html()
    import json
    provenance = scrub_provenance({
        "models": ["fixture"],
        "token_cost": 0,
        "token_count": 0,
        "wall_ms": 0,
        "steps": [{"tool": "publish_dashboard", "count": 1}],
    })
    row = conn.execute("SELECT id FROM rvbbit.dashboards WHERE slug=%s", (SAMPLE_SLUG,)).fetchone()
    if row:
        dashboard_id = row.get("id") if isinstance(row, dict) else row[0]
        current = conn.execute(
            "SELECT latest_version FROM rvbbit.dashboards WHERE id=%s", (dashboard_id,)
        ).fetchone()
        version = int(current.get("latest_version") if isinstance(current, dict) else current[0]) + 1
        conn.execute(
            "INSERT INTO rvbbit.dashboard_versions (dashboard_id, version, html, kind, created_by, notes) "
            "VALUES (%s,%s,%s,'live','demo-seed','public demo fixture')",
            (dashboard_id, version, html),
        )
        conn.execute(
            "UPDATE rvbbit.dashboards SET latest_version=%s, demo_public=true, archived=false, "
            "app_kind='dashboard', updated_at=now() WHERE id=%s",
            (version, dashboard_id),
        )
    else:
        version = 1
        created = conn.execute(
            "INSERT INTO rvbbit.dashboards "
            "(slug, name, description, owner_email, team, status, latest_version, app_kind, demo_public) "
            "VALUES (%s,%s,%s,'demo-seed@calliope.local',NULL,'live',1,'dashboard',true) RETURNING id",
            (SAMPLE_SLUG, "Regional revenue", "A small public demo dashboard. No model was called."),
        ).fetchone()
        dashboard_id = created.get("id") if isinstance(created, dict) else created[0]
        conn.execute(
            "INSERT INTO rvbbit.dashboard_versions (dashboard_id, version, html, kind, created_by, notes) "
            "VALUES (%s,1,%s,'live','demo-seed','public demo fixture')",
            (dashboard_id, html),
        )
    record_version(conn, dashboard_id, version, html, None, None)
    conn.execute(
        "INSERT INTO rvbbit.demo_build_provenance "
        "(dashboard_id, version, thread_id, models, token_cost, token_count, wall_ms, steps) "
        "VALUES (%s,%s,NULL,%s::jsonb,%s,%s,%s,%s::jsonb) "
        "ON CONFLICT (dashboard_id, version) DO UPDATE SET "
        "models=EXCLUDED.models, token_cost=EXCLUDED.token_cost, token_count=EXCLUDED.token_count, "
        "wall_ms=EXCLUDED.wall_ms, steps=EXCLUDED.steps, thread_id=NULL, recorded_at=now()",
        (
            dashboard_id, version, json.dumps(provenance["models"]), provenance["token_cost"],
            provenance["token_count"], provenance["wall_ms"], json.dumps(provenance["steps"]),
        ),
    )
    return {"slug": SAMPLE_SLUG, "version": version, "url": f"/d/{SAMPLE_SLUG}"}
