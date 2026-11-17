import copy
import json
from pathlib import Path

import pytest
import ui_app_preview as preview
import ui_apps


def test_server_read_preview_resolves_authorized_names_but_supplied_mode_is_inert(monkeypatch):
    import contextvars
    from types import SimpleNamespace
    import ui_app_files
    from app_actions import ActionError
    actor = contextvars.ContextVar('preview-test-actor', default='outside')
    server = SimpleNamespace(_SESSION_SUB=actor, tool_run_sql=lambda *a, **k: pytest.fail('No SQL declared'))
    calls = []
    def read(host, subject, ident):
        assert host is server and subject == actor.get() == 'tech@example.test'
        calls.append(ident)
        if ident == 'private':
            raise ActionError('NOT_FOUND', 'File not found', 404)
        return {'name':'inspection.jpg'}
    monkeypatch.setattr(ui_app_files, 'read', read)
    doc = {'schema':'calliope.app.v1', 'data':{'state':{}, 'files':['allowed','private'], 'names':{}},
           'components':[{'id':'root','component':'CAttachments','label':'Saved evidence',
                          'value':{'path':'/files'},'names':{'path':'/names'},'readOnly':True}]}
    supplied = preview.request_snapshot(server, doc, {}, 'tech@example.test')
    assert not calls and supplied['messages'][2]['updateDataModel']['value']['names'] == {}
    result = preview.request_snapshot(server, doc, {'mode':'server-read'}, 'tech@example.test')
    model = result['messages'][2]['updateDataModel']['value']
    assert model['names'] == {'allowed':'inspection.jpg','private':'Attachment unavailable'}
    assert result['preview']['data_sha256'] == preview.digest(model)
    assert result['preview']['definition_sha256'] == preview.digest(doc)
    assert actor.get() == 'outside' and doc['data']['names'] == {}


@pytest.fixture
def doc():
    return json.loads((Path(__file__).parent / 'examples/ui-apps/service-work-orders.json').read_text())


def test_canonical_definition_is_previewed_without_rewrite_or_reads(doc):
    result = preview.snapshot(doc)
    assert result['preview']['definition_sha256'] == preview.digest(doc)
    assert result['preview']['ready']
    assert all(not q['executed'] for q in result['queries'].values())
    assert result['receipt'] is None
    assert result['messages'][1]['updateComponents']['components'] == doc['components']


def test_actions_expose_resolved_inputs_without_writes_or_clearing_draft(doc):
    state = copy.deepcopy(doc['data']['state'])
    state['note'] = 'Measured superheat, ready for review'
    result = preview.snapshot(doc, state=state, event='save_note', context={'id': 'WO-1001', 'version': 1})
    assert result['receipt'] is None
    assert result['preview_event']['simulated']
    assert 'Measured superheat' in json.dumps(result['preview_event']['inputs'])
    assert result['messages'][2]['updateDataModel']['value']['state']['note'] == state['note']


def test_revision_guard_and_read_authority(doc):
    with pytest.raises(ValueError, match='revision changed'):
        preview.snapshot(doc, expected_hash='old')
    with pytest.raises(ValueError, match='authorized'):
        preview.snapshot(doc, mode='server-read')


def test_read_failure_is_not_ready_and_dependencies_are_not_run():
    doc = {'schema': 'calliope.app.v1', 'data': {'state': {}, 'a': 'old', 'b': 'old'},
           'components': [{'id': 'root', 'component': 'CText', 'text': {'path': '/b'}}],
           'queries': {'a': {'sql': 'SELECT 1', 'path': '/a', 'shape': 'value'},
                       'b': {'sql': 'SELECT $1', 'params': [{'path': '/a'}], 'path': '/b', 'shape': 'value'}}}
    calls = []
    def query(sql, **opts):
        calls.append(sql)
        return {'error': {'code': 'OFFLINE', 'message': 'Unavailable'}}
    result = preview.snapshot(doc, mode='server-read', query_runner=query)
    assert len(calls) == 1
    assert not result['preview']['ready']
    assert result['queries']['b']['error']['code'] == 'DEPENDENCY_UNAVAILABLE'


def test_preview_uses_the_publication_composition_and_form_rules():
    doc = {'schema': 'calliope.app.v1', 'data': {'state': {'form': {'id': '', 'note': ''}},
           'record': {'id': 'A', 'note': 'Original'}, 'status': 'ready'},
           'components': [{'id': 'root', 'component': 'CStack', 'children': []}],
           'fragments': {'ready': {'root': 'label', 'components': [{'id': 'label', 'component': 'CText', 'text': {'path': '/state/form/note'}}]}},
           'switch': {'root': {'path': '/status', 'cases': {'ready': 'ready'}, 'default': 'ready'}},
           'forms': [{'path': '/state/form', 'source': '/record'}]}
    ui_apps.validate(doc)
    result = preview.snapshot(doc)
    assert result['messages'][2]['updateDataModel']['value']['state']['form']['note'] == 'Original'
    assert result['messages'][1]['updateComponents']['components'][1]['id'] == 'root.label'
    state = result['messages'][2]['updateDataModel']['value']['state']
    state['form']['note'] = 'Unsaved'
    next_result = preview.snapshot(doc, state=state)
    assert next_result['messages'][2]['updateDataModel']['value']['state']['form']['note'] == 'Unsaved'


def test_presets_have_bounds_and_explicit_dimensions_win():
    assert preview.viewport('phone') == {'preset': 'phone', 'width': 390, 'height': 844}
    assert preview.viewport('desktop', 1000, 800)['width'] == 1000
    with pytest.raises(ValueError): preview.viewport('phone', True, 800)
    with pytest.raises(ValueError): preview.viewport('phone', 20, 800)


def test_server_sql_values_use_the_published_json_representation():
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from uuid import UUID
    import ui_app_preview
    row={'amount':Decimal('123456789.123456789'),'day':date(2026,10,11),
         'time':datetime(2026,10,11,tzinfo=timezone.utc),
         'id':UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')}
    document={'schema':'calliope.app.v1','data':{'state':{},'row':{'amount':'0'}},
              'components':[{'id':'root','component':'CText','text':{'path':'/row/amount'}}],
              'queries':{'row':{'sql':'SELECT 1','path':'/row','shape':'first'}}}
    result=ui_app_preview.snapshot(document,mode='server-read',actor='alice',
        query_runner=lambda *a,**kw:{'rows':[row],'row_count':1,'elapsed_ms':Decimal('2.5')})
    model=result['messages'][2]['updateDataModel']['value']
    assert result['preview']['ready']
    assert model['row']==json.loads(json.dumps(row,default=str))
    assert model['row']['amount']=='123456789.123456789'
    assert result['preview']['data_sha256']==ui_app_preview.digest(model)
    json.dumps(result,allow_nan=False)
