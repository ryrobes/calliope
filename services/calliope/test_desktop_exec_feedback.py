"""A short error and a completed job must not disappear behind stdout/sleeps."""
from copy import deepcopy
from urllib.parse import urlsplit, parse_qs
import uuid
import desktop_tools as tools


def test_poll_uses_independent_cursors_and_stops_waiting_on_completion(monkeypatch):
    rid, jid, sid = (str(uuid.uuid4()) for _ in range(3))
    clock, requests, events = [0.0], [], []
    job = {'id': jid, 'run_id': rid, 'status': 'running', 'stdout_next_offset': 800,
           'stderr_next_offset': 0, 'stdout': '', 'stderr': ''}
    class Runtime:
        def request(self, method, path):
            requests.append(path)
            assert parse_qs(urlsplit(path).query) == {'stdout_offset':['800'], 'stderr_offset':['0']}
            return deepcopy(job if len(requests) == 1 else {**job, 'status':'failed',
                'stderr':'Yes button not found', 'stderr_next_offset':20})
    monkeypatch.setattr(tools, 'DesktopRuntime', lambda config: Runtime())
    monkeypatch.setattr(tools.access, 'run_for_session', lambda *args: {'id':rid})
    monkeypatch.setattr(tools.access, 'append_event', lambda *args: events.append(args))
    monkeypatch.setattr(tools.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tools.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = tools.desktop_exec(None, None, sid, 'owner', job_id=jid, stdout_offset=800, wait_seconds=20)
    assert result['stderr'] == 'Yes button not found'
    assert result['next_poll'] == {'session_id':sid, 'job_id':jid, 'stdout_offset':800, 'stderr_offset':20, 'wait_seconds':20}
    assert clock[0] == .25 and len(events) == 1


def test_poll_wait_is_bounded_and_cancellation_returns_immediately(monkeypatch):
    rid, jid, sid = (str(uuid.uuid4()) for _ in range(3))
    clock, status, calls = [0.0], ['running'], []
    class Runtime:
        def request(self, method, path):
            calls.append(path)
            return {'id':jid, 'run_id':rid, 'status':status[0]}
    monkeypatch.setattr(tools, 'DesktopRuntime', lambda config: Runtime())
    monkeypatch.setattr(tools.access, 'run_for_session', lambda *args: {'id':rid})
    monkeypatch.setattr(tools.access, 'append_event', lambda *args: None)
    monkeypatch.setattr(tools.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tools.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = tools.desktop_exec(None,None,sid,'owner',job_id=jid,wait_seconds=90)
    assert result['status'] == 'running' and clock[0] == 20
    status[0] = 'cancelled'
    calls.clear()
    assert tools.desktop_exec(None,None,sid,'owner',job_id=jid)['status'] == 'cancelled'
    assert len(calls) == 1 and clock[0] == 20
