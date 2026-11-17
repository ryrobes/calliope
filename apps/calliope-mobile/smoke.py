#!/usr/bin/env python3
"""Device smoke test after opening any published Demo app. No business writes.

Exercises the real native renderer + server snapshots. Semantic fills complement
touch/IME checks; they do not prove keyboard, gestures or accessibility work.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
from probe import Device

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'dist/evidence')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phone = Device(args.serial)
    report = {'pages': {}}

    def settle(predicate=lambda s: True):
        end = time.monotonic()+25
        while time.monotonic() < end:
            s = phone.probe({'op': 'inspect'})
            if s.get('failed'):
                raise RuntimeError(s['notice'])
            if not s.get('busy') and not s.get('render', {}).get('refresh_pending') and predicate(s):
                return s
            time.sleep(.25)
        raise RuntimeError('Native app did not settle')

    def press(ident):
        result = phone.probe({'op': 'press', 'id': ident})
        assert result.get('queued'), result

    def fill(ident, value):
        result = phone.probe({'op': 'fill', 'id': ident, 'value': value})
        assert not result.get('error'), result

    s = settle()
    assert s['slug'].startswith('demo-'), 'Open a Demo app first'
    ime = subprocess.check_output(phone.adb+['shell', 'dumpsys', 'input_method'], text=True)
    if 'mInputShown=true' in ime:
        subprocess.run(phone.adb+['shell', 'input', 'keyevent', 'KEYCODE_BACK'], check=True)
    # Navigation events and bound draft fields only; never press a Save/Submit button.
    press('navigation.page-adaptive')
    settle(lambda s: s['surface']['data']['state']['page']=='adaptive')
    fill('page.search-filter', '')
    s = settle(lambda s: s['surface']['data']['state']['filter']['search']=='')
    time.sleep(.6)
    s = settle()
    total = len(s['surface']['data']['jobs'])
    fill('page.search-filter', 'Cedar')
    s = settle(lambda s: 0 < len(s['surface']['data']['jobs']) < total)
    report['sql_filter'] = {'before': total, 'after': len(s['surface']['data']['jobs']), 'on_change': True}
    fill('page.search-filter', '')
    s = settle(lambda s: len(s['surface']['data']['jobs'])==total)
    other = next(n for n in s['surface']['nodes'] if n['component']=='CRecordItem' and n['props']['recordId'] != s['surface']['data']['job']['id'])
    press(other['id'])
    s = settle(lambda s: s['surface']['data']['job']['id']==other['props']['recordId'])
    selected = [n for n in s['surface']['nodes'] if n['component']=='CRecordItem' and n['props']['recordId']==n['props']['selectedId']]
    assert len(selected)==1
    report['selection'] = {'one_selected': True}
    for label, text in (
        ('parts', 'The ECM blower motor has failed. The exact model and rotation are verified. Arrange a replacement motor.'),
        ('comeback', 'The same cooling problem returned one day after the previous repair. This is a repeat visit and failed repair.'),
    ):
        fill('page.notes', text)
        s = settle(lambda s: s['surface']['data']['decision'].get('label')==label)
        assert s['surface']['data']['state']['jobForm']['notes']==text
        report['decision_'+label] = {'status': s['surface']['data']['decision']['status'], 'draft_retained': True}
    pid = subprocess.check_output(phone.adb+['shell', 'pidof', 'ai.rvbbit.calliope.mobile']).strip()
    subprocess.run(phone.adb+['shell', 'input', 'keyevent', 'KEYCODE_HOME'], check=True)
    time.sleep(1)
    subprocess.run(phone.adb+['shell', 'am', 'start', '-n', 'ai.rvbbit.calliope.mobile/.CalliopeActivity'], check=True, capture_output=True)
    s = settle()
    assert s['surface']['data']['state']['jobForm']['notes']==text
    assert subprocess.check_output(phone.adb+['shell', 'pidof', 'ai.rvbbit.calliope.mobile']).strip()==pid
    report['home_resume'] = {'same_process': True, 'draft_retained': True}
    for page in ('dossier', 'pipeline', 'briefing', 'hvac', 'counter', 'receiving', 'dispatch', 'field', 'adaptive', 'case', 'charts'):
        press('navigation.page-'+page)
        s = settle(lambda s: s['surface']['data']['state']['page']==page)
        report['pages'][page] = {'nodes': len(s['surface']['nodes']), 'failed': s['failed'], 'charts': s['render']['charts']}
        phone.capture(args.output/f'page-{page}.png')
    press('navigation.page-field')
    settle(lambda s: s['surface']['data']['state']['page']=='field')
    (args.output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
