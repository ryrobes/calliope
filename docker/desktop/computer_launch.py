"""Resolve a launcher handoff to observed, surviving Linux application windows."""
import os
from pathlib import Path
import re
import shlex
import shutil
import time


def process_identity(pid):
    """Start time guards against PID reuse; zombies are not running apps."""
    try:
        root = Path('/proc') / str(int(pid))
        fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z': return None
        return (fields[19], os.readlink(root / 'exe'))
    except (OSError, ValueError, TypeError, IndexError):
        return None


def key(window):
    return (window.get('pid'), window.get('window_id'))


def identities(args, receipt):
    names, executables = set(), set()
    for value in (args.get('name'), args.get('launch_path'), receipt.get('name')):
        if not isinstance(value, str) or not value.strip(): continue
        try: words = shlex.split(value)
        except ValueError: continue
        if not words: continue
        program = Path(words[0]).name.lower()
        # Generic launchers/interpreters do not identify an application.
        if program in {'env', 'sh', 'bash', 'node', 'java', 'xdg-open', 'gio', 'gtk-launch'} or program.startswith('python'):
            continue
        names.add(program)
        names.add(value.lower())
        path = shutil.which(words[0])
        if path: executables.add(os.path.realpath(path))
    return names, executables


def same_app(window, identity, names, executables):
    app = str(window.get('app_name') or '').lower()
    # WM_CLASS names commonly differ only in punctuation/case; Office also
    # suffixes the executable family with the document application.
    norm = lambda value: re.sub(r'[^a-z0-9]', '', value)
    return identity[1] in executables or any(
        norm(app) == norm(name) or app.startswith(name + '-') for name in names)


def launch(client, args, timeout):
    args = dict(args)
    debug = args.pop('debug', False)
    if not isinstance(debug, bool):
        raise ValueError('debug must be a boolean')
    started = time.monotonic()
    deadline = started + timeout
    notes = []
    def windows():
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError('Window observation budget exhausted')
        return client._call_driver('list_windows', {}, timeout=min(3, remaining))['windows']
    try:
        before = windows()
    except Exception as exc:
        before = []; notes.append('Pre-launch window observation unavailable: ' + str(exc))
    before_processes = {w['pid']: process_identity(w['pid']) for w in before}
    # Never retry the launch, including after a missing/uncertain response.
    receipt = client._call_driver('launch_app', args, timeout=max(.001, deadline - time.monotonic()))
    names, executables = identities(args, receipt)
    known = {pid: identity for pid, identity in before_processes.items() if identity and any(
        w['pid'] == pid and same_app(w, identity, names, executables) for w in before)}
    prior = {key(w): w for w in before}
    reported = {key(w) for w in receipt.get('windows', [])}
    launcher_pid = receipt.get('pid')
    launcher_identity = process_identity(launcher_pid)
    # The upstream driver already waits up to 3s on the launcher PID. Give a
    # delegated/slow window another short interval and require two observations.
    settle_deadline = min(deadline, time.monotonic() + 2)
    previous = None
    selected, observed, basis = [], [], 'unresolved'
    while time.monotonic() < settle_deadline:
        try:
            live = windows()
            processes = {w['pid']: process_identity(w['pid']) for w in live}
            related = [w for w in live if processes[w['pid']] and (
                (w['pid'] == launcher_pid and processes[w['pid']] == launcher_identity) or
                key(w) in reported or known.get(w['pid']) == processes[w['pid']] or
                same_app(w, processes[w['pid']], names, executables))]
            new = [w for w in related if key(w) not in prior]
            changed = [w for w in related if key(w) in prior and w.get('title') != prior[key(w)].get('title')]
            observed = new or changed or related
            basis = 'new_app_window' if new else 'changed_app_window' if changed else 'existing_app_window'
            signature = sorted((key(w), processes[w['pid']], w.get('title') or '') for w in observed)
            if signature and signature == previous:
                selected = observed
                break
            previous = signature
        except Exception as exc:
            notes.append('Post-launch window observation unavailable: ' + str(exc))
            break
        time.sleep(min(.15, max(0, settle_deadline - time.monotonic())))
    pids = {w['pid'] for w in selected}
    status = 'ready' if len(selected) == 1 else 'multiple_windows' if selected else 'unresolved'
    explanation = {
        'ready': 'Use this pid/window_id for the observed app window. Verify the requested document or folder in that window.',
        'multiple_windows': 'Several app windows remain; select from windows. No single window_id was assumed.',
        'unresolved': 'Launch was dispatched, but no stable app window was identified. Do not use launcher_pid as an app target.',
    }[status]
    return {**receipt, 'pid': next(iter(pids)) if len(pids) == 1 else None,
            'window_id': selected[0]['window_id'] if len(selected) == 1 else None,
            'running': True if selected else None, 'active': None, 'windows': selected,
            'launcher_pid': launcher_pid, **({'launch_receipt': receipt} if debug else {}),
            'window_resolution': {'status': status, 'basis': basis if selected else 'unresolved',
                'elapsed_ms': round((time.monotonic() - started) * 1000),
                'launcher_running': process_identity(launcher_pid) is not None,
                'note': explanation, 'observation_notes': notes}}
