"""Ephemeral draft screenshots using the normal artifact runtime, never publication."""
from collections import OrderedDict
import contextvars
import hashlib
import json
import logging
import threading
import time

_lock = threading.Lock()
_cache_lock = threading.Lock()
_states = OrderedDict()
_pending = OrderedDict()
_worker = None
MAX_STATES = 16
MIN_RENDER_INTERVAL = 4.0
FOLLOWUP_DELAY_MS = 5000
LOG = logging.getLogger(__name__)


def _identity(session_id, item, subject):
    return str(session_id), item['key'], str(subject)


def _destination(config, session_id, item, subject):
    from artifact_work import folder
    name = hashlib.sha256((item['key'] + ':' + str(subject)).encode()).hexdigest()
    return folder(config, session_id) / (name + '.png')


def _capture(source, output, subject, owner, cache, *, on_frame=None, should_continue=None):
    import server
    calls = 0

    def query(sql, opts):
        nonlocal calls
        key = json.dumps([sql, opts], sort_keys=True, default=str)
        with _cache_lock:
            prior = cache.get(key)
            if prior and time.monotonic() - prior[0] < 120:
                return prior[1]
            calls += 1
            if calls > 16:
                return {'error': {'message': 'Draft preview query budget reached'}}
        token = server._SESSION_SUB.set(subject)
        started = time.monotonic()
        try:
            value = server.tool_run_sql(sql, as_of=opts.get('as_of'), limit=opts.get('limit'),
                                        _max_rows=server.ARTIFACT_ROW_CAP, _statement_timeout_ms=5000)
        finally:
            server._SESSION_SUB.reset(token)
        server._record('run_sql', {'sql': sql, 'origin': 'artifact_draft_preview'}, value,
                       value.get('error') if isinstance(value, dict) else None,
                       int((time.monotonic()-started)*1000), caller_override=owner)
        encoded = json.dumps(value, default=str)
        with _cache_lock:
            # Only successful bounded results survive the next render. No
            # credentials, browser sessions, or other viewers share this cache.
            if len(encoded) < 1024*1024 and isinstance(value, dict) and not value.get('error'):
                cache[key] = (time.monotonic(), json.loads(encoded))
                while len(cache) > 8:
                    cache.popitem(last=False)
        return value

    def followup(page, shot, telemetry):
        on_frame(1)
        # Keep this document and its running queries alive. Sleeping in Python
        # would stop Playwright dispatching the query bridge's JS callbacks.
        remaining = FOLLOWUP_DELAY_MS
        while remaining > 0:
            if should_continue is not None and not should_continue():
                return
            step = min(remaining, 100)
            page.wait_for_timeout(step)
            remaining -= step
        if should_continue is not None and not should_continue():
            return
        # Re-evaluate the diagnostics too: a first-frame "still loading" banner
        # must not be burned into the completed follow-up.
        page.evaluate("document.querySelectorAll('[data-rvbbit-capture-diagnostic]').forEach(el => el.remove())")
        telemetry['readiness'] = server._wait_for_capture_bridge(page, 500)
        telemetry['theme'] = server._capture_theme_state(page)
        telemetry['dashboard'] = page.evaluate('window.rvbbitDashboard?.check() || null')
        telemetry['render_issues'] = server._capture_render_issues(page)
        telemetry['annotation'] = server._annotate_incomplete_capture(page, telemetry)
        if should_continue is not None and not should_continue():
            return
        page.screenshot(**shot)
        on_frame(2)

    document = server._capturable_dashboard_html('draft-preview', {'html': source, 'manifest': {}, 'name': 'Draft preview'})
    return server._capture_html_with_playwright(document, output, 1280, 800, False, 400,
                                                quality=82, data_timeout_ms=12000, query_runner=query,
                                                after_capture=followup if on_frame is not None else None)


def _capture_layout(source, output):
    """Rasterize incoming, sanitized layout without executing draft code or SQL."""
    from playwright.sync_api import sync_playwright
    from server import _launch_playwright_chromium
    from artifact_progress import draft_html
    document = draft_html(source)
    if not document:
        raise ValueError('No draft layout yet')
    with sync_playwright() as p:
        browser = _launch_playwright_chromium(p)
        try:
            context = browser.new_context(viewport={'width': 1040, 'height': 650},
                                          java_script_enabled=False, offline=True)
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(document, wait_until='load', timeout=5000)
            # With script execution disabled, Chromium's normal animation-frame
            # readiness probe cannot run. Give its compositor one paint window.
            page.wait_for_timeout(150)
            page.screenshot(path=str(output), animations='disabled', timeout=5000)
        finally:
            browser.close()


def _work():
    global _worker
    while True:
        with _lock:
            if not _pending:
                _worker = None
                return
            key, (state, digest, source, output, subject, owner, context, kind) = _pending.popitem(last=False)
        temp = output.with_suffix('.rendering.png')
        def current():
            with _lock:
                return _states.get(key) is state and state['wanted'] == digest

        def publish(frame):
            with _lock:
                # Keep first frames during continuous edits so they cannot
                # starve previews. Only the current edit gets a follow-up.
                if _states.get(key) is not state or (frame == 2 and state['wanted'] != digest):
                    return
                temp.replace(output)
                state.update(ready=f'{digest}-{frame}', ready_kind=kind,
                             status='ready' if state['wanted'] == digest and (kind == 'layout' or frame == 2) else 'rendering')

        try:
            if kind == 'layout':
                context.run(_capture_layout, source, temp)
                publish(1)
            else:
                context.run(_capture, source, temp, subject, owner, state['cache'],
                            on_frame=publish, should_continue=current)
        except Exception as exc:
            LOG.warning('Artifact draft render failed (%s)', type(exc).__name__)
            with _lock:
                if _states.get(key) is state and state['wanted'] == digest:
                    state['status'] = 'failed'
        finally:
            temp.unlink(missing_ok=True)


def describe(config, session_id, item, source, subject, owner):
    """Start/coalesce a render without blocking SSE or browser polling."""
    global _worker
    if item.get('kind') == 'image':
        return {}  # Waiting for a provider image never starts a browser capture.
    kind = 'render' if source else 'layout'
    source = source or item.get('preview_html')
    if not source or item.get('phase') in {'published', 'stopped'}:
        return {}
    key = _identity(session_id, item, subject)
    digest = hashlib.sha256((kind + ':' + source).encode('utf-8')).hexdigest()
    output = _destination(config, session_id, item, subject)
    with _lock:
        state = _states.setdefault(key, {'wanted': None, 'ready': None, 'cache': OrderedDict()})
        _states.move_to_end(key)
        while len(_states) > MAX_STATES:
            old, _ = _states.popitem(last=False)
            _pending.pop(old, None)
        if state['wanted'] != digest:
            state.update(wanted=digest, status='rendering')
        now = time.monotonic()
        if state.get('scheduled') != digest and (key in _pending or now >= state.get('next_at', 0)):
            if key not in _pending:
                state['next_at'] = now + MIN_RENDER_INTERVAL
            state['scheduled'] = digest
            _pending[key] = (state, digest, source, output, subject, owner, contextvars.copy_context(), kind)
        if _worker is None and _pending:
            _worker = threading.Thread(target=_work, name='artifact-draft-render', daemon=True)
            _worker.start()
        result = {'render_status': state['status']}
        if state['ready'] and output.is_file():
            result['render_url'] = f'/api/calliope/sessions/{session_id}/artifact-work/{item["key"]}/image?v={state["ready"]}'
            result['render_kind'] = state['ready_kind']
        return result


def image_path(config, session_id, item, subject):
    return _destination(config, session_id, item, subject)
