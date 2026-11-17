#!/usr/bin/env python3
"""Export only guest settings from the actual desktop container for a VM move."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


def settings(container, *, address, decision_url, version):
    address = str(ipaddress.IPv4Address(address))
    url = urlsplit(decision_url)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
        raise ValueError('Decision URL must be an HTTP(S) endpoint without embedded credentials')
    if url.query or url.fragment or not url.path.endswith('/api/calliope/computer/decide'):
        raise ValueError('Supply the complete Computer decision endpoint without query or fragment')
    if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}', version):
        raise ValueError('Invalid desktop image version')
    current = dict(item.split('=', 1) for item in container['Config']['Env'] if '=' in item)
    if not current.get('VNC_PW') or not current.get('CALLIOPE_DESKTOP_CONTROL_KEY'):
        raise ValueError('Source desktop is missing its VNC password or control key')
    result = {
        'CALLIOPE_VERSION': version,
        'CALLIOPE_REVISION': version,
        'CALLIOPE_REGISTRY': 'ghcr.io/ryrobes',
        'CALLIOPE_COMPUTER_ENABLED': 'true',
        'CALLIOPE_COMPUTER_BIND_ADDRESS': address,
        'CALLIOPE_COMPUTER_DECISION_URL': decision_url,
        'CALLIOPE_DESKTOP_RESOLUTION': current.get('VNC_RESOLUTION') or '1280x720',
        'CALLIOPE_DESKTOP_MAX_FRAME_RATE': current.get('MAX_FRAME_RATE') or '24',
        'CALLIOPE_DESKTOP_VNC_PASSWORD': current['VNC_PW'],
        'CALLIOPE_DESKTOP_CONTROL_KEY': current['CALLIOPE_DESKTOP_CONTROL_KEY'],
    }
    if current.get('VNC_VIEW_ONLY_PW'):
        result['CALLIOPE_DESKTOP_VNC_VIEW_PASSWORD'] = current['VNC_VIEW_ONLY_PW']
    return result


def write_env(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write('# Limited Computer VM settings; exported from the running guest.\n')
            for key, value in values.items():
                # Compose dotenv expands dollar references inside double quotes.
                output.write(key + '=' + json.dumps(value.replace('$', '$$'), ensure_ascii=False) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='calliope-desktop')
    parser.add_argument('--output', required=True)
    parser.add_argument('--address', required=True)
    parser.add_argument('--decision-url', required=True)
    parser.add_argument('--image-version', required=True)
    args = parser.parse_args()
    inspected = subprocess.run(['docker', 'inspect', args.container],
        capture_output=True, text=True, check=True)
    values = settings(json.loads(inspected.stdout)[0], address=args.address,
        decision_url=args.decision_url, version=args.image_version)
    write_env(args.output, values)
    print(f'Wrote limited Computer settings to {args.output}; values not printed.')


if __name__ == '__main__':
    main()
