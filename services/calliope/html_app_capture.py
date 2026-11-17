"""Viewer-owned HTML thumbnails with a narrow, read-only company-app bridge."""
import json
import re
from urllib.parse import urlsplit


def app_read(server, actor, slug, body):
    import ui_apps
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', slug):
        raise ValueError('Invalid app')
    if not isinstance(body, dict) or set(body) - {'version', 'state', 'record'}:
        raise ValueError('Captures may read app data; commands are disabled')
    server._artifact_require_view(slug, caller_override=actor)
    return ui_apps.snapshot(server, slug, actor, body)


def install(page, origin, runner):
    """Do not lend the isolated page a session or service bearer."""
    prefix = origin.rstrip('/') + '/api/apps/'
    def handle(route):
        headers = {'Access-Control-Allow-Origin': 'null', 'Access-Control-Allow-Methods': 'POST',
                   'Access-Control-Allow-Headers': 'content-type', 'Cache-Control': 'no-store'}
        request = route.request
        if request.method == 'OPTIONS':
            return route.fulfill(status=204, headers=headers)
        try:
            path = urlsplit(request.url).path
            match = re.fullmatch('/api/apps/([a-zA-Z0-9_-]{1,160})/ui', path)
            if request.method != 'POST' or not match or len(request.post_data or '') > 160000:
                raise ValueError('Unsupported capture read')
            result = runner(match[1], json.loads(request.post_data or '{}'))
            return route.fulfill(status=200, headers=headers, content_type='application/json', body=json.dumps(result, default=str))
        except Exception:
            return route.fulfill(status=403, headers=headers, content_type='application/json',
                                 body='{"error":{"message":"App read unavailable to this viewer during capture"}}')
    page.route(prefix + '*/ui', handle)


def thumbnail(server, row, kind, slug, actor):
    import ui_app_captures
    token = server._SESSION_SUB.set(actor)
    path = ui_app_captures.thumbnail_path(server, kind, slug, actor)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp.jpg')
    try:
        server._artifact_require_view(slug, caller_override=actor)
        result = server._capture_html_with_playwright(server._capturable_dashboard_html(slug, row), tmp,
            width=server._THUMB_W, height=server._THUMB_H, full_page=False, wait_ms=1200,
            quality=server._THUMB_Q, app_read_runner=lambda target, body: app_read(server, actor, target, body))
        if (not (result.get('readiness') or {}).get('ready') or (result.get('readiness') or {}).get('failed')
                or any(result.get(k) for k in ('page_errors', 'runtime_errors', 'render_issues'))):
            raise RuntimeError('Capture incomplete; keeping the previous thumbnail')
        server._artifact_require_view(slug, caller_override=actor)
        _, current = server._load_live_app_version(slug)
        if current and current['version'] == row['version']:
            tmp.chmod(0o600)
            tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
        server._SESSION_SUB.reset(token)
