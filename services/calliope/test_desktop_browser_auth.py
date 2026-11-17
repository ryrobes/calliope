"""Exercise delegated browser identity with real signed tokens and request cookies."""
import json
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

import auth
import desktop_browser_auth as browser_auth

PUBLIC = 'https://callie.example'
ACTOR = 'owner@example.com'
RID = str(uuid.uuid4())


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv('WAREHOUSE_PUBLIC_URL', PUBLIC)
    monkeypatch.setattr(auth, 'JWT_SECRET', 'unit-test-computer-key-never-shared')
    monkeypatch.setattr(auth, 'AUTH_MODE', 'shared')
    data = {'requester': ACTOR, 'status': 'active'}
    class Conn:
        def execute(self, query, args):
            assert args == (RID,)
            return SimpleNamespace(fetchone=lambda: data if data['status'] == 'active' else None)
    @contextmanager
    def factory():
        yield Conn()
    monkeypatch.setattr(browser_auth, '_conn_factory', factory)
    return data, factory


def token(**overrides):
    claims = {'typ': 'computer-session', 'sub': ACTOR, 'idt': ACTOR, 'mpd': True,
              'rid': RID, 'iat': int(time.time()), 'exp': int(time.time()) + 300,
              'aud': PUBLIC, 'iss': PUBLIC}
    return auth.jwt.encode({**claims, **overrides}, auth.JWT_SECRET, algorithm=auth.JWT_ALG)


def request(computer=None, host='callie.example'):
    # Always include a valid, DIFFERENT human identity to detect fallback.
    response = Response()
    auth.set_session(response, 'other-human@example.com', True)
    cookies = response.headers['set-cookie'].split(';')[0]
    if computer is not None:
        cookies += '; ' + browser_auth.COOKIE + '=' + computer
    return Request({'type': 'http', 'scheme': 'https', 'path': '/apps/private-fixture',
                    'headers': [(b'host', host.encode()), (b'cookie', cookies.encode())]})


def test_computer_identity_wins_and_human_browser_unchanged(store):
    session = auth.read_session_full(request(token()))
    assert session['identity'] == ACTOR and session['via'] == 'computer'
    assert session['computer_run_id'] == RID
    assert not session['name'] and not session['picture']
    assert auth.read_session_full(request())['identity'] == 'other-human@example.com'


@pytest.mark.parametrize('claims', [
    {'exp': int(time.time()) - 1}, {'aud': 'https://elsewhere.example'},
    {'iss': 'https://elsewhere.example'}, {'typ': 'session'},
    {'rid': 'not-a-run'}, {'idt': 'another@example.com'},
])
def test_invalid_computer_identity_never_falls_back_to_human(store, claims):
    assert auth.read_session_full(request(token(**claims))) is None


def test_wrong_host_forged_token_and_unavailable_database(store, monkeypatch):
    assert auth.read_session_full(request(token(), host='elsewhere.example')) is None
    assert auth.read_session_full(request('forged')) is None
    monkeypatch.setattr(browser_auth, '_conn_factory', lambda: (_ for _ in ()).throw(RuntimeError()))
    assert auth.read_session_full(request(token())) is None


@pytest.mark.parametrize('status', ['queued', 'finished', 'interrupted', 'failed'])
def test_only_active_run_can_authenticate(store, status):
    store[0]['status'] = status
    assert auth.read_session_full(request(token())) is None


def test_unmapped_role_remains_unmapped(store):
    result = auth.read_session_full(request(token(sub='rvbbit_guest', mpd=False)))
    assert result['mapped'] is False and result['sub'] == 'rvbbit_guest'


def test_renewal_uses_database_actor_and_only_control_channel_contains_token(store, monkeypatch):
    data, factory = store
    calls, events = [], []
    monkeypatch.setattr(browser_auth.access, 'sync_runs', lambda *args: None)
    monkeypatch.setattr(browser_auth.access, 'append_event', lambda *args: events.append(args))
    class Runtime:
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if path == 'status':
                return {'active_run': RID, 'runs': {RID: {'context': {'requester': 'guest-forgery'}}}}
            return {'status': 'ready', 'run_id': RID, 'expires_at': body['expires_at']}
    result = browser_auth.sync(factory, None, Runtime())
    payload = calls[-1][2]
    assert auth.read_session_full(request(payload['cookie']))['identity'] == ACTOR
    assert payload['cookie'] not in json.dumps(result) + json.dumps(events, default=str)
    assert result['expires_at'] - time.time() <= 300
    assert events[-1][3] == 'browser.authenticated'


