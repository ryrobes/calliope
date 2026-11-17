import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
import sys
import uuid

import pytest

import desktop_agents as agents


def identifier(): return str(uuid.uuid4())


@pytest.mark.parametrize('status,blocked', [('running', True), ('queued', True), ('blocked', True), ('complete', False), ('failed', False)])
def test_live_agent_owns_run_between_jobs_but_ended_agent_can_handoff(monkeypatch, status, blocked):
    rid, old, new = identifier(), identifier(), identifier()
    owner = {'key': 'specialist:' + old, 'id': old, 'kind': 'specialist', 'profile_name': 'analytical-skeptic'}
    current = {'key': 'turn:' + new, 'id': new, 'kind': 'turn'}
    written, queries = [], []
    def rows(query, params):
        if 'FOR UPDATE' in query: return {'id': rid, 'status': 'active'}
        if 'SELECT e.payload' in query: return {'payload': owner, 'display_name': 'Cassandra'}
        if 'SELECT status FROM' in query: return {'status': status}
        raise AssertionError(query)
    monkeypatch.setattr(agents.access, 'append_event_on_connection', lambda *args: written.append(args))
    factory = lambda: Connection(rows, queries)
    if blocked:
        with pytest.raises(agents.access.DesktopError, match='Cassandra') as error:
            agents.attach(factory, rid, 'owner@example.com', current)
        assert error.value.code == 'DESKTOP_BUSY' and not written
    else:
        agents.attach(factory, rid, 'owner@example.com', current)
        assert written[0][4] == current
    assert 'FOR UPDATE' in queries[0][0]


def test_same_agent_can_continue_and_public_identity_omits_private_ids(monkeypatch):
    rid, aid = identifier(), identifier()
    owner = {'key': 'specialist:' + aid, 'id': aid, 'kind': 'specialist', 'profile_name': 'analytical-skeptic',
             'actor': 'owner@example.com', 'hermes_run_id': 'private-run'}
    def rows(query, params):
        if 'FOR UPDATE' in query: return {'id': rid, 'status': 'active'}
        if 'SELECT e.payload' in query: return {'payload': owner, 'display_name': 'Cassandra'}
        raise AssertionError(query)
    factory = lambda: Connection(rows, [])
    written = []
    monkeypatch.setattr(agents.access, 'append_event_on_connection', lambda *args: written.append(args))
    agents.attach(factory, rid, 'owner@example.com', owner)
    assert not written  # Already owns it: no duplicate ownership receipt.
    assert agents.active_agent(factory, rid) == {'kind': 'specialist', 'display_name': 'Cassandra'}
    assert agents.active_agent(factory, None) is None


@pytest.mark.parametrize('operation', ['execute', 'pause', 'finish'])
def test_conflicting_agent_cannot_dispatch_or_end_the_other_agents_run(monkeypatch, operation):
    import desktop_tools
    rid, controller = identifier(), {'key': 'specialist:' + identifier()}
    calls = []
    monkeypatch.setattr(desktop_tools, 'DesktopRuntime', lambda config: SimpleNamespace(
        request=lambda *args: calls.append(args)))
    monkeypatch.setattr(agents.access, 'run_for_session', lambda *args: {'id': rid, 'requester': 'owner@example.com'})
    def busy(*args): raise agents.access.DesktopError('DESKTOP_BUSY', 'Cassandra owns this run', 409)
    monkeypatch.setattr(agents, 'attach', busy)
    with pytest.raises(agents.access.DesktopError, match='Cassandra'):
        if operation == 'finish':
            desktop_tools.finish_desktop(None, None, identifier(), 'owner@example.com', 'Done', controller)
        else:
            desktop_tools.desktop_exec(None, None, identifier(), 'owner@example.com', controller=controller,
                **({'pause_reason': 'Help'} if operation == 'pause' else {'code': 'print(1)', 'intent': 'Check'}))
    assert calls == []


