"""Schema release identity and comparison rules must fail closed on real changes."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('schema_' + name, ROOT / 'scripts/schema' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = load('release')
baseline = load('baseline')


def test_incremental_and_adopted_history_are_allowed_but_downgrades_are_not():
    lock = {'migrations': {'0001_first': 'aaa', '0002_second': 'bbb'}}
    target = {'calliope_version': '1.2.0-rc.1'}
    release.check_state(['0001_first'], {}, None, target, lock)
    release.check_state(['0001_first'], {'0001_first': 'aaa'}, '1.1.4', target, lock)
    with pytest.raises(ValueError, match='absent'):
        release.check_state(['0003_newer'], {}, None, target, lock)
    with pytest.raises(ValueError, match='checksum'):
        release.check_state(['0001_first'], {'0001_first': 'changed'}, None, target, lock)
    with pytest.raises(ValueError, match='product downgrade'):
        release.check_state(['0001_first'], {}, '1.2.0', target, lock)
    with pytest.raises(ValueError, match='product downgrade'):
        release.check_state(['0001_first'], {}, '1.2.0-rc.2', target, lock)
    assert release.version_key('1.2.0-rc.10') > release.version_key('1.2.0-rc.2')


def test_schema_comparison_accepts_only_postgres_deparser_normalization():
    pytest.importorskip('pglast')
    same = baseline.canonical_schema
    assert same('SELECT NULL::text;') == same('SELECT NULL::text AS text;')
    assert same('SELECT 1 WHERE (a AND b) AND (c AND d);') == same('SELECT 1 WHERE a AND b AND (c AND d);')
    assert same('SELECT NULL::text AS customer;') != same('SELECT NULL::text AS text;')
    assert same('SELECT 1 WHERE a AND b;') != same('SELECT 1 WHERE a OR b;')
    assert same('CREATE TABLE t(a text); GRANT SELECT ON t TO PUBLIC;') != same('CREATE TABLE t(a text);')
    assert same('CREATE TABLE t(a text CHECK(length(a)>3));') != same('CREATE TABLE t(a text CHECK(length(a)>2));')


def test_oid_normalization_keeps_distinct_object_identities():
    data = {'rvbbit.tables': [{'table_oid': 1}, {'table_oid': 2}, {'table_oid': 3}, {'table_oid': None}]}
    mappings = [{'table': 'rvbbit.tables', 'column': 'table_oid', 'old': 1, 'identity': 'one'},
                {'table': 'rvbbit.tables', 'column': 'table_oid', 'old': 2, 'identity': 'two'}]
    normalized = baseline.normalized_data(data, mappings)
    assert {str(row['table_oid']) for row in normalized['rvbbit.tables']} == {'one', 'two', '3', 'None'}
    assert data['rvbbit.tables'][0]['table_oid'] == 1
