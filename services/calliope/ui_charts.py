"""Vega-Lite rendering policy: composition and expressions, with host-owned I/O."""
import copy
import json


def validate(spec):
    if not isinstance(spec, dict) or len(json.dumps(spec, allow_nan=False).encode()) > 1_000_000:
        raise ValueError('Expected a Vega-Lite object of at most 1 MB')
    rows = 0
    def dataset(value):
        nonlocal rows
        if isinstance(value, list):
            rows += len(value)
        elif isinstance(value, dict):
            rows += len(value.get('features', [])) or 1
        else:
            raise ValueError('Chart datasets must be inline arrays or objects')
        if rows > 5000:
            raise ValueError('Chart datasets exceed 5000 rows')
    def visit(value, depth=0):
        if depth > 32:
            raise ValueError('Chart nesting exceeds 32 levels')
        if isinstance(value, list):
            for child in value: visit(child, depth+1)
        elif isinstance(value, dict):
            for key, child in value.items():
                # SQL columns named url/signal/etc are ordinary data, not code.
                if key == 'datasets':
                    if not isinstance(child, dict): raise ValueError('Invalid named datasets')
                    for data in child.values(): dataset(data)
                elif key == 'values' and depth and isinstance(child, (list, dict)):
                    dataset(child)
                else:
                    if key in {'url', 'href', 'signals'} or (key == 'mark' and
                            (child == 'image' or isinstance(child, dict) and child.get('type') == 'image')):
                        raise ValueError('External chart data, links and image marks are disabled')
                    visit(child, depth+1)
    visit(spec)
    return spec


def wrap_title(title, width):
    """Break long titles and subtitles into lines, as the web Stage does."""
    limit = max(16, int((width - 24) / 7.2))
    def lines(text):
        if not isinstance(text, str) or len(text) <= limit:
            return text
        out, line = [], ''
        for word in text.split():
            if line and len(line) + len(word) + 1 > limit:
                out.append(line)
                line = word
            else:
                line = f'{line} {word}' if line else word
        return out + [line] if line else out
    if isinstance(title, dict):
        return {**title, **{key: lines(title[key]) for key in ('text', 'subtitle') if key in title}}
    return lines(title)


def fit(spec, width, height):
    """Unit/layer views fit exactly; compositions use bounded child view sizes.

    Vega-Lite ignores top-level fit for facets/repeats/concat. Give each child
    a useful viewport, then hosts contain the resulting whole chart uniformly.
    """
    spec = copy.deepcopy(spec)
    width, height = max(180, min(2048, width)), max(180, min(960, height))
    def facet_count(node, channel):
        field = node.get('facet', node.get('encoding', {})).get(channel, {}).get('field')
        data = node.get('data', spec.get('data', {}))
        values = data.get('values', spec.get('datasets', {}).get(data.get('name'), []))
        if field and isinstance(values, list):
            return max(1, len({json.dumps(row.get(field)) for row in values}))
        return 1
    def size(node, w, h):
        node.pop('autosize', None)
        if 'title' in node:
            node['title'] = wrap_title(node['title'], w)
        if any(k in node for k in ('hconcat', 'vconcat', 'concat', 'facet', 'repeat')) or any(k in node.get('encoding', {}) for k in ('row', 'column', 'facet')):
            node.pop('width', None); node.pop('height', None)
            for key in ('hconcat', 'vconcat', 'concat'):
                if key in node:
                    children = node[key]
                    cols = len(children) if key == 'hconcat' else 1 if key == 'vconcat' else min(node.get('columns', 2), len(children))
                    rows = (len(children)+max(1, cols)-1)//max(1, cols)
                    for child in children: size(child, max(100, w//max(1, cols)-24), max(100, h//rows-24))
            if 'spec' in node:
                repeat = node.get('repeat')
                if isinstance(repeat, list): cols, rows = min(node.get('columns', 2), max(1,len(repeat))), 1
                elif isinstance(repeat, dict): cols, rows = max(1,len(repeat.get('column',[]))), max(1,len(repeat.get('row',[])))
                else: cols, rows = facet_count(node, 'column'), facet_count(node, 'row')
                size(node['spec'], max(100, w//max(1,cols)-40), max(120, h//rows-70))
            if 'encoding' in node:
                node['width'], node['height'] = max(100, w//2-32), max(120, h-70)
        else:
            node.update(width=w, height=h, autosize={'type':'fit', 'contains':'padding'})
    size(spec, width, height)
    return spec
