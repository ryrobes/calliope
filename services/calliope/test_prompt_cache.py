"""Real usage shapes, unknown coverage, privacy and prompt stability contracts."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import calliope
import dashboard_mode


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


ledger = load('cache_ledger', 'docker/calliope/hermes-run-ledger.py')
exporter = load('cache_export', 'scripts/appliance/export-run-ledger.py')


@pytest.mark.parametrize('usage,expected', [
    ({'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 800, 'cache_write_tokens': 100}}, (1000, 800, 100)),
    ({'input_tokens': 1000, 'input_tokens_details': {'cached_tokens': 800, 'cache_write_tokens': 100}}, (1000, 800, 100)),
    ({'input_tokens': 100, 'cache_read_input_tokens': 800, 'cache_creation_input_tokens': 100}, (1000, 800, 100)),
    ({'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 800}, (1000, 800, None)),
    ({'prompt_tokens': 1000}, (1000, None, None)),
    ({'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 0}}, (1000, 0, None)),
    ({'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': -1, 'cache_write_tokens': float('nan')}}, (1000, None, None)),
])
def test_provider_cache_counters_keep_missing_distinct_from_zero(usage, expected):
    r = ledger.cache_usage_metadata(usage)
    assert tuple(r[k] for k in ('prompt_tokens', 'cache_read_tokens', 'cache_write_tokens')) == expected


def test_prefix_diagnostics_never_export_prompts_credentials_or_identity():
    request = {'messages': [{'role': 'system', 'content': 'private scaffold'}, {'role': 'user', 'content': 'private question'}],
               'tools': [{'type': 'function', 'function': {'name': 'lookup', 'description': 'private schema'}}],
               'extra_headers': {'Authorization': 'private credential', 'x-hutch-cache-scope': 'private scope'}}
    meta = ledger.measured_payload({}, request)['_ledger_cache_request']
    assert 'private' not in json.dumps(meta)
    assert meta['identity_source'] == 'hutch_scope' and len(meta['identity_sha256']) == 64
    request['messages'][-1]['content'] = 'a different user question'
    assert ledger.cache_request_metadata(request) == meta
    request['messages'][0]['content'] = 'changed scaffold'
    changed = ledger.cache_request_metadata(request)
    assert changed['identity_sha256'] == meta['identity_sha256']
    assert changed['system_prefix_sha256'] != meta['system_prefix_sha256']


def test_cache_export_has_correct_denominator_no_stream_double_count_and_unknown_legacy():
    def done(usage=None, **kw):
        return {'event': 'model.completed', 'model': 'calliope-sol', 'cache_usage': usage, **kw}
    read = {'prompt_tokens': 1000, 'cache_read_tokens': 800, 'cache_write_tokens': 0}
    write = {'prompt_tokens': 1000, 'cache_read_tokens': 0, 'cache_write_tokens': 900}
    rows = [done(write, reported_cost_usd=.1), done(read, reported_cost_usd=.01), done(),
            done({'prompt_tokens': 9000, 'cache_read_tokens': None, 'cache_write_tokens': None}),
            {'event': 'stream.completed', 'cache_usage': read, 'reported_cost_usd': 9}]
    cache = exporter.summarize_cache(rows)
    overall = cache['overall']
    assert overall['cache_read_fraction'] == .4
    assert overall['cache_read_tokens'] == 800 and overall['cache_write_tokens'] == 900
    assert overall['completed_requests'] == 4 and overall['requests_with_read_counter'] == 2
    assert overall['requests_with_provider_cache_counters'] == 2
    assert overall['reported_cost_usd'] == .11 and overall['requests_with_reported_cost'] == 2
    assert len(cache['by_route']) == 1
    assert exporter.summarize_cache([done()])['overall']['cache_read_tokens'] is None


def test_cache_transition_comparisons_do_not_mix_agents():
    def started(scope, tools='t', system='s'):
        return {'event': 'model.started', 'model': 'alias', 'cache_request': {
            'identity_source': 'hutch_scope', 'identity_sha256': scope,
            'tools_sha256': tools, 'system_prefix_sha256': system}}
    cache = exporter.summarize_cache([started('iris'), started('cassandra', system='other'),
                                     started('iris'), started('iris', tools='new')])
    assert cache['prefix_transitions'] == {'compared_requests': 2, 'tool_list_unchanged': 1,
        'system_prefix_unchanged': 2, 'both_unchanged': 1, 'tool_list_changed': 1}


def test_authoring_contract_stays_before_dynamic_state_without_losing_context():
    first = calliope._instructions([{'id': 'surface-one'}], None, authoring_mode='directed',
                                  vocabulary_note='UNIQUE_DYNAMIC_VOCAB')
    second = calliope._instructions([{'id': 'surface-two'}], {'id': 'selected'}, authoring_mode='directed',
                                   vocabulary_note='UNIQUE_DYNAMIC_VOCAB_2')
    contract = dashboard_mode.instruction('directed')
    assert first.count(contract) == second.count(contract) == 1
    assert first.index(contract) < first.index('UNIQUE_DYNAMIC_VOCAB') < first.index('CALLIOPE_SURFACE_STATE=')
    assert first.split('UNIQUE_DYNAMIC_VOCAB')[0] == second.split('UNIQUE_DYNAMIC_VOCAB')[0]
    assert 'surface-two' in second and 'selected' in second and 'UNIQUE_DYNAMIC_VOCAB_2' in second
