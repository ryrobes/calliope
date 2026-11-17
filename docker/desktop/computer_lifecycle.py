"""Graceful GUI shutdown and conservative cleanup of this desktop's old locks.

Recovery documents are never deleted. Only locks from a recorded previous
instance, for this user and this persistent LibreOffice profile, are moved.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import time
import uuid

from desktop_session import session_environment


def office_running(proc=Path('/proc')):
    for p in proc.iterdir():
        try:
            if p.name.isdigit() and p.stat().st_uid == os.getuid() and (p / 'comm').read_text().strip() == 'soffice.bin':
                if (p / 'status').read_text().split('State:', 1)[1].lstrip().startswith('Z'):
                    continue
                return True
        except (OSError, IndexError):
            pass
    return False


def lock_paths(home):
    # Do not traverse symlinks (shared remote mounts, browser caches, etc.).
    # Include normal document folders, but skip hidden directories entirely.
    for root, dirs, files in os.walk(home, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not Path(root, d).is_symlink()]
        for name in files:
            if name.startswith('.~lock.') and name.endswith('#'):
                yield Path(root, name)
    yield home / '.config/libreoffice/4/.lock'


def owned_lock(path, home, user, hosts):
    if not path.resolve().is_relative_to(home) or path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_size > 4096:
        return False
    try:
        text = path.read_text()
        if path.name == '.lock':
            ini = configparser.ConfigParser()
            ini.read_string(text)
            return ini.get('Lockdata', 'User') == user and ini.get('Lockdata', 'Host') in hosts
        fields = next(csv.reader([text.rstrip().removesuffix(';')]))
        return (len(fields) == 5 and fields[1] == user and fields[2] in hosts
                and fields[4] == (home / '.config/libreoffice/4').as_uri())
    except (ValueError, UnicodeError, configparser.Error, csv.Error):
        return False


def write_state(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def startup(home=Path.home(), host=None, user=None, running=office_running):
    home = home.resolve()
    state = home / '.local/state/calliope-desktop/lifecycle'
    marker = state / 'instance.json'
    host = host or socket.gethostname()
    user = user or pwd.getpwuid(os.getuid()).pw_name
    try:
        prior = json.loads(marker.read_text())
    except (OSError, ValueError):
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    receipt = {'phase': 'startup', 'host': host, 'time': time.time(), 'quarantined': []}
    if running():
        # Also makes manual invocation harmless while an office process lives.
        receipt['status'] = 'office_running_cleanup_skipped'
        return receipt
    previous = prior.get('host') if prior.get('user') == user else None
    if previous:
        archive = state / 'retired-locks' / (str(int(time.time())) + '-' + uuid.uuid4().hex[:8])
        for path in lock_paths(home):
            try:
                if not owned_lock(path, home, user, {previous}):
                    continue
                relative = path.relative_to(home)
                destination = archive / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.replace(destination)
                receipt['quarantined'].append(str(relative))
            except OSError as exc:
                receipt.setdefault('errors', []).append({'path': str(path.relative_to(home)), 'error': type(exc).__name__})
    receipt['status'] = 'ready'
    write_state(state / 'last-startup.json', receipt)
    write_state(marker, {'host': host, 'user': user, 'started_at': time.time()})
    return receipt


def shutdown(home=Path.home(), timeout=12):
    """Send WM_DELETE_WINDOW while X and its session bus are still available.

Apps can finish cleanly or show an unsaved-work prompt. Never click Discard,
force-save, or erase recovery records to make shutdown look successful.
"""
    env = {**os.environ, **session_environment()}
    env.setdefault('DISPLAY', ':1')
    def command(args):
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=2)
    def windows():
        result = command(['wmctrl', '-l'])
        if result.returncode:
            raise RuntimeError('window_list_unavailable')
        ids = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            wid = line.split()[0]
            kind = command(['xprop', '-id', wid, '_NET_WM_WINDOW_TYPE'])
            if kind.returncode == 0 and not any(t in kind.stdout for t in ('_NET_WM_WINDOW_TYPE_DOCK', '_NET_WM_WINDOW_TYPE_DESKTOP')):
                ids.append(wid)
        return ids
    receipt = {'phase': 'shutdown', 'time': time.time()}
    try:
        initial = windows()
        for wid in initial:
            command(['wmctrl', '-ic', wid])
        deadline = time.monotonic() + timeout
        remaining = initial
        office_alive = office_running()
        while (remaining or office_alive) and time.monotonic() < deadline:
            time.sleep(.25)
            remaining = windows()
            office_alive = office_running()
        receipt.update(status='apps_remain_recovery_preserved' if remaining or office_alive else 'closed',
                       requested=len(initial), remaining=len(remaining), office_running=office_alive)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        receipt.update(status='shutdown_incomplete_recovery_preserved', error=type(exc).__name__)
    write_state(home / '.local/state/calliope-desktop/lifecycle/last-shutdown.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['startup', 'shutdown'])
    args = parser.parse_args()
    print(json.dumps(startup() if args.phase == 'startup' else shutdown()), flush=True)
