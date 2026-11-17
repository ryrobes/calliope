#!/usr/bin/env python3
"""Overlay only the UI decision adapter on the running Lab server image.

Run as root with a release directory containing ui_decisions.py. The current
runtime, Compose files, environment, and image are inventoried and retained for
rollback. Existing application source is never replaced from a dirty checkout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--container',default='calliope-server');p.add_argument('--root',type=Path,default=Path('/opt/rvbbit/ui-decisions'))
    a=p.parse_args();os.umask(0o077)
    if os.geteuid()!=0:raise SystemExit('Run as root on Lab')
    a.root.mkdir(parents=True,exist_ok=True)
    folder=a.root/time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());folder.mkdir()
    inspect=json.loads(run(['docker','inspect',a.container],capture_output=True,text=True).stdout)[0]
    (folder/'before.json').write_text(json.dumps(inspect))
    before={r['Names']:r['ID'] for r in map(json.loads,run(['docker','ps','--format','json'],capture_output=True,text=True).stdout.splitlines())}
    source=folder/'server.py';run(['docker','cp',a.container+':/app/server.py',str(source)])
    original=source.read_text();(folder/'server.before.py').write_text(original)
    anchor='    register_dashboard_routes(m)\n'
    if '    ui_decisions.register(m, _conn)' not in original:
        if original.count(anchor)!=1:raise SystemExit('Unknown server registration point')
        source.write_text(original.replace(anchor,anchor+'    import ui_decisions\n    ui_decisions.register(m, _conn)\n'))
    compile(source.read_text(),str(source),'exec')
    shutil.copy2(a.release/'ui_decisions.py',folder/'ui_decisions.py')
    compile((folder/'ui_decisions.py').read_text(),'ui_decisions.py','exec')
    token=Path('/opt/rvbbit/ui-studio/token').read_text().strip()
    (folder/'ui-decision-token.sha256').write_text(hashlib.sha256(token.encode()).hexdigest())
    (folder/'ui-decision-token.sha256').chmod(0o444)
    (folder/'.dockerignore').write_text('*\n!Dockerfile\n!server.py\n!ui_decisions.py\n!ui-decision-token.sha256\n')
    image='calliope-lab/server:ui-decisions-'+a.revision[:12]
    (folder/'Dockerfile').write_text('FROM '+inspect['Config']['Image']+'\nCOPY server.py ui_decisions.py ui-decision-token.sha256 /app/\nLABEL io.calliope.ui-decisions.revision="'+a.revision+'"\n')
    run(['docker','build','-t',image,str(folder)])
    labels=inspect['Config']['Labels'];override=a.root/'compose.json'
    files=[f for f in labels['com.docker.compose.project.config_files'].split(',') if f!=str(override)]
    base=['docker','compose','--project-name',labels['com.docker.compose.project'],'--env-file',labels['com.docker.compose.project.environment_file']]
    for f in files:base+=['-f',f]
    rendered=json.loads(run(base+['config','--format','json'],capture_output=True,text=True).stdout)
    service=labels['com.docker.compose.service']
    env=dict(v.split('=',1) for v in inspect['Config']['Env'])
    # Override potential on-disk env drift with the exact running environment.
    for key in rendered['services'][service].get('environment',{}):env.setdefault(key,None)
    if override.exists():shutil.copy2(override,folder/'compose.before.json')
    override.write_text(json.dumps({'services':{service:{'image':image,'environment':env}}}));override.chmod(0o600)
    command=base+['-f',str(override)]
    run(command+['config','--quiet'])
    run(command+['up','-d','--no-deps','--pull','never',service])
    for _ in range(30):
        state=json.loads(run(['docker','inspect',a.container],capture_output=True,text=True).stdout)[0]
        if state['State'].get('Health',{}).get('Status')=='healthy':break
        time.sleep(2)
    else:raise SystemExit('Health check failed. Rollback image recorded in '+str(folder/'before.json'))
    after={r['Names']:r['ID'] for r in map(json.loads,run(['docker','ps','--format','json'],capture_output=True,text=True).stdout.splitlines())}
    same_env=sorted(state['Config']['Env'])==sorted(inspect['Config']['Env'])
    preserved=all(after.get(k)==v for k,v in before.items() if k!=a.container)
    receipt={'revision':a.revision,'image':image,'prior_image':inspect['Config']['Image'],'backup':str(folder),'environment_preserved':same_env,'other_containers_preserved':preserved,'adapter_sha256':hashlib.sha256((folder/'ui_decisions.py').read_bytes()).hexdigest()}
    (a.root/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
    if not same_env or not preserved:raise SystemExit('Runtime drift detected; inspect deployment receipt')

if __name__=='__main__':main()
