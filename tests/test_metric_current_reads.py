"""Metrics read current data unless a historical data timestamp is requested."""
import uuid
from pathlib import Path

import psycopg
import pytest


@pytest.fixture
def metric_names(rvbbit):
    names = []

    def define(body, check=None):
        name = 'test_current_metric_' + uuid.uuid4().hex[:10]
        rvbbit.execute(
            'SELECT rvbbit.define_metric(%s, %s, p_check => %s)',
            (name, body, check),
        )
        names.append(name)
        return name

    yield define
    rvbbit.execute("RESET rvbbit.as_of_timestamp")
    for name in reversed(names):
        rvbbit.execute("SELECT rvbbit.set_category('metric',%s,NULL)", (name,))
        rvbbit.execute('DELETE FROM rvbbit.metric_observations WHERE metric_name=%s', (name,))
        rvbbit.execute('DELETE FROM rvbbit.metric_defs WHERE name=%s', (name,))


def snapshot(conn, table, values):
    source = 'SELECT * FROM (VALUES ' + ','.join(f'({i})' for i in values) + ') src(id)'
    if not values:
        source = 'SELECT NULL::integer AS id WHERE false'
    return conn.execute('SELECT generation FROM rvbbit.snapshot_load(%s::regclass,%s)',
                        (table, source)).fetchone()[0]


def value(conn, name, as_of=None):
    return conn.execute('SELECT rvbbit.metric(%s, p_data_as_of => %s)',
                        (name, as_of)).fetchone()[0]['value']


def setup_table(conn, table, retained=False):
    conn.execute("SET rvbbit.compact_vortex_layout = 'off'")
    conn.execute("SET rvbbit.compact_hive_layout = 'off'")
    conn.execute(f'CREATE TABLE {table} (id integer) USING rvbbit')
    if retained:
        conn.execute("SELECT rvbbit.set_acceleration_storage_policy(%s::regclass, history_policy=>'retained')", (table,))


def test_current_only_metric_check_and_observation(rvbbit, temp_table, metric_names):
    setup_table(rvbbit, temp_table)
    snapshot(rvbbit, temp_table, [1, 2, 3])
    name = metric_names(f'SELECT sum(id)::bigint AS value FROM {temp_table}',
                        'SELECT value=6 AS ok FROM metric')
    assert value(rvbbit, name) == 6
    assert rvbbit.execute('SELECT rvbbit.check_metric(%s)', (name,)).fetchone()[0]['ok'] is True
    observation = rvbbit.execute('SELECT rvbbit.materialize_metric(%s)', (name,)).fetchone()[0]
    stored = rvbbit.execute('SELECT value, status FROM rvbbit.metric_observations WHERE observation_id=%s', (observation,)).fetchone()
    assert stored[0]['value'] == 6
    assert stored[1] == 'pass'


@pytest.mark.parametrize('route', ['', 'rvbbit_native', 'datafusion_vector'])
def test_latest_snapshot_in_nested_composed_join_and_empty_snapshot(rvbbit, temp_table, metric_names, route):
    setup_table(rvbbit, temp_table, retained=True)
    rvbbit.execute("SELECT set_config('rvbbit.route_force_candidate', %s, false)", (route,))
    snapshot(rvbbit, temp_table, [100, 200])
    snapshot(rvbbit, temp_table, [2, 3])
    child = metric_names(f'WITH latest AS MATERIALIZED (SELECT id FROM {temp_table}) SELECT coalesce(sum(a.id),0)::bigint AS value FROM latest a JOIN {temp_table} b ON a.id=b.id')
    parent = metric_names(f'SELECT value*2 AS value FROM {{metric:{child}}}',
                          'SELECT value=10 AS ok FROM metric')
    assert value(rvbbit, child) == 5
    assert value(rvbbit, parent) == 10
    assert rvbbit.execute('SELECT rvbbit.check_metric(%s)', (parent,)).fetchone()[0]['ok'] is True
    snapshot(rvbbit, temp_table, [])
    assert value(rvbbit, child) == 0
    assert value(rvbbit, parent) == 0


def test_explicit_retained_history_and_relative_reference(rvbbit, temp_table, metric_names):
    setup_table(rvbbit, temp_table, retained=True)
    snapshot(rvbbit, temp_table, [1, 2])
    old = rvbbit.execute('SELECT clock_timestamp()').fetchone()[0]
    snapshot(rvbbit, temp_table, [10, 20, 30])
    name = metric_names(f'SELECT sum(id)::bigint AS value FROM {temp_table}',
                        'SELECT value=3 AS ok FROM metric')
    assert value(rvbbit, name) == 60
    assert value(rvbbit, name, old) == 3
    assert rvbbit.execute('SELECT rvbbit.check_metric(%s,p_data_as_of=>%s)', (name, old)).fetchone()[0]['ok'] is True
    # Zero offset exercises the relative-reference runner at the historical instant.
    relative = metric_names(f'SELECT {{metric:{name}.0sec}}::bigint AS value')
    assert value(rvbbit, relative, old) == 3