def test_concurrent_claims_and_returning_owner_with_real_postgres():
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ.get('COMPUTER_OWNERSHIP_TEST_DSN')
    if not dsn:
        pytest.skip('requires a disposable PostgreSQL database')
    factory = lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    rid = identifier()
    controllers = [{'id': identifier(), 'kind': 'specialist', 'profile_name': name} for name in ('Cassandra', 'Iris')]
    for c in controllers: c['key'] = 'specialist:' + c['id']
    with factory() as conn:
        assert conn.execute("SELECT to_regnamespace('rvbbit') ns").fetchone()['ns'] is None
        conn.execute('''CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.calliope_desktop_runs(id uuid PRIMARY KEY,status text);
            CREATE TABLE rvbbit.calliope_specialist_runs(id uuid PRIMARY KEY,status text);
            CREATE TABLE rvbbit.calliope_specialists(profile_name text,display_name text);
            CREATE TABLE rvbbit.calliope_workbench_events(id uuid PRIMARY KEY,desktop_run_id uuid,
                sequence bigint,event_type text,actor_email text,idempotency_key text,payload jsonb,
                UNIQUE(desktop_run_id,sequence),UNIQUE(desktop_run_id,idempotency_key));''')
        conn.execute("INSERT INTO rvbbit.calliope_desktop_runs VALUES(%s,'active')", (rid,))
        for c in controllers:
            conn.execute("INSERT INTO rvbbit.calliope_specialist_runs VALUES(%s,'running')", (c['id'],))
            conn.execute('INSERT INTO rvbbit.calliope_specialists VALUES(%s,%s)', (c['profile_name'], c['profile_name']))
    try:
        barrier = Barrier(2)
        def claim(c):
            barrier.wait(timeout=5)
            try:
                agents.attach(factory, rid, 'owner@example.com', c)
                return 'acquired', c
            except agents.access.DesktopError as exc:
                assert exc.code == 'DESKTOP_BUSY'
                return 'busy', c
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, controllers))
        assert sorted(status for status, _ in results) == ['acquired', 'busy']
        first = next(c for status, c in results if status == 'acquired')
        second = next(c for status, c in results if status == 'busy')
        with factory() as conn:
            conn.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete' WHERE id=%s", (first['id'],))
        agents.attach(factory, rid, 'owner@example.com', second)
        assert agents.active_agent(factory, rid)['display_name'] == second['profile_name']
        with factory() as conn:
            conn.execute("UPDATE rvbbit.calliope_specialist_runs SET status=CASE WHEN id=%s THEN 'running' ELSE 'complete' END", (first['id'],))
        agents.attach(factory, rid, 'owner@example.com', first)
        agents.attach(factory, rid, 'owner@example.com', first)
        assert agents.active_agent(factory, rid)['display_name'] == first['profile_name']
        with factory() as conn:
            assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_workbench_events').fetchone()['n'] == 3
    finally:
        with factory() as conn: conn.execute('DROP SCHEMA rvbbit CASCADE')


class Connection:
    def __init__(self, rows, queries): self.rows, self.queries = rows, queries
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def transaction(self): return nullcontext()
    def execute(self, query, params=()):
        self.queries.append((query, params))
        self.result = self.rows(query, params)
        return self
    def fetchone(self): return self.result
    def fetchall(self): return self.result


def test_scheduled_identity_comes_from_the_claimed_occurrence_not_metadata():
    sid, occurrence = identifier(), identifier()
    queries = []
    record = {'kind': 'work_order', 'id': occurrence, 'actor': 'owner@example.com', 'hermes_run_id': None}
    factory = lambda: Connection(lambda query, params: [record], queries)
    authorization = SimpleNamespace(subject='forged@example.com', platform='cron', session_ref='cron_exact_occurrence')
    actor, controller = agents.resolve(factory, sid, authorization, True)
    assert actor == 'owner@example.com' and controller['key'] == 'work_order:' + occurrence
    assert queries[0][1] == (sid, 'cron_exact_occurrence', sid, 'cron_exact_occurrence')
    assert "status='running'" in queries[0][0]
    empty = lambda: Connection(lambda *args: [], [])
    with pytest.raises(agents.access.DesktopError, match='no longer active'):
        agents.resolve(empty, sid, authorization, True)


