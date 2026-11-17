"""Own the ordinary subprocess tree, including double-forked/setsid children.

A job is finished when its entire tree exits. Persistent GUI apps are launched
through the separately supervised desktop driver. This is process lifecycle
management inside the guest, not a boundary against hostile guest code.
"""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

libc = ctypes.CDLL(None, use_errno=True)
parent = int(sys.argv[1])
root = os.getpid()
actual_parent = os.getppid()


def children():
    relations = {}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            # comm may contain spaces or closing parentheses.
            fields = (item / 'stat').read_text().rsplit(')', 1)[1].split()
            relations[int(item.name)] = int(fields[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    descendants = {root}
    while True:
        more = {pid for pid, ppid in relations.items() if ppid in descendants}
        if more <= descendants:
            return descendants - {root}
        descendants |= more


def stop(signum, frame):
    # Freeze the observed tree before killing it so descendants cannot keep
    # forking during cleanup. Subreaping retains detached grandchildren.
    for pid in children():
        try:
            os.kill(pid, signal.SIGSTOP)
        except ProcessLookupError:
            pass
    for pid in children():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os._exit(128 + signum)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), 'Cannot supervise detached job children')
if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
    raise OSError(ctypes.get_errno(), 'Cannot bind job lifetime to its supervisor')
privileged = os.geteuid() == 0 and os.environ.get('CALLIOPE_COMPUTER_JOB_UID') == '1000'
if os.getppid() != (actual_parent if privileged else parent) or os.getppid() == 1:
    raise SystemExit('Desktop supervisor exited before job launch')
if privileged:
    Path(os.environ['CALLIOPE_COMPUTER_SUPERVISOR_PID_FILE']).write_text(str(root))

def desktop_user():
    os.setgroups([1000])
    os.setgid(1000)
    os.setuid(1000)

env = dict(os.environ)
env.pop('CALLIOPE_COMPUTER_JOB_UID', None)
env.pop('CALLIOPE_COMPUTER_SUPERVISOR_PID_FILE', None)
if privileged:
    env.update(HOME='/home/kasm-user', USER='kasm-user', LOGNAME='kasm-user')
    env['PYTHONPATH'] = str(Path(__file__).parent)
    env['PATH'] = '/opt/computer-venv/bin:' + env.get('PATH', '/usr/bin:/bin')
process = subprocess.Popen(sys.argv[2:], env=env, preexec_fn=desktop_user if privileged else None)
result = process.wait()
# Adopted background children are still executing this job. Keep its supervisor
# alive for cancellation/timeout; an exited shell is not completion evidence.
while True:
    try:
        pid, _ = os.waitpid(-1, os.WNOHANG)
        if pid == 0:
            time.sleep(.03)
    except ChildProcessError:
        break
raise SystemExit(result)
