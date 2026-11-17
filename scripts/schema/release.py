#!/usr/bin/env python3
"""Install the baked baseline or check/record an incremental schema release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path('/usr/share/rvbbit/schema-release')


def sql(statement, *, source=None):
    env = {**os.environ, 'PGOPTIONS': '-c rvbbit.force_heap_scan=on'}
    args = ['psql', '-X', '-Atq', '-v', 'ON_ERROR_STOP=1']
    # libpq does not expand a URI supplied through PGDATABASE. Bootstrap runs
    # in its own container, so pass its connection string explicitly to psql.
    if os.environ.get('RVBBIT_DSN'):
        args += ['--dbname', os.environ['RVBBIT_DSN']]
    if source:
        args += ['--single-transaction', '-f', str(source)]
    proc = subprocess.run(args, input=None if source else statement, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        # Never include the connection arguments in failure diagnostics.
        raise ValueError('Schema operation failed:\n' + proc.stderr[-8000:])
    return proc.stdout.strip()


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def version_key(version):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?', version)
    if not match:
        raise ValueError('Invalid recorded product version')
    suffix = match[4]
    return (*map(int, match.group(1, 2, 3)), suffix is None,
            tuple((0, int(x)) if x.isdigit() else (1, x) for x in suffix.split('.')) if suffix else ())


def check_state(applied, recorded, prior_version, release, lock):
    unknown = sorted(set(applied) - lock['migrations'].keys())
    if unknown:
        raise ValueError('Database contains migrations absent from this image; refusing downgrade: ' + ', '.join(unknown))
    for name, digest in recorded.items():
        if lock['migrations'].get(name) != digest:
            raise ValueError('Recorded migration checksum differs from this image: ' + name)
    if prior_version and version_key(release['calliope_version']) < version_key(prior_version):
        raise ValueError(f"Database has run Calliope {prior_version}; refusing product downgrade to {release['calliope_version']}")


def check(release, lock):
    if sql("SELECT to_regclass('rvbbit.schema_migrations') IS NOT NULL") != 't':
        raise ValueError('Database has no migration history; initialize it before starting the application')
    applied = json.loads(sql("SELECT coalesce(json_agg(name ORDER BY name),'[]') FROM rvbbit.schema_migrations"))
    recorded, prior_version = {}, None
    if sql("SELECT to_regclass('rvbbit.schema_release_migrations') IS NOT NULL") == 't':
        recorded = json.loads(sql("SELECT coalesce(json_object_agg(name,sha256),'{}') FROM rvbbit.schema_release_migrations"))
    if sql("SELECT to_regclass('rvbbit.schema_release_installation') IS NOT NULL") == 't':
        prior_version = sql('SELECT product_version FROM rvbbit.schema_release_installation WHERE singleton') or None
    check_state(applied, recorded, prior_version, release, lock)
    return applied, recorded


def record(release, lock, mode):
    applied, recorded = check(release, lock)
    if set(applied) != set(lock['migrations']):
        raise ValueError('Cannot record a release before all its migrations have completed')
    source = 'baseline' if mode == 'baseline' else ('recorded-upgrade' if recorded else 'adopted-history')
    rows = ','.join('(' + lit(name) + ',' + lit(digest) + ',' + lit(source) + ')'
                    for name, digest in lock['migrations'].items())
    sql("""BEGIN;
CREATE TABLE IF NOT EXISTS rvbbit.schema_release_migrations(
 name text PRIMARY KEY,sha256 text NOT NULL,provenance text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS rvbbit.schema_release_installation(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),product_version text NOT NULL,
 engine_version text NOT NULL,migration_target text NOT NULL,baseline_id text,
 last_verified_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO rvbbit.schema_release_migrations(name,sha256,provenance) VALUES """ + rows + """
ON CONFLICT(name) DO NOTHING;
INSERT INTO rvbbit.schema_release_installation(singleton,product_version,engine_version,migration_target,baseline_id)
VALUES(true,""" + ','.join(map(lit, [release['calliope_version'], release['rvbbit_engine_version'],
                                   release['schema']['migration_target']])) + ',' + (
    lit(release['schema']['baseline']) if mode == 'baseline' else 'NULL') + """)
ON CONFLICT(singleton) DO UPDATE SET product_version=EXCLUDED.product_version,
 engine_version=EXCLUDED.engine_version,migration_target=EXCLUDED.migration_target,last_verified_at=clock_timestamp();
COMMIT;""")
    print(json.dumps({'schema_verified': True, 'product_version': release['calliope_version'],
                      'migration_count': len(applied), 'provenance': source}))


def install(root, release, lock):
    if sql("SELECT EXISTS(SELECT FROM pg_extension WHERE extname='pg_rvbbit')") == 't':
        raise ValueError('The schema baseline is only for an empty database')
    if sql("SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%')") == 't':
        raise ValueError('The schema baseline refuses a database containing existing relations')
    manifest = json.loads((root / 'baseline/manifest.json').read_text())
    script = root / 'baseline/baseline.sql'
    if manifest['id'] != release['schema']['baseline'] or manifest['migrations'] != lock['migrations']:
        raise ValueError('Baked schema baseline and release manifest disagree')
    if hashlib.sha256(script.read_bytes()).hexdigest() != manifest['baseline_sha256']:
        raise ValueError('Baked schema baseline checksum mismatch')
    extdir = Path(subprocess.check_output(['pg_config', '--sharedir'], text=True).strip()) / 'extension'
    library = Path(subprocess.check_output(['pg_config', '--pkglibdir'], text=True).strip()) / 'pg_rvbbit.so'
    for path, key in ((extdir / ('pg_rvbbit--' + release['rvbbit_engine_version'] + '.sql'), 'extension_install_sha256'),
                      (library, 'extension_library_sha256')):
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[key]:
            raise ValueError('Baked schema baseline does not match its native extension')
    if not all(manifest['verification'].values()):
        raise ValueError('Baked schema baseline has not passed build-time verification')
    sql('', source=script)
    record(release, lock, 'baseline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'check', 'record'])
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    release = json.loads((args.root / 'release.json').read_text())
    lock = json.loads((args.root / 'schema-lock.json').read_text())
    if args.action == 'install':
        install(args.root, release, lock)
    elif args.action == 'check':
        applied, _ = check(release, lock)
        print(json.dumps({'schema_compatible': True, 'applied': len(applied),
                          'pending': len(lock['migrations']) - len(applied)}))
    else:
        record(release, lock, 'upgrade')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
