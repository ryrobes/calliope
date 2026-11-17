#!/usr/bin/env python3
"""Register an ingestion-test fixture as a mirrored source from inside calliope-server.

Calls the same functions as the authenticated /api/calliope/setup/databases
endpoints (save credential, probe, discover, plan, apply) without an HTTP
login, for operator setup of lab and test instances. Prints no credentials.
Start the fixture first (docker/docker-compose.ingestion-test.yml on the
instance's network), then:

  docker cp scripts/appliance/register-sample-mirror.py <calliope-container>:/tmp/
  docker exec <calliope-container> python /tmp/register-sample-mirror.py --fixture oracle --owner admin@example.com
"""
import argparse
import asyncio
import json
import os
import sys
from urllib.parse import quote

# Test-only fixture readers documented in docker/sample-sources/README.md;
# destination schemas match scripts/hosted-appliance-smoke.py.
FIXTURES = {
    'oracle': ('oracle_samples', 'Oracle sample company (Customer Orders, Sales History)', 'oracle',
               'oracle+oracledb://mirror_reader:Rvbbit_Oracle_Readonly_2026%21@sample-oracle:1521/?service_name=FREEPDB1',
               (('CO', 'oracle_co'), ('SH', 'oracle_sh'))),
    'postgres': ('fixture_postgres', 'Fixture PostgreSQL commerce', 'postgresql',
                 'postgresql://mirror_reader:mirror_readonly@sample-postgres:5432/commerce',
                 (('sales', 'commerce_sales_pg'), ('support', 'commerce_support_pg'))),
    'mysql': ('fixture_mysql', 'Fixture MySQL CRM', 'mysql',
              'mysql+pymysql://mirror_reader:mirror_readonly@sample-mysql:3306/crm', (('crm', 'crm_mysql'),)),
    'mssql': ('fixture_mssql', 'Fixture SQL Server operations', 'mssql',
              'mssql+pymssql://mirror_reader:Rvbbit_Mirror_Readonly_2026%21@sample-mssql:1433/operations',
              (('field_ops', 'operations_mssql'),)),
}


def table_plan(table):
    """Same reviewed defaults as scripts/hosted-appliance-smoke.py."""
    columns = {str(col.get('name') or '') for col in table.get('columns') or [] if isinstance(col, dict)}
    key = [str(v) for v in table.get('primary_key') or []]
    incremental = bool(key and 'updated_at' in columns)
    return {'source_table': str(table.get('name') or ''),
            'destination_table': str(table.get('destination_table') or table.get('name') or ''),
            'load_mode': 'incremental_upsert' if incremental else 'snapshot',
            'primary_key': key or None, 'cursor_column': 'updated_at' if incremental else None,
            'included_columns': None}


async def register(c, factory, fixture, owner):
    name, label, dialect, source, schemas = fixture
    secrets = (source,)
    name = c._setup_mirror_name(name)
    descriptor = c._setup_source_descriptor(source, dialect)
    descriptor['environment'] = 'sample data'
    descriptor['configured_by'] = owner
    with factory() as conn:
        row = conn.execute('SELECT rvbbit.upsert_mirror_connection(%s,%s,%s,%s::jsonb,true) AS connection_name',
                           (name, label, dialect, json.dumps(descriptor, separators=(',', ':')))).fetchone()
    name = str(row['connection_name'])
    path = f'/connections/{quote(name, safe="")}'
    stored = await c._setup_mirror_worker_request(factory, 'PUT', f'{path}/credential',
                                                  {'source_dsn': source}, secret_values=secrets)
    print('credential', name, stored.get('credential_ref'))
    probe = await c._setup_mirror_worker_request(factory, 'POST', f'{path}/probe', {}, secret_values=secrets)
    print('probe', {k: probe.get(k) for k in ('ok', 'elapsed_ms')})
    for source_schema, destination in schemas:
        discovery = await c._setup_mirror_worker_request(
            factory, 'POST', f'{path}/discover',
            {'source_schema': source_schema, 'include_views': True, 'limit': 200},
            secret_values=secrets, timeout_seconds=90)
        tables = discovery.get('tables') if isinstance(discovery.get('tables'), list) else []
        if not tables:
            raise SystemExit(f'No tables discovered in {source_schema}')
        plan = c._setup_mirror_plan({'source_schema': source_schema, 'destination_schema': destination,
                                     'schedule_seconds': None, 'run_now': True,
                                     'tables': [table_plan(t) for t in tables]}, name)
        run_id = await asyncio.to_thread(c._apply_setup_mirror_plan, factory, plan)
        print('queued', {'job': plan['job_name'], 'source': source_schema, 'destination': destination,
                         'tables': len(tables), 'run_id': run_id})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--fixture', choices=sorted(FIXTURES), action='append', required=True)
    parser.add_argument('--owner', required=True, help='Administrator recorded as configured_by')
    args = parser.parse_args()
    sys.path.insert(0, '/app')
    import psycopg
    from psycopg.rows import dict_row
    import calliope as c

    def factory(*_args, **_kwargs):
        return psycopg.connect(os.environ['WAREHOUSE_DSN'], row_factory=dict_row, autocommit=True)

    for fixture in args.fixture:
        asyncio.run(register(c, factory, FIXTURES[fixture], args.owner))


if __name__ == '__main__':
    main()
