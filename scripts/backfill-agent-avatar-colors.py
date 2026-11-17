#!/usr/bin/env python3
"""Run inside calliope-server with PYTHONPATH=/app. Dry-run unless --apply.

Updates only portrait color metadata through Hermes' revisioned, CAS-protected
profile document API. Both static and generated database portraits are supported.
"""
import argparse
import json
from pathlib import Path
import yaml
import avatar_colors
import hermes_profiles
import server

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply',action='store_true')
parser.add_argument('--profile',action='append',help='Limit to profile keys; defaults to all profiles')
args=parser.parse_args()
assets=Path(server.__file__).parent/'calliope'

def read_image(filename):
    path=assets/filename
    if path.is_file():return path.read_bytes()
    if filename.startswith('generated-') and filename.endswith('.png'):
        with server._conn() as c:
            row=c.execute('SELECT png FROM rvbbit.generated_specialist_avatars WHERE specialist_key=%s',(filename[len('generated-'):-4],)).fetchone()
        return bytes(row['png']) if row else None
    return None

for profile in hermes_profiles.profiles().get('profiles',[]):
    key=profile['key']
    if args.profile and key not in args.profile:continue
    document=hermes_profiles.document(server._conn,key,'profile')
    if 'content' not in document:raise RuntimeError(f'Cannot read profile {key}')
    original=yaml.safe_load(document['content']) or {}
    updated=avatar_colors.enrich(original,read_image)
    changed=updated!=original
    if changed and args.apply:
        result=hermes_profiles.save_document(server._conn,key,'profile',yaml.safe_dump(updated,sort_keys=False,allow_unicode=True),document['content_hash'],actor='avatar-color-backfill',reason='Cache portrait accent colors')
        if not result.get('ok'):raise RuntimeError(f'Profile update failed: {key}: {result.get("phase")}')
        assert not result.get('restart_required'), 'Identity edits should be live'
    print(json.dumps({'profile':key,'changed':changed,'applied':changed and args.apply,**avatar_colors.fields(updated)}))
