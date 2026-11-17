#!/usr/bin/env python3
"""Explicitly submit one labeled demo report after offline/process-death recovery.

Open the Field notebook and optionally attach a confirmed test photo first.
This interrupts Calliope Mobile and temporarily removes its test USB tunnel.
It never changes Wi-Fi, airplane mode, or the desktop's display/input.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
from probe import Device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--submit-demo-report', action='store_true', required=True)
    parser.add_argument('--require-evidence', action='store_true')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'dist/offline-evidence')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phone = Device(args.serial)
    package = 'ai.rvbbit.calliope.mobile'

    def adb(*words):
        return subprocess.check_output(phone.adb+list(words), text=True)

    def settle(predicate=lambda s: True):
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                state = phone.probe({'op': 'inspect'})
                if not state.get('busy') and not state.get('connecting') and predicate(state):
                    return state
            except (subprocess.SubprocessError, ValueError):
                pass
            time.sleep(.3)
        raise RuntimeError('Device did not settle; tunnel is restored in finally')

    def restart():
        adb('shell', 'am', 'force-stop', package)
        adb('shell', 'am', 'start', '-n', package+'/.CalliopeActivity')
        settle(lambda s: s.get('offline') and s.get('apps'))
        phone.probe({'op': 'open_app', 'slug': slug})
        return settle(lambda s: s.get('slug') == slug and s.get('draft_saved'))

    state = settle(lambda s: s.get('draft_saved'))
    slug = state.get('slug')
    if slug != 'demo-field-notebook' or state.get('pending'):
        raise RuntimeError('Open Demo Field Notebook with no pending submission first')
    form = state['surface']['data']['state']['fieldForm']
    if form.get('notes') or form.get('temperature') or form.get('pressure'):
        raise RuntimeError('Keep existing work: start with a blank demo report')
    files = form.get('attachments', [])
    if args.require_evidence and not files:
        raise RuntimeError('Attach a non-sensitive test photo before running')
    if not any(line.split()[-2:] == ['tcp:18080', 'tcp:18080'] for line in adb('reverse', '--list').splitlines()):
        raise RuntimeError('This test requires the standard tcp:18080 ADB mapping')
    marker = 'Offline recovery smoke test '+str(uuid.uuid4())
    report = {'marker': marker, 'evidence_count': len(files)}
    try:
        adb('reverse', '--remove', 'tcp:18080')
        # Restart also closes HTTP keepalive sockets that survive reverse removal.
        state = restart()
        for ident, value in [('page.notes', marker), ('page.temperature', '72.5'), ('page.pressure', '118')]:
            result = phone.probe({'op': 'fill', 'id': ident, 'value': value})
            assert not result.get('error'), result
        settle(lambda s: s.get('draft_saved') and s['surface']['data']['state']['fieldForm']['notes'] == marker)
        state = restart()
        recovered = state['surface']['data']['state']['fieldForm']
        assert recovered['notes'] == marker and recovered['attachments'] == files
        assert recovered['temperature'] == '72.5' and recovered['pressure'] == '118'
        report['draft_restart'] = True
        phone.capture(args.output/'offline-draft.png')
        phone.probe({'op': 'press', 'id': 'page.submit'})
        state = settle(lambda s: s.get('pending'))
        key = state['pending']['request_key']
        state = restart()
        assert state['pending']['request_key'] == key
        assert phone.probe({'op': 'fill', 'id': 'page.notes', 'value': 'must stay frozen'}).get('error')
        report.update(pending_restart=True, request_key=key, editing_paused=True)
        phone.capture(args.output/'pending.png')
    finally:
        adb('reverse', 'tcp:18080', 'tcp:18080')
    state = settle()
    assert state['pending']['request_key'] == key  # reconnect alone never submits
    phone.probe({'op': 'send_pending'})
    state = settle(lambda s: not s.get('pending') and s.get('draft_saved'))
    assert not state.get('failed'), state.get('notice')
    assert state['surface']['data']['state']['fieldForm']['notes'] == ''
    report.update(submitted=True, form_reset=True)
    phone.capture(args.output/'submitted.png')
    (args.output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