def test_untrusted_cron_cannot_acquire_an_assignment_identity():
    auth = SimpleNamespace(subject=None, platform='cron', session_ref='cron_claimed')
    def forbidden(): raise AssertionError('Untrusted transport must not consult the occurrence ledger')
    with pytest.raises(agents.access.DesktopError) as error:
        agents.resolve(forbidden, identifier(), auth, False)
    assert error.value.status == 403


def test_ended_api_turn_cannot_reopen_computer():
    auth = SimpleNamespace(subject='owner@example.com', platform='api_server', session_ref='calliope_same_notebook', run_ref='run_old')
    with pytest.raises(agents.access.DesktopError) as error:
        agents.resolve(lambda: Connection(lambda *args: None, []), identifier(), auth, True)
    assert error.value.code == 'COMPUTER_TASK_ENDED'


def test_stop_fence_blocks_later_dispatch_but_not_a_new_turn():
    old = {'key': 'turn:' + identifier()}
    current = {'key': 'turn:' + identifier()}
    def rows(query, params):
        return {'exists': 1} if "event_type='agent.stop.requested'" in query and params == (old['key'],) else None
    factory = lambda: Connection(rows, [])
    with pytest.raises(agents.access.DesktopError) as error:
        with agents.guard(factory, old):
            pytest.fail('Stopped agent reached execution')
    assert error.value.code == 'COMPUTER_STOPPED'
    with agents.guard(factory, current): pass


def test_stop_targets_the_bound_turn_not_a_later_sql_turn(monkeypatch):
    tid, sid, rid = identifier(), identifier(), identifier()
    controller = {'key': 'turn:' + tid, 'kind': 'turn', 'id': tid, 'session_id': sid}
    pending = {'payload': controller, 'desktop_run_id': rid, 'actor_email': 'owner@example.com'}
    queries, calls, receipts, stopped = [], [], [], []
    def rows(query, params):
        if "e.event_type='agent.stop.requested'" in query: return [pending]
        if 'FROM rvbbit.calliope_turns' in query:
            assert params == (tid, sid)
            return {'id': tid, 'status': 'running', 'hermes_run_id': 'run_original'}
        if 'FROM rvbbit.calliope_sessions' in query: return {'id': sid}
        raise AssertionError(query)
    factory = lambda: Connection(rows, queries)
    async def cancel(*args, **kwargs): calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, 'calliope', SimpleNamespace(
        _session_hermes_base_url=lambda *args: 'http://original-profile',
        _mark_turn_user_stopped=lambda conn, turn: stopped.append(turn),
        _USER_STOPPED_TURN_ERROR='stopped by user', _hermes_json=cancel))
    monkeypatch.setattr(agents.access, 'append_event', lambda *args: receipts.append(args))
    asyncio.run(agents.cancel_pending(factory, SimpleNamespace()))
    assert stopped == [tid]
    assert calls[0][0][2] == '/v1/runs/run_original/stop'
    assert calls[0][1]['base_url'] == 'http://original-profile'
    assert receipts[0][3] == 'agent.stopped'


