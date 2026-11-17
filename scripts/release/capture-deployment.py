#!/usr/bin/env python3
"""Export a safe image inventory on a deployment host; does not pull or change containers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def project(containers, host):
    rows = []
    for item in containers:
        name = item.get('Name', '').lstrip('/')
        if not name.startswith(('calliope-', 'rvbbit-')):
            continue
        config, state = item.get('Config', {}), item.get('State', {})
        labels = config.get('Labels') or {}
        rows.append({'container': name, 'image': config.get('Image'), 'image_id': item.get('Image'),
                     'version': labels.get('org.opencontainers.image.version'),
                     'revision': labels.get('org.opencontainers.image.revision'),
                     'state': state.get('Status'), 'health': (state.get('Health') or {}).get('Status'), 'host': host})
    return sorted(rows, key=lambda r: r['container'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host-label', default='Instance host')
    args = parser.parse_args()
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids])) if ids else []
    rows = project(containers, args.host_label)
    for row in rows:
        image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', row['image_id']]))[0]
        row['digests'] = image.get('RepoDigests') or []
    payload = {'captured_at': datetime.now(timezone.utc).isoformat(), 'containers': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n')
    temp.chmod(0o644)
    temp.replace(args.output)
    print(f'Recorded {len(rows)} containers in {args.output}')


if __name__ == '__main__':
    main()
