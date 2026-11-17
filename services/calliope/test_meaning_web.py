"""Meaning discussions preserve exact evidence without executing a chat or SQL."""
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest
import calliope
import meaning_web as web
from app_actions import ActionError

REF = {'kind': 'meaning', 'area': 'subject:service', 'node': 'metric:cost'}
PACKET = {'node': {'id': REF['node'], 'kind': 'metric', 'title': 'Service cost',
                  'reference': REF, 'version': 3, 'status': 'implied'},
          'detail': {'sql': 'SELECT sum(cost) FROM jobs', 'defaults': {'days': 30}},
          'edges': [], 'as_of': '2026-10-09'}


@pytest.fixture
def harness(monkeypatch):
    h = SimpleNamespace(old=None, calls=[], sessions=[], surfaces=[], deleted=[])
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def transaction(self): return nullcontext()
        def execute(self, sql, params=()):
            h.calls.append((sql, params))
            return SimpleNamespace(fetchone=lambda: h.old)
    h.server = SimpleNamespace(_conn=Conn)
    monkeypatch.setattr(web.graph, 'inspect_object', lambda *a: copy.deepcopy(PACKET))
    monkeypatch.setattr(calliope.CalliopeConfig, 'from_env', lambda: None)
    def create(config, conn, actor, title):
        h.sessions.append((actor, title))
        return {'id': str(uuid.uuid4()), 'hermes_session_id': 'test-hermes'}
    def insert(conn, sid, tid, projected):
        h.surfaces.extend(projected)
        f = projected[0]
        return [{'id': str(uuid.uuid4()), 'session_id': sid, **f}]
    monkeypatch.setattr(calliope, '_create_session_record_sync', create)
    monkeypatch.setattr(calliope, '_insert_surfaces', insert)
    monkeypatch.setattr(calliope, '_hermes_json_sync', lambda *a: h.deleted.append(a))
    h.body = {'reference': REF, 'intent': 'ask', 'request_key': str(uuid.uuid4())}
    return h


@pytest.mark.parametrize('intent', ['ask', 'promote', 'disagree'])
def test_handoff_is_owned_exact_evidence_and_unsent_prompt(harness, intent):
    h = harness
    result = web.handoff(h.server, 'person@example.com', {**h.body, 'intent': intent})
    assert h.sessions == [('person@example.com', 'Meaning · Service cost')]
    f = h.surfaces[0]
    assert f['payload']['meaning_context']['detail'] == PACKET['detail']
    assert f['payload']['meaning_context']['node']['version'] == 3
    assert f['source']['reference'] == REF and f['source']['intent'] == intent
    assert f['payload']['meaning_context']['intent'] == intent
    query = parse_qs(urlsplit(result['url']).query)
    assert query['surface'] == query['meaning_pin'] == [result['surface']['id']]
    assert query['session'] == [result['session']['id']]
    assert query['prompt'] and 'autorun' not in query
    assert all('UPDATE rvbbit.metric' not in sql and 'SELECT sum(cost)' not in sql for sql, _ in h.calls)
    assert any("'evidence_search'" in sql for sql, _ in h.calls)
    assert not result['reused']


def test_unauthorized_reference_is_rejected_before_session_creation(harness, monkeypatch):
    def denied(*a): raise ActionError('NOT_FOUND', 'Not available', 404)
    monkeypatch.setattr(web.graph, 'inspect_object', denied)
    with pytest.raises(ActionError): web.handoff(harness.server, 'actor', harness.body)
    assert not harness.sessions and not harness.calls


@pytest.mark.parametrize('extra', [{'sql': 'DROP'}, {'reference': {**REF, 'sql': 'DROP'}},
                                  {'intent': 'execute'}, {'request_key': 'invalid'}])
def test_client_cannot_supply_definition_or_unknown_intent(harness, extra):
    with pytest.raises((ValueError, TypeError)):
        web.handoff(harness.server, 'actor', {**harness.body, **extra})
    assert not harness.sessions


def test_retries_reuse_owned_surface_and_conflicting_key_fails(harness):
    h = harness
    first = web.handoff(h.server, 'actor', h.body)
    h.old = {'id': first['surface']['id'], 'session_id': first['session']['id'], 'source': h.surfaces[0]['source']}
    second = web.handoff(h.server, 'actor', h.body)
    assert second['reused'] and second['session'] == first['session']
    assert len(h.sessions) == 1
    lookup = next((sql, p) for sql, p in h.calls if 'JOIN rvbbit.calliope_sessions' in sql)
    assert 's.owner_email=%s' in lookup[0] and lookup[1][0] == 'actor'
    with pytest.raises(ActionError) as e:
        web.handoff(h.server, 'actor', {**h.body, 'intent': 'disagree'})
    assert e.value.status == 409 and len(h.sessions) == 1


