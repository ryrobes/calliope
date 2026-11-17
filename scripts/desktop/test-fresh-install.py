#!/usr/bin/env python3
"""Boot a built desktop with empty volumes; exercise its API, Cua and persistence.

Uses an isolated Compose project, random loopback ports and generated credentials.
Never connects to an existing desktop or model provider. Requires local Docker.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Already built local image; never pulled implicitly')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cpus', type=float, help='Optional CPU limit for reproducing small-host failures')
    parser.add_argument('--memory', default=os.environ.get('CALLIOPE_DESKTOP_TEST_MEMORY'),
                        help='Optional test-container memory limit, e.g. 4g')
    args = parser.parse_args()
    if args.cpus is not None and args.cpus <= 0:
        parser.error('--cpus must be greater than zero')
    project = 'calliope-desktop-check-' + uuid.uuid4().hex[:12]
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        folder = Path(temporary)
        env_file = folder / '.env'
        subprocess.run(['python3', str(ROOT / 'scripts/release/provision-computer-use-env.py'),
                        '--env-file', str(env_file)], check=True, stdout=subprocess.DEVNULL)
        env = dict(line.split('=', 1) for line in env_file.read_text().splitlines() if '=' in line)
        # Extend the shipping image/volume/runtime definition, overriding only
        # image identity, container name, loopback test ports and model callback.
        compose_file = folder / 'compose.json'
        desktop = {
            'extends': {'file': str(ROOT / 'docker/docker-compose.computer-base.yml'), 'service': 'desktop'},
            'image': args.image, 'container_name': project,
            'ports': ['127.0.0.1::9000', '127.0.0.1::6901'],
            'environment': {'CALLIOPE_COMPUTER_DECISION_URL': 'http://unused.invalid/decide'},
        }
        if args.cpus is not None:
            desktop['cpus'] = args.cpus
        if args.memory:
            desktop['mem_limit'] = args.memory
        compose_file.write_text(json.dumps({'services': {'desktop': desktop},
            'volumes': {'rvbbit_desktop': {}, 'rvbbit_desktop_home': {}}}))
        compose = ['docker', 'compose', '-p', project, '--env-file', str(env_file), '-f', str(compose_file)]
        # The parent shell must not override our fresh credentials or volume settings.
        child_env = {k: v for k, v in os.environ.items() if not k.startswith(('CALLIOPE_', 'COMPOSE_'))}
        def run(*command, capture=False):
            return subprocess.run(command, env=child_env, check=True, text=True,
                                  stdout=subprocess.PIPE if capture else None).stdout
        try:
            run(*compose, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '180')
            port = run(*compose, 'port', 'desktop', '9000', capture=True).strip()
            # Existing remote preflight verifies the public control API, Cua,
            # visible browser, workbook output, screenshot and video checksums.
            check_env = {**child_env, 'WAREHOUSE_CALLIOPE_DESKTOP_CONTROL_KEY': env['CALLIOPE_DESKTOP_CONTROL_KEY']}
            checked = subprocess.run(['python3', str(ROOT / 'scripts/desktop/check-remote-computer.py'),
                                      '--url', 'http://' + port], env=check_env,
                                     check=True, capture_output=True, text=True)
            receipt = json.loads(checked.stdout)
            artifacts = report.with_suffix('.evidence')
            shutil.copytree(receipt['evidence'], artifacts, dirs_exist_ok=True)
            shutil.rmtree(receipt['evidence'])
            receipt['evidence'] = str(artifacts)
            metadata = json.loads(run('docker', 'inspect', project, capture=True))[0]
            assert not metadata['HostConfig'].get('DeviceRequests'), 'Test must not request GPU hardware'
            assert not metadata['HostConfig'].get('Devices'), 'Test must not map host devices'
            receipt['image_id'] = metadata['Image']
            receipt['image'] = args.image
            # Verify both home content and runtime receipts survive replacement.
            # Force recreation, not just a process restart, using the same volumes.
            run(*compose, 'up', '-d', '--force-recreate', '--no-build', '--pull', 'never',
                '--wait', '--wait-timeout', '180')
            workbook = json.loads(receipt['job_stdout'].strip())['workbook']
            verification = '''import json,sys
from pathlib import Path
from openpyxl import load_workbook
assert sum(r[0] for r in load_workbook(sys.argv[1]).active.iter_rows(min_row=2,min_col=2,values_only=True))==25
assert (Path('/var/lib/calliope-desktop/computer/runs')/sys.argv[2]/'video/manifest.json').is_file()
print('Home and runtime volumes survived container replacement')
'''
            run('docker', 'exec', '--user', '1000:1000', project, 'python3', '-c', verification, workbook, receipt['run_id'])
            receipt.update({'empty_volumes': True, 'gpu_devices': 0, 'persistence_verified': True,
                            'model_inference_tested': False, 'result': 'passed'})
            report.write_text(json.dumps(receipt, indent=2) + '\n')
            print('Fresh desktop passed; report: ' + str(report), flush=True)
        except subprocess.CalledProcessError as exc:
            # The preflight reports bounded assertion/error output, not credentials.
            if exc.stderr:
                print(exc.stderr[-4000:], flush=True)
            # Keep recording diagnostics before deleting this test's volumes.
            # Do not dump Docker environment variables or authentication config.
            diagnostic_code = '''import json,os,subprocess,urllib.request
from pathlib import Path
root=Path('/var/lib/calliope-desktop/computer')
files={}
for pattern in ('runs/*/video/capture.log','runs/*/video/manifest.json','runs/*/video/masking-*.json'):
    for path in root.glob(pattern):
        files[str(path.relative_to(root))]=path.read_text(errors='replace')[-8000:]
disk=os.statvfs(root)
try:
    request=urllib.request.Request('http://127.0.0.1:9000/health',headers={'X-Calliope-Desktop-Key':os.environ['CALLIOPE_DESKTOP_CONTROL_KEY']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response:
        health=json.load(response)
except Exception as error:
    health={'error':str(error)}
processes=subprocess.run(['ps','-eo','pid,comm,stat'],capture_output=True,text=True,timeout=10).stdout
print(json.dumps({'health':health,'processes':processes,'recordings':files,'storage_free_bytes':disk.f_bavail*disk.f_frsize}))
'''
            diagnostics = subprocess.run(['docker', 'exec', project, 'python3', '-c', diagnostic_code],
                                         env=child_env, capture_output=True, text=True)
            detail = diagnostics.stdout.strip() or diagnostics.stderr[-2000:]
            state = subprocess.run(['docker', 'inspect', '--format', '{{json .State}}', project],
                                   env=child_env, capture_output=True, text=True)
            logs = subprocess.run(['docker', 'logs', '--tail', '150', project],
                                  env=child_env, capture_output=True, text=True)
            startup = (logs.stdout + logs.stderr)[-18000:]
            # This guest has only generated test credentials, but they still do
            # not belong in a CI artifact. Never include Docker's Config.Env.
            for value in sorted(env.values(), key=len, reverse=True):
                if len(value) >= 8:
                    startup = startup.replace(value, '[redacted]')
            report.with_suffix('.failure.json').write_text(json.dumps({
                'error': str(exc), 'capture': detail, 'container_state': state.stdout.strip(),
                'startup_log': startup}, indent=2) + '\n')
            print('Desktop recording diagnostics: ' + detail, flush=True)
            print('Desktop startup diagnostics: ' + startup, flush=True)
            raise
        finally:
            subprocess.run([*compose, 'down', '--volumes', '--remove-orphans'], env=child_env, check=True)


if __name__ == '__main__':
    main()
