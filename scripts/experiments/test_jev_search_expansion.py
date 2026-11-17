from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import freeze_search_expansions as freeze
import jev_search_expansion_trial as trial


def fixture():
    c={'key':'D01','doc_id':10,'title':'T','source':'S','excerpts':[{'chunk_idx':2,'text':'Original'}]}
    packet=freeze.expanded_packet(c,{'chunks':[{'idx':2,'chunk':'Original'},{'idx':3,'chunk':'New'}]}, {'chunks':[{'idx':0,'chunk':'Update'}]})
    return {'id':'case','query':'Search','filters':{},'candidates':[c],
            'base_result':{'hits':[{'doc_id':10,'chunk':'Original'}],'documents':[{'related':['keep me']}],'count':1},
            'expansions':{'D01':{'packet':packet}}}


def test_selector_sees_only_tool_input_and_original_excerpts():
    c=fixture();c['conversation']='PRIVATE CHAT';c['future']='FUTURE';c['expansions']['D01']['packet']['chunks'][0]['text']='SECRET EXPANSION'
    s=trial.selector_payload(c)['state']
    assert set(s)=={'query','filters','sources','operation'}
    assert 'SECRET EXPANSION' not in str(s) and 'PRIVATE CHAT' not in str(s) and 'FUTURE' not in str(s)


def test_enrichment_preserves_every_original_field_without_mutation():
    c=fixture();before=deepcopy(c);out=trial.enrich(c,['D01'])
    assert c==before
    assert {k:out[k] for k in c['base_result']}==c['base_result']
    assert [x['chunk_idx'] for x in out['expanded_documents'][0]['chunks']]==[0,3]
    assert trial.enrich(c,[])==c['base_result']


@pytest.mark.parametrize('keys',[['UNKNOWN'],['D01','D01'],['D01','D02','D03']])
def test_invalid_source_and_budget_fail_closed(keys):
    with pytest.raises(ValueError):trial.enrich(fixture(),keys)


def test_whole_chunk_budget_and_head_priority():
    c=fixture()['candidates'][0]
    result=freeze.expanded_packet(c,{'chunks':[{'idx':1,'chunk':'x'*11000},{'idx':3,'chunk':'neighbor'}]}, {'chunks':[{'idx':0,'chunk':'update'}]})
    assert result['omitted_chunk_indices']==[1]
    assert [x['chunk_idx'] for x in result['chunks']]==[0,3]
    assert result['added_chars']<=10000


def test_selector_can_abstain_without_filling_expansion_slots():
    c=fixture();answer={'type':'choice','choice':'unrelated','probabilities':{k:float(k=='unrelated') for k in trial.CRITERIA}}
    assert trial.select({'response':{'answers':{'D01':answer}}},c)==[]


def test_reviewer_cannot_cite_original_chunk_as_new_evidence():
    c=fixture();review={'preferred':['A'],'branches':{'A':{'utility':'material_gain','novel_facts':[{'source_key':'D01','chunk_idx':2,'fact':'Original'}]}}}
    with pytest.raises(ValueError,match='unavailable'):
        trial.validate_review(review,{'A':'jev'},{'jev':['D01']},c)
