#!/usr/bin/env python3
"""Real-device cancellation, background and microphone-permission recovery.

Requires an unlocked test phone and a completed contextual Callie reply in the
chosen app. Uses its real TTS (may incur usage); recordings are cancelled, never
transcribed or sent. No business writes. Temporarily revokes this app's microphone
permission and restores it in finally. Does not change device networking.
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
    parser.add_argument('--adb')
    parser.add_argument('--slug', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live-voice', action='store_true', required=True)
    args = parser.parse_args()
    phone = Device(args.serial, args.adb)
    args.output.mkdir(parents=True, exist_ok=True)
    package = 'ai.rvbbit.calliope.mobile'
    permission = 'android.permission.RECORD_AUDIO'
    result = {}

    def adb(*words):
        return subprocess.check_output(phone.adb + list(words), text=True, timeout=20)

    def call(op, **kw):
        return phone.probe(dict(op=op, **kw))

    def wait(predicate, op='assistant_inspect', seconds=55):
        deadline = time.monotonic() + seconds
        value = {}
        while time.monotonic() < deadline:
            try:
                value = call(op)
            except (subprocess.SubprocessError, ValueError):
                time.sleep(.3)
                continue
            if predicate(value):
                return value
            time.sleep(.3)
        safe = {k: value.get(k) for k in ('voice', 'audio_host', 'error', 'notice')}
        raise RuntimeError(f'Voice check timed out: {safe}')

    def resume():
        adb('shell', 'am', 'start', '-n', package + '/.CalliopeActivity')

    def open_app():
        wait(lambda s: bool(s.get('apps')), 'inspect')
        call('open_app', slug=args.slug)
        wait(lambda s: not s.get('busy') and s.get('slug') == args.slug, 'inspect')
        call('assistant_open')
        return wait(lambda a: not a.get('loading') and a.get('stt') and a.get('tts'))

    def idle():
        return wait(lambda a: a.get('voice') == 'Idle'
                    and a.get('audio_host', {}).get('session') == -1
                    and not any(a['audio_host'].get(k) for k in ('recording', 'playing', 'focus')))

    def background_audio_stopped():
        # GPUI pauses its probe while backgrounded. Query Android's audio service
        # instead; historical event logs are not current player/focus state.
        dump = adb('shell', 'dumpsys', 'audio')
        focus = dump.split('Audio Focus stack entries', 1)[1].split('MultiFocusStack', 1)[0]
        players = dump.split('  players:', 1)[1].split('  ducked players', 1)[0]
        matches = [line.strip() for line in players.splitlines() if f'u/pid:{uid}/' in line]
        assert package not in focus and not any('state:started' in line for line in matches)
        (args.output / 'background-audio.json').write_text(json.dumps({
            'app_uid': uid, 'app_players': matches, 'app_holds_focus': package in focus,
        }, indent=2))

    permission_state = adb('shell', 'dumpsys', 'package', package)
    assert permission + ': granted=true' in permission_state, 'Begin with microphone access granted'
    uid = re.search(r'uid:(\d+)', adb('shell', 'cmd', 'package', 'list', 'packages', '-U', package)).group(1)
    changed_permission = False
    try:
        resume()
        a = open_app()
        assert a.get('turns') and not a.get('running'), 'A completed Callie reply is required'
        index = len(a['turns']) - 1
        call('assistant_stop_audio')
        draft = idle()['draft']
        for background in (False, True):
            call('assistant_dictate')
            wait(lambda a: a.get('audio_host', {}).get('recording'))
            if background:
                adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
                time.sleep(1)
                resume()
            else:
                call('assistant_stop_audio')
            assert idle()['draft'] == draft
            result['record_background_cancel' if background else 'record_cancel_no_transcript'] = True
        for background in (False, True):
            call('assistant_read', index=index)
            wait(lambda a: a.get('audio_host', {}).get('playing'))
            if background:
                adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
                time.sleep(1)
                background_audio_stopped()
                resume()
            else:
                call('assistant_stop_audio')
            idle()
            result['playback_background_cancel' if background else 'playback_cancel_releases_focus'] = True
        call('assistant_read', index=index)
        adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
        time.sleep(3)
        background_audio_stopped()
        resume()
        idle()
        result['delayed_playback_stays_stopped'] = True
        changed_permission = True
        adb('shell', 'pm', 'revoke', package, permission)
        adb('shell', 'pm', 'set-permission-flags', package, permission, 'user-fixed')
        resume()
        open_app()
        call('assistant_dictate')
        a = wait(lambda a: a.get('voice') == 'Idle' and bool(a.get('error')))
        assert 'microphone' in a['error'].lower() and not a['audio_host']['recording']
        result['denied_microphone_clear_error'] = True
        adb('shell', 'pm', 'clear-permission-flags', package, permission, 'user-fixed')
        adb('shell', 'pm', 'grant', package, permission)
        changed_permission = False
        call('assistant_dictate')
        wait(lambda a: a.get('audio_host', {}).get('recording'))
        call('assistant_stop_audio')
        idle()
        result['reenabled_microphone_recovers'] = True
    finally:
        if changed_permission:
            adb('shell', 'pm', 'clear-permission-flags', package, permission, 'user-fixed')
            adb('shell', 'pm', 'grant', package, permission)
        resume()
        try:
            call('assistant_stop_audio')
        except subprocess.SubprocessError:
            pass
        (args.output / 'voice-results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
