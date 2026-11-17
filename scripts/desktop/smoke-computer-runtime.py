#!/usr/bin/env python3
"""Run inside a disposable guest; exercise real execution and recording."""
import json
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0, '/opt/calliope-desktop')
from computer_runtime import ComputerRuntime
from calliope_desktop import _display_resolution

# The production service owns its provisioned storage subdirectories. A fresh
# guest's unprivileged test process must not assume it can create their parent.
root = Path.home() / '.local/state/calliope-capability-spike'
runtime = ComputerRuntime(root, resolution=_display_resolution)
run, job = str(uuid.uuid4()), str(uuid.uuid4())
try:
    runtime.open_run(run, {'goal': 'Cua capability spike'})
    runtime.execute(run, job, intent='Prepare the persistent visible browser through Cua', code='''
import json
from calliope_computer import cua, browser
windows = cua.call('list_windows')['windows']
browser_window = next(w for w in windows if 'chrome' in w['app_name'].lower())
anchor = {'pid': browser_window['pid'], 'window_id': browser_window['window_id']}
print(json.dumps(cua.call('browser_prepare', {**anchor, 'strategy': {'kind': 'existing_profile'}})))
state = cua.call('get_browser_state', anchor)
print(json.dumps(state))
assert state.get('target_id'), state
import http.server, threading
class Portal(http.server.BaseHTTPRequestHandler):
    saved = []
    def do_GET(self):
        if self.path.startswith('/saved?'):
            from urllib.parse import parse_qs, urlsplit
            Portal.saved.append(parse_qs(urlsplit(self.path).query)['name'][0])
            self.send_response(200); self.end_headers(); return
        body = b'<html><title>Callie capability test</title><label>Name<input id="name"></label><button>Save</button><p id="result"></p><script>document.querySelector("button").onclick=()=>{document.querySelector("#result").textContent=document.querySelector("input").value;fetch("/saved?name="+encodeURIComponent(document.querySelector("input").value))}</script></html>'
        self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
portal = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Portal)
threading.Thread(target=portal.serve_forever, daemon=True).start()
target = {'target_id': state['target_id'], 'tab_id': state['tabs'][0]['tab_id']}
cua.call('browser_navigate', {**target, 'url': 'http://127.0.0.1:'+str(portal.server_port)})
snapshot = cua.call('get_browser_state', {**target, 'snapshot_format': 'semantic_v2'})
field = next(x for x in snapshot['refs'] if x['role']=='textbox' and x['name']=='Name')
cua.call('browser_type', {**target, 'ref': field['ref'], 'text': 'Callie café ✓', 'replace': True})
snapshot = cua.call('get_browser_state', {**target, 'snapshot_format': 'semantic_v2'})
button = next(x for x in snapshot['refs'] if x['role']=='button' and x['name']=='Save')
with browser() as visible:
    page = next(p for c in visible.contexts for p in c.pages if str(portal.server_port) in p.url)
    page.get_by_role('button', name='Save', exact=True).click()
    assert page.locator('#result').inner_text() == 'Callie café ✓'
snapshot = cua.call('get_browser_state', {**target, 'snapshot_format': 'semantic_v2'})
import time
for attempt in range(20):
    if Portal.saved: break
    time.sleep(0.1)
assert Portal.saved == ['Callie café ✓'], {'server_received': Portal.saved, 'snapshot': snapshot}
assert 'Callie café ✓' in snapshot['outline'], snapshot
print('BROWSER_FORM_VERIFIED: Cua Unicode typing, trusted Playwright click, server receipt, DOM result')
from pathlib import Path
import os
native_file = Path.home() / ('callie-smoke-' + os.environ['CALLIOPE_COMPUTER_RUN'] + '.odt')
import zipfile
with zipfile.ZipFile(native_file, 'w') as archive:
    archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
    archive.writestr('content.xml', '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text><text:p>Before</text:p></office:text></office:body></office:document-content>')
    archive.writestr('META-INF/manifest.xml', '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
def saved_text():
    with zipfile.ZipFile(native_file) as archive:
        return archive.read('content.xml').decode()
cua.call('launch_app', {'name': 'libreoffice', 'additional_arguments': ['--norestore', '--nologo', '--nofirststartwizard', '--writer', str(native_file)]})
for attempt in range(50):
    windows = cua.call('list_windows')['windows']
    native = next((w for w in windows if native_file.stem in w['title']), None)
    if native: break
    time.sleep(0.2)
assert native, windows
native_target = {'target': {'kind': 'window', 'pid': native['pid'], 'window_id': native['window_id']}, 'delivery_mode': 'foreground'}
native_state = cua.call('get_window_state', {'pid': native['pid'], 'window_id': native['window_id']})
print('NATIVE_STATE_KEYS:', list(native_state))
cua.call('hotkey', {**native_target, 'keys': ['ctrl','a']})
cua.call('clipboard_write', {'text': 'Callie office café verified'})
cua.call('hotkey', {**native_target, 'keys': ['ctrl','v']})
cua.call('hotkey', {**native_target, 'keys': ['ctrl','s']})
for attempt in range(40):
    if 'Callie office café verified' in saved_text(): break
    time.sleep(0.2)
assert 'Callie office café verified' in saved_text(), 'Native edit was not saved'
print('NATIVE_OFFICE_VERIFIED:', str(native_file))
import time
time.sleep(32)
portal.shutdown()
''')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = runtime.job(job)
        if result['status'] != 'running':
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('Cua job did not settle within 120 seconds')
    print(json.dumps({k:v for k,v in result.items() if k!='code'}, indent=2))
    runtime.finish(run, 'Capability observation completed')
    manifest = root / 'computer' / 'runs' / run / 'video' / 'manifest.json'
    print(manifest.read_text())
    assert result['status'] == 'completed', result['stderr']
    assert json.loads(manifest.read_text())['segments'], 'No finalized video segment'
finally:
    runtime.close()
