#!/usr/bin/env bash
# Rebase managed backend URLs after each catalog install. Explicit opt-in only.
set -euo pipefail
base="${RVBBIT_CLOVER_BASE_URL:-}"
[[ -n "$base" ]] || exit 0
base="${base%/}"
if [[ -n "${RVBBIT_DSN:-}" ]]; then
    db=(psql "$RVBBIT_DSN")
else
    db=(psql --username "${POSTGRES_USER:-postgres}" --dbname "${POSTGRES_DB:-rvbbit}")
fi
"${db[@]}" -X -v ON_ERROR_STOP=1 -v clover_base="$base" <<'SQL'
BEGIN;
SELECT set_config('rvbbit.configured_clover_base', :'clover_base', true);
DO $validate$
BEGIN
  IF current_setting('rvbbit.configured_clover_base') !~ '^https?://[a-zA-Z0-9._-]+(:[0-9]+)?$' THEN
    RAISE EXCEPTION 'RVBBIT_CLOVER_BASE_URL must be an HTTP(S) origin without credentials, path or query';
  END IF;
END
$validate$;
UPDATE rvbbit.backends
SET endpoint_url=current_setting('rvbbit.configured_clover_base') || regexp_replace(endpoint_url,'^https?://[^/]+','')
WHERE auth_header_env='RVBBIT_CLOVER_KEY' AND endpoint_url ~ '^https?://[^/]+/';
COMMIT;
SQL
if [[ -n "${RVBBIT_CLOVER_CATALOG_SQL:-}" ]]; then
    "${db[@]}" -X -v ON_ERROR_STOP=1 --single-transaction -f "${RVBBIT_CLOVER_INFERENCE_SQL:-/etc/clover/inference.sql}"
    "${db[@]}" -X -v ON_ERROR_STOP=1 --single-transaction -f "$RVBBIT_CLOVER_CATALOG_SQL"
fi
