#!/usr/bin/env python3
"""Build, validate and package one Calliope shipping stack. Never reads Lab state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / 'release/calliope-release.json'
SCHEMA_LOCK = ROOT / 'release/schema-lock.json'


def read(path):
    return json.loads(path.read_text())


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def migration_hashes():
    directory = ROOT / 'crates/pg_rvbbit/sql/migrations'
    return {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob('*.sql'))}


def validate():
    release = read(RELEASE)
    version = (ROOT / 'VERSION').read_text().strip()
    if release['calliope_version'] != version:
        raise ValueError('VERSION and release manifest disagree.')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Use an explicit SemVer product version.')
    engine = re.search(r"default_version\s*=\s*'([^']+)'", (ROOT / 'crates/pg_rvbbit/pg_rvbbit.control').read_text())[1]
    if engine != release['rvbbit_engine_version']:
        raise ValueError('Extension and release engine versions disagree.')
    hashes = migration_hashes()
    registered = re.findall(r'"([0-9]{4}_[a-z0-9_]+)"', (ROOT / 'crates/pg_rvbbit/src/migrations.rs').read_text())
    if registered != list(hashes):
        raise ValueError('SQL migrations and the ordered compiled registry disagree.')
    if hashes != read(SCHEMA_LOCK)['migrations']:
        raise ValueError('Migration hashes changed. Run shipping.py schema-lock to append new migrations; published SQL is immutable.')
    if release['schema']['migration_target'] != registered[-1]:
        raise ValueError('Release migration target does not match the embedded migrations.')
    runtime_catalog('ghcr.io/ryrobes', version)
    if '-' not in version and release['schema'].get('baseline') is None:
        raise ValueError('Stable shipping releases require a verified fresh-install schema baseline.')
    builds = read(ROOT / 'release/services.json')
    if sorted(release['images']) != sorted(x['image'] for x in builds.values()):
        raise ValueError('Release image list and build inputs disagree.')
    compose = yaml.safe_load((ROOT / 'deploy/compose.yaml').read_text())
    if compose.get('include'):
        raise ValueError('Shipping Compose must be standalone.')
    product_images = {s['image'].split('/')[-1].split(':')[0]
                      for s in compose['services'].values() if 'CALLIOPE_REGISTRY' in s['image']}
    if product_images != set(release['images']):
        raise ValueError('Shipping services and released product images disagree.')
    for name, service in compose['services'].items():
        if service.get('extends') or service.get('build'):
            raise ValueError(f'{name}: shipping Compose may not extend or build services.')
        for mount in service.get('volumes', []):
            if isinstance(mount, dict) and mount.get('type') == 'bind':
                source = mount['source']
                if not (source.startswith(('./state/', './config/')) or source == '${RVBBIT_DOCKER_SOCKET:-/var/run/docker.sock}'):
                    raise ValueError(f'{name}: mount depends on files outside the shipping bundle: {source}')
    for item in builds.values():
        if not (ROOT / item['context'] / item['dockerfile']).is_file():
            raise ValueError(f'Missing image build input: {item}')
    return release, builds, compose


def runtime_catalog(registry, version):
    doc = json.loads(subprocess.check_output([
        str(ROOT / 'capabilities/tools/rvbbit-capability'), 'catalog', 'seed-json',
        '--root', str(ROOT / 'capabilities/packs'), '--image-prefix', registry,
        '--image-tag', version, '--default-install-mode', 'image'], text=True))
    release = read(RELEASE)
    selected = set(release['runtime_capabilities'])
    names = release.get('runtime_image_names') or {}
    if set(names) != selected:
        raise ValueError('runtime_image_names must name every runtime capability.')
    rows = [r for r in doc['capabilities'] if r['catalog_entry']['id'] in selected]
    if {r['catalog_entry']['id'] for r in rows} != selected:
        raise ValueError('A required runtime capability is missing from the image catalog.')
    # New bundles publish calliope-python-runtime and calliope-mcp-gateway.
    # Leave a stored rvbbit-python-runtime / rvbbit-mcp-gateway reference
    # alone: those tags belong to rvbbit-sql, and :1.2.0 there is not Calliope.
    prefix = registry.rstrip('/')
    for row in rows:
        catalog_id = row['catalog_entry']['id']
        image = row['catalog_entry']['runtime_image']
        expected = f"{prefix}/{names[catalog_id]}:{version}"
        if image != expected:
            raise ValueError(f'{catalog_id} image is {image}; the release manifest requires {expected}.')
    return rows


def concrete_image(image, release, registry):
    image = re.sub(r'\$\{CALLIOPE_REGISTRY:-[^}]+\}', registry, image)
    image = re.sub(r'\$\{CALLIOPE_VERSION:-[^}]+\}', release['calliope_version'], image)
    # Upstream override expressions have one default in the canonical file.
    image = re.sub(r'\$\{[^}:]+:-([^}]+)\}', r'\1', image)
    if '$' in image:
        raise ValueError(f'Unresolved image reference: {image}')
    return image


def references(release, compose, registry, catalog):
    return sorted({concrete_image(s['image'], release, registry) for s in compose['services'].values()}
                  | {r['catalog_entry']['runtime_image'] for r in catalog})


def inspect_images(refs, *, anonymous=True):
    result = {}
    missing = []
    with tempfile.TemporaryDirectory(prefix='calliope-public-images-') as tmp:
        env = {**os.environ, 'DOCKER_CONFIG': tmp} if anonymous else dict(os.environ)
        for ref in refs:
            proc = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', ref], env=env,
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            match = re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})', proc.stdout, re.M)
            if proc.returncode or not match:
                missing.append(ref)
            else:
                result[ref] = ref.split('@')[0] + '@' + match[1]
    return result, missing


def public_lock(refs):
    result, missing = inspect_images(refs)
    if missing:
        raise ValueError('Images unavailable anonymously; publish them and enable public access:\n' + '\n'.join(missing))
    return result


def verify_release_images(refs, locked, version, revision, *, anonymous=True):
    """A digest alone does not prove an image was built from this release."""
    metadata = {}
    with tempfile.TemporaryDirectory(prefix='calliope-image-identity-') as tmp:
        env = {**os.environ, 'DOCKER_CONFIG': tmp} if anonymous else dict(os.environ)
        for ref in refs:
            raw = subprocess.check_output(['docker', 'buildx', 'imagetools', 'inspect', locked[ref],
                                           '--format', '{{json .Image}}'], env=env, text=True)
            document = json.loads(raw)
            config = document.get('linux/amd64', document)
            labels = config.get('config', {}).get('Labels') or {}
            if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
                raise ValueError(f'{ref}: missing the shipping linux/amd64 image.')
            if (labels.get('org.opencontainers.image.version') != version
                    or labels.get('org.opencontainers.image.revision') != revision
                    or labels.get('org.opencontainers.image.source') != 'https://github.com/ryrobes/calliope'):
                raise ValueError(f'{ref}: published image does not match release {version} at {revision}.')
            metadata[ref] = {'version': version, 'revision': revision, 'platform': 'linux/amd64'}
    return metadata


def reuse_bundle_images(path, release, registry):
    """Version installer-only releases without relabelling unchanged images."""
    base = read(path)
    if (base.get('distribution_state') != 'public-images-locked'
            or base.get('calliope_version') != release['calliope_version']
            or base.get('registry') != registry or not base.get('image_lock')):
        raise ValueError('Reuse requires a public locked bundle of the same product version and registry.')
    changed = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '--no-renames',
                                      base['revision'], 'HEAD', '--'], text=True).splitlines()
    allowed = {'docker/origin/Caddyfile.calliope', 'docker/docker-compose.calliope.yml',
               'scripts/release/shipping.py', 'scripts/release/test-origin.py',
               'tests/test_shipping_bundle.py'}
    if any(not (p.startswith('deploy/') or p.startswith('docs/') or p in allowed) for p in changed):
        raise ValueError('Image source changed; bump the product version and build new images instead of reusing them.')
    return base


def bundle(output, *, registry='ghcr.io/ryrobes', candidate=False, lock=False, lock_private=False, reuse_images_from=None,
           image_tag=None):
    release, builds, compose = validate()
    if output.exists():
        raise ValueError('Output already exists; choose a new release directory.')
    catalog = runtime_catalog(registry, release['calliope_version'])
    refs = references(release, compose, registry, catalog)
    if sum((candidate, lock, lock_private)) != 1:
        raise ValueError('Choose --lock (public), --lock-private (visibility pending), or --candidate (unverified tags).')
    if reuse_images_from and not lock:
        raise ValueError('Reusing images requires --lock and anonymous public-image verification.')
    if image_tag and (not candidate or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', image_tag)):
        raise ValueError('--image-tag names locally built product images and is only valid with --candidate.')
    product = {registry + '/' + name + ':' + release['calliope_version'] for name in release['images']}
    if lock or lock_private:
        require_clean_checkout()
    base = reuse_bundle_images(reuse_images_from, release, registry) if reuse_images_from else None
    public_missing = []
    if lock_private:
        locked, missing = inspect_images(refs, anonymous=False)
        if missing:
            raise ValueError('Published image set is incomplete or inaccessible:\n' + '\n'.join(missing))
        _, public_missing = inspect_images(refs)
    else:
        locked = public_lock(refs) if lock else {}
    bundle_revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    revision = base['revision'] if base else bundle_revision
    if base and locked != base['image_lock']:
        raise ValueError('Published image digests differ from the base bundle; refusing an installer-only release.')
    identities = {}
    if locked:
        owned = [registry + '/' + name + ':' + release['calliope_version'] for name in release['images']]
        owned += [row['catalog_entry']['runtime_image'] for row in catalog]
        identities = verify_release_images(owned, locked, release['calliope_version'], revision,
                                           anonymous=not lock_private)
    for service in compose['services'].values():
        ref = concrete_image(service['image'], release, registry)
        if image_tag and ref in product:
            # Development installs of local builds; runtime images keep the release tag.
            ref = ref.rsplit(':', 1)[0] + ':' + image_tag
        service['image'] = locked.get(ref, ref)
        # Release identity follows the bundle, not a previous installation's .env.
        environment = service.get('environment', {})
        for key in ('CALLIOPE_VERSION', 'WAREHOUSE_CALLIOPE_VERSION'):
            if key in environment:
                environment[key] = release['calliope_version']
    for row in catalog:
        entry, manifest = row['catalog_entry'], row['capability_manifest']
        ref = entry['runtime_image']
        pinned = locked.get(ref, ref)
        entry['runtime_image'] = entry['prebuilt_runtime_image'] = pinned
        entry['prebuilt_runtime']['image'] = pinned
        manifest['runtime']['image'] = pinned
        if manifest.get('prebuilt_runtime'):
            manifest['prebuilt_runtime']['image'] = pinned
    for service in compose['services'].values():
        environment = service.get('environment', {})
        for key in ('CALLIOPE_REVISION', 'STUDIO_REVISION', 'WAREHOUSE_CALLIOPE_REVISION'):
            if key in environment:
                environment[key] = revision
        if 'WAREHOUSE_CALLIOPE_DESKTOP_IMAGE_REF' in environment:
            environment['WAREHOUSE_CALLIOPE_DESKTOP_IMAGE_REF'] = compose['services']['desktop']['image']
            environment['WAREHOUSE_CALLIOPE_DESKTOP_IMAGE_REVISION'] = revision
    sequence = int(base.get('bundle_sequence', 0)) + 1 if base else 0
    bundle_version = release['calliope_version'] + (f'+bundle.{sequence}' if sequence else '')
    release = {**release, 'revision': revision, 'registry': registry,
               'bundle_version': bundle_version, 'bundle_sequence': sequence, 'bundle_revision': bundle_revision,
               'distribution_state': ('awaiting-public-images' if public_missing else 'public-images-locked') if (lock or lock_private) else 'development-candidate',
               'images_needing_public_access': public_missing, 'image_tag': image_tag,
               'image_lock': locked, 'image_identities': identities,
               'runtime_images': [r['catalog_entry']['runtime_image'] for r in catalog]}
    output.mkdir(parents=True)
    (output / 'config').mkdir()
    (output / 'lib').mkdir()
    copies = {
        'deploy/configure.py': 'configure.py', 'deploy/install.sh': 'install.sh',
        'deploy/settings.env.example': 'settings.env.example',
        'deploy/README.md': 'README.md', 'docker/origin/Caddyfile.calliope': 'config/Caddyfile',
        'deploy/pg_hba.conf': 'config/pg_hba.conf',
        'docker/clover/inference.sql': 'config/inference.sql',
        'crates/rvbbit_hutch/src/ledger.sql': 'config/ledger.sql',
        'services/clover/research/schema.sql': 'config/research.sql',
        'scripts/clover/configure.py': 'lib/clover_configure.py',
        'scripts/clover/capability_options.json': 'lib/capability_options.json',
        'scripts/clover/ledger.py': 'lib/ledger.py', 'scripts/clover/research.py': 'lib/research.py',
        'release/schema-lock.json': 'schema-lock.json',
    }
    for source, target in copies.items():
        shutil.copy2(ROOT / source, output / target)
    (output / 'compose.yaml').write_text(yaml.safe_dump(compose, sort_keys=False, width=110))
    (output / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    sql = []
    for row in catalog:
        entry = json.dumps(row['catalog_entry']).replace("'", "''")
        manifest = json.dumps(row['capability_manifest']).replace("'", "''")
        sql.append(f"SELECT rvbbit.upsert_capability_catalog_entry(catalog_entry => '{entry}'::jsonb, capability_manifest => '{manifest}'::jsonb, catalog_source => 'calliope-release');")
    (output / 'config/capabilities.sql').write_text('\n'.join(sql) + '\n')
    sums = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob('*')) if p.is_file()}
    (output / 'checksums.json').write_text(json.dumps(sums, indent=2) + '\n')
    return release


def require_clean_checkout():
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit the checkout before building/publishing a release or sealing its bundle.')


def image_exists(ref):
    result = subprocess.run(['docker', 'manifest', 'inspect', ref], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return True
    # Auth/network errors do not prove a tag is absent.
    if not re.search(r'manifest unknown|no such manifest|manifest.*not found', result.stderr, re.I):
        raise ValueError(f'Cannot establish whether {ref} exists. Check registry authentication/connectivity.\n' + result.stderr.strip()[-2000:])
    return False


def require_unpublished(ref):
    if image_exists(ref):
        raise ValueError(f'{ref} already exists. Published tags are immutable; bump the release version.')


def reuse_published(ref, version, revision):
    """A retry may reuse an exact published source revision, never overwrite it."""
    if not image_exists(ref):
        return False
    run(['docker', 'pull', '--platform', 'linux/amd64', ref], stdout=subprocess.DEVNULL)
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', ref], text=True))[0]
    labels = info['Config'].get('Labels') or {}
    if (labels.get('org.opencontainers.image.version') != version
            or labels.get('org.opencontainers.image.revision') != revision):
        raise ValueError(f'{ref} belongs to a different release revision. Published tags are immutable; bump the version.')
    print(f'Reusing published image for the same source revision: {ref}', flush=True)
    return True


def verify_image(service, ref):
    if service == 'postgres':
        run(['python3', str(ROOT / 'scripts/schema/test-image.py'), '--image', ref,
             '--report', '/tmp/calliope-schema-image-test.json'])
    elif service == 'desktop':
        run(['python3', str(ROOT / 'scripts/desktop/test-fresh-install.py'), '--image', ref,
             '--report', '/tmp/calliope-desktop-fresh-install.json'])


def build_runtimes(push=False):
    release, _, _ = validate()
    version = os.environ.get('CALLIOPE_VERSION', release['calliope_version'])
    if version != release['calliope_version']:
        raise ValueError('Build version must match the release manifest.')
    registry = os.environ.get('CALLIOPE_REGISTRY', 'ghcr.io/ryrobes')
    selected = list(release['runtime_capabilities'])
    if push:
        require_clean_checkout()
        revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        for row in runtime_catalog(registry, version):
            if reuse_published(row['catalog_entry']['runtime_image'], version, revision):
                selected.remove(row['catalog_entry']['id'])
    if not selected:
        return
    args = ['python3', str(ROOT / 'scripts/release/capability-images.py'),
            '--image-prefix', registry, '--version', version]
    for name in selected:
        args += ['--only', name]
    if push:
        args.append('--push')
    run(args)


def build(service, push=False):
    release, builds, _ = validate()
    item = builds[service]
    version = os.environ.get('CALLIOPE_VERSION', release['calliope_version'])
    if version != release['calliope_version']:
        raise ValueError('Build version must match VERSION and the release manifest.')
    registry = os.environ.get('CALLIOPE_REGISTRY', 'ghcr.io/ryrobes')
    ref = registry + '/' + item['image'] + ':' + version
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    if push:
        require_clean_checkout()
        if reuse_published(ref, version, revision):
            verify_image(service, ref)
            return
    args = ['docker', 'build', '-f', str(ROOT / item['context'] / item['dockerfile']), '-t', ref,
            '--label', 'org.opencontainers.image.version=' + version,
            '--label', 'org.opencontainers.image.revision=' + revision,
            '--label', 'org.opencontainers.image.source=https://github.com/ryrobes/calliope',
            '--build-arg', 'CALLIOPE_REVISION=' + revision, '--build-arg', 'REVISION=' + revision]
    if builder := os.environ.get('CALLIOPE_BUILD_BUILDER'):
        # CI can isolate large exports from the Docker daemon and runner process.
        args += ['--builder', builder, '--load']
    if service == 'hermes':
        args += ['--build-arg', 'HERMES_BASE_IMAGE=' + release['upstreams']['hermes_base_image']]
    run([*args, str(ROOT / item['context'])])
    if builder and os.environ.get('CALLIOPE_RELEASE_STOP_BUILDER') == '1':
        # The CI worker is dedicated to this job. Release its build/export
        # memory before booting the image on the same small runner.
        run(['docker', 'buildx', 'stop', builder])
    if service == 'postgres':
        verify_image(service, ref)
    if push:
        if service == 'desktop':
            verify_image(service, ref)
        run(['docker', 'push', ref])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    sub.add_parser('schema-lock')
    sub.add_parser('matrix')
    b = sub.add_parser('bundle')
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--registry', default='ghcr.io/ryrobes')
    b.add_argument('--candidate', action='store_true')
    b.add_argument('--image-tag', help='With --candidate: product image tag of local development builds')
    b.add_argument('--lock', action='store_true')
    b.add_argument('--lock-private', action='store_true', help='Pin authenticated published images and report any needing public access')
    b.add_argument('--reuse-images-from', type=Path, help='Prior public release.json for an installer-only bundle revision; image source changes are rejected')
    r = sub.add_parser('build-runtimes')
    r.add_argument('--push', action='store_true')
    b = sub.add_parser('build')
    b.add_argument('--service', required=True)
    b.add_argument('--push', action='store_true')
    a = p.parse_args()
    if a.command == 'schema-lock':
        hashes = migration_hashes()
        if SCHEMA_LOCK.exists():
            for name, digest in read(SCHEMA_LOCK)['migrations'].items():
                if hashes.get(name) != digest:
                    raise ValueError(f'Previously recorded migration changed or disappeared: {name}')
        SCHEMA_LOCK.write_text(json.dumps({'migrations': hashes}, indent=2) + '\n')
    elif a.command == 'check':
        validate(); print('Release versions, migration hashes and standalone Compose agree.')
    elif a.command == 'matrix':
        print(json.dumps({'service': [*read(ROOT / 'release/services.json'), 'runtimes']}))
    elif a.command == 'build-runtimes':
        build_runtimes(a.push)
    elif a.command == 'build':
        build(a.service, a.push)
    else:
        release = bundle(a.output, registry=a.registry, candidate=a.candidate, lock=a.lock, lock_private=a.lock_private,
                         reuse_images_from=a.reuse_images_from, image_tag=a.image_tag)
        print(json.dumps({'bundle': str(a.output), 'state': release['distribution_state']}))


if __name__ == '__main__':
    try:
        main()
    except ValueError as e:
        raise SystemExit(str(e))
