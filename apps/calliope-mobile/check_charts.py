#!/usr/bin/env python3
"""Exercise every SQL-backed chart family on the phone; no business writes.

Starts at the app tiles. Uses the renderer's structural driver for selection,
then an actual Android touch event to verify picking and a tooltip. Screenshots
and a small result file go into the ignored evidence directory.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
from probe import Device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'dist/evidence/charts')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phone = Device(args.serial)

    def settle(predicate=lambda s: True):
        end = time.monotonic()+35
        state = {}
        while time.monotonic() < end:
            try:
                state = phone.probe({'op': 'inspect'})
            except subprocess.CalledProcessError:
                time.sleep(.3)  # The private socket is created after connection.
                continue
            if state.get('failed'): raise RuntimeError(state['notice'])
            if not state['busy'] and not state['render']['refresh_pending'] and predicate(state):
                return state
            time.sleep(.2)
        raise RuntimeError('Native chart did not settle: '+json.dumps(state.get('render', {}).get('charts', {}))[:600])

    settle()
    assert phone.probe({'op': 'home'})['ok']
    settle(lambda s: not s['slug'])
    phone.capture(args.output/'tiles.png')
    assert phone.probe({'op': 'open_app', 'slug': 'demo-chart-gallery'})['ok']
    state = settle(lambda s: s['surface']['data'].get('state', {}).get('page') == 'charts')
    control = next(n for n in state['surface']['nodes'] if n['bindings'].get('value') == {'path': '/state/controls/chart'})
    families = [o['value'] for o in control['props']['options']]
    report = {'families': {}}
    for family in families:
        assert phone.probe({'op': 'fill', 'id': control['id'], 'value': [family]}).get('ok')
        state = settle(lambda s: s['surface']['data']['state']['controls']['chart'] == [family]
            and any(n['component']=='CVegaChart' and n['bindings'].get('spec') == {'path': '/charts/'+family} for n in s['surface']['nodes'])
            and s['render']['charts'] and all(c['ready'] for c in s['render']['charts'].values()))
        chart = next(iter(state['render']['charts'].values()))
        report['families'][family] = {'ready': True, 'width': chart['width'], 'height': chart['height'], 'hits': len(chart['scene']['hits'])}
        print(family, 'ready', flush=True)

    assert phone.probe({'op': 'fill', 'id': 'page.tech', 'value': ['Sam Rivera']}).get('ok')
    state = settle(lambda s: bool(s['surface']['data']['charts']['facet']['datasets']['visits'])
        and all(r['technician']=='Sam Rivera' for r in s['surface']['data']['charts']['facet']['datasets']['visits']))
    report['sql_filter'] = {'on_change': True, 'rows': len(state['surface']['data']['charts']['facet']['datasets']['visits'])}
    phone.probe({'op': 'fill', 'id': 'page.tech', 'value': ['Everyone']})
    phone.probe({'op': 'fill', 'id': control['id'], 'value': ['heatmap']})
    settle(lambda s: s['surface']['data']['state']['controls']['chart']==['heatmap'] and s['render']['charts'] and all(c['ready'] for c in s['render']['charts'].values()))

    # Read this app's Android viewport, including the status-bar inset. Never
    # alter the device's density, orientation or global animation settings.
    density = subprocess.check_output(phone.adb+['shell', 'wm', 'density'], text=True)
    scale = int(re.findall(r'density: (\d+)', density)[-1])/160
    windows = subprocess.check_output(phone.adb+['shell', 'dumpsys', 'window', 'windows'], text=True)
    own = next(w for w in windows.split('Window #') if 'mOwnerUid=' in w and 'package=ai.rvbbit.calliope.mobile' in w)
    left, top, right, bottom = map(int, re.search(r'mAppBounds=Rect\((\d+), (\d+) - (\d+), (\d+)\)', own).groups())
    for _ in range(4):
        state = settle()
        chart = next(iter(state['render']['charts'].values()))
        if chart['bounds']['y']+chart['bounds']['height'] < (bottom-top)/scale: break
        subprocess.run(phone.adb+['shell', 'input', 'swipe', str(right//2), str(int(bottom*.82)), str(right//2), str(int(bottom*.36)), '450'], check=True)
        time.sleep(.4)
    state = settle()
    chart = next(iter(state['render']['charts'].values()))
    bounds, scene = chart['bounds'], chart['scene']
    hit = scene['hits'][0]
    contain = min(bounds['width']/scene['width'], bounds['height']/scene['height'])
    x = bounds['x']+(bounds['width']-scene['width']*contain)/2+(hit['bounds'][0]+hit['bounds'][2])/2*contain
    y = bounds['y']+(bounds['height']-scene['height']*contain)/2+(hit['bounds'][1]+hit['bounds'][3])/2*contain
    subprocess.run(phone.adb+['shell', 'input', 'tap', str(round(left+x*scale)), str(round(top+y*scale))], check=True)
    state = settle(lambda s: next(iter(s['render']['charts'].values()))['hovered'] is not None)
    report['physical_tap'] = {'tooltip': hit['tooltip'], 'picked': True}
    phone.capture(args.output/'heatmap-tooltip.png')
    (args.output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print('SQL filter and physical tap passed', flush=True)


if __name__ == '__main__':
    main()
