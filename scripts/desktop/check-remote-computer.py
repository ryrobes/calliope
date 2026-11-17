#!/usr/bin/env python3
"""Run inside the appliance to check a NEW, idle Computer VM over its private API.

Creates one disposable run, a workbook, and local evidence copies. Do not point
this at a Computer with unfinished work. Uses the appliance's existing Computer
key; never prints credentials or changes the appliance's configured endpoint.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
args = parser.parse_args()
base = args.url.rstrip('/')
key = os.environ['WAREHOUSE_CALLIOPE_DESKTOP_CONTROL_KEY']
client = build_opener(ProxyHandler({}))


def request(method, path, body=None, *, binary=False):
    data = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data, {'X-Calliope-Desktop-Key': key,
        'Content-Type': 'application/json'}, method=method)
    with client.open(req, timeout=45) as response:
        result = response.read()
    return result if binary else json.loads(result)


ready_deadline = time.monotonic() + 20
while True:
    health = request('GET', '/health')
    if health['ready'] or time.monotonic() >= ready_deadline:
        break
    time.sleep(.5)
assert health['ready'], {'message': 'Desktop session, browser and Cua must be ready', 'health': health}
state = request('GET', '/computer/status')
assert state['active_run'] is None and not state['queue'], 'Destination is not idle; no changes made'
run, job = str(uuid.uuid4()), str(uuid.uuid4())
output = Path(tempfile.mkdtemp(prefix='computer-vm-preflight-'))
summary = {'url': base, 'run_id': run, 'job_id': job, 'evidence': str(output)}
opened, passed = False, False
try:
    request('POST', '/computer/open', {'run_id': run,
        'context': {'goal': 'Disposable private-VM preflight; no user profile migration'}})
    opened = True
    code = '''
import faulthandler,json,os,sys,time
faulthandler.dump_traceback_later(30, repeat=True, file=sys.stderr)
def checkpoint(label):
    print('preflight: '+label, file=sys.stderr, flush=True)
checkpoint('imports')
from pathlib import Path
from openpyxl import Workbook,load_workbook
from calliope_computer import browser,cua
checkpoint('attach visible browser')
with browser() as visible:
    checkpoint('visible browser attached')
    assert visible.contexts and any(c.pages for c in visible.contexts)
checkpoint('browser detached; list Cua windows')
assert cua.call('list_windows')['windows']
checkpoint('Cua windows ready; create workbook')
path=Path.home()/('vm-preflight-'+os.environ['CALLIOPE_COMPUTER_RUN']+'.xlsx')
book=Workbook(); sheet=book.active
sheet.append(['Item','Amount']); sheet.append(['Alpha',10]); sheet.append(['Beta',15])
book.save(path)
assert sum(r[0] for r in load_workbook(path).active.iter_rows(min_row=2,min_col=2,values_only=True))==25
print(json.dumps({'workbook':str(path),'verified_total':25}),flush=True)
checkpoint('workbook verified; record across segment boundary')
time.sleep(32)
checkpoint('script complete')
faulthandler.cancel_dump_traceback_later()
'''
    request('POST', '/computer/execute', {'run_id': run, 'job_id': job, 'code': code,
        'intent': 'Verify visible browser, Cua, workbook readback and segmented recording on the new VM', 'timeout': 120})
    until = time.monotonic() + 130
    while time.monotonic() < until:
        result = request('GET', '/computer/jobs/' + job)
        if result['status'] != 'running':
            break
        time.sleep(1)
    assert result['status'] == 'completed', {k: result.get(k) for k in ('status', 'stdout', 'stderr')}
    summary['job_stdout'] = result['stdout']
    shot = request('POST', '/computer/observe', {})['screenshot']
    raw = request('GET', '/computer/files/' + quote(shot['object_key'], safe='/'), binary=True)
    assert hashlib.sha256(raw).hexdigest() == shot['sha256']
    (output / 'desktop.png').write_bytes(raw)
    summary['screenshot'] = shot
    passed = True
finally:
    if opened:
        request('POST', '/computer/finish', {'run_id': run,
            'outcome': 'Private VM preflight passed' if passed else 'Private VM preflight stopped after failure'})

prefix = '/computer/files/computer/runs/' + run + '/video/'
manifest = request('GET', prefix + 'manifest.json')
assert manifest['fps'] == 2 and len(manifest['segments']) >= 2
assert not manifest['recording']
for segment in manifest['segments']:
    raw = request('GET', prefix + quote(segment['file'], safe=''), binary=True)
    assert len(raw) == segment['bytes'] and hashlib.sha256(raw).hexdigest() == segment['sha256']
    (output / segment['file']).write_bytes(raw)
summary['recording'] = manifest
(output / 'receipt.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