def test_explicit_history_rejected_and_setting_restored(rvbbit, temp_table, metric_names):
    setup_table(rvbbit, temp_table)
    snapshot(rvbbit, temp_table, [1])
    name = metric_names(f'SELECT count(*) AS value FROM {temp_table}', 'SELECT value=1 AS ok FROM metric')
    sentinel = '2001-01-01 00:00:00+00'
    rvbbit.execute("SELECT set_config('rvbbit.as_of_timestamp', %s, false)", (sentinel,))
    for call in ['metric', 'check_metric']:
        # A normal call deliberately requests current data, then restores its caller.
        rvbbit.execute(f'SELECT rvbbit.{call}(%s)', (name,)).fetchone()
        assert rvbbit.execute("SHOW rvbbit.as_of_timestamp").fetchone()[0] == sentinel
        # KPI checks deliberately report errors as verdicts; metric() raises.
        # Check both contracts without allowing a fallback to current data.
        with rvbbit.transaction():
            if call == 'metric':
                with pytest.raises(psycopg.Error, match='history_policy=current'):
                    with rvbbit.transaction():
                        rvbbit.execute('SELECT rvbbit.metric(%s,p_data_as_of=>now())', (name,)).fetchone()
            else:
                verdict = rvbbit.execute('SELECT rvbbit.check_metric(%s,p_data_as_of=>now())', (name,)).fetchone()[0]
                assert verdict['status'] == 'error'
                assert 'history_policy=current' in verdict['error']
            assert rvbbit.execute("SHOW rvbbit.as_of_timestamp").fetchone()[0] == sentinel
    rvbbit.execute('RESET rvbbit.as_of_timestamp')
    assert value(rvbbit, name) == 1


def test_heap_and_mixed_sources(rvbbit, temp_table, metric_names):
    setup_table(rvbbit, temp_table)
    snapshot(rvbbit, temp_table, [1, 2])
    rvbbit.execute('CREATE TEMP TABLE metric_heap_source (id integer)')
    rvbbit.execute('INSERT INTO metric_heap_source VALUES (2),(9)')
    heap = metric_names('SELECT count(*) AS value FROM metric_heap_source')
    mixed = metric_names(f'SELECT count(*) AS value FROM {temp_table} a JOIN metric_heap_source h USING(id)')
    assert value(rvbbit, heap) == 2
    assert value(rvbbit, mixed) == 1



def test_bulk_materialization_preserves_current_default(rvbbit, temp_table, metric_names):
    setup_table(rvbbit, temp_table)
    snapshot(rvbbit, temp_table, [1, 2, 3])
    name = metric_names(f'SELECT sum(id)::bigint AS value FROM {temp_table}')
    category = name
    rvbbit.execute("SELECT rvbbit.set_category('metric',%s,%s)", (name, category))
    row = rvbbit.execute('SELECT * FROM rvbbit.materialize_all_metrics(p_category=>%s)', (category,)).fetchone()
    assert row[0] == name and row[2] == 'ok', row
    stored = rvbbit.execute('SELECT value FROM rvbbit.metric_observations WHERE observation_id=%s', (row[1],)).fetchone()[0]
    assert stored['value'] == 6
    historical = rvbbit.execute('SELECT * FROM rvbbit.materialize_all_metrics(p_category=>%s,p_data_as_of=>now())', (category,)).fetchone()
    assert historical[2] == 'error' and 'history_policy=current' in historical[3]

def test_current_read_migration_is_repeatable_and_matches_bootstrap(rvbbit):
    root = Path(__file__).resolve().parents[1] / 'crates/pg_rvbbit'
    migration = (root / 'sql/migrations/0414_metric_current_reads.sql').read_text()
    catalog = (root / 'src/catalog.rs').read_text()
    for name in ['metric', '_run_check']:
        start = catalog.index(f'CREATE OR REPLACE FUNCTION rvbbit.{name}(')
        definition = catalog[start:catalog.index('$fn$;', start)+5]
        assert definition in migration
    with rvbbit.transaction():
        rvbbit.execute(migration)
        rvbbit.execute(migration)