def test_failed_cancellation_leaves_durable_request_for_retry(monkeypatch):
    tid, sid = identifier(), identifier()
    event = {'payload': {'key':'turn:'+tid, 'kind':'turn', 'id':tid, 'session_id':sid},
             'desktop_run_id': identifier(), 'actor_email':'owner@example.com'}
    def rows(query, params):
        if "e.event_type='agent.stop.requested'" in query: return [event]
        if 'FROM rvbbit.calliope_turns' in query:
            return {'status':'interrupted','error':'stopped by user','hermes_run_id':'run_original'}
        return {'id':sid}
    async def unavailable(*args, **kwargs): raise RuntimeError('Transport unavailable')
    monkeypatch.setitem(sys.modules, 'calliope', SimpleNamespace(
        _session_hermes_base_url=lambda *args:'http://profile', _mark_turn_user_stopped=lambda *args:None,
        _USER_STOPPED_TURN_ERROR='stopped by user', _hermes_json=unavailable))
    receipts=[]
    monkeypatch.setattr(agents.access,'append_event',lambda *args:receipts.append(args))
    asyncio.run(agents.cancel_pending(lambda:Connection(rows,[]),SimpleNamespace()))
    assert receipts == []


def test_late_tool_call_cannot_adopt_a_new_turn_in_same_conversation():
    sid = identifier()
    auth = SimpleNamespace(subject='owner@example.com', platform='api_server',
                           session_ref='same_conversation', run_ref='run_old')
    def rows(query, params):
        assert 'hermes_run_id=%s' in query
        assert params == (sid, 'same_conversation', 'owner@example.com', 'run_old')
        # The only running turn has run_new; the exact-run predicate excludes it.
        return None
    with pytest.raises(agents.access.DesktopError) as error:
        agents.resolve(lambda: Connection(rows, []), sid, auth, True)
    assert error.value.code == 'COMPUTER_TASK_ENDED'
    auth.run_ref = None
    with pytest.raises(agents.access.DesktopError) as error:
        agents.resolve(lambda: Connection(rows, []), sid, auth, True)
    assert error.value.code == 'COMPUTER_CONTEXT_REQUIRED'


@pytest.mark.parametrize('kind', ['work_order', 'workflow'])
def test_scheduled_stop_waits_for_scheduler_terminal_receipt(monkeypatch, kind):
    aid, sid, rid = identifier(), identifier(), identifier()
    controller = {'key': kind + ':' + aid, 'kind': kind, 'id': aid,
                  'session_id': sid, 'session_ref': 'cron_exact_occurrence'}
    event = {'payload': controller, 'desktop_run_id': rid, 'actor_email': 'owner@example.com'}
    row = {'status': 'blocked', 'hermes_job_id': 'job_exact', 'result_summary': 'Login expired'}
    requests, finished, receipts = [], [], []
    terminal = False
    def rows(query, params):
        if "e.event_type='agent.stop.requested'" in query:
            return [event]
        assert params == (aid, sid, 'cron_exact_occurrence')
        assert 'hermes_session_id=%s' in query
        return row
    async def stop(config, method, path, body, **kwargs):
        requests.append((path, body))
        return {'terminal': terminal, 'stop_requested': not terminal}
    def finish(*args, **kwargs):
        finished.append((args, kwargs))
        row['status'] = 'failed'
    monkeypatch.setitem(sys.modules, 'calliope', SimpleNamespace(
        _hermes_json=stop, finish_work_order_run=finish, finish_workflow_run=finish))
    monkeypatch.setattr(agents.access, 'append_event', lambda *args: receipts.append(args))
    factory = lambda: Connection(rows, [])
    asyncio.run(agents.cancel_pending(factory, SimpleNamespace()))
    assert len(finished) == 1 and finished[0][0][1:3] == (aid, 'failed')
    assert finished[0][1]['stop_requested'] is True
    assert finished[0][1]['details']['previous_summary'] == 'Login expired'
    assert receipts == []  # Accepted is not the same as stopped.
    terminal = True
    asyncio.run(agents.cancel_pending(factory, SimpleNamespace()))
    assert len(finished) == 1
    assert requests == [('/v1/cron/executions/stop', {'session_id':'cron_exact_occurrence', 'job_id':'job_exact'})] * 2
    assert receipts[0][3] == 'agent.stopped'
