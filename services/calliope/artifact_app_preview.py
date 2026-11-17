"""Ephemeral app renders within artifact-work, scoped to work and viewer."""
from collections import OrderedDict
import contextvars
import hashlib
import json
import logging
from pathlib import Path
import shutil
import threading
import time
import re

import artifact_work
import ui_app_preview

_lock = threading.Lock()
_states, _pending = OrderedDict(), OrderedDict()
_worker = None
_last_cleanup = 0
LOG = logging.getLogger(__name__)
VARIANTS = {'web-desktop', 'web-phone', 'native-desktop', 'native-phone'}


def cleanup(config):
    """Bound private capture storage, including leftovers across restarts."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 120:
        return
    _last_cleanup = now
    root = Path(config.file_root) / 'artifact-work'
    for path in root.glob('*/app-*'):
        if re.fullmatch(r'app-[0-9a-f]{64}', path.name):
            try:
                if now - path.stat().st_mtime > 7200:
                    shutil.rmtree(path, ignore_errors=True)
            except FileNotFoundError:
                pass


def directory(config, session, item, subject):
    key = hashlib.sha256((item['key']+':'+str(subject)).encode()).hexdigest()
    return artifact_work.folder(config, session) / ('app-'+key)


def image_path(config, session, item, subject, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown app preview renderer or viewport')
    return directory(config, session, item, subject)/(variant+'.png')


def theme_for(conn_factory, owner):
    from calliope import _resolved_appearance_payload
    with conn_factory() as conn:
        row = conn.execute('SELECT color_mode,appearance,updated_at FROM rvbbit.calliope_appearance_preferences WHERE owner_email=%s', (owner,)).fetchone()
        workspace = conn.execute('SELECT appearance,color_mode,updated_at FROM rvbbit.calliope_workspace_settings WHERE singleton').fetchone()
    return _resolved_appearance_payload(row, workspace, owner)


def capture(document, destination, subject, theme):
    import server
    import ui_app_captures
    snapshot = ui_app_preview.request_snapshot(server, document, {'mode': 'server-read'}, subject)
    return ui_app_captures.matrix(document, snapshot, destination, theme=theme,
                                  launch=server._launch_playwright_chromium)


def _work():
    global _worker
    while True:
        with _lock:
            if not _pending:
                _worker = None
                return
            key, (state, revision, document, output, subject, theme, context) = _pending.popitem(last=False)
        work = output/'pending'
        try:
            result = context.run(capture, document, work, subject, theme)
            with _lock:
                # A slow old revision cannot replace any part of a newer one.
                if _states.get(key) is not state or state['wanted'] != revision:
                    continue
                state['status'] = 'ready' if result['ready'] else 'failed'
                state['errors'] = {}
                for variant, receipt in result['captures'].items():
                    source = work/(variant+'.png')
                    if receipt['ready'] and source.is_file():
                        image_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                        source.replace(output/(variant+'.png'))
                        state['captures'][variant] = {**receipt, 'capture_revision': revision, 'image_sha256': image_hash}
                    else:
                        state['errors'][variant] = receipt.get('error') or receipt.get('page_errors') or receipt.get('chart_errors') or 'Render did not become ready'
                (output/'evidence.json').write_text(json.dumps(result, indent=2))
        except Exception as exc:
            LOG.warning('App draft render failed (%s)', type(exc).__name__)
            with _lock:
                if _states.get(key) is state and state['wanted'] == revision:
                    state.update(status='failed', errors={'preview': str(exc)[:300]})
        finally:
            shutil.rmtree(work, ignore_errors=True)
            with _lock:
                if _states.get(key) is not state:
                    shutil.rmtree(output, ignore_errors=True)


def describe(config, session, item, document, subject, theme):
    global _worker
    base = f'/api/calliope/sessions/{session}/artifact-work/{item["key"]}'
    result = {'preview_url': base+'/preview'}
    if not document or item.get('phase') in {'published', 'stopped'}:
        return result
    key = (str(session), item['key'], str(subject))
    revision = ui_app_preview.digest([document, theme])
    output = directory(config, session, item, subject)
    cleanup(config)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _lock:
        state = _states.setdefault(key, {'wanted': None, 'captures': {}, 'errors': {}})
        _states.move_to_end(key)
        while len(_states) > 16:
            old, expired = _states.popitem(last=False)
            _pending.pop(old, None)
            if expired.get('output'):
                shutil.rmtree(expired['output'], ignore_errors=True)
        state['output'] = output
        if state['wanted'] != revision:
            state.update(wanted=revision, status='rendering')
            _pending[key] = (state, revision, document, output, subject, theme, contextvars.copy_context())
        if _worker is None and _pending:
            _worker = threading.Thread(target=_work, name='app-draft-render', daemon=True)
            _worker.start()
        result.update(render_status=state['status'], preview_errors=state['errors'], app_previews={
            variant: {**receipt, 'url': base+'/image?variant='+variant+'&v='+receipt['image_sha256'],
                      'stale': receipt['capture_revision'] != revision}
            for variant, receipt in state['captures'].items()})
    return result
