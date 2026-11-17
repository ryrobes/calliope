"""Portable record contracts: identity, read projection, and authorization edges."""
import copy
import json
from types import SimpleNamespace

import pytest
import ui_apps
import ui_records
from app_actions import ActionError


@pytest.fixture
def doc():
    return {'schema': 'calliope.app.v1', 'title': 'Visits',
            'data': {'state': {'selected': 'A', 'page': 'field', 'draft': {'notes': ''}},
                     'visit': {'id': '', 'customer': '', 'status': ''}, 'other': []},
            'components': [{'id': 'root', 'component': 'CText', 'text': {'path': '/visit/customer'}}],
            'queries': {
                'visit': {'sql': 'SELECT id,customer,status FROM visits WHERE id=$1', 'params': [{'path': '/state/selected'}], 'path': '/visit', 'shape': 'first'},
                'expensive': {'sql': 'SELECT 42 AS answer', 'params': [], 'path': '/other'}},
            'navigation': {'paths': ['/state/page', '/state/selected']},
            'records': {'visit': {'id': {'path': '/visit/id'}, 'title': {'path': '/visit/customer'},
                'when': {'/state/page': 'field'},
                'open': {'/state/page': 'field', '/state/selected': {'recordId': True}},
                'summary': {'queries': ['visit'], 'components': [
                    {'id': 'root', 'component': 'CText', 'text': {'path': '/visit/status'}}]}}}}


def server(doc, rows=None):
    calls = []
    def sql(statement, **kwargs):
        calls.append(statement)
        return {'rows': rows if rows is not None else [{'id': 'B', 'customer': 'Harbor', 'status': 'Scheduled'}], 'row_count': 1}
    return SimpleNamespace(_load_live_app_version=lambda slug, version: ({'name': 'Test'}, {
        'kind': 'a2ui', 'version': version or 11, 'source_files': {'app.json': json.dumps(doc)}}),
        tool_run_sql=sql, calls=calls)


REF = {'slug': 'field', 'record': 'visit', 'id': 'B'}


def test_open_uses_server_navigation_and_original_record_id(doc):
    srv = server(doc)
    snap = ui_apps.snapshot(srv, 'field', 'viewer', {'record': REF})
    data = snap['messages'][2]['updateDataModel']['value']
    assert data['state']['selected'] == 'B'
    assert snap['records'] == [{'reference': REF, 'title': 'Harbor'}]
    assert snap['version'] == 11
    assert "'B'" in srv.calls[0]
    for body in ({'record': REF, 'state': doc['data']['state']}, {'record': REF, 'version': 11, 'event': 'save'}):
        with pytest.raises(ValueError): ui_apps.snapshot(srv, 'field', 'viewer', body)


def test_summary_is_small_fresh_and_runs_only_its_dependency_closure(doc, monkeypatch):
    grants = []
    monkeypatch.setattr(ui_records, 'allowed', lambda srv, actor, slug: grants.append((actor, slug)))
    srv = server(doc)
    snap = ui_records.summary(srv, 'viewer', REF)
    assert grants == [('viewer', 'field')]
    assert len(srv.calls) == 1 and 'FROM visits' in srv.calls[0]
    assert snap['messages'][2]['updateDataModel']['value'] == {}
    assert snap['messages'][1]['updateComponents']['components'][0]['text'] == 'Scheduled'
    assert snap['records'][0]['reference'] == REF
    doc['queries']['visit']['params'] = [{'path': '/other/0/answer'}]
    doc['data']['other'] = [{'answer': 'B'}]
    assert ui_records.dependencies(doc, ['visit']) == {'expensive', 'visit'}


def test_missing_record_never_opens_default_or_another_record(doc):
    for rows in ([], [{'id': 'A', 'customer': 'Wrong', 'status': 'Active'}]):
        with pytest.raises(ActionError) as error:
            ui_apps.snapshot(server(doc, rows), 'field', 'viewer', {'record': REF})
        assert error.value.status == 404
    srv = server(doc)
    srv.tool_run_sql = lambda *a, **k: {'error': {'code': 'BROKEN'}}
    with pytest.raises(ActionError) as error:
        ui_apps.snapshot(srv, 'field', 'viewer', {'record': REF})
    assert error.value.status == 503


def test_removed_contract_and_other_app_are_rejected(doc):
    doc['records'] = {}
    with pytest.raises(ActionError): ui_apps.snapshot(server(doc), 'field', 'viewer', {'record': REF})
    with pytest.raises(ValueError): ui_records.reference(REF, 'other')


@pytest.mark.parametrize('bad', [True, [], {}, None, '', 'x' * 513])
def test_bad_identity(bad):
    with pytest.raises(ValueError): ui_records.reference({**REF, 'id': bad})


@pytest.mark.parametrize('change', ['form_navigation', 'draft_identity', 'interactive_summary', 'missing_query', 'bad_key_query'])
def test_record_declaration_is_bounded_and_cannot_write(doc, change):
    record = doc['records']['visit']
    if change == 'form_navigation': record['open']['/state/draft/notes'] = 'write'
    if change == 'draft_identity': record['id'] = {'path': '/state/selected'}
    if change == 'interactive_summary': record['summary']['components'][0] = {'id': 'root', 'component': 'Button', 'text': 'Save'}
    if change == 'missing_query': record['summary']['queries'] = ['gone']
    if change == 'bad_key_query': record['summary']['queries'] = ['expensive']
    with pytest.raises(ValueError): ui_apps.validate(doc)


def test_changed_selection_and_wrong_page_do_not_offer_stale_pins(doc):
    data = copy.deepcopy(doc['data'])
    data['visit'].update(id='B', customer='Harbor')
    assert ui_records.links(doc, data, 'field', {}) == []
    data['state']['selected'] = 'B'
    assert ui_records.links(doc, data, 'field', {})[0]['reference'] == REF
    data['state']['page'] = 'other'
    assert ui_records.links(doc, data, 'field', {}) == []


def test_authorization_precedes_loading_queries(doc, monkeypatch):
    srv = server(doc)
    def deny(*args): raise ActionError('NOT_FOUND', 'Unavailable', 404)
    monkeypatch.setattr(ui_records, 'allowed', deny)
    with pytest.raises(ActionError): ui_records.summary(srv, 'other-user', REF)
    assert srv.calls == []
