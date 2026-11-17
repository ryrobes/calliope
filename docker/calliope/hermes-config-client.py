#!/usr/bin/env python3
"""Validate or atomically apply a Hermes config through Calliope's supervisor."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw or str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--revision-id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8643")
    args = parser.parse_args()

    token = os.environ.get("API_SERVER_KEY") or os.environ.get("HERMES_CONTROL_KEY")
    if not token:
        print("API_SERVER_KEY or HERMES_CONTROL_KEY is required", file=sys.stderr)
        return 2
    if not args.candidate.is_file():
        print(f"candidate does not exist: {args.candidate}", file=sys.stderr)
        return 2

    candidate = args.candidate.read_text(encoding="utf-8")
    status, validation = _request(
        args.base_url,
        token,
        "POST",
        "/v1/validate",
        {"config_yaml": candidate},
    )
    if status >= 400 or not validation.get("valid"):
        print(json.dumps(validation, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    if args.validate_only:
        print(json.dumps(validation, indent=2, sort_keys=True))
        return 0

    status, current = _request(args.base_url, token, "GET", "/v1/config")
    if status >= 400 or not current.get("config_hash"):
        print(json.dumps(current, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    status, result = _request(
        args.base_url,
        token,
        "POST",
        "/v1/apply",
        {
            "config_yaml": candidate,
            "expected_hash": current["config_hash"],
            "revision_id": args.revision_id,
        },
    )
    stream = sys.stdout if status < 400 and result.get("ok") else sys.stderr
    print(json.dumps(result, indent=2, sort_keys=True), file=stream)
    return 0 if status < 400 and result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
