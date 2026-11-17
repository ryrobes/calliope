"""Old control endpoints cannot reset the persistent home or bypass its writer."""
import sys
import subprocess
import threading
from types import SimpleNamespace
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calliope_desktop


@pytest.mark.parametrize('missing', [None, 'vnc', 'display', 'session', 'browser', 'cua', 'runtime'])
def test_health_waits_for_the_working_desktop_not_just_x11(monkeypatch, missing):
    import desktop_session
    monkeypatch.setattr(calliope_desktop, '_vnc_ready', lambda: missing != 'vnc')
    monkeypatch.setattr(calliope_desktop, '_display_ready', lambda: missing != 'display')
    monkeypatch.setattr(calliope_desktop, '_browser_ready', lambda: missing != 'browser')
    monkeypatch.setattr(desktop_session, 'session_environment',
                        lambda: {} if missing == 'session' else {'DBUS_SESSION_BUS_ADDRESS': 'desktop-bus'})
    monkeypatch.setattr(calliope_desktop, 'driver', SimpleNamespace(_ready=lambda: missing != 'cua'))
    monkeypatch.setattr(calliope_desktop, 'computer',
                        None if missing == 'runtime' else SimpleNamespace(status=lambda: {}))
    health = calliope_desktop._health()
    assert health['ok'] is True
    assert health['ready'] is (missing is None)


def test_input_release_retries_timeout_without_restarting_driver_twice(monkeypatch):
    revoked, calls = [], []
    monkeypatch.setattr(calliope_desktop, 'driver', SimpleNamespace(revoke=lambda: revoked.append(True)), raising=False)

    def release(command, *, timeout):
        calls.append((command, timeout))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(calliope_desktop, '_run', release)
    calliope_desktop._revoke_computer_input()
    assert revoked == [True]
    assert [timeout for _, timeout in calls] == [2, 5, 2, 2, 2]
    assert calls[0][0] == calls[1][0]
    assert [command[-1] for command, _ in calls[2:]] == ['1', '2', '3']


@pytest.mark.parametrize('failure', ['timeout', 'exit'])
def test_failed_release_does_not_silently_grant_control(monkeypatch, failure):
    monkeypatch.setattr(calliope_desktop, 'driver', SimpleNamespace(revoke=lambda: None), raising=False)

    def release(command, *, timeout):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(calliope_desktop, '_run', release)
    with pytest.raises(calliope_desktop.ComputerError) as error:
        calliope_desktop._revoke_computer_input()
    assert error.value.code == 'INPUT_REVOCATION_FAILED'


def test_retired_mutation_endpoints_are_inert_and_still_require_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(calliope_desktop, 'CONTROL_KEY', 'test-private-key')
    monkeypatch.setattr(calliope_desktop, 'ROOT', tmp_path)
    existing = tmp_path / 'preserved-office-document.txt'
    existing.write_text('keep this document')
    server = calliope_desktop.ThreadingHTTPServer(('127.0.0.1', 0), calliope_desktop.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for endpoint in ('reset', 'ground', 'actions', 'recordings/start', 'recordings/stop'):
            for key, status in (('', 401), ('test-private-key', 410)):
                request = Request(f'http://127.0.0.1:{server.server_port}/{endpoint}', b'{}',
                                  {'X-Calliope-Desktop-Key': key, 'Content-Type': 'application/json'})
                with pytest.raises(HTTPError) as error:
                    urlopen(request, timeout=3)
                assert error.value.code == status
                error.value.close()
        assert existing.read_text() == 'keep this document'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_shared_file_open_respects_active_writer_and_uses_desktop_bus(monkeypatch,tmp_path):
    import desktop_session
    file=tmp_path/'report.txt';file.write_text('demo')
    state={'mode':'agent'};calls=[]
    monkeypatch.setattr(calliope_desktop,'computer',SimpleNamespace(lock=threading.RLock(),state=state))
    monkeypatch.setattr(calliope_desktop,'shared_files',SimpleNamespace(path=lambda sid,name:file))
    monkeypatch.setattr(desktop_session,'session_environment',lambda:{'DBUS_SESSION_BUS_ADDRESS':'desktop-bus'})
    monkeypatch.setenv('CALLIOPE_DESKTOP_CONTROL_KEY','private-test-key')
    monkeypatch.setattr(calliope_desktop.subprocess,'Popen',lambda *args,**kwargs:calls.append((args,kwargs)))
    responses=[]
    handler=SimpleNamespace(path='/computer/shared/open',_send=responses.append)
    with pytest.raises(calliope_desktop.ComputerError,match='Callie is working'):
        calliope_desktop.Handler._computer_post(handler,{'session_id':'session','path':'report.txt'})
    assert calls==[]
    state['mode']='idle'
    calliope_desktop.Handler._computer_post(handler,{'session_id':'session','path':'report.txt'})
    assert calls[0][0][0]==['xdg-open',str(file)]
    assert calls[0][1]['env']['DBUS_SESSION_BUS_ADDRESS']=='desktop-bus'
    assert 'CALLIOPE_DESKTOP_CONTROL_KEY' not in calls[0][1]['env']
    assert responses==[{'status':'dispatched'}]
