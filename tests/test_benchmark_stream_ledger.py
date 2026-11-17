"""A failed transport retry remains visible without double-counting logical usage."""
import runpy
from pathlib import Path


def test_stream_retry_is_diagnostic_and_unclosed_attempt_is_visible():
    summarize = runpy.run_path(str(Path(__file__).resolve().parents[1] /
        'scripts/appliance/export-run-ledger.py'))['summarize']
    rows = [
        {'event': 'model.started', 'attempt_id': 'logical'},
        {'event': 'stream.started', 'stream_attempt_id': 's1', 'stream_attempt_no': 1},
        {'event': 'stream.failed', 'stream_attempt_id': 's1', 'duration_ms': 180123},
        {'event': 'stream.started', 'stream_attempt_id': 's2', 'stream_attempt_no': 2},
        {'event': 'stream.completed', 'stream_attempt_id': 's2'},
        {'event': 'model.completed', 'attempt_id': 'logical', 'usage': {'output_tokens': 15}},
    ]
    for i, row in enumerate(rows): row['epoch_seconds'] = i
    result = summarize(rows)
    assert result['stream_attempts_started'] == 2
    assert result['stream_retries'] == 1 and result['stream_attempts_failed'] == 1
    assert result['stream_attempts_without_terminal'] == 0
    assert result['failed_stream_seconds'] == 180.123
    assert result['model_attempts_started'] == 1
    assert result['known_usage_totals']['output_tokens'] == 15
    assert summarize(rows[:4])['stream_attempts_without_terminal'] == 1


def test_relative_cost_is_separate_and_unknown_coverage_is_not_zero():
    summarize=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/appliance/benchmark.py'))['relative_cost_summary']
    result=summarize([
        {'benchmark_attribution':'linked','cost_kind':'included','cost_usd':0,'pricing':{'relative_cost_usd':2.5}},
        {'benchmark_attribution':'linked','cost_kind':'unknown'},
        {'benchmark_attribution':'window_only','cost_kind':'unknown'},
        {'benchmark_attribution':'linked','cost_kind':'reserved','pricing':{'relative_cost_usd':10}},
        {'benchmark_attribution':'prior_revaluation','pricing':{'relative_cost_usd':20}},
    ])
    assert result['linked']=={'known_relative_cost_usd':2.5,'priced_activities':1,'unpriced_activities':1}
    assert result['window_only']['known_relative_cost_usd'] is None
    assert 'prior_revaluation' not in result
