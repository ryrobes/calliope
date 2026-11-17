#!/usr/bin/env python3
"""Begin and export manual Calliope benchmark trials from outside the appliance.

Controller dependencies: Python 3.12+ and SSH. Collection uses Python/psycopg
already installed in the existing Warehouse container; analysis is ordinary
files on the host. Run --help and see docs/BENCHMARK_HARNESS.md.
"""
from __future__ import annotations
import argparse
import atexit
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile

HERE=Path(__file__).resolve().parent

def now():return datetime.now(timezone.utc).isoformat()
def stamp(value):return datetime.fromisoformat(str(value).replace('Z','+00:00'))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def read(path):return json.loads(path.read_text())
def rows(path):return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def command(args,argv,**kwargs):
    if args.ssh_host:
        if not hasattr(args,'_control_dir'):
            args._control_dir=Path(tempfile.mkdtemp(prefix='benchmark-ssh-'))
            def cleanup():
                subprocess.run(['ssh','-S',str(args._control_dir/'socket'),'-O','exit',args.ssh_host],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                shutil.rmtree(args._control_dir,ignore_errors=True)
            atexit.register(cleanup)
        base=['ssh']+(['-F',str(args.ssh_config)] if args.ssh_config else [])+[
            '-o','ControlMaster=auto','-o','ControlPersist=60','-o','ControlPath='+str(args._control_dir/'socket'),
            '-o','ConnectTimeout=15',args.ssh_host,shlex.join(argv)]
    else:base=argv
    for attempt in range(3):
        result=subprocess.run(base,**kwargs)
        if result.returncode==0:return result
        if result.returncode!=255 or not args.ssh_host:break
        if attempt<2:
            out=kwargs.get('stdout')
            if hasattr(out,'seek'):out.seek(0);out.truncate()
            time.sleep(20)
    raise RuntimeError('Capture command failed (exit '+str(result.returncode)+'); no completed archive was published')

def safe_extract(archive,dest):
    with tarfile.open(archive,'r:gz') as tar:
        for m in tar.getmembers():
            if not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts:raise ValueError('Unsafe capture member: '+m.name)
        # Member validation above works on both Python 3.11 and newer runtimes.
        tar.extractall(dest,filter='data')

def collect(args,options,dest):
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='calliope-benchmark-') as temp:
        archive=Path(temp)/'capture.tar.gz'
        with archive.open('wb') as out:
            command(args,['docker','exec','-i','calliope-server','python','-c',(HERE/'benchmark-capture.py').read_text()],
                    input=json.dumps(options).encode(),stdout=out)
        safe_extract(archive,dest)
    info=command(args,['docker','ps','-aq'],capture_output=True,text=True).stdout.split()
    raw=json.loads(command(args,['docker','inspect',*info],capture_output=True,text=True).stdout)
    # Intentionally never write Docker Env, mounts with credentials, or raw inspect.
    images=[{'name':c['Name'].lstrip('/'),'image':c['Config']['Image'],'image_id':c['Image'],
             'revision':c['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),
             'state':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status') } for c in raw]
    write(dest/'state/images.json',images)
    for name in ('install-receipt.json','benchmark-baseline.json'):
        r=command(args,['python3','-c',"from pathlib import Path; p=Path('/opt/rvbbit')/"+repr(name)+"; print(p.read_text() if p.exists() else '{}')"],capture_output=True,text=True)
        write(dest/'state'/name,json.loads(r.stdout))
    write(dest/'state/host.json',{'uname':command(args,['uname','-a'],capture_output=True,text=True).stdout.strip(),
          'docker':command(args,['docker','version','--format','{{.Server.Version}}'],capture_output=True,text=True).stdout.strip()})
    if options['mode']=='export':
        for service in ('calliope-server','calliope-hermes','calliope-hindsight'):
            log=dest/'logs'/(service+'.log');log.parent.mkdir(exist_ok=True)
            with log.open('wb') as f:
                command(args,['docker','logs','--timestamps','--since',options['started_at'],service],stdout=f,stderr=subprocess.STDOUT)


def cost_summary(before,after,runids,hids,start):
    old={r['activity_id']:r for r in before}; selected=[];totals=defaultdict(lambda:defaultdict(float))
    for r in after:
        linked=r.get('run_id') in runids or r.get('session_id') in hids
        in_window=stamp(r['occurred_at'])>=stamp(start)
        prior=old.get(r['activity_id'])
        changed=prior is not None and prior!=r
        if not linked and not in_window and not changed:continue
        group='linked' if linked else 'window_only' if in_window else 'prior_revaluation'
        selected.append({**r,'benchmark_attribution':group,'before':prior})
        kind=r.get('cost_kind','unknown')
        if group!='prior_revaluation' and r.get('cost_usd') is not None:
            # Included/estimated/reported/reserved are separate, never a bill total.
            totals[group][kind]+=float(r['cost_usd'])
    return selected,dict(totals)


def relative_cost_summary(costs):
    """Catalog equivalents remain separate from reported bills and budget holds."""
    result={}
    for row in costs:
        group=row['benchmark_attribution']
        if group=='prior_revaluation' or row.get('cost_kind')=='reserved': continue
        value=(row.get('pricing') or {}).get('relative_cost_usd')
        item=result.setdefault(group,{'known_relative_cost_usd':None,'priced_activities':0,'unpriced_activities':0})
        if value is None:
            item['unpriced_activities']+=1
        else:
            item['known_relative_cost_usd']=(item['known_relative_cost_usd'] or 0)+float(value)
            item['priced_activities']+=1
    return result


def report(trial,bundle):
    capture=bundle/'after';selection=read(capture/'trace/selection.json')
    runids=set(selection['run_ids']);hids=set(selection['hermes_sessions'])
    costs,totals=cost_summary(rows(bundle/'before/costs/activity.jsonl'),rows(capture/'costs/activity.jsonl'),runids,hids,trial['started_at'])
    with (bundle/'costs.jsonl').open('w') as f:
        for r in costs:f.write(json.dumps(r)+'\n')
    spec=importlib.util.spec_from_file_location('ledger_export',HERE/'export-run-ledger.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    events=rows(capture/'trace/events.jsonl');trace=mod.summarize(events)
    write(bundle/'cache.json',trace['cache'])
    turns=read(capture/'thread/turns.json')
    unsettled=[r['id'] for r in turns if r.get('status') not in ('complete','completed','failed','error','cancelled','canceled','interrupted')]
    unfinished_runs=[r['id'] for r in read(capture/'thread/linked-runs.json') if r.get('status') not in ('complete','completed','failed','error','cancelled','canceled','interrupted','succeeded','skipped')]
    ledger_calls={r.get('attempt_id') or r.get('event_id') for r in events if r.get('event') in ('model.completed','model.failed')}
    imported={r['activity_id'].removeprefix('hermes:') for r in costs if r['source']=='hermes_ledger'}
    missing=sorted(ledger_calls-imported)
    summary={'trial_id':trial['trial_id'],'baseline':trial['baseline'],'label':trial['label'],
             'session_ids':trial['session_ids'],'cost_totals_by_attribution_and_kind':totals,
             'relative_cost_by_attribution':relative_cost_summary(costs),
             'activity_counts':dict(Counter(r['benchmark_attribution'] for r in costs)),
             'trace':trace,'running_turns':unsettled,'ledger_attempts_not_yet_in_cost_view':missing,
             'unfinished_linked_runs':unfinished_runs,
             'capture':read(capture/'capture.json')}
    previous={r['id'] for r in read(bundle/'before/state/session_index.json')}
    summary['preexisting_threads']=[r['id'] for r in read(capture/'thread/sessions.json') if r['id'] in previous]
    summary['comparison_warnings']=[]
    if summary['preexisting_threads']:summary['comparison_warnings'].append('Selected threads predate begin; this is a retrospective capture, not a fresh trial.')
    if summary['capture']['missing_attachments']:summary['comparison_warnings'].append('Some thread attachments could not be copied; see capture.missing_attachments.')
    for name,key in [('hermes-skills','skill_changes'),('generated-views','generated_view_changes')]:
        old=read(bundle/('before/state/'+name+'.json')) if (bundle/('before/state/'+name+'.json')).exists() else None
        new=read(capture/('state/'+name+'.json')) if (capture/('state/'+name+'.json')).exists() else None
        if old is None or new is None:
            summary[key]={'coverage':'unavailable'}
            summary['comparison_warnings'].append(name+' before/after capture is unavailable.')
            continue
        if name=='generated-views':
            old={r['schema']+'.'+r['name']:{k:v for k,v in r.items() if k!='oid'} for r in old}
            new={r['schema']+'.'+r['name']:{k:v for k,v in r.items() if k!='oid'} for r in new}
        summary[key]={'created':sorted(set(new)-set(old)),'deleted':sorted(set(old)-set(new)),
                      'modified':sorted(k for k in set(new)&set(old) if new[k]!=old[k])}
        if any(summary[key].values()):summary['comparison_warnings'].append(name+' changed during the trial; inspect '+key+'.')
    if trace.get('unidentified_tool_events'):
        summary['comparison_warnings'].append('Legacy tool events lack call IDs; trace closure is uncertain.')
    summary['file_changes']={
        'created_or_modified':[k for k,v in read(capture/'state/files.json').items() if v.get('exported_as')],
        'deleted':sorted(set(read(bundle/'before/state/files.json'))-set(read(capture/'state/files.json')))}
    memory_pending=[r for r in rows(capture/'state/memory/async_operations.jsonl') if r.get('status') in ('pending','processing','running','queued')]
    summary['memory_operations_pending_in_instance']=len(memory_pending)
    if memory_pending:summary['comparison_warnings'].append('Memory background operations remain pending in the instance; they may not belong to this thread.')
    summary['observed_work_complete']=bool(events) and not (unsettled or unfinished_runs or missing or trace['open_agent_spans'] or trace['open_tool_calls'] or trace['model_attempts_without_terminal'] or trace.get('stream_attempts_without_terminal',0))
    summary['completion_limit']='Observed thread/trace closure only; unlinked work and provider-internal activity cannot be proven complete.'
    write(bundle/'summary.json',summary)
    body=['# '+trial['label'],'','Baseline: '+trial['baseline'],'Trial: '+trial['trial_id'],'',
          'Observed work complete: '+str(summary['observed_work_complete']),
          'Unimported model attempts: '+str(len(missing)),'',
          'Stream attempts: '+str(trace['stream_attempts_started']),
          'Stream retries: '+str(trace['stream_retries']),
          'Failed stream attempts: '+str(trace['stream_attempts_failed']),
          'Time in failed streams (seconds): '+str(round(trace['failed_stream_seconds'],3)),
          'Unclosed stream attempts: '+str(trace['stream_attempts_without_terminal']),
          'Stream receipts are diagnostic; failed-attempt usage and cost remain unknown until reconciled.',
          '', '## Prompt cache evidence','',
          'See cache.json for per-route counters, request identity and prefix changes. Missing counters are unknown, not zero.',
          '', '```json',json.dumps(trace['cache']['overall'],indent=2),'```',
          '', '## Cost evidence','',
          'Directly linked and window-only activity are separate. Prior revaluations are excluded from trial totals.',
          'Included value, estimates, reported charges, and reserved budgets are separate; unknown is not zero.','',
          '```json',json.dumps(totals,indent=2),'```',
          '', '## Relative cost','',
          'Token-price equivalents, not provider charges. Per-activity rate snapshots and cache assumptions are in costs.jsonl. Unpriced activities are excluded, not zero.',
          '```json',json.dumps(summary['relative_cost_by_attribution'],indent=2),'```','','## Thread','']
    for t in sorted(turns,key=lambda x:(x['session_id'],x['ordinal'])):
        body+=['### Turn '+str(t['ordinal'])+' · '+t['status'],'','**User**',t.get('user_message') or '',
               '','**Assistant**',t.get('assistant_message') or '','']
    (bundle/'report.md').write_text('\n'.join(body))
    return summary


def seal_archive(bundle):
    checks={str(p.relative_to(bundle)):sha(p) for p in sorted(bundle.rglob('*')) if p.is_file() and p!=bundle/'checksums.json'}
    write(bundle/'checksums.json',checks)
    target=bundle.with_suffix('.zip')
    if target.exists():raise ValueError('Archive already exists: '+str(target))
    partial=target.with_suffix('.zip.partial')
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(bundle.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(bundle)))
    verify(partial);partial.rename(target)
    target.with_suffix('.zip.sha256').write_text(sha(target)+'  '+target.name+'\n')
    return target

def verify(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip():raise ValueError('ZIP CRC failure')
        expected=json.loads(z.read('checksums.json'))
        actual={n for n in z.namelist() if n!='checksums.json'}
        if actual!=set(expected) or len(z.namelist())!=len(actual)+1:raise ValueError('Archive membership mismatch')
        for name,value in expected.items():
            if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Unsafe archive path')
            with z.open(name) as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=value:raise ValueError('Checksum mismatch: '+name)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ssh-host',help='Guest SSH target; omit when running in the guest')
    p.add_argument('--ssh-config',type=Path)
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('begin');b.add_argument('--trial',type=Path,required=True);b.add_argument('--baseline',required=True);b.add_argument('--label',required=True)
    e=sub.add_parser('export');e.add_argument('--trial',type=Path,required=True);e.add_argument('--session-id',action='append',required=True)
    e.add_argument('--table',action='append',default=[],help='Optional exact schema.table data snapshot')
    e.add_argument('--attach',action='append',type=Path,default=[],help='Screenshot, download, or notes; copied verbatim')
    s=sub.add_parser('fixture');s.add_argument('--output',type=Path,required=True);s.add_argument('--baseline',required=True);s.add_argument('--public-data',action='store_true')
    v=sub.add_parser('verify');v.add_argument('archive',type=Path)
    a=p.parse_args()
    if a.command=='verify':verify(a.archive);print('Verified '+str(a.archive));return
    if a.command=='begin':
        a.trial.mkdir(parents=True,mode=0o700,exist_ok=False)
        collect(a,{'mode':'begin'},a.trial/'before')
        trial={'schema_version':1,'trial_id':str(uuid.uuid4()),'baseline':a.baseline,'label':a.label,'started_at':now(),
               'collector_sha256':sha(HERE/'benchmark-capture.py'),'controller_sha256':sha(Path(__file__))}
        marker=read(a.trial/'before/state/benchmark-baseline.json')
        if marker.get('baseline')!=a.baseline:raise ValueError('Guest baseline marker does not match --baseline; begin was not completed')
        write(a.trial/'trial.json',trial)
        print('Trial started. Now use Calliope normally. '+str(a.trial));return
    if a.command=='fixture':
        a.output.mkdir(parents=True,mode=0o700,exist_ok=False)
        collect(a,{'mode':'fixture','public_data':a.public_data},a.output/'state')
        if read(a.output/'state/state/benchmark-baseline.json').get('baseline')!=a.baseline:
            raise ValueError('Guest baseline marker does not match --baseline; fixture was not sealed')
        write(a.output/'fixture.json',{'schema_version':1,'baseline':a.baseline,'captured_at':now(),'collector_sha256':sha(HERE/'benchmark-capture.py')})
        print(seal_archive(a.output));return
    trial=read(a.trial/'trial.json');trial['session_ids']=sorted(set(a.session_id));trial['export_requested_at']=now()
    if trial['collector_sha256']!=sha(HERE/'benchmark-capture.py'):raise ValueError('Collector changed since begin; use the same script revision')
    # Distinct export attempts support later settlement without overwriting evidence.
    bundle=a.trial/('export-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6])
    bundle.mkdir(mode=0o700)
    shutil.copytree(a.trial/'before',bundle/'before');write(bundle/'trial.json',trial)
    collect(a,{'mode':'export','started_at':trial['started_at'],'sessions':trial['session_ids'],'tables':a.table,
               'files_before':read(a.trial/'before/state/files.json')},bundle/'after')
    for i,path in enumerate(a.attach):
        if not path.is_file():raise ValueError('Attachment must be a file: '+str(path))
        dest=bundle/'attachments'/(str(i)+'-'+path.name);dest.parent.mkdir(exist_ok=True);shutil.copyfile(path,dest)
    summary=report(trial,bundle)
    (bundle/'README.txt').write_text('Start with report.md and summary.json. See docs/BENCHMARK_HARNESS.md in the collector checkout.\nRaw private transcripts and authored files are included; credential stores are excluded.\nScreenshots are manual attachments, not auto-rendered. HTML may require its original runtime/remote assets.\nThe before/after state captures are separate snapshots. Data exports are CSV.gz with schemas in data/manifest.json.\nDo not add source cost tables together: costs.jsonl is the selected central view.\n')
    archive=seal_archive(bundle)
    print(json.dumps({'archive':str(archive),'observed_work_complete':summary['observed_work_complete'],'unimported_attempts':len(summary['ledger_attempts_not_yet_in_cost_view'])}))

if __name__=='__main__':main()
