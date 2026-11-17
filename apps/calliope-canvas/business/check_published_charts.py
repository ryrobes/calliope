#!/usr/bin/env python3
"""Render every chart in a published SQL snapshot with the native export engine.

Usage: python check_published_charts.py charts-snapshot.json output-directory
The snapshot comes from /api/apps/<slug>/ui or demos/check_postgres.py.
"""
import json
from pathlib import Path
import sys
from render_chart import render


def check(snapshot, output):
    output.mkdir(parents=True, exist_ok=True)
    charts = snapshot['messages'][2]['updateDataModel']['value']['charts']
    results = []
    for name, spec in charts.items():
        for width in (360, 1000):
            file = output / f'{name}-{width}.png'
            render(spec, file, fonts=str(Path.home()/'.local/share/calliope-canvas/fonts'), width=width, height=440)
            scene = json.loads(Path(str(file)+'.json').read_text())
            assert file.read_bytes().startswith(b'\x89PNG'), name
            assert scene['width'] > 0 and scene['height'] > 0, name
            assert len(scene['hits']) <= 4096, name
            for hit in scene['hits']:
                x1, y1, x2, y2 = hit['bounds']
                assert x1 < x2 and y1 < y2, (name, hit)
                assert x1 >= 0 and y1 >= 0 and x2 <= scene['width'] and y2 <= scene['height'], (name, hit)
            results.append({'name': name, 'viewport': width, 'width': scene['width'],
                            'height': scene['height'], 'hits': len(scene['hits'])})
        print(name, 'OK', flush=True)
    (output/'native-charts.json').write_text(json.dumps(results, indent=2))
    return results


if __name__ == '__main__':
    check(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
