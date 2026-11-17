import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest
from starlette.requests import Request

import desktop_decisions as decisions
from desktop_decision_token import issue, verify


def test_credentials_expire_and_cannot_be_changed_or_signed_with_another_key():
    token = issue('private', 'run', 'job', 5, 60)
    assert verify('private', token)['generation'] == 5
    for bad_key, bad_token in [('other', token), ('private', token[:-1] + 'z'),
                                ('private', issue('private', 'run', 'job', 5, -1))]:
        with pytest.raises(ValueError):
            verify(bad_key, bad_token)


def test_chooser_candidates_are_choices_not_executable_code():
    body = {'goal': 'Export CSV', 'observation': 'Export and Cancel buttons',
            'options': [{'id': 'a', 'label': 'Export CSV', 'action': 'click'}, {'id': 'b', 'label': 'Cancel'}]}
    assert decisions.upstream(body)[0] == 'decisions'
    assert decisions.upstream({**body,'provider':'cua-s1'})[0] == 'computer_decide'
    name, payload = decisions.upstream({**body, 'provider': 'jev'})
    assert name == 'decisions' and payload['inputs'][0]['questions']['next_action']['type'] == 'choice'
    with pytest.raises(ValueError):
        decisions.upstream({**body, 'provider': 'jev', 'screenshot': 'png'})
    with pytest.raises(ValueError):
        decisions.upstream({**body, 'options': [body['options'][0]] * 2})


def test_minimal_candidates_satisfy_the_cua_scorer_contract():
    body = {'goal': 'Save the edited document', 'observation': 'Save and Discard toolbar',
            'options': [{'id': 'save', 'label': 'Save'}, {'id': 'discard', 'label': 'Discard'}]}
    _, payload = decisions.upstream({**body,'provider':'cua-s1'})
    assert payload['inputs'][0]['options'] == [
        {'id': 'save', 'label': 'Save', 'role': 'option', 'action': 'select'},
        {'id': 'discard', 'label': 'Discard', 'role': 'option', 'action': 'select'}]
    for bad in ({'label': ''}, {'label': 'x' * 1001}, {'role': None}, {'action': ['click']}, {'entity_id': 7}):
        with pytest.raises(ValueError):
            decisions.upstream({**body, 'options': [{**body['options'][0], **bad}, body['options'][1]]})


@pytest.mark.parametrize('takeover', [False, True])
def test_routed_result_is_recorded_but_discarded_after_handoff(monkeypatch, takeover):
    rid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    state = {'active_run': rid, 'mode': 'agent', 'generation': 2,
             'jobs': {jid: {'run_id': rid, 'status': 'running', 'generation': 2}}}
    events, calls = {}, []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            self.result = {'requester': 'person@example.com'} if 'SELECT requester' in query else events.get(params[1])
            return self
        def fetchone(self): return self.result
    def append(conn, run, actor, kind, payload, key):
        return events.setdefault(key, {'payload': deepcopy(payload), 'event_type': kind})
    async def runtime(*args): return deepcopy(state)
    async def transport(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer server-only-key'
        assert request.url.path == '/b/decisions/predict'
        assert b'computer-decision' not in request.content
        if takeover:
            state.update(mode='human', generation=3)
        return httpx.Response(200, json={'outputs': [{'answers': {'next_action': {'choice':'export'}}}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(decisions.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(decisions.access, 'append_event', append)
    monkeypatch.setenv('RVBBIT_CLOVER_KEY', 'server-only-key')
    gateway = SimpleNamespace(config=SimpleNamespace(desktop_enabled=True, desktop_control_key='private'),
                              conn_factory=Connection, runtime=runtime)
    route = decisions.Decisions(gateway).route
    token = issue('private', rid, jid, 2, 60)
    request_id = str(uuid.uuid4())
    body = {'request_id': request_id, 'goal': 'Export', 'observation': 'Two buttons',
            'options': [{'id': 'export', 'label': 'Export'}, {'id': 'cancel', 'label': 'Cancel'}]}
    def request():
        async def receive(): return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
        return Request({'type': 'http', 'method': 'POST', 'path': '/decide',
                        'headers': [(b'authorization', ('Bearer ' + token).encode())]}, receive)
    response = asyncio.run(route(request()))
    assert response.status_code == (409 if takeover else 200)
    assert events['decision-result:' + request_id]['payload']['result']['selected_id'] == 'export'
    repeated = asyncio.run(route(request()))
    assert repeated.status_code == response.status_code and len(calls) == 1
    if not takeover:
        body['goal'] = 'Different goal'
        assert asyncio.run(route(request())).status_code == 409


def test_chooser_follows_shared_self_hosted_gateway(monkeypatch):
    monkeypatch.delenv('CALLIOPE_COMPUTER_CHOOSER_BASE',raising=False)
    monkeypatch.delenv('RVBBIT_CLOVER_BASE_URL',raising=False)
    monkeypatch.setenv('RVBBIT_CLOVER_OPENAI_BASE_URL','')
    assert decisions.chooser_base() == 'http://clover:8090'
    monkeypatch.setenv('RVBBIT_CLOVER_OPENAI_BASE_URL','http://clover:8090/v1/')
    assert decisions.chooser_base() == 'http://clover:8090'
    monkeypatch.setenv('RVBBIT_CLOVER_BASE_URL','http://other-clover:8090/')
    assert decisions.chooser_base() == 'http://other-clover:8090'
    monkeypatch.setenv('CALLIOPE_COMPUTER_CHOOSER_BASE','https://specialist.example/')
    assert decisions.chooser_base() == 'https://specialist.example'
