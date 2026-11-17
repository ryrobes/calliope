#!/usr/bin/env python3
"""Fetch the pinned dashboard runtime libraries into services/calliope/vendor/.

The manifest (services/calliope/vendor/manifest.json) is the contract: every
library, version, file, source URL, and sha256. This script downloads what is
missing or mismatched into vendor/<name>@<version>/<file> and verifies every
hash, so a Docker build (or a dev checkout) gets exactly the bytes the page
shim was written against. Nothing is written into git; the image carries it.

Usage:
  scripts/vendor-dashboard-assets.py            # fetch + verify
  scripts/vendor-dashboard-assets.py --check    # verify only (CI / tests), exit 1 on any gap
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "services" / "calliope" / "vendor"
MANIFEST = VENDOR / "manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; do not download")
    ap.add_argument("--vendor-dir", default=str(VENDOR))
    args = ap.parse_args()
    vendor = Path(args.vendor_dir)
    manifest_path = vendor / "manifest.json" if (vendor / "manifest.json").is_file() else MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    fetched = verified = 0
    for name, lib in sorted(manifest["libraries"].items()):
        version = lib["version"]
        for fname, spec in sorted(lib["files"].items()):
            target = vendor / f"{name}@{version}" / fname
            if target.is_file() and _sha256(target) == spec["sha256"]:
                verified += 1
                continue
            if args.check:
                problems.append(f"missing or mismatched: {target.relative_to(vendor)}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(spec["url"], headers={"User-Agent": "rvbbit-vendor/1"})
            data = urllib.request.urlopen(req, timeout=180).read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != spec["sha256"]:
                problems.append(f"sha256 mismatch for {name}@{version}/{fname}: got {digest}")
                continue
            target.write_bytes(data)
            fetched += 1
            print(f"fetched {name}@{version}/{fname} ({len(data):,} bytes)")
    print(f"vendor assets: {verified} verified, {fetched} fetched, {len(problems)} problems")
    for p in problems:
        print("  ! " + p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
