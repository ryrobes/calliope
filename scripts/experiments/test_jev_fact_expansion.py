from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import freeze_section_expansions as freeze
import jev_fact_expansion_trial as trial


def fixture(n=1):
    case = {'id':'test','query':'Q','filters':{},'candidates':[], 'chunk_metadata':{},'windows':{},
            'base_result':{'hits':[],'documents':['Preserve all breadcrumbs']}}
    for i in range(n):
        doc = {'doc_id':10+i,'title':f'Doc {i}','source':'S',
               'excerpts':[{'chunk_idx':3,'text':f'Original {i}'}]}
        rows = [{'idx':2,'chunk':f'Before {i}'},{'idx':3,'chunk':f'Original {i}'},{'idx':4,'chunk':f'After {i}'}]
        case['candidates'].append(doc)
        case['base_result']['hits'].append({'doc_id':10+i,'chunk_idx':3,'chunk':f'Original {i}'})
        case['chunk_metadata'][str(10+i)] = [{'idx':r['idx'],'hash':freeze.digest(r['chunk'])} for r in rows]
        case['windows'][f'S{i+1:02}'] = {'chunks':rows}
    case['sections'],case['excluded'] = freeze.sections(case,case['chunk_metadata'])
    return case


def test_pool_never_reads_unfetched_fifth_window():
    c = fixture(5)
    c['windows']['S05'] = {'chunks':None}
    p = trial.build_pool(c)
    assert p['windows'] == ['S01','S02','S03','S04']
    assert len(p['chunks']) == 8


def test_selector_gets_real_new_text_but_not_conversation():
    c = fixture(); c['conversation']='PRIVATE CHAT'
    state = trial.selector_payload(c,trial.build_pool(c))['state']
    assert 'Before 0' in str(state) and 'After 0' in str(state)
    assert 'PRIVATE CHAT' not in str(state)


def test_pool_whole_chunks_and_round_robin():
    c = fixture(4)
    p = trial.build_pool(c)
    assert [v['doc_id'] for v in p['chunks'][:4]] == [10,11,12,13]
    huge = 'x'*13000
    c['windows']['S01']['chunks'][0]['chunk'] = huge
    c['chunk_metadata']['10'][0]['hash'] = freeze.digest(huge)
    p = trial.build_pool(c)
    assert huge not in str(p['chunks'])
    assert p['omissions'] and p['candidate_chars'] <= 12000


def test_original_preserved_and_rejected_chunks_still_fetchable():
    c = fixture(); before=deepcopy(c); p=trial.build_pool(c)
    out=trial.attach(c,p,['N01'])
    assert c==before
    assert {k:out[k] for k in c['base_result']}==c['base_result']
    assert trial.covered_sections(c,out)==[]
    assert trial.covered_sections(c,trial.attach(c,p,['N01','N02']))==['S01']


@pytest.mark.parametrize('keys',[['UNKNOWN'],['N01','N01'],['N01','N02','N03','N04','N05']])
def test_invalid_or_overbudget_keys_rejected(keys):
    c=fixture(4)
    with pytest.raises(ValueError):trial.attach(c,trial.build_pool(c),keys)


def test_actual_text_provenance_checked_before_selecting():
    c=fixture(); c['windows']['S01']['chunks'][0]['chunk']='Modified'
    with pytest.raises(ValueError,match='provenance'):trial.build_pool(c)


def test_chunk_selector_can_abstain_and_reject_nan():
    p=trial.build_pool(fixture())
    answers={c['key']:{'type':'choice','choice':'unrelated','probabilities':{k:float(k=='unrelated') for k in trial.CRITERIA}} for c in p['chunks']}
    assert trial.select({'response':{'answers':answers}},p)==[]
    answers['N01']['probabilities']['direct']=float('nan')
    with pytest.raises(ValueError,match='score'):trial.select({'response':{'answers':answers}},p)


def test_prefetch_cost_counted_even_if_no_chunks_attached():
    assert trial.read_metrics(['S01','S02'],['S02','S03']) == {
        'upfront_window_reads':2,'followup_requests':2,'followup_cache_hits':1,
        'additional_window_reads':1,'total_window_reads':3}


def test_duplicates_across_windows_not_sent_twice():
    c=fixture(2)
    c['windows']['S02']['chunks'][0]['chunk']='Before 0'
    c['chunk_metadata']['11'][0]['hash']=freeze.digest('Before 0')
    p=trial.build_pool(c)
    assert len(p['chunks'])==3
    assert p['chunks'][0]['also_in_windows']==['S02']


def test_pack_keeps_presentation_order_and_whole_output_budget():
    p=trial.build_pool(fixture(4))
    assert trial.pack(p,['N04','N02'])==['N02','N04']
    p['chunks'][0]['text']='x'*6000
    assert trial.pack(p,['N01','N02'])==['N02']
