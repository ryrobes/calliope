#!/usr/bin/env bash
set -euo pipefail
desk_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$desk_dir/web/dist/index.html" ]]; then
  (cd "$desk_dir/web" && npm ci --ignore-scripts && npm run build)
fi
if curl --silent --fail http://127.0.0.1:18455/api/health | python3 -c 'import json,sys; assert json.load(sys.stdin)["application"]=="calliope-transaction-desk"' 2>/dev/null; then
  echo 'Transaction desk is already running: http://127.0.0.1:18455/'
else
  exec python3 "$desk_dir/server.py"
fi
