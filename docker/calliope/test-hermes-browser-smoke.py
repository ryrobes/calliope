"""Run inside the built image as the Hermes uid, with an empty /opt/data.

No provider, warehouse, or external website is contacted. Exercises the actual
browser_exec tool and the supervisor's Chromium recovery loop.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.update(HOME="/opt/data", HERMES_HOME="/opt/data",
                  API_SERVER_KEY="local-smoke-only", BU_CDP_URL="http://127.0.0.1:9222")
spec = importlib.util.spec_from_file_location("supervisor", "/usr/local/bin/calliope-hermes-supervisor")
# The installed executable has no .py suffix.
if spec is None:
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader("supervisor", SourceFileLoader("supervisor", "/usr/local/bin/calliope-hermes-supervisor"))
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<title>Calliope browser smoke</title><h1>Fresh volume works</h1>")

    def log_message(self, *_args):
        pass


page = ThreadingHTTPServer(("127.0.0.1", 0), Page)
threading.Thread(target=page.serve_forever, daemon=True).start()
threading.Thread(target=supervisor.browser_keeper_loop, daemon=True).start()


def ready():
    deadline = time.monotonic() + 30
    while not supervisor._cdp_alive():
        assert time.monotonic() < deadline, "Chromium did not become ready"
        time.sleep(.25)


ready()
from tools.browser_use_cli import browser_exec
from calliope_browser_origin import OriginProxy
proxy = OriginProxy(("127.0.0.1", 0), page.server_address)
threading.Thread(target=proxy.serve_forever, daemon=True).start()

for attempt in range(2):
    result = browser_exec(
        f'new_tab("http://127.0.0.1:{proxy.server_address[1]}")\nprint(page_info())',
        session=f"smoke-{attempt}", timeout_s=60, task_id="smoke", local=True,
    )
    payload = json.loads(result) if isinstance(result, str) else result
    assert payload.get("success"), payload
    assert "Fresh volume works" in str(payload) or "Calliope browser smoke" in str(payload), payload
    if attempt == 0:
        # Exercise keeper recovery, not just direct launch of the binary.
        owner = os.readlink(Path("/opt/data/.config/chromium/SingletonLock"))
        os.kill(int(owner.rsplit("-", 1)[1]), signal.SIGKILL)
        time.sleep(1)
        ready()
print("Browser smoke passed: fresh volume, real browser_exec, Chromium restart")

# A successful Python probe on Chrome's network error page is not successful navigation.
import socket
with socket.socket() as unavailable:
    unavailable.bind(("127.0.0.1", 0))
    missing_port = unavailable.getsockname()[1]
result = browser_exec(f'new_tab("http://127.0.0.1:{missing_port}")\nprint(page_info())',
                      session="smoke-unreachable", timeout_s=60, task_id="smoke", local=True)
payload = json.loads(result) if isinstance(result, str) else result
assert not payload.get("success"), payload
if "chrome-error://chromewebdata/" in str(payload.get("output")):
    assert payload.get("error_type") == "browser_navigation_failed", payload
proxy.shutdown()
proxy.server_close()
print("Browser loopback origin and navigation failure: passed")
