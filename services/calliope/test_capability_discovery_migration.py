"""Exercise the real crawler-definition migration in a disposable local Postgres.

No rvbbit extension, provider, existing database or lab service is contacted.
Only the installed crawler definition is needed; its body is never executed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'crates/pg_rvbbit/sql/migrations'


@pytest.fixture(scope='module')
def database():
    if os.geteuid() == 0 or not all(shutil.which(x) for x in ('initdb', 'pg_ctl')):
        pytest.skip('Needs unprivileged local PostgreSQL binaries')
    with tempfile.TemporaryDirectory(prefix='capability-migration-') as tmp:
        data = str(Path(tmp) / 'data')
        subprocess.run(['initdb', '-D', data, '-A', 'trust', '--no-locale', '-E', 'UTF8'],
                       check=True, capture_output=True)
        # A private socket directory; no TCP listener and no existing cluster.
        subprocess.run(['pg_ctl', '-D', data, '-l', str(Path(tmp)/'postgres.log'),
                        '-o', f"-k {tmp} -c listen_addresses=''", '-w', 'start'],
                       check=True, capture_output=True)
        try:
            with psycopg.connect(host=tmp, dbname='postgres', autocommit=True) as conn:
                conn.execute('CREATE SCHEMA rvbbit')
                conn.execute('CREATE TABLE rvbbit.capability_mcp_tools '
                             '(name text, updated_at timestamptz)')
                conn.execute("INSERT INTO rvbbit.capability_mcp_tools VALUES ('example', '2000-01-01')")
                conn.execute((MIGRATIONS/'0216_capability_alert_enabled.sql').read_text())
                yield conn
        finally:
            subprocess.run(['pg_ctl', '-D', data, '-m', 'immediate', '-w', 'stop'],
                           check=True, capture_output=True)


def test_crawler_migration_preserves_extensions_and_fails_closed(database):
    import capability_discovery as discovery
    definition_sql = "SELECT pg_get_functiondef('rvbbit.capability_crawl(boolean,text)'::regprocedure)"
    before = database.execute(definition_sql).fetchone()[0]
    migration = (MIGRATIONS/'0464_actionable_capability_docs.sql').read_text()
    database.execute(migration)
    after = database.execute(definition_sql).fetchone()[0]
    assert after == before.replace(discovery.LEGACY_SIGNATURE, discovery.MCP_SIGNATURE).replace(
        discovery.LEGACY_SUFFIX, discovery.MCP_SUFFIX)
    assert database.execute("SELECT updated_at > '2000-01-01' FROM rvbbit.capability_mcp_tools").fetchone()[0]
    database.execute(migration)
    assert database.execute(definition_sql).fetchone()[0] == after
    refreshed_at = database.execute('SELECT updated_at FROM rvbbit.capability_mcp_tools').fetchone()[0]
    unknown = after.replace(discovery.MCP_SIGNATURE, 'Unexpected upstream contract')
    database.execute(unknown)
    with pytest.raises(psycopg.errors.RaiseException, match='review migration 0464'):
        database.execute(migration)
    assert database.execute(definition_sql).fetchone()[0] == unknown
    assert database.execute('SELECT updated_at FROM rvbbit.capability_mcp_tools').fetchone()[0] == refreshed_at
