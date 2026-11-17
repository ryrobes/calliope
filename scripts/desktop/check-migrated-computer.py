#!/usr/bin/env python3
"""Acceptance check inside the appliance after moving its existing Computer.

Uses normal Computer tools and durable receipts in the supplied session. Tests
the hosted invoice acceptance fixture in the migrated visible browser, writes
one marker, records without a viewer, and cancels a second long-running job.
Requires an idle desktop and the existing signed-in acceptance browser profile.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import psycopg
from psycopg.rows import dict_row

from calliope import CalliopeConfig
import desktop_evidence
import desktop_tools
from desktop_runtime import DesktopRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--check-choosers', action='store_true',
                        help='Also check both optional chooser callbacks from a registered remote job')
    args = parser.parse_args()
    config = CalliopeConfig.from_env()
    runtime = DesktopRuntime(config)
    state = runtime.request('GET', 'status')
    assert not state['active_run'] and not state['queue'], 'Desktop must be idle'

    def conn():
        return psycopg.connect(os.environ['WAREHOUSE_DSN'], row_factory=dict_row, autocommit=True)

    def execute(code, intent, timeout=120):
        job = desktop_tools.desktop_exec(conn, config, args.session, args.actor,
                                        code=code, intent=intent, timeout=timeout)
        deadline = time.monotonic() + timeout + 20
        while job['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(1)
            job = desktop_tools.desktop_exec(conn, config, args.session, args.actor, job_id=job['id'])
        assert job['status'] == 'completed', {k: job.get(k) for k in ('id', 'status', 'stderr')}
        return job

    def archive(rid):
        path = desktop_evidence.directory(config, rid)
        for attempt in range(12):
            desktop_evidence.sync(conn, config)
            with conn() as c:
                done = c.execute('SELECT 1 FROM rvbbit.calliope_workbench_events '
                                 "WHERE desktop_run_id=%s::uuid AND idempotency_key='evidence.archived'",
                                 (rid,)).fetchone()
            if done:
                break
            time.sleep(1)
        assert done, 'Archive receipt was not finalized'
        manifest = json.loads((path / 'manifest.json').read_text())
        assert manifest['segments'], 'Recording missing'
        for segment in manifest['segments']:
            p = path / 'video' / segment['file']
            assert p.stat().st_size == segment['bytes']
            with p.open('rb') as source:
                assert hashlib.file_digest(source, 'sha256').hexdigest() == segment['sha256']
        return {'run_id': rid, 'archive': str(path), 'manifest': manifest}

    opened = desktop_tools.open_desktop(conn, config, args.session, args.actor,
                                       'Verify migrated Computer profile, private app, files and headless recording')
    passed = False
    try:
        code = '''
import json,time
from pathlib import Path
from calliope_computer import browser,cua
prior=Path.home()/'computer-integration-changed.txt'
assert prior.read_text() == 'zo\\nhuman takeover verified\\n'
assert cua.call('list_windows')['windows']
results=[]
with browser() as b:
    p=b.contexts[0].pages[0]
    p.goto(URL,wait_until='domcontentloaded')
    p.get_by_role('heading',name='Invoice review',exact=True).wait_for(timeout=30000)
    customer=p.get_by_role('combobox',name='Customer',exact=True)
    status=p.get_by_role('combobox',name='Status',exact=True)
    cases=[('All customers','All statuses',5,'$1,000.00'),('All customers','Paid',3,'$550.00'),
           ('North','Paid',2,'$400.00'),('South','Pending',1,'$250.00')]
    for c,s,n,total in cases:
        customer.select_option(label=c); status.select_option(label=s)
        p.get_by_text(str(n)+' invoices shown',exact=True).wait_for()
        actual=p.get_by_role('status').inner_text()
        assert actual==total,(c,s,actual)
        assert p.locator('tbody tr').count()==n
        results.append({'customer':c,'status':s,'rows':n,'total':actual})
    p.get_by_role('button',name='Reset filters',exact=True).click()
    assert p.get_by_role('status').inner_text()=='$1,000.00'
    assert p.locator('tbody tr').count()==5
    results.append({'reset':True,'rows':5,'total':'$1,000.00'})
    print(json.dumps({'url':p.url,'title':p.title(),'checks':results,'prior_file_preserved':True}),flush=True)
marker=Path.home()/'computer-vm-migration-verified.txt'
marker.write_text('Dedicated VM: existing profile, files, app and recording verified.\\n')
assert marker.read_text().startswith('Dedicated VM:')
if CHECK_CHOOSERS:
    from calliope_computer import choose
    for provider in ('cua-s1','jev'):
        result=choose(goal='Save the edited document',observation='Toolbar offers Save and Discard',
                      options=[{'id':'Save','label':'Save'},{'id':'Discard','label':'Discard'}],provider=provider)
        assert result['selected_id']=='Save',result
        print(json.dumps({'chooser':provider,'result':result}),flush=True)
time.sleep(32)
'''.replace('URL', repr(args.url)).replace('CHECK_CHOOSERS', repr(args.check_choosers))
        job = execute(code, 'Verify real app login and five filters using the migrated visible browser; preserve a marker')
        shot = desktop_tools.look_at_desktop(conn, config, args.session, args.actor)
        with Path(shot['path']).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == shot['screenshot']['sha256']
        passed = True
    finally:
        desktop_tools.finish_desktop(conn, config, args.session, args.actor,
                                     'Migration acceptance passed' if passed else 'Migration acceptance failed; inspect job receipt')
    proof = {'desktop_url': config.desktop_url, 'image_revision': config.desktop_image_revision,
             'job': job, 'screenshot': shot['screenshot'], 'main': archive(opened['run_id'])}

    cancelled = desktop_tools.open_desktop(conn, config, args.session, args.actor,
                                          'Verify cancellation across the private VM connection')
    long_job = desktop_tools.desktop_exec(conn, config, args.session, args.actor,
        code="import time; print('waiting for cancellation',flush=True); time.sleep(180); print('UNEXPECTED_COMPLETION',flush=True)",
        intent='Start a long wait and verify finish cancels its process', timeout=240)
    time.sleep(3)
    desktop_tools.finish_desktop(conn, config, args.session, args.actor, 'Operator migration check: cancel the waiting process')
    stopped = desktop_tools.desktop_exec(conn, config, args.session, args.actor, job_id=long_job['id'])
    assert stopped['status'] == 'cancelled', stopped['status']
    assert 'UNEXPECTED_COMPLETION' not in stopped.get('stdout', '')
    proof['cancellation'] = {**archive(cancelled['run_id']), 'job': stopped}
    state = runtime.request('GET', 'status')
    assert state['mode'] == 'idle' and not state['active_run']
    args.output.write_text(json.dumps(proof, indent=2, default=str) + '\n')
    print(json.dumps({'passed': True, 'run_id': opened['run_id'], 'cancellation_run': cancelled['run_id'],
                      'evidence': str(args.output), 'job_stdout': job['stdout']}, indent=2))


if __name__ == '__main__':
    main()
