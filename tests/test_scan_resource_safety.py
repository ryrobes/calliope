"""Bounded regressions for the 2026-09-22 enrichment scan OOM.

Use an isolated PostgreSQL with RVBBIT_SCAN_BATCH_CACHE_MB=1 to exercise
cache overflow/rescan fallback; no large production data is needed.
"""
import time
import psycopg
import pytest


def prepare(conn, table):
    conn.execute("SET rvbbit.compact_vortex_layout = 'off'")
    conn.execute("SET rvbbit.compact_hive_layout = 'off'")
    conn.execute("SET work_mem = '4MB'")
    conn.execute(f"CREATE TABLE {table} (id int, body text) USING rvbbit")
    conn.execute(f"INSERT INTO {table} SELECT g, repeat(md5(g::text),128) FROM generate_series(1,20000) g")
    result=conn.execute(f"SELECT rvbbit.refresh_acceleration('{table}'::regclass,false)").fetchone()[0]
    assert result['status']=='ok'


def plan(conn, sql, params=None):
    return '\n'.join(r[0] for r in conn.execute('EXPLAIN (COSTS OFF) '+sql, params))


def test_sampling_keeps_native_semantics(rvbbit,temp_table):
    prepare(rvbbit,temp_table)
    sql=f'SELECT count(*) FROM {temp_table} TABLESAMPLE SYSTEM (0)'
    assert 'Sample Scan' in plan(rvbbit,sql)
    assert 'RvbbitParquetScan' not in plan(rvbbit,sql)
    assert rvbbit.execute(sql).fetchone()[0]==0
    assert rvbbit.execute(f'SELECT count(*) FROM {temp_table} TABLESAMPLE BERNOULLI (100)').fetchone()[0]==20000
    sql=f'SELECT id FROM {temp_table} TABLESAMPLE SYSTEM (10) REPEATABLE (42) ORDER BY id'
    expected=rvbbit.execute(sql).fetchall()
    rvbbit.execute('SET rvbbit.force_heap_scan=on')
    assert rvbbit.execute(sql).fetchall()==expected
    rvbbit.execute('RESET rvbbit.force_heap_scan')


def test_scan_projection_memory_is_bounded(rvbbit,temp_table):
    prepare(rvbbit,temp_table)
    rvbbit.execute('''CREATE OR REPLACE FUNCTION pg_temp.scan_memory_bytes() RETURNS bigint
        LANGUAGE sql VOLATILE AS 'SELECT sum(total_bytes)::bigint FROM pg_backend_memory_contexts' ''')
    sql=f'''SELECT sum(length(doc::text)),max(bytes) FROM (
        SELECT jsonb_build_object('id',id,'text',body) AS doc,
        pg_temp.scan_memory_bytes() AS bytes FROM {temp_table} OFFSET 0) q'''
    assert 'RvbbitParquetScan' in plan(rvbbit,sql)
    length,peak=rvbbit.execute(sql).fetchone()
    assert length>80_000_000
    print(f'peak PostgreSQL contexts: {peak} bytes for 20,000 projected rows')
    assert peak<64*1024*1024, f'Per-row PG memory grew to {peak} bytes'


def test_over_budget_parameterized_rescan_keeps_all_rows(rvbbit,temp_table):
    prepare(rvbbit,temp_table)
    rvbbit.execute('SET enable_memoize=off')
    sql=f'''SELECT k, x.n, x.bytes FROM (VALUES(%s::int),(%s::int),(%s::int),(%s::int)) keys(k)
       CROSS JOIN LATERAL (SELECT count(*) n,sum(length(body)) bytes
       FROM {temp_table} WHERE id >= keys.k OFFSET 0) x'''
    params=(3,10000,20001,3)
    assert 'RvbbitParquetScan' in plan(rvbbit,sql,params)
    rows=rvbbit.execute(sql, params).fetchall()
    assert rows==[(k,max(20001-k,0),(20001-k)*4096 if k<=20000 else None) for k in [3,10000,20001,3]]


def test_rejected_rows_are_interruptible(rvbbit,temp_table):
    prepare(rvbbit,temp_table)
    sql=f"SELECT id FROM {temp_table} WHERE body ~ 'never-matches$'"
    assert 'RvbbitParquetScan' in plan(rvbbit,sql)
    rvbbit.execute("SET statement_timeout='1ms'")
    start=time.monotonic()
    try:
        with pytest.raises(psycopg.errors.QueryCanceled):
            rvbbit.execute(sql).fetchall()
    finally:
        rvbbit.execute('RESET statement_timeout')
    assert time.monotonic()-start<3
    assert rvbbit.execute('SELECT 1').fetchone()[0]==1


def test_sampling_accelerated_only_is_rejected(rvbbit,temp_table):
    rvbbit.execute(f'CREATE TABLE {temp_table} (id int) USING rvbbit')
    rvbbit.execute(f'INSERT INTO {temp_table} SELECT generate_series(1,100)')
    rvbbit.execute(f"SELECT rvbbit.compact('{temp_table}'::regclass,false)")
    with pytest.raises(psycopg.Error,match='TABLESAMPLE requires a retained authoritative heap'):
        rvbbit.execute(f'SELECT id FROM {temp_table} TABLESAMPLE SYSTEM(100)')


def test_nested_sample_not_rewritten_as_full_count(rvbbit,temp_table):
    prepare(rvbbit,temp_table)
    for sql in [
        f'SELECT (SELECT count(*) FROM {temp_table} TABLESAMPLE SYSTEM(0))',
        f'WITH s AS MATERIALIZED (SELECT id FROM {temp_table} TABLESAMPLE SYSTEM(0)) SELECT count(*) FROM s',
    ]:
        assert rvbbit.execute(sql).fetchone()[0]==0


def test_partial_replay_cache_is_discarded_before_rescan(rvbbit, temp_table):
    """An early LIMIT must not prepend a duplicate batch on the next scan."""
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    rvbbit.execute("SET enable_memoize = off")
    rvbbit.execute(f"CREATE TABLE {temp_table} (id int) USING rvbbit")
    rvbbit.execute(f"INSERT INTO {temp_table} SELECT generate_series(1,20000)")
    rvbbit.execute(f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass,false)")
    sql = f'''SELECT keys.minimum, keys.n, x.total FROM
        (VALUES (%s::int,%s::int),(%s::int,%s::int),(%s::int,%s::int)) keys(minimum,n)
        CROSS JOIN LATERAL (
            SELECT count(*) AS total FROM (
                SELECT id FROM {temp_table} WHERE id >= keys.minimum LIMIT keys.n
            ) limited OFFSET 0
        ) x'''
    params = (1, 1, 2, 30000, 1, 30000)
    assert 'RvbbitParquetScan' in plan(rvbbit, sql, params)
    assert rvbbit.execute(sql, params).fetchall() == [
        (1, 1, 1), (2, 30000, 19999), (1, 30000, 20000)
    ]
