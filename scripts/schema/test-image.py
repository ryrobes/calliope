#!/usr/bin/env python3
"""Verify the Postgres image's real empty-volume entrypoint and release guard."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time
import uuid

_spec = importlib.util.spec_from_file_location('baseline_roles', Path(__file__).with_name('check-baseline-roles.py'))
baseline_roles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline_roles)


def run(*args, input=None, check=True):
    result = subprocess.run(args, input=input, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(' '.join(args[:4]) + ' failed:\n' + result.stderr[-6000:])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    name = 'calliope-schema-test-' + uuid.uuid4().hex[:12]
    volume = name + '-data'
    tls_volume = name + '-tls'
    run('docker', 'volume', 'create', volume)
    run('docker', 'volume', 'create', tls_volume)
    run('docker', 'network', 'create', '--internal', name)
    try:
        run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python3',
            '-e', 'CALLIOPE_SQL_HOST=sql-test.example', '-v', tls_volume + ':/tls',
            args.image, '/usr/local/bin/rvbbit-postgres-tls')
        hba = Path(__file__).resolve().parents[2] / 'deploy/pg_hba.conf'
        def start():
            run('docker', 'run', '-d', '--name', name, '--network', name, '--network-alias', 'database',
                '-e', 'POSTGRES_PASSWORD=isolated-image-test', '-e', 'POSTGRES_DB=rvbbit',
                '-v', volume + ':/var/lib/postgresql', '-v', tls_volume + ':/tls:ro',
                '-v', str(hba) + ':/etc/rvbbit/pg_hba.conf:ro', args.image,
                'postgres', '-c', 'ssl=on', '-c', 'ssl_cert_file=/tls/server.crt',
                '-c', 'ssl_key_file=/tls/server.key', '-c', 'hba_file=/etc/rvbbit/pg_hba.conf',
                '-c', 'cron.use_background_workers=on')
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                status = run('docker', 'inspect', name, '--format', '{{.State.Status}}').stdout.strip()
                if status == 'exited':
                    logs = run('docker', 'logs', name)
                    raise RuntimeError('Fresh Postgres exited:\n' + (logs.stdout + logs.stderr)[-16000:])
                comm = run('docker', 'exec', name, 'cat', '/proc/1/comm', check=False)
                ready = run('docker', 'exec', name, 'pg_isready', '-U', 'postgres', '-d', 'rvbbit', check=False)
                if comm.stdout.strip() == 'postgres' and ready.returncode == 0:
                    return
                time.sleep(1)
            raise RuntimeError('Fresh Postgres did not become ready')
        def sql(statement):
            return run('docker', 'exec', '-i', name, 'psql', '-U', 'postgres', '-d', 'rvbbit',
                       '-X', '-Atq', '-v', 'ON_ERROR_STOP=1', input=statement).stdout.strip()
        def guard(action, check=True):
            return run('docker', 'exec', '-e', 'PGUSER=postgres', '-e', 'PGDATABASE=rvbbit', name,
                       'rvbbit-schema-release', action, check=check)
        def tls_client(host='sql-test.example', password='isolated-image-test', mode='verify-full', check=True):
            return run('docker', 'exec', '-e', 'PGPASSWORD=' + password, name, 'psql',
                       f'host={host} hostaddr=127.0.0.1 dbname=rvbbit user=postgres sslmode={mode} sslrootcert=/tls/ca.crt',
                       '-X', '-Atq', '-v', 'ON_ERROR_STOP=1', '-c',
                       'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()', check=check)
        start()
        assert tls_client().stdout.strip() == 't'
        assert tls_client(host='wrong.example', check=False).returncode != 0
        assert 'password authentication failed' in tls_client(password='wrong', check=False).stderr
        # Internal clients that currently use Rust's NoTls still authenticate.
        assert tls_client(mode='disable').stdout.strip() == 'f'
        assert sql('SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL') == '0'
        assert sql("SELECT count(*) FROM pg_hba_file_rules WHERE type='hostnossl' AND address='all' AND auth_method='reject'") == '1'
        release = json.loads(run('docker', 'exec', name, 'cat', '/usr/share/rvbbit/schema-release/release.json').stdout)
        manifest = json.loads(run('docker', 'exec', name, 'cat', '/usr/share/rvbbit/schema-release/baseline/manifest.json').stdout)
        image = json.loads(run('docker', 'image', 'inspect', args.image).stdout)[0]
        labels = image['Config'].get('Labels') or {}
        assert labels.get('org.opencontainers.image.version') == release['calliope_version']
        assert int(sql('SELECT count(*) FROM rvbbit.schema_migrations')) == len(manifest['migrations'])
        assert sql('SELECT DISTINCT provenance FROM rvbbit.schema_release_migrations') == 'baseline'
        # Roles are cluster-wide, so only an empty volume proves the baseline creates them.
        baseline_roles.verify(json.loads(sql(baseline_roles.QUERY)))
        assert 'up to date' in sql('SELECT rvbbit.migrate()')
        assert json.loads(guard('check').stdout)['pending'] == 0
        # Bootstrap has no local Postgres socket. Exercise the actual image
        # guard from a second container, rather than only through docker exec.
        remote_guard = run('docker', 'run', '--rm', '--network', name,
                           '-e', 'RVBBIT_DSN=postgresql://postgres:isolated-image-test@database:5432/rvbbit',
                           '--entrypoint', 'rvbbit-schema-release', args.image, 'check')
        assert json.loads(remote_guard.stdout)['pending'] == 0
        sql("CREATE TABLE public.customer_probe(id int PRIMARY KEY,value text); INSERT INTO public.customer_probe VALUES(1,'retained');")
        cron_id = run('docker', 'exec', name, 'psql', '-U', 'postgres', '-d', 'postgres', '-XAtqc',
                      "SELECT cron.schedule_in_database('shipping-cron-probe','1 second',"
                      "'INSERT INTO public.customer_probe VALUES(2,''scheduled'') ON CONFLICT DO NOTHING','rvbbit')").stdout.strip()
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if sql('SELECT value FROM public.customer_probe WHERE id=2') == 'scheduled':
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Scheduled SQL did not run with the shipping authentication rules')
        finally:
            run('docker', 'exec', name, 'psql', '-U', 'postgres', '-d', 'postgres', '-XAtqc',
                'SELECT cron.unschedule(' + str(int(cron_id)) + ')')
        run('docker', 'rm', '-f', name)
        start()
        assert sql('SELECT value FROM public.customer_probe WHERE id=1') == 'retained'
        last = release['schema']['migration_target']
        sql("UPDATE rvbbit.schema_release_installation SET product_version='1.1.4'; DELETE FROM rvbbit.schema_migrations WHERE name='" + last + "'; DELETE FROM rvbbit.schema_release_migrations WHERE name='" + last + "';")
        assert json.loads(guard('check').stdout)['pending'] == 1
        assert 'applied 1 of ' in sql('SELECT rvbbit.migrate()')
        guard('record')
        assert sql('SELECT value FROM public.customer_probe WHERE id=1') == 'retained'
        sql("INSERT INTO rvbbit.schema_migrations(name) VALUES('9999_future_test');")
        assert 'refusing downgrade' in guard('check', check=False).stderr
        sql("DELETE FROM rvbbit.schema_migrations WHERE name='9999_future_test'; UPDATE rvbbit.schema_release_migrations SET sha256='changed' WHERE name='" + last + "';")
        assert 'checksum differs' in guard('check', check=False).stderr
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = {'image': args.image, 'image_id': image['Id'],
                  'revision': labels.get('org.opencontainers.image.revision'),
                  'baseline': manifest['id'], 'migration_count': len(manifest['migrations']),
                  'build_verification': manifest['verification'],
                  'empty_volume_baseline': True, 'baseline_demo_roles': True, 'restart_preserves_data': True, 'pending_only_upgrade': True,
                  'newer_schema_rejected': True, 'checksum_mismatch_rejected': True,
                  'sql_tls_verified': True, 'sql_wrong_hostname_rejected': True,
                  'sql_password_required': True, 'internal_sql_compatible': True,
                  'remote_schema_guard': True, 'scheduled_sql_executed': True}
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    finally:
        run('docker', 'rm', '-f', name, check=False)
        run('docker', 'volume', 'rm', volume)
        run('docker', 'volume', 'rm', tls_volume)
        run('docker', 'network', 'rm', name)


if __name__ == '__main__':
    main()
