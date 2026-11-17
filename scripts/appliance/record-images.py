#!/usr/bin/env python3
"""Record installed appliance image identities without exporting container secrets."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--registry', default='ghcr.io/ryrobes')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--computer', action='store_true')
    args = parser.parse_args()
    components = ['postgres', 'datarabbit', 'warren', 'doc-extract', 'hermes', 'server']
    if args.computer:
        components.append('desktop')
    images = []
    for component in components:
        ref = f'{args.registry}/calliope-{component}:{args.version}'
        result = subprocess.run(['docker', 'image', 'inspect', ref],
                                check=True, text=True, capture_output=True)
        image = json.loads(result.stdout)[0]
        labels = image.get('Config', {}).get('Labels') or {}
        revision = labels.get('org.opencontainers.image.revision')
        if revision != args.revision:
            raise SystemExit(f'{ref}: expected revision {args.revision}, got {revision}')
        # Classic Docker reports a config digest as Id; containerd-backed
        # Docker can report an OCI manifest digest. Compare the actual ordered
        # layer diff IDs and runtime config across stores, not these display IDs.
        content = {'rootfs': image['RootFS'], 'config': image['Config'],
                   'os': image['Os'], 'architecture': image['Architecture']}
        fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()
        images.append({'component': component, 'reference': ref, 'daemon_image_id': image['Id'],
                       'content_sha256': fingerprint,
                       'rootfs_diff_ids': image['RootFS'].get('Layers', []),
                       'repo_digests': image.get('RepoDigests', []), 'revision': revision,
                       'os': image['Os'], 'architecture': image['Architecture'],
                       'created': image['Created'], 'size_bytes': image['Size']})
    document = {'version': args.version, 'revision': args.revision,
                'recorded_at': datetime.now(timezone.utc).isoformat(), 'images': images}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Do not silently replace an earlier experiment's manifest.
    with args.output.open('x') as handle:
        json.dump(document, handle, indent=2)
        handle.write('\n')
    print(f'Verified {len(images)} image revisions; wrote {args.output}')


if __name__ == '__main__':
    main()
