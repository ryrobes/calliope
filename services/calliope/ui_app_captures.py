"""Capture both renderers from one resolved app preview, with no business I/O."""
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import urllib.parse
import urllib.request

import ui_apps
from ui_app_preview import digest, viewport


def web(snapshot, output, size, theme, launch=None, *, full_page=False):
    from playwright.sync_api import sync_playwright
    boot = json.dumps(snapshot, allow_nan=False).replace('<', '\\u003c')
    source = ui_apps.shell().replace('<div id="root">',
        '<script type="application/json" id="calliope-preview-snapshot">' + boot + '</script><div id="root">')
    root = Path(__file__).parent
    errors = []
    with sync_playwright() as p:
        browser = launch(p) if launch else p.chromium.launch()
        try:
            context = browser.new_context(viewport={k: size[k] for k in ('width', 'height')}, device_scale_factor=1)
            def handle(route):
                path = urllib.parse.urlparse(route.request.url).path
                if path == '/':
                    return route.fulfill(status=200, content_type='text/html', body=source)
                if path == '/api/calliope/appearance':
                    return route.fulfill(status=200, content_type='application/json', body=json.dumps(theme))
                for prefix, directory in [('/ui-app-assets/', ui_apps.ROOT), ('/theme/fonts/', root/'theme/fonts')]:
                    if path.startswith(prefix):
                        asset = (directory/path[len(prefix):]).resolve()
                        if asset.is_relative_to(directory.resolve()) and asset.is_file():
                            return route.fulfill(status=200, body=asset.read_bytes(), content_type=mimetypes.guess_type(str(asset))[0] or 'application/octet-stream')
                route.abort()
            context.route('**/*', handle)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)[:500]))
            page.goto('https://app-preview.invalid/', wait_until='load')
            settled = False
            try:
                page.wait_for_function("document.querySelector('main[data-preview-ready=\"true\"]') || document.querySelector('.notice.error')?.textContent.trim()", timeout=15000)
                problem = page.locator('.notice.error').all_text_contents()
                if problem:
                    raise ValueError('App preview: ' + ' '.join(problem))
                page.evaluate('document.fonts.ready')
                # Charts repaint after a ResizeObserver debounce. Two settled
                # frames plus this debounce window exclude an initial empty SVG.
                page.wait_for_timeout(250)
                page.wait_for_function("[...document.querySelectorAll('.vega-plot')].every(e=>e.dataset.renderState==='ready'||e.dataset.renderState==='failed')", timeout=20000)
                page.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                settled = True
            except Exception as exc:
                errors.append(str(exc).splitlines()[0][:300])
            chart_errors = page.locator('.c-vega [role="alert"]').all_text_contents()
            page.screenshot(path=str(output), animations='disabled', full_page=full_page)
            return {'renderer': 'Web', 'platform': 'chromium-isolated', 'viewport': size,
                    'renderer_build': digest(ui_apps.shell()), 'theme_sha256': digest(theme),
                    'preview': snapshot['preview'], 'ready': snapshot['preview']['ready'] and settled and not errors and not chart_errors,
                    'page_errors': errors, 'chart_errors': chart_errors}
        finally:
            browser.close()


def published(server, row, output, width, height, *, full_page=False):
    """Capture the exact saved definition using governed reads and inert actions."""
    import artifact_app_preview
    from ui_app_preview import request_snapshot
    subject = server._require_application_subject().subject
    document = ui_apps.definition(row['source_files'])
    snapshot = request_snapshot(server, document, {'mode': 'server-read'}, subject)
    theme = artifact_app_preview.theme_for(server._conn, subject)
    size = {'preset': 'custom', 'width': width, 'height': height}
    return web(snapshot, output, size, theme, server._launch_playwright_chromium,
               full_page=full_page)


def thumbnail_path(server, kind, slug, actor):
    """Resolved app data belongs to this viewer; never use the shared HTML shot."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('App thumbnails require a signed-in viewer')
    audience = hashlib.sha256(actor.strip().lower().encode()).hexdigest()
    return server._live_app_capture_root()/'thumbs'/kind/'viewers'/audience/(slug+'.jpg')


def thumbnail_stale(server, kind, slug, actor, updated_at):
    path = thumbnail_path(server, kind, slug, actor)
    return not path.is_file() or path.stat().st_mtime < updated_at.timestamp()


def thumbnail(server, row, kind, slug, actor):
    from PIL import Image
    path = thumbnail_path(server, kind, slug, actor)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source, pending = path.with_suffix('.tmp.png'), path.with_suffix('.tmp.jpg')
    token = server._SESSION_SUB.set(actor)
    try:
        server._artifact_require_view(slug, caller_override=actor)
        # Render at desktop proportions, then downsample for the Gallery card.
        result = published(server, row, source, 1280, 800)
        if not result.get('ready'):
            raise RuntimeError('App capture incomplete; keeping the previous thumbnail')
        with Image.open(source) as image:
            image.convert('RGB').resize((server._THUMB_W, server._THUMB_H),
                                       Image.Resampling.LANCZOS).save(pending, quality=server._THUMB_Q)
        # A publication or access change during rendering must not expose an old
        # revision as a fresh authorized image.
        server._artifact_require_view(slug, caller_override=actor)
        app, latest = server._load_live_app_version(slug)
        if not app or app.get('runtime_kind') != 'a2ui' or latest['version'] != row['version']:
            return
        pending.chmod(0o600)
        pending.replace(path)
    finally:
        server._SESSION_SUB.reset(token)
        source.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)


def native(document, snapshot, theme, presets):
    """The private worker gets resolved data, never an instance credential."""
    url = os.environ.get('CALLIOPE_UI_STUDIO_URL', 'http://ui-studio:18456').rstrip('/')
    token_path = os.environ.get('CALLIOPE_UI_STUDIO_TOKEN_FILE', '/run/secrets/studio-token')
    token = Path(token_path).read_text().strip()
    value = {**snapshot, 'previewDefinition': document}
    request = urllib.request.Request(url+'/v1/run', json.dumps({
        'op': 'render', 'document': value, 'theme': theme, 'viewports': presets}).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=180) as response:
        result = json.load(response)
    for capture in result['captures'].values():
        capture['preview'] = snapshot['preview']
        capture['ready'] = capture['ready'] and snapshot['preview']['ready']
        capture['theme_sha256'] = digest(theme)
    return result


def matrix(document, snapshot, directory, *, theme=None, presets=('desktop', 'phone'), launch=None):
    """Evidence files are private; readiness belongs to each renderer separately."""
    theme = theme or {}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    captures = {}
    for name in presets:
        size = viewport(name)
        path = directory/f'web-{name}.png'
        captures['web-'+name] = web(snapshot, path, size, theme, launch)
    try:
        result = native(document, snapshot, theme, list(presets))
        for name, capture in result['captures'].items():
            (directory/f'native-{name}.png').write_bytes(base64.b64decode(capture.pop('data'), validate=True))
            captures['native-'+name] = capture
    except Exception as exc:
        # Web evidence remains useful, but absence of native evidence is visible.
        for name in presets:
            captures['native-'+name] = {'ready': False, 'renderer': 'GPUI',
                'platform': 'linux-isolated', 'viewport': viewport(name), 'error': str(exc)[:300]}
    result = {'preview': snapshot['preview'], 'captures': captures,
              'ready': snapshot['preview']['ready'] and all(c['ready'] for c in captures.values())}
    (directory/'evidence.json').write_text(json.dumps(result, indent=2)+'\n')
    return result
