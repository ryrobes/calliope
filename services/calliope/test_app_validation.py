import copy
import contextvars
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import app_validation as validation
import ui_app_preview


@pytest.fixture
def doc():
    return json.loads((Path(__file__).parent/'examples/ui-apps/northstar-company/follow-up-reason/northstar-my-visits.app.json').read_text())


def context():
    return {'slug':'visits','version':2,'location':'[["/state/selected","V1"],["/state/page","detail"]]',
        'records':[{'form':'/state/draft','id':'V1'}],
        'fields':[{'id':'notes','path':'/state/draft/notes','value':'Unsent notes','saved_value':'Saved notes','saved_available':True}],
        'visible':[{'id':'visit-title','content':{'text':'Captured customer'}},
                   {'id':'address','content':{'text':'Captured address'}}]}


def test_replay_restores_navigation_and_draft_without_changing_definition(doc):
    before=copy.deepcopy(doc)
    data,checks,provenance=validation.replay_model(doc,context())
    snapshot=ui_app_preview.snapshot(doc,data=data,state=data['state'])
    model=snapshot['messages'][2]['updateDataModel']['value']
    assert model['state']['page']=='detail'
    assert model['state']['selected']=='V1'
    assert model['state']['draft']['notes']=='Unsent notes'
    assert model['model']['record']['notes']=='Saved notes'
    assert model['model']['record']['title']=='Captured customer'
    assert not provenance['complete'] and 'defaults' in provenance['notice']
    assert checks==[{'path':'/state/draft/notes','equals':'Unsent notes'},
        {'component':'notes','property':'value','equals':'Unsent notes'}]
    assert snapshot['preview']['definition_sha256']==ui_app_preview.digest(before)
    assert doc==before


def test_replay_rejects_forged_binding_and_navigation(doc):
    value=context();value['fields'][0]['path']='/model/record/notes'
    value['location']='[["/model/record/title","forged navigation"]]'
    value['visible']=[]
    data,checks,provenance=validation.replay_model(doc,value)
    assert not checks and 'field:notes' in provenance['unmapped']
    assert data['model']['record']['notes']==doc['data']['model']['record']['notes']
    assert data['model']['record']['title']==doc['data']['model']['record']['title']


def test_candidate_keeps_additive_defaults_and_reports_removed_bindings(doc):
    data,_,_=validation.replay_model(doc,context())
    candidate=copy.deepcopy(doc['data']);candidate['state']['new_field']='new default'
    del candidate['state']['draft']['notes']
    merged,removed=validation.overlay(candidate,data)
    assert merged['state']['new_field']=='new default'
    assert removed==['/state/draft/notes']
    _,results=validation.evaluate(doc,data,[{'component':'visit-title','property':'text','equals':'Captured customer'},
        {'path':'/state/draft/notes','equals':'changed'}])
    assert [r['passed'] for r in results]==[True,False]


def test_rebound_field_fails_captured_component_assertion(doc):
    data,checks,_=validation.replay_model(doc,context())
    next(n for n in doc['components'] if n['id']=='notes')['value']={'path':'/state/draft/review_note'}
    _,results=validation.evaluate(doc,data,checks)
    assert results[0]['passed'] and not results[1]['passed']


def test_simulated_action_compares_payload_without_write_or_draft_reset():
    doc={'schema':'calliope.app.v1','data':{'state':{'note':'unsent'}},
        'components':[{'id':'root','component':'CText','text':'Test'}],
        'events':{'save':{'action':'save_note','inputs':{'note':{'path':'/state/note'}}}}}
    snapshot,results=validation.evaluate(doc,doc['data'],[{'event':'save','equals':{'note':'unsent'}}])
    assert results[0]['passed'] and snapshot['receipt'] is None
    assert snapshot['messages'][2]['updateDataModel']['value']['state']['note']=='unsent'


def test_private_scenario_and_replay_never_load_without_app_access(monkeypatch):
    monkeypatch.setattr(validation.app_revisions,'ensure',lambda *a:None)
    server=SimpleNamespace(_conn=MagicMock(),_artifact_require_view=MagicMock(side_effect=ValueError('denied')))
    with pytest.raises(ValueError,match='denied'):
        validation.scenario(server,'viewer','private','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    server._conn.assert_not_called()


def test_check_records_exact_candidate_and_failure_even_without_screenshots(doc,tmp_path,monkeypatch):
    data,checks,provenance=validation.replay_model(doc,context())
    server=SimpleNamespace(_conn=MagicMock(),_live_app_capture_root=lambda:tmp_path)
    monkeypatch.setattr(validation.app_revisions,'ensure',lambda *a:None)
    monkeypatch.setattr(validation,'load_version',lambda *a:({}, {'version':3,'kind':'a2ui','source_files':{'app.json':json.dumps(doc)}}))
    monkeypatch.setattr(validation,'scenario',lambda *a:{'data':data,'checks':checks,'provenance':provenance})
    candidate=copy.deepcopy(doc);candidate['title']='Candidate'
    result=validation.check_revision(server,'author','visits',scenario_id='case',document=candidate,captures=False)
    assert result['ready'] and result['candidate']
    assert result['definition_sha256']==ui_app_preview.digest(candidate)
    assert result['provenance']['complete'] is False and result['device_acceptance'] is False
    assert result['checks'][0]['passed']
    checks[0]['equals']='Must fail'
    result=validation.check_revision(server,'author','visits',scenario_id='case',captures=False)
    assert result['ready'] is False


def test_html_check_uses_isolated_host_and_restores_actor(tmp_path,monkeypatch):
    monkeypatch.setattr(validation.app_revisions,'ensure',lambda *a:None)
    monkeypatch.setattr(validation,'load_version',lambda *a:({}, {'version':2,'kind':'html','html':'saved'}))
    actor=contextvars.ContextVar('actor',default='outside')
    def capture(source,path,*args,**kwargs):
        assert source=='candidate' and actor.get()=='author'
        assert callable(kwargs['app_read_runner'])
        path.write_bytes(b'PNG')
        return {'readiness':{'ready':True}}
    server=SimpleNamespace(_conn=MagicMock(),_live_app_capture_root=lambda:tmp_path,_SESSION_SUB=actor,
        _capturable_dashboard_html=lambda slug,row:row['html'],_capture_html_with_playwright=capture)
    result=validation.check_revision(server,'author','dashboard',html_source='candidate')
    assert result['ready'] and result['definition_sha256']==ui_app_preview.digest('candidate')
    assert set(result['captures'])=={'web-phone','web-desktop'} and actor.get()=='outside'


def test_complete_supplied_fixture_does_not_need_request_snapshot(doc,monkeypatch):
    monkeypatch.setattr(validation.app_revisions,'ensure',lambda *a:None)
    monkeypatch.setattr(validation,'load_version',lambda *a:({}, {'version':3,'kind':'a2ui','source_files':{'app.json':json.dumps(doc)}}))
    server=SimpleNamespace(_conn=MagicMock())
    result=validation.save_scenario(server,'author','visits','Empty queue',data=doc['data'])
    assert result['provenance']['kind']=='supplied-fixture'
