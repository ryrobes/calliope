#!/usr/bin/env python3
"""Preserve an existing container-only desktop profile before its first upgrade.

Run on the Docker host immediately before `compose up`. The old container and
an owner-only host backup remain available for rollback. Fresh installations
and already-persistent desktops need no migration.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', default='/opt/rvbbit/.env')
    parser.add_argument('--backup-root', default='/opt/rvbbit/deploy-backups')
    parser.add_argument('--container', default='calliope-desktop')
    args = parser.parse_args()
    inspected = docker('inspect', args.container, check=False)
    if inspected.returncode:
        print('No existing desktop container; home will initialize on first start.')
        return
    container = json.loads(inspected.stdout)[0]
    if any(mount['Destination'] == '/home/kasm-user' for mount in container['Mounts']):
        print('Desktop home is already persistent.')
        return
    env = {}
    for line in Path(args.env_file).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('\"').strip("'")
    volume = env.get('CALLIOPE_DESKTOP_HOME_VOLUME') or 'calliope_desktop_home'
    docker('volume', 'create', volume)
    image = container['Image']
    mount = f'{volume}:/destination'
    check = docker('run', '--rm', '--user', '0', '--entrypoint', '/bin/sh', '-v', mount, image,
                   '-c', 'test -z "$(ls -A /destination)"', check=False)
    if check.returncode:
        raise SystemExit('The target home volume is nonempty. Inspect it before migrating; nothing was overwritten.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path(args.backup_root) / ('computer-home-' + stamp)
    backup.mkdir(mode=0o700, parents=True)
    home = backup / 'home'
    home.mkdir(mode=0o700)
    running = container['State']['Running']
    try:
        docker('stop', '--time', '30', args.container)
        docker('cp', '-a', args.container + ':/home/kasm-user/.', str(home))
        docker('run', '--rm', '--user', '0', '--entrypoint', '/bin/sh',
               '-v', str(home) + ':/source:ro', '-v', mount, image,
               '-c', 'cp -a /source/. /destination/ && chown -R 1000:1000 /destination')
    except BaseException:
        if running:
            docker('start', args.container, check=False)
        raise
    print(f'Desktop home preserved in {volume}; rollback backup: {backup}. Ready for compose up.')


if __name__ == '__main__':
    main()