def test_idle_never_provisions_queued_identity(store, monkeypatch):
    monkeypatch.setattr(browser_auth.access, 'sync_runs', lambda *args: None)
    assert browser_auth.sync(store[1], None, SimpleNamespace(request=lambda *args: pytest.fail()),
                             {'active_run': None, 'runs': {RID: {}}}) == {'status': 'idle'}


def test_stale_status_cannot_revive_finished_run_or_demote_active_run():
    import os
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ.get('COMPUTER_AUTH_TEST_DSN')
    if not dsn:
        pytest.skip('Set COMPUTER_AUTH_TEST_DSN to a disposable PostgreSQL database')
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction(force_rollback=True):
        conn.execute('CREATE SCHEMA IF NOT EXISTS rvbbit')
        conn.execute("CREATE TABLE rvbbit.calliope_desktop_runs (id uuid PRIMARY KEY, requester text, "
                     "status text, outcome text, runtime_state jsonb DEFAULT '{}', updated_at timestamptz, ended_at timestamptz)")
        conn.execute("INSERT INTO rvbbit.calliope_desktop_runs(id,requester,status) VALUES (%s,%s,'queued')", (RID, ACTOR))
        @contextmanager
        def factory():
            yield conn
        for incoming, expected in [('active', 'active'), ('queued', 'active'),
                                   ('finished', 'finished'), ('active', 'finished'), ('queued', 'finished')]:
            browser_auth.access.sync_runs(factory, {'runs': {RID: {'id': RID, 'status': incoming}}})
            assert conn.execute('SELECT status FROM rvbbit.calliope_desktop_runs').fetchone()['status'] == expected


def test_shared_chat_participant_cannot_reuse_another_actors_run(monkeypatch):
    from contextlib import nullcontext
    import desktop_tools
    access = browser_auth.access
    run = {'id': RID, 'requester': ACTOR, 'status': 'active'}
    monkeypatch.setattr(access.calliope_access, 'require_participant', lambda *a: None)
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def transaction(self): return nullcontext()
        def execute(self, sql, params=()):
            assert sql.startswith('SELECT'), 'Wrong identity must not mutate the run'
            return SimpleNamespace(fetchone=lambda: run)
    with pytest.raises(access.DesktopError) as err:
        access.open_run(Conn, RID, 'different-participant@example.com', 'New task')
    assert err.value.code == 'COMPUTER_IDENTITY_CHANGED'

    # Same actor may continue; participant access still permits observation,
    # but a different actor must not dispatch code using this browser identity.
    assert access.open_run(Conn, RID, ACTOR, 'Continue')['id'] == RID
    monkeypatch.setattr(desktop_tools, 'DesktopRuntime', lambda *a: SimpleNamespace())
    monkeypatch.setattr(access, 'run_for_session', lambda *a: run)
    with pytest.raises(access.DesktopError) as err:
        desktop_tools.desktop_exec(None, None, RID, 'different-participant@example.com',
                                   code='print(1)', intent='New task')
    assert err.value.code == 'COMPUTER_IDENTITY_CHANGED'


def test_browser_auth_outage_does_not_prevent_shell_repair(monkeypatch):
    import desktop_tools
    monkeypatch.setattr(desktop_tools.shared_files, 'sync', lambda *a: {'status':'ready','pending':0})
    calls = []
    run = {'id': RID, 'requester': ACTOR, 'status': 'active'}
    class Runtime:
        def request(self, method, path, body):
            calls.append(path)
            assert path == 'execute'
            return {'id': body['job_id'], 'status': 'completed'}
    monkeypatch.setattr(desktop_tools, 'DesktopRuntime', lambda *a: Runtime())
    monkeypatch.setattr(desktop_tools.access, 'run_for_session', lambda *a: run)
    monkeypatch.setattr(desktop_tools.desktop_agents, 'attach', lambda *a: None)
    monkeypatch.setattr(desktop_tools.access, 'append_event', lambda *a: None)
    monkeypatch.setattr(browser_auth, 'prepare', lambda *a: {'status': 'pending'})
    with pytest.raises(desktop_tools.access.DesktopError) as err:
        desktop_tools.desktop_exec(None, None, RID, ACTOR, code='print(1)', intent='Use browser')
    assert err.value.code == 'COMPUTER_AUTH_PENDING' and not calls
    result = desktop_tools.desktop_exec(None, None, RID, ACTOR, code='true', language='shell', intent='Repair browser')
    assert result['status'] == 'completed' and calls == ['execute']
