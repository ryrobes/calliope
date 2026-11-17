import json
from pathlib import Path

import computer_lifecycle as lifecycle


def document_lock(home, name='sheet.xlsx', host='old', user='desktop'):
    path = home / ('Documents/.~lock.' + name + '#')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f',{user},{host},25.09.2026 10:00,{home.as_uri()}/.config/libreoffice/4;')
    return path


def seed(home):
    lifecycle.startup(home, 'old', 'desktop', running=lambda: False)


def test_only_known_prior_instance_locks_are_quarantined(tmp_path):
    seed(tmp_path)
    own = document_lock(tmp_path)
    other = document_lock(tmp_path, 'other.xlsx', host='unrelated-host')
    foreign = document_lock(tmp_path, 'foreign.xlsx', user='somebody-else')
    profile = tmp_path / '.config/libreoffice/4'
    profile.mkdir(parents=True)
    (profile / '.lock').write_text('[Lockdata]\nUser=desktop\nHost=old\n')
    recovery = profile / 'user/backup/unsaved.odt'
    recovery.parent.mkdir(parents=True)
    recovery.write_bytes(b'important recovery data')
    result = lifecycle.startup(tmp_path, 'new', 'desktop', running=lambda: False)
    assert len(result['quarantined']) == 2
    assert not own.exists() and not (profile / '.lock').exists()
    assert other.exists() and foreign.exists() and recovery.read_bytes() == b'important recovery data'
    assert len(list((tmp_path / '.local/state/calliope-desktop/lifecycle/retired-locks').rglob('.~lock*'))) == 1
    assert lifecycle.startup(tmp_path, 'new', 'desktop', running=lambda: False)['quarantined'] == []


def test_live_office_or_unknown_instance_never_loses_locks(tmp_path):
    own = document_lock(tmp_path)
    assert lifecycle.startup(tmp_path, 'old', 'desktop', running=lambda: False)['quarantined'] == []
    assert own.exists()
    result = lifecycle.startup(tmp_path, 'new', 'desktop', running=lambda: True)
    assert result['status'] == 'office_running_cleanup_skipped' and own.exists()
    state = json.loads((tmp_path / '.local/state/calliope-desktop/lifecycle/instance.json').read_text())
    assert state['host'] == 'old'


def test_symlinks_and_foreign_profiles_are_not_followed(tmp_path):
    home = tmp_path / 'home'; home.mkdir(); seed(home)
    outside = document_lock(tmp_path / 'outside')
    (home / 'Shared').symlink_to(outside.parent, target_is_directory=True)
    linked = home / '.~lock.link.xlsx#'; linked.symlink_to(outside)
    lock = document_lock(home)
    lock.write_text(',desktop,old,25.09.2026 10:00,file:///tmp/another-profile;')
    assert lifecycle.startup(home, 'new', 'desktop', running=lambda: False)['quarantined'] == []
    assert outside.exists() and linked.is_symlink() and lock.exists()


def test_shutdown_sends_close_without_discarding_dirty_work(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='0x123 0 desktop Unsaved document\n')
    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    monkeypatch.setattr(lifecycle, 'session_environment', lambda: {})
    monkeypatch.setattr(lifecycle, 'office_running', lambda: False)
    result = lifecycle.shutdown(tmp_path, timeout=0)
    assert result['status'] == 'apps_remain_recovery_preserved'
    assert calls == [['wmctrl', '-l'], ['xprop', '-id', '0x123', '_NET_WM_WINDOW_TYPE'], ['wmctrl', '-ic', '0x123']]


def test_shutdown_does_not_close_desktop_or_panel(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        stdout = '0x1 -1 host Panel\n0x2 -1 host Desktop\n' if args == ['wmctrl', '-l'] else (
            '_NET_WM_WINDOW_TYPE_DOCK' if args[2] == '0x1' else '_NET_WM_WINDOW_TYPE_DESKTOP')
        return SimpleNamespace(returncode=0, stdout=stdout)
    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    monkeypatch.setattr(lifecycle, 'session_environment', lambda: {})
    monkeypatch.setattr(lifecycle, 'office_running', lambda: False)
    assert lifecycle.shutdown(tmp_path, timeout=0)['status'] == 'closed'
    assert not any('-ic' in args for args in calls)


def test_shutdown_does_not_report_closed_while_office_is_finishing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=''))
    monkeypatch.setattr(lifecycle, 'session_environment', lambda: {})
    monkeypatch.setattr(lifecycle, 'office_running', lambda: True)
    result = lifecycle.shutdown(tmp_path, timeout=0)
    assert result['status'] == 'apps_remain_recovery_preserved'
    assert result['remaining'] == 0 and result['office_running']
