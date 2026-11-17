"""Resolution Ledger writes stay atomic, including optional alias failures."""
import ast
import json
import os
from pathlib import Path
import re
import sys

import psycopg
from psycopg.rows import dict_row
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger():
    dsn = os.environ.get('CALLIOPE_APPEARANCE_TEST_DSN')
    if not dsn:
        pytest.skip('Set CALLIOPE_APPEARANCE_TEST_DSN to an empty disposable database')
    import calliope
    def connect():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    with connect() as c:
        assert c.execute("SELECT to_regnamespace('rvbbit') AS n").fetchone()['n'] is None
        c.execute('CREATE SCHEMA rvbbit')
        migration = (ROOT/'crates/pg_rvbbit/sql/migrations/0415_calliope_term_bindings.sql').read_text()
        c.execute(migration); c.execute(migration)
        c.execute("CREATE TABLE rvbbit.metric_defs(name text PRIMARY KEY, labels jsonb)")
        c.execute("CREATE VIEW rvbbit.metric_catalog AS SELECT * FROM rvbbit.metric_defs")
        c.execute("INSERT INTO rvbbit.metric_defs VALUES('leads','{}')")
    path = ROOT/'services/calliope/server.py'
    fn = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name=='_mcp_bind_calliope_term')
    ns = dict(_conn=connect, _logged=lambda tool,args,fn: fn(), re=re, json=json, sys=sys)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
    calliope._term_bindings_invalidate_cache()
    try:
        yield ns['_mcp_bind_calliope_term'], connect
    finally:
        calliope._term_bindings_invalidate_cache()
        with connect() as c:c.execute('DROP SCHEMA rvbbit CASCADE')


def test_failed_replacement_keeps_previous_binding(ledger):
    bind,connect = ledger
    bind('bind','Leads','metric','leads','Original meaning')
    with connect() as c:
        c.execute("ALTER TABLE rvbbit.calliope_term_bindings ADD CHECK(gloss <> 'Rejected meaning')")
    with pytest.raises(psycopg.errors.CheckViolation):
        bind('bind','Leads','metric','leads','Rejected meaning')
    with connect() as c:
        rows=c.execute('SELECT gloss,invalidated_at FROM rvbbit.calliope_term_bindings').fetchall()
    assert rows==[dict(gloss='Original meaning',invalidated_at=None)]


def test_optional_alias_failure_does_not_rollback_binding(ledger):
    bind,connect = ledger
    with connect() as c:
        c.execute("ALTER TABLE rvbbit.metric_defs ADD CHECK(labels='{}'::jsonb)")
    assert bind('bind','Leads','metric','leads','Settled meaning')['status']=='bound'
    with connect() as c:
        assert c.execute('SELECT gloss FROM rvbbit.calliope_term_bindings').fetchone()['gloss']=='Settled meaning'


def test_replacement_and_invalidation_keep_receipts(ledger):
    bind,connect = ledger
    bind('bind','Leads','metric','leads','Original meaning')
    bind('bind','Leads','metric','leads','Corrected meaning')
    assert bind('list')['count']==1
    assert bind('invalidate','Leads',reason='Operator correction')['status']=='invalidated'
    assert bind('list')['count']==0
    with connect() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_term_bindings WHERE invalidated_at IS NOT NULL').fetchone()['n']==2
