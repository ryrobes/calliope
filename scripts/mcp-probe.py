#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.12.4,<2", "httpx>=0.27"]
# ///
"""Smoke-probe a Calliope warehouse MCP endpoint over streamable HTTP.

Usage:
  MCP_TOKEN=... uv run mcp_probe.py [--url http://localhost:8080/mcp] [--list-only]
                                    [--call TOOL '{"json":"args"}']

Never prints the token. Exit code 0 = every step it attempted succeeded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _text(result) -> str:
    parts = []
    for c in getattr(result, "content", []) or []:
        t = getattr(c, "text", None)
        if t is not None:
            parts.append(t)
        else:
            parts.append(f"<{getattr(c, 'type', '?')}>")
    return "\n".join(parts)


def _short(s: str, n: int = 900) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"… [{len(s)} chars]"


async def call(session: ClientSession, name: str, args: dict, label: str | None = None):
    label = label or name
    t0 = time.perf_counter()
    try:
        res = await session.call_tool(name, args)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {label}: EXCEPTION {type(e).__name__}: {e}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    txt = _text(res)
    flag = "✗" if getattr(res, "isError", False) else "✓"
    print(f"  {flag} {label} ({ms:.0f} ms, isError={getattr(res, 'isError', False)})")
    print("    " + _short(txt).replace("\n", "\n    "))
    return res


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("MCP_URL", "http://localhost:8080/mcp"))
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--call", nargs=2, metavar=("TOOL", "JSON"), action="append", default=[])
    ap.add_argument("--sql", default="select 1 as one, now() as ts")
    ap.add_argument("--query", default="customers orders revenue")
    args = ap.parse_args()

    token = os.environ.get("MCP_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if not token:
        print("WARNING: MCP_TOKEN unset — expecting a 401 or an unauthenticated dev server")

    print(f"→ {args.url}")
    ok = True
    async with streamablehttp_client(args.url, headers=headers) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            t0 = time.perf_counter()
            init = await session.initialize()
            print(f"✓ initialize ({(time.perf_counter()-t0)*1000:.0f} ms) "
                  f"server={init.serverInfo.name} v{init.serverInfo.version} "
                  f"protocol={init.protocolVersion} session={get_session_id()}")
            instr = (init.instructions or "")
            print(f"  instructions: {len(instr)} chars; first line: {instr.splitlines()[0][:120] if instr else '-'}")

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"✓ tools/list → {len(names)} tools")
            print("  " + ", ".join(names))
            if args.list_only:
                return 0

            for name, js in args.call:
                r = await call(session, name, json.loads(js))
                ok &= bool(r) and not r.isError

            if args.call:
                return 0 if ok else 1

            have = set(names)
            print("\n— smoke sequence —")
            if "search_tools" in have:
                r = await call(session, "search_tools", {"query": "run a read-only sql query"})
                ok &= bool(r) and not r.isError
            if "list_metrics" in have:
                r = await call(session, "list_metrics", {})
                ok &= bool(r) and not r.isError
            table = None
            if "search_data" in have:
                r = await call(session, "search_data", {"query": args.query, "limit": 3})
                ok &= bool(r) and not r.isError
                if r and not r.isError:
                    try:
                        payload = json.loads(_text(r))
                        data = payload.get("data", payload)
                        matches = data.get("matches") or data.get("results") or []
                        # search_data rows are {"object": "schema.table[.col]", "kind": ...};
                        # ground the first *table* hit (business-context hits have no schema).
                        for m in matches:
                            if m.get("kind") == "db_table" and m.get("object"):
                                table = m["object"]
                                break
                            if m.get("table") or m.get("name"):
                                table = m.get("table") or m.get("name")
                                break
                    except Exception:  # noqa: BLE001
                        pass
            if table and "describe_table" in have:
                r = await call(session, "describe_table", {"table": table})
                ok &= bool(r) and not r.isError
            if "validate_sql" in have:
                r = await call(session, "validate_sql", {"sql": args.sql})
                ok &= bool(r) and not r.isError
                r = await call(session, "validate_sql", {"sql": "delete from pg_class"}, "validate_sql (should reject DML)")
            if "run_sql" in have:
                r = await call(session, "run_sql", {"sql": args.sql, "limit": 5})
                ok &= bool(r) and not r.isError
                if table:
                    r = await call(session, "run_sql", {"sql": f"select * from {table}", "limit": 3},
                                   f"run_sql on {table}")
                    ok &= bool(r) and not r.isError
                r = await call(session, "run_sql", {"sql": "select 1;", "limit": 1, "as_of": "2020-01-01T00:00:00Z"},
                               "run_sql with as_of (informational)")
    print("\nRESULT:", "OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
