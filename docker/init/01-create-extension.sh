#!/usr/bin/env bash
set -euo pipefail
export PGUSER="${POSTGRES_USER:-postgres}" PGDATABASE="${POSTGRES_DB:-rvbbit}"
if [[ "${RVBBIT_SCHEMA_INSTALL_MODE:-baseline}" == history ]]; then
    # Explicit diagnostic path for comparing a fresh schema with the baseline.
    psql -X -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION pg_rvbbit; SELECT rvbbit.migrate();'
else
    rvbbit-schema-release install
fi
psql -X -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
-- Sanity check: print version into the postgres log so `docker compose logs`
-- confirms the extension actually loaded.
DO $$
BEGIN
    RAISE NOTICE 'rvbbit loaded: %', rvbbit.rvbbit_build_info();
END $$;

-- Seed provider model/rate metadata so EXPLAIN SEMANTIC, receipts/costs,
-- and UI model pickers start with useful data. No-ops gracefully when
-- provider keys or network access are unavailable at boot.
DO $$
BEGIN
    RAISE NOTICE 'rvbbit provider catalogs refreshed: %',
        (SELECT jsonb_agg(to_jsonb(r)) FROM rvbbit.refresh_provider_catalogs('auto') r);
END $$;
SQL
