#!/usr/bin/env python3
"""Back up a stopped Computer or restore its two volumes on a fresh Docker host.

Run as root on the Docker host. Archives contain the private browser profile;
transfer the entire backup directory privately, never through model context.
This intentionally does not stop/start services or switch appliance URLs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

VOLUMES = {'runtime': '/var/lib/calliope-desktop', 'home': '/home/kasm-user'}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def save(path, value):
    with path.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')


def backup(container, directory):
    item = json.loads(docker('inspect', container))[0]
    if item['State']['Running']:
        raise ValueError('Stop the source desktop before copying its profile')
    mounts = {m['Destination']: m for m in item['Mounts']}
    for destination in VOLUMES.values():
        if mounts.get(destination, {}).get('Type') != 'volume':
            raise ValueError('Both Computer paths must use named volumes')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt = {'container': container, 'image_id': item['Image'],
               'image_ref': item['Config']['Image'],
               'restart_policy': item['HostConfig']['RestartPolicy'], 'volumes': {}}
    for key, destination in VOLUMES.items():
        mount = mounts[destination]
        archive = directory / (key + '.tar')
        subprocess.run(['tar', '--numeric-owner', '--acls', '--xattrs',
                        '-cpf', str(archive), '-C', mount['Source'], '.'], check=True)
        receipt['volumes'][key] = {'source_volume': mount['Name'],
                                  'archive': archive.name,
                                  'bytes': archive.stat().st_size,
                                  'sha256': digest(archive)}
    save(directory / 'manifest.json', receipt)
    print(json.dumps(receipt, indent=2))


def restore(directory, project):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise ValueError('Invalid Compose project name')
    receipt = json.loads((directory / 'manifest.json').read_text())
    names = {key: project + '_rvbbit_desktop' + ('_home' if key == 'home' else '')
             for key in VOLUMES}
    existing = set(docker('volume', 'ls', '--format', '{{.Name}}').splitlines())
    if existing.intersection(names.values()):
        raise ValueError('Destination volumes must not exist; never overwrite a prior desktop')
    # Verify both archives before creating either destination volume.
    for key in VOLUMES:
        archive = directory / (key + '.tar')
        record = receipt['volumes'][key]
        if archive.stat().st_size != record['bytes'] or digest(archive) != record['sha256']:
            raise ValueError('Backup checksum mismatch: ' + key)
    for key, name in names.items():
        compose_volume = 'rvbbit_desktop' + ('_home' if key == 'home' else '')
        docker('volume', 'create', '--label', 'com.docker.compose.project=' + project,
               '--label', 'com.docker.compose.volume=' + compose_volume, name)
        mount = Path(json.loads(docker('volume', 'inspect', name))[0]['Mountpoint'])
        if any(mount.iterdir()):
            raise ValueError('Destination unexpectedly contains files')
        subprocess.run(['tar', '--numeric-owner', '--same-owner', '--acls', '--xattrs',
                        '-xpf', str(directory / (key + '.tar')), '-C', str(mount)], check=True)
        # Compare contents, modes, owners and timestamps while both copies are stopped.
        subprocess.run(['tar', '--numeric-owner', '--acls', '--xattrs',
                        '-df', str(directory / (key + '.tar')), '-C', str(mount)], check=True)
    save(directory / 'restore-receipt.json', {'project': project, 'volumes': names,
                                             'verified_against_archives': True})
    print('Restored and compared both volumes; no desktop started.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--container', default='calliope-desktop')
    parser.add_argument('--project', default='calliope-computer')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root on the Docker host')
    os.umask(0o077)
    if args.operation == 'backup':
        backup(args.container, args.directory.resolve())
    else:
        restore(args.directory.resolve(), args.project)


if __name__ == '__main__':
    main()
