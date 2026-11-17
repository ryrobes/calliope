#!/usr/bin/env python3
"""Bump Calliope product packages without changing the RVBBIT engine."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_FILES = (
    ROOT / "datarabbit" / "package.json",
    ROOT / "datarabbit" / "package-lock.json",
    ROOT / "datarabbit" / "electron" / "package.json",
    ROOT / "services" / "calliope" / "calliope-editor" / "package.json",
    ROOT / "services" / "calliope" / "calliope-editor" / "package-lock.json",
    ROOT / "services" / "calliope" / "calliope-sketch" / "package.json",
    ROOT / "services" / "calliope" / "calliope-sketch" / "package-lock.json",
    ROOT / "services" / "calliope" / "charts-runtime" / "package.json",
    ROOT / "services" / "calliope" / "charts-runtime" / "package-lock.json",
    ROOT / "services" / "calliope" / "deck-runtime" / "package.json",
    ROOT / "services" / "calliope" / "deck-runtime" / "package-lock.json",
)


def clean_version(raw: str) -> str:
    version = raw.strip().removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?", version):
        raise SystemExit(f"invalid Calliope semver: {raw!r}")
    return version


def update_package(path: Path, version: str) -> None:
    data = json.loads(path.read_text())
    data["version"] = version
    packages = data.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        packages[""]["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"updated {path.relative_to(ROOT)}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: bump-calliope-version.py VERSION")
    version = clean_version(sys.argv[1])

    (ROOT / "VERSION").write_text(version + "\n")
    print("updated VERSION")

    manifest_path = ROOT / "release" / "calliope-release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["calliope_version"] = version
    # Reuse is an explicit decision for a particular release, not inherited.
    manifest.pop("reused_images", None)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print("updated release/calliope-release.json")

    for path in PACKAGE_FILES:
        update_package(path, version)

    for relative in ("docker/docker-compose.calliope.yml", "docker/docker-compose.computer-base.yml", "deploy/compose.yaml"):
        compose_path = ROOT / relative
        if not compose_path.exists():
            continue
        updated, count = re.subn(r"CALLIOPE_VERSION:-[^}]+", f"CALLIOPE_VERSION:-{version}", compose_path.read_text())
        if not count:
            raise SystemExit(f"{relative} has no default release version")
        compose_path.write_text(updated)
        print(f"updated {relative} ({count} image references)")


if __name__ == "__main__":
    main()
