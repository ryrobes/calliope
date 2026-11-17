#!/usr/bin/env python3
"""Prepare private Postgres storage for Clover Research; no connection or inference.

Apply research-bootstrap.sql as the database administrator, then start the
research Compose profile. Runtime credentials access only the evidence schema.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
from urllib.parse import quote

SCHEMA = Path(__file__).resolve().parents[2] / 'services/clover/research/schema.sql'


def private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    path.chmod(0o600)


def prepare(directory, host='postgres', port=5432, database='rvbbit', *, schema_path=None, sslmode='prefer'):
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    keys_path = directory / 'research-keys.json'
    keys = json.loads(keys_path.read_text()) if keys_path.exists() else {'password': secrets.token_urlsafe(32)}
    password = keys['password']
    if not host or any(c in host for c in '/?#@\r\n') or not 1 <= port <= 65535:
        raise ValueError('Invalid Postgres address')
    if sslmode not in ('disable', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise ValueError('Invalid PostgreSQL sslmode')
    dsn = f"postgresql://clover_research:{quote(password,safe='')}@{host}:{port}/{quote(database,safe='')}?sslmode={sslmode}"
    private(keys_path, json.dumps(keys) + '\n')
    private(directory / 'research.env', 'CLOVER_RESEARCH_DSN=' + dsn + '\n')
    literal = "'" + password.replace("'", "''") + "'"
    bootstrap = """DO $$ BEGIN
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='clover_research') THEN
    CREATE ROLE clover_research LOGIN;
  END IF;
END $$;
""" + 'ALTER ROLE clover_research NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION LOGIN PASSWORD ' + literal + ';\n'
    bootstrap += "ALTER ROLE clover_research SET rvbbit.force_heap_scan='on';\n"
    bootstrap += (schema_path or SCHEMA).read_text() + '''
GRANT USAGE ON SCHEMA clover_research TO clover_research;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA clover_research TO clover_research;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA clover_research TO clover_research;
-- RVBBIT's relation hook consults only these registry columns, including when
-- force_heap_scan is enabled. No source data privileges are granted.
DO $$ BEGIN
  IF to_regclass('rvbbit.tables') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA rvbbit TO clover_research;
    GRANT SELECT(table_oid,acceleration_enabled) ON rvbbit.tables TO clover_research;
  END IF;
END $$;
'''
    private(directory / 'research-bootstrap.sql', bootstrap)
    return {'prepared': True, 'storage': 'postgres', 'schema': 'clover_research'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='postgres')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', default='rvbbit')
    args = parser.parse_args()
    try:
        result = prepare(args.config, args.host, args.port, args.database)
    except Exception as error:
        parser.exit(1, f'Research preparation failed ({type(error).__name__}); check private configuration.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
