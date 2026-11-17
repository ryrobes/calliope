#!/usr/bin/env python3
"""Exercise record drafts, Back, restart, reconnect and background polling on a phone.

Requires the standard USB test tunnel and an unlocked device. Only labeled local
test drafts are edited by default. --submit-demo-report explicitly permits one
fictional field report; an optional confirmed test photo may be attached before
running. ADB reverse is restored even if the test fails. Wi-Fi is untouched.
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
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--submit-demo-report', action='store_true')
    parser.add_argument('--require-evidence', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phone = Device(args.serial)
    package = 'ai.rvbbit.calliope.mobile'
    slug = 'demo-field-notebook'

    def adb(*words):
        return subprocess.check_output(phone.adb+list(words), text=True)

    def settle(predicate=lambda s: True, draft=True):
        deadline = time.monotonic()+70
        state = {}
        while time.monotonic() < deadline:
            try:
                state = phone.probe({'op':'inspect'})
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Android can start the activity before its private probe is ready.
                time.sleep(.25)
                continue
            if not state.get('busy') and (not draft or state.get('draft_saved')) and predicate(state):
                return state
            time.sleep(.25)
        raise RuntimeError({k:state.get(k) for k in ('notice','draft_error','offline','slug')})

    def form(state):
        return state['surface']['data']['state']['fieldForm']

    def fill(notes):
        result = phone.probe({'op':'fill','id':'page.notes','value':notes})
        assert not result.get('error'), result
        return settle(lambda s:form(s)['notes']==notes)

    def select(record):
        state = phone.probe({'op':'inspect'})
        node = next(n for n in state['surface']['nodes'] if n['component']=='CRecordItem' and n['props'].get('recordId')==record)
        assert phone.probe({'op':'press','id':node['id']}).get('queued')
        return settle(lambda s:form(s)['id']==record)

    def restart():
        adb('shell','am','force-stop',package)
        adb('shell','am','start','-n',package+'/.CalliopeActivity')
        settle(lambda s:bool(s.get('apps')),draft=False)
        assert phone.probe({'op':'open_app','slug':slug}).get('ok')
        return settle()

    state = settle()
    assert state.get('slug')==slug and not state.get('pending'), 'Open Field notebook with no pending submission'
    assert not any(form(state).get(k) for k in ('notes','temperature','pressure','correction_of')), 'Keep existing work: begin with a blank report'
    files = form(state)['attachments']
    assert not files or args.submit_demo_report, 'Existing test evidence requires explicit --submit-demo-report'
    assert not args.require_evidence or files, 'Attach a confirmed test photo first'
    assert 'tcp:18080 tcp:18080' in adb('reverse','--list'), 'Standard USB tunnel required'
    a = form(state)['id']
    b = next(n['props']['recordId'] for n in state['surface']['nodes'] if n['component']=='CRecordItem' and n['props'].get('recordId')!=a and n['props'].get('onSelect')=='select_job')
    marker = 'Record recovery '+str(uuid.uuid4())
    fill(marker+' A')
    state = select(b)
    assert not any(form(state).get(k) for k in ('notes','temperature','pressure','correction_of','attachments')), 'Keep the other record\'s existing draft'
    fill(marker+' B')
    adb('shell','input','keyevent','KEYCODE_BACK')
    state = settle(lambda s:form(s)['id']==a)
    assert form(state)['notes']==marker+' A'
    report = {'a':a,'b':b,'marker':marker,'hardware_back':True,'online_a_b_a':True}
    try:
        adb('reverse','--remove','tcp:18080')
        state = restart()
        assert state['offline'] and form(state)['notes']==marker+' A'
        state = select(b)
        assert state['offline'] and form(state)['notes']==marker+' B'
        adb('shell','input','keyevent','KEYCODE_BACK')
        settle(lambda s:form(s)['id']==a)
        fill(marker+' A offline')
        state = restart()
        assert form(state)['notes']==marker+' A offline' and form(state)['attachments']==files
        report.update(offline_cached_selection=True,offline_back=True,draft_restart=True)
        phone.capture(args.output/'offline.png')
        if args.submit_demo_report:
            phone.probe({'op':'press','id':'page.submit'})
            state = settle(lambda s:bool(s.get('pending')))
            key = state['pending']['request_key']
            state = restart()
            assert state['pending']['request_key']==key
            report.update(pending_restart_same_key=True,request_key=key)
    finally:
        adb('reverse','tcp:18080','tcp:18080')
    state = settle(lambda s:not s['offline'])
    report['automatic_reconnect'] = True
    if args.submit_demo_report:
        assert state['pending']['request_key']==key
        report['reconnect_did_not_submit'] = True
        phone.probe({'op':'send_pending'})
        state = settle(lambda s:not s.get('pending'))
        assert not state.get('failed'), state.get('notice')
        rows = [r for r in state['surface']['data']['reports'] if r.get('notes')==marker+' A offline']
        assert len(rows)==1 and len(rows[0]['attachments'])==len(files)
        report['submitted_once'] = True
    key_b = next(d['key'] for d in state['drafts'] if d['record']==b and d['form']=='/state/fieldForm')
    phone.probe({'op':'resume_draft','key':key_b})
    state = settle(lambda s:form(s)['id']==b)
    assert form(state)['notes']==marker+' B'
    report['other_record_preserved'] = True
    phone.probe({'op':'refresh'})
    before = settle()['read_requests']
    adb('shell','input','keyevent','KEYCODE_HOME')
    time.sleep(32)
    adb('shell','am','start','-n',package+'/.CalliopeActivity')
    state = settle(lambda s:s['read_requests']>before)
    assert state['read_requests']==before+1
    report['background_polling_paused'] = True
    # Clean only this run's local markers; a failed test intentionally keeps recovery data.
    for entry in list(state['drafts']):
        if entry['form']=='/state/fieldForm' and entry['record'] in (a,b):
            phone.probe({'op':'resume_draft','key':entry['key']})
            state = settle(lambda s:form(s)['id']==entry['record'])
            assert form(state)['notes'].startswith(marker)
            phone.probe({'op':'discard_draft','key':entry['key']})
            assert phone.probe({'op':'inspect'})['discard_confirmation']==entry['key']
            phone.probe({'op':'confirm_discard'})
            settle(lambda s:all(d['key']!=entry['key'] for d in s['drafts']))
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
