#!/usr/bin/env python3
"""Capture a published GPUI app without touching the user's desktop.

Uses the existing Calliope connection, a disposable local profile and Xvfb.
No fixture server or SQLite database. This isolates display/profile state, not
server permissions: semantic actions still operate as the connected user.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time

from studio import NATIVE_BINARY, capture, probe


def stop(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


@contextmanager
def isolated_app(slug, log_path, connection=None, binary=NATIVE_BINARY, mode='app', allow_error=False):
    """Yield the existing probe/capture session shape; always reclaim processes."""
    for tool in ('Xvfb', 'xdotool'):
        if not shutil.which(tool):
            raise RuntimeError(f'Missing {tool}')
    if not (shutil.which('magick') or shutil.which('import')):
        raise RuntimeError('Missing ImageMagick')
    icds = sorted(Path('/usr/share/vulkan/icd.d').glob('lvp_icd*.json'))
    if not icds:
        raise RuntimeError('Install the software Vulkan driver (vulkan-swrast on Arch)')
    config_root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
    connection = connection or config_root/'calliope-canvas/connection.json'
    if not connection.is_file():
        raise RuntimeError('Sign in with the desktop client first, or supply --connection')
    if not binary.is_file():
        raise RuntimeError('Build the release client first: cargo build --release --locked')

    with tempfile.TemporaryDirectory(prefix='calliope-ui-') as directory:
        root = Path(directory)
        for folder in ('config/calliope-canvas', 'data/calliope-canvas', 'cache', 'runtime'):
            (root/folder).mkdir(parents=True, mode=0o700)
        target = root/'config/calliope-canvas/connection.json'
        shutil.copyfile(connection, target)
        target.chmod(0o600)
        # Reuse typography, but never share writable Home, preferences or drafts.
        data_root = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))
        fonts = data_root/'calliope-canvas/fonts'
        if fonts.is_dir():
            shutil.copytree(fonts, root/'data/calliope-canvas/fonts')
        (root/'data/calliope-canvas/preferences.json').write_text(
            json.dumps({'wallpaper_strength': 0.22, 'read_aloud': False}))
        env = os.environ.copy()
        for key in ('WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'HYPRLAND_INSTANCE_SIGNATURE',
                    'VK_DRIVER_FILES', 'VK_ADD_DRIVER_FILES'):
            env.pop(key, None)
        env.update(XDG_CONFIG_HOME=str(root/'config'), XDG_DATA_HOME=str(root/'data'),
                   XDG_CACHE_HOME=str(root/'cache'), XDG_RUNTIME_DIR=str(root/'runtime'),
                   XDG_SESSION_TYPE='x11', VK_ICD_FILENAMES=str(icds[0]),
                   CALLIOPE_UI_PROBE=str(root/'probe.sock'))
        processes = []
        with log_path.open('wb') as log:
            try:
                read_fd, write_fd = os.pipe()
                try:
                    display = subprocess.Popen(
                        ['Xvfb', '-displayfd', str(write_fd), '-screen', '0',
                         '1920x1200x24', '-nolisten', 'tcp'],
                        pass_fds=[write_fd], stdout=log, stderr=log, start_new_session=True)
                    processes.append(display)
                finally:
                    os.close(write_fd)
                with os.fdopen(read_fd) as pipe:
                    if not select.select([pipe], [], [], 10)[0]:
                        raise RuntimeError('Xvfb startup timed out')
                    number = pipe.readline().strip()
                    if not number.isdecimal():
                        raise RuntimeError('Xvfb did not allocate an isolated display')
                env['DISPLAY'] = ':'+number
                launch = [str(binary), '--meaning'] if mode == 'meaning' else [str(binary), '--app', slug]
                app = subprocess.Popen(launch, env=env,
                                       stdout=log, stderr=log, start_new_session=True)
                processes.append(app)
                session = {'native': True, 'display': env['DISPLAY'],
                           'probe': env['CALLIOPE_UI_PROBE'] + ('.meaning' if mode == 'meaning' else '')}
                deadline = time.monotonic()+40
                while time.monotonic() < deadline:
                    if app.poll() is not None:
                        raise RuntimeError(f'Native client exited; inspect {log_path}')
                    # A created but not yet mapped window cannot receive X focus.
                    ids = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(app.pid)],
                                         env=env, text=True, capture_output=True,
                                         timeout=5).stdout.splitlines()
                    if ids and 'window' not in session:
                        session['window'] = ids[-1]
                        # Xvfb has no window manager to assign initial focus or
                        # geometry. These events target only its private display.
                        subprocess.run(['xdotool', 'windowmove', ids[-1], '0', '0',
                                        'windowsize', ids[-1], '1440', '1000',
                                        'windowfocus', ids[-1], 'mousemove',
                                        '--window', ids[-1], '720', '500'],
                                       env=env, check=True, timeout=5)
                        time.sleep(.3)
                    if ids and Path(session['probe']).exists():
                        state = probe(session, {'op': 'inspect'})
                        if state.get('failed') and not allow_error:
                            raise RuntimeError(state.get('notice', 'App load failed'))
                        ready = (state.get('graph') or {}).get('nodes') if mode == 'meaning' else state.get('surface', {}).get('nodes')
                        if not state.get('busy') and (ready or (allow_error and state.get('failed'))):
                            yield session
                            break
                    time.sleep(.2)
                else:
                    raise RuntimeError(f'Published app did not load; inspect {log_path}')
            finally:
                for process in reversed(processes):
                    stop(process)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('slug')
    parser.add_argument('output', type=Path)
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument('--connection', type=Path)
    parser.add_argument('--binary', type=Path, default=NATIVE_BINARY)
    args = parser.parse_args()
    if not 390 <= args.width <= 1920 or not 400 <= args.height <= 1200:
        parser.error('Dimensions must be width 390..1920, height 400..1200')
    output = args.output.resolve()
    if output.suffix.lower() != '.png':
        parser.error('Output must be a .png file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with isolated_app(args.slug, output.with_suffix('.log'), args.connection, args.binary) as session:
        receipt = capture(session, output, args.width, args.height, 0)
        state = probe(session, {'op': 'inspect'})
        receipt.update(slug=args.slug, version=state.get('version'),
                       backend='Xvfb / software Vulkan', profile='disposable')
        receipt['ready'] = receipt['ready'] and not state.get('failed', False)
    output.with_suffix('.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    return 0 if receipt['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