def test_relationship_must_touch_selected_object(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(web.graph, 'graph', lambda *a: {'edges': [{'id': 'edge', 'from': 'other', 'to': 'unrelated'}]})
    with pytest.raises(ActionError): web.handoff(h.server, 'actor', {**h.body, 'edge': 'edge'})
    assert not h.sessions
    edge = {'id': 'edge', 'from': REF['node'], 'to': 'relation:jobs', 'evidence': {'source': 'registry'}}
    monkeypatch.setattr(web.graph, 'graph', lambda *a: {'edges': [edge]})
    web.handoff(h.server, 'actor', {**h.body, 'edge': 'edge'})
    assert h.surfaces[0]['payload']['meaning_context']['relationship_focus'] == edge


def test_partial_failure_cleans_up_only_new_session(harness, monkeypatch):
    def failed(*a): raise RuntimeError('surface save failed')
    monkeypatch.setattr(calliope, '_insert_surfaces', failed)
    with pytest.raises(RuntimeError): web.handoff(harness.server, 'actor', harness.body)
    assert any('DELETE FROM rvbbit.calliope_sessions' in sql and 'owner_email=%s' in sql for sql, _ in harness.calls)
    assert harness.deleted == [(None, 'DELETE', '/api/sessions/test-hermes')]


def test_selected_surface_carries_definition_as_untrusted_evidence():
    sid = str(uuid.uuid4())
    row = {'id': sid, 'kind': 'evidence', 'title': 'Service cost', 'payload': {'mode': 'meaning_object', 'meaning_context': PACKET},
           'source': {'origin': 'meaning_map'}, 'created_at': '2026-10-09'}
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): return SimpleNamespace(fetchall=lambda: [row])
    _, selected = calliope._compact_surface_context(Conn, 'session', sid)
    assert selected['meaning_object']['node']['reference'] == REF
    assert selected['meaning_object']['detail']['sql'] == PACKET['detail']['sql']
    assert 'untrusted evidence' in calliope._selected_surface_context_text(selected)
    compact, selected = calliope._compact_surface_context(Conn, 'session', None)
    assert not selected and compact[0].get('meaning_object') is None


def test_web_routes_authentication_mime_origin_and_size(monkeypatch):
    from contextvars import ContextVar
    from concurrent.futures import ThreadPoolExecutor
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    import auth
    import warehouse_theme
    routes = []
    class Router:
        def custom_route(self, path, methods):
            def register(fn):
                routes.append(Route(path, fn, methods=methods)); return fn
            return register
    def actor(request):
        if request.headers.get('x-test-user') == 'person':
            return 'person', {'sub': 'person'}, None
        return None, None, JSONResponse({'error': {'code': 'AUTH_REQUIRED'}}, status_code=401)
    monkeypatch.setattr(auth, 'background_layer', lambda *a, **kw: '')
    monkeypatch.setattr(warehouse_theme, 'account_control', lambda *a, **kw: '<span>Account</span>')
    captured = []
    import meaning_data
    data_calls = []
    monkeypatch.setattr(meaning_data, 'explore', lambda s,a,b: data_calls.append((a,b)) or {'rows': []})
    monkeypatch.setattr(web, 'handoff', lambda s,a,b: captured.append((a,b)) or {'ok': True})
    # Business Logic has its own real-Postgres route tests; this harness isolates Meaning.
    import business_logic
    monkeypatch.setattr(business_logic, 'ensure', lambda factory: None)
    with ThreadPoolExecutor(1) as pool:
        server = SimpleNamespace(_CONTROL_EXECUTOR=pool, _SESSION_SUB=ContextVar('test_subject'), _conn=lambda: None)
        web.graph.register(Router(), server, actor, lambda body,status=200: JSONResponse(body,status_code=status))
        with TestClient(Starlette(routes=routes)) as client:
            assert client.get('/meaning', follow_redirects=False).status_code == 302
            assert client.get('/meaning/meaning.js').status_code == 401
            assert client.post('/api/calliope/meaning/discuss', json={}).status_code == 401
            assert client.post('/api/calliope/meaning/data', json={}).status_code == 401
            headers = {'x-test-user': 'person'}
            page = client.get('/meaning', headers=headers)
            assert page.status_code == 200 and '__MEANING_' not in page.text
            assert page.headers['cache-control'] == 'private, no-store'
            asset = client.get('/meaning/meaning-layout.mjs', headers=headers)
            assert asset.status_code == 200 and asset.headers['content-type'].startswith('text/javascript')
            # Versioned assets are immutable and compressed; the map's layout engine is 1.6 MB raw.
            version = page.text.split('meaning-desk.js?v=', 1)[1].split('"', 1)[0]
            engine = client.get('/meaning/meaning-elk.js?v=' + version, headers=headers)
            assert engine.headers['content-encoding'] == 'gzip' and 'immutable' in engine.headers['cache-control']
            assert engine.text.startswith('(function(f){') and 'data-meaning-version="' + version in page.text
            assert 'no-cache' in client.get('/meaning/meaning-elk.js?v=stale', headers=headers).headers['cache-control']
            assert client.get('/meaning/server.py', headers=headers).status_code == 404
            path = '/api/calliope/meaning/discuss'
            assert client.post(path, json={}, headers={**headers,'Origin':'https://untrusted.example'}).status_code == 403
            assert client.post(path, json={'data':'a'*8001}, headers=headers).status_code == 413
            assert not captured
            assert client.post(path, json={'intent':'ask'}, headers=headers).status_code == 200
            assert captured == [('person', {'intent':'ask'})]
            path = '/api/calliope/meaning/data'
            assert client.post(path, json={}, headers={**headers,'Origin':'https://untrusted.example'}).status_code == 403
            assert client.post(path, json={'data':'a'*8001}, headers=headers).status_code == 413
            assert not data_calls
            assert client.post(path, json={'reference':REF}, headers=headers).status_code == 200
            assert data_calls == [('person', {'reference':REF})]
            assert client.get('/meaning/meaning-data.mjs', headers=headers).headers['content-type'].startswith('text/javascript')
