#!/usr/bin/env python3
"""Idempotently provision the persistent computer secrets in an env file."""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import tempfile
from pathlib import Path


MANAGED = (
    "CALLIOPE_COMPUTER_ENABLED",
    "CALLIOPE_DESKTOP_CONTROL_KEY",
    "CALLIOPE_DESKTOP_VNC_PASSWORD",
    "CALLIOPE_DESKTOP_VNC_VIEW_PASSWORD",
    "CALLIOPE_DESKTOP_VNC_BASIC",
    "CALLIOPE_DESKTOP_VNC_VIEW_BASIC",
)


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def basic(username: str, password: str) -> str:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="/opt/rvbbit/.env")
    args = parser.parse_args()
    path = Path(args.env_file)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    current = parse_env(lines)
    control = current.get("CALLIOPE_DESKTOP_CONTROL_KEY") or secrets.token_urlsafe(48)
    password = current.get("CALLIOPE_DESKTOP_VNC_PASSWORD") or secrets.token_urlsafe(24)
    viewer = current.get("CALLIOPE_DESKTOP_VNC_VIEW_PASSWORD") or secrets.token_urlsafe(24)
    values = {
        "CALLIOPE_COMPUTER_ENABLED": "true",
        "CALLIOPE_DESKTOP_CONTROL_KEY": control,
        "CALLIOPE_DESKTOP_VNC_PASSWORD": password,
        "CALLIOPE_DESKTOP_VNC_VIEW_PASSWORD": viewer,
        "CALLIOPE_DESKTOP_VNC_BASIC": basic("kasm_user", password),
        # The pinned Kasm startup script creates the read-only `kasm_viewer`
        # account with VNC_PW (the `-r` flag supplies the capability boundary)
        # and currently ignores VNC_VIEW_ONLY_PW for web authentication.
        "CALLIOPE_DESKTOP_VNC_VIEW_BASIC": basic("kasm_viewer", password),
    }
    retained = [line for line in lines if line.split("=", 1)[0].strip() not in MANAGED]
    if retained and retained[-1].strip():
        retained.append("")
    retained.extend(["# Calliope Computer (managed by provision-computer-use-env.py)"])
    retained.extend(f"{key}={values[key]}" for key in MANAGED)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"computer use enabled in {path}; secrets preserved or generated (values not printed)")


if __name__ == "__main__":
    main()
