"""Offline chart image + picking geometry, using the pinned vl-convert engine.

Only host-validated Vega-Lite enters this helper. Metadata is attached to a COPY
of the compiled scene: vl-convert's scene serializer preserves arrays/scalars,
but deliberately strips arbitrary object keys. Never evaluate authored JS here.
"""
import copy
import json
import math
from pathlib import Path
import re
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import vl_convert as vega
from ui_charts import validate, fit


def expression(value):
    return value.get('signal', json.dumps(value.get('value'))) if isinstance(value, dict) else 'null'


def instrument(compiled, spec):
    fields = set()
    def collect(value):
        if isinstance(value, list):
            for item in value: collect(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key == 'values' and isinstance(item, list):
                    fields.update(k for row in item if isinstance(row, dict) for k in row)
                elif key == 'datasets' and isinstance(item, dict):
                    for rows in item.values():
                        if isinstance(rows, list): fields.update(k for row in rows if isinstance(row, dict) for k in row)
                else: collect(item)
    collect(spec)
    def visit(marks):
        for mark in marks:
            visit(mark.get('marks', []))
            if mark.get('type') not in ('rect', 'symbol'):
                continue
            update = mark.setdefault('encode', {}).setdefault('update', {})
            # Include compiler-generated aggregate/bin fields. JSON quoting
            # handles unusual SQL aliases without accepting authored expressions.
            for value in update.values():
                for match in re.finditer(r'datum\[("(?:[^"\\]|\\.)*")\]', expression(value)):
                    fields.add(json.loads(match[1]))
            tooltip = expression(update.get('tooltip'))
            # Compiler-produced tooltip object literals have quoted property
            # names; values are evaluated by Vega, including currency/date formats.
            keys = list(dict.fromkeys(json.loads(m[1]) for m in re.finditer(r'("(?:[^"\\]|\\.)*")\s*:', tooltip))) if tooltip.startswith('{') else []
            if tooltip == 'datum': keys = sorted(fields)[:64]
            tips = '[' + ','.join('[' + json.dumps(k) + ',(' + tooltip + ')[' + json.dumps(k) + ']]' for k in keys[:64]) + ']' if keys else tooltip
            pairs = '[' + ','.join('[' + json.dumps(k) + ',datum[' + json.dumps(k) + ']]' for k in sorted(fields)[:128]) + ']'
            update['description'] = {'signal': '[' + pairs + ',' + tips + ']'}
    visit(compiled.get('marks', []))


def interactions(scene):
    hits = []
    def walk(mark, ox, oy, clip, viewport):
        kind = mark.get('marktype')
        if mark.get('clip') is True:
            clip = [max(clip[0], viewport[0]), max(clip[1], viewport[1]), min(clip[2], viewport[2]), min(clip[3], viewport[3])]
        for item in sorted(mark.get('items', []), key=lambda i: i.get('zindex', 0)):
            x, y = ox + item.get('x', 0), oy + item.get('y', 0)
            if kind == 'group':
                child_viewport = [x, y, x + item.get('width', 0), y + item.get('height', 0)]
                for child in sorted(item.get('items', []), key=lambda i: i.get('zindex', 0)):
                    walk(child, x, y, clip, child_viewport)
                continue
            metadata = item.get('description')
            if not mark.get('interactive', True) or item.get('opacity', 1) == 0 or not isinstance(metadata, list) or len(metadata) != 2:
                continue
            datum = {k: val for k, val in metadata[0] if val is None or isinstance(val, (str, int, float, bool))}
            tip = metadata[1]
            def display(val):
                return json.dumps(val, ensure_ascii=False) if val is None or isinstance(val, bool) else str(val)
            tooltip = '\n'.join(f'{k}: {display(val)}' for k, val in tip) if isinstance(tip, list) else (display(tip) if tip is not None else '')
            radius = 0
            if kind == 'rect':
                right, bottom = x + item.get('width', 0), y + item.get('height', 0)
                bounds = [min(x, right), min(y, bottom), max(x, right), max(y, bottom)]
            elif kind == 'symbol' and item.get('shape', 'circle') in ('circle', 'square'):
                if item.get('shape') == 'square' and item.get('angle', 0) % 90: continue
                radius = math.sqrt(max(0, item.get('size', 64))) / 2
                bounds = [x-radius, y-radius, x+radius, y+radius]
                if item.get('shape', 'circle') == 'square': radius = 0
            else:
                continue
            clipped = [max(bounds[0], clip[0]), max(bounds[1], clip[1]), min(bounds[2], clip[2]), min(bounds[3], clip[3])]
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                hits.append({'bounds': clipped, 'center': [x, y], 'radius': radius, 'datum': datum, 'tooltip': tooltip[:4096]})
                if len(hits) > 10000: raise ValueError('Chart interaction budget exceeded')
    viewport = [0, 0, scene['width'], scene['height']]
    walk(scene['scenegraph'], *scene['origin'], viewport, viewport)
    return {'width': scene['width'], 'height': scene['height'], 'hits': hits}


def render(spec, output, fonts=None, width=None, height=None):
    validate(spec)
    if width is not None: spec = fit(spec, width, height)
    if fonts and Path(fonts).is_dir(): vega.register_font_directory(fonts)
    compiled = vega.vegalite_to_vega(spec, vl_version='6.1')
    # PNG keeps the original accessible descriptions. Only the scene export copy
    # carries metadata. Both passes use the same compiled, deterministic dataflow.
    picking = copy.deepcopy(compiled)
    instrument(picking, spec)
    scene = vega.vega_to_scenegraph(picking, allowed_base_urls=[])
    w, h = scene['width'], scene['height']
    if not all(isinstance(n, (int, float)) and math.isfinite(n) and 0 < n <= 4096 for n in (w, h)) or w*h > 8_000_000:
        raise ValueError('Chart dimensions exceed raster budget')
    png = vega.vega_to_png(compiled, scale=2, allowed_base_urls=[])
    Path(output).write_bytes(png)
    Path(str(output)+'.json').write_text(json.dumps(interactions(scene), allow_nan=False))


if __name__ == '__main__':
    # Limits apply only to this disposable converter, never the server process.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8_000_000, 8_000_000))
    render(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2],
           sys.argv[3] or None if len(sys.argv) > 3 else None,
           int(sys.argv[4]) if len(sys.argv)>4 else None,
           int(sys.argv[5]) if len(sys.argv)>5 else None)
