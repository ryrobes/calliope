"""Bounded build telemetry, shared by Hermes and Calliope.

Raw arguments and reasoning never cross this boundary. preview_html is inert;
render_html is server-only authored HTML for capture and must be removed before
returning browser metadata. Neither becomes a published artifact.
"""
import html
import json
from html.parser import HTMLParser
import math
import re

MAX_HTML = 120_000
PHASES = {'composing', 'writing', 'preview', 'checking', 'publishing', 'published', 'failed', 'stopped', 'generating', 'image_ready'}
SENSITIVE = re.compile(r'(?i)(?:bearer\s+\S+|(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"\']?[^\s\"\'<>;,]+)')
ALLOWED_TAGS = set('html head body title style div span p main section article aside header footer nav h1 h2 h3 h4 h5 h6 b strong em i small label table thead tbody tfoot tr th td caption colgroup col ul ol li dl dt dd pre code blockquote br hr svg g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop clippath'.split())
ATTRS = set('class id style width height viewbox d points fill stroke stroke-width opacity transform x y x1 x2 y1 y2 cx cy r rx ry offset stop-color stop-opacity text-anchor font-size font-family colspan rowspan'.split())


class DraftHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.blocked, self.in_style = [], [], False
        self.in_head = self.in_title = self.has_content = False

    def handle_starttag(self, tag, attrs):
        if self.blocked or tag in {'script', 'iframe', 'object', 'embed', 'template', 'noscript', 'foreignobject'}:
            if tag not in {'embed', 'img', 'br', 'hr', 'input', 'link', 'meta', 'source', 'wbr', 'area', 'base', 'col', 'param', 'track'}:
                self.blocked.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag == 'style':
            self.in_style = True
        if tag == 'head':
            self.in_head = True
        if tag == 'title':
            self.in_title = True
        if tag == 'body':
            self.in_head = False
        if tag in {'path', 'rect', 'circle', 'ellipse', 'polyline', 'polygon'}:
            self.has_content = True
        values = []
        for key, value in attrs:
            if key in ATTRS and value is not None:
                value = SENSITIVE.sub('[redacted]', value)
                values.append(f' {"viewBox" if key == "viewbox" else key}="{html.escape(value, quote=True)}"')
        self.parts.append('<' + tag + ''.join(values) + '>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag in ALLOWED_TAGS:
            self.parts.append('</' + tag + '>')
        if tag == 'style':
            self.in_style = False
        if tag == 'head':
            self.in_head = False
        if tag == 'title':
            self.in_title = False

    def handle_data(self, value):
        if not self.blocked:
            if value.strip() and not (self.in_style or self.in_head or self.in_title):
                self.has_content = True
            clean = SENSITIVE.sub('[redacted]', value)
            self.parts.append(clean if self.in_style else html.escape(clean))


def draft_html(source):
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_HTML:
        return None
    parser = DraftHTML()
    try:
        parser.feed(source)
    except Exception:
        return None
    if not parser.has_content:
        return None
    # CSP precedes all parsed content. The UI also uses an empty sandbox.
    result = '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'; script-src \'none\'">' + ''.join(parser.parts)
    return result if len(result) <= MAX_HTML else None


def public_progress(value):
    """Fail closed on malformed packets; never forward arbitrary extra fields."""
    if not isinstance(value, dict) or value.get('phase') not in PHASES:
        return None
    build_id = str(value.get('build_id') or '')
    if not re.fullmatch(r'[\w.-]{1,120}', build_id):
        return None
    result = {'build_id': build_id, 'phase': value['phase']}
    for key in ('filename', 'tool_name'):
        result[key] = SENSITIVE.sub('[redacted]', str(value.get(key) or '')).replace('\x00', '')[:160]
    for key in ('received_bytes', 'revision', 'updated_at'):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0:
            result[key] = min(number, 10**13)
    if value.get('kind') == 'a2ui':
        result['kind'] = 'a2ui'
        source = value.get('render_app')
        if isinstance(source, dict) and source.get('schema') == 'calliope.app.v1':
            try:
                encoded = json.dumps(source, allow_nan=False)
                if len(encoded.encode()) <= 1_500_000:
                    result['render_app'] = json.loads(encoded)
            except (ValueError, TypeError, RecursionError):
                pass
        # Server-only source, like render_html. It is never browser telemetry.
        return result
    if value.get('kind') == 'image':
        # Only the image request's display fields cross this boundary. No
        # reference URLs, provider credentials, result bytes or arbitrary args.
        result['kind'] = 'image'
        for key, limit in [('call_id', 240), ('prompt', 1600), ('title', 160), ('aspect_ratio', 24)]:
            result[key] = SENSITIVE.sub('[redacted]', str(value.get(key) or '')).replace('\x00', '')[:limit]
        return result
    lines = value.get('lines')
    result['lines'] = [min(100, max(1, int(n))) for n in (lines[:72] if isinstance(lines, list) else []) if isinstance(n, (int, float)) and math.isfinite(n)]
    # Complete authored HTML stays on the authenticated server-to-server
    # channel for capture. The browser polling route strips it before returning.
    source = value.get('render_html')
    if isinstance(source, str) and len(source) <= 180_000:
        result['render_html'] = source
    if value.get('preview_kind') in {'incoming', 'saved'}:
        result['preview_kind'] = value['preview_kind']
    if isinstance(value.get('preview_html'), str):
        preview = draft_html(value['preview_html'])
        if preview:
            result['preview_html'] = preview
    return result
