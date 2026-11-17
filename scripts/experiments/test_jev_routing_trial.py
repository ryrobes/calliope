import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import jev_routing_trial as routing


def test_expected_label_and_alternative_packets_never_enter_model_state():
    case={'question':'q','context':'','evidence':[],'expected':'runtime','packets':'secret answer'}
    result=routing.state(case,'today')
    assert set(result)=={'question','context','evidence','as_of','available_routes'}


@pytest.mark.parametrize('label',['run_sql','delete_everything',None])
def test_unknown_routes_cannot_dispatch(label):
    receipt={'response':{'answers':{'next':{'type':'choice','choice':label,
        'probabilities':{r:0 for r in routing.ROUTES}}}}}
    with pytest.raises(ValueError,match='Unknown route'):
        routing.read_route(receipt,True)


def test_no_fabricated_result_for_unimplemented_search():
    assert routing.answer_evidence({'evidence':[]},'search_documents',{'packets':{}}) is None


def test_effect_evidence_takes_priority_without_truncating_body():
    effect={'id':'R01','chunk':'e'*17500}
    old={'id':'C01','chunk':'o'*1000}
    result=routing.answer_evidence({'evidence':[old]},'runtime',{'packets':{'runtime':[effect]}})
    assert result==[effect]


def test_document_expansion_cannot_inject_unrelated_prefetched_document():
    manifest={'packets':{'expand_document':[{'id':'D01','doc_id':42,'chunk':'private'}]}}
    assert routing.answer_evidence({'evidence':[{'doc_id':3}]},'expand_document',manifest) is None


def test_answer_route_preserves_exact_router_evidence_under_shared_cap():
    items=[{'id':f'C{i:02}','chunk':'fact'} for i in range(24)]
    case={'evidence':routing.bounded(items)}
    assert routing.answer_evidence(case,'answer',{})==items


def test_heuristic_reads_source_kind_in_addition_to_keywords():
    case={'question':'Which engine counts appear in telemetry?','evidence':[]}
    assert routing.heuristic(case)=='runtime'
    case['evidence']=[{'kind':'runtime'}]
    assert routing.heuristic(case)=='answer'
