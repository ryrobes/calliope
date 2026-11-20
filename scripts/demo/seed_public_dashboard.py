#!/usr/bin/env python3
"""Seed one public demo dashboard without calling a model.

Requires the demo migration (calliope_demo_guest) and WAREHOUSE_DSN, which
defaults to the local bench database. The dashboard reads public_demo.orders.

    PYTHONPATH=services/calliope python3 scripts/demo/seed_public_dashboard.py

A psql-only copy of the same fixture is deploy/demo/seed_public_dashboard.sql.
"""
from __future__ import annotations

import os
import sys

import psycopg
from psycopg.rows import dict_row

import demo_mode


def main() -> int:
    dsn = os.environ.get("WAREHOUSE_DSN", "").strip()
    if not dsn:
        print(
            "WAREHOUSE_DSN is not set. On a running install:\n"
            "  docker compose exec -T -e PYTHONPATH=/app calliope python - "
            "< scripts/demo/seed_public_dashboard.py",
            file=sys.stderr,
        )
        return 2
    if not demo_mode.enabled():
        print("CALLIOPE_DEMO_MODE is off. The fixture still publishes the dashboard;", file=sys.stderr)
        print("set CALLIOPE_DEMO_MODE=1 before visitors can open it.", file=sys.stderr)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result = demo_mode.seed_sample(conn)
    print(f"Public demo dashboard: /d/{result['slug']} (version {result['version']})")
    print("It is marked public. Open / or /d/" + result["slug"] + " with no cookie.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except demo_mode.DemoSQLError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
