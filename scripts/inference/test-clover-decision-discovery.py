#!/usr/bin/env python3
"""Check signatures, examples, retrieval and seed freshness on a disposable DB.

Apply migrations 0432..0434 first. Uses normal PG* connection variables.
No decision inference is executed. Capability search may use the configured
embedder; a fixture without one exercises its lexical fallback.
"""
import os
from pathlib import Path
import psycopg

if os.environ.get('CLOVER_DECISIONS_DISPOSABLE_TEST') != '1':
    raise SystemExit('Set CLOVER_DECISIONS_DISPOSABLE_TEST=1 only for a disposable database')

expected={'truth','truth_score','choose','leaf','branch','choice_branch',
          'decision_tree','decision_tree_batch','truth_question','choice_question'}
with psycopg.connect(autocommit=True) as conn:
    conn.execute('SELECT rvbbit.capability_crawl(false)')
    rows=conn.execute("SELECT name,signature,example FROM rvbbit.capability_seed WHERE name=ANY(%s)",(list(expected),)).fetchall()
    assert {r[0] for r in rows}==expected
    for name,signature,example in rows:
        assert signature.startswith('rvbbit.'+name+'(')
        found=conn.execute("SELECT name,doc FROM rvbbit.capability_search_for(NULL,%s,25,ARRAY['cap_operator'])",(name,)).fetchall()
        assert any(n==name and signature in doc for n,doc in found),name
        conn.execute('EXPLAIN '+example)
    assert not conn.execute('SELECT rvbbit.capability_search_stale()').fetchone()[0]
    try:
        conn.execute("UPDATE rvbbit.capability_seed SET description=description || ' Changed documentation.' WHERE name='choice_branch'")
        conn.execute("UPDATE rvbbit.catalog_docs SET updated_at=now() WHERE graph_id='rvbbit_capabilities'")
        assert conn.execute('SELECT rvbbit.capability_search_stale()').fetchone()[0]
    finally:
        conn.execute(Path('crates/pg_rvbbit/sql/migrations/0434_clover_decision_discovery.sql').read_text())
    assert not conn.execute('SELECT rvbbit.capability_search_stale()').fetchone()[0]
print('PASS: ten discoverable helpers, executable examples, exact signatures, idempotency, and seed freshness')
