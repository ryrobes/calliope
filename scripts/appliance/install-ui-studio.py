#!/usr/bin/env python3
"""Install the optional UI Studio on an appliance from a prepared release directory.

Run as root on the Lab guest. Requires a locally loaded, verified runner image.
Does not recreate or modify existing Calliope containers/configuration/databases.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time


def docker(*args, **kwargs):
    return subprocess.run(['docker',*args],check=True,**kwargs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('/opt/rvbbit/ui-studio'))
    p.add_argument('--image',required=True); p.add_argument('--revision',required=True)
    p.add_argument('--network',default='calliope'); p.add_argument('--hermes',default='calliope-hermes')
    a=p.parse_args(); os.umask(0o077)
    if os.geteuid()!=0: raise SystemExit('Run this installer as root on the appliance')
    release=a.release.resolve(); root=a.root.resolve(); root.mkdir(mode=0o700,parents=True,exist_ok=True)
    before=[json.loads(line) for line in docker('ps','--format','json',capture_output=True,text=True).stdout.splitlines()]
    backup=root/('backups/'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())); backup.mkdir(parents=True,mode=0o700)
    for name in ['compose.yml','runtime.env']:
        if (root/name).exists(): shutil.copy2(root/name,backup/name)
    token_path=root/'token'
    if not token_path.exists(): token_path.write_text(secrets.token_urlsafe(48))
    os.chown(token_path,10001,10001); token_path.chmod(0o400)
    (root/'data').mkdir(mode=0o700,exist_ok=True); os.chown(root/'data',10001,10001)
    shutil.copy2(release/'docker-compose.ui-studio.yml',root/'compose.yml')
    (root/'runtime.env').write_text(f'UI_STUDIO_IMAGE={a.image}\nUI_STUDIO_REVISION={a.revision}\nUI_STUDIO_ROOT={root}\nUI_STUDIO_NETWORK={a.network}\n')
    compose=['compose','--project-name','calliope-ui-studio','--env-file',str(root/'runtime.env'),'-f',str(root/'compose.yml')]
    docker(*compose,'config','--quiet')
    docker(*compose,'up','-d','--no-deps','ui-studio')
    for _ in range(30):
        result=docker('inspect','calliope-ui-studio','--format','{{.State.Health.Status}}',capture_output=True,text=True)
        if result.stdout.strip()=='healthy': break
        time.sleep(2)
    else: raise SystemExit('Runner failed health check; previous configuration is in '+str(backup))
    # Install versioned source in the durable Hermes volume. No gateway restart.
    docker('exec',a.hermes,'mkdir','-p','/tmp/calliope-ui-install')
    for name in ['studio_client.py','SKILL.md']:
        docker('cp',str(release/name),a.hermes+':/tmp/calliope-ui-install/'+name)
    config={'url':'http://ui-studio:18456','token':token_path.read_text().strip()}
    install=r'''
import hashlib,json,os,shutil,sys
from pathlib import Path
config=json.load(sys.stdin)
base=Path('/opt/data'); tool=base/'.local/bin/calliope-ui-studio'; cfg=base/'.config/calliope-ui-studio.json'
backup=base/'backups'/('ui-studio-'+sys.argv[1][:12]); backup.mkdir(parents=True,exist_ok=True)
for f in (tool,cfg):
 if f.exists(): shutil.copy2(f,backup/f.name)
tool.parent.mkdir(parents=True,exist_ok=True); cfg.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile('/tmp/calliope-ui-install/studio_client.py',tool); tool.chmod(0o700)
cfg.write_text(json.dumps(config)); cfg.chmod(0o600)
roots=[base,base/'profiles/dashboard-studio']
for root in roots:
 if not root.exists(): continue
 folder=root/'skills/productivity/calliope-native-apps'; folder.mkdir(parents=True,exist_ok=True)
 target=folder/'SKILL.md'
 if target.exists(): shutil.copy2(target,backup/(root.name+'-SKILL.md'))
 shutil.copyfile('/tmp/calliope-ui-install/SKILL.md',target); target.chmod(0o600)
 for f in (folder,target): os.chown(f,10000,10000)
for f in (base/'.local',tool.parent,tool,cfg.parent,cfg): os.chown(f,10000,10000)
print(json.dumps({'client_sha256':hashlib.sha256(tool.read_bytes()).hexdigest(),'skill_sha256':hashlib.sha256(Path('/tmp/calliope-ui-install/SKILL.md').read_bytes()).hexdigest(),'profiles':['main','dashboard-studio']}))
'''
    installed=docker('exec','-i',a.hermes,'python','-c',install,a.revision,input=json.dumps(config),text=True,capture_output=True)
    docker('exec','-u','hermes',a.hermes,'/opt/data/.local/bin/calliope-ui-studio','catalog','/opt/data/ui-studio-catalog.json',capture_output=True,text=True)
    after=[json.loads(line) for line in docker('ps','--format','json',capture_output=True,text=True).stdout.splitlines()]
    existing={x['Names']:x['ID'] for x in before if x['Names']!='calliope-ui-studio'}
    current={x['Names']:x['ID'] for x in after}
    preserved=all(current.get(name)==ident for name,ident in existing.items())
    receipt={'revision':a.revision,'image':a.image,'installed':json.loads(installed.stdout),'existing_containers_preserved':preserved,'backup':str(backup)}
    (root/'receipt.json').write_text(json.dumps(receipt,indent=2)); print(json.dumps(receipt,indent=2))
    if not preserved: raise SystemExit('An existing container changed during installation; inspect the receipt')


if __name__=='__main__': main()
