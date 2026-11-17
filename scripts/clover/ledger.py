#!/usr/bin/env python3
"""Prepare the private Postgres ledger configuration, or import historic SQLite metadata.

Preparation does not connect to anything. Apply ledger-bootstrap.sql as a database
administrator, mount ledger.env into Clover, then restart only Clover. Imports use
psycopg and CLOVER_LEDGER_DSN; no credentials in command arguments or output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
from urllib.parse import quote

SCHEMA = Path(__file__).resolve().parents[2] / "crates/rvbbit_hutch/src/ledger.sql"


def private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    path.chmod(0o600)


def prepare(directory, instance_id, host, port, database, *, schema_path=None, sslmode='prefer'):
    directory = directory.expanduser().resolve()
    cfg = json.loads((directory / 'hutch.json').read_text())
    if not instance_id or len(instance_id.encode()) > 128:
        raise ValueError('instance_id must be 1..128 bytes')
    keys_path = directory / 'ledger-keys.json'
    keys = json.loads(keys_path.read_text()) if keys_path.exists() else {'password': secrets.token_urlsafe(32)}
    password = keys['password']
    private(keys_path, json.dumps(keys) + '\n')
    if sslmode not in ('disable', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise ValueError('Invalid PostgreSQL sslmode')
    dsn = f"postgresql://clover_ledger:{quote(password, safe='')}@{host}:{port}/{quote(database, safe='')}?sslmode={sslmode}"
    if any(c in dsn for c in '\r\n'):
        raise ValueError('Invalid database address')
    private(directory / 'ledger.env', 'CLOVER_LEDGER_DSN=' + dsn + '\n')
    # Stable password on repeated preparation, with no privileges on app tables.
    literal = "'" + password.replace("'", "''") + "'"
    bootstrap = """DO $$ BEGIN
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='clover_ledger') THEN
    CREATE ROLE clover_ledger LOGIN;
  END IF;
END $$;
""" + 'ALTER ROLE clover_ledger NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION LOGIN PASSWORD ' + literal + ';\n'
    bootstrap += "ALTER ROLE clover_ledger SET rvbbit.force_heap_scan='on';\n"
    bootstrap += 'CREATE SCHEMA IF NOT EXISTS clover_telemetry;\n'
    bootstrap += (schema_path or SCHEMA).read_text() + '\n'
    bootstrap += '''GRANT USAGE ON SCHEMA clover_telemetry TO clover_ledger;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA clover_telemetry TO clover_ledger;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA clover_telemetry TO clover_ledger;
GRANT EXECUTE ON FUNCTION clover_telemetry.ingest(jsonb) TO clover_ledger;
-- Current RVBBIT relation hooks consult this two-column registry even with
-- force_heap_scan enabled. Grant no source data or other catalog columns.
DO $$ BEGIN
  IF to_regclass('rvbbit.tables') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA rvbbit TO clover_ledger;
    GRANT SELECT(table_oid,acceleration_enabled) ON rvbbit.tables TO clover_ledger;
  END IF;
END $$;
'''
    private(directory / 'ledger-bootstrap.sql', bootstrap)
    cfg['ledger_postgres'] = {'dsn_env': 'CLOVER_LEDGER_DSN', 'instance_id': instance_id, 'queue_capacity': 4096}
    private(directory / 'hutch.json', json.dumps(cfg, indent=2) + '\n')
    return {'prepared': True, 'instance_id': instance_id, 'mode': 'postgres'}


def events(db, instance_id, source_id):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    stream_ids = {r[0] for r in db.execute("SELECT DISTINCT invocation_id FROM upstream_attempts WHERE mode='stream'")} if 'upstream_attempts' in tables else set()
    for table, kind, timestamp in [('invocations', 'invocation', 'ts'), ('upstream_attempts', 'attempt', 'ts'),
                                  ('provider_price_snapshots', 'price', 'captured_at'),
                                  ('speech_usage', 'speech', None), ('media_usage', 'media', None)]:
        if table not in tables:
            continue
        for row in db.execute('SELECT * FROM ' + table):
            data = dict(row)
            key = data.get('id', data.get('invocation_id'))
            event_id = 'sqlite:' + hashlib.sha256(json.dumps([instance_id, source_id, table, key]).encode()).hexdigest()
            stamp = data.get(timestamp) if timestamp else '1970-01-01T00:00:00Z'
            if kind == 'invocation':
                data.setdefault('stream', data.get('invocation_id') in stream_ids)
                data.setdefault('reference_cost_nanousd', data.get('would_be_cost_microusd', 0) * 1000)
                # Historic specialists mixed tariffs and worker-reported charges.
                data['cost_basis'] = 'reference_tariff' if data['backend'].startswith('llm:') else 'legacy_specialist_mixed'
            if kind == 'attempt':
                # Error bodies are not part of the metadata export contract.
                data['reconcile_error'] = 'provider_cost_unavailable' if data.get('reconcile_error') else None
            if kind == 'media':
                data['usage_json'] = json.loads(data['usage_json'])
            yield {'kind': kind, 'instance_id': instance_id, 'event_id': event_id, 'ts': stamp, 'data': data}


def import_sqlite(path, instance_id, source_id):
    import psycopg
    from psycopg.types.json import Jsonb
    # A backup snapshot includes WAL and never holds a SQLite read transaction
    # throughout a potentially slow Postgres import. Stop the writer for cutover.
    with tempfile.TemporaryDirectory(prefix='clover-ledger-') as temp:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(Path(temp) / 'snapshot.sqlite') as db:
            source.backup(db)
            db.row_factory = sqlite3.Row
            counts = {}
            with psycopg.connect(os.environ['CLOVER_LEDGER_DSN'], connect_timeout=5) as pg:
                pg.execute("SET statement_timeout='30s'")
                batch = []
                for event in events(db, instance_id, source_id):
                    counts[event['kind']] = counts.get(event['kind'], 0) + 1
                    batch.append(event)
                    if len(batch) == 128:
                        pg.execute('SELECT clover_telemetry.ingest(%s)', [Jsonb(batch)])
                        pg.commit()
                        batch = []
                if batch:
                    pg.execute('SELECT clover_telemetry.ingest(%s)', [Jsonb(batch)])
            return {'imported_or_already_present': counts, 'instance_id': instance_id, 'source_id': source_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--config', type=Path, required=True)
    prep.add_argument('--instance-id', default='self-hosted')
    prep.add_argument('--host', default='postgres')
    prep.add_argument('--port', type=int, default=5432)
    prep.add_argument('--database', default='rvbbit')
    imp = sub.add_parser('import-sqlite')
    imp.add_argument('--sqlite', type=Path, required=True)
    imp.add_argument('--instance-id', required=True)
    imp.add_argument('--source-id', default='legacy-sqlite', help='Keep the same value on every retry of this history import')
    args = parser.parse_args()
    try:
        result = prepare(args.config, args.instance_id, args.host, args.port, args.database) if args.command == 'prepare' else import_sqlite(args.sqlite, args.instance_id, args.source_id)
    except Exception as e:
        # Driver errors can echo DSNs, SQL or records. Keep those out of logs.
        print('Ledger operation failed (' + type(e).__name__ + '). Check private configuration and database access.', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
