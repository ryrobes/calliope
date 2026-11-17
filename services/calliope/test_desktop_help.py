from types import SimpleNamespace
import sys
import uuid

import pytest

import desktop_tools as tools


def test_pause_publishes_one_resumable_blocker_without_executing_code(monkeypatch):
    rid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    calls, published = [], []
    class Runtime:
        def __init__(self, config):
            pass
        def request(self, method, path, payload=None):
            calls.append((method, path, payload))
            return {'mode': 'agent', 'generation': 8}
    monkeypatch.setattr(tools, 'DesktopRuntime', Runtime)
    monkeypatch.setattr(tools.access, 'run_for_session', lambda *args: {'id': rid, 'goal': 'Download report'})
    monkeypatch.setattr(tools.access, 'append_event', lambda *args: None)
    def publish(*args, **kwargs):
        published.append((args, kwargs))
        return {'id': 'inbox-item'}
    monkeypatch.setitem(sys.modules, 'calliope', SimpleNamespace(publish_work_item=publish))
    result = tools.desktop_exec(None, None, sid, 'person@example.com', pause_reason='The portal needs sign-in')
    assert result['status'] == 'paused' and result['work_item_id'] == 'inbox-item'
    assert [call[1] for call in calls] == ['status', 'control']
    assert calls[1][2]['mode'] == 'paused'
    assert published[0][0][1:3] == (sid, 'blocked')
    assert published[0][1]['dedupe_key'] == 'computer:' + rid
    assert published[0][1]['context']['input_request_id'] is None


def test_agent_help_request_cannot_revoke_a_human_takeover(monkeypatch):
    calls = []
    class Runtime:
        def __init__(self, config):
            pass
        def request(self, method, path, payload=None):
            calls.append(path)
            return {'mode': 'human'}
    monkeypatch.setattr(tools, 'DesktopRuntime', Runtime)
    monkeypatch.setattr(tools.access, 'run_for_session', lambda *args: {'id': str(uuid.uuid4())})
    with pytest.raises(tools.access.DesktopError, match='person already controls'):
        tools.desktop_exec(None, None, str(uuid.uuid4()), 'person@example.com', pause_reason='Need help')
    assert calls == ['status']
