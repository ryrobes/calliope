#!/usr/bin/env python3
"""Guest service for the installation’s persistent computer."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import uuid
from urllib.parse import unquote, urlsplit, parse_qs
from urllib.request import build_opener, ProxyHandler
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import calliope_proving_ground as proving_ground
from computer_runtime import ComputerRuntime, ComputerError
from computer_files import SharedFiles


DISPLAY = os.environ.get("DISPLAY", ":1")
RESOLUTION = os.environ.get("VNC_RESOLUTION", "1280x720")
ROOT = Path(os.environ.get("CALLIOPE_DESKTOP_STORAGE", "/var/lib/calliope-desktop"))
SCREENSHOTS = ROOT / "screenshots"
CONTROL_KEY = os.environ.get("CALLIOPE_DESKTOP_CONTROL_KEY", "")
PORT = int(os.environ.get("CALLIOPE_DESKTOP_CONTROL_PORT", "9000"))
computer: ComputerRuntime | None = None
driver = None
shared_files = SharedFiles()


class RequestError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _run(args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "DISPLAY": DISPLAY}
    return subprocess.run(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def _vnc_ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6901), timeout=0.3):
            return True
    except OSError:
        return False


def _display_ready() -> bool:
    try:
        return _run(["xdpyinfo"], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _browser_ready() -> bool:
    try:
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:9223/json/list', timeout=.5) as response:
            return any(page.get('type') == 'page' and page.get('webSocketDebuggerUrl')
                       for page in json.load(response))
    except (OSError, ValueError, TypeError):
        return False


def _health() -> dict:
    from desktop_session import session_environment
    state = computer.status() if computer else {}
    vnc = _vnc_ready()
    display = _display_ready()
    session = bool(session_environment())
    browser = _browser_ready()
    cua = driver is not None and driver._ready()
    return {
        'ok': True,
        'ready': computer is not None and all((vnc, display, session, browser, cua)),
        'vnc_ready': vnc and display,
        'vnc_port_ready': vnc, 'display_ready': display,
        'session_ready': session, 'browser_ready': browser, 'cua_ready': cua,
        'recording_id': state.get('active_run') if (state.get('recording') or {}).get('recording') else None,
    }


def _display_resolution() -> str:
    result = _run(["xdpyinfo"], timeout=5)
    if result.returncode == 0:
        match = re.search(rb"dimensions:\s+(\d+x\d+)\s+pixels", result.stdout)
        if match:
            return match.group(1).decode()
    return RESOLUTION


def _screenshot(request_id: str) -> dict[str, Any]:
    path = SCREENSHOTS / f"{request_id}.png"
    result = _run(
        [
            sys.executable,
            str(Path(__file__).with_name("computer_capture.py")),
            "--display", DISPLAY,
            "--png",
            str(path),
        ]
    )
    if result.returncode != 0 or not path.is_file():
        raise RequestError(
            "SCREENSHOT_FAILED",
            result.stderr.decode("utf-8", "replace")[-1000:] or "Screenshot failed.",
            502,
        )
    capture = json.loads(result.stdout)
    return {
        "object_key": f"screenshots/{path.name}",
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": capture["width"],
        "height": capture["height"],
        "masking": capture["masking"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CalliopeDesktop/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"desktop-control {self.address_string()} {fmt % args}", flush=True)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Calliope-Desktop-Key", "")
        return bool(CONTROL_KEY and hmac.compare_digest(supplied, CONTROL_KEY))

    def _local(self) -> bool:
        return str(self.client_address[0]) in {"127.0.0.1", "::1"}

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_lab_error(self, exc: proving_ground.LabError) -> None:
        self._send({"error": {"code": exc.code, "message": str(exc)}}, exc.status)

    def _body(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 512_000:
                raise ValueError
            value = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, json.JSONDecodeError):
            raise RequestError("INVALID_REQUEST", "Request body must be a JSON object.") from None

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/computer/"):
            if not self._authorized():
                self._send({"error": {"code": "UNAUTHORIZED"}}, 401)
                return
            try:
                self._computer_get()
            except ComputerError as exc:
                self._send({"error": {"code": exc.code, "message": str(exc)}}, exc.status)
            except ValueError as exc:
                self._send({"error": {"code": "INVALID_FILE", "message": str(exc)}}, 400)
            return
        if self.path.startswith("/lab"):
            if not (self._local() or self._authorized()):
                self._send({"error": {"code": "UNAUTHORIZED"}}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                self._send_raw(*proving_ground.get(self.path))
            except proving_ground.LabError as exc:
                self._send_lab_error(exc)
            return
        if not self._authorized():
            self._send({"error": {"code": "UNAUTHORIZED"}}, HTTPStatus.UNAUTHORIZED)
            return
        if self.path != "/health":
            self._send({"error": {"code": "NOT_FOUND"}}, HTTPStatus.NOT_FOUND)
            return
        self._send(_health())

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/computer/"):
            if not self._authorized():
                self._send({"error": {"code": "UNAUTHORIZED"}}, 401)
                return
            try:
                if self.path == '/computer/shared/upload':
                    self._send(shared_files.put(self.headers.get('X-Session-Id'),
                        unquote(self.headers.get('X-File-Name', '')), self.headers.get('X-Upload-Id'),
                        self.headers.get('X-File-Sha256'), self.rfile, int(self.headers.get('Content-Length', '-1'))))
                else:
                    self._computer_post(self._body())
            except (ComputerError, RequestError) as exc:
                self._send({"error": {"code": exc.code, "message": str(exc)}}, exc.status)
            except ValueError as exc:
                self._send({"error": {"code": "INVALID_FILE", "message": str(exc)}}, 400)
            except Exception as exc:
                self._send({"error": {"code": "COMPUTER_FAILED", "message": str(exc)[:1000]}}, 500)
            return
        if self.path.startswith("/lab"):
            if self.path == "/lab/start" and not self._authorized():
                self._send({"error": {"code": "UNAUTHORIZED"}}, HTTPStatus.UNAUTHORIZED)
                return
            if not (self._local() or self._authorized()):
                self._send({"error": {"code": "UNAUTHORIZED"}}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                body = self._body()
                if self.path == "/lab/start":
                    result = proving_ground.start(body.get("scenario"), body.get("run_id"))
                    # The fixture uses the same visible profile as ordinary work.
                    from calliope_computer import browser
                    with browser() as chrome:
                        chrome.contexts[0].pages[0].goto(result["url"])
                    result["browser_launched"] = True
                    self._send(result)
                else:
                    self._send_raw(*proving_ground.post(self.path, body))
            except proving_ground.LabError as exc:
                self._send_lab_error(exc)
            except RequestError as exc:
                self._send({"error": {"code": exc.code, "message": str(exc)}}, exc.status)
            return
        if not self._authorized():
            self._send({"error": {"code": "UNAUTHORIZED"}}, HTTPStatus.UNAUTHORIZED)
            return
        if self.path in {"/reset", "/ground", "/actions", "/recordings/start", "/recordings/stop"}:
            self._send({"error": {"code": "DESKTOP_LAB_RETIRED",
                                 "message": "Use the persistent Computer runtime."}}, 410)
        else:
            self._send({"error": {"code": "NOT_FOUND"}}, 404)

    def _computer_get(self):
        if computer is None:
            raise ComputerError("STARTING", "Computer is starting.", 503)
        url = urlsplit(self.path)
        if url.path == "/computer/status":
            self._send(computer.status())
        elif url.path == '/computer/shared':
            self._send(shared_files.listing(parse_qs(url.query).get('session_id', [''])[0]))
        elif url.path.startswith('/computer/terminal/'):
            query = parse_qs(url.query)
            self._send(computer.terminal(url.path.rsplit('/', 1)[1], int(query.get('offset', ['0'])[0])))
        elif url.path.startswith("/computer/jobs/"):
            query = parse_qs(url.query)
            try:
                offset = int(query.get("stdout_offset", query.get("offset", ["0"]))[0])
                stderr_offset = int(query.get("stderr_offset", ["0"])[0])
            except ValueError:
                raise ComputerError("INVALID_OFFSET", "Offset must be a byte offset.", 400) from None
            self._send(computer.job(url.path.rsplit("/", 1)[1], stdout_offset=offset, stderr_offset=stderr_offset))
        elif url.path.startswith("/computer/files/") or url.path == '/computer/shared/download':
            if url.path == '/computer/shared/download':
                query = parse_qs(url.query)
                path = shared_files.path(query.get('session_id', [''])[0], query.get('path', [''])[0])
            else:
                relative = unquote(url.path[len("/computer/files/"):])
                path = (ROOT / relative).resolve()
                if not path.is_relative_to(ROOT.resolve()):
                    raise ComputerError("FILE_NOT_FOUND", "Unknown computer artifact.", 404)
            if not path.is_file():
                raise ComputerError("FILE_NOT_FOUND", "Unknown computer artifact.", 404)
            # Streaming avoids reading recordings into the service's memory.
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                if not match:
                    raise ComputerError("INVALID_RANGE", "Use a single explicit byte range.", 416)
                start = int(match[1])
                end = min(int(match[2]) if match[2] else end, end)
                if start > end:
                    raise ComputerError("INVALID_RANGE", "Range exceeds this artifact.", 416)
                status = 206
            import mimetypes
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(remaining, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            raise ComputerError("NOT_FOUND", "Unknown computer endpoint.", 404)

    def _computer_post(self, body):
        if computer is None:
            raise ComputerError("STARTING", "Computer is starting.", 503)
        if self.path == "/computer/open":
            result = computer.open_run(body.get("run_id"), body.get("context") or {})
        elif self.path == "/computer/browser-auth":
            result = computer.browser_auth(body.get('run_id'), body.get('origin'),
                                           body.get('cookie'), body.get('expires_at'))
        elif self.path == "/computer/execute":
            result = computer.execute(body.get("run_id"), body.get("job_id"),
                                      code=str(body.get("code") or ""), intent=str(body.get("intent") or ""),
                                      language=body.get("language", "python"), timeout=int(body.get("timeout", 600)))
        elif self.path == "/computer/control":
            result = computer.control(body.get("run_id"), body.get("mode"), body.get("actor"),
                                      body.get("expected_generation"))
        elif self.path == "/computer/human-input":
            result = computer.human_input(body.get("run_id"), int(body.get("generation", -1)),
                                          body.get("actor"), body.get("event") or {})
        elif self.path == "/computer/finish":
            result = computer.finish(body.get("run_id"), str(body.get("outcome") or "Stopped"))
        elif self.path == "/computer/observe":
            result = {"screenshot": _screenshot(str(uuid.uuid4())), "state": computer.status()}
        elif self.path == "/computer/recordings/archived":
            result = computer.prune_recordings(body.get('run_id'), body.get('segments'))
        elif self.path == '/computer/shared/action':
            result = shared_files.mutate(body.get('session_id'), body.get('operation'),
                                         body.get('path'), body.get('target'))
        elif self.path == '/computer/shared/open':
            with computer.lock:
                if computer.state.get('mode') == 'agent':
                    raise ComputerError('COMPUTER_BUSY', 'Callie is working. Take over her desktop before opening a file.', 409)
                path = shared_files.path(body.get('session_id'), body.get('path'))
                if not path.exists():
                    raise ComputerError('FILE_NOT_FOUND', 'File no longer exists.', 404)
                # A viewer action belongs to the persistent desktop, outside job supervision.
                from desktop_session import session_environment
                env = {k:v for k,v in os.environ.items() if k in
                       {'PATH','HOME','LANG','LC_ALL','XAUTHORITY','LD_LIBRARY_PATH'}}
                env.update(session_environment()); env['DISPLAY'] = DISPLAY
                subprocess.Popen(['xdg-open', str(path)], env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                result = {'status': 'dispatched'}
        else:
            raise ComputerError("NOT_FOUND", "Unknown computer endpoint.", 404)
        self._send(result)


def _revoke_computer_input():
    try:
        driver.revoke()
    except Exception as exc:
        raise ComputerError("INPUT_REVOCATION_FAILED", "Could not replace the previous Cua session.", 503) from exc
    # An interrupted drag/shortcut must not leave an OS button held.
    commands = [["xdotool", "keyup", "ctrl", "alt", "shift", "super"]]
    commands.extend(["xdotool", "mouseup", button] for button in ("1", "2", "3"))
    for command in commands:
        # Releasing an already released key/button is idempotent. X can briefly
        # stall during a live VNC handoff; retry that release once while the
        # runtime remains paused, without granting the next driver early.
        try:
            try:
                result = _run(command, timeout=2)
            except subprocess.TimeoutExpired:
                result = _run(command, timeout=5)
            result.check_returncode()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ComputerError("INPUT_REVOCATION_FAILED",
                                "Could not release desktop input; retry the handoff.", 503) from exc


def main() -> None:
    global computer, driver
    if not CONTROL_KEY:
        raise SystemExit("CALLIOPE_DESKTOP_CONTROL_KEY is required")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    from computer_driver import Driver
    driver = Driver()
    computer = ComputerRuntime(ROOT, display=DISPLAY, resolution=_display_resolution,
                               revoke_input=_revoke_computer_input, prepare_input=driver.prepare_cursor)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"desktop-control listening on :{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        computer.close()
        driver.close()


if __name__ == "__main__":
    main()
