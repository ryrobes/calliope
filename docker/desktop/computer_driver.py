"""Own the Cua daemon so a handoff can discard its queued input.

Revoking Cua authorization grants alone does not revoke unrestricted sessions.
Replacing the daemon terminates that session's in-process dispatch queue while
leaving the user's browser/profile and native applications open.
"""
from __future__ import annotations

import os
import json
import logging
from pathlib import Path
import socket
import subprocess
import threading
import time


class Driver:
    def __init__(self):
        self.socket = os.environ.get('CALLIOPE_CUA_SOCKET', '/tmp/calliope-cua.sock')
        self.process = None
        self.cursor_sessions = set()
        self.cursor_overlay_enabled = False
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {'CALLIOPE_DESKTOP_CONTROL_KEY', 'VNC_PW', 'VNC_VIEW_ONLY_PW'}}
        self.worker = threading.Thread(target=self._watch, daemon=True)
        self.worker.start()

    def _ready(self):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(.1)
                probe.connect(self.socket)
            return True
        except OSError:
            return False

    def _start(self):
        self.cursor_sessions.clear()
        # Only our own terminated daemon used this socket. A stale inode must
        # not make the replacement appear ready before it begins listening.
        Path(self.socket).unlink(missing_ok=True)
        from desktop_session import session_environment
        self.env.update(session_environment())
        self.cursor_overlay_enabled = self._cursor_overlay_supported()
        command = ['cua-driver', 'serve', '--socket', self.socket, '--permission-mode', 'unrestricted',
                   '--dangerously-bypass-approvals']
        # set_agent_cursor_enabled(false) only hides an idle cursor: Cua
        # revives it on the next input. Disable the renderer at daemon startup.
        if not self.cursor_overlay_enabled:
            command.append('--no-overlay')
        self.process = subprocess.Popen(
            command,
            env=self.env, stdin=subprocess.DEVNULL, start_new_session=True)

    def _cursor_overlay_supported(self):
        # Synthetic badges left persistent trails on our Xvnc desktop. Keep
        # ordinary pointer input by default. An explicit opt-in also requires
        # an existing compositor; never reconfigure the window manager here.
        if self.env.get('CALLIOPE_COMPUTER_CURSOR_BADGES', '').lower() != 'true':
            return False
        try:
            from computer_layers import compositor_present
            if compositor_present(): return True
        except OSError:
            pass
        logging.warning('X11 compositor unavailable; synthetic cursor badges disabled to avoid stale screen trails')
        return False

    def _stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2)
            self.process = None

    def prepare_cursor(self, session):
        """Presentation setup once per driver generation, outside agent calls."""
        with self.lock:
            if session in self.cursor_sessions:
                return
            subprocess.run(['cua-driver', 'call', 'set_agent_cursor_enabled', json.dumps({
                'session': session, 'enabled': self.cursor_overlay_enabled}), '--socket', self.socket], env=self.env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=3, check=True)
            subprocess.run(['cua-driver', 'call', 'set_agent_cursor_motion', json.dumps({
                'session': session, 'glide_duration_ms': 140, 'dwell_after_click_ms': 120,
                'idle_hide_ms': 1800}), '--socket', self.socket], env=self.env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=3, check=True)
            self.cursor_sessions.add(session)

    def revoke(self):
        with self.lock:
            self._stop()
            self._start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self._ready():
                    return
                if self.process.poll() is not None:
                    break
                time.sleep(.02)
            raise RuntimeError('The replacement desktop driver did not become ready')

    def _watch(self):
        try:
            subprocess.run(['cua-driver', 'telemetry', 'disable'], env=self.env,
                           stdin=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logging.warning('Cua telemetry configuration: %s', type(exc).__name__)
        while not self.closed.wait(.5):
            try:
                with self.lock:
                    if self.process is not None and self.process.poll() is None:
                        continue
                    display = subprocess.run(['xdpyinfo'], env=self.env, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, timeout=3, check=False)
                    if display.returncode == 0:
                        from desktop_session import session_environment
                        # Xvnc starts before Xfce. Wait for the real session bus
                        # so app launches cannot autospawn an isolated bus.
                        if not session_environment():
                            continue
                        self._start()
            except (OSError, subprocess.TimeoutExpired) as exc:
                logging.warning('Cua driver restart deferred: %s', type(exc).__name__)
                self.closed.wait(3)

    def close(self):
        self.closed.set()
        self.worker.join(timeout=5)
        with self.lock:
            self._stop()
