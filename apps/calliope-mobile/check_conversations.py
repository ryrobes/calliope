#!/usr/bin/env python3
"""Inspect owned mobile threads, history, Stage and shell navigation.

Uses the app's real session APIs. Default is read-only; --send explicitly adds
one harmless test question to a new server conversation. Keyboard/gesture tests
remain separate XCTest/ADB checks. No business action is submitted.
"""
import argparse
import json
from pathlib import Path
import time
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    device = parser.add_mutually_exclusive_group(required=True)
    device.add_argument('--device', help='iOS Simulator UUID; run on the Mac')
    device.add_argument('--serial', help='Android serial')
    parser.add_argument('--adb')
    parser.add_argument('--session', help='Owned thread with expected ordinary chat history')
    parser.add_argument('--send', action='store_true', help='Create a real conversation and send one harmless message')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.device:
        from probe_ios import Simulator
        phone = Simulator(args.device)
    else:
        from probe import Device
        phone = Device(args.serial, args.adb)

    def command(op, **body):
        result = phone.probe({'op': op, **body})
        assert not result.get('error'), result
        return result

    def press(ident, **body):
        return command('press', id=ident, **body)

    def settle(predicate=lambda p: True, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            p = command('inspect')
            c = p.get('conversation') or {}
            assert not c.get('error'), c.get('error')
            if not p.get('loading') and not c.get('loading') and not c.get('running') and predicate(p):
                return p
            time.sleep(.25)
        raise TimeoutError('Conversation did not settle')

    # An install/relaunch returns before the saved account has reconnected.
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            state = phone.probe({'op': 'inspect'})
            if not state.get('account'):
                break
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        time.sleep(.5)
    command('navigate', screen='conversations')
    press('chat-back')
    p = settle()
    assert p['sessions'] and all(s['access_role'] == 'owner' for s in p['sessions'])
    chosen = next(s for s in p['sessions'] if s['id'] == args.session) if args.session else next(s for s in p['sessions'] if s.get('turn_count', 0))
    report = {'owned_threads': len(p['sessions']), 'session': chosen['id']}
    press('chat-open', session=chosen['id'])
    p = settle(lambda p: bool(p.get('conversation', {}).get('turns')))
    original = p['conversation']['turns']
    assert p['conversation']['session'] == chosen['id']
    report['history_turns'] = len(original)
    press('chat-stage')
    p = settle()
    assert p['stage']
    report['stage_items'] = len(p['conversation']['surfaces'])
    press('chat-messages')
    command('navigate', screen='apps')
    p = command('inspect')
    assert 'slug' in p and not p.get('account'), p
    command('navigate', screen='conversations')
    p = settle()
    assert p['conversation']['session'] == chosen['id']
    assert p['conversation']['turns'] == original
    report['apps_round_trip'] = True
    press('chat-back')
    rejected = phone.probe({'op': 'press', 'id': 'chat-open', 'session': '00000000-0000-0000-0000-000000000000'})
    assert rejected.get('error'), rejected
    press('chat-new')
    p = settle()
    assert p['conversation']['session'] == '' and p['conversation']['turns'] == []
    command('fill', id='chat-message', value='Unsent mobile smoke draft')
    time.sleep(.3)
    press('chat-back')
    press('chat-new')
    p = settle()
    assert p['conversation']['draft'] == 'Unsent mobile smoke draft', p['conversation']
    command('fill', id='chat-message', value='')
    report['new_chat_is_lazy_and_draft_survives_back'] = True
    if args.send:
        command('fill', id='chat-message', value='Reply with exactly MOBILE-CHAT-OK. Do not use tools or change any data.')
        press('chat-send')
        p = settle(lambda p: bool(p['conversation']['turns']) and bool(p['conversation']['turns'][-1]['answer']), timeout=180)
        assert 'MOBILE-CHAT-OK' in p['conversation']['turns'][-1]['answer']
        report['created_session'] = p['conversation']['session']
    press('chat-back')
    press('chat-open', session=chosen['id'])
    settle()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
