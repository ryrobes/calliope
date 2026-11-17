#!/usr/bin/env bash
# Compatibility entry point. release/services.json is the single build inventory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
services=()
push=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) export CALLIOPE_VERSION="$2"; shift 2 ;;
    --registry) export CALLIOPE_REGISTRY="$2"; shift 2 ;;
    --service) services+=("$2"); shift 2 ;;
    --push) push=(--push); shift ;;
    -h|--help)
      echo 'Usage: build-calliope.sh [--version VERSION] [--registry PREFIX] [--service SERVICE] [--push]'
      echo 'Without --service, build every shipping image including the core Warren runtimes.'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
python3 "$ROOT/scripts/release/shipping.py" check
if [[ ${#services[@]} -eq 0 ]]; then
  mapfile -t services < <(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))))' "$ROOT/release/services.json")
  services+=(runtimes)
fi
for service in "${services[@]}"; do
  if [[ "$service" == runtimes ]]; then
    python3 "$ROOT/scripts/release/shipping.py" build-runtimes "${push[@]}"
  else
    python3 "$ROOT/scripts/release/shipping.py" build --service "$service" "${push[@]}"
  fi
done
