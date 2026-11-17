"""Private visual feedback using the real Stage renderer, without a Desktop.

The browser has no credentials or network access. It receives only authorized
surfaces; chart reads execute through the normal viewer-scoped SQL bridge.
"""
import asyncio
import json
import mimetypes
import math
import re
import threading
import time
import uuid
import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, unquote

SLOTS = threading.BoundedSemaphore(2)
ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://stage-capture.calliope.invalid'


def load(conn_factory, surface_id, owner):
    import calliope
    sid = str(uuid.UUID(str(surface_id)))
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s::uuid AND kind IN ('chart','board') "
            "AND rvbbit.calliope_session_can_view(session_id,%s,false)", (sid, owner),
        ).fetchone()
        if not row:
            raise LookupError('Chart or board is not available in this chat')
        members = (row.get('payload') or {}).get('members', []) if row['kind'] == 'board' else []
        children = conn.execute(
            "SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s AND id=ANY(%s::uuid[])",
            (row['session_id'], members),
        ).fetchall() if members else []
    return calliope._surface_json(row), [calliope._surface_json(r) for r in children]


def render(target, members, owner, output_root, *, timeout_ms=45000):
    import calliope
    import server
    from playwright.sync_api import sync_playwright
    if not SLOTS.acquire(timeout=10):
        raise TimeoutError('Stage renderer is busy; publication is saved')
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    prefix = output_root / ('stage-' + target['id'] + '-' + uuid.uuid4().hex[:10])
    surfaces = {s['id']: s for s in [target, *members]}
    errors, reports = [], {}
    unsupported = [{'surface_id': s['id'], 'kind': s['kind']} for s in members
                   if s['kind'] not in {'chart', 'query', 'answer'}]
    query_pool = ThreadPoolExecutor(max_workers=2)
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        with sync_playwright() as pw:
            browser = server._launch_playwright_chromium(pw)
            try:
                page = browser.new_page(viewport={'width': 1200, 'height': 900}, device_scale_factor=1)
                page.on('pageerror', lambda e: errors.append(str(e)[:500]))
                page.add_init_script('window.__calliopeStageCapture=' + json.dumps({
                    'target': target['id'], 'session_id': target['session_id'],
                    'surfaces': list(surfaces.values()), 'owner': owner,
                }, default=str))

                def route(r):
                    url = urlsplit(r.request.url)
                    if url.scheme + '://' + url.netloc != ORIGIN:
                        r.abort(); return
                    path = unquote(url.path)
                    match = re.fullmatch(r'/api/calliope/surfaces/([a-f0-9-]+)/(chart-data|chart-render)', path)
                    if match:
                        sid, kind = match.groups()
                        surface = surfaces.get(sid)
                        if not surface or surface['kind'] != 'chart':
                            r.fulfill(status=404, json={'error': 'Unknown chart'}); return
                        if kind == 'chart-render':
                            reports[sid] = r.request.post_data_json
                            r.fulfill(json={'accepted': True}); return
                        try:
                            # Playwright's sync dispatcher runs an event loop;
                            # execute the async SQL bridge in a separate worker.
                            ctx = contextvars.copy_context()
                            remaining = max(.1, deadline - time.monotonic())
                            data = query_pool.submit(ctx.run, lambda: asyncio.run(server.stage_chart_data(surface, owner))).result(timeout=remaining)
                            # Match the normal chart-data HTTP boundary, which
                            # serializes PostgreSQL Decimal/date values as strings.
                            r.fulfill(body=json.dumps({**data, 'render_attempt': 'capture-' + sid}, default=str),
                                      content_type='application/json')
                        except Exception as exc:
                            r.fulfill(status=500, json={'error': {'message': str(exc)[:500]}})
                        return
                    if path == calliope._CHART_INTERPRETER_ROUTE:
                        r.fulfill(body=calliope._chart_interpreter_script(), content_type='text/javascript'); return
                    if path.startswith('/api/'):
                        r.fulfill(json={}); return
                    if path == '/calliope':
                        html = re.sub(r'__CALLIOPE_[A-Z_]+__', '', (ROOT/'calliope/index.html').read_text())
                        r.fulfill(body=html, content_type='text/html'); return
                    file = (ROOT / path.lstrip('/')).resolve()
                    if file.is_relative_to(ROOT) and file.is_file() and path.startswith(('/calliope/', '/theme/', '/vendor/')):
                        r.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
                    else:
                        r.fulfill(status=404)

                page.route('**/*', route)
                page.goto(ORIGIN + '/calliope', wait_until='domcontentloaded')
                page.wait_for_selector('[data-stage-capture-root]', timeout=15000)
                settled = True
                try:
                    page.wait_for_function("""() => window.__stageCaptureMounted &&
                      [...document.querySelectorAll('[data-chart-surface]')].every(e=>e.matches('.is-ready,.is-error')) &&
                      [...document.querySelectorAll('iframe[data-query-result]')].every(f=>{
                        const d=f.contentDocument;return d?.body.classList.contains('data-viewer') &&
                          !d.body.textContent.includes('Loading saved result');
                      })""", timeout=max(1, (deadline - time.monotonic()) * 1000))
                    page.evaluate('document.fonts.ready')
                    page.wait_for_timeout(250)
                except Exception:
                    settled = False
                root = page.locator('[data-stage-capture-root]')
                box = root.bounding_box()
                if not box or box['height'] < 50:
                    raise RuntimeError('Stage renderer produced no measurable surface')
                height = min(6000, math.ceil(box['height']))
                full = str(prefix) + '-overview.jpg'
                page.screenshot(path=full, type='jpeg', quality=78, full_page=True,
                                clip={'x': 0, 'y': 0, 'width': 1200, 'height': height})
                from PIL import Image
                with Image.open(full) as check:
                    if check.width != 1200 or abs(check.height - height) > 1:
                        raise RuntimeError('Stage capture did not cover the requested board area')
                    height = check.height
                # Tall boards get legible strips as well as an overview. Report
                # clipped coverage explicitly; never pretend a viewport is all.
                paths = [full]
                if height > 1400:
                    with Image.open(full) as im:
                        im.resize((600, max(1, round(height / 2)))).save(full, quality=75)
                        for n, top in enumerate(range(0, height, 1000)):
                            path = str(prefix) + f'-section-{n+1}.jpg'
                            im.crop((0, top, 1200, min(top + 1000, height))).save(path, quality=78)
                            paths.append(path)
                diagnostic = page.locator('.is-error .chart-status, .board-cell-error').all_text_contents()
                errors.extend(diagnostic)
                errors.extend(str(v.get('message'))[:500] for v in reports.values() if v.get('status') == 'error')
                return {'surface_id': target['id'], 'kind': target['kind'], 'width': 1200,
                        'height': int(box['height']), 'captured_height': height,
                        'clipped': box['height'] > 6000, 'paths': paths,
                        'status': 'error' if errors else 'partial' if unsupported else 'ready' if settled else 'pending',
                        'unsupported_members': unsupported,
                        'errors': errors[:8], 'charts': reports,
                        'message': 'Actual Stage rendering. Overview first, followed by top-to-bottom sections for tall boards. '
                                   'Inspect layout and data; a ready render is not proof of analytical correctness. '
                                   'If your harness exposes the attached images as MEDIA paths, use vision_analyze on those paths to inspect them. '
                                   'Do not open your chat on the Desktop to inspect this surface.'}
            finally:
                browser.close()
    finally:
        query_pool.shutdown(wait=False, cancel_futures=True)
        SLOTS.release()


def capture(conn_factory, surface_id, owner, output_root):
    target, members = load(conn_factory, surface_id, owner)
    return render(target, members, owner, output_root)
