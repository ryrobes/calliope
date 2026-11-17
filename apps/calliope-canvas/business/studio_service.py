#!/usr/bin/env python3
"""Authenticated, bounded Lab runner. Only disposable fixtures live here."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import studio
from presentation import SPEC, VIEWS
from studio_document import BUILTINS, normalize

ROOT = Path(os.environ.get('STUDIO_ROOT', '/var/lib/calliope-ui-studio'))
LOCK = threading.RLock()
MAX_REQUEST = 4 * 1024 * 1024
TTL = 3600


def stop(directory):
    path = directory / 'session.json'
    if path.exists():
        session = studio.read_json(path)
        for pid in reversed(session['pids']):
            studio.stop_owned(session, pid)


def reap(root, now=None):
    now = now or time.time()
    for directory in root.iterdir():
        if directory.is_dir() and now - directory.stat().st_mtime > TTL:
            stop(directory)
            shutil.rmtree(directory)


def run(body, root=ROOT):
    if not isinstance(body, dict):
        raise ValueError('Expected a command object')
    op = body.get('op')
    if op == 'render':
        # One bounded, self-cleaning capture job; no user display or business
        # credentials are involved. Both widths use the same model and theme.
        from studio_contract import viewport
        document = body.get('document')
        loaded = normalize(document)
        theme = body.get('theme', {})
        if not isinstance(theme, dict) or len(json.dumps(theme).encode()) > 64000:
            raise ValueError('Theme must be a bounded appearance object')
        presets = body.get('viewports', ['desktop', 'phone'])
        if not isinstance(presets, list) or not 1 <= len(presets) <= 3 or len(set(presets)) != len(presets):
            raise ValueError('Choose one to three distinct viewports')
        sizes = [viewport(p) for p in presets]
        session = 'capture-' + uuid.uuid4().hex
        try:
            run({'op': 'start', 'session': session, 'native': True}, root)
            run({'op': 'load', 'session': session, 'document': document, 'theme': theme}, root)
            captures = {v['preset']: run({'op': 'screenshot', 'session': session, 'viewport': v['preset']}, root) for v in sizes}
            return {'preview': loaded.get('preview'), 'captures': captures,
                    'ready': all(c['ready'] for c in captures.values())}
        finally:
            directory = root / session
            if directory.exists():
                stop(directory)
                shutil.rmtree(directory)
    if op == 'catalog':
        from patterns import PAGE_PATTERN, workspace_template
        import studio_contract
        import ui_apps
        return {'catalog': SPEC, 'builtinProperties': {k: sorted(v) for k,v in BUILTINS.items()},
                'views': VIEWS, 'pagePattern': PAGE_PATTERN,
                'starter': json.loads(ui_apps.template()['source_files']['app.json']),
                'legacyFixtureStarter': workspace_template(), 'viewports': studio_contract.VIEWPORTS}
    if op == 'check':
        snapshot = normalize(body.get('document'))
        return {'valid': True, 'components': len(studio.nodes(snapshot)[0]), 'mode': 'inert-preview',
                **({'preview': snapshot['preview']} if snapshot.get('preview') else {})}
    if op == 'example':
        view = body.get('view', 'dispatch')
        if view not in VIEWS:
            raise ValueError('Unknown example view')
        from server import Store
        with tempfile.TemporaryDirectory() as work:
            snapshot = normalize(Store(Path(work) / 'fixture.sqlite').snapshot(view=view))
        components, data = studio.nodes(snapshot)
        return ({'components': snapshot['baseComponents'], 'data':{k:v for k,v in data.items() if k!='resources'}, 'reactive':snapshot['reactive'],'title':view+' example'} if snapshot.get('reactive') else {'components': components, 'data': data, 'title': view + ' example'})
    session_id = body.get('session')
    if not isinstance(session_id, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', session_id):
        raise ValueError('Session must be 1..64 letters, digits, dash or underscore')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    reap(root)
    directory = root / session_id
    if op == 'start':
        if directory.exists():
            raise ValueError('Session already exists; stop it or use another ID')
        if sum(p.is_dir() for p in root.iterdir()) >= 4:
            raise ValueError('Four sessions are already active; stop one first')
        directory.mkdir(mode=0o700)
    elif not (directory / 'session.json').is_file():
        raise ValueError('Unknown or expired session')
    if op == 'stop':
        stop(directory)
        shutil.rmtree(directory)
        return {'stopped': session_id}
    command = [sys.executable, str(studio.ROOT / 'studio.py'), '--session', str(directory)]
    if not body.get('full'):
        command.append('--compact')
    if op == 'start':
        command += ['start'] + (['--native'] if body.get('native') else [])
    elif op in ('inspect', 'offline', 'online', 'restart-native'):
        command.append(op)
    elif op in ('fill', 'press', 'decision', 'fixture_event'):
        ident = body.get('id')
        if not isinstance(ident, str) or len(ident) > 120:
            raise ValueError('Component ID required')
        command += [op, ident]
        if op == 'decision':
            if not isinstance(body.get('value'),str) or len(body['value'])>100: raise ValueError('Variant label required')
            command.append(body['value'])
        if op == 'fill':
            command.append(json.dumps(body.get('value')))
    elif op == 'load':
        snapshot = normalize(body.get('document'))
        if 'theme' in body:
            snapshot['previewTheme'] = body['theme']
        studio.write_json(directory / 'definition.json', snapshot)
        command += ['load', str(directory / 'definition.json')]
    elif op == 'scenario':
        steps = body.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 40 or any(not isinstance(s,dict) or s.get('op') not in ('fill','press','decision','assert','assert_event','assert_data','fixture_event') for s in steps):
            raise ValueError('Scenario requires 1..40 fill/press/assert/assert_event steps')
        studio.write_json(directory / 'scenario.json', steps)
        command += ['scenario', str(directory / 'scenario.json')]
    elif op == 'screenshot':
        from studio_contract import viewport
        size = viewport(body.get('viewport', 'desktop'), body.get('width'), body.get('height'))
        width, height, scroll = size['width'], size['height'], body.get('scroll', 0)
        if any(type(v) is not int for v in (width,height,scroll)):
            raise ValueError('Capture dimensions must be integers')
        command += ['screenshot', str(directory / 'capture.png'), '--width', str(width), '--height', str(height), '--scroll', str(scroll)]
    elif op == 'attach':
        if not isinstance(body.get('id'),str):
            raise ValueError('Attachment component ID required')
        name = body.get('name', '')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_. -]{1,120}',name) or name.startswith('.'):
            raise ValueError('Use a simple evidence filename')
        content = base64.b64decode(body.get('data',''),validate=True)
        if not 0 < len(content) <= 2*1024*1024:
            raise ValueError('Evidence must be 1 byte..2 MB')
        files = directory / 'uploads'; files.mkdir(exist_ok=True)
        path = files / name; path.write_bytes(content)
        command += ['attach', body['id'], str(path)]
    else:
        raise ValueError('Unsupported Studio operation')
    os.utime(directory, None)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if completed.returncode:
            try:
                error = json.loads(completed.stderr.splitlines()[-1])['error']
            except (ValueError, KeyError, IndexError):
                error = completed.stderr[-1000:]
            raise ValueError(error or 'Studio operation failed')
        result = json.loads(completed.stdout)
        if op == 'start':
            return {'session': session_id, 'native': bool(body.get('native')), 'idle_expiry_seconds': TTL, 'scope': 'disposable SQL fixture'}
        if op == 'screenshot':
            raw = (directory / 'capture.png').read_bytes()
            result = {**{k:v for k,v in result.items() if k!='screenshot'}, 'mime': 'image/png', 'data': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
            loaded = studio.read_json(directory / 'snapshot.json')
            result.update(viewport=size, platform='linux-isolated',
                          renderer_build=os.environ.get('STUDIO_REVISION', 'development'),
                          preview=loaded.get('preview'))
        if op == 'inspect' and body.get('id'):
            # A compact subtree/prefix read avoids emitting every form on each edit.
            result['nodes'] = [n for n in result.get('nodes', []) if n['id'].startswith(str(body['id']))]
        return result
    except Exception:
        if op == 'start':
            stop(directory)
            shutil.rmtree(directory)
        raise
    finally:
        if op == 'attach':
            path.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CalliopeUIStudio/1'

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass  # Never log definitions, commands or authorization headers.

    def do_GET(self):
        self.reply(200 if self.path == '/health' else 404, {'service': 'calliope-ui-studio', 'revision': os.environ.get('STUDIO_REVISION','development')})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer ' + self.server.token):
            return self.reply(401, {'error':'Studio authorization required'})
        if self.path != '/v1/run':
            return self.reply(404, {'error':'Unknown endpoint'})
        if self.headers.get('Origin'):
            return self.reply(403, {'error':'Studio is an agent API, not a browser endpoint'})
        try:
            self.connection.settimeout(15)
            length = int(self.headers.get('Content-Length','0'))
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json' or not 0 < length <= MAX_REQUEST:
                return self.reply(413, {'error':'Expected JSON body of at most 4 MB'})
            body = json.loads(self.rfile.read(length))
            with LOCK:
                result = run(body)
            self.reply(200, result)
        except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
            self.reply(400, {'error':str(exc)[:1000]})


def main():
    os.umask(0o077)
    Path(os.environ.get('XDG_RUNTIME_DIR','/tmp/studio-runtime')).mkdir(mode=0o700,parents=True,exist_ok=True)
    token = Path(os.environ['STUDIO_TOKEN_FILE']).read_text().strip()
    if len(token) < 32:
        raise SystemExit('Studio token must contain at least 32 characters')
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    # A container restart invalidates all PIDs and sessions. Do not signal stale
    # PIDs from another namespace; only reclaim private disposable work files.
    for directory in ROOT.iterdir():
        if directory.is_dir(): shutil.rmtree(directory)
    server = ThreadingHTTPServer(('0.0.0.0',18456), Handler)
    server.token = token
    def janitor():
        while True:
            time.sleep(30)
            with LOCK: reap(ROOT)
    threading.Thread(target=janitor,daemon=True).start()
    server.serve_forever()


if __name__ == '__main__': main()
