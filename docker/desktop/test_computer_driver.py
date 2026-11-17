import json
from threading import RLock
import computer_driver as driver
import computer_layers
import desktop_session
import pytest


def instance():
    d=driver.Driver.__new__(driver.Driver)
    d.env={};d.lock=RLock();d.cursor_sessions=set();d.socket='/tmp/test-cua.sock'
    return d


def test_synthetic_badges_default_off_without_reconfiguring_the_desktop(monkeypatch):
    calls=[]
    monkeypatch.setattr(driver.subprocess,'run',lambda cmd,**kw:calls.append(cmd))
    d=instance();d.cursor_overlay_enabled=d._cursor_overlay_supported()
    d.prepare_cursor('test-session');d.prepare_cursor('test-session')
    assert not d.cursor_overlay_enabled
    assert json.loads(calls[0][3])=={'session':'test-session','enabled':False}
    assert calls[1][2]=='set_agent_cursor_motion'
    assert len(calls)==2


def test_explicit_badge_opt_in_requires_an_existing_compositor(monkeypatch):
    d=instance();d.env['CALLIOPE_COMPUTER_CURSOR_BADGES']='true'
    monkeypatch.setattr(computer_layers,'compositor_present',lambda:False)
    assert not d._cursor_overlay_supported()
    monkeypatch.setattr(computer_layers,'compositor_present',lambda:True)
    assert d._cursor_overlay_supported()


@pytest.mark.parametrize('enabled', [False, True])
def test_start_disables_renderer_not_just_idle_cursor(monkeypatch, tmp_path, enabled):
    d=instance();d.socket=str(tmp_path/'cua.sock')
    d.cursor_sessions.add('old')
    calls=[]
    monkeypatch.setattr(desktop_session,'session_environment',lambda:{})
    monkeypatch.setattr(d,'_cursor_overlay_supported',lambda:enabled)
    monkeypatch.setattr(driver.subprocess,'Popen',lambda command,**kw:calls.append(command))
    d._start()
    assert ('--no-overlay' in calls[0]) is (not enabled)
    assert not d.cursor_sessions
