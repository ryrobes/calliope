import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
import specialist_stage_context as recall
import stage_context as sc


def run():
    return dict(id='run', runner_id='worker', thread_id='thread', round_no=2,
                source_session_id='parent', owner_email='person@example.com', task='Fix the maturity dropdown',
                context={'current_turn': {'object_refs': ['pinned']},
                         'parent_conversation': {'current_user_message': 'Use 14-day cohorts'},
                         'provided': {'warning': 'Keep partial cohorts marked'}})


def items():
    return [dict(surface_id=str(n), kind='query', title=f'Query {n}', _lineage=str(n),
                 payload={'rows': ['never forward']}) for n in range(12)]


class DB:
    def __init__(self):
        self.saved = None
        self.calls = []
    def __call__(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, args=None):
        self.calls.append((sql, args))
        row = None
        if sql.startswith('SELECT task,result'):
            row = {'task': 'Build cohort chooser', 'result': 'The maturity warning is missing'}
        if sql.startswith('UPDATE'):
            self.saved = json.loads(args[0])
            row = {'snapshot': self.saved}
        return SimpleNamespace(fetchone=lambda: row)


@pytest.fixture(autouse=True)
def scope(monkeypatch):
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', 'select')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_OWNERS', '')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_SESSIONS', '')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake')


def test_assignment_selection_frozen_and_reused_without_touching_context(monkeypatch):
    db = DB(); original = run(); before = copy.deepcopy(original); calls = []
    async def decide(task, history, pool, selected, key):
        calls.append((task, history))
        return [pool[5]], {'usage': {'cost': .001}, 'answers': {'s5': {'choice': 'useful'}}}
    monkeypatch.setattr(sc, 'decide', decide)
    first = asyncio.run(recall.prepare(db, original, lambda: (items(), None)))
    assert original == before
    assert first == db.saved and first['selected_ids'] == ['5']
    assert calls[0][0] == original['task']
    assert calls[0][1][-1]['text'] == 'The maturity warning is missing'
    assert first['usage']['cost'] == .001
    prompt = recall.prompt_context(first)
    assert prompt['items'][0]['id'] == '5'
    assert 'never forward' not in json.dumps(prompt) and 'usage' not in prompt
    replay = {**original, 'context': {**original['context'], recall.KEY: first}}
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', 'off')
    def fail(): raise AssertionError('must reuse the frozen snapshot')
    assert asyncio.run(recall.prepare(db, replay, fail)) == first
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['off', 'shadow'])
def test_off_and_shadow_do_not_change_handoff(monkeypatch, mode):
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', mode)
    async def decide(*args): return [items()[3]], {'usage': {'cost': .001}}
    monkeypatch.setattr(sc, 'decide', decide)
    db = DB()
    result = asyncio.run(recall.prepare(db, run(), lambda: (items(), None)))
    assert recall.prompt_context(result) is None
    assert (db.saved is None) == (mode == 'off')


@pytest.mark.parametrize('blocked', ['owner', 'session', 'bound_run'])
def test_scoping_and_bound_runs_skip_loading(monkeypatch, blocked):
    r = run()
    if blocked == 'owner': monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_OWNERS', 'other@example.com')
    if blocked == 'session': monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_SESSIONS', 'other')
    if blocked == 'bound_run': r['hermes_run_id'] = 'already-submitted'
    def fail(): raise AssertionError('must not load')
    assert asyncio.run(recall.prepare(DB(), r, fail)) is None


def test_provider_failure_preserves_normal_handoff_and_records_safe_failure(monkeypatch):
    async def fail(*args): raise TimeoutError('private upstream body')
    monkeypatch.setattr(sc, 'decide', fail)
    db = DB()
    result = asyncio.run(recall.prepare(db, run(), lambda: (items(), None)))
    assert result['status'] == 'fallback' and result['reason'] == 'TimeoutError'
    assert recall.prompt_context(result) is None
    assert 'private upstream body' not in json.dumps(result)


def test_small_inventory_and_persistence_failure_make_no_hosted_call(monkeypatch):
    async def fail(*args): raise AssertionError('no hosted inference')
    monkeypatch.setattr(sc, 'decide', fail)
    small = items()[:3]
    result = asyncio.run(recall.prepare(DB(), run(), lambda: (small, None)))
    assert result['status'] == 'skipped' and len(result['items']) == 3
    def cannot_save(*args): raise RuntimeError('db unavailable')
    monkeypatch.setattr(recall, '_freeze', cannot_save)
    assert asyncio.run(recall.prepare(DB(), run(), lambda: (small, None))) is None
