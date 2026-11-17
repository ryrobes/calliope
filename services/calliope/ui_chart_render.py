"""Authenticated, bounded PNG + picking export for Calliope native clients.

No SQL or authored Python/JavaScript runs here. The caller supplies the already
resolved Vega-Lite data. Conversion uses the same pinned engine as desktop.
"""
import base64
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import ui_charts

_SLOTS = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
_CACHE = OrderedDict()
_CACHE_BYTES = 0
MAX_OUTPUT = 8_000_000
ROOT = Path(__file__).parent


class RenderBusy(Exception):
    pass


def render(actor, body):
    global _CACHE_BYTES
    if not isinstance(body, dict) or set(body) - {'spec', 'width', 'height'}:
        raise ValueError('Use spec, width and height')
    spec = ui_charts.validate(body.get('spec'))
    width, height = body.get('width'), body.get('height')
    if type(width) is not int or type(height) is not int or not 180 <= width <= 2048 or not 180 <= height <= 960:
        raise ValueError('Chart viewport must be width 180..2048 and height 180..960')
    payload = json.dumps(body, sort_keys=True, allow_nan=False).encode()
    # Actor-scoped, memory-only and short-lived; no persisted copy of business data.
    key = hashlib.sha256(actor.encode() + b'\0' + payload).digest()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            _CACHE.move_to_end(key)
            return cached[1]
    if not _SLOTS.acquire(timeout=2):
        raise RenderBusy('Chart renderer is busy; refresh to retry')
    try:
        with tempfile.TemporaryDirectory(prefix='calliope-chart-') as work:
            folder = Path(work)
            source, output = folder/'spec.json', folder/'chart.png'
            source.write_text(json.dumps(spec, allow_nan=False))
            with (folder/'stderr').open('wb') as errors:
                try:
                    process = subprocess.Popen([sys.executable, '-I', str(ROOT/'ui_chart_worker.py'),
                        str(source), str(output), str(ROOT/'theme/fonts'), str(width), str(height)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
                    started = time.monotonic()
                    while process.poll() is None:
                        # V8 reserves huge virtual address ranges, so RLIMIT_AS
                        # breaks startup. Bound resident memory instead on Linux.
                        status = Path(f'/proc/{process.pid}/status')
                        try:
                            rss = next((int(line.split()[1])*1024 for line in status.read_text().splitlines()
                                        if line.startswith('VmRSS:')), 0)
                        except FileNotFoundError:
                            rss = 0
                        if time.monotonic() - started > 12 or rss > 768_000_000 or (folder/'stderr').stat().st_size > 1_000_000:
                            raise ValueError('Chart rendering exceeded its resource budget')
                        time.sleep(.025)
                finally:
                    if 'process' in locals() and process.poll() is None:
                        process.kill()
                    if 'process' in locals(): process.wait()
            if process.returncode:
                # Avoid returning authored data or filesystem details from native errors.
                raise ValueError('Vega could not render this chart within its limits')
            geometry = folder/'chart.png.json'
            if output.stat().st_size > MAX_OUTPUT or geometry.stat().st_size > MAX_OUTPUT:
                raise ValueError('Chart output exceeds its size budget')
            png = output.read_bytes()
            scene = json.loads(geometry.read_bytes())
            if not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid chart image')
            value = {'png': base64.b64encode(png).decode('ascii'), 'scene': scene}
            size = len(json.dumps(value).encode())
        with _LOCK:
            old = _CACHE.pop(key, None)
            if old: _CACHE_BYTES -= old[2]
            while _CACHE and (_CACHE_BYTES + size > 32_000_000 or len(_CACHE) >= 32):
                _, old = _CACHE.popitem(last=False)
                _CACHE_BYTES -= old[2]
            _CACHE[key] = (time.monotonic(), value, size)
            _CACHE_BYTES += size
        return value
    finally:
        _SLOTS.release()


def register(m, server, authorize, respond):
    import asyncio
    import contextvars
    import os

    @m.custom_route('/api/calliope/ui-charts/render', methods=['POST'])
    async def chart(request):
        origin = request.headers.get('origin')
        allowed = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
        if (request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in allowed)
                or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
            return respond({'error': {'code': 'INVALID_ORIGIN'}}, 403)
        loop = asyncio.get_running_loop()
        actor, _, error = await loop.run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, authorize, request)
        if error: return error
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1_100_000:
                return respond({'error': {'code': 'TOO_LARGE'}}, 413)
        def run():
            try:
                result = render(actor, json.loads(raw))
                response = respond(result)
                response.headers['cache-control'] = 'no-store'
                return response
            except RenderBusy as exc:
                return respond({'error': {'code': 'CHART_BUSY', 'message': str(exc)}}, 503)
            except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
                return respond({'error': {'code': 'INVALID_CHART', 'message': str(exc)[:300]}}, 400)
        return await loop.run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)
