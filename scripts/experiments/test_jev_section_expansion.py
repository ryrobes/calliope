from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import freeze_section_expansions as freeze
import jev_section_expansion_trial as trial


def fixture():
    doc = {'doc_id': 10, 'title': 'T', 'source': 'S', 'excerpts': [{'chunk_idx': 3, 'text': 'Original'}]}
    rows = [{'idx': 0, 'chunk': 'Unrelated introduction'}, {'idx': 2, 'chunk': 'Earlier context'},
            {'idx': 3, 'chunk': 'Original'}, {'idx': 4, 'chunk': 'Useful next paragraph'}]
    meta = {'10': [{'idx': r['idx'], 'hash': freeze.digest(r['chunk'])} for r in rows]}
    case = {'id': 'test', 'query': 'Q', 'filters': {}, 'candidates': [doc],
            'base_result': {'hits': [{'doc_id': 10, 'chunk_idx': 3, 'chunk': 'Original'}], 'documents': ['retain']},
            'chunk_metadata': meta}
    case['sections'], case['excluded'] = freeze.sections(case, meta)
    case['windows'] = {'S01': {'chunks': rows[1:]}}
    return case


def test_metadata_skip_exhausted_without_opening_text():
    c = fixture()
    c['chunk_metadata']['10'] = [{'idx': 3, 'hash': freeze.digest('Original')}]
    selected, excluded = freeze.sections(c, c['chunk_metadata'])
    assert not selected
    assert excluded[0]['reason'] == 'no_unseen_chunks_in_window'


def test_identical_sources_merge_before_selection_and_retain_lineage():
    c = fixture()
    c['candidates'].append({**c['candidates'][0], 'doc_id': 11, 'source': 'Second'})
    c['chunk_metadata']['11'] = c['chunk_metadata']['10']
    choices, _ = freeze.sections(c, c['chunk_metadata'])
    assert len(choices) == 1
    assert choices[0]['equivalent_sources'][0]['doc_id'] == 11


def test_stale_original_hit_fails_instead_of_mixing_revisions():
    c = fixture()
    c['candidates'][0]['excerpts'][0]['text'] = 'Changed'
    with pytest.raises(ValueError, match='Source changed'):
        freeze.sections(c, c['chunk_metadata'])


def test_selection_has_no_new_text_or_conversation():
    c = fixture()
    c['conversation'] = 'PRIVATE CONVERSATION'
    state = str(trial.selector_payload(c))
    assert 'Useful next paragraph' not in state
    assert 'PRIVATE CONVERSATION' not in state
    assert 'new_hashes' not in state


def test_expansion_preserves_original_and_never_adds_automatic_head():
    c = fixture()
    before = deepcopy(c)
    out = trial.enrich(c, ['S01'])
    assert c == before
    assert {k: out[k] for k in c['base_result']} == c['base_result']
    assert [r['chunk_idx'] for r in out['expanded_documents'][0]['chunks']] == [2, 4]
    assert trial.enrich(c, ['S01'], out) == out


def test_overlap_and_known_text_are_not_added_again():
    c = fixture()
    c['candidates'][0]['excerpts'].append({'chunk_idx': 4, 'text': 'Useful next paragraph'})
    out = trial.enrich(c, ['S01'])
    assert [r['chunk_idx'] for r in out['expanded_documents'][0]['chunks']] == [2]
    c['sections'].append({**c['sections'][0], 'key': 'S02'})
    assert trial.nonredundant(c, ['S01', 'S02']) == ['S01']


def test_changed_or_out_of_window_text_rejected():
    c = fixture()
    c['windows']['S01']['chunks'][0]['chunk'] = 'Forged'
    with pytest.raises(ValueError, match='provenance'):
        trial.enrich(c, ['S01'])


@pytest.mark.parametrize('keys', [['unknown'], ['S01', 'S01'], ['S01', 'S02', 'S03']])
def test_unknown_duplicate_and_overbudget_keys_rejected(keys):
    with pytest.raises(ValueError):
        trial.enrich(fixture(), keys)


def test_lookup_bounds_and_answer_citations():
    with pytest.raises(ValueError, match='lookup'):
        trial.validate_step({'action': 'expand', 'sections': ['S01']}, ['S01'], [], 0)
    with pytest.raises(ValueError, match='unavailable'):
        trial.validate_step({'action': 'answer', 'answer': 'Claim [d11c0]', 'coverage': 'sufficient'}, [], ['d10c0'], 0)


def test_oversize_chunks_skipped_whole():
    c = fixture()
    c['windows']['S01']['chunks'][0]['chunk'] = 'x' * 11000
    c['chunk_metadata']['10'][1]['hash'] = freeze.digest('x' * 11000)
    out = trial.enrich(c, ['S01'])['expanded_documents'][0]
    assert out['omitted_chunk_indices'] == [2]
    assert [r['chunk_idx'] for r in out['chunks']] == [4]


def test_multiple_protocol_actions_are_not_silently_discarded():
    receipt = {'response': {'choices': [{'finish_reason': 'stop', 'message': {
        'content': '{"action":"expand"}\n{"action":"answer"}'}}]}}
    with pytest.raises(ValueError):
        trial.parse(receipt)


def test_missing_provider_cost_is_not_free():
    assert trial.usage_total([{'cost': 0.1}, {}], 'cost') is None
    assert trial.usage_total([{'cost': 0.1}, {'cost': 0.2}], 'cost') == pytest.approx(0.3)
